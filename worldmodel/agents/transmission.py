"""Belief transmission across the published social network.

:mod:`worldmodel.agents.speech` owns the words. This module owns **who may say them to whom,
how much gets through, and what the hearer does with what arrives**. The point of the port is
that the network is not invented: every channel here is a join over edges the unified graph
actually publishes.

| Channel | Published edge | What opens it |
| --- | --- | --- |
| ``committee`` | ``committee_member`` (3,895) | both sit on the same committee id |
| ``cosponsorship`` | ``cosponsored_measure`` (1,283,245) | both put their names to the same measures |
| ``lobby_contact`` | ``contacted_government_entity`` (406,766) | one filing reported contacting both chambers |

The third is deliberately marked ``attested=False``. ``lda_lobbying`` publishes the contact at
*chamber* granularity — ``lda:government_entity:1`` is labelled ``SENATE`` — so the edge says a
registrant reported contacting the Senate, not that it reached this senator. The channel is
therefore *possible* rather than attested, carries the lowest fidelity in :data:`CHANNELS`, and
:class:`Link.attested` says so rather than the code pretending otherwise.

**Trust is derived, not declared.** :func:`tie` counts shared committees, shared cosponsorships
(a bounded scan, so the count is an honest *lower bound* and says so) and whether the published
party is the same, and cites the record ids for each. :func:`trust_from` combines them with
authored weights. Nothing about it is fitted.

**Hearsay is a different evidence kind.** A heard claim enters the hearer's store with
``Evidence(source=Ref('told:<speaker>'), method='heard:<channel>')`` and premises that are
claims *about the utterance* — who said it, over which channel, how many tellers deep, the whole
chain, and the published record id the chain bottoms out in (or ``nobody``). ``explain`` walks
those premises, so "why do you believe that" answers with the chain of tellers and then with a
dataset version and record id, or admits there is no record at the end of it.

**A contradiction is adjudicated, never overwritten.** :func:`adjudicate` weighs the live claims
on a functional predicate: direct observation of a published record sits at
:data:`DIRECT_FLOOR`, and a single teller is capped below it by :data:`HEARSAY_CEILING`, so one
teller can never beat the record however trusted. Independent tellers combine
(``1 - Π(1 - wᵢ)`` over disjoint chains) and *can*. The loser is retracted with a recorded
reason, and a tie leaves both claims standing rather than picking one.

Nothing here is fitted to an outcome and nothing here is ``validated``.
"""
import datetime as _dt
import hashlib
from dataclasses import dataclass

from . import load_tensacode
from . import speech as _speech
from .grounding import advance_state
from .person import Episode

tc = load_tensacode()

UTC = _dt.timezone.utc


# --------------------------------------------------------------------------- channels


@dataclass(frozen=True)
class Channel(object):
    """One published relationship that carries speech, and what it costs to speak over it.

    ``fidelity`` and ``willingness`` are authored — the design contract forbids fitting anything
    in this layer — but *which* channel exists between two agents is entirely a matter of record.
    """

    name: str
    predicate: str          # the published edge predicate the channel is joined on
    fidelity: float         # share of the utterance that survives, before register mismatch
    willingness: float      # how readily a speaker brings something up on this channel
    attested: bool          # does the edge name both parties, or only a body they share?
    note: str = ''


CHANNELS = (
    Channel('committee', 'committee_member', 0.90, 0.80, True,
            'both are published members of the same committee: they are in the room together'),
    Channel('cosponsorship', 'cosponsored_measure', 0.75, 0.60, True,
            'both put their names to the same measures: the traffic is staff-mediated'),
    Channel('lobby_contact', 'contacted_government_entity', 0.50, 0.90, False,
            'one filing reported contacting both chambers; the edge is chamber-level, so the '
            'channel is possible rather than attested'),
)
BY_NAME = {channel.name: channel for channel in CHANNELS}

#: The ``lda`` government entities that stand for a chamber. Declared as candidates and then
#: **verified against the published label** before the channel opens, so a re-numbered id closes
#: the channel instead of silently linking the wrong body.
CHAMBER_ENTITY = {'senate': ('lda:government_entity:1', 'SENATE'),
                  'house': ('lda:government_entity:2', 'HOUSE OF REPRESENTATIVES')}

#: How far a cosponsorship or lobbying scan may go per pair. Every count it produces is a lower
#: bound within this many rows, and :class:`Tie` reports the bound rather than implying totals.
SCAN = 400


@dataclass(frozen=True)
class Tie(object):
    """What the record says about the relationship between two agents. No trust in it yet."""

    a: str
    b: str
    shared_committees: tuple = ()
    shared_measures: tuple = ()
    same_party: object = None      # True, False, or tc.Unknown when either party is unpublished
    same_chamber: object = None
    shared_filings: tuple = ()
    records: tuple = ()            # published record ids supporting every line above
    scanned: int = 0
    bounded: bool = True           # the counts are lower bounds within ``scanned`` rows

    def to_json(self):
        return {'a': self.a, 'b': self.b, 'shared_committees': list(self.shared_committees),
                'shared_measures': len(self.shared_measures),
                'shared_measure_examples': list(self.shared_measures[:3]),
                'same_party': None if isinstance(self.same_party, tc.Unknown) else self.same_party,
                'same_chamber': None if isinstance(self.same_chamber, tc.Unknown) else self.same_chamber,
                'shared_filings': len(self.shared_filings), 'records_cited': len(self.records),
                'index_rows_scanned': self.scanned, 'counts_are_lower_bounds': self.bounded}


