"""Corporate readings: structural measures over a firm's own processing, deliberately not feelings.

A person's affect is read off their cognition (:mod:`worldmodel.agents.affect`). A firm's state is
read the same *structural* way - from what its store holds, what its filings say and what its
provenance graph looks like - and is named so that nobody mistakes it for an emotion:

============================  ==================================================================
person reading                corporate reading
============================  ==================================================================
``valence``                   ``viability_gradient`` - movement along a solvency manifold
``arousal``                   ``commitment_revision_rate`` - how fast it revises its own commitments
``fear``                      ``liquidity_pressure`` + ``covenant_proximity``
``shame``                     ``exposure`` - reputational and regulatory surface
``attachment``                ``coupling`` - contractual and ownership coupling, entity indexed
``integration`` (cross module) ``cross_role_integration`` (cross *role*)
``counterfactual``            ``contingency_weight``
``self_attention``/``self_causal``  **absent**; ``group_self_coupling`` instead
============================  ==================================================================

Five things here are *structurally* different from the person reading, not just renamed. They are
the whole point of the module and are spelled out in ``docs/corporate-cognition.md``:

1. **The manifold is published, so the reading is a step function, not a smoothed trace.** A
   person's readings are exponentially smoothed each tick with a weight set by gain ``gamma``.
   A firm's readings change only when a filing changes them and carry the filing date. There is
   no inertia parameter because there is no continuous inner state to have inertia.
2. **Arousal counts commitment revisions, not belief updates.** A firm can absorb an unlimited
   amount of new information at zero ``commitment_revision_rate`` as long as no policy, contract,
   guidance or objective claim changes. A person cannot.
3. **Integration is indexed by role, not by module.** The provenance statistic is the same; the
   partition is the org chart. A firm whose derivations never cross a role boundary is
   structurally compartmentalized, which is a fact about the org chart, not about a mood.
4. **There is no motif.** No nearest-prototype lookup, no asymmetric transition costs, no named
   state. Instead :func:`regime` applies *declared thresholds* and returns a test result.
   Hysteresis, where it exists in a firm, comes from covenant ratchets and disclosure rules, not
   from the shape of an affect space.
5. **The diagonal of the coupling field is not a self.** A person's entity-indexed salience field
   has self-salience on its diagonal. A firm's coupling field has ``group_self_coupling``: the
   share of its coupling mass that lies inside its own consolidation boundary. That boundary is
   published (GLEIF consolidation, ownership bands), so a firm's "self" is a matter of record.

Every reading is a :class:`Component`: a value *or* ``None`` with a ``basis`` string saying why it
is unknown. Nothing is imputed, and no component is fitted to any outcome.
"""
import datetime as _dt
import math
from dataclasses import dataclass, field

from .affect import soft_viability

UTC = _dt.timezone.utc

#: Predicates that count as a *commitment* of the firm. Revising one of these is what the
#: corporate analogue of arousal measures; nothing else moves it.
COMMITMENT_PREDICATES = ('policy', 'commitment', 'guidance', 'contract', 'objective', 'precedent')

#: Predicates that put the firm on a regulator's or the public's record. Reputational and
#: regulatory exposure is read from these, and from nothing the firm says about itself.
EXPOSURE_PREDICATES = ('sanctioned', 'designated', 'enforcement_action', 'investigation',
                       'restatement', 'litigation', 'material_weakness')

#: How strongly an ownership or contractual tie couples a counterparty to the firm. Authored
#: weights, declared here rather than buried: they order counterparties, they do not measure
#: anything. A weight is applied only when the published record supports the relation.
COUPLING_WEIGHTS = {
    'ultimately_consolidated_by': 1.00,
    'directly_consolidated_by': 0.85,
    'parent_of': 0.85,
    'controls': 0.70,
    'ten_percent_owner': 0.45,
    'reported_holding': 0.25,
    'creditor_of': 0.40,
    'counterparty_of': 0.30,
}

