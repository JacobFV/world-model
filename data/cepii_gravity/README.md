# cepii_gravity

CEPII Gravity database, release V202211: country-pair x year frictions and country-year
macro/membership variables, 1948-2021.

## Source

- Download: `https://www.cepii.fr/DATA_DOWNLOAD/gravity/data/Gravity_csv_V202211.zip` (207 MB, no key;
  V202211 is the newest CSV release listed on the CEPII page as of 2026-09-15)
- Contents: `Gravity_V202211.csv` (~1.25 GB, 87 columns), `Countries_V202211.csv`, `Label_*.csv`
- Documentation: https://www.cepii.fr/CEPII/en/bdd_modele/bdd_modele_item.asp?id=8

## Licence

Etalab Open Licence 2.0 (per the CEPII page): any use with attribution. Cite Conte, M.,
P. Cotterlaz and T. Mayer (2022), *The CEPII Gravity database*, CEPII Working Paper 2022-05.
Some columns are derived from third-party sources (WDI, Penn World Table, Facebook SCI,
WTO RTA database); keep the attribution with derived outputs.

## Output (`normalized`)

Entities: countries `iso3:XXX` (CEPII `country_id`, ISO numeric code in attributes;
historical ids without an ISO3 become `cepii:country:<id>`).

Country-year observations (subject `iso3:XXX`, `dimensions.frequency = annual`):
`population` (thousand_people), `gdp_current_usd` (thousand_USD), `gdp_per_capita_current_usd`
(thousand_USD_per_person), `gdp_ppp_current_international_usd`, `gdp_per_capita_ppp_current_international_usd`,
`gatt_member`, `wto_member`, `eu_member` (indicator_0_1).

Undirected country-pair observations (subject = the country with the smaller CEPII id,
`dimensions.partner` = the other, `dimensions.pair = undirected`), run-length encoded over
consecutive years with valid window [first year, last year + 1):
`bilateral_distance_most_populated_cities`, `bilateral_distance_population_weighted`,
`bilateral_distance_capitals` (km), `contiguity`, `common_official_language`,
`common_ethnic_language`, `common_colonizer`, `colonial_relationship_post_1945`,
`colonial_dependency_ever`, `sibling_ever`, `common_legal_origin`,
`regional_trade_agreement_in_force` (indicator_0_1), `religious_proximity_index` (index_0_1),
`regional_trade_agreement_type`, `regional_trade_agreement_coverage` (category_code, label
in attributes), `diplomatic_disagreement` (score), `social_connectedness_index_2021` (index).

Rows where either country does not exist in that year are skipped; empty source cells
produce no observation. Trade-flow columns are not re-emitted (see `cepii_baci`).

## Rebuild

```sh
python3 -m worldmodel acquire cepii_gravity --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run cepii_gravity
python3 -m worldmodel verify cepii_gravity
python3 -m unittest data/cepii_gravity/tests/test_pipeline.py
```
