"""A firm: agentive, not experiential.

``docs/agents-design.md`` section 2 is the specification and ``docs/corporate-cognition.md`` is
the commentary. What is here, and why none of it is a person with relabelled fields:

**Roles, not one self.** A :class:`~worldmodel.agents.roles.Charter` declares board, executive
and operating-unit roles. Each is a *claim scope*. The firm has no single self-model scope, no
``me`` ref, no self-salience. What would be a self is split across scopes that hold different,
sometimes contradictory beliefs, and nothing merges them.

**Decisions are procedures.** :meth:`Firm.decide` runs a
:class:`~worldmodel.agents.roles.Procedure`: one role proposes (under *its own* objective, which
is what makes agenda-setting a source of divergence), others review, a role with the authority to
settle approves (under *its* objective), and only then may an act execute. Each step passes
:func:`~worldmodel.agents.roles.authorize`. There is more than one ``choose`` in a decision and
they run under different objectives; that is the model, not an implementation detail.

**Institutional memory outlives members.** Claims live in three kinds of scope. ``firm:<id>``
holds policy, precedent, contract and filed facts and is **never** decayed or forgotten.
``role:<org>:<role>`` holds the office's mandate and objective and survives its holders.
``holder:<person>`` holds what is personal to the individual. :meth:`Firm.vacate` retracts and
forgets *only* the holder scope, reports which derivations were withdrawn with it, and writes a
``precedent`` claim recording the succession - so the firm remembers that the office changed
hands after it has forgotten everything the departing person privately believed. There is no
episodic decay anywhere: :attr:`Firm.decay_policy` is ``'none'`` and says why.

**Principal-agent divergence is representable.** The firm's objective and a role's objective are
separate claim sets in separate scopes. :meth:`Firm.divergences` finds the options on which they
disagree, records a ``diverges_from`` claim whose premises are *both* objective claims, and
:meth:`Decision.divergence_lines` renders it as "the CFO's objective diverged from the firm's
here". Nothing averages them.

**Corporate readings, not feelings.** :meth:`Firm.read` returns a
:class:`~worldmodel.agents.corporate_affect.CorporateReading`, whose viability gradient is a real
cash trajectory when the filings are real.

**Ascription.** :meth:`Firm.ascription` is agency 0.85, phenomenality 0.0, and therefore
``harm_constraint`` ``None``: model a firm with the agent template and apply no
phenomenality-weighted harm constraint to it.
"""
import datetime as _dt
import re
from dataclasses import dataclass, field, replace

from . import load_tensacode

tc = load_tensacode()

from tensacode.cognition import Fragment, Rule, Thought, explain as _explain, integrate, think  # noqa: E402

from . import corporate_affect as ca  # noqa: E402
from .roles import (ActLog, Authority, Charter, CorporateAct, Holder, Procedure, Role,  # noqa: E402
                    Source, Step, run_procedure, seated)

UTC = _dt.timezone.utc
V = tc.Var


def _utc(value):
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=UTC)
    if value is None:
        return None
    text = str(value)[:19].replace('Z', '')
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = _dt.datetime.fromisoformat(str(value)[:10])
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- objectives


@dataclass(frozen=True)
class Aim:
    """One component of an objective: a measure, a direction, and how much it counts.

    An ``Aim`` is the *object* of an ``objective`` claim, so it carries provenance like any other
    claim and two scopes can hold contradictory aims about the same measure without either being
    privileged.
    """

    measure: str
    direction: str = 'maximize'    # 'maximize' | 'minimize' | 'at_least' | 'at_most'
    target: float = None
    weight: float = 1.0
    scale: float = 1.0

    def score(self, value):
        """Higher is better. ``at_least``/``at_most`` score 0 when satisfied, negative when not."""
        if value is None:
            return None
        if self.direction == 'maximize':
            return value / self.scale
        if self.direction == 'minimize':
            return -value / self.scale
        if self.direction == 'at_least':
            return min(0.0, value - (self.target or 0.0)) / self.scale
        if self.direction == 'at_most':
            return min(0.0, (self.target or 0.0) - value) / self.scale
        raise ValueError('unknown objective direction %r' % self.direction)


@dataclass(frozen=True)
class Option:
    """A course of action, with the measure changes whoever built it *declares* it would cause.

    ``effects`` are declared assumptions, not estimates and not fitted to anything. They are what
    makes objectives comparable across options; the decision record keeps them so that a reader
    can disagree with the assumption rather than with the arithmetic.
    """

    id: str
    label: str = ''
    effects: tuple = ()            # ((measure, delta), ...)
    amount: float = None           # capital at risk, checked against the acting role's limit
    power: str = 'allocate_capital'

    def delta(self, measure):
        for name, value in self.effects:
            if name == measure:
                return float(value)
        return 0.0

    def measures(self, base):
        return {name: value + self.delta(name) for name, value in base.items() if value is not None}


STATUS_QUO = Option('status_quo', 'do nothing', (), None, 'allocate_capital')


def utility(aims, option, base):
    """Total score of ``option`` under ``aims``, given the firm's current measures.

    ``None`` when an aim names a measure the record does not support: an objective that cannot be
    evaluated is not scored as zero.
    """
    if not aims:
        return None
    projected = option.measures(base)
    total = 0.0
    for aim in aims:
        score = aim.score(projected.get(aim.measure))
        if score is None:
            return None
        total += aim.weight * score
    return round(total, 9)


def preferred(aims, options, base):
    """The option ``aims`` ranks first. Ties break on option id, so the result is deterministic."""
    scored = [(utility(aims, option, base), option) for option in options]
    scored = [(u, o) for u, o in scored if u is not None]
    if not scored:
        return None, None
    best = min(scored, key=lambda pair: (-pair[0], pair[1].id))
    return best[1], best[0]


# --------------------------------------------------------------------------- deterministic choose


def _argmax_runtime():
    """A tensacode runtime whose ``choose`` is a deterministic argmax over a declared utility.

    ``tc.choose`` abstains with ``Unknown`` when more than one option is feasible and no backend
    is bound, which is correct: a decision needs an implementation. This one is in-process,
    deterministic and seed-free, so a firm's decision is reproducible.
    """
    @tc.implementation('choose', name='worldmodel.agents.argmax', version='1')
    def choose_argmax(request):
        objective, options = request.target, list(request.subject)
        given = request.params.get('given')
        scored = [(objective.utility(option, given), str(getattr(option, 'id', option)), option)
                  for option in options]
        scored = [row for row in scored if row[0] is not None]
        if not scored:
            return options[0]
        return min(scored, key=lambda row: (-row[0], row[1]))[2]

    return tc.Runtime([choose_argmax])


# --------------------------------------------------------------------------- divergence


