"""A society of grounded agents: one clock, one world, many minds, and a channel between them.

``docs/agents-design.md`` gives three kinds of agent and ``docs/corporate-cognition.md`` says how
two of them differ from a person. Each of them is individually grounded and, until this module,
none of them ever met. What is added here is the *closure*: an act by one agent becomes a percept
of another, and the loop runs over a mixed population on one shared clock and one shared world.

**The world is the published index.** Nothing in this module invents a fact. The shared state is
``data/world_evidence/index.sqlite`` exactly as :mod:`worldmodel.agents.grounding` reads it; what
the agents hold are *beliefs about* it. The divergence between the two is measured, not corrected
(:mod:`worldmodel.agents.observatory`).

**The tick.**

1. **Deliver.** Every agent receives the utterances published in the *previous* tick that its
   audience membership lets it hear. Never as ground truth: gossip lands in a ``heard:<id>`` scope
   carrying the relay chain and a hearsay confidence, and organizational speech goes through
   ``disclosure.receive``, whose audience lattice decides whether the listener holds anything at all.
2. **Appraise what was heard.** A bounded ``think`` pass over the delivered frontier, so a decision
   in tick *t* can rest on something said in tick *t-1*.
3. **Perceive, think, decide, act.** A person runs :meth:`worldmodel.agents.person.Person.tick`; a
   firm perceives the quarter that closed inside the window, runs its capital procedure and
   discloses through its ``DisclosureDesk``; an institution answers the requests the population made
   of it by issuing instruments off its ``Docket``. Every disclosure and every instrument is
   authorized before it happens, and one that is not authorized is not made.
4. **Publish.** Whatever each agent said is placed in the channel for tick *t*, to be delivered at
   tick *t+1*.
5. **Measure.** Belief-versus-record divergence, belief provenance, motifs, regimes, legitimacy and
   decision structure are sampled into a :class:`TickRecord`, which is the only thing the
   observatory reads.

**A person gossips; an organization does not.** The two speech acts are kept apart, because they
have different properties: gossip lapses after :data:`HEARSAY_TICKS` and carries a fidelity that
decays with the chain, while a filing or an instrument is on the record, is audience-scoped by a
lattice this module obeys rather than implements, and does not go stale.
:attr:`Utterance.formal` is the switch, and ``disclosure.receive`` is the single mechanism for the
organizational side - a filing read by the public and a subpoena served on a firm arrive the same
way, with provenance that says which.

**Simultaneity is explicit: acts are visible in the NEXT tick, never the one that produced them.**
Every agent in tick *t* therefore reads exactly the same channel contents no matter what order the
agents run in, so the order of agents can change what is *written* and can never change what is
*read*. Determinism then needs only a deterministic order over writers, which is
``(tier, entity_id)``. The alternative - same-tick visibility - would make an agent's percepts a
function of its position in the loop, which is how a simulation acquires an accidental hierarchy
that nobody declared. ``docs/civ-sim/architecture.md`` makes the same choice for cross-shard
messages ("a one-tick delay"), and ``research/civ_sim/minds.py`` implements it as the ``pending``
list that a mind drains on its next think.

**Three tiers, as in the civ sim.** Focal agents carry full claim stores. Compact agents are a
handful of numbers and a bounded slice of the event log; they still hear and still *relay*, which
is what lets a claim cross the population without every hop costing a store. Cohorts are one row
for many entities and absorb reach as a count. Promotion seeds a store from published records and
the event log and invents nothing; demotion consolidates and records what was dropped.

**Nothing here is fitted and nothing here is validated.** Every constant in this module is
declared at module level so it can be argued with. The relay fidelity, the hearsay half-life and
the audience cap are modelling choices, not measurements.
"""
import datetime as _dt
import importlib
import json
import time
from collections import namedtuple
from dataclasses import dataclass, field

from . import load_tensorcode

tc = load_tensorcode()

from tensorcode.cognition import Rule, Thought, think  # noqa: E402

from . import corporate_affect as ca  # noqa: E402
from .grounding import LEGISLATOR, EvidenceIndex, default_index_path  # noqa: E402
from .institution import Institution, committee_procedure, committee_records  # noqa: E402
from .person import Person  # noqa: E402
from .roles import Charter, InstitutionalAct, Source  # noqa: E402

UTC = _dt.timezone.utc
V = tc.Var

#: The three tiers, cheapest last. The order is also the deterministic order agents act in.
FOCAL, COMPACT, COHORT = 'focal', 'compact', 'cohort'
TIERS = (FOCAL, COMPACT, COHORT)

#: One claim costs about this much in a tensorcode ``Store`` today. Measured in the civ-sim
#: benchmark and quoted in ``docs/agents-design.md`` ("Scale"); :func:`measure_claim_bytes`
#: measures this checkout's own cost so the budget is not taken on trust.
CLAIM_BYTES = 1200

#: What a relayed claim keeps of its confidence per hop. Declared, not measured: it is the
#: modelling assumption that hearsay degrades with chain length, and the observatory reports the
#: degradation curve so the assumption is visible rather than buried.
RELAY_FIDELITY = 0.8

#: Below this, a relayed claim is not worth carrying and the relay stops. Declared.
RELAY_FLOOR = 0.25

#: How many ticks a heard claim stays live before it lapses. Hearsay is not a filing: nobody
#: re-publishes it, so it goes stale. Declared.
HEARSAY_TICKS = 4

#: How much each agent may say and relay per tick. Both are bounds on the channel, not beliefs.
UTTER_PER_TICK = 2
RELAY_PER_TICK = 1

#: How many listeners one utterance may reach. Without a cap a single committee membership makes
#: every act global, and reach stops measuring anything.
AUDIENCE_CAP = 32

#: How many logged utterances a promotion may replay into a fresh store. Declared bound.
REPLAY_LIMIT = 16

#: The predicates whose divergence from the record is sampled every tick for a person. Each one is
#: a published edge the legislator horizon actually perceives, so belief and record are comparable.
#: ``cosponsored`` is in the list because it is where the perception bound bites hardest on real
#: data: a member with six hundred cosponsorships holds the few dozen its seeding budget allowed.
DIVERGENCE_PREDICATES = ('serves_on', 'sponsored', 'cosponsored', 'received_support', 'opposed_by')

#: Decisions that are a request to move a measure. Only these queue an institutional act: a
#: decision to work a committee is not a request to report a bill, and conflating the two would
#: manufacture ultra-vires attempts out of ordinary committee work.
ADVANCING = ('advance_measure',)

#: Decisions that put an agent against something. Everything else is read as advancing.
CONTESTING = ('counter_opponent', 'build_coalition', 'campaign')

#: What a firm is asked to say about itself when no disclosure module is installed.
FIRM_UTTERANCE = 'regime'

#: The instrument an institution issues in answer to a request that it move a measure. The
#: instrument vocabulary in ``agents.instruments`` is a closed, authored set and has no
#: "report a measure out of committee" verb, so the committee puts a **finding** on the record
#: about the measure instead. What matters is unchanged and is grounded: ``make_finding`` must be
#: a power the acting seat holds, and the measure must be inside the role's declared jurisdiction,
#: which for a committee is the set of measures published as referred to it.
INSTITUTION_INSTRUMENT = 'finding'


# --------------------------------------------------------------------------- optional seams

#: Two sibling modules are owned by other work in flight and may not exist in this checkout.
#: They are resolved lazily, by name, through the narrow protocols below; absent, the society
#: falls back to the behaviour documented on each field. ``docs/agent-society.md`` §"Seams" is
#: the contract.
Seams = namedtuple('Seams', 'hearsay_weight desk docket instrument_powers receive')


def seams():
    """Resolve the optional speech, disclosure and instrument seams. Every field may be ``None``.

    ``hearsay_weight(trust, fidelity, score=, upstream=) -> float``
        ``agents.transmission``: what one teller's word is worth, capped strictly below direct
        observation. Used for the confidence on a *gossip* claim, with the relay chain entering
        through ``upstream`` rather than as an exponent bolted on. Fallback: the relay fidelity
        itself, as a ``relayed`` score.
    ``desk`` = ``agents.disclosure.DisclosureDesk``
        A firm does not gossip, it **discloses**: a formal, dated, role-attributed,
        audience-scoped act on a permanent register. With the desk installed a firm's utterance
        *is* a ``Disclosure``. Fallback: a plain ``(org, regime, <regime>)`` utterance.
    ``docket`` = ``agents.instruments.Docket``, ``instrument_powers`` =
    ``agents.instruments.COMMITTEE_INSTRUMENT_POWERS``
        An institution does not chat, it **issues**. With the docket installed the institution's
        answer to a request is an ``Issuance`` whose authorization runs through the same
        ``authorize`` against its published jurisdiction. Fallback: a bare
        ``InstitutionalAct(power='report_measure')`` and an ``authority`` utterance.
    ``receive(store, speech, receiver=, at=, scope=, confidence=) -> Reception | tc.Unknown``
        ``agents.disclosure``: the **one** mechanism by which organizational speech becomes another
        agent's percept. ``Disclosure`` and ``Issuance`` both satisfy it, so a filing read by the
        public and an instrument served on a firm arrive the same way with provenance that says
        which. Its audience lattice is authoritative here: a private briefing reaches nobody else,
        and a listener outside the audience holds **nothing** rather than holding it weakly.
    """
    return Seams(_hook('transmission', 'hearsay_weight'),
                 _hook('disclosure', 'DisclosureDesk'),
                 _hook('instruments', 'Docket'),
                 _hook('instruments', 'COMMITTEE_INSTRUMENT_POWERS'),
                 _hook('disclosure', 'receive'))


def _hook(module_name, attribute):
    try:
        module = importlib.import_module('.' + module_name, __package__)
    except ImportError:
        return None
    return getattr(module, attribute, None)


# --------------------------------------------------------------------------- the channel


