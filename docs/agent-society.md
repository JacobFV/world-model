# A society of grounded agents

Implementation of the closure `docs/agents-design.md` implies and never states: agents that meet.
Code: `worldmodel/agents/society.py` (the loop), `worldmodel/agents/observatory.py` (measurement),
`worldmodel/society_cli.py` (`wm society-run`, `wm society-report`).
Config: `examples/society-congress.json`. Tests: `tests/test_agents_society.py` (46).

Persons, firms and institutions were each individually grounded and none of them ever met. One
agent's act could not become another's percept, so nothing could emerge and there was nothing to
measure. This layer adds the meeting and the measurement, in that order, and the measurement is
the half that makes the loop worth running.

Nothing here is fitted, scored against a baseline, or `validated`. Every number the observatory
produces describes the behaviour of the *simulation*; none of it is agreement with the world. §6
says what that means concretely and where the labels live.

## 1. The loop

One `Society` holds a mixed population over one shared clock and one shared world. The world is
the published index (`data/world_evidence/index.sqlite`) exactly as `agents/grounding.py` reads
it; the agents hold *beliefs about* it.

One tick, in order:

| Phase | What happens |
| --- | --- |
| **1. deliver** | Every agent receives the utterances published in the **previous** tick that its audience membership lets it hear. They land as claims in a `heard:<id>` scope, never as ground truth — gossip with the speaker as its evidence source, the relay chain in its locator and a hearsay confidence below one; organizational speech through `disclosure.receive`, which carries its own provenance and its own audience lattice. |
| **2. appraise what was heard** | A bounded `think` pass over the delivered frontier, so a decision in tick *t* can rest on something a colleague said in tick *t−1*. |
| **3. perceive, think, decide, act** | A person runs the unmodified `Person.tick(window)`. A firm perceives the quarter whose `period_end` closed inside the window, runs its capital procedure, and discloses through its `DisclosureDesk`. An institution answers the requests the population made of it by issuing instruments off its `Docket` — each of those, and each disclosure, authorized before it happens. |
| **4. publish** | Whatever each agent said goes into the channel at tick *t*, for delivery at *t+1*. |
| **5. measure** | Divergence, belief provenance, motifs, regimes, legitimacy and decision structure are sampled into a `TickRecord`. The observatory reads only these. |

### The simultaneity decision

**An act published in tick *t* is delivered at the start of tick *t+1*. It is never visible in the
tick that produced it.** `Society.simultaneity == 'next_tick'` and
`Society.simultaneity_rationale` says why in the code as well as here:

every agent in a tick reads exactly the same channel contents whatever order the agents run in, so
**agent order can change what is written and can never change what is read**. Determinism then
needs only a deterministic order over writers, which is `(tier, entity_id)`. Same-tick visibility
would make an agent's percepts a function of its position in the loop — an undeclared hierarchy
that nobody chose and that no config can see. `docs/civ-sim/architecture.md` makes the same call
for cross-shard messages ("a one-tick delay"), and `research/civ_sim/minds.py` implements it as the
`pending` list a mind drains on its next think.

The consequence is visible in the measurements and is asserted in the tests: propagation latency
has a **floor of one tick**, and `test_acts_are_visible_in_the_next_tick_and_not_in_their_own`
checks that every delivery's tick is exactly its utterance's tick plus one.

### What an act is, and the two speech acts

An `Utterance` is a proposition, not a message format: `content` is a plain
`(subject, predicate, object)` triple and `claim_key` is that proposition's content-addressed
identity, independent of who said it or how often it was relayed. That is what makes "how far did
this claim travel" answerable.

**A person gossips; an organization does not.** The distinction is not decoration and the society
does not flatten it. `Utterance.formal` separates the two paths:

| Speaker | Speech act | Says | How it becomes a percept |
| --- | --- | --- | --- |
| person | informal | `(measure, <decision kind>, advancing\|contesting)` | the society writes `(measure, advanced_by\|contested_by, speaker)` and `(listener, heard_from, speaker)` into the listener's `heard:` scope, with the speaker as evidence source, the relay chain in the locator, and a confidence from `transmission.hearsay_weight` |
| firm | a `Disclosure` | a press release of `(org, regime, <regime>)`, and a filing of the first held claim it has not yet said | `disclosure.receive` |
| institution | an `Issuance` | `(committee, finding, measure)` on a measure the population asked it to move | `disclosure.receive` |

