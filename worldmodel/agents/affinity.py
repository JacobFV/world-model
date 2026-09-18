"""Informal social ties, inferred from published co-membership, with an honest epistemic status.

``worldmodel.agents.transmission`` grounds who may speak to whom in **formal** relationships: a
committee seat, a cosponsorship, a lobbying contact. People also talk because they are *friends*,
and friendship is published nowhere. This module infers a social tie from the only thing the
catalog does publish — **being attached to the same thing** — and labels every inference as one.

Three rules govern the whole module.

1. **A tie is never a published claim.** :class:`AffinityTie` carries ``inferred=True``, a
   ``method``, and the published record ids of every edge the inference walked. The predicate it
   writes into a store is :data:`INFERRED_PREDICATE` (``affinity_with``), not ``friend_of``;
   there is no ``friend_of`` anywhere in this file. ``affinity_with`` asserts *evidence of shared
   context*, and its ``Evidence.method`` says ``inferred:``.
2. **No published support, no tie.** :func:`tie` returns a ``tc.Unknown`` with a reason when no
   published edge joins the pair. Homophily (party, delegation, cohort, donors) **modulates** an
   existing tie and can never create one, because homophily is an assumption about who talks to
   whom rather than a record of it.
3. **Nothing is inferred from a name.** ``docs/identity-units-crosswalks.md`` records the measured
   result: inferred name matching scored recall 0.0044 and precision 0.0226 and wrongly merged
   103,211 entities. Identity here comes from the ``resolved`` table or not at all. Where that
   leaves two populations unjoinable, they stay unjoined — see :data:`NOT_AVAILABLE`.

## The published signals, and their measured selectivity

Co-membership is only as good as the context is small. Measured over the default index
(``data/world_evidence/index.sqlite``, 26,409,127 edges), by counting distinct members per
context:

| Signal | Predicate | Rows | Contexts | Median members | p99 | Max |
| --- | --- | --- | --- | --- | --- | --- |
| ``office`` | ``holds_position`` | 996,250 | 208,621 | 2 | 30 | **12,671** |
| ``board`` | ``director_of`` | 85,275 | 56,977 | 1 | 5 | 20 |
| ``co_authorship`` | ``authored`` | 82,907 | 8,462 | 7 | 46 | 100 |
| ``kinship`` | ``family_member_of`` | 50,289 | 41,505 | 1 | 4 | 19 |
| ``affiliation`` | ``last_known_affiliation`` | 42,401 | 7,495 | 1 | — | 1,410 |
| ``committee`` | ``committee_member`` | 3,895 | 228 | 13 | 54 | 67 |
| ``research_institution`` | ``research_affiliation`` | 3,985 | 389 | 3 | 86 | 233 |
| ``association`` | ``associate_of`` | 2,407 | — | pairwise | — | — |
| ``organization`` | ``member_of_organization`` | 581 | 244 | 1 | 20 | 55 |
| ``employer`` | ``employed_by`` | 474 | 200 | 1 | 28 | 39 |
| ``cosponsorship`` | ``cosponsored_measure`` | 1,283,245 | 86,439 | 5 | 159 | 425 |

The spread inside a single predicate is the whole argument for :func:`selectivity`. The largest
``holds_position`` context in this index is ``opensanctions:Q13218630`` — *"United States
representative"*, **12,671** holders. The second is *"Member of the National People's Congress of
China"* at 11,288. Amy Klobuchar's own ``holds_position`` edge points at
``opensanctions:Q4416090`` — *"United States senator"*, **2,870** holders. Sharing that context
with somebody is very nearly no evidence at all, and the strength model has to say so. At the
other end, the sixteen boards Arzu Alieva and Leyla Aliyeva both sit on have **two or three**
directors in total (seven of two, nine of three), which is about as selective as published
evidence gets.

## Strength

``strength = min(1, noisy_or(base · selectivity · 0.85**repeat) · duration · homophily)``

* **selectivity** ``(members - 1) ** -alpha``: 1.0 for a two-person context, 0.155 for a
  13-member subcommittee, 0.0026 for the 2,870-member senator role — a factor of 61 between the
  last two. See :func:`selectivity`.
* **frequency** enters through the noisy-OR: nine co-authored papers combine to much more than
  one, saturating rather than exceeding 1. Repeats of the *same* signal are discounted
  geometrically (:func:`discount`), because twenty-five bills cosponsored with the same colleague
  are mostly one fact. Without that discount, measured on this index, Klobuchar/Blumenthal and
  Alieva/Aliyeva both came out at exactly 1.0000 and the number stopped distinguishing them.
* **duration** is a small bonus over a decade — and is ``Unknown`` on almost everything here,
  because **none** of the co-membership predicates above carries a date in this index except
  ``cosponsored_measure``. Measured: ``authored``, ``research_affiliation``, ``holds_position``,
  ``director_of`` and ``committee_member`` have zero rows with a non-null ``valid_from``; all
  1,283,245 ``cosponsored_measure`` rows have one. An undated tie gets no bonus and no penalty.
* **homophily** is a multiplier of at most :data:`HOMOPHILY_CAP`, and only ever a bonus.

## The channel it opens

An informal channel should plausibly carry **more candour and less fidelity** than a formal one,
and :data:`AFFINITY_CHANNEL` says so in numbers that are declared, not buried:

| | ``committee`` | ``cosponsorship`` | ``affinity`` |
| --- | --- | --- | --- |
| fidelity | 0.90 | 0.75 | **0.55** |
| willingness | 0.80 | 0.60 | **0.95** |
| relevance filter | the shared committee and its docket | the shared measures | **none** |
| attested | yes | yes | **no** |

Candour is not only the willingness number. It is implemented as the **absence of a relevance
filter**: :func:`link_for` builds a ``Link`` with ``about=()``, and ``speech.sayable`` treats an
empty ``about`` as *no filter*, so anything the speaker believes is fair game on this channel
while the committee channel carries only committee business. That is the "things get said that
would not be filed" clause, and it is a property of the channel rather than a constant.

Lower fidelity has a consequence that falls out of the existing machinery rather than being added
here: ``lexicon.slip`` moves a measure's stage one rung *up* the ladder below full fidelity, so a
channel at 0.55 garbles more than one at 0.90. "They get garbled more" is the same mechanism
already measured in ``docs/agent-communication.md``, driven by a lower number.

Every authored constant in this module is enumerated in :data:`ASSUMPTIONS` with what it assumes,
why, and what would argue against it.

## What the catalog does not support

:data:`NOT_AVAILABLE` is the honest half of the table. In particular **there is no education
anywhere in this index**: no predicate matching ``educat``/``alma``/``school``/``student``/
``degree``/``attend``/``alumn`` exists among the 101 published predicates, and no observation
metric either. Shared-alma-mater ties are therefore not inferable for **anybody**, and this
module does not invent one. Nor does it reach for name similarity to bridge the author
namespaces that the ``resolved`` table leaves apart.

Nothing here is fitted to an outcome and nothing here is ``validated``.
"""
import dataclasses
import datetime as _dt
import math
from dataclasses import dataclass, field

from . import INSTALL_HINT, load_tensorcode

#: The substrate is optional here in the way ``lexicon`` is optional to ``speech``: the declared
#: tables and the strength arithmetic below are pure Python and import in any environment, so the
#: authored assumptions can be inspected and tested without the extra. Anything that has to say
#: ``Unknown`` needs the substrate, because ``Unknown`` is a substrate type and this module will
#: not invent a second one.
tc = load_tensorcode(required=False)

UTC = _dt.timezone.utc


def _substrate():
    if tc is None:
        raise ImportError(INSTALL_HINT)
    return tc


def _unknown(reason, detail):
    return _substrate().Unknown(reason, detail)


# --------------------------------------------------------------------------- signals


@dataclass(frozen=True)
class Signal(object):
    """One published predicate that evidences a shared context, and what it is worth.

    ``shape`` is ``'context'`` when the predicate attaches a person to a thing and two people are
    joined by both pointing at it, or ``'pair'`` when the published edge names both people
    directly. A ``'pair'`` edge is *attested* in the same sense ``transmission.Link.attested``
    means it: the record names both parties. It is still not a claim that they are friends.

    ``base`` is authored (see :data:`ASSUMPTIONS`) and answers "how much does being in this kind
    of room together, at perfect selectivity, evidence a tie". ``rows`` and ``measured`` record
    what the default index actually holds, so a shrunken or grown index is visible rather than
    silently changing the weights.
    """

    name: str
    predicate: str
    shape: str                 # 'context' | 'pair'
    base: float
    rows: int                  # measured rows in the default index
    measured: str              # measured member-per-context distribution
    per_signal: int = 60       # how many of this entity's own edges on this predicate to read
    note: str = ''

    @property
    def attested_pair(self):
        return self.shape == 'pair'


