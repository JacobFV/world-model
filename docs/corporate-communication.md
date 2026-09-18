# Corporate communication

Organizational speech, as against a person's. Implementation of the disclosure and instrument
layer over `docs/agents-design.md` sections 2 and 3.
Code: `worldmodel/agents/disclosure.py`, `worldmodel/agents/instruments.py`.
Tests: `tests/test_agents_disclosure.py` (34), `tests/test_agents_instruments.py` (20).

A person gossips. An organization **discloses**, and an institution **issues**. Those are three
different acts, and the differences are structural rather than stylistic. What follows is the list
of places where they are different, the real filings the whole thing was built against, and the
two kinds of silence the model is careful to keep apart.

## 1. What is structurally different from a person speaking

The reference point is `~/Documents/tensacode/tensacode/python/research/civ_sim/talk.py` and
`minds.py` (`_sayable`, `_speak`), which this deliberately diverges from.

| | Person (`civ_sim/talk.py`, `minds.py`) | Firm (`disclosure.py`) | Institution (`instruments.py`) |
| --- | --- | --- | --- |
| **The act** | an utterance to whoever is standing within 6 metres. `converse` pairs neighbours; there is no addressee beyond "the person in front of me" | a `Disclosure`: a **form** from a closed vocabulary, an **audience**, an **effective date**, an **authorizing role**, and a permanent place on a register | an `Issuance`: an exercise of power over a *subject*, which must pass `authorize` against declared power **and** declared jurisdiction |
| **Who speaks** | a `pid`. One mind, one mouth | a **role**, seated by published record. `Disclosure.role_id` is the office; `Disclosure.holder` is whoever the record places in it. The office speaks, not the person | a role again, and the act is refused rather than performed when the seat or the power is not there |
| **Selection** | `_sayable(m)` builds a weighted list and `converse` samples it. What a person says is *drawn* from what they know | `DisclosureDesk.select` sorts held claims into disclose / defer / withhold, and the **only** admissible reason is an obligation. Nothing samples anything | what is issued is what authority reaches; nothing else can be issued at all |
| **Timing** | immediate. There is no interval between knowing and saying | `known_at` and `effective_date` are separate fields and `Gap.delay_days` is their difference. **Delay is a choice and it is made every quarter** | `effective_from`, `expires`, and the acts that move them (`lapse`, `contest`) |
| **Loss** | lossy by construction: dialects, `parse` failures, `misheard the amount`, a `noise` parameter that corrupts what was heard | **lossless and exact.** A filing is a document; the number in it is the number. What varies is *whether it was said at all, when, and to whom* | exact, and the instrument's text is not even the interesting part - its reach is |
| **Falsehood** | `talk.py` has `_invert` and a `lie` flag | **there is no deception flag anywhere.** `test_there_is_no_deception_flag_anywhere_in_the_model` pins that the dataclasses carry no `lie`, `misleading` or `false_statement` field. What is modelled is *selection under obligation* | same; an instrument is contested, not disbelieved |
| **Reach** | whoever is nearby | a lattice. A public filing reaches the regulator and counterparties; a private briefing to a counterparty reaches nobody else. `AUDIENCE_REACH` is that lattice | an audience plus named recipients: who a subpoena was *served on* is part of what it is |
| **Permanence** | an utterance is gone once heard | `DisclosureRegister` is **append-only**. A correction is a new disclosure whose `supersedes` names the old one, and both stay queryable | `Institution.instruments` grows; an instrument lapses, it is not deleted |
| **Consequence** | trust moves | the act **discharges an obligation**, or fails to. `compliance` is the legal consequence, with a citation | legitimacy erodes for the attempt, whether or not it succeeded |

Two things are shared on purpose:

* **Reception is one mechanism.** `disclosure.receive` turns a disclosure *or* an instrument that
  reaches an agent into a claim in that agent's store. A subpoena served on a firm and a 10-Q read
  by the public go through the same function, and both land with
  `Evidence.method = 'disclosed_by:<org>/<channel>'`.