@dataclass(frozen=True)
class Divergence:
    """One role whose own objective ranks the options differently from the firm's.

    Kept as a first-class record *and* as a claim, so it is queryable, explainable and cannot be
    smoothed away by an averaging step that does not exist.
    """

    role_id: str
    firm_choice: str
    role_choice: str
    firm_utility_firm_choice: float
    firm_utility_role_choice: float
    contested: tuple = ()
    claim_id: str = None

    @property
    def loss(self):
        """What the firm gives up, in its own objective's units, if the role's preference wins."""
        return round(self.firm_utility_firm_choice - self.firm_utility_role_choice, 9)

    def line(self):
        return ("%s's objective diverged from the firm's: it prefers %s where the firm prefers %s"
                " (firm objective loses %.4f%s)"
                % (self.role_id, self.role_choice, self.firm_choice, self.loss,
                   '' if not self.contested else '; contested on ' + ', '.join(self.contested)))


# --------------------------------------------------------------------------- decision


@dataclass(frozen=True)
class Decision:
    """What a procedure settled, who settled it, and everything it did not smooth away."""

    procedure_id: str
    outcome: object
    chosen: object = None
    proposed: object = None
    decided_claim: str = None
    divergences: tuple = ()
    base: dict = field(default_factory=dict)
    at: object = None
    firm_preferred: object = None
    agenda_cost: float = None

    @property
    def settled(self):
        return self.chosen is not None and self.outcome.completed

    def divergence_lines(self):
        return [d.line() for d in self.divergences]

    def agenda_line(self):
        """What the firm gave up because a role, not the firm, set the agenda.

        The settling role chooses between what was *proposed* and the status quo. A role whose own
        objective diverges can therefore keep the firm's own first preference off the slate, and
        the firm ends up worse off under its own objective without any step being unauthorized.
        """
        if self.agenda_cost is None or self.firm_preferred is None or self.chosen is None:
            return ''
        if self.firm_preferred.id == self.chosen.id:
            return 'agenda: the firm settled on its own first preference (%s)' % self.chosen.id
        return ('agenda: the firm prefers %s but %s was proposed, so the settling role chose between '
                '%s and the status quo; the firm settled on %s, %.4f below its own first preference'
                % (self.firm_preferred.id, (self.proposed or self.chosen).id,
                   (self.proposed or self.chosen).id, self.chosen.id, self.agenda_cost))

    def lines(self):
        out = list(self.outcome.lines())
        if self.proposed is not None:
            out.append('proposed: %s' % self.proposed.id)
        out.append('settled: %s' % ('none (%s)' % (self.outcome.blocked_at or 'no feasible option')
                                    if self.chosen is None else self.chosen.id))
        if self.agenda_line():
            out.append(self.agenda_line())
        out.extend('divergence: ' + line for line in self.divergence_lines())
        return out


@dataclass(frozen=True)
class Succession:
    """What the firm forgot and what it kept when a role-holder left."""

    role_id: str
    person: str
    at: object
    retained: tuple = ()
    forgotten: tuple = ()
    withdrawn: tuple = ()
    precedent_claim: str = None

    def lines(self):
        return ['%s left %s on %s' % (self.person, self.role_id, str(self.at)[:10]),
                '  forgotten (personal to the holder): %d claim(s)' % len(self.forgotten),
                '  withdrawn (derivations that rested only on them): %d claim(s)' % len(self.withdrawn),
                '  retained (firm and office scopes): %d claim(s)' % len(self.retained),
                '  precedent recorded: %s' % (self.precedent_claim or 'none')]


# --------------------------------------------------------------------------- grounded inputs


@dataclass(frozen=True)
class FirmRecords:
    """Everything a firm is seeded from, all of it carrying a dataset/version/record id.

    Anything absent here stays absent: the firm makes no claim about it, and
    :attr:`FirmRecords.unknown` names what was looked for and not found.
    """

    entity_id: str
    name: str = ''
    quarters: tuple = ()
    roles: tuple = ()
    holders: tuple = ()
    ties: tuple = ()
    unknown: tuple = ()
    sources: tuple = ()

    def role(self, role_id):
        for role in self.roles:
            if role.id == role_id:
                return role
        return None


#: How a published SEC insider relationship or officer title becomes a role. **Authored.**
#: The catalog publishes who is an officer or director of an issuer and with what title; it does
#: not publish bylaws, so the *seat* is grounded and the *mapping to a seat name* is ours.
TITLE_ROLES = (
    (r'chief executive|(^|\W)ceo(\W|$)', 'ceo', 'executive'),
    (r'chief financial|(^|\W)cfo(\W|$)', 'cfo', 'executive'),
    (r'general counsel|chief legal', 'general_counsel', 'executive'),
    (r'chief accounting|(^|\W)cao(\W|$)|controller', 'controller', 'executive'),
    (r'chief operating|(^|\W)coo(\W|$)|ops excellence|operations', 'coo', 'executive'),
)

#: An authored generic delegation of authority for a US public company. The catalog publishes
#: **no** bylaws, so this is not grounded and is applied only when a caller asks for it by name.
#: It exists so a worked example can show authority binding; it is not evidence of anything.
STANDARD_DELEGATION = {
    'board': Authority(powers=frozenset({'allocate_capital', 'set_policy', 'approve_disclosure',
                                         'appoint_officer', 'settle_firm_objective'}),
                       spending_limit=1.0e10, quorum=3),
    'ceo': Authority(powers=frozenset({'allocate_capital', 'set_policy', 'propose_capital'}),
                     spending_limit=5.0e8, quorum=1),
    'cfo': Authority(powers=frozenset({'propose_capital', 'approve_disclosure'}),
                     spending_limit=1.0e8, quorum=1),
    'general_counsel': Authority(powers=frozenset({'approve_disclosure'}), spending_limit=0.0, quorum=1),
    'controller': Authority(powers=frozenset({'approve_disclosure'}), spending_limit=0.0, quorum=1),
    'coo': Authority(powers=frozenset({'propose_capital'}), spending_limit=2.5e7, quorum=1),
    'operating_unit': Authority(powers=frozenset({'propose_capital'}), spending_limit=5.0e6, quorum=1),
}

#: The default capital procedure: a unit or officer proposes, the CFO reviews under its own
#: objective, the board approves under the firm's. Three roles, two objectives, one outcome.
CAPITAL_PROCEDURE = Procedure(
    id='capital_allocation',
    settles='firm',
    steps=(Step('propose', 'cfo', 'propose', 'propose_capital',
                note='the proposing role sets the agenda under its own objective'),
           Step('review', 'general_counsel', 'review', 'approve_disclosure',
                note='disclosure review; no capital power'),
           Step('approve', 'board', 'approve', 'allocate_capital',
                note='the board settles under the firm objective')))