#: The co-membership signals this index supports, strongest base first. Every ``rows`` figure and
#: every ``measured`` string was counted against ``data/world_evidence/index.sqlite``; nothing in
#: this table is a guess about the catalog, only about people.
SIGNALS = (
    Signal('kinship', 'family_member_of', 'pair', 0.95, 50_289,
           '41,505 pairs; median 1 relative, max 19; the edge names both people',
           per_signal=40,
           note='published kinship. The strongest tie the catalog attests, and the only reason it '
                'is here at all is that the informal channel is exactly where family talks.'),
    Signal('association', 'associate_of', 'pair', 0.80, 2_407,
           '2,407 person-to-person edges published by OpenSanctions',
           per_signal=40,
           note='the closest the catalog comes to publishing a social tie outright. It is an '
                'attested association, not a declared friendship, so the tie over it is still '
                'inferred; what is published is that a source thought them associates.'),
    Signal('board', 'director_of', 'context', 0.75, 85_275,
           '56,977 companies; median 1 director, p99 5, max 20',
           per_signal=80,
           note='board interlock. Small by construction, so selectivity is usually high; note '
                'that a nominee company can appear as a director and is not filtered out.'),
    Signal('co_authorship', 'authored', 'context', 0.70, 82_907,
           '8,462 DOIs; median 7 authors, p99 46, max 100',
           per_signal=120,
           note='co-authorship of the same DOI. One 100-author paper and nine four-author papers '
                'are very different evidence, which is the whole point of the strength model.'),
    Signal('committee', 'committee_member', 'context', 0.65, 3_895,
           '228 committees; median 13 members, p99 54, max 67',
           per_signal=40,
           note='a formal channel in transmission.py and also a room people are in together. It '
                'appears here so that the informal tie can be stronger between two members of a '
                'ten-person subcommittee than between two members of a 67-person body.'),
    Signal('employer', 'employed_by', 'context', 0.60, 474,
           '200 employers; median 1, p99 28, max 39', per_signal=40,
           note='published employment, OpenSanctions only; tiny in this index.'),
    Signal('organization', 'member_of_organization', 'context', 0.55, 581,
           '244 organizations; median 1, p99 20, max 55', per_signal=40,
           note='published organizational membership, OpenSanctions only; tiny in this index.'),
    Signal('research_institution', 'research_affiliation', 'context', 0.45, 3_985,
           '389 ROR institutions; median 3, p90 25, max 233', per_signal=40,
           note='same research institution. Note the published granularity is sometimes a '
                'department ("Neuroscience Graduate Program, Oregon Health & Science University"), '
                'which is far more selective than a whole university.'),
    Signal('affiliation', 'last_known_affiliation', 'context', 0.40, 42_401,
           '7,495 institutions; median 1, p90 9, max 1,410 (a national laboratory)',
           per_signal=40,
           note='OpenAlex last-known affiliation. Bigger than research_affiliation but it '
                'describes a *different, unjoinable* population of authors: see NOT_AVAILABLE.'),
    Signal('cosponsorship', 'cosponsored_measure', 'context', 0.40, 1_283_245,
           '86,439 measures; median 5 cosponsors, p90 36, p99 159, max 425',
           per_signal=120,
           note='the only dated signal in this table, so it is the only one that can support a '
                'duration term at all.'),
    Signal('office', 'holds_position', 'context', 0.35, 996_250,
           '208,621 positions; median 2 holders, p99 30, max 12,671 ("United States '
           'representative"). 90% of positions have five holders or fewer.',
           per_signal=40,
           note='same office. Bimodal: "mayor of Vădastra" has two holders and is strong '
                'evidence; "United States senator" has 2,870 and is almost none. The base weight '
                'cannot tell them apart and is not asked to — selectivity does.'),
)
BY_NAME = {signal.name: signal for signal in SIGNALS}
BY_PREDICATE = {signal.predicate: signal for signal in SIGNALS}
CONTEXT_SIGNALS = tuple(s for s in SIGNALS if s.shape == 'context')
PAIR_SIGNALS = tuple(s for s in SIGNALS if s.shape == 'pair')


#: What a reader will look for and not find, with the reason. Each entry was checked against the
#: default index; none of them is a placeholder for something this module quietly approximates.
NOT_AVAILABLE = {
    'education': (
        'No education anywhere. Of the 101 predicates published in this index, none matches '
        'educat / alma / school / student / degree / attend / alumn, and no observation metric '
        'does either. A shared-alma-mater tie is therefore not inferable for any entity in this '
        'catalog, and no substitute is used. The obvious substitute — matching people by name to '
        'an external roster — is refused on measured grounds: docs/identity-units-crosswalks.md '
        'records inferred name matching at recall 0.0044, precision 0.0226, 103,211 entities '
        'wrongly merged.'),
    'friendship': (
        'Nothing publishes friend_of, knows, or any declared social relationship between private '
        'individuals. This module exists because of that absence, not in spite of it.'),
    'institutional_lineage': (
        'institution_lineage_ancestor exists (2,421 rows) but relates openalex:I* to openalex:I*, '
        'while research_affiliation targets ror:*. Measured: the resolved cluster of '
        'openalex:I142606810 is empty, so nothing bridges the two namespaces. Institutional '
        'lineage therefore cannot reach a person in this index and is not used as a signal.'),
    'author_identity_across_namespaces': (
        'authored and research_affiliation name authors as orcid:* or crossref:author:<sha256>; '
        'last_known_affiliation names them as openalex:A*. Measured: the resolved clusters of '
        'orcid:0000-0003-2029-7692 and openalex:A5134754013 are both empty. The same human can '
        'therefore appear as several unjoined nodes, so every co-authorship count here is a '
        'lower bound, and the two research signals describe disjoint populations. Joining them '
        'would take name inference, which is refused.'),
    'residence': (
        'within / located_in describe places and organisations, not where a person lives, so '
        'residential proximity — the classic informal-tie signal — is not inferable.'),
    'contribution_amounts': (
        'fec publishes supports_candidate as a per-cycle boolean and all three '
        'fec_individual_contributions* datasets contributed zero records to this index (see '
        'docs/agents-design.md). Shared donors can therefore be counted but not weighted by '
        'money.'),
    'roll_call_agreement': (
        'individual roll-call positions are kind=\'event\' records with no subject or entity id, '
        'reachable only by primary key (grounding.RollCalls). Voting together is a plausible '
        'affinity signal and is deliberately left out rather than approximated from the '
        'DW-NOMINATE scaling, which is not the same thing.'),
}


# --------------------------------------------------------------------------- selectivity, strength


#: How fast the evidence in a shared context decays as the context gets bigger. ``alpha = 1`` is
#: pure inverse partner count; ``alpha = 0`` ignores size entirely.
SELECTIVITY_ALPHA = 0.75
#: Above this many members a context is *labelled* broad in reports. It is a label, not a cutoff:
#: nothing is dropped for being broad, it simply earns almost nothing.
BROAD_CONTEXT = 200
#: A per-context weight is clamped below 1 so the noisy-OR can never be handed a certainty.
MAX_CONTEXT_WEIGHT = 0.95


def selectivity(members, *, bounded=False, alpha=SELECTIVITY_ALPHA):
    """How much being in a context of ``members`` people together is worth, in ``(0, 1]``.

    ``(members - 1) ** -alpha``: in a context of *n* people you have *n - 1* possible
    counterparts, so the prior that any particular one of them is the person you actually talk to
    falls with the count. ``alpha`` is sublinear because attention in a large body is not uniform
    — you know the people on your subcommittee, not a random draw from the chamber.

    ==========  ============
    members     selectivity
    ==========  ============
    2           1.000000
    3           0.594604
    13          0.155101
    28          0.084426
    67          0.043186
    100         0.031862
    2,870       0.002551
    12,671      0.000837
    ==========  ============

    When the member count hit a scan bound, the true count is *at least* ``members``, so the value
    returned is an **upper** bound on the true selectivity and the caller records ``bounded``.
    """
    partners = max(1, int(members) - 1)
    return round(float(partners) ** -float(alpha), 6)


#: What the *k*-th repeat of the same kind of shared context is worth, relative to the first.
#: Repeated co-membership in one kind of context is correlated evidence, not independent evidence.
REPEAT_DISCOUNT = 0.85


def combine(weights):
    """Noisy-OR over lines of evidence: ``1 - Π(1 - w)``.

    Strictly increasing in the number of contexts, so more shared contexts always beat fewer, and
    saturating, so sixteen two-person boards do not overflow past 1.
    """
    product = 1.0
    for weight in weights:
        product *= (1.0 - min(MAX_CONTEXT_WEIGHT, max(0.0, float(weight))))
    return 1.0 - product


def discount(weights, *, factor=REPEAT_DISCOUNT):
    """The same weights with the *k*-th largest multiplied by ``factor ** k``.

    Applied **within** a signal, never across signals. Twenty-five bills cosponsored with the same
    colleague are mostly one fact — they are in the same coalition — whereas a board seat *and* a
    published kinship edge are two facts. Without this the noisy-OR saturates at 1.0 for any pair
    with a dozen shared contexts and stops telling two strong ties apart: measured on this index,
    Klobuchar/Blumenthal and Alieva/Aliyeva both came out at exactly 1.0000 before it was added,
    and come out at 0.62 and 1.00 after.
    """
    return tuple(weight * factor ** rank
                 for rank, weight in enumerate(sorted(weights, reverse=True)))


