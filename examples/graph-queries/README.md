# Cross-dataset queries on the unified graph

Six questions that **no single dataset in this catalog can answer**, run against the index that
`python3 -m worldmodel unify` builds. [docs/unified-graph.md](../../docs/unified-graph.md) records
the real output of each one, the scope it was run in, and what it does not establish.

```sh
python3 -m worldmodel unify                                  # build data/world_evidence/index.sqlite
python3 -m worldmodel unify-resolve --workdir /tmp/resolve    # attach asserted identity clusters
python3 examples/graph-queries/run_all.py --save              # run all six, write outputs/
python3 examples/graph-queries/q2_county_economy_hazard_assistance.py --county 06001
python3 examples/graph-queries/q3_commodity_production_trade_price.py --commodity soybean
python3 examples/graph-queries/q4_legislator_bills_votes_money.py --bioguide P000197
python3 examples/graph-queries/q7_who_moves_on_this_bill.py --bill congress:bill:119-hr-1
WORLD_MODEL_INDEX=data/world_evidence/companies.sqlite python3 examples/graph-queries/q1_*.py
```

On a 131 GB index each script takes seconds to a few minutes; the first run of a query is slower
than the second because it is warming the page cache. `outputs/` holds the saved real output that
[docs/unified-graph.md](../../docs/unified-graph.md) quotes.

| Script | Question | Datasets that have to meet |
| --- | --- | --- |
| `q1_sanctioned_to_listed_holders.py` | From a sanctions listing, through a legal entity and its consolidation group, to reported securities holders | ofac_sanctions, other_sanctions_lists, opensanctions(_graph), sec_gleif, gleif_parent_relationships, sec_issuer_reference, sec_ownership_datasets |
| `q2_county_economy_hazard_assistance.py` | One US county's employment, population, demographics, hazard risk, weather, storms and disaster assistance | census_geography, census_business, census_population, acs_5yr_tables, fema_nri, noaa_climdiv, noaa_storm_events, openfema |
| `q3_commodity_production_trade_price.py` | One commodity from field production, through country balances and reported trade, to price | usda_agriculture, usda_fas_psd, un_comtrade, classifications |
| `q4_legislator_bills_votes_money.py` | One legislator's committees, sponsored bills, recorded votes, campaign finance and lobbying counterparties | congress_people, voteview_rollcalls, govinfo_billstatus, congress_gov_api, fec_candidates, fec, lda_lobbying |
| `q5_sanctioned_vessel_to_port_network.py` | A sanctioned vessel, its AIS identity, and the port and waterway network around it | ofac_sanctions, other_sanctions_lists, opensanctions(_graph), marine_ais, transport, airport_nodes |
| `q6_bank_filings_to_identity_to_group.py` | A bank from its SEC filing identity to its FDIC charter and its GLEIF corporate group | sec_issuer_reference, sec_gleif, gleif_parent_relationships, fdic_bank_financials, sec_financial_statements |
| `q7_who_moves_on_this_bill.py` | Who moves on one bill: sponsors, committees of referral and their seats, members who broke with their party on its roll calls, LDA clients whose filings cite its number, and the committee money those members received. Reads the `influence_panel` derived dataset and the input versions its manifest pins, not the index | influence_panel (govinfo_billstatus, voteview_rollcalls, congress_people, lda_lobbying, fec) |

`resolution_evaluation.py` is not a query: it measures what *inferred*, name-based matching would
add on top of the asserted identity links, against held-out published LEIs.

```sh
python3 examples/graph-queries/resolution_evaluation.py --workdir /tmp/resolve-eval --save
```

## Conventions every script follows

- **Every edge names its dataset.** Results carry `from_dataset` on each edge and observation, and
  a `which_dataset_supplied_which_edge` map. `datasets_used` is derived, not hand-written.
- **Asserted and inferred are never mixed.** Identity links come from published `same_as` rows,
  shared unique identifiers, and published crosswalk fields (`worldmodel.resolution.bridges`: the
  GLEIF registration-authority entity ID, the CUSIP inside a US ISIN). Where a script has to fall
  back on a name string (the FDIC leg of `q6`), the result labels that field `INFERRED, not
  asserted` and the limitations say so.
- **Every result carries `what_this_does_not_establish`.** Vintage mismatches, reported-versus-
  measured distinctions, out-of-scope datasets and missing crosswalks are stated, not implied.
- **Anchors are discovered, not hardcoded.** Each script searches the index for the best-supported
  chain, so the examples keep working as the catalog grows. Pass `--county` / `--bioguide` /
  `--commodity` to pin one.
- **Nothing is materialized that does not have to be.** Catalog-scale scans stream through
  `common.stream`; bounded traversal uses `Graph.neighborhood` / `paths`.
