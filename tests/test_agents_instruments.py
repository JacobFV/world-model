"""Institutional instruments: authorized issuance, refusal on the record, lapse, contest, service.

The fixture is a small regulator with three seats; the real-data smoke test uses a published
congressional committee whose jurisdiction is the set of measures actually referred to it.
Everything skips unless the optional ``agents`` extra (tensacode) is installed.
"""
import datetime as dt
import unittest
from pathlib import Path

from worldmodel.agents import tensacode_available

HAVE_TC = tensacode_available()
DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
INDEX = DATA_ROOT / 'world_evidence' / 'index.sqlite'
NEEDS_TC = 'institutional instruments require the optional agents extra (tensacode)'

if HAVE_TC:
    import tensacode as tc

    from worldmodel.agents.disclosure import provenance_of, receive
    from worldmodel.agents.institution import (Institution, InstitutionRecords, RecordingExecutor)
    from worldmodel.agents.instruments import (COMMITTEE_INSTRUMENT_POWERS, Docket, INSTRUMENT_KINDS,
                                               Issuance, WORKED_EXAMPLE, kind_of,
                                               with_instrument_powers)
    from worldmodel.agents.roles import Authority, Charter, Holder, Role, Source

ORG = 'usgov:agency:fixture_commission'
SUBJECT = 'sec:cik:0000000042'
OTHER_SUBJECT = 'sec:cik:0000000043'
OUTSIDE = 'sec:cik:0000000099'
AT = dt.date(2026, 3, 1)
FIXTURE = Source('fixture_register', 'v1', 'rec:statute:1') if HAVE_TC else None
SUBJECTS = frozenset({SUBJECT, OTHER_SUBJECT})

if HAVE_TC:
    ROLES = (Role('commissioner', ORG, 'Commissioner', 'chair',
                  Authority(powers=frozenset({'refer', 'issue_rule', 'subpoena', 'make_finding'}),
                            jurisdiction=SUBJECTS, sources=(FIXTURE,)), sources=(FIXTURE,)),
             Role('staff', ORG, 'Enforcement Staff', 'staff',
                  Authority(powers=frozenset({'make_finding'}), jurisdiction=SUBJECTS,
                            sources=(FIXTURE,)), sources=(FIXTURE,)),
             # authority declared, jurisdiction *not* declared: the Unknown case
             Role('ombudsman', ORG, 'Ombudsman', 'staff',
                  Authority(powers=frozenset({'refer'}), sources=(FIXTURE,)), sources=(FIXTURE,)),
             Role('clerk', ORG, 'Clerk', 'staff', sources=(FIXTURE,)))

    HOLDERS = (Holder('bioguide:X000001', 'commissioner', dt.date(2023, 1, 1), None, (), (FIXTURE,)),
               Holder('bioguide:X000002', 'staff', dt.date(2023, 1, 1), None, (), (FIXTURE,)),
               Holder('bioguide:X000004', 'ombudsman', dt.date(2023, 1, 1), None, (), (FIXTURE,)),
               Holder('bioguide:X000003', 'clerk', dt.date(2023, 1, 1), None, (), (FIXTURE,)))


def fixture_docket(**kwargs):
    records = InstitutionRecords(ORG, 'Fixture Commission', ROLES, HOLDERS, (),
                                 ('enabling statute text: not published in any catalog dataset',),
                                 (FIXTURE,))
    charter = Charter(ORG, {r.id: r for r in ROLES}).validate()
    institution = Institution(records, charter)
    return Docket(institution, executor=RecordingExecutor(), **kwargs)