#: How the published relationship becomes a trust number. Authored weights, declared here so they
#: can be argued with; the *terms* are all counts of published records.
TRUST_WEIGHTS = {'base': 0.15, 'committee': 0.30, 'cosponsorship': 0.35, 'party': 0.20}
#: Saturation points: two shared committees, twenty-five shared cosponsorships.
TRUST_FULL = {'committee': 2.0, 'cosponsorship': 25.0}


def trust_from(tie):
    """``[0.15, 1.0]`` from the published tie. An unpublished party contributes nothing."""
    value = TRUST_WEIGHTS['base']
    value += TRUST_WEIGHTS['committee'] * min(1.0, len(tie.shared_committees) / TRUST_FULL['committee'])
    value += TRUST_WEIGHTS['cosponsorship'] * min(1.0, len(tie.shared_measures) / TRUST_FULL['cosponsorship'])
    if tie.same_party is True:
        value += TRUST_WEIGHTS['party']
    return round(min(1.0, value), 4)


def _seat(index, person, at=None):
    """``(chamber, party, record_id)`` for the role that holds at ``at``. See ``published_role``."""
    return _speech.published_role(index, person.grounding, at=at or person.now)


def _edge_ids(index, rows):
    out = []
    for row in rows:
        record = index.record_at(row['record_rowid']) if row.get('record_rowid') else None
        if record is not None:
            out.append(record['_provenance']['record_id'])
    return out


def tie(index, speaker, hearer, *, scan=SCAN, at=None):
    """Join the two agents' published edges. Bounded by ``scan`` rows per predicate per side."""
    a_ids, b_ids = speaker.grounding.cluster, hearer.grounding.cluster
    a_id, b_id = speaker.grounding.entity_id, hearer.grounding.entity_id
    records, scanned = [], 0

    a_committees = index.edges(a_ids, 'committee_member', limit=scan)
    b_committees = index.edges(b_ids, 'committee_member', limit=scan)
    scanned += len(a_committees) + len(b_committees)
    b_committee_ids = {row['object'] for row in b_committees}
    shared_committees = tuple(sorted({row['object'] for row in a_committees} & b_committee_ids))
    if shared_committees:
        records += _edge_ids(index, [r for r in a_committees if r['object'] in shared_committees])
        records += _edge_ids(index, [r for r in b_committees if r['object'] in shared_committees])

    a_measures = index.edges(a_ids, 'cosponsored_measure', limit=scan)
    b_measures = index.edges(b_ids, 'cosponsored_measure', limit=scan)
    scanned += len(a_measures) + len(b_measures)
    b_measure_ids = {row['object'] for row in b_measures}
    shared_measures = tuple(sorted({row['object'] for row in a_measures} & b_measure_ids))
    if shared_measures:
        records += _edge_ids(index, [r for r in a_measures if r['object'] in shared_measures][:8])

    a_chamber, a_party, a_claim = _seat(index, speaker, at=at)
    b_chamber, b_party, b_claim = _seat(index, hearer, at=at)
    if a_party is None or b_party is None:
        same_party = tc.Unknown('no_published_party',
                                'the record does not give a party for %s' % (a_id if a_party is None else b_id))
    else:
        same_party = a_party == b_party
    if a_chamber is None or b_chamber is None:
        same_chamber = tc.Unknown('no_published_chamber', 'the record does not give a chamber for both')
    else:
        same_chamber = a_chamber == b_chamber

    shared_filings, filing_rows = _shared_filings(index, a_chamber, b_chamber, scan=scan)
    scanned += filing_rows
    if shared_filings:
        records.append('lda:chamber-contact:%s' % (a_chamber or 'unknown'))

    return Tie(a=a_id, b=b_id, shared_committees=shared_committees, shared_measures=shared_measures,
               same_party=same_party, same_chamber=same_chamber, shared_filings=shared_filings,
               records=tuple(dict.fromkeys(x for x in records if x)) + tuple(x for x in (a_claim, b_claim) if x),
               scanned=scanned, bounded=True)


def _shared_filings(index, a_chamber, b_chamber, *, scan=SCAN, limit=24):
    """Filings that reported contacting both agents' chambers, verified by published label."""
    entities = []
    for chamber in (a_chamber, b_chamber):
        candidate = CHAMBER_ENTITY.get(chamber or '')
        if candidate is None:
            return (), 0
        entity_id, expected = candidate
        record = index.entity(entity_id)
        if record is None or str(record.get('label') or '').strip().upper() != expected:
            return (), 0  # the id was renumbered: close the channel rather than link the wrong body
        entities.append(entity_id)
    rows_read = 0
    sets = []
    for entity_id in dict.fromkeys(entities):
        rows = index.edges([entity_id], 'contacted_government_entity', direction='in', limit=min(scan, 200))
        rows_read += len(rows)
        sets.append({row['subject'] for row in rows})
    shared = set.intersection(*sets) if sets else set()
    return tuple(sorted(shared)[:limit]), rows_read


