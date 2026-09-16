# Optional daily economy mechanisms

These mechanisms extend the synthetic commercial-bank economy. They demonstrate
accounting and execution consistency; their coefficients are not empirically fitted
and their responses are not causally validated. Existing configurations without the
options below retain their original production, purchasing and ledger behavior.

## Run the integrated example

```sh
python3 -m worldmodel.cli coupled-economy --request examples/economy-policy-feedback.json
```

The Python equivalent is `simulate_coupled_economy(json.load(open(path)))`.
Public APIs and process ports are unchanged:

- `initialize_economy(config)` accepts banks, firms, households and optional `mechanisms`.
- `step_economy(state, policy, shock=None)` returns a new state, advancing one day.
- `simulate_coupled_economy({initial_state, policies, shocks})` runs a bounded sequence.
- The existing `coupled_economy.deterministic` process consumes `economy_state`
  and `economy_policy`. Its cadence remains exactly 86,400 seconds. The Python
  transition accepts separate shocks; no new process port is implied.

The four-day example includes labor sharing constraints, production credit, a
production-cost shock, a policy-rate rule, lagged demand expectations, inventory
loss and funded collateral recovery. Its expected final inventory is 2 goods,
valued at $13.60 production cost; loan principal is zero after $15 recovery and
$19 write-off. Total interest paid is $0.03. Bank A equity is $81.03, bank B equity
is $50, and aggregate reserves remain $200. The firm is bankrupt.

## Ordering and state

Each transition validates inputs and preflights work before ledger transactions.
It then:

1. Applies current direct/rule-based interest rates and labor-supply decisions;
   computes prices from opening inventory and previous unmet demand.
2. Applies inventory losses and production-cost shocks; processes declared
   bankruptcies and their optional funded sales in stable firm-ID order.
3. Allocates labor and funds production in that same order, originating only the
   required credit and transferring production outlays to each designated worker.
4. Processes purchases by household ID, then firm ID, consuming purchased goods.
5. Pays interest on opening principal and old arrears; processes an unpaid-interest
   bankruptcy if configured; then makes requested principal repayments.
6. Checks ledger, aggregate reserves, deposits, principal, equity, goods and optional
   inventory-cost identities. Records the step and updates next-day expectations.

Failed transitions leave caller state, policy and shocks unchanged. Returned history
retains previous steps without replay or policy rewriting. There is no stochastic
kernel: identical state/policy/shock inputs produce identical results, including
across JSON serialization. No seed-dependent generalization claim follows.

## Labor

Households may declare `labor_capacity`; firms may declare `labor_per_unit`.
Both are integers: capacity is worker-units per daily step, and labor per unit is
worker-units per good. Capacity accepts zero; labor per unit must be positive.
The policy input `households[id].labor_capacity` changes capacity prospectively
and persists. Omitted capacity means unconstrained labor; omitted labor per unit
means one worker-unit per good. Values are bounded by 1,000,000.

A firm receives the minimum output allowed by requested production, physical
capacity, available labor, deposits plus feasible credit, and settlement reserves.
Unfunded production consumes no labor and creates no goods. One worker shared by
multiple firms is allocated in stable firm-ID order. `history[].labor` reports
capacity (`null` for unconstrained), requested, allocated and unmet worker-units.
Unmet labor includes finance/physical rationing as well as scarcity; it is not an
estimate of involuntary unemployment.

Production cost remains `unit_cost` dollars per good paid to the designated worker.
A shock `unit_cost:{firm_id:positive_cent_amount}` changes this cost prospectively.
The example labels such a shock an energy-cost scenario, but there is no separate
energy supplier, energy stock, or physical input-output calibration.

## Interest and monetary policy

```json
{"mechanisms":{"interest":{
  "policy_rate":0.04,"spread":0.02,"day_count":365,
  "duration_days":1,"insufficient":"defer"
}}}
```

