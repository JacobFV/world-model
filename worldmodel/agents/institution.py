"""An institution: roles and procedure like a firm, but viability is authority, not solvency.

A legislature, an agency, a regulator, a committee. Structurally it shares the firm's machinery -
roles as claim scopes, procedures over roles, institutional memory that outlives members - and
differs in exactly one place, which is the place that matters:

**Its viability measure is authority and legitimacy.** A firm can be lawful and insolvent; an
institution can be solvent and illegitimate, and only the second kills it. So there is no
viability manifold over cash here. :class:`LegitimacyReading` reads four things off the record:
how much of the institution's declared jurisdiction is covered by a *current* instrument
(``mandate_coverage``), the share of its attempted acts that were within its declared authority
(``authority_margin``), how many ultra-vires attempts are on the record (``ultra_vires``), and how
much of what it has done has been contested (``contestation``).

**Every act goes through** :func:`~worldmodel.agents.roles.authorize`. :meth:`Institution.act`
authorizes first and invokes second. An act whose authorization does not hold never reaches the
executor - the refusal is returned as a ``rejected`` ``Receipt``, appended to the
:class:`~worldmodel.agents.roles.ActLog`, *and* written into the store as an ``ultra_vires`` claim
with the reasons. Nothing is silently performed, and nothing is silently dropped either.

**Legitimacy erodes.** Recorded ultra-vires attempts decay legitimacy monotonically, whether or
not the act succeeded, because the attempt is the thing that is on the record. A lapsed instrument
lowers mandate coverage. Both are one-directional: an institution does not recover legitimacy by
behaving itself for a while, it recovers it by acquiring authority it did not have.

**Ascription** is the same as a firm's: agency high, phenomenality zero. Its members are subjects
of experience; it is not.
"""
import datetime as _dt
import math
from dataclasses import dataclass, replace

from . import load_tensacode

tc = load_tensacode()

from tensacode.cognition import explain as _explain  # noqa: E402

from . import corporate_affect as ca  # noqa: E402
from .roles import (ActLog, Authority, Charter, Holder, InstitutionalAct, Procedure, Role, Source,  # noqa: E402
                    Step, authorize, run_procedure, seated)

UTC = _dt.timezone.utc

#: How much one recorded ultra-vires attempt costs. Authored, stated here rather than buried: it
#: sets how fast legitimacy erodes and it is not calibrated against anything.
ULTRA_VIRES_DECAY = 0.35

#: How much weight contestation carries in the composite. Authored.
CONTESTATION_WEIGHT = 0.5


def _utc(value):
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=UTC)
    if value is None:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(str(value)[:19].replace('Z', ''))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- instruments


@dataclass(frozen=True)
class Instrument:
    """What gives an institution authority over something: a statute, a referral, a delegation.

    ``subjects`` is the set of entity ids the instrument reaches. This is the grounded part: a
    bill referred to a committee *is* that committee's authority to report that bill, and the
    referral is a published record.
    """

    id: str
    grants: frozenset = frozenset()
    subjects: frozenset = frozenset()
    effective_from: object = None
    expires: object = None
    lapsed: bool = False
    sources: tuple = ()

    def current(self, at=None):
        if self.lapsed:
            return False
        at = _utc(at)
        if at is None:
            return True
        start, end = _utc(self.effective_from), _utc(self.expires)
        return (start is None or start <= at) and (end is None or at <= end)


@dataclass(frozen=True)
class LegitimacyReading:
    """An institution's viability: authority and legitimacy, read off the record."""

    as_of: object = None
    mandate_coverage: ca.Component = ca.Component(None, 'no instrument and no declared jurisdiction')
    authority_margin: ca.Component = ca.Component(None, 'no act attempted yet')
    ultra_vires: ca.Component = ca.Component(None, 'no act attempted yet')
    contestation: ca.Component = ca.Component(None, 'nothing contested')
    legitimacy: ca.Component = ca.Component(None, 'not computable without a declared jurisdiction')
    procedural_integration: ca.Component = ca.Component(None, 'nothing derived yet')
    regime: str = 'unknown'
    ascription: ca.Ascription = ca.INSTITUTION_ASCRIPTION
    sources: tuple = ()

    def values(self):
        return {name: getattr(self, name).value
                for name in ('mandate_coverage', 'authority_margin', 'ultra_vires', 'contestation',
                             'legitimacy', 'procedural_integration')}

    def lines(self):
        out = ['legitimacy as of %s  regime=%s' % (str(self.as_of)[:10], self.regime)]
        for name, value in self.values().items():
            component = getattr(self, name)
            out.append('  %-24s %-10s  %s' % (name, 'unknown' if value is None else '%.4f' % value,
                                              component.basis))
        out.append('  %-24s agency=%.2f phenomenality=%.2f harm_constraint=%s'
                   % ('ascription', self.ascription.agency, self.ascription.phenomenality,
                      self.ascription.harm_constraint))
        return out


