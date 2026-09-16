> **Historical record.** This is the bounded-sample exploration run made before full-scale
> acquisition existed. The catalog now holds 107 published normalized datasets and about
> 1.31 billion records; the blockers recorded here (BEA, USDA, freight) are resolved. Use
> `wm catalog` for current status and
> [strategic-affordances-audit.md](strategic-affordances-audit.md) for the current audit.
> The per-sample manifest links below no longer resolve: those payloads were pruned to
> reclaim space, which is also why `wm evidence-audit` currently aborts.

# Sample exploration — 2026-09-15

**11 of 14 real source families sampled; 762 real records retained.**
The latest real sample payloads total **519,498 bytes (507.3 KiB)**.
The entire data tree, including older sampling attempts, manifests, code snapshots, and offline demo, is **2.77 MiB**.
No temporary original downloads remain. All 18 datasets have sampling policies;
the four offline/demo-derived datasets were also sampled successfully.

The 64 MiB temporary allowance includes room for sample staging. Whole-file
downloads are capped at 62 MiB; API responses usually at 1 MiB. Each retained
sample is capped at 100 rows and 1 MiB (GLEIF is intentionally 25 rows).

## Results

| Dataset | Rows | Retained bytes | Downloaded file/response | Result |
| --- | ---: | ---: | ---: | --- |
| `bea_input_output` | — | — | — | Missing BEA_API_KEY; source requires an API key |
| `bls_labor` | 12 | 1,010 | 1,133 bytes | [Sample manifest](../data/bls_labor/samples/09019ed80922c72945589bd0998e9396806172d11ddaa32d521bc265b24a6eb1/manifest.json) |
| `census_business` | 100 | 102,962 | 11,115,845 bytes | [Sample manifest](../data/census_business/samples/d38f0ecd3a0f4e6df239c2cda107d82437e810ec64a52d5fbee28ded27184791/manifest.json) |
| `census_geography` | 58 | 4,087 | 5,383 bytes | [Sample manifest](../data/census_geography/samples/6fc6429756ce176e4f37033ede19fceb8db07412388a536b4be91682c1925f66/manifest.json) |
| `census_population` | 66 | 129,105 | 41,378 bytes | [Sample manifest](../data/census_population/samples/2ccf6e0d6ee8fb1bc3d166bb8e8356221b503f62cff092491abc87fdb5276e70/manifest.json) |
| `classifications` | 100 | 8,002 | 44,036 bytes | [Sample manifest](../data/classifications/samples/fcdf1dd62d97c9d462fb0cb01cc3752873306afa866a317e0e9c3c91cee3dd37/manifest.json) |
| `eia_energy` | 1 | 171 | 699 bytes | [Sample manifest](../data/eia_energy/samples/b529cdd17549d8f7fbc48fef58cbbd7ac698b7be2c1cb3667d38ef5b0c7f2ac2/manifest.json) |
| `fec` | 100 | 123,759 | 123,880 bytes | [Sample manifest](../data/fec/samples/8bef7812aaf07dab722c8c07e0673b496492e441ab722aa2b5afec25720d101e/manifest.json) |
| `freight` | — | — | — | Network/timeout failure from https://faf.ornl.gov/faf5/data/download_files/FAF5.7.1_Reprocessed_1997-2012_State.zip |
| `sec_gleif` | 25 | 67,096 | 69,034 bytes | [Sample manifest](../data/sec_gleif/samples/6451c192b852e054965ae1920aba0b846e36919b1780cb1622aca4464a51bc98/manifest.json) |
| `transport` | 100 | 33,998 | 44,331 bytes | [Sample manifest](../data/transport/samples/c750632442bfbdfc4e5a19cfd217495bee129126d3de0d0086c64bb092df51c3/manifest.json) |
| `usaspending` | 100 | 30,450 | 30,943 bytes | [Sample manifest](../data/usaspending/samples/8150c844ee0625590bc2d67b28ec04395929ffee779e066029e26a709ee03a10/manifest.json) |
| `usda_agriculture` | — | — | — | Missing USDA_NASS_API_KEY; source requires an API key |
| `usgs_resources` | 100 | 18,858 | 21,108 bytes | [Sample manifest](../data/usgs_resources/samples/a5bc0c51fc6c14a30e6eae687daca4464bc136b9499bc252723359ffb440a3c5/manifest.json) |

## What exploration revealed