@dataclass(frozen=True)
class Utterance:
    """One act placed in the shared channel: the unit that can become another agent's percept.

    ``content`` is a plain ``(subject, predicate, object)`` triple, so an utterance is a
    proposition and not a message format. ``claim_key`` is that proposition's identity,
    independent of who said it or how many times it was relayed - which is what makes "how far
    did this claim travel" answerable at all.
    """

    id: str
    tick: int
    actor: str
    kind: str                 # 'stance' | 'regime' | 'authority' | 'disclosure'
    content: tuple            # (subject, predicate, object)
    salience: float = 0.5
    claim_key: str = ''
    origin_tick: int = 0
    origin_actor: str = ''
    chain: tuple = ()         # relayers between the origin and this speaker, oldest first
    fidelity: float = 1.0
    records: tuple = ()       # published record ids the content bottoms out in, when it has any
    memo: str = ''
    speech: object = None     # a Disclosure or an Issuance, when an organization said it

    @property
    def hops(self):
        return len(self.chain)

    @property
    def formal(self):
        """True when this is organizational speech with its own register, audience and authority.

        A formal utterance is received through ``disclosure.receive`` and its audience lattice
        overrides the society's own audience graph; an informal one is gossip and is written into
        the listener's ``heard:`` scope by the society.
        """
        return self.speech is not None

    def to_json(self):
        return {'id': self.id, 'tick': self.tick, 'actor': self.actor, 'kind': self.kind,
                'content': [_plain(part) for part in self.content], 'salience': round(self.salience, 4),
                'claim_key': self.claim_key, 'origin_tick': self.origin_tick,
                'origin_actor': self.origin_actor, 'chain': list(self.chain),
                'fidelity': round(self.fidelity, 4), 'records': list(self.records), 'memo': self.memo,
                'formal': self.formal,
                'speech': None if self.speech is None else
                          {'id': getattr(self.speech, 'id', None),
                           'form': getattr(self.speech, 'form', None),
                           'audience': sorted(getattr(self.speech, 'audience', ()) or ()),
                           'recipients': list(getattr(self.speech, 'recipients', ()) or ()),
                           'cite': self.speech.cite() if hasattr(self.speech, 'cite') else None}}


@dataclass(frozen=True)
class Delivery:
    """One utterance reaching one listener. The propagation measurements are counts of these."""

    tick: int
    listener: str
    tier: str
    utterance: str
    claim_key: str
    speaker: str
    hops: int
    fidelity: float
    origin_tick: int
    origin_actor: str
    accepted: bool = True     # False: nothing was held - a cohort, or outside a formal audience
    refused: str = None       # why nothing was held, when the audience lattice said no
    channel: str = None       # the reception channel, for formal speech

    def to_json(self):
        return {'tick': self.tick, 'listener': self.listener, 'tier': self.tier,
                'claim_key': self.claim_key, 'speaker': self.speaker, 'hops': self.hops,
                'fidelity': round(self.fidelity, 4), 'origin_tick': self.origin_tick,
                'origin_actor': self.origin_actor, 'accepted': self.accepted,
                'refused': self.refused, 'channel': self.channel}


class Channel:
    """The shared, append-only act log, indexed by the tick that produced each utterance.

    This is the civ sim's event log: one structure for the whole society rather than a mailbox per
    agent, so a promotion can replay history it never held and a demotion can drop a store without
    losing what the population said.
    """

    def __init__(self):
        self._by_tick = {}
        self.utterances = []
        self.deliveries = []

    def publish(self, utterance):
        self._by_tick.setdefault(utterance.tick, []).append(utterance)
        self.utterances.append(utterance)
        return utterance

    def at(self, tick):
        """Everything said in ``tick``, in a deterministic order."""
        return tuple(sorted(self._by_tick.get(tick, ()),
                            key=lambda u: (-u.salience, u.actor, u.claim_key, u.id)))

    def about(self, entity_id, *, limit=None):
        """Logged utterances whose content mentions ``entity_id``, most salient first.

        This is what a promotion replays. It reads the shared log, not a per-agent buffer, so an
        agent that was compact the whole time still has a history to be promoted with.
        """
        hits = [u for u in self.utterances
                if entity_id in (str(u.content[0]), str(u.content[2]), u.actor, u.origin_actor)]
        hits.sort(key=lambda u: (-u.salience * u.fidelity, u.tick, u.claim_key, u.id))
        return tuple(hits[:limit] if limit else hits)

    def deliver(self, delivery):
        self.deliveries.append(delivery)
        return delivery

    def __len__(self):
        return len(self.utterances)


# --------------------------------------------------------------------------- tiers


@dataclass
class CompactState:
    """An agent that exists but does not think: a few numbers and a bounded slice of the log.

    A compact agent is not inert. It hears, and it relays what it heard with the declared fidelity
    decay, so a claim can cross a population of thousands without every hop paying for a store.
    What it cannot do is appraise, decide or explain, and that is the whole difference.
    """

    entity_id: str
    kind: str
    label: str = ''
    state: str = 'unknown'          # the motif or regime last known for this entity
    viability: float = None
    heard: int = 0
    relayed: int = 0
    claims_at_demotion: int = None
    sampled: bool = False           # True: disaggregated out of a cohort, so it is a sample
    dropped: object = None          # the DemotionReport, when this state came from a demotion

    def to_json(self):
        return {'entity_id': self.entity_id, 'kind': self.kind, 'tier': COMPACT, 'state': self.state,
                'viability': None if self.viability is None else round(self.viability, 4),
                'heard': self.heard, 'relayed': self.relayed,
                'claims_at_demotion': self.claims_at_demotion, 'sampled': self.sampled,
                'dropped': self.dropped.to_json() if self.dropped is not None else None}


@dataclass
class Cohort:
    """One row for many entities: the far tier. It absorbs reach and relays nothing."""

    id: str
    kind: str
    members: tuple = ()
    heard: int = 0
    disaggregated: tuple = ()

    def to_json(self):
        return {'id': self.id, 'kind': self.kind, 'tier': COHORT, 'members': len(self.members),
                'heard': self.heard, 'disaggregated': list(self.disaggregated)}


@dataclass(frozen=True)
class PromotionReport:
    """What a promoted store was seeded from. ``invented`` is zero by construction; it is reported
    so that the claim is checkable rather than asserted."""

    entity_id: str
    at_tick: int
    kind: str
    from_records: int = 0
    from_event_log: int = 0
    replayed: tuple = ()
    unknown: tuple = ()
    sampled: bool = False
    invented: int = 0

    @property
    def claims(self):
        return self.from_records + self.from_event_log

    def to_json(self):
        return {'entity_id': self.entity_id, 'at_tick': self.at_tick, 'kind': self.kind,
                'from_records': self.from_records, 'from_event_log': self.from_event_log,
                'replayed': list(self.replayed), 'unknown': list(self.unknown),
                'sampled': self.sampled, 'invented': self.invented, 'claims': self.claims}

    def lines(self):
        return ['promoted %s at tick %d' % (self.entity_id, self.at_tick),
                '  from published records: %d claim(s)' % self.from_records,
                '  replayed from the event log: %d claim(s)' % self.from_event_log,
                '  Unknown (no published record): %s' % (', '.join(self.unknown) or 'none'),
                '  invented: %d' % self.invented]


@dataclass(frozen=True)
class DemotionReport:
    """What a demotion dropped. A store is released, so this is the only record of what was in it."""

    entity_id: str
    at_tick: int
    kind: str
    claims_dropped: int = 0
    by_predicate: tuple = ()        # ((predicate, count), ...) sorted
    scopes_dropped: tuple = ()
    episodes_dropped: int = 0
    relations_dropped: int = 0
    derivations_dropped: int = 0    # claims that carried a derived_from chain: the explanations lost
    retained: tuple = ()            # (name, value) pairs that survive as compact state

    def to_json(self):
        return {'entity_id': self.entity_id, 'at_tick': self.at_tick, 'kind': self.kind,
                'claims_dropped': self.claims_dropped,
                'by_predicate': [list(pair) for pair in self.by_predicate],
                'scopes_dropped': list(self.scopes_dropped), 'episodes_dropped': self.episodes_dropped,
                'relations_dropped': self.relations_dropped,
                'derivations_dropped': self.derivations_dropped,
                'retained': [list(pair) for pair in self.retained]}

    def lines(self):
        return ['demoted %s at tick %d' % (self.entity_id, self.at_tick),
                '  dropped %d claim(s) across %d predicate(s)' % (self.claims_dropped, len(self.by_predicate)),
                '  dropped %d explanation chain(s)' % self.derivations_dropped,
                '  dropped %d episode(s) and %d relation(s)' % (self.episodes_dropped, self.relations_dropped),
                '  retained: ' + ', '.join('%s=%s' % pair for pair in self.retained)]


@dataclass
class Member:
    """One participant, at whatever tier it currently sits."""

    entity_id: str
    kind: str                    # 'person' | 'firm' | 'institution'
    tier: str = FOCAL
    label: str = ''
    config: dict = field(default_factory=dict)
    agent: object = None         # focal only: the Person / Firm / Institution
    compact: CompactState = None
    cohort_id: str = None
    inbox: tuple = ()            # what was delivered this tick, for the relay choice
    said: int = 0
    promotions: tuple = ()
    demotions: tuple = ()

    @property
    def focal(self):
        return self.tier == FOCAL and self.agent is not None


# --------------------------------------------------------------------------- the social rules


def social_rules(me, coupling):
    """Appraisal rules whose premises are things *other agents said*.

    These are the point of the module: without them an agent could hear and never be moved. Each
    one joins a claim the agent holds about itself with a claim that arrived through the channel,
    so the appraisal's provenance runs through another agent's decision and out to the published
    record that decision rested on. The crossing rules are gated by the same coupling dial kappa a
    person's own rules use, so a compartmentalized agent hears a colleague and barely plans on it.
    """
    crossing = coupling >= 0.35
    score = tc.Score(round(coupling, 2), 'coupled')

    def contested(bindings, mind):
        yield tc.Claim(me, 'appraises', ('rebuff', _id(bindings['b']))), tc.Score(0.6, 'appraisal')
        if crossing:
            yield tc.Claim(me, 'wants', ('build_coalition', _id(bindings['a']))), score

    def advanced(bindings, mind):
        yield tc.Claim(me, 'appraises', ('support', _id(bindings['a']))), tc.Score(0.5, 'appraisal')
        if crossing:
            yield tc.Claim(me, 'wants', ('committee_work', _id(bindings['a']))), score

    def warned(bindings, mind):
        """Something said by a counterpart the agent already takes to oppose it."""
        yield tc.Claim(me, 'appraises', ('threat', _id(bindings['d']))), tc.Score(0.7, 'appraisal')
        if crossing:
            yield tc.Claim(me, 'wants', ('campaign', _id(bindings['d']))), score

    def refused(bindings, mind):
        """An institution refused to act on a measure this agent put its name to."""
        yield tc.Claim(me, 'appraises', ('rebuff', _id(bindings['b']))), tc.Score(0.65, 'appraisal')
        if crossing:
            yield tc.Claim(me, 'means', ('blocked_in_committee', _id(bindings['b']))), score

    def finding(bindings, mind):
        """An institution put a finding on the record about a measure.

        The join against the agent's own sponsorships is done here, keyed, rather than as a second
        pattern: the instrument's statement carries the measure as a plain id and the agent's own
        ``sponsored`` claim carries it as a ``Ref``, so a pattern join would silently never match.
        Keying the lookup is also the rule discipline ``docs/civ-sim/architecture.md`` §2 asks for.
        """
        measure = str(_id(bindings['b']))
        mine = mind.claims(me, 'sponsored', tc.Ref(measure)) if ':' in measure else ()
        yield tc.Claim(me, 'appraises', ('standing' if mine else 'exposure', measure)), \
            tc.Score(0.55 if mine else 0.3, 'appraisal')
        if crossing and mine:
            yield tc.Claim(me, 'wants', ('committee_work', _id(bindings['a']))), score

    return [
        Rule('a_colleague_contests_a_measure_i_sponsored',
             ((me, 'sponsored', V('b')), (V('b'), 'contested_by', V('a'))), contested),
        Rule('a_colleague_advances_a_measure_i_sponsored',
             ((me, 'sponsored', V('b')), (V('b'), 'advanced_by', V('a'))), advanced),
        Rule('i_heard_from_someone_who_opposes_me',
             ((me, 'opposed_by', V('d')), (me, 'heard_from', V('d'))), warned),
        Rule('a_committee_refused_my_measure',
             ((me, 'sponsored', V('b')), (V('b'), 'refused_by', V('a'))), refused),
        Rule('a_committee_made_a_finding', ((V('a'), 'finding', V('b')),), finding),
    ]


