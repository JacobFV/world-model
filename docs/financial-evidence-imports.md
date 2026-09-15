# Dated financial evidence and reviewed contracts

These interfaces accept explicit source evidence. They do not acquire an entitled
security feed, infer bilateral loans from aggregate SEC balances, or merge issuers by
name. Files under `examples/financial_imports/` are **fictional regression fixtures**.
Imported documents and feed rows inherit the rights in their raw receipts; MIT covers
the adapter code, not third-party data rights.

## Issuer, security and listing identities

`FinancialIdentityIndex(entities, assignments, links=())` in
`worldmodel.financial_identity` is a bounded candidate index. Entities declare exactly
`id`, `kind` (`issuer`, `security`, `listing`) and `label`. Assignments declare
`entity_id`, `namespace`, `value`, optional `scope`/`currency`, explicit `valid_from`
and `valid_to` (null means unknown), `observed_at` and nonempty `evidence`.

- CIK/LEI assignments identify issuers; ISIN/FIGI identify securities.
- Tickers identify listings with an explicit MIC scope and currency. Reuse after
  delisting is a new dated assignment, not a rename of the previous issuer.
- `source_local` identifiers require a snapshot-specific scope. A row number or
  snapshot-local identifier never automatically becomes a permanent security ID.
- `issuer_security` and `listing_security` link different kinds. `successor` links
  issuers and `share_class_change` links securities without merging either pair.
- `equivalent` requires endpoints of the same kind and stays review evidence.

Links declare `kind`, `subject`, `object`, validity bounds, `observed_at`, and
`evidence`. `resolve(namespace, value, at=..., known_at=..., scope=...)` returns all
visible candidates and validity evidence. Contradictory assignments and unknown dates
remain explicit. An optional `decision={selected, reviewer, reason}` records a choice
without deleting candidates or conflicts. `search(name, kind=...)` returns same-name
entities separately. `relationships(entity_id, at=..., known_at=...)` exposes dated
links. Each input collection is bounded to 10,000 rows.

```python
result = index.resolve('ticker', 'REUSE', scope='XNYS',
                       at='2023-01-01', known_at='2023-01-02')
assert result['status'] in ('resolved', 'ambiguous', 'temporal_unknown',
                            'conflicting_assignments', 'not_found')
```

## Strict authorized price and corporate-action imports

`data/market_prices/pipeline.py` and `data/market_corporate_actions/pipeline.py` accept
canonical JSONL only. `worldmodel.financial_feeds.validate_feed_row(kind, row)` is the
same closed-field validator used by both. No network request or adjustment calculation
occurs. Adapt an actually authorized provider response into this declared format and
retain that response as source evidence; these contracts do not grant feed access.
No API credentials are needed for file import. Remote sampling remains explicitly
blocked until actual feed access and terms are configured.

Both row types require `source_record_id` (revision-specific), `source_published_at`,
`observed_at`, and an `identity` object with exactly:

| Field | Contract |
| --- | --- |
| `issuer_id`, `security_id`, `listing_id` | Distinct explicit namespaced entity IDs |
| `issuer_identifier` | `{namespace: sec_cik or lei, value}` |
| `instrument_identifier` | `{namespace: isin, figi or source_local, value}`; never ticker |
| `venue_mic`, `ticker`, `currency` | Explicit listing MIC, symbol and three-letter currency |
| `valid_from`, `valid_to`, `known_at` | Dated identity interval covering the event; knowledge available by observation |
| `source_snapshot` | Explicit provider snapshot identity; scope for source-local IDs |

A quote additionally requires:

- `quote_at`, `valid_to`, `price`, `currency`, `price_type` (`open`, `close`, `last`, `nav`).
  `valid_to` is the feed's explicitly declared quote expiration; it must fit the
  identity interval. The adapter never extends a point quote to the full listing life.
- `session`: `name`, `calendar`, `calendar_version`, IANA `timezone`, `open_at`, `close_at`.
  The quote timestamp must lie within these declared session boundaries. The adapter
  validates the supplied interval; it does not independently verify holiday schedules.
- `adjustment`: `policy` (`unadjusted`, `split_adjusted`, `total_return`), `as_of`,
  `corporate_action_ids`, `method`, `version`. Adjustment knowledge must be available
  by `observed_at`. These fields describe the imported value; the adapter does not
  assert it has independently reproduced the provider's adjustment factors.

An action instead requires `action_type`, `effective_at`, `announced_at`, `status`
(`announced`, `confirmed`, `cancelled`) and exact `terms`:

| Type | Terms |
| --- | --- |
| `split` | Positive `numerator`, `denominator` |
| `dividend` | `amount`, `currency`, `ex_date`, `record_date`, `payment_date` |
| `merger`, `share_class_change` | `successor_issuer_id`, `successor_security_id`, positive `exchange_ratio` |
| `delisting` | Explicit `reason` |

Corporate-action claims use a one-microsecond interval at `effective_at`, preserve
announcement/status, and do not silently modify identity mappings or price histories.
Unknown successor/exchange terms need a different unresolved-candidate source, rather
than invented values in this strict import contract. The imported identifier mappings
are source assertions, not independent issuer-equivalence verification.

Imports are bounded to 100 files, 16 MB aggregate input, and 10,000 rows. Every
normalized entity and claim retains an exact raw row locator. Use separate revision
IDs for corrected source records. The current declarations publish `normalized` stages.

## Documents → candidates → explicit reviewed obligations

The two adjacent datasets are deliberately distinct:

1. `contract_candidates` consumes JSONL documents with exactly `text`, `sha256`
   (UTF-8 text hash), `accession`, `url`, and `observed_at`. At least accession or URL
   must be supplied. It publishes a `candidates` JSONL stage.
