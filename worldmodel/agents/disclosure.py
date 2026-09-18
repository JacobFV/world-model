"""Disclosure: what an organization *says*, as against what it holds.

A person gossips. An organization **discloses**, and that is a different speech act with
different properties. ``docs/corporate-communication.md`` is the commentary; this module is the
mechanism. Five things are modelled explicitly, and each one is a place where organizational
speech is structurally unlike a person's:

**1. A disclosure has declared properties.** It is *formal* (a :class:`Form` from a closed
vocabulary, not a sentence), *dated* (an ``effective_date`` separate from the date the thing was
known), *attributable to a role* rather than to a person, *addressed to a declared audience*
(public / regulator / counterparty / internal), *legally consequential* (it is the act that
discharges an :class:`Obligation`) and *permanent* (:class:`DisclosureRegister` is append-only; a
disclosure is superseded, never edited). A person's utterance in the civ sim has none of these:
it has a dialect, a listener standing nearby, and a chance of being misheard.

**2. The gap between belief and statement.** This is the corporate analogue of rumour and it is
the substance of the module. A firm's claim store holds what it holds; what it *says* is a
**selected, timed, audience-scoped subset** of that. :meth:`DisclosureDesk.undisclosed` returns
the claims it holds and has not said; :meth:`DisclosureDesk.gaps` returns, for every claim, when
it was held, when (if ever) it was disclosed, to whom, and under what obligation. The three
dimensions are first class: *selection* (:func:`select`), *timing* (``Gap.delay_days`` - delay is
a choice, and one that is made every quarter), and *scope* (:meth:`Disclosure.reaches`).

Deception is **not** modelled as a special case. There is no "lie" flag anywhere here, unlike
``civ_sim/talk.py``'s ``_invert``. What is modelled is *selection under obligation*: a firm holds
more than it says, that is the normal condition, and the question a reader can ask is whether an
obligation compelled the part it did not say.

**3. Obligation.** :class:`Obligation` declares what is compelled: periodic reporting, material
events, ownership thresholds, insider transactions - each with a trigger, a deadline and the
citation it comes from. :func:`compliance` returns one of ``met``, ``unmet``, ``pending``,
``unknown`` or ``no_obligation``, and the two negative-looking answers are carefully distinct:

* where **no obligation is declared**, silence is ``no_obligation`` - not a violation;
* where an obligation or its deadline is **not declared**, the answer is ``unknown`` - never
  compliance. :attr:`ComplianceFinding.compliant` returns ``tc.Unknown`` in that case rather
  than ``True`` or ``False``, so an Unknown obligation cannot be read as a clean record.

**4. Reception.** :func:`receive` turns a disclosure that reaches another agent into a claim in
*that* agent's store, with ``Evidence.method='disclosed_by:<org>/<channel>'`` - the corporate
parallel to "X told me". Because a ``tc.Claim`` id is content identity, the counterparty who was
briefed privately and the public who read the filing a month later hold **the same claim id**
with different ``observed_at`` and different evidence, which is exactly the asymmetry to keep.

**5. Grounding.** None of the dates are invented. ``sec_issuer_reference`` publishes one
``sec_filing`` event per filing carrying the *form*, the *accession number*, the *filing date*
and the *report date*; the report date is when the period the filing covers ended, and the filing
date is when it was said. Their difference is the belief/disclosure gap, published, for every
filing of every issuer in the catalog. :func:`filings_from_index` reads them.

Nothing here is fitted to an outcome, scored against a baseline, or ``validated``.
"""
import datetime as _dt
from collections import defaultdict
from dataclasses import dataclass, replace

from . import load_tensacode

tc = load_tensacode()

from .roles import Act, Source, authorize, seated  # noqa: E402

UTC = _dt.timezone.utc


# --------------------------------------------------------------------------- audience


#: Who a disclosure is addressed to. Not a volume dial: these are different *channels*, with
#: different legal weight and different reach, and a firm chooses between them.
AUDIENCES = ('public', 'regulator', 'counterparty', 'internal')

#: Which audiences a disclosure addressed to one audience actually reaches. A public filing is
#: read by the regulator and by counterparties; a private briefing to a counterparty is not read
#: by the public. This asymmetry is the whole of what "scope" means for an organization.
AUDIENCE_REACH = {
    'public': ('public', 'regulator', 'counterparty', 'internal'),
    'regulator': ('regulator',),
    'counterparty': ('counterparty',),
    'internal': ('internal',),
}


# --------------------------------------------------------------------------- form


@dataclass(frozen=True)
class Form:
    """The form a disclosure takes. Form is not decoration: it fixes what the act can do.

    ``on_the_record`` means the statement is permanent and citable by a third party.
    ``revisable`` means the form has a published correction mechanism (an amended filing); a
    press release has none - a later release does not amend an earlier one, it adds to it.
    ``discharges_obligation`` means a statement in this form can satisfy a declared obligation:
    a press release does not satisfy a filing requirement, however complete it is.
    """

    id: str
    label: str
    default_audience: str
    on_the_record: bool
    revisable: bool
    discharges_obligation: bool
    attributable_to_role: bool = True


#: The closed vocabulary of forms. **Authored**: the catalog publishes filings, not a taxonomy of
#: corporate speech. Stated at module level rather than inside a function because changing any of
#: these flags changes what a disclosure can do.
FORMS = {
    'filing': Form('filing', 'a filing on the public record', 'public', True, True, True),
    'press_release': Form('press_release', 'a press release', 'public', True, False, False),
    'testimony': Form('testimony', 'testimony to a body with authority', 'regulator', True, False, True),
    'private_briefing': Form('private_briefing', 'a private briefing', 'counterparty', False, False, False),
    'internal_memo': Form('internal_memo', 'an internal memorandum', 'internal', False, True, False,
                          attributable_to_role=True),
    # An institution's form. It is here rather than in ``instruments`` so that reception is one
    # mechanism: an instrument served on a firm becomes a percept the same way a filing read by
    # the public does, with provenance saying who it came from.
    'instrument': Form('instrument', 'an instrument issued under declared authority', 'public',
                       True, True, True),
}


def form_of(form_id):
    try:
        return FORMS[form_id]
    except KeyError:
        raise ValueError('unknown disclosure form %r (expected one of %s)'
                         % (form_id, ', '.join(sorted(FORMS)))) from None


# --------------------------------------------------------------------------- time


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


def _days(later, earlier):
    later, earlier = _utc(later), _utc(earlier)
    if later is None or earlier is None:
        return None
    return round((later - earlier).total_seconds() / 86400.0, 3)


def business_days_between(start, end):
    """Weekdays strictly after ``start`` up to and including ``end``. ``None`` if either is absent.

    Weekdays only. **Federal and market holiday calendars are published in no catalog dataset**,
    which is why :data:`HOLIDAY_SLACK_BUSINESS_DAYS` exists: a filing that misses a business-day
    deadline by a day or two may have been on time, and that is reported ``unknown`` rather than
    as a violation.
    """
    start, end = _utc(start), _utc(end)
    if start is None or end is None:
        return None
    sign, first, last = (1, start.date(), end.date()) if end >= start else (-1, end.date(), start.date())
    count, day = 0, first
    while day < last:
        day += _dt.timedelta(days=1)
        if day.weekday() < 5:
            count += 1
    return sign * count


def add_business_days(start, count):
    start = _utc(start)
    if start is None or count is None:
        return None
    day, added = start.date(), 0
    while added < int(count):
        day += _dt.timedelta(days=1)
        if day.weekday() < 5:
            added += 1
    return _dt.datetime(day.year, day.month, day.day, tzinfo=UTC)


# --------------------------------------------------------------------------- obligation


#: The kinds of compelled disclosure this layer knows about. Everything else is voluntary, and
#: voluntary silence is not a violation.
OBLIGATION_KINDS = ('periodic', 'material_event', 'ownership_threshold', 'insider_transaction')

