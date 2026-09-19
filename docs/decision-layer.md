# The decision layer

Gap #3 of the [strategic affordances audit](strategic-affordances-audit.md) is a decision
contract: "no structured representation of a decision-maker, controllable actions, action
cost, budget, constraints, objectives, horizon or success criteria that is validated end to
end". This layer is that representation, plus two things you can do with it that do not
overclaim: search for the scenarios where a supplied policy breaks, and optimize a policy
only where the dynamics are validated.

Three rules run through all of it.

1. **A declared evidence status is a claim, and the store decides.** Every mechanism an
   environment runs carries `validated` (with its report id), `estimated but not validated`
   or `assumed`. The status is re-derived from the published validation reports in
   `calibration_reports` and can only be *lowered*, never raised.
2. **Every output says what it rests on.** Rollouts carry the status of each mechanism they
   used; anything resting on a mechanism that is not `validated` is labelled `UNVALIDATED`
   automatically.
3. **Nothing here is a causal claim.** The one process this layer can run a learner on,
   `monetary_model`, is validated on *forecast skill against a holdout*. Validated forecast
   skill is not validated counterfactual response.

```sh
wm decision-validate examples/decision/monetary-treasury-stress.json
wm decision-rollout  examples/decision/monetary-treasury-stress.json --policies examples/decision/monetary-treasury-policies.json
wm decision-stress   examples/decision/monetary-treasury-stress.json --policies examples/decision/monetary-treasury-policies.json --evaluations 2000 --seed 7
wm decision-optimize examples/decision/monetary-treasury-optimize.json --request examples/decision/monetary-treasury-optimize-request.json
python3 examples/decision/run_examples.py        # all four, writing examples/decision/outputs/
```

## 1. The contract

`worldmodel.decision_contract/1` — JSON Schema in
[`worldmodel/decision/contract.schema.json`](../worldmodel/decision/contract.schema.json),
validated by `worldmodel.decision.contract.validate_contract` (stdlib only: `schema_errors`
implements the subset of JSON Schema the document uses and *raises* on any keyword it does
not implement, rather than ignoring it).

| Section | What it must say |
| --- | --- |
| `decision_maker` | who decides, and in what role |
| `actions` | what they control: type, unit, inclusive bounds, `cost_per_unit` and `cost_basis` |
| `budget` | the total action-cost limit and its unit (required as soon as any action costs something) |
| `constraints` | hard limits on metrics, aggregated over the horizon |
| `objectives` | metric, aggregate, direction, **explicit weights that must sum to 1**, and a positive scale |
| `horizon` | steps and step unit, which must match the kernel's |
| `success_criteria` | what counts as success: metric, aggregate, operator, threshold, scale |
| `mechanisms` | for every mechanism the environment uses, an `evidence` block with its status |
| `uncertain_inputs`, `assumed_parameters` | what the fragility search may vary, with declared ranges |
| `parameter_uncertainty` | how far report-backed parameters may move, and the shock bound |
| `does_not_establish` | the author's own limitations, added to the standing ones |

Cross-field rules the schema cannot state are checked too: weights sum to one; a
`validated` claim must cite `report_id` and `component`; an `assumed` mechanism may not
cite a report; a costly action needs a budget; nominal values lie inside their ranges;
criterion ids are unique and `budget` is reserved. Then the *kernel* checks the rest: the
contract must declare exactly the actions and mechanisms the kernel runs, its metrics must
exist, its action bounds must lie inside the kernel's, and its uncertain inputs and
assumed parameters must be ones the kernel accepts, in the right units.

Two kernels ship (`worldmodel/decision/kernels.py`):

- **`monetary_policy_rate`** — the monetary family's smoothed Taylor rule as a
  one-quarter-ahead transition, `i_t = max(elb, rho i_{t-1} + (1-rho) i*_t + e_t)`, for a
  decision-maker who takes the rate as given. Drivers (inflation, output gap) are either
  declared linear paths or contiguous blocks resampled from the first-release quarters the
  validated attempt was fitted on.
- **`coupled_economy_firm`** — the synthetic commercial-bank economy
  (`worldmodel.coupled_economy`) from one firm's seat: daily production and credit line,
  with a policy-rate rule, loan-rate pass-through, price adjustment and demand response.

