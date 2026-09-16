# cepii_baci

CEPII BACI, HS2017 nomenclature, release V202601: reconciled bilateral merchandise trade
flows by exporter x importer x HS6 product x year, 2017-2024. Values are thousand current
USD (FOB, reconciled from mirror declarations); quantities are metric tons.

## Source

- Download: `https://www.cepii.fr/DATA_DOWNLOAD/baci/data/BACI_HS17_V202601.zip` (795 MB, no key)
- Contents: `BACI_HS17_Y{2017..2024}_V202601.csv` (`t,i,j,k,v,q`), `country_codes_V202601.csv`,
  `product_codes_HS17_V202601.csv`, `Readme.txt`
- Documentation: https://www.cepii.fr/CEPII/en/bdd_modele/bdd_modele_item.asp?id=37

## Licence

Etalab Open Licence 2.0: any use, including commercial, with attribution. Cite
Gaulier, G. and Zignago, S. (2010), *BACI: International Trade Database at the
Product-Level. The 1994-2007 Version*, CEPII Working Paper 2010-23.

## Stages

Flow representation in both stages: one `observation` per exporter-importer-product-year,
`subject` = exporter, `metric` = `bilateral_trade_value`, `unit` = `thousand_USD`,
`dimensions` = `{importer, product, frequency: annual}`, valid window = the calendar year,
`attributes.quantity_t` = quantity in metric tons (null where CEPII publishes none).

- `normalized` (default output): every HS6 flow line (~14 M per year). Entities: countries
  `iso3:XXX` (BACI numeric code, ISO2 in attributes), statistical areas without ISO3
  `baci:area:<code>` (e.g. 490 "Other Asia, nes"), products `hs17:NNNNNN`. Record IDs are
  readable keys `baci:v202601:<year>:<exporter code>:<importer code>:<hs6>`.
- `hs4`: exporter-importer-HS4 aggregates (`hs17:NNNN`, `attributes.aggregate =
  "hs6_to_hs4"`, HS6 line counts and lines without quantity), country-pair
  `resource_flow` entities `baci:flow:EXP:IMP` with `flow_source` / `flow_destination`
  relations and yearly `bilateral_trade_total_value` (thousand_USD) on the flow entity.

Both stages stream the ZIP with bounded memory; `hs4` relies on CEPII's sort order
(year, exporter, importer, product) and fails loudly if a file is not grouped.

## Rebuild

```sh
python3 -m worldmodel acquire cepii_baci --dry-run
python3 -m worldmodel acquire cepii_baci --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run cepii_baci               # normalized (HS6)
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run cepii_baci --stage hs4
python3 -m worldmodel verify cepii_baci
python3 -m unittest data/cepii_baci/tests/test_pipeline.py
```

A manually downloaded ZIP can be imported with `wm import cepii_baci BACI_HS17_V202601.zip`.
`artifacts/` and `scratch/` are ignored by Git.