Organizational speech goes through **one** mechanism, `disclosure.receive`, and its audience
lattice is authoritative: a public filing reaches the regulator and the counterparties, a private
briefing reaches nobody else, and a listener outside the audience holds **nothing** rather than
holding it weakly. The society's own audience graph is only a first filter; reception is where the
lattice binds, so a listener the lattice excludes produces a recorded, unaccepted `Delivery` with
the reason on it instead of silence. A relay carries the speech object along, so a relayed private
briefing still reaches nobody outside its recipients.

Two consequences worth stating:

- **A rumour lapses; a filing does not.** Gossip claims are registered for hearsay expiry
  (`hearsay_ticks`, default 4); claims received through `receive` are not, because a filing and an
  instrument are on the record and the record does not go stale.
- **A refused organizational act is silence to the population and evidence to a reader.** A
  disclosure whose authorization does not hold is never made and an instrument that does not
  authorize is never issued — so nobody hears it — but it is on the `ActLog`, in the store as an
  `ultra_vires` claim, and in the tick record as a decision with `authority` set.

The decision *kind* is in a person's utterance and not just its direction, because a decision to
move a measure and a decision to work the committee holding it are different propositions — and
only the first is a request an institution can act on. Conflating them manufactured ultra-vires
attempts out of ordinary committee work in the first version of this module.

The instrument an institution issues in answer to a request is a **finding**
(`INSTITUTION_INSTRUMENT`). `agents.instruments` has a closed, authored instrument vocabulary with
no "report a measure out of committee" verb, and inventing one would mean editing a module this
work does not own; what matters is unchanged and grounded either way — `make_finding` must be a
power the acting seat holds, and the measure must be inside the role's declared jurisdiction,
which for a committee *is* the set of measures published as referred to it.

### What makes an agent move

`SocialPerson` is `Person` plus one rule pack (`social_rules`) and a `heard:` scope. Nothing in
`person.py` changed. Each social rule joins a claim the agent holds about *itself* with a claim
that arrived through the channel, so the appraisal's provenance runs through another agent's
decision and out to the published record that decision rested on:

- `a_colleague_contests_a_measure_i_sponsored` → `appraises (rebuff, measure)`, and at κ ≥ 0.35
  `wants (build_coalition, colleague)`;
- `a_colleague_advances_a_measure_i_sponsored` → `appraises (support, colleague)`;
- `i_heard_from_someone_who_opposes_me` → `appraises (threat, counterpart)`;
- `a_committee_refused_my_measure` → `appraises (rebuff, measure)` and `means (blocked_in_committee, …)`.
  This one only ever fires on the **fallback** institutional path, where the society announces the
  refusal itself. With the docket installed a refused instrument is never issued and therefore never
  said, so nobody hears about it — which is the more faithful behaviour and the reason the rule is
  kept rather than removed;
- `a_committee_made_a_finding` → `appraises (standing, measure)` when the measure is one the agent
  sponsored and `(exposure, measure)` when it is not. This one joins against the agent's own
  sponsorships **inside the rule body, keyed**, rather than as a second pattern: an instrument's
  statement carries the measure as a plain id and the agent's own `sponsored` claim carries it as a
  `Ref`, so a pattern join would silently never match. Keying the lookup is also the rule discipline
  `docs/civ-sim/architecture.md` §2 asks for.

`SocialFirm` adds one rule per published consolidation relation: a firm that hears `distressed`
from a counterparty **inside its own consolidation boundary** derives a
`(counterparty_distress, …)` corporate appraisal in its board scope. The premise pair is a tie the
catalog published and a regime another agent said, so the derivation crosses a role boundary *and*
an agent boundary, which is what `cross_role_integration` should be counting.

`SocialPerson._decide` also keeps the chosen `(kind, target)` as a plain value. The `decided` claim
is content-addressed, so a decision identical to the previous one retracts and re-tells the same
claim id and is forgotten in the same patch. The store is right to do that — the proposition did
not change — but the society still has to know what was chosen in order to say it out loud.

### Hearsay lapses

Nobody re-publishes a rumour. A heard claim is retracted and forgotten after `hearsay_ticks`
(declared, default 4). That is what keeps divergence bounded instead of monotonically growing, and
it is the mechanism `test_hearsay_lapses_and_is_forgotten` pins.

