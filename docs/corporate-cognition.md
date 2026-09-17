# Corporate cognition

Implementation of `docs/agents-design.md` sections 2 (Firm) and 3 (Institution).
Code: `worldmodel/agents/roles.py`, `corporate_affect.py`, `firm.py`, `institution.py`.
Tests: `tests/test_agents_firm.py` (45), `tests/test_agents_institution.py` (16).

The thing this layer has to avoid is a human mind with the labels changed. What follows is the
list of places where the structure, not the vocabulary, is different — and the real firm the
whole thing was built against.

## 1. What is structurally different from a person

| | Person (`worldmodel/agents/person.py`, `affect.py`) | Firm / Institution (here) |
| --- | --- | --- |
| **The self** | one `Person.me` ref, one self scope, self-salience on the diagonal of the salience field | **no self scope.** Board, executive and operating-unit roles are separate claim scopes that can hold contradictory claims, and nothing merges them. `group_self_coupling` replaces self-salience: the share of coupling mass inside the *published* consolidation boundary |
| **Deciding** | one `choose` over intentions under constraints | a **procedure**: ≥2 `choose` calls in different role scopes under *different* objective sets, each step gated by `authorize`. The order of steps, and which role may put what on the agenda, changes the outcome |
| **Objectives** | one agent, one set of needs | two or more objective claim sets in separate scopes, which may conflict. `Firm.divergences` records the conflict as a claim; nothing averages them |
| **Memory** | `Episode.decay` fades salience with a 240-day constant, `_consolidate` turns repeated episodes into standing beliefs, `_forget_stale` drops `means`/`recalls`/`projects` after 4 ticks | `Firm.decay_policy == 'none'`. A filed fact, a policy, a contract and a precedent are retracted when a later record supersedes them and **at no other time**. There is no salience, no half-life and no consolidation pass |
| **Forgetting** | the agent forgets its own episodes | only a **holder scope** is ever cleared, and only by `Firm.vacate`. The office keeps its mandate, the firm keeps its policy, and a `precedent` claim records that the seat changed hands — so the firm remembers the succession after it has forgotten what the person believed |
| **Arousal** | rate of belief update: every percept counts | `commitment_revision_rate`: only `policy`, `commitment`, `guidance`, `contract`, `objective` and `precedent` count. A firm can read an unlimited number of new facts at *zero* arousal. A person cannot. (`test_arousal_counts_commitment_revisions_not_percepts`) |
| **Integration** | share of derived claims whose premises cross two or more cognitive *modules*, scaled by κ | `cross_role_integration`: the same provenance statistic, partitioned by the **org chart**. A firm whose derivations never cross a role boundary is compartmentalized — a fact about the org chart, not about a mood. No κ dial, because there is no single stream to couple |
| **Named states** | nearest prototype in a 7-D affect space, with *asymmetric* transition costs (fear→anger cheap, back dear) | **no motif, no prototypes, no transition matrix.** `regime()` applies declared thresholds and returns a test result (`solvent` / `pressured` / `distressed` / `unknown`). Hysteresis in a firm comes from covenant ratchets and disclosure rules, not from the shape of an affect space |
| **Inertia** | readings exponentially smoothed each tick with weight `0.25 + 0.6·γ` | **no smoothing.** Readings are step functions between filings and carry the filing date. There is no continuous inner state to have inertia; a firm's "now" is the last thing it published |
| **Perceptual axes** | α, κ, γ all live and state-dependent | κ and γ are absent. Only ascription survives, and only as an *outward* fact: αA 0.85, αP 0.0 |
| **Viability** | nutrition, warmth, safety, standing, attachment security | firm: runway, operating margin, free-cash margin, cash cover. Institution: **no solvency term at all** — mandate coverage, authority margin, ultra-vires count, contestation |

Two things are shared on purpose, because sharing them is the claim rather than a shortcut:

* **The same soft minimum.** `corporate_affect.viability` calls
  `worldmodel.agents.affect.soft_viability` — the identical `-logsumexp(-k·margin)/k` a person uses
  — over corporate margins. The *form* of a viability manifold is general; the variables are not.
  (`test_the_firm_uses_the_same_soft_minimum_as_a_person`)
* **The same provenance mechanism.** Integration and contingency weight are counted from
  `Evidence.derived_from` exactly as in `affect.read_affect`. Only the partition changes.

## 2. Roles, authority, and the `authorize` that tensacode does not ship

`docs/agents-design.md` lists `@tc.action` / `authorize` as substrate. `@tc.action` exists;
**`authorize` does not exist anywhere in tensacode** (it is named in `docs/civ-sim/architecture.md`
and never implemented). It is implemented here, in `roles.py`, with these semantics:

```
authorize(act, *, role, charter, holder, at, log, seat_policy, seats) -> tc.Verdict
```

1. the act must be a registered `@tc.action` — an unregistered value is not an act;
2. the role's authority must be **declared** at all, else `unknown`;
3. the role (plus delegations) must hold the act's `power`;
4. if the act requires jurisdiction, the role's declared jurisdiction must contain the subject —
   and an *undeclared* jurisdiction is `unknown`, not permission;
5. if the act carries an amount, the role's declared spending limit must cover it — an undeclared
   limit is `unknown`;
6. the seat must be occupied at `at` (see §8 on what the catalog actually publishes);
7. a role whose declared quorum is more than one must have that many seats filled - a board is
   many seats in one role, and a board below quorum cannot settle.

Three outcomes, not two: `holds`, `fails`, `unknown`. Silence in the record is never read as
either permission or prohibition. Every call — grant, refusal and `unknown` alike — appends an
`ActRecord` to an append-only `ActLog`. `authorize` performs nothing: `Institution.act` authorizes
first and calls `tc.invoke` second, so a refused act provably never reaches the executor
(`test_an_unauthorized_act_is_refused_and_never_performed` asserts `executor.calls == []`).

## 3. Principal-agent divergence

The firm's objective lives in scope `firm:<id>` with the firm as subject. A role's objective lives
in scope `role:<org>:<role>` with the role scope as subject. They are different claim sets and
`test_the_two_objective_sets_stay_in_separate_scopes` pins that they share no claim id.

`Firm.divergences(options)` computes each side's preferred option and, where they differ, records

```
Claim(role_scope, 'diverges_from', (org, firm_choice, role_choice, contested_measures),
      scope=Ref('divergence:<org>'))
```

whose `derived_from` is **both** objective claim sets. So `explain` on the divergence prints the
CFO's aim and the firm's aims side by side, and `Divergence.line()` renders:

> `cfo`'s objective diverged from the firm's: it prefers `buyback` where the firm prefers
> `retain` (firm objective loses 0.1277; contested on `cash_cover`)

The divergence is not merely reported — it *acts*, through the agenda. The settling role chooses
between what was **proposed** and the status quo. A proposing role whose objective diverges can
therefore keep the firm's own first preference off the slate, and the firm ends up worse off under
its own objective with **every step authorized**. `Decision.agenda_cost` quantifies exactly that,
and `Decision.agenda_line()` says it in words. This is the mechanism, not a decoration: it is why
"which role decides what" had to be part of the model.

## 4. The worked example: LyondellBasell Industries N.V.

`sec:cik:0001489393` ≡ `lei:BN6WCCZ8OVP3ITUUVN49`. It is the worked example because this catalog
publishes, for this one issuer, all four things at once — very few issuers clear all four:

| What | How many | Dataset |
| --- | --- | --- |
| quarters carrying cash, revenue, `CostsAndExpenses` **and** capex together, with filing dates and accession numbers | 45 complete, 2011-03-31 → 2026-06-30 (the last 8 are used) | `sec_company_assets` |
| named insiders with officer titles and periods of report, including a CFO handover | 35 people → 19 seats | `sec_ownership_datasets` |
| GLEIF consolidation edges | 103 edges over 52 subsidiaries | `gleif_parent_relationships` |
| 13F filers reporting a position in one of its securities (via the ISIN↔CUSIP bridge) | 2, plus 3 ten-percent owners | `sec_gleif` + `sec_ownership_datasets` |