* **`authorize` is the existing one.** `roles.authorize` is used unchanged, with its three
  outcomes; nothing here reimplements permission.

## 2. The gap between belief and statement

This is the substance. A firm's claim store holds what it holds; what it *says* is a selected,
timed, audience-scoped subset of that.

```
firm:<org>          what it holds        (already the firm's institutional memory)
disclosed:<org>     one claim per statement, derived_from the claim it says
disclosure_refusals:<org>   statements attempted without authority
```

`DisclosureDesk.gaps(audience=…, at=…)` returns a `Gap` per held claim in one of three states:

* **`undisclosed`** — held, said to nobody. Queryable, inspectable, explainable; simply not on the
  register. (`test_an_undisclosed_belief_is_queryable_and_distinct_from_a_disclosed_one`)
* **`disclosed`** — said to this audience, with the date and the accession number, and
  `delay_days` between holding and saying.
* **`selectively_disclosed`** — said to *someone*, but not to this audience. This is the state that
  made scope worth modelling: the same claim is `disclosed` to the counterparty and
  `selectively_disclosed` to the public, in the same store, at the same instant.

Saying something writes a `disclosed` claim whose `derived_from` is the claim it says, so
retracting what the firm holds withdraws the record that it said it — the firm no longer holds the
thing it disclosed, and the provenance graph knows.

**Selection** (`select`) has exactly six reasons, all of them about obligation:
`already_disclosed`, `compelled_now`, `compelled_overdue`, `not_yet_due`,
`no_obligation_declared`, `obligation_unknown`. Nothing weighs advantage, reputation or strategy,
because none of that is published. An obligation is the only thing that moves a claim out of
silence, and an obligation that is `Unknown` moves it nowhere.

## 3. Obligation, and the two kinds of silence

`compliance(obligation, trigger_at, disclosure, now=…)` returns one of five states, and the
distinctions between them are the point:

| state | meaning |
| --- | --- |
| `met` | disclosed, in a form that can discharge it, reaching the obliged audience, on or before the deadline |
| `unmet` | the deadline passed. Either nothing was said, or what was said came late, or what was said cannot discharge it |
| `pending` | declared, triggered, not yet due. Silence before a deadline is not a violation |
| `unknown` | an obligation is named but its terms or its deadline are not declared, or the record does not publish when it was triggered |
| `no_obligation` | nothing is owed. **Silence is not a violation** |

`ComplianceFinding.compliant` returns `True`, `False`, or a `tc.Unknown` — there is no path by
which an Unknown obligation returns `True`
(`test_an_unknown_obligation_is_not_treated_as_compliance`). Three places where the Unknown is
load-bearing rather than decorative:

* **Deadlines come from a published filer category.** `sec_issuer_reference` publishes
  `filer_category`; `FILER_DEADLINES` turns it into the day counts the regulation sets. With the
  category absent the deadline is `None` and compliance is `unknown` — *never* the middle row of
  the table.
* **Holiday calendars are not published here.** `business_days_between` counts weekdays only, so a
  business-day deadline missed by one to three days may not have been missed at all.
  `HOLIDAY_SLACK_BUSINESS_DAYS = 3` turns that band into `unknown` with the reason, and a Form 4
  filed 294 days late stays `unmet`.
* **Form matters.** A press release does not discharge a filing requirement and a briefing to a
  counterparty does not reach the public, however complete either is. Both come back as `unmet`
  with the reason on the finding.

`Obligation.owed_by` carries a distinction it is easy to get wrong: a Form 4 on an issuer's filing
record is the **insider's** duty and a Schedule 13D is the **acquirer's**. EDGAR files both under
the issuer, so without that field the issuer gets blamed for somebody else's lateness.

## 4. The worked example: LyondellBasell, 2025-2026

