"""The words: registers, templates, spoken tokens, and the frame rules that read them back.

This module is to :mod:`worldmodel.agents.speech` what :mod:`worldmodel.agents.affect` is to
``person``: the declared tables and the pure functions over them, with **no dependency on the
cognitive substrate at all**. It knows about concepts, sentences and tokens; it does not know
what a ``Claim`` is. That is why the tables can be inspected and tested in any environment,
including one without the optional ``agents`` extra installed.

``tensacode.language`` is imported lazily inside :func:`parse`, so importing this module never
needs the substrate either — only actually parsing a sentence does.

## The two directions

**Production is local and templated.** :func:`say` puts one concept into one sentence from
:data:`BASE_FORMS` as overridden by the speaker's register. No model is called, and the general
generator in ``tensacode.language`` is deliberately not used: the civ sim measured its stemming
and tense faults (*"snow cames"*, ``owes`` stemmed to ``ow``) and kept production local for
exactly that reason. The note survives the port because the fault does.

**Understanding is general and lossy.** :func:`parse` runs the chart parser in
``tensacode.language`` over the canonicalized sentence and maps the resulting ``Frame`` back onto
one of :data:`CONCEPTS`. It is lossy in ways that were measured rather than invented:

1. *reported speech drops adjuncts.* ``"Warner told me that Kaine sits on Judiciary"`` parses as
   ``tell(content={sit(subject=Kaine)}, location=Judiciary)``: the location attaches to the
   **outer** frame, so the inner clause no longer says which committee, and nothing is invented
   in its place. Measured over every concept in all four registers, ``serves_on`` is the one that
   does not survive attribution (:data:`RELAY_LOSSY_CONCEPTS`); a stage relayed as a passive
   (*"was referred to committee"*) keeps its adjunct and survives.
2. *register mismatch.* A hearer un-substitutes only the wordings its own register uses
   (:meth:`Register.canonicalize`), and :data:`FRAME_RULES` maps **only the canonical verbs**. A
   wording built on a different verb therefore reaches the rules as an unmapped stem and the
   sentence is not understood. Registers split on published facts — the chamber the agent holds
   office in and the party the record gives it — while the wordings themselves are authored.
   The consequence is legible: procedural news crosses the aisle (the procedural vocabulary is
   chamber-bound) and evaluative news crosses the chambers (that vocabulary is party-bound).
3. *the subject has to be the right kind of thing.* A measure cannot hold a committee seat, so
   *"S1241 sits in committee"* heard by a register whose ``sits on`` means a seat is refused
   rather than turned into nonsense.

On top of that, :func:`slip` lets a stage move **one rung up** :data:`LADDER` when the channel
loses fidelity — rumour exaggerates, an authored asymmetry with the same standing as the affect
module's asymmetric transition costs. It is seeded, so it is reproducible.

Nothing here is fitted to an outcome and nothing here is ``validated``.
"""
import random
import re
from dataclasses import dataclass

#: Everything a grounded legislator can put into words on this catalog. One concept is one
#: sentence shape; a claim whose predicate maps to no concept is simply not sayable, and
#: :func:`concept_of` returns ``None`` rather than improvising a wording for it.
CONCEPTS = ('stage_enacted', 'stage_passed_chamber', 'stage_reported', 'stage_referred',
            'stage_pending', 'stage_failed', 'cosponsored', 'sponsored', 'serves_on', 'policy_area')

#: Which agent predicate carries which concept. ``measure_stage`` fans out over the stages
#: because a stage is what the sentence is *about*, not an argument of it.
STAGE_CONCEPT = {'enacted': 'stage_enacted', 'passed_chamber': 'stage_passed_chamber',
                 'reported': 'stage_reported', 'referred': 'stage_referred',
                 'pending': 'stage_pending', 'failed': 'stage_failed'}
PREDICATE_CONCEPT = {'cosponsored': 'cosponsored', 'sponsored': 'sponsored',
                     'serves_on': 'serves_on', 'policy_area': 'policy_area'}

#: The stage ladder, least to most advanced. :func:`slip` walks *up* it.
LADDER = ('pending', 'referred', 'reported', 'passed_chamber', 'enacted')
#: How a stage is misheard when the channel loses fidelity. Declared, not fitted: a rumour about
#: a measure exaggerates its progress, it does not invent a setback. ``failed`` is terminal in
#: both directions, so nothing slips out of it.
SLIP = {'pending': 'referred', 'referred': 'reported', 'reported': 'passed_chamber',
        'passed_chamber': 'enacted', 'enacted': 'enacted', 'failed': 'failed'}

