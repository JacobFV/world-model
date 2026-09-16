# Identity, units and crosswalks

Many sources describe the same things with different identifiers, units, currencies,
geography vintages and classification revisions. This layer joins them *defensibly*:
every conversion, crosswalk allocation, identity link and belief carries its method,
inputs and evidence. Source records are never rewritten.

| Concern | Module | Main entry points |
| --- | --- | --- |
| Units, currency, price level | `worldmodel/units.py` | `parse_unit`, `convert`, `RateSeries`, `convert_currency`, `PriceIndexSeries`, `deflate`, `rebase_index` |
| Code systems and crosswalks | `worldmodel/crosswalks.py`, `worldmodel/reference/` | `CountryCodes`, `UsGeography`, `Crosswalk.apportion`, `naics_concordance`, `zcta_county_crosswalk`, `load_concordance_csv` |
| Entity resolution | `worldmodel/resolution/` | `link_mapping`, `shared_identifier_links`, `ResolutionEngine`, `inputs_from_records` |
| Identity lookup | `worldmodel/identity.py` | `IdentityIndex(records, accept_matches='reviewed')`, `candidate_links` |
| Reconciliation and belief | `worldmodel/reconciliation.py` | `reconcile_claims` (unchanged), `materialize_beliefs`, `validate_policy` |
| Resolved graph queries | `worldmodel/graph.py` | `build_from_records`, `attach_resolution`, `neighborhood`, `paths`, `degree_centrality`, `pagerank`, `flow_aggregate`, `resolved_entity` |
| CLI | `worldmodel/identity_cli.py` | `units-convert`, `country-code`, `county-lookup`, `crosswalk-apportion`, `reference-declarations`, `link-identifiers`, `resolve-entities`, `beliefs`, `graph-*` |

## Units

```python
from worldmodel.units import convert, RateSeries, convert_currency, PriceIndexSeries, deflate
convert(12, 'million kilowatt hours', 'GWh')                      # factor 1.0, steps listed
convert(180, 'BU / ACRE', 't/ha', commodity='corn')               # USDA 56 lb/bu standard weight
convert(1, 'USD/year', 'USD/day', day_count='act365')             # calendar units need a convention
rates = RateSeries('fred:DEXUSEU', 'EUR', 'USD', [('2024-03-01', 1.08)], evidence=[...])
convert_currency(100, 'thousand_EUR', 'USD', at='2024-03-01', rates=rates)
cpi = PriceIndexSeries('fred:CPIAUCSL', rows, base_period='1982-1984=100', currency='USD', frequency='monthly')
deflate(1000, 'USD', from_period='2020', to_period='2017', index=cpi)   # -> unit USD_2017, series refs
```

Dimensions include SI base quantities, calendar months, per-currency dimensions
(`currency:USD`, constant prices `currency:USD@2017`), index points tied to a base
(`index:1982-1984=100`), ratios versus `ratio_difference` (percentage points and basis
points), and separate count dimensions (people, establishments, firms, households, TEU,
shares, vessels, jobs).

The following conversions are **refused**:

- **Currency and price level.** No hidden constants: pass a dated `RateSeries` or
  `PriceIndexSeries`. Stale rates are refused unless `policy='previous'` and
  `max_staleness_days` are set. Incomplete years are refused unless `allow_partial` is set.
- **Ratios and index bases.** Percent never converts to percentage points. Index points
  with different bases need `rebase_index`.
- **Ambiguous symbols.** `ton`, `MT`, `oz`, `therm`, `hp`, `cal`, `kt`, `K` and bare
  `index` are rejected; use the explicit units named in the error.
- **Commodity-dependent conversions.** Bushels to mass, or barrels to energy, need
  `commodity=`. Approximate heat contents also need `allow_approximate=True`, or an
  explicit dated `factors=[...]` entry.

Records may carry optional fields, all validated in `model.validate_record` and all
backward compatible:

- `conversion`: `{from_value, from_unit, factor, method, series|steps}`. Dimensional
  factors are checked against the registry. Currency and deflation conversions must
  cite series IDs with dates or periods.
- `price_basis`: `nominal`, `real`, `index` or `unspecified`. `real` requires `base_period`.
- `vintage`, `retracts`, `supersedes` and `match`.

## Crosswalks and reference tables

Tracked tables live in `worldmodel/reference/`; see its `README.md` for the full list.
They cover ISO 3166 (including formerly used codes), UN M49, World Bank, COW and
Gleditsch-Ward periods with dated links to ISO; ISO 4217 currencies; US states; 2020 and
2023 counties with validity dates; Census county change events from the 1990s to the
2020s; Connecticut planning regions; NAICS 2022 codes; and the 2012→2017 and 2017→2022
NAICS concordances. `manifest.json` pins source URLs, licences and SHA-256 hashes.
`build_reference_files.py` regenerates everything from official downloads.

