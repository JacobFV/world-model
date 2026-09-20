# The OpenFIGI bridge: turning 13F CUSIPs into issuers

[firm-panel.md](firm-panel.md) ends on a measurement and a name for the fix:

> The information table publishes the CUSIP, a free-text issuer name and no issuer CIK. … Of
> 87,357,054 holding rows read, 1,511,440 (1.7%) are in a CUSIP that chain ties to a CIK. … The
> aggregate covers **393 issuers and 14,841 issuer-quarters**; 371 of those issuers file no
> financial statements in these sources (they are funds).
>
> The binding gap is CUSIP → issuer CIK. … OpenFIGI's mapping API (`ID_CUSIP` → FIGI with ticker
> and exchange, which then meets `sec_issuer_reference`'s published `issuer_listing` ticker@MIC
> edges — the `figi_ticker` mapping spec already declares the second half).

This page is that route: what was acquired, how it was held to the publisher's limits, which
bridges it supports, what is refused, and what the measurement says it is worth.

**The short version.** 11,246 requests to the OpenFIGI mapping API over 7 h 51 min for 199,048,648
bytes, published as 804,007 records (24.8 MB gzipped): 746,777 mapping statements and 57,230
securities. 51,651 of those statements become `cusip` identity claims, 50,794 survive the 1:1
cardinality their mapping specification declares, and 2,620 MIC-scoped listings meet
`sec_issuer_reference`'s `issuer_listing` edges with no conflicts at all. Composed, that is a
published CUSIP → issuer map over **5,362 CUSIPs**, and on the 13F holdings it moves the share of
rows that reach an issuer from **1.88% to 56.30%**, the issuers covered from **393 to 5,054**, and
the issuers that actually file financial statements — operating companies rather than funds — from
**22 to 4,799**. The two independent routes that reach an issuer never disagree, and neither does
the new map disagree with the GLEIF chain on any of the 25 CUSIPs both of them cover.

## The route, and why

**`POST https://api.openfigi.com/v3/mapping`**, acquired with the `url_list` strategy as one
request per batch of mapping jobs. The mapping endpoint is POST-only — the identifiers being asked
about travel in the request body — so the declaration has to name every identifier it will send,
and [`build_requests.py`](../data/openfigi_mappings/build_requests.py) derives that list from
pinned normalized artifacts rather than anyone writing it by hand.

Three request groups:

| group | jobs | what it asks | requests |
| --- | ---: | --- | ---: |
| `cusip` | 94,924 | `idType: ID_CUSIP` for every check-digit-valid CUSIP on a 13F information table | 9,493 |
| `ticker_mic` | 7,665 | `idType: TICKER` + `micCode` for every SEC-published symbol whose SEC exchange value is an ISO 10383 MIC | 767 |
| `ticker_us` | 9,859 | `idType: TICKER` + `exchCode: US` for every SEC-published symbol | 986 |

### The limits are the publisher's, and they were measured

The documentation states 25 requests per minute and 10 mapping jobs per request without an API
key, and 25 per 6 seconds with 100 jobs with one. Both unkeyed limits were checked against the
service on 2026-09-19 before anything was declared:

| request | answer |
| --- | --- |
| 3 jobs | `HTTP 200`, `ratelimit-policy: 25;w=60`, `ratelimit-limit: 25` |
| 11 jobs | `HTTP 413` — `Request may only contain 10 mapping jobs.` |
| 100 jobs | `HTTP 413` — the same |

So the declaration asks for ten jobs a request at `requests_per_second: 0.4` (24 a minute), the
runner honours `Retry-After` and retries the documented 429/5xx statuses with exponential backoff,
and the whole acquisition was **11,247 requests over 7 h 51 min** (2026-09-19 20:04 to 2026-09-20
03:55 UTC) for **199,048,648 bytes** (220,520,253 on disk) against a declared 400,000,000. No key
is held; an API key would cut the run to well under an hour, and obtaining one needs a password
account this session cannot create.

### Why the request carries a MIC

`sec_issuer_reference` publishes an issuer's listings as `issuer_listing` edges to
`ticker:<MIC>:<symbol>`, and the `figi_ticker` mapping specification requires a `mic` scope,
because a bare ticker is not an identifier. **OpenFIGI's answer carries an `exchCode`, not a MIC** —
`UN`, `UW`, `UA`, `UP`, `UF`, `PQ` are Bloomberg's own exchange codes, and nothing in this catalog
publishes a map from those to ISO 10383. Translating them would be a guess dressed as a join.

