# Grounded cognitive agents

A design contract. Implementation lives in `worldmodel/agents/`.

## Why this exists

The catalog holds 1.37 billion records about real firms, people, places and institutions.
Nothing in it *acts*. The process registry can move aggregates through time, but its
"human_decision" picks the first item from a list, and its "business_behavior" is a
placeholder. There is no model of an entity that perceives, believes, wants, decides and
remembers.

This layer adds one. It is **not** a fitting exercise. Nothing here is required to beat a
forecasting baseline; the point is structured cognition whose internals can be inspected and
explained, grounded in entities that actually exist.

## The cognitive substrate

We use the tensacode substrate (`~/Documents/tensacode`, zero runtime dependencies, same
author) rather than reinventing it. The pieces we rely on:

| Primitive | What it gives us |
| --- | --- |
| `Store` of claims | Beliefs with evidence, scope and provenance; retraction withdraws what depended on it |
| `Fragment` / `integrate` | Perception as a snapshot; what is no longer seen retracts |
| `Rule` / `think` | Appraisal as bottom-up rules; every derived claim cites its premises |
| `explain` | Why an agent believes something, all the way to observations |
| `choose` with constraints | Decision under norms, authority and feasibility; `Unknown` is an honest non-decision |
| `@action` / `authorize` | Acts with declared effects; institutional acts checked against role authority |

**Grounding is the addition.** In the civ sim, the world is invented. Here an agent's percepts
come from the unified graph: a firm's real financials, a legislator's real committee seats, a
county's real employment. The world holds what sources published; the agent holds *beliefs
about* it. Divergence between the two is where error, rumour and ideology live — and that
divergence is the interesting part, not a defect.

## Three kinds of agent, deliberately different

The mistake to avoid is giving a corporation a human mind with the labels changed.
The ascription axes from the source material give us the principled split: **agency (αA) and
phenomenality (αP) dissociate**. A corporation can be highly agentive and not experiential at
all; that is not a simplification, it is the claim.

### 1. Person

Closest to the civ-sim model. Needs, episodic memory with decay, kinship and bonds,
consolidation of repeated episodes into semantic belief, theory of mind about others.

Affect is read as structural measures over the agent's own processing, never as a declared
mood: valence (movement along a viability gradient), arousal (rate of belief update),
integration (how much of what is derived crosses modules), effective rank (how many concerns
are active), counterfactual weight (share of processing on non-actual futures), and
self-salience split into attentional and causal. A named motif (fear, grief, flow…) is the
nearest prototype in that space, with asymmetric transition costs — fear slides to anger more
easily than back.

αA high, αP high.

### 2. Firm

A firm is **agentive but not experiential**. It does not feel hunger or shame. It has:

- **Roles, not a single self.** Board, executive, operating units. Decisions are procedures
  over roles, and which role decides is part of the model.
- **Institutional memory that outlives its members.** Claims survive the departure of whoever
  formed them. A person's episodic decay does not apply; policy, precedent and contract do.
- **Principal-agent divergence.** The firm's objective and a role-holder's objective are not
  the same claim set, and may conflict. This must be representable, not smoothed away.
- **Corporate affect analogues**, which are *not* emotions:
  | Person | Firm |
  | --- | --- |
  | valence | solvency/viability gradient — movement toward or away from distress |
  | arousal | rate of revision of its own commitments |
  | fear | liquidity pressure and covenant proximity |
  | shame | reputational and regulatory exposure |
  | attachment | contractual and ownership coupling |
  These are read the same structural way, from the firm's own processing, and are labelled
  with corporate names so nobody mistakes them for feelings.

αA high, αP near zero. An agent perceiving a firm should model it with the agent template and
apply no harm constraint weighted by phenomenality.

### 3. Institution

Legislatures, agencies, regulators, committees. Like a firm in having roles and procedure,
unlike it in having **authority and legitimacy** rather than solvency as its viability
measure. Institutional acts must pass `authorize` against declared role authority; an act
without authority is refused and recorded, not silently performed.

