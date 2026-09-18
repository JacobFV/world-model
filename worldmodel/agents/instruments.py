"""Institutional instruments: an institution does not chat, it issues.

``docs/corporate-communication.md`` is the commentary. A firm **discloses**
(:mod:`worldmodel.agents.disclosure`); an institution **issues an instrument** - a referral, a
rule, a subpoena, a finding - and that is a third kind of speech act again, with properties
neither a person's nor a firm's:

**It must be authorized.** An instrument is not a statement about the world, it is an exercise of
power over a subject, so it goes through the existing
:func:`~worldmodel.agents.roles.authorize` against the role's *declared* power and *declared*
jurisdiction. An instrument that does not authorize is never issued: the refusal is on the
:class:`~worldmodel.agents.roles.ActLog`, in the store as an ``ultra_vires`` claim, and the
institution's legitimacy erodes for having tried. That machinery is
:meth:`~worldmodel.agents.institution.Institution.act`'s and is used, not reimplemented.

**It has a life.** :class:`~worldmodel.agents.institution.Instrument` already carries
``effective_from``, ``expires`` and ``lapsed``; this module adds the acts that move it -
:meth:`Docket.lapse` and :meth:`Docket.contest` - and keeps the record of who contested what, so
"is this still in force" has an answer at any date.

**It is addressed.** An instrument carries an audience and named recipients like a disclosure
does, because who a subpoena was served on is part of what it is. Reception is
:func:`~worldmodel.agents.disclosure.receive`, shared deliberately: an instrument served on a firm
becomes a claim in the firm's store whose provenance says *this was served on me by that
committee*, exactly as a filing read by the public says *this was disclosed by that issuer*.

**It grants power it did not have.** A referral is the grounded case and the reason the whole
thing works: a measure referred to a committee *is* that committee's authority over that measure,
and the referral is a published edge. So the jurisdiction an instrument creates and the
jurisdiction :func:`authorize` checks are the same set, read from the catalog.

Nothing here is fitted to an outcome and nothing here is ``validated``.
"""
import datetime as _dt
from dataclasses import dataclass, replace

from . import load_tensacode

tc = load_tensacode()

from .disclosure import AUDIENCE_REACH, AUDIENCES  # noqa: E402
from .institution import Instrument  # noqa: E402
from .roles import Act, Authority, seated  # noqa: E402

UTC = _dt.timezone.utc


def _utc(value):
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, _dt.date):
        return _dt.datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = str(value).replace('Z', '+00:00')
    for candidate in (text, text[:19], text[:10]):
        try:
            parsed = _dt.datetime.fromisoformat(candidate)
        except ValueError:
            continue
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


# --------------------------------------------------------------------------- kinds


@dataclass(frozen=True)
class InstrumentKind:
    """One kind of instrument, and what issuing it does.

    ``power`` is the name :func:`~worldmodel.agents.roles.authorize` checks against the role's
    declared authority - so the kind of instrument and the power to issue it are the same
    vocabulary, and a role that may hold a hearing does not thereby get to issue a subpoena.

    ``grants`` are the powers the issued instrument *confers* on its holder: a referral confers
    the power to report the measure referred, which is how an instrument extends authority rather
    than merely announcing it. ``term_days`` is a declared default life; ``None`` means the
    instrument stands until it is lapsed or superseded.
    """

    id: str
    power: str
    label: str
    default_audience: str = 'public'
    grants: frozenset = frozenset()
    revisable: bool = True
    contestable: bool = True
    term_days: float = None
    compels_subject: bool = False


#: The closed vocabulary of instruments. **Authored** - no catalog dataset publishes chamber rules
#: or an agency's organic statute - and stated at module level because changing any row changes
#: what an institution can do. The *jurisdiction* these act on is published; the verbs are not.
INSTRUMENT_KINDS = {
    'referral': InstrumentKind('referral', 'refer', 'a referral of a matter to another body',
                               'public', frozenset({'report_measure', 'hold_hearing'}),
                               term_days=None),
    'rule': InstrumentKind('rule', 'issue_rule', 'a rule of general application', 'public',
                           frozenset({'enforce_rule'}), term_days=None),
    'subpoena': InstrumentKind('subpoena', 'subpoena', 'a subpoena for testimony or documents',
                               'counterparty', frozenset(), term_days=90.0, compels_subject=True),
    'finding': InstrumentKind('finding', 'make_finding', 'a finding of fact on the record', 'public',
                              frozenset(), revisable=False, term_days=None),
}