def firm_social_rules(firm):
    """One rule: a firm that hears distress from something inside its own consolidation boundary.

    The premise pair is deliberately a *tie* the catalog published and a *regime* another agent
    said, so the derivation crosses both a role boundary and an agent boundary, and
    ``cross_role_integration`` counts it for the right reason.
    """
    roles = firm.charter.roles
    listener = roles.get('board') or roles.get('cfo') or (sorted(roles.values(), key=lambda r: r.id)[0]
                                                          if roles else None)
    if listener is None:
        return []
    scope = listener.scope
    me = firm.ref

    def distress(bindings, mind):
        yield tc.Claim(scope, 'appraises_corporate', ('counterparty_distress', _id(bindings['c'])),
                       scope=scope), tc.Score(0.6, 'uncalibrated')

    out = []
    for relation in ca.GROUP_RELATIONS:
        out.append(Rule('a_%s_counterparty_reported_distress' % relation,
                        ((me, relation, V('c')), (V('c'), 'regime', 'distressed')), distress))
    return out


class SocialPerson(Person):
    """A :class:`~worldmodel.agents.person.Person` that can also be moved by what others said.

    Only two things are added, and neither touches the person loop: the social rule pack joins the
    base rules, and the store carries a ``heard:<id>`` scope the society writes into. Everything
    else - perception bound, appraisal, affect geometry, ``choose``, consolidation - is the
    unmodified person agent.
    """

    def __init__(self, grounding, store, **kwargs):
        super().__init__(grounding, store, **kwargs)
        self.heard_scope = tc.Ref('heard:%s' % grounding.entity_id)
        self.heard_at = {}
        self.last_decision = None

    def rules(self):
        return super().rules() + social_rules(self.me, self.coupling)

    def _decide(self, what, premises, why, utility):
        """Remember what was chosen, as a value rather than as a claim.

        The ``decided`` claim is content-addressed, so a decision *identical* to the previous one
        retracts and re-tells the same claim id and ends up forgotten in the same patch. The claim
        store is right to do that - the proposition did not change - but the society still needs to
        know what the agent chose this tick in order to say it out loud. So the choice is kept here
        as a plain value and the claim keeps doing what it does.
        """
        self.last_decision = tuple(what)
        return super()._decide(what, premises, why, utility)


class SocialFirm:
    """A thin social wrapper over a firm, because ``Firm`` is not a tick-loop object.

    A firm does not have a tick: it has filings. This holds the firm, the declared objectives and
    options from the society config, and the ``heard:<org>`` scope, and drives one filing-and-
    procedure step per society tick. The firm itself is untouched.
    """

    def __init__(self, firm, *, options=(), objectives=(), role_objectives=(), procedure=None,
                 desk=None):
        self.firm = firm
        self.options = tuple(options)
        self.objectives = tuple(objectives)
        self.role_objectives = tuple(role_objectives)
        self.procedure = procedure
        self.desk = desk               # a disclosure.DisclosureDesk, when that module is installed
        self.heard_scope = tc.Ref('heard:%s' % firm.entity_id)
        self.heard_at = {}
        self.social = firm_social_rules(firm)
        self._base_rules = firm.rules
        firm.rules = lambda: self._base_rules() + self.social
        self.perceived = 0
        self.last_decision = None
        self.said_claims = set()       # claim ids already disclosed, so a filing is not repeated

    @property
    def entity_id(self):
        return self.firm.entity_id

    @property
    def store(self):
        return self.firm.mind

    def speaking_role(self):
        """The first role the charter gives ``approve_disclosure``, or ``None``.

        Deterministic by role id. With no declared delegation of authority every role is
        ``UNDECLARED`` and this returns ``None``, so a firm whose bylaws the catalog does not
        publish says nothing rather than saying something on undeclared authority.
        """
        charter = self.firm.charter
        for role_id in sorted(charter.roles):
            if 'approve_disclosure' in charter.powers_of(role_id):
                return role_id
        return None


class SocialInstitution:
    """A thin social wrapper over an institution: it acts on what the population asked for."""

    def __init__(self, institution, *, procedure_id='report_measure', acts_per_tick=2, docket=None):
        self.institution = institution
        self.procedure_id = procedure_id
        self.acts_per_tick = int(acts_per_tick)
        self.docket = docket           # an instruments.Docket, when that module is installed
        self.heard_scope = tc.Ref('heard:%s' % institution.entity_id)
        self.heard_at = {}
        self.requests = []             # (measure_id, asked_by) queued from the channel
        self.executor = None

    @property
    def entity_id(self):
        return self.institution.entity_id

    @property
    def store(self):
        return self.institution.mind


# --------------------------------------------------------------------------- the clock


@dataclass(frozen=True)
class Clock:
    """A shared clock over real time. One tick is one half-open window on published validity."""

    start: object
    window_days: int = 90

    def window(self, tick):
        span = _dt.timedelta(days=self.window_days)
        begin = self.start + span * tick
        return (begin, begin + span)

    def to_json(self):
        return {'start': self.start.isoformat(), 'window_days': self.window_days}


# --------------------------------------------------------------------------- tick records


@dataclass(frozen=True)
class DecisionRecord:
    """One decision, or one honest non-decision, or one refused act."""

    tick: int
    actor: str
    kind: str                     # 'person' | 'firm' | 'institution'
    outcome: str
    unknown_reason: str = None
    detail: str = None
    subject: str = None
    divergences: tuple = ()       # ((role, firm_choice, role_choice), ...)
    outcome_changed: bool = False
    agenda_cost: float = None
    authority: str = None         # 'holds' | 'fails' | 'unknown' for an institutional act
    reasons: tuple = ()

    @property
    def is_unknown(self):
        return self.unknown_reason is not None

    def to_json(self):
        return {'tick': self.tick, 'actor': self.actor, 'kind': self.kind, 'outcome': self.outcome,
                'unknown_reason': self.unknown_reason, 'detail': self.detail, 'subject': self.subject,
                'divergences': [list(d) for d in self.divergences],
                'outcome_changed': self.outcome_changed,
                'agenda_cost': None if self.agenda_cost is None else round(self.agenda_cost, 6),
                'authority': self.authority, 'reasons': list(self.reasons)}


@dataclass(frozen=True)
class DivergenceSample:
    """How far one agent's beliefs on one predicate sit from what the index published.

    This is a *distance between two sets*, not an error: bounded perception, hearsay and a tick
    delay all put claims in the belief set that the record does not currently carry, and all leave
    published edges outside it. The observatory reports the distribution and says so.
    """

    tick: int
    agent: str
    predicate: str
    held: int = 0
    published: int = 0
    overlap: int = 0
    unsupported: int = 0          # believed, not currently published
    missing: int = 0              # published, not believed

    @property
    def union(self):
        return self.held + self.published - self.overlap

    @property
    def distance(self):
        """Jaccard distance between what the agent holds and what the index publishes."""
        return 0.0 if self.union <= 0 else round(1.0 - self.overlap / self.union, 6)

    def to_json(self):
        return {'tick': self.tick, 'agent': self.agent, 'predicate': self.predicate,
                'held': self.held, 'published': self.published, 'overlap': self.overlap,
                'unsupported': self.unsupported, 'missing': self.missing, 'distance': self.distance}


@dataclass
class TickRecord:
    """Everything one tick produced. The observatory reads only these."""

    tick: int
    window: tuple
    delivered: int = 0
    published: int = 0
    relayed: int = 0
    expired: int = 0
    heard_derived: int = 0
    perception: dict = field(default_factory=dict)
    decisions: tuple = ()
    motifs: dict = field(default_factory=dict)
    regimes: dict = field(default_factory=dict)
    legitimacy: dict = field(default_factory=dict)
    viability: dict = field(default_factory=dict)
    divergence: tuple = ()
    provenance: dict = field(default_factory=dict)
    claims_live: dict = field(default_factory=dict)
    tiers: dict = field(default_factory=dict)
    promotions: tuple = ()
    demotions: tuple = ()
    ms: float = 0.0

    def to_json(self):
        return {'tick': self.tick, 'window': [w.isoformat() for w in self.window],
                'delivered': self.delivered, 'published': self.published, 'relayed': self.relayed,
                'expired': self.expired, 'heard_derived': self.heard_derived,
                'perception': {k: v for k, v in sorted(self.perception.items())},
                'decisions': [d.to_json() for d in self.decisions],
                'motifs': dict(sorted(self.motifs.items())), 'regimes': dict(sorted(self.regimes.items())),
                'legitimacy': {k: (None if v is None else round(v, 6))
                               for k, v in sorted(self.legitimacy.items())},
                'viability': {k: (None if v is None else round(v, 6))
                              for k, v in sorted(self.viability.items())},
                'divergence': [d.to_json() for d in self.divergence],
                'provenance': {k: dict(sorted(v.items())) for k, v in sorted(self.provenance.items())},
                'claims_live': dict(sorted(self.claims_live.items())), 'tiers': dict(sorted(self.tiers.items())),
                'promotions': [p.to_json() for p in self.promotions],
                'demotions': [d.to_json() for d in self.demotions],
                'ms': round(self.ms, 3)}


