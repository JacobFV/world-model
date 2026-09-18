"""Affect read as structural measures over an agent's own processing.

Nothing here is a mood variable that rules set. Every reading is computed from what the
agent's claim store currently holds and from what this tick's ``Thought`` changed, exactly
the way ``docs/civ-sim/affect-and-selfhood.md`` section 2.2 describes it:

===================  ==============================================================
``valence``          movement along a viability gradient: ``V(v_t) - V(v_{t-1})``,
                     where ``V`` is a soft distance to the nearest viability boundary
``arousal``          size of the belief update, normalized by the working-memory cap
``integration``      share of derived claims whose premises cross two or more modules,
                     scaled by the coupling dial kappa (a provenance statistic, *not* IIT Phi)
``effective_rank``   ``exp(H(p))`` over the weights of the live appraisals: how many
                     concerns are active at once
``counterfactual``   share of derived claims that are about non-actual futures
                     (``wants``, ``means``, ``projects``)
``self_attention``   share of live percept claims whose subject is the agent
``self_causal``      share of the chosen intention's premises that are self-model claims
===================  ==============================================================

A **motif** is the nearest prototype in that seven-dimensional space, with an *asymmetric*
transition cost added to the distance, so fear slides into anger more cheaply than back and
grief does not run back into the attachment that preceded it.

None of this claims anything is experienced. The readings are structural measures over
computation. This module is pure Python and needs neither numpy nor tensorcode.
"""
import math

READINGS = ('valence', 'arousal', 'integration', 'effective_rank', 'counterfactual',
            'self_attention', 'self_causal')

#: Which cognitive module a predicate belongs to. ``integration`` counts a derived claim as
#: crossing modules when its premises come from two or more of these. Predicates absent here
#: are ``other``, which never makes a crossing on its own.
MODULE = {
    # what the published record said about the situation, arriving as perception
    'serves_on': 'perception', 'chamber': 'perception', 'party': 'perception',
    'represents': 'perception', 'in_office': 'perception', 'term_ends': 'perception',
    'measure_stage': 'perception', 'measure_status': 'perception', 'origin_chamber': 'perception',
    'policy_area': 'perception', 'referred': 'perception', 'question': 'perception',
    'result': 'perception', 'measure': 'perception', 'committee_type': 'perception',
    'ideal_point_economic': 'perception', 'ideal_point_social': 'perception',
    'votes_scaled': 'perception', 'seniority': 'perception', 'caucus_size': 'perception',
    # events that arrived in the window
    'sponsored': 'event', 'cosponsored': 'event', 'voted': 'event', 'vote_outcome': 'event',
    'received_support': 'event', 'assigned_to': 'event', 'left': 'event',
    # standing social structure
    'colleague_of': 'social', 'campaign_committee': 'social', 'co_sponsor_with': 'social',
    'trusts': 'social', 'aligned_with': 'social', 'opposed_by': 'social',
    'obliged_to': 'social', 'models': 'social',
    # the self model
    'name': 'self', 'role': 'self', 'bound_to': 'self', 'grounded_in': 'self',
    'holds_office': 'self', 'ascribes': 'self', 'same_as': 'self', 'birthday': 'self',
    'gender': 'self',
    # what the agent derived
    'appraises': 'appraisal', 'motif': 'appraisal', 'viability': 'appraisal',
    'wants': 'plan', 'decided': 'plan', 'means': 'narrative', 'projects': 'narrative',
    'recalls': 'memory', 'remembers': 'memory',
}

#: Prototype readings, in ``READINGS`` order. Seven of these (flow, shame, joy, suffering,
#: fear, desire, calm-as-baseline) follow the profiles the source material gives; the rest are
#: authored here and in the civ-sim port, and are labelled as ours in the design doc.
PROTOTYPES = {
    'calm':       (0.05, 0.15, 0.30, 0.30, 0.10, 0.30, 0.40),
    'joy':        (0.50, 0.45, 0.50, 0.70, 0.20, 0.30, 0.50),
    'flow':       (0.25, 0.35, 0.60, 0.30, 0.10, 0.10, 0.80),
    'desire':     (0.10, 0.50, 0.40, 0.30, 0.70, 0.40, 0.60),
    'fear':      (-0.50, 0.75, 0.40, 0.30, 0.70, 0.50, 0.40),
    'anger':     (-0.45, 0.85, 0.30, 0.20, 0.50, 0.50, 0.80),
    'grief':     (-0.55, 0.35, 0.40, 0.20, 0.80, 0.60, 0.30),
    'shame':     (-0.40, 0.50, 0.40, 0.30, 0.30, 0.90, 0.80),
    'boredom':   (-0.10, 0.05, 0.10, 0.15, 0.05, 0.60, 0.20),
    'suffering': (-0.70, 0.40, 0.20, 0.10, 0.30, 0.60, 0.30),
    'attachment': (0.45, 0.30, 0.60, 0.50, 0.30, 0.20, 0.50),
    'awe':        (0.40, 0.80, 0.70, 0.80, 0.30, 0.05, 0.30),
}
MOTIFS = tuple(sorted(PROTOTYPES))

