"""Roles, declared authority, and procedure: how an organization decides.

A person decides. An organization *does not*: it runs a procedure over roles, and which role
may settle what is part of the model rather than an implementation detail. This module holds
the machinery both :mod:`worldmodel.agents.firm` and :mod:`worldmodel.agents.institution` need:

``Role`` / ``Authority``
    A role is a **claim scope** with declared powers, not a label on a person. Its scope ref
    (``role:<org>:<id>``) is where that role's own beliefs and objectives live, and it outlives
    every holder.

``Holder``
    A person seated in a role for an interval, with the published records that say so. A holder
    has a scope of their own (``holder:<person>``), and that is the *only* scope a departure
    clears - see :meth:`worldmodel.agents.firm.Firm.vacate`.

``authorize``
    The check ``docs/agents-design.md`` names but tensorcode does not ship. An act is refused
    unless it is a registered ``@tc.action``, the acting role holds the power it declares, the
    act's subject is inside the role's declared jurisdiction, and its amount is inside the
    role's declared limit. Authority that was never *declared* returns ``unknown``, which is not
    ``fails``: we do not read silence as either permission or prohibition.

``ActLog``
    Append-only. **Every** authorization is recorded, grants and refusals alike, so an act that
    did not happen is still on the record with the reason it was refused.

``Procedure`` / ``run_procedure``
    An ordered sequence of steps, each naming the role that must act, the kind of act
    (``propose``, ``review``, ``approve``, ``ratify``, ``execute``) and any concurring roles
    needed for quorum. A step whose authorization does not hold blocks the procedure at that
    step; nothing downstream runs.

tensorcode is an optional dependency (see :mod:`worldmodel.agents`). This module needs it and
raises the package's install hint at import time when it is missing.
"""
import datetime as _dt
from dataclasses import dataclass, field, replace

from . import load_tensorcode

tc = load_tensorcode()

UTC = _dt.timezone.utc

#: Kinds of step a procedure can contain. The distinction is not cosmetic: ``propose`` fixes the
#: agenda (and is therefore where a role's own objective enters), ``approve`` settles it under
#: the objective of the approving role, and ``execute`` is the only kind that may touch the world.
STEP_KINDS = ('propose', 'review', 'approve', 'ratify', 'execute')

#: Tiers a role can sit in. ``board`` is the only tier that carries the organization's own
#: objective by default; everything else carries its own.
TIERS = ('board', 'executive', 'operating_unit', 'chair', 'member', 'staff')


class AuthorityError(ValueError):
    """A charter that cannot be interpreted (unknown role, unknown step kind, cyclic delegation)."""


def _utc(value):
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = str(value).replace('Z', '+00:00')
    try:
        parsed = _dt.datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = _dt.datetime.fromisoformat(text[:10])
        except ValueError:
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- provenance


@dataclass(frozen=True)
class Source:
    """One published record, pinned to the dataset version it was read from.

    This is the only way a claim gets into an organization's store. ``Unknown`` history has no
    ``Source`` and therefore no claim: absent is absent.
    """

    dataset: str
    version: str
    record_id: str
    stage: str = 'normalized'
    locator: str = None

    @property
    def ref(self):
        return tc.Ref('dataset:%s/%s@%s' % (self.dataset, self.stage, str(self.version)[:12]))

    def evidence(self, observed_at=None, method='published'):
        return tc.Evidence(self.ref, _utc(observed_at) or _dt.datetime.now(UTC),
                           locator=self.locator or self.record_id, method='%s:%s' % (method, self.dataset))

    def cite(self):
        return '%s@%s#%s' % (self.dataset, str(self.version)[:12], self.record_id)


# --------------------------------------------------------------------------- authority


@dataclass(frozen=True)
class Authority:
    """What a role may do, as *declared*. Silence is ``unknown``, never permission.

    ``spending_limit`` and ``jurisdiction`` are ``None`` when the charter does not declare them.
    An act that turns on an undeclared dimension is authorized ``unknown``: the honest answer is
    that the record does not say, and ``run_procedure`` blocks on it just as it blocks on a
    refusal, but the log distinguishes the two.
    """

    powers: frozenset = frozenset()
    spending_limit: float = None       # USD; None: not declared
    jurisdiction: frozenset = None     # entity ids this role may act on; None: not declared
    quorum: int = 1                    # concurring role-holders needed for the role to act
    declared: bool = True              # False: the charter names the role but not its powers
    sources: tuple = ()

    def holds(self, power):
        return power in self.powers


