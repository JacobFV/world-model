# firm_panel

A derived, versioned panel of SEC issuers by fiscal period. Every financial value is a list of
**vintages**: the first filing that reported it and each later filing that reported a different
number, each dated by its filing date. It is built by
[`worldmodel/panels/firm.py`](../../worldmodel/panels/firm.py) from six published datasets.
Measured row counts, coverage and limits: [docs/firm-panel.md](../../docs/firm-panel.md).

## Stages

| Stage | Unit | What a row holds |
| --- | --- | --- |
| `links` | issuer CIK | the LEI a published identifier ties to the CIK — GLEIF LEI-CDF `RegistrationAuthority` `RA000665` (the SEC, whose entity ID is the CIK) or the LEI in SEC submissions metadata — and the CUSIPs of the US/CA ISINs the GLEIF/ANNA mapping assigns to that LEI |
| `ownership` | (CIK, 13F report quarter) | 13F long share positions (`SH`, no put/call) in those CUSIPs: managers, filings, shares, USD value, the latest contributing filing date and the Rule 13f-1 due date |
| `panel` (output) | (CIK, fiscal period end) | revenue, net income, assets, liabilities, equity, cash and shares as filed, each as dated vintages; the fiscal year/period and form of the filing whose own report date is that period; the LEI; the 13F aggregate for the calendar quarter containing the period end; `federal_contracts: null` |

All stages are gzip JSONL. Each row carries `evidence`: per input `dataset@stage@version`, the
number of contributing records, a SHA-256 over their record ids and a sample of ids.

## Joins

Only published identifiers join rows:

- **CIK ↔ LEI**: GLEIF's registration-authority entity ID under authority `RA000665`, read by
  [`worldmodel/resolution/bridges.py`](../../worldmodel/resolution/bridges.py) (only the decimal CIK
  shape, never a series or adviser file number), and the `lei` identifier assignment SEC submissions
  publish. A CIK printed by two LEIs, an LEI claimed for two CIKs and disagreement between the two
  publishers are **refused** and listed in the construction row.
- **CUSIP ↔ security**: ISO 6166 — the nine-character NSIN inside a US or CA ISIN is the CUSIP, with
  the check digit recomputed. GLEIF says which LEI issued the ISIN; the CIK comes from the link above.
  The 13F information table publishes no issuer CIK (`identity_basis` says so on every security), so
  a CUSIP outside this chain stays unlinked.
- **Filings**: accession numbers as the SEC publishes them.

Nothing is matched by name, and no federal-contract identifier is matched at all: `usaspending`
publishes recipients by SAM UEI and CAGE code and no dataset here publishes a UEI→CIK or UEI→LEI
crosswalk, so `federal_contracts` is `null` on every row.

## Publication dates

- A financial value is public on the **filing date** of the report that carries it
  (`available_at` on each vintage). A restatement is a later vintage with its own filing date.
- A 13F aggregate is dated by the **latest filing it includes**; `due_date` is the quarter end plus
  45 days and `value_share_filed_by_due_date` says how much of it was on time.
- Identity links are dated by the **GLEIF snapshot** they were read from: they are current
  assertions, not dated history.

## What it does not establish

- **As filed, unaudited by the SEC.** The data sets are extracted as filed; the SEC does not verify them.
- **A vintage list is not a correction history.** A later, different number is recorded as a
  restatement; the filer's reason for it is not.
- **Institutional ownership is 13F only**: long US-listed positions of managers over the reporting
  threshold. It is not float, not total institutional ownership, and never a percentage of shares
  outstanding (this panel does not divide one source by another).
- **No LEI row means GLEIF does not tie that CIK to an LEI** — GLEIF records the register of
  incorporation, so a Delaware-incorporated filer often carries its Delaware file number instead. It
  does not mean the issuer has no LEI.
- **Fiscal periods are the periods filings report**, so a period an issuer only ever reported as a
  comparative (before its first filing in these sources) is absent.
- **Currencies are not converted**; each vintage keeps the filer's `unit`.

## Rebuild

Every dependency must be pinned, or the runner would rebuild the source datasets:

```sh
WORLD_MODEL_DATA=/path/to/data WORLD_MODEL_RAW_VERIFY=size python3 -m worldmodel run firm_panel \
  --input sec_financial_statements/normalized@<v> --input sec_company_assets/normalized@<v> \
  --input sec_gleif/normalized@<v> --input sec_issuer_reference/normalized@<v> \
  --input sec_13f_history/normalized@<v> --input sec_ownership_datasets/normalized@<v>
```

The pinned versions of the published build are in its manifest and in
[docs/firm-panel.md](../../docs/firm-panel.md). Read it with `wm firm-panel lookup <cik|lei>` and
`wm firm-panel coverage`. Tests: `tests/test_firm_panel.py` (builder on fixtures).
`artifacts/` and `scratch/` are ignored by Git.