A contract compiles into the existing materialization environment:
`CompiledDecision.environment()` returns a `worldmodel.environments.Environment` whose
actions are routed inputs bound to the decision-maker, whose observations and reward terms
select that entity's materialized outputs, and whose `evaluate(history, seed)` replays the
kernel deterministically (the last prefix is cached, so a step costs one transition). An
objective that a per-step reward cannot express — a `max` or `final` aggregate — is
reported in `reward_approximations` and scored on the finished rollout instead of being
quietly dropped.

## 2. Evidence status, re-derived

`EvidenceIndex.from_store(store)` reads every validation report in `calibration_reports`,
re-verifies it (report digest, estimate digest, re-evaluated criteria, via
`calibration_record`) and joins it to the pre-registered plan — the same procedure as
`wm calibration-status --all`. For the run below it saw 75 reports over 63 registered
attempts and five validated processes.

A `validated` claim survives only if the cited report exists, re-verifies, passes every
declared criterion, is the counted report for its attempt label, its attempt is in the plan
and not superseded, and its component is the one the contract names. Otherwise it is
lowered to `estimated but not validated` (a report that exists but fails) or `assumed` (no
report, or one that does not verify). A claim lower than the evidence stands as claimed.

Then **binding coverage** lowers it again: a mechanism is only as good as the parameters
actually bound from its report. If a report does not set every parameter the mechanism
consists of, the mechanism as *run* is `assumed`, whatever its report says. `bound_from_report`
and `assumed` are listed per mechanism.

Resolution also attaches caveats automatically. For the validated monetary mechanism:

```
current attempt monetary_model.cpi_okun_proxy_v2 on the same component fails
  beats_persistence_dm, no_revision_leakage (a different specification or series)
current attempt monetary_model.okun_unrate_realtime_v2 on the same component fails
  beats_persistence_dm, parameters_within_declared_bounds (a different specification or series)
scored at horizon 1 (native steps); rollouts iterate it for 8 quarters, and multi-step skill
  was never tested
scored conditional on realized inflation, output_gap; here they are scenario inputs, and
  forecasting them was never tested
holdout after 2014-12-31 through cutoff 2024-12-31; nothing says the fitted conduct persists
  outside that window
```

The economy contract is the mixed case. It claims one validated, one estimated and two
assumed mechanisms, and the store agrees with all four:

| Mechanism | Declared | Resolved | Why |
| --- | --- | --- | --- |
| `loan_rate_pass_through` | validated | **validated** | `interest_pass_through.fred_realtime_v2` re-verifies and passes; all four parameters (`spread`, `pass_through`, `adjustment_speed`, `impact_pass_through`) bind |
| `policy_rate_rule` | estimated but not validated | estimated but not validated | `policy_rule.fred_realtime_v3` binds all five parameters and fails `beats_persistence_dm` |
| `price_adjustment` | assumed | assumed | `price_adjustment.eia_monthly` estimates `adjustment = -0.0035`, outside the mechanism's [0, 1]; it cannot be bound |
| `demand_response` | assumed | assumed | `demand_price_elasticity.eia_monthly` estimates `elasticity = -0.124` (gasoline, wrong sign here) and fails its bounds |

## 3. Adversarial scenario search

`stress_test(compiled, policies, evaluations=..., seed=...)` searches, per policy, the box
the contract declares:

- uncertain inputs and assumed parameters within their declared ranges;
- report-backed parameters within their **estimated** uncertainty. For the monetary
  mechanism that is the joint Wald ellipsoid (Mahalanobis radius 2) of the reduced-form
  coefficients under the HC1 covariance **recomputed by refitting the report's own
  estimator on the report's pinned inputs**, accepted only because the refit reproduces the
  report's coefficients and standard errors exactly (`max_relative_difference = 0.0`, n = 72
  after the lower-bound exclusions). An independent ±2 SE box was the first implementation
  and was wrong: its corners (every coefficient at its maximum at once) are not in the
  estimate's uncertainty at all, and they produced 17-percentage-point "worst cases";
- standardized policy shocks within ±1.2816, the 80% central interval of the predictive
  distribution whose coverage the report validated (0.861 observed against 0.8 nominal).

The budget is spent half on uniform random draws (shared across policies: common random
numbers) and half on a seeded coordinate pattern search from the worst draws. **Failure
regions are computed from the random phase only**, because refinement deliberately
oversamples failures.

### Worked example A — a policy-rate path against the fitted real-time monetary dynamics

