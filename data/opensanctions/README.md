# opensanctions

OpenSanctions **sanctions** collection, `targets.simple.csv`: deduplicated sanctioned persons, companies,
vessels, aircraft, crypto wallets and securities, merged across 90+ official lists (EU, UK, US,
CH, CA, AU, JP, UA, …).

- URL: `https://data.opensanctions.org/datasets/latest/sanctions/targets.simple.csv` (307-redirects to a dated artifact), ~73 MB
- No credential required.

## Licence: NON-COMMERCIAL

**CC BY-NC 4.0.** Non-commercial use only; commercial use requires a paid OpenSanctions data licence
(https://www.opensanctions.org/licensing/). Attribution: "OpenSanctions (https://www.opensanctions.org), CC BY-NC 4.0".
`dataset.json` records `redistribution: "restricted"` and `non_commercial: true`, and derived stages inherit these rights
metadata. Keep outputs out of commercial products, or use `ofac_sanctions` and `other_sanctions_lists`, which are
the commercially safe official sources.

## Rebuild

```sh
python3 -m worldmodel acquire opensanctions --dry-run
python3 -m worldmodel acquire opensanctions --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run opensanctions
python3 -m worldmodel verify opensanctions
python3 -m unittest data/opensanctions/tests/test_pipeline.py
```

Each acquisition is a current snapshot. Re-acquire periodically to track listing changes.

## Output (`normalized`, gzip evidence JSONL)

- Entities `opensanctions:<id>` (`person`, `organization`, `business` for Company, `vessel`, `aircraft`, `account` for CryptoWallet,
  `security`, `asset`, `location`), plus `opensanctions:program:<program_id>` (`regulation`) and `iso3:XXX` countries.
- Claims: `sanctions_alias`, `birth_date`, `address` (free text), `identifier` (`imo:` when recognisable), `contact`,
  `sanctions_summary` (the published sanctions text, truncated to 2,000 characters).
- Links: `associated_country` → `iso3:*`. OpenSanctions does not say whether a country is a nationality, jurisdiction
  or address. `gb-nir` maps to GBR, and `ua-cri`/`ua-dpr`/`ua-lpr` map to UKR with a `subdivision` attribute. Historical codes
  (`suhh`, `csxx`, `yucs`) remain unmapped claims. Also `subject_to_sanctions_program`.
- Events: `sanctions_listing_first_seen` at the crawler's `first_seen` time. This is **not** an official designation date;
  use the official datasets for designation dates.

Not included: ownership and control relationships. They exist only in the full FollowTheMoney export
(`default/entities.ftm.json`, ~2.6 GB), which exceeds this dataset's budget.