Policy rate and spread are annual fractions in [0,1]; the loan rate is their sum.
`day_count` is an integer in 1..366, default 365. Duration is exactly one day,
optionally written as `duration_days:1`; a conflicting duration is rejected.
`insufficient` must explicitly be `defer` or `bankrupt`.

Daily new interest, in integer cents, is:

`round_half_up(opening_principal_cents * (policy_rate + spread) / day_count)`.

New loans receive their first interest charge the following day. No fractions of a
cent are accumulated between days. Interest is paid after sales and before ordinary
principal repayment. A banking transaction
`{kind:"interest",bank,borrower,amount}` debits the borrower's deposit and credits
bank equity by the same cent amount. Principal and reserves do not change. The bank
must have a loan-account entry for the borrower, including zero principal when old
arrears remain. Payment cannot exceed the borrower's deposit.

Unpaid interest persists as `firm.interest_arrears`. These are memorandum obligations,
not booked bank receivables or capitalized principal. Subsequent active days attempt
to pay old arrears plus new interest. Under `bankrupt`, insufficient payment first
uses available cash, retains remaining arrears, marks the firm bankrupt and writes
off principal. A bankrupt firm no longer accrues or pays interest; existing memorandum
arrears remain visible. A declared opening bankruptcy is processed before that day's
interest charge. There is no silent arrears cancellation, compounding or recovery.

`policy.policy_rate` overrides the current rate and persists; previous history stays
unchanged. Optionally configure `interest.policy_rule` with all of:

```json
{"reference_rate":0.04,"inflation_target":0.02,
 "inflation_response":1.5,"output_response":0.5,
 "minimum_rate":0,"maximum_rate":0.25}
```

Without a direct override, each rule-based day requires explicit `shock.inflation`
and `shock.output_gap` fractions in [-1,1]. The equation is:

`clamp(reference_rate + inflation_response*(inflation-inflation_target)
       + output_response*output_gap, minimum_rate, maximum_rate)`.

Rate levels/targets/bounds are in [0,1], responses in [0,10]. A direct override
wins and is bounded by [0,1], independently of the rule's narrower bounds.
`history[].monetary_policy` records the applied rate, spread, annual loan rate,
duration and source. This is an assumed policy reaction rule, not an estimated
central-bank decision process. It neither creates reserves nor models a central-bank
balance sheet, liquidity facilities or regulatory capital.

## Estimation hooks

These keys let estimates from `worldmodel.estimation` drive the simulation. The
parameter names match the `maps_to` paths in `requirements.json`. Every key is
optional, and leaving it out keeps behavior bit-identical. Bind estimates with
`initialize_economy(config, calibration=...)`, `calibrate_state(state, calibration)`,
`simulate_coupled_economy(..., calibration=...)` or the process parameter
`{"calibration": record}`. `state['calibration']` records each bound path with its
component, `estimate_id`, `record_id` and `validated` flag.
`parameter_provenance(state)` labels every mechanism parameter as estimated or
assumed. Both backends share these functions, and the numpy backend reproduces them
exactly.

- **Interest pass-through.** Set `interest.pass_through` (0..1.5, default 1),
  `adjustment_speed` (0..1 per day, default 1) and `impact_pass_through` (-0.5..1.5,
  default 0). The annual loan rate then persists as `interest.loan_rate`. Each day:
  - `pre = loan_rate + impact_pass_through * (policy_rate - previous policy_rate)`
  - `loan_rate = pre + adjustment_speed * (spread + pass_through*policy_rate - pre)`

  Rates are clamped to [0,3] and computed in exact rationals. Interest due uses this
  rate. The first day starts at the long-run rate. `monetary_policy` records
  `long_run_loan_rate` and the coefficients.
- **Deposit interest.** Configure `deposit_interest` with `spread` (-1..1),
  `pass_through`, optional `adjustment_speed`, `impact_pass_through` and
  `day_count`. It requires `interest`. The deposit rate follows the same
  error-correction rule and is floored at 0. After repayments, each account earns
  `round_half_up(opening_deposit * deposit_rate / day_count)`, households first
  then firms, each in ID order. The payment is a `banking` `deposit_interest`
  transaction: deposits rise and bank equity falls. Deposits then change by
  `created - repaid - interest_paid + deposit_interest_paid`, and equity by
  `interest_paid - defaulted - deposit_interest_paid`.