def discounted_weights(contexts, *, factor=REPEAT_DISCOUNT):
    """``(context, discounted weight)`` for a set of shared contexts, strongest first."""
    by_signal = {}
    for context in contexts:
        by_signal.setdefault(context.signal, []).append(context)
    out = []
    for group in by_signal.values():
        group = sorted(group, key=lambda c: (-c.weight, c.context_id))
        for rank, context in enumerate(group):
            out.append((context, round(context.weight * factor ** rank, 6)))
    return tuple(sorted(out, key=lambda pair: (-pair[1], pair[0].signal, pair[0].context_id)))


def co_membership_of(contexts, *, factor=REPEAT_DISCOUNT):
    """The published half of the strength: noisy-OR over the discounted per-context weights."""
    return round(combine([w for _c, w in discounted_weights(contexts, factor=factor)]), 6)


#: A decade of shared context earns the full duration bonus.
DURATION_FULL_DAYS = 3652.0
#: And the full bonus is this much. Small, because duration is measurable for exactly one signal.
DURATION_BONUS = 0.25


def duration_multiplier(span_days):
    """``1 + DURATION_BONUS · min(1, span / decade)``, and exactly 1.0 when the span is Unknown.

    Absence of a date is not evidence of a short relationship, so an undated tie is neither
    rewarded nor punished. On this index that is nearly every tie: only ``cosponsored_measure``
    carries ``valid_from``.
    """
    if span_days is None or isinstance(span_days, float) and math.isnan(span_days):
        return 1.0
    try:
        span = float(span_days)
    except (TypeError, ValueError):
        return 1.0
    return 1.0 + DURATION_BONUS * min(1.0, max(0.0, span) / DURATION_FULL_DAYS)


#: Homophily weights. Each is a **bonus only**: an opposite-party pair earns nothing rather than
#: being penalised, because penalising would assert that opposition suppresses informal contact,
#: which is a stronger claim than similarity encouraging it and equally unsupported here.
HOMOPHILY_WEIGHTS = {'same_party': 0.10, 'same_delegation': 0.10, 'same_cohort': 0.08,
                     'shared_donors': 0.12}
#: Shared donor committees saturating the donor term.
DONOR_FULL = 50.0
#: The most homophily can do, whatever else is true.
HOMOPHILY_CAP = 1.40


@dataclass(frozen=True)
class Homophily(object):
    """Who resembles whom, from published records. A modulator, never a basis.

    Each field is ``True``, ``False`` or a ``tc.Unknown`` with a reason; an ``Unknown`` contributes
    nothing rather than being guessed at, exactly as ``transmission.Tie.same_party`` does.
    """

    same_party: object = None
    same_delegation: object = None
    same_cohort: object = None
    shared_donors: int = 0
    donors_bounded: bool = True
    records: tuple = ()
    detail: dict = field(default_factory=dict)

    @property
    def multiplier(self):
        bonus = 0.0
        for key in ('same_party', 'same_delegation', 'same_cohort'):
            if getattr(self, key) is True:
                bonus += HOMOPHILY_WEIGHTS[key]
        if self.shared_donors:
            bonus += HOMOPHILY_WEIGHTS['shared_donors'] * min(1.0, self.shared_donors / DONOR_FULL)
        return round(min(HOMOPHILY_CAP, 1.0 + bonus), 6)

    @property
    def terms(self):
        out = {}
        for key in ('same_party', 'same_delegation', 'same_cohort'):
            value = getattr(self, key)
            out[key] = None if _is_unknown(value) else value
        out['shared_donors'] = self.shared_donors
        out['donor_count_is_a_lower_bound'] = self.donors_bounded
        return out

    def to_json(self):
        return {'multiplier': self.multiplier, 'terms': self.terms,
                'records_cited': len(self.records), 'detail': dict(self.detail),
                'assumption': 'homophily modulates a tie that published co-membership already '
                              'supports; on its own it produces no tie at all'}


def _is_unknown(value):
    return tc is not None and isinstance(value, tc.Unknown)


# --------------------------------------------------------------------------- the tie


@dataclass(frozen=True)
class Context(object):
    """One published shared context, and the two edges that put both entities in it."""

    signal: str
    predicate: str
    kind: str                  # the signal name, repeated for readability in reports
    context_id: str            # '' for a pair signal, where the edge names both people
    label: object = None
    members: int = 2            # the count selectivity is computed on
    members_seen: int = 2       # the distinct members actually read, which may be fewer
    members_bounded: bool = False
    a_id: str = ''             # the cluster member the published edge on a's side actually names
    b_id: str = ''
    records: tuple = ()        # published record ids for both edges
    attested_pair: bool = False
    first: object = None       # earliest valid_from on either edge, when the predicate is dated
    last: object = None

    @property
    def selectivity(self):
        return selectivity(self.members, bounded=self.members_bounded)

    @property
    def weight(self):
        return round(min(MAX_CONTEXT_WEIGHT, BY_NAME[self.signal].base * self.selectivity), 6)

    @property
    def broad(self):
        return self.members > BROAD_CONTEXT

    def to_json(self):
        return {'signal': self.signal, 'predicate': self.predicate, 'context': self.context_id or None,
                'label': self.label, 'members': self.members, 'members_read': self.members_seen,
                'member_count_is_a_lower_bound': self.members_bounded, 'broad': self.broad,
                'selectivity': self.selectivity, 'weight': self.weight,
                'attested_pair': self.attested_pair, 'joined_via': [self.a_id, self.b_id],
                'records': list(self.records),
                'first': _iso(self.first), 'last': _iso(self.last)}


#: The predicate an inferred tie writes. Deliberately not ``friend_of``: it asserts evidence of
#: shared context between two entities, and its evidence says how it was inferred.
INFERRED_PREDICATE = 'affinity_with'
#: The inference method, versioned so a store can be read back and audited.
METHOD = 'co_membership_v1'


@dataclass(frozen=True)
class AffinityTie(object):
    """An **inferred** informal tie, with the published records the inference stands on.

    This is not a published relationship and never becomes one. ``inferred`` is ``True`` by
    construction; :func:`assert_into` refuses to write a tie whose ``inferred`` flag is false.
    """

    a: str
    b: str
    contexts: tuple = ()
    homophily: Homophily = None
    inferred: bool = True
    method: str = METHOD
    duration_days: object = None      # int, or tc.Unknown when no shared context is dated
    scanned: int = 0
    bounded: bool = True              # counts are lower bounds within the scan
    label_a: object = None
    label_b: object = None

    # -- the strength model, as terms rather than one number ---------------------------------
    @property
    def basis(self):
        """The kinds of shared context this tie rests on, strongest base first."""
        order = [signal.name for signal in SIGNALS]
        return tuple(sorted({c.signal for c in self.contexts}, key=order.index))

    @property
    def predicates(self):
        return tuple(sorted({c.predicate for c in self.contexts}))

    @property
    def frequency(self):
        """How many distinct published contexts the two share."""
        return len(self.contexts)

    @property
    def exclusivity(self):
        """The most selective shared context: the smallest room they are in together."""
        return min((c.members for c in self.contexts), default=0)

    @property
    def weights(self):
        """``(context, discounted weight)`` for every shared context, strongest first.

        The discount is applied inside each signal by :func:`discount`, so the second shared
        committee counts less than the first but a kinship edge is not discounted by a board seat.
        """
        return discounted_weights(self.contexts)

    @property
    def co_membership(self):
        return co_membership_of(self.contexts)

    @property
    def strength(self):
        multiplier = self.homophily.multiplier if self.homophily is not None else 1.0
        value = self.co_membership * duration_multiplier(_days(self.duration_days)) * multiplier
        return round(min(1.0, value), 6)

    @property
    def terms(self):
        """Every term of the strength calculation, so the number can be argued with."""
        return {'co_membership_noisy_or': self.co_membership,
                'repeat_discount': REPEAT_DISCOUNT,
                'contexts': [dict(c.to_json(), discounted_weight=w) for c, w in self.weights],
                'frequency': self.frequency, 'exclusivity_smallest_context': self.exclusivity,
                'duration_days': None if _is_unknown(self.duration_days) else self.duration_days,
                'duration_multiplier': duration_multiplier(_days(self.duration_days)),
                'homophily': self.homophily.to_json() if self.homophily is not None else None,
                'strength': self.strength}

    @property
    def records(self):
        out = []
        for context in self.contexts:
            out += list(context.records)
        if self.homophily is not None:
            out += list(self.homophily.records)
        return tuple(dict.fromkeys(record for record in out if record))

    @property
    def attested_contexts(self):
        """Shared contexts whose published edge names **both** people, not just a shared thing."""
        return tuple(c for c in self.contexts if c.attested_pair)

    def to_json(self):
        return {'a': self.a, 'b': self.b, 'label_a': self.label_a, 'label_b': self.label_b,
                'inferred': self.inferred, 'method': self.method,
                'predicate_if_written': INFERRED_PREDICATE,
                'is_a_published_relationship': False,
                'strength': self.strength, 'basis': list(self.basis),
                'predicates': list(self.predicates), 'terms': self.terms,
                'records': list(self.records), 'records_cited': len(self.records),
                'attested_pair_contexts': len(self.attested_contexts),
                'index_rows_scanned': self.scanned, 'counts_are_lower_bounds': self.bounded}

    def explain(self, *, limit=8, records=2):
        """One printable block: the strength, then the strongest contexts and their records."""
        lines = ['%s <-> %s  strength %.4f  (inferred, %s)'
                 % (self.label_a or self.a, self.label_b or self.b, self.strength, self.method)]
        lines.append('  co-membership %.4f x duration %.3f x homophily %.3f  [%d shared context%s: %s]'
                     % (self.co_membership, duration_multiplier(_days(self.duration_days)),
                        self.homophily.multiplier if self.homophily is not None else 1.0,
                        self.frequency, '' if self.frequency == 1 else 's', ', '.join(self.basis)))
        shown = self.weights[:limit] if limit else self.weights
        for context, weight in shown:
            lines.append('  %-20s %-38s members %-6s sel %.4f  w %.4f -> %.4f%s'
                         % (context.signal, _one_line(context.label or context.context_id)[:38],
                            ('>=%d' % context.members) if context.members_bounded else context.members,
                            context.selectivity, context.weight, weight,
                            '  [attested pair]' if context.attested_pair else ''))
            for record in context.records[:records]:
                lines.append('      record %s' % record)
        if len(self.weights) > len(shown):
            lines.append('  ... and %d more shared contexts, together worth %.4f'
                         % (len(self.weights) - len(shown),
                            combine([w for _c, w in self.weights[len(shown):]])))
        if self.homophily is not None:
            lines.append('  homophily %s' % self.homophily.terms)
        return '\n'.join(lines)