def _slug(text):
    return re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_')[:40] or 'unit'


def role_for_title(title, relationship):
    """``(role_id, tier)`` for one published insider record. Returns ``None`` for a pure owner."""
    relationship = tuple(relationship or ())
    lowered = (title or '').lower()
    for pattern, role_id, tier in TITLE_ROLES:
        if re.search(pattern, lowered):
            return role_id, tier
    if 'Officer' in relationship:
        return ('unit_' + _slug(title or 'officer')), 'operating_unit'
    if 'Director' in relationship:
        return 'board', 'board'
    return None


def charter_from_records(records, *, authority=None, procedures=None, delegations=()):
    """Build a charter from published roles. ``authority`` is authored; without it, undeclared.

    Passing ``authority=STANDARD_DELEGATION`` attaches the authored delegation of authority. With
    ``authority=None`` every role's authority is ``UNDECLARED``, so every act authorizes
    ``unknown`` - which is the honest reading of a catalog that publishes no bylaws.
    """
    roles = {}
    for role in records.roles:
        declared = None
        if authority is not None:
            declared = authority.get(role.id) or authority.get(role.tier)
        roles[role.id] = role if declared is None else replace(role, authority=declared)
    procedures = procedures if procedures is not None else {CAPITAL_PROCEDURE.id: CAPITAL_PROCEDURE}
    procedures = {key: value for key, value in procedures.items()
                  if all(step.role_id in roles for step in value.steps)}
    return Charter(records.entity_id, roles, tuple(delegations), procedures, records.sources).validate()


# --------------------------------------------------------------------------- the firm


