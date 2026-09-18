"""Instrumentation for a society of grounded agents: what emerged, measured rather than asserted.

A loop of agents that talk to each other is easy to write and easy to over-claim. This module is
the check on that. It reads only what :mod:`worldmodel.agents.society` recorded - the tick records,
the channel's utterances and deliveries, the promotion and demotion reports - and turns them into
four families of measurement:

**Belief divergence.** How far each agent's beliefs sit from what the index published, per agent,
per predicate, over time. Divergence is *expected*: perception is bounded, hearsay arrives with a
fidelity below one and a tick late, and consolidation forms standing beliefs no single edge
supports. So it is reported as a **distribution** - quantiles, a histogram and the per-agent series
- and never as an error rate. A society whose divergence were zero would be one whose agents had no
inner life at all.

**Information propagation.** How far a claim travels: reach (how many agents came to hold it),
latency (how many ticks after it was first said), and degradation with chain length (the fidelity
each hop survives with, and how many deliveries happened at each hop count).

**Affect and regime trajectories.** The motif a person is in each tick and the regime a firm reads
each tick, their dwell times, and - the interesting one - whether the *asymmetric transition costs*
declared in :mod:`worldmodel.agents.affect` show up as hysteresis in the observed transitions. The
cheap direction should be traversed more often than the dear one; ``hysteresis_index`` is the share
of observed reciprocal pairs where it was.

**Decision structure.** How often a decision was ``Unknown`` (an honest non-decision, with the
reason), how often a firm's principal-agent divergence actually changed the outcome rather than
merely being recorded, and how often an institutional act was refused as ultra vires.

Everything is reproducible from the society's seed and config, and the whole report goes out
through :func:`worldmodel.artifacts.publish_report` like any other derived report.

**What none of this measures.** Agreement with the world. Every number here describes the
*simulation's* internal behaviour. Nothing in this layer is fitted, scored against a baseline, or
``validated``; :data:`NOT_ACCURACY` is attached to every statistic that could be mistaken for
predictive accuracy, and :meth:`Observatory.report` carries the disclaimer at the top level, per
section, and per labelled statistic. Three copies is deliberate: a reader who takes one number out
of the report takes the label with it.
"""
import math
from dataclasses import dataclass

from .affect import transition_cost

#: Mirrors :data:`worldmodel.agents.society.TIERS`. It is restated rather than imported so that
#: this module - whose statistics are plain Python - imports with or without the optional
#: substrate, exactly like ``agents.affect``. :func:`Observatory.tiering` checks the two agree.
TIERS = ('focal', 'compact', 'cohort')

#: Attached to every statistic a reader might mistake for predictive accuracy.
NOT_ACCURACY = ('internal_behaviour_of_the_simulation: this is not agreement with the world, not a '
                'forecast error and not a validated statistic')

#: Attached to the divergence family specifically, which is the easiest one to misread.
DIVERGENCE_LABEL = ('expected_by_construction: bounded perception, relayed hearsay, a one-tick '
                    'delivery delay and episodic consolidation all put belief and record apart. '
                    'This is a distance between two sets, not an error rate, and a zero would mean '
                    'the agents had no inner life')

#: The epistemic status every report carries.
EPISTEMIC_STATUS = 'simulation_internal_behaviour'

QUANTILES = (0.0, 0.25, 0.5, 0.75, 0.9, 1.0)


def quantiles(values, points=QUANTILES):
    """Sample quantiles by linear interpolation. Empty input gives an empty mapping."""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return {}
    out = {}
    for point in points:
        position = point * (len(ordered) - 1)
        low, high = int(math.floor(position)), int(math.ceil(position))
        weight = position - low
        out['p%d' % round(point * 100)] = round(ordered[low] * (1 - weight) + ordered[high] * weight, 6)
    return out


def distribution(values, *, bins=10, label=None):
    """A distribution, not a point estimate: count, mean, spread, quantiles and a histogram."""
    ordered = sorted(float(v) for v in values)
    if not ordered:
        return {'count': 0, 'label': label}
    mean = sum(ordered) / len(ordered)
    variance = sum((v - mean) ** 2 for v in ordered) / len(ordered)
    histogram = [0] * bins
    low, high = ordered[0], ordered[-1]
    span = (high - low) or 1.0
    for value in ordered:
        index = min(bins - 1, int((value - low) / span * bins))
        histogram[index] += 1
    out = {'count': len(ordered), 'mean': round(mean, 6), 'sd': round(math.sqrt(variance), 6),
           'min': round(low, 6), 'max': round(high, 6), 'quantiles': quantiles(ordered),
           'histogram': {'low': round(low, 6), 'high': round(high, 6), 'bins': histogram}}
    if label:
        out['label'] = label
    return out