### The audience

Who can hear whom comes from published co-membership, not from a social-network model:

- a legislator is heard by the other legislators in the society who sit on a committee it sits on
  (`committee_member`), and by that committee when the committee is in the population;
- a firm is heard by whatever it is consolidated with in either direction
  (`directly_consolidated_by`, `ultimately_consolidated_by`);
- an institution is heard by its published members;
- plus any explicit `links` the config declares, which are labelled as declared.

Capped at `audience_cap` (32) listeners, because without a cap one committee membership makes every
act global and reach stops measuring anything.

## 2. Three tiers, and what promotion and demotion may do

Following `docs/civ-sim/architecture.md` §1 and §"Promotion and demotion":

| Tier | Representation | Behaviour |
| --- | --- | --- |
| **focal** | a full `tc.Store`: claims with evidence, scopes, rules, `choose`, `authorize` | perceives, appraises, decides, acts, relays, explains |
| **compact** | a `CompactState`: a state label, a viability number, two counters | hears and **relays** — which is what lets a claim cross a large population without every hop costing a store — and nothing else. It cannot appraise, decide or explain, and that is the whole difference |
| **cohort** | one `Cohort` row for many entities | absorbs reach as a count; relays nothing |

**Promotion invents nothing.** A promoted store is seeded from two sources and no others:

1. published records, through the same `seed_store` / `firm_records` / `committee_records` path used
   at construction, so a facet with no published record stays `Unknown` and is *named* in
   `PromotionReport.unknown`;
2. the shared event log, replayed through the *same* absorption path a live delivery uses. An
   informal utterance keeps the speaker as its evidence source, the relay chain in its locator, the
   fidelity it had, and the method `event-log:<origin>`; a formal one keeps the disclosure module's
   own `disclosed_by:<org>/<channel>` provenance. Either way a reader can tell a replayed statement
   from a published record at a glance, and `disclosure.disclosed_not_observed` answers it as a
   query rather than as a convention of this module.

Nothing carries over from the compact numbers. The state a compact agent tracked is not evidence
and does not become a claim. `PromotionReport.invented` is `0` by construction and is reported so
the claim is checkable rather than asserted; `test_promotion_invents_nothing` walks every claim in
a freshly promoted store and fails on any evidence source that is not `dataset:` (a published
record), `agent:` or `disclosure:` (a logged act) or the `charter:` that declared a seat.
Disaggregating a cohort member sets `sampled=True`, and an entity the catalog publishes nothing
about promotes to a store with **zero** claims rather than an invented one.

**Demotion records what was dropped.** A store is released, so the `DemotionReport` is the only
remaining record of what was in it: total claims, the count per predicate, the scopes, the number
of **explanation chains** lost (claims that carried a `derived_from`), the episodes and relations,
and the handful of values retained as compact state. `test_demotion_records_what_was_dropped`
checks that the per-predicate counts sum to the total, so the report cannot quietly under-report.

### The budget, measured

A claim costs about 1.2 KB in a `tc.Store` today (`CLAIM_BYTES`, measured in the civ-sim benchmark
and quoted in `docs/agents-design.md` §Scale). This module does not take that on trust:

- `measure_claim_bytes` pickles the live stores. On the worked run that is **307 B/claim** — a
  *lower bound*, because pickling shares nothing and compacts everything. It is reported and
  **never used for the ceiling**, because using it would inflate the ceiling.
- `measure_resident_bytes` re-seeds one focal agent under `tracemalloc` and divides the allocation
  delta by the claim count. On the worked run that is **1 548 B/claim**, i.e. the 1.2 KB figure is
  optimistic for a *legislator* store on this catalog by about 29 %.

`FocalBudget.bytes_per_claim` uses the resident measurement when there is one and the declared
1.2 KB otherwise. On the worked run, at **469.5 live claims per focal agent** and 1 548 B/claim, one
focal agent is ~0.69 MB, so:

| Memory budget | Focal ceiling at this run's claims/agent |
| --- | --- |
| 1 GB | ~1 477 agents |
| 8 GB | **11 816 agents** |
| 64 GB | ~94 500 agents |

That is a **memory** ceiling and is labelled as one; it says nothing about how many agents can be
ticked per second. The `--memory-gb` flag moves it, and a run whose agents hold more claims moves
it down proportionally. The civ sim's "a few thousand focal agents" is comfortably inside this,
which is the point of measuring rather than assuming.