class Firm:
    """A firm as a set of role scopes over one institutional store."""

    #: A person's episodic memory decays and consolidates. A firm's does not, and this is the
    #: reason: what a firm knows is held in policy, precedent, contract and filings, which are
    #: documents. They are superseded by later documents, never by the passage of time.
    decay_policy = 'none'
    decay_rationale = ('Institutional memory is documentary. A filed fact, a policy and a precedent '
                       'are retracted when a later record supersedes them and at no other time; '
                       'there is no salience, no half-life and no consolidation pass.')

    def __init__(self, records, charter=None, *, boundaries=None, covenants=(), store=None,
                 seat_policy='observed_or_unknown'):
        self.records = records
        self.seat_policy = seat_policy
        self.entity_id = records.entity_id
        self.name = records.name or records.entity_id
        self.charter = charter if charter is not None else charter_from_records(records)
        self.boundaries = boundaries or ca.DistressBoundaries()
        self.covenants = tuple(covenants)
        self.mind = store if store is not None else tc.Store()
        self.log = ActLog()
        self.holders = list(records.holders)
        self.quarters = tuple(sorted(records.quarters, key=lambda q: str(q.period_end)))
        self.ties = tuple(records.ties)
        self._last_thought = ([], [])
        self._runtime = _argmax_runtime()
        self._seed()

    # -- construction -------------------------------------------------------------------
    @property
    def ref(self):
        return tc.Ref(self.entity_id)

    @property
    def scope(self):
        """Where policy, precedent, contract and filed facts live. Never decayed."""
        return tc.Ref('firm:%s' % self.entity_id)

    @property
    def percept_scope(self):
        """The current view. A snapshot scope: superseded each filing, and that is all."""
        return tc.Ref('percept:%s' % self.entity_id)

    @property
    def divergence_scope(self):
        return tc.Ref('divergence:%s' % self.entity_id)

    def role_scopes(self):
        return self.charter.scopes()

    def _seed(self):
        """Seed roles, holders and ties. Every claim carries the record that published it."""
        edits = []
        for role in sorted(self.charter.roles.values(), key=lambda r: r.id):
            for source in role.sources:
                edits.append(tc.Tell(tc.Claim(role.scope, 'office_of', self.ref, scope=role.scope),
                                     (source.evidence(),)))
            if not role.sources:
                edits.append(tc.Tell(tc.Claim(role.scope, 'office_of', self.ref, scope=role.scope),
                                     (tc.Evidence(tc.Ref('charter:%s' % self.entity_id),
                                                  _dt.datetime.now(UTC), method='declared'),)))
        for holder in sorted(self.holders, key=lambda h: (h.role_id, h.person)):
            for source in holder.sources or ():
                edits.append(tc.Tell(tc.Claim(tc.Ref(holder.person), 'holds_office',
                                              tc.Ref('role:%s:%s' % (self.entity_id, holder.role_id)),
                                              valid=tc.Interval(_utc(holder.since), _utc(holder.until)),
                                              scope=self.scope),
                                     (source.evidence(holder.since),)))
        for tie in sorted(self.ties, key=lambda t: (t.relation, t.counterparty)):
            for source in tie.sources or ():
                edits.append(tc.Tell(tc.Claim(self.ref, tie.relation, tc.Ref(tie.counterparty), scope=self.scope),
                                     (source.evidence(),)))
        if edits:
            self.mind.apply(tc.Patch(tuple(edits), self.mind.revision))

    # -- objectives ---------------------------------------------------------------------
    def set_objective(self, aims, *, role_id=None, source=None, at=None):
        """Record an objective for the firm (``role_id=None``) or for one role.

        The firm's aims and a role's aims are separate claim sets in separate scopes. Nothing
        reconciles them and nothing needs to: conflict between them is the phenomenon.
        """
        subject = self.ref if role_id is None else self.charter.role(role_id).scope
        scope = self.scope if role_id is None else self.charter.role(role_id).scope
        evidence = (source.evidence(at) if source is not None
                    else tc.Evidence(tc.Ref('charter:%s' % self.entity_id), _utc(at) or _dt.datetime.now(UTC),
                                     method='declared:objective'))
        edits = tuple(tc.Tell(tc.Claim(subject, 'objective', aim, scope=scope), (evidence,)) for aim in aims)
        commit = self.mind.apply(tc.Patch(edits, self.mind.revision))
        self._note_revision(commit.added, commit.retracted)
        self.reconsider(commit.added)
        return tuple(commit.added)

    def adopt_commitment(self, predicate, obj, *, source=None, at=None):
        """Adopt a policy, guidance, contract or precedent. This is what a firm's memory is made of.

        Only these predicates move ``commitment_revision_rate``, the arousal analogue: a firm that
        reads a thousand new facts and revises no commitment is, structurally, unaroused.
        """
        if predicate not in ca.COMMITMENT_PREDICATES:
            raise ValueError('%r is not a commitment predicate (%s)'
                             % (predicate, ', '.join(ca.COMMITMENT_PREDICATES)))
        evidence = (source.evidence(at) if source is not None
                    else tc.Evidence(tc.Ref('charter:%s' % self.entity_id), _utc(at) or _dt.datetime.now(UTC),
                                     method='declared:%s' % predicate))
        claim = tc.Claim(self.ref, predicate, obj, scope=self.scope)
        commit = self.mind.apply(tc.Patch((tc.Tell(claim, (evidence,)),), self.mind.revision))
        self._note_revision(commit.added, commit.retracted)
        self.reconsider(commit.added)
        return claim.id

    def withdraw_commitment(self, claim_id, reason='superseded by a later record'):
        """Withdraw a commitment. Retracted, never forgotten: the firm keeps the fact that it held it."""
        commit = self.mind.apply(tc.Patch((tc.Retract(claim_id, reason),), self.mind.revision))
        self._note_revision(commit.added, commit.retracted)
        return tuple(commit.retracted)

    def _note_revision(self, added, retracted):
        seen_added, seen_retracted = self._last_thought
        self._last_thought = (list(seen_added) + list(added), list(seen_retracted) + list(retracted))

    def reconsider(self, added_ids, *, rules=None):
        """Fire the appraisal rules on claims that just arrived outside a filing.

        Adopting an objective or a policy is a new premise, so the firm reasons about it the same
        way it reasons about a filing. Without this a rule whose premises are an objective and a
        filed measure would never fire, because the filing arrived first.
        """
        records = tuple(self.mind.claim(i) for i in added_ids if i in self.mind._claims)
        if not records:
            return Thought()
        thought = think(self.mind, rules if rules is not None else self.rules(),
                        since=Thought(added=records))
        self._note_revision([r.id for r in thought.added], [r.id for r in thought.retracted])
        return thought

    def aims(self, role_id=None):
        """The aims held in one scope, in a deterministic order."""
        subject = self.ref if role_id is None else self.charter.role(role_id).scope
        scope = self.scope if role_id is None else self.charter.role(role_id).scope
        records = self.mind.claims(subject, 'objective', scope=scope)
        return tuple(r.claim.object for r in sorted(records, key=lambda r: (r.claim.object.measure, r.id)))

    def aim_claims(self, role_id=None):
        subject = self.ref if role_id is None else self.charter.role(role_id).scope
        scope = self.scope if role_id is None else self.charter.role(role_id).scope
        return tuple(r.id for r in sorted(self.mind.claims(subject, 'objective', scope=scope), key=lambda r: r.id))

    def roles_with_objectives(self):
        return tuple(role_id for role_id in sorted(self.charter.roles) if self.aims(role_id))

    # -- perception ---------------------------------------------------------------------
    def perceive(self, quarter, *, rules=None):
        """Read one filed quarter: a permanent record in the firm scope, plus a current view.

        The filed facts go into ``firm:<id>`` with the fiscal quarter as their validity interval
        and are **never** retracted by a later filing - a 10-Q does not un-happen. Only the
        derived current view (runway, margins) is a snapshot, so ``explain`` on a decision made
        three quarters ago still reaches the filing it rested on.
        """
        filed = _utc(quarter.filed) or _dt.datetime.now(UTC)
        interval = tc.Interval(_utc(quarter.period_end), _utc(quarter.period_end))
        permanent, current = [], []
        for name in ('cash', 'revenue', 'costs', 'capex'):
            value = getattr(quarter, name)
            if value is None:
                continue
            source = quarter.source_for(name)
            permanent.append((tc.Claim(self.ref, name, float(value), valid=interval, scope=self.scope),
                              source.record_id if source is not None else None))
        for name, value in self.quarter_measures(quarter).items():
            if value is not None:
                current.append((tc.Claim(self.ref, name, round(float(value), 6), scope=self.percept_scope),
                                '+'.join(s.record_id for s in quarter.sources) or None))
        source = quarter.sources[0] if quarter.sources else None
        ref = source.ref if source is not None else tc.Ref('filing:%s' % self.entity_id)
        method = 'published:%s' % (source.dataset if source is not None else 'declared')
        fragments = []
        if permanent:
            fragments.append(Fragment(ref, tuple(permanent), method=method, observed_at=filed))
        fragments.append(Fragment(ref, tuple(current), snapshot_of=self.percept_scope,
                                  method='derive:current-view', observed_at=filed))
        thought = integrate(self.mind, *fragments)
        thought = thought + think(self.mind, rules if rules is not None else self.rules(), since=thought)
        self._last_thought = ([r.id for r in thought.added], [r.id for r in thought.retracted])
        return thought

    def quarter_measures(self, quarter):
        """The measures a decision is scored on, from one filed quarter. All published or derived."""
        out = {'cash_usd': quarter.cash, 'revenue_usd': quarter.revenue,
               'costs_usd': quarter.costs, 'capex_usd': quarter.capex}
        runway = ca.cash_runway(quarter, self.boundaries)
        out['runway_quarters'] = runway
        measures = ca.covenant_measures(quarter)
        out['operating_margin'] = measures.get('operating_margin')
        out['cash_cover'] = measures.get('cash_cover')
        if quarter.revenue and quarter.costs is not None and quarter.capex is not None:
            out['free_cash_margin'] = (quarter.revenue - quarter.costs - quarter.capex) / abs(quarter.revenue)
        return out

    def measures(self):
        """The current view: the measures derived from the most recent quarter perceived."""
        if not self.quarters:
            return {}
        return self.quarter_measures(self.quarters[-1])

    # -- rules --------------------------------------------------------------------------
    def rules(self):
        """Appraisal rules whose premises deliberately cross role boundaries.

        The point is not the appraisals; it is that the provenance of what a firm concludes runs
        through the org chart, so ``cross_role_integration`` measures something real about the
        firm rather than about a cognitive architecture.
        """
        me = self.ref
        boundaries = self.boundaries
        roles = self.charter.roles

        def has(role_id):
            return role_id in roles

        out = []
        if has('cfo'):
            cfo = roles['cfo'].scope

            def liquidity(bindings, mind):
                aim = bindings['aim']
                if aim.measure != 'runway_quarters':
                    return
                runway = bindings['r']
                if runway < (aim.target if aim.target is not None else boundaries.runway_quarters):
                    yield tc.Claim(cfo, 'appraises_corporate', ('liquidity_pressure', round(runway, 3)),
                                   scope=cfo), tc.Score(0.8, 'uncalibrated')

            out.append(Rule('runway_below_the_firm_policy',
                            ((me, 'runway_quarters', V('r')), (me, 'objective', V('aim'))), liquidity))

            def margin(bindings, mind):
                aim = bindings['aim']
                if aim.measure != 'operating_margin':
                    return
                value = bindings['m']
                if value < (aim.target if aim.target is not None else 0.0):
                    yield tc.Claim(cfo, 'appraises_corporate', ('margin_shortfall', round(value, 4)),
                                   scope=cfo), tc.Score(0.7, 'uncalibrated')

            out.append(Rule('operating_margin_below_the_cfo_aim',
                            ((me, 'operating_margin', V('m')), (roles['cfo'].scope, 'objective', V('aim'))), margin))
        if has('cfo') and has('board'):
            board = roles['board'].scope
            cfo = roles['cfo'].scope

            def escalate(bindings, mind):
                yield tc.Claim(board, 'appraises_corporate', ('escalated', bindings['a'][0]),
                               scope=board), tc.Score(0.6, 'uncalibrated')

            # The second premise is the board's own office claim, so the derivation's provenance
            # genuinely spans two role scopes; that is what ``cross_role_integration`` counts.
            out.append(Rule('the_board_hears_what_the_cfo_appraised',
                            ((cfo, 'appraises_corporate', V('a')), (board, 'office_of', me)), escalate))
        return out

    # -- readings -----------------------------------------------------------------------
    def read(self, *, as_of=None):
        """The corporate readings at the latest filed quarter. None of them is a feeling."""
        current = self.quarters[-1] if self.quarters else None
        previous = self.quarters[-2] if len(self.quarters) > 1 else None
        if current is None:
            return ca.CorporateReading(as_of=as_of, ascription=ca.FIRM_ASCRIPTION)
        pressure, proximity = ca.liquidity_pressure(current, self.boundaries, self.covenants)
        field_out, diagonal = ca.coupling(self.ties)
        added, retracted = self._last_thought
        return ca.CorporateReading(
            as_of=as_of or current.filed,
            viability=ca.viability(current, self.boundaries),
            viability_gradient=ca.viability_gradient(current, previous, self.boundaries),
            commitment_revision_rate=ca.commitment_revision_rate(self.mind, self.ref, added, retracted),
            liquidity_pressure=pressure, covenant_proximity=proximity,
            exposure=ca.exposure(self.mind, self.ref, as_of=_utc(as_of or current.filed)),
            coupling=field_out, group_self_coupling=diagonal,
            cross_role_integration=ca.cross_role_integration(self.mind, self.role_scopes() + (self.scope,)),
            contingency_weight=ca.contingency_weight(self.mind),
            regime=ca.regime(ca.viability(current, self.boundaries), self.boundaries),
            ascription=ca.FIRM_ASCRIPTION,
            sources=current.sources)

    def ascription(self):
        """Agency high, phenomenality zero. Perceivers apply no harm constraint to a firm."""
        return ca.FIRM_ASCRIPTION

    # -- divergence ---------------------------------------------------------------------
    def divergences(self, options, *, record=True, at=None):
        """Where a role's own objective ranks the options differently from the firm's."""
        base = self.measures()
        firm_aims = self.aims()
        if not firm_aims:
            return ()
        firm_choice, firm_utility = preferred(firm_aims, options, base)
        if firm_choice is None:
            return ()
        found, edits = [], []
        for role_id in self.roles_with_objectives():
            role_aims = self.aims(role_id)
            role_choice, _ = preferred(role_aims, options, base)
            if role_choice is None or role_choice.id == firm_choice.id:
                continue
            firm_utility_of_role_choice = utility(firm_aims, role_choice, base)
            contested = _contested(firm_aims, firm_choice, role_choice, base)
            divergence = Divergence(role_id, firm_choice.id, role_choice.id, firm_utility,
                                    firm_utility_of_role_choice, contested)
            if record:
                premises = tuple(sorted(set(self.aim_claims() + self.aim_claims(role_id))))
                claim = tc.Claim(self.charter.role(role_id).scope, 'diverges_from',
                                 (self.entity_id, firm_choice.id, role_choice.id, contested),
                                 scope=self.divergence_scope)
                edits.append(tc.Tell(claim, (tc.Evidence(tc.Ref('reading:principal-agent'),
                                                         _utc(at) or _dt.datetime.now(UTC),
                                                         method='compare:objective-sets@1',
                                                         confidence=tc.Score(abs(divergence.loss), 'utility'),
                                                         derived_from=premises),)))
                divergence = replace(divergence, claim_id=claim.id)
            found.append(divergence)
        if edits:
            self.mind.apply(tc.Patch(tuple(edits), self.mind.revision))
        return tuple(found)

    # -- decision -----------------------------------------------------------------------
    def decide(self, procedure_id, options, *, at=None, executor=None):
        """Run a procedure over roles. Not one ``choose``: several, under different objectives."""
        procedure = self.charter.procedure(procedure_id)
        options = tuple(options)
        base = self.measures()
        at = _utc(at) or _dt.datetime.now(UTC)
        divergences = self.divergences(options, at=at)
        self.project(options, at=at)

        def aims_for(role_id):
            """A role decides under its own objective, or under the firm's when it declares none."""
            own = self.aims(role_id)
            return (own, role_id) if own else (self.aims(), 'firm')

        def act_for(step, state):
            option = state.get('proposed') or (options[0] if options else STATUS_QUO)
            # Proposing and reviewing commit no capital, so no amount is checked. The amount binds
            # at the step that settles: that is where a spending limit is a limit.
            commits = step.kind in ('approve', 'ratify', 'execute') and step.power != 'approve_disclosure'
            return CorporateAct(power=step.power, subject=self.entity_id,
                                amount=option.amount if commits else None,
                                memo='%s: %s' % (step.kind, option.id), payload=option.id)

        def on_step(step, state):
            aims, whose = aims_for(step.role_id)
            if step.kind == 'propose':
                option, score = preferred(aims, options, base)
                if option is None:
                    return None, 'no option scores under the %s objective' % whose
                state['proposed'] = option
                return option, '%s proposes %s under the %s objective (utility %.4f)' % (
                    step.role_id, option.id, whose, score)
            if step.kind == 'review':
                option = state.get('proposed')
                return option, '%s reviewed %s' % (step.role_id, option.id if option else 'nothing')
            if step.kind in ('approve', 'ratify'):
                option = state.get('proposed')
                slate = tuple(dict.fromkeys([o for o in (option, STATUS_QUO) if o is not None]))
                chosen = self._choose(slate, aims, base, step.role_id)
                if isinstance(chosen, tc.Unknown):
                    return None, '%s could not settle: %s' % (step.role_id, chosen.reason)
                return chosen, '%s settles on %s under the %s objective' % (step.role_id, chosen.id, whose)
            return state.get('proposed'), ''

        outcome = run_procedure(procedure, charter=self.charter, holders=self.holders,
                                act_for=act_for, at=at, log=self.log, on_step=on_step,
                                seat_policy=self.seat_policy)
        proposed = next((o.value for o in outcome.outcomes if o.step.kind == 'propose'), None)
        chosen = next((o.value for o in reversed(outcome.outcomes)
                       if o.step.kind in ('approve', 'ratify') and o.value is not None), None)
        decided = None
        if chosen is not None and outcome.completed:
            decided = self._record_decision(procedure, chosen, at)
        firm_aims = self.aims()
        firm_preferred, firm_best = preferred(firm_aims, options, base) if firm_aims else (None, None)
        agenda_cost = None
        if firm_best is not None and chosen is not None:
            got = utility(firm_aims, chosen, base)
            agenda_cost = None if got is None else round(firm_best - got, 9)
        return Decision(procedure.id, outcome, chosen, proposed, decided, divergences, base, at,
                        firm_preferred, agenda_cost)

    def project(self, options, *, at=None):
        """Write each option's declared consequences into its own hypothetical scope.

        These are the non-actual futures ``contingency_weight`` measures. They sit in
        ``plan:<org>:<option>`` scopes, derived from the aims they are relevant to and the filings
        they start from, so a projection can be explained and contradicted like anything else.
        """
        base = self.measures()
        wanted = {aim.measure for aim in self.aims()}
        for role_id in self.charter.roles:
            wanted.update(aim.measure for aim in self.aims(role_id))
        premises = set(self.aim_claims())
        for role_id in self.charter.roles:
            premises.update(self.aim_claims(role_id))
        premises.update(r.id for r in self.mind.claims(self.ref, scope=self.scope)
                        if r.claim.predicate in ('cash', 'revenue', 'costs', 'capex'))
        evidence_at = _utc(at) or _dt.datetime.now(UTC)
        edits = []
        for option in options:
            scope = tc.Ref('plan:%s:%s' % (self.entity_id, option.id))
            projected = option.measures(base)
            for measure in sorted(wanted):
                if projected.get(measure) is None:
                    continue
                claim = tc.Claim(self.ref, 'projects', (option.id, measure, round(projected[measure], 6)),
                                 scope=scope)
                edits.append(tc.Tell(claim, (tc.Evidence(tc.Ref('plan:%s' % option.id), evidence_at,
                                                         method='declared-effects@1',
                                                         derived_from=tuple(sorted(premises))),)))
        if not edits:
            return ()
        return tuple(self.mind.apply(tc.Patch(tuple(edits), self.mind.revision)).added)

    def _choose(self, slate, aims, base, role_id):
        objective = tc.Objective('%s:%s' % (self.entity_id, role_id),
                                 'the objective held in the %s scope' % role_id,
                                 utility=lambda option, given: utility(aims, option, given or base))
        evaluable = tc.Constraint('objective_is_evaluable',
                                  lambda option, given: utility(aims, option, given or base) is not None)
        with tc.use(self._runtime):
            return tc.choose(list(slate), objective=objective, given=base, constraints=(evaluable,))

    def _record_decision(self, procedure, option, at):
        """Write the decision as a claim whose premises reach the filings it rested on."""
        premises = set(self.aim_claims())
        for role_id in self.charter.roles:
            premises.update(self.aim_claims(role_id))
        premises.update(r.id for r in self.mind.claims(self.ref, scope=self.scope)
                        if r.claim.predicate in ('cash', 'revenue', 'costs', 'capex'))
        claim = tc.Claim(self.ref, 'decided', (procedure.id, option.id), scope=self.scope)
        self.mind.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
            tc.Ref('procedure:%s' % procedure.id), at, method='procedure@1',
            derived_from=tuple(sorted(premises))),)),), self.mind.revision))
        return claim.id

    # -- succession ---------------------------------------------------------------------
    def seat(self, holder):
        self.holders.append(holder)
        return holder

    def remember_personally(self, person, claims, *, source=None, at=None):
        """Record something private to a role-holder. This is what a departure clears."""
        scope = tc.Ref('holder:%s' % person)
        evidence = (source.evidence(at) if source is not None
                    else tc.Evidence(tc.Ref('holder:%s' % person), _utc(at) or _dt.datetime.now(UTC),
                                     method='personal'))
        edits = tuple(tc.Tell(tc.Claim(tc.Ref(person), predicate, obj, scope=scope), (evidence,))
                      for predicate, obj in claims)
        return tuple(self.mind.apply(tc.Patch(edits, self.mind.revision)).added)

    def vacate(self, role_id, *, at=None, reason='role-holder left', person=None):
        """A role-holder leaves. The person's scope is cleared; the office and the firm are not.

        This is the explicit model of what a firm forgets. Everything in ``holder:<person>`` is
        retracted and then ``Store.forget``-ten, derivations that rested only on it are withdrawn
        with it, and a ``precedent`` claim records the succession in the firm scope - so the firm
        still knows the office changed hands after it has forgotten what the holder believed.
        """
        at = _utc(at) or _dt.datetime.now(UTC)
        holder = seated(self.holders, role_id, at) if person is None else \
            next((h for h in self.holders if h.role_id == role_id and h.person == person), None)
        if holder is None:
            raise ValueError('no holder of %s to vacate at %s' % (role_id, str(at)[:10]))
        scope = holder.scope
        personal = [r.id for r in self.mind.claims(scope=scope)]
        withdrawn, forgotten = (), ()
        if personal:
            commit = self.mind.apply(tc.Patch(tuple(tc.Retract(cid, reason) for cid in personal),
                                              self.mind.revision))
            forgotten = tuple(cid for cid in commit.retracted if cid in set(personal))
            withdrawn = tuple(cid for cid in commit.retracted if cid not in set(personal))
            self.mind.forget(commit.retracted)
        precedent = tc.Claim(self.ref, 'precedent',
                             ('role_vacated', role_id, holder.person, str(at)[:10]), scope=self.scope)
        evidence = (holder.sources[0].evidence(at) if holder.sources
                    else tc.Evidence(tc.Ref('charter:%s' % self.entity_id), at, method='succession'))
        self.mind.apply(tc.Patch((tc.Tell(precedent, (evidence,)),), self.mind.revision))
        self.holders = [replace(h, until=at, basis='appointment') if h is holder else h
                        for h in self.holders]
        retained = tuple(sorted(r.id for r in self.mind.claims(scope=self.scope)) +
                         sorted(r.id for scope_ref in self.role_scopes()
                                for r in self.mind.claims(scope=scope_ref)))
        return Succession(role_id, holder.person, at, retained, forgotten, withdrawn, precedent.id)

    # -- inspection ---------------------------------------------------------------------
    def explain(self, claim_id, depth=6):
        return _explain(self.mind, claim_id, depth=depth)

    def claims_in(self, scope_ref):
        return tuple(sorted(self.mind.claims(scope=scope_ref), key=lambda r: r.id))

    def unknown(self):
        """What was looked for in the catalog and not found. Never filled in with a guess."""
        return tuple(self.records.unknown)

    def summary(self):
        out = ['%s (%s)' % (self.name, self.entity_id),
               '  roles: ' + ', '.join('%s[%s]' % (r.id, r.tier)
                                       for r in sorted(self.charter.roles.values(), key=lambda r: r.id)),
               '  seated: ' + (', '.join('%s=%s' % (r, (seated(self.holders, r) or Holder('vacant', r)).person)
                                         for r in sorted(self.charter.roles)) or 'none'),
               '  filed quarters: %d' % len(self.quarters),
               '  ties: %d' % len(self.ties),
               '  memory: firm scope %d claim(s), decay policy %s'
               % (len(self.claims_in(self.scope)), self.decay_policy)]
        if self.records.unknown:
            out.append('  unknown: ' + '; '.join(self.records.unknown))
        return out