#: How many business days a deadline may be missed by before the miss is called ``unmet`` rather
#: than ``unknown``. **Authored**, and it exists for one honest reason: the catalog publishes no
#: holiday calendar, so :func:`business_days_between` counts weekdays and can over-count by the
#: number of holidays in the window.
HOLIDAY_SLACK_BUSINESS_DAYS = 3

#: Who owes the obligation. A Form 4 on an issuer's filing record is the *insider's* obligation and
#: a Schedule 13D is the *acquirer's*; the SEC filing index files both under the issuer, so this
#: distinction has to be carried explicitly or the issuer gets blamed for someone else's lateness.
OBLIGATION_OWED_BY = ('organization', 'insider', 'holder')


@dataclass(frozen=True)
class Obligation:
    """A declared duty to disclose: what triggers it, to whom, in what form, by when.

    ``declared=False`` means the duty is named but its terms are not known. Everything about such
    an obligation is ``Unknown`` and :func:`compliance` says so; it never reads as compliance.
    ``deadline_days`` is calendar days from the trigger, ``deadline_business_days`` business days;
    when **both are ``None`` the deadline is not declared** and compliance is ``unknown``.
    """

    id: str
    kind: str
    trigger: str = ''
    audience: str = 'regulator'
    form: str = 'filing'
    deadline_days: float = None
    deadline_business_days: float = None
    owed_by: str = 'organization'
    declared: bool = True
    citation: str = ''
    filed_form: str = ''
    sources: tuple = ()

    def __post_init__(self):
        if self.kind not in OBLIGATION_KINDS:
            raise ValueError('unknown obligation kind %r (expected one of %s)'
                             % (self.kind, ', '.join(OBLIGATION_KINDS)))
        if self.owed_by not in OBLIGATION_OWED_BY:
            raise ValueError('unknown obligation holder %r' % (self.owed_by,))
        if self.audience not in AUDIENCES:
            raise ValueError('unknown audience %r' % (self.audience,))

    @property
    def deadline_declared(self):
        return self.declared and (self.deadline_days is not None or self.deadline_business_days is not None)

    def due(self, trigger_at):
        """When the disclosure is due, or ``None`` when the record does not support a deadline."""
        trigger_at = _utc(trigger_at)
        if trigger_at is None or not self.deadline_declared:
            return None
        if self.deadline_days is not None:
            return trigger_at + _dt.timedelta(days=float(self.deadline_days))
        return add_business_days(trigger_at, self.deadline_business_days)

    def cite(self):
        return self.citation or 'no citation declared'


#: Compliance states. ``no_obligation`` and ``unknown`` are deliberately different answers:
#: nothing was owed, versus we cannot tell. Neither is ``met``.
COMPLIANCE_STATES = ('met', 'unmet', 'pending', 'unknown', 'no_obligation')


@dataclass(frozen=True)
class ComplianceFinding:
    """Whether a declared obligation was discharged, and on what basis.

    :attr:`compliant` is the guarded form: ``True``, ``False``, or a ``tc.Unknown``. There is no
    path by which an Unknown obligation returns ``True``.
    """

    state: str
    basis: str = ''
    obligation_id: str = None
    due: object = None
    disclosed_at: object = None
    late_days: float = None
    sources: tuple = ()

    def __post_init__(self):
        if self.state not in COMPLIANCE_STATES:
            raise ValueError('unknown compliance state %r' % (self.state,))

    @property
    def known(self):
        return self.state in ('met', 'unmet')

    @property
    def compliant(self):
        if self.state == 'met':
            return True
        if self.state == 'unmet':
            return False
        return tc.Unknown('obligation_%s' % self.state, self.basis)

    def line(self):
        return '%-13s %-26s %s' % (self.state, self.obligation_id or '-', self.basis)


def compliance(obligation, trigger_at, disclosure=None, *, now=None,
               slack=HOLIDAY_SLACK_BUSINESS_DAYS):
    """Test one obligation against one disclosure. The order of the checks is the answer's reason.

    1. no obligation at all -> ``no_obligation``. Silence with nothing owed is not a violation.
    2. an obligation whose terms or deadline are not declared -> ``unknown``. Never compliance.
    3. a disclosure that does not reach the obliged audience, or is not in a form that can
       discharge an obligation, does not count as an answer to it - a press release does not
       satisfy a filing requirement.
    4. disclosed on or before the deadline -> ``met``; after it -> ``unmet``, unless the miss is
       inside the holiday slack, in which case ``unknown`` with the reason.
    5. nothing disclosed: ``pending`` before the deadline, ``unmet`` after it, and ``unknown``
       when no as-of date was given to compare against.
    """
    if obligation is None:
        return ComplianceFinding('no_obligation',
                                 'no obligation is declared for this claim, so silence is not a violation')
    if not obligation.declared:
        return ComplianceFinding('unknown', 'obligation %s is named but its terms are not declared'
                                 % obligation.id, obligation.id, sources=obligation.sources)
    due = obligation.due(trigger_at)
    if due is None:
        reason = ('no deadline is declared for obligation %s' % obligation.id
                  if _utc(trigger_at) is not None else
                  'the record does not publish when obligation %s was triggered' % obligation.id)
        return ComplianceFinding('unknown', reason, obligation.id, sources=obligation.sources)
    if disclosure is not None:
        form = form_of(disclosure.form)
        if not form.discharges_obligation:
            disclosure, note = None, ('a %s cannot discharge obligation %s; ' % (form.label, obligation.id))
        elif not disclosure.reaches(obligation.audience):
            disclosure, note = None, ('what was said does not reach the %s; ' % obligation.audience)
        else:
            note = ''
    else:
        note = ''
    if disclosure is None:
        now = _utc(now)
        if now is None:
            return ComplianceFinding('unknown', note + 'nothing disclosed and no as-of date to compare with '
                                     'the deadline', obligation.id, due, sources=obligation.sources)
        if now <= due:
            return ComplianceFinding('pending', note + 'due %s under %s; not yet due as of %s'
                                     % (str(due)[:10], obligation.cite(), str(now)[:10]),
                                     obligation.id, due, sources=obligation.sources)
        return ComplianceFinding('unmet', note + 'due %s under %s; nothing disclosed as of %s'
                                 % (str(due)[:10], obligation.cite(), str(now)[:10]),
                                 obligation.id, due, None, _days(now, due), obligation.sources)
    said = _utc(disclosure.effective_date)
    if said is None:
        return ComplianceFinding('unknown', 'the disclosure carries no effective date',
                                 obligation.id, due, sources=obligation.sources)
    if said <= due:
        return ComplianceFinding('met', 'disclosed %s, due %s under %s'
                                 % (str(said)[:10], str(due)[:10], obligation.cite()),
                                 obligation.id, due, said, 0.0, obligation.sources + disclosure.sources)
    late_business = business_days_between(due, said)
    if obligation.deadline_business_days is not None and late_business is not None and late_business <= slack:
        return ComplianceFinding(
            'unknown', 'disclosed %s against a deadline of %s, %d business day(s) late by a weekday '
            'count; no federal holiday calendar is published in this catalog, so whether the '
            'deadline was missed is Unknown' % (str(said)[:10], str(due)[:10], late_business),
            obligation.id, due, said, _days(said, due), obligation.sources + disclosure.sources)
    return ComplianceFinding('unmet', 'disclosed %s, due %s under %s: %.0f day(s) late'
                             % (str(said)[:10], str(due)[:10], obligation.cite(), _days(said, due)),
                             obligation.id, due, said, _days(said, due),
                             obligation.sources + disclosure.sources)


# --------------------------------------------------------------------------- the act