#: Concepts whose meaning does not survive attributed relay, because the parser attaches the
#: adjunct that carries them to the outer ``tell`` frame rather than to the content clause.
#: Measured across all four registers, not assumed: naming your source costs you the committee.
RELAY_LOSSY_CONCEPTS = ('serves_on',)

#: How much a speaker wants to bring one concept up. Authored, in the civ sim's ``_sayable`` shape.
CONCEPT_WEIGHT = {'stage_enacted': 0.90, 'stage_failed': 0.80, 'stage_passed_chamber': 0.75,
                  'stage_reported': 0.60, 'stage_referred': 0.45, 'stage_pending': 0.30,
                  'cosponsored': 0.50, 'sponsored': 0.70, 'serves_on': 0.40, 'policy_area': 0.25}
#: A rumour this weak is not passed on at all. Declared.
RELAY_FLOOR = 0.12


def concept_of(predicate, obj):
    """The concept a claim can be said with, or ``None`` when it cannot be said at all."""
    if predicate == 'measure_stage':
        return STAGE_CONCEPT.get(str(obj))
    return PREDICATE_CONCEPT.get(predicate)


# --------------------------------------------------------------------------- registers


#: The canonical wording of each concept: the phrasing :data:`FRAME_RULES` maps directly.
#: Every register's own forms are un-substituted back to these before parsing.
BASE_FORMS = {
    'stage_enacted': '{subject} became law',
    'stage_passed_chamber': '{subject} passed the floor',
    'stage_reported': '{subject} was reported by committee',
    'stage_referred': '{subject} was referred to committee',
    'stage_pending': '{subject} is waiting',
    'stage_failed': '{subject} failed',
    'cosponsored': '{subject} cosponsored {object}',
    'sponsored': '{subject} introduced {object}',
    'serves_on': '{subject} sits on {object}',
    'policy_area': '{subject} concerns {object}',
}

#: The procedural half of a register, keyed by the chamber the agent's published role puts it in.
#: Which register an agent speaks is a matter of record (``holds_role`` carries the ``role_type``);
#: the wordings are authored, and say so.
CHAMBER_FORMS = {
    'senate': {'stage_referred': '{subject} was referred to committee',
               'stage_reported': '{subject} was reported by committee',
               'sponsored': '{subject} introduced {object}'},
    'house': {'stage_referred': '{subject} went to committee',
              'stage_reported': '{subject} cleared committee',
              'sponsored': '{subject} filed {object}'},
}
#: The evaluative half, keyed by the published party. Same standing: the split is published, the
#: wordings are authored.
PARTY_FORMS = {
    'Democrat': {'stage_enacted': '{subject} became law', 'cosponsored': '{subject} cosponsored {object}',
                 'serves_on': '{subject} sits on {object}'},
    'Republican': {'stage_enacted': '{subject} was signed into law', 'cosponsored': '{subject} signed onto {object}',
                   'serves_on': '{subject} serves on {object}'},
}
#: A registrant speaking to a chamber is not a legislator and has neither a chamber nor a party.
LOBBY_FORMS = {'stage_referred': '{subject} sits in committee', 'stage_reported': '{subject} cleared committee',
               'stage_passed_chamber': '{subject} cleared the floor', 'stage_enacted': '{subject} is law',
               'cosponsored': '{subject} joined {object}'}


@dataclass(frozen=True)
class Register(object):
    """One speech register: a concept-to-surface map, plus the records that chose it.

    ``basis`` names the published record ids (or store claim ids) behind the choice, so "why does
    this agent talk like that" answers the same way everything else in this layer does.
    """

    name: str
    forms: tuple = ()          # ((concept, phrase), ...), sorted, so a Register is hashable
    chamber: str = None
    party: str = None
    basis: tuple = ()

    @property
    def table(self):
        return dict(self.forms)

    def phrase(self, concept):
        return self.table.get(concept) or BASE_FORMS.get(concept)

    def canonicalize(self, sentence):
        """Rewrite this register's own wordings back to the canonical ones.

        A wording from a *foreign* register is left exactly as it stands: the chart parser takes
        its verb as an ordinary open-class word, :data:`FRAME_RULES` does not recognise it, and
        the sentence is not understood. That is what makes register divergence cost something
        instead of being decoration.

        Returns ``(text, foreign_phrases)``; ``foreign_phrases`` are wordings in the original that
        are neither this register's nor canonical.
        """
        mine = {}
        for concept in CONCEPTS:
            skeleton = _skeleton(self.phrase(concept))
            if skeleton:
                mine.setdefault(skeleton, concept)
        text = sentence
        for skeleton in sorted(mine, key=len, reverse=True):
            canonical = _skeleton(BASE_FORMS.get(mine[skeleton], ''))
            if canonical and canonical != skeleton and skeleton in text:
                text = text.replace(skeleton, canonical)
        foreign = tuple(s for s in ALL_SKELETONS
                        if s in sentence and s not in mine and s not in CANONICAL_SKELETONS)
        return text, foreign