`sec:cik:0001489393`, the same issuer `worldmodel.agents.firm` is built against, because this
catalog publishes for this one CIK both halves of the join: **1,000 filings** with real accession
numbers, forms, filing dates and report dates (`sec_issuer_reference`), and **45 quarters** of
financials whose record ids end in the accession number they were filed under
(`sec_company_assets`). `worldmodel.agents.disclosure.worked_example(index=…, catalog=…)` runs it.

The gap is entirely grounded: `report_date` (the period the filing speaks about) and `filing_date`
(when it was said) are **two fields of the same published record**, so the distance between holding
a fact and disclosing it is read, not assumed.

```
LyondellBasell Industries N.V. | filer_category Large accelerated filer
periodic filings 37   insider filings 670

  2025-09-30  10-Q  0001489393-25-000056  filed 2025-10-31  held 31d earlier -> met
  2025-12-31  10-K  0001489393-26-000012  filed 2026-02-20  held 51d earlier -> met
  2026-03-31  10-Q  0001489393-26-000028  filed 2026-05-01  held 31d earlier -> met
  2026-06-30  10-Q  0001489393-26-000061  filed 2026-07-31  held 31d earlier -> met
```

### Something known at one date and disclosed at another

Read the desk on **2026-07-15**, two weeks after the quarter ended and two weeks before the 10-Q:

```
sec:cik:0001489393 disclosure desk  (audience: public)
  holds 16 claim(s), said 8 statement(s), refused 0
  disclosed    cash 2635000000.0  held 2026-03-31, said 2026-05-01 (31 day(s) later, 0001489393-26-000028)
  disclosed    cash 3443000000.0  held 2025-12-31, said 2026-02-20 (51 day(s) later, 0001489393-26-000012)
  disclosed    cash 1784000000.0  held 2025-09-30, said 2025-10-31 (31 day(s) later, 0001489393-25-000056)
  undisclosed  cash 2630000000.0  held 2026-06-30, not said to the public [pending]
  undisclosed  revenue 9177000000.0  held 2026-06-30, not said to the public [pending]
  undisclosed  capex 269000000.0   held 2026-05-01, not said to the public [no_obligation]
  ...
  obligation insider_transaction:4    unknown  the record does not publish when obligation
                                               insider_transaction:4 was triggered
  obligation periodic:10-k            met      disclosed 2026-02-20, due 2026-03-01 under 17 CFR 240.13a-1
  obligation periodic:10-q            met      disclosed 2025-10-31, due 2025-11-09 under 17 CFR 240.13a-13
```

`cash 2,630,000,000` for the quarter ended **2026-06-30** is held and not said. It becomes
`disclosed` on **2026-07-31** under accession **0001489393-26-000061**, 31 days later. On the same
screen the three quarters before it are disclosed with their own accession numbers, the capex
figures are undisclosed with **no obligation to disclose them separately**, and the Form 4
obligation is `unknown` because nothing in the store carries its trigger. Three different kinds of
silence, none of them called a violation, none of them called compliance.

The selection at that instant:

```
selection as of 2026-07-15
  disclose  already_disclosed      periodic:10-q    (6 claims)
  defer     not_yet_due            periodic:10-q    (2 claims: the 2026-06-30 cash and revenue)
  withhold  no_obligation_declared -                (6 claims: costs and capex)
```

### The same claim, held and said, in one provenance chain

Because `hold` writes the *same* claim the filing seeded — identical subject, predicate, value and
validity — the store keeps **one** claim carrying both grounds:

```
sec:cik:0001489393 disclosed ('cash', 'filing', ('public',), '2026-02-20', '0001489393-26-000012')
  ← disclosure:sec:cik:0001489393:...:d002 from:
    sec:cik:0001489393 cash 3443000000.0
      ← observed ... at secfacts:0001489393:us-gaap:CashAndCashEquivalents…:2025-12-31:0001489393-26-000012
        via published:sec_company_assets
      ← observed ... at secfacts:0001489393:us-gaap:CashAndCashEquivalents…:2025-12-31:0001489393-26-000012
        via held:sec_company_assets
```