`worldmodel.agents.firm.worked_example(index=…, catalog=…)` seeds it and runs one decision. Seeding
takes ~17 s, almost all of it streaming `sec_company_assets` once. Real output:

### Roles, from real insider filings

```
roles: board[board], ceo[executive], cfo[executive], controller[executive], coo[executive],
       general_counsel[executive], and 13 operating-unit seats
seated: board=sec:cik:0001977097  ceo=sec:cik:0001911538  cfo=sec:cik:0002056904
        controller=sec:cik:0002062500  general_counsel=sec:cik:0001634184  …
filed quarters: 8      ties: 108      memory: firm scope 176 claim(s), decay policy none
```

The CFO seat changed hands on the record, which is the succession case seeded from real data:

```
sec:cik:0001556386  2023-02-20 → 2025-02-28  ('EVP & CFO',)
sec:cik:0002056904  2025-03-01 → 2026-04-16  ('EVP & Chief Financial Officer',)
```

### A procedure-based decision

The firm's objective is its declared distress boundaries: runway ≥ 4 quarters, cash cover ≥ 0.25.
The CFO's objective: maximize reported operating margin. Both are *declared* (the catalog publishes
what a firm did, not what it was trying to do), and the two options' effects are declared
assumptions.

```
procedure capital_allocation: completed
  propose propose by cfo -> holds (the record last observed sec:cik:0002056904 in cfo on
                                   2026-04-16; continued tenure is Unknown)
    cfo proposes buyback under the cfo objective (utility 17.2138)
  review review by general_counsel -> holds (… last observed on 2026-02-28 …)
  approve approve by board -> holds (… last observed on 2026-05-22 …)
    board settles on status_quo under the firm objective
proposed: buyback
settled: status_quo
agenda: the firm prefers retain but buyback was proposed, so the settling role chose between
        buyback and the status quo; the firm settled on status_quo, 0.0000 below its own first
        preference
divergence: cfo's objective diverged from the firm's: it prefers buyback where the firm prefers
        retain (firm objective loses 0.1277; contested on cash_cover)
```

Every step authorized and every step carries the seat caveat, because the SEC publishes a period
of report rather than an appointment: the record last observed this CFO in this seat on 2026-04-16
and says nothing about the day after. The act proceeds and the caveat is on the verdict and in the
`ActLog`; `seat_policy='observed'` turns it into a refusal for a caller who wants that.

The **agenda cost is 0.0000 here and that is the right answer**: this issuer is comfortably inside
both of its declared boundaries, so doing nothing satisfies the firm's objective exactly as well as
retaining cash would, and the CFO's agenda-setting costs the firm nothing it can name. The
divergence is still recorded, with the measure it turns on, because the CFO *would* have taken the
firm below its cash-cover floor. On the distressed fixture — where the firm is past a boundary and
`retain` strictly improves — the same code reports an agenda cost of 0.8600
(`test_the_proposing_role_sets_the_agenda_under_its_own_objective`). Both numbers come out of the
same arithmetic; neither is tuned to look interesting.

### The readings

```
reading as of 2026-07-31  regime=solvent
  viability                  +0.3665   soft-min over cash_cover, free_cash_margin, operating_margin, runway
  viability_gradient         +0.4305   V(2026-06-30) - V(2026-03-31), both as filed
  commitment_revision_rate   +1.0000   2 of 2 live commitments revised this period
  liquidity_pressure         +0.2500   runway 12.00 quarters against a declared need of 4.0; no covenant terms declared
  covenant_proximity         unknown   no covenant terms declared for this issuer
  exposure                   +0.0000   searched sanctioned, designated, enforcement_action, investigation,
                                       restatement, litigation, material_weakness; found 0 live item(s)
  group_self_coupling        +0.9585   52 of 57 counterparties lie inside the consolidation boundary
  cross_role_integration     +1.0000   2 of 2 derived claims cross a role boundary
  contingency_weight         +0.8182   9 of 11 derived claims sit in a hypothetical scope
  ascription                 agency=0.85 phenomenality=0.00 harm_constraint=None
```