def kind_of(kind_id):
    try:
        return INSTRUMENT_KINDS[kind_id]
    except KeyError:
        raise ValueError('unknown instrument kind %r (expected one of %s)'
                         % (kind_id, ', '.join(sorted(INSTRUMENT_KINDS)))) from None


#: Which committee seat may issue which instrument. **Authored**, for the same reason
#: :data:`~worldmodel.agents.institution.COMMITTEE_AUTHORITY` is: the catalog publishes referrals
#: and memberships, not chamber rules. A plain member holds nothing here, and that is the point of
#: the second refusal in the worked example.
COMMITTEE_INSTRUMENT_POWERS = {
    'chair': frozenset({'refer', 'subpoena', 'make_finding'}),
    'vice_chair': frozenset({'make_finding'}),
    'ranking_member': frozenset(),
    'member': frozenset(),
}


def with_instrument_powers(charter, powers_by_role=None):
    """A copy of ``charter`` whose roles also hold the instrument verbs declared for them.

    Widening only, never narrowing, and it touches nothing else: the jurisdiction, quorum and
    spending limit each role already declares are carried through untouched. Passing no table
    leaves the charter alone, which is the honest default - a role's power to issue is as
    undeclared as its power to do anything else until something declares it.
    """
    if not powers_by_role:
        return charter
    out = charter
    for role_id, extra in sorted(powers_by_role.items()):
        if role_id not in charter.roles or not extra:
            continue
        role = charter.roles[role_id]
        authority = role.authority
        widened = Authority(powers=frozenset(authority.powers) | frozenset(extra),
                            spending_limit=authority.spending_limit,
                            jurisdiction=authority.jurisdiction, quorum=authority.quorum,
                            declared=True, sources=authority.sources)
        out = out.with_role(replace(role, authority=widened))
    return out.validate()


# --------------------------------------------------------------------------- the act


@tc.action(effect='external', idempotent=True, reversible=False)
@dataclass(frozen=True)
class InstrumentAct(Act):
    """Issuing an instrument. Always jurisdictional, and never reversible.

    ``requires_jurisdiction`` is ``True`` and is not optional: an instrument reaching a subject
    outside the issuer's declared jurisdiction is the definition of *ultra vires*, and an
    *undeclared* jurisdiction authorizes ``unknown`` rather than granting permission.
    ``reversible=False`` because an issued instrument is withdrawn by lapsing or quashing it, both
    of which are new acts on the record, not an undo.
    """

    requires_jurisdiction: bool = True


# --------------------------------------------------------------------------- contests


@dataclass(frozen=True)
class Contest:
    """Somebody with standing says the instrument should not have issued."""

    instrument_id: str
    by: str
    reason: str
    at: object = None
    quashes: bool = False
    claim_id: str = None
    sources: tuple = ()

    def line(self):
        return '%s  %s contested %s: %s%s' % (str(self.at)[:10], self.by, self.instrument_id,
                                              self.reason, ' (quashed)' if self.quashes else '')


# --------------------------------------------------------------------------- issuance


