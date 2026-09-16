# noaa_climdiv

NOAA NCEI **nClimDiv** monthly climate series, 1895-present, for U.S. counties, states and NCEI climate regions.

## Source and licence

Fixed-width files from <https://www.ncei.noaa.gov/monitoring-content/data/us/climdiv/monthly/current/> (version
`v1.0.0-20260904`, ~134 MB): county `tmpccy` (average temperature), `pcpncy` (precipitation), `pdsicy` (Palmer drought
severity index); statewide/regional `tmpcst`, `tmaxst`, `tminst`, `pcpnst`, `pdsist`, `phdist`, `zndxst`, `pmdist`,
`hddcst`, `cddcst`; plus the inventory readme. Public domain; cite Vose et al. (2014). The series are recomputed every
month, so file names change: refresh the `files` list before re-acquiring. `SEC_USER_AGENT` is sent as contact
User-Agent. Plan estimate was 60 MB for county+state tmp/pcp/pdsi/phdi/zndx; the county files alone are ~41 MB each, so
county scope was limited to temperature, precipitation and PDSI (140 MB desired, justified by actual file sizes).

## Rebuild

```sh
wm acquire noaa_climdiv --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run noaa_climdiv && wm verify noaa_climdiv
python3 -m unittest discover -s data/noaa_climdiv/tests -t data/noaa_climdiv/tests
```

## Evidence

- Entities: `geo:US:county:<GEOID5>`, `geo:US:state:<FIPS>`, `noaa:climregion:<code>` (NCEI regions 101-110 and agricultural
  / basin regions). NCEI state codes are alphabetical, not FIPS; `helpers.py` maps them (49 = Hawaii, 50 = Alaska).
- Observations: `average_temperature`, `maximum_temperature`, `minimum_temperature` (degF), `precipitation` (inches),
  `palmer_drought_severity_index`, `palmer_hydrological_drought_index`, `palmer_z_index`,
  `modified_palmer_drought_severity_index` (index), `heating_degree_days`, `cooling_degree_days` (degF days).
  `dimensions.frequency` monthly or annual (`aggregation` sum for precipitation/degree days, mean otherwise; incomplete
  years skipped), `dimensions.dataset_version`.
- County monthly values are emitted from `parameters.county_monthly_from` (1991) to bound output size; county annual
  values and all state/regional values cover 1895-present. Missing sentinels (-99.90, -9.99) are dropped.