def _one_line(text):
    """A published label on one line. Crossref titles carry newlines and inline ``<scp>`` markup."""
    return ' '.join(str(text or 'pairwise').split())


def _iso(value):
    if value is None:
        return None
    return value.isoformat() if isinstance(value, _dt.datetime) else str(value)


def _days(value):
    return None if (value is None or _is_unknown(value)) else value


# --------------------------------------------------------------------------- reading the index


#: How many members of one context to read before giving up and calling it broad. A context that
#: hits this is already far past the point where it evidences anything.
MEMBER_CAP = 500
#: How many distinct counterparts one entity's whole neighbourhood may produce.
PARTNER_CAP = 4000
#: How many counterparts :func:`network` scores in full (homophily needs per-pair index reads).
CANDIDATE_CAP = 40
#: How many donor committees to read per side when counting shared donors.
DONOR_SCAN = 400


@dataclass(frozen=True)
class Attachment(object):
    """One published edge attaching one entity to one shared context."""

    signal: str
    context_id: str
    entity_id: str             # the cluster member the edge names
    record_rowid: object = None
    valid_from: object = None
    valid_to: object = None


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _record_id(index, rowid):
    if not rowid:
        return None
    record = index.record_at(rowid)
    return record['_provenance']['record_id'] if record is not None else None


def _label(index, entity_id):
    record = index.entity(entity_id)
    return str(record['label']) if record is not None and record.get('label') else None


def _with_records(index, context, rowid):
    """The same context with its published record id resolved. Deferred until a tie is emitted."""
    record = _record_id(index, rowid)
    return dataclasses.replace(context, records=(record,) if record else ())