@dataclass(frozen=True)
class Issuance:
    """One attempt to issue an instrument: what happened, and what it left on the record.

    ``status`` is ``issued`` or ``refused``. A refused issuance has ``instrument is None`` and
    carries the authorization's reasons; it is kept, not discarded, because the attempt is what is
    on the record and what erodes the institution's legitimacy.

    The attributes :func:`~worldmodel.agents.disclosure.receive` needs (``org``, ``statement``,
    ``form``, ``audience``, ``recipients``, ``effective_date``) are present on purpose, so an
    instrument is *received* by the same mechanism a disclosure is.
    """

    id: str
    org: str
    kind: str
    subject: str
    role_id: str
    audience: frozenset = frozenset({'public'})
    recipients: tuple = ()
    effective_date: object = None
    expires: object = None
    instrument: object = None
    status: str = 'issued'
    reasons: tuple = ()
    holder: str = ''
    memo: str = ''
    contests: tuple = ()
    quashed: bool = False
    sources: tuple = ()

    # -- what it says -------------------------------------------------------------------
    @property
    def form(self):
        return 'instrument'

    @property
    def accession(self):
        """Instruments carry no accession number; the locator is the instrument id."""
        return None

    @property
    def statement(self):
        """``(issuer, <kind>, subject)`` - what a recipient ends up holding."""
        return (self.org, self.kind, self.subject)

    @property
    def issued(self):
        return self.status == 'issued'

    @property
    def spec(self):
        return kind_of(self.kind)

    def reaches(self, audience):
        return any(audience in AUDIENCE_REACH.get(declared, ()) for declared in self.audience)

    def reaches_receiver(self, receiver):
        if self.reaches('public'):
            return True
        return str(receiver) in tuple(self.recipients)

    def current(self, at=None):
        effective = _utc(self.effective_date)
        at = _utc(at)
        return at is None or effective is None or effective <= at

    # -- whether it still binds ----------------------------------------------------------
    def in_force(self, at=None):
        """Issued, not quashed, and inside its own term. ``False`` for a refused issuance."""
        if not self.issued or self.quashed or self.instrument is None:
            return False
        return bool(self.instrument.current(at))

    @property
    def lapsed(self):
        return self.instrument is not None and self.instrument.lapsed

    def line(self):
        if not self.issued:
            return '%s  %-9s on %-28s by %-14s -> %s (%s)' % (
                str(self.effective_date)[:10], self.kind, self.subject, self.role_id, self.status,
                '; '.join(self.reasons) or 'authorization did not hold')
        return '%s  %-9s on %-28s by %-14s -> %s%s%s' % (
            str(self.effective_date)[:10], self.kind, self.subject, self.role_id,
            ','.join(sorted(self.audience)),
            '' if self.expires is None else ', expires %s' % str(self.expires)[:10],
            '' if not self.contests else ', contested x%d%s' % (len(self.contests),
                                                                ' (quashed)' if self.quashed else ''))


# --------------------------------------------------------------------------- the docket


