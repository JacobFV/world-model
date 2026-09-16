# other_sanctions_lists

Official non-OFAC sanctions and export-control screening lists, one shard each:

| Shard | Source | URL | Format |
| --- | --- | --- | --- |
| 0 | UN Security Council Consolidated List | `https://scsanctions.un.org/resources/xml/en/consolidated.xml` (redirects to a signed blob URL) | XML (~2 MB) |
| 1 | UK Sanctions List (FCDO, includes OFSI group IDs) | `https://sanctionslist.fcdo.gov.uk/docs/UK-Sanctions-List.csv` | CSV with a `Report Date:` preamble line, one row per name/address variant (~50 MB) |
| 2 | U.S. Consolidated Screening List (ITA: OFAC, BIS Entity/DPL/UVL/MEU, State ISN/DTC) | `https://data.trade.gov/downloadable_consolidated_screening_list/v1/consolidated.csv` | CSV (~17 MB) |

No credentials are needed. The EU Financial Sanctions Files require an EU Login token and are not
included; the `opensanctions` dataset covers EU lists indirectly.

## Licences

- UN list: public UN Security Council document (UN terms of use).
- UK Sanctions List: Open Government Licence v3.0. Attribution: "Contains public sector information licensed under the Open Government Licence v3.0."
- Consolidated Screening List: U.S. federal government work (public domain).

## Rebuild

```sh
python3 -m worldmodel acquire other_sanctions_lists --dry-run
python3 -m worldmodel acquire other_sanctions_lists --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run other_sanctions_lists
python3 -m worldmodel verify other_sanctions_lists
python3 -m unittest data/other_sanctions_lists/tests/test_pipeline.py
```

Shards are recognised by content, so a single manually downloaded file can be imported with `wm import`.
Each acquisition is a **current snapshot**. Re-acquire periodically (e.g. monthly) to build
delisting/amendment history.

## Output (`normalized`, gzip evidence JSONL)

- Entities: `un:sanctions:<REFERENCE_NUMBER>`, `uk:sanctions:<Unique ID>`, `us_csl:<_id>` (`person`/`organization`/`vessel`/`aircraft`).
  Regimes and programs are `regulation` entities: `un:regime:<list type>`, `uk:regime:<slug>`, `ofac:program:<CODE>` for Treasury CSL
  programs (the same IDs as `ofac_sanctions`), `us_csl:program:<slug>`. Lists are `us_csl:list:<abbrev>`. Also `iso3:XXX` countries,
  `org:UN:security_council` and `gov:GBR:fcdo_ofsi`.
- Claims: `sanctions_alias`, `identifier` (`imo:`, `lei:`, `swift:`, `isin:`, `mmsi:`, `crypto:` IDs where recognisable), `address`,
  `birth_date`, `place_of_birth`, `vessel_particulars`, `related_party_text` (unresolved UK owner/parent/subsidiary text), `contact`,
  `sanctions_feature`, `sanctions_listing`.
- Links: `located_in`, `nationality`, `citizenship`, `flag_state` → `iso3:*`; `subject_to_sanctions_program`; `listed_on`;
  `same_designation_as` (UK → `un:sanctions:*` via UN reference number; CSL Treasury rows → `ofac:party:<entity_number>`);
  `mentioned_in_listing_narrative` (UN reference numbers cited in UN comments; the association type is unspecified).
- Events: `sanctions_designation` (UN `LISTED_ON`; UK `Date Designated` per designation and regime; CSL `start_date` per list row).

Known caveats: UK "Primary name variation" rows are emitted as aliases. A country name that doesn't map to
ISO3 is kept as a claim with `unmapped_country: true`. Many CSL Treasury rows have no `start_date`; use `ofac_sanctions` for those dates.