The mapping API takes a `micCode` *filter*, though, so the second group asks the question the other
way round: the request carries the MIC and OpenFIGI answers inside it. The MIC on the resulting row
is then OpenFIGI's own scoping of the listing, and `figi_ticker` is assertable at the scope its
specification demands without anyone inventing a crosswalk.

The SEC's other three scope values are not MICs — `OTC` (1,963 symbols), `CBOE` (45) and a blank
`SEC_UNSPECIFIED` (187) — so those symbols are asked for only at OpenFIGI's own `US` composite
scope, in the third group.

### The answer is positional, which is what makes a bad one dangerous

The mapping endpoint returns one result per job, in request order, and **does not echo the
identifier it answered**. A response with a different number of results than its request had jobs
would silently re-align every following result with the wrong CUSIP — the worst possible failure
for an identity layer, because it looks like data. The declaration is therefore the only thing that
maps a result back to its query, and the pipeline holds every shard to its request's job count.

It caught something on the first run. **Six of the 11,246 requests came back as `HTTP 200` with the
one-element body `[{"error":"There was an error while processing this request."}]`** — the service
saying it did not process the request at all. Because the status is 200 the runner cannot retry it.
Re-sending all six by hand answered correctly, so the failure is transient, and the pipeline now
reads that exact shape as a **counted failed request**: 6 requests, 60 CUSIPs never mapped, nothing
published for them. Any other length mismatch still fails the build.

### What the CUSIPs are

141,566 distinct CUSIP-shaped values appear on the 13F information tables of `sec_13f_history`
(132,085) and `sec_ownership_datasets` (43,754). **94,924 carry a valid modulus-10
double-add-double check digit**; 46,642 do not.

The failures are not noise, they are a convention: 13F filers construct a CUSIP for an option by
putting a 9 in the seventh position — `037833900` for an Apple call, `037833950` for an Apple put,
`78462F953` for an SPDR put. They belong to no issue, they fail the check digit, and they are never
sent. They carry 1,382,328 of the 87,357,054 holding rows (1.6%), and the option rows among them
are already excluded from the ownership aggregate. The check digit does not catch `000000000`,
which is a filer placeholder for a security they could not identify and which passes; it is refused
by value in [`worldmodel/resolution/openfigi.py`](../worldmodel/resolution/openfigi.py).

### What the route cannot do

* It is an **undated snapshot**. OpenFIGI dates no mapping, so nothing here is dated; the retrieval
  date in every record's evidence is the only date there is. A ticker that moved between issuers,
  or a CUSIP reassigned after a corporate action, is read at today's value.
* It is a **security master, not an issuer register**. A FIGI names an instrument. Reaching an
  issuer takes a second published edge, and the composition is a crosswalk, not a merge.
* Coverage is whatever OpenFIGI holds. A CUSIP it does not answer says nothing about the security.
* The terms disclaim the data: the identifiers and descriptions are supplied **as is**, with no
  warranty and liability capped at US$50.

## What was acquired

**804,007 published records** — 746,777 `openfigi_mapping` assertions and 57,230 `security`
entities — from 11,246 shards.

| answers, by the level of the FIGI returned | statements |
| --- | ---: |
| `venue` — one exchange's line of a security whose composite is elsewhere | 656,631 |
| `unlisted` — an instrument OpenFIGI publishes no composite for (bond, muni, preferred) | 31,027 |
| `composite` — another country's composite of the same security | 29,840 |
| `us_composite` — the United States composite (`exchCode` `US`) | 29,279 |

| answers, by what was asked | statements |
| --- | ---: |
| `ID_CUSIP` (no scope) | 735,502 |
| `TICKER` at `exchCode: US` | 8,655 |
| `TICKER` at `micCode: XNYS` | 2,620 |
| `TICKER` at `micCode: XNAS` | **0 — see below** |

By market sector: 715,750 Equity, 14,888 Corp, 10,460 Muni, 2,273 Mtge, 2,172 Pfd, 1,234 Govt. At
the two levels that can become identity, the securities are 14,846 Corp, 13,049 Common Stock,
12,497 Mutual Fund, 10,460 Muni, 2,172 Preferred Stock, 2,045 Depositary Receipt, 1,175 Pool, 737
Warrant, and a tail of notes, bills, REITs and units.