# --------------------------------------------------------------------------- executors


class RecordingExecutor:
    """The default executor: records that the act was performed, and performs nothing else.

    An institution in this layer has no world to act on, so the honest executor is one whose only
    effect is the record. It counts calls, which is how a test can show that a refused act never
    reached it.
    """

    def __init__(self):
        self.calls = []

    def execute(self, act, *, key):
        self.calls.append((act, key))
        return tc.Receipt(act, 'applied', idempotency_key=key, effect_id='recorded:%d' % len(self.calls))


# --------------------------------------------------------------------------- the institution


@dataclass(frozen=True)
class InstitutionRecords:
    """Everything an institution is seeded from, each item with its published record."""

    entity_id: str
    name: str = ''
    roles: tuple = ()
    holders: tuple = ()
    instruments: tuple = ()
    unknown: tuple = ()
    sources: tuple = ()


class Institution:
    """Roles, procedure, and a viability measure made of authority rather than money."""

    decay_policy = 'none'
    decay_rationale = ('The memory of an institution is its record: instruments, precedent and the '
                       'act log. Instruments lapse on their own terms and precedent is superseded '
                       'by later precedent; neither fades with time.')

    def __init__(self, records, charter=None, *, store=None, instruments=None,
                 ultra_vires_decay=ULTRA_VIRES_DECAY, seat_policy='observed_or_unknown'):
        self.records = records
        self.seat_policy = seat_policy
        self.entity_id = records.entity_id
        self.name = records.name or records.entity_id
        self.charter = charter if charter is not None else Charter(records.entity_id,
                                                                  {r.id: r for r in records.roles}).validate()
        self.mind = store if store is not None else tc.Store()
        self.log = ActLog()
        self.holders = list(records.holders)
        self.instruments = list(instruments if instruments is not None else records.instruments)
        self.ultra_vires_decay = float(ultra_vires_decay)
        self._attempts = 0
        self._ultra_vires = 0
        self._contests = 0
        self._seed()

    # -- scopes -------------------------------------------------------------------------
    @property
    def ref(self):
        return tc.Ref(self.entity_id)

    @property
    def scope(self):
        return tc.Ref('institution:%s' % self.entity_id)

    @property
    def refusal_scope(self):
        """Where refused acts live. A refusal is a record, not an absence."""
        return tc.Ref('refusals:%s' % self.entity_id)

    def role_scopes(self):
        return self.charter.scopes()

    def _seed(self):
        edits = []
        for role in sorted(self.charter.roles.values(), key=lambda r: r.id):
            for source in role.sources or ():
                edits.append(tc.Tell(tc.Claim(role.scope, 'office_of', self.ref, scope=role.scope),
                                     (source.evidence(),)))
        for holder in sorted(self.holders, key=lambda h: (h.role_id, h.person)):
            for source in holder.sources or ():
                edits.append(tc.Tell(tc.Claim(tc.Ref(holder.person), 'holds_office', holder_role_ref(self, holder),
                                              valid=tc.Interval(_utc(holder.since), _utc(holder.until)),
                                              scope=self.scope), (source.evidence(holder.since),)))
        for instrument in sorted(self.instruments, key=lambda i: i.id):
            for source in instrument.sources or ():
                edits.append(tc.Tell(tc.Claim(self.ref, 'authorized_by', tc.Ref('instrument:%s' % instrument.id),
                                              scope=self.scope), (source.evidence(instrument.effective_from),)))
        if edits:
            self.mind.apply(tc.Patch(tuple(edits), self.mind.revision))

    # -- authority ----------------------------------------------------------------------
    def declared_jurisdiction(self):
        """Every subject any role declares authority over, from the charter."""
        out = set()
        for role in self.charter.roles.values():
            if role.authority.jurisdiction:
                out.update(role.authority.jurisdiction)
        return frozenset(out)

    def covered_jurisdiction(self, at=None):
        """Every subject a *current* instrument reaches."""
        out = set()
        for instrument in self.instruments:
            if instrument.current(at):
                out.update(instrument.subjects)
        return frozenset(out)

    def act(self, act, *, role_id, executor=None, key=None, at=None):
        """Authorize, then invoke. A refused act never reaches the executor.

        Returns a ``tc.Receipt``. ``rejected`` means the authorization did not hold and nothing
        was attempted; the reason is on the receipt, in the :class:`ActLog` and in an
        ``ultra_vires`` claim in the store.
        """
        role = self.charter.role(role_id)
        at = _utc(at) or _dt.datetime.now(UTC)
        holder = seated(self.holders, role_id, at)
        self._attempts += 1
        verdict = authorize(act, role=role, charter=self.charter, holder=holder, at=at,
                            log=self.log, org=self.entity_id, seat_policy=self.seat_policy)
        if verdict.status != 'holds':
            self._ultra_vires += 1
            reason = '; '.join(verdict.reasons) or 'authorization did not hold'
            claim = tc.Claim(role.scope, 'ultra_vires',
                             (act.power, act.subject, verdict.status, reason), scope=self.refusal_scope)
            self.mind.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
                tc.Ref('authorize:%s' % self.entity_id), at, method='authorize@1'),)),), self.mind.revision))
            return tc.Receipt(act, 'rejected', idempotency_key=key,
                              error='%s: %s' % (verdict.status, reason))
        executor = executor if executor is not None else RecordingExecutor()
        receipt = tc.invoke(act, executor=executor, key=key or self._key(act, at))
        if receipt.status == 'applied':
            claim = tc.Claim(self.ref, 'acted', (act.power, act.subject, str(at)[:10]), scope=self.scope)
            premises = tuple(sorted(r.id for r in self.mind.claims(self.ref, 'authorized_by', scope=self.scope)))
            self.mind.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
                tc.Ref('authorize:%s' % self.entity_id), at, method='authorize@1',
                derived_from=premises),)),), self.mind.revision))
        return receipt

    def _key(self, act, at):
        return '%s:%s:%s:%s' % (self.entity_id, act.power, act.subject or '-', str(at)[:10])

    def decide(self, procedure_id, *, at=None, act_for=None, on_step=None):
        """Run a declared procedure over roles, authorizing every step."""
        procedure = self.charter.procedure(procedure_id)
        at = _utc(at) or _dt.datetime.now(UTC)
        if act_for is None:
            def act_for(step, state):
                return InstitutionalAct(power=step.power, subject=self.entity_id,
                                        memo='%s by %s' % (step.kind, step.role_id))
        outcome = run_procedure(procedure, charter=self.charter, holders=self.holders,
                                act_for=act_for, at=at, log=self.log, on_step=on_step,
                                seat_policy=self.seat_policy)
        self._attempts += len(outcome.outcomes)
        self._ultra_vires += sum(1 for o in outcome.outcomes if not o.passed)
        return outcome

    # -- erosion ------------------------------------------------------------------------
    def lapse(self, instrument_id, *, at=None, reason='instrument lapsed'):
        """Mark an instrument lapsed. Mandate coverage falls and cannot rise without a new one."""
        found = False
        out = []
        for instrument in self.instruments:
            if instrument.id == instrument_id:
                found, instrument = True, replace(instrument, lapsed=True)
            out.append(instrument)
        if not found:
            raise ValueError('no instrument %r' % instrument_id)
        self.instruments = out
        claim = tc.Claim(self.ref, 'precedent', ('instrument_lapsed', instrument_id, reason), scope=self.scope)
        self.mind.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
            tc.Ref('instrument:%s' % instrument_id), _utc(at) or _dt.datetime.now(UTC),
            method='lapse'),)),), self.mind.revision))
        return claim.id

    def contest(self, subject, reason, *, source=None, at=None):
        """Record that something the institution did is contested. Legitimacy falls with it."""
        at = _utc(at) or _dt.datetime.now(UTC)
        self._contests += 1
        claim = tc.Claim(self.ref, 'contested', (str(subject), reason), scope=self.scope)
        evidence = source.evidence(at) if source is not None else \
            tc.Evidence(tc.Ref('contest:%s' % self.entity_id), at, method='recorded')
        self.mind.apply(tc.Patch((tc.Tell(claim, (evidence,)),), self.mind.revision))
        return claim.id

    # -- readings -----------------------------------------------------------------------
    def read(self, *, as_of=None):
        """The legitimacy reading. This is an institution's viability; there is no solvency term."""
        at = _utc(as_of)
        declared = self.declared_jurisdiction()
        covered = self.covered_jurisdiction(at)
        if declared:
            coverage = ca.Component(round(len(declared & covered) / len(declared), 6),
                                    '%d of %d declared subject(s) covered by a current instrument'
                                    % (len(declared & covered), len(declared)))
        elif covered:
            coverage = ca.Component(1.0, 'instruments are current and no role declares a narrower jurisdiction')
        else:
            coverage = ca.Component(None, 'no current instrument and no declared jurisdiction')
        if self._attempts:
            rate = self._ultra_vires / self._attempts
            margin = ca.Component(round(1.0 - rate, 6),
                                  '%d of %d attempted act(s) were within declared authority'
                                  % (self._attempts - self._ultra_vires, self._attempts))
        else:
            margin = ca.Component(None, 'no act attempted yet')
        ultra = ca.Component(float(self._ultra_vires),
                             '%d ultra-vires attempt(s) on the record' % self._ultra_vires)
        contestation = ca.Component(round(1.0 - math.exp(-self._contests), 6),
                                    '%d recorded contest(s)' % self._contests)
        erosion = math.exp(-self.ultra_vires_decay * self._ultra_vires)
        if coverage.known:
            value = coverage.value * erosion * (1.0 - CONTESTATION_WEIGHT * contestation.value)
            legitimacy = ca.Component(round(max(0.0, min(1.0, value)), 6),
                                      'mandate coverage %.3f x ultra-vires erosion %.3f x contestation %.3f'
                                      % (coverage.value, erosion, contestation.value))
        else:
            legitimacy = ca.Component(None, 'not computable without a declared or covered jurisdiction')
        return LegitimacyReading(
            as_of=as_of, mandate_coverage=coverage, authority_margin=margin, ultra_vires=ultra,
            contestation=contestation, legitimacy=legitimacy,
            procedural_integration=ca.cross_role_integration(self.mind, self.role_scopes() + (self.scope,)),
            regime=_regime(legitimacy, margin), ascription=ca.INSTITUTION_ASCRIPTION,
            sources=self.records.sources)

    def ascription(self):
        return ca.INSTITUTION_ASCRIPTION

    # -- inspection ---------------------------------------------------------------------
    def refusals(self):
        """Every refused act, from the store rather than from memory."""
        return tuple(sorted(self.mind.claims(scope=self.refusal_scope), key=lambda r: r.id))

    def explain(self, claim_id, depth=6):
        return _explain(self.mind, claim_id, depth=depth)

    def unknown(self):
        return tuple(self.records.unknown)

    def summary(self):
        return ['%s (%s)' % (self.name, self.entity_id),
                '  roles: ' + ', '.join('%s[%s]' % (r.id, r.tier)
                                        for r in sorted(self.charter.roles.values(), key=lambda r: r.id)),
                '  instruments: %d (%d current)' % (len(self.instruments),
                                                    sum(1 for i in self.instruments if i.current())),
                '  declared jurisdiction: %d subject(s)' % len(self.declared_jurisdiction()),
                '  acts attempted: %d, refused: %d' % (self._attempts, self._ultra_vires)]