## 3. The observatory

Four families. Each carries `NOT_ACCURACY`; the report carries the disclaimer at the top level, per
section and per labelled statistic — three copies, so a reader who lifts one number out of the
report lifts the label with it.

### Belief divergence

Per agent, per predicate, per tick: the **Jaccard distance** between the set of objects the agent
holds on a predicate and the set the index currently publishes. Both directions of disagreement are
also reported on their own, because they mean different things:

- `unsupported` — believed and not currently published: hearsay, and the standing beliefs
  `Person._consolidate` forms out of repeated episodes;
- `missing` — published and not believed: the perception bound doing its job.

And the other half, `belief_provenance`: every live claim in a focal store counted once by where it
came from — derived from premises, published (`dataset:`), **told** (`agent:` for gossip or
`disclosure:` for a filing or an instrument; either way it arrived through the channel), or the
agent's own bookkeeping. The per-predicate distance compares object sets; this says how much of the
whole store the record ever supported at all.

Divergence is reported as a **distribution** — count, mean, sd, quantiles, a histogram, the
per-agent and per-predicate breakdowns and the full series — and never as an error rate.
`DIVERGENCE_LABEL` says so in the payload: *"bounded perception, relayed hearsay, a one-tick
delivery delay and episodic consolidation all put belief and record apart. This is a distance
between two sets, not an error rate, and a zero would mean the agents had no inner life."*

### Information propagation

Per claim: `reach` (distinct agents delivered to at least once), `reach_fraction` over the rest of
the population, `latency_ticks` (first delivery minus the tick the claim was first said; floor 1,
see §1), `max_hops` and `min_fidelity`. Then the degradation table: deliveries and mean fidelity at
each hop count. The declared `RELAY_FIDELITY` shows up in it as `relay_fidelity ** hops`, which is
the point — the decay is *read off* the deliveries so a reader can check the assumption instead of
being told it.

Two counts keep the speech acts apart rather than pooling them: `formal_utterances` versus
`informal_utterances`, and `accepted` versus `not_held` — a delivery that held nothing, either
because the listener was a cohort absorbing reach as a count or because the formal audience lattice
excluded it. Overhearing is not modelled, so an excluded listener holds nothing rather than holding
it weakly, and the reason is on the `Delivery`.

### Affect and regime trajectories

The motif sequence per person and the regime sequence per firm, their run lengths (dwell in ticks),
and the hysteresis the affect geometry predicts. For each reciprocal motif pair the report prints
the observed traffic in both directions **next to the declared transition costs**, the asymmetry
`(n(a→b) − n(b→a)) / total`, which direction the costs make cheap, and whether the two agree.
`hysteresis_index` is the share of decided pairs where the cheap direction was the one actually
traversed. The pair table is the evidence; the index is a summary of it, and a short run gives few
transitions and therefore a coarse index. Firms have no motif and persons have no regime, so the
two stickiness numbers are over different populations and say so.

**Regime hysteresis is deliberately not measured**, and the report says so in
`regime_hysteresis: {measured: false, why: …}` rather than leaving the absence to be noticed.
`docs/corporate-cognition.md` §1 is explicit that a firm has no prototypes and no transition-cost
matrix — `regime()` applies declared thresholds and returns a test result, and a firm's stickiness
comes from covenant ratchets and disclosure rules, not from the shape of an affect space. There is
no declared cost to read an asymmetry against, and asserting one would invent exactly the mechanism
that layer keeps distinct from a person's. The regime sequences and run lengths are reported instead.

### Decision structure

- **persons**: total decisions, how many were `Unknown` and under which reason
  (`no_feasible_option`, `tie_within_margin`), and the chosen intentions by kind. `Unknown` is an
  honest non-decision, not a failure, and the label says so.
- **firms**: decisions, how many recorded a principal-agent divergence, and — kept deliberately
  apart — how many of those **changed the outcome** (the settled option is not the firm's own first
  preference), plus the distribution of `agenda_cost`. A recorded divergence that never changes an
  outcome is a divergence that did not act. `speech_acts_blocked` is counted separately from
  decisions, because what a procedure settled and what a firm was not authorized to say are
  different events and pooling them would make the divergence share meaningless.
