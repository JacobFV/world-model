# Political, geopolitical and market model families

`worldmodel.models` adds eleven estimable model families and a strategic
multi-actor layer. Each family is a mechanistic or statistical model with explicit
parameters, units and the public datasets needed to estimate them. None is
validated: every descriptor, fit and report carries `validated: false` until the
estimation layer (`worldmodel/estimation/`) scores it on held-out data (see Holdout forecasts).

## Common contract

Every family module exposes:

| Member | Meaning |
|---|---|
| `FAMILY` | `id`, `title`, `parameters` (`value`, `unit`, `source` ∈ fit/external/assumed/data, optional `bounds`, `series`), `requirements` (publisher, dataset, series/fields, URL, frequency, role, parameters served, access), `identification`, `limitations`, `validated: false` |
| `fit(data, cutoff=None)` | `{estimate, diagnostics, evidence}`. Rows after `cutoff` are excluded. `evidence` records the method, identification label, data windows and data hash. |
| `simulate(config, mode, seed)` | `mode` is `deterministic` (exact expectations or point solutions) or `stochastic` (seeded draws). Accounting checks raise instead of clipping. |
| `synthetic(seed)` | `{data, truth, config}` fixtures where the true parameters are known. |

Registry helpers: `models.families()`, `describe(id)`, `requirements(id=None)`,
`parameter_hooks(id)` (for the estimation layer), `fit`, `simulate` and `synthetic`.
The helpers in `models/base.py` are pure Python: dense solves, OLS (HC1/CR1), alternating-projection
demeaning, PPML with high-dimensional fixed effects, NB2, Nelder–Mead,
golden-section search, Poisson-binomial, and seeded Poisson/NB/binomial/multinomial
draws. numpy is optional. It accelerates legislative ideal-point sweeps and the trade
price loop, and the equivalence test skips cleanly when numpy is absent.

Identification labels matter. `correlational` and `predictive_association` mean
the coefficient must not be read as a causal effect. Instrumental-variable fits are
labeled with their untested exclusion assumption.

## Families