#: The reasons ``worldmodel.agents.roles.authorize`` can refuse on, as short categories. The
#: mapping is over the phrases ``authorize`` itself writes, so a new refusal reason shows up as
#: ``other`` rather than being silently folded into an existing bucket.
REFUSAL_CATEGORIES = (
    ('outside the declared jurisdiction', 'outside_declared_jurisdiction'),
    ('holds no power', 'role_holds_no_power'),
    ('jurisdiction of role', 'jurisdiction_not_declared'),
    ('authority of role', 'authority_not_declared'),
    ('no spending limit declared', 'spending_limit_not_declared'),
    ('exceeds the declared limit', 'over_spending_limit'),
    ('quorum of', 'quorum_not_met'),
    ('continued tenure is Unknown', 'seat_unobserved'),
    ('no published record of a holder', 'no_published_holder'),
    ('not a registered action', 'not_an_action'),
)


def refusal_category(reason):
    for needle, name in REFUSAL_CATEGORIES:
        if needle in reason:
            return name
    return 'other'


def runs(sequence):
    """``((value, length), ...)`` for consecutive equal values. Dwell time, in ticks."""
    out = []
    for value in sequence:
        if out and out[-1][0] == value:
            out[-1][1] += 1
        else:
            out.append([value, 1])
    return tuple((value, length) for value, length in out)


# --------------------------------------------------------------------------- hysteresis


@dataclass(frozen=True)
class MotifPair:
    """One reciprocal motif pair, with the declared cost next to the observed traffic.

    Printing them together is the point: the reader can see whether the asymmetry in the counts
    follows the asymmetry in the costs, or whether the claim is being made on their behalf.
    """

    a: str
    b: str
    forward: int = 0          # a -> b
    backward: int = 0         # b -> a
    cost_forward: float = 0.0
    cost_backward: float = 0.0

    @property
    def total(self):
        return self.forward + self.backward

    @property
    def asymmetry(self):
        """``(a->b minus b->a) / total``. Positive: the a->b direction dominates."""
        return 0.0 if not self.total else round((self.forward - self.backward) / self.total, 6)

    @property
    def cheaper(self):
        """Which direction the declared transition costs make cheap: ``1`` a->b, ``-1`` b->a."""
        if self.cost_forward == self.cost_backward:
            return 0
        return 1 if self.cost_forward < self.cost_backward else -1

    @property
    def agrees(self):
        """Was the cheap direction the one actually traversed more often?"""
        if self.cheaper == 0 or self.asymmetry == 0:
            return None
        return (self.asymmetry > 0) == (self.cheaper > 0)

    def to_json(self):
        return {'from': self.a, 'to': self.b, 'forward': self.forward, 'backward': self.backward,
                'cost_forward': round(self.cost_forward, 4), 'cost_backward': round(self.cost_backward, 4),
                'asymmetry': self.asymmetry, 'cheaper_direction': self.cheaper, 'agrees': self.agrees}


# --------------------------------------------------------------------------- the observatory