Contract [`monetary-treasury-stress.json`](../examples/decision/monetary-treasury-stress.json):
a fictional floating-rate treasury reserves each quarter's interest at the last observed
policy rate plus a margin, for eight quarters from 2024Q4 (the last first-release quarter in
the fitted panel: rate 4.65, inflation 2.58, gap 1.81). Candidate policies are *plans*: a
reserve path, in the language of a planned policy-rate path. Success: no quarter under-reserved
by more than 75 bp, and no more than 2 pp-quarters cumulatively. Budget: 6 pp-quarters of
idle liquidity. Only the reaction function is estimated; the treasury, its tolerance and
its plans are invented.

At the nominal scenario (inflation 2.6 → 2.2, gap 1.8 → 0.5, no shocks, coefficients at
their estimates):

| Plan | Success | Worst quarter shortfall | Total shortfall | Idle budget used |
| --- | --- | ---: | ---: | ---: |
| `hold_at_4.65` | yes | 0.04 pp | 0.08 | 1.02 / 6 |
| `last_rate_plus_50bp` | yes | 0.00 pp | 0.00 | 4.00 / 6 |
| `cuts_to_3.40_then_hold` | **no** | 1.07 pp | 5.94 | 0.00 |
| `cuts_25bp_every_quarter` | **no** | 1.38 pp | 7.44 | 0.00 |

The first finding needs no search: **a plan that cuts 25 bp a quarter is already inconsistent
with the fitted reaction function at nominal inputs.** With a 1.8% gap and 2.6% inflation the
fitted rule wants roughly 4.9%, and `rho = 0.85` moves it there slowly; a budget built on
cuts is under-reserved from the first quarter.

Then 2,000 rollouts per policy (999 random, 1,000 refined, seed 7, 4.1 s for all four):

- `cuts_25bp_every_quarter`: fails in **709 of 999** random draws (budget 67, total shortfall
  614, worst-quarter 623). *It breaks when `output_gap_end ∈ [0, 3]` and `inflation_end ∈
  [4.17, 6]`: 127 of 128 draws there fail (99%). It holds best when `output_gap_end ∈ [-6, -3]`
  and `inflation_end ∈ [0.5, 2.33]` — and even there 51% fail.*
- `cuts_to_3.40_then_hold`: fails in 675 of 999. Drivers: `output_gap_start` (spread 0.35),
  `output_gap_end` (0.33), `inflation_end` (0.25) — all "fails more when higher".
- `hold_at_4.65`: fails in 670 of 999, but mostly for a different reason: 414 of those failing
  draws breach the **budget** rather than the shortfall tolerance. Holding a 4.65% reserve while the fitted rule cuts is expensive, and the declared
  6 pp-quarter liquidity budget is what breaks. No single dimension separates failures at this
  sample size.
- `last_rate_plus_50bp`: **0 of 999** random draws fail — and the refinement still found a
  failure, violating the 75 bp tolerance by 3.16 scales (a 1.54 pp shortfall in one quarter).
  Its scenario is not a level but a whipsaw: seven quarters of shocks at the *low* edge of the
  validated 80% interval while inflation climbs from 1.5 to 6.0 and the gap from -3 to +3, then
  one shock at the *high* edge. The rate is held down, the rule's desired rate runs away, and
  a single quarter closes the gap.

That last point is the case for local refinement: uniform sampling of a 16-dimensional box
found nothing; a pattern search from the worst draws found the corner.

### Worked example B — a firm's production plan in the coupled economy

Contract [`economy-firm-stress.json`](../examples/decision/economy-firm-stress.json): one firm
choosing a constant daily production quantity and credit line for 30 days, with a unit-cost and
rate-expectation shock on day 10. Success: never bankrupt (unpaid interest bankrupts it),
deposits cover principal on day 30, borrowing never exceeds $150. 600 rollouts per policy,
seed 3, 12.9 s.

| Plan (production/day, credit line) | Nominal | Random failures | Refined worst |
| --- | --- | ---: | ---: |
| `cautious` (2, $20) | success | 53 of 299 (18%) | 0.40 scales |
| `steady` (4, $60) | success | 133 of 299 (44%) | 1.2 scales |
| `aggressive` (9, $150) | **fails** `solvent_at_end` (net cash −$119) | 261 of 299 (87%) | 3.0 scales |