#: A role whose powers the charter does not state. Every act by it authorizes ``unknown``.
UNDECLARED = Authority(powers=frozenset(), declared=False)


@dataclass(frozen=True)
class Role:
    """A seat in an organization: a claim scope plus declared authority.

    The role, not its holder, is the unit of memory. ``scope`` is where the role's mandate and
    objectives live and it survives every succession.
    """

    id: str
    org: str
    title: str
    tier: str = 'executive'
    authority: Authority = UNDECLARED
    sources: tuple = ()

    def __post_init__(self):
        if self.tier not in TIERS:
            raise AuthorityError('unknown tier %r (expected one of %s)' % (self.tier, ', '.join(TIERS)))

    @property
    def scope(self):
        return tc.Ref('role:%s:%s' % (self.org, self.id))

    @property
    def subject(self):
        """Claims *about* the role (its objective, its mandate) take the role scope as subject."""
        return self.scope


@dataclass(frozen=True)
class Holder:
    """A person seated in a role over an interval, with the records that published it.

    ``basis`` matters, and getting it wrong is the easiest way to misread the catalog. SEC insider
    filings publish a *period of report*, not an appointment and a resignation: the window says
    when the record observed this person in this seat, and says nothing about the day after it
    ends. ``basis='observed_interval'`` (the default for grounded holders) therefore yields
    :meth:`seat_status` ``'unobserved'`` after ``until``, not ``'after'``. Only
    ``basis='appointment'`` - which a modelled succession sets - means the seat actually ended.
    """

    person: str
    role_id: str
    since: object = None
    until: object = None
    titles: tuple = ()
    sources: tuple = ()
    basis: str = 'observed_interval'     # or 'appointment'

    @property
    def scope(self):
        return tc.Ref('holder:%s' % self.person)

    def seat_status(self, moment=None):
        """``'observed'``, ``'before'``, ``'after'`` (tenure ended) or ``'unobserved'``."""
        since, until = _utc(self.since), _utc(self.until)
        moment = _utc(moment)
        if moment is None:
            if until is None:
                return 'observed'
            return 'after' if self.basis == 'appointment' else 'unobserved'
        if since is not None and moment < since:
            return 'before'
        if until is not None and moment > until:
            return 'after' if self.basis == 'appointment' else 'unobserved'
        return 'observed'

    def seated_at(self, moment=None):
        """True only when the record positively places this person in the seat at ``moment``."""
        return self.seat_status(moment) == 'observed'


@dataclass(frozen=True)
class Delegation:
    """``grantor`` lends ``power`` to ``grantee``. Delegation widens powers, never limits."""

    grantor: str
    grantee: str
    power: str
    sources: tuple = ()


# --------------------------------------------------------------------------- acts


@dataclass(frozen=True)
class Act:
    """Base for an organizational act. Subclasses carry the ``@tc.action`` registration.

    ``power`` is what the acting role must hold. ``subject`` is the entity acted upon, checked
    against the role's declared jurisdiction when the act declares ``requires_jurisdiction``.
    ``amount`` is checked against the role's declared spending limit.
    """

    power: str
    subject: str = None
    amount: float = None
    memo: str = ''
    payload: object = None
    requires_jurisdiction: bool = False

    def __str__(self):
        return '%s(%s%s)' % (type(self).__name__, self.power,
                             '' if self.subject is None else ' on ' + str(self.subject))


@tc.action(effect='write', idempotent=True, reversible=True)
@dataclass(frozen=True)
class CorporateAct(Act):
    """A firm act: capital allocation, a commitment, a disclosure, a policy change."""


@tc.action(effect='write', idempotent=True, reversible=False)
@dataclass(frozen=True)
class InstitutionalAct(Act):
    """An institutional act: reporting a measure, issuing a rule, opening an investigation.

    Defaults to requiring jurisdiction, because that is what makes an institutional act
    *ultra vires* rather than merely unwise.
    """

    requires_jurisdiction: bool = True


@dataclass(frozen=True)
class ActRecord:
    """One authorization, granted or refused, on the permanent record."""

    at: object
    org: str
    role_id: str
    holder: str
    act_kind: str
    power: str
    subject: str
    amount: float
    status: str            # 'granted' | 'refused' | 'unknown'
    reasons: tuple = ()
    memo: str = ''

    @property
    def granted(self):
        return self.status == 'granted'

    def line(self):
        head = '%s %s/%s %s' % (str(self.at)[:10], self.org, self.role_id, self.power)
        tail = '' if not self.reasons else ' (%s)' % '; '.join(self.reasons)
        return '%s -> %s%s' % (head, self.status, tail)


