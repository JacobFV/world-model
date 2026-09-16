# fema_nri

FEMA **National Risk Index** v1.20 (December 2025) for U.S. counties (all published fields) and census tracts
(composite indices plus per-hazard expected annual loss, annualized frequency and risk score).

## Source and licence

FEMA's zipped tables on fema.gov (`NRI_Table_Counties.zip`, `NRI_Table_CensusTracts.zip`) return HTTP 403 to scripted
clients. This dataset instead pages FEMA's own public ArcGIS Online feature services (owner `FEMA_NationalRiskIndex`):
`National_Risk_Index_Counties` and `National_Risk_Index_Census_Tracts` `/FeatureServer/0/query` without geometry,
2,000 features per page (~220 MB of JSON lines). No credential and no bot-wall circumvention. Public domain (U.S. federal
work); NRI scores are relative model outputs, EAL values are modeled USD per year.

**Manual alternative:** download the county and census tract table ZIPs in a browser from
<https://hazards.fema.gov/nri/data-resources> and run `wm import fema_nri NRI_Table_Counties.zip` (and the tracts ZIP as a
second import); the pipeline reads `NRI_Table_*.csv` members.

## Rebuild

```sh
wm acquire fema_nri --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run fema_nri && wm verify fema_nri
python3 -m unittest discover -s data/fema_nri/tests -t data/fema_nri/tests
```

## Evidence

- Entities `geo:US:county:<GEOID5>` and `geo:US:tract:<GEOID11>` with NRI ids and ratings; `within` assertions.
- Observations (valid 2025-12-01 .. 2026-12-01, `dimensions.hazard` = `all` or one of 18 hazards such as `hurricane`,
  `inland_flooding`, `wildfire`; `dimensions.consequence` where applicable): `national_risk_index_score`,
  `expected_annual_loss` (USD/year or people/year), `expected_annual_loss_score`, `expected_annual_loss_risk_value`,
  `social_vulnerability_score`, `community_resilience_score`, `community_resilience_value`, `community_risk_factor`,
  `population_exposure`, `building_value_exposure`, `agriculture_value_exposure`, `area`, and per hazard
  `hazard_event_count`, `hazard_annualized_frequency`, `hazard_exposure_total`, `historic_loss_ratio`,
  `hazard_risk_index_score`.