| Family | Model | Key parameters (unit) | Required public data |
|---|---|---|---|
| `legislative` | Logistic quadratic-utility ideal points (IDEAL/emIRT family, 1–2D). MAP by alternating Newton, with lopsided/min-vote screens and anchors. Leave-one-out party-whip discipline. Exact Poisson-binomial floor passage, committee gate with chair veto (exact joint with the floor), thresholds (majority/fraction/count), pivotal members | ideal points (latent sd), bill a/b (logit), discipline (logit), salience | Voteview `HSall_votes/members/rollcalls`; Congress.gov committees |
| `elections` | Pooled OLS with cycle-clustered errors; national-swing and district variance components; logit turnout. Exact seat distribution by quantile quadrature over the swing; optional Student-t Monte Carlo | coefficients (two-party share), sigma_national, sigma_district, turnout logit | MEDSL House returns, district presidential lean, FEC `weball`, FRED income/GDP/UNRATE, VEP/CVAP turnout |
| `influence` | LDA client→registrant→issue/agency and FEC donor→committee→candidate networks with conserving attribution: equal issue/agency splits, pro-rata pass-through, retained and unattributed nodes. HHI, attribute exposure, weighted PageRank. Two-way FE panel (CR1), optional FE-2SLS; `panel_design` export hook | influence coefficient, SE, attribution rules | Senate LDA API; FEC `itcont`, `itpas2`, `ccl`; Voteview outcomes |
| `trade` | PPML gravity with exporter-year/importer-year (or pair) FE. Multi-sector Caliendo–Parro exact-hat GE with input-output linkages and fixed deficits; reduces to Anderson–van Wincoop with one sector. Partial-equilibrium fidelity. Stochastic elasticity draws | trade_elasticity θ per sector, distance/sanction coefficients, IO and value-added shares | CEPII BACI, UN Comtrade, CEPII Gravity, WITS/TRAINS tariffs, EXIOBASE/BEA/OECD ICIO, GSDB |
| `sanctions` | OFAC 50% rule as a least fixed point (aggregate ownership through blocked owners only), optional control criterion, multiplicative look-through exposure, counterparty exposure categories. Route closure with Dijkstra converts cost changes into trade-cost multipliers for `trade`. Gravity fit gives a sanction tariff-equivalent | ownership threshold, route-cost semi-elasticity, prohibitive multiplier | OFAC SDN/Consolidated, OpenSanctions ownership, GLEIF Level 2, EU list, BACI + GSDB |
| `conflict` | Discrete multivariate Hawkes with geometric kernel and row-normalized contiguity diffusion. EM on branching structure plus profile likelihood for decay, numerical-Hessian SEs, branching ratio. NB2 alternative with lagged own/neighbor counts. Exact expected paths, impulse (diffusion) responses, escalation probabilities | background log-rate coefficients, self/neighbor excitation (events/event), decay (monthly retention), NB dispersion | UCDP GED, GDELT 2.0, V-Dem, WDI, CShapes |
| `assets` | OLS factor betas with variance-targeted GARCH(1,1) QMLE and Ljung–Box diagnostics. Variance term structure, normal VaR/ES, simulated factor+GARCH paths (normal or t) | alpha (log return/day), betas, omega/alpha/beta, factor mean/covariance | Licensed daily bars; Kenneth French factors; FRED `DTB3` |
| `market_abm` | Fundamentalist/chartist/noise wealth-share rules with no shorting or borrowing. Walrasian clearing (monotone bisection, largest-remainder integer fills) or price-time-priority limit order book. Integer cents/shares conserved every step. Grid SMM fit with common random numbers | strengths, chartist window, noise sd, fundamental volatility, aggressiveness | Daily closes (moments); optional LOBSTER/TAQ |
| `commodities` | Balance identity with competitive-storage reduced form: price solves availability = use + desired stocks. Log-linear storage/supply OLS; demand OLS or 2SLS with a supply shifter; reported balance discrepancies | demand/supply/storage elasticities, target stocks-to-use, supply lag | EIA WPSR (`WCESTUS1`, `WCRFPUS2`, `WCRIMUS2`, `WCREXUS2`), EIA spot `RWTC`, USDA PSD/WASDE, NASS QuickStats, FRED `DCOILWTICO` |
| `monetary` | Smoothed Taylor rule with ELB censoring (structural coefficients from OLS reduced form). Nelson–Siegel factors at a grid-chosen λ; VAR(1) factor dynamics with policy-change loading | rho, phi_pi, phi_y, r_star (percent), elb, λ (per year), VAR matrices | FRED `FEDFUNDS/DFF`, `PCEPILFE/CPIAUCSL`, `GDPC1/GDPPOT/UNRATE/NROU`, `DGS1MO…DGS30` |
| `regional` | Leave-one-out Bartik shocks (optional tradable subset) with year FE and region clusters. PPML migration gravity with origin-year and destination FE. Out-migration response to employment rate. Deterministic flows or integer binomial/multinomial movers with exact population accounting | shift-share, destination-employment, out-migration and distance elasticities; birth/death rates | BLS QCEW, Census CBP, IRS SOI county flows, Census PEP components, BLS LAUS, NBER county distances |

### Holdout forecasts

Each estimable family module declares `holdout_forecaster` for the estimation layer
(`worldmodel/estimation/model_families.py`, criteria in `requirements.json`). The
forecaster turns the estimate fitted at a forecast origin into point forecasts with
predictive sds for the family's own observable target, using only rows before the target
time plus declared conditional inputs of the target rows.

