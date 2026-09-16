# acs_pums

U.S. Census Bureau **ACS 2020-2024 5-year Public Use Microdata Sample**, aggregated to weighted 2020 PUMA-level
statistics. No person or housing microdata rows are retained in normalized output.

## Source and licence

`https://www2.census.gov/programs-surveys/acs/data/pums/2024/5-Year/csv_pus.zip` (person records, 2,273,780,719 bytes;
members `psam_pusa..d.csv`) and `csv_hus.zip` (housing records, 952,510,302 bytes; `psam_husa..d.csv`). Public domain;
PUMS are disclosure-protected samples (top-coding, data swapping). No credential.

## Rebuild

```sh
wm budget && wm acquire acs_pums --allow-network      # 3.2 GB; use --resume after a budget stop
WORLD_MODEL_RAW_VERIFY=size wm run acs_pums && wm verify acs_pums
python3 -m unittest discover -s data/acs_pums/tests -t data/acs_pums/tests
```

## Evidence

- Entities `geo:US:puma20:<state FIPS><PUMA>` (`location`) `within` `geo:US:state:<FIPS>`.
- Weighted counts (person weight PWGTP, housing weight WGTP; valid 2020-01-01 .. 2025-01-01, `dimensions.survey =
  acs_pums_5yr`): `population` (total, `age_group`, `sex`), `population_16_plus` by `employment_status`,
  `persons_with_income` by `personal_income_2024usd` bin, `employed_population` by `naics_sector` (NAICSP first two digits)
  and `soc_major_group` (SOCP first two digits), `workers` by `commute_mode`, `commuters` by `travel_time_minutes`,
  `population_25_plus` by `educational_attainment`, `population_below_poverty`, `housing_units`, `vacant_housing_units`,
  `households` (total, `tenure`, `household_income_2024usd`), `renter_households` by gross rent and rent burden,
  `owner_households` by home value and owner cost burden.
- Weighted means (`attributes.statistic = weighted_mean`): `mean_personal_income`, `mean_wage_income_employed`,
  `mean_commute_time` (minutes), `mean_household_income`, `mean_gross_rent`, `mean_home_value`,
  `mean_selected_monthly_owner_costs`; weighted medians from fine histograms: `median_household_income`,
  `median_gross_rent`, `median_home_value`. Dollar values are adjusted to 2024 dollars with ADJINC / ADJHSG.
- No margins of error (replicate weights PWGTP1-80 / WGTP1-80 are not used); small PUMA cells are noisy.