@dataclass(frozen=True)
class Link(object):
    """One open channel between two agents, with the published support that opened it."""

    channel: Channel
    speaker: str
    hearer: str
    about: tuple = ()        # the published entity ids this channel is about
    tie: Tie = None
    trust: float = 0.5
    records: tuple = ()

    @property
    def attested(self):
        return self.channel.attested

    @property
    def fidelity(self):
        return self.channel.fidelity

    def to_json(self):
        return {'channel': self.channel.name, 'predicate': self.channel.predicate,
                'speaker': self.speaker, 'hearer': self.hearer, 'attested': self.channel.attested,
                'fidelity': self.channel.fidelity, 'willingness': self.channel.willingness,
                'trust': self.trust, 'about': list(self.about[:6]), 'about_count': len(self.about),
                'records_cited': len(self.records), 'note': self.channel.note,
                'tie': self.tie.to_json() if self.tie else None}


#: How much of one committee's docket counts as current business, and the total across an
#: agent's seats. Both are bounds, not totals: a measure referred longer ago than this slice is
#: outside what the agent is taken to have in front of it, and its number will not resolve.
PER_COMMITTEE = 400
DOCKET_CAP = 8000


def jurisdiction(index, committees, *, per_committee=PER_COMMITTEE, cap=DOCKET_CAP):
    """The measures published as referred to these committees, most recent first.

    This is what makes the committee channel about more than the committee: a bill before a
    panel both agents sit on is their business, and ``referred_to_committee`` (175,853 edges)
    says which bills those are. The slice is per committee so that a member of eighteen panels
    does not spend the whole budget on the first one, and the same slice is what the agent can
    put a name to (:func:`names_for`) — one bound, used consistently.
    """
    out = []
    for committee in committees:
        if len(out) >= cap:
            break
        rows = index.edges([committee], 'referred_to_committee', direction='in',
                           limit=min(per_committee, cap - len(out)))
        out += [row['subject'] for row in rows]
    return tuple(dict.fromkeys(out))


def links(index, speaker, hearer, *, scan=SCAN, known_tie=None, at=None):
    """Every channel published between these two agents, strongest first. May be empty."""
    if speaker.grounding.entity_id == hearer.grounding.entity_id:
        return ()
    found = known_tie if known_tie is not None else tie(index, speaker, hearer, scan=scan, at=at)
    value = trust_from(found)
    out = []
    if found.shared_committees:
        about = found.shared_committees + jurisdiction(index, found.shared_committees)
        out.append(Link(BY_NAME['committee'], found.a, found.b, about=about,
                        tie=found, trust=value, records=found.records))
    if found.shared_measures:
        out.append(Link(BY_NAME['cosponsorship'], found.a, found.b, about=found.shared_measures,
                        tie=found, trust=value, records=found.records))
    if found.shared_filings:
        out.append(Link(BY_NAME['lobby_contact'], found.a, found.b, about=found.shared_filings,
                        tie=found, trust=value, records=found.records))
    return tuple(sorted(out, key=lambda link: (-link.channel.fidelity, link.channel.name)))


# --------------------------------------------------------------------------- weighing evidence


#: The weight of a claim read straight off a published record.
DIRECT_FLOOR = 0.70
#: The hard cap on what **one** teller's word is worth, whatever the trust and the channel. It is
#: strictly below :data:`DIRECT_FLOOR`, which is the rule the design asks for: hearsay from one
#: teller never outranks direct observation.
HEARSAY_CEILING = 0.60
#: Before trust, fidelity and chain length are applied.
HEARSAY_BASE = 0.90
#: Each extra published record naming the same thing adds this much, up to 1.0.
CORROBORATION_STEP = 0.10


def hearsay_weight(trust, fidelity, score=1.0, upstream=1.0):
    """What one teller's word is worth, capped strictly below direct observation.

    Chain length enters through ``upstream`` — the confidence the *speaker* had in what it was
    passing on — so the definition is recursive rather than an exponent bolted on: a teller
    cannot make you more certain than it was itself, times what this channel costs. Direct
    observation enters the recursion at ``upstream = 1``.
    """
    value = (HEARSAY_BASE * max(0.0, trust) * max(0.0, fidelity) * max(0.0, score)
             * max(0.0, upstream))
    return round(min(HEARSAY_CEILING, value), 5)


def direct_weight(records):
    return round(min(1.0, DIRECT_FLOOR + CORROBORATION_STEP * max(0, len(records) - 1)), 5)


def _independent_groups(provenances):
    """Split hearsay supports into groups whose chains of tellers do not overlap.

    Two reports that passed through the same mouth are one report, not two, so they may not
    corroborate each other. Chains are compared as sets of teller ids.
    """
    groups = []
    for provenance in provenances:
        chain = set(provenance.chain) or {'(unattributed)'}
        for group in groups:
            if group['chain'] & chain:
                group['chain'] |= chain
                group['weights'].append(provenance.confidence)
                break
        else:
            groups.append({'chain': set(chain), 'weights': [provenance.confidence]})
    return groups


def combined_hearsay(provenances):
    """``1 - Π(1 - wᵢ)`` over *independent* tellers; within a group, the best single report."""
    total = 0.0
    for group in _independent_groups(provenances):
        total = 1.0 - (1.0 - total) * (1.0 - min(HEARSAY_CEILING, max(group['weights'])))
    return round(total, 5)


