"""Speech acts between grounded persons: a belief becomes English, and English becomes a belief.

Three modules make up belief transmission and the split is deliberate:

| Module | What it knows | Needs tensorcode |
| --- | --- | --- |
| ``agents/lexicon.py`` | registers, templates, spoken tokens, frame rules, the stage slip | no |
| ``agents/speech.py`` | claims: what an agent can say, what it resolves a name to, what it heard | yes |
| ``agents/transmission.py`` | channels, trust, provenance, conflict, cascades | yes |

This module is the claim-facing half of the surface. :mod:`worldmodel.agents.lexicon` holds the
declared tables and the pure functions over sentences — including the three measured losses in
understanding, which are documented there. What is added here is everything that touches an
agent:

* :func:`register_for` — which register an agent speaks, read from the ``holds_role`` edge that
  covers the moment, because the register has to be a matter of record and not of hash order;
* :class:`Names` — a **hearer-bounded** token resolver. ``"S1241"`` is a token, not an id. A
  hearer resolves it only against entities it can reach on its own; anything else lands on
  ``measure:S1241``, a *different* ``Ref`` from the published ``congress:bill:119-s-1241``, so a
  half-understood name becomes visible divergence rather than a silent success;
* :func:`hear` — :func:`lexicon.parse` plus name resolution plus the stage slip, as a
  :class:`Heard` that can become a claim;
* :class:`Provenance` / :func:`supports_of` — reading an evidence list back as *published*,
  *hearsay* (with its chain of tellers and the record it bottoms out in, or ``nobody``),
  *derived* or *unattributed*;
* :func:`sayable` — what an agent believes, can word, is willing to repeat, and that is relevant
  to the channel. Nothing else is said.

Nothing here is fitted to an outcome and nothing here is ``validated``.
"""
from dataclasses import dataclass, field

from . import load_tensorcode
from .grounding import _parse_time
from .lexicon import (  # noqa: F401  - re-exported: this module is the public face of both
    ALL_SKELETONS, BASE_FORMS, BASE_REGISTER, CANONICAL_SKELETONS, CHAMBER_FORMS, CONCEPTS,
    CONCEPT_WEIGHT, LADDER, LOBBY_FORMS, LOBBY_REGISTER, OBJECT_KIND, OBJECT_ROLES, PARTY_FORMS,
    PREDICATE_CONCEPT, RELAY_FLOOR, RELAY_LOSSY_CONCEPTS, SLIP, STAGE_CONCEPT, SUBJECT_KIND,
    SUBJECT_ROLES, FRAME_RULES, MEASURE_ID, Reading, Register, concept_of, intelligibility,
    is_entity_id, kind_of_token, match_concept, one_word, parse, register_from, say, slip,
    spoken_token)

tc = load_tensorcode()


# --------------------------------------------------------------------------- the register


def published_role(index, grounding, *, at=None):
    """``(chamber, party, record_id)`` from the ``holds_role`` edge covering ``at``.

    The agent's *store* is not good enough for this. Seeding writes one ``chamber`` claim per role
    record it read, a legislator who moved from the House to the Senate has two, and
    ``Store.claims`` orders by claim id — so reading the register off the store picks a chamber by
    hash. The published edge carries ``valid_from``/``valid_to``, so which role actually holds at
    ``at`` is a matter of record. This reads that, bounded to the agent's own degree.
    """
    if index is None:
        return None, None, None
    rows = index.edges(grounding.cluster, 'holds_role', limit=40)
    best, best_from = None, None
    for row in rows:
        valid_from, valid_to = _parse_time(row['valid_from']), _parse_time(row['valid_to'])
        if at is not None and ((valid_from is not None and valid_from > at)
                               or (valid_to is not None and valid_to <= at)):
            continue
        if best is None or (valid_from is not None and (best_from is None or valid_from > best_from)):
            best, best_from = row, valid_from
    if best is None:
        return None, None, None
    record = index.entity(best['object'])
    if record is None:
        return None, None, None
    attributes = record.get('attributes') or {}
    role_type = attributes.get('role_type')
    chamber = ('senate' if role_type == 'sen' else 'house' if role_type == 'rep' else None)
    party = (attributes.get('source_term') or {}).get('party')
    return chamber, (str(party) if party else None), record['_provenance']['record_id']