- **Default hazard.** Configure `default_hazard` with `intercept`, `persistence`,
  `unemployment_sensitivity`, `rate_sensitivity`, a `seed` (uint64), the initial
  period `hazard` in (0,1), `unemployment` (updated by shock `unemployment`) and
  `period_days` (default 91.3125). At the first step of each new period:
  - `hazard = logistic(intercept + persistence*logit(hazard) + unemployment_sensitivity*unemployment + rate_sensitivity*i)`,
    where `i` is the policy rate that closed the previous day.
  - The daily probability is `1-(1-hazard)^(1/period_days)`.

  Before production, each active firm in ID order defaults when a counter-based
  splitmix64 draw of (seed, step, SHA-256 of the firm ID) falls below the
  probability. A default writes off principal like a declared bankruptcy (cause
  `default_hazard`). Draws are independent of order and backend. `history[].default_hazard`
  records the hazard, the probability and the default count.
- **Deposit growth.** Configure `deposit_growth` with `mean_growth_per_month`,
  `persistence`, `rate_semi_elasticity` and `period_days` (30.4375). The monthly
  target is `g = mean*(1-persistence) + persistence*g_prev +
  semi_elasticity*(policy rate change)`. Households keep
  `round_half_up(opening_deposit * exp(g/period_days))` (ppb-quantized) out of
  purchases. Deposits cannot grow without credit in this closed economy, so this
  is a retention rule, not money creation.
- **Credit growth.** Configure `credit_growth` with `mean_growth_per_month`,
  `persistence` and `base_credit_limit`. It generates
  `credit_limit = base * exp(log_index)` for firm actions that omit
  `credit_limit`, then adds `g/period_days` to `log_index` each day. Explicit
  limits still win.
- **Price cost pass-through.** `price_feedback.cost_pass_through` (0..1.5) adds
  `cost_pass_through*(unit_cost - last_unit_cost)/last_unit_cost` to the quote
  fraction. `firm.last_unit_cost` stores the opening unit cost of the previous quote,
  so a `unit_cost` shock moves the next day's quote.
- **Labor.** `labor.employment_output_elasticity` (0..2) is used after each step.
  Every household with `labor_capacity` has its capacity scaled by
  `(output/previous_output)^elasticity`, rounded half up. `household.labor_output`
  tracks that previous output. Capacity is unchanged when either output is zero.
- **Policy-rule smoothing.** `policy_rule.smoothing` (0..0.999, estimated per
  `period_days`, default 91.3125) gives
  `rate = rho*previous + (1-rho)*rule` with `rho = smoothing^(1/period_days)`,
  before the bounds are applied (source `smoothed_bounded_rule`).

The work budget reserves one slot per firm for hazard write-offs and one per account
for deposit interest.

## Inventory costs and financial metrics

Enable `mechanisms.inventory_valuation:true`. Quantity remains integer goods;
`firm.inventory_value` is exact-cent aggregate production cost. Supply an opening
value explicitly, or initialization assumes opening quantity times current unit
cost. This default is a scenario assumption, not a recovered historical valuation.

Production adds its full outlay to inventory cost. A sale or destruction allocates
`round_half_up(total_cost_cents * removed_units / quantity_before_removal)` cents.
Remaining cost is reduced by precisely that allocation; exhausting inventory removes
all remaining cost. Consequently rounding never creates or loses total book cost.

Per-firm metrics distinguish:

- `revenue`: funded sale proceeds, including explicit collateral sales.
- `cost`: production cash outlay during this day.
- `profit`: the existing metric, unchanged: revenue minus production cash outlay.
- `cost_of_goods_sold`: weighted-average production cost of goods sold.
- `inventory_write_down`: cost of goods destroyed by the explicit loss shock.
- `operating_profit`: revenue minus cost of goods sold and inventory write-down.
- With interest enabled, `interest_due` means this day's new charge; `interest_paid`
  includes payments of old arrears, and `cash_surplus_after_interest` subtracts those
  actual payments from `profit`.