@dataclass
class Weighed(object):
    """One candidate object for a functional predicate, and what stands behind it."""

    object: object
    claim_ids: tuple
    weight: float
    kind: str                # 'published' | 'hearsay' | 'derived'
    tellers: tuple = ()
    records: tuple = ()

    def to_json(self):
        return {'object': str(self.object), 'weight': round(self.weight, 4), 'kind': self.kind,
                'tellers': list(self.tellers), 'records': list(self.records[:4]),
                'claims': list(self.claim_ids)}


#: Two candidates this close are not distinguishable, and both are left standing.
ADJUDICATION_MARGIN = 0.02


@dataclass
class Adjudication(object):
    """What a hearer did about holding two incompatible claims. Never a silent overwrite."""

    subject: str = ''
    predicate: str = ''
    candidates: tuple = ()
    kept: object = None
    dropped: tuple = ()
    reason: str = ''
    unknown: object = None

    def to_json(self):
        return {'subject': self.subject, 'predicate': self.predicate,
                'candidates': [c.to_json() for c in self.candidates],
                'kept': None if self.kept is None else str(self.kept.object),
                'dropped': [str(c.object) for c in self.dropped], 'reason': self.reason,
                'unknown': None if self.unknown is None else {'reason': self.unknown.reason,
                                                              'detail': self.unknown.detail}}


def weigh(store, subject, predicate, *, scope=None):
    """Group the live claims on ``(subject, predicate)`` by object and weigh each group."""
    by_object = {}
    for record in store.claims(subject, predicate, scope=scope):
        obj = getattr(record.claim.object, 'id', record.claim.object)
        by_object.setdefault(str(obj), []).append(record)
    out = []
    for _object, records in sorted(by_object.items()):
        published, hearsay, derived = [], [], []
        for record in records:
            # Every *line of support*, not one per record: two people telling you the same thing
            # land on one claim with two pieces of evidence, and corroboration has to see both.
            for provenance in _speech.supports_of(store, record):
                (published if provenance.observed else hearsay if provenance.kind == 'hearsay'
                 else derived).append(provenance)
        if published:
            weight = direct_weight([p.origin for p in published if p.origin])
            kind, tellers = 'published', ()
        elif hearsay:
            weight = combined_hearsay(hearsay)
            kind = 'hearsay'
            tellers = tuple(dict.fromkeys(p.chain[0] for p in hearsay if p.chain))
        else:
            weight, kind, tellers = 0.30, 'derived', ()
        out.append(Weighed(object=records[0].claim.object, claim_ids=tuple(r.id for r in records),
                           weight=weight, kind=kind, tellers=tellers,
                           records=tuple(p.origin for p in published + hearsay if p.origin)))
    return sorted(out, key=lambda w: (-w.weight, str(w.object)))


def adjudicate(store, subject, predicate, *, scope=None, now=None, me=None):
    """Resolve a contradiction on a functional predicate, on the record.

    The loser is **retracted with a reason**, not forgotten, so ``claims(include_retracted=True)``
    still shows what the agent used to hold and why it stopped. Two candidates within
    :data:`ADJUDICATION_MARGIN` are left standing and the outcome is an ``Unknown``: an agent
    that cannot tell two reports apart holds both, which is honest.
    """
    candidates = weigh(store, subject, predicate, scope=scope)
    result = Adjudication(subject=str(subject), predicate=predicate, candidates=tuple(candidates))
    if len(candidates) < 2:
        result.kept = candidates[0] if candidates else None
        result.reason = 'no contradiction'
        return result
    best, second = candidates[0], candidates[1]
    if best.weight - second.weight <= ADJUDICATION_MARGIN:
        result.unknown = tc.Unknown('tie_within_margin',
                                    '%s and %s are within %.2f (%.3f vs %.3f); both are kept'
                                    % (best.object, second.object, ADJUDICATION_MARGIN,
                                       best.weight, second.weight))
        result.reason = 'tie within margin; both claims left standing'
        return result
    reason = ('%s (%s, %.3f) outweighs %s (%s, %.3f)'
              % (best.object, best.kind, best.weight, second.object, second.kind, second.weight))
    edits = []
    for candidate in candidates[1:]:
        for claim_id in candidate.claim_ids:
            edits.append(tc.Retract(claim_id, reason))
    if me is not None:
        edits.append(tc.Tell(
            tc.Claim(me, 'adjudicated', (str(subject), predicate, str(getattr(best.object, 'id', best.object)),
                                         tuple(str(getattr(c.object, 'id', c.object)) for c in candidates[1:]))),
            (tc.Evidence(tc.Ref('adjudication:evidence-weight'), now or _dt.datetime.now(UTC),
                         locator=reason, method='adjudicate@1',
                         confidence=tc.Score(round(best.weight, 3), 'evidence_weight'),
                         derived_from=tuple(sorted(best.claim_ids))),)))
    if edits:
        store.apply(tc.Patch(tuple(edits), store.revision))
    result.kept, result.dropped, result.reason = best, tuple(candidates[1:]), reason
    return result


#: The predicates a hearer treats as functional: at most one object per subject. Declaring them
#: is what makes ``Store.conflicts`` and :func:`adjudicate` see a contradiction at all.
FUNCTIONAL = ('measure_stage', 'policy_area')