def _contested(firm_aims, firm_choice, role_choice, base):
    """Measures on which moving to the role's preference makes the firm's objective worse."""
    out = []
    for aim in firm_aims:
        here = aim.score(firm_choice.measures(base).get(aim.measure))
        there = aim.score(role_choice.measures(base).get(aim.measure))
        if here is None or there is None:
            continue
        if there < here:
            out.append(aim.measure)
    return tuple(sorted(set(out)))


# --------------------------------------------------------------------------- grounding


SEC_METRIC_FIELDS = {'cash_and_cash_equivalents': 'cash', 'revenues': 'revenue',
                     'costs_and_expenses': 'costs', 'capital_expenditure': 'capex'}


def quarters_from_observation_set(data, entity_id, *, limit=None):
    """Turn ``estimation.loaders.cash_balance_data`` output into :class:`Quarter` records.

    Every value keeps the SEC record id it was filed under (``secfacts:<cik>:<concept>:...``,
    which ends in the accession number), so a reading traces back to a filing.
    """
    buckets = {}
    for record in data.records:
        field_name = SEC_METRIC_FIELDS.get(record.get('metric'))
        if field_name is None or record.get('subject') != entity_id:
            continue
        end = (record.get('attributes') or {}).get('fiscal_period_end') or record.get('valid_to')
        bucket = buckets.setdefault(str(end)[:10], {'filed': '', 'sources': [], 'fields': []})
        bucket[field_name] = float(record['value'])
        bucket['filed'] = max(bucket['filed'], str(record.get('observed_at'))[:10])
        pinned = record.get('_input') or {}
        first = None
        for record_id in str(record.get('id') or '').split('+'):
            if record_id:
                source = Source(pinned.get('dataset', 'sec_company_assets'), pinned.get('version', ''),
                                record_id, pinned.get('stage', 'normalized'))
                bucket['sources'].append(source)
                first = first or source
        if first is not None:
            bucket['fields'].append((field_name, first))
    out = []
    for end in sorted(buckets):
        bucket = buckets[end]
        out.append(ca.Quarter(period_end=_dt.date.fromisoformat(end),
                              filed=_dt.date.fromisoformat(bucket['filed']) if bucket['filed'] else None,
                              cash=bucket.get('cash'), revenue=bucket.get('revenue'),
                              costs=bucket.get('costs'), capex=bucket.get('capex'),
                              sources=tuple(bucket['sources']),
                              field_sources=tuple(sorted(bucket['fields']))))
    complete = [q for q in out if q.complete]
    return tuple(complete[-limit:] if limit else complete)