Operating profit excludes financing, taxes, capital charges and unbooked arrears.
Inventory book cost is not collateral market value. This is not a full firm GAAP/IFRS
balance sheet or an accrual model for every liability.

## Bounded price and demand response

`mechanisms.price_feedback` requires `initial_price`, `minimum_price`,
`maximum_price` (positive cent prices), `target_inventory` (integer goods),
`adjustment` and `unmet_demand_response` (fractions in [0,1]). For an omitted firm
price action, the opening-day quote is:

`round_to_cent(last_price * (1 +
  (adjustment*(target_inventory-opening_inventory)
   + unmet_demand_response*last_unmet_demand) / max(1,target_inventory)))`,

clamped to the declared minimum/maximum. `last_unmet_demand` is yesterday's total
unfilled adjusted demand, capped at 1,000,000 goods. Opening inventory is the previous
close, before today's loss shock or production. An explicit positive-cent `price`
action overrides the automatic equation and its narrower bounds. Final quotes are
recorded in `history[].prices`. Bankruptcy does not create new price-based sales.

`mechanisms.demand_feedback` requires positive-cent `reference_price` and supports
`elasticity`, `energy_response`, `rate_response` in [0,4], default zero. A household's
explicit purchase quantity becomes a base demand quantity:

`floor(base_units * (reference_price/current_price)**elasticity * factor)`,

where `factor = clamp(1-energy_response*previous_expected_energy_change
                       -rate_response*previous_expected_rate_change, 0, 4)`.

Adjusted demand is capped at 1,000,000 goods per existing order. Powers use finite
floating-point arithmetic; no demand orders are invented for absent household/firm
pairs. `expected_energy_change` and `expected_rate_change` shocks accept fractions
in [-1,1], persist and affect demand only on the following day. Initial expectations
are zero. Rate expectations are explicit assumptions, not automatically inferred
from today's policy rate. Demand events record the base, adjusted quantity, price
and lagged expectations. Purchases still require stock, deposits and bank reserves.

## Funded collateral recovery

During an explicit `bankrupt:true` firm action, optionally supply:

```json
{"collateral_sale":{"buyer":"worker","units":3,"unit_price":5}}
```

The buyer must be an existing household. Units are bounded nonnegative integers,
must not exceed inventory after today's loss, and sale price is positive exact cents.
The realized sale is rationed to buyer deposits and sending-bank settlement reserves.
Purchased goods are consumed, as with other household purchases. No loan finances
recovery, and no noncash bank asset or market appraisal is introduced.

Proceeds first enter the firm's deposit account. At most those proceeds repay
outstanding principal; the remainder of principal is written off against bank equity.
Excess proceeds and pre-existing firm deposits remain with the bankrupt firm. Inventory
cost is allocated separately from sale price. The audit records requested/sold units,
proceeds, principal recovered, remaining write-off and optional book cost. Zero sales
are allowed; repeated bankruptcy does not write off a loan twice. A previously
bankrupt firm cannot request another positive collateral sale. Unpaid-interest
bankruptcy has no automatic collateral buyer and performs no implicit sale.

## Work bounds and checked identities

All size and work caps are named limits in `worldmodel/limits.py` (see
`describe_limits()`). Defaults are sized for national-scale synthetic runs and can
be lowered or raised per call (`limits={...}`), per block (`use_limits(...)`), via
the `WORLD_MODEL_LIMITS` JSON environment variable, or with CLI `--limits`. A
rejected request raises `LimitExceeded` (a `ValueError`) naming the limit.

