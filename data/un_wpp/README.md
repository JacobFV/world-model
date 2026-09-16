# un_wpp

UN DESA Population Division, **World Population Prospects 2024**, medium variant: annual demographic
indicators and 1 July population by 5-year age group and sex, 1950-2100 (estimates 1950-2023,
projections 2024-2100).

## Source and licence

- Files: `WPP2024_Demographic_Indicators_Medium.csv.gz`, `WPP2024_PopulationByAge5GroupSex_Medium.csv.gz`
  from <https://population.un.org/wpp/downloads> (CSV format, standard projections). ~46.5 MB.
- Licence: CC BY 3.0 IGO. Cite "United Nations, DESA, Population Division (2024). World Population Prospects 2024."
- No credential.

## Rebuild

```sh
wm acquire un_wpp --dry-run && wm acquire un_wpp --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run un_wpp && wm verify un_wpp
python3 -m unittest discover -s data/un_wpp/tests -t data/un_wpp/tests
```

## Evidence

- Entities: countries/areas `iso3:<ISO3>` (`country`), aggregates `unwpp:loc:<LocID>` (`location`, `attributes.aggregate`),
  `within` assertions to the WPP geographic parent.
- Observations (`dimensions.variant = medium`, `attributes.projection` true for 2024+): `population` (people; total, by sex,
  and by `age_group`/`sex` for countries and World), `population_density` (people/km2), `median_age` (years),
  `population_growth_rate` (percent), `natural_change`, `births`, `deaths`, `net_migration` (people/year),
  `crude_birth_rate`, `crude_death_rate`, `net_migration_rate` (per 1000), `total_fertility_rate` (births per woman),
  `sex_ratio_at_birth`, `life_expectancy_at_birth` (years, by sex), `infant_mortality_rate`, `under5_mortality_rate`
  (per 1000 live births). Stocks are valid on 1 July; flows and rates cover the calendar year.
- Population counts are published in thousands and converted to people (rounding noise remains).