def _skeleton(phrase):
    """The register-bearing part of a template: everything that is not a slot."""
    if not phrase:
        return ''
    body = phrase.replace('{subject}', '').replace('{object}', '')
    return ' '.join(body.split())


def _all_skeletons():
    out = set()
    for table in (BASE_FORMS, LOBBY_FORMS, *CHAMBER_FORMS.values(), *PARTY_FORMS.values()):
        for phrase in table.values():
            out.add(_skeleton(phrase))
    return tuple(sorted((s for s in out if s), key=len, reverse=True))


#: Every wording any register uses, longest first (so containment tests are unambiguous), and the
#: subset of them that is canonical and therefore always understood.
ALL_SKELETONS = _all_skeletons()
CANONICAL_SKELETONS = frozenset(_skeleton(phrase) for phrase in BASE_FORMS.values())


def register_from(chamber, party, *, name=None, basis=()):
    """Build the register for a published ``(chamber, party)``. Either may be ``None``."""
    forms = dict(BASE_FORMS)
    forms.update(CHAMBER_FORMS.get(chamber, {}))
    forms.update(PARTY_FORMS.get(party, {}))
    label = name or '%s/%s' % (chamber or 'unknown', (party[:1] if party else 'unknown'))
    return Register(name=label, forms=tuple(sorted(forms.items())), chamber=chamber, party=party,
                    basis=tuple(basis))


#: The register a lobbying registrant speaks, and the fallback for an agent with no published role.
LOBBY_REGISTER = Register(name='lobby', forms=tuple(sorted({**BASE_FORMS, **LOBBY_FORMS}.items())))
BASE_REGISTER = Register(name='base', forms=tuple(sorted(BASE_FORMS.items())))


def intelligibility(speaker, hearer):
    """The share of concepts the two registers word the same way, in ``[0, 1]``.

    This is the civ sim's ``intelligibility`` with dialects replaced by registers: how much of
    what one says the other can un-substitute. It multiplies the channel's fidelity.
    """
    same = sum(1 for concept in CONCEPTS
               if _skeleton(speaker.phrase(concept)) == _skeleton(hearer.phrase(concept)))
    return round(same / float(len(CONCEPTS)), 4)


# --------------------------------------------------------------------------- spoken tokens


MEASURE_ID = re.compile(r'^congress:bill:(\d+)-([a-z]+)-(\d+)$')
MEASURE_TOKEN = re.compile(r'^([A-Z]+)(\d+)$')
ENTITY_ID = re.compile(r'^[a-z][a-z0-9_]*(:[A-Za-z0-9_.\-]+)+$')


def is_entity_id(value):
    return isinstance(value, str) and bool(ENTITY_ID.match(value))


def one_word(text):
    return re.sub(r'[^A-Za-z0-9]', '', str(text or ''))


def spoken_token(entity_id, label=None):
    """The single word a person would use out loud for a published entity.

    One word, deliberately: the chart parser splits ``"HR 1234"`` into a name and a number and
    hands back ``recipient=HR, object=1234``, losing the measure. Measured, so the surface form
    is ``HR1234``.
    """
    entity_id = str(entity_id)
    match = MEASURE_ID.match(entity_id)
    if match:
        return '%s%s' % (match.group(2).upper(), match.group(3))
    if entity_id.startswith('bioguide:') or entity_id.startswith('icpsr:'):
        # People are named by family name out loud. ``congress_legislators`` publishes labels both
        # ways round - "Hirono, Mazie K." and "Mazie K. Hirono" - so the comma decides which end.
        text = str(label or '').strip()
        family = text.split(',')[0] if ',' in text else (text.split()[-1] if text else '')
        return one_word(family) or entity_id.split(':')[-1]
    if entity_id.startswith('congress:committee:'):
        head = re.split(r'[,\s]', str(label or '').strip())[0]
        return one_word(head) or entity_id.split(':')[-1]
    return one_word(label) or one_word(entity_id.split(':')[-1]) or 'Something'