```python
from worldmodel.crosswalks import CountryCodes, UsGeography, naics_concordance
CountryCodes().convert('345', 'cow', 'iso3', at='2010-01-01')    # SRB (YUG in 1995, SCG in 2004)
CountryCodes().convert('CS', 'iso2', 'iso3')                     # refused: reused code needs a date
walk = UsGeography().county_crosswalk('2000-01-01', '2020-01-01')
walk.apportion({'02232': 100})       # split by published populations; conserves the total
walk.apportion({'08001': 10})        # refused: Broomfield transfer has no county weights
walk.apportion({'08001': 10}, unweighted='equal')   # documented assumption, error_bound=10
naics_concordance(2012, 2022)        # composed via 2017; split weights stay unknown
```

`Crosswalk.apportion` reports:

- totals in and out, and the conservation residual (it raises if the residual is non-zero)
- unmapped mass
- weights that needed normalizing, with their deviation
- equal-split assumptions and a worst-case `error_bound`

Intensive values (rates, prices) use `weighted_mean` with an explicit basis. Rows carry
half-open validity intervals; a dated crosswalk refuses undated queries.

Large or licence-restricted datasets are declared in
`worldmodel/reference/acquisition_declarations.json`, in the `acquisition` block format
(`python3 -m worldmodel reference-declarations`):

| Group | Datasets |
| --- | --- |
| US geography | ZCTA↔county 2020; ZCTA 2010↔2020; county subdivision 2010↔2020; CBSA delineations for 2018, 2020 and 2023 |
| Trade and industry codes | Census HS↔NAICS/SITC trade concordances; WITS HS→SITC; BEA IO/SUT tables |
| Identifiers | GLEIF golden copy and ISIN–LEI mapping; SEC ticker/exchange data and bulk submissions; FEC cn/cm/ccl files; congress-legislators IDs; Voteview members |
| Sanctions and banks | OFAC SDN; OpenSanctions (non-commercial licence); FDIC institutions |

Sources that could not be located are listed there as well.

## Entity resolution

**Deterministic links** come from published mappings (`MAPPING_SPECS`): LEI↔CIK,
LEI↔UK company number, ISIN↔CUSIP, CIK→ticker@MIC, FEC candidate→committee, bioguide↔FEC
and bioguide↔ICPSR, UEI↔LEI, OFAC↔OpenSanctions, FDIC cert↔RSSD, and FIGI→ticker. Each spec
declares its relation (`same_as`, `listed_as` or `authorized_committee`), cardinality and
whether it is dated. Rows that violate cardinality within overlapping periods become
conflicts, not links. `shared_identifier_links` connects source entities that carry the same
unique identifier, and reports any entity holding two concurrent values.

**Published-identifier bridges** (`worldmodel/resolution/bridges.py`) read the same kind of
published crosswalk out of fields that are not identifier assertions, because several sources
print another registry's identifier in an attribute rather than as a claim:

| Bridge | Published field it reads | Mapping spec |
| --- | --- | --- |
| `gleif_sec_cik` | GLEIF LEI-CDF `Entity.RegistrationAuthority`, authority `RA000665` (US SEC), decimal entity ID | `gleif_sec_cik` |
| `gleif_companies_house` | the same fields with authority `RA000585` (Companies House) | `gleif_companies_house` |
| `gleif_isin_cusip` | `issuer_security` edges to `isin:<US or CA ISIN>`; the nine-character NSIN is the CUSIP (ISO 6166), with the check digit recomputed | `isin_cusip` |

Two rules keep a bridge from manufacturing identity. The **authority code decides the
namespace**, never the value shape: a numeric GLEIF registration-authority entity ID under a
state registry is not a CIK, and on the published golden copy 2,851 such values collide with
real CIKs by coincidence. And a bridge row is held to the **cardinality its spec declares**:
where two LEIs print one CIK, or two print one UK company number, the rows are refused and
reported (`bridge_conflicts`) rather than merged. `unify-resolve --no-bridges` turns them off,
which is the baseline the "nothing inferred by default" regression test compares against.

**Probabilistic matching** (`ResolutionEngine`, SQLite work file) runs these stages:

1. **Normalize.** Legal suffixes are recorded separately. Cyrillic and Greek are
   transliterated, accents folded, person names reordered, and USPS address
   abbreviations applied.
2. **Block.** Keys are a name prefix, first token plus ZIP3, a two-token Soundex, a sorted
   token pair, and identifiers. Blocks above `max_block_size` are skipped and reported,
   so the engine never falls back to O(n²).
3. **Compare** candidate pairs using Jaro-Winkler levels, TF-IDF cosine levels,
   postal/city/country agreement, and identifier agreement or conflict. `workers`
   splits this across forked processes.
4. **Fellegi-Sunter scoring with EM.** With `training='auto'`, if enough candidate pairs
   share a published unique identifier, m is estimated from those labeled matches and u
   from candidates whose unique identifiers conflict. These labels come from the same
   blocked population, so blocking does not bias them. EM then fits only the prior.
   Without identifiers, unsupervised EM runs with u initialized from random pairs. That
   mode is much weaker on hard data; see the performance section below.
