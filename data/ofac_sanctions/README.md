# ofac_sanctions

U.S. Treasury OFAC sanctions lists in the Sanctions List Service **advanced XML** schema:

- `SDN_ADVANCED.XML`: Specially Designated Nationals and Blocked Persons (~127 MB)
- `CONS_ADVANCED.XML`: consolidated non-SDN lists (SSI, NS-MBS, CMIC, NS-PLC, …) (~4.5 MB)

Both come from `https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/`, which
302-redirects to a short-lived signed S3 URL. No credential is required.

## Licence

U.S. federal government work: public domain. Attribute "U.S. Treasury OFAC Sanctions List Service".

## Rebuild

```sh
python3 -m worldmodel acquire ofac_sanctions --dry-run
python3 -m worldmodel acquire ofac_sanctions --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run ofac_sanctions
python3 -m worldmodel verify ofac_sanctions
python3 -m unittest data/ofac_sanctions/tests/test_pipeline.py
```

A manually downloaded XML can be imported with `wm import ofac_sanctions SDN_ADVANCED.XML`
(the shard is recognised by content).

## Snapshots and history

Each acquisition is an immutable **current-state snapshot**. OFAC entry-event dates give original
listing dates, but delistings and changes are only visible by comparing snapshots, so re-acquire
periodically (e.g. monthly) to build a listing/delisting history.

## Output (`normalized`, gzip evidence JSONL)

The pipeline streams the XML with `helpers.iter_items`. It rejects DTDs and entity declarations and
holds only lookup tables (locations, ID documents) in memory.

| Record | Details |
| --- | --- |
| entity `ofac:party:<ProfileID>` | `person`, `organization`, `vessel`, `aircraft` (by PartyType/SubType); attributes `fixed_ref`, `source_file`, `list_publication_date` |
| entity `ofac:program:<CODE>` | `regulation` (e.g. `ofac:program:RUSSIA-EO14024`); shared with CSL rows in `other_sanctions_lists` |
| entity `gov:USA:treasury_ofac` | `government_agency`; entity `iso3:XXX` `country` |
| assertion (claims) | `sanctions_alias`, `identifier` (`value.id` namespaced where recognisable: `imo:`, `lei:`, `mmsi:`, `swift:`, `isin:`, `duns:`, `crypto:<chain>:`, `callsign:` …), `address`, `birth_date`, `place_of_birth`, `contact`, `sanctions_feature` (directive/secondary-sanctions/other published features), `sanctions_listing` (entries without dates) |
| assertion (links) | `located_in`, `nationality`, `citizenship`, `registered_in`, `flag_state`, `former_flag_state`, `other_flag_state` → `iso3:*`; `subject_to_sanctions_program` → program |
| assertion (relationships) | `controls` (from "Owned or Controlled By", with the direction reversed, and "Owns, controls, or operates"), `affiliated_with`, `acts_for_or_on_behalf_of`, `provides_support_to`, `leader_or_official_of`, `family_member_of`, `principal_executive_officer_of`, `significant_role_in`, `property_in_interest_of`; attributes `source_relation`, `former` |
| event `sanctions_designation` | `occurred_at` = earliest entry-event date; participants party, programs, OFAC; attributes list, measures, legal basis, all event dates |

Evidence locators are positional XPaths, e.g. `shard:0/xpath:/Sanctions/DistinctParties/DistinctParty[54]`.