#: Relations that lie *inside* the firm's own consolidation boundary. The diagonal of the
#: coupling field is the share of coupling mass carried by these.
GROUP_RELATIONS = ('ultimately_consolidated_by', 'directly_consolidated_by', 'parent_of')


@dataclass(frozen=True)
class Component:
    """One reading: a number, or ``None`` with the reason the record does not support one."""

    value: float = None
    basis: str = ''
    sources: tuple = ()

    @property
    def known(self):
        return self.value is not None

    def __repr__(self):
        return 'Component(unknown: %s)' % self.basis if self.value is None else \
            'Component(%.4f, %s)' % (self.value, self.basis)


UNKNOWN_NO_FILING = Component(None, 'no published filing in scope')


@dataclass(frozen=True)
class DistressBoundaries:
    """Where the solvency manifold's boundaries are. Declared thresholds, not estimates.

    ``runway_quarters`` is the cash runway a firm is treated as needing; ``operating_margin`` and
    ``free_cash_margin`` are the margin floors; ``cash_cover_quarters`` is how many quarters of
    operating cost the cash balance should cover. Changing any of these changes the regime a firm
    reads as, so they are stated here rather than inside a function.
    """

    runway_quarters: float = 4.0
    operating_margin: float = 0.0
    free_cash_margin: float = 0.0
    cash_cover_quarters: float = 0.25   # about a month of operating cost held as cash
    margin_scale: float = 0.10        # a 10pp margin move is one unit of margin
    runway_cap: float = 12.0          # a firm that is not burning cash is capped here, not at infinity
    sharpness: float = 6.0            # the soft-min sharpness, matching ``affect.soft_viability``
    pressured: float = 0.15           # viability at or below this reads 'pressured'
    distressed: float = -0.05         # viability at or below this reads 'distressed'


@dataclass(frozen=True)
class Quarter:
    """One published fiscal quarter, as filed, with the records it came from."""

    period_end: object
    filed: object
    cash: float = None
    revenue: float = None
    costs: float = None
    capex: float = None
    sources: tuple = ()
    #: ``((field_name, Source), ...)``: which record published which number, so a claim about cash
    #: cites the cash record rather than everything filed that quarter.
    field_sources: tuple = ()

    @property
    def complete(self):
        return None not in (self.cash, self.revenue, self.costs, self.capex)

    def source_for(self, field_name):
        for name, source in self.field_sources:
            if name == field_name:
                return source
        return self.sources[0] if self.sources else None

    def cite(self):
        return tuple(s.cite() for s in self.sources)


def _clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, value))


def margins(quarter, boundaries=DistressBoundaries()):
    """Signed distances to each declared distress boundary, in roughly ``[-1, 1]``.

    Negative means the firm is past that boundary. The four margins are the corporate analogue
    of a person's viability variables (nutrition, warmth, safety...): each is a separate way to
    stop existing, and the soft minimum is dominated by the tightest one.
    """
    out = {}
    if quarter.revenue is not None and quarter.costs is not None and quarter.revenue:
        operating = (quarter.revenue - quarter.costs) / abs(quarter.revenue)
        out['operating_margin'] = _clamp((operating - boundaries.operating_margin) / boundaries.margin_scale)
        if quarter.capex is not None:
            free = (quarter.revenue - quarter.costs - quarter.capex) / abs(quarter.revenue)
            out['free_cash_margin'] = _clamp((free - boundaries.free_cash_margin) / boundaries.margin_scale)
    if quarter.cash is not None and quarter.costs is not None and quarter.costs > 0:
        cover = quarter.cash / quarter.costs
        out['cash_cover'] = _clamp(cover / boundaries.cash_cover_quarters - 1.0)
    runway = cash_runway(quarter, boundaries)
    if runway is not None:
        out['runway'] = _clamp(runway / boundaries.runway_quarters - 1.0)
    return out