5. **Cluster.** Greedy union in descending probability order, subject to these
   constraints:
   - no conflicting unique identifiers
   - no rejected review
   - no entity-type conflict
   - a maximum cluster size
   - transitivity: never merge clusters that contain a scored non-match pair

   Accepted reviews force links. Rejected merges and residual transitivity violations are
   reported.
6. **Outputs.** `match_assertions` yields `same_as` records with
   `epistemic_status: inferred`, `confidence`, and a `match` block holding score, weight,
   method, model digest, per-comparison features, decision (`auto_match` or
   `possible_match`) and `reviewer_status`. `resolved_view` returns input, model and
   cluster digests plus the policy and reviews. Rerunning with the same inputs, reviews
   and policy yields the same `view_digest`, whatever the input order.

`IdentityIndex` joins components through unmarked or `source_asserted` `same_as` links,
and through inferred links only once they are reviewed as `accepted`. Unreviewed inferred
links appear only in `candidate_links`. `accept_matches='all'` is an explicit opt-in.

```sh
python3 -m worldmodel resolve-entities --graph reference_evidence --workdir /tmp/res --observed-at 2026-09-15 --workers 8
python3 -m worldmodel graph-attach-resolution --workdir /tmp/res
python3 -m worldmodel graph-neighborhood org:acme --hops 2 --resolved --predicate owns --min-weight 0.5
python3 -m worldmodel graph-paths org:a org:b --max-hops 4 --resolved
```

## Current belief

`materialize_beliefs(records, known_at=..., at=None, policy=...)` groups evidence by
(subject, variable, period, dimensions). The policy selects:

- **Rule:** `latest_vintage`, `reliability_weighted` (source priors, optional half-life)
  or `source_priority`.
- **Staleness:** `stale_after_days`, set globally or per variable.
- **Conflict threshold:** `conflict_share`.
- **Unit normalization:** `units`, using dimensional conversion only.
- **Inferred claims:** `include_inferred`.

Records named in `retracts` (or marked `epistemic_status: retracted`) are removed. Records
named in `supersedes` are kept but not selected.

Each belief has a `status`:

- `known`: one value is selected.
- `conflicting`: `value` is null; `leading_value` and every alternative's share are reported.
- `unknown`: only explicit missing evidence exists.
- `retracted`: all evidence was retracted.

`truth_value` separates an observed `False` from unknown (`None`). The output includes a
conflict report and a digest over the policy and beliefs. `python3 -m worldmodel beliefs`
publishes the result as an artifact.

## Performance

Measured on 2026-09-15 on a 20-core ARM64 machine with 121 GB RAM, using fictional
organizations with duplicates, typos, missing fields and 30% LEI coverage.

**Resolution, 1M records, 8 workers:** 108.6 s end to end (9,211 records/s), 2.8 GiB peak RSS.

| Stage | Time (s) |
| --- | ---: |
| Load | 33.1 |
| Blocking | 17.3 |
| Compare | 24.3 |
| EM | 7.9 |
| Cluster | 21.5 |

- 9.79M candidate pairs, a reduction ratio of 0.99998; 1 oversized block was skipped.
- Pairwise precision 0.9992, recall 0.870 (identifier-supervised training).

**Graph, 1M edges:** build 14.6 s, attaching 343k resolved entities 1.8 s, 3-hop resolved
neighborhood 2.1 ms, bounded path query 0.9 ms, degree centrality 0.8 s.

**At 50k records:** precision 0.998 and recall 0.905 with identifier training, versus
precision 0.64 with unsupervised EM.

An earlier run showed that an unscoped name-prefix blocking key grows quadratically with
corpus size (149M pairs at 1M records). The key is now scoped by ZIP3 or country.

To rerun:

```sh
WORLDMODEL_SCALE_TEST=1 WORLDMODEL_SCALE_RECORDS=1000000 WORLDMODEL_SCALE_WORKERS=8 \
  python3 -m unittest tests.test_resolution_scale
```

## Limits

- **Synthetic benchmark.** Scale numbers come from fictional data. Real precision and
  recall need labeled samples from each source pair.
- **Unsupervised fallback.** EM without identifier labels over-links look-alike names.
  Use `training='identifier'`, reviews or a stricter threshold.
- **Conditional independence.** Fellegi-Sunter assumes it; correlated comparisons (name
  with TF-IDF) overstate evidence.
- **Transliteration.** It is simplified and lossy. There is no CJK romanization and no
  nickname table.
- **Acquired but not yet loaded.** Population-weighted ZCTA and county crosswalks, CBSA
  vintages, HS/BEA concordances and weights for NAICS splits are declared for
  acquisition, not loaded.
- **Scale beyond one node.** The graph index and resolution work file are single-node
  SQLite. 10M-record runs are bounded by disk and pair counts, not RAM, but have not been
  measured in this session.