class Docket:
    """An institution's instruments: issued, refused, in force, lapsed, contested.

    Wraps an existing :class:`~worldmodel.agents.institution.Institution`. Every issuance goes
    through :meth:`Institution.act`, which authorizes first and invokes second, so a refused
    instrument provably never reaches the executor and is recorded three ways: the ``Receipt``, the
    ``ActLog``, and an ``ultra_vires`` claim in the store.

    Passing ``powers`` **replaces the wrapped institution's charter** with
    :func:`with_instrument_powers` applied to it, which is the one side effect this class has on the
    object it wraps. It is explicit and it widens only: role ids, jurisdictions, quorums and
    spending limits are carried through unchanged, so nothing already declared is weakened and no
    role scope moves. Passing no ``powers`` leaves the institution exactly as it was, and then every
    issuance authorizes ``unknown`` - the honest reading of a catalog that publishes no chamber
    rules.
    """

    def __init__(self, institution, *, powers=None, executor=None, register_instruments=True):
        self.institution = institution
        self.entity_id = institution.entity_id
        if powers:
            institution.charter = with_instrument_powers(institution.charter, powers)
        self.executor = executor
        self.register_instruments = bool(register_instruments)
        self._issuances = []
        self._by_id = {}
        self._counter = 0
        self._notes = []

    # -- scopes -------------------------------------------------------------------------
    @property
    def scope(self):
        """Where issued instruments are recorded. The institution's own scope holds the act."""
        return tc.Ref('instruments:%s' % self.entity_id)

    @property
    def issuances(self):
        return tuple(self._issuances)

    def get(self, issuance_id):
        return self._by_id.get(issuance_id)

    @property
    def refusals(self):
        return tuple(i for i in self._issuances if not i.issued)

    def in_force(self, at=None):
        return tuple(i for i in self._issuances if i.in_force(at))

    # -- issuing ------------------------------------------------------------------------
    def issue(self, kind, subject, *, role_id, audience=None, recipients=(), effective_date=None,
              expires=None, grants=None, subjects=None, memo='', sources=(), id=None,
              executor=None, term_days=None):
        """Issue an instrument, or refuse it. Returns an :class:`Issuance` either way.

        ``subjects`` is what the resulting instrument *reaches* and defaults to the subject it was
        issued about. ``grants`` defaults to the kind's declared grants, so a referral confers the
        power to report the measure referred and nothing else.

        The refusal path is not an error: an institution attempting an act outside its declared
        authority is a thing that happens, it is recorded, and its legitimacy falls for it.
        """
        spec = kind_of(kind)
        audiences = frozenset((audience,) if isinstance(audience, str)
                              else audience or (spec.default_audience,))
        unknown = sorted(a for a in audiences if a not in AUDIENCES)
        if unknown:
            raise ValueError('unknown audience(s) %s' % ', '.join(unknown))
        at = _utc(effective_date) or _dt.datetime.now(UTC)
        term = spec.term_days if term_days is None else term_days
        ends = _utc(expires) if expires is not None else \
            (at + _dt.timedelta(days=float(term)) if term else None)
        self._counter += 1
        issuance_id = id or '%s:%s:%03d' % (self.entity_id, kind, self._counter)
        act = InstrumentAct(power=spec.power, subject=subject,
                            memo=memo or '%s issued by %s' % (kind, role_id), payload=issuance_id)
        holder = seated(self.institution.holders, role_id, at)
        receipt = self.institution.act(act, role_id=role_id, at=at,
                                       executor=executor if executor is not None else self.executor,
                                       key='%s:%s:%s:%s' % (self.entity_id, kind, subject or '-', str(at)[:10]))
        record = self.institution.log.records[-1] if len(self.institution.log) else None
        reasons = tuple(record.reasons) if record is not None else ()
        if receipt.status != 'applied':
            issuance = Issuance(issuance_id, self.entity_id, kind, subject, role_id, audiences,
                                tuple(recipients), at, ends, None,
                                'refused', reasons,
                                holder.person if holder is not None else '', memo, sources=tuple(sources))
            self._issuances.append(issuance)
            self._by_id[issuance_id] = issuance
            return issuance
        instrument = Instrument(
            id=issuance_id,
            grants=frozenset(grants if grants is not None else spec.grants),
            subjects=frozenset(subjects if subjects is not None else ((subject,) if subject else ())),
            effective_from=at, expires=ends, lapsed=False, sources=tuple(sources))
        if self.register_instruments:
            self.institution.instruments = list(self.institution.instruments) + [instrument]
        issuance = Issuance(issuance_id, self.entity_id, kind, subject, role_id, audiences,
                            tuple(recipients), at, ends, instrument, 'issued', reasons,
                            holder.person if holder is not None else '', memo, sources=tuple(sources))
        self._issuances.append(issuance)
        self._by_id[issuance_id] = issuance
        self._record(issuance)
        return issuance

    def _record(self, issuance):
        """An ``issued`` claim in the docket scope, derived from the authority it rested on."""
        premises = tuple(sorted(r.id for r in self.institution.mind.claims(
            self.institution.ref, 'authorized_by', scope=self.institution.scope)))
        claim = tc.Claim(self.institution.ref, 'issued',
                         (issuance.kind, issuance.subject, tuple(sorted(issuance.audience)),
                          str(issuance.effective_date)[:10]), scope=self.scope)
        self.institution.mind.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
            tc.Ref('instrument:%s' % issuance.id), _utc(issuance.effective_date),
            method='issue:%s' % issuance.kind, derived_from=premises),)),),
            self.institution.mind.revision))
        return claim.id

    # -- the life of an instrument -------------------------------------------------------
    def lapse(self, issuance_id, *, at=None, reason='instrument lapsed'):
        """The instrument stops binding. Mandate coverage falls with it and does not recover."""
        issuance = self._require(issuance_id)
        if issuance.instrument is None:
            raise ValueError('issuance %r was refused and has no instrument to lapse' % issuance_id)
        self.institution.lapse(issuance.instrument.id, at=at, reason=reason)
        return self._replace(issuance, instrument=replace(issuance.instrument, lapsed=True))

    def contest(self, issuance_id, reason, *, by, at=None, quashes=False, source=None):
        """Record that the instrument is contested; optionally quash it.

        Contestation is a recorded fact about what the institution did, so it lowers legitimacy
        through :meth:`Institution.contest` whether or not it succeeds. ``quashes=True`` also
        lapses the instrument, which is the only way an instrument stops binding early.
        """
        issuance = self._require(issuance_id)
        if not issuance.spec.contestable:
            raise ValueError('a %s is declared uncontestable' % issuance.kind)
        at = _utc(at) or _dt.datetime.now(UTC)
        claim_id = self.institution.contest(issuance.subject or issuance_id,
                                           '%s contested %s: %s' % (by, issuance.kind, reason),
                                           source=source, at=at)
        contest = Contest(issuance_id, str(by), reason, at, bool(quashes), claim_id,
                          (source,) if source is not None else ())
        issuance = self._replace(issuance, contests=issuance.contests + (contest,),
                                 quashed=issuance.quashed or bool(quashes))
        if quashes and issuance.instrument is not None and not issuance.instrument.lapsed:
            issuance = self.lapse(issuance_id, at=at, reason='quashed on contest: %s' % reason)
        return issuance

    def _require(self, issuance_id):
        issuance = self._by_id.get(issuance_id)
        if issuance is None:
            raise ValueError('no issuance %r on this docket' % issuance_id)
        return issuance

    def _replace(self, issuance, **changes):
        updated = replace(issuance, **changes)
        self._issuances = [updated if i.id == issuance.id else i for i in self._issuances]
        self._by_id[issuance.id] = updated
        return updated

    # -- inspection ---------------------------------------------------------------------
    def note(self, text):
        self._notes.append(text)
        return text

    def unknown(self):
        return tuple(self.institution.unknown()) + tuple(self._notes)

    def lines(self, *, at=None):
        out = ['%s docket: %d issuance(s), %d refused, %d in force at %s'
               % (self.entity_id, len(self._issuances), len(self.refusals),
                  len(self.in_force(at)), str(at)[:10] if at else 'now')]
        for issuance in self._issuances:
            out.append('  ' + issuance.line())
            for contest in issuance.contests:
                out.append('    ' + contest.line())
        if self._notes:
            out.append('  unknown: ' + '; '.join(self._notes))
        return out