class ActLog:
    """Append-only record of every authorization attempt. Refusals are kept, not dropped."""

    def __init__(self):
        self._records = []

    def append(self, record):
        self._records.append(record)
        return record

    @property
    def records(self):
        return tuple(self._records)

    @property
    def grants(self):
        return tuple(r for r in self._records if r.status == 'granted')

    @property
    def refusals(self):
        return tuple(r for r in self._records if r.status != 'granted')

    def by_role(self, role_id):
        return tuple(r for r in self._records if r.role_id == role_id)

    def __len__(self):
        return len(self._records)

    def lines(self):
        return [r.line() for r in self._records]


# --------------------------------------------------------------------------- the charter


@dataclass(frozen=True)
class Procedure:
    """An ordered decision procedure over roles."""

    id: str
    steps: tuple = ()
    settles: str = ''          # the objective set the final approving role decides under

    def step(self, step_id):
        for step in self.steps:
            if step.id == step_id:
                return step
        raise AuthorityError('procedure %s has no step %r' % (self.id, step_id))


@dataclass(frozen=True)
class Step:
    """One step of a procedure: who acts, in what capacity, with what power."""

    id: str
    role_id: str
    kind: str
    power: str
    concurring: tuple = ()     # role ids that must also authorize for quorum
    note: str = ''

    def __post_init__(self):
        if self.kind not in STEP_KINDS:
            raise AuthorityError('unknown step kind %r (expected one of %s)' % (self.kind, ', '.join(STEP_KINDS)))


@dataclass(frozen=True)
class Charter:
    """The organization's standing structure: roles, delegations, procedures.

    A charter is data and it is **immutable across successions**. Holders change; this does not.
    """

    org: str
    roles: dict = field(default_factory=dict)
    delegations: tuple = ()
    procedures: dict = field(default_factory=dict)
    sources: tuple = ()

    def role(self, role_id):
        try:
            return self.roles[role_id]
        except KeyError:
            raise AuthorityError('charter for %s has no role %r' % (self.org, role_id)) from None

    def procedure(self, procedure_id):
        try:
            return self.procedures[procedure_id]
        except KeyError:
            raise AuthorityError('charter for %s has no procedure %r' % (self.org, procedure_id)) from None

    def powers_of(self, role_id):
        """Own powers plus anything delegated to this role. Deterministic ordering."""
        role = self.role(role_id)
        powers = set(role.authority.powers)
        for grant in self.delegations:
            if grant.grantee == role_id and self.role(grant.grantor).authority.holds(grant.power):
                powers.add(grant.power)
        return frozenset(powers)

    def scopes(self):
        return tuple(self.roles[key].scope for key in sorted(self.roles))

    def with_role(self, role):
        roles = dict(self.roles)
        roles[role.id] = role
        return replace(self, roles=roles)

    def validate(self):
        """Every delegation and every step must name a role this charter declares."""
        for grant in self.delegations:
            self.role(grant.grantor)
            self.role(grant.grantee)
        for procedure in self.procedures.values():
            for step in procedure.steps:
                self.role(step.role_id)
                for concurring in step.concurring:
                    self.role(concurring)
        return self


# --------------------------------------------------------------------------- authorize


#: What an act needs from the seat. ``observed`` requires the record to place a holder in the seat
#: at the moment of the act; ``observed_or_unknown`` (the default) lets the act proceed when the
#: record's observation window has merely run out, recording that continued tenure is Unknown.
SEAT_POLICIES = ('observed', 'observed_or_unknown', 'ignore')


