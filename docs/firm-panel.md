# The firm panel

`firm_panel` is a derived, versioned dataset
([declaration](../data/firm_panel/dataset.json), [README](../data/firm_panel/README.md), builder
[`worldmodel/panels/firm.py`](../worldmodel/panels/firm.py)) with one row per SEC issuer x fiscal
period. Its point is the thing a single-value fundamentals table throws away: **every value is the
list of vintages that filers reported for it**, each dated by the filing date, so a restatement is a
later vintage and an as-of reader can reconstruct what was on file on any day.

Built 2026-09-18 from commit `8942c67` (clean tree) on the second GB10 (CPU only), 17 min 12 s wall
clock, peak RSS 717 MiB.

| Stage | Version | Rows | Bytes (gzip) |
| --- | --- | --- | --- |
| `links` | `9739b425f206…` | 4,966 CIK-LEI links + 1 construction row | 660 KB |
| `ownership` | `ebea1152c5cd…` | 14,841 issuer-quarters + 1 construction row | 1.8 MB |
| `panel` (output) | `383d60250652…` | 401,138 issuer-period rows + 1 construction row | 124.3 MB |

Pinned inputs (every one is recorded in all three manifests):

| Dataset | Version | What the panel takes from it |
| --- | --- | --- |
| `sec_financial_statements` | `269c4814` | every as-filed value of the ten tracked concepts, 2012Q4-2026Q2 filings (7,332,623 loaded) |
| `sec_company_assets` | `68335122` | the same concepts from filings made before the first FSDS quarter (598,631 loaded) |
| `sec_gleif` | `925f8bdf` | LEI ↔ CIK through registration authority `RA000665`; LEI → ISIN → CUSIP |
| `sec_issuer_reference` | `b5923f52` | the `lei` identifier assignment SEC submissions publish (25 rows) |
| `sec_13f_history` | `94be7bc7` | 13F information tables and summary pages, filings 2013-04..2025-08 |
| `sec_ownership_datasets` | `6d7bf0fc` | the same, filings 2025-09..2026-08 |

