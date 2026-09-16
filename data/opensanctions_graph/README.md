# opensanctions_graph

OpenSanctions **default** collection: the full FollowTheMoney (FtM) entity graph, `entities.ftm.json`
(JSON lines, about 2.6 GB). It covers sanctions, PEPs, crime/wanted notices, debarment, company registries and more.

- URL: `https://data.opensanctions.org/datasets/latest/default/entities.ftm.json` (307-redirects to a dated artifact)
- No credential required. The artifact host ignored `Range` at probe time, so an interrupted download restarts from zero.

This is a separate dataset from `opensanctions` (sanctions `targets.simple.csv`), so that build stays stable.
Both use the same `opensanctions:<id>` entity namespace, so their records merge on entity ID.

## Licence: NON-COMMERCIAL

**CC BY-NC 4.0.** Non-commercial use only; commercial use requires a paid OpenSanctions data licence
(https://www.opensanctions.org/licensing/). Attribution: "OpenSanctions (https://www.opensanctions.org), CC BY-NC 4.0".
`dataset.json` records `redistribution: "restricted"` and `non_commercial: true`, and derived stages inherit these
rights metadata.

## Scope (two passes, bounded memory)

1. **Seeds**: "thing" entities whose FtM `topics` start with `sanction` (including `sanction.linked`/`.control`/`.counter`),
   `role.pep`, `role.rca`, `poi`, `crime` (all `crime.*`), `wanted` or `export.control` (including `.linked`).
   Every relationship entity is indexed with its endpoints in a temporary SQLite file under `scratch/`, which is deleted afterwards.
2. **Kept**: every relationship with at least one seed endpoint, the one-hop counterparties of those relationships, and
   every `Sanction` record of a seed. Other entities (for example debarment-only or plain registry companies) are dropped.

## Rebuild

```sh
python3 -m worldmodel budget
python3 -m worldmodel acquire opensanctions_graph --dry-run
python3 -m worldmodel acquire opensanctions_graph --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run opensanctions_graph   # two full passes over 2.6 GB
python3 -m worldmodel verify opensanctions_graph
python3 -m unittest data/opensanctions_graph/tests/test_pipeline.py
```

Each acquisition is a current snapshot; re-acquire periodically.

## Output (`normalized`, gzip evidence JSONL)

**Entities** `opensanctions:<ftm id>`, typed by schema: Person→`person`, Company→`business`,
Organization/LegalEntity→`organization`, PublicBody→`government_agency`, Vessel→`vessel`, Airplane→`aircraft`,
Position→`office`, Security→`security`, CryptoWallet/BankAccount→`account`, Address→`location`, other→`entity`.
Attributes include `ftm_schema`, `topics`, `datasets`, `target` and `scope` (`seed` or `counterparty`).
Also emitted: `opensanctions:program:<programId>` (`regulation`) and `iso3:XXX` countries.

**Relationships** are assertions between `opensanctions:*` entities. Attributes carry `relationship_id`, `role`, dates,
`percentage`, `shares_count`, `relationship` and `datasets`.

| FtM schema | Subject → object | Predicate |
| --- | --- | --- |
| Ownership | owner → asset | `owns`, or `controls` when the role or ownershipType mentions control or beneficial ownership |
| Directorship | director → organization | `director_of` |
| Family | person → relative | `family_member_of` |
| Associate | person → associate | `associate_of` |
| Membership | member → organization | `member_of_organization` |
| Employment | employee → employer | `employed_by` |
| Representation | agent → client | `represents` |
| Succession | predecessor → successor | `succeeded_by` |
| UnknownLink | subject → object | `linked_to` |
| Occupancy | holder → position | `holds_position` |

**Other records**
- `Sanction` → event `sanctions_designation` (dated by startDate, else listingDate, else date), with authority, program, reason and provisions. Undated records become a `sanctions_listing` claim. Also `subject_to_sanctions_program`.
- Claims: `sanctions_alias` (up to 20 names), `identifier` (`lei:`, `imo:`, `swift:`, `isin:`, `wikidata:`, `ru_inn:`, `ru_ogrn:`, `permid:` …), `birth_date`, `incorporation_date`.
- Links: `associated_country`, `nationality`, `citizenship`, `registered_in`, `flag_state` → `iso3:*`.

Caveats: FtM relationships are source claims of varying quality (for example LLM-extracted `origin` values or leak
databases). Check `datasets` before relying on them. Counterparties are not seeds, so their own further links are not included.