# --------------------------------------------------------------------------- issuing


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class IssuanceTests(unittest.TestCase):
    def test_an_instrument_inside_the_jurisdiction_is_issued_with_an_audience_and_a_date(self):
        docket = fixture_docket()
        issuance = docket.issue('subpoena', SUBJECT, role_id='commissioner', audience='counterparty',
                                recipients=(SUBJECT,), effective_date=AT)
        self.assertIsInstance(issuance, Issuance)
        self.assertTrue(issuance.issued)
        self.assertEqual(sorted(issuance.audience), ['counterparty'])
        self.assertEqual(issuance.recipients, (SUBJECT,))
        self.assertEqual(str(issuance.effective_date)[:10], '2026-03-01')
        self.assertEqual(str(issuance.expires)[:10], '2026-05-30')   # the declared 90-day term
        self.assertTrue(issuance.in_force(AT))
        self.assertEqual(len(docket.executor.calls), 1)
        # the instrument is the existing institution.Instrument, registered with the institution
        self.assertIn(issuance.instrument, docket.institution.instruments)
        self.assertEqual(issuance.instrument.subjects, frozenset({SUBJECT}))

    def test_an_unauthorized_instrument_is_refused_and_never_reaches_the_executor(self):
        docket = fixture_docket()
        refused = docket.issue('subpoena', OUTSIDE, role_id='commissioner', effective_date=AT)
        self.assertFalse(refused.issued)
        self.assertEqual(refused.status, 'refused')
        self.assertIsNone(refused.instrument)
        self.assertFalse(refused.in_force(AT))
        self.assertTrue(any('outside the declared jurisdiction' in r for r in refused.reasons))
        self.assertEqual(docket.executor.calls, [])                  # nothing was performed
        self.assertEqual(docket.institution.instruments, [])         # and nothing was registered
        # and it is on the record three ways
        self.assertTrue(docket.institution.log.refusals)
        self.assertEqual([r.claim.predicate for r in docket.institution.refusals()], ['ultra_vires'])
        self.assertEqual(docket.refusals, (refused,))

    def test_a_role_without_the_power_is_refused_for_a_different_reason(self):
        docket = fixture_docket()
        refused = docket.issue('subpoena', SUBJECT, role_id='staff', effective_date=AT)
        self.assertFalse(refused.issued)
        self.assertTrue(any("holds no power 'subpoena'" in r for r in refused.reasons))
        self.assertFalse(any('jurisdiction' in r for r in refused.reasons))
        self.assertEqual(docket.executor.calls, [])

    def test_an_undeclared_jurisdiction_is_unknown_rather_than_permission(self):
        docket = fixture_docket()
        refused = docket.issue('referral', SUBJECT, role_id='ombudsman', effective_date=AT)
        self.assertFalse(refused.issued)
        self.assertTrue(any('jurisdiction of role ombudsman is not declared' in r
                            for r in refused.reasons))
        self.assertEqual(docket.executor.calls, [])

    def test_an_undeclared_authority_is_unknown_too(self):
        docket = fixture_docket()
        refused = docket.issue('finding', SUBJECT, role_id='clerk', effective_date=AT)
        self.assertFalse(refused.issued)
        self.assertTrue(any('authority of role clerk is not declared' in r for r in refused.reasons))

    def test_issuing_writes_a_claim_derived_from_the_authority_it_rested_on(self):
        docket = fixture_docket()
        docket.issue('rule', SUBJECT, role_id='commissioner', effective_date=AT)
        issued = docket.institution.mind.claims(docket.institution.ref, 'issued', scope=docket.scope)
        self.assertEqual(len(issued), 1)
        self.assertEqual(issued[0].claim.object[0], 'rule')
        self.assertEqual(issued[0].evidence[0].method, 'issue:rule')

    def test_the_kind_vocabulary_is_closed(self):
        self.assertEqual(sorted(INSTRUMENT_KINDS), ['finding', 'referral', 'rule', 'subpoena'])
        self.assertEqual(kind_of('referral').power, 'refer')
        self.assertTrue(kind_of('subpoena').compels_subject)
        self.assertFalse(kind_of('finding').revisable)
        with self.assertRaises(ValueError):
            kind_of('memo')
        docket = fixture_docket()
        with self.assertRaises(ValueError):
            docket.issue('memo', SUBJECT, role_id='commissioner', effective_date=AT)