def register_for(person, *, name=None, at=None, index=None):
    """The register an agent speaks, from the chamber and party the record gives it.

    Read from the published role covering ``at`` when an index is open (:func:`published_role`),
    and from the agent's own seeded claims otherwise. An agent with no published role at all gets
    the base register, and ``Register.basis`` says which records chose whichever it got.
    """
    store, me = person.store, person.grounding.me
    index = index if index is not None else person.grounding.index
    at = at or person.now
    chamber, party, record_id = published_role(index, person.grounding, at=at)
    basis = [record_id] if record_id else []
    if chamber is None:
        for record in store.claims(me, 'chamber'):
            chamber = str(record.claim.object).lower()
            basis.append(record.id)
            break
    if party is None:
        for record in store.claims(me, 'party'):
            party = str(record.claim.object)
            basis.append(record.id)
            break
    return register_from(chamber, party, name=name, basis=basis)


# --------------------------------------------------------------------------- names


class Names(object):
    """Token-to-entity resolution bounded by what one hearer can actually reach.

    A hearer resolves a spoken token against the entities in its own store and whatever the caller
    adds (``worldmodel.agents.transmission.names_for`` adds the docket and the roster of the
    committees it is published as sitting on). Anything else resolves to a deliberately *different* Ref —
    ``measure:S1241`` rather than ``congress:bill:119-s-1241`` — and ``unresolved`` records the
    token, so a half-understood name shows up as divergence rather than a silent success.
    """

    KINDS = {'measure': ('congress:bill:',), 'person': ('bioguide:', 'icpsr:'),
             'committee': ('congress:committee:',)}

    def __init__(self, index=None):
        self.index = index
        self.to_token = {}
        self.to_id = {}
        self.unresolved = []

    def learn(self, entity_id, label=None):
        entity_id = str(entity_id)
        if entity_id in self.to_token:
            return self.to_token[entity_id]
        # A measure's spoken form comes out of its id, so learning one costs nothing. Only a person
        # or a committee needs its published label, and only then is the index read at all.
        if label is None and self.index is not None and not MEASURE_ID.match(entity_id):
            record = self.index.entity(entity_id)
            label = (record or {}).get('label')
        token = spoken_token(entity_id, label)
        self.to_token[entity_id] = token
        self.to_id.setdefault(token.lower(), entity_id)
        return token

    def learn_all(self, entity_ids):
        for entity_id in entity_ids:
            if is_entity_id(entity_id):
                self.learn(entity_id)
        return self

    @classmethod
    def of(cls, person, *, index=None, extra=()):
        """Build the resolver a person hears with, from its own store plus ``extra`` ids."""
        names = cls(index if index is not None else person.grounding.index)
        ids = set()
        for record in person.store.claims():
            for value in (record.claim.subject, record.claim.object):
                candidate = getattr(value, 'id', value)
                if is_entity_id(candidate):
                    ids.add(str(candidate))
        names.learn_all(sorted(ids) + [str(entity_id) for entity_id in extra])
        return names

    def token(self, entity_id):
        return self.learn(entity_id)

    def resolve(self, token, *, expect=None):
        """``(ref, resolved)``. An unresolved token becomes an explicitly degraded Ref."""
        if not token:
            return None, False
        key = str(token).lower()
        if key in self.to_id:
            entity_id = self.to_id[key]
            if expect is None or any(entity_id.startswith(prefix) for prefix in self.KINDS.get(expect, ())):
                return tc.Ref(entity_id), True
        self.unresolved.append(str(token))
        kind = expect or kind_of_token(token)
        return tc.Ref('%s:%s' % (kind, one_word(token) or 'unknown')), False


# --------------------------------------------------------------------------- hearing


