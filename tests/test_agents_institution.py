"""Institutional cognition: declared authority, refusal on the record, and legitimacy that erodes.

The fixtures are a small regulator with three seats and one statutory instrument. The real-data
smoke tests use a published congressional committee, whose jurisdiction is the set of measures
actually referred to it. Everything skips unless the optional ``agents`` extra (tensorcode) is
installed, the same way the numpy tests skip on ``fast``.
"""
import datetime as dt
import math
import unittest
from pathlib import Path

from worldmodel.agents import tensorcode_available

HAVE_TC = tensorcode_available()
DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
INDEX = DATA_ROOT / 'world_evidence' / 'index.sqlite'
NEEDS_TC = 'institutional cognition requires the optional agents extra (tensorcode)'

if HAVE_TC:
    from worldmodel.agents.institution import (COMMITTEE_AUTHORITY, Institution, InstitutionRecords,
                                               Instrument, RecordingExecutor, ULTRA_VIRES_DECAY,
                                               committee_procedure, committee_records)
    from worldmodel.agents.roles import (Authority, Charter, Holder, InstitutionalAct, Procedure,
                                         Role, Source, Step)

ORG = 'usgov:agency:fixture_commission'
FIXTURE = Source('fixture_register', 'v1', 'rec:statute:1') if HAVE_TC else None
SUBJECTS = frozenset({'sec:cik:0000000042', 'sec:cik:0000000043'})
OUTSIDE = 'sec:cik:0000000099'
AT = dt.date(2026, 3, 1)

if HAVE_TC:
    ROLES = (Role('commissioner', ORG, 'Commissioner', 'chair',
                  Authority(powers=frozenset({'issue_rule', 'open_investigation'}), jurisdiction=SUBJECTS,
                            sources=(FIXTURE,)), sources=(FIXTURE,)),
             Role('staff', ORG, 'Enforcement Staff', 'staff',
                  Authority(powers=frozenset({'open_investigation'}), jurisdiction=SUBJECTS,
                            sources=(FIXTURE,)), sources=(FIXTURE,)),
             Role('clerk', ORG, 'Clerk', 'staff', Authority(powers=frozenset(), jurisdiction=SUBJECTS),
                  sources=(FIXTURE,)))

    HOLDERS = (Holder('bioguide:X000001', 'commissioner', dt.date(2023, 1, 1), None, (), (FIXTURE,)),
               Holder('bioguide:X000002', 'staff', dt.date(2023, 1, 1), None, (), (FIXTURE,)),
               Holder('bioguide:X000003', 'clerk', dt.date(2023, 1, 1), None, (), (FIXTURE,)))

    INSTRUMENT = Instrument('organic_act', frozenset({'issue_rule', 'open_investigation'}), SUBJECTS,
                            sources=(FIXTURE,))

    RULEMAKING = Procedure('rulemaking', (Step('propose', 'staff', 'propose', 'open_investigation'),
                                          Step('issue', 'commissioner', 'approve', 'issue_rule')),
                           settles='authority')