def roles_from_index(index, entity_id, *, known_at=None, limit=400, notes=None):
    """Roles and holders from published SEC insider filings (``insider_of``).

    The catalog publishes the *seat*: who was an officer or director of this issuer, with what
    title, over what period of report, in which filing. It does not publish bylaws, so the
    authority attached to a seat is authored (see :data:`STANDARD_DELEGATION`) and is applied
    only when a caller asks for it.
    """
    rows = index.edges([entity_id], 'insider_of', direction='in', known_at=known_at, limit=limit)
    if notes is not None and len(rows) >= limit:
        notes.append('seats are bounded at the %d most recent insider filings; there may be more' % limit)
    seats, role_sources = {}, {}
    for row in rows:
        record = index.record_at(row['record_rowid'])
        if record is None:
            continue
        attributes = record.get('attributes') or {}
        mapped = role_for_title(attributes.get('officer_title'), attributes.get('relationship'))
        provenance = record.get('_provenance') or {}
        source = Source(provenance.get('dataset', 'sec_ownership_datasets'), provenance.get('version', ''),
                        provenance.get('record_id', record.get('id', '')), provenance.get('stage', 'normalized'))
        person = record.get('subject') or row['subject']
        if mapped is None:
            continue
        role_id, tier = mapped
        role_sources.setdefault(role_id, (tier, []))[1].append(source)
        seat = seats.setdefault((role_id, person), {'since': None, 'until': None, 'titles': set(), 'sources': []})
        since, until = str(record.get('valid_from') or '')[:10], str(record.get('valid_to') or '')[:10]
        if since and (seat['since'] is None or since < seat['since']):
            seat['since'] = since
        if until and (seat['until'] is None or until > seat['until']):
            seat['until'] = until
        if attributes.get('officer_title'):
            seat['titles'].add(attributes['officer_title'])
        seat['sources'].append(source)
    roles = tuple(Role(role_id, entity_id, role_id.replace('_', ' ').title(), tier,
                       sources=tuple(sorted(set(sources), key=lambda s: s.record_id))[:4])
                  for role_id, (tier, sources) in sorted(role_sources.items()))
    holders = tuple(Holder(person, role_id,
                           since=_dt.date.fromisoformat(seat['since']) if seat['since'] else None,
                           until=_dt.date.fromisoformat(seat['until']) if seat['until'] else None,
                           titles=tuple(sorted(seat['titles'])),
                           sources=tuple(sorted(set(seat['sources']), key=lambda s: s.record_id))[:4])
                    for (role_id, person), seat in sorted(seats.items()))
    return roles, holders