def authorize(act, *, role, charter=None, holder=None, at=None, log=None, org=None,
              seat_policy='observed_or_unknown', seats=None):
    """Check one act against one role's declared authority. Returns a ``tc.Verdict``.

    The order of checks is the order of the reasons a refusal can have, and each one is
    reported by name:

    1. the act must be a registered ``@tc.action`` (an unregistered value is not an act);
    2. the role's authority must be *declared* at all - otherwise ``unknown``;
    3. the role (with delegations) must hold the act's ``power``;
    4. when the act requires jurisdiction, the role's declared jurisdiction must contain the
       act's subject - and an undeclared jurisdiction is ``unknown``, not permission;
    5. when the act carries an amount, the role's declared spending limit must cover it - and
       an undeclared limit is ``unknown``;
    6. when a ``holder`` is given, that holder must be seated in the role at ``at``;
    7. when ``seats`` is given (``run_procedure`` counts them), a role whose declared quorum is
       more than one must have that many seats filled at ``at``.

    Whatever the outcome, an ``ActRecord`` is appended to ``log``. Nothing about this call
    performs the act: :func:`tensorcode.invoke` is a separate step that callers gate on the
    verdict, which is how an unauthorized act is refused rather than silently performed.
    """
    reasons, status = [], 'granted'
    spec = tc.actions.spec_of(act)
    if spec is None:
        reasons.append('not a registered action')
        status = 'refused'
    else:
        powers = charter.powers_of(role.id) if charter is not None else role.authority.powers
        if not role.authority.declared:
            reasons.append('authority of role %s is not declared' % role.id)
            status = 'unknown'
        elif act.power not in powers:
            reasons.append('role %s holds no power %r (holds %s)'
                           % (role.id, act.power, ', '.join(sorted(powers)) or 'nothing'))
            status = 'refused'
        if getattr(act, 'requires_jurisdiction', False) and act.subject is not None:
            if role.authority.jurisdiction is None:
                reasons.append('jurisdiction of role %s is not declared' % role.id)
                status = 'unknown' if status != 'refused' else status
            elif act.subject not in role.authority.jurisdiction:
                reasons.append('%s is outside the declared jurisdiction of %s' % (act.subject, role.id))
                status = 'refused'
        if act.amount is not None:
            if role.authority.spending_limit is None:
                reasons.append('no spending limit declared for role %s' % role.id)
                status = 'unknown' if status != 'refused' else status
            elif float(act.amount) > float(role.authority.spending_limit):
                reasons.append('amount %.0f exceeds the declared limit %.0f for %s'
                               % (float(act.amount), float(role.authority.spending_limit), role.id))
                status = 'refused'
    if seat_policy != 'ignore':
        if holder is None:
            reasons.append('no published record of a holder of %s' % role.id)
            if seat_policy == 'observed':
                status = 'refused'
            elif status == 'granted':
                status = 'unknown'
        else:
            seat = holder.seat_status(at)
            if seat == 'unobserved':
                reasons.append('the record last observed %s in %s on %s; continued tenure is Unknown'
                               % (holder.person, role.id, str(holder.until)[:10]))
                if seat_policy == 'observed':
                    status = 'refused'
            elif seat != 'observed':
                reasons.append('%s %s %s (%s)'
                               % (holder.person,
                                  'had not yet taken' if seat == 'before' else 'had left',
                                  role.id, str(holder.since if seat == 'before' else holder.until)[:10]))
                status = 'refused'
    if seats is not None and role.authority.declared and role.authority.quorum > 1:
        if seats < role.authority.quorum:
            reasons.append('quorum of %d not met for %s: %d seat(s) filled'
                           % (role.authority.quorum, role.id, seats))
            status = 'refused'
    verdict = tc.Verdict('holds' if status == 'granted' else ('unknown' if status == 'unknown' else 'fails'),
                         tuple(reasons), tuple(s.ref for s in role.authority.sources + role.sources))
    if log is not None:
        log.append(ActRecord(at=at, org=org or role.org, role_id=role.id,
                             holder=holder.person if holder is not None else '',
                             act_kind=type(act).__name__, power=act.power, subject=act.subject,
                             amount=act.amount, status=status, reasons=tuple(reasons), memo=act.memo))
    return verdict


# --------------------------------------------------------------------------- procedure


@dataclass(frozen=True)
class StepOutcome:
    step: Step
    verdict: object
    act: object = None
    value: object = None
    note: str = ''

    @property
    def passed(self):
        return self.verdict.status == 'holds'


@dataclass(frozen=True)
class ProcedureOutcome:
    """What the procedure did, step by step, and where it stopped."""

    procedure_id: str
    outcomes: tuple = ()
    blocked_at: str = None
    value: object = None

    @property
    def completed(self):
        return self.blocked_at is None

    @property
    def refusals(self):
        return tuple(o for o in self.outcomes if not o.passed)

    def lines(self):
        out = ['procedure %s: %s' % (self.procedure_id, 'completed' if self.completed else
                                     'blocked at ' + self.blocked_at)]
        for outcome in self.outcomes:
            out.append('  %s %s by %s -> %s%s' % (
                outcome.step.id, outcome.step.kind, outcome.step.role_id, outcome.verdict.status,
                '' if not outcome.verdict.reasons else ' (%s)' % '; '.join(outcome.verdict.reasons)))
            if outcome.note:
                out.append('    ' + outcome.note)
        return out