@tc.action(effect='external', idempotent=True, reversible=False)
@dataclass(frozen=True)
class DisclosureAct(Act):
    """Saying something on the record, as an organizational act.

    ``effect='external'`` because the statement leaves the organization: a filing is not a write
    to the firm's own memory, it is an effect on the world's. ``reversible=False`` because a
    filing cannot be unsaid - only amended by a later one, which is what
    :attr:`Form.revisable` and :attr:`Disclosure.supersedes` are for.

    ``requires_jurisdiction`` defaults to ``False``: a firm speaking about *itself* needs no
    jurisdiction. :meth:`DisclosureDesk.disclose` sets it to ``True`` when the statement is about
    another entity, at which point the acting role's declared jurisdiction binds.
    """


# --------------------------------------------------------------------------- the statement


def statement_of(record):
    """``(subject, predicate, object)`` for a claim record or claim: what would be said.

    The object is flattened to its entity id when it is a ``Ref``, so a statement is a plain
    value that can live inside another claim and be compared across stores.
    """
    claim = getattr(record, 'claim', record)
    obj = claim.object
    return (claim.subject.id, claim.predicate, getattr(obj, 'id', obj))


@dataclass(frozen=True)
class Disclosure:
    """One thing an organization said: to whom, in what form, when, by whose authority.

    ``known_at`` is when the organization held the underlying claim and ``effective_date`` is when
    it said it. The difference is :attr:`delay_days`, and on real filings both dates come out of
    the same published record.
    """

    id: str
    org: str
    statement: tuple
    form: str
    audience: frozenset
    role_id: str
    effective_date: object
    known_at: object = None
    recipients: tuple = ()
    claim_id: str = None
    obligation_id: str = None
    accession: str = None
    filed_form: str = None
    revisable: bool = None
    supersedes: str = None
    verdict_status: str = 'holds'
    verdict_reasons: tuple = ()
    holder: str = ''
    sources: tuple = ()

    @property
    def status(self):
        return 'disclosed'

    @property
    def form_spec(self):
        return form_of(self.form)

    @property
    def on_the_record(self):
        return self.form_spec.on_the_record

    @property
    def delay_days(self):
        """Calendar days between holding the claim and saying it. ``None`` when ``known_at`` is absent."""
        return _days(self.effective_date, self.known_at)

    def reaches(self, audience):
        """Does what was said reach ``audience``? A public filing does; a private briefing does not."""
        return any(audience in AUDIENCE_REACH.get(declared, ()) for declared in self.audience)

    def reaches_receiver(self, receiver):
        """Does it reach a *named* agent? Public speech reaches anyone; private speech its recipients."""
        if self.reaches('public'):
            return True
        return str(receiver) in tuple(self.recipients)

    def current(self, at=None):
        at = _utc(at)
        effective = _utc(self.effective_date)
        return at is None or effective is None or effective <= at

    def cite(self):
        if self.accession:
            return '%s %s' % (self.filed_form or self.form, self.accession)
        return '%s %s' % (self.form, self.id)

    def line(self):
        delay = self.delay_days
        return ('%s  %-14s -> %-26s by %-16s %s%s'
                % (str(self.effective_date)[:10], self.filed_form or self.form,
                   ','.join(sorted(self.audience)), self.role_id, self.cite(),
                   '' if delay is None else '  (held %.0f day(s) earlier)' % delay))


@dataclass(frozen=True)
class DisclosureRefusal:
    """A disclosure that was not authorized. On the record, and never in the register.

    ``status`` is ``fails`` or ``unknown``, straight off the ``tc.Verdict``: a role whose
    authority the charter does not declare cannot be said to have been refused permission.
    """

    org: str
    statement: tuple
    form: str
    role_id: str
    at: object
    status: str = 'fails'
    reasons: tuple = ()
    claim_id: str = None
    holder: str = ''

    @property
    def disclosed(self):
        return False

    def line(self):
        return ('%s  refused: %s by %s -> %s (%s)'
                % (str(self.at)[:10], self.form, self.role_id, self.status, '; '.join(self.reasons)))


# --------------------------------------------------------------------------- the register


class DisclosureRegister:
    """Append-only record of everything an organization has said.

    Append-only is not an implementation convenience: an organization cannot unsay a disclosure.
    A correction is a *new* disclosure whose ``supersedes`` names the old one, and both stay
    queryable, which is how "what did they say and when did they change it" has an answer.
    """

    def __init__(self):
        self._items = []
        self._by_claim = defaultdict(list)
        self._by_id = {}
        self._refusals = []

    def record(self, disclosure):
        if disclosure.id in self._by_id:
            raise ValueError('disclosure %r is already on the register' % disclosure.id)
        self._items.append(disclosure)
        self._by_id[disclosure.id] = disclosure
        if disclosure.claim_id:
            self._by_claim[disclosure.claim_id].append(disclosure)
        return disclosure

    def refuse(self, refusal):
        self._refusals.append(refusal)
        return refusal

    @property
    def items(self):
        return tuple(self._items)

    @property
    def refusals(self):
        return tuple(self._refusals)

    def get(self, disclosure_id):
        return self._by_id.get(disclosure_id)

    def about(self, claim_id, *, audience=None, at=None):
        """Everything said about one claim, oldest first, optionally filtered by reach and date."""
        out = [d for d in self._by_claim.get(claim_id, ())
               if (audience is None or d.reaches(audience)) and d.current(at)]
        return tuple(sorted(out, key=lambda d: (str(d.effective_date), d.id)))

    def first_about(self, claim_id, *, audience=None, at=None):
        said = self.about(claim_id, audience=audience, at=at)
        return said[0] if said else None

    def latest_about(self, claim_id, *, audience=None, at=None):
        said = self.about(claim_id, audience=audience, at=at)
        return said[-1] if said else None

    def reaching(self, audience, *, at=None):
        return tuple(d for d in self._items if d.reaches(audience) and d.current(at))

    def superseded(self):
        """``(old_id, new_id)`` for every correction. An amended filing does not erase the first."""
        return tuple((d.supersedes, d.id) for d in self._items if d.supersedes)

    def __len__(self):
        return len(self._items)

    def lines(self):
        return [d.line() for d in self._items] + [r.line() for r in self._refusals]


# --------------------------------------------------------------------------- the gap


#: How a held claim stands with respect to an audience.
GAP_STATES = ('undisclosed', 'disclosed', 'selectively_disclosed')


@dataclass(frozen=True)
class Gap:
    """One claim, and the distance between holding it and saying it.

    ``state`` is ``undisclosed`` (held, said to nobody), ``disclosed`` (said to this audience) or
    ``selectively_disclosed`` (said to *someone*, but not to this audience) - which is the
    interesting one, and the reason scope had to be modelled rather than assumed.
    """

    claim_id: str
    statement: tuple
    held_since: object = None
    state: str = 'undisclosed'
    disclosed_at: object = None
    audience: str = 'public'
    told_audiences: frozenset = frozenset()
    disclosure_id: str = None
    accession: str = None
    obligation_id: str = None
    compliance: ComplianceFinding = None

    def __post_init__(self):
        if self.state not in GAP_STATES:
            raise ValueError('unknown gap state %r' % (self.state,))

    @property
    def delay_days(self):
        """How long the claim was held before it was said to this audience. Delay is a choice."""
        return _days(self.disclosed_at, self.held_since)

    @property
    def open(self):
        return self.state != 'disclosed'

    def line(self):
        head = '%-12s %s %s' % (self.state, self.statement[1], _short(self.statement[2]))
        if self.state == 'disclosed':
            return '%s  held %s, said %s (%s day(s) later%s)' % (
                head, str(self.held_since)[:10], str(self.disclosed_at)[:10],
                'unknown' if self.delay_days is None else '%.0f' % self.delay_days,
                '' if not self.accession else ', ' + self.accession)
        if self.state == 'selectively_disclosed':
            return '%s  held %s, said only to %s' % (head, str(self.held_since)[:10],
                                                     ','.join(sorted(self.told_audiences)) or 'nobody')
        return '%s  held %s, not said to the %s%s' % (
            head, str(self.held_since)[:10], self.audience,
            '' if self.compliance is None else ' [%s]' % self.compliance.state)