# --------------------------------------------------------------------------- budget


@dataclass(frozen=True)
class FocalBudget:
    """What a focal ceiling actually is on this checkout, measured rather than assumed."""

    focal_agents: int = 0
    claims_live: int = 0
    claims_per_focal: float = 0.0
    max_claims: int = 0
    declared_bytes_per_claim: int = CLAIM_BYTES
    serialized_bytes_per_claim: float = None
    resident_bytes_per_claim: float = None
    memory_budget_bytes: int = 0

    @property
    def bytes_per_claim(self):
        """The cost the ceiling is computed with.

        The measured *resident* cost when there is one, otherwise the declared 1.2 KB. The
        serialized measurement is deliberately **not** used: pickling shares nothing and compacts
        everything, so it runs several times under the live cost and would inflate the ceiling. It
        is reported as a lower bound and nothing else.
        """
        if self.resident_bytes_per_claim:
            return float(self.resident_bytes_per_claim)
        return float(self.declared_bytes_per_claim)

    @property
    def focal_ceiling(self):
        """How many focal agents this budget supports at the measured claims-per-agent."""
        per_agent = self.bytes_per_claim * max(1.0, self.claims_per_focal)
        return int(self.memory_budget_bytes // per_agent) if per_agent > 0 else 0

    def to_json(self):
        return {'focal_agents': self.focal_agents, 'claims_live': self.claims_live,
                'claims_per_focal': round(self.claims_per_focal, 2), 'max_claims': self.max_claims,
                'declared_bytes_per_claim': self.declared_bytes_per_claim,
                'serialized_bytes_per_claim': None if self.serialized_bytes_per_claim is None
                else round(self.serialized_bytes_per_claim, 1),
                'resident_bytes_per_claim': None if self.resident_bytes_per_claim is None
                else round(self.resident_bytes_per_claim, 1),
                'bytes_per_claim_used': round(self.bytes_per_claim, 1),
                'memory_budget_bytes': self.memory_budget_bytes,
                'focal_ceiling': self.focal_ceiling,
                'basis': 'declared 1.2 KB/claim from docs/agents-design.md, replaced by the '
                         'tracemalloc measurement when --measure-resident was given; the '
                         'serialized figure is a lower bound and is never used for the ceiling. '
                         'The ceiling is memory only and says nothing about tick time'}


def measure_claim_bytes(stores):
    """Serialized bytes per claim across ``stores``. A lower bound on the resident cost.

    Pickling a ``ClaimRecord`` graph shares nothing, so this is a *serialized* size and is
    labelled as one. :func:`measure_resident_bytes` is the resident measurement.
    """
    import pickle
    total_bytes, total_claims = 0, 0
    for store in stores:
        records = list(store._claims.values())
        if not records:
            continue
        try:
            total_bytes += len(pickle.dumps(records, protocol=pickle.HIGHEST_PROTOCOL))
        except Exception:
            return None
        total_claims += len(records)
    return None if not total_claims else total_bytes / total_claims


def measure_resident_bytes(build):
    """Resident bytes per claim for one agent, by tracing the allocations ``build()`` makes.

    ``build()`` must return ``(object_with_a_store, store)``. Uses ``tracemalloc``, so the number
    is Python's own accounting of the peak allocation attributable to the build, not RSS.
    """
    import tracemalloc
    started = not tracemalloc.is_tracing()
    if started:
        tracemalloc.start()
    before = tracemalloc.take_snapshot()
    held, store = build()
    after = tracemalloc.take_snapshot()
    grew = sum(stat.size_diff for stat in after.compare_to(before, 'filename'))
    claims = len(store._claims)
    if started:
        tracemalloc.stop()
    return (grew / claims if claims else None), held


# --------------------------------------------------------------------------- the society


class Society:
    """A mixed population of grounded agents over one clock, one world and one channel."""

    #: Declared, so a reader can see the choice rather than infer it from the code.
    simultaneity = 'next_tick'
    simultaneity_rationale = (
        'An act published in tick t is delivered at the start of tick t+1. Every agent in a tick '
        'therefore reads the same channel whatever order the agents run in, so agent order can '
        'change what is written and can never change what is read. Same-tick visibility would make '
        'an agent perceptual field a function of its position in the loop, which is an undeclared '
        'hierarchy that nobody chose and no config can see.')

    def __init__(self, *, clock, index=None, catalog=None, seed=0, config=None,
                 hearsay_ticks=HEARSAY_TICKS, relay_fidelity=RELAY_FIDELITY,
                 audience_cap=AUDIENCE_CAP, utter_per_tick=UTTER_PER_TICK,
                 relay_per_tick=RELAY_PER_TICK, divergence_predicates=DIVERGENCE_PREDICATES):
        self.clock = clock
        self.index = index
        self.catalog = catalog
        self.seed = int(seed)
        self.config = dict(config or {})
        self.hearsay_ticks = int(hearsay_ticks)
        self.relay_fidelity = float(relay_fidelity)
        self.audience_cap = int(audience_cap)
        self.utter_per_tick = int(utter_per_tick)
        self.relay_per_tick = int(relay_per_tick)
        self.divergence_predicates = tuple(divergence_predicates)
        self.members = {}
        self.cohorts = {}
        self.channel = Channel()
        self.ticks = 0
        self.history = []
        self.links = {}
        self.seams = seams()
        self._audience = None
        self._promotions = []
        self._demotions = []
        self._schedule = {'promote': {}, 'demote': {}}

    # ------------------------------------------------------------------ construction

    @classmethod
    def from_config(cls, config, *, index=None, catalog=None, seed=None):
        """Build a society from a JSON-shaped config. Everything grounded goes through the index."""
        config = validate_config(config)
        clock = Clock(_parse_date(config['clock']['start']),
                      int(config['clock'].get('window_days', 90)))
        society = cls(clock=clock, index=index, catalog=catalog,
                      seed=config.get('seed', 0) if seed is None else seed, config=config,
                      hearsay_ticks=config.get('hearsay_ticks', HEARSAY_TICKS),
                      relay_fidelity=config.get('relay_fidelity', RELAY_FIDELITY),
                      audience_cap=config.get('audience_cap', AUDIENCE_CAP))
        for entry in config.get('cohorts', ()):
            society.add_cohort(entry['id'], entry.get('kind', 'person'), tuple(entry.get('members', ())))
        for entry in config.get('persons', ()):
            society.add_person(entry['entity_id'], tier=entry.get('tier', FOCAL), config=entry)
        for entry in config.get('firms', ()):
            society.add_firm(entry['entity_id'], tier=entry.get('tier', FOCAL), config=entry)
        for entry in config.get('institutions', ()):
            society.add_institution(entry['entity_id'], tier=entry.get('tier', FOCAL), config=entry)
        for pair in config.get('links', ()):
            society.link(pair[0], pair[1])
        for entry in config.get('promote', ()):
            society._schedule['promote'].setdefault(int(entry['at_tick']), []).append(entry['entity_id'])
        for entry in config.get('demote', ()):
            society._schedule['demote'].setdefault(int(entry['at_tick']), []).append(entry['entity_id'])
        return society

    def add_person(self, entity_id, *, tier=FOCAL, config=None):
        member = Member(entity_id, 'person', tier, config=dict(config or {}))
        self.members[entity_id] = member
        if tier == FOCAL:
            self._make_person(member)
        else:
            member.compact = CompactState(entity_id, 'person', label=self._label(entity_id))
        member.label = member.label or self._label(entity_id)
        self._audience = None
        return member

    def add_firm(self, entity_id, *, tier=FOCAL, config=None):
        member = Member(entity_id, 'firm', tier, config=dict(config or {}))
        self.members[entity_id] = member
        if tier == FOCAL:
            self._make_firm(member)
        else:
            member.compact = CompactState(entity_id, 'firm', label=self._label(entity_id))
        member.label = member.label or self._label(entity_id)
        self._audience = None
        return member

    def add_institution(self, entity_id, *, tier=FOCAL, config=None):
        member = Member(entity_id, 'institution', tier, config=dict(config or {}))
        self.members[entity_id] = member
        if tier == FOCAL:
            self._make_institution(member)
        else:
            member.compact = CompactState(entity_id, 'institution', label=self._label(entity_id))
        member.label = member.label or self._label(entity_id)
        self._audience = None
        return member

    def add_cohort(self, cohort_id, kind, members):
        cohort = Cohort(cohort_id, kind, tuple(members))
        self.cohorts[cohort_id] = cohort
        self._audience = None
        return cohort

    def link(self, a, b):
        """Declare that ``a`` and ``b`` can hear each other, on top of the grounded audience."""
        self.links.setdefault(a, set()).add(b)
        self.links.setdefault(b, set()).add(a)
        self._audience = None

    # -- the three constructors ---------------------------------------------------------
    def _make_person(self, member):
        """Seed one person agent from published records.

        ``LEGISLATOR`` is the only perception horizon this catalog publishes enough to support
        (see ``docs/agents-design.md``, "What the catalog actually publishes about a legislator"),
        so it is used rather than read from the config: a ``role`` key that silently fell back to
        the legislator horizon would be worse than not having one.
        """
        if self.index is None:
            raise ValueError('a person agent needs an open index; pass index= to Society')
        agent = SocialPerson.ground(self.index, member.entity_id, horizon=LEGISLATOR,
                                    seed=self.seed)
        member.agent = agent
        member.label = agent.grounding.label or member.entity_id
        return agent

    def _make_firm(self, member):
        from .firm import (Aim, CAPITAL_PROCEDURE, Firm, Option, STANDARD_DELEGATION,
                           charter_from_records, firm_records)
        entry = member.config
        records = firm_records(member.entity_id, index=self.index,
                               catalog=self.catalog if entry.get('financials', 'catalog') == 'catalog' else None,
                               max_quarters=int(entry.get('max_quarters', 8)))
        declared = entry.get('quarters') or ()
        if declared:
            quarters = tuple(_declared_quarter(q) for q in declared)
            records = type(records)(records.entity_id, records.name, quarters, records.roles,
                                    records.holders, records.ties,
                                    records.unknown + ('quarterly financials: declared in the society '
                                                       'config, not published',),
                                    records.sources)
        authority = STANDARD_DELEGATION if entry.get('authority') == 'standard_delegation' else None
        charter = charter_from_records(records, authority=authority)
        firm = Firm(records, charter, seat_policy=entry.get('seat_policy', 'observed_or_unknown'))
        objectives = tuple(Aim(**aim) for aim in entry.get('objective', ()))
        role_objectives = tuple((role_id, tuple(Aim(**aim) for aim in aims))
                                for role_id, aims in sorted((entry.get('role_objectives') or {}).items()))
        options = tuple(Option(o['id'], o.get('label', ''),
                               tuple((name, float(value)) for name, value in (o.get('effects') or [])),
                               o.get('amount'), o.get('power', 'allocate_capital'))
                        for o in entry.get('options', ()))
        desk = None
        if self.seams.desk is not None and entry.get('disclose', True):
            # A firm's own speech act. Every filed claim already carries the filing date as its
            # evidence date, so the desk's known-at needs nothing invented.
            desk = self.seams.desk(firm)
        member.agent = SocialFirm(firm, options=options, objectives=objectives,
                                  role_objectives=role_objectives,
                                  procedure=entry.get('procedure', CAPITAL_PROCEDURE.id), desk=desk)
        member.label = firm.name
        return member.agent

    def _make_institution(self, member):
        if self.index is None:
            raise ValueError('an institution agent needs an open index; pass index= to Society')
        entry = member.config
        records = committee_records(self.index, member.entity_id,
                                   referrals=int(entry.get('referrals', 200)),
                                   members=int(entry.get('members', 80)))
        if not records.roles:
            raise ValueError('no committee_member record published for %s; it cannot be an '
                             'institution agent on this index' % member.entity_id)
        chair = 'chair' if 'chair' in {r.id for r in records.roles} else sorted(r.id for r in records.roles)[0]
        procedure = committee_procedure(chair)
        charter = Charter(member.entity_id, {r.id: r for r in records.roles},
                         procedures={procedure.id: procedure}).validate()
        institution = Institution(records, charter)
        docket = None
        if self.seams.docket is not None and entry.get('issue', True):
            # The docket widens the charter with the declared instrument verbs and nothing else;
            # the jurisdiction it authorizes against is still the published referral set.
            docket = self.seams.docket(institution, powers=self.seams.instrument_powers)
        member.agent = SocialInstitution(institution, procedure_id=procedure.id,
                                         acts_per_tick=int(entry.get('acts_per_tick', 2)),
                                         docket=docket)
        member.agent.role_id = chair
        member.label = institution.name
        return member.agent

    # ------------------------------------------------------------------ the audience graph

    def audience(self, actor):
        """Who can hear ``actor``, from published records plus declared links.

        A legislator is heard by the other legislators in this society who sit on a committee it
        sits on, and by that committee. A firm is heard by whatever it is consolidated with. An
        institution is heard by its members. Nothing here is a social-network model; it is the
        co-membership the catalog already publishes, capped so that reach stays a measurement.
        """
        if self._audience is None:
            self._audience = self._build_audience()
        return self._audience.get(actor, ())

    def _build_audience(self):
        committees, listeners = {}, {}
        for entity_id, member in sorted(self.members.items()):
            for other in self._published_neighbours(member):
                if other in self.members and other != entity_id:
                    listeners.setdefault(entity_id, set()).add(other)
                    listeners.setdefault(other, set()).add(entity_id)
                if member.kind == 'person' and other.startswith('congress:committee:'):
                    committees.setdefault(other, set()).add(entity_id)
        for committee, sitting in committees.items():
            for entity_id in sitting:
                listeners.setdefault(entity_id, set()).update(sitting - {entity_id})
                if committee in self.members:
                    listeners.setdefault(entity_id, set()).add(committee)
                    listeners.setdefault(committee, set()).add(entity_id)
        for a, others in self.links.items():
            for b in others:
                listeners.setdefault(a, set()).add(b)
                listeners.setdefault(b, set()).add(a)
        for cohort in self.cohorts.values():
            for entity_id in cohort.members:
                if entity_id in self.members:
                    listeners.setdefault(entity_id, set()).add(cohort.id)
        out = {}
        for entity_id, heard_by in listeners.items():
            out[entity_id] = tuple(sorted(heard_by)[:self.audience_cap])
        return out

    def _published_neighbours(self, member):
        """The entity ids the index links ``member`` to, bounded and by declared predicate only."""
        if self.index is None:
            return ()
        cluster = self.index.cluster(member.entity_id)
        found = set()
        if member.kind == 'person':
            for row in self.index.edges(cluster, 'committee_member', direction='out', limit=16):
                found.add(row['object'])
        elif member.kind == 'institution':
            for row in self.index.edges([member.entity_id], 'committee_member', direction='in', limit=80):
                found.add(row['subject'])
        elif member.kind == 'firm':
            for predicate in ca.GROUP_RELATIONS[:2]:
                for row in self.index.edges(cluster, predicate, direction='out', limit=8):
                    found.update(self.index.cluster(row['object']))
                for row in self.index.edges(cluster, predicate, direction='in', limit=16):
                    found.update(self.index.cluster(row['subject']))
        return tuple(sorted(found))

    # ------------------------------------------------------------------ ordering

    def order(self):
        """The deterministic order agents act in: focal first, then compact, each by entity id."""
        return tuple(sorted(self.members.values(),
                            key=lambda m: (TIERS.index(m.tier), m.entity_id)))

    # ------------------------------------------------------------------ one tick

    def tick(self):
        """Run one tick over the whole population. Returns a :class:`TickRecord`."""
        started = time.perf_counter()
        tick, window = self.ticks, self.clock.window(self.ticks)
        record = TickRecord(tick=tick, window=window)
        inbound = self.channel.at(tick - 1) if tick else ()

        # 1. deliver what was said last tick, and 2. appraise it
        for member in self.order():
            heard = self._for(member, inbound)
            member.inbox = heard
            record.delivered += len(heard)
            if member.focal:
                record.heard_derived += self._deliver_focal(member, heard, window)
            elif member.compact is not None:
                member.compact.heard += len(heard)
        for cohort in sorted(self.cohorts.values(), key=lambda c: c.id):
            reached = [u for u in inbound if cohort.id in self.audience(u.actor)]
            cohort.heard += len(reached)
            for utterance in reached:
                self.channel.deliver(Delivery(tick, cohort.id, COHORT, utterance.id, utterance.claim_key,
                                              utterance.actor, utterance.hops, utterance.fidelity,
                                              utterance.origin_tick, utterance.origin_actor, accepted=False))
            record.delivered += len(reached)

        # 3. hearsay lapses: nobody re-publishes a rumour
        record.expired = self._expire(tick)

        # 4. perceive, think, decide, act
        said = []
        for member in self.order():
            if member.focal:
                said.extend(self._act(member, window, record))
            elif member.compact is not None:
                said.extend(self._relay(member, tick, record))

        # 5. publish, for delivery next tick
        for utterance in said:
            self.channel.publish(utterance)
        record.published = len(said)

        # 6. measure
        record.divergence = self._sample_divergence(tick)
        record.provenance = self._sample_provenance()
        record.tiers = self._tier_counts()
        record.claims_live = {m.entity_id: len(m.agent.store._claims)
                              for m in self.members.values() if m.focal}
        self.ticks += 1
        for entity_id in self._schedule['demote'].get(self.ticks, ()):
            report = self.demote(entity_id)
            if report is not None:
                record.demotions += (report,)
        for entity_id in self._schedule['promote'].get(self.ticks, ()):
            report = self.promote(entity_id)
            if report is not None:
                record.promotions += (report,)
        record.ms = (time.perf_counter() - started) * 1e3
        self.history.append(record)
        return record

    def run(self, ticks):
        """Run ``ticks`` ticks and return the records, oldest first."""
        return tuple(self.tick() for _ in range(int(ticks)))

    # -- delivery ----------------------------------------------------------------------
    def _for(self, member, inbound):
        """The utterances ``member`` can hear: audience membership, minus its own voice.

        For **formal** speech the society's audience graph is only a first filter; the disclosure
        module's own audience lattice decides whether the listener ends up holding anything, and
        it is applied at reception so that a listener outside the audience produces a recorded
        refusal rather than being quietly skipped here.
        """
        out = []
        for utterance in inbound:
            if utterance.actor == member.entity_id or member.entity_id in utterance.chain:
                continue
            if member.entity_id not in self.audience(utterance.actor):
                continue
            out.append(utterance)
        return tuple(out)

    def _deliver_focal(self, member, heard, window):
        """Absorb what was heard and appraise it. Returns how many claims the agent derived from it.

        Two mechanisms, chosen by what was said and not by who is listening:

        * **formal speech** - a firm's ``Disclosure`` or an institution's ``Issuance`` - is received
          through ``disclosure.receive``, whose audience lattice decides whether the listener holds
          anything at all. A listener outside the audience holds nothing, and that refusal is
          recorded as an unaccepted :class:`Delivery` rather than as silence.
        * **gossip** - what a person said - is written into the listener's ``heard:`` scope with the
          speaker as its evidence source and the relay chain in its locator.
        """
        if not heard:
            return 0
        agent = member.agent
        added = []
        for utterance in heard:
            added.extend(self._absorb(member, utterance, at=window[0], note='heard'))
            if member.kind == 'institution' and utterance.kind == 'stance':
                subject, decided, _stance = utterance.content
                if decided in ADVANCING:
                    member.agent.requests.append((str(subject), utterance.actor))
        if not added:
            return 0
        store = agent.store
        records = tuple(store.claim(cid) for cid in dict.fromkeys(added) if cid in store._claims)
        if not records:
            return 0
        # Appraise what was heard before the agent perceives, so a decision this tick can rest on
        # something a colleague said last tick. A firm has its own re-entry point for the same job.
        if member.kind == 'person':
            derived = think(store, agent.rules(), since=Thought(added=records), max_rounds=3)
            return len(derived.added)
        if member.kind == 'firm':
            thought = member.agent.firm.reconsider(tuple(dict.fromkeys(added)))
            return len(thought.added)
        return 0

    def _absorb(self, member, utterance, *, at, note='heard'):
        """One utterance into one focal store. Returns the claim ids it added.

        Shared by delivery and by the replay a promotion does, so a claim that arrives live and the
        same claim replayed out of the event log are absorbed by exactly one code path.
        """
        agent, tick = member.agent, self.ticks
        store, scope = agent.store, agent.heard_scope
        if utterance.formal:
            return self._receive(member, utterance, at=at, note=note)
        edits = []
        for claim in self._gossip_claims(member, utterance, scope):
            edits.append(tc.Tell(claim, (tc.Evidence(
                tc.Ref('agent:%s' % utterance.actor), at,
                locator='%s via %s%s' % (utterance.claim_key,
                                         '->'.join(utterance.chain + (utterance.actor,)),
                                         '' if note == 'heard' else ' (replayed at tick %d)' % tick),
                method=('hearsay:%s' if note == 'heard' else 'event-log:%s') % utterance.origin_actor,
                confidence=self._hearsay_confidence(member, utterance)),)))
        self.channel.deliver(Delivery(tick, member.entity_id, member.tier, utterance.id,
                                      utterance.claim_key, utterance.actor, utterance.hops,
                                      utterance.fidelity, utterance.origin_tick, utterance.origin_actor,
                                      accepted=bool(edits)))
        if not edits:
            return ()
        commit = store.apply(tc.Patch(tuple(edits), store.revision))
        for claim_id in commit.added:
            agent.heard_at[claim_id] = tick     # a rumour lapses; see ``_expire``
        return tuple(commit.added)

    def _receive(self, member, utterance, *, at, note='heard'):
        """Formal organizational speech, through ``disclosure.receive`` and its audience lattice.

        The claim it writes is **not** registered for hearsay expiry: a filing and an instrument are
        on the record, and the record does not go stale the way a rumour does. That asymmetry is the
        difference between the two speech acts, not an oversight.
        """
        receive = self.seams.receive
        agent, tick = member.agent, self.ticks
        if receive is None:                     # cannot happen: ``formal`` needs the module present
            return ()
        reception = receive(agent.store, utterance.speech, receiver=member.entity_id, at=at,
                            scope=agent.heard_scope,
                            confidence=self._hearsay_confidence(member, utterance))
        refused = getattr(reception, 'reason', None) if isinstance(reception, tc.Unknown) else None
        self.channel.deliver(Delivery(tick, member.entity_id, member.tier, utterance.id,
                                      utterance.claim_key, utterance.actor, utterance.hops,
                                      utterance.fidelity, utterance.origin_tick, utterance.origin_actor,
                                      accepted=refused is None, refused=refused,
                                      channel=getattr(reception, 'channel', None)))
        if refused is not None:
            return ()
        return (reception.claim_id,) if reception.claim_id in agent.store._claims else ()

    def _hearsay_confidence(self, member, utterance):
        """What a relayed claim is worth. Uses the transmission seam when it is installed.

        ``transmission.hearsay_weight`` caps hearsay strictly below direct observation and lets the
        chain enter through ``upstream`` rather than as an exponent, which is a better definition
        than the flat fidelity this module would otherwise use; without it the fidelity itself is
        recorded, labelled ``relayed`` so nobody mistakes it for a calibrated probability.
        """
        weight = self.seams.hearsay_weight
        if weight is None:
            return tc.Score(round(utterance.fidelity, 4), 'relayed')
        trust = 0.5
        relations = getattr(member.agent, 'relations', None)
        if isinstance(relations, dict) and utterance.actor in relations:
            trust = float(relations[utterance.actor].get('trust', 0.5))
        return tc.Score(weight(trust, self.relay_fidelity, 1.0, utterance.fidelity), 'hearsay')

    def _gossip_claims(self, member, utterance, scope):
        """The claims one *informal* utterance becomes in one listener's store. Nothing is fact.

        Every claim lands in the listener's ``heard:`` scope with the speaker as its evidence
        source, so ``explain`` on anything derived from it names the agent that said it and the
        chain it travelled - and the published record underneath, when the content had one.
        """
        subject, predicate, obj = utterance.content
        out = []
        if utterance.kind == 'stance':
            relation = 'advanced_by' if obj == 'advancing' else 'contested_by'
            out.append(tc.Claim(tc.Ref(str(subject)), relation, tc.Ref(utterance.origin_actor), scope=scope))
        elif utterance.kind == 'authority':
            relation = 'reported_by' if obj == 'holds' else 'refused_by'
            out.append(tc.Claim(tc.Ref(str(subject)), relation, tc.Ref(utterance.origin_actor), scope=scope))
        elif utterance.kind in ('regime', 'disclosure'):
            out.append(tc.Claim(tc.Ref(str(subject)), str(predicate), obj, scope=scope))
        if member.kind == 'person':
            out.append(tc.Claim(member.agent.me, 'heard_from', tc.Ref(utterance.actor), scope=scope))
        return out

    def _expire(self, tick):
        """Retract and forget heard claims older than the declared hearsay half-life."""
        dropped = 0
        for member in self.order():
            if not member.focal or not getattr(member.agent, 'heard_at', None):
                continue
            store = member.agent.store
            stale = [cid for cid, at in sorted(member.agent.heard_at.items())
                     if tick - at >= self.hearsay_ticks and cid in store._claims]
            if not stale:
                continue
            commit = store.apply(tc.Patch(tuple(tc.Retract(cid, 'hearsay lapsed') for cid in stale),
                                          store.revision))
            store.forget(commit.retracted)
            for cid in commit.retracted:
                member.agent.heard_at.pop(cid, None)
            dropped += len(commit.retracted)
        return dropped

    # -- acting ------------------------------------------------------------------------
    def _act(self, member, window, record):
        if member.kind == 'person':
            return self._act_person(member, window, record)
        if member.kind == 'firm':
            return self._act_firm(member, window, record)
        return self._act_institution(member, window, record)

    def _act_person(self, member, window, record):
        agent, tick = member.agent, self.ticks
        report = agent.tick(window)
        record.perception[member.entity_id] = report.perception.bound if report.perception else None
        record.motifs[member.entity_id] = report.motif
        record.viability[member.entity_id] = report.viability
        decision = self._person_decision(tick, member, report)
        record.decisions += (decision,)
        said = []
        if not decision.is_unknown and decision.subject:
            # The utterance names *which* decision it was, not only its direction: a decision to
            # move a measure and a decision to work the committee that holds it are different
            # propositions, they reach different listeners differently, and only one of them is a
            # request an institution can act on.
            stance = 'contesting' if decision.outcome in CONTESTING else 'advancing'
            said.append(self._utter(member, 'stance', (decision.subject, decision.outcome, stance),
                                    salience=0.8 if stance == 'contesting' else 0.7,
                                    memo='%s: %s' % (decision.outcome, decision.subject)))
        said.extend(self._relay(member, tick, record))
        member.said += len(said)
        return said[:self.utter_per_tick + self.relay_per_tick]

    @staticmethod
    def _person_decision(tick, member, report):
        if report.unknown is not None:
            return DecisionRecord(tick, member.entity_id, 'person', 'unknown',
                                  unknown_reason=report.unknown.reason, detail=report.unknown.detail)
        kind, _, rest = report.decision.partition(':')
        chosen = getattr(member.agent, 'last_decision', None)
        subject = None
        if chosen and len(chosen) >= 2:
            kind, subject = str(chosen[0]), (str(chosen[1]) or None)
        return DecisionRecord(tick, member.entity_id, 'person', kind, subject=subject,
                              detail=rest.strip() or None)

    def _act_firm(self, member, window, record):
        social, firm, tick = member.agent, member.agent.firm, self.ticks
        at = window[1]
        if social.perceived == 0:
            if social.objectives:
                firm.set_objective(social.objectives, at=at)
            for role_id, aims in social.role_objectives:
                if role_id in firm.charter.roles and aims:
                    firm.set_objective(aims, role_id=role_id, at=at)
        for quarter in firm.quarters:
            end = _as_datetime(quarter.period_end)
            if end is not None and window[0] <= end < window[1]:
                firm.perceive(quarter)
                social.perceived += 1
        reading = firm.read(as_of=at)
        record.regimes[member.entity_id] = reading.regime
        record.viability[member.entity_id] = reading.viability.value
        said = []
        if social.options and social.procedure in firm.charter.procedures:
            decision = firm.decide(social.procedure, social.options, at=at)
            social.last_decision = decision
            changed = bool(decision.chosen is not None and decision.firm_preferred is not None
                           and decision.chosen.id != decision.firm_preferred.id)
            record.decisions += (DecisionRecord(
                tick, member.entity_id, 'firm',
                decision.chosen.id if decision.chosen is not None else 'none',
                unknown_reason=None if decision.chosen is not None else (decision.outcome.blocked_at
                                                                         or 'no_feasible_option'),
                subject=decision.proposed.id if decision.proposed is not None else None,
                divergences=tuple((d.role_id, d.firm_choice, d.role_choice) for d in decision.divergences),
                outcome_changed=changed, agenda_cost=decision.agenda_cost),)
        said.extend(self._speak_firm(member, social, reading, at, record))
        said.extend(self._relay(member, tick, record))
        member.said += len(said)
        return said[:self.utter_per_tick + self.relay_per_tick]

    def _speak_firm(self, member, social, reading, at, record):
        """What a firm says this tick. With the disclosure desk installed, it *discloses*.

        Two statements at most, in this order and for this reason: the regime reading as a press
        release, because that is the thing other agents in the population act on, and the first
        held claim it has not yet said as a filing, because the belief/statement gap is the whole
        point of having a desk. A statement whose authorization does not hold is not made: the
        refusal is on the ``ActLog``, in the store as an ``ultra_vires`` claim, and here as a
        decision record with ``authority`` set.
        """
        tick = self.ticks
        if social.desk is None:
            return [self._utter(member, FIRM_UTTERANCE,
                                (member.entity_id, 'regime', reading.regime),
                                salience=0.8 if reading.regime in ('distressed', 'pressured') else 0.4,
                                memo='regime %s as of %s' % (reading.regime, str(at)[:10]),
                                records=tuple(s.record_id for s in reading.sources[:2]))]
        role_id = social.speaking_role()
        if role_id is None:
            record.decisions += (DecisionRecord(
                tick, member.entity_id, 'firm', 'said_nothing', authority='unknown',
                reasons=('no role in this charter is declared to hold approve_disclosure',)),)
            return []
        out = []
        plan = [('press_release', None, (member.entity_id, 'regime', reading.regime),
                 0.8 if reading.regime in ('distressed', 'pressured') else 0.4)]
        unsaid = next((r for r in social.desk.held(at=at) if r.id not in social.said_claims), None)
        if unsaid is not None:
            plan.append(('filing', unsaid.id, None, 0.5))
        for form, claim_id, statement, salience in plan[:self.utter_per_tick]:
            speech = social.desk.disclose(claim_id, form=form, audience='public', role_id=role_id,
                                          effective_date=at, statement=statement)
            if getattr(speech, 'status', '') != 'disclosed':
                record.decisions += (DecisionRecord(
                    tick, member.entity_id, 'firm', 'disclosure_refused',
                    subject=form, authority='unknown' if speech.status == 'unknown' else 'fails',
                    reasons=tuple(speech.reasons)),)
                continue
            if claim_id is not None:
                social.said_claims.add(claim_id)
            out.append(self._utter(member, 'disclosure', tuple(speech.statement), salience=salience,
                                   memo=speech.line(), speech=speech,
                                   records=tuple(s.record_id for s in (speech.sources or ())[:2])))
        return out

    def _act_institution(self, member, window, record):
        social, institution, tick = member.agent, member.agent.institution, self.ticks
        at = window[1]
        reading = institution.read(as_of=at)
        record.legitimacy[member.entity_id] = reading.legitimacy.value
        record.regimes[member.entity_id] = reading.regime
        said, requests = [], []
        seen = set()
        for measure, asked_by in social.requests:
            if measure in seen:
                continue
            seen.add(measure)
            requests.append((measure, asked_by))
        social.requests = []
        for measure, asked_by in sorted(requests)[:social.acts_per_tick]:
            said.extend(self._issue(member, social, measure, asked_by, at, record))
        said.extend(self._relay(member, tick, record))
        member.said += len(said)
        return said[:self.utter_per_tick + self.relay_per_tick]

    def _issue(self, member, social, measure, asked_by, at, record):
        """The institution's answer to one request. With the docket installed, it *issues*.

        Either way the authorization is the same and it is grounded: the acting seat must hold the
        power, and the measure must be inside the role's declared jurisdiction, which for a
        committee **is** the set of measures published as referred to it. A refused act never
        reaches the executor, never becomes an utterance, and is on the record three ways - the
        receipt, the ``ActLog`` and an ``ultra_vires`` claim - which is why a refusal is silence to
        the population and evidence to a reader.
        """
        tick, institution = self.ticks, social.institution
        if social.docket is not None:
            issuance = social.docket.issue(INSTITUTION_INSTRUMENT, measure, role_id=social.role_id,
                                           audience='public', recipients=(asked_by,),
                                           effective_date=at,
                                           memo='asked by %s to move %s' % (asked_by, measure))
            log = institution.log.records[-1] if len(institution.log) else None
            status = 'holds' if issuance.issued else \
                ('unknown' if getattr(log, 'status', '') == 'unknown' else 'fails')
            record.decisions += (DecisionRecord(
                tick, member.entity_id, 'institution',
                INSTITUTION_INSTRUMENT if issuance.issued else 'refused', subject=measure,
                authority=status, reasons=tuple(issuance.reasons),
                detail='asked by %s' % asked_by),)
            if not issuance.issued:
                return []
            return [self._utter(member, 'instrument', tuple(issuance.statement),
                                salience=0.6, memo=issuance.line(), speech=issuance)]
        receipt = institution.act(InstitutionalAct(power='report_measure', subject=measure),
                                  role_id=social.role_id, at=at)
        status = 'holds' if receipt.status == 'applied' else 'fails'
        reasons = () if receipt.error is None else (receipt.error,)
        if receipt.error and receipt.error.startswith('unknown:'):
            status = 'unknown'
        record.decisions += (DecisionRecord(
            tick, member.entity_id, 'institution',
            'reported' if status == 'holds' else 'refused', subject=measure,
            authority=status, reasons=reasons, detail='asked by %s' % asked_by),)
        return [self._utter(member, 'authority', (measure, 'report_measure', status),
                            salience=0.85 if status != 'holds' else 0.5,
                            memo='%s %s for %s' % ('reported' if status == 'holds' else 'refused',
                                                   measure, asked_by))]

    def _relay(self, member, tick, record):
        """Pass on the loudest thing this agent heard last tick, one hop further and one step fainter.

        Relay is what makes propagation a *chain* rather than a star, and it is the only thing a
        compact agent does. The fidelity decay is declared (:data:`RELAY_FIDELITY`) unless the
        transmission seam overrides it.
        """
        out = []
        candidates = sorted(member.inbox, key=lambda u: (-u.salience * u.fidelity, u.claim_key, u.id))
        for utterance in candidates[:self.relay_per_tick]:
            fidelity = utterance.fidelity * self.relay_fidelity
            if fidelity < RELAY_FLOOR:
                continue
            out.append(Utterance(
                id='%s@%d#%s' % (member.entity_id, tick, utterance.claim_key[-8:]),
                tick=tick, actor=member.entity_id, kind=utterance.kind, content=utterance.content,
                salience=round(utterance.salience * fidelity, 6), claim_key=utterance.claim_key,
                origin_tick=utterance.origin_tick, origin_actor=utterance.origin_actor,
                chain=utterance.chain + (utterance.actor,), fidelity=round(fidelity, 6),
                records=utterance.records, memo='relayed: %s' % utterance.memo,
                # Formal speech keeps its own audience lattice when it is passed on: a relayed
                # private briefing still reaches nobody outside its recipients, and ``receive``
                # is the thing that enforces it rather than this loop.
                speech=utterance.speech))
            record.relayed += 1
            if member.compact is not None:
                member.compact.relayed += 1
        return out

    def _utter(self, member, kind, content, *, salience=0.5, memo='', records=(), speech=None):
        key = claim_key(content)
        return Utterance(id='%s@%d#%s' % (member.entity_id, self.ticks, key[-8:]),
                         tick=self.ticks, actor=member.entity_id, kind=kind, content=tuple(content),
                         salience=float(salience), claim_key=key, origin_tick=self.ticks,
                         origin_actor=member.entity_id, chain=(), fidelity=1.0,
                         records=tuple(records), memo=memo, speech=speech)

    # -- measurement -------------------------------------------------------------------
    def _sample_divergence(self, tick):
        """Compare each focal person's beliefs with the live published edges, predicate by predicate."""
        out = []
        for member in self.order():
            if not member.focal or member.kind != 'person':
                continue
            grounding, store = member.agent.grounding, member.agent.store
            for predicate in self.divergence_predicates:
                spec = next((s for s in grounding.horizon.specs if s.predicate == predicate), None)
                index_predicate = spec.index_predicate if spec else predicate
                direction = spec.direction if spec else 'out'
                try:
                    rows = grounding.graph_facts(index_predicate, direction=direction, limit=200)
                except ValueError:
                    continue
                published = {str(row['object'] if direction == 'out' else row['subject']) for row in rows}
                held = {str(_id(r.claim.object)) for r in store.claims(member.agent.me, predicate)}
                overlap = len(held & published)
                out.append(DivergenceSample(tick, member.entity_id, predicate, len(held),
                                            len(published), overlap, len(held - published),
                                            len(published - held)))
        return tuple(out)

    def _sample_provenance(self):
        """Where each focal agent's live beliefs came from: the record, another agent, or itself.

        This is the other half of divergence. The predicate-by-predicate distance says how far a
        belief *set* sits from a published edge set; this says how much of the whole store the
        record ever supported at all. A claim is counted once, by the first of: derived from
        premises, published (a ``dataset:`` source), **told** (an ``agent:`` source for gossip or a
        ``disclosure:`` source for a filing or an instrument - either way, it arrived through the
        channel), and otherwise internal (memory, a reading, an ascription, a charter, a procedure).
        """
        out = {}
        for member in self.order():
            if not member.focal:
                continue
            counts = {'published': 0, 'hearsay': 0, 'derived': 0, 'internal': 0}
            for record in member.agent.store._claims.values():
                if record.retracted:
                    continue
                evidence = record.evidence[0] if record.evidence else None
                if evidence is None:
                    counts['internal'] += 1
                elif evidence.derived_from:
                    counts['derived'] += 1
                elif evidence.source.kind == 'dataset':
                    counts['published'] += 1
                elif evidence.source.kind in ('agent', 'disclosure'):
                    counts['hearsay'] += 1
                else:
                    counts['internal'] += 1
            total = sum(counts.values())
            counts['total'] = total
            counts['from_the_record_share'] = round(counts['published'] / total, 6) if total else None
            out[member.entity_id] = counts
        return out

    def _tier_counts(self):
        counts = {tier: 0 for tier in TIERS}
        for member in self.members.values():
            counts[member.tier] = counts.get(member.tier, 0) + 1
        counts[COHORT] = counts.get(COHORT, 0) + len(self.cohorts)
        return counts

    # ------------------------------------------------------------------ promotion

    def promote(self, entity_id):
        """Compact (or cohort member) -> focal. Seeded from published records and the event log only.

        Nothing is carried over from the compact numbers except as a *label*: the state a compact
        agent tracked is not evidence and does not become a claim. A facet with no published record
        stays ``Unknown``, exactly as at construction, and the report says which.
        """
        member = self.members.get(entity_id)
        sampled = False
        if member is None:
            cohort = next((c for c in self.cohorts.values() if entity_id in c.members), None)
            if cohort is None:
                return None
            sampled = True
            cohort.disaggregated += (entity_id,)
            member = Member(entity_id, cohort.kind, COMPACT, config={})
            member.compact = CompactState(entity_id, cohort.kind, sampled=True)
            member.cohort_id = cohort.id
            self.members[entity_id] = member
            self._audience = None
        if member.tier == FOCAL:
            return None
        sampled = sampled or bool(member.compact and member.compact.sampled)
        builder = {'person': self._make_person, 'firm': self._make_firm,
                   'institution': self._make_institution}[member.kind]
        member.tier = FOCAL
        builder(member)
        store = member.agent.store
        from_records = len(store._claims)
        if member.kind == 'person':
            unknown = tuple(member.agent.report.unknown)
        elif member.kind == 'firm':
            unknown = tuple(member.agent.firm.unknown())
        else:
            unknown = tuple(member.agent.institution.unknown())
        replayed, log_claims = self._replay(member)
        report = PromotionReport(entity_id, self.ticks, member.kind, from_records, log_claims,
                                 replayed, unknown, sampled, invented=0)
        member.promotions += (report,)
        member.compact = None
        self._promotions.append(report)
        self._audience = None
        return report

    def _replay(self, member):
        """Replay the shared event log into a freshly promoted store, as hearsay and nothing more.

        The log is what the population said. Replaying it is not inventing history: each replayed
        claim keeps the speaker as its evidence source, the relay chain in its locator and the
        fidelity it had, so a reader can tell a replayed rumour from a published record at a glance.
        """
        logged = self.channel.about(member.entity_id, limit=REPLAY_LIMIT)
        replayed, added = [], []
        for utterance in logged:
            added.extend(self._absorb(member, utterance,
                                      at=self.clock.window(utterance.tick)[1], note='replay'))
            replayed.append(utterance.claim_key)
        return tuple(dict.fromkeys(replayed)), len(dict.fromkeys(added))

    def demote(self, entity_id):
        """Focal -> compact. Consolidates to a few numbers and records everything dropped."""
        member = self.members.get(entity_id)
        if member is None or not member.focal:
            return None
        agent = member.agent
        store = agent.store
        by_predicate, scopes, derivations = {}, set(), 0
        for record in store._claims.values():
            by_predicate[record.claim.predicate] = by_predicate.get(record.claim.predicate, 0) + 1
            scopes.add(str(record.claim.scope) if record.claim.scope else 'world')
            if record.evidence and record.evidence[0].derived_from:
                derivations += 1
        dropped = sum(by_predicate.values())
        state, viability, episodes, relations = self._consolidate(member)
        report = DemotionReport(
            entity_id, self.ticks, member.kind, dropped,
            tuple(sorted(by_predicate.items())), tuple(sorted(scopes)), episodes, relations,
            derivations, (('state', state), ('viability', 'unknown' if viability is None
                                             else round(viability, 4)),
                          ('claims', dropped)))
        member.tier = COMPACT
        member.agent = None
        member.compact = CompactState(entity_id, member.kind, label=member.label, state=state,
                                      viability=viability, claims_at_demotion=dropped, dropped=report)
        member.demotions += (report,)
        self._demotions.append(report)
        self._audience = None
        return report

    def _consolidate(self, member):
        """What survives a demotion: a state label, a viability number, and two counts."""
        agent = member.agent
        if member.kind == 'person':
            return (agent.motif, agent.last_viability, len(agent.episodes), len(agent.relations))
        if member.kind == 'firm':
            reading = agent.firm.read(as_of=self.clock.window(max(0, self.ticks - 1))[1])
            return (reading.regime, reading.viability.value, 0, len(agent.firm.ties))
        reading = agent.institution.read(as_of=self.clock.window(max(0, self.ticks - 1))[1])
        return (reading.regime, reading.legitimacy.value, 0, len(agent.institution.holders))

    # ------------------------------------------------------------------ budget

    def budget(self, *, memory_budget_bytes=8 * 1024 ** 3, resident=None):
        """Measure what a focal agent costs here, and what ceiling that buys.

        ``resident`` is a measured resident bytes-per-claim (see :func:`measure_resident_bytes`);
        without one the ceiling falls back to the serialized measurement and then to the declared
        1.2 KB. The ceiling is a *memory* ceiling and is labelled as one: it says nothing about how
        many agents can be ticked per second.
        """
        stores = [m.agent.store for m in self.members.values() if m.focal]
        claims = [len(store._claims) for store in stores]
        return FocalBudget(focal_agents=len(stores), claims_live=sum(claims),
                           claims_per_focal=(sum(claims) / len(claims)) if claims else 0.0,
                           max_claims=max(claims) if claims else 0,
                           serialized_bytes_per_claim=measure_claim_bytes(stores),
                           resident_bytes_per_claim=resident,
                           memory_budget_bytes=int(memory_budget_bytes))

    # ------------------------------------------------------------------ inspection

    def population(self):
        out = []
        for entity_id, member in sorted(self.members.items()):
            row = {'entity_id': entity_id, 'kind': member.kind, 'tier': member.tier,
                   'label': member.label, 'said': member.said,
                   'audience': len(self.audience(entity_id)),
                   'promotions': [p.to_json() for p in member.promotions],
                   'demotions': [d.to_json() for d in member.demotions]}
            if member.focal:
                row['claims_live'] = len(member.agent.store._claims)
            elif member.compact is not None:
                row['compact'] = member.compact.to_json()
            out.append(row)
        return out

    def to_json(self):
        return {'clock': self.clock.to_json(), 'seed': self.seed, 'ticks': self.ticks,
                'simultaneity': self.simultaneity, 'simultaneity_rationale': self.simultaneity_rationale,
                'index': str(getattr(self.index, 'path', '')) or None,
                'constants': {'claim_bytes': CLAIM_BYTES, 'relay_fidelity': self.relay_fidelity,
                              'relay_floor': RELAY_FLOOR, 'hearsay_ticks': self.hearsay_ticks,
                              'audience_cap': self.audience_cap, 'utter_per_tick': self.utter_per_tick,
                              'relay_per_tick': self.relay_per_tick, 'replay_limit': REPLAY_LIMIT},
                'seams': {name: getattr(self.seams, name) is not None for name in self.seams._fields},
                'population': self.population(), 'cohorts': [c.to_json() for c in
                                                             sorted(self.cohorts.values(), key=lambda c: c.id)],
                'utterances': len(self.channel), 'deliveries': len(self.channel.deliveries)}

    def summary(self):
        counts = self._tier_counts()
        out = ['society over %d tick(s) from %s, window %d day(s), seed %d'
               % (self.ticks, self.clock.start.date().isoformat(), self.clock.window_days, self.seed),
               '  tiers: ' + ', '.join('%s=%d' % (tier, counts.get(tier, 0)) for tier in TIERS),
               '  simultaneity: %s (an act is a percept in the NEXT tick)' % self.simultaneity,
               '  channel: %d utterance(s), %d delivery(ies)' % (len(self.channel),
                                                                 len(self.channel.deliveries))]
        absent = [name for name in self.seams._fields if getattr(self.seams, name) is None]
        if absent:
            out.append('  seams not installed (fallbacks in use): ' + ', '.join(absent))
        return out

    def close(self):
        if self.index is not None:
            self.index.close()
            self.index = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # ------------------------------------------------------------------ helpers

    def _label(self, entity_id):
        if self.index is None:
            return entity_id
        record = self.index.entity(entity_id)
        return (record or {}).get('label') or entity_id


# --------------------------------------------------------------------------- config


REQUIRED_CLOCK = ('start',)
KINDS = ('persons', 'firms', 'institutions')


def validate_config(config):
    """Check a society config and return it. Raises ``ValueError`` with what is wrong."""
    if not isinstance(config, dict):
        raise ValueError('a society config is a JSON object')
    clock = config.get('clock')
    if not isinstance(clock, dict) or not all(key in clock for key in REQUIRED_CLOCK):
        raise ValueError('config.clock needs at least {"start": "<ISO date>"}')
    _parse_date(clock['start'])
    seen = set()
    for key in KINDS:
        entries = config.get(key, ())
        if not isinstance(entries, (list, tuple)):
            raise ValueError('config.%s must be a list' % key)
        for entry in entries:
            if not isinstance(entry, dict) or 'entity_id' not in entry:
                raise ValueError('every entry in config.%s needs an entity_id' % key)
            if entry.get('tier', FOCAL) not in TIERS:
                raise ValueError('unknown tier %r (expected one of %s)'
                                 % (entry.get('tier'), ', '.join(TIERS)))
            if entry['entity_id'] in seen:
                raise ValueError('duplicate entity_id %r in the config' % entry['entity_id'])
            seen.add(entry['entity_id'])
    for entry in config.get('cohorts', ()):
        if 'id' not in entry:
            raise ValueError('every cohort needs an id')
    for key in ('promote', 'demote'):
        for entry in config.get(key, ()):
            if 'at_tick' not in entry or 'entity_id' not in entry:
                raise ValueError('every %s entry needs at_tick and entity_id' % key)
    return config


def load_config(path):
    from pathlib import Path
    return validate_config(json.loads(Path(path).read_text(encoding='utf-8')))


def claim_key(content):
    """The speaker-independent identity of a proposition: a content-addressed claim id.

    Two agents saying the same thing produce the same key, and a claim relayed five times keeps it.
    That is what makes "how far did *this claim* travel" a question with an answer, and it needs no
    registry: ``tc.Claim.id`` is already a hash of the proposition.
    """
    subject, predicate, obj = content
    return tc.Claim(tc.Ref(str(subject)), str(predicate),
                    tc.Ref(obj) if _is_entity(obj) else obj).id


def _is_entity(value):
    return isinstance(value, str) and ':' in value and ' ' not in value


def _id(value):
    return getattr(value, 'id', value)


def _plain(value):
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)


