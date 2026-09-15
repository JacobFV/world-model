# Durable local execution journal

`worldmodel.execution_journal.ExecutionJournal` stores checkpoint versions, history,
shared quota reservations and external-effect bookkeeping in SQLite. It is an optional
operational adapter for cooperating processes on one local filesystem. It does not
change `CheckpointEvaluator`'s numerical behavior or make external calls itself.

## History and checkpoint persistence

```python
from worldmodel.execution_journal import ExecutionJournal

with ExecutionJournal('runtime.sqlite') as journal:
    sequence = journal.append('episode:1', {'action': {'flow': 2}}, key='action:1')
    page = journal.history('episode:1', after=0, limit=20)
    # page: items, next_after, has_more, payload_bytes
    saved = journal.save_evaluator(
        'episode:1', evaluator,
        history_events=[{'event': 'checkpoint saved'}],
        expected_sequence=0,
    )

# Construct a compatible CheckpointEvaluator before reopening.
with ExecutionJournal('runtime.sqlite') as journal:
    result = journal.load_evaluator('episode:1', compatible_evaluator)
```

The evaluator must have initialized state before saving. Loading invokes its normal
restore validation, including graph/request/registry/source identity and held-pressure,
RNG and scheduler contracts. Ordinary live-backend restore remains unsupported; the guarded journal adapter below supports reconciled live sessions. The journal's
`save_checkpoint(session, checkpoint, history_events=..., expected_sequence=...)` and
`load_checkpoint(session, sequence=None, expected_identity=None)` also accept checkpoint
envelopes directly. Envelope validation alone does not validate a runtime's state:
use that runtime's restore method before execution.

A checkpoint and its supplied history events commit in one transaction. Saving follows
an already committed evaluator state; a journal write failure does not undo that
in-memory step or external work. Coordinate recovery explicitly before continuing. A storage,
validation or sequence conflict rolls back both. `expected_sequence` is an optional
compare-and-swap guard: zero means no previous checkpoint. Checkpoint sequence numbers
increase independently of environment steps. Saving the same state again creates a
new version; callers can use the sequence guard to reconcile a lost save response.

History appends have per-stream sequence numbers. An optional idempotency `key` returns
the original sequence for identical JSON; reusing it for different JSON is rejected.
Pagination uses an exclusive `after` cursor. An empty page retains that cursor.
`has_more` means more rows were available in that query, not a promise that later
writers cannot append. Pages are individually consistent reads, not one cross-page
snapshot of a changing database.

## Shared monotonically consumed quotas

```python
with ExecutionJournal('runtime.sqlite') as journal:
    journal.configure_quota('provider_calls', 100)
    reservation = journal.reserve('provider_calls', 1, 'attempt:001')
    print(journal.quota('provider_calls'))
```

Quota ceilings are immutable once configured. Reservations use `BEGIN IMMEDIATE`, so
concurrent connections cannot spend the same remaining units. A repeated reservation
key and equal units returns `already_reserved=True` without spending again; different
units are rejected. This indicates an existing reservation, **not authorization for
another call**. `QuotaExceeded` occurs before a new reservation commits. There are no
refunds, reset or deletion APIs; failed work remains charged. Checkpoint restoration
does not reset shared usage. `reservations(name, after=0, limit=100)` provides a bounded
audit listing.

Quotas are explicit integer units, not inferred money or handler counts. Callers must
reserve their conservative work bound before execution. `save_evaluator` does not
intercept process calls, and the evaluator's own process-local budget remains separate.
Uncooperative callers or another database can bypass this local accounting service.

## External-effect lifecycle

```python
from worldmodel.execution_journal import ExecutionJournal

with ExecutionJournal('runtime.sqlite') as journal:
    journal.configure_quota('provider_calls', 100)
    attempt = journal.begin_effect(
        'request:001', {'prompt': 'explicit request'}, quota='provider_calls', units=1,
    )
    if attempt['execute']:
        try:
            # An explicitly configured provider; use the same idempotency key
            # with that provider if its API supports this contract.
            answer = provider.predict(prompt='explicit request', idempotency_key='request:001')
        except Exception:
            journal.mark_uncertain('request:001', 'provider result unavailable')
            raise
        journal.complete_effect('request:001', answer, provider_receipt={'request_id': 'provider-id'})
    else:
        answer = attempt['result']
```