Rebuild command with every dependency pinned:
[data/firm_panel/README.md](../data/firm_panel/README.md#rebuild).

## What a row holds

`panel` row `firm_panel:{cik}:{period_end}`:

- **`instants`** — `total_assets`, `total_liabilities`, `stockholders_equity`, `cash_and_equivalents`,
  `cash_restricted_cash_and_equivalents`, `shares_outstanding` and the dei cover-page
  `shares_outstanding_cover`, each a list of vintages.
- **`durations`** — `revenue`, `net_income` and `weighted_average_shares_diluted`, keyed by the
  number of quarters the value covers (`"1"` a quarter, `"4"` a year, `"2"`/`"3"` year-to-date),
  each a list of vintages.
- **a vintage** — `value`, `unit`, `available_at` (the filing date), `accession`, `form`, `concept`
  (the us-gaap/ifrs/dei tag, so alternative revenue tags stay distinguishable), `source`,
  `revision` (`first_report` or `restated`), `confirmations` (later filings that repeated it
  unchanged) and the input `record_id`.
- **period identity** — `fiscal_year`, `fiscal_period` and `form` of the earliest filing whose own
  report date is this period, `first_filed`, `first_accession`, `filings_for_period`.
- **`lei`** — the LEI and which publisher asserted it, with the snapshot date the link was read from.
- **`institutional_ownership`** — the 13F aggregate for the calendar quarter that contains the
  period end (managers, filings, shares, USD value, `available_at`, `due_date`).
- **`federal_contracts`** — always `null`; see below.
- **`linked`** — one flag per block, which the coverage tables count.
- **`evidence`** — per input `dataset@stage@version`: contributing records, a SHA-256 over their
  record ids, a sample of ids.

Reading: `wm firm-panel lookup 0000036104`, `wm firm-panel lookup <LEI> --as-of 2020-06-30`,
`wm firm-panel coverage`, `wm firm-panel status`. `--as-of` drops rows that were not yet filed and
reports each value at its latest vintage on that date.

## Row counts and what was dropped

| Count | Value |
| --- | --- |
| issuer-period rows | 401,138 |
| issuers (CIKs) | 16,867 |
| distinct fiscal period ends | 595 (2004-12-31 .. 2026-06-30, plus 5 rows at filer-typo dates: 2028-02-29, 2053-03-31, 2201-03-31, 2201-10-31, 2215-09-30) |
| rows by decade | 1,369 in the 2000s, 243,723 in the 2010s, 156,042 in the 2020s |
| rows by form of the first filing | 288,580 10-Q, 93,594 10-K, 9,094 20-F, 6,089 10-Q/A, 1,556 10-K/A, 1,319 40-F, rest transition and amended forms |
| values loaded | 7,931,254 (7,332,623 FSDS + 598,631 companyfacts) from 411,447 filings |
| vintages written | 3,515,687 (3,120,652 FSDS + 395,035 companyfacts) |
| series with at least one restatement | 212,624; **96,168 rows (24.0%)** carry a restated value |
| dimensional or co-registrant values skipped | 13,043,105 |
| values at an instant that is no filing's report date | 598,748 (dropped, counted) |
| two tags for one metric in one filing | 11,738 (the higher-priority tag is kept) |

Per-metric presence and restatements:

| Metric | Rows with it | Share | Later vintages |
| --- | --- | --- | --- |
| `total_assets` | 395,455 | 98.6% | 20,674 |
| `stockholders_equity` | 361,749 | 90.2% | 31,701 |
| `cash_and_equivalents` | 345,629 | 86.2% | 21,473 |
| `total_liabilities` | 322,545 | 80.4% | 15,320 |
| `net_income` (quarter) | 301,547 | 75.2% | 36,161 |
| `shares_outstanding` | 280,882 | 70.0% | 17,603 |
| `revenue` (quarter) | 223,749 | 55.8% | 30,586 |
| `weighted_average_shares_diluted` (quarter) | 172,720 | 43.1% | 15,454 |
| `net_income` (annual) | 97,321 | 24.3% | 10,003 |
| `revenue` (annual) | 74,562 | 18.6% | 9,500 |
| `shares_outstanding_cover` (dei) | 42,571 | 10.6% | 93 |

`total_liabilities` is below `total_assets` because many filers tag only
`LiabilitiesAndStockholdersEquity`; the cover-page share count is almost entirely a companyfacts-era
value (FSDS `num.txt` publishes only 464 of them). The longest vintage chain in the panel is 15
(`firm_panel:0001938338:2022-12-31:cash_restricted_cash_and_equivalents`).

## Identity: what linked, and what did not

**LEI — the published crosswalk covers funds, not operating issuers.** The `links` stage accepts
4,966 CIKs: 4,941 from GLEIF's `RegistrationAuthority` `RA000665` (the SEC's own register) and 25
from the `lei` value SEC submissions metadata publishes. GLEIF prints an RA000665 entity ID for
27,931 LEIs, but only 4,971 of those IDs have the decimal shape of a CIK — the rest are `S…`/`C…`
fund series and class IDs and `805-…` investment-adviser file numbers, which the bridge refuses
because the authority code, not the value shape, decides the namespace. And of the 4,941 accepted
GLEIF links, **4,636 are GLEIF category `FUND`**: registered funds put their SEC registration in
that field; operating companies put their state of incorporation there (Apple's LEI carries a
California file number, so Apple has no LEI in this panel).

The result, measured on the panel:

| | Issuers | Share |
| --- | --- | --- |
| issuers in the panel | 16,867 | |
| with an LEI link | 97 | 0.58% |
| … of those, from GLEIF RA000665 / SEC submissions | 84 / 13 | |
| with a 13F ownership row | 21 | 0.12% |
| rows with an LEI | 2,829 | 0.71% |
| rows with institutional ownership | 709 | 0.18% |

15 CIKs are **refused** because two LEIs print the same CIK (the `gleif_sec_cik` spec is 1:1); they
are listed in the `links` construction row.

**CUSIP → issuer.** 20,151 CUSIPs on 567 of the linked CIKs, read as the NSIN of a US or CA ISIN in
the GLEIF/ANNA mapping with the ISO 6166 check digit recomputed; no CUSIP was refused for mapping to
two linked LEIs. Because the linked CIKs are mostly funds and bond-issuing entities, the CUSIP set
is dominated by debt: one CIK (Federal Agricultural Mortgage) alone carries 9,338 CUSIPs.

**13F.** The information table publishes the CUSIP, a free-text issuer name and no issuer CIK
(`identity_basis` on every 13F security entity says so), so the GLEIF chain above is the only
published way in. Of 87,357,054 holding rows read, 1,511,440 (1.7%) are in a CUSIP that chain ties
to a CIK; 75,972,597 are in CUSIPs it does not. Positions excluded before linking: 4,537,033 put/call
options, 705,832 principal-amount (`PRN`) rows, 4,629,436 rows of filings superseded by a later
report. The aggregate covers **393 issuers and 14,841 issuer-quarters**; 371 of those issuers file no
financial statements in these sources (they are funds), which is why only 21 issuers show ownership
in the panel.