- **institutions**: attempts, refusals, `ultra_vires_share`, the `holds`/`fails`/`unknown` split and
  the refusal reasons as short categories over the phrases `authorize` itself writes
  (`REFUSAL_CATEGORIES`), so a new reason shows up as `other` rather than being folded into an
  existing bucket.

Plus `tiering`: tier counts, cohorts, every promotion and demotion report, and the budget.

### Reproducibility and publication

`report()['reproduce']` carries the seed, the tick count, the whole config, the clock and the
command line that regenerates it. `Observatory.publish` goes out through
`worldmodel.artifacts.publish_report`, so a report is content-addressed and verifiable like any
other derived dataset, and `wm society-report <artifact>` renders it back.

## 4. The worked run

```sh
wm society-run --config examples/society-congress.json --ticks 6 --summary --measure-resident
```

Thirteen members, all bound to entities this index publishes:

| | |
| --- | --- |
| focal persons | `bioguide:A000375` Jodey Arrington (chair, House Budget), `bioguide:B001296` Brendan Boyle (ranking member), `bioguide:S000185` Robert Scott, `bioguide:M001177` Tom McClintock and `bioguide:C001118` Ben Cline — the last two sit on **both** Budget and Judiciary |
| compact person | `bioguide:D000399` Lloyd Doggett — hears and relays, no store |
| focal institutions | `congress:committee:hsbu00` House Budget, `congress:committee:hsju00` House Judiciary |
| focal firm | `sec:cik:0001489393` LyondellBasell Industries N.V. — roles and holders from real SEC insider filings, 8 real quarters from `sec_company_assets` |
| compact firms | four real GLEIF subsidiaries |
| cohort | four more published GLEIF subsidiaries, as one row |
| scheduled | a subsidiary promoted at tick 2, a focal member demoted at tick 3 |

What is grounded and what is declared: the members, their seats, their sponsorships, the
committees' **jurisdictions** (`referred_to_committee` — which committee may report a measure is a
published fact), the firm's filings and its consolidation tree are all published. The firm's
objective, the CFO's objective, the two capital options' effects, the delegation of authority, the
instrument vocabulary and the three `links` are **declared assumptions** and are labelled as such
in the config and in `agents/firm.py` / `agents/instruments.py`.

Measured over six 90-day ticks from 2025-01-01, seed 7. 100 utterances — **37 formal** (the firm's
press releases and filings, the committees' findings) and **63 informal** (what the legislators
said) — and 258 deliveries, of which 244 were held and 14 held nothing because the listener was the
cohort.

**Divergence.** 135 samples, mean 0.4163, sd 0.3525, and the shape is the interesting part: the
quantiles run `p0 = 0.00`, `p25 = 0.00`, `p50 = 0.65`, `p75 = 0.74`, `p100 = 0.81`. It is
**bimodal, not noisy**, and the per-predicate breakdown says why:

| predicate | mean distance | why |
| --- | --- | --- |
| `serves_on` | 0.000 | a handful of committee seats, all seeded, all still published |
| `opposed_by` | 0.000 | nothing believed and nothing published |
| `sponsored` | 0.601 | hundreds of sponsorships against a seeding budget of a few dozen |
| `cosponsored` | 0.736 | the same, harder — this is where the perception bound bites |
| `received_support` | 0.744 | the same, over FEC support edges |

None of that is error. It is `missing`: the perception bound doing its job. Belief provenance closes
the loop — **67.6 %** of a focal store's first evidence is a published record on average, and
**14.2 %** arrived through the channel. The spread is the point: the freshly promoted subsidiary,
about which the catalog publishes almost nothing, sits at 12.5 % from the record and **87.5 %** from
the channel. It is an agent that knows what it was told and almost nothing else, and the report says
so in a number rather than in a caveat.

**Propagation.** 15 distinct claims, mean reach 0.394 of the rest of the population and max 0.75;
mean latency 1.169 ticks with a floor of 1 (see §1) and a tail to 2. Degradation comes out as
`1.0000` at 0 hops over 126 deliveries and `0.8000` at 1 hop over 132 — exactly `0.8 ** hops`,
which is what the declared constant says and is now visible instead of implied.