def cash_runway(quarter, boundaries=DistressBoundaries()):
    """Quarters of cash at the quarter's own burn rate, capped. ``None`` when the filing is short."""
    if quarter.cash is None or quarter.revenue is None or quarter.costs is None or quarter.capex is None:
        return None
    burn = (quarter.costs + quarter.capex) - quarter.revenue
    if burn <= 0:
        return boundaries.runway_cap
    return min(boundaries.runway_cap, quarter.cash / burn)


def viability(quarter, boundaries=DistressBoundaries()):
    """Soft distance to the nearest distress boundary. The same function a person uses."""
    parts = margins(quarter, boundaries)
    if not parts:
        return Component(None, 'the filing does not carry cash, revenue, costs and capex together')
    return Component(round(soft_viability(parts, sharpness=boundaries.sharpness), 6),
                     'soft-min over ' + ', '.join(sorted(parts)), quarter.sources)


def viability_gradient(current, previous, boundaries=DistressBoundaries()):
    """Movement along the solvency manifold between two *filed* quarters: the valence analogue.

    This is the one reading that is literally real when the filings are real: a cash trajectory
    from consecutive 10-Qs is a viability gradient, not a proxy for one.
    """
    if previous is None:
        return Component(None, 'no prior filed quarter; a gradient needs two')
    now, before = viability(current, boundaries), viability(previous, boundaries)
    if not (now.known and before.known):
        return Component(None, 'a filed quarter on one side is incomplete')
    return Component(round(now.value - before.value, 6),
                     'V(%s) - V(%s), both as filed' % (current.period_end, previous.period_end),
                     current.sources + previous.sources)


def liquidity_pressure(quarter, boundaries=DistressBoundaries(), covenants=()):
    """The fear analogue: short runway and nearness to a covenant test.

    Covenant terms are **not** published in companyfacts. When none are declared the reading
    rests on runway alone and says so; it never invents a covenant.
    """
    runway = cash_runway(quarter, boundaries)
    if runway is None:
        return Component(None, 'runway needs cash, revenue, costs and capex in the same quarter'), \
            Component(None, 'no covenant terms declared for this issuer')
    pressure = 1.0 / (1.0 + runway / boundaries.runway_quarters)
    proximity = covenant_proximity(quarter, covenants)
    if proximity.known:
        return (Component(round(max(pressure, proximity.value), 6),
                          'max(runway pressure, covenant proximity)', quarter.sources), proximity)
    return (Component(round(pressure, 6),
                      'runway %.2f quarters against a declared need of %.1f; no covenant terms declared'
                      % (runway, boundaries.runway_quarters), quarter.sources), proximity)


@dataclass(frozen=True)
class Covenant:
    """A declared financial covenant. ``measure`` is a key of :func:`covenant_measures`."""

    id: str
    measure: str
    limit: float
    direction: str = 'at_least'       # or 'at_most'
    sources: tuple = ()


def covenant_measures(quarter):
    """The measures a covenant can test, computed from one filed quarter."""
    out = {}
    if quarter.revenue and quarter.costs is not None:
        out['operating_margin'] = (quarter.revenue - quarter.costs) / abs(quarter.revenue)
    if quarter.cash is not None and quarter.costs:
        out['cash_cover'] = quarter.cash / quarter.costs
    runway = cash_runway(quarter)
    if runway is not None:
        out['runway_quarters'] = runway
    return out


def covenant_proximity(quarter, covenants=()):
    """How close the tightest declared covenant is to breach, in ``[0, 1]``. 1 means breached."""
    if not covenants:
        return Component(None, 'no covenant terms declared for this issuer')
    values = covenant_measures(quarter)
    worst, basis = None, ''
    for covenant in sorted(covenants, key=lambda c: c.id):
        actual = values.get(covenant.measure)
        if actual is None or not covenant.limit:
            continue
        headroom = ((actual - covenant.limit) if covenant.direction == 'at_least'
                    else (covenant.limit - actual)) / abs(covenant.limit)
        near = _clamp(1.0 - headroom, 0.0, 1.0)
        if worst is None or near > worst:
            worst, basis = near, 'covenant %s on %s' % (covenant.id, covenant.measure)
    if worst is None:
        return Component(None, 'declared covenants test measures this filing does not carry')
    return Component(round(worst, 6), basis, tuple(s for c in covenants for s in c.sources))


