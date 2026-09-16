# lda_lobbying

Lobbying Disclosure Act filings from the LDA.gov REST API (the Senate LDA site was retired on 2026-06-30).

## Source and scope

- `https://lda.gov/api/v1/filings/?filing_year={2025,2026}&filing_period={quarter}&ordering=dt_posted`
- `https://lda.gov/api/v1/contributions/?filing_year={2025,2026}&filing_period={mid_year,year_end}&ordering=dt_posted`
- `page_size` is capped at 25 by the API; partitions by period keep page depth low (deep offsets are slow).
  Partition counts were verified to equal the filing-year totals (filings 2025: 108,981; 2026: 56,517 as of
  2026-09-15; contributions 2025: 40,460; 2026: 18,151).
- Registrants and clients are embedded in filings, so the separate reference endpoints are not crawled.

Credential: `LDA_API_KEY` (register at https://lda.gov/api/register/), sent as `Authorization: Token <key>`;
authenticated limit ~120 requests/min (configured at 1.8 requests/s). Licence: U.S. government records;
LDA.gov API terms apply.

## Normalized evidence

- `lda:filing:{uuid}` (`publication`: type, period, posted time, income/expenses, amendment flag)
- `lda:registrant:{id}`, `lda:client:{id}` organizations; `filed_by`, `filed_on_behalf_of`;
  `lobbies_for` registrant → client for the reporting period
- observations `lobbying_income` / `lobbying_expenses` (USD, reporting period; amendments flagged in
  dimensions — keep the latest report per registrant × client × period)
- `lobbying_issue` (general issue code + description), `contacted_government_entity` →
  `lda:government_entity:{id}`, `lobbied_for_client` `lda:lobbyist:{id}` → client (issue codes, covered positions),
  `foreign_entity_interest`, `affiliated_organization`
- LD-203: `lda:contribution_report:{uuid}`, observation `lobbyist_contribution_amount` (USD, item date;
  contribution type, payee, honoree, contributor names as published)

Registrant contact phone numbers and street addresses are not emitted.

## Rebuild

```sh
python3 -m worldmodel acquire lda_lobbying --allow-network   # ~9,000 pages; resumable with --resume
WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run lda_lobbying
```