def _short(value):
    text = str(value)
    return text if len(text) <= 40 else text[:37] + '...'


# --------------------------------------------------------------------------- selection


#: Why a claim was put in one of the three selection buckets. Reasons, not scores: the point is
#: that a reader can see what compelled the choice, and what did not.
SELECTION_REASONS = ('already_disclosed', 'compelled_now', 'compelled_overdue', 'not_yet_due',
                     'no_obligation_declared', 'obligation_unknown')


@dataclass(frozen=True)
class Selection:
    """What a role selected to disclose now, what it deferred, and what it holds back.

    Every item carries the reason it landed where it did, and the *only* reasons available are
    about obligation. ``withhold`` is not a lie: it is a claim nothing compels the organization to
    say, and the reason says which of the two kinds of silence it is.
    """

    at: object = None
    disclose: tuple = ()      # ((claim_id, obligation_id, reason), ...)
    defer: tuple = ()
    withhold: tuple = ()

    def lines(self):
        out = ['selection as of %s' % str(self.at)[:10]]
        for name in ('disclose', 'defer', 'withhold'):
            for claim_id, obligation_id, reason in getattr(self, name):
                out.append('  %-9s %-18s %-22s %s' % (name, reason, obligation_id or '-', claim_id))
        return out


def select(gaps, *, at=None, obligations=None):
    """Sort undisclosed claims into disclose / defer / withhold, by declared obligation alone.

    This is the whole of the selection model, and it is deliberately thin: nothing here weighs
    advantage, reputation or timing strategy, because none of that is published. What it does
    express is that **an obligation is the only thing that moves a claim out of silence**, and
    that an obligation which is Unknown moves it nowhere - it does not become a duty and it does
    not become permission to stay quiet, it stays Unknown and is reported.
    """
    at = _utc(at)
    obligations = {o.id: o for o in (obligations or ())}
    disclose, defer, withhold = [], [], []
    for gap in gaps:
        if gap.state == 'disclosed':
            disclose.append((gap.claim_id, gap.obligation_id, 'already_disclosed'))
            continue
        obligation = obligations.get(gap.obligation_id) if gap.obligation_id else None
        finding = gap.compliance or compliance(obligation, gap.held_since, None, now=at)
        if finding.state == 'no_obligation':
            withhold.append((gap.claim_id, None, 'no_obligation_declared'))
        elif finding.state == 'unknown':
            withhold.append((gap.claim_id, gap.obligation_id, 'obligation_unknown'))
        elif finding.state == 'pending':
            defer.append((gap.claim_id, gap.obligation_id, 'not_yet_due'))
        elif finding.state == 'unmet':
            disclose.append((gap.claim_id, gap.obligation_id, 'compelled_overdue'))
        else:
            disclose.append((gap.claim_id, gap.obligation_id, 'compelled_now'))

    def key(row):
        return (row[2], str(row[1]), row[0])       # deterministic ordering, no ties broken by luck

    return Selection(at, tuple(sorted(disclose, key=key)), tuple(sorted(defer, key=key)),
                     tuple(sorted(withhold, key=key)))


# --------------------------------------------------------------------------- reception


#: How a disclosure reached whoever holds it now. The channel is kept because it is the difference
#: between a counterparty who was told and a member of the public who looked it up.
RECEPTION_CHANNELS = ('filing', 'press', 'briefing', 'testimony', 'service')

#: The evidence ``method`` prefix that marks a claim as *told*, not observed.
DISCLOSED_METHOD = 'disclosed_by'


@dataclass(frozen=True)
class Reception:
    """A disclosure that reached an agent and became a claim in its store.

    ``lag_days`` is how long after the statement the receiver got it. A counterparty briefed on
    the day and a member of the public reading the filing a month later produce **the same claim
    id** with different ``heard_at`` and different evidence; that is the asymmetry.
    """

    disclosure_id: str
    speaker: str
    receiver: str
    statement: tuple
    claim_id: str
    heard_at: object
    channel: str
    audience: str = ''
    accession: str = None
    lag_days: float = None
    method: str = ''

    def line(self):
        return ('%s  %s heard from %s via %s: %s %s %s'
                % (str(self.heard_at)[:10], self.receiver, self.speaker, self.channel,
                   self.statement[0], self.statement[1], _short(self.statement[2])))


def receive(store, disclosure, *, receiver, at=None, channel=None, scope=None, confidence=None,
            valid=None):
    """Turn a disclosure that reaches ``receiver`` into a claim in ``receiver``'s store.

    The evidence records *that it was disclosed*: the source ref is the disclosure, the locator is
    the accession number when there is one, and the method is
    ``disclosed_by:<org>/<channel>`` - never ``published:<dataset>``, which is what an
    independent observation of the same fact would carry. :func:`provenance_of` reads that
    distinction back out, so "did this agent see this or was it told" is a query.

    Returns a :class:`Reception`, or ``tc.Unknown`` when the disclosure does not reach the
    receiver at all: overhearing a private briefing is not modelled, and a receiver outside the
    audience holds nothing rather than holding it weakly.
    """
    if not disclosure.reaches_receiver(receiver):
        return tc.Unknown('not_in_the_audience',
                          '%s said this to %s; %s is not in that audience'
                          % (disclosure.org, ','.join(sorted(disclosure.audience)), receiver))
    channel = channel or {'filing': 'filing', 'private_briefing': 'briefing',
                          'testimony': 'testimony', 'instrument': 'service',
                          'internal_memo': 'briefing'}.get(disclosure.form, 'press')
    if channel not in RECEPTION_CHANNELS:
        raise ValueError('unknown reception channel %r' % (channel,))
    heard_at = _utc(at) or _utc(disclosure.effective_date) or _dt.datetime.now(UTC)
    subject, predicate, obj = disclosure.statement
    claim = tc.Claim(tc.Ref(subject), predicate, obj, valid=valid or tc.Interval(), scope=scope)
    method = '%s:%s/%s' % (DISCLOSED_METHOD, disclosure.org, channel)
    evidence = tc.Evidence(tc.Ref('disclosure:%s:%s' % (disclosure.org, disclosure.id)), heard_at,
                           locator=disclosure.accession or disclosure.id, method=method,
                           confidence=confidence)
    store.apply(tc.Patch((tc.Tell(claim, (evidence,)),), store.revision))
    return Reception(disclosure.id, disclosure.org, str(receiver), disclosure.statement, claim.id,
                     heard_at, channel, ','.join(sorted(disclosure.audience)), disclosure.accession,
                     _days(heard_at, disclosure.effective_date), method)


def provenance_of(store, claim_id):
    """``('disclosed', org, channel)`` or ``('observed', method)`` for every piece of evidence.

    The corporate parallel to a person's "X told me" versus "I saw it". A claim can carry both,
    and then it is held on two grounds and the tuple list shows it.
    """
    record = store._claims.get(claim_id)
    if record is None:
        return ()
    out = []
    for evidence in record.evidence:
        method = evidence.method or ''
        if method.startswith(DISCLOSED_METHOD + ':'):
            rest = method.split(':', 1)[1]
            org, _, channel = rest.partition('/')
            out.append(('disclosed', org, channel or 'unknown'))
        else:
            out.append(('observed', method or 'unknown'))
    return tuple(out)


def disclosed_not_observed(store, claim_id):
    """True when every ground this agent has for the claim is that somebody disclosed it."""
    provenance = provenance_of(store, claim_id)
    return bool(provenance) and all(row[0] == 'disclosed' for row in provenance)


# --------------------------------------------------------------------------- the desk