def ties_from_index(index, entity_id, *, cluster=None, known_at=None, limit=200, securities=400, notes=None):
    """Ownership and holding ties: GLEIF consolidation, insider ten-percent owners, 13F holders.

    Every query carries a declared row budget. A budget that binds is reported through ``notes``
    rather than silently truncating, because an absent tie must not be read as no tie.
    """
    cluster = tuple(cluster or index.cluster(entity_id))
    ties = []

    def provenance(row):
        record = index.record_at(row['record_rowid'])
        info = (record or {}).get('_provenance') or {}
        return Source(info.get('dataset', 'unknown'), info.get('version', ''),
                      info.get('record_id', ''), info.get('stage', 'normalized'))

    for predicate in ('directly_consolidated_by', 'ultimately_consolidated_by'):
        rows = index.edges(cluster, predicate, direction='in', known_at=known_at, limit=limit)
        if notes is not None and len(rows) >= limit:
            notes.append('%s is bounded at %d edges; there may be more' % (predicate, limit))
        for row in rows:
            ties.append(ca.Tie(row['subject'], predicate, sources=(provenance(row),)))
        for row in index.edges(cluster, predicate, direction='out', known_at=known_at, limit=8):
            ties.append(ca.Tie(row['object'], 'parent_of', sources=(provenance(row),)))
    for row in index.edges([entity_id], 'insider_of', direction='in', known_at=known_at, limit=limit):
        record = index.record_at(row['record_rowid'])
        if record and 'TenPercentOwner' in ((record.get('attributes') or {}).get('relationship') or ()):
            info = record.get('_provenance') or {}
            ties.append(ca.Tie(record.get('subject') or row['subject'], 'ten_percent_owner',
                               sources=(Source(info.get('dataset', 'sec_ownership_datasets'),
                                               info.get('version', ''), info.get('record_id', '')),)))
    issued = [row['object'] for row in index.edges(cluster, 'issuer_security', direction='out',
                                                   known_at=known_at, limit=securities)]
    if notes is not None and len(issued) >= securities:
        notes.append('holders are read through the %d most recent issued securities; there may be more'
                     % securities)
    cusips = []
    for security in issued:
        for member in index.cluster(security):
            if member.startswith('cusip:'):
                cusips.append(member)
    for cusip in sorted(set(cusips)):
        for row in index.edges([cusip], 'reported_holding', direction='in', known_at=known_at, limit=64):
            ties.append(ca.Tie(row['subject'], 'reported_holding', sources=(provenance(row),)))
    seen, out = set(), []
    for tie in ties:
        key = (tie.counterparty, tie.relation)
        if key in seen:
            continue
        seen.add(key)
        out.append(tie)
    return tuple(sorted(out, key=lambda t: (t.relation, t.counterparty)))