All three break in the same place: *`household_demand ∈ [1, 3.33]` and
`unit_cost_multiplier ∈ [1.6, 2]` — 41 of 41 draws there fail for `steady` and `aggressive`,
30 of 41 for `cautious`.* The driving dimension for `steady` is household demand (bin spread
0.91, fails more when lower), then the cost shock (0.33), then the assumed price-adjustment
speed (0.19). Three of 299 draws bankrupt the firm outright through unpaid interest.

**This is a fragility claim and the report says so**: `not_an_optimality_claim` and
`failure_rates_are_not_probabilities` are fields of every report. A failure rate is the share
of uniform draws over a *declared* box, not a probability of failure in the world; finding no
failure is not evidence that none exists; and nothing here says any of these plans is best.
And because two of this environment's mechanisms are assumed and one is estimated-but-failing,
every one of these numbers carries the automatic label:

> UNVALIDATED: this rests on demand_response (assumed), policy_rate_rule (estimated but not
> validated), price_adjustment (assumed). It is optimal, robust or fragile only under those
> assumptions, and says nothing about the world beyond them.

## 4. Optimization, gated on validated dynamics

`optimization_gate(compiled)` refuses unless **all** of:

1. the evidence came from the `calibration_reports` store (not a hand-built index);
2. every mechanism the environment runs resolves to `validated` there, *after* binding
   coverage;
3. training scenarios come from an observed source — resampled published data with
   provenance — because an invented driver distribution is an assumed mechanism under
   another name.

### The refusal

Offering the economy environment to the optimizer produces
[`economy-firm-refusal.json`](../examples/decision/outputs/economy-firm-refusal.json), not a
policy:

```
refused: mechanism demand_response is assumed: declared assumed: its values are written in
  the contract or kernel configuration
refused: mechanism policy_rate_rule is estimated but not validated: estimated: parameters
  come from the cited report, which fails beats_persistence_dm
refused: mechanism price_adjustment is assumed: ...
refused: training scenarios are not drawn from observed data (no training-scenario source
  configured); an invented driver distribution is an assumed mechanism
```

Note that `loan_rate_pass_through` is listed in the same report as `validated`: one validated
mechanism in an environment does not make the environment optimizable.

### The gated run

Contract [`monetary-treasury-optimize.json`](../examples/decision/monetary-treasury-optimize.json):
the same treasury, now choosing a margin from `{-0.25, 0, 0.25, 0.5, 0.75, 1.0}` each quarter
from bucketed observations of last quarter's rate, inflation and gap. Episodes resample
contiguous nine-quarter blocks of the 106 first-release quarters the validated attempt was
fitted on (`fred_macro_panel@7dcce89c`, `fred_cpi@34fe03f5`, pinned to the report's own input
versions), with policy shocks drawn from the fitted residual distribution. The contract splits
that history at 2015-01-01 — the start of the report's holdout — into **31 training blocks
(1996Q1–2013Q1)** and **16 evaluation blocks (2015Q1–2023Q2)**.

Tabular Q-learning (`worldmodel.rl.train_tabular`), 6,000 training episodes, 80,000
transitions, 41 states visited, γ = 0 (the treasury is a price taker: its action does not move
the rate, so each quarter is a contextual bandit). Evaluation: 2,000 held-out seeds, the same
seeds — and therefore the same scenarios — for every policy. Weighted score is
−(0.75 × total shortfall + 0.25 × total idle reserve); higher is better.

| Policy | Held-out history: score | success | feasible | Training history: score | success | feasible |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| learned (tabular Q) | −1.922 | 0.483 | 1.000 | **−1.102** | 0.885 | 1.000 |
| persistence (margin 0) | −2.359 | 0.327 | 1.000 | −1.249 | 0.737 | 1.000 |
| fixed 25 bp | −1.855 | 0.501 | 1.000 | −1.218 | 0.911 | 1.000 |
| fixed 50 bp | **−1.635** | 0.655 | 1.000 | −1.436 | 0.974 | 1.000 |
| fitted-rule 75th percentile | −1.729 | 0.331 | 0.860 | −1.302 | 0.856 | 1.000 |

Paired differences (learned − baseline, 95% intervals over the 2,000 paired seeds):