# --------------------------------------------------------------------------- the life of one


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class InstrumentLifeTests(unittest.TestCase):
    def test_an_instrument_expires_on_its_own_declared_term(self):
        docket = fixture_docket()
        issuance = docket.issue('subpoena', SUBJECT, role_id='commissioner', effective_date=AT)
        self.assertTrue(issuance.in_force(dt.date(2026, 4, 1)))
        self.assertFalse(issuance.in_force(dt.date(2026, 9, 1)))
        self.assertFalse(issuance.in_force(dt.date(2026, 1, 1)))     # not yet effective

    def test_an_instrument_can_be_lapsed_and_stops_binding(self):
        docket = fixture_docket()
        issuance = docket.issue('rule', SUBJECT, role_id='commissioner', effective_date=AT)
        self.assertTrue(issuance.in_force(AT))
        lapsed = docket.lapse(issuance.id, at=dt.date(2026, 4, 1), reason='superseded by a later rule')
        self.assertTrue(lapsed.lapsed)
        self.assertFalse(lapsed.in_force(dt.date(2026, 4, 2)))
        self.assertEqual(docket.in_force(dt.date(2026, 4, 2)), ())
        # mandate coverage falls with it and the lapse is precedent in the store
        precedents = [r.claim.object for r in
                      docket.institution.mind.claims(docket.institution.ref, 'precedent')]
        self.assertTrue(any(p[0] == 'instrument_lapsed' for p in precedents))
        with self.assertRaises(ValueError):
            docket.lapse('no-such-issuance')

    def test_a_contest_is_recorded_and_lowers_legitimacy(self):
        docket = fixture_docket()
        issuance = docket.issue('rule', SUBJECT, role_id='commissioner', effective_date=AT)
        before = docket.institution.read(as_of=AT).legitimacy.value
        contested = docket.contest(issuance.id, 'exceeds the organic act', by=SUBJECT,
                                   at=dt.date(2026, 4, 1))
        after = docket.institution.read(as_of=dt.date(2026, 4, 1)).legitimacy.value
        self.assertEqual(len(contested.contests), 1)
        self.assertEqual(contested.contests[0].by, SUBJECT)
        self.assertFalse(contested.quashed)
        self.assertTrue(contested.in_force(dt.date(2026, 4, 2)))     # contested is not quashed
        self.assertLess(after, before)
        self.assertTrue(docket.institution.mind.claims(docket.institution.ref, 'contested'))

    def test_quashing_a_contested_instrument_lapses_it(self):
        docket = fixture_docket()
        issuance = docket.issue('rule', SUBJECT, role_id='commissioner', effective_date=AT)
        quashed = docket.contest(issuance.id, 'ultra vires', by=SUBJECT, at=dt.date(2026, 4, 1),
                                 quashes=True)
        self.assertTrue(quashed.quashed)
        self.assertTrue(quashed.lapsed)
        self.assertFalse(quashed.in_force(dt.date(2026, 4, 2)))
        self.assertEqual(docket.get(issuance.id).id, issuance.id)

    def test_a_refused_issuance_has_no_instrument_to_lapse(self):
        docket = fixture_docket()
        refused = docket.issue('rule', OUTSIDE, role_id='commissioner', effective_date=AT)
        with self.assertRaises(ValueError):
            docket.lapse(refused.id)


# --------------------------------------------------------------------------- powers


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class InstrumentPowerTests(unittest.TestCase):
    def test_with_instrument_powers_widens_and_never_narrows(self):
        base = Charter(ORG, {r.id: r for r in ROLES}).validate()
        widened = with_instrument_powers(base, {'staff': frozenset({'subpoena'})})
        self.assertIn('subpoena', widened.role('staff').authority.powers)
        self.assertIn('make_finding', widened.role('staff').authority.powers)
        self.assertEqual(widened.role('staff').authority.jurisdiction, SUBJECTS)
        self.assertEqual(base.role('staff').authority.powers, frozenset({'make_finding'}))
        # no table at all leaves the charter exactly as it was
        self.assertIs(with_instrument_powers(base, None), base)
        self.assertIs(with_instrument_powers(base, {}), base)

    def test_a_declared_extension_makes_an_undeclared_role_declared(self):
        base = Charter(ORG, {r.id: r for r in ROLES}).validate()
        widened = with_instrument_powers(base, {'clerk': frozenset({'make_finding'})})
        self.assertFalse(base.role('clerk').authority.declared)
        self.assertTrue(widened.role('clerk').authority.declared)
        self.assertIsNone(widened.role('clerk').authority.jurisdiction)   # still Unknown

    def test_committee_powers_give_the_chair_the_verbs_and_a_member_none(self):
        self.assertIn('refer', COMMITTEE_INSTRUMENT_POWERS['chair'])
        self.assertEqual(COMMITTEE_INSTRUMENT_POWERS['member'], frozenset())