class Observatory:
    """Measurement over a :class:`~worldmodel.agents.society.Society` that has been run.

    Construct it after :meth:`~worldmodel.agents.society.Society.run`; it holds no state of its own
    beyond the society, so two observatories over the same society give the same report.
    """

    def __init__(self, society):
        self.society = society

    # ------------------------------------------------------------------ divergence

    def belief_divergence(self):
        """Per agent, per predicate and in aggregate: how far belief sits from the record.

        The distance is Jaccard between the set of objects the agent holds on a predicate and the
        set the index currently publishes, so it is bounded in ``[0, 1]`` and symmetric in the two
        directions of disagreement - a belief with no live edge and a live edge nobody believes
        both count. Both directions are also reported separately, because they mean different
        things: ``unsupported`` is what an agent came to hold anyway (hearsay, consolidation), and
        ``missing`` is what its perception bound kept out.
        """
        samples = [sample for record in self.society.history for sample in record.divergence]
        by_agent, by_predicate, series = {}, {}, {}
        for sample in samples:
            by_agent.setdefault(sample.agent, []).append(sample.distance)
            by_predicate.setdefault(sample.predicate, []).append(sample.distance)
            series.setdefault(sample.agent, {}).setdefault(sample.predicate, []).append(
                {'tick': sample.tick, 'distance': sample.distance, 'held': sample.held,
                 'published': sample.published, 'unsupported': sample.unsupported,
                 'missing': sample.missing})
        return {
            'label': DIVERGENCE_LABEL,
            'not_accuracy': NOT_ACCURACY,
            'measure': 'jaccard_distance(beliefs_on_predicate, published_edges_on_predicate)',
            'samples': len(samples),
            'aggregate': distribution([s.distance for s in samples], label=DIVERGENCE_LABEL),
            'unsupported_claims': distribution([s.unsupported for s in samples],
                                               label='believed and not currently published: hearsay '
                                                     'and consolidated standing beliefs'),
            'missing_edges': distribution([s.missing for s in samples],
                                          label='published and not believed: the perception bound'),
            'per_agent': {agent: distribution(values) for agent, values in sorted(by_agent.items())},
            'per_predicate': {predicate: distribution(values)
                              for predicate, values in sorted(by_predicate.items())},
            'provenance': self.belief_provenance(),
            'series': {agent: {predicate: rows for predicate, rows in sorted(predicates.items())}
                       for agent, predicates in sorted(series.items())},
        }

    def belief_provenance(self):
        """The other half of divergence: how much of a store the record ever supported at all.

        The per-predicate distance compares two *sets of objects*. This counts whole claims by
        where they came from - a published record, another agent, the agent's own derivation, or
        its own bookkeeping - which is what says whether a society is drifting away from the
        catalog as it runs.
        """
        history = self.society.history
        final = history[-1].provenance if history else {}
        shares, hearsay = [], []
        series = {}
        for record in history:
            for agent, counts in record.provenance.items():
                series.setdefault(agent, []).append(
                    {'tick': record.tick, 'total': counts['total'],
                     'published': counts['published'], 'hearsay': counts['hearsay'],
                     'derived': counts['derived'], 'internal': counts['internal'],
                     'from_the_record_share': counts['from_the_record_share']})
        for counts in final.values():
            if counts['total']:
                shares.append(counts['from_the_record_share'])
                hearsay.append(counts['hearsay'] / counts['total'])
        return {
            'label': 'a claim is counted once, by the first of: derived from premises, published '
                     '(a dataset: source), told - which is an agent: source for gossip or a '
                     'disclosure: source for a filing or an instrument, either way through the '
                     'channel - otherwise internal bookkeeping',
            'final': {agent: counts for agent, counts in sorted(final.items())},
            'from_the_record_share': distribution(shares,
                                                  label='share of a focal store whose first evidence '
                                                        'is a published record'),
            'hearsay_share': distribution(hearsay,
                                          label='share of a focal store that arrived through the '
                                                'channel rather than the index'),
            'series': {agent: rows for agent, rows in sorted(series.items())},
        }

    # ------------------------------------------------------------------ propagation

    def propagation(self):
        """How a claim spreads: reach, latency, and degradation with chain length.

        A claim's ``reach`` is the number of distinct agents it was delivered to at least once;
        ``latency`` is the tick of an agent's first delivery minus the tick the claim was first
        said. Degradation is not modelled here - it is *read off* the deliveries, hop count by hop
        count, so the declared ``RELAY_FIDELITY`` shows up as a curve a reader can check.
        """
        society = self.society
        deliveries = society.channel.deliveries
        population = max(1, len(society.members) - 1)
        origins = {}
        for utterance in society.channel.utterances:
            current = origins.get(utterance.claim_key)
            if current is None or utterance.origin_tick < current[0]:
                origins[utterance.claim_key] = (utterance.origin_tick, utterance.origin_actor,
                                                utterance.kind)
        claims, by_hop = {}, {}
        for delivery in deliveries:
            row = by_hop.setdefault(delivery.hops, {'deliveries': 0, 'fidelity': 0.0, 'accepted': 0})
            row['deliveries'] += 1
            row['fidelity'] += delivery.fidelity
            row['accepted'] += int(delivery.accepted)
            claim = claims.setdefault(delivery.claim_key, {'first_seen': {}, 'hops': 0, 'deliveries': 0,
                                                           'min_fidelity': 1.0})
            claim['deliveries'] += 1
            claim['hops'] = max(claim['hops'], delivery.hops)
            claim['min_fidelity'] = min(claim['min_fidelity'], delivery.fidelity)
            seen = claim['first_seen']
            if delivery.listener not in seen or delivery.tick < seen[delivery.listener]:
                seen[delivery.listener] = delivery.tick
        rows, reaches, latencies = [], [], []
        for claim_key, claim in sorted(claims.items()):
            origin_tick, origin_actor, kind = origins.get(claim_key, (0, '', ''))
            lags = [tick - origin_tick for tick in claim['first_seen'].values()]
            reach = len(claim['first_seen'])
            reaches.append(reach / population)
            latencies.extend(lags)
            rows.append({'claim_key': claim_key, 'kind': kind, 'origin_tick': origin_tick,
                         'origin_actor': origin_actor, 'reach': reach,
                         'reach_fraction': round(reach / population, 6),
                         'deliveries': claim['deliveries'], 'max_hops': claim['hops'],
                         'min_fidelity': round(claim['min_fidelity'], 6),
                         'latency_ticks': {'min': min(lags) if lags else None,
                                           'mean': round(sum(lags) / len(lags), 4) if lags else None,
                                           'max': max(lags) if lags else None}})
        refused = {}
        for delivery in deliveries:
            if delivery.accepted:
                continue
            reason = delivery.refused or ('cohort' if delivery.tier == 'cohort' else 'nothing_to_hold')
            refused[reason] = refused.get(reason, 0) + 1
        formal = sum(1 for u in society.channel.utterances if u.formal)
        return {
            'not_accuracy': NOT_ACCURACY,
            'population': len(society.members),
            'utterances': len(society.channel),
            'formal_utterances': formal,
            'informal_utterances': len(society.channel) - formal,
            'speech_acts_note': ('formal = an organization disclosed or issued, received through '
                                 'disclosure.receive and gated by its audience lattice; informal = a '
                                 'person said something, written into the listener hearsay scope'),
            'deliveries': len(deliveries),
            'accepted': sum(1 for d in deliveries if d.accepted),
            'not_held': dict(sorted(refused.items())),
            'not_held_note': ('a delivery that held nothing: a cohort absorbing reach as a count, or '
                              'a listener the formal audience lattice excludes - overhearing is not '
                              'modelled, so such a listener holds nothing rather than holding it '
                              'weakly'),
            'distinct_claims': len(claims),
            'reach_fraction': distribution(reaches, label='share of the rest of the population that '
                                                          'was delivered the claim at least once'),
            'latency_ticks': distribution(latencies, label='ticks between a claim first being said '
                                                           'and an agent first hearing it; the floor '
                                                           'is 1 because acts are visible next tick'),
            'degradation': [{'hops': hops, 'deliveries': row['deliveries'],
                             'accepted': row['accepted'],
                             'mean_fidelity': round(row['fidelity'] / row['deliveries'], 6)}
                            for hops, row in sorted(by_hop.items())],
            'declared_relay_fidelity': society.relay_fidelity,
            'degradation_note': ('mean fidelity at h hops is relay_fidelity**h by construction; it is '
                                 'reported so the declared decay is visible rather than implied'),
            'claims': rows,
        }

    # ------------------------------------------------------------------ trajectories

    def trajectories(self):
        """Motif and regime sequences, dwell times, and the hysteresis the costs predict."""
        society = self.society
        motifs, regimes, legitimacy, viability = {}, {}, {}, {}
        for record in society.history:
            for agent, motif in record.motifs.items():
                motifs.setdefault(agent, []).append(motif)
            for agent, regime in record.regimes.items():
                regimes.setdefault(agent, []).append(regime)
            for agent, value in record.legitimacy.items():
                legitimacy.setdefault(agent, []).append(value)
            for agent, value in record.viability.items():
                viability.setdefault(agent, []).append(value)
        pairs, transitions = self._hysteresis(motifs)
        dwell = {}
        for agent, sequence in motifs.items():
            for value, length in runs(sequence):
                dwell.setdefault(value, []).append(length)
        return {
            'not_accuracy': NOT_ACCURACY,
            'motifs': {agent: sequence for agent, sequence in sorted(motifs.items())},
            'motif_runs': {agent: [list(pair) for pair in runs(sequence)]
                           for agent, sequence in sorted(motifs.items())},
            'motif_dwell_ticks': {motif: distribution(lengths) for motif, lengths in sorted(dwell.items())},
            'regimes': {agent: sequence for agent, sequence in sorted(regimes.items())},
            'regime_runs': {agent: [list(pair) for pair in runs(sequence)]
                            for agent, sequence in sorted(regimes.items())},
            'legitimacy': {agent: values for agent, values in sorted(legitimacy.items())},
            'viability': {agent: values for agent, values in sorted(viability.items())},
            'transitions': transitions,
            'hysteresis': {
                'label': 'the transition-cost asymmetry declared in worldmodel.agents.affect, read '
                         'back out of the observed motif transitions',
                'pairs': [pair.to_json() for pair in pairs],
                'reciprocal_pairs_observed': sum(1 for p in pairs if p.agrees is not None),
                'agreeing': sum(1 for p in pairs if p.agrees),
                'index': self._hysteresis_index(pairs),
                'note': ('a short run gives few transitions and therefore a coarse index; the pair '
                         'table is the evidence and the index is a summary of it'),
            },
            'stickiness': {
                'motif_mean_dwell': round(
                    sum(length for sequence in motifs.values() for _v, length in runs(sequence))
                    / max(1, sum(len(runs(sequence)) for sequence in motifs.values())), 4),
                'regime_mean_dwell': round(
                    sum(length for sequence in regimes.values() for _v, length in runs(sequence))
                    / max(1, sum(len(runs(sequence)) for sequence in regimes.values())), 4),
                'note': 'mean run length in ticks; a firm has no motif and a person has no regime, '
                        'so the two numbers are over different populations',
            },
            'regime_hysteresis': {
                'measured': False,
                'why': ('A regime is not a motif. docs/corporate-cognition.md 1 says a firm has no '
                        'prototypes and no transition-cost matrix: regime() applies declared '
                        'thresholds and returns a test result, and a firm stickiness comes from '
                        'covenant ratchets and disclosure rules rather than from the shape of an '
                        'affect space. There is therefore no declared cost to read an asymmetry '
                        'against, and asserting one would be inventing the mechanism this layer is '
                        'supposed to keep distinct from a person. What is reported instead is the '
                        'regime run lengths and the regime sequence per firm.'),
            },
        }

    @staticmethod
    def _hysteresis(motifs):
        counted, observed = {}, []
        for agent, sequence in sorted(motifs.items()):
            for before, after in zip(sequence, sequence[1:]):
                if before == after:
                    continue
                counted[(before, after)] = counted.get((before, after), 0) + 1
                observed.append({'agent': agent, 'from': before, 'to': after,
                                 'declared_cost': round(transition_cost(before, after), 4),
                                 'declared_cost_back': round(transition_cost(after, before), 4)})
        pairs, seen = [], set()
        for (before, after), forward in sorted(counted.items()):
            key = tuple(sorted((before, after)))
            if key in seen:
                continue
            seen.add(key)
            a, b = key
            pairs.append(MotifPair(a, b, counted.get((a, b), 0), counted.get((b, a), 0),
                                   transition_cost(a, b), transition_cost(b, a)))
        return tuple(pairs), tuple(observed)

    @staticmethod
    def _hysteresis_index(pairs):
        decided = [p for p in pairs if p.agrees is not None]
        return None if not decided else round(sum(1 for p in decided if p.agrees) / len(decided), 6)

    # ------------------------------------------------------------------ decisions

    def decision_structure(self):
        """Non-decisions, principal-agent divergence that mattered, and refused institutional acts."""
        decisions = [d for record in self.society.history for d in record.decisions]
        persons = [d for d in decisions if d.kind == 'person']
        firms = [d for d in decisions if d.kind == 'firm']
        institutions = [d for d in decisions if d.kind == 'institution']
        unknown_reasons, chosen_kinds, refusal_reasons = {}, {}, {}
        for decision in persons:
            if decision.is_unknown:
                unknown_reasons[decision.unknown_reason] = unknown_reasons.get(decision.unknown_reason, 0) + 1
            else:
                chosen_kinds[decision.outcome] = chosen_kinds.get(decision.outcome, 0) + 1
        for decision in institutions:
            for reason in decision.reasons:
                head = refusal_category(reason)
                refusal_reasons[head] = refusal_reasons.get(head, 0) + 1
        # A firm's record carries two different kinds of event and pooling them would make the
        # divergence share meaningless: what its procedure settled, and what it was or was not
        # authorized to say.
        blocked_speech = [d for d in firms if d.outcome in ('said_nothing', 'disclosure_refused')]
        capital = [d for d in firms if d.outcome not in ('said_nothing', 'disclosure_refused')]
        with_divergence = [d for d in capital if d.divergences]
        changed = [d for d in with_divergence if d.outcome_changed]
        costly = [d for d in with_divergence if d.agenda_cost]
        speech_reasons = {}
        for decision in blocked_speech:
            speech_reasons[decision.outcome] = speech_reasons.get(decision.outcome, 0) + 1
        refused = [d for d in institutions if d.authority != 'holds']
        return {
            'not_accuracy': NOT_ACCURACY,
            'persons': {
                'decisions': len(persons),
                'unknown': sum(1 for d in persons if d.is_unknown),
                'unknown_share': round(sum(1 for d in persons if d.is_unknown) / len(persons), 6)
                if persons else None,
                'unknown_by_reason': dict(sorted(unknown_reasons.items())),
                'chosen_by_kind': dict(sorted(chosen_kinds.items())),
                'label': 'Unknown is an honest non-decision, not a failure: tc.choose abstains when '
                         'the constraints exclude everything or two options sit inside its margin',
            },
            'firms': {
                'decisions': len(capital),
                'speech_acts_blocked': len(blocked_speech),
                'speech_acts_blocked_by_kind': dict(sorted(speech_reasons.items())),
                'with_divergence': len(with_divergence),
                'outcome_changed': len(changed),
                'outcome_changed_share': round(len(changed) / len(with_divergence), 6)
                if with_divergence else None,
                'agenda_cost_nonzero': len(costly),
                'agenda_cost': distribution([d.agenda_cost for d in capital if d.agenda_cost is not None],
                                            label='what the firm gave up, in its own objective units, '
                                                  'because a role and not the firm set the agenda'),
                'label': 'a recorded divergence that never changes an outcome is a divergence that '
                         'did not act; the two counts are kept apart on purpose',
            },
            'institutions': {
                'attempts': len(institutions),
                'refused': len(refused),
                'ultra_vires_share': round(len(refused) / len(institutions), 6) if institutions else None,
                'by_authority': {status: sum(1 for d in institutions if d.authority == status)
                                 for status in ('holds', 'fails', 'unknown')},
                'refusal_reasons': dict(sorted(refusal_reasons.items())),
                'label': 'an act refused as ultra vires is on the record twice, in the ActLog and as '
                         'an ultra_vires claim; silence in the charter authorizes unknown, never yes',
            },
        }

    # ------------------------------------------------------------------ tiers and budget

    def tiering(self, *, memory_budget_bytes=8 * 1024 ** 3, resident=None):
        from .society import TIERS as SOCIETY_TIERS
        assert TIERS == SOCIETY_TIERS, 'the tier vocabulary drifted between society and observatory'
        society = self.society
        budget = society.budget(memory_budget_bytes=memory_budget_bytes, resident=resident)
        return {
            'tiers': society._tier_counts(),
            'cohorts': [cohort.to_json() for cohort in sorted(society.cohorts.values(), key=lambda c: c.id)],
            'promotions': [report.to_json() for report in society._promotions],
            'demotions': [report.to_json() for report in society._demotions],
            'budget': budget.to_json(),
            'label': 'the focal ceiling is a memory ceiling at the measured claims-per-agent of this '
                     'run; it is not a throughput claim',
        }

    # ------------------------------------------------------------------ the report

    def report(self, *, memory_budget_bytes=8 * 1024 ** 3, resident=None, include_series=True):
        """The whole measurement, as a JSON-serializable dict reproducible from the seed."""
        society = self.society
        divergence = self.belief_divergence()
        if not include_series:
            divergence = {k: v for k, v in divergence.items() if k != 'series'}
            divergence['provenance'] = {k: v for k, v in divergence['provenance'].items() if k != 'series'}
        return {
            'schema': 'worldmodel.agents.observatory/1',
            'epistemic_status': EPISTEMIC_STATUS,
            'validated': False,
            'not_accuracy': NOT_ACCURACY,
            'disclaimer': ('This report measures the internal behaviour of a simulation of grounded '
                           'agents. No statistic in it is a forecast, a fit, or a comparison against '
                           'an observed outcome. The agents are seeded from published records and '
                           'their beliefs diverge from those records by design.'),
            'reproduce': {'seed': society.seed, 'ticks': society.ticks,
                          'config': society.config or None,
                          'clock': society.clock.to_json(),
                          'index': str(getattr(society.index, 'path', '')) or None,
                          'command': 'wm society-run --config <config> --ticks %d --seed %d'
                                     % (society.ticks, society.seed)},
            'society': society.to_json(),
            'ticks': [record.to_json() for record in society.history],
            'belief_divergence': divergence,
            'propagation': self.propagation(),
            'trajectories': self.trajectories(),
            'decision_structure': self.decision_structure(),
            'tiering': self.tiering(memory_budget_bytes=memory_budget_bytes, resident=resident),
        }

    def publish(self, store, *, dataset='agent_society_report', parameters=None, report=None):
        """Publish the report through the ordinary artifact path. Returns the artifact reference."""
        from ..artifacts import publish_report
        payload = report if report is not None else self.report()
        parameters = dict(parameters or {})
        parameters.setdefault('seed', self.society.seed)
        parameters.setdefault('ticks', self.society.ticks)
        return publish_report(store, dataset, payload, parameters,
                              entrypoint='worldmodel.agents.observatory:Observatory.report')


