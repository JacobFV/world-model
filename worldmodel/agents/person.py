"""The person agent: the civ-sim mind, grounded in a real entity from the unified graph.

One tick is the civ-sim loop with a real world underneath it::

    perceive      what the entity's neighbourhood, role and relationships expose this window,
                  ranked by salience and capped (``grounding.perceive``) -> a snapshot Fragment
    remember      the window's events arrive as knowledge claims; salient ones become episodes
    think         appraisal rules derive appraisals, wants and meanings, each citing its premises;
                  rules that cross modules are gated by the coupling dial kappa
    read affect   the seven structural measures; the motif is the nearest prototype under
                  asymmetric transition costs (``worldmodel.agents.affect``)
    recall        the most salient relevant episode is brought to mind and cited by the decision
    model others  targets with high ascribed agency get a ``model:<id>`` scope: what the agent
                  takes them to want and believe. This is what raises counterfactual weight.
    choose        ``tc.choose`` over intentions under hard constraints; ``Unknown`` is an honest
                  non-decision and is recorded as one
    consolidate   repeated episodes become standing beliefs with pointers back to the episodes
                  that made them; the rest fade

There is no language model anywhere in the loop, nothing is fitted to an outcome, and nothing
here is ``validated``. The affect readings are structural measures over the agent's own
processing; they are not evidence that anything is experienced.
"""
import datetime as _dt
import math
import random
from dataclasses import dataclass, field

from . import load_tensacode
from .affect import READINGS, SIGNS, read_affect, soft_viability
from .grounding import LEGISLATOR, TERMINAL_STAGES, EvidenceIndex, default_index_path, perceive, seed_store

tc = load_tensacode()
from tensacode import cognition as _cognition  # noqa: E402  (only importable once tensacode is present)
explain_claim, Fragment, Rule, integrate, think = (
    _cognition.explain, _cognition.Fragment, _cognition.Rule, _cognition.integrate, _cognition.think)
V = tc.Var
UTC = _dt.timezone.utc
PERCEPT = tc.Ref('scope:percept')

#: Ascription alpha = (agency, phenomenality) by entity namespace. The two dissociate: a
#: campaign committee is highly agentive and not experiential at all; a bill is neither.
#: A target with agency at or above ``TOM_AGENCY`` gets modelled with the agent template.
ASCRIPTION = {
    'bioguide': (0.9, 0.9), 'icpsr': (0.9, 0.9), 'opensanctions': (0.8, 0.8),
    'fec': (0.85, 0.02), 'congress': (0.7, 0.02), 'lei': (0.85, 0.02), 'sec': (0.85, 0.02),
}
BILL_PREFIXES = ('congress:bill:', 'congress:rollcall:')
DEFAULT_ASCRIPTION = (0.3, 0.05)
TOM_AGENCY = 0.6

#: Intentions a legislator agent may form. The utility terms are authored in ``Person.options``
#: and stated there so they can be argued with; none of them is fitted to an observed outcome.
INTENTIONS = ('advance_measure', 'build_coalition', 'committee_work', 'constituency_service',
              'fundraise', 'campaign', 'counter_opponent', 'hold_position')

#: Every appraisal tag this agent's rules can produce. It is the denominator for the effective
#: rank reading: ``exp(H)`` over the live appraisals divided by how many concerns are possible,
#: so "one concern dominating" and "everything at once" land at the ends of [0, 1].
APPRAISAL_TAGS = ('rebuff', 'efficacy', 'exposure', 'pride', 'pressure', 'standing', 'support',
                  'threat', 'obligation', 'isolation')
assert all(tag in SIGNS for tag in APPRAISAL_TAGS), 'every appraisal tag needs a declared valence sign'

#: What the agent takes a modelled target to want and believe, by the published relation that
#: brought it into view. Authored; it is the agent's ascription, not a claim about the target.
STANCE = {'received_support': ('access', 'supports'), 'opposed_by': ('defeat', 'opposes'),
          'serves_on': ('jurisdiction', 'considers'), 'referred': ('jurisdiction', 'considers'),
          'campaign_committee': ('access', 'represents')}


def ascription_of(entity_id):
    if any(str(entity_id).startswith(prefix) for prefix in BILL_PREFIXES):
        return (0.05, 0.0)
    return ASCRIPTION.get(str(entity_id).partition(':')[0], DEFAULT_ASCRIPTION)


@dataclass
class Episode:
    """One remembered happening, with a salience that decays and is refreshed by recall."""

    at: object
    tag: str
    text: str
    salience: float
    who: tuple = ()
    claim_id: str = None
    last_recalled: object = None

    def decay(self, now, half_life_days=240.0):
        if self.at is None or now is None:
            return self.salience
        days = max(0.0, (now - self.at).total_seconds() / 86400.0)
        return self.salience * math.exp(-days / max(1.0, half_life_days))


@dataclass(frozen=True)
class Intention:
    kind: str
    target: str
    u: float
    premises: tuple
    why: str

    def __repr__(self):
        return '%s(%s)' % (self.kind, self.target or '-')