@dataclass
class Heard(object):
    """What a hearer recovered, and what it did not. Failure is an outcome, not an error."""

    concept: str = None
    subject: object = None      # a Ref
    object: object = None
    resolved: bool = True       # did every name resolve to a published entity?
    slipped: bool = False       # did a low-fidelity channel move the stage up the ladder?
    attributed: str = None
    score: float = 0.0
    readings: int = 0
    foreign: tuple = ()
    note: str = ''
    via: str = 'none'           # 'general' | 'none'

    @property
    def understood(self):
        return self.concept is not None

    def claim(self):
        """The claim this reading asserts, or ``None`` when nothing was recovered."""
        if not self.understood:
            return None
        if self.concept.startswith('stage_'):
            return tc.Claim(self.subject, 'measure_stage', self.concept[len('stage_'):])
        return tc.Claim(self.subject, PREDICATE_CONCEPT[self.concept], self.object)

    def to_json(self):
        return {'concept': self.concept, 'subject': str(self.subject) if self.subject else None,
                'object': str(self.object) if self.object is not None else None,
                'resolved': self.resolved, 'slipped': self.slipped, 'attributed': self.attributed,
                'score': round(self.score, 3), 'readings': self.readings,
                'foreign': list(self.foreign), 'note': self.note, 'via': self.via}


def hear(sentence, register, names, *, fidelity=1.0, seed=0, utterance_id=''):
    """Understand one sentence in ``register`` and resolve its names against ``names``.

    ``fidelity`` is the channel's, already multiplied by the intelligibility of the two registers.
    Below one it lets a stage slip one rung up the ladder (:func:`lexicon.slip`), deterministically
    in ``(seed, utterance_id)``, so two runs of the same exchange mishear it the same way.
    """
    reading = parse(sentence, register)
    if not reading.understood:
        return Heard(readings=reading.readings, foreign=reading.foreign, note=reading.note,
                     attributed=reading.attributed, via=reading.via)
    concept = reading.concept
    subject, resolved = names.resolve(reading.subject_token, expect=SUBJECT_KIND[concept])
    obj, object_resolved = None, True
    if concept in OBJECT_ROLES:
        want = OBJECT_KIND[concept]
        if want is None:
            obj = one_word(reading.object_token) or None
        else:
            obj, object_resolved = names.resolve(reading.object_token, expect=want)
    concept, slipped = slip(concept, fidelity, seed=seed, utterance=utterance_id)
    notes = []
    if slipped:
        notes.append('misheard how far it had got')
    if reading.foreign:
        notes.append('unfamiliar wording: ' + ', '.join(reading.foreign))
    if not (resolved and object_resolved):
        notes.append('name not resolved to a published entity')
    score = max(0.05, 1.0 - 0.15 * len(reading.foreign) - 0.2 * (0 if resolved and object_resolved else 1)
                - 0.1 * max(0, reading.readings - 1))
    return Heard(concept=concept, subject=subject, object=obj, resolved=resolved and object_resolved,
                 slipped=slipped, attributed=reading.attributed, score=round(score, 3),
                 readings=reading.readings, foreign=reading.foreign, note='; '.join(notes),
                 via=reading.via)


# --------------------------------------------------------------------------- provenance


@dataclass
class Provenance(object):
    """Where a belief came from: a published record, a chain of tellers, or nobody."""

    kind: str = 'unattributed'  # 'published' | 'hearsay' | 'derived' | 'unattributed'
    origin: str = None          # the published record id the chain bottoms out in, or None
    chain: tuple = ()           # tellers, nearest first
    hops: int = 0
    confidence: float = 1.0
    utterances: tuple = ()      # the utterance claim ids in this store, when it is hearsay

    @property
    def observed(self):
        return self.kind == 'published'

    def to_json(self):
        return {'kind': self.kind, 'origin': self.origin, 'chain': list(self.chain), 'hops': self.hops,
                'confidence': round(self.confidence, 3)}


#: Ranking of the evidence kinds. :func:`provenance_of` reports the strongest one a claim has.
KIND_ORDER = ('published', 'hearsay', 'derived', 'unattributed')


def supports_of(store, record):
    """**Every** line of support on one claim, one :class:`Provenance` per piece of evidence.

    A claim's identity in the substrate is its content, so two people telling you the same thing
    land on *one* ``ClaimRecord`` with *two* pieces of evidence. Anything that counts tellers —
    corroboration above all — has to read the evidence list and not the record.
    """
    out = []
    for evidence in record.evidence:
        method = str(evidence.method or '')
        if method.startswith('published') or method == 'perceive':
            locator = str(evidence.locator or '')
            out.append(Provenance(kind='published', origin=locator.split(' in ')[0] or None, hops=0))
        elif method.startswith('heard'):
            out.append(_hearsay_provenance(store, evidence))
        elif evidence.derived_from:
            out.append(Provenance(kind='derived', hops=0))
        else:
            out.append(Provenance(kind='unattributed', hops=0))
    return tuple(out)