- **Population:** 66 rows: one national total, four regions, nine divisions and 52 state-level entries. Preserve `SUMLEV`; summing every row double-counts nested geographies. The source is vintage-2024 Population Estimates, with yearly columns that should become time-indexed observations. This sample does not supply ACS income.
- **Business patterns:** all first 100 rows have `fipstate="01"` (Alabama). NAICS values include `------` totals and padded sector codes such as `11----`; preserve hierarchy and source revision. There are multiple legal-form categories and employment-size columns. Numeric columns have companion disclosure/noise flags. This is aggregate evidence, not named companies.
- **Geography:** 58 California counties with `GEOID`, `STATE`, `COUNTY` and `NAME`. Leading-zero IDs survive. Polygon geometry was intentionally omitted; jurisdiction attributes are inspectable without GIS archives.
- **Classifications:** 100 valid six-digit NAICS 2022 code/title pairs, beginning with `111110` / Soybean Farming. The workbook has blank formatted rows, now filtered explicitly. A 2022 vocabulary must not be joined blindly to CBP's 2017 classification revision.
- **Company identities:** 25 GLEIF records include 12 funds and 13 general entities. Registration statuses are 11 issued, 10 lapsed and four retired. Preserve identifier status and nested address structures; an LEI record is not proof of a currently operating establishment. SEC filings were not sampled.
- **Labor:** the corrected POST query returned 12 monthly observations for 2024 from series `LNS14000000`. An initial GET ignored the year query parameters and returned 32 observations across 2024–2026; that earlier acquisition remains preserved, but the current policy uses POST. Check returned periods rather than trusting request parameters.
- **Energy:** one California 2023 all-sector electricity retail-sales observation. The response supplies `sales-units="million kilowatt hours"`. Carry that unit explicitly instead of treating the numeric string as raw kWh. Facilities/grid assets are outside this particular sample.
- **Political committees:** 100 committee records with nested candidate/cycle arrays and many missing optional agent fields. A 2024 cycle filter does not make mutable registration fields a historical snapshot; some returned filing dates are later. No individual contribution records were fetched.
- **Procurement:** 100 award summaries selected with a January 2024 filter, ordered by award amount. Their amounts are award totals, not a computed January spending flow. Normalization must distinguish award-level totals from transaction obligations/outlays and preserve award identifiers.
- **Transport:** 100 highway ways in a small San Francisco box, with IDs, tags and center points. This is useful for inspecting tags and identities; center points do not provide a routable road graph. Full way geometry and node connectivity require a separate bounded query.
- **Minerals:** 100 historical MRDS records: 85 occurrences and 15 past producers. The service's real layer is 3, not 0. Keep development status and source record IDs; do not turn every record into an active mine.

## Archive and access outcomes

The **CBP ZIP succeeded**: 11,115,845 bytes downloaded (10.60 MiB), 100 CSV records retained, and the full archive removed. Its `.txt` member is CSV-formatted and read as a stream. The 44,036-byte NAICS XLSX also succeeded. Neither archive needed to be extracted in full onto disk.

The selected recent FAF state database is advertised above our file budget. A smaller historical 1997–2012 archive was configured instead, but its ORNL host reset the connection during this run. **No freight rows were obtained**; the failure is a connectivity blocker, not a claim that ZIP sampling is impossible. See the [official FAF download catalog](https://www.bts.gov/faf/faf5).

**BEA** requires `BEA_API_KEY`; **USDA NASS** requires `USDA_NASS_API_KEY`. Neither was present, so no request with fabricated credentials or unbounded fallback download was made. The limits and selected query criteria remain configured. See [BEA API registration](https://apps.bea.gov/api/signup/) and [NASS Quick Stats API](https://quickstats.nass.usda.gov/api).

Census ACS/CBP API calls returned HTML `Missing Key` pages. The successful population and business samples use official bounded [Population Estimates CSV](https://www2.census.gov/programs-surveys/popest/datasets/2020-2024/state/totals/) and [CBP ZIP](https://www2.census.gov/programs-surveys/cbp/datasets/2023/) alternatives, with the source change recorded in their policies.

## Reproduce or inspect

```sh
python3 -m worldmodel explore census_business
python3 -m worldmodel explore sec_gleif
python3 -m worldmodel sample freight --allow-network
python3 -m worldmodel sample all --allow-network
```

Each acquisition records selection criteria, original byte count/checksum, endpoint,
code snapshot, and the retained sample reference. Full original files are discarded.
Samples do not replace full-data raw/final pointers. Earlier small samples remain
available as separate immutable artifacts; this report describes the latest attempts.
No claims of statistical representativeness or completed production normalization are made.

See [sampling implementation and budgets](sampling.md) and the
[machine-readable exploration report](sample-exploration-2026-09-15.json).