def commitment_revision_rate(store, org_ref, added_ids=(), retracted_ids=(), predicates=COMMITMENT_PREDICATES):
    """The arousal analogue: the share of live commitments this period added or withdrew.

    Reads only commitment-bearing predicates. New *percepts* - however many arrive - move this
    reading by exactly nothing, which is the intended difference from a person's arousal.
    """
    live = [r for r in store._claims.values()
            if not r.retracted and r.claim.predicate in predicates and r.claim.subject == org_ref]

    def counts(ids):
        n = 0
        for claim_id in ids:
            record = store._claims.get(claim_id)
            if record is not None and record.claim.predicate in predicates and record.claim.subject == org_ref:
                n += 1
        return n
    changed = counts(added_ids) + counts(retracted_ids)
    if not live and not changed:
        return Component(None, 'the firm holds no commitments to revise')
    denominator = max(1, len(live))
    return Component(round(min(1.0, changed / denominator), 6),
                     '%d of %d live commitments revised this period' % (changed, denominator))


def exposure(store, org_ref, as_of=None, half_life_days=730.0, predicates=EXPOSURE_PREDICATES):
    """The shame analogue: reputational and regulatory surface, from what regulators published.

    Nothing the firm says about itself contributes. An empty record reads ``0.0`` with a basis
    naming what was searched, which is different from ``None``: we looked and found nothing.
    """
    as_of = as_of or _dt.datetime.now(UTC)
    if isinstance(as_of, _dt.date) and not isinstance(as_of, _dt.datetime):
        as_of = _dt.datetime(as_of.year, as_of.month, as_of.day, tzinfo=UTC)
    total, seen = 0.0, []
    for record in store._claims.values():
        if record.retracted or record.claim.predicate not in predicates:
            continue
        if record.claim.subject != org_ref:
            continue
        observed = record.evidence[0].observed_at if record.evidence else None
        weight = 1.0
        if observed is not None:
            days = max(0.0, (as_of - observed).total_seconds() / 86400.0)
            weight = math.exp(-days * math.log(2.0) / max(1.0, half_life_days))
        total += weight
        seen.append(record.claim.predicate)
    value = 1.0 - math.exp(-total)
    return Component(round(value, 6),
                     'searched %s; found %d live item(s)%s'
                     % (', '.join(predicates), len(seen), '' if not seen else ': ' + ', '.join(sorted(set(seen)))))


@dataclass(frozen=True)
class Tie:
    """One published ownership or contractual tie, and the record that published it."""

    counterparty: str
    relation: str
    weight: float = None
    sources: tuple = ()

    def strength(self):
        base = COUPLING_WEIGHTS.get(self.relation)
        if base is None:
            return None
        return base if self.weight is None else base * max(0.0, min(1.0, float(self.weight)))


def coupling(ties):
    """The attachment analogue as an entity-indexed field, plus its diagonal.

    Returns ``(field, group_self_coupling)``. The field maps counterparty id to coupling
    strength; the diagonal is the share of the total that lies inside the consolidation
    boundary - a firm's "self" is where its consolidated group ends, and that is published.

    A counterparty tied more than one way (consolidated *and* directly consolidated, or a holder
    that is also a ten-percent owner) takes its **strongest** tie. Coupling is one strength per
    counterparty, not an additive score, so two publishers describing the same relation cannot
    inflate it.
    """
    strongest = {}
    for tie in ties:
        strength = tie.strength()
        if strength is None:
            continue
        current = strongest.get(tie.counterparty)
        if current is None or strength > current[0]:
            strongest[tie.counterparty] = (strength, tie.relation)
    if not strongest:
        return {}, Component(None, 'no published ownership, control or holding edge in scope')
    total = sum(strength for strength, _ in strongest.values())
    inside = sum(strength for strength, relation in strongest.values() if relation in GROUP_RELATIONS)
    n_inside = sum(1 for _, relation in strongest.values() if relation in GROUP_RELATIONS)
    return ({key: round(value[0], 6) for key, value in sorted(strongest.items())},
            Component(round(inside / total, 6),
                      '%d of %d counterparties lie inside the consolidation boundary'
                      % (n_inside, len(strongest))))