@dataclass
class TickReport:
    """What one tick did. Everything in it is derived from claims that can be explained."""

    window: tuple = None
    perception: object = None
    added: int = 0
    retracted: int = 0
    derived: int = 0
    affect: dict = field(default_factory=dict)
    motif: str = 'calm'
    viability: float = 0.0
    decision: str = ''
    decision_id: str = None
    unknown: object = None
    recalled: str = None
    episodes: int = 0
    consolidated: tuple = ()

    def to_json(self):
        return {'window': [w.isoformat() for w in self.window] if self.window else None,
                'perception': self.perception.bound if self.perception is not None else None,
                'added': self.added, 'retracted': self.retracted, 'derived': self.derived,
                'affect': dict(self.affect), 'motif': self.motif, 'viability': round(self.viability, 4),
                'decision': self.decision, 'decision_claim': self.decision_id,
                'unknown': None if self.unknown is None else {'reason': self.unknown.reason, 'detail': self.unknown.detail},
                'recalled': self.recalled, 'episodes': self.episodes, 'consolidated': list(self.consolidated)}


class Person:
    """A person agent bound to one real entity id, believing only what records support.

    ``Person.ground(index, 'bioguide:K000367')`` seeds a store from published records and
    returns an agent that can be ticked over a window of real time.
    """

    def __init__(self, grounding, store, *, seed=0, coupling=None, gain=None, half_life_days=240.0,
                 tom_limit=4, working_memory_cap=None):
        self.grounding = grounding
        self.store = store
        self.me = grounding.me
        self.seed = int(seed)
        self.index = grounding.index
        rng = random.Random('%s:%d' % (grounding.entity_id, self.seed))
        # The perceptual axes. Given explicitly, or drawn once per agent from its own seed so
        # that two runs of the same agent are the same agent.
        self.coupling = float(coupling) if coupling is not None else round(0.35 + 0.5 * rng.random(), 3)
        self.gain = float(gain) if gain is not None else round(0.30 + 0.5 * rng.random(), 3)
        self.half_life_days = float(half_life_days)
        self.tom_limit = int(tom_limit)
        self.working_memory_cap = int(working_memory_cap or grounding.horizon.per_tick)
        self.affect = {key: 0.0 for key in READINGS}
        self.motif = 'calm'
        self.last_viability = None
        self.episodes = []
        self.relations = {}
        self.tom = {}
        self.decision = '(nothing yet)'
        self.decision_id = None
        self.decision_unknown = None
        self.recalled = None
        self.ticks = 0
        self.now = None
        self._born = {}
        self.store.declare('motif', functional=True)
        self.store.declare('name', functional=True)
        self.store.declare('chamber', functional=True)
        self.runtime = self._runtime()

    # ------------------------------------------------------------------ construction

    @classmethod
    def ground(cls, index, entity_id, *, horizon=LEGISLATOR, known_at=None, seed=0, store=None, **kwargs):
        """Seed a store from published records and bind an agent to ``entity_id``."""
        store = store if store is not None else tc.Store()
        grounding, report = seed_store(store, index, entity_id, horizon=horizon, known_at=known_at)
        agent = cls(grounding, store, seed=seed, **kwargs)
        agent.report = report
        for claim_id in list(store._claims):
            agent._born.setdefault(claim_id, 0)
        return agent

    @staticmethod
    def _runtime():
        from tensacode.backends.builtin import UtilityChooser
        # A margin, so two options the agent cannot tell apart produce Unknown rather than a
        # coin flip dressed up as a decision.
        return tc.Runtime([UtilityChooser(margin=0.02)])

    # ------------------------------------------------------------------ relations

    def relation(self, other):
        return self.relations.setdefault(str(other), {'trust': 0.5, 'grudge': 0.0, 'gratitude': 0.0,
                                                      'label': 'counterpart', 'episodes': 0})

    def _seen(self):
        out = set()
        for record in self.store.claims():
            obj = getattr(record.claim.object, 'id', record.claim.object)
            out.add(str(obj))
            out.add(str(record.claim.subject))
        return out

    # ------------------------------------------------------------------ the tick

    def tick(self, window):
        """One tick over the half-open real-time window ``(start, end)``."""
        end = window[1]
        self.now = end
        self.ticks += 1
        perception = perceive(self.index, self.grounding, window=window, seen=self._seen(), now=end)

        # One snapshot fragment, so that what is no longer published falls out of working memory
        # and the appraisals resting on it are withdrawn. The locator names both the published
        # record and the dataset version it came from, because a snapshot has a single source.
        snapshot = Fragment(
            tc.Ref('obs:%s' % end.date().isoformat()),
            tuple((p.claim(scope=PERCEPT), '%s in %s' % (p.record_id, p.source)) for p in perception.percepts),
            snapshot_of=PERCEPT, method='perceive', observed_at=end)
        # Events are told outside the snapshot scope, so what happened stays known once the
        # window that carried it has passed.
        events = [Fragment(p.source, ((p.claim(), p.record_id),),
                           method='published:%s' % p.dataset, observed_at=p.observed_at or end)
                  for p in perception.events]
        thought = integrate(self.store, snapshot, *events)
        # On the first tick the agent appraises the whole of what it was seeded with. ``think`` is
        # semi-naive - a match whose premises are all old already fired - so without this the
        # seeded structure (which committees it sits on, which office it holds) would never be
        # appraised at all, only the parts of it that a later percept happened to re-present.
        awakening = _cognition.Thought(added=tuple(self.store.claim(claim_id)
                                                   for claim_id in list(self.store._claims))) \
            if self.ticks == 1 else _cognition.Thought()
        derived = think(self.store, self.rules(), since=thought + awakening, max_rounds=4)
        for record in thought.added + derived.added:
            self._born.setdefault(record.id, self.ticks)

        for percept in perception.events:
            self._remember(percept)

        viability = soft_viability(self.viability_margins())
        reading = read_affect(
            self.store, self.me,
            ([r.id for r in thought.added + derived.added], [r.id for r in thought.retracted + derived.retracted]),
            previous=self.affect, motif=self.motif, viability=viability,
            last_viability=self.last_viability if self.last_viability is not None else viability,
            coupling=self.coupling, gain=self.gain, percept_scope=PERCEPT,
            working_memory_cap=self.working_memory_cap, concerns=len(APPRAISAL_TAGS))
        self.last_viability = viability
        self._record_motif(reading, derived)
        self.affect, self.motif = dict(reading), reading.motif

        self.recalled = self._recall()
        self._theory_of_mind(perception)
        decision, unknown = self._choose()
        consolidated = self._consolidate()

        return TickReport(window=window, perception=perception, added=len(thought.added) + len(derived.added),
                          retracted=len(thought.retracted) + len(derived.retracted), derived=len(derived.added),
                          affect=dict(self.affect), motif=self.motif, viability=viability,
                          decision=decision, decision_id=self.decision_id, unknown=unknown,
                          recalled=self.recalled, episodes=len(self.episodes), consolidated=consolidated)

    # ------------------------------------------------------------------ viability

    def viability_margins(self):
        """Signed distances to the boundaries that make this agent's position viable.

        Every margin is read from what the agent *believes* (its own store), so it moves when
        its beliefs move. They are authored soft boundaries, declared here rather than fitted:
        the design contract forbids fitting anything in this layer to outcomes.
        """
        store, me = self.store, self.me
        in_office = bool(store.claims(me, 'holds_office') or store.claims(me, 'in_office'))
        committees = len(store.claims(me, 'serves_on'))
        sponsored = store.claims(me, 'sponsored')
        advanced = sum(1 for record in sponsored if self._stage_of(record.claim.object) in TERMINAL_STAGES)
        prevailed = sum(1 for record in store.claims(me, 'vote_outcome') if _pair(record.claim.object) == 'prevailed')
        rebuffed = sum(1 for record in store.claims(me, 'vote_outcome') if _pair(record.claim.object) == 'rebuffed')
        support = len(store.claims(me, 'received_support'))
        opposed = len(store.claims(me, 'opposed_by'))
        return {
            'office': 0.9 if in_office else -0.9,
            'standing': _clip((committees - 1) / 4.0),
            'efficacy': _clip(2.0 * advanced / len(sponsored) - 0.2) if sponsored else 0.0,
            'coalition': _clip((prevailed - rebuffed) / float(prevailed + rebuffed)) if (prevailed or rebuffed) else 0.0,
            'support': _clip((support - 3 * opposed) / 8.0),
        }

    def _stage_of(self, obj):
        subject = obj if isinstance(obj, tc.Ref) else tc.Ref(str(obj))
        for record in self.store.claims(subject, 'measure_stage'):
            return str(record.claim.object)
        return None

    # ------------------------------------------------------------------ appraisal rules

    def rules(self):
        """Bottom-up appraisal. Each rule cites its premises, so every appraisal explains itself.

        Rules marked *crossing* move a claim from one module into another (perception into
        plan, event into narrative). Those fire with the coupling dial kappa as their
        confidence, and are dropped below ``0.35``: at low coupling an agent perceives a
        rebuff and it barely reaches planning, which is the compartmentalized regime.
        """
        me = self.me
        kappa = self.coupling
        crossing = kappa >= 0.35
        score = tc.Score(round(kappa, 2), 'coupled')

        def rebuff(bindings, mind):
            if _pair(bindings['o']) != 'rebuffed':
                return
            yield tc.Claim(me, 'appraises', ('rebuff', _first(bindings['o']))), tc.Score(0.7, 'appraisal')
            if crossing:
                yield tc.Claim(me, 'wants', ('build_coalition', _first(bindings['o']))), score

        def prevail(bindings, mind):
            if _pair(bindings['o']) != 'prevailed':
                return
            yield tc.Claim(me, 'appraises', ('efficacy', _first(bindings['o']))), tc.Score(0.6, 'appraisal')

        def absent(bindings, mind):
            if _pair(bindings['o']) != 'absent':
                return
            yield tc.Claim(me, 'appraises', ('exposure', _first(bindings['o']))), tc.Score(0.4, 'appraisal')

        def measure_carried(bindings, mind):
            yield tc.Claim(me, 'appraises', ('pride', bindings['b'].id)), tc.Score(0.85, 'appraisal')
            if crossing:
                yield tc.Claim(me, 'means', ('legacy', bindings['b'].id)), score

        def measure_stuck(bindings, mind):
            yield tc.Claim(me, 'appraises', ('pressure', bindings['b'].id)), tc.Score(0.5, 'appraisal')
            if crossing:
                yield tc.Claim(me, 'wants', ('advance_measure', bindings['b'].id)), score

        def standing(bindings, mind):
            yield tc.Claim(me, 'appraises', ('standing', bindings['c'].id)), tc.Score(0.5, 'appraisal')
            if crossing:
                yield tc.Claim(me, 'wants', ('committee_work', bindings['c'].id)), score

        def supported(bindings, mind):
            yield tc.Claim(me, 'appraises', ('support', bindings['d'].id)), tc.Score(0.55, 'appraisal')
            if crossing:
                yield tc.Claim(me, 'wants', ('fundraise', bindings['d'].id)), score

        def threatened(bindings, mind):
            yield tc.Claim(me, 'appraises', ('threat', bindings['d'].id)), tc.Score(0.75, 'appraisal')
            if crossing:
                yield tc.Claim(me, 'wants', ('campaign', bindings['d'].id)), score

        def obliged(bindings, mind):
            yield tc.Claim(me, 'appraises', ('obligation', bindings['d'].id)), tc.Score(0.45, 'appraisal')

        def isolation(bindings, mind):
            if mind.claims(me, 'serves_on'):
                return
            yield tc.Claim(me, 'appraises', ('isolation', bindings['ch'])), tc.Score(0.45, 'appraisal')

        def jurisdiction(bindings, mind):
            """A measure the agent put its name to was sent to a committee it does not sit on."""
            if mind.claims(me, 'serves_on', bindings['c']):
                return
            yield tc.Claim(me, 'appraises', ('rebuff', bindings['c'].id)), tc.Score(0.35, 'appraisal')

        def pattern_of_defeat(bindings, mind):
            """Memory plus a fresh outcome makes a story. Memory -> narrative: a crossing rule."""
            if _pair(bindings['o']) != 'rebuffed' or not crossing:
                return
            yield tc.Claim(me, 'means', ('pattern_of_defeat', _head(bindings['m']))), score

        def standing_backer(bindings, mind):
            """Support from someone the agent already takes itself to owe. Social -> event."""
            yield tc.Claim(me, 'appraises', ('obligation', bindings['d'].id)), tc.Score(0.6, 'appraisal')
            if crossing:
                yield tc.Claim(me, 'means', ('owed', bindings['d'].id)), score

        return [
            Rule('rebuffed_on_the_floor', ((me, 'vote_outcome', V('o')),), rebuff),
            Rule('prevailed_on_the_floor', ((me, 'vote_outcome', V('o')),), prevail),
            Rule('missed_the_vote', ((me, 'vote_outcome', V('o')),), absent),
            Rule('measure_carried', ((me, 'sponsored', V('b')), (V('b'), 'measure_stage', 'enacted')), measure_carried),
            Rule('measure_referred_and_stuck', ((me, 'sponsored', V('b')), (V('b'), 'measure_stage', 'referred')), measure_stuck),
            Rule('seat_on_a_committee', ((me, 'serves_on', V('c')),), standing),
            Rule('support_arrived', ((me, 'received_support', V('d')),), supported),
            Rule('opposition_declared', ((me, 'opposed_by', V('d')),), threatened),
            Rule('standing_obligation', ((me, 'obliged_to', V('d')),), obliged),
            Rule('no_committee_seat', ((me, 'chamber', V('ch')),), isolation),
            Rule('measure_out_of_jurisdiction',
                 ((me, 'sponsored', V('b')), (V('b'), 'referred', V('c'))), jurisdiction),
            Rule('pattern_of_defeat', ((me, 'vote_outcome', V('o')), (me, 'recalls', V('m'))), pattern_of_defeat),
            Rule('support_from_a_standing_backer',
                 ((me, 'received_support', V('d')), (me, 'obliged_to', V('d'))), standing_backer),
        ]

    # ------------------------------------------------------------------ affect

    def _record_motif(self, reading, derived):
        """Write the motif as a claim whose premises are the appraisals that produced it."""
        live = self.store.claims(self.me, 'appraises')
        new = [record for record in derived.added if record.claim.predicate == 'appraises']
        support = tuple(record.id for record in (new or live[:3]))
        current = self.store.claims(self.me, 'motif')
        if current and str(current[0].claim.object) == reading.motif:
            return
        edits = tuple(tc.Retract(record.id, 'motif changed') for record in current) + (
            tc.Tell(tc.Claim(self.me, 'motif', reading.motif),
                    (tc.Evidence(tc.Ref('reading:affect-geometry'), self.now or _dt.datetime.now(UTC),
                                 method='nearest-prototype@1', confidence=tc.Score(reading.distance, 'distance'),
                                 derived_from=support),)),)
        commit = self.store.apply(tc.Patch(edits, self.store.revision))
        self.store.forget(commit.retracted)
        for claim_id in commit.added:
            self._born[claim_id] = self.ticks

    # ------------------------------------------------------------------ memory

    def _remember(self, percept):
        who = tuple(str(x) for x in ({_first(percept.object) if percept.predicate in ('voted', 'vote_outcome')
                                      else percept.object}) if x)
        salience = min(1.0, percept.salience)
        self.episodes.append(Episode(at=percept.valid_from or percept.observed_at, tag=percept.episode,
                                     text=percept.text, salience=salience, who=who))
        for target in who:
            self.relation(target)['episodes'] += 1
        del self.episodes[:-80]

    def _recall(self):
        """Bring the most salient relevant episode to mind, so the decision that follows can cite it."""
        if not self.episodes:
            return None
        kinds = {_head(record.claim.object) for record in self.store.claims(self.me, 'appraises')}
        prefer = {'rebuff': 'vote', 'efficacy': 'vote', 'pride': 'measure_enacted', 'pressure': 'sponsor',
                  'support': 'donation', 'threat': 'opposition', 'standing': 'committee'}
        wanted = {prefer[k] for k in kinds if k in prefer}

        def weight(episode):
            recent = 0.35 if (episode.last_recalled is not None and self.ticks - episode.last_recalled < 3) else 1.0
            return episode.decay(self.now, self.half_life_days) * (1.6 if episode.tag in wanted else 1.0) * recent

        best = max(self.episodes, key=weight)
        best.last_recalled = self.ticks
        if best.decay(self.now, self.half_life_days) < 0.05:
            return None
        claim = tc.Claim(self.me, 'recalls', (best.tag, best.text))
        commit = self.store.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
            tc.Ref('memory:%s' % (best.at.date().isoformat() if best.at else 'undated')),
            self.now or _dt.datetime.now(UTC), method='recall',
            confidence=tc.Score(round(best.decay(self.now, self.half_life_days), 3), 'salience')),)),),
            self.store.revision))
        for claim_id in commit.added:
            self._born[claim_id] = self.ticks
            best.claim_id = claim_id
        return best.text

    def _consolidate(self):
        """Repeated episodes about one counterpart become a standing belief; the rest fade.

        The standing belief keeps ``derived_from`` pointers to the episodes that made it, so
        "why do you take yourself to be obliged to that committee?" still explains all the way
        down to published record ids.
        """
        by_who = {}
        for episode in self.episodes:
            for who in episode.who:
                by_who.setdefault(who, []).append(episode)
        formed = []
        for who, episodes in sorted(by_who.items()):
            relation = self.relation(who)
            donations = [e for e in episodes if e.tag == 'donation']
            hostile = [e for e in episodes if e.tag == 'opposition']
            joint = [e for e in episodes if e.tag in ('measure_enacted', 'measure_passed_chamber', 'committee')]
            if len(donations) >= 2 and relation['gratitude'] < 0.9:
                relation.update(gratitude=min(1.0, relation['gratitude'] + 0.3), label='backer')
                if self._tell_self(tc.Claim(self.me, 'obliged_to', tc.Ref(who)), donations, 'consolidation'):
                    formed.append('obliged_to %s' % who)
            if len(hostile) >= 2 and relation['grudge'] < 0.9:
                relation.update(grudge=min(1.0, relation['grudge'] + 0.3), label='opponent')
                if self._tell_self(tc.Claim(self.me, 'opposed_by', tc.Ref(who)), hostile, 'consolidation'):
                    formed.append('opposed_by %s' % who)
            if len(joint) >= 2 and relation['trust'] < 0.9:
                relation.update(trust=min(1.0, relation['trust'] + 0.2), label='ally')
                if self._tell_self(tc.Claim(self.me, 'aligned_with', tc.Ref(who)), joint, 'consolidation'):
                    formed.append('aligned_with %s' % who)
        self.episodes = [e for e in self.episodes if e.decay(self.now, self.half_life_days) > 0.04][-60:]
        self._forget_stale()
        return tuple(formed)

    def _tell_self(self, claim, episodes, method):
        premises = tuple(e.claim_id for e in episodes if e.claim_id and e.claim_id in self.store._claims)
        commit = self.store.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
            tc.Ref('%s:tick-%d' % (method, self.ticks)), self.now or _dt.datetime.now(UTC),
            method=method, derived_from=premises),)),), self.store.revision))
        for claim_id in commit.added:
            self._born[claim_id] = self.ticks
        return commit.added[0] if commit.added else None

    def _forget_stale(self):
        """Narrative and recall claims are short-lived; the rest is held by its premises."""
        drop = []
        for claim_id, record in list(self.store._claims.items()):
            age = self.ticks - self._born.get(claim_id, self.ticks)
            if record.claim.predicate in ('means', 'recalls', 'projects') and age > 4:
                drop.append(claim_id)
        if drop:
            self.store.forget(drop)
            for claim_id in drop:
                self._born.pop(claim_id, None)

    # ------------------------------------------------------------------ theory of mind

    def _theory_of_mind(self, perception):
        """Model the targets this agent ascribes agency to, in their own ``model:<id>`` scope.

        Ascription is two-dimensional and the dimensions dissociate: a campaign committee is
        modelled with the agent template (high agency) and carries no phenomenality at all, so
        it gets a mind-model and no harm weight. A bill gets neither.
        """
        # Candidates come from what is in view this tick *and* from the standing relations the
        # agent holds: you model the counterparts you are in a relationship with, not only the
        # ones in front of you this instant. Percepts outrank standing relations.
        targets = {}

        def consider(entity_id, predicate, salience):
            if not isinstance(entity_id, str) or entity_id == self.grounding.entity_id:
                return
            agency, phenomenality = ascription_of(entity_id)
            if agency < TOM_AGENCY:
                return
            held = targets.get(entity_id)
            # Keep the loudest sighting, but a relation the agent has a stance about always beats
            # an incidental attribute percept: what a committee *is* says less than what it does.
            if held is not None and not (predicate in STANCE and held[2] not in STANCE) and salience <= held[3]:
                return
            targets[entity_id] = (agency, phenomenality, predicate, salience)

        for percept in perception.percepts:
            obj = percept.object
            consider(_first(obj) if isinstance(obj, tuple) else obj, percept.predicate, percept.salience)
            consider(percept.subject, percept.predicate, percept.salience)
        for predicate in STANCE:
            for record in self.store.claims(self.me, predicate):
                consider(str(getattr(record.claim.object, 'id', record.claim.object)), predicate, 0.25)
        edits = []
        for entity_id, (agency, phenomenality, predicate, _salience) in sorted(
                targets.items(), key=lambda kv: (-kv[1][3], kv[0]))[:self.tom_limit]:
            scope = tc.Ref('model:%s' % entity_id)
            target = tc.Ref(entity_id)
            now = self.now or _dt.datetime.now(UTC)
            # The model is derived from the published relation that brought the target into view,
            # so "why do you think that PAC wants access?" explains down to the FEC record.
            premises = tuple(sorted({r.id for r in self.store.claims(self.me, predicate, target)}))
            because = (tc.Evidence(tc.Ref('ascription:agent-template'), now, method='theory-of-mind@1',
                                   confidence=tc.Score(round(agency, 2), 'ascribed_agency'),
                                   derived_from=premises),)
            edits.append(tc.Tell(tc.Claim(self.me, 'ascribes', (entity_id, round(agency, 2), round(phenomenality, 2))),
                                 (tc.Evidence(tc.Ref('ascription:defaults'), now, method='ascription@1'),)))
            want, believes = STANCE.get(predicate, ('position', 'notes'))
            edits.append(tc.Tell(tc.Claim(target, 'projects', ('wants', want, self.grounding.entity_id), scope=scope),
                                 because))
            edits.append(tc.Tell(tc.Claim(target, 'projects', ('believes', believes, self.grounding.entity_id),
                                          scope=scope), because))
            self.tom[entity_id] = {'wants': want, 'believes': believes, 'because': predicate,
                                   'agency': round(agency, 2), 'phenomenality': round(phenomenality, 2),
                                   'as_of_tick': self.ticks}
        if edits:
            commit = self.store.apply(tc.Patch(tuple(edits), self.store.revision))
            for claim_id in commit.added:
                self._born.setdefault(claim_id, self.ticks)

    # ------------------------------------------------------------------ choosing

    def options(self):
        """The intentions on the table this tick, with the premises each one rests on."""
        store, me = self.store, self.me
        wants = {}
        for record in store.claims(me, 'wants'):
            wants.setdefault(_head(record.claim.object), []).append(record)
        appraisals = {}
        for record in store.claims(me, 'appraises'):
            appraisals.setdefault(_head(record.claim.object), []).append(record)
        percepts = [record.id for record in store.claims(scope=PERCEPT)]
        recalls = [record.id for record in store.claims(me, 'recalls')]
        margins = self.viability_margins()
        bias = MOTIF_BIAS.get(self.motif, {})

        def ids(*groups):
            out = []
            for group in groups:
                for record in (group if isinstance(group, list) else [group] if group else []):
                    out.append(record.id)
            return tuple(dict.fromkeys(out))

        options = []
        # Which stuck measure to push: one the agent has formed a want about, otherwise the
        # first by entity id. Deterministic either way, and it prefers what the agent wants.
        wanted_bills = {_first(record.claim.object) for record in wants.get('advance_measure', ())}
        stuck = sorted((record for record in store.claims(me, 'sponsored')
                        if self._stage_of(record.claim.object) in (None, 'referred', 'pending')),
                       key=lambda r: (str(getattr(r.claim.object, 'id', r.claim.object)) not in wanted_bills,
                                      str(getattr(r.claim.object, 'id', r.claim.object))))
        if stuck:
            target = str(getattr(stuck[0].claim.object, 'id', stuck[0].claim.object))
            # The premises are the ones about *this* measure, so the explanation is about the
            # measure the agent actually decided to push.
            about = [record for record in appraisals.get('pressure', []) + wants.get('advance_measure', [])
                     if _first(record.claim.object) == target]
            options.append(Intention('advance_measure', target,
                                     0.45 + 0.5 * _pressure(appraisals.get('pressure')) - 0.3 * margins['efficacy'],
                                     ids(stuck[0], about) or tuple(percepts[:2]),
                                     'move %s, a measure I put my name to' % target))
        committees = store.claims(me, 'serves_on')
        if committees:
            options.append(Intention('committee_work', committees[0].claim.object.id,
                                     0.35 + 0.35 * margins['standing'] + 0.2 * bool(appraisals.get('standing')),
                                     ids(appraisals.get('standing', []), wants.get('committee_work', [])) or tuple(percepts[:2]),
                                     'work the committee I sit on'))
        if appraisals.get('rebuff'):
            options.append(Intention('build_coalition', _first(appraisals['rebuff'][0].claim.object),
                                     0.40 + 0.45 * _pressure(appraisals['rebuff']) - 0.25 * margins['coalition'],
                                     ids(appraisals.get('rebuff', []), wants.get('build_coalition', [])) + tuple(recalls[:1]),
                                     'I keep losing these votes; go find votes'))
        if store.claims(me, 'received_support') or appraisals.get('support'):
            options.append(Intention('fundraise', '',
                                     0.30 + 0.35 * max(0.0, -margins['support']) + 0.15 * bool(appraisals.get('obligation')),
                                     ids(appraisals.get('support', []), appraisals.get('obligation', [])) or tuple(percepts[:1]),
                                     'the cycle needs money'))
        if appraisals.get('threat') or margins['office'] < 0 or margins['support'] < 0:
            options.append(Intention('campaign', '',
                                     0.35 + 0.5 * bool(appraisals.get('threat')) + 0.3 * max(0.0, -margins['support']),
                                     ids(appraisals.get('threat', []), wants.get('campaign', [])) + tuple(recalls[:1]),
                                     'the seat is not safe'))
        options.append(Intention('constituency_service', str(_object_of(store.claims(me, 'represents'))),
                                 0.30 + 0.25 * max(0.0, -margins['standing']),
                                 ids(store.claims(me, 'represents')) or tuple(percepts[:1]),
                                 'the people who sent me here'))
        if appraisals.get('pride'):
            options.append(Intention('hold_position', '',
                                     0.28 + 0.3 * margins['efficacy'],
                                     ids(appraisals.get('pride', [])), 'stay the course; it is working'))
        if appraisals.get('rebuff') and store.claims(me, 'opposed_by'):
            target = store.claims(me, 'opposed_by')[0].claim.object
            options.append(Intention('counter_opponent', getattr(target, 'id', str(target)),
                                     0.25 + 0.4 * bool(appraisals.get('threat')),
                                     ids(appraisals.get('threat', [])), 'block what they are pushing'))
        return [Intention(o.kind, o.target, round(o.u + bias.get(o.kind, 0.0), 4), o.premises, o.why)
                for o in options]

    def constraints(self):
        """Hard requirements, checked before any backend sees an option.

        ``harm_forbidden_by_ascription`` is the mechanism the affect design names: the harm
        constraint is weighted by the *phenomenality* the agent ascribes to the target, which
        is why opposing a campaign committee is permitted and campaigning against a person is
        not. It is a constraint, not a penalty, so it cannot be traded away by utility.
        """
        store, me = self.store, self.me
        in_office = bool(store.claims(me, 'holds_office') or store.claims(me, 'in_office'))

        def harm(option, _given):
            if option.kind != 'counter_opponent' or not option.target:
                return True
            _agency, phenomenality = ascription_of(option.target)
            return phenomenality * 0.8 <= 0.42

        def authority(option, _given):
            if option.kind not in ('advance_measure', 'committee_work'):
                return True
            chamber = _object_of(store.claims(me, 'chamber'))
            if chamber is None:
                return tc.Unknown('no_published_chamber', 'the record does not say which chamber')
            if option.kind == 'committee_work':
                return bool(store.claims(me, 'serves_on', tc.Ref(option.target)))
            origin = _object_of(store.claims(tc.Ref(option.target), 'origin_chamber'))
            return origin is None or str(origin).lower() == str(chamber).lower()

        def seated(option, _given):
            return in_office or option.kind in ('campaign', 'fundraise', 'constituency_service')

        def grounded(option, _given):
            return bool(option.premises) and all(pid in store._claims for pid in option.premises)

        return (tc.Constraint('harm_forbidden_by_ascription', harm),
                tc.Constraint('within_role_authority', authority),
                tc.Constraint('holds_the_office', seated),
                tc.Constraint('grounded_in_a_live_premise', grounded))

    def _choose(self):
        options = self.options()
        objective = tc.Objective('expected_viability',
                                 'movement along the viability gradient this agent reads from its own '
                                 'beliefs: office, standing, efficacy, coalition and support, biased by motif',
                                 lambda option, _given: option.u)
        with tc.use(self.runtime):
            choice = tc.choose(options, objective=objective, constraints=self.constraints())
        self.runtime.trace.spans.clear()
        if isinstance(choice, tc.Unknown):
            self.decision = 'no decision (%s: %s)' % (choice.reason, choice.detail)
            self.decision_unknown = choice
            self._decide(('unknown', choice.reason), (), choice.reason, 0.0)
            return self.decision, choice
        self.decision_unknown = None
        self.decision = '%s: %s' % (choice.kind, choice.why)
        self._decide((choice.kind, choice.target), choice.premises, choice.why, choice.u)
        self_premises = sum(1 for pid in choice.premises
                            if (record := self.store._claims.get(pid)) and record.claim.subject == self.me)
        self.affect['self_causal'] = round(self_premises / max(1, len(choice.premises)), 3)
        return self.decision, None

    def _decide(self, what, premises, why, utility):
        previous = self.store.claims(self.me, 'decided')
        premises = tuple(pid for pid in premises if pid in self.store._claims)
        edits = tuple(tc.Retract(record.id, 'a new decision replaces it') for record in previous) + (
            tc.Tell(tc.Claim(self.me, 'decided', what),
                    (tc.Evidence(tc.Ref('choose:expected_viability'), self.now or _dt.datetime.now(UTC),
                                 locator=why, method='tc.choose@utility',
                                 confidence=tc.Score(round(utility, 3), 'utility'), derived_from=premises),)),)
        commit = self.store.apply(tc.Patch(edits, self.store.revision))
        self.store.forget(commit.retracted)
        if commit.added:
            self.decision_id = commit.added[0]
            self._born[self.decision_id] = self.ticks
        return self.decision_id

    # ------------------------------------------------------------------ explaining

    def find(self, text):
        """Claims matching ``text`` as ``predicate``, ``predicate object`` or a claim id."""
        if text in self.store._claims:
            return [self.store.claim(text)]
        parts = str(text).split(None, 1)
        predicate, rest = parts[0], (parts[1] if len(parts) > 1 else None)
        out = []
        for record in self.store.claims(predicate=predicate):
            obj = getattr(record.claim.object, 'id', record.claim.object)
            if rest is None or rest.lower() in str(obj).lower():
                out.append(record)
        return sorted(out, key=lambda r: r.id)

    def explain(self, target, *, depth=6):
        """Why the agent believes ``target``, down to the published record ids."""
        records = self.find(target) if isinstance(target, str) else [target]
        lines = []
        for record in records:
            lines.extend(explain_claim(self.store, record.id, depth=depth))
        return lines

    def inspect(self, *, claims=40, memories=10):
        store = self.store
        beliefs = []
        for record in sorted(store._claims.values(), key=lambda r: (-self._born.get(r.id, 0), r.id))[:claims]:
            evidence = record.evidence[-1] if record.evidence else None
            obj = getattr(record.claim.object, 'id', record.claim.object)
            beliefs.append({'claim': record.id, 'subject': str(record.claim.subject),
                            'predicate': record.claim.predicate, 'object': _plain(obj),
                            'scope': str(record.claim.scope) if record.claim.scope else None,
                            'source': str(evidence.source) if evidence else None,
                            'record_id': evidence.locator if evidence else None,
                            'method': evidence.method if evidence else None,
                            'derived': bool(evidence and evidence.derived_from),
                            'tick': self._born.get(record.id)})
        return {
            'entity_id': self.grounding.entity_id,
            'label': self.grounding.label,
            'canonical_id': self.grounding.canonical_id,
            'cluster': list(self.grounding.cluster),
            'role': self.grounding.horizon.role,
            'axes': {'coupling_kappa': self.coupling, 'gain_gamma': self.gain, 'seed': self.seed},
            'ticks': self.ticks,
            'seed_report': self.report.to_json() if getattr(self, 'report', None) else None,
            'claims_live': len(store.claims()),
            'affect': dict(self.affect),
            'motif': self.motif,
            'viability': {'margins': {k: round(v, 3) for k, v in self.viability_margins().items()},
                          'soft_distance': round(soft_viability(self.viability_margins()), 4)},
            'appraisals': _tally(store.claims(self.me, 'appraises')),
            'wants': _tally(store.claims(self.me, 'wants')),
            'intentions': [{'kind': o.kind, 'target': o.target, 'utility': o.u, 'why': o.why,
                            'premises': len(o.premises)} for o in self.options()],
            'decision': self.decision,
            'decision_claim': self.decision_id,
            'unknown': None if self.decision_unknown is None else
                       {'reason': self.decision_unknown.reason, 'detail': self.decision_unknown.detail},
            'why': self.explain(self.store.claim(self.decision_id), depth=4) if self.decision_id
                   and self.decision_id in self.store._claims else [],
            'episodes': [{'at': e.at.date().isoformat() if e.at else None, 'tag': e.tag, 'text': e.text,
                          'salience': round(e.decay(self.now, self.half_life_days), 3), 'who': list(e.who)}
                         for e in sorted(self.episodes, key=lambda e: -e.decay(self.now, self.half_life_days))[:memories]],
            'relations': [{'id': k, **{kk: (round(vv, 2) if isinstance(vv, float) else vv) for kk, vv in v.items()}}
                          for k, v in sorted(self.relations.items(),
                                             key=lambda kv: -(kv[1]['trust'] + kv[1]['grudge'] + kv[1]['gratitude']))[:8]],
            'theory_of_mind': [{'target': k, **v} for k, v in sorted(self.tom.items())],
            'unknown_facets': list(self.report.unknown) if getattr(self, 'report', None) else [],
            'beliefs': beliefs,
        }