class DisclosureDesk:
    """An organization's disclosure function: what it holds, what it says, and the gap.

    Wraps a :class:`~worldmodel.agents.firm.Firm` (anything with ``entity_id``, ``ref``,
    ``scope``, ``mind``, ``charter``, ``holders`` and ``log`` will do) and adds three scopes:

    ``firm:<org>``          the claims it **holds** - already the firm's institutional memory
    ``disclosed:<org>``     one ``disclosed`` claim per statement, derived from the claim it says
    ``undisclosed:<org>``   the *refusals*: statements attempted without authority

    Nothing here mutates the firm's charter or its roles. The only writes are claims, and every
    one of them cites either a published record or the disclosure act that produced it.
    """

    #: Predicates the desk treats as candidates for disclosure. ``None`` means every claim the
    #: organization holds about itself. Declared so that a caller can narrow it rather than having
    #: the module guess which of a firm's beliefs are the kind of thing one discloses.
    default_predicates = None

    def __init__(self, firm, *, obligations=(), register=None, predicates=None,
                 slack=HOLIDAY_SLACK_BUSINESS_DAYS, seat_policy=None, executor=None):
        self.firm = firm
        self.entity_id = firm.entity_id
        self.register = register if register is not None else DisclosureRegister()
        self.obligations = {o.id: o for o in obligations}
        self.predicates = predicates if predicates is not None else self.default_predicates
        self.slack = int(slack)
        self.seat_policy = seat_policy or getattr(firm, 'seat_policy', 'observed_or_unknown')
        self._executor = executor
        self._counter = 0
        self._held_at = {}          # claim_id -> the date the organization held it
        self._obligation_of = {}    # claim_id -> obligation id
        self._notes = []

    # -- scopes -------------------------------------------------------------------------
    @property
    def ref(self):
        return self.firm.ref

    @property
    def held_scope(self):
        """Where the organization's own claims live. The firm scope; never decayed."""
        return self.firm.scope

    @property
    def disclosed_scope(self):
        """One claim per statement made. Separate from the held claim, on purpose."""
        return tc.Ref('disclosed:%s' % self.entity_id)

    @property
    def refusal_scope(self):
        """Statements attempted without authority. A refusal is a record, not an absence."""
        return tc.Ref('disclosure_refusals:%s' % self.entity_id)

    # -- holding ------------------------------------------------------------------------
    def hold(self, predicate, obj, *, known_at, source=None, basis='closed internally',
             valid=None, obligation_id=None, subject=None):
        """Record something the organization knows and has not said. Returns the claim id.

        ``known_at`` is when it knew, which is **not** when it filed. On real filings this is the
        published period end or report date, so the date the organization held the fact and the
        date it said it both come out of the record rather than out of an assumption.
        """
        known_at = _utc(known_at)
        subject_ref = tc.Ref(subject) if subject else self.ref
        evidence = (source.evidence(known_at, method='held') if source is not None
                    else tc.Evidence(tc.Ref('internal:%s' % self.entity_id), known_at,
                                     method='held:%s' % basis))
        claim = tc.Claim(subject_ref, predicate, obj, valid=valid or tc.Interval(), scope=self.held_scope)
        self.firm.mind.apply(tc.Patch((tc.Tell(claim, (evidence,)),), self.firm.mind.revision))
        self._held_at.setdefault(claim.id, known_at)
        if obligation_id:
            self._obligation_of[claim.id] = obligation_id
        return claim.id

    def note_known_at(self, claim_id, known_at, *, obligation_id=None):
        """Declare when the organization held a claim it already has (one seeded by a filing).

        Without this the only date on a filed claim is the *filing* date, which would make the
        belief/disclosure gap vanish by construction. The date itself is published - it is the
        period end, or the report date on the filing - so this records a fact, not a guess.
        """
        self._held_at[claim_id] = _utc(known_at)
        if obligation_id:
            self._obligation_of[claim_id] = obligation_id
        return claim_id

    def held(self, *, predicates=None, at=None):
        """Every live claim the organization holds about itself, deterministically ordered."""
        predicates = predicates if predicates is not None else self.predicates
        out = []
        for record in self.firm.mind.claims(self.ref, scope=self.held_scope):
            if predicates is not None and record.claim.predicate not in predicates:
                continue
            if at is not None and self.known_at(record.id) is not None \
                    and self.known_at(record.id) > _utc(at):
                continue
            out.append(record)
        return tuple(sorted(out, key=lambda r: (r.claim.predicate, r.id)))

    def known_at(self, claim_id):
        """When the organization held this claim, from :meth:`hold`/:meth:`note_known_at`, else
        the earliest evidence date on it."""
        if claim_id in self._held_at:
            return self._held_at[claim_id]
        record = self.firm.mind._claims.get(claim_id)
        if record is None or not record.evidence:
            return None
        return min(_utc(e.observed_at) for e in record.evidence if e.observed_at is not None) \
            if any(e.observed_at is not None for e in record.evidence) else None

    # -- saying -------------------------------------------------------------------------
    def disclose(self, claim_id=None, *, form, audience, role_id, effective_date, statement=None,
                 recipients=(), obligation_id=None, accession=None, filed_form=None, sources=(),
                 supersedes=None, about=None, executor=None, id=None, memo=''):
        """Say something. Authorized first, invoked second, registered third.

        The order matters and is the same discipline :meth:`Institution.act` uses: a statement
        whose authorization does not hold **never reaches the executor and never reaches the
        register**. It is returned as a :class:`DisclosureRefusal`, appended to the firm's
        ``ActLog``, and written into the store as an ``ultra_vires`` claim, so an organization
        that tried to say something it had no authority to say is on the record for having tried.
        """
        spec = form_of(form)
        audiences = frozenset((audience,) if isinstance(audience, str) else audience)
        unknown_audience = sorted(a for a in audiences if a not in AUDIENCES)
        if unknown_audience:
            raise ValueError('unknown audience(s) %s' % ', '.join(unknown_audience))
        if claim_id is None and statement is None:
            raise ValueError('a disclosure needs either a claim to say or an explicit statement')
        if statement is None:
            record = self.firm.mind._claims.get(claim_id)
            if record is None:
                raise ValueError('no claim %r in the organization store' % claim_id)
            statement = statement_of(record)
        at = _utc(effective_date)
        role = self.firm.charter.role(role_id)
        holder = seated(self.firm.holders, role_id, at)
        subject = about or self.entity_id
        act = DisclosureAct(power='approve_disclosure', subject=subject,
                            memo=memo or '%s to %s: %s' % (form, ','.join(sorted(audiences)), statement[1]),
                            payload=statement, requires_jurisdiction=bool(about and about != self.entity_id))
        verdict = authorize(act, role=role, charter=self.firm.charter, holder=holder, at=at,
                            log=self.firm.log, org=self.entity_id, seat_policy=self.seat_policy)
        if verdict.status != 'holds':
            return self._refuse(statement, form, role_id, at, verdict, holder)
        executor = executor if executor is not None else self._executor
        if executor is not None:
            receipt = tc.invoke(act, executor=executor, key=self._key(statement, at, form))
            if receipt.status != 'applied':
                return self._refuse(statement, form, role_id, at,
                                    tc.Verdict('fails', ('executor: %s' % (receipt.error or receipt.status),)),
                                    holder)
        self._counter += 1
        disclosure = Disclosure(
            id=id or '%s:d%03d' % (self.entity_id, self._counter), org=self.entity_id,
            statement=tuple(statement), form=form, audience=audiences, role_id=role_id,
            effective_date=at, known_at=self.known_at(claim_id) if claim_id else None,
            recipients=tuple(recipients), claim_id=claim_id,
            obligation_id=obligation_id or self._obligation_of.get(claim_id),
            accession=accession, filed_form=filed_form,
            revisable=spec.revisable, supersedes=supersedes, verdict_status=verdict.status,
            verdict_reasons=tuple(verdict.reasons),
            holder=holder.person if holder is not None else '', sources=tuple(sources))
        self.register.record(disclosure)
        self._record_said(disclosure)
        return disclosure

    def _refuse(self, statement, form, role_id, at, verdict, holder):
        refusal = DisclosureRefusal(self.entity_id, tuple(statement), form, role_id, at,
                                    'unknown' if verdict.status == 'unknown' else 'fails',
                                    tuple(verdict.reasons),
                                    holder=holder.person if holder is not None else '')
        claim = tc.Claim(self.firm.charter.role(role_id).scope, 'ultra_vires',
                         ('approve_disclosure', statement[1], refusal.status,
                          '; '.join(refusal.reasons) or 'authorization did not hold'),
                         scope=self.refusal_scope)
        self.firm.mind.apply(tc.Patch((tc.Tell(claim, (tc.Evidence(
            tc.Ref('authorize:%s' % self.entity_id), at or _dt.datetime.now(UTC),
            method='authorize@1'),)),), self.firm.mind.revision))
        self.register.refuse(replace(refusal, claim_id=claim.id))
        return self.register.refusals[-1]

    def _record_said(self, disclosure):
        """A ``disclosed`` claim, derived from the claim it says, in its own scope.

        Derived from rather than merely referring to it: retract the underlying claim and the
        record that it was disclosed is withdrawn with it, which is the right behaviour - the
        organization no longer holds the thing it said.
        """
        premises = (disclosure.claim_id,) if disclosure.claim_id else ()
        claim = tc.Claim(self.ref, 'disclosed',
                         (disclosure.statement[1], disclosure.form,
                          tuple(sorted(disclosure.audience)), str(disclosure.effective_date)[:10],
                          disclosure.accession or disclosure.id), scope=self.disclosed_scope)
        evidence = tc.Evidence(tc.Ref('disclosure:%s:%s' % (self.entity_id, disclosure.id)),
                               _utc(disclosure.effective_date) or _dt.datetime.now(UTC),
                               locator=disclosure.accession or disclosure.id,
                               method='disclose:%s' % disclosure.form, derived_from=premises)
        self.firm.mind.apply(tc.Patch((tc.Tell(claim, (evidence,)),), self.firm.mind.revision))
        return claim.id

    def _key(self, statement, at, form):
        return '%s:%s:%s:%s' % (self.entity_id, form, statement[1], str(at)[:10])

    def said(self, *, audience=None, at=None):
        """Every statement on the register, optionally only those reaching ``audience`` by ``at``."""
        if audience is None:
            return tuple(d for d in self.register.items if d.current(at))
        return self.register.reaching(audience, at=at)

    # -- the gap ------------------------------------------------------------------------
    def gaps(self, *, audience='public', at=None, predicates=None, now=None):
        """Every held claim, with whether and when it was said to ``audience``. The core query."""
        now = _utc(now) or _utc(at)
        out = []
        for record in self.held(predicates=predicates, at=at):
            claim_id = record.id
            held = self.known_at(claim_id)
            statement = statement_of(record)
            to_audience = self.register.first_about(claim_id, audience=audience, at=at)
            to_anyone = self.register.about(claim_id, at=at)
            obligation_id = self._obligation_of.get(claim_id)
            if to_anyone:
                obligation_id = obligation_id or to_anyone[0].obligation_id
            obligation = self.obligations.get(obligation_id) if obligation_id else None
            finding = compliance(obligation, held, to_audience, now=now, slack=self.slack)
            if to_audience is not None:
                state, disclosed_at = 'disclosed', to_audience.effective_date
            elif to_anyone:
                state, disclosed_at = 'selectively_disclosed', None
            else:
                state, disclosed_at = 'undisclosed', None
            out.append(Gap(claim_id, statement, held, state, disclosed_at, audience,
                           frozenset(a for d in to_anyone for a in d.audience),
                           to_audience.id if to_audience else None,
                           to_audience.accession if to_audience else None,
                           obligation_id, finding))
        return tuple(sorted(out, key=lambda g: (g.state, g.statement[1], g.claim_id)))

    def undisclosed(self, *, audience='public', at=None, predicates=None):
        """The claims the organization holds and has not said to ``audience``. Inspectable, queryable."""
        return tuple(gap for gap in self.gaps(audience=audience, at=at, predicates=predicates)
                     if gap.open)

    def select(self, *, audience='public', at=None, predicates=None):
        """What obligation compels it to say now, what it may defer, and what nothing compels."""
        return select(self.gaps(audience=audience, at=at, predicates=predicates), at=at,
                      obligations=tuple(self.obligations.values()))

    def obligation_status(self, *, now=None):
        """Every declared obligation against what was actually said. ``Unknown`` stays ``Unknown``.

        One summary row per obligation, taking its **earliest** trigger and its **earliest**
        discharge, because a periodic duty recurs and a roll-up has to pick a representative pair.
        The per-claim findings, which are the ones to reason from, are on :meth:`gaps`.
        """
        out = []
        for obligation_id in sorted(self.obligations):
            obligation = self.obligations[obligation_id]
            claim_ids = [cid for cid, oid in self._obligation_of.items() if oid == obligation_id]
            disclosures = [d for d in self.register.items if d.obligation_id == obligation_id]
            trigger = min((self.known_at(cid) for cid in claim_ids
                           if self.known_at(cid) is not None), default=None)
            said = min(disclosures, key=lambda d: str(d.effective_date)) if disclosures else None
            out.append((obligation_id, compliance(obligation, trigger, said, now=now, slack=self.slack)))
        return tuple(out)

    # -- inspection ---------------------------------------------------------------------
    def refusals(self):
        """Every refused statement, read from the store rather than from memory."""
        return tuple(sorted(self.firm.mind.claims(scope=self.refusal_scope), key=lambda r: r.id))

    def note(self, text):
        self._notes.append(text)
        return text

    def unknown(self):
        """What was looked for and not found. Never filled in with a guess."""
        return tuple(self._notes)

    def lines(self, *, audience='public', at=None):
        out = ['%s disclosure desk  (audience: %s)' % (self.entity_id, audience),
               '  holds %d claim(s), said %d statement(s), refused %d'
               % (len(self.held()), len(self.register), len(self.register.refusals))]
        for gap in self.gaps(audience=audience, at=at):
            out.append('  ' + gap.line())
        for obligation_id, finding in self.obligation_status(now=at):
            out.append('  obligation %-24s %s' % (obligation_id, finding.line()))
        if self._notes:
            out.append('  unknown: ' + '; '.join(self._notes))
        return out