def fixture_institution(**kwargs):
    records = InstitutionRecords(ORG, 'Fixture Commission', ROLES, HOLDERS, (INSTRUMENT,),
                                 ('enabling statute text: not published in any catalog dataset',),
                                 (FIXTURE,))
    charter = Charter(ORG, {r.id: r for r in ROLES}, procedures={'rulemaking': RULEMAKING}).validate()
    return Institution(records, charter, **kwargs)


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class AuthorizationTests(unittest.TestCase):
    def test_an_authorized_act_reaches_the_executor(self):
        institution = fixture_institution()
        executor = RecordingExecutor()
        receipt = institution.act(InstitutionalAct(power='issue_rule', subject='sec:cik:0000000042'),
                                  role_id='commissioner', executor=executor, at=AT)
        self.assertEqual(receipt.status, 'applied')
        self.assertEqual(len(executor.calls), 1)
        self.assertEqual([r.claim.predicate for r in institution.mind.claims(institution.ref, 'acted')],
                         ['acted'])

    def test_an_unauthorized_act_is_refused_and_never_performed(self):
        institution = fixture_institution()
        executor = RecordingExecutor()
        receipt = institution.act(InstitutionalAct(power='issue_rule', subject='sec:cik:0000000042'),
                                  role_id='clerk', executor=executor, at=AT)
        self.assertEqual(receipt.status, 'rejected')
        self.assertIn('holds no power', receipt.error)
        self.assertEqual(executor.calls, [])                 # the effect was never attempted
        refusals = institution.refusals()
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0].claim.predicate, 'ultra_vires')
        self.assertEqual(refusals[0].claim.object[0], 'issue_rule')
        self.assertEqual(institution.log.refusals[0].status, 'refused')

    def test_an_act_outside_the_declared_jurisdiction_is_ultra_vires(self):
        institution = fixture_institution()
        executor = RecordingExecutor()
        receipt = institution.act(InstitutionalAct(power='issue_rule', subject=OUTSIDE),
                                  role_id='commissioner', executor=executor, at=AT)
        self.assertEqual(receipt.status, 'rejected')
        self.assertIn('outside the declared jurisdiction', receipt.error)
        self.assertEqual(executor.calls, [])
        self.assertEqual(institution.refusals()[0].claim.object[1], OUTSIDE)

    def test_an_undeclared_jurisdiction_is_unknown_rather_than_permission(self):
        records = InstitutionRecords(ORG, 'Fixture Commission',
                                     (Role('commissioner', ORG, 'Commissioner', 'chair',
                                           Authority(powers=frozenset({'issue_rule'}))),), HOLDERS[:1])
        institution = Institution(records, Charter(ORG, {'commissioner': records.roles[0]}))
        executor = RecordingExecutor()
        receipt = institution.act(InstitutionalAct(power='issue_rule', subject=OUTSIDE),
                                  role_id='commissioner', executor=executor, at=AT)
        self.assertEqual(receipt.status, 'rejected')
        self.assertTrue(receipt.error.startswith('unknown:'))
        self.assertIn('jurisdiction of role commissioner is not declared', receipt.error)
        self.assertEqual(executor.calls, [])

    def test_a_refused_step_stops_the_procedure(self):
        institution = fixture_institution()
        outcome = institution.decide('rulemaking', at=AT,
                                     act_for=lambda step, state: InstitutionalAct(
                                         power=step.power, subject=OUTSIDE))
        self.assertFalse(outcome.completed)
        self.assertEqual(outcome.blocked_at, 'propose')
        self.assertEqual(len(outcome.outcomes), 1)

    def test_a_procedure_whose_steps_are_all_authorized_completes(self):
        institution = fixture_institution()
        outcome = institution.decide('rulemaking', at=AT,
                                     act_for=lambda step, state: InstitutionalAct(
                                         power=step.power, subject='sec:cik:0000000043'))
        self.assertTrue(outcome.completed)
        self.assertEqual([o.step.role_id for o in outcome.outcomes], ['staff', 'commissioner'])


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class LegitimacyTests(unittest.TestCase):
    def test_viability_is_authority_not_solvency(self):
        reading = fixture_institution().read(as_of=AT)
        self.assertEqual(set(reading.values()),
                         {'mandate_coverage', 'authority_margin', 'ultra_vires', 'contestation',
                          'legitimacy', 'procedural_integration'})
        self.assertNotIn('viability_gradient', reading.values())
        self.assertNotIn('liquidity_pressure', reading.values())
        self.assertEqual(reading.mandate_coverage.value, 1.0)

    def test_legitimacy_erodes_with_every_recorded_ultra_vires_attempt(self):
        institution = fixture_institution()
        institution.act(InstitutionalAct(power='issue_rule', subject='sec:cik:0000000042'),
                        role_id='commissioner', at=AT)
        clean = institution.read(as_of=AT).legitimacy.value
        institution.act(InstitutionalAct(power='issue_rule', subject=OUTSIDE),
                        role_id='commissioner', at=AT)
        once = institution.read(as_of=AT).legitimacy.value
        institution.act(InstitutionalAct(power='issue_rule', subject=OUTSIDE),
                        role_id='commissioner', at=AT)
        twice = institution.read(as_of=AT).legitimacy.value
        self.assertGreater(clean, once)
        self.assertGreater(once, twice)
        self.assertAlmostEqual(twice, clean * math.exp(-2 * ULTRA_VIRES_DECAY), places=5)
        self.assertEqual(institution.read(as_of=AT).ultra_vires.value, 2.0)

    def test_erosion_does_not_recover_by_behaving_afterwards(self):
        institution = fixture_institution()
        institution.act(InstitutionalAct(power='issue_rule', subject=OUTSIDE),
                        role_id='commissioner', at=AT)
        eroded = institution.read(as_of=AT).legitimacy.value
        for _ in range(5):
            institution.act(InstitutionalAct(power='issue_rule', subject='sec:cik:0000000042'),
                            role_id='commissioner', at=AT)
        self.assertAlmostEqual(institution.read(as_of=AT).legitimacy.value, eroded, places=9)
        self.assertGreater(institution.read(as_of=AT).authority_margin.value, 0.5)

    def test_a_lapsed_instrument_drops_mandate_coverage(self):
        institution = fixture_institution()
        self.assertEqual(institution.read(as_of=AT).mandate_coverage.value, 1.0)
        institution.lapse('organic_act', at=AT)
        after = institution.read(as_of=AT)
        self.assertEqual(after.mandate_coverage.value, 0.0)
        self.assertEqual(after.legitimacy.value, 0.0)
        self.assertEqual(after.regime, 'illegitimate')
        self.assertTrue(any(r.claim.object[0] == 'instrument_lapsed'
                            for r in institution.mind.claims(institution.ref, 'precedent')))

    def test_contestation_lowers_legitimacy_and_is_on_the_record(self):
        institution = fixture_institution()
        before = institution.read(as_of=AT).legitimacy.value
        institution.contest('sec:cik:0000000042', 'issued without notice and comment', at=AT)
        after = institution.read(as_of=AT)
        self.assertLess(after.legitimacy.value, before)
        self.assertGreater(after.contestation.value, 0.0)
        self.assertEqual([r.claim.object[1] for r in institution.mind.claims(institution.ref, 'contested')],
                         ['issued without notice and comment'])

    def test_an_expired_instrument_is_not_current(self):
        instrument = Instrument('sunset', frozenset({'issue_rule'}), SUBJECTS,
                                effective_from=dt.date(2020, 1, 1), expires=dt.date(2024, 1, 1))
        self.assertTrue(instrument.current(dt.date(2022, 1, 1)))
        self.assertFalse(instrument.current(dt.date(2026, 1, 1)))

    def test_ascription_is_agentive_and_not_phenomenal(self):
        ascription = fixture_institution().ascription()
        self.assertEqual(ascription.template, 'agent')
        self.assertEqual(ascription.phenomenality, 0.0)
        self.assertIsNone(ascription.harm_constraint)
        self.assertIn('not a subject of experience', ascription.rationale)

    def test_memory_does_not_decay(self):
        self.assertEqual(Institution.decay_policy, 'none')
        self.assertIn('record', Institution.decay_rationale)


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
@unittest.skipUnless(INDEX.is_file(),
                     'No local unified index; skipping the real-data smoke test')