**Hysteresis.** Six ticks give only a handful of motif transitions, so the index is coarse and is
labelled as coarse. Both pairs where the declared costs are asymmetric went the cheap way —
`boredom → desire` twice against zero returns (cost 0.10 out, 0.45 back) and `fear → anger` once
against zero (0.10 out, 0.60 back) — for an index of **1.0 over 2 decided pairs**; the three pairs
with symmetric costs are correctly reported as `agrees = None` rather than counted as agreement.
Motif mean dwell 2.45 ticks against a regime mean dwell of 2.75.

**Decision structure.** 27 person decisions, 2 of them `Unknown` (7.4 %), both
`tie_within_margin` — two intentions the chooser could not tell apart, recorded as a non-decision
rather than broken by a coin flip. The firm decided in all six ticks, recorded a CFO-versus-firm
divergence in all six, and in all six the settled option was **not** the firm's own first
preference: the agenda mechanism from `docs/corporate-cognition.md` §3 acting, with an
`agenda_cost` of 0.0000 because this issuer is comfortably inside both declared boundaries — the
same number and the same reason as the single-firm worked example. Alongside those six there are
four **blocked speech acts**, all `said_nothing`, all the promoted subsidiary's: no role in its
charter is declared to hold `approve_disclosure`, because the catalog publishes no bylaws for it, so
it does not speak on undeclared authority. The two counts are reported separately, because pooling
them would make the divergence share meaningless.

The institutional result is the one worth reading twice. **20 attempts, 5 held, 15 refused, all 15
`outside_declared_jurisdiction`.** The Budget chair keeps pushing
`congress:bill:119-hconres-15`; the index publishes that measure as referred to **Judiciary**, so:

```
tick 1  hsbu00  refused  congress:bill:119-hconres-15   fails   (outside the declared jurisdiction of chair)
tick 1  hsju00  finding  congress:bill:119-hconres-15   holds
```

The same request, two committees, opposite answers, and the thing that decides is a published
`referred_to_committee` edge and nothing else. Judiciary's finding is an `Issuance`, so it reaches
the population through `disclosure.receive` with provenance that says it was served by that
committee; Budget's refusal reaches nobody and is on the record twice, in the `ActLog` and as an
`ultra_vires` claim.

**Tiering and budget.** 8 focal, 5 compact, 1 cohort; 3 756 live claims at 469.5 per focal agent and
1 548 B/claim resident → a focal ceiling of **11 816 agents** at 8 GB (§2). The promotion at tick 2
added 1 claim from published records and 1 replayed from the event log, invented 0, and named four
facets `Unknown` including the subsidiary's financials. The demotion at tick 3 dropped 837 claims
and 202 explanation chains, which is the only record that store ever existed.

## 5. Seams

Three sibling modules owned by other work — `agents.transmission`, `agents.disclosure` and
`agents.instruments` — are reached through four seams. They are resolved lazily by name through
`society.seams()`, every field may be `None`, and `Society.to_json()['seams']` reports which are
installed so a report never silently depends on one. Every fallback is exercised by a test, so the
society runs on a checkout that has none of them.

| Seam | What the society uses it for | Fallback when absent |
| --- | --- | --- |
| `transmission.hearsay_weight(trust, fidelity, score=, upstream=)` | the confidence on a gossip claim. It caps hearsay strictly below direct observation and lets the chain enter through `upstream` rather than as an exponent bolted on, which is a better definition than this module's own | the bare fidelity, as a `relayed` score — labelled so nobody reads it as a calibrated probability |
| `disclosure.DisclosureDesk` | a firm's utterance **is** a `Disclosure`: formal, dated, role-attributed, audience-scoped, on a permanent register, and authorized before it is made | a plain `(org, regime, <regime>)` utterance from the firm's own `CorporateReading` |
| `instruments.Docket` + `instruments.COMMITTEE_INSTRUMENT_POWERS` | an institution's answer to a request **is** an `Issuance`, authorized against its published jurisdiction; the docket widens the charter with the declared instrument verbs and nothing else | a bare `InstitutionalAct(power='report_measure')` and an `authority` utterance |
| `disclosure.receive` | the one mechanism by which organizational speech becomes another agent's percept, and the authoritative audience lattice | the gossip path, which has no lattice |

The wiring is tested in both directions: `test_the_optional_seams_are_reported_present_or_absent`
asserts the reported flags match what `seams()` actually resolved,
`test_the_transmission_seam_weighs_hearsay_below_direct_observation` checks the seam *and* the
fallback, and `test_a_firm_speaks_through_the_disclosure_desk` /
`test_an_institution_answers_with_an_instrument` pin the formal path.