def kind_of_token(token):
    """What kind of thing a spoken token names, from its shape alone."""
    return 'measure' if MEASURE_TOKEN.match(str(token or '').upper()) else 'person'


# --------------------------------------------------------------------------- saying


def say(concept, subject_token, object_token, register, *, attributed=None):
    """One concept as one sentence in ``register``. Templates only; no model, no generator."""
    phrase = register.phrase(concept)
    if phrase is None:
        return None
    body = ' '.join(phrase.format(subject=subject_token, object=object_token or '').split())
    if attributed:
        # "X told me that ..." is the only reported-speech frame whose content clause the chart
        # parser keeps; "X said ..." drops it. Measured, so this is the form used. The embedded
        # clause keeps its own capitalization: a token like ``S1241`` is a name, and lower-casing
        # it turns it into a word.
        return '%s told me that %s' % (attributed, body)
    return body[0].upper() + body[1:]


# --------------------------------------------------------------------------- hearing


#: How a parsed frame maps onto a concept: ``(stems, required word among the adjuncts, concept)``,
#: in order. **Only the canonical verbs are here**, which is the whole of the register mechanism:
#: a register wording enactment as *"was signed into law"* is understood by a register that says
#: the same, and is opaque to one that says *"became law"*, because ``sign`` is not a canonical
#: stem and nothing maps it. Where a required word disambiguates (``referr`` to *committee*,
#: ``pass`` on the *floor*), relay that dropped the adjunct matches no rule at all and the
#: sentence is honestly not understood.
FRAME_RULES = (
    (('becom', 'became'), 'law', 'stage_enacted'),
    (('pass',), 'floor', 'stage_passed_chamber'),
    (('report',), 'committee', 'stage_reported'),
    (('referr', 'refer'), 'committee', 'stage_referred'),
    (('wait',), None, 'stage_pending'),
    (('fail',), None, 'stage_failed'),
    (('cosponsor',), None, 'cosponsored'),
    (('introduc',), None, 'sponsored'),
    (('sit',), None, 'serves_on'),
    (('concern',), None, 'policy_area'),
)
#: What kind of thing each concept's subject has to be.
SUBJECT_KIND = {'stage_enacted': 'measure', 'stage_passed_chamber': 'measure', 'stage_reported': 'measure',
                'stage_referred': 'measure', 'stage_pending': 'measure', 'stage_failed': 'measure',
                'policy_area': 'measure', 'cosponsored': 'person', 'sponsored': 'person',
                'serves_on': 'person'}
#: Which role of the frame carries the concept's object, and what kind of thing that object is.
OBJECT_ROLES = {'cosponsored': ('object', 'destination'), 'sponsored': ('object',),
                'serves_on': ('location', 'object'), 'policy_area': ('object', 'topic')}
OBJECT_KIND = {'cosponsored': 'measure', 'sponsored': 'measure', 'serves_on': 'committee',
               'policy_area': None}
SUBJECT_ROLES = ('subject', 'agent', 'theme')
#: The reported-speech verbs whose content clause is looked into.
REPORTING_VERBS = ('tell', 'told', 'say')


@dataclass(frozen=True)
class Reading(object):
    """What a sentence was understood to mean, in tokens. No entity ids and no claims yet.

    ``concept`` is ``None`` when nothing was recovered, which is a normal outcome: ``note`` says
    which of the three losses happened.
    """

    concept: str = None
    subject_token: str = ''
    object_token: str = None
    attributed: str = None
    foreign: tuple = ()
    readings: int = 0
    note: str = ''
    via: str = 'none'          # 'general' | 'none'

    @property
    def understood(self):
        return self.concept is not None


def _text(value):
    return str(getattr(value, 'text', value) or '').strip()


def _subject_role(frame):
    """Which role holds the thing the sentence is about.

    A passive puts it in ``subject`` and the doer in ``agent``, so only the role actually used as
    the subject is excluded from the adjunct words.
    """
    return next((role for role in SUBJECT_ROLES if frame.roles.get(role) is not None), None)