#: How the motif tilts the utility of an intention. Authored; the civ sim's ``motif_bias``.
MOTIF_BIAS = {
    'fear': {'campaign': 0.25, 'fundraise': 0.20, 'hold_position': -0.10},
    'anger': {'counter_opponent': 0.30, 'build_coalition': 0.15},
    'grief': {'constituency_service': 0.25, 'hold_position': 0.10},
    'shame': {'constituency_service': 0.30, 'committee_work': 0.15},
    'joy': {'advance_measure': 0.20, 'hold_position': 0.15},
    'flow': {'advance_measure': 0.25, 'committee_work': 0.20},
    'boredom': {'build_coalition': 0.15, 'fundraise': 0.10},
    'attachment': {'committee_work': 0.20, 'constituency_service': 0.15},
    'desire': {'advance_measure': 0.20, 'fundraise': 0.20},
    'suffering': {'hold_position': 0.20},
    'awe': {'constituency_service': 0.10},
    'calm': {},
}


def legislator(entity_id, *, index=None, index_path=None, cache_mb=64, **kwargs):
    """Convenience: open the default index if needed and ground a legislator agent on it."""
    owned = index is None
    index = index if index is not None else EvidenceIndex(index_path or default_index_path(), cache_mb=cache_mb)
    try:
        return Person.ground(index, entity_id, horizon=LEGISLATOR, **kwargs)
    except Exception:
        if owned:
            index.close()
        raise