# --------------------------------------------------------------------------- worked example

#: The House Committee on the Budget, the same institution ``worldmodel.agents.institution`` is
#: built against. Its declared jurisdiction is the set of measures actually referred to it, which
#: is what makes one referral authorized and another ultra vires on published data alone.
WORKED_EXAMPLE = 'congress:committee:hsbu00'

#: Where a committee referral goes. **Authored**: the catalog publishes referrals *into* a
#: committee, never a committee's referrals out, so the recipient of a referral is declared here.
REFERRAL_RECIPIENT = 'usgov:agency:doj'


def worked_example(*, index, entity_id=WORKED_EXAMPLE, at=None, referrals=200):
    """Seed a real committee and have it issue one authorized referral and two that are not.

    Returns ``(docket, report)``. The measure in the authorized referral is one actually referred
    to this committee (``referred_to_committee``, published); the measure in the refused one is
    not. The third attempt is by a plain member, who holds no power to refer at all - so the two
    refusals have *different reasons*, which is the point of having three outcomes rather than two.
    """
    from .institution import Institution, RecordingExecutor, committee_records

    records = committee_records(index, entity_id, referrals=referrals)
    institution = Institution(records)
    docket = Docket(institution, powers=COMMITTEE_INSTRUMENT_POWERS, executor=RecordingExecutor())
    docket.note('a committee\'s referrals *out* are published in no catalog dataset; the recipient '
                'of a referral is authored (%s)' % REFERRAL_RECIPIENT)
    jurisdiction = sorted(institution.declared_jurisdiction())
    at = _utc(at) or _dt.datetime.now(UTC)
    inside = jurisdiction[0] if jurisdiction else None
    outside = 'congress:measure:not-referred-to-%s' % entity_id.split(':')[-1]
    rows = []
    if inside is not None:
        rows.append(('a measure referred to this committee, by the chair',
                     docket.issue('referral', inside, role_id='chair', audience='public',
                                  recipients=(REFERRAL_RECIPIENT,), effective_date=at,
                                  memo='referral of a measure within the declared jurisdiction')))
    rows.append(('a measure never referred to this committee, by the chair',
                 docket.issue('referral', outside, role_id='chair', audience='public',
                              recipients=(REFERRAL_RECIPIENT,), effective_date=at,
                              memo='referral of a measure outside the declared jurisdiction')))
    if 'member' in institution.charter.roles and inside is not None:
        rows.append(('a measure referred to this committee, by a plain member',
                     docket.issue('referral', inside, role_id='member', audience='public',
                                  recipients=(REFERRAL_RECIPIENT,), effective_date=at,
                                  memo='referral attempted without the power to refer')))
    report = {'entity_id': entity_id, 'name': institution.name, 'jurisdiction': len(jurisdiction),
              'attempts': tuple((label, issuance) for label, issuance in rows),
              'executor_calls': len(docket.executor.calls) if docket.executor is not None else 0,
              'legitimacy': institution.read(as_of=at), 'unknown': docket.unknown()}
    return docket, report


__all__ = ['COMMITTEE_INSTRUMENT_POWERS', 'Contest', 'Docket', 'INSTRUMENT_KINDS', 'Instrument',
           'InstrumentAct', 'InstrumentKind', 'Issuance', 'REFERRAL_RECIPIENT', 'WORKED_EXAMPLE',
           'kind_of', 'with_instrument_powers', 'worked_example']