def holder_role_ref(institution, holder):
    return tc.Ref('role:%s:%s' % (institution.entity_id, holder.role_id))


def _regime(legitimacy, margin):
    if not legitimacy.known:
        return 'unknown'
    if legitimacy.value <= 0.25:
        return 'illegitimate'
    if legitimacy.value <= 0.6 or (margin.known and margin.value < 0.9):
        return 'contested'
    return 'authoritative'


# --------------------------------------------------------------------------- grounding

#: How a published committee membership title becomes a role. **Authored mapping**; the titles,
#: ranks and sides themselves are published in ``congress_people``.
COMMITTEE_ROLES = {'chair': ('chair', 'chair'), 'chairman': ('chair', 'chair'),
                   'chairwoman': ('chair', 'chair'), 'vice chair': ('vice_chair', 'chair'),
                   'ranking member': ('ranking_member', 'member')}

#: Powers a committee role holds. Authored: the catalog publishes referrals and memberships, not
#: chamber rules. The *jurisdiction* is grounded (it is the referral set); the power names are not.
COMMITTEE_AUTHORITY = {
    'chair': frozenset({'report_measure', 'hold_hearing', 'set_agenda'}),
    'vice_chair': frozenset({'hold_hearing'}),
    'ranking_member': frozenset({'request_hearing'}),
    'member': frozenset(),
}


