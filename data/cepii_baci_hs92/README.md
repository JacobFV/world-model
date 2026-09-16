# cepii_baci_hs92

CEPII BACI in the HS1992 nomenclature, release V202601: reconciled bilateral merchandise
trade flows by exporter x importer x HS6 product x year, 1995-2024. Values are thousand
current USD; quantities are metric tons. This is the long consistent-nomenclature history
that complements [`cepii_baci`](../cepii_baci/README.md) (HS2017, 2017-2024, finer recent products).

## Source

- Download: `https://www.cepii.fr/DATA_DOWNLOAD/baci/data/BACI_HS92_V202601.zip`
  (2,417,732,497 bytes, verified 2026-09-15; no key)
- Contents: `BACI_HS92_Y{1995..2024}_V202601.csv` (`t,i,j,k,v,q`, about 8.7 GB uncompressed),
  `country_codes_V202601.csv`, `product_codes_HS92_V202601.csv`, `Readme.txt`
- Documentation: https://www.cepii.fr/CEPII/en/bdd_modele/bdd_modele_item.asp?id=37

## Licence

Etalab Open Licence 2.0: any use, including commercial, with attribution. Cite
Gaulier, G. and Zignago, S. (2010), *BACI: International Trade Database at the
Product-Level. The 1994-2007 Version*, CEPII Working Paper 2010-23.

## Stages

Both stages use the same flow representation as `cepii_baci`: one `observation` per
exporter-importer-product-year, with
- `subject` = exporter (`iso3:XXX`, or `baci:area:<code>` for areas without an ISO3)
- `metric` = `bilateral_trade_value`, `unit` = `thousand_USD`
- `dimensions` = `{importer, product, frequency: annual}`
- valid window = the calendar year
- `attributes.quantity_t` in metric tons (omitted when CEPII has no quantity)

Stages:
- `normalized` (default output) has every HS6 line, about 277 M flows. Products use
  `hs92:NNNNNN`. IDs look like `baci92:<year>:<exporter code>:<importer code>:<hs6>`.
- `hs4` has exporter-importer-HS4 aggregates (`hs92:NNNN`, `attributes.aggregate =
  "hs6_to_hs4"`). It also has country-pair `resource_flow` entities `baci92:flow:EXP:IMP`
  with `flow_source` / `flow_destination` relations, and yearly
  `bilateral_trade_total_value` on the flow entity.

HS92 and HS17 product ids are different nomenclatures, so join them only through a
concordance. Countries share the `iso3:` namespace. For 2017-2024 both datasets cover
the same flows; CEPII reconciles each nomenclature separately, so totals can differ slightly.

## Rebuild

```sh
python3 -m worldmodel acquire cepii_baci_hs92 --dry-run
python3 -m worldmodel acquire cepii_baci_hs92 --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run cepii_baci_hs92
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run cepii_baci_hs92 --stage hs4
python3 -m worldmodel verify cepii_baci_hs92
python3 -m unittest data/cepii_baci_hs92/tests/test_pipeline.py
```