**What the society deliberately does not duplicate.** `agents.speech`, `agents.transmission` and
`agents.lexicon` own a full person-to-person speech layer of their own — registers per chamber and
party, surface sentences, misunderstanding (`lexicon.slip`), trust from published ties, evidence
adjudication and `transmission.propagate`. That is a sibling mechanism with its own clock
discipline (`tell` delivers synchronously), and re-routing this module's one-tick channel through
it would be a rewrite rather than a composition. The society composes with it today at the
confidence seam and otherwise keeps its own bounded gossip path; putting a person's utterance on
the wire as a `lexicon` sentence, so that relay degradation is *meaning* loss rather than a
confidence multiplier, is the obvious next composition and is not done here.

## 6. What this does not claim

- **No agreement with the world.** Every statistic measures the simulation. `NOT_ACCURACY` is
  attached to each family, `EPISTEMIC_STATUS` is `simulation_internal_behaviour`, and
  `validated: false` is in the payload. Divergence in particular is *expected* and carries
  `DIVERGENCE_LABEL`; reading it as an error rate is reading it backwards.
- **Nothing is fitted.** `RELAY_FIDELITY`, `RELAY_FLOOR`, `HEARSAY_TICKS`, `AUDIENCE_CAP`,
  `UTTER_PER_TICK`, `RELAY_PER_TICK`, `REPLAY_LIMIT`, `CLAIM_BYTES`, `INSTITUTION_INSTRUMENT`,
  `ADVANCING`/`CONTESTING` and the social rule pack's scores are declared at module level so they
  can be argued with. They are modelling choices.
- **No language model anywhere in the loop**, as the design contract requires.
- **The affect reading inside `Person.tick` covers the perceptual thought only.** Hearsay is
  integrated and appraised in phase 2, before the perceptual tick, so it reaches *beliefs,
  appraisals and decisions* in the same tick; the belief change it caused is reported separately as
  `TickRecord.heard_derived` rather than folded into that tick's `arousal`.
- **Evidence timestamps are not deterministic.** `tensorcode.cognition.think` stamps a derived
  claim's evidence with wall-clock time. Claim *ids* are content-addressed, so decisions, motifs,
  divergence and everything the observatory reports are reproducible from the seed; no report field
  carries a rule's evidence timestamp.
- **A short run is a short run.** Six ticks give a handful of motif transitions and about fifteen
  distinct claims. The hysteresis index and the reach distribution are coarse at that length and
  say so in the payload.
- **The legitimacy reading a tick records is taken before that tick's acts.** `Institution.read`
  runs at the top of the institution's turn, so the trajectory shows erosion one tick after the
  ultra-vires attempt that caused it. The attempt counts are exact; only the tick they are
  attributed to lags.
- **The society does not model overhearing, and does not model lying.** A listener outside a
  formal audience holds nothing; a speaker says a subset of what it holds and the gap is measured
  (`DisclosureDesk.gaps`), but there is no falsity flag anywhere in this layer.

## 7. Running it

```sh
# a fixture society (no index needed beyond the test fixture) — both modules, skipping cleanly
# when tensorcode is absent
python3 -m unittest tests.test_agents_society -q

# with the optional substrate from a local checkout
python3 -m unittest tests.test_agents_society -q

# the worked run
wm society-run --config examples/society-congress.json --ticks 6 --summary
wm society-run --config examples/society-congress.json --ticks 6 --publish
wm society-report agent_society_report@<version>
wm society-report agent_society_report@<version> --section propagation
```

`--no-series` drops the per-agent divergence and provenance series for a smaller artifact;
`--measure-resident` turns on the `tracemalloc` measurement; `--memory-gb` moves the focal ceiling;
`--index` and `--cache-mb` point at and tune the read-only index; `--seed` overrides the config
seed; `--json`/`--section` on `society-report` print the stored payload instead of the summary. The
real-data tests skip unless this checkout has `data/world_evidence/index.sqlite`.

One operational note, not about this module: `worldmodel.artifacts.publish_report` calls
`provenance.capture_code`, which refuses to publish if any `worldmodel/*.py` changed after import.
Running `--publish` (or the whole suite) while another process is editing the package therefore
fails with *"Implementation changed after import"*. Run it from a snapshot copy of the tree, or when
the tree is quiet.