def _hearsay_provenance(store, evidence):
    """Read the chain back off the claims the heard belief was derived from."""
    chain, origin, hops = (), None, 1
    for claim_id in evidence.derived_from:
        held = store._claims.get(claim_id)
        if held is None or held.retracted:
            continue
        predicate, obj = held.claim.predicate, held.claim.object
        if predicate == 'utterance_chain':
            chain = tuple(str(x) for x in (obj if isinstance(obj, (tuple, list)) else (obj,)))
        elif predicate == 'utterance_origin':
            origin = None if str(obj) == 'nobody' else str(obj)
        elif predicate == 'utterance_hops':
            hops = int(obj)
    score = evidence.confidence.value if evidence.confidence is not None else 0.5
    return Provenance(kind='hearsay', origin=origin, chain=chain, hops=hops, confidence=float(score),
                      utterances=tuple(evidence.derived_from))


def provenance_of(store, record):
    """The strongest line of support a claim has: published beats heard beats derived."""
    supports = supports_of(store, record)
    for kind in KIND_ORDER:
        of_kind = [p for p in supports if p.kind == kind]
        if of_kind:
            return max(of_kind, key=lambda p: p.confidence)
    return Provenance(kind='unattributed', hops=0)


# --------------------------------------------------------------------------- what is sayable


@dataclass
class Sayable(object):
    """A claim a speaker could put into words now, with why it would and where it got it."""

    concept: str
    subject: str
    object: object
    claim_id: str
    weight: float
    provenance: Provenance = field(default_factory=Provenance)

    def to_json(self):
        return {'concept': self.concept, 'subject': self.subject,
                'object': str(self.object) if self.object is not None else None,
                'weight': round(self.weight, 3), 'provenance': self.provenance.to_json()}


def sayable(person, *, about=(), relay_floor=RELAY_FLOOR):
    """What this agent believes, can word, will repeat, and that is relevant — nothing else.

    ``about`` is the set of published entity ids the channel is about (a committee both sit on and
    what is before it, the measures both put their names to). A claim touching none of them is not
    this hearer's business and is left unsaid; an empty ``about`` means no relevance filter. A
    rumour the agent holds below ``relay_floor`` is not passed on at all. Deterministically ordered.
    """
    about = {str(x) for x in about}
    store = person.store
    out = []
    for record in store.claims():
        claim = record.claim
        if claim.scope is not None:
            continue  # the percept snapshot and the theory-of-mind scopes are not things one says
        concept = concept_of(claim.predicate, getattr(claim.object, 'id', claim.object))
        if concept is None:
            continue
        # The subject is whatever the claim says, including somebody else: gossip is a thing an
        # agent can pass on. Seeding only ever writes ``serves_on``/``cosponsored``/``sponsored``
        # about the agent itself, so a third-party one in the store arrived by being told.
        subject = str(getattr(claim.subject, 'id', claim.subject))
        obj = getattr(claim.object, 'id', claim.object)
        if about and not ({subject, str(obj)} & about):
            continue
        provenance = provenance_of(store, record)
        if provenance.kind == 'hearsay' and provenance.confidence < relay_floor:
            continue
        # How much a speaker wants to repeat something is not how much it believes it: a hop-one
        # rumour is worth bringing up at a confidence of 0.4. Chain-length decay belongs in the
        # *confidence*, which is where it is measured.
        weight = CONCEPT_WEIGHT.get(concept, 0.2) * (1.0 if provenance.observed
                                                     else 0.4 + 0.6 * provenance.confidence)
        out.append(Sayable(concept=concept, subject=subject, object=obj, claim_id=record.id,
                           weight=round(weight, 5), provenance=provenance))
    return sorted(out, key=lambda s: (-s.weight, s.concept, s.subject, str(s.object)))