def prepare(person):
    """Declare the functional predicates on an agent's store. Idempotent."""
    for predicate in FUNCTIONAL:
        person.store.declare(predicate, functional=True)
    return person


# --------------------------------------------------------------------------- telling


#: A sayable this uninteresting, after willingness, is not brought up at all.
SPEAK_FLOOR = 0.08


def trust_in(person, other):
    """How much this agent trusts ``other``, from the published tie when one has been read."""
    relation = person.relation(str(other))
    return float(relation.get('tie_trust', relation.get('trust', 0.5)))


#: How many co-members of one of the agent's own committees it can put a name to, and the total.
#: Bounded like everything else: a colleague outside the slice is a name the agent does not know.
PEERS_PER_COMMITTEE = 40
PEER_CAP = 400


def colleagues(index, committees, *, per_committee=PEERS_PER_COMMITTEE, cap=PEER_CAP):
    """The people published as sitting on these committees. The other half of who you can name."""
    out = []
    for committee in committees:
        if len(out) >= cap:
            break
        rows = index.edges([committee], 'committee_member', direction='in',
                           limit=min(per_committee, cap - len(out)))
        out += [row['subject'] for row in rows if str(row['subject']).startswith('bioguide:')]
    return tuple(dict.fromkeys(out))


def names_for(person, *, index=None, extra=(), reach=True):
    """The token resolver this agent hears with, cached on the agent.

    It is built from **what this agent can reach on its own**: the entities in its store, the
    recent docket of the committees it is published as sitting on (:func:`jurisdiction`), and the
    people published as sitting on them (:func:`colleagues`). Nothing the speaker knows is handed
    over, so a bill number or a name outside the hearer's own reach stays an unresolved token and
    becomes a ``measure:S1241`` or ``person:Alpha`` Ref rather than the published id.
    """
    book = getattr(person, '_spoken_names', None)
    if book is None:
        book = _speech.Names.of(person, index=index)
        if index is not None and reach:
            seats = [str(getattr(record.claim.object, 'id', record.claim.object))
                     for record in person.store.claims(person.grounding.me, 'serves_on')]
            book.learn_all(jurisdiction(index, seats))
            book.learn_all(colleagues(index, seats))
        person._spoken_names = book
    book.learn_all([str(entity_id) for entity_id in extra])
    return book


def attributes_source(provenance, trust_in_teller):
    """Does the speaker name its source in the words? Declared, and it costs something.

    You name the person who told you when you trust them and they told you directly; past that
    the name has fallen out of the telling and survives only in provenance. Naming a source is
    not free: the parser hangs an adjunct on the outer ``tell`` frame, so an attributed
    ``serves_on`` loses the committee (:data:`~worldmodel.agents.speech.RELAY_LOSSY_CONCEPTS`).
    """
    return provenance.kind == 'hearsay' and provenance.hops == 1 and trust_in_teller >= 0.5


@dataclass
class Transmission(object):
    """One attempt to move one belief between two agents. Every outcome is a real outcome."""

    speaker: str = ''
    hearer: str = ''
    channel: str = ''
    outcome: str = ''          # see OUTCOMES
    sentence: str = None
    concept: str = None
    said: object = None        # the Sayable the speaker chose
    heard: object = None       # the speech.Heard the hearer recovered
    claim_id: str = None
    utterance: str = None
    confidence: float = 0.0
    trust: float = 0.0
    fidelity: float = 0.0
    intelligibility: float = 1.0
    hops: int = 0
    chain: tuple = ()
    origin: str = None
    registers: tuple = ()
    note: str = ''
    adjudication: object = None

    @property
    def integrated(self):
        return self.outcome == 'integrated'

    def to_json(self):
        return {'speaker': self.speaker, 'hearer': self.hearer, 'channel': self.channel,
                'outcome': self.outcome, 'sentence': self.sentence, 'concept': self.concept,
                'said': self.said.to_json() if self.said else None,
                'heard': self.heard.to_json() if self.heard else None,
                'claim': self.claim_id, 'utterance': self.utterance,
                'confidence': round(self.confidence, 4), 'trust': round(self.trust, 4),
                'fidelity': round(self.fidelity, 4), 'intelligibility': self.intelligibility,
                'hops': self.hops, 'chain': list(self.chain), 'origin': self.origin,
                'registers': list(self.registers), 'note': self.note,
                'adjudication': self.adjudication.to_json() if self.adjudication else None}


#: Every way an exchange can end. All seven are recorded; none of them is patched over.
OUTCOMES = ('integrated', 'no_channel', 'nothing_sayable', 'unwilling', 'not_understood',
            'already_believed', 'outweighed')


