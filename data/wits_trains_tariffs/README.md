# wits_trains_tariffs

MFN applied tariffs at HS6 from UNCTAD TRAINS via the World Bank WITS SDMX API.

## Scope

- 59 reporters: G20 economies, the European Union as one customs union (code 918), and other large or
  regional trading economies. Iraq (368) returned no TRAINS data and is excluded.
- **Every year from 2018 to the latest year each reporter reports**, as probed on 2026-09-15: 332
  reporter-years (2018: 56, 2019: 57, 2020: 57, 2021: 54, 2022: 54, 2023: 54). Reporters do not all report
  every year, and the grid lists only the years that exist, so 404s are not expected.
- Coverage ends in different years by reporter: 2023 for 54 reporters, 2022 for one, 2021 for two
  (including Russia), 2020 for Iran and 2019 for Egypt. Compare reporters within a year rather than
  assuming a common latest year.
- All HS6 products, partner `000` (MFN), `DATATYPE=reported`, ~5 MB of SDMX-ML per reporter-year
  (~1.64 GB total).
- Out of scope: preferential (bilateral partner) rates, ad valorem equivalents (`aveestimated`), and years
  before 2018. The same URL template supports them (`A.{reporter}.{partner}..{reported|aveestimated}`).

Endpoint: `https://wits.worldbank.org/API/V1/SDMX/V21/rest/data/DF_WITS_Tariff_TRAINS/A.{reporter}.000..reported/?startperiod={year}&endperiod={year}`.
The following were verified on 2026-09-15:
- WITS returns HTTP 500 unless an `Accept` header is sent. It also rejects the default Python-urllib User-Agent.
- `endperiod=2026` returns 500.
- Multi-year all-product queries return "response too large", so each reporter-year is its own request.

No key is required; the requests are throttled to 0.5/s (332 requests take ~11 minutes).

`desired_bytes` (2.2 GB) covers this grid plus the retained earlier latest-year-only raw artifact, because
per-dataset ledger usage counts every raw artifact still on disk.

## Rebuild

```sh
python3 -m worldmodel acquire wits_trains_tariffs --dry-run
python3 -m worldmodel acquire wits_trains_tariffs --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run wits_trains_tariffs
python3 -m worldmodel verify wits_trains_tariffs
```

## Evidence

- `helpers.py`: stdlib streaming SDMX-ML GenericData reader (iterparse, one series in memory).
- Entities:
  - Reporters are `iso3:XXX`. The mapping from numeric M49 codes uses local `countries.json`, which was
    copied from `worldmodel/reference/countries_iso3166.csv`.
  - `wits:economy:918` is the European Union and `wits:economy:000` is World; both are aggregate jurisdictions.
  - Products are `hs:NNNNNN` (`product`).
- Observations use metric `mfn_applied_tariff_simple_avg`, unit `percent`, on the reporter, with annual valid
  windows. A value is the simple average of the national tariff lines in that HS6 subheading. Other
  `TARIFFTYPE` values map to `preferential_applied_tariff_simple_avg` /
  `effectively_applied_tariff_simple_avg`.
  - Dimensions: `frequency=annual`, `partner`, `product`, `tariff_type`, `hs_revision` (from `NOMENCODE`,
    e.g. H6 = HS2022), `datatype`, `measure`. The HS revision changes across years, so HS6 codes are not
    comparable across revisions without a concordance.
  - Attributes: min/max line rates (percent) and line counts. `non_ad_valorem_lines` counts specific or
    compound lines that are excluded from the ad valorem average, so an average over mostly non-ad-valorem
    lines understates protection.
- Locators are `shard:<n>/series:<ordinal>`, where the ordinal is the 0-based `generic:Series` position in
  the response.

## Licence

WITS/TRAINS terms: free use with attribution ("UNCTAD TRAINS via WITS"); no resale; redistribution restricted.

## Local files

`artifacts/` and `scratch/` are ignored by Git.
