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

Existing limits remain: 1..100 firms/households each, at most 1,000 days and 100,000
actor-days; 10,000 reserved transactions, 40,000 postings and 1,000,000 transaction ×
ledger-balance cells over retained history. Every purchase entry reserves work, even
zero/rationed orders. Each production request reserves origination and wage-payment
slots. Interest reserves payment and potential default slots for every firm/day,
including omitted firm actions. A collateral declaration reserves sale, repayment
and write-off slots. All loops use existing actors/orders, not a new cross-product.
The bounded runner preflights the whole policy sequence before any transition.

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