Two evidences, one claim: *published* at the filing date, *held* at the period end. The gap is the
distance between them and it is visible in `explain`.

Every one of those statements carries the seat caveat from `authorize`, because SEC insider filings
publish a period of report rather than an appointment:

```
sec:cik:0001489393:d001  holds  sec:cik:0001634184
  the record last observed sec:cik:0001634184 in general_counsel on 2026-02-28;
  continued tenure is Unknown
```

The disclosure is attributed to the *office*, the record names who the office last held, and the
Unknown about the day after rides on `Disclosure.verdict_reasons` rather than being guessed away.

### A real unmet obligation

Filtering the 670 Form 4s on this issuer's record for a lag beyond the two-business-day statutory
deadline finds three that are unambiguously late:

```
4  0001562180-18-001729  transaction 2017-06-06  filed 2018-03-29  unmet, 294 day(s) late
4  0001562180-22-001424  transaction 2019-03-31  filed 2022-02-11  unmet, 1046 day(s) late
4  0001562180-23-005840  transaction 2021-12-10  filed 2023-07-10  unmet, 573 day(s) late
```

Both dates are on the same published filing record and the deadline is
`Exchange Act s.16(a)`. The obligation is recorded as `owed_by='insider'`: the issuer's filing
record carries it, but it is not the issuer's duty.

## 5. Institutional instruments: the House Budget Committee

`congress:committee:hsbu00`, the institution `worldmodel.agents.institution` is built against. Its
jurisdiction is **published**: the measures actually referred to it (`referred_to_committee`). So
one referral is inside its authority and another is not, on catalog data alone.

`INSTRUMENT_KINDS` is a closed vocabulary — `referral`, `rule`, `subpoena`, `finding` — where the
kind of instrument and the power `authorize` checks are the same word, so a role that may hold a
hearing does not thereby get to issue a subpoena. `COMMITTEE_INSTRUMENT_POWERS` (authored, like
`COMMITTEE_AUTHORITY` before it) gives the chair the verbs and a plain member none.

```
House Committee on the Budget | declared jurisdiction 200 subject(s)
  a measure referred to this committee, by the chair    -> issued
     2026-09-01  referral on congress:bill:118-hconres-117 by chair -> public
  a measure never referred to this committee, by the chair -> refused
     congress:measure:not-referred-to-hsbu00 is outside the declared jurisdiction of chair
  a measure referred to this committee, by a plain member  -> refused
     role member holds no power 'refer' (holds nothing)
  executor calls: 1
```

Three attempts, **two different refusals**, one executor call. That is why `authorize` has three
outcomes and why the reasons are carried: "outside the jurisdiction" and "holds no power" are not
the same failure, and a caller can tell them apart. A third case, an *undeclared* jurisdiction,
authorizes `unknown` rather than granting permission
(`test_an_undeclared_jurisdiction_is_unknown_rather_than_permission`).

Legitimacy after those three attempts, from the existing `Institution.read`:

```
legitimacy as of 2026-09-01  regime=contested
  mandate_coverage     1.0000   200 of 200 declared subject(s) covered by a current instrument
  authority_margin     0.3333   1 of 3 attempted act(s) were within declared authority
  ultra_vires          2.0000   2 ultra-vires attempt(s) on the record
  legitimacy           0.4966   mandate coverage 1.000 x ultra-vires erosion 0.497 x contestation 0.000
```

An instrument has a life: `Docket.lapse` ends it (mandate coverage falls and does not recover),
`Docket.contest` records that somebody with standing objected (legitimacy falls whether or not the
objection succeeds), and `contest(..., quashes=True)` lapses it early. A `subpoena` also carries a
declared 90-day term, so `in_force` is `False` outside it without anybody doing anything.

## 6. Reception: "X told me", for organizations

`receive(store, utterance, receiver=…, at=…)` writes the statement into the *receiver's* store with

