# Informal channels: inferred affinity, and its epistemic status

Implementation: `worldmodel/agents/affinity.py`. Tests: `tests/test_agents_affinity.py`. The design
contract for the layer this sits in is `docs/agents-design.md`; the formal channels it sits beside
are `docs/agent-communication.md`.

## Why this exists

`transmission.py` grounds who may speak to whom in **formal** relationships: a committee seat, a
cosponsorship, a lobbying contact. Those are the channels a filing clerk knows about. People also
talk because they are *friends*, and friendship is published nowhere in 1.37 billion records.

The temptation at this point is to invent the missing edge — to match names against an alumni
roster, or to guess that two people with the same surname went to the same school. That temptation
is exactly what `docs/identity-units-crosswalks.md` measured and rejected: inferred name matching
scored **recall 0.0044, precision 0.0226, and wrongly merged 103,211 entities**. So this module
does something narrower and says so loudly: it infers a tie from **co-membership in a published
context**, records which context and which records, and stamps `inferred: true` on every one.

Three rules, and the tests enforce all three:

1. **A tie is never a published claim.** The predicate it writes is `affinity_with`, not
   `friend_of`. There is no `friend_of` string constant anywhere in the module
   (`test_friend_of_is_never_used_as_a_value_anywhere_in_the_module` checks the parse tree, so the
   prose may discuss it while no code value ever is it).
2. **No published support, no tie.** `tie()` returns `tc.Unknown('no_shared_published_context', …)`
   when no published edge joins the pair.
3. **Nothing is inferred from a name.** Identity comes from the `resolved` table or not at all.

## The signals, and the measured spread that motivates the model

Counted against `data/world_evidence/index.sqlite` (26,409,127 edges), by distinct members per
context:

| Signal | Predicate | Shape | Base | Rows | Contexts | Median | p99 | Max |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `kinship` | `family_member_of` | pair | 0.95 | 50,289 | 41,505 | 1 | 4 | 19 |
| `association` | `associate_of` | pair | 0.80 | 2,407 | — | pairwise | — | — |
| `board` | `director_of` | context | 0.75 | 85,275 | 56,977 | 1 | 5 | 20 |
| `co_authorship` | `authored` | context | 0.70 | 82,907 | 8,462 | 7 | 46 | 100 |
| `committee` | `committee_member` | context | 0.65 | 3,895 | 228 | 13 | 54 | 67 |
| `employer` | `employed_by` | context | 0.60 | 474 | 200 | 1 | 28 | 39 |
| `organization` | `member_of_organization` | context | 0.55 | 581 | 244 | 1 | 20 | 55 |
| `research_institution` | `research_affiliation` | context | 0.45 | 3,985 | 389 | 3 | 86 | 233 |
| `affiliation` | `last_known_affiliation` | context | 0.40 | 42,401 | 7,495 | 1 | — | 1,410 |
| `cosponsorship` | `cosponsored_measure` | context | 0.40 | 1,283,245 | 86,439 | 5 | 159 | 425 |
| `office` | `holds_position` | context | 0.35 | 996,250 | 208,621 | 2 | 30 | **12,671** |

`shape` is the important column. A **pair** signal's published edge names both people — it is
*attested* in the sense `transmission.Link.attested` means it, and `Context.attested_pair` says so.
A **context** signal attaches one person to a thing, and two people are joined by both pointing at
it.