**54,685 of the 94,924 CUSIPs (57.6%) got at least one answer.** An entity record is published only
for a FIGI this catalog can meet — 23,583 US composites, 31,027 unlisted instruments and 2,620
MIC-scoped listings — because a quarter of a million Frankfurt and Mexico City lines of US equities
would be a bigger graph, not a better-joined one; those rows are published as statements only.

### Nasdaq: the MIC the SEC names is not the MIC the venue uses

All 767 `ticker_mic` requests for `XNAS` (4,365 symbols) returned **no matches**, and that is not
"Nasdaq publishes no listings". Probed directly:

```
AAPL XNAS  0 matches           AAPL XNYS  1  BBG000B9XVV8 exchCode UN
AAPL XNGS  1  BBG000B9Y5X2 UW  AAPL ARCX  1  BBG000B9XWM6 exchCode UP
AAPL XNMS  0 matches           AAPL BATS  1  BBG000B9Y6P9 exchCode UF
AAPL XNCM  0 matches           AAPL XASE  1  BBG000B9XSK7 exchCode UA
```

OpenFIGI files a Nasdaq listing under the **segment** MIC `XNGS` (Nasdaq Global Select Market).
`sec_issuer_reference` maps the SEC's `Nasdaq` label to the **operating** MIC `XNAS`. They are both
correct ISO 10383 codes at different granularities, and `ticker:XNAS:AAPL` and `ticker:XNGS:AAPL`
are different scopes, so **an `XNGS` answer is not rewritten as an `XNAS` edge here.** The
consequence is that the MIC-scoped listing crosswalk covers NYSE and not Nasdaq; the US composite
route covers both, and the measurement below is not weakened, because every issuer the MIC route
reaches the composite route reaches too and they never disagree. The fix is in
[follow-ups](#follow-ups) and it is already half-published: `iso_mic_venues` carries 1,289
`mic_operating_venue` assertions, which is ISO 10383's own statement that `XNGS` rolls up to
`XNAS`.

`XNYS` itself answered 2,620 of 3,300 symbols; most of the remainder are NYSE American and NYSE
Arca listings the SEC labels `NYSE`, which sit under `XASE` and `ARCX`.

## The bridges

OpenFIGI's answers are **not** published as `identifier_assignment` rows. The FIGI in an answer is
OpenFIGI's own assignment — it is the FIGI registration authority — but the CUSIP or ticker in the
answer is the *query*, the identifier this catalog asked about. Turning "OpenFIGI answered this
CUSIP with this FIGI" into "this CUSIP is this security" is a judgement, so it is made in the
resolution layer ([`worldmodel/resolution/openfigi.py`](../worldmodel/resolution/openfigi.py)),
where each family is held to the cardinality its specification declares and `unify-resolve
--no-bridges` turns the whole layer off in one flag.

| bridge / spec | cardinality | rows | linked | refused |
| --- | --- | ---: | ---: | ---: |
| `openfigi_cusip_figi` (bridge + `link_mapping` spec) | 1:1 | 51,651 | **50,794** | 345 CUSIPs (2,319 conflicting pairs) |
| `figi_ticker` (`link_mapping` spec, MIC-scoped) | 1:1 in a `mic` scope | 2,620 | **2,620** | 0 |

**`openfigi_cusip_figi`** fires only where the answer names *one instrument*: the US composite
(`exchCode` `US`), or a row with no composite at all. A `venue` row is one exchange's line of the
security and a non-US `composite` is another numbering agency's business; neither is what a CUSIP
names, and neither is ever read as identity. The claim is asserted on the **security**, never on an
issuer: it says `cusip:037833100` and `figi:BBG000B9XRY4` are one security, and nothing about Apple
Inc.

**`figi_ticker`** fires only on an answer whose request carried a `micCode`, and it uses the ticker
OpenFIGI published rather than the one that was asked for, so a symbol the publisher spells
differently (`BRK/B` against the SEC's `BRK-B`) is not silently renamed. Every row carries
`temporal_validity: unknown`, because OpenFIGI dates no mapping.

### What is refused, and why

| refusal | count | why |
| --- | ---: | --- |
| a CUSIP whose check digit does not recompute | 46,642 values | a filer's option pseudo-CUSIP (9 in the seventh position) is not a CUSIP and belongs to no issue |
| `000000000` | by value | it passes the check digit and is a filer placeholder for an unidentified security |
| `venue`-level rows | 656,631 statements | a CUSIP does not name a listing |
| non-US `composite` rows | 29,840 statements | a CUSIP is a North American number; a German composite is another identifier's business |
| a CUSIP two securities answer | 345 CUSIPs | the 1:1 specification forbids it |
| an exchange code read as a MIC | all of them | `UN`/`UW`/`UA` are Bloomberg's codes and nobody publishes the translation |
| an `XNGS` answer read as an `XNAS` edge | all 0 answers | the segment MIC and the operating MIC are different scopes |
| a bare ticker | always | symbols are reused across venues and reissued over time |
| a FIGI read as its issuer | always | a FIGI names an instrument |
| requests the service failed | 6 requests / 60 CUSIPs | `HTTP 200` with a request-level error body |

The 345 refused CUSIPs are worth looking at, because the rule catches something a human would have
to know the history to catch. They are almost all closed-end funds that were renamed or
reorganised and now carry two US composite FIGIs for one CUSIP:

```
003009867  BBG000BB3MF4 'ABRDN ASIA-PACIFIC INCOME'  | BBG000BSB1Q2 'ABRDN ASIA-PACIFIC INCOME'
003011111  BBG000BB6Q80 'ABRDN AUSTRALIA EQUITY FUND'| BBG000BTWTP7 'ABRDN AUSTRALIA EQUITY FUND'
00301W105  BBG000BGWCV5 'ABRDN EMERGING MARKETS EX CH'| BBG00KTGCQ61 'ABRDN EMERGING MARKETS EX CH'
```

675 of the refused rows are Equity and 182 are Mtge. **No FIGI was refused for carrying two
CUSIPs**, and **no ticker@MIC was refused at all** — the listing side is perfectly one-to-one over
2,620 rows. One symbol on the SEC's own side is refused: `ticker:CBOE:BRZL` is published for two
CIKs (`0000927971` and `0001420924`), so no issuer is read from it.

## The measurement

Two measurements: a direct count against the 13F holdings, which is what the firm panel's number
means, and the identity layer's own joined-share measurement.

### The 13F holdings, counted the firm panel's way

`data/openfigi_mappings/measure_13f_linkage.py` reproduces the panel's rules exactly — the same
amendment resolution (9,971 restatements applied, 4,666 new-holdings amendments, 10,677 superseded
filings), the same exclusion of put/call and principal-amount rows — and then recounts with the
CUSIP → issuer map composed from published edges. Run against the published artifacts; **the
baseline reproduces `docs/firm-panel.md` to the row**.

| | before (GLEIF chain) | after (OpenFIGI) | both together |
| --- | ---: | ---: | ---: |
| CUSIP → CIK links | 20,151 | 5,362 | 25,488 |
| …that appear on a holding row | 836 | 5,362 | 6,173 |
| holding rows reached, of 87,357,054 | 1,640,499 (**1.88%**) | 49,177,772 (**56.30%**) | 50,312,775 (57.59%) |
| rows the ownership aggregate counts, of 77,484,037 | 1,511,440 (1.95%) | 44,534,997 (**57.48%**) | 45,598,956 (58.85%) |
| …as the panel prints it (aggregated rows over all rows) | **1.73%** | **50.98%** | 52.20% |
| issuers covered | **393** | **5,054** | 5,422 |
| issuer-quarters | 14,841 | **180,334** | 194,135 |
| issuers filing financial statements in these sources | 22 | **4,799** | 4,804 |
| issuers SEC types `operating` | 18 | **4,312** | 4,314 |
| issuers SEC types `other` / `investment` / untyped | 185 / 3 / 187 | 741 / 1 / 0 | 917 / 4 / 187 |

Against the baseline `docs/firm-panel.md` states — 1.7%, 393 issuers, 371 of them funds — the route
takes the holdings from **1.7% to 51.0% of rows**, the issuer count from **393 to 5,054**, and it
inverts the composition: before, 371 of 393 issuers filed no financial statements; after, **4,799
of 5,054 do**. That is the difference between a fund crosswalk and an operating-company one, and it
is the whole point of the track.

The two maps barely overlap and never disagree: 25 CUSIPs are in both, 5,337 only in the new one,
20,126 only in the GLEIF chain, and **0 disagreements**. The GLEIF chain's 20,151 links are
dominated by debt on a few fund issuers (one CIK carries 9,338 of them) and touch only 836 CUSIPs
that anyone actually reported holding; the new map's 5,362 links are the equities that carry the
rows. They are complementary, which is why the combined row is the one a rebuild should use.

Inside the new map, the two ways of reaching an issuer also never disagree: 2,620 MIC-scoped
answers and 8,654 US-composite answers produce 8,654 FIGI → CIK links with **0 FIGIs reached by
two different CIKs**.

### The identity layer, over the resolution's scope

Two `unify-resolve --no-attach` runs over the same scope and the same code, differing only by
whether this dataset is in it
(`--exclude census_aspep,epa_aqs_daily,fred_deposit_rates,fred_state_employment_vintages,market_corporate_actions,opm_fedscope`),
52 min per run, 1.33 GiB peak RSS. The scope is a clean control: the entity count grows by
**exactly 57,230**, which is this dataset's own entity count, so nothing else moved between the
two runs.

| | before | after | change |
| --- | ---: | ---: | ---: |
| entity IDs in scope | 9,048,525 | 9,105,755 | +57,230 (the new dataset's own) |
| clusters | 148,123 | **164,959** | +16,836 |
| entity IDs in a cluster | 318,851 | **356,282** | +37,431 |
| **joined across ≥2 datasets** | 526,719 (**5.821%**) | **567,052 (6.227%)** | +40,333 |
| **joined across ≥2 publishers** | 424,447 (**4.691%**) | **464,780 (5.104%)** | +40,333 |
| joined by a shared entity ID alone | 233,618 (2.582%) | 233,618 (2.565%) | 0 |
| shared-identifier links | 196,270 | 220,939 | +24,669 |

The bridge's own accounting:

| count | value |
| --- | ---: |
| `bridge_claims_openfigi_cusip_figi` | 51,651 |
| `bridge_cardinality_refusals_openfigi_cusip_figi` | 345 |
| **`bridge_joined_openfigi_cusip_figi`** (claims that met another publisher) | **24,669** |
| `identifiers_figi` (new) | 57,230 |
| `identifiers_cusip` | 2,422,613 → 2,474,264 |

The most interesting line is one the bridge does not own. **`bridge_joined_gleif_isin_cusip` goes
from 9,288 to 13,362.** GLEIF's ISIN-to-LEI mapping has published 2,291,868 CUSIP claims in this
catalog all along and almost none of them met anything; 4,074 more of them now do, because the
FIGI securities give those CUSIPs a second publisher to meet. A bridge that makes an existing
bridge worth more is the shape this layer is supposed to have.

By domain, one moves, and it is the right one:

| domain | joined before | joined after |
| --- | ---: | ---: |
| companies | 158,598 (4.06%) | **198,931 (5.02%)** |
| demographics | 344,828 (32.43%) | 344,828 (32.43%) |
| energy_trade | 219,804 (8.49%) | 219,804 (8.49%) |
| macro / politics / transport | unchanged | unchanged |

The new cluster shape enters the catalog's top four:

| cluster shape | before | after |
| --- | ---: | ---: |
| `acs_5yr_tables` + `census_geography` + `fema_nri` + `lehd_lodes` | 83,059 | 83,059 |
| `opensanctions` + `opensanctions_graph` | 62,951 | 62,951 |
| `sec_gleif` + `wikidata_identifiers` | 50,336 | 50,336 |
| **`openfigi_mappings` + `sec_ownership_datasets`** | — | **19,738** |

`by_entity_type` in the report is unchanged, and that is not a null result but an artefact worth
stating: `join_coverage` reads entity types from the **index**, and the index was built before this
dataset published, so it holds none of its 57,230 securities. The same is true of any measurement
taken against the index rather than the scope until the lead rebuilds it.

### Nothing was attached

Both resolution runs used `unify-resolve --no-attach`. `data/world_evidence/index.sqlite` and its
`resolved` table are unchanged, the candidate clusters and reports are in the session scratchpad
rather than the data root, and nothing was added under
`data/world_evidence/resolution_history/`.

**Attaching is the lead agent's step**, and it needs the index rebuilt first, because the index as
it stands was built before this dataset published and therefore holds none of its 57,230 security
entities:

```sh
wm unify --exclude census_aspep,epa_aqs_daily,fred_deposit_rates,fred_state_employment_vintages,market_corporate_actions,opm_fedscope
wm unify-resolve --workdir data/world_evidence/resolution_history/<date>-openfigi \
  --exclude census_aspep,epa_aqs_daily,fred_deposit_rates,fred_state_employment_vintages,market_corporate_actions,opm_fedscope
```

`unify-resolve` exports what is attached before replacing it (`report['previous_resolution']`), so
the current resolution stays re-attachable with `wm graph-attach-resolution --workdir <that
directory>`.

## What this does not establish

* **That a CUSIP's issuer is this issuer.** The route composes three published edges — OpenFIGI's
  answer to a CUSIP, OpenFIGI's answer to an SEC-published symbol, and the SEC's own
  `issuer_listing` edge. Each one is published; the composition is this repository's, and it is a
  crosswalk, not a merge. No `same_as` is asserted between a security and an issuer anywhere.
* **That the link is current, or was ever current on a given date.** OpenFIGI dates no mapping and
  `sec_issuer_reference` publishes a current snapshot with no listing dates. Every row is an
  undated 2026-09 reading applied to holdings reported from 2013 to 2025. A ticker that moved
  between issuers, or a CUSIP reassigned in a corporate action, is read at today's value — this is
  the largest single caveat on the 56% and the one a user should check before attributing an old
  quarter's holding to an issuer.
* **That OpenFIGI is right.** The terms supply the data as is. Where two securities answer one
  CUSIP the row is refused, not adjudicated; nothing here checks an answer against CUSIP Global
  Services, which is the register that issued the number.
* **That the remaining 43% of rows have no issuer.** 40,239 of the 94,924 CUSIPs got no answer at
  all, and most of the answered ones are bonds, munis and mutual funds whose issuer has no
  `issuer_listing` edge because it has no exchange-listed ticker. A missing link is the absence of
  a published route, not the absence of an issuer.
* **That the panel now covers these issuers.** This measurement counts what the published edges
  would reach. `firm_panel` has not been rebuilt against them, and its `ownership` stage still
  reads the GLEIF chain alone.
* **That the identity layer joins a CUSIP to a CIK.** It does not and should not: the bridge joins
  `cusip:` to `figi:`, both securities. The issuer arrives through relationship edges, which is
  why the clusters below grow in securities rather than in companies.

## Follow-ups

1. **Nasdaq at the right MIC.** 4,365 SEC-published Nasdaq symbols have no MIC-scoped listing here
   because OpenFIGI files them under `XNGS` and the SEC label maps to `XNAS`. The next acquisition
   should ask for `XNGS`, `XNMS` and `XNCM` (about 1,300 more requests, under an hour) and compose
   through the `mic_operating_venue` assertions `iso_mic_venues` already publishes, which are ISO
   10383's own statement that a segment MIC rolls up to an operating MIC. That is a published edge,
   so it needs no new judgement — only the request.
2. **Re-acquire the six failed requests.** They are transient and recoverable, and 60 CUSIPs is the
   whole cost, but the acquisition is not resumable once committed, so it waits for a re-run. A
   runner that treated a one-element error body as a retryable failure would fix the class.
3. **An API key.** 25 requests per 6 seconds and 100 jobs per request would turn a 7 h 51 min
   acquisition into about 20 minutes, which is the difference between "re-acquire when something
   changes" and "re-acquire once a year". It needs a password account.
4. **Rebuild the index and attach.** Until then the bridge is worth what the hub measurement says,
   not what the scope measurement says.
5. **The GLEIF chain and this map are complementary, and the panel should use both.** 20,126 CUSIPs
   only the GLEIF chain reaches, 5,337 only this one, 0 disagreements. `firm_panel`'s `links` stage
   takes CUSIPs from GLEIF alone; adding this map is a one-input change that takes its ownership
   coverage from 393 issuers to 5,422.
6. **`ssga_dia_holdings` is still stranded.** It publishes a ticker and a CUSIP on the same holding
   row for 30 issuers but no MIC. Those CUSIPs are now in this map, so the 30 rows can reach an
   issuer through the CUSIP without anyone having to scope the ticker.