def committee_records(index, entity_id, *, known_at=None, members=80, referrals=200):
    """Seed an institution from a published congressional committee.

    Grounded: the members and their titles, ranks and sides (``committee_member``), and the
    measures referred to the committee (``referred_to_committee``), which *is* its jurisdiction -
    a committee may report a bill referred to it and no other. Not grounded, and named as such:
    the power vocabulary attached to each seat.
    """
    entity = index.entity(entity_id, known_at=known_at)
    sources = ()
    if entity is not None:
        info = entity.get('_provenance') or {}
        sources = (Source(info.get('dataset', ''), info.get('version', ''), info.get('record_id', '')),)
    referred, referral_sources = [], []
    for row in index.edges([entity_id], 'referred_to_committee', direction='in',
                           known_at=known_at, limit=referrals):
        referred.append(row['subject'])
        record = index.record_at(row['record_rowid'])
        info = (record or {}).get('_provenance') or {}
        if info:
            referral_sources.append(Source(info.get('dataset', ''), info.get('version', ''),
                                           info.get('record_id', '')))
    jurisdiction = frozenset(referred)
    seats, role_sources = {}, {}
    for row in index.edges([entity_id], 'committee_member', direction='in', known_at=known_at, limit=members):
        record = index.record_at(row['record_rowid'])
        if record is None:
            continue
        attributes = record.get('attributes') or {}
        title = str(attributes.get('title') or '').strip().lower()
        role_id, tier = COMMITTEE_ROLES.get(title, ('member', 'member'))
        info = record.get('_provenance') or {}
        source = Source(info.get('dataset', 'congress_people'), info.get('version', ''),
                        info.get('record_id', record.get('id', '')))
        role_sources.setdefault(role_id, (tier, []))[1].append(source)
        person = record.get('subject') or row['subject']
        seat = seats.setdefault((role_id, person), [])
        seat.append(source)
    roles = tuple(Role(role_id, entity_id, role_id.replace('_', ' ').title(), tier,
                       Authority(powers=COMMITTEE_AUTHORITY.get(role_id, frozenset()),
                                 jurisdiction=jurisdiction or None, quorum=1,
                                 sources=tuple(referral_sources[:2])),
                       sources=tuple(sorted(set(sources_list), key=lambda s: s.record_id))[:3])
                  for role_id, (tier, sources_list) in sorted(role_sources.items()))
    holders = tuple(Holder(person, role_id, titles=(role_id,),
                           sources=tuple(sorted(set(seat), key=lambda s: s.record_id))[:2])
                    for (role_id, person), seat in sorted(seats.items()))
    unknown = ['chamber rules and the powers attached to each seat: not published in any catalog dataset']
    if len(referred) >= referrals:
        unknown.append('jurisdiction is bounded at the %d most recent referrals; there may be more' % referrals)
    if not jurisdiction:
        unknown.append('jurisdiction: no referred_to_committee edge published for %s' % entity_id)
    if not roles:
        unknown.append('membership: no committee_member edge published for %s' % entity_id)
    instruments = ()
    if jurisdiction:
        instruments = (Instrument('referrals', frozenset({'report_measure', 'hold_hearing'}),
                                  jurisdiction, sources=tuple(referral_sources[:4])),)
    return InstitutionRecords(entity_id, (entity or {}).get('label', '') or entity_id,
                              roles, holders, instruments, tuple(unknown), sources)


def committee_procedure(chair_role='chair'):
    """Report a measure: the chair sets the agenda, then the committee reports. Two steps, both authorized."""
    return Procedure('report_measure', (Step('agenda', chair_role, 'propose', 'set_agenda'),
                                        Step('report', chair_role, 'approve', 'report_measure')),
                     settles='authority')


__all__ = ['CONTESTATION_WEIGHT', 'COMMITTEE_AUTHORITY', 'COMMITTEE_ROLES', 'Institution',
           'InstitutionRecords', 'Instrument', 'LegitimacyReading', 'RecordingExecutor',
           'ULTRA_VIRES_DECAY', 'committee_procedure', 'committee_records']