# --------------------------------------------------------------------------- grounding

#: The dataset that publishes the filing index: form, accession number, filing date, report date.
FILING_DATASET = 'sec_issuer_reference'

#: The published record-id template for one filing, and for the issuer's reference record. Both are
#: primary keys, which is the only way in: these are ``kind='event'`` records with no subject and no
#: entity id, so no index reaches them (the same shape as voteview's roll-call positions, see
#: :class:`~worldmodel.agents.grounding.RollCalls`).
FILING_ID = 'secsub:%s:filing:%s'
ISSUER_ID = 'secsub:%s:entity'


@dataclass(frozen=True)
class Filing:
    """One published filing: the form, the accession number, and the two dates that matter.

    ``report_date`` is the date the filing speaks *about* (a period end, a transaction date, an
    event date) and ``filing_date`` is when it was said. Both are published in the same record,
    which is what makes the gap grounded rather than assumed.
    """

    accession: str
    form: str
    filing_date: object
    report_date: object = None
    primary_document: str = ''
    file_number: str = ''
    items: tuple = ()
    source: object = None

    @property
    def lag_days(self):
        return _days(self.filing_date, self.report_date)

    def cite(self):
        return '%s %s filed %s' % (self.form, self.accession, str(self.filing_date)[:10])


def _connection_of(index):
    """The read-only sqlite connection behind an :class:`EvidenceIndex`.

    ``EvidenceIndex`` offers ``record_by_id`` for a single primary key but no range over one, and
    a filing history is exactly a range over the ``(dataset, stage, version, id)`` primary key.
    See the report for the hook this would rather be
    (``EvidenceIndex.records_by_id_prefix``).
    """
    connection = getattr(index, '_connection', None)
    if connection is None:
        raise ValueError('need an EvidenceIndex with an open connection to read filings')
    return connection