class RealDataSmokeTests(unittest.TestCase):
    """The House Budget Committee: its members are published and so is its jurisdiction."""

    COMMITTEE = 'congress:committee:hsbu00'

    def institution(self, index):
        records = committee_records(index, self.COMMITTEE)
        charter = Charter(records.entity_id, {r.id: r for r in records.roles},
                          procedures={'report_measure': committee_procedure()}).validate()
        return records, Institution(records, charter)

    def test_a_referred_measure_may_be_reported_and_an_unreferred_one_may_not(self):
        from worldmodel.agents.grounding import EvidenceIndex
        with EvidenceIndex(INDEX) as index:
            records, institution = self.institution(index)
            self.assertIn('chair', institution.charter.roles)
            self.assertIn('member', institution.charter.roles)
            self.assertEqual(institution.charter.role('member').authority.powers, frozenset())
            self.assertEqual(institution.charter.role('chair').authority.powers,
                             COMMITTEE_AUTHORITY['chair'])
            jurisdiction = sorted(institution.declared_jurisdiction())
            self.assertTrue(jurisdiction, 'the committee should have published referrals')
            self.assertTrue(all(item.startswith('congress:bill:') for item in jurisdiction[:5]))
            self.assertTrue(all(role.sources for role in institution.charter.roles.values()))

            executor = RecordingExecutor()
            allowed = institution.act(
                InstitutionalAct(power='report_measure', subject=jurisdiction[0]),
                role_id='chair', executor=executor, at='2026-09-01')
            refused = institution.act(
                InstitutionalAct(power='report_measure', subject='congress:bill:118-hr-99999999'),
                role_id='chair', executor=executor, at='2026-09-01')
            by_member = institution.act(
                InstitutionalAct(power='report_measure', subject=jurisdiction[0]),
                role_id='member', executor=executor, at='2026-09-01')
        self.assertEqual(allowed.status, 'applied')
        self.assertEqual(refused.status, 'rejected')
        self.assertIn('outside the declared jurisdiction', refused.error)
        self.assertEqual(by_member.status, 'rejected')
        self.assertIn('holds no power', by_member.error)
        self.assertEqual(len(executor.calls), 1)             # only the authorized act was performed
        self.assertEqual(len(institution.refusals()), 2)     # both refusals are on the record
        reading = institution.read(as_of='2026-09-01')
        self.assertEqual(reading.mandate_coverage.value, 1.0)
        self.assertAlmostEqual(reading.authority_margin.value, 1.0 / 3.0, places=6)
        self.assertLess(reading.legitimacy.value, 1.0)
        self.assertTrue(any('not published' in item for item in institution.unknown()))

    def test_membership_and_jurisdiction_carry_published_record_ids(self):
        from worldmodel.agents.grounding import EvidenceIndex
        with EvidenceIndex(INDEX) as index:
            records, institution = self.institution(index)
        self.assertTrue(records.holders)
        self.assertTrue(all(holder.sources for holder in records.holders))
        self.assertTrue(all(source.dataset for holder in records.holders for source in holder.sources))
        self.assertTrue(records.instruments)
        self.assertTrue(records.instruments[0].sources)
        self.assertTrue(all('@' in source.cite() and '#' in source.cite()
                            for source in records.instruments[0].sources))


if __name__ == '__main__':
    unittest.main()
