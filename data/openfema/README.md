# openfema

**OpenFEMA** API: disaster declarations, per-disaster financial summaries, Public Assistance funded project
summaries and Individuals and Households Program housing assistance (owners and renters).

## Source and licence

`https://www.fema.gov/api/open/{v1|v2}/<Entity>?$format=jsona&$orderby=id&$select=...` paged with `$skip`/`$top=10000`:
DisasterDeclarationsSummaries v2 (70k), FemaWebDisasterSummaries v1 (4k), PublicAssistanceFundedProjectsSummaries v1
(197k), HousingAssistanceOwners v2 (160k), HousingAssistanceRenters v2 (148k); ~200 MB of JSON lines. Public domain; OpenFEMA
terms: data as-is, no implied FEMA endorsement. No credential. Values are revised as obligations change; re-acquire
to refresh. Plan desired 150 MB; JSON with the selected fields needed 200 MB (desired set to 210 MB).

## Rebuild

```sh
wm acquire openfema --allow-network
WORLD_MODEL_RAW_VERIFY=size wm run openfema && wm verify openfema
python3 -m unittest discover -s data/openfema/tests -t data/openfema/tests
```

## Evidence

- Entities: disasters `fema:disaster:<disasterNumber>` (`entity`, incident type, declaration type, incident period),
  designated counties/states `geo:US:county:` / `geo:US:state:`.
- Events: `disaster_declaration` per designated area (participants disaster + geography; program flags in attributes).
- Observations (subject = disaster, `attributes.cumulative_as_of_retrieval`): `ia_registrations_approved`,
  `ihp_amount_approved` (USD, `dimensions.program`), `pa_federal_obligated` (USD, by category or by applicant),
  `pa_project_count`, `hmgp_federal_obligated`; housing assistance by `tenure`, state, county name and ZIP code:
  `ihp_valid_registrations`, `ihp_inspections`, `fema_inspected_damage`, `ihp_approved_registrations`,
  `ihp_amount_approved`, `ihp_repair_replace_amount`, `ihp_rental_amount`, `ihp_other_needs_amount` (USD).
- PA and housing records carry county names, not FIPS; join via names (or the declaration events) downstream.
