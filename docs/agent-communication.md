# Belief transmission across the published social network

Implementation: `worldmodel/agents/lexicon.py`, `worldmodel/agents/speech.py`,
`worldmodel/agents/transmission.py`. Tests: `tests/test_agents_speech.py`. The design contract for
the layer this sits in is `docs/agents-design.md`.

## Why this exists

The grounded person agent in `docs/agents-design.md` perceives published records and believes what
they support. It has no way to learn anything from anybody else. In the civ sim
([`research/civ_sim`](https://github.com/TensaCo/tensacode/tree/main/tensacode/python/research/civ_sim)) claims pass between minds as English
sentences and are parsed back lossily, so rumours drift and villages end up believing different
things. That is the interesting part, and it is what this ports.

The port changes one thing on purpose: **the network is grounded too.** In the civ sim, who can
talk to whom is proximity in an invented world. Here it is a join over edges the catalog
publishes.

| Channel | Published edge | Rows | What opens it | Attested? |
| --- | --- | --- | --- | --- |
| `committee` | `committee_member` | 3,895 | both are published members of the same committee id | yes |
| `cosponsorship` | `cosponsored_measure` | 1,283,245 | both put their names to the same measures | yes |
| `lobby_contact` | `contacted_government_entity` | 406,766 | one filing reported contacting both chambers | **no** |

The third is marked `attested=False` and carries the lowest fidelity because `lda_lobbying`
publishes the contact at *chamber* granularity: `lda:government_entity:1` is labelled `SENATE` and
`:2` `HOUSE OF REPRESENTATIVES`. The edge says a registrant reported contacting the Senate, not
that it reached this senator, so the channel is *possible* rather than attested and
`Link.attested` says so. The ids are declared as candidates and then **verified against the
published label** before the channel opens, so a renumbered entity closes the channel instead of
silently linking the wrong body.

`committee` is about more than the committee. `referred_to_committee` (175,853 edges) says which
measures are before a panel, so a bill in front of a committee both agents sit on is their
business. The slice is bounded (`PER_COMMITTEE = 400` per committee, `DOCKET_CAP = 8000` in total)
and the same slice is what the hearer can put a name to, so one bound is used consistently.

## Three modules

| Module | What it knows | Needs tensorcode |
| --- | --- | --- |
| `agents/lexicon.py` | registers, templates, spoken tokens, frame rules, the stage slip | no |
| `agents/speech.py` | claims: what an agent can say, how it resolves a name, what it heard | yes |
| `agents/transmission.py` | channels, trust, hearsay provenance, conflict, cascades | yes |

`lexicon` is to `speech` what `affect` is to `person`: pure Python, so the declared tables are
inspectable and testable in an environment with no optional extra installed.
`tensorcode.language` is imported lazily inside `lexicon.parse`, so only *parsing* needs the
substrate, not importing.

## The API

```python
from worldmodel.agents import speech as sp, transmission as tr

tr.tie(index, speaker, hearer)          # -> Tie: shared committees, shared measures, party, records
tr.trust_from(tie)                      # -> [0.15, 1.0]
tr.links(index, speaker, hearer)        # -> (Link, ...), highest fidelity first; may be empty

sp.register_for(person, at=when)        # -> Register, from the holds_role edge covering `when`
sp.intelligibility(a, b)                # -> share of concepts the two registers word alike
sp.sayable(person, about=link.about)    # -> [Sayable]: believed, wordable, relevant, repeatable
sp.say(concept, subj, obj, register)    # -> one English sentence (templates; no model)
sp.hear(sentence, register, names, ...) # -> Heard: concept, resolved Refs, slip, score, note

tr.tell(speaker, hearer, index=index)   # -> Transmission: the whole exchange, any outcome
tr.receive(hearer, claim, speaker=...)  # integrate a heard claim with "X told me" provenance
tr.adjudicate(store, subject, pred)     # -> Adjudication: weigh, retract the loser with a reason
tr.propagate(people, subject=bill)      # -> Cascade: a claim down a chain, plus what each believes
tr.trace(person, claim_id)              # -> the chain of tellers, then the record, or `nobody`
tr.divergence(person, subject, pred)    # -> published vs believed, and how far apart
tr.report(cascade)                      # printable
```

`tr.OUTCOMES` is the closed set of ways one exchange can end: `integrated`, `no_channel`,
`nothing_sayable`, `unwilling`, `not_understood`, `already_believed`, `outweighed`. All of them are
recorded (see *Honest non-transmission* below); none is patched over.

## Trust, from the record

`Tie` counts what is published and cites the record ids for each term: shared committee ids, shared
cosponsored measures (a bounded scan, so the count is an honest *lower bound* and
`Tie.bounded` says so), whether the published party is the same, whether the chamber is. Party and
chamber come from the `holds_role` edge covering the moment, not from the agent's store: seeding
writes one `chamber` claim per role record, a legislator who moved chambers has two, and
`Store.claims` orders by claim id — so reading the register off the store picks a chamber by hash.
Marsha Blackburn came out as a House member that way before this was fixed.

`trust_from` combines them with authored weights, declared in `TRUST_WEIGHTS` so they can be
argued with: base 0.15, shared committees up to 0.30 (saturating at two), shared cosponsorships up
to 0.35 (saturating at twenty-five), same party 0.20. An **unpublished** party contributes nothing
rather than being guessed at, and `Tie.same_party` is a `tc.Unknown` with a reason.

The published value is written to `person.relation(other)['tie_trust']`. `relation['trust']`
belongs to the person agent's own consolidation and is deliberately not clobbered.

## Register, and what it costs

A register is a concept-to-wording map. The *split* is published; the *wordings* are authored, and
the module says so. Two axes:

* **chamber** governs the procedural vocabulary — the Senate says *"S3419 was referred to
  committee"*, the House *"S3419 went to committee"*;
* **party** governs the evaluative vocabulary — *"S3419 became law"* against *"S3419 was signed
  into law"*, *"cosponsored"* against *"signed onto"*, *"sits on"* against *"serves on"*.

`FRAME_RULES` maps **only the canonical verbs**. A hearer un-substitutes the wordings its own
register uses and leaves everything else standing, so a wording built on a different verb reaches
the rules as an unmapped stem and the sentence is not understood. The consequence is legible and
falls out of the design rather than being written in: **procedural news crosses the aisle, and
evaluative news crosses the chambers.** `intelligibility` measures the overlap (0.7 between two
senators of different parties, 0.5 between a senator and a lobbying registrant) and multiplies the
channel's fidelity.

## What is lossy, and how it was measured

Understanding goes through the chart parser in `tensorcode.language`. Production stays local and
templated, for the reason `civ_sim/language.py` records: the general generator has stemming and
tense faults (*"snow cames"*, `owes` stemmed to `ow`). The note survives the port because the
fault does.

1. **Reported speech drops adjuncts.** `"Warner told me that Kaine sits on Judiciary"` parses as
   `tell(content={sit(subject=Kaine)}, location=Judiciary)` — the location attaches to the *outer*
   frame, so the inner clause no longer says which committee. Measured over every concept in all
   four registers, `serves_on` is the only one that does not survive attribution
   (`RELAY_LOSSY_CONCEPTS`); a stage relayed as a passive keeps its adjunct. **Naming your source
   costs you the committee.** Nothing is invented in its place.
2. **A measure has to be one word.** `"HR 1234"` parses as `recipient=HR, object=1234`, which
   loses the measure, so the spoken token is `HR1234` — derived from the id, so learning one costs
   no index read.
3. **Names are resolved by the hearer, not handed over.** `Names` resolves a token only against
   what the hearer can reach on its own: the entities in its store, the recent docket of the
   committees it is published as sitting on, and the people published as sitting on them.
   Anything else lands on `measure:S1241` or `person:Alpha` — a *different* `Ref` from the
   published `congress:bill:119-s-1241` — so a half-understood name is visible divergence and not
   a silent success. All three slices are bounded (`PER_COMMITTEE`, `DOCKET_CAP`,
   `PEERS_PER_COMMITTEE`, `PEER_CAP`), so an older bill or a colleague outside the slice is
   honestly a name the agent does not know.
4. **The stage slips upward.** Below full fidelity, `lexicon.slip` moves a stage one rung up
   `LADDER` (`pending → referred → reported → passed_chamber → enacted`). Rumour exaggerates; it
   does not invent a setback. This is an authored asymmetry with the same standing as the affect
   module's asymmetric transition costs, and it is seeded so a cascade is reproducible.
5. **A measure cannot hold a committee seat.** The lobby register's *"sits in committee"* heard by
   a register whose `sits on` means a seat is refused as incoherent rather than turned into
   nonsense.

Confidence is recursive rather than an exponent bolted on:

```
w_n = min(HEARSAY_CEILING, HEARSAY_BASE · trust_n · fidelity_n · parse_score_n · w_{n-1}),  w_0 = 1
```

A teller cannot make you more certain than it was itself, times what the channel costs.

## Hearsay is a distinct evidence kind

A heard claim enters the hearer's store as

```
Evidence(source=Ref('told:bioguide:B001288'), method='heard:committee',
         locator='<the sentence>', confidence=Score(0.079, 'hearsay'),
         derived_from=(<claims about the utterance>))
```

and the premises are claims *about the utterance*, told first: `said_by`, `said_on` (the channel),
`words`, `utterance_hops`, `utterance_chain`, `utterance_origin` — the published record id the
chain bottoms out in, or the literal `nobody`. So `explain` walks speaker → channel → chain →
record:

```
congress:bill:119-s-3419 measure_stage 'passed_chamber'
  ← told:bioguide:B001288 (hearsay 0.08) from:
    utterance:40905ed19df9 utterance_chain ('bioguide:B001288', 'bioguide:B001243', 'bioguide:H001042')
      ← observed in told:bioguide:B001288 at S3419 passed the floor via heard:committee (hearsay 0.08)
    utterance:40905ed19df9 utterance_hops 3
    utterance:40905ed19df9 said_on 'committee'
    utterance:40905ed19df9 words 'S3419 passed the floor'
    utterance:40905ed19df9 utterance_origin 'govinfo_billstatus:measure:119-s-3419'
```

Hearsay shows as hearsay, the tellers are named in order, and the chain ends in a dataset record id
— or admits there is none.

Because a claim's identity in the substrate is its *content*, two people telling you the same thing
land on **one** `ClaimRecord` with **two** pieces of evidence. `speech.supports_of` therefore reads
the evidence list rather than the record; anything that counts tellers has to.

## Conflict: adjudicated, never overwritten

`measure_stage` and `policy_area` are declared functional on the hearer's store, which is what
makes `Store.conflicts` and `adjudicate` see a contradiction at all. `weigh` groups the live claims
by object and weighs each group:

* **published**: `DIRECT_FLOOR = 0.70`, plus `0.10` for each further record naming the same thing;
* **one teller**: capped at `HEARSAY_CEILING = 0.60`, strictly below the floor, **so a single
  teller can never outrank direct observation however trusted the teller and however good the
  channel**;
* **independent tellers**: `1 − Π(1 − wᵢ)` over groups whose chains of tellers are disjoint, so two
  reports at 0.5 combine to 0.75 and *do* beat the record. Two reports that passed through the same
  mouth are one report and are not allowed to corroborate each other.

The loser is **retracted with the reason recorded**, not forgotten, so
`claims(include_retracted=True)` still shows what the agent used to hold and why it stopped, and a
`(me, adjudicated, (subject, predicate, kept, dropped))` claim is written citing the winner. Two
candidates within `ADJUDICATION_MARGIN` (0.02) are a tie: **both are left standing** and the
outcome is a `tc.Unknown('tie_within_margin', …)`. An agent that cannot tell two reports apart
holds both.

## Honest non-transmission

| Outcome | Recorded as |
| --- | --- |
| `no_channel` | `(me, said_nothing, (hearer, 'no_channel'))` in the speaker's store |
| `nothing_sayable` | `(me, said_nothing, (hearer, 'nothing_sayable'))` |
| `unwilling` | `(me, said_nothing, (hearer, 'unwilling'))`, with the weight and the floor in the locator |
| `not_understood` | `(me, did_not_understand, (speaker, reason))` in the **hearer's** store |

A speaker whose only sayable belief is a rumour weaker than `RELAY_FLOOR` (0.12) does not pass it
on, and a sayable worth less than `SPEAK_FLOOR` (0.08) after willingness is not brought up. Both
are declared, and both are recorded when they bite.

## The worked example, on real people

`congress:bill:119-s-3419`, the *Reuniting Families Act*, published stage **`referred`**
(`govinfo_billstatus:measure:119-s-3419`, `"Read twice and referred to the Committee on the
Judiciary."`). Four members of Senate Judiciary (`congress:committee:ssju00`), alternating party so
the register mismatch bites; only Hirono cosponsored it, so it is news to the other three. Every
hop runs over the `committee` channel because all four are published members of `ssju00`, and the
measure is in that committee's published docket.

```
congress:bill:119-s-3419 measure_stage: published 'referred'
  Hirono   (senate/D) -> Blackburn (senate/R)  committee  integrated
      "S3419 was referred to committee"                      trust 0.80  fidelity 0.63  hop 1
      note: misheard how far it had got
  Blackburn(senate/R) -> Booker    (senate/D)  committee  integrated
      "Hirono told me that S3419 was reported by committee"  trust 0.79  fidelity 0.63  hop 2
      note: misheard how far it had got
  Booker   (senate/D) -> Cruz      (senate/R)  committee  integrated
      "S3419 passed the floor"                               trust 0.69  fidelity 0.63  hop 3

  Hirono    believes referred        (published, 0 hops, conf 1.00)
  Blackburn believes reported        (hearsay,   1 hop,  conf 0.45)   distance +1
  Booker    believes passed_chamber  (hearsay,   2 hops, conf 0.20)   distance +2
  Cruz      believes passed_chamber  (hearsay,   3 hops, conf 0.08)   distance +2
```

Three tellers on, a bill that is sitting in committee is believed to have passed the Senate, at a
confidence 5.7× lower than the record, and Cruz can still name Booker, Blackburn and Hirono in
order and point at `govinfo_billstatus:measure:119-s-3419`. Note the attribution pattern falling
out of the declared policy: Blackburn names her source (hop one, trust ≥ 0.5) and Booker does not
(the name has fallen out of the telling by hop two, and survives only in provenance).

Fidelity is 0.63 on every hop — `0.90` for the committee channel times `0.70` intelligibility
between a Democrat and a Republican register. Same-party pairs would run at 0.90 and drift less.
**Mixing parties does not garble procedural news; it makes the story drift faster.**

## What is measured, and how

| Thing | How | Fixture | Real chain |
| --- | --- | --- | --- |
| confidence over three hops | `Transmission.confidence` | 0.538 → 0.289 → 0.156 (3.5×) | 0.454 → 0.202 → 0.079 (5.7×) |
| distance from the record | `divergence(...)['distance']`, rungs on `LADDER` | 0 or +1 | +1, +2, +2 |
| register overlap | `intelligibility` | 1.0 within party, 0.7 across | 0.7 |
| relay loss | round-trip every concept with and without attribution | `serves_on` only | same |
| bound on the channel | `Tie.scanned`, `Tie.bounded`, `PER_COMMITTEE`, `DOCKET_CAP`, `PEERS_PER_COMMITTEE` | reported | reported |

## Gossip

`sayable` does not require a claim's subject to be the agent itself. Seeding only ever writes
`serves_on` / `cosponsored` / `sponsored` *about the agent*, so a third-party one in a store arrived
by being told — and it can be passed on again, which is what makes third-party gossip work. The
hearer resolves the name from the roster of the committees it sits on, so "Alpha cosponsored S1241"
reaches a second-hop hearer as `(bioguide:S000001, cosponsored, congress:bill:119-s-1241)` with a
two-teller chain, and reaches a stranger as `person:Alpha` — a Ref that is visibly not a published
id.

## What this does not claim

* Nothing here is fitted to an outcome and nothing here is `validated`. The channel fidelities, the
  willingness weights, the trust weights, the stage ladder and the slip direction are all
  **authored and declared**; what is grounded is *which* channel exists, *who* is on it, and the
  record every claim bottoms out in.
* The `lobby_contact` channel is chamber-level and says so. It is not evidence that a registrant
  spoke to a particular member.
* No language model is called anywhere. Production is templates; understanding is a chart parser
  with no statistics in it.
* The concepts a legislator can say are a closed set of ten. A belief outside it is not sayable, and
  `concept_of` returns `None` rather than improvising a wording.

## Suggested hook

`transmission` writes into `person.store`, appends a `person.episodes` entry, updates
`person.relation(other)` and registers new claim ids in `person._born` (the tick bookkeeping dict).
Everything but `_born` is public. A small `Person.hear(transmission)` on `person.py` would remove
the one private access; it is described here rather than added, because `person.py` is owned
elsewhere.

## Not wired into `worldmodel/agents/__init__.py`

The package's `_LAZY` table (which makes `worldmodel.agents.Person` and friends resolve without
importing the substrate) has no entries for these three modules, because `__init__.py` is shared
with work in flight elsewhere in this session and a concurrent write would silently drop one side's
entries. Nothing depends on it — every caller and every test imports the modules directly, which is
also how `person.py` is used in practice. Adding
`'Channel': 'transmission', 'Link': 'transmission', 'Transmission': 'transmission',
'Register': 'lexicon', 'Names': 'speech'` (and any others wanted) is a one-line follow-up for
whoever lands last.

---

## Appendix: the informal channel (added by `worldmodel/agents/affinity.py`)

*This section is appended by the implementation of inferred affinity ties. It does not change
anything above it; `lexicon.py`, `speech.py` and `transmission.py` are untouched. The full account
is `docs/agent-affinity.md`.*

The channel table above is complete for **formal** relationships — a committee seat, a
cosponsorship, a lobbying contact. `worldmodel/agents/affinity.py` adds a fourth channel for the
relationships nobody files:

| Channel | Joined on | Rows | Attested? | Fidelity | Willingness | Relevance filter |
| --- | --- | --- | --- | --- | --- | --- |
| `committee` | `committee_member` | 3,895 | yes | 0.90 | 0.80 | the committee and its docket |
| `cosponsorship` | `cosponsored_measure` | 1,283,245 | yes | 0.75 | 0.60 | the shared measures |
| `lobby_contact` | `contacted_government_entity` | 406,766 | **no** | 0.50 | 0.90 | the chamber |
| `affinity` | **an inference** over eleven co-membership predicates | — | **no** | **0.55** | **0.95** | **none** |

Four things about it are worth recording here, because they bear on the account above.

**It is the first channel that is not a join on one predicate.** `Channel.predicate` documents which
published edge opens each formal channel. The informal one is an inference over `family_member_of`
(50,289), `associate_of` (2,407), `director_of` (85,275), `authored` (82,907), `committee_member`,
`employed_by`, `member_of_organization`, `research_affiliation`, `last_known_affiliation`,
`cosponsored_measure` and `holds_position` (996,250), weighted by how *selective* each shared context
is — a two-person board against the 2,870 published holders of "United States senator". That is why
it lives in its own module with its own strength model rather than as a fourth row in
`transmission.CHANNELS`, and why its `predicate` reads `inferred:affinity_with`.

**`transmission.py` needed no change to accept it.** `tell(speaker, hearer, link=…)` already takes a
caller-supplied `Link` and reads only `channel.{name,predicate,fidelity,willingness,attested,note}`
and `link.{about,trust,records}`. `affinity.link_for` returns exactly that — a real
`transmission.Channel` inside a real `transmission.Link` — and `affinity.gossip` wraps `tell`. The
narrow protocol is documented in `affinity.InformalLink`, which is the field-compatible fallback for
an environment where `transmission` is not importable.

**Candour turned out to be structural, not a number.** *Honest non-transmission* above notes that a
speaker with nothing relevant to say records `nothing_sayable`. Relevance comes from `Link.about`,
and `speech.sayable` treats an **empty** `about` as *no filter*. The informal channel therefore sets
`about=()`, and the effect is large: measured for Klobuchar speaking to Blumenthal on the real index,
the committee channel (`about` = 418 ids) leaves 37 claims sayable and the affinity channel leaves
**257**. Seven times as much can be said in the hallway as in the hearing room, and it falls out of
the existing relevance mechanism rather than a new constant.

**The stage slip does the rest.** *What is lossy* above measures that `lexicon.slip` moves a stage
one rung **up** `LADDER` below full fidelity. Nothing was added for the informal channel: at 0.55
fidelity — against 0.90 for `committee` — the existing slip simply fires more often, so rumour on
this channel exaggerates faster. A real exchange over it:

```
Klobuchar -> Blumenthal  affinity  integrated
    "SRES371 passed the floor"      trust 0.76  fidelity 0.55  conf 0.3023
    heard as heard:affinity, origin govinfo_billstatus:measure:119-sres-371
```

**And it can refuse to open.** `channels()` returns the formal links first (0.90, 0.75) and the
informal one last (0.55), so an agent says what it can on the record before it says anything in the
hallway. Below an inferred strength of 0.20 no informal channel opens at all: Klobuchar and Jon
Tester entered the Senate the same year and share 77 donor committees, but the only published
context they share is a role with 2,870 holders, so `link_for` returns
`Unknown('tie_too_weak_for_a_channel', …)` carrying the strength and the context. Homophily is an
assumption about who talks to whom; it never creates a channel on its own.