def tell(speaker, hearer, *, index=None, link=None, choose=None, seed=0, now=None, attribute=None,
         scan=SCAN):
    """Speaker says one thing to hearer over a published channel. Returns a :class:`Transmission`.

    The whole of the exchange is here: find the channel in the record, decide what is sayable and
    whether it is worth saying, realize it in the speaker's register, hear it in the hearer's,
    and integrate what survives with ``X told me`` provenance — then adjudicate if the hearer now
    holds two incompatible claims.
    """
    index = index or speaker.grounding.index or hearer.grounding.index
    now = now or hearer.now or speaker.now or _dt.datetime.now(UTC)
    speaker_id, hearer_id = speaker.grounding.entity_id, hearer.grounding.entity_id
    open_links = (link,) if link is not None else links(index, speaker, hearer, scan=scan, at=now)
    if not open_links:
        note = 'no committee, cosponsorship or lobbying edge joins them in this index'
        _record_silence(speaker, hearer_id, 'no_channel', note, now)
        return Transmission(speaker=speaker_id, hearer=hearer_id, channel='', outcome='no_channel',
                            note=note)
    # Two people joined by more than one published relationship have more than one channel. The
    # highest-fidelity one that has something to carry wins; a channel with nothing relevant on it
    # is not an excuse to fall silent when another channel is open.
    link, chosen = open_links[0], None
    for candidate_link in open_links:
        found = _speech.sayable(speaker, about=candidate_link.about)
        picked = (choose(found) if choose is not None else (found[0] if found else None))
        if picked is not None:
            link, chosen = candidate_link, picked
            break
    prepare(hearer)
    trust = link.trust
    # The published tie is written as its own key. ``relation['trust']`` belongs to the person
    # agent's own consolidation and is not clobbered here; ``tie_trust`` is the part that derives
    # from the record, and it is what this module reads back (see :func:`trust_in`).
    speaker.relation(hearer_id).update(tie_trust=trust, label=link.channel.name)
    hearer.relation(speaker_id).update(tie_trust=trust, label=link.channel.name)
    if chosen is None:
        note = ('nothing this agent believes is both sayable and relevant on %s'
                % ', '.join(sorted(open_link.channel.name for open_link in open_links)))
        _record_silence(speaker, hearer_id, 'nothing_sayable', note, now)
        return Transmission(speaker=speaker_id, hearer=hearer_id, channel=link.channel.name,
                            outcome='nothing_sayable', trust=trust, note=note)

    willing = link.channel.willingness * (0.4 + 0.6 * trust)
    if chosen.weight * willing < SPEAK_FLOOR:
        note = ('worth %.3f to say over %s at trust %.2f, below the %.2f floor'
                % (chosen.weight * willing, link.channel.name, trust, SPEAK_FLOOR))
        _record_silence(speaker, hearer_id, 'unwilling', note, now)
        return Transmission(speaker=speaker_id, hearer=hearer_id, channel=link.channel.name,
                            outcome='unwilling', trust=trust, said=chosen, note=note)

    speaker_register = _speech.register_for(speaker, at=now, index=index)
    hearer_register = _speech.register_for(hearer, at=now, index=index)
    understanding = _speech.intelligibility(speaker_register, hearer_register)
    # The hearer resolves names only against what *it* can reach, plus whoever is talking to it.
    # Nothing the speaker knows is handed over: an unresolved token is the point, not a bug.
    names = names_for(hearer, index=index, extra=(speaker_id,))
    speaker_names = names_for(speaker, index=index, extra=(chosen.subject,))
    subject_token = speaker_names.token(chosen.subject)
    object_token = (speaker_names.token(chosen.object) if _speech.is_entity_id(str(chosen.object))
                    else _speech.one_word(chosen.object) if chosen.object is not None else None)

    teller = chosen.provenance.chain[0] if chosen.provenance.chain else None
    trust_in_teller = trust_in(speaker, teller) if teller else 0.0
    name_source = attributes_source(chosen.provenance, trust_in_teller) if attribute is None else bool(attribute)
    attributed = speaker_names.token(teller) if (name_source and teller) else None
    sentence = _speech.say(chosen.concept, subject_token, object_token, speaker_register,
                           attributed=attributed)
    if sentence is None:
        note = 'this register has no wording for %s' % chosen.concept
        _record_silence(speaker, hearer_id, 'nothing_sayable', note, now)
        return Transmission(speaker=speaker_id, hearer=hearer_id, channel=link.channel.name,
                            outcome='nothing_sayable', trust=trust, said=chosen, note=note)

    hops = chosen.provenance.hops + 1
    chain = (speaker_id,) + tuple(chosen.provenance.chain)
    origin = chosen.provenance.origin
    utterance_id = _utterance_ref(speaker_id, hearer_id, sentence, hops)
    fidelity = round(link.fidelity * understanding, 4)
    heard = _speech.hear(sentence, hearer_register, names, fidelity=fidelity, seed=seed,
                         utterance_id=utterance_id)
    base = Transmission(speaker=speaker_id, hearer=hearer_id, channel=link.channel.name,
                        sentence=sentence, concept=chosen.concept, said=chosen, heard=heard,
                        trust=trust, fidelity=fidelity, intelligibility=understanding, hops=hops,
                        chain=chain, origin=origin, utterance=utterance_id,
                        registers=(speaker_register.name, hearer_register.name))
    if not heard.understood:
        base.outcome = 'not_understood'
        base.note = heard.note
        _record_misunderstanding(hearer, speaker_id, sentence, heard, now)
        return base

    upstream = 1.0 if chosen.provenance.observed else chosen.provenance.confidence
    confidence = hearsay_weight(trust, fidelity, heard.score, upstream)
    base.confidence = confidence
    claim = heard.claim()
    existing = hearer.store.claims(claim.subject, claim.predicate, claim.object)
    claim_id, _premises = receive(hearer, claim, speaker=speaker_id, channel=link.channel.name,
                                  sentence=sentence, hops=hops, chain=chain, origin=origin,
                                  confidence=confidence, now=now, utterance_id=utterance_id)
    base.claim_id = claim_id
    if existing and all(_speech.provenance_of(hearer.store, r).observed for r in existing):
        base.outcome = 'already_believed'
        base.note = 'the hearer already held this from a published record; the telling adds a teller'
    else:
        base.outcome = 'integrated'
    hearer.episodes.append(Episode(at=now, tag='told', text='"%s" (from %s)' % (sentence, speaker_id),
                                   salience=min(1.0, 0.3 + 0.5 * confidence), who=(speaker_id,),
                                   claim_id=claim_id))
    hearer.relation(speaker_id)['episodes'] += 1
    if claim.predicate in FUNCTIONAL:
        base.adjudication = adjudicate(hearer.store, claim.subject, claim.predicate, now=now,
                                       me=hearer.grounding.me)
        if base.adjudication.kept is not None and claim_id not in base.adjudication.kept.claim_ids:
            base.outcome = 'outweighed'
            base.note = base.adjudication.reason
    return base