`begin_effect` atomically reserves optional quota and commits `pending` **before**
returning `execute=True`. A storage or quota failure leaves neither effect nor
reservation. A concurrent claimant or reopen sees the existing state:

| State | Repeated `begin_effect` behavior |
| --- | --- |
| `pending` | Raises `EffectUnresolved`; an earlier attempt may be running or may have crashed. |
| `uncertain` | Raises `EffectUnresolved`; do not silently retry. |
| `completed` | Returns `execute=False` and the stored result without another reservation. |

Request, quota and units are bound to the effect key. Reusing it with different values
fails. `mark_uncertain(key, reason)` records a bounded explanation. There is no transition
back to pending and no automatic lease expiry: expiring a lease cannot prove an external
operation did not happen. Reopening does not mark all pending effects uncertain because
another process may still be running them.

After checking the provider, an operator may call
`complete_effect(key, confirmed_result, provider_receipt={...})`. Completing an uncertain
effect requires a nonempty receipt object; the journal records the caller's attestation
and does not independently verify it. If the provider cannot establish what happened,
leave the effect unresolved. Repeating completion with identical result/receipt is
idempotent; changing a completed result is rejected.

`effects(after=0, limit=100)` pages current states. Transition records are available
through `history('effect:' + key)`. This stream prefix is reserved; effect keys are
limited to 193 characters so their audit streams remain readable. Both preserve checksums; results and receipts should
exclude credentials. A crash after a provider completes but before the journal commits
completion can leave a pending effect. Exactly-once execution requires provider-side
idempotency or reconciliation cooperation; this journal does not claim it.

## Bounds and operational limits

Default creation limits are 1 MiB per ordinary JSON payload, 32 MiB per checkpoint,
64 MiB total logical stored payload and 100,000 records. Smaller limits can be supplied
with `limits={...}`. Reopens adopt stored limits; explicitly conflicting limits fail.
Limits cannot exceed those defaults. Histories/effects page at most 100 items and
8 MiB of payload by default, with an explicit page-byte ceiling up to 32 MiB. If the
first next item cannot fit the requested page bytes, the read fails rather than
silently skipping it. JSON data must use string object keys, lists and finite native
scalars; tuples and Python-specific objects are rejected.

The logical budget excludes SQLite pages, indexes, identifiers and WAL overhead.
It is not a physical disk quota. Checkpoints retain complete evaluator snapshots;
this adds durable storage, not out-of-core simulation or incremental checkpoint deltas.
`storage_usage()` reports logical bytes/records separately from database and WAL bytes.
Keep adequate local disk space; logical retention does not promise immediate file shrinkage.

Use one connection per thread/process. SQLite WAL mode, full synchronous commits and
immediate write transactions provide local coordination with a bounded lock wait.
This is not a distributed scheduler or a network/object-storage protocol. Preserve the
complete SQLite state when moving it: use SQLite backup facilities or close writers
and checkpoint the WAL before copying. Unsigned hashes detect accidental corruption. The live SQLite database remains a trusted
local authority; optional authentication and immutable publication are described below.


## Durable environment steps and CLI

`JournaledEnvironment` wraps an `Environment` whose evaluator is a `CheckpointEvaluator`:

```python
from worldmodel.journaled_environment import JournaledEnvironment

with ExecutionJournal('runtime.sqlite') as journal:
    episode = JournaledEnvironment(env, journal, 'experiment-1', seed=7,
                                   max_process_calls=1000)
    observation, info = episode.reset()
    result = episode.step({'flow': 2}, action_id='action:0')
    artifact = episode.publish(store, 'episode_report')

# Construct a fresh compatible env, then resume the same authority.
with ExecutionJournal('runtime.sqlite') as journal:
    episode = JournaledEnvironment(env, journal, 'experiment-1', seed=7,
                                   max_process_calls=1000, resume=True)
    result = episode.step({'flow': 2}, action_id='action:0')  # stored result
```

Session identity binds the graph, request, environment specification, registry/source
identity, seed and quota ceiling. Reusing an action ID with different content fails.
Initialization captures the initial state without executing process handlers or provider
calls and consumes zero process-call quota. `reset()` reports the current committed
observation; it never rewinds a resumed session. Use a new session for a new episode.