# --------------------------------------------------------------------------- reception


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class ServiceTests(unittest.TestCase):
    def test_an_instrument_served_on_an_agent_becomes_a_percept_with_served_provenance(self):
        docket = fixture_docket()
        issuance = docket.issue('subpoena', SUBJECT, role_id='commissioner', audience='counterparty',
                                recipients=(SUBJECT,), effective_date=AT)
        target = tc.Store()
        reception = receive(target, issuance, receiver=SUBJECT, at=dt.date(2026, 3, 3))
        self.assertEqual(reception.channel, 'service')
        self.assertEqual(reception.statement, (ORG, 'subpoena', SUBJECT))
        self.assertEqual(provenance_of(target, reception.claim_id), (('disclosed', ORG, 'service'),))
        self.assertEqual(target._claims[reception.claim_id].claim.predicate, 'subpoena')

    def test_an_agent_not_served_receives_nothing(self):
        docket = fixture_docket()
        issuance = docket.issue('subpoena', SUBJECT, role_id='commissioner', audience='counterparty',
                                recipients=(SUBJECT,), effective_date=AT)
        bystander = tc.Store()
        result = receive(bystander, issuance, receiver=OTHER_SUBJECT, at=AT)
        self.assertIsInstance(result, tc.Unknown)
        self.assertEqual(len(bystander._claims), 0)

    def test_a_public_instrument_reaches_anyone(self):
        docket = fixture_docket()
        issuance = docket.issue('rule', SUBJECT, role_id='commissioner', effective_date=AT)
        self.assertTrue(issuance.reaches('public'))
        reader = tc.Store()
        reception = receive(reader, issuance, receiver=OUTSIDE, at=AT)
        self.assertEqual(reception.channel, 'service')
        self.assertEqual(reception.statement[1], 'rule')


# --------------------------------------------------------------------------- real data


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
@unittest.skipUnless(INDEX.is_file(), 'No local unified index; skipping the real-data smoke test')
class RealCommitteeSmokeTests(unittest.TestCase):
    """A real committee: one referral inside its published jurisdiction and two that are not."""

    def test_a_real_committee_issues_one_authorized_referral_and_two_that_are_not(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.instruments import worked_example
        with EvidenceIndex(INDEX) as index:
            docket, report = worked_example(index=index, at=dt.date(2026, 9, 1))
        self.assertEqual(report['entity_id'], WORKED_EXAMPLE)
        self.assertGreater(report['jurisdiction'], 50)
        attempts = dict(report['attempts'])
        self.assertEqual(len(attempts), 3)
        by_label = {label: issuance for label, issuance in report['attempts']}
        inside = by_label['a measure referred to this committee, by the chair']
        outside = by_label['a measure never referred to this committee, by the chair']
        member = by_label['a measure referred to this committee, by a plain member']
        self.assertTrue(inside.issued)
        self.assertTrue(inside.subject.startswith('congress:'))
        self.assertIn(inside.subject, docket.institution.declared_jurisdiction())
        self.assertFalse(outside.issued)
        self.assertTrue(any('outside the declared jurisdiction' in r for r in outside.reasons))
        self.assertFalse(member.issued)
        self.assertTrue(any("holds no power 'refer'" in r for r in member.reasons))
        # exactly one act reached the executor, and both refusals are on the record
        self.assertEqual(report['executor_calls'], 1)
        self.assertEqual(len(docket.refusals), 2)
        self.assertGreaterEqual(len(docket.institution.refusals()), 2)
        # legitimacy erodes for the attempts, whether or not they succeeded
        self.assertTrue(report['legitimacy'].ultra_vires.known)
        self.assertGreaterEqual(report['legitimacy'].ultra_vires.value, 2.0)
        self.assertTrue(report['unknown'])

    def test_the_real_referral_is_grounded_in_a_published_referral_edge(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.institution import committee_records
        with EvidenceIndex(INDEX) as index:
            records = committee_records(index, WORKED_EXAMPLE, referrals=40)
            published = {row['subject'] for row in index.edges([WORKED_EXAMPLE],
                                                               'referred_to_committee',
                                                               direction='in', limit=40)}
        jurisdictions = {s for role in records.roles for s in (role.authority.jurisdiction or ())}
        self.assertTrue(jurisdictions)
        self.assertTrue(jurisdictions <= published)


if __name__ == '__main__':
    unittest.main()