#: Asymmetric transition costs. A low number means the move is cheap. Pairs that are absent
#: cost ``DEFAULT_TRANSITION`` (and staying put costs nothing).
EASY = {
    ('fear', 'anger'): 0.10, ('anger', 'fear'): 0.60,
    ('attachment', 'grief'): 0.05, ('grief', 'attachment'): 0.80,
    ('joy', 'calm'): 0.05, ('calm', 'boredom'): 0.10,
    ('shame', 'anger'): 0.15, ('anger', 'shame'): 0.55,
    ('boredom', 'desire'): 0.10, ('desire', 'boredom'): 0.45,
    ('flow', 'joy'): 0.08, ('grief', 'suffering'): 0.10,
}
DEFAULT_TRANSITION = 0.25
TRANSITION_WEIGHT = 0.6


def transition_cost(current, candidate):
    """How dear it is to move from ``current`` to ``candidate``. Not symmetric."""
    if current == candidate:
        return 0.0
    return EASY.get((current, candidate), DEFAULT_TRANSITION)


def nearest_motif(reading, current='calm'):
    """The nearest prototype to ``reading``, with the asymmetric transition cost added.

    Returns ``(motif, cost)``. Ties break on the motif name so the result is deterministic.
    """
    vector = [float(reading.get(key, 0.0)) for key in READINGS]
    best, best_cost = current, None
    for name in MOTIFS:
        proto = PROTOTYPES[name]
        distance = math.sqrt(sum((a - b) ** 2 for a, b in zip(vector, proto)))
        cost = distance + TRANSITION_WEIGHT * transition_cost(current, name)
        if best_cost is None or cost < best_cost:
            best, best_cost = name, cost
    return best, round(best_cost, 4)


def soft_viability(margins, sharpness=6.0):
    """Soft distance to the nearest viability boundary: ``-logsumexp(-k*margin)/k``.

    ``margins`` are signed distances in roughly ``[-1, 1]``; negative means past a boundary.
    The soft minimum is dominated by the tightest margin but stays differentiable, so the
    *movement* between two ticks is a gradient rather than a step.
    """
    values = [float(v) for v in (margins.values() if hasattr(margins, 'values') else margins)]
    if not values:
        return 0.0
    shifted = [-sharpness * v for v in values]
    peak = max(shifted)
    total = sum(math.exp(s - peak) for s in shifted)
    return -(peak + math.log(total)) / sharpness


def entropy_rank(weights, concerns):
    """``exp(H(p))`` over normalized weights, divided by ``concerns`` so the reading is in [0, 1]."""
    total = sum(max(0.0, float(w)) for w in weights)
    if total <= 0 or concerns <= 0:
        return 0.0
    entropy = 0.0
    for weight in weights:
        share = max(0.0, float(weight)) / total
        if share > 0:
            entropy -= share * math.log(share)
    return min(1.0, math.exp(entropy) / concerns)


def module_of(predicate):
    return MODULE.get(predicate, 'other')


class AffectReading(dict):
    """The seven readings plus the motif they classify to, kept as a plain mapping."""

    def __init__(self, values, motif, distance):
        super().__init__({key: values.get(key, 0.0) for key in READINGS})
        self.motif = motif
        self.distance = distance

    def __repr__(self):
        body = ', '.join('%s=%.3f' % (key, self[key]) for key in READINGS)
        return 'AffectReading(%s -> %s @ %.3f)' % (body, self.motif, self.distance)


DERIVED_PREDICATES = ('appraises', 'wants', 'means', 'projects', 'motif')
FUTURE_PREDICATES = ('wants', 'means', 'projects')