## Grounding contract

An agent is bound to an entity id from the unified graph (`sec:cik:…`, `lei:…`,
`bioguide:…`, `geo:US:county:…`). At construction:

- seed claims come **only** from published records, each carrying its dataset, version and
  record id as evidence;
- absent history stays absent — `Unknown`, never invented;
- what the agent believes and what the graph says are separately queryable.

Perception is bounded: an agent sees what its position, role and relationships give it access
to, not the whole catalog.

## What this layer does not claim

- No claim that anything is experienced. The affect readings are structural measures over
  computation, and the corporate ones are deliberately named to prevent the slide.
- No claim of predictive validity. These agents are not fitted to outcomes and are not
  scored against baselines. When that changes, it goes through the estimation layer like
  anything else, and until then nothing here is `validated`.
- No language model in the tick loop.

## Scale

The civ sim's tiering applies: a few thousand focal agents with full claim stores, everyone
else as compact numeric state, distant populations as cohorts. Promotion seeds a store from
the row and the event log; demotion consolidates and records what was dropped. A claim costs
about 1.2 KB today, so focal counts are budgeted accordingly.

---

## Implementation notes: person (`worldmodel/agents/`)

Appended by the implementation of the grounding layer and the person agent. The contract above
is unchanged; this records what the catalog actually supports and the decisions that were not
forced by the contract.

### Modules

| Module | What it holds | Needs tensacode |
| --- | --- | --- |
| `agents/__init__.py` | `load_tensacode()` / `tensacode_available()`, mirroring `worldmodel.backends`; lazy attribute access so a missing substrate costs an `ImportError` only when something that needs it is touched | no |
| `agents/affect.py` | the seven readings, the twelve prototypes, the asymmetric transition costs, `soft_viability`, `entropy_rank`, the predicate→module table | no (pure Python, no numpy) |
| `agents/grounding.py` | `EvidenceIndex`, `Horizon`/`PerceptSpec`/`MetricSpec`, `RollCalls`, `perceive`, `seed_store`, `Grounding` | yes |
| `agents/person.py` | `Person`: the tick loop, appraisal rules, episodes, consolidation, theory of mind, `choose` | yes |
| `agents_cli.py` | `wm agent-inspect`, `wm agent-explain` | yes |

The optional extra is `agents` (`pip install "worldmodel-substrate[agents]"`). tensacode is not
on PyPI yet, so a local checkout installs with `pip install -e <tensacode>/tensacode/python`, or
runs with `PYTHONPATH=<tensacode>/tensacode/python/src`. Without it the core imports normally and
every agent test skips, exactly like the numpy `fast` extra.

### What the catalog actually publishes about a legislator

Only five edge predicates ever have a `bioguide:` subject — `sponsored_measure`,
`cosponsored_measure`, `committee_member`, `holds_role`, `same_as` — and the reverse direction is
almost empty. Everything else reaches a legislator through the **resolved identity cluster**:
money via `fec:candidate:*` (`supports_candidate`, `opposes_candidate`,
`authorized_committee_of`), ideology and party via `icpsr:*` (`party_affiliation`,
`congressional_service`, the DW-NOMINATE and Nokken-Poole observations). `LEGISLATOR` therefore
perceives over the cluster, not over the single id.

Individual **roll-call positions** are published, but as `kind='event'` records with no subject,
object or entity id, so no index reaches them. `RollCalls` addresses them by primary key on the
published id template `voteview_rollcalls:positions:{S|H}{congress}:{rollnumber}`, walking roll
numbers downward from a bisected maximum: about twenty lookups to find the end of a session, ten
to find the last roll before a date, then at most `roll_call_scan` (60) more. That is the only
place in this layer that reads a table by key rather than by index, and it is bounded.