def cross_role_integration(store, role_scopes, derived_predicates=None,
                           skip_hypothetical=('plan', 'scenario')):
    """The integration analogue, partitioned by role instead of by cognitive module.

    Counts the share of derived claims whose premises come from two or more distinct role
    scopes. The provenance mechanism is identical to ``affect.read_affect``'s; the partition is
    the org chart, so this measures how much of what the firm concludes actually crosses a
    departmental boundary.
    """
    scopes = set(role_scopes)
    derived, crossing = 0, 0
    for record in store._claims.values():
        if record.retracted or not record.evidence or not record.evidence[0].derived_from:
            continue
        if derived_predicates is not None and record.claim.predicate not in derived_predicates:
            continue
        if (skip_hypothetical and record.claim.scope is not None
                and record.claim.scope.kind in skip_hypothetical):
            continue    # a projection is a non-actual future; contingency_weight counts those
        derived += 1
        premise_scopes = set()
        for premise in record.evidence[0].derived_from:
            premise_record = store._claims.get(premise)
            if premise_record is not None and premise_record.claim.scope in scopes:
                premise_scopes.add(premise_record.claim.scope)
        if len(premise_scopes) >= 2:
            crossing += 1
    if not derived:
        return Component(None, 'the firm has derived nothing yet')
    return Component(round(crossing / derived, 6),
                     '%d of %d derived claims cross a role boundary' % (crossing, derived))


def contingency_weight(store, hypothetical_kinds=('plan', 'scenario')):
    """The counterfactual-weight analogue: the share of derivation done in hypothetical scopes."""
    derived, hypothetical = 0, 0
    for record in store._claims.values():
        if record.retracted or not record.evidence or not record.evidence[0].derived_from:
            continue
        derived += 1
        scope = record.claim.scope
        if scope is not None and scope.kind in hypothetical_kinds:
            hypothetical += 1
    if not derived:
        return Component(None, 'the firm has derived nothing yet')
    return Component(round(hypothetical / derived, 6),
                     '%d of %d derived claims sit in a hypothetical scope' % (hypothetical, derived))


def regime(viability_component, boundaries=DistressBoundaries()):
    """A declared-threshold test, not a nearest-prototype motif. Three outcomes and ``unknown``."""
    if not viability_component.known:
        return 'unknown'
    if viability_component.value <= boundaries.distressed:
        return 'distressed'
    if viability_component.value <= boundaries.pressured:
        return 'pressured'
    return 'solvent'


# --------------------------------------------------------------------------- ascription


@dataclass(frozen=True)
class Ascription:
    """How a perceiving agent should model this target: ``agency`` and ``phenomenality``.

    The two axes dissociate (``docs/civ-sim/affect-and-selfhood.md`` section 1.2). A firm is
    high on agency and at zero on phenomenality: predict it with the agent template, and apply
    **no** phenomenality-weighted harm constraint to it, because there is nothing it is like to
    be it. ``harm_constraint`` is therefore ``None`` rather than a small number.
    """

    agency: float
    phenomenality: float
    template: str = 'agent'
    rationale: str = ''

    @property
    def harm_constraint(self):
        """The constraint weight a perceiver should attach to harming this target."""
        return None if self.phenomenality <= 0.0 else self.phenomenality

    def to_json(self):
        return {'agency': self.agency, 'phenomenality': self.phenomenality, 'template': self.template,
                'harm_constraint': self.harm_constraint, 'rationale': self.rationale}