The viability gradient is not a proxy. `+0.4305` is the movement between the 10-Q for the quarter
ended 2026-03-31 and the one for 2026-06-30, on numbers those two filings contain.

`cross_role_integration` is `2 of 2` here because this issuer is solvent: runway is at the cap and
margin is above the floor, so **no appraisal rule fires at all** and the only derived claims are the
decision and the divergence. On a firm past a boundary the CFO's `liquidity_pressure` appraisal
fires and the board's escalation rule derives from it, and the reading drops below 1.0 because some
derivations then stay inside one scope (`test_integration_counts_only_derivations_that_span_two_role_scopes`
pins that on the distressed fixture). A healthy firm having nothing to appraise is the correct
behaviour, not a missing reading.

`exposure = 0.0000` is a *found nothing*, not an *unknown*: the basis names the seven predicates
that were searched. `covenant_proximity` is `unknown`, because covenant terms are published in no
catalog dataset and are not going to be invented.

### The explanation, down to a filing

```
sec:cik:0001489393 decided ('capital_allocation', 'status_quo')
  ← procedure:capital_allocation from:
    sec:cik:0001489393 cash 2635000000.0
      ← observed in dataset:sec_company_assets/normalized@683351223512
        at secfacts:0001489393:us-gaap:CashAndCashEquivalentsAtCarryingValue:USD::2026-03-31:0001489393-26-000028
        via published:sec_company_assets
```

`0001489393-26-000028` is the accession number of the 10-Q. Every filed claim cites the concept
record that published *that* number, not the quarter's records in bulk.

## 5. Institution: the House Budget Committee

`congress:committee:hsbu00`. Its **jurisdiction is grounded**, and that is the point: a committee
may report a bill referred to it and no other, and `referred_to_committee` is a published edge. The
committee's 200 most recent referrals (a declared bound, reported in `unknown()`) become its
`Instrument`, and the chair's `Authority.jurisdiction` is exactly that set.

```
House Committee on the Budget (congress:committee:hsbu00)
  roles: chair[chair], member[member], ranking_member[member]
  instruments: 1 (1 current)      declared jurisdiction: 200 subject(s)

report a referred bill     (chair)  -> applied
report an unreferred bill  (chair)  -> rejected: bill … is outside the declared jurisdiction of chair
report a referred bill     (member) -> rejected: role member holds no power 'report_measure'
executor calls: 1
```

One act, three attempts. The two refusals are on the record twice — in the `ActLog` and as
`ultra_vires` claims in the store — and the executor was called once.

```
legitimacy as of 2026-09-01  regime=contested
  mandate_coverage     1.0000   200 of 200 declared subject(s) covered by a current instrument
  authority_margin     0.3333   1 of 3 attempted act(s) were within declared authority
  ultra_vires          2.0000   2 ultra-vires attempt(s) on the record
  contestation         0.0000   0 recorded contest(s)
  legitimacy           0.4966   mandate coverage 1.000 x ultra-vires erosion 0.497 x contestation 0.000
```

**Legitimacy erodes and does not recover by good behaviour.** `exp(-λ·ultra_vires)` is monotone
non-increasing in the number of recorded ultra-vires *attempts*, whether or not they succeeded,
because the attempt is what is on the record. Behaving well afterwards raises `authority_margin`
and leaves `legitimacy` exactly where it was
(`test_erosion_does_not_recover_by_behaving_afterwards`). Lapsing the instrument drops mandate
coverage to 0 and legitimacy with it.

## 6. Ascription