def _utterance_ref(speaker_id, hearer_id, sentence, hops):
    payload = '%s|%s|%s|%d' % (speaker_id, hearer_id, sentence, hops)
    return 'utterance:%s' % hashlib.sha256(payload.encode()).hexdigest()[:12]


def receive(hearer, claim, *, speaker, channel='committee', sentence='', hops=1, chain=None,
            origin=None, confidence=0.5, now=None, utterance_id=None):
    """Write one heard claim into a hearer's store with ``X told me`` provenance.

    The claims *about the utterance* are told first and become the heard claim's premises, so
    ``explain`` walks speaker → channel → chain → published record id, or stops at ``nobody``.
    This is the step :func:`tell` performs once the words have been understood; it is public
    because a caller with a channel of its own (or a test) needs the same integration without
    going through the grammar. Returns ``(claim_id, premise_ids)``.
    """
    store = hearer.store
    speaker_id = str(speaker)
    chain = tuple(chain) if chain is not None else (speaker_id,)
    now = now or hearer.now or _dt.datetime.now(UTC)
    utterance_id = utterance_id or _utterance_ref(speaker_id, hearer.grounding.entity_id,
                                                  sentence or str(claim.id), hops)
    utterance = tc.Ref(utterance_id)
    heard_evidence = tc.Evidence(tc.Ref('told:%s' % speaker_id), now, locator=sentence,
                                 method='heard:%s' % channel,
                                 confidence=tc.Score(max(0.01, min(1.0, confidence)), 'hearsay'))
    facts = (
        (tc.Claim(utterance, 'said_by', tc.Ref(speaker_id))),
        (tc.Claim(utterance, 'said_on', channel)),
        (tc.Claim(utterance, 'words', sentence)),
        (tc.Claim(utterance, 'utterance_hops', int(hops))),
        (tc.Claim(utterance, 'utterance_chain', tuple(chain))),
        (tc.Claim(utterance, 'utterance_origin', origin or 'nobody')),
    )
    commit = store.apply(tc.Patch(tuple(tc.Tell(fact, (heard_evidence,)) for fact in facts), store.revision))
    premises = tuple(sorted({fact.id for fact in facts}))
    content_evidence = tc.Evidence(tc.Ref('told:%s' % speaker_id), now, locator=sentence,
                                   method='heard:%s' % channel,
                                   confidence=tc.Score(max(0.01, min(1.0, confidence)), 'hearsay'),
                                   derived_from=premises)
    content = store.apply(tc.Patch((tc.Tell(claim, (content_evidence,)),), store.revision))
    for claim_id in tuple(commit.added) + tuple(content.added):
        hearer._born.setdefault(claim_id, hearer.ticks)
    return claim.id, premises


def _record_silence(speaker, hearer_id, outcome, note, now):
    """A speaker with nothing to say records that it had nothing to say."""
    claim = tc.Claim(speaker.grounding.me, 'said_nothing', (hearer_id, outcome))
    commit = speaker.store.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
        tc.Ref('speech:silence'), now, locator=note, method='speech:non-transmission'),)),),
        speaker.store.revision))
    for claim_id in commit.added:
        speaker._born.setdefault(claim_id, speaker.ticks)
    return claim.id


def _record_misunderstanding(hearer, speaker_id, sentence, heard, now):
    """A hearer who recovered nothing records *that*, rather than quietly believing nothing."""
    claim = tc.Claim(hearer.grounding.me, 'did_not_understand', (speaker_id, heard.note or 'no reading'))
    commit = hearer.store.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
        tc.Ref('told:%s' % speaker_id), now, locator=sentence, method='heard:not-understood'),)),),
        hearer.store.revision))
    for claim_id in commit.added:
        hearer._born.setdefault(claim_id, hearer.ticks)
    return claim.id


# --------------------------------------------------------------------------- cascades


@dataclass
class Cascade(object):
    """A claim moving down a chain of agents, and what each one ended up believing."""

    chain: tuple = ()
    steps: tuple = ()
    subject: str = ''
    predicate: str = ''
    published: object = None
    beliefs: tuple = ()

    def to_json(self):
        return {'chain': list(self.chain), 'subject': self.subject, 'predicate': self.predicate,
                'published': self.published, 'steps': [s.to_json() for s in self.steps],
                'beliefs': [dict(b) for b in self.beliefs]}