| Family | Target | Metrics | Baselines |
|---|---|---|---|
| `legislative` | Held-out roll-call votes of unrevealed members, given revealed members' votes on the same roll call (`options.holdout_revealed_members`, default every third member) | Brier, Brier skill, calibration, log score | member last vote and yea rate; revealed yea share |
| `elections` | District two-party share next cycle; seat count (secondary group `dem_seats`) | MAE/RMSE, CRPS, pinball, 80% coverage | district last and mean share; `incumbent_party_holds` (every seat stays with the party holding it, at that party's average holding share; for seats, the current split) |
| `influence` | Next-period panel outcome given realized exposure | MAE/RMSE, CRPS, coverage | unit last/mean; fixed effects without the influence coefficient |
| `trade` | Next-year international bilateral flows, balanced to realized exporter/importer totals (GE counterfactual not scored) | MAE/RMSE, CRPS, coverage | pair last/mean; frictionless flows with the same totals |
| `sanctions` | None: `NON_ESTIMABLE` (legal-rule determination; see below) | — | — |
| `conflict` | Country-month event counts (Hawkes intensity) | MAE/RMSE, CRPS, coverage | persistence, mean |
| `assets` | Next-day log return given realized factor returns (GARCH variance); unconditional return as secondary | CRPS, pinball 10/50/90, coverage, log score | last/mean return; same mean with constant volatility |
| `market_abm` | Return sd of the next non-overlapping window (`windows` rows, see `moment_windows`); abs-return autocorrelation and kurtosis as secondary | CRPS, coverage, MAE | previous window, mean of past windows |
| `commodities` | Next-period price given realized production and net imports; ending stocks as secondary | MAE/RMSE, CRPS, coverage | last/mean price |
| `monetary` | Policy rate given realized inflation and output gap | MAE/RMSE, CRPS, coverage | persistence, drift, mean |
| `regional` | Next-year log employment growth from the leave-one-out shift-share shock | MAE/RMSE, CRPS, coverage | region last/mean growth; year effect only |

`regional` declares `options: True`, so the estimator's declared options reach its forecaster. `options['interval_method']` selects the predictive spread: `pooled_year_draw` (default: one scale for every region, the pooled within-year residual variance plus the between-year variance of a fresh year draw) or `per_unit_year_mean` (each region's own residual mean square shrunk toward the pooled one by a single prior observation, plus the sampling variance of the estimated year level). The second exists because a pooled scale across regions whose residual volatility differs by an order of magnitude is simultaneously too wide for the stable ones and too narrow for the volatile ones, and pooled coverage averaged over regions can look acceptable while being wrong for every region.

Notes on family contracts that the holdouts rely on:

- **Elections.** `sigma_national` uses between-cycle degrees of freedom equal to cycles minus
  regressors that are constant within a cycle (constant, economy and midterm terms); it is
  `null`, and no holdout forecast is made, until identified. Share intervals add the
  coefficient covariance implied by cycle random effects.
- **Assets.** The holdout requires supplied factor series. The constructed equal-weight
  market contains the target returns.
- **Market ABM.** The SMM fit treats the sample as starting `burn_in` steps (default 0)
  after the declared initial state. `simulation_horizon_steps` lets the fit and holdout
  simulate once and reuse prefixes (stochastic paths are prefix-consistent). Window
  moments are forecast across `holdout_simulation_runs` seeds (`holdout_seed + run`) at the
  same elapsed steps, because wealth-share dynamics make return moments drift.
  `synthetic(seed, steps, agents_per_type, window, burn_in, truth, grid)` builds holdout fixtures.
- **Regional.** Migration and population growth are not scored (no population components
  in the fit contract), so `two_region_migration` cannot inherit validation from this family.
- **Sanctions.** Blocking, look-through exposure and route closure are exact consequences of
  designations and ownership records, checked by rule tests rather than forecast scores. The
  sanction coefficient is scored through the trade holdout when flows carry `sanction`.

### Conservation and accounting checks

- **Trade:** each country's trade balance equals minus its deficit, value added clears, world income equals world value added plus tariff revenue, and deficits sum to zero. Checked in the baseline and the counterfactual.
- **Sanctions:** exposure categories (blocked, look-through and clean) sum to the total per holder. Recorded ownership above 100 percent is rejected.
- **Influence:** LDA amounts are conserved across issues and agencies. Committee receipts equal attributed plus retained amounts, and transfers equal attributed plus unattributed amounts.
- **Market ABM:** integer cash and share totals are checked after every clearing step.
- **Commodities:** stock change equals production plus net imports minus consumption.
- **Regional:** population change equals births minus deaths plus external migration, and internal migration sums to zero. The integer stochastic path is exact.

## Process registry and materializer

`models.register_model_processes(registry)` registers:

- `<family>_model` for all eleven families. The `<family>_config` input is an object with unit
  `<family>-config`, and `<family>_forecast` is an object output with unit `<family>-forecast`
  (family-prefixed like `composition_config`, because one shared port name cannot carry eleven units). Each has
  `.deterministic` (cost 5) and `.stochastic` (cost 10) implementations. Stochastic seeds come
  from the materializer's per-binding RNG. Outputs are end-of-step `set` pressures;
  binding `parameters` merge into `config.parameters`.