def run_procedure(procedure, *, charter, holders, act_for, at=None, log=None, on_step=None,
                  seat_policy='observed_or_unknown'):
    """Run every step in order, stopping at the first step whose authorization does not hold.

    ``act_for(step, state)`` returns the act that step attempts (or ``None`` to skip the
    authorization and only run ``on_step``). ``on_step(step, state)`` performs the step's own
    work - proposing an option, scoring one - and returns ``(value, note)``. ``state`` is a
    plain dict that steps read and write, so a later step sees what an earlier one proposed.

    This, rather than a single ``choose``, is what an organization's decision *is*.
    """
    charter.validate()
    state, outcomes = {}, []
    for step in procedure.steps:
        role = charter.role(step.role_id)
        holder = _seated(holders, step.role_id, at)
        act = act_for(step, state)
        if act is None:
            verdict = tc.Verdict('holds', ('no act required at this step',))
        else:
            verdict = authorize(act, role=role, charter=charter, holder=holder, at=at, log=log,
                                org=charter.org, seat_policy=seat_policy,
                                seats=_filled(holders, step.role_id, at))
            for concurring_id in step.concurring:
                other = charter.role(concurring_id)
                other_verdict = authorize(act, role=other, charter=charter, seat_policy=seat_policy,
                                          holder=_seated(holders, concurring_id, at), at=at, log=log,
                                          org=charter.org, seats=_filled(holders, concurring_id, at))
                if other_verdict.status != 'holds':
                    verdict = tc.Verdict(other_verdict.status,
                                         verdict.reasons + tuple('concurrence %s: %s' % (concurring_id, r)
                                                                 for r in other_verdict.reasons))
        value, note = (None, '')
        if verdict.status == 'holds' and on_step is not None:
            value, note = on_step(step, state)
        outcomes.append(StepOutcome(step, verdict, act, value, note))
        if verdict.status != 'holds':
            return ProcedureOutcome(procedure.id, tuple(outcomes), blocked_at=step.id,
                                    value=state.get('value'))
        state[step.id] = value
        if value is not None:
            state['value'] = value
    return ProcedureOutcome(procedure.id, tuple(outcomes), None, state.get('value'))


def _seated(holders, role_id, at):
    """The holder the record places in ``role_id`` at ``at``, preferring a positive observation.

    Falls back to the most recent holder whose observation window has merely run out, so that an
    act can be attributed to *someone* while :func:`authorize` records that continued tenure is
    Unknown. Returns ``None`` only when no record ever put anyone in the seat.
    """
    mine = [h for h in holders if h.role_id == role_id]
    observed = [h for h in mine if h.seat_status(at) == 'observed']
    if observed:
        observed.sort(key=lambda h: (str(h.since or ''), h.person))
        return observed[-1]
    lapsed = [h for h in mine if h.seat_status(at) == 'unobserved']
    if lapsed:
        lapsed.sort(key=lambda h: (str(h.until or ''), h.person))
        return lapsed[-1]
    ended = [h for h in mine if h.seat_status(at) == 'after']
    if ended:
        ended.sort(key=lambda h: (str(h.until or ''), h.person))
        return ended[-1]
    mine.sort(key=lambda h: (str(h.since or ''), h.person))
    return mine[0] if mine else None


def _filled(holders, role_id, at):
    """How many seats in ``role_id`` the record fills at ``at``. A board is many seats, one role."""
    return sum(1 for h in holders
               if h.role_id == role_id and h.seat_status(at) in ('observed', 'unobserved'))


def seated(holders, role_id, at=None):
    """Public form of the seat lookup, for callers that want to show who acted."""
    return _seated(holders, role_id, at)


def filled_seats(holders, role_id, at=None):
    """Public form of the seat count, for quorum checks outside a procedure."""
    return _filled(holders, role_id, at)


__all__ = ['Act', 'ActLog', 'ActRecord', 'Authority', 'AuthorityError', 'Charter', 'CorporateAct',
           'Delegation', 'Holder', 'InstitutionalAct', 'Procedure', 'ProcedureOutcome', 'Role',
           'SEAT_POLICIES', 'Source', 'Step', 'StepOutcome', 'STEP_KINDS', 'TIERS', 'UNDECLARED', 'authorize',
           'filled_seats', 'run_procedure', 'seated']