# ------------------------------------------------------------------------ small helpers


def _clip(value, low=-1.0, high=1.0):
    return max(low, min(high, float(value)))


def _tally(records, examples=3):
    """Appraisals and wants by tag, with a count and a few examples.

    A senator with thirty referred bills holds thirty ``pressure`` appraisals; listing them all
    buries the shape of what the agent is concerned about, which is what an inspector wants.
    """
    grouped = {}
    for record in records:
        grouped.setdefault(_head(record.claim.object), set()).add(_first(record.claim.object))
    return [{'tag': tag, 'count': len(about), 'about': sorted(about)[:examples]}
            for tag, about in sorted(grouped.items())]


def _pressure(records, full=4.0):
    """How much a pile of appraisals of one kind pushes, saturating at ``full`` of them.

    An unbounded count would let one loud channel dominate every choice for ever, which is how
    an agent stops being able to change its mind.
    """
    return min(1.0, len(records or ()) / full)


def _head(obj):
    if isinstance(obj, (tuple, list)) and obj:
        return str(obj[0])
    return str(obj)


def _first(obj):
    """The thing an ``(about, tag)`` or ``(tag, about)`` pair is about."""
    if isinstance(obj, (tuple, list)) and len(obj) >= 2:
        return str(obj[0]) if str(obj[0]).count(':') >= 1 else str(obj[1])
    return str(obj)


def _pair(obj):
    if isinstance(obj, (tuple, list)) and len(obj) >= 2:
        return str(obj[1])
    return str(obj)


def _object_of(records):
    for record in records:
        return getattr(record.claim.object, 'id', record.claim.object)
    return None


def _plain(value):
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