def _parse_date(value):
    text = str(value)
    parsed = _dt.datetime.fromisoformat(text.replace('Z', '+00:00')) if len(text) > 10 \
        else _dt.datetime.fromisoformat(text)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _as_datetime(value):
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=UTC)
    if value is None:
        return None
    try:
        return _parse_date(str(value)[:10])
    except ValueError:
        return None


def _declared_quarter(entry):
    """A quarter declared in the config rather than read from a filing. Labelled as declared."""
    source = Source('society_config', 'declared', 'config:%s' % entry.get('period_end', ''))
    return ca.Quarter(period_end=_dt.date.fromisoformat(str(entry['period_end'])),
                      filed=_dt.date.fromisoformat(str(entry['filed'])) if entry.get('filed') else None,
                      cash=entry.get('cash'), revenue=entry.get('revenue'),
                      costs=entry.get('costs'), capex=entry.get('capex'),
                      sources=(source,),
                      field_sources=tuple((name, source) for name in
                                          ('cash', 'revenue', 'costs', 'capex') if entry.get(name) is not None))


def open_index(path=None, *, data_root=None, cache_mb=64):
    """The society's world: the unified-graph index, read-only."""
    return EvidenceIndex(path or default_index_path(data_root), cache_mb=cache_mb)


__all__ = ['ADVANCING', 'AUDIENCE_CAP', 'CLAIM_BYTES', 'COHORT', 'COMPACT', 'CONTESTING',
           'Channel', 'Clock', 'Cohort', 'CompactState', 'DecisionRecord', 'Delivery',
           'DemotionReport', 'DivergenceSample', 'FOCAL', 'FocalBudget', 'HEARSAY_TICKS',
           'INSTITUTION_INSTRUMENT', 'Member', 'PromotionReport', 'RELAY_FIDELITY', 'RELAY_FLOOR',
           'Seams', 'SocialFirm', 'SocialInstitution', 'SocialPerson', 'Society', 'TIERS',
           'TickRecord', 'Utterance', 'claim_key', 'firm_social_rules', 'load_config',
           'measure_claim_bytes', 'measure_resident_bytes', 'open_index', 'seams', 'social_rules',
           'validate_config']