def firm_records(entity_id, *, index, catalog=None, known_at=None, max_quarters=8, name=None):
    """Assemble a :class:`FirmRecords` from the catalog. Only published records become claims.

    ``index`` is a :class:`~worldmodel.agents.grounding.EvidenceIndex`; ``catalog`` is a
    :class:`worldmodel.store.Store` over the dataset root, needed for the quarterly financials,
    which live in the published ``sec_company_assets`` records rather than in the graph index.
    Anything the catalog does not publish lands in ``unknown`` with the reason.
    """
    unknown, quarters = [], ()
    if catalog is not None:
        from ..estimation.loaders import MissingData, cash_balance_data
        try:
            data, _, _ = cash_balance_data(catalog, issuer=entity_id)
            quarters = quarters_from_observation_set(data, entity_id, limit=max_quarters)
        except MissingData as error:
            unknown.append('quarterly financials: %s' % error)
    else:
        unknown.append('quarterly financials: no catalog store was given')
    roles, holders = roles_from_index(index, entity_id, known_at=known_at, notes=unknown)
    if not roles:
        unknown.append('roles: no insider_of records published for %s' % entity_id)
    ties = ties_from_index(index, entity_id, known_at=known_at, notes=unknown)
    if not ties:
        unknown.append('ownership: no consolidation, control or holding edge published for %s' % entity_id)
    unknown.append('covenant terms: not published in any catalog dataset')
    unknown.append('bylaws and delegation of authority: not published in any catalog dataset')
    entity = index.entity(entity_id, known_at=known_at)
    sources = ()
    if entity is not None:
        info = entity.get('_provenance') or {}
        sources = (Source(info.get('dataset', ''), info.get('version', ''), info.get('record_id', '')),)
    return FirmRecords(entity_id, name or (entity or {}).get('label', '') or entity_id,
                       quarters, roles, holders, ties, tuple(unknown), sources)


# --------------------------------------------------------------------------- worked example

#: LyondellBasell Industries N.V. It is the worked example because this catalog publishes, for
#: this one issuer, all of: 45 quarters carrying cash, revenue, costs and capex together, with
#: filing dates and accession numbers (``sec_company_assets``); 35 named insiders with officer
#: titles and periods of report, including a CFO handover on 2025-03-01
#: (``sec_ownership_datasets``); 103 GLEIF
#: consolidation edges over 52 subsidiaries (``gleif_parent_relationships``); and 13F filers
#: reporting a position in one of its securities, reached through the ISIN-CUSIP bridge
#: (``sec_gleif`` + ``sec_ownership_datasets``). Very few issuers clear all four.
WORKED_EXAMPLE = 'sec:cik:0001489393'


def worked_example(*, index, catalog, entity_id=WORKED_EXAMPLE, at=None):
    """Seed the worked-example firm, set conflicting objectives, and run one real decision.

    Returns ``(firm, decision)``. The objectives and the two options are *declared* here, and
    labelled as such: the catalog publishes what the firm did, not what it was trying to do.
    """
    records = firm_records(entity_id, index=index, catalog=catalog)
    charter = charter_from_records(records, authority=STANDARD_DELEGATION)
    firm = Firm(records, charter)
    for quarter in firm.quarters:
        firm.perceive(quarter)
    at = at or (firm.quarters[-1].filed if firm.quarters else None)
    # The firm wants to stay solvent; the CFO is paid on reported margin. Both are declared.
    boundaries = firm.boundaries
    firm.set_objective((Aim('runway_quarters', 'at_least', boundaries.runway_quarters, weight=1.0, scale=1.0),
                        Aim('cash_cover', 'at_least', boundaries.cash_cover_quarters, weight=0.5, scale=1.0)),
                       at=at)
    if 'cfo' in charter.roles:
        firm.set_objective((Aim('operating_margin', 'maximize', None, weight=1.0, scale=0.01),), role_id='cfo', at=at)
    options = (Option('buyback', 'return capital to shareholders this quarter',
                      (('cash_usd', -1.0e9), ('runway_quarters', -2.5), ('cash_cover', -0.35),
                       ('operating_margin', 0.004)), amount=1.0e9),
               Option('retain', 'hold the cash and fund the turnaround',
                      (('runway_quarters', 0.8), ('cash_cover', 0.12), ('operating_margin', -0.002)),
                      amount=0.0),
               STATUS_QUO)
    decision = firm.decide(CAPITAL_PROCEDURE.id, options, at=at)
    return firm, decision


__all__ = ['Aim', 'CAPITAL_PROCEDURE', 'Decision', 'Divergence', 'Firm', 'FirmRecords', 'Option',
           'STANDARD_DELEGATION', 'STATUS_QUO', 'Succession', 'TITLE_ROLES', 'WORKED_EXAMPLE',
           'charter_from_records', 'firm_records', 'preferred', 'quarters_from_observation_set',
           'role_for_title', 'roles_from_index', 'ties_from_index', 'utility', 'worked_example']