FIRM_ASCRIPTION = Ascription(
    agency=0.85, phenomenality=0.0, template='agent',
    rationale='A firm pursues declared objectives through a procedure over roles, so the agent '
              'template predicts it better than stripped dynamics (high agency). Nothing in its '
              'processing is a candidate for experience, so phenomenality is zero and no '
              'phenomenality-weighted harm constraint applies to it.')

INSTITUTION_ASCRIPTION = Ascription(
    agency=0.85, phenomenality=0.0, template='agent',
    rationale='An institution acts through declared authority and procedure (high agency) and is '
              'not a subject of experience (zero phenomenality). Its members are; it is not.')


# --------------------------------------------------------------------------- the reading


@dataclass(frozen=True)
class CorporateReading:
    """Everything read off a firm at one filing date. **None of these is a feeling.**"""

    as_of: object = None
    viability: Component = UNKNOWN_NO_FILING
    viability_gradient: Component = UNKNOWN_NO_FILING
    commitment_revision_rate: Component = UNKNOWN_NO_FILING
    liquidity_pressure: Component = UNKNOWN_NO_FILING
    covenant_proximity: Component = UNKNOWN_NO_FILING
    exposure: Component = UNKNOWN_NO_FILING
    coupling: dict = field(default_factory=dict)
    group_self_coupling: Component = UNKNOWN_NO_FILING
    cross_role_integration: Component = UNKNOWN_NO_FILING
    contingency_weight: Component = UNKNOWN_NO_FILING
    regime: str = 'unknown'
    ascription: Ascription = FIRM_ASCRIPTION
    sources: tuple = ()

    #: Which person reading each corporate reading stands in for. Kept as data so the mapping is
    #: inspectable and so no code has to guess.
    PERSON_ANALOGUE = {
        'viability_gradient': 'valence', 'commitment_revision_rate': 'arousal',
        'liquidity_pressure': 'fear', 'covenant_proximity': 'fear',
        'exposure': 'shame', 'coupling': 'attachment',
        'cross_role_integration': 'integration', 'contingency_weight': 'counterfactual',
        'group_self_coupling': 'self_attention (replaced, not renamed)',
    }

    def values(self):
        """The numeric readings as a plain mapping; unknown components come through as ``None``."""
        return {name: getattr(self, name).value
                for name in ('viability', 'viability_gradient', 'commitment_revision_rate',
                             'liquidity_pressure', 'covenant_proximity', 'exposure',
                             'group_self_coupling', 'cross_role_integration', 'contingency_weight')}

    def lines(self):
        out = ['reading as of %s  regime=%s' % (str(self.as_of)[:10], self.regime)]
        for name, value in self.values().items():
            component = getattr(self, name)
            shown = 'unknown' if value is None else '%+.4f' % value
            out.append('  %-26s %-10s  %s' % (name, shown, component.basis))
        if self.coupling:
            top = sorted(self.coupling.items(), key=lambda kv: (-kv[1], kv[0]))[:5]
            out.append('  %-26s %s' % ('coupling (top)', ', '.join('%s=%.2f' % kv for kv in top)))
        out.append('  %-26s agency=%.2f phenomenality=%.2f harm_constraint=%s'
                   % ('ascription', self.ascription.agency, self.ascription.phenomenality,
                      self.ascription.harm_constraint))
        return out


__all__ = ['Ascription', 'COMMITMENT_PREDICATES', 'COUPLING_WEIGHTS', 'Component', 'CorporateReading',
           'Covenant', 'DistressBoundaries', 'EXPOSURE_PREDICATES', 'FIRM_ASCRIPTION',
           'GROUP_RELATIONS', 'INSTITUTION_ASCRIPTION', 'Quarter', 'Tie', 'cash_runway',
           'commitment_revision_rate', 'contingency_weight', 'coupling', 'covenant_measures',
           'covenant_proximity', 'cross_role_integration', 'exposure', 'liquidity_pressure',
           'margins', 'regime', 'viability', 'viability_gradient']