Before a step, one transaction claims the action and reserves its conservative process
invocation bound. Reservations never refund failed work. The resulting checkpoint,
action outcome and audit entry commit together before the result is returned. Failed
persistence restores the prior numerical state; if recovery cannot be established,
the wrapper becomes unusable. A lost commit response requires explicit resume, which
loads the durable outcome. A repeated completed action returns that outcome without
new calls or reservations. The quota unit is a process-handler invocation, not tokens,
provider requests or money: handlers may make several provider calls.

Explicit resume can supersede a pending numerical action; an older writer then fails
its sequence guard. Stop the old writer before takeover. Live sessions require all
provider effects to be resolved before takeover or restore, including effects still
pending on another connection. There is no timeout-based assumption of cancellation.

The environment CLI supports `--journal PATH --session ID --resume` with the checkpoint
backend. Omit `--resume` on first creation. CLI action IDs follow their position in the
request, so resume accepts the original full action list and reuses committed prefixes.
`--session` and `--resume` require a journal; a journal requires an explicit session.
The optional `WORLD_MODEL_JOURNAL_KEY` supplies a UTF-8 authentication key of at least
32 bytes from the process environment; it is absent from requests and provenance.

## Journal-aware provider calls

Wrap an explicitly injected provider with
`JournaledBackend(provider, journal, session)` and pass it as the evaluator's
`agent_backend`. The provider exposes a stable JSON `identity` and `predict(request)`.
If `supports_idempotency is True`, the adapter calls
`predict(request, idempotency_key=stable_key)`. Each action and call ordinal determines
a stable key. A pending effect commits before invocation; successful responses become
completed effects. Exceptions leave uncertain effects and prevent silent retries.

If a handler partially succeeds, confirmed earlier responses remain available. After
an operator reconciles unresolved responses using `complete_effect` and a provider
receipt, retrying that same action reuses completed responses. Handlers must reproduce
the same call order and request content on retry; mismatches fail. This is not an
exactly-once guarantee without provider cooperation. Arbitrary side effects made outside
the injected backend are outside the journal's protection.

Default live checkpoint restore still rejects. The adapter uses an instance-bound
capability tied to its exact evaluator, backend and session, and refuses restoration
while any session effect is pending or uncertain. A caller-supplied arbitrary token or
a capability from another session cannot authorize restore.

## Verified backup, authentication and retention

```python
with ExecutionJournal('runtime.sqlite', signing_key=secret_bytes) as journal:
    journal.verify()
    exported = journal.backup('archive.sqlite')  # new destination only
    sizes = journal.storage_usage()

with ExecutionJournal.from_backup(exported['manifest'], signing_key=secret_bytes) as copy:
    copy.verify()
```

`backup()` uses SQLite's consistent backup operation and verifies the copied database.
It writes a database plus a JSON manifest with its SHA-256, size, logical usage and
optional HMAC-SHA256 signature. `from_backup()` verifies these before opening a separate
copy, either at a new `destination=` or a temporary location removed on close. It never
opens the immutable export for writes. `verify()` checks database integrity, every
payload checksum, storage accounting and quota reservation totals.

Pass a secret of at least 32 bytes as `signing_key` to authenticate payloads and exported
manifests. Keep it outside datasets and artifacts. Reopening requires the same key;
a wrong key fails. Authentication is not encryption, and a malicious editor of the live
database remains outside this local trust model. Exclude credentials from requests,
identities and receipts. Unsigned backups provide corruption detection only.

`prune(stream, kind='history'|'checkpoints', keep_last=1, limit=100)` deletes a bounded
number of superseded records and adjusts logical accounting atomically. It preserves
keyed history, at least the latest checkpoint, all quota reservations, effects and
session action authority. Protected `episode:` and `effect:` streams cannot be pruned.
It does not rotate databases or reclaim quota. A backup represents authority only as of
export: restoring it cannot account for later work in the original database. Stop and
reconcile the original authority before disaster recovery; independent copies do not
share a monotonic quota.

`JournaledEnvironment.publish()` stores a verified SQLite snapshot as an immutable raw
artifact and publishes an episode report with that raw reference, graph, materialized
view and request provenance. The snapshot contains the entire shared journal, including
other sessions, so publication is a privileged local export. Supplying `raw_inputs`
adds the original request receipt to lineage. Standalone `save_evaluator()` remains a
post-step persistence API; use the environment adapter for atomic step outcomes and
rollback. All coordination here is bounded, local SQLite work, not a distributed
scheduler or an out-of-core simulation.