def _adjunct_words(frame, subject_role):
    words = set()
    for role, value in frame.roles.items():
        if role == subject_role:
            continue
        words.update(word.lower() for word in _text(value).split())
    return words


def match_concept(frame):
    """The concept a parsed frame carries, or ``None`` when no rule matches it."""
    stem = str(getattr(frame, 'predicate', '') or '').lower()
    words = _adjunct_words(frame, _subject_role(frame))
    for stems, needed, concept in FRAME_RULES:
        if stem not in stems:
            continue
        if needed is not None and needed not in words:
            continue
        return concept
    return None


def understand(text):
    """The general chart parser over an already-canonicalized sentence.

    Returns ``(meanings, readings)``. A *parser fault* never raises at the caller: it must not stop
    an agent from hearing, it must make the agent fail to understand. A **missing substrate** is a
    different thing and does raise, with the install hint, rather than looking like a village that
    could not make out the words.
    """
    from . import load_tensacode
    load_tensacode()  # raises ImportError with the install hint when the extra is absent
    try:
        from tensacode.language import ENGLISH
        from tensacode.language import understand as _understand
        got = _understand(ENGLISH, text)
    except Exception:  # noqa: BLE001 - see above
        return (), 0
    meanings = tuple(m for m in (got.meanings or ()) if hasattr(m, 'roles'))
    return meanings, len(got.meanings or ())


def parse(sentence, register):
    """Sentence -> :class:`Reading`, in the hearer's register. Lossy by construction."""
    text, foreign = register.canonicalize(sentence)
    meanings, readings = understand(text)
    if not meanings:
        return Reading(foreign=foreign, readings=readings, note='not understood', via='none')
    frame = meanings[0]
    attributed = None
    outer = str(getattr(frame, 'predicate', '') or '').lower()
    if outer in REPORTING_VERBS:
        content = frame.roles.get('content')
        if not hasattr(content, 'roles'):
            return Reading(foreign=foreign, readings=readings, via='general',
                           attributed=_text(frame.roles.get('subject')) or None,
                           note='reported speech whose content clause did not survive the parse')
        attributed = _text(frame.roles.get('subject')) or None
        frame = content
    concept = match_concept(frame)
    if concept is None:
        note = 'no concept for %s(%s)' % (getattr(frame, 'predicate', '?'), ','.join(sorted(frame.roles)))
        if foreign:
            note += '; unfamiliar wording: ' + ', '.join(foreign)
        return Reading(foreign=foreign, readings=readings, attributed=attributed, note=note, via='general')
    subject_role = _subject_role(frame)
    subject_token = _text(frame.roles[subject_role]) if subject_role else ''
    if kind_of_token(subject_token) != SUBJECT_KIND[concept]:
        return Reading(foreign=foreign, readings=readings, attributed=attributed, via='general',
                       note='incoherent: %s cannot be the subject of %s'
                            % (subject_token or '(nothing)', concept))
    object_token = None
    if concept in OBJECT_ROLES:
        object_token = next((_text(frame.roles[role]) for role in OBJECT_ROLES[concept]
                             if frame.roles.get(role) is not None), '')
        if not object_token:
            # Attributed relay hangs the adjunct on the outer ``tell`` frame, so the inner clause
            # no longer says what it was about (:data:`RELAY_LOSSY_CONCEPTS`). Nothing is invented.
            return Reading(foreign=foreign, readings=readings, attributed=attributed, via='general',
                           note='the words no longer say what %s was about' % concept)
    return Reading(concept=concept, subject_token=subject_token, object_token=object_token,
                   attributed=attributed, foreign=foreign, readings=readings, via='general')


def slip(concept, fidelity, *, seed=0, utterance=''):
    """``(concept, slipped)``: a stage may move one rung up :data:`LADDER` on a lossy channel.

    Deterministic in ``(utterance, concept, seed)``, so two runs of the same exchange produce the
    same misunderstanding and a cascade is reproducible.
    """
    if not concept or not concept.startswith('stage_') or fidelity >= 1.0:
        return concept, False
    rng = random.Random('%s|%s|%d' % (utterance, concept, int(seed)))
    if rng.random() <= max(0.0, min(1.0, fidelity)):
        return concept, False
    stage = SLIP[concept[len('stage_'):]]
    if stage == concept[len('stage_'):]:
        return concept, False
    return 'stage_' + stage, True