def filings_from_index(index, entity_id, *, forms=None, since=None, until=None, limit=1200,
                       notes=None, stage='normalized'):
    """Every filing this issuer published, newest first, addressed by record-id prefix.

    ``sec_issuer_reference`` publishes one ``sec_filing`` event per filing under the id
    ``secsub:<cik10>:filing:<accession>``, so one issuer's whole filing history is a single
    **bounded range scan of the records primary key** - never a table scan, and never more than
    ``limit`` rows. This is the second place in this layer that addresses records by key rather
    than by index, after :class:`~worldmodel.agents.grounding.RollCalls`, and for the same reason:
    the records carry no subject, so no index reaches them.
    """
    if not entity_id.startswith('sec:cik:'):
        if notes is not None:
            notes.append('filings: %s is not an SEC CIK, and %s keys on CIK only'
                         % (entity_id, FILING_DATASET))
        return ()
    cik = entity_id.split(':')[-1]
    version = index.pinned_version(FILING_DATASET, stage)
    if version is None:
        if notes is not None:
            notes.append('filings: %s is not one of this index\'s inputs' % FILING_DATASET)
        return ()
    prefix = FILING_ID % (cik, '')
    rows = _connection_of(index).execute(
        'SELECT rowid FROM records WHERE dataset=? AND stage=? AND version=? AND id>=? AND id<?'
        ' ORDER BY id LIMIT ?',
        (FILING_DATASET, stage, version, prefix, prefix[:-1] + chr(ord(prefix[-1]) + 1), int(limit))).fetchall()
    if notes is not None and len(rows) >= int(limit):
        notes.append('filings are bounded at the %d most recent published filings; there may be more' % limit)
    wanted = None if forms is None else {str(f).upper() for f in forms}
    since, until = _utc(since), _utc(until)
    out = []
    for row in rows:
        record = index.record_at(row[0])
        if record is None:
            continue
        attributes = record.get('attributes') or {}
        form = str(attributes.get('form') or '')
        if wanted is not None and form.upper() not in wanted:
            continue
        filed = _utc(attributes.get('filing_date'))
        if (since is not None and (filed is None or filed < since)) or \
                (until is not None and (filed is None or filed > until)):
            continue
        provenance = record.get('_provenance') or {}
        out.append(Filing(
            accession=str(attributes.get('accession') or ''), form=form, filing_date=filed,
            report_date=_utc(attributes.get('report_date')),
            primary_document=str(attributes.get('primary_document') or ''),
            file_number=str(attributes.get('file_number') or ''),
            items=tuple(attributes.get('items') or ()),
            source=Source(provenance.get('dataset', FILING_DATASET), provenance.get('version', ''),
                          provenance.get('record_id', record.get('id', '')),
                          provenance.get('stage', stage))))
    return tuple(sorted(out, key=lambda f: (str(f.filing_date), f.accession), reverse=True))


def filer_category_from_index(index, entity_id, *, stage='normalized'):
    """The issuer's published filer category, which is what sets its periodic deadlines.

    ``'Large accelerated filer'``, ``'Accelerated filer'``, ``'Non-accelerated filer'`` or
    ``None`` when the record does not say - in which case the deadline is **Unknown** and so is
    compliance. Read by primary key off the issuer's reference record, not guessed from size.
    """
    if not entity_id.startswith('sec:cik:'):
        return None
    record = index.record_by_id(FILING_DATASET, ISSUER_ID % entity_id.split(':')[-1], stage=stage)
    if record is None:
        return None
    return (record.get('attributes') or {}).get('filer_category') or None


#: Periodic-report deadlines in calendar days after the fiscal period end, by published filer
#: category. The *category* is published (``sec_issuer_reference``, ``filer_category``); the
#: **day counts are the regulation**, written down here rather than derived, with the citation.
#: A category the record does not publish yields no deadline, and therefore ``unknown`` compliance.
FILER_DEADLINES = {
    'Large accelerated filer': {'10-K': 60, '10-Q': 40, '20-F': 120},
    'Accelerated filer': {'10-K': 75, '10-Q': 40, '20-F': 120},
    'Non-accelerated filer': {'10-K': 90, '10-Q': 45, '20-F': 120},
}

#: Which published SEC form discharges which kind of obligation, on what clock, under what rule.
#: **Authored** - the catalog publishes filings, not the rules that compel them - and the citation
#: is carried so a reader can check the deadline rather than trust it. ``owed_by`` matters: a
#: Form 4 filed on an issuer's record is the *insider's* duty and a Schedule 13D the *acquirer's*.
SEC_FORM_OBLIGATIONS = {
    '10-K': ('periodic', 'fiscal year end', 'organization', '17 CFR 240.13a-1', None),
    '10-Q': ('periodic', 'fiscal quarter end', 'organization', '17 CFR 240.13a-13', None),
    '20-F': ('periodic', 'fiscal year end', 'organization', '17 CFR 240.13a-1', None),
    '8-K': ('material_event', 'the reportable event', 'organization', '17 CFR 240.13a-11', 4),
    '3': ('insider_transaction', 'becoming an insider', 'insider', 'Exchange Act s.16(a)', 10),
    '4': ('insider_transaction', 'the reportable transaction', 'insider', 'Exchange Act s.16(a)', 2),
    '5': ('insider_transaction', 'fiscal year end', 'insider', 'Exchange Act s.16(a)', None),
    'SC 13D': ('ownership_threshold', 'crossing five percent', 'holder', '17 CFR 240.13d-1(a)', 5),
    'SC 13D/A': ('ownership_threshold', 'a material change in the position', 'holder',
                 '17 CFR 240.13d-2(a)', 2),
}


def obligation_for_filing(filing, *, filer_category=None, org=None):
    """The obligation a published filing discharges, or ``None`` when none is declared for its form.

    A form this table does not name returns ``None``, and ``None`` means *no obligation declared* -
    which :func:`compliance` reports as ``no_obligation``, not as a violation. A periodic form
    whose filer category is not published returns an obligation with **no deadline**, which
    reports as ``unknown``; it is never silently given the middle category's days.
    """
    form = (filing.form or '').upper()
    spec = SEC_FORM_OBLIGATIONS.get(form) or SEC_FORM_OBLIGATIONS.get(form.rstrip('/A').strip())
    if spec is None:
        return None
    kind, trigger, owed_by, citation, business_days = spec
    calendar_days = None
    if kind == 'periodic':
        table = FILER_DEADLINES.get(filer_category or '') or {}
        calendar_days = table.get(form) or table.get(form.split('/')[0])
    declared = calendar_days is not None or business_days is not None
    return Obligation(
        id='%s:%s' % (kind, form.lower().replace(' ', '_')), kind=kind, trigger=trigger,
        audience='regulator', form='filing', deadline_days=calendar_days,
        deadline_business_days=business_days, owed_by=owed_by, declared=True,
        citation=citation if declared else
        '%s; no deadline derivable: filer category is not published for %s' % (citation, org or 'this issuer'),
        filed_form=form, sources=(filing.source,) if filing.source else ())


# --------------------------------------------------------------------------- worked example