Two facets are honestly **Unknown** on this catalog and are reported as such rather than filled
in: `contribution_amounts` (`fec` publishes `supports_candidate` as a per-cycle boolean, and all
three `fec_individual_contributions*` datasets contributed zero records to the default index) and
`lobbying_contacts` (`lda_lobbying` carries no `bioguide:` linkage).

### The perception bound, precisely

`Horizon` + `perceive` enforce, in this order: the resolved cluster within `hops`; only the
role's declared predicates and metrics; `observed_at <= known_at` when a knowledge horizon is
pinned (`observed_at` is the *ingest* clock of the unify run, so this defaults to off and real
time is carried by `valid_from`/`valid_to`); dated assertions only inside the tick window, with
undated ones as standing condition; at most `edge_scan` (400) index rows read; then salience
ranking, and a cap of `per_tick` (24, inside the civ sim's 12-30 band) with **no single
predicate allowed more than `per_predicate_share` (a quarter) of working memory** — without that
quota a busy senate session fills every slot with roll calls and the agent stops seeing its
committees or its money. `Perception.bound` reports rows read, percepts formed, kept and dropped,
so the bound is auditable rather than implicit.

Salience is `base weight × novelty × recency`, with an `event_boost` for something that happened
inside the window and a second boost when a measure the agent put its name to reached a terminal
stage *and the published `latest_action` date falls in the window*. That last clause is what
stops a bill enacted years ago being re-announced as this quarter's news.

### Decisions not forced by the contract

- **Viability** for a legislator is five authored soft margins read from the agent's own store —
  office, committee standing, legislative efficacy, floor coalition, campaign support — combined
  with `soft_viability` (`-logsumexp(-k·margin)/k`). They are declared in
  `Person.viability_margins` rather than fitted; the contract forbids fitting here.
- **The first tick appraises the whole seeded store.** `think` is semi-naive, so without an
  explicit "awakening" frontier the seeded structure would never be appraised at all — only the
  parts a later percept happened to re-present.
- **Ascription** is a per-namespace table: `bioguide`/`icpsr` are (0.9, 0.9), `fec` and `congress`
  committees are agentive and not experiential (0.85/0.7 agency, 0.02 phenomenality), bills and
  roll calls are (0.05, 0.0). Targets at or above agency 0.6 get a `model:<id>` scope holding what
  the agent takes them to want and believe; `harm_forbidden_by_ascription` is weighted by
  *phenomenality*, which is why opposing a PAC is choosable and campaigning against a person is
  refused rather than discounted.
- **Theory of mind draws on standing relations, not only on this tick's percepts.** You model the
  counterparts you are in a relationship with, not only the ones in front of you; a percept still
  outranks a standing relation, and a relation the agent has a stance about always beats an
  incidental attribute percept about the same entity.
- **`Unknown` arrives two ways** and both are recorded as `(me, decided, ('unknown', reason))`:
  `no_feasible_option` when the constraints exclude everything, and `tie_within_margin` when two
  options fall within the chooser's 0.02 margin. Ties are not broken by a coin flip.
- **Determinism** is structural: percepts are ranked by `(-salience, predicate, object, record_id)`
  and no jitter is added to utilities. `seed` fixes the per-agent perceptual axes (κ, γ) only, so
  two runs of the same seed are the same agent.

### Inspecting one

```sh
# what it believes now, plus three quarterly ticks
python3 -m worldmodel agent-inspect bioguide:K000367 --ticks 3 --window-days 90

# why it believes something, down to the published record ids
python3 -m worldmodel agent-explain bioguide:K000367 'decided' --ticks 3 --depth 8
python3 -m worldmodel agent-explain bioguide:K000367 'appraises rebuff'

# what it holds that the graph does not currently support
python3 -m worldmodel agent-inspect bioguide:K000367 --diverge serves_on
```

Both are read-only and print JSON. `agent-explain` returns the indented support chain *and* a
flat `records` list of every published record id the chain bottoms out in, so the answer to "why
does this agent think that" is a set of dataset versions and record ids, not a narrative.