**Amendments.** 322,931 13F summary pages over 307,588 manager-periods: 9,971 `RESTATEMENT`
amendments replace their report, 4,666 `NEW HOLDINGS` amendments add to it, and 10,677 filings are
superseded. A manager counted once per issuer-quarter even when a new-holdings amendment adds the
same issuer.

**Federal contract obligations: no link exists.** `usaspending` publishes prime-contract recipients
by SAM Unique Entity ID and CAGE code. No dataset in this catalog publishes a UEI→CIK or UEI→LEI
crosswalk: the `uei_lei` mapping spec names "published SAM/USAspending recipient records that report
an LEI" as its source and no such source is acquired, and SAM publishes no CIK. Attributing
obligations to an issuer would require matching recipient names to issuer names, which this panel
does not do. `federal_contracts` is therefore `null` on every row and the block is reported as not
linked rather than as zero obligations.

## Publication dates

| Rule | Applies to | Date used |
| --- | --- | --- |
| `sec_filing_date` | every financial value | the filing date of the report that carries it; a later filing with a different number is a new vintage with its own date |
| `thirteen_f_filing_date` | the 13F aggregate | the latest contributing filing date, with `due_date` = quarter end + 45 days (Rule 13f-1) and `value_share_filed_by_due_date` |
| `gleif_snapshot` | LEI and CUSIP links | the GLEIF golden-copy / ISIN-mapping snapshot date (2026-09-15): current assertions, not dated history |

Two consequences worth stating. First, 13F aggregates keep arriving years late: the 2023-12-31
aggregate for US Bancorp is dated 2026-08-26 because a filing that late still counts positions in
that quarter, while its due date was 2024-02-14 — an as-of reader should use `due_date` and the
value share filed by then. Second, the identity links are a 2026 snapshot: a row's financial values
are point-in-time, its LEI is not.

## What the panel does not establish

- **As filed, unaudited by the SEC.** DERA extracts the data sets as filed and does not verify them.
  Filer typos survive: five rows sit at period ends from 2028 to 2215 because a filing said so.
- **A vintage list is not a correction history.** A later, different value is recorded as
  `restated`; nothing here says why it changed, and an unchanged repeat is a `confirmation`, not
  evidence of an audit.
- **Revenue is tag-dependent.** `Revenues`, `RevenueFromContractWithCustomerExcludingAssessedTax`
  and `SalesRevenueNet` all map to `revenue`; one tag per filing and period is kept, in that
  priority, and the chosen `concept` is on every vintage. Two filers' revenue lines are comparable
  only if their concepts are.
- **Institutional ownership is 13F long positions only** — no shorts, no holders below the
  threshold, no non-13F holders — and it is never divided by shares outstanding here.
- **13F dollar values before 2023-01-03 were reported in thousands** and the adapter converts them;
  filers who reported dollars anyway are inflated by 1,000 in the *quarter totals* of the
  construction row (the per-issuer aggregates of linked CUSIPs have a median implied price of
  $15.65 in 2019 and $10.76 in 2024, so they are not systematically distorted, but the
  `value_linked_by_quarter` denominators before 2023 are).
- **The 13F report period is the one the filer stated.** Eight issuer-quarters sit in 1987 and a few
  in 2001-2004 because a 2020s filing stated that period; they are kept as filed.
- **A missing LEI is not the absence of an LEI**, and a missing ownership row is not the absence of
  institutional holders. Both are the absence of a published link.
- **No currency conversion**: 20-F and 40-F filers keep their reporting currency in `unit`.
- **Rows are not a sample.** They are every fiscal period these two sources report for every filer.

## Follow-ups

- The binding gap is CUSIP → issuer CIK. Two acquirable sources would fix it: OpenFIGI's mapping
  API (`ID_CUSIP` → FIGI with ticker and exchange, which then meets `sec_issuer_reference`'s
  published `issuer_listing` ticker@MIC edges — the `figi_ticker` mapping spec already declares the
  second half), or the SEC's own quarterly "Official List of Section 13(f) Securities" (CUSIP plus
  issuer name, which would still need a name match and is therefore not a fix on its own).
  With a CUSIP→CIK link the ownership stage would cover most of the 87M 13F rows unchanged.
- `ssga_dia_holdings` publishes a ticker and a CUSIP on the same holding row for 30 issuers, but
  without a MIC, and the `sec_cik_ticker` spec requires MIC scope; a dated ticker@MIC source would
  turn those into links.
- `sec_company_assets` is used only for filings before 2012-10-01. Its 2009-2012 filings also carry
  cover-page share counts that FSDS lacks; a later pass could keep companyfacts as a second opinion
  on the FSDS era instead of partitioning by filing date.
- A published UEI→LEI source (SAM entity extract with an LEI field, or GLEIF's own vendor mappings)
  is what the federal-contracts block needs.
