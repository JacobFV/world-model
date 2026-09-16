# federal_register_documents

Federal Register document metadata from the FederalRegister.gov API (an informational rendition; the
official edition is the GPO PDF). Status is never inferred as effective law.

## Source and scope

`https://www.federalregister.gov/api/v1/documents.json` for every publication month from 2021-01 through
2026-09 (`per_page=1000`, `order=oldest`, explicit `fields[]`: type, subtype, title, abstract, action,
agencies, CFR references, citation, dates, EO/proclamation numbers, dockets, RINs, pages, topics, URLs).
~155,000 documents expected (~73,000 / 126 MB for 2024-01..2026-09 alone). The GovInfo FR full-text XML ZIPs in the source plan are not acquired: the
structured metadata above is what simulations use. No credentials. Licence: U.S. government work.

## Normalized evidence

- `federalregister:{document_number}`: `regulation` (Rule, Proposed Rule), `law` (Executive Order,
  Proclamation) or `publication` (notices, other presidential documents)
- `document_status` (published type, action, publication/signing/comment-close/effective dates,
  `effective_status` = `unknown` unless the source gives an effective date), `legal_effective_date` for rules
- `issued_document_by` → `federalregister:agency:{id}` (`part_of` parent agency)
- `cfr_references` (title/part/chapter list), `identifier_assignment` (rin, docket, executive_order,
  proclamation, presidential_document), `federal_register_topics`

The current month is partial at acquisition time. Legacy JSONL samples keep the sample adapter.

## Rebuild

```sh
python3 -m worldmodel acquire federal_register_documents --allow-network
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run federal_register_documents
```