| Limit | Default | Replaces |
|---|---:|---|
| `coupled_max_firms` / `coupled_max_households` | 250,000 / 2,500,000 | 1..100 each |
| `coupled_max_steps` | 36,500 | 1,000 days / policies |
| `coupled_max_step_transactions` | 50,000,000 reserved slots per step | 10,000 cumulative transactions |
| `coupled_max_ledger_cells` | 20,000,000 balance cells | transactions × cells ≤ 1,000,000 |
| `coupled_max_retained_postings` | 20,000,000 (full-history journals) | 40,000 postings |
| `coupled_max_history_actor_steps` | 50,000,000 (full-history rows) | 100,000 actor-days |
| `economy_max_quantity` | 1,000,000,000 goods/labor units | 1,000,000 (also the demand and unmet-demand clamps and the no-labor-limit sentinel) |
| `banking_max_banks` / `banking_max_transactions` | 10,000 / 50,000,000 | 100 / 10,000 |
| `banking_max_audit_cells` | 50,000,000 (only with `audit='full'`) | 1,000,000 |
| `economy_max_days`, `economy_max_businesses`, `economy_max_business_days` | 36,500 / 1,000,000 / 1e9 | 10,000 / 1,000 / 100,000 |
| `economy_max_retained_business_days` | 5,000,000 snapshot rows (`history='full'`) | — |
| `economy_max_replay_firm_days` | 1e9 cumulative replay firm-days | 100,000 |

Every purchase entry still reserves work, even zero/rationed orders. Each
production request reserves origination and wage-payment slots. Interest reserves
payment and potential default slots for every firm/day, including omitted firm
actions. A collateral declaration reserves sale, repayment and write-off slots.
The bounded runner preflights the whole policy sequence before any transition.

The quadratic work the old bounds protected against is gone: transactions update
one integer-cent ledger in place (`banking.apply_transaction`, touched-bank checks
only) instead of re-parsing and exporting the whole ledger per transaction, actor
lookups are dictionaries, and step outputs share retained history records rather
than deep-copying history. Work per step is linear in actors plus purchase entries.

History retention is explicit (`initialize_economy(config, history=...)`,
`simulate_coupled_economy(..., history=...)` or `history_policy` in the initial
config/state): `'full'` (default, identical records to earlier releases),
`'summary'` (one exact summary per step: accounting, cents-exact totals, event
counts, labor totals, monetary policy, work) or `{'mode': 'every_n', 'every': N}`
(full records every N steps, summaries otherwise). `step == len(history)` in all
modes; summaries of a full run equal `summarize_record` of its records.

### Vectorized backend

`simulate_coupled_economy(..., backend='numpy'|'auto')` and
`worldmodel.coupled_economy_numpy.ArrayEconomy` (optional `fast` extra) keep int64
cent columns and reproduce the reference exactly (final state, summary records and
errors are tested equal on randomized and deliberately rationed scenarios).
Order-dependent couplings (shared worker labor, interbank reserves, household
deposits across purchases, firm inventory across buyers) are solved as the unique
fixed point of the sequential recurrence; weighted-average cost allocation runs in
exact buyer-rank rounds. Full audit records, declared bankruptcies/collateral sales
on active firms and out-of-range balances fall back to the reference step
(`diagnostics` counts them). `ColumnarPolicy.from_arrays` avoids building
per-household dictionaries; `ArrayEconomy.checkpoint(dir)`/`restore(dir)` use
verified binary array checkpoints. Measured throughput is in
[scale-benchmarks.md](scale-benchmarks.md).

Every step checks:

- Bank reserves plus loans equal deposits plus equity; total reserves are fixed.
- Deposit change equals new principal minus repayments minus interest paid.
- Principal change equals new loans minus repayments minus write-offs.
- Equity change equals interest paid minus principal write-offs.
- Opening goods plus production minus sales minus destruction equal closing goods.
- With costing enabled, opening cost plus production outlay minus sales cost minus
  write-down equals closing inventory cost.

Validation tests cover malformed values, shared/zero labor, cent rounding, rate
changes, default/arrears, price and expectation lags, funded recovery, work preflight,
serialization and preservation of previous history. These checks establish mechanism
consistency within the declared boundaries, not empirical realism.