#: LyondellBasell Industries N.V., the same issuer ``worldmodel.agents.firm`` is built against, and
#: for the same reason: this catalog publishes, for this one CIK, 1,000 filings with real accession
#: numbers, forms, filing dates and report dates *and* 45 quarters of financials that carry the
#: accession number inside their record ids, so a held claim and the statement that disclosed it
#: can be joined on published data alone.
WORKED_EXAMPLE = 'sec:cik:0001489393'

#: The role a periodic filing is attributed to here. **Authored**: the catalog publishes who signs
#: no filing in a structured field. ``general_counsel`` holds ``approve_disclosure`` under
#: :data:`~worldmodel.agents.firm.STANDARD_DELEGATION`, which is the authority being bound.
DISCLOSING_ROLE = 'general_counsel'


def worked_example(*, index, catalog=None, entity_id=WORKED_EXAMPLE, quarters=4, at=None,
                   role_id=DISCLOSING_ROLE):
    """Seed a real firm, hold its real quarter-end numbers, and disclose them on their real dates.

    Returns ``(desk, report)``. ``report`` is a dict of the things a reader wants to check: the
    filings used, the gap between holding and saying for each quarter, and the compliance finding
    for each obligation - including a genuinely late Form 4 on this issuer's record.

    Nothing is fitted and nothing is invented. The two dates on every row are the published
    ``report_date`` and ``filing_date`` of the same filing record, and the numbers are the
    ``sec_company_assets`` facts whose record ids end in that filing's accession number.
    """
    from .firm import STANDARD_DELEGATION, Firm, charter_from_records, firm_records

    notes = []
    records = firm_records(entity_id, index=index, catalog=catalog, max_quarters=quarters)
    firm = Firm(records, charter_from_records(records, authority=STANDARD_DELEGATION))
    for quarter in firm.quarters:
        firm.perceive(quarter)
    category = filer_category_from_index(index, entity_id)
    if category is None:
        notes.append('filer category: not published for %s, so periodic deadlines are Unknown' % entity_id)
    periodic = filings_from_index(index, entity_id, forms=('10-Q', '10-K'), notes=notes)
    insider = filings_from_index(index, entity_id, forms=('4',), notes=notes)
    by_period = {str(f.report_date)[:10]: f for f in periodic if f.report_date is not None}
    obligations = {}
    for filing in periodic + insider:
        obligation = obligation_for_filing(filing, filer_category=category, org=entity_id)
        if obligation is not None:
            obligations.setdefault(obligation.id, obligation)
    desk = DisclosureDesk(firm, obligations=tuple(obligations.values()),
                          predicates=('cash', 'revenue', 'costs', 'capex'))
    for note in notes:
        desk.note(note)
    desk.note('who signed a filing: no catalog dataset publishes the signatory, so the role a '
              'disclosure is attributed to is authored (%s)' % role_id)
    desk.note('Forms 3/4/5 and Schedules 13D/G are the insider\'s or the acquirer\'s obligation, '
              'not the issuer\'s; the SEC filing index files them under the issuer')
    rows = []
    for quarter in firm.quarters:
        period = str(quarter.period_end)[:10]
        filing = by_period.get(period)
        if filing is None:
            desk.note('no 10-Q or 10-K published with report_date %s; that quarter is not joined' % period)
            continue
        obligation = obligation_for_filing(filing, filer_category=category, org=entity_id)
        # What it knew: the quarter's cash, dated at the period end, cited to the concept record
        # whose published id ends in this filing's accession number.
        cash_claim = revenue_claim = None
        for name in ('cash', 'revenue'):
            value = getattr(quarter, name)
            if value is None:
                continue
            # The same claim the firm already holds from the filing: identical subject, predicate,
            # value and validity, so ``Store`` adds a second piece of evidence to *one* claim rather
            # than making a near-duplicate. The claim now carries both grounds - published at the
            # filing date, held at the period end - and the gap is the distance between them.
            claim_id = desk.hold(name, float(value), known_at=quarter.period_end,
                                 source=quarter.source_for(name),
                                 valid=tc.Interval(_utc(quarter.period_end), _utc(quarter.period_end)),
                                 basis='books closed for the period ending %s' % period,
                                 obligation_id=obligation.id if obligation else None)
            if name == 'cash':
                cash_claim = claim_id
            elif name == 'revenue':
                revenue_claim = claim_id
        if cash_claim is None:
            desk.note('the filing for %s publishes no cash balance; that quarter is held but not said'
                      % period)
            continue
        # One filing, several statements: a 10-Q says a number of things, and each of them is its
        # own disclosure of its own held claim under the same accession number.
        disclosure = None
        for claim_id in [cid for cid in (cash_claim, revenue_claim) if cid is not None]:
            said = desk.disclose(
                claim_id, form='filing', audience='public', role_id=role_id,
                effective_date=filing.filing_date, obligation_id=obligation.id if obligation else None,
                accession=filing.accession, filed_form=filing.form,
                sources=(filing.source,) if filing.source else ())
            disclosure = disclosure or said
        rows.append({'period_end': period, 'form': filing.form, 'accession': filing.accession,
                     'filing_date': str(filing.filing_date)[:10],
                     'held_days_before_disclosure': disclosure.delay_days,
                     'compliance': compliance(obligation, quarter.period_end, disclosure,
                                              now=filing.filing_date)})
    # The late Form 4: a transaction reported many months after it happened, against a declared
    # two-business-day deadline. This is an obligation on the record and unmet, not a modelling
    # choice: both dates come off the same published filing record.
    late = []
    for filing in insider:
        obligation = obligation_for_filing(filing, filer_category=category, org=entity_id)
        if obligation is None or filing.report_date is None:
            continue
        finding = compliance(obligation, filing.report_date,
                             Disclosure('%s:%s' % (entity_id, filing.accession), entity_id,
                                        (entity_id, 'insider_transaction', filing.accession),
                                        'filing', frozenset({'public'}), role_id,
                                        filing.filing_date, filing.report_date,
                                        accession=filing.accession, filed_form=filing.form,
                                        sources=(filing.source,) if filing.source else ()),
                             now=filing.filing_date)
        if finding.state == 'unmet':
            late.append({'accession': filing.accession, 'form': filing.form,
                         'transaction': str(filing.report_date)[:10],
                         'filed': str(filing.filing_date)[:10], 'finding': finding})
    at = _utc(at) or (_utc(firm.quarters[-1].period_end) + _dt.timedelta(days=7)
                      if firm.quarters else None)
    report = {'entity_id': entity_id, 'name': firm.name, 'filer_category': category,
              'periodic_filings': len(periodic), 'insider_filings': len(insider),
              'quarters': tuple(rows), 'late_insider_filings': tuple(late[:5]),
              'as_of': at, 'undisclosed_at': desk.undisclosed(at=at),
              'unknown': desk.unknown()}
    return desk, report


__all__ = ['AUDIENCES', 'AUDIENCE_REACH', 'COMPLIANCE_STATES', 'ComplianceFinding', 'DISCLOSED_METHOD',
           'DISCLOSING_ROLE', 'Disclosure', 'DisclosureAct', 'DisclosureDesk', 'DisclosureRefusal',
           'DisclosureRegister', 'FILER_DEADLINES', 'FILING_DATASET', 'FORMS', 'Filing', 'Form',
           'GAP_STATES', 'Gap', 'HOLIDAY_SLACK_BUSINESS_DAYS', 'OBLIGATION_KINDS',
           'OBLIGATION_OWED_BY', 'Obligation', 'RECEPTION_CHANNELS', 'Reception', 'SELECTION_REASONS',
           'SEC_FORM_OBLIGATIONS', 'Selection', 'WORKED_EXAMPLE', 'add_business_days',
           'business_days_between', 'compliance', 'disclosed_not_observed', 'filer_category_from_index',
           'filings_from_index', 'form_of', 'obligation_for_filing', 'provenance_of', 'receive',
           'select', 'statement_of', 'worked_example']