- `taylor_rule_policy_rate` takes inflation and output gap (both `percent`) and outputs a policy rate (`percent`).
  It emits a `target` pressure with strength `-ln(rho)/quarter`, so the analytic integrator reproduces
  quarterly smoothing.
- `two_region_migration` takes employment rates (`ratio`) and base out-migration rates (`per_year`) and outputs
  `population_a`/`population_b` (`people`). It declares `conserved_quantities`, so both schedulers audit
  total population.
- `conflict_event_intensity` takes background and neighbor intensity (`events/month`) and outputs intensity. It is a mean-field
  relaxation toward `(mu + eta_n * neighbor)/(1 - eta_s)`.

`models.registry_with_models()` returns `default_registry()` plus these processes and
skips registration if a future import hook already added them.

## Strategic multi-actor layer (`worldmodel.models.games`)

A game specification declares:

- `actors`: each with `id`, `goals` (`metric`, `weight`, `direction`), `budget` (resource → amount),
  `observes` (private signal keys) and `actions`. An action has an `id`, `cost` (resource → amount), `overrides`
  (config patch) and `append` (dotted list path → items). Two actors setting the same leaf
  fails; appends compose.
- `scenarios`: `id`, `probability`, `overrides` and `signals`.
- `environment`: `{family, base, mode, seed}`, or an injected `evaluate(profile, scenario)` callable.
- `metrics`: `{name: {path: [...]}}` into the family report.
- `solver`: `pure_nash`, `best_response_dynamics`, `fictitious_play` (reports NashConv) or
  `support_enumeration` (two actors, complete information).
- `max_evaluations`: preflight bound on profiles × scenarios.

Private information uses agent-normal form: players are (actor, observed signal)
pairs. Actions whose cost exceeds the budget are excluded and reported.
`compare_strategies(spec, focal_actor, response, ranking, constraints)` has the other actors best-respond
to each focal candidate, then ranks candidates with `worldmodel.strategy.rank_outcomes`, which
`evaluate_strategies` now also uses.
`MultiActorEnvironment(env, actors)` partitions an `Environment`'s actions among actors.
It returns private observation views (`perception.split_observations`), enforces per-unit
action budgets and computes per-actor goal rewards. Privileged state is not exposed.

## Commands

```sh
python3 -m worldmodel models list
python3 -m worldmodel models describe trade
python3 -m worldmodel models requirements conflict
python3 -m worldmodel models hooks elections          # parameters + requirements for estimation
python3 -m worldmodel models processes
python3 -m worldmodel models fit legislative --synthetic
python3 -m worldmodel models fit monetary --data fred_panel.json --cutoff 2019-12-31 --publish
python3 -m worldmodel models simulate --request examples/models-trade.json
python3 -m worldmodel models simulate --request examples/models-conflict.json --mode stochastic --seed 3
python3 -m worldmodel models game --request examples/models-trade-war-game.json
python3 -m worldmodel models compare --request examples/models-trade-war-game.json
```

Examples: `examples/models-{legislative,elections,influence,trade,sanctions,conflict,assets,market-abm,commodities,monetary,regional}.json`
and `examples/models-trade-war-game.json`. All use synthetic or assumed parameters. In the trade-war example, the
pure equilibrium has the USA imposing a 25% tariff only after a low-elasticity signal, and China not retaliating. That
result is conditional on the illustrative baseline and is not a policy finding.

Tests: `tests/test_political_market_models.py` (recovery of known parameters on
synthetic data, mode agreement, accounting, registry/materializer integration, CLI),
`tests/test_estimation_model_families.py` (holdout validation passes on correctly specified
synthetic fixtures, fails on misspecified ones, and cannot see future rows) and
`tests/test_strategic_games.py`.

## Limits

These are laptop-scale reference implementations. Pure-Python PPML and GE loops
suit hundreds, not tens of thousands, of fixed-effect groups or bilateral-sector cells.
For BACI-scale work, install numpy or delegate to the estimation layer. No family
currently reads acquired datasets directly; loaders from catalog artifacts into these
`fit` contracts remain to be written. Equilibria and forecasts are conditional on declared models and
priors; none is causally calibrated.