`FIRM_ASCRIPTION` and `INSTITUTION_ASCRIPTION` are `agency=0.85, phenomenality=0.0,
template='agent'`, and `harm_constraint` is `None` rather than a small number. A perceiving agent
should predict a firm with the agent template (`W = αA·W_agent + (1-αA)·W_mech` with αA high) and
apply **no** phenomenality-weighted harm constraint to it. The two axes dissociating is the claim
the corporate design rests on; here it is a value a perceiver can read, not a remark.

Its members are subjects of experience. It is not.

## 7. What is grounded and what is authored

Grounded — every one of these becomes a claim only with a `dataset@version#record_id`:

* quarterly cash, revenue, costs and capex, with filing dates and accession numbers;
* who held which seat, with what published title, over what period of report;
* consolidation, ten-percent ownership and 13F reported holdings;
* committee membership with title, rank and side; measures referred to a committee.

Authored, and labelled as such in the code because the catalog publishes none of it:

* **`STANDARD_DELEGATION`** — a generic delegation of authority for a US public company. No
  catalog dataset publishes bylaws. With `authority=None` (the default) every grounded role is
  `UNDECLARED` and every act authorizes `unknown`, which is the honest reading; the worked example
  passes `STANDARD_DELEGATION` explicitly so that authority has something to bind against.
* **`TITLE_ROLES`** — the map from a published officer title to a seat name. The *seat* is
  published; calling it `cfo` is ours.
* **`COMMITTEE_AUTHORITY`** — the power vocabulary attached to a committee seat. The jurisdiction
  is published; the verbs are not.
* **`COUPLING_WEIGHTS`**, **`DistressBoundaries`**, **`ULTRA_VIRES_DECAY`**,
  **`CONTESTATION_WEIGHT`** — ordering and threshold constants. Stated at module level rather than
  buried in a function, because changing them changes what a firm reads as.
* **`Option.effects`** — declared consequences of a course of action. Not estimates, not fitted.
  The decision record keeps them so a reader can disagree with the assumption rather than with the
  arithmetic.

**Perception is bounded**, as the grounding contract requires. A firm reads exactly six index
predicates, all of them anchored on its own resolved identity cluster and served by an index —
`insider_of` (in), `directly_consolidated_by` and `ultimately_consolidated_by` (both directions),
`issuer_security` (out) and `reported_holding` (in, through the ISIN↔CUSIP bridge) — plus the
`sec_company_assets` observations for its own CIK. An institution reads two: `committee_member`
and `referred_to_committee`. Each query carries an explicit row budget, and a budget that binds is
reported in `unknown()` rather than silently truncating — on LyondellBasell the securities budget
does bind (1,805 ISINs, 400 read), so the firm says "holders are read through the 400 most recent
issued securities; there may be more" instead of presenting its holder list as complete.

## 8. What this does not claim

* Nothing here is experienced. The readings are structural measures over computation and are named
  so that the slide cannot happen by accident.
* Nothing here is fitted to an outcome, scored against a baseline, or `validated`. `Aim`, `Option`
  and every constant in §7 are declarations. If any of this is ever to make a claim about the
  world it goes through the estimation layer like everything else.
* No language model anywhere in the loop. `tc.choose` runs against a deterministic in-process
  argmax over a declared utility (`firm._argmax_runtime`), so a decision repeats exactly
  (`test_the_decision_is_deterministic_and_repeats_exactly`).
* A published observation window is **not** an appointment. SEC insider filings publish a period of
  report; the day after it ends, whether the person still holds the seat is `Unknown`. `Holder.basis`
  carries that distinction and `authorize` records it on the verdict rather than guessing
  (`seat_policy='observed'` turns the Unknown into a refusal for callers who want it).

## 9. Running it

```sh
# both test modules; they skip cleanly when tensacode is absent
python3 -m unittest discover -s tests -p 'test_agents_*' -q

# with the optional substrate from a local checkout
PYTHONPATH=<tensacode>/tensacode/python/src python3 -m unittest discover -s tests -p 'test_agents_*' -q
```

The real-data tests additionally skip unless this checkout has `data/world_evidence/index.sqlite`
and a published `data/sec_company_assets`.