| Baseline | Training history | Held-out history |
| --- | --- | --- |
| persistence | **+0.147** [0.133, 0.162] | **+0.437** [0.414, 0.460] |
| fixed 25 bp | **+0.116** [0.103, 0.128] | **−0.067** [−0.085, −0.050] |
| fixed 50 bp | **+0.334** [0.316, 0.352] | **−0.287** [−0.316, −0.258] |
| fitted-rule q75 | **+0.200** [0.175, 0.225] | **−0.193** [−0.241, −0.144] |

**The learned policy beats every baseline on the history it trained on, and loses to two
fixed rules and to the model's own quantile rule on held-out history.** It beats only
persistence there. Part of the reason is visible in the run: on held-out blocks the frozen
policy hit states it had never seen 2,087 times (out of 16,000 steps) and fell back to a zero
margin — 2015-2023 contains the lower bound and the 2022 hikes, and the 1996-2013 blocks do
not teach them. This is a toy learner on a five-parameter reaction function, and it still
overfits the sample it was given; that is the finding, not a defect to be tuned away.

Two further honesty notes about this run. The fitted-rule baseline is the *model's own*
one-step 75th percentile, computed from last quarter's drivers because this quarter's are
unknown at decision time — which is exactly the gap between what the report validated
(conditional forecasts with realized inputs) and what a decision needs. And the resampled
blocks are drawn from the same history the mechanism was fitted on: held-out seeds are
held-out draws, and the held-out-history split is held out from the *learner*, not from the
estimator.

## 5. What none of this establishes

Every rollout, stress report and optimization result carries these, plus whatever the
contract adds:

- Holdout forecast skill does not identify an intervention response. A validated mechanism
  forecast well on data it had not seen, conditional on realized inputs; it was never tested
  on what happens when someone intervenes.
- No mechanism here is causally identified; the estimates are reduced-form and their own
  reports label them so (`descriptive_reaction_function`, `error_correction_ols_hac`, …).
- Declared bounds on uncertain inputs and assumed parameters are author statements, not
  probabilities or confidence sets. Failure rates over them are not probabilities of failure.
- A policy that does well in one of these environments is optimal, at best, for that
  environment's assumptions.
- Specifically for the monetary examples: the reaction function was scored at horizon 1 and
  is iterated for eight quarters here; its `phi_pi = 0.38` does not satisfy the Taylor
  principle, so the pass rests on forecast skill, not on credible structural coefficients;
  inflation and output-gap paths are declared or resampled, never forecast; two other current
  attempts on the same component fail.
- Specifically for the economy example: the economy has one firm, one household and two banks,
  and its money, goods and prices are fictional. Its accounting identities (reserves + loans =
  deposits + equity, conserved reserves, conserved goods) are enforced exactly and are reported
  as `identities_enforced` — they are true by construction and carry no behavioral claim. A
  loan rate estimated on monthly US prime and federal funds data is applied to a fictional
  daily loan; that transfer is itself an assumption.
- The decision-makers, their costs, budgets and tolerances are invented in both examples. The
  layer checks that a decision is *stated*; it cannot check that the statement is true of
  anyone.

## 6. Files

| Path | What |
| --- | --- |
| `worldmodel/decision/contract.schema.json`, `contract.py` | the contract and its stdlib validator |
| `worldmodel/decision/evidence.py` | evidence resolution from the published reports, the labels, `load_attempt_data` |
| `worldmodel/decision/kernels.py` | the two kernels, parameter binding, scenario spaces, the covariance refit |
| `worldmodel/decision/compile.py` | compilation into `Environment`, rollouts, declarative policies, `recommend` |
| `worldmodel/decision/fragility.py` | the adversarial search and its failure-region report |
| `worldmodel/decision/gated_rl.py` | the gate, the refusal and the bounded tabular learner |
| `worldmodel/decision/cli.py` | `wm decision-validate / -rollout / -stress / -optimize` |
| `examples/decision/*.json` | the three contracts, their candidate policies and the optimization request |
| `examples/decision/outputs/*.json` | the outputs quoted above |
| `tests/test_decision_*.py` | 75 tests: schema and semantics, evidence lowering, binding coverage, replay determinism, search reproducibility, the gate and the refusals |

Related: [estimation and validation](estimation-and-validation.md),
[calibration status](calibration-status.md) (which attempts pass, and on what),
[environments and surfaces](environments-and-surfaces.md),
[structured rollouts](structured-rollouts.md) (the scenario benchmark and the
non-identifiability diagnostic this layer sits beside).