# --------------------------------------------------------------------------- rendering


def render(report):
    """A compact human-readable rendering of a published report. Used by ``wm society-report``."""
    out = ['%s  seed=%s  ticks=%s' % (report.get('schema', 'society report'),
                                      report.get('reproduce', {}).get('seed'),
                                      report.get('reproduce', {}).get('ticks')),
           'epistemic status: %s (validated=%s)' % (report.get('epistemic_status'), report.get('validated')),
           report.get('disclaimer', ''), '']
    society = report.get('society') or {}
    out.append('simultaneity: %s' % society.get('simultaneity'))
    out.append('population: %d member(s), %d utterance(s), %d delivery(ies)'
               % (len(society.get('population') or ()), society.get('utterances', 0),
                  society.get('deliveries', 0)))
    seams = society.get('seams') or {}
    missing = sorted(name for name, present in seams.items() if not present)
    if missing:
        out.append('seams not installed (fallbacks in use): ' + ', '.join(missing))
    divergence = report.get('belief_divergence') or {}
    aggregate = divergence.get('aggregate') or {}
    out += ['', 'belief divergence (%s)' % divergence.get('measure', ''),
            '  samples=%s mean=%s sd=%s' % (aggregate.get('count'), aggregate.get('mean'),
                                            aggregate.get('sd')),
            '  quantiles: ' + ', '.join('%s=%s' % pair for pair in
                                        sorted((aggregate.get('quantiles') or {}).items())),
            '  ' + str(divergence.get('label', ''))]
    provenance = divergence.get('provenance') or {}
    record_share = provenance.get('from_the_record_share') or {}
    hearsay_share = provenance.get('hearsay_share') or {}
    out += ['  belief provenance: share from the record mean=%s min=%s; share from the channel mean=%s max=%s'
            % (record_share.get('mean'), record_share.get('min'),
               hearsay_share.get('mean'), hearsay_share.get('max'))]
    propagation = report.get('propagation') or {}
    reach, latency = propagation.get('reach_fraction') or {}, propagation.get('latency_ticks') or {}
    out += ['', 'propagation',
            '  utterances: %s formal (disclosed or issued), %s informal (said)'
            % (propagation.get('formal_utterances'), propagation.get('informal_utterances')),
            '  distinct claims=%s deliveries=%s (held %s; held nothing: %s)'
            % (propagation.get('distinct_claims'), propagation.get('deliveries'),
               propagation.get('accepted'),
               ', '.join('%s=%s' % pair for pair in sorted((propagation.get('not_held') or {}).items()))
               or 'none'),
            '  reach fraction mean=%s max=%s' % (reach.get('mean'), reach.get('max')),
            '  latency ticks mean=%s max=%s' % (latency.get('mean'), latency.get('max'))]
    for row in propagation.get('degradation') or ():
        out.append('  %d hop(s): %d delivery(ies), mean fidelity %.4f'
                   % (row['hops'], row['deliveries'], row['mean_fidelity']))
    trajectories = report.get('trajectories') or {}
    hysteresis = trajectories.get('hysteresis') or {}
    out += ['', 'trajectories',
            '  motif mean dwell=%s  regime mean dwell=%s'
            % ((trajectories.get('stickiness') or {}).get('motif_mean_dwell'),
               (trajectories.get('stickiness') or {}).get('regime_mean_dwell')),
            '  hysteresis index=%s over %s reciprocal pair(s)'
            % (hysteresis.get('index'), hysteresis.get('reciprocal_pairs_observed'))]
    for pair in hysteresis.get('pairs') or ():
        out.append('  %s<->%s  %d/%d  cost %.2f/%.2f  agrees=%s'
                   % (pair['from'], pair['to'], pair['forward'], pair['backward'],
                      pair['cost_forward'], pair['cost_backward'], pair['agrees']))
    if (trajectories.get('regime_hysteresis') or {}).get('measured') is False:
        out.append('  regime hysteresis: not measured (a firm has no transition-cost matrix; see '
                   'docs/corporate-cognition.md 1)')
    decisions = report.get('decision_structure') or {}
    persons, firms, institutions = (decisions.get('persons') or {}, decisions.get('firms') or {},
                                    decisions.get('institutions') or {})
    out += ['', 'decision structure',
            '  persons: %s decision(s), %s unknown (share %s) %s'
            % (persons.get('decisions'), persons.get('unknown'), persons.get('unknown_share'),
               persons.get('unknown_by_reason') or ''),
            '  firms: %s decision(s), %s with divergence, %s changed the outcome'
            % (firms.get('decisions'), firms.get('with_divergence'), firms.get('outcome_changed')),
            '  institutions: %s attempt(s), %s refused (ultra vires share %s)'
            % (institutions.get('attempts'), institutions.get('refused'),
               institutions.get('ultra_vires_share'))]
    tiering = report.get('tiering') or {}
    budget = tiering.get('budget') or {}
    out += ['', 'tiering and budget',
            '  tiers: ' + ', '.join('%s=%s' % pair for pair in sorted((tiering.get('tiers') or {}).items())),
            '  focal agents=%s claims live=%s claims/agent=%s'
            % (budget.get('focal_agents'), budget.get('claims_live'), budget.get('claims_per_focal')),
            '  bytes/claim used=%s (declared %s, serialized %s, resident %s)'
            % (budget.get('bytes_per_claim_used'), budget.get('declared_bytes_per_claim'),
               budget.get('serialized_bytes_per_claim'), budget.get('resident_bytes_per_claim')),
            '  focal ceiling at %s bytes: %s agent(s)'
            % (budget.get('memory_budget_bytes'), budget.get('focal_ceiling'))]
    for entry in tiering.get('promotions') or ():
        out.append('  promoted %s at tick %s: %s from records, %s replayed, %s invented, Unknown: %s'
                   % (entry['entity_id'], entry['at_tick'], entry['from_records'],
                      entry['from_event_log'], entry['invented'], ', '.join(entry['unknown']) or 'none'))
    for entry in tiering.get('demotions') or ():
        out.append('  demoted %s at tick %s: dropped %s claim(s), %s explanation chain(s)'
                   % (entry['entity_id'], entry['at_tick'], entry['claims_dropped'],
                      entry['derivations_dropped']))
    return out


__all__ = ['DIVERGENCE_LABEL', 'EPISTEMIC_STATUS', 'MotifPair', 'NOT_ACCURACY',
           'REFUSAL_CATEGORIES', 'Observatory', 'distribution', 'quantiles', 'refusal_category',
           'render', 'runs']