The spread *inside* one predicate is the whole argument for a selectivity term. `holds_position`
has a median of two holders per position and a maximum of 12,671 (`opensanctions:Q13218630`,
*"United States representative"*; the runner-up is *"Member of the National People's Congress of
China"* at 11,288). Amy Klobuchar's own `holds_position` edge points at `opensanctions:Q4416090`,
*"United States senator"* — **2,870 holders**. Sharing *that* with somebody is very nearly no
evidence at all. At the other end, sixteen of the boards Arzu Alieva and Leyla Aliyeva both sit on
have two or three directors in total (seven of two, nine of three).

Two signals came with a wrinkle worth recording. `associate_of` is the closest this catalog comes
to publishing a social tie outright, and it is included as an attested pair signal — but what is
published is that *a source thought them associates*, not that they are friends, so the tie over it
is still inferred. And `research_affiliation` is sometimes published at *department* granularity
("Neuroscience Graduate Program, Oregon Health & Science University"), which is far more selective
than a whole university; the member count is read from the index rather than assumed.

## What the catalog does **not** support

`affinity.NOT_AVAILABLE` is a declared table, and every entry was checked:

* **Education: absent entirely.** Of the 101 predicates published in this index, **none** matches
  `educat` / `alma` / `school` / `student` / `degree` / `attend` / `alumn`, and no observation
  metric does either. A shared-alma-mater tie is therefore not inferable for *anybody* in this
  catalog. Nothing stands in for it.
  `RealAffinitySmokeTests.test_the_catalog_publishes_no_education_predicate_at_all` asserts the
  absence against the live index, so a future import that adds education will make the test fail
  and force the signal to be added deliberately.
* **Friendship: absent.** Nothing publishes `friend_of`, `knows`, or any declared social
  relationship between private individuals.
* **Institutional lineage: present but unreachable.** `institution_lineage_ancestor` has 2,421
  rows, but relates `openalex:I*` to `openalex:I*` while `research_affiliation` targets `ror:*`.
  Measured: the resolved cluster of `openalex:I142606810` is **empty**. Nothing bridges the two
  namespaces, so lineage cannot reach a person here and is not used.
* **Author identity across namespaces: split, and left split.** `authored` and
  `research_affiliation` name authors as `orcid:*` or `crossref:author:<sha256>`;
  `last_known_affiliation` names them as `openalex:A*`. Measured: the resolved clusters of
  `orcid:0000-0003-2029-7692` and `openalex:A5134754013` are both empty. The same human can appear
  as several unjoined nodes, so **every co-authorship count here is a lower bound**, and the two
  research signals describe disjoint populations. Joining them would take name inference.
* **Residence: absent.** `within` / `located_in` describe places and organisations, not where a
  person lives, so residential proximity — the classic informal-tie signal — is not inferable.
* **Contribution amounts: absent.** `fec` publishes `supports_candidate` as a per-cycle boolean.
  Shared donors can be *counted*, never weighted by money.
* **Roll-call agreement: deliberately left out.** Individual positions are `kind='event'` records
  with no subject, reachable only by primary key (`grounding.RollCalls`). Voting together is a
  plausible affinity signal; approximating it from the DW-NOMINATE scaling would not be the same
  thing, so it is absent rather than faked.

## The strength model

```
strength = min(1, noisy_or( base · selectivity · 0.85**repeat ) · duration · homophily)
```

**Selectivity** — `(members - 1) ** -0.75`. In a context of *n* people you have *n - 1* possible
counterparts, so the prior that any particular one is the person you actually talk to falls with the
count. The exponent is sublinear because attention in a large body is not uniform: you know the
people on your subcommittee, not a random draw from the chamber.

| members | 2 | 3 | 13 | 28 | 67 | 100 | 2,870 | 12,671 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| selectivity | 1.000 | 0.595 | 0.155 | 0.084 | 0.043 | 0.032 | 0.0026 | 0.00084 |

A 13-member subcommittee is 61× the evidence of the 2,870-member senator role. That factor is the
point of the term.

**Frequency** enters through the noisy-OR: more shared contexts always beat fewer, monotonically,
and saturate rather than overflowing past 1.

**The repeat discount** was added because the noisy-OR saturated. Measured on this index *before*
it existed, Klobuchar/Blumenthal and Alieva/Aliyeva both came out at exactly **1.0000** — two very
different ties that strength could no longer tell apart. Repeats of the *same* signal are therefore
worth `0.85**k` of the first: twenty-five bills cosponsored with the same colleague are mostly the
one fact that they are in the same coalition. It does **not** discount across signals, so a board
seat and a kinship edge stay two facts. After the discount those two pairs come out at 0.76 and
1.00. The nesting problem underneath it (four subcommittees of one parent committee count as four
contexts, not one) is damped but not modelled, because this index publishes no parent/child edge
between committee ids — declared as `combine.nesting_is_not_discounted` rather than quietly fixed.

**Duration** is a bonus of at most 0.25 over a decade, and is `Unknown` on nearly everything —
because **none of the co-membership predicates carries a date except `cosponsored_measure`**.
Measured: `authored`, `research_affiliation`, `holds_position`, `director_of`, `committee_member`,
`employed_by`, `member_of_organization`, `last_known_affiliation`, `family_member_of` and
`associate_of` all have **zero** rows with a non-null `valid_from`; all 1,283,245
`cosponsored_measure` rows have one. An undated tie gets neither bonus nor penalty, because absence
of a date is not evidence of a short relationship.
`test_the_co_membership_predicates_are_undated_except_cosponsorship` pins this against the index.

**Homophily** is a multiplier of at most 1.40 from `same_party` (+0.10), `same_delegation` (+0.10),
`same_cohort` (+0.08, the same year entering office, read from the earliest `holds_role` edge) and
`shared_donors` (+0.12, saturating at 50 shared `supports_candidate` committees). Every term is a
published fact; that people who resemble each other talk to each other is the **assumption**, and
it is a *bonus only*: an opposite-party pair earns nothing rather than being penalised, because
penalising would assert that opposition suppresses informal contact — a stronger claim than
similarity encouraging it. An unpublished term is a `tc.Unknown` with a reason and contributes
nothing.

**Homophily can never create a tie.** `tie()` refuses before homophily is even consulted, and
`test_homophily_on_its_own_never_makes_a_tie` builds two fixture legislators matching on party,
delegation *and* cohort with no shared context and asserts the answer is `Unknown`.

## The API

```python
from worldmodel.agents import affinity as af

af.tie(index, a, b, at=when)            # -> AffinityTie, or tc.Unknown with a reason
af.network(index, a, top=6, also=(...)) # -> Network: the neighbourhood, plus what is not inferable
af.shared_contexts(index, a, b)         # -> (Context, ...), the exact per-pair join
af.homophily(index, a, b, at=when)      # -> Homophily: a modulator, never a basis
af.selectivity(members)                 # -> (0, 1]
af.co_membership_of(contexts)           # -> the published half of the strength

af.claim(tie)                           # -> Claim(a, 'affinity_with', b) — never friend_of
af.evidence(tie)                        # -> Evidence(method='inferred:co_membership_v1', …)
af.assert_into(store, tie)              # -> (claim_id, premise_ids); refuses inferred=False

af.link_for(index, a, b)                # -> transmission.Link over the informal channel, or Unknown
af.channels(index, a, b)                # -> formal links + the informal one, highest fidelity first
af.gossip(a, b, index=index)            # -> transmission.tell over the informal channel
```

`AffinityTie` records the two entities, the `contexts` (each with its kind, the published records,
the member count and whether that count is a lower bound), `homophily`, `inferred=True`, `method`,
`duration_days` and the full `terms` of the strength calculation. `tie.explain()` prints it.
Everything takes a plain entity id, a `Grounding` or a `Person` — inferring a tie needs no agent.

## The channel it opens

An informal channel should carry **more candour and less fidelity** than a formal one:

| | `committee` | `cosponsorship` | `lobby_contact` | `affinity` |
| --- | --- | --- | --- | --- |
| fidelity | 0.90 | 0.75 | 0.50 | **0.55** |
| willingness | 0.80 | 0.60 | 0.90 | **0.95** |
| attested | yes | yes | no | **no** |
| relevance filter | the shared committee and its docket | the shared measures | the chamber | **none** |

* **Fidelity 0.55**, below both attested formal channels. A hallway remark has no clerk, no
  transcript and no bill text to check it against. This has a consequence that falls out of the
  existing machinery rather than being added: `lexicon.slip` moves a measure's stage one rung *up*
  the ladder below full fidelity, so a channel at 0.55 drifts faster than one at 0.90. "They get
  garbled more" is the mechanism `docs/agent-communication.md` already measured, run at a lower
  number.
* **Willingness 0.95**, above every attested formal channel, because nothing said here is filed.
* **Candour is not only that number.** It is implemented as the *absence of a relevance filter*:
  `link_for` builds the link with `about=()`, and `speech.sayable` reads an empty `about` as no
  filter at all. Measured on the real index for Klobuchar speaking to Blumenthal:

  ```
  committee      fid 0.90 will 0.80 trust 1.00 attested True  about 418  sayable 37
  cosponsorship  fid 0.75 will 0.60 trust 1.00 attested True  about 139  sayable 59
  affinity       fid 0.55 will 0.95 trust 0.76 attested False about 0    sayable 257
  lobby_contact  fid 0.50 will 0.90 trust 1.00 attested False about 24   sayable 0
  ```

  **Seven times as much is sayable on the informal channel as on the committee channel**, and it is
  a property of the channel's structure, not a constant.
* **Attested `False`**, for the same reason `lobby_contact` is: the edges that opened it name a
  shared context, not a conversation. Nobody published that these two speak.
* **Trust is the tie strength itself**, floored at `TRUST_FLOOR = 0.15` to match
  `transmission.TRUST_WEIGHTS['base']`, so the two channel families are on one scale.
* **Below strength 0.20 no channel opens.** A tie can be real evidence of co-membership and still
  not license the claim that two people talk. The refusal is a `tc.Unknown` carrying the strength
  and the contexts, not a silent zero.

Every number above is in `affinity.ASSUMPTIONS`, a table of 22 entries each with what it assumes,
why, and what observation would argue against it.
`test_every_authored_constant_is_declared_in_the_assumptions_table` asserts that the table and the
live constants agree *and* that the key sets are identical, so an assumption cannot be added to the
code without being declared and justified.

## The seam onto `transmission`, which is not edited

`transmission.tell(speaker, hearer, link=…)` already accepts a caller-supplied `Link`, and reads
only `channel.{name,predicate,fidelity,willingness,attested,note}`, `link.{about,trust,records}`.
That is the whole protocol this module depends on, so integration needed **no change to
`transmission.py`**: `link_for` returns a real `transmission.Channel` inside a real
`transmission.Link`, and `gossip` is a four-line wrapper. `InformalChannel`/`InformalLink` are
field-compatible fallbacks used only if `transmission` is not importable.

Why the channel lives here rather than as a fourth entry in `transmission.CHANNELS`: the formal
channels are each a join on **one** published predicate, and `Channel.predicate` documents which.
This one is an *inference* over eleven, with a strength model and a refusal threshold behind it. It
would not fit the row.

A heard claim arrives with `method='heard:affinity'`, so `explain` and `transmission.trace` show
informal hearsay as informal hearsay and still bottom out in a published record id:

```
measure:SRES371 measure_stage 'passed_chamber'
  ← told:bioguide:K000367 (hearsay 0.30) via heard:affinity from:
    utterance:6cda27fbf841 said_on 'affinity'
    utterance:6cda27fbf841 words 'SRES371 passed the floor'
    utterance:6cda27fbf841 utterance_origin 'govinfo_billstatus:measure:119-sres-371'
```

And a tie written into a store explains as an inference, with the refusal in the chain:

```
bioguide:K000367 affinity_with Ref(id='bioguide:B001277')
  ← inference:co_membership_v1 (inferred_affinity 0.76) via inferred:co_membership_v1 from:
    …shared_contexts 41
    …basis ('committee', 'cosponsorship', 'office')
    …smallest_shared_context 5
    …homophily_multiplier 1.22
    …is_published_relationship False
    …supporting_records ('congress_people:be0c06a3…', 'govinfo_billstatus:cosponsor:119-s-3041:K000367:2026-05-18', …)
```

## Worked networks, on real people

### A legislator: Amy Klobuchar (`bioguide:K000367`)

139 contexts read, 219 counterparts seen, 41 scored, 0.5s.

| # | Counterpart | Strength | co-membership × duration × homophily | Basis |
| --- | --- | --- | --- | --- |
| 1 | Jim Klobuchar | 0.9500 | 0.9500 × 1.000 × 1.000 | kinship (1 context) |
| 2 | John Bessler | 0.9500 | 0.9500 × 1.000 × 1.000 | kinship (1) |
| 3 | Christopher A. Coons | 0.9394 | 0.7620 × 1.010 × 1.220 | committee, cosponsorship, office (47) |
| 4 | Peter Welch | 0.8970 | 0.6829 × 1.010 × 1.300 | committee, cosponsorship, office (55) |
| 5 | Cory A. Booker | 0.8635 | 0.7023 × 1.008 × 1.220 | committee, cosponsorship, office (60) |
| … | Jon Tester | **0.0040** | 0.0033 × 1.000 × 1.200 | office (1) |

The top two are her father and her husband, from two published `family_member_of` edges
(`opensanctions:Q6196159`, `opensanctions:Q6221766`) — a kinship edge names both people, so
selectivity is 1.0 and the tie rests on 0.95 alone. Coons' 0.7620 is led by two bills that only
*two* and *three* senators cosponsored:

```
cosponsorship  S 1797 (119th): Expanding Seniors Acce  members 2   sel 1.0000  w 0.4000 -> 0.4000
    record govinfo_billstatus:cosponsor:119-s-1797:C001088:2025-05-15
cosponsorship  S 5375 (119th): ARCH Act                members 3   sel 0.5946  w 0.2378 -> 0.2022
    record govinfo_billstatus:cosponsor:119-s-5375:C001088:2026-08-07
committee      Crime and Counterterrorism              members 10  sel 0.1925  w 0.1251 -> 0.1251
    record congress_people:8c11f09f85e6b7b7f377c8a826a9aec9be294ff8
committee      Privacy, Technology, and the Law        members 10  sel 0.1925  w 0.1251 -> 0.1063
    record congress_people:36047359c414982babe16a77ad92d0865d1f95ed
… and 41 more shared contexts, together worth 0.2544
homophily {'same_party': True, 'same_delegation': False, 'same_cohort': False, 'shared_donors': 75}
```

A two-cosponsor bill is worth 3.2× a ten-member subcommittee seat, and 120× the shared senator
role. Welch gets the largest homophily multiplier (1.300) because he matches Klobuchar on party
*and* cohort *and* 73 donor committees; note that it still leaves him below Coons, whose
co-membership is stronger. Homophily modulates; it does not decide.

### The tie that is **not** inferable, and why

**Klobuchar and a researcher.** `af.tie(index, 'bioguide:K000367', 'orcid:0000-0002-9649-3588')`:

```
Unknown no_shared_published_context
  no family_member_of/associate_of/director_of/authored/committee_member/employed_by/
  member_of_organization/research_affiliation/last_known_affiliation/cosponsored_measure/
  holds_position edge joins bioguide:K000367 and orcid:0000-0002-9649-3588 in this index;
  homophily alone does not make a tie, and nothing here infers one from a name
```

There is no shared context, and the two signals one would reach for are both refused by the tables
above: education does not exist in this catalog at all, and a name match is what scored precision
0.0226. The honest answer is `Unknown` with the reason.

**Klobuchar and Jon Tester** (`bioguide:T000464`) is the more interesting refusal, because
something *is* published:

```
Amy Klobuchar <-> Jon Tester  strength 0.0040  [1 shared context: office]
  co-membership 0.0033 x duration 1.000 x homophily 1.200
  office  United States senator  members >=500  sel 0.0095  w 0.0033 -> 0.0033
      record osgraph:20e4df3a47d46b478adc0cbe6cd1606675dd82c8d84c32f12dfe07eaff453e85
  homophily {'same_party': True, 'same_delegation': False, 'same_cohort': True, 'shared_donors': 77}

link_for -> Unknown tie_too_weak_for_a_channel
  strength 0.0043 is below the declared channel floor 0.20; the shared context is office of >=500,
  which evidences co-membership but not conversation
```

They entered the Senate the same year and share 77 donor committees. The *only* published shared
context is a role with 2,870 holders. Homophily raises the number by 20% and it is still 50× below
the channel floor, so **no informal channel opens** and the refusal is recorded with the strength
and the context in it. That is rule 2 and rule 4 working together: being in the same very large
room, plus resembling each other, is not evidence that two people talk.

Note the `>=500`: `MEMBER_CAP` stopped the count at 501 rows. Those rows contain duplicates
(`holds_position` is published once per term — 26 rows for Klobuchar's cluster alone), so only 189
distinct people were seen. Treating 189 as the context size would have made it look ten times more
selective than it is, so a bounded context is treated as having **at least** `MEMBER_CAP` members
and its selectivity is reported as an upper bound.

### A researcher: Bryant R. England (`orcid:0000-0002-9649-3588`)

23 contexts read, 223 counterparts seen.

| # | Counterpart | Strength | Shared DOIs |
| --- | --- | --- | --- |
| 1 | Ted R. Mikuls (`orcid:0000-0002-0897-2272`) | 0.5464 | 18 |
| 2 | Grant W. Cannon (`orcid:0000-0001-6640-9173`) | 0.4886 | 15 |
| 3 | Katherine D. Wysham (`orcid:0000-0001-8707-7649`) | 0.4451 | 11 |

```
co_authorship  Long-Term Opioids in Gout: A Matched C  members 8   sel 0.2324  w 0.1627 -> 0.1627
    record crossref:10.1002/acr.25622:author:7
co_authorship  Heterogeneity of Rheumatoid Arthritis-  members 10  sel 0.1925  w 0.1347 -> 0.1145
    record crossref:10.1002/acr.25620:author:9
co_authorship  Mortality in <scp>US</scp> Veterans Wi  members 12  sel 0.1656  w 0.1159 -> 0.0712
    record crossref:10.1002/acr.25691:author:10
… and 12 more shared contexts, together worth 0.1874
homophily {'same_party': None, 'same_delegation': None, 'same_cohort': None, 'shared_donors': 0}
```

Every homophily term is `Unknown` here — a researcher has no published party, delegation or entry
date — so the multiplier is exactly 1.000 and the strength is pure co-membership. The ordering
comes entirely from frequency and author-count: 18 shared papers of 8-19 authors beats 15 beats 11.
A single one of those papers would be worth 0.16; a single 100-author paper would be worth 0.022.
And the count is a **lower bound**, because the same human can appear as both an `orcid:` node and a
`crossref:author:<sha256>` node and the `resolved` table does not join them.

### A director: Arzu Alieva (`opensanctions:Q23683242`)

| # | Counterpart | Strength | Basis |
| --- | --- | --- | --- |
| 1 | Leyla Aliyeva (`opensanctions:Q298532`) | 0.9998 | kinship + 16 boards |
| 2 | Mehriban Alieva (`opensanctions:Q671930`) | 0.9935 | kinship + 5 boards |
| 3 | Heydar Aliev Jr. (`opensanctions:Q106103307`) | 0.9500 | kinship only |

```
kinship  published family_member_of edge     members 2  sel 1.0000  w 0.9500 -> 0.9500  [attested pair]
    record osgraph:583386ce1220ce59d8d0cd239104a54117e2d015bcd14e850e97ede3490d5f8a
board    Exaltation Limited                  members 2  sel 1.0000  w 0.7500 -> 0.7500
    record osgraph:e408bc6b024e1105b50eeb70949594e7e9e7464c58def7d774dd5a1a77eca657
board    Kingsview Developments Limited      members 2  sel 1.0000  w 0.7500 -> 0.6375
    record osgraph:1f1fb3db5885c7cb91a2ad46457f72f1556212cf955da1161aa1c10ab7cba553
… and 11 more shared contexts, together worth 0.7784
```

This is what the top of the scale looks like: a published kinship edge naming both people, plus
sixteen shared boards, seven of which have **exactly two** directors and nine three. Compare the third row —
kinship and nothing else, 0.9500 — to see how much the boards add, and note that a
two-person-board pair reaches the ceiling only because both the base and the selectivity are at
their maximum. No amount of 400-member co-membership gets there.

## What is measured, and how

| Thing | How | Fixture | Real data |
| --- | --- | --- | --- |
| frequency ordering | `test_frequent_co_membership_beats_a_one_off` | strictly increasing 1→9 contexts | 18 > 15 > 11 shared DOIs |
| exclusivity ordering | `test_an_exclusive_context_beats_a_broad_one_at_the_same_frequency` | 2 > 8 > 100 authors | subcommittee (10) > panel (21) > office (2,870) |
| a broad context alone | `test_one_exclusive_context_beats_many_very_broad_ones` | 12 × 2,870 < 1 × 2 | Tester 0.0040, refused a channel |
| every tie cites records | `test_every_context_of_an_emitted_tie_cites_a_published_record` | all 5 pairs | all ties in three networks |
| no tie without support | `test_no_tie_is_emitted_without_a_published_edge_joining_the_pair` | `Unknown` | Klobuchar × researcher: `Unknown` |
| homophily cannot create | `test_homophily_on_its_own_never_makes_a_tie` | party+delegation+cohort → `Unknown` | Tester: 1.20 × 0.0033 |
| candour | `test_the_informal_channel_can_carry_more_than_the_formal_one` | ≥ formal | 257 sayable vs 37 |
| education absent | — | — | `test_the_catalog_publishes_no_education_predicate_at_all` |
| undated signals | — | — | `test_the_co_membership_predicates_are_undated_except_cosponsorship` |
| the seam | `test_gossip_goes_through_transmission_and_is_recorded_as_hearsay` | `heard:affinity` in the trace | same |

## The bound

`network()` reads the entity's own attachments (bounded by `Signal.per_signal`, 40-120 rows per
predicate), each context's members (bounded by `MEMBER_CAP = 500`), groups counterparts by
**canonical** id from the `resolved` table, scores the top `CANDIDATE_CAP = 40` in full — homophily
costs per-pair index reads — and returns the top `top`. `Network.to_json()['bound']` reports
contexts read, counterparts seen, counterparts scored, rows scanned and both caps, so the bound is
auditable rather than implicit. The real legislator network above is 0.5 s; the researcher and
director networks are under 0.1 s.

Two consequences are declared rather than hidden. Every count is a **lower bound** within the scan
(`AffinityTie.bounded`). And counterparts are merged only where `resolved` says they are the same
thing: where it says nothing, nothing is merged, which is why an `openalex:` author and an `orcid:`
author who are plainly the same person stay two counterparts.

## What this does not claim

* Nothing here is fitted to an outcome and nothing here is `validated`. The base weights, the
  selectivity exponent, the repeat discount, the duration horizon, the homophily weights and the
  channel numbers are all **authored**, enumerated in `ASSUMPTIONS`, and each carries a falsifier.
  What is grounded is *which* context is shared, *how many people are in it*, and the published
  record id behind every edge.
* An inferred tie is not a published relationship, and the module will not let one pretend to be:
  the predicate is `affinity_with`, the evidence method starts `inferred:`, and
  `is_published_relationship False` is written into the support chain.
* The strength number is an ordering over evidence, not a probability of friendship. Nobody
  measured how often two members of a ten-person subcommittee actually talk, and this layer does
  not pretend to know.
* No language model is called. Production and understanding are `transmission`'s, unchanged.