def read_affect(store, me, thought_ids, *, previous, motif, viability, last_viability,
                coupling=0.6, gain=0.5, percept_scope=None, working_memory_cap=20,
                concerns=7, self_causal=None, update_scale=3.0):
    """Read the seven measures off ``store`` and this tick's change, then name the motif.

    ``thought_ids`` is ``(added_ids, retracted_ids)`` from the tick's ``Thought``s.
    ``previous`` is the last reading (for the exponential smoothing that gives affect inertia);
    ``viability``/``last_viability`` are ``soft_viability`` values. ``coupling`` is kappa and
    ``gain`` is gamma. Nothing here writes to the store; the caller records the motif claim so
    that it carries its own provenance.
    """
    added, retracted = thought_ids
    live = store.claims(me, 'appraises')

    def claim_of(claim_id):
        return store._claims.get(claim_id)

    # The appraisal term saturates: eight rebuffs in one tick are worse than one, but not eight
    # times worse, and a raw sum would peg valence at the floor and stay there.
    weights = [SIGNS.get(_tag(record.claim.object), 0.0)
               for claim_id in added
               if (record := claim_of(claim_id)) is not None and record.claim.predicate == 'appraises']
    count = len(weights)
    delta = (sum(weights) / count) * min(1.0, count / 4.0) if count else 0.0
    valence = 3.0 * (viability - last_viability) + delta + 0.3 * viability
    # Belief update per tick, normalized by ``update_scale`` times the working-memory cap. A
    # tick that replaces working memory outright *and* brings as much again as new knowledge
    # sits near the top of the range; a quiet tick sits near the bottom.
    churn = (len(added) + 0.5 * len(retracted)) / max(1.0, update_scale * working_memory_cap)
    arousal = min(1.0, churn * (0.5 + gain))

    derived = [r for r in store._claims.values()
               if not r.retracted and r.claim.predicate in DERIVED_PREDICATES
               and r.evidence and r.evidence[0].derived_from]
    crossing = [r for r in derived
                if len({module_of(claim_of(p).claim.predicate) for p in r.evidence[0].derived_from
                        if claim_of(p) is not None}) >= 2]
    integration = coupling * (len(crossing) / len(derived)) if derived else 0.0

    weights = {}
    for record in live:
        confidence = record.evidence[0].confidence.value if record.evidence and record.evidence[0].confidence else 0.5
        tag = _tag(record.claim.object)
        weights[tag] = weights.get(tag, 0.0) + confidence
    effective_rank = entropy_rank(list(weights.values()), concerns)

    future = [r for r in derived if r.claim.predicate in FUTURE_PREDICATES]
    counterfactual = len(future) / len(derived) if derived else 0.0

    percepts = store.claims(scope=percept_scope) if percept_scope is not None else []
    self_attention = (sum(1 for r in percepts if r.claim.subject == me) / len(percepts)) if percepts else 0.0

    reading = {
        'valence': max(-1.0, min(1.0, valence)),
        'arousal': arousal,
        'integration': integration,
        'effective_rank': effective_rank,
        'counterfactual': counterfactual,
        'self_attention': self_attention,
        'self_causal': previous.get('self_causal', 0.4) if self_causal is None else self_causal,
    }
    weight = 0.25 + 0.6 * gain
    smoothed = {key: round((1 - weight) * float(previous.get(key, 0.0)) + weight * value, 3)
                for key, value in reading.items()}
    name, distance = nearest_motif(smoothed, motif)
    return AffectReading(smoothed, name, distance)


#: How an appraisal tag moves valence. Authored, not fitted; changing it changes the motif an
#: agent lands on, so it is stated here rather than buried in a rule.
SIGNS = {
    'threat': -0.40, 'loss': -0.50, 'need': -0.25, 'injustice': -0.20, 'shame': -0.30,
    'exposure': -0.30, 'pressure': -0.25, 'rebuff': -0.25, 'isolation': -0.20,
    'obligation': -0.10, 'mistrust': -0.15,
    'support': 0.30, 'attachment': 0.30, 'pride': 0.40, 'standing': 0.30,
    'opportunity': 0.25, 'wonder': 0.35, 'efficacy': 0.35,
}


def _tag(obj):
    """Appraisal objects are ``(tag, about)`` pairs; be tolerant of a bare tag."""
    if isinstance(obj, (tuple, list)) and obj:
        return str(obj[0])
    return str(obj)