def _span(contexts):
    """``duration_days``: the span of the dated shared contexts, or ``Unknown`` when none is dated.

    On this index that is nearly always Unknown, because ``cosponsored_measure`` is the only
    predicate in :data:`SIGNALS` with a non-null ``valid_from``.
    """
    dated = [c for c in contexts if c.first is not None]
    if not dated:
        return _unknown('undated_signals',
                        'none of the shared predicates (%s) carries valid_from in this index, so '
                        'how long they have shared the context is not published'
                        % ', '.join(sorted({c.predicate for c in contexts})))
    span = max(c.last or c.first for c in dated) - min(c.first for c in dated)
    return int(span.total_seconds() // 86400)


def entity_id_of(thing):
    """Accept a plain id, a ``Grounding`` or a ``Person``. Nothing here needs an agent."""
    grounding = getattr(thing, 'grounding', None)
    if grounding is not None:
        return str(grounding.entity_id)
    return str(getattr(thing, 'entity_id', thing))


def _cluster(index, entity_id):
    try:
        return index.cluster(entity_id)
    except Exception:                                   # pragma: no cover - defensive
        return (entity_id,)


def attachments(index, entity_id, *, signals=CONTEXT_SIGNALS, cluster=None):
    """Every published context this entity is attached to, across its resolved identity cluster.

    Bounded by ``Signal.per_signal`` rows per predicate, so the cost is the entity's own degree.
    Duplicate rows (Klobuchar's ``holds_position`` edge is published 26 times, once per term) are
    kept as separate attachments and deduplicated by context id downstream.
    """
    ids = tuple(cluster) if cluster is not None else _cluster(index, entity_id)
    out = []
    for signal in signals:
        if signal.shape != 'context':
            continue
        for row in index.edges(ids, signal.predicate, limit=signal.per_signal):
            out.append(Attachment(signal.name, str(row['object']), str(row['subject']),
                                  row.get('record_rowid'), _parse_time(row.get('valid_from')),
                                  _parse_time(row.get('valid_to'))))
    return tuple(out)


def members_of(index, signal, context_id, *, cap=MEMBER_CAP):
    """``(members, bounded, size)`` for one context.

    ``members`` is a list of ``(entity_id, record_rowid)``, distinct on the published id. ``size``
    is the member count used for :func:`selectivity`.

    Two subtleties, both resolved conservatively:

    * distinctness is over the **published** ids, so two ids in the same resolved cluster count
      twice. That over-counts members and therefore understates selectivity, which is the safe
      direction;
    * when the row budget runs out, the number of distinct members *seen* is not the size of the
      context — duplicate rows eat the budget, and ``holds_position`` publishes one row per term
      (26 of them for Klobuchar's cluster). Reading 501 rows of ``opensanctions:Q4416090``
      ("United States senator", 2,870 holders) yields only 189 distinct people, which would make
      the context look ten times more selective than it is. A bounded context is therefore treated
      as having **at least ``cap``** members and its selectivity is an upper bound.
    """
    signal = BY_NAME[signal] if isinstance(signal, str) else signal
    rows = index.edges([context_id], signal.predicate, direction='in', limit=cap + 1)
    seen, members = set(), []
    for row in rows:
        subject = str(row['subject'])
        if subject in seen:
            continue
        seen.add(subject)
        members.append((subject, row.get('record_rowid')))
    bounded = len(rows) > cap
    return members[:cap], bounded, (max(len(members), cap) if bounded else max(2, len(members)))


def pair_edges(index, a_ids, b_ids, *, signals=PAIR_SIGNALS):
    """Published edges that name both entities directly, in either direction."""
    b_set = {str(x) for x in b_ids}
    out = []
    for signal in signals:
        for a_id in a_ids:
            for direction, other in (('out', 'object'), ('in', 'subject')):
                for row in index.edges([a_id], signal.predicate, direction=direction,
                                       limit=signal.per_signal):
                    if str(row[other]) in b_set:
                        out.append((signal, str(a_id), str(row[other]), row.get('record_rowid'),
                                    _parse_time(row.get('valid_from'))))
    return out


def shared_contexts(index, a, b, *, signals=SIGNALS, cap=MEMBER_CAP):
    """Every published context ``a`` and ``b`` are both in. The per-pair path; exact, not sampled.

    Returns ``(contexts, rows_scanned)``. A pair signal produces a context of exactly two members
    (so selectivity 1.0) marked ``attested_pair``; a context signal produces one whose member
    count is read from the index and may be a lower bound.
    """
    a_id, b_id = entity_id_of(a), entity_id_of(b)
    a_ids, b_ids = _cluster(index, a_id), _cluster(index, b_id)
    context_signals = tuple(s for s in signals if s.shape == 'context')
    pair_signals = tuple(s for s in signals if s.shape == 'pair')
    a_att = attachments(index, a_id, signals=context_signals, cluster=a_ids)
    b_att = attachments(index, b_id, signals=context_signals, cluster=b_ids)
    scanned = len(a_att) + len(b_att)

    b_by_context = {}
    for attachment in b_att:
        b_by_context.setdefault((attachment.signal, attachment.context_id), attachment)
    out = []
    done = set()
    for attachment in a_att:
        key = (attachment.signal, attachment.context_id)
        if key in done or key not in b_by_context:
            continue
        done.add(key)
        other = b_by_context[key]
        members, bounded, size = members_of(index, attachment.signal, attachment.context_id, cap=cap)
        scanned += len(members)
        dates = [d for d in (attachment.valid_from, other.valid_from) if d is not None]
        records = tuple(x for x in (_record_id(index, attachment.record_rowid),
                                    _record_id(index, other.record_rowid)) if x)
        out.append(Context(signal=attachment.signal, predicate=BY_NAME[attachment.signal].predicate,
                           kind=attachment.signal, context_id=attachment.context_id,
                           label=_label(index, attachment.context_id),
                           members=size, members_seen=len(members), members_bounded=bounded,
                           a_id=attachment.entity_id, b_id=other.entity_id, records=records,
                           first=min(dates) if dates else None, last=max(dates) if dates else None))

    for signal, from_id, to_id, rowid, when in pair_edges(index, a_ids, b_ids, signals=pair_signals):
        key = (signal.name, '')
        if key in done:
            continue
        done.add(key)
        scanned += 1
        record = _record_id(index, rowid)
        out.append(Context(signal=signal.name, predicate=signal.predicate, kind=signal.name,
                           context_id='', label='published %s edge' % signal.predicate,
                           members=2, members_bounded=False, a_id=from_id, b_id=to_id,
                           records=(record,) if record else (), attested_pair=True,
                           first=when, last=when))
    order = [signal.name for signal in SIGNALS]
    return tuple(sorted(out, key=lambda c: (order.index(c.signal), c.context_id))), scanned


# --------------------------------------------------------------------------- homophily, from record


def seat(index, entity_id, *, at=None, cluster=None):
    """``(chamber, party, delegation, entered, record_id)`` from the ``holds_role`` edge at ``at``.

    This mirrors ``speech.published_role`` — and for the same reason it gives: seeding writes one
    ``chamber`` claim per role record, so a store answers by hash and the edge has to be read
    instead. It differs in taking a plain entity id rather than a ``Grounding``, and in also
    returning the published ``jurisdiction_code`` (the state delegation) and the earliest term
    start (the cohort), neither of which ``published_role`` reports.
    """
    ids = tuple(cluster) if cluster is not None else _cluster(index, entity_id)
    rows = index.edges(ids, 'holds_role', limit=40)
    best, best_from, earliest = None, None, None
    for row in rows:
        valid_from, valid_to = _parse_time(row.get('valid_from')), _parse_time(row.get('valid_to'))
        if valid_from is not None and (earliest is None or valid_from < earliest):
            earliest = valid_from
        if at is not None and ((valid_from is not None and valid_from > at)
                               or (valid_to is not None and valid_to <= at)):
            continue
        if best is None or (valid_from is not None and (best_from is None or valid_from > best_from)):
            best, best_from = row, valid_from
    if best is None:
        return None, None, None, earliest, None
    record = index.entity(best['object'])
    if record is None:
        return None, None, None, earliest, None
    attributes = record.get('attributes') or {}
    role_type = attributes.get('role_type')
    chamber = 'senate' if role_type == 'sen' else 'house' if role_type == 'rep' else None
    party = (attributes.get('source_term') or {}).get('party')
    # 'MN' for a senator, 'MN-3' for a representative: the delegation is the state either way.
    delegation = str(attributes.get('jurisdiction_code') or '').split('-')[0] or None
    return chamber, (str(party) if party else None), delegation, earliest, record['_provenance']['record_id']


def _donor_committees(index, entity_id, *, cluster=None, scan=DONOR_SCAN):
    ids = tuple(cluster) if cluster is not None else _cluster(index, entity_id)
    rows = index.edges(ids, 'supports_candidate', direction='in', limit=scan)
    return {str(row['subject']) for row in rows}, len(rows) >= scan


def homophily(index, a, b, *, at=None, donors=True, scan=DONOR_SCAN, a_donors=None):
    """Who resembles whom, from published records only. Returns a :class:`Homophily`.

    Shared party, shared state delegation, shared cohort (the same year entering office) and
    shared donor committees. Every one of them is a published fact; that people who resemble each
    other talk to each other is the **assumption**, and it is why this only ever multiplies a tie
    that co-membership already supports.
    """
    _substrate()
    a_id, b_id = entity_id_of(a), entity_id_of(b)
    a_ids, b_ids = _cluster(index, a_id), _cluster(index, b_id)
    a_chamber, a_party, a_delegation, a_entered, a_record = seat(index, a_id, at=at, cluster=a_ids)
    b_chamber, b_party, b_delegation, b_entered, b_record = seat(index, b_id, at=at, cluster=b_ids)
    records = tuple(x for x in (a_record, b_record) if x)

    if a_party is None or b_party is None:
        same_party = _unknown('no_published_party', 'the record gives no party for %s'
                              % (a_id if a_party is None else b_id))
    else:
        same_party = a_party == b_party
    if a_delegation is None or b_delegation is None:
        same_delegation = _unknown('no_published_delegation',
                                   'no published jurisdiction_code for %s'
                                   % (a_id if a_delegation is None else b_id))
    else:
        same_delegation = a_delegation == b_delegation
    if a_entered is None or b_entered is None:
        same_cohort = _unknown('no_published_entry_date',
                               'no dated holds_role edge for %s'
                               % (a_id if a_entered is None else b_id))
    else:
        same_cohort = a_entered.year == b_entered.year

    shared, bounded = 0, False
    if donors:
        mine, mine_bounded = (a_donors if a_donors is not None
                              else _donor_committees(index, a_id, cluster=a_ids, scan=scan))
        theirs, theirs_bounded = _donor_committees(index, b_id, cluster=b_ids, scan=scan)
        shared = len(mine & theirs)
        bounded = mine_bounded or theirs_bounded
    detail = {'party': [a_party, b_party], 'delegation': [a_delegation, b_delegation],
              'entered': [a_entered.year if a_entered else None, b_entered.year if b_entered else None],
              'chamber': [a_chamber, b_chamber]}
    return Homophily(same_party=same_party, same_delegation=same_delegation, same_cohort=same_cohort,
                     shared_donors=shared, donors_bounded=bounded, records=records, detail=detail)


# --------------------------------------------------------------------------- the public entry point


def tie(index, a, b, *, signals=SIGNALS, at=None, donors=True, cap=MEMBER_CAP, scan=DONOR_SCAN):
    """The inferred tie between two entities, or ``tc.Unknown`` when none is inferable.

    A tie is emitted **only** when at least one published edge joins the pair. When none does,
    the answer is ``Unknown('no_shared_published_context', …)`` — not a weak tie, not a guess from
    homophily, and certainly not a name match.
    """
    _substrate()
    a_id, b_id = entity_id_of(a), entity_id_of(b)
    if a_id == b_id:
        return _unknown('same_entity', 'an entity has no affinity tie to itself: %s' % a_id)
    contexts, scanned = shared_contexts(index, a_id, b_id, signals=signals, cap=cap)
    if not contexts:
        return _unknown('no_shared_published_context',
                        'no %s edge joins %s and %s in this index; homophily alone does not make '
                        'a tie, and nothing here infers one from a name'
                        % ('/'.join(s.predicate for s in signals), a_id, b_id))
    resemblance = homophily(index, a_id, b_id, at=at, donors=donors, scan=scan)
    return AffinityTie(a=a_id, b=b_id, contexts=contexts, homophily=resemblance,
                       duration_days=_span(contexts), scanned=scanned, bounded=True,
                       label_a=_label(index, a_id), label_b=_label(index, b_id))


@dataclass
class Network(object):
    """The inferred affinity neighbourhood of one entity, and the bound that produced it."""

    entity_id: str
    label: object = None
    ties: tuple = ()
    contexts_read: int = 0
    partners_seen: int = 0
    candidates_scored: int = 0
    rows_scanned: int = 0
    bounded: bool = True
    not_inferable: tuple = ()      # (entity_id, tc.Unknown) for counterparts asked about and refused

    def strongest(self, count=5):
        return self.ties[:count]

    def to_json(self):
        return {'entity': self.entity_id, 'label': self.label,
                'ties': [t.to_json() for t in self.ties],
                'bound': {'contexts_read': self.contexts_read, 'partners_seen': self.partners_seen,
                          'candidates_scored': self.candidates_scored,
                          'index_rows_scanned': self.rows_scanned,
                          'counts_are_lower_bounds': self.bounded,
                          'candidate_cap': CANDIDATE_CAP, 'member_cap': MEMBER_CAP},
                'not_inferable': [{'entity': eid, 'reason': getattr(u, 'reason', str(u)),
                                   'detail': getattr(u, 'detail', '')} for eid, u in self.not_inferable]}

    def report(self):
        lines = ['inferred affinity network of %s (%s)' % (self.label or self.entity_id, self.entity_id),
                 '  %d contexts read, %d counterparts seen, %d scored (cap %d); every tie below is '
                 'inferred, not published' % (self.contexts_read, self.partners_seen,
                                              self.candidates_scored, CANDIDATE_CAP)]
        for value in self.ties:
            lines.append('')
            lines.append(value.explain(limit=6, records=1))
        for entity, reason in self.not_inferable:
            lines.append('')
            lines.append('%s: NOT inferable - %s (%s)'
                         % (entity, getattr(reason, 'reason', reason), getattr(reason, 'detail', '')))
        return '\n'.join(lines)


def network(index, entity_id, *, signals=SIGNALS, at=None, top=10, candidates=CANDIDATE_CAP,
            cap=MEMBER_CAP, donors=True, also=()):
    """The inferred affinity neighbourhood of one entity, strongest tie first.

    The walk is: read this entity's own attachments (bounded by ``Signal.per_signal``), read each
    context's members (bounded by ``cap``), group the counterparts by how many contexts they
    share, score the top ``candidates`` in full — homophily costs per-pair index reads, so it is
    not paid for every acquaintance — and return the top ``top``.

    Counterparts are keyed by their **canonical** id, so the same person reached as
    ``bioguide:K000367`` through a committee and as ``opensanctions:Q22237`` through an office is
    one counterpart. That merge comes from the ``resolved`` table; where ``resolved`` says nothing,
    nothing is merged.

    ``also`` names counterparts to report on whatever their score, including ones for which no tie
    is inferable: those land in :attr:`Network.not_inferable` with the reason.
    """
    _substrate()
    entity_id = entity_id_of(entity_id)
    ids = _cluster(index, entity_id)
    mine = {str(x) for x in ids}
    own = attachments(index, entity_id, signals=tuple(s for s in signals if s.shape == 'context'),
                      cluster=ids)
    rows = len(own)
    canonical = {}

    def canon(value):
        if value not in canonical:
            canonical[value] = str(index.canonical(value))
        return canonical[value]

    # canonical counterpart -> {(signal, context_id): (Context without its record id, edge rowid)}.
    # Keying by the context rather than appending means a predicate published once per term (the
    # 26 identical holds_position rows on Klobuchar's cluster) counts once, not 26 times.
    shared = {}
    seen_contexts = set()
    for attachment in own:
        key = (attachment.signal, attachment.context_id)
        if key in seen_contexts:
            continue
        seen_contexts.add(key)
        members, bounded, size = members_of(index, attachment.signal, attachment.context_id, cap=cap)
        rows += len(members)
        label = _label(index, attachment.context_id)
        for member_id, rowid in members:
            partner = canon(member_id)
            if member_id in mine or partner == canon(entity_id):
                continue
            if partner not in shared and len(shared) >= PARTNER_CAP:
                continue
            shared.setdefault(partner, {})[key] = (
                Context(signal=attachment.signal, predicate=BY_NAME[attachment.signal].predicate,
                        kind=attachment.signal, context_id=attachment.context_id, label=label,
                        members=size, members_seen=len(members), members_bounded=bounded,
                        a_id=attachment.entity_id, b_id=member_id,
                        first=attachment.valid_from, last=attachment.valid_from),
                rowid)
    # Pair signals: a published edge that names both people. Cheap, and never bounded away.
    for signal in (s for s in signals if s.shape == 'pair'):
        for direction, other in (('out', 'object'), ('in', 'subject')):
            for row in index.edges(ids, signal.predicate, direction=direction, limit=signal.per_signal):
                partner_id = str(row[other])
                if partner_id in mine:
                    continue
                rows += 1
                when = _parse_time(row.get('valid_from'))
                shared.setdefault(canon(partner_id), {})[(signal.name, '')] = (
                    Context(signal=signal.name, predicate=signal.predicate, kind=signal.name,
                            context_id='', label='published %s edge' % signal.predicate, members=2,
                            a_id=entity_id, b_id=partner_id, attested_pair=True, first=when,
                            last=when),
                    row.get('record_rowid'))

    asked = [entity_id_of(x) for x in also]
    extra = {canon(x) for x in asked}
    # Rank counterparts on co-membership alone; homophily is only paid for the ones that survive.
    ranked = sorted(shared.items(),
                    key=lambda item: (-co_membership_of([c for c, _ in item[1].values()]), item[0]))
    chosen = [key for key, _ in ranked[:candidates]]
    chosen += [key for key in sorted(extra) if key in shared and key not in chosen]

    my_donors = _donor_committees(index, entity_id, cluster=ids) if donors else (set(), False)
    ties, order = [], [signal.name for signal in SIGNALS]
    for key in chosen:
        contexts = tuple(sorted(
            (_with_records(index, context, rowid) for context, rowid in shared[key].values()),
            key=lambda c: (order.index(c.signal), c.context_id)))
        ties.append(AffinityTie(a=entity_id, b=key, contexts=contexts,
                                homophily=homophily(index, entity_id, key, at=at, donors=donors,
                                                    a_donors=my_donors),
                                duration_days=_span(contexts), scanned=rows, bounded=True,
                                label_a=_label(index, entity_id), label_b=_label(index, key)))
    ties.sort(key=lambda value: (-value.strength, value.b))
    keep = list(ties[:top]) + [value for value in ties[top:] if value.b in extra]

    refused = []
    for wanted in asked:
        if canon(wanted) in shared:
            continue
        # The network walk is bounded; the per-pair path is not. Ask it directly before saying no.
        exact = tie(index, entity_id, wanted, signals=signals, at=at, donors=donors)
        if _is_unknown(exact):
            refused.append((wanted, exact))
        else:
            keep.append(exact)
    keep.sort(key=lambda value: (-value.strength, value.b))
    return Network(entity_id=entity_id, label=_label(index, entity_id), ties=tuple(keep),
                   contexts_read=len(seen_contexts), partners_seen=len(shared),
                   candidates_scored=len(chosen), rows_scanned=rows, bounded=True,
                   not_inferable=tuple(refused))


# --------------------------------------------------------------------------- writing it down


def claim(value, *, scope=None):
    """The tie as a substrate claim. ``affinity_with``, never ``friend_of``."""
    substrate = _substrate()
    return substrate.Claim(substrate.Ref(value.a), INFERRED_PREDICATE, substrate.Ref(value.b),
                           scope=scope)


def evidence(value, *, now=None):
    """The tie's evidence: an *inference* over published records, saying so in ``method``."""
    substrate = _substrate()
    return substrate.Evidence(
        substrate.Ref('inference:%s' % value.method), now or _dt.datetime.now(UTC),
        locator='; '.join(value.records[:8]) or 'no records',
        method='inferred:%s' % value.method,
        confidence=substrate.Score(max(0.01, min(1.0, value.strength)), 'inferred_affinity'))


def assert_into(store, value, *, now=None, scope=None):
    """Write one inferred tie into a store, with its basis as premises.

    Returns ``(claim_id, premise_ids)``, the same shape ``transmission.receive`` returns, and for
    the same reason: the premises are claims **about the inference**, so ``explain`` shows an
    inferred tie as inferred and then walks down to the published record ids —
    ``inferred_by``, ``basis``, ``shared_contexts``, ``smallest_shared_context``,
    ``homophily_multiplier``, ``supporting_records`` and the flat refusal
    ``is_published_relationship False``.
    """
    substrate = _substrate()
    if not value.inferred:
        raise ValueError('affinity ties are inferred by construction; refusing to write one that '
                         'claims otherwise')
    when = now or _dt.datetime.now(UTC)
    basis_ref = substrate.Ref('inference:%s:%s:%s' % (value.method, value.a, value.b))
    premises = (
        substrate.Claim(basis_ref, 'inferred_by', value.method),
        substrate.Claim(basis_ref, 'basis', tuple(value.basis)),
        substrate.Claim(basis_ref, 'shared_contexts', int(value.frequency)),
        substrate.Claim(basis_ref, 'smallest_shared_context', int(value.exclusivity)),
        substrate.Claim(basis_ref, 'homophily_multiplier',
                        value.homophily.multiplier if value.homophily is not None else 1.0),
        substrate.Claim(basis_ref, 'supporting_records', tuple(value.records[:16])),
        substrate.Claim(basis_ref, 'is_published_relationship', False),
    )
    note = substrate.Evidence(substrate.Ref('inference:%s' % value.method), when,
                              locator='co-membership over %s' % ', '.join(value.predicates),
                              method='inferred:%s:premise' % value.method)
    store.apply(substrate.Patch(tuple(substrate.Tell(premise, (note,)) for premise in premises),
                                store.revision))
    premise_ids = tuple(sorted({premise.id for premise in premises}))
    body = evidence(value, now=when)
    content = substrate.Evidence(body.source, when, locator=body.locator, method=body.method,
                                 confidence=body.confidence, derived_from=premise_ids)
    written = claim(value, scope=scope)
    store.apply(substrate.Patch((substrate.Tell(written, (content,)),), store.revision))
    return written.id, premise_ids


# --------------------------------------------------------------------------- the informal channel


@dataclass(frozen=True)
class InformalChannel(object):
    """The channel an inferred tie opens. Field-compatible with ``transmission.Channel``.

    The first six fields are exactly ``transmission.Channel``'s, so ``transmission.tell`` can be
    handed one of these unchanged and nothing in that module needs editing. The rest are the
    authored assumptions that make this channel *informal* rather than a formal one with different
    numbers, and they are stated here rather than buried:

    * **fidelity 0.55**, below ``committee``'s 0.90 and ``cosponsorship``'s 0.75. A hallway remark
      has no clerk, no transcript and no bill text to check it against. Because
      ``lexicon.slip`` moves a measure's stage one rung up the ladder below full fidelity, a
      channel at 0.55 drifts faster than one at 0.90 — "they get garbled more" is the existing
      mechanism run at a lower number, not a new one.
    * **willingness 0.95**, above every attested formal channel, because nothing said here is
      filed. This is the candour axis as a number.
    * **no relevance filter** is the candour axis as a *structure*: :func:`link_for` builds the
      link with ``about=()``, and ``speech.sayable`` reads an empty ``about`` as no filter at all,
      so anything the speaker believes can be brought up. The committee channel carries committee
      business; this one carries whatever there is.
    * **attested False**, for the same reason ``lobby_contact`` is: the edges that opened it name
      a shared context, not a conversation. Nobody published that these two speak.
    """

    name: str = 'affinity'
    predicate: str = 'inferred:affinity_with'
    fidelity: float = 0.55
    willingness: float = 0.95
    attested: bool = False
    note: str = ('an inferred informal tie: more candour than any filed channel, less fidelity '
                 'than any of them, and no published record that the two ever spoke')
    candour: str = 'unfiltered'
    fidelity_basis: str = ('0.55 < committee 0.90 and cosponsorship 0.75: nothing written down, '
                           'nothing to check the telling against')
    willingness_basis: str = ('0.95 > committee 0.80 and cosponsorship 0.60: things get said that '
                              'would not be filed')
    candour_basis: str = ('about=() so speech.sayable applies no relevance filter; the formal '
                          'channels are restricted to their shared business')

    def to_json(self):
        return {'name': self.name, 'predicate': self.predicate, 'fidelity': self.fidelity,
                'willingness': self.willingness, 'attested': self.attested,
                'candour': self.candour, 'fidelity_basis': self.fidelity_basis,
                'willingness_basis': self.willingness_basis, 'candour_basis': self.candour_basis,
                'note': self.note}

    def as_channel(self):
        """The same channel as a ``transmission.Channel``, when that module is importable."""
        try:
            from .transmission import Channel
        except ImportError:
            return self
        return Channel(self.name, self.predicate, self.fidelity, self.willingness, self.attested,
                       self.note)


AFFINITY_CHANNEL = InformalChannel()

#: A tie this weak does not license the claim that these two talk, so no channel opens. It is a
#: refusal with a reason, not a silent zero.
CHANNEL_FLOOR = 0.20
#: The floor on trust over the channel, matching ``transmission.TRUST_WEIGHTS['base']``: an
#: inferred acquaintance you can barely evidence is trusted like a stranger.
TRUST_FLOOR = 0.15


def channel_trust(value):
    """How much a hearer trusts a teller over the informal channel: the tie strength itself."""
    return round(max(TRUST_FLOOR, min(1.0, value.strength)), 4)


@dataclass(frozen=True)
class InformalLink(object):
    """The fallback link shape, used only if ``transmission`` is not importable.

    It carries exactly the attributes ``transmission.tell`` reads off a ``Link``
    (``channel``, ``speaker``, ``hearer``, ``about``, ``tie``, ``trust``, ``records``, and the
    ``attested``/``fidelity`` properties), which is the narrow protocol this module depends on.
    """

    channel: object
    speaker: str
    hearer: str
    about: tuple = ()
    tie: object = None
    trust: float = TRUST_FLOOR
    records: tuple = ()

    @property
    def attested(self):
        return self.channel.attested

    @property
    def fidelity(self):
        return self.channel.fidelity

    def to_json(self):
        return {'channel': self.channel.name, 'predicate': self.channel.predicate,
                'speaker': self.speaker, 'hearer': self.hearer, 'attested': self.attested,
                'fidelity': self.fidelity, 'willingness': self.channel.willingness,
                'trust': self.trust, 'about': [], 'about_count': 0,
                'relevance_filter': 'none (candour)', 'records_cited': len(self.records),
                'note': self.channel.note,
                'tie': self.tie.to_json() if self.tie is not None else None}


def link_for(index, speaker, hearer, *, known_tie=None, floor=CHANNEL_FLOOR, at=None, donors=True):
    """A channel over the inferred tie, or ``tc.Unknown`` with a reason. Never edits transmission.

    Returns a ``transmission.Link`` when that module is importable, so
    ``transmission.tell(speaker, hearer, link=link_for(...))`` works with no change to it, and an
    :class:`InformalLink` otherwise. ``about`` is empty on purpose: that is the candour.
    """
    _substrate()
    found = known_tie if known_tie is not None else tie(index, speaker, hearer, at=at, donors=donors)
    if _is_unknown(found):
        return found
    if found.strength < floor:
        return _unknown('tie_too_weak_for_a_channel',
                        'strength %.4f is below the declared channel floor %.2f; the shared '
                        'context is %s, which evidences co-membership but not conversation'
                        % (found.strength, floor,
                           ', '.join('%s of %s%d' % (c.signal, '>=' if c.members_bounded else '',
                                                     c.members) for c in found.contexts)))
    trust = channel_trust(found)
    try:
        from .transmission import Link
    except ImportError:
        return InformalLink(AFFINITY_CHANNEL, found.a, found.b, about=(), tie=found, trust=trust,
                            records=found.records)
    return Link(AFFINITY_CHANNEL.as_channel(), found.a, found.b, about=(), tie=found, trust=trust,
                records=found.records)


def channels(index, speaker, hearer, *, at=None, formal=True, floor=CHANNEL_FLOOR, donors=True):
    """Every channel between two agents: the formal ones, plus the informal one. Strongest first.

    A caller iterating this says what it can on the record before it says anything in the
    hallway, because ``committee`` (0.90) and ``cosponsorship`` (0.75) both outrank ``affinity``
    (0.55). The informal channel is what carries the things the formal ones have no jurisdiction
    over — it is the only one of them with no relevance filter.
    """
    out = []
    if formal:
        try:
            from .transmission import links as formal_links
        except ImportError:
            formal_links = None
        if formal_links is not None:
            try:
                out += list(formal_links(index, speaker, hearer, at=at))
            except AttributeError:
                # ``transmission.links`` needs ``Person``/``Grounding`` objects (it reads
                # ``speaker.grounding.cluster``); this function also accepts plain entity ids,
                # for which only the informal channel is computable. A formal channel that
                # cannot be read does not close the informal one.
                pass
    informal = link_for(index, speaker, hearer, at=at, floor=floor, donors=donors)
    if not _is_unknown(informal):
        out.append(informal)
    return tuple(sorted(out, key=lambda link: (-link.channel.fidelity, link.channel.name)))


def gossip(speaker, hearer, *, index=None, known_tie=None, floor=CHANNEL_FLOOR, **kwargs):
    """``transmission.tell`` over the informal channel. The seam, and it edits nothing.

    Returns whatever ``tell`` returns, or a ``tc.Unknown`` when no informal channel is open.
    ``docs/agent-affinity.md`` records why this lives here rather than as a fourth entry in
    ``transmission.CHANNELS``: the formal channels are joins on one published predicate each, and
    this one is an inference over eleven.
    """
    from .transmission import tell
    index = index or speaker.grounding.index or hearer.grounding.index
    link = link_for(index, speaker, hearer, known_tie=known_tie, floor=floor,
                    at=kwargs.get('now'))
    if _is_unknown(link):
        return link
    return tell(speaker, hearer, index=index, link=link, **kwargs)


# --------------------------------------------------------------------------- authored assumptions


@dataclass(frozen=True)
class Assumption(object):
    """One authored number or rule, what it assumes, why, and what would argue against it."""

    name: str
    value: object
    assumes: str
    why: str
    falsifier: str = ''

    def to_json(self):
        return {'name': self.name, 'value': self.value, 'assumes': self.assumes, 'why': self.why,
                'falsifier': self.falsifier}


#: Every authored constant in this module, enumerated. ``tests/test_agents_affinity.py`` asserts
#: that this table and the live constants agree and that nothing is missing from it, so an
#: assumption cannot be added to the code without being declared here.
ASSUMPTIONS = (
    Assumption('selectivity.alpha', SELECTIVITY_ALPHA,
               'evidence from a shared context decays as (members-1)**-0.75',
               'alpha=1 would treat a 2,870-member body as 1/2869 of a two-person board, which '
               'assumes attention is spread uniformly over a chamber; alpha=0 would say a '
               'chamber and a two-person board are the same evidence. 0.75 is authored between '
               'them and makes a 13-member subcommittee 61x the evidence of the senator role.',
               'any measured contact-rate-versus-context-size curve would replace it'),
    Assumption('selectivity.max_context_weight', MAX_CONTEXT_WEIGHT,
               'no single shared context is conclusive',
               'a two-person board at base 0.95 would otherwise reach certainty and make the '
               'noisy-OR insensitive to everything else',
               'a context kind that really is conclusive would need its own base of 1.0'),
    Assumption('selectivity.broad_context', BROAD_CONTEXT,
               'a context above 200 members is reported as broad',
               'a label for readers, not a cutoff: nothing is dropped for being broad, it simply '
               'earns almost nothing through selectivity',
               'nothing: it has no effect on any number'),
    Assumption('combine.noisy_or', 'noisy-OR',
               'shared contexts of *different* kinds are independent lines of evidence',
               'monotone in frequency and saturating, so more shared contexts always beat fewer '
               'and nothing overflows past 1',
               'nothing measured; it is the standard combination rule for independent evidence'),
    Assumption('combine.repeat_discount', REPEAT_DISCOUNT,
               'the k-th repeat of the same kind of shared context is worth 0.85**k of the first',
               'repeated co-membership in one kind of context is correlated: twenty-five bills '
               'cosponsored with the same colleague are mostly the one fact that they are in the '
               'same coalition. Without it the noisy-OR saturates — measured on this index, '
               'Klobuchar/Blumenthal and Alieva/Aliyeva both came out at exactly 1.0000 and '
               'strength stopped distinguishing two very different ties. It does not discount '
               'across signals, so a board seat and a kinship edge stay two facts',
               'a per-kind measured correlation between repeat co-memberships'),
    Assumption('combine.nesting_is_not_discounted', False,
               'four subcommittees of one parent committee count as four contexts, not one',
               'the repeat discount damps it but does not model it: discounting nesting properly '
               'needs a published committee hierarchy, and this index has no parent/child edge '
               'between committee ids. Overstated in that direction, and said so rather than '
               'quietly corrected',
               'a published committee hierarchy would replace the assumption with a join'),
    Assumption('duration.full_days', DURATION_FULL_DAYS,
               'a decade of shared context is as long as duration is worth counting',
               'a round authored horizon; it only ever affects the single dated signal',
               'a measured relationship between tenure overlap and contact'),
    Assumption('duration.bonus', DURATION_BONUS,
               'duration is worth at most a quarter more',
               'small on purpose: measured, only cosponsored_measure carries valid_from in this '
               'index (0 dated rows on authored, director_of, holds_position, committee_member '
               'and research_affiliation), so a large duration term would reward one signal for '
               'an accident of publication',
               'dates on the other predicates would justify raising it'),
    Assumption('duration.unknown_is_neutral', 1.0,
               'an undated tie gets neither bonus nor penalty',
               'absence of a date is not evidence of a short relationship, and Unknown is the '
               'honest answer everywhere else in this layer',
               'nothing; the alternative is to invent a date'),
    Assumption('homophily.weights', dict(HOMOPHILY_WEIGHTS),
               'people who share a party, a delegation, a cohort or a donor talk more',
               'this is the one frankly sociological assumption in the module. It is why '
               'homophily only multiplies a tie that published co-membership already supports, '
               'and why tie() refuses to emit anything on homophily alone',
               'any measured contact rate between matched and unmatched pairs'),
    Assumption('homophily.bonus_only', True,
               'dissimilarity is not penalised',
               'penalising an opposite-party pair would assert that opposition suppresses '
               'informal contact — a stronger claim than similarity encouraging it, and equally '
               'unsupported here. docs/agent-communication.md measured the opposite for formal '
               'channels: mixing parties does not garble procedural news, it makes the story '
               'drift faster',
               'a measured cross-party contact rate below the base rate'),
    Assumption('homophily.donor_full', DONOR_FULL,
               'fifty shared donor committees saturate the donor term',
               'contribution *amounts* do not exist in this index at all (see NOT_AVAILABLE), so '
               'a shared donor can only be counted, and a count needs a saturation point',
               'itemised contribution data would replace the count with a weight'),
    Assumption('homophily.cap', HOMOPHILY_CAP,
               'homophily can raise a tie by at most 40%',
               'it keeps the sociological assumption subordinate to the published evidence: no '
               'combination of resemblances can turn a weak co-membership into a strong tie',
               'nothing measured; it is a declared bound on how much of the number is authored'),
    Assumption('signals.base_weights', {s.name: s.base for s in SIGNALS},
               'what each kind of shared room is worth at perfect selectivity',
               'ordered by how much choosing is involved: kinship and a published association '
               'above a board seat, a board seat above a co-authored paper, a paper above a '
               'committee, a committee above a shared employer, and same-office last because the '
               'catalog puts 2,870 people in "United States senator"',
               'the ordering is the falsifiable part; any measured contact rate by tie kind '
               'would replace it'),
    Assumption('channel.fidelity', AFFINITY_CHANNEL.fidelity,
               'an informal channel carries less of the utterance than a formal one',
               AFFINITY_CHANNEL.fidelity_basis,
               'a measured error rate on informal versus formal relay'),
    Assumption('channel.willingness', AFFINITY_CHANNEL.willingness,
               'an informal channel carries more candour than a formal one',
               AFFINITY_CHANNEL.willingness_basis,
               'a measured disclosure rate by channel'),
    Assumption('channel.candour_is_no_relevance_filter', AFFINITY_CHANNEL.candour,
               'anything the speaker believes can be brought up on an informal channel',
               AFFINITY_CHANNEL.candour_basis,
               'evidence that informal talk is in fact topic-bounded'),
    Assumption('channel.attested', AFFINITY_CHANNEL.attested,
               'the channel is possible, not attested',
               'the edges that opened it name a shared context, not a conversation — the same '
               'reason transmission marks lobby_contact unattested',
               'a published record of the two speaking, which would make it a formal channel'),
    Assumption('channel.floor', CHANNEL_FLOOR,
               'below strength 0.20 no channel opens',
               'a tie can be real evidence of co-membership and still not license the claim that '
               'two people talk. The refusal is returned as an Unknown with the strength and the '
               'contexts in it, not as a silent zero',
               'nothing measured; it is where the authored line is drawn'),
    Assumption('channel.trust_floor', TRUST_FLOOR,
               'an inferred acquaintance is trusted like a stranger, not less',
               'it matches transmission.TRUST_WEIGHTS["base"] = 0.15 so the two channel families '
               'are on one scale',
               'nothing; it is a shared constant'),
    Assumption('bound.member_cap', MEMBER_CAP,
               'a context is read to 500 members and then called broad',
               'a context that big already earns almost nothing, so paying to count it exactly '
               'buys no accuracy. Where it bites, Context.members_bounded says so and the '
               'selectivity reported is an upper bound',
               'nothing; raising it changes cost, not conclusions'),
    Assumption('bound.candidate_cap', CANDIDATE_CAP,
               'only the top 40 counterparts are scored in full',
               'homophily costs per-pair index reads, and a neighbourhood can run to thousands. '
               'Network.bound reports what was seen against what was scored',
               'nothing; it is a cost bound and it is reported'),
)


def assumption_values():
    """The live value of every assumption, read from the constants rather than the table."""
    return {
        'selectivity.alpha': SELECTIVITY_ALPHA,
        'selectivity.max_context_weight': MAX_CONTEXT_WEIGHT,
        'selectivity.broad_context': BROAD_CONTEXT,
        'combine.noisy_or': 'noisy-OR',
        'combine.repeat_discount': REPEAT_DISCOUNT,
        'combine.nesting_is_not_discounted': False,
        'duration.full_days': DURATION_FULL_DAYS,
        'duration.bonus': DURATION_BONUS,
        'duration.unknown_is_neutral': duration_multiplier(None),
        'homophily.weights': dict(HOMOPHILY_WEIGHTS),
        'homophily.bonus_only': True,
        'homophily.donor_full': DONOR_FULL,
        'homophily.cap': HOMOPHILY_CAP,
        'signals.base_weights': {s.name: s.base for s in SIGNALS},
        'channel.fidelity': AFFINITY_CHANNEL.fidelity,
        'channel.willingness': AFFINITY_CHANNEL.willingness,
        'channel.candour_is_no_relevance_filter': AFFINITY_CHANNEL.candour,
        'channel.attested': AFFINITY_CHANNEL.attested,
        'channel.floor': CHANNEL_FLOOR,
        'channel.trust_floor': TRUST_FLOOR,
        'bound.member_cap': MEMBER_CAP,
        'bound.candidate_cap': CANDIDATE_CAP,
    }


# --------------------------------------------------------------------------- the worked examples


#: The real entities the documentation and the smoke tests are built on. All of them are matters
#: of record in this catalog; the tests assert the mechanism, not these particular people.
WORKED_EXAMPLE = {
    'legislator': 'bioguide:K000367',                       # Amy Klobuchar
    'legislator_cluster': ('bioguide:K000367', 'fec:candidate:S6MN00267', 'icpsr:40700',
                           'opensanctions:Q22237'),
    'delegation_colleague': 'bioguide:S001203',             # Tina Smith, MN, same party
    'judiciary_colleague': 'bioguide:B001277',              # Richard Blumenthal, 4 shared panels
    'no_shared_committee': 'bioguide:C001035',              # Susan Collins: no shared panel
    'broad_context_only': 'bioguide:T000464',               # Jon Tester: same office, nothing else
    'broad_office': 'opensanctions:Q4416090',               # "United States senator", 2,870 holders
    'broadest_office': 'opensanctions:Q13218630',           # "United States representative", 12,671
    'kin': 'opensanctions:Q6196159',                        # Jim Klobuchar, published family edge
    'spouse': 'opensanctions:Q6221766',                     # John Bessler, published family edge
    'researcher': 'orcid:0000-0002-9649-3588',              # Bryant R. England
    'co_author': 'orcid:0000-0002-0897-2272',               # Ted R. Mikuls, 18 shared DOIs
    'director': 'opensanctions:Q23683242',                  # Arzu Alieva
    'co_director': 'opensanctions:Q298532',                 # Leyla Aliyeva, 16 shared boards
    'unjoinable_researcher': 'openalex:A5134754013',        # OpenAlex: an unbridged namespace
}