def propagate(people, *, index=None, subject=None, predicate='measure_stage', seed=0, now=None,
              attribute=None, scan=SCAN):
    """Pass a belief down a chain of agents, one published channel at a time.

    ``people`` is the chain in order: the first is the one that read the record, and each
    subsequent agent hears only from the one before it. Returns a :class:`Cascade` holding every
    step, what each agent ended up believing, and what the record actually says.
    """
    index = index or people[0].grounding.index
    steps = []
    for speaker, hearer in zip(people, people[1:]):
        def pick(candidates, subject=subject, predicate=predicate):
            if subject is None:
                return candidates[0] if candidates else None
            for candidate in candidates:
                if candidate.subject == subject and _matches(candidate, predicate):
                    return candidate
            return None
        steps.append(tell(speaker, hearer, index=index, choose=pick, seed=seed, now=now,
                          attribute=attribute, scan=scan))
    published = None
    if subject is not None and predicate == 'measure_stage':
        published = advance_state(index.entity(subject))
    beliefs = tuple(believed(person, subject, predicate) for person in people) if subject else ()
    return Cascade(chain=tuple(p.grounding.entity_id for p in people), steps=tuple(steps),
                   subject=subject or '', predicate=predicate, published=published, beliefs=beliefs)


def _matches(candidate, predicate):
    if predicate == 'measure_stage':
        return candidate.concept.startswith('stage_')
    return candidate.concept == predicate


def believed(person, subject, predicate):
    """What one agent holds on ``(subject, predicate)`` right now, with where it got it."""
    out = {'agent': person.grounding.entity_id, 'holds': [], 'unknown': True}
    for record in person.store.claims(tc.Ref(subject), predicate):
        supports = _speech.supports_of(person.store, record)
        provenance = _speech.provenance_of(person.store, record)
        out['holds'].append({'object': str(getattr(record.claim.object, 'id', record.claim.object)),
                             'claim': record.id, 'supports': [s.to_json() for s in supports],
                             **provenance.to_json()})
        out['unknown'] = False
    out['holds'].sort(key=lambda h: (h['hops'], h['object']))
    return out


def trace(person, claim_id, *, depth=8):
    """Why this agent believes it: the chain of tellers, then the published record or ``nobody``."""
    from tensacode import cognition as _cognition
    record = person.store._claims.get(claim_id)
    if record is None:
        return {'claim': claim_id, 'lines': ['(forgotten %s)' % claim_id], 'chain': (), 'origin': None}
    provenance = _speech.provenance_of(person.store, record)
    return {'claim': claim_id,
            'belief': '%s %s %s' % (record.claim.subject, record.claim.predicate,
                                    getattr(record.claim.object, 'id', record.claim.object)),
            'kind': provenance.kind, 'chain': list(provenance.chain), 'hops': provenance.hops,
            'origin': provenance.origin, 'confidence': round(provenance.confidence, 4),
            'lines': _cognition.explain(person.store, claim_id, depth=depth)}


def divergence(person, subject, predicate, *, index=None):
    """What this agent holds against what the record publishes. Reported, never corrected."""
    index = index or person.grounding.index
    record = index.entity(subject) if index is not None else None
    published = advance_state(record) if predicate == 'measure_stage' else None
    held = believed(person, subject, predicate)
    objects = [h['object'] for h in held['holds']]
    return {'agent': person.grounding.entity_id, 'subject': subject, 'predicate': predicate,
            'published': published, 'believed': objects,
            'diverges': bool(objects) and published is not None and published not in objects,
            'silent': not objects,
            'distance': _ladder_distance(published, objects[0]) if objects and published else None,
            'provenance': [{k: h[k] for k in ('kind', 'chain', 'hops', 'origin', 'confidence')}
                           for h in held['holds']]}


def _ladder_distance(published, believed_value):
    """How many steps up or down :data:`~worldmodel.agents.speech.LADDER` the belief has moved."""
    ladder = _speech.LADDER
    if published not in ladder or believed_value not in ladder:
        return None
    return ladder.index(believed_value) - ladder.index(published)


# --------------------------------------------------------------------------- inspection


def report(cascade):
    """A compact, printable account of one cascade: the chain, the drift and the outcomes."""
    lines = ['%s %s: published %r' % (cascade.subject, cascade.predicate, cascade.published)]
    for step in cascade.steps:
        lines.append('  %s -> %s over %s: %s' % (step.speaker, step.hearer, step.channel, step.outcome))
        if step.sentence:
            lines.append('      "%s"  (trust %.2f, fidelity %.2f, hop %d)'
                         % (step.sentence, step.trust, step.fidelity, step.hops))
        if step.heard is not None and step.heard.note:
            lines.append('      note: %s' % step.heard.note)
    for belief in cascade.beliefs:
        held = ', '.join('%s (%s, %d hops, conf %.2f)' % (h['object'], h['kind'], h['hops'], h['confidence'])
                         for h in belief['holds']) or 'nothing'
        lines.append('  %s believes: %s' % (belief['agent'], held))
    return lines
