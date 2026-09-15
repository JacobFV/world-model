# Strategic systems expansion

Approved intent: user requested transport, people, banking, energy, anticipatory business behavior and broader strategic uses. Build executable bounded systems and extend source acquisition; do not claim complete global coverage or causal calibration.

## Global constraints

Python standard library, existing immutable Store/Runner provenance. Retain at most 100 source rows / 1 MiB per new sample; shared 64 MiB temporary disk buffer. No unbounded downloads. Synthetic scenarios explicitly labeled. Source/model availability and calibration status independently reported. Existing examples/tests remain compatible. No Git repository; no commits/worktree deletion.

## Subsystem A: transport and traveler planning

worldmodel/transport.py exposes network_from_osm(elements, speed_kph=30)->dict, route(network, request)->dict, traveler_events(route_result, person_id, evidence=None)->list. Network has nodes [{id,lat,lon}], edges [{id,source,target,mode,duration_seconds,cost,capacity?,departures?:ISO list,evidence:[]}]. Directed OSM node connectivity and one-way rules preserved; no invented connections from center points. Routing supports road/walk/air/sea/rail/transfer, earliest arrival or least cost with explicit bounded search, permitted modes, closed edges, demand/capacity, departure time and scheduled departures. Traveler links are scenario-only unless supplied evidence; distinguish planned journey from actual boarded vehicle. Add fictional multimodal network/travel request examples and tests.

## Subsystem B: bank-energy-business economy

worldmodel/economy.py exposes simulate_economy(config)->dict and example_economy()->dict. Config explicit initial bank/business/supplier balance sheets, daily steps, credit limits, bank rates, policy-rate and energy-price shock schedule, expectations and inventory policies. Output snapshots [{step,... metrics}], journal balanced postings, events and assumptions/status. Preserve cash/loan accounting, credit limits, nonnegative inventory, default recognition. Firms anticipate higher energy prices by changing target inventories subject to cash/credit/storage constraints; borrowing costs discourage inventory. Production consumes energy and earns sales; insolvency/stockouts explicit. Return final metrics suitable for strategy objectives. Include simple bond valuation/duration and an accounting validator. A register_economy_processes(registry) adapter permits a unitless object economy_state -> economy_state set pressure, one step per explicit cadence. Parameters always labeled illustrative unless supplied fitted estimates.

## Subsystem C: standardized source expansion

worldmodel/strategic_sources.py exposes SOURCE_IDS, normalize(context), schema()->{entity_types,relations,variables}. Create bounded dataset configs for usable Fed/FRED rates/inflation/energy series, Treasury yields or fiscal series, bank financial data, SEC fundamentals, OSM topology, airport nodes, and marine/AIS availability. Verify official endpoint docs. Explicit unavailable credentials/oversize/missing licensed feeds, no pretend acquisition. Normalize successful samples with immutable raw row evidence, units, valid/known times, canonical source identifiers. Prefer finite dated windows to first-ever historical rows. No global live tracking promises. Sampling uses existing engine; source headers may need optional config/env support. Root will own generic sampling changes if requested.

## Subsystem D: strategy, calibration, integration

worldmodel/strategy.py: evaluate_strategies(config)->dict; finite candidate policies × stress scenarios, hard run budget, objective direction, constraints, expected/worst-case values, regret and feasible ranking. Feed economy simulator; no magic optimizer or unsupported causal claims. worldmodel/calibration.py: fit_ar1(rows, train_end)->dict; chronological train/holdout only, missing values excluded explicitly, fitted coefficients and MAE versus persistence, source row IDs retained. Empirical descriptive fit is not causal calibration. Generic immutable report publication stores exact inputs/raw refs/code/results and typed output graph if applicable. CLI commands route, economy, strategies, calibrate, sources, strategic-build. Expand ontology by modular schema additions. Unify normalized strategic evidence into separate strategic_evidence graph while preserving original real sample graph and synthetic scenario separation.

Acceptance: real bounded source pulls attempted and profiled; usable normalized evidence linked to graph; transport and economic examples run; strategy comparisons publish auditable results; chronological calibration runs on a real series when available; all appropriate tests pass. Document strategic affordances (routing/disruptions, liquidity/inventory, rate/energy stress, policy comparison, bottlenecks, data gaps) and explicitly remaining global coverage/validation gaps.

## Accepted expansion: fields, actor kernels, lifecycle and institutions

User steering: fields and topologies are the underlying substrate; typed graphs are lazy descriptions. Add an independent bounded field kernel supporting explicit supports/cells, adjacency, measures, intensive/extensive fields, conservative diffusion/transport and territorial overlays. Projection generates only requested cells/fields/relations, never requires eager whole-world graph construction. Spatial refinement cannot invent information. Register a fields_state process adapter and expose fields/field-view CLI reports.

Add multiple numerical implementations for human/business/government behavior with explicit state and assumptions, supporting rational utility, stochastic choice, behavioral/adaptive rules, production/demand and government policy reactions. Caller can choose implementation_id explicitly in process bindings. Keep existing agent backend contract. No claim that any policy reaction is causally calibrated.

Add append-only lifecycle events for birth/incorporation/growth/merge/death/dissolution, preserving known and valid times and provenance; no destructive entity deletion. Actors cannot evolve outside lifecycle validity. Expand institutional schema for conflict/war, military units/alliances, academic institutions/researchers/publications/citations, exchanges/tickers/instruments, posts/publishers/platforms, laws/statutes/regulations/cases/rulings/courts, geographic territories/watersheds/water bodies/land cover. Schema does not imply acquired observations. Explicit conflicts between territorial claims remain evidence.