```
Evidence(source   = Ref('disclosure:<org>:<id>'),
         locator  = accession number,
         method   = 'disclosed_by:<org>/<channel>')
```

never `published:<dataset>`, which is what an independent observation carries. `provenance_of`
reads the distinction back out and `disclosed_not_observed` answers "is the only reason this agent
believes this that somebody told it". A claim that carries both grounds returns both.

Because a `tc.Claim` id is *content identity*, the asymmetry the brief asks for falls out rather
than being engineered:

```
counterparty briefed 2024-10-05, channel 'briefing', locator = the briefing id
public read   2024-11-01, channel 'filing',   locator = 0000000042-24-000046
same claim id, different observed_at, different evidence
```

An agent outside the audience gets `tc.Unknown('not_in_the_audience', …)` and holds **nothing**;
overhearing a private briefing is not modelled, and a receiver outside the audience does not hold
the claim weakly.

## 7. What is grounded and what is authored

Grounded — nothing becomes a claim without a `dataset@version#record_id`:

* every filing's **form, accession number, filing date and report date** (`sec_issuer_reference`,
  one `sec_filing` event per filing);
* the issuer's **filer category**, which is what sets its periodic deadlines;
* the **quarterly numbers** and the accession number they were filed under (`sec_company_assets`,
  whose record ids end in the accession);
* a committee's **jurisdiction**: the measures referred to it (`congress_gov_api` through
  `referred_to_committee`).

Authored, and labelled as such in the code:

* **`FORMS`** — the vocabulary of corporate speech and its flags (`on_the_record`, `revisable`,
  `discharges_obligation`). The catalog publishes filings, not a taxonomy.
* **`SEC_FORM_OBLIGATIONS`** and **`FILER_DEADLINES`** — the rules that compel disclosure, with
  their citations. The *category* is published; the day counts are the regulation, written down
  rather than derived.
* **`HOLIDAY_SLACK_BUSINESS_DAYS`** — how far a business-day deadline may be missed before the
  miss is called `unmet` rather than `unknown`.
* **`INSTRUMENT_KINDS`**, **`COMMITTEE_INSTRUMENT_POWERS`**, **`REFERRAL_RECIPIENT`** — the verbs,
  who may use them, and where a committee referral goes. The jurisdiction is published; none of
  these are.
* **`DISCLOSING_ROLE`** — no catalog dataset publishes who signed a filing, so the role a
  disclosure is attributed to is declared by the caller and reported in `desk.unknown()`.

**Perception is bounded**, as the grounding contract requires. `filings_from_index` reads one
issuer's whole filing history as a single **range over the records primary key**
(`secsub:<cik10>:filing:<accession>`), because `sec_filing` records are `kind='event'` rows with no
subject and no entity id and therefore appear in no index — the same shape as voteview's roll-call
positions and handled the same way, by key, with a declared row budget (1,200) that is reported in
`unknown()` when it binds.

## 8. What this does not claim

* Nothing here is fitted to an outcome, scored against a baseline, or `validated`. Every deadline
  is a citation and every form flag is a declaration.
* No claim that a disclosure is true. The model has no notion of a false statement and deliberately
  none: what it can say is who was told what, when, and whether anything compelled it.
* No language model anywhere. A disclosure is a claim plus five declared properties, not a sentence,
  and nothing generates text.
* A published record is not an intention. That a firm filed 31 days after its quarter end is a
  fact; that it *chose* to wait is a reading the model makes representable and does not assert.

## 9. Running it

```sh
# both test modules; they skip cleanly when tensorcode is absent
python3 -m unittest tests.test_agents_disclosure tests.test_agents_instruments -q

# with the optional substrate from a local checkout
PYTHONPATH=<tensacode>/tensacode/python/src python3 -m unittest \
    tests.test_agents_disclosure tests.test_agents_instruments -q
```

The real-data tests additionally skip unless this checkout has
`data/world_evidence/index.sqlite`; the disclosure worked example also needs a published
`data/sec_company_assets`.