2. `reviewed_obligations` consumes a pinned candidate stage and a separate authorized
   JSONL review file. It publishes a typed `reviewed` stage.
3. `market_obligations` is a compatibility view of that reviewed evidence.

`extract_candidates(document)` recognizes labelled lines and simple two-column text
tables (`Principal: 1,000` or `Principal | 1,000`). Supported labels are borrower,
lender, principal, currency, annual rate, rate type, maturity, collateral, seniority,
schedule, effective date and supersedes. Each candidate preserves document hash and
accession/URL, section, line, character offsets, optional table row/column, exact quote,
method/version/model, confidence and candidate status. Character offsets index Python
Unicode text, not UTF-8 byte offsets. Effective dates remain unknown until reviewed.
The fixed 0.7 confidence is an extractor annotation, not calibrated legal accuracy.

This bounded adapter does not parse arbitrary prose, PDF layouts, OCR or scanned
agreements. Supply faithful extracted text and keep its source-document relationship
in the raw receipt. Text is capped at one million characters/four million UTF-8 bytes
per document; aggregate candidates are capped at 10,000. Amendments, duplicate terms,
multi-currency clauses and undated clauses remain separate candidates.

`review_obligation(candidates, review)` validates the exact review format illustrated
by `fictional_review.jsonl`. Reviews include candidate IDs grouped by field, explicit
resolved parties with resolution evidence, exact terms, schedule, collateral, seniority,
completeness, disposition, reviewer/time, validity, and superseded obligation IDs.
No name search automatically resolves the borrower or lender. Candidate content hashes,
field matches, numeric/date values and knowledge cutoffs are checked. Missing amount,
unknown party or conflicting currencies cannot become approved simulation terms.
An incomplete/rejected/superseded review remains a `contract_review` audit claim.

Only a complete approved USD obligation with explicitly supported `bullet_act365`
schedule, `unsecured` collateral and `pari_passu` seniority emits `obligation_terms`
for the current exposure model. Other reviewed structures stay audit evidence. The
model's terminal proportional net settlement remains a scenario approximation; it
does not reconstruct contractual interim liquidity, collateral priority or default
probabilities. Contract terms never supply a counterparty's missing initial cash.

Superseded reviews must be included in the same bounded review import and have
explicit nonoverlapping validity. The pipeline rejects overlapping amendments rather
than silently truncating old terms using later knowledge. Preserve earlier immutable
review versions for earlier knowledge cutoffs. Network completeness remains unknown.

## Reproducible fictional walkthrough and materialized query

Use `wm --data-root /tmp/worldmodel-fictional` in place of `wm` below to keep these
fictional fixtures in an isolated runtime root. Replace printed
hash placeholders with the exact references returned by each command:

```sh
wm import contract_candidates examples/financial_imports/fictional_document.jsonl --source-json '{"publisher":"fictional fixture","license_id":"MIT"}'
wm run contract_candidates --raw contract_candidates@DOCUMENT_HASH
wm import reviewed_obligations examples/financial_imports/fictional_review.jsonl --source-json '{"publisher":"fictional review fixture","license_id":"MIT"}'
wm run reviewed_obligations --input contract_candidates/candidates@CANDIDATE_VERSION --raw reviewed_obligations@REVIEW_HASH
wm run market_obligations --input reviewed_obligations/reviewed@REVIEWED_VERSION

wm import market_prices examples/financial_imports/fictional_quote.jsonl --source-json '{"publisher":"fictional fixture","license_id":"MIT"}'
wm run market_prices --raw market_prices@QUOTE_HASH
wm materialize market_prices/normalized@PRICE_VERSION --request examples/financial_imports/quote_view.json
```

The materialized quote view contains the imported object value at two times inside its
explicit validity interval, with source IDs, currency/session/adjustment policy, origin
and raw evidence retained. It runs no price forecast. A later query outside that interval
fails for missing state rather than silently carrying the quote forward. The same
mechanism can request `contract_review` or `obligation_terms` on a reviewed obligation
within its explicit interval. Actual exposure runs additionally require separately
pinned cash evidence and the scenario's explicit shock/horizon assumptions.

## Dated bounded movement plans

`worldmodel.transport.route(network, request)` now accepts edge `valid_from`/`valid_to`,
`closures` (lists of those bounds), `capacity_windows` (bounds plus `capacity`) and
`duration_range_seconds: [lower, upper]`. Whole traversal must fit validity and avoid
closures or insufficient capacity. Waiting for reopening is allowed; missed scheduled
departures remain missed. Overlapping capacity windows apply conservatively.

`duration_policy` defaults to `upper_bound`; `nominal` is an explicit alternative.
Bounds are not probabilities. Returned legs retain both bounds and the chosen duration.
`network.transfers`, when supplied, is an explicit list of `{node, from_mode, to_mode,
minimum_seconds, valid_from, valid_to}`. A mode change without a matching active transfer
is infeasible. An empty list forbids mode changes. Explicit same-mode rules (for example air-to-air connections) are also enforced; unlisted same-mode continuation has zero transfer time. Omitting the field preserves legacy
zero-interchange-time assumptions, which are stated in the result. Transfer duration
must fit its rule's validity; subsequent service waiting can continue after transfer.
The search retains distinct incoming modes, avoiding invalid dominance across modes.

Optional `geographic_bounds: [west, south, east, north]` requires every node's longitude
and latitude to lie inside that explicit local box. No topology is inferred beyond
supplied edges. Network, departure, transfer and interval counts have hard bounds in
addition to the existing search label/expansion limits. Capacity is a feasibility check,
not a reservation, throughput simulation or congestion estimate. See `dated_route.json`.
A route remains a plan: aircraft registration, vessel ownership or corporate association
never proves a person boarded. Passenger/crew evidence is still a separate unresolved
question unless explicitly supplied by an appropriate source.
