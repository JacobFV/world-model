"""Organizational speech: disclosure as an act, the gap between belief and statement, obligation.

Fixtures are fully declared and deterministic. The real-data smoke tests skip unless this checkout
has the unified index and the published ``sec_company_assets`` dataset, and everything skips unless
the optional ``agents`` extra (tensorcode) is installed - the same way the numpy tests skip on
``fast``.
"""
import datetime as dt
import unittest
from pathlib import Path

from worldmodel.agents import tensorcode_available

HAVE_TC = tensorcode_available()
DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
INDEX = DATA_ROOT / 'world_evidence' / 'index.sqlite'
SEC_ASSETS = DATA_ROOT / 'sec_company_assets' / 'manifests'
NEEDS_TC = 'organizational communication requires the optional agents extra (tensorcode)'

if HAVE_TC:
    import tensorcode as tc

    from worldmodel.agents.disclosure import (AUDIENCE_REACH, ComplianceFinding, Disclosure,
                                              DisclosureDesk, DisclosureRefusal, DisclosureRegister,
                                              FORMS, HOLIDAY_SLACK_BUSINESS_DAYS, Obligation,
                                              WORKED_EXAMPLE, add_business_days,
                                              business_days_between, compliance,
                                              disclosed_not_observed, filings_from_index, form_of,
                                              obligation_for_filing, provenance_of, receive, select,
                                              statement_of)
    from worldmodel.agents.firm import Firm, FirmRecords, STANDARD_DELEGATION, charter_from_records
    from worldmodel.agents.roles import Holder, Role, Source

ORG = 'sec:cik:0000000042'
COUNTERPARTY = 'sec:cik:0000009001'
PUBLIC_READER = 'sec:cik:0000009002'
Q_END = dt.date(2024, 9, 30)
FILED = dt.date(2024, 11, 1)
FIXTURE = Source('fixture_filings', 'v1', 'rec:filing:1') if HAVE_TC else None

if HAVE_TC:
    ROLES = (Role('board', ORG, 'Board of Directors', 'board', sources=(FIXTURE,)),
             Role('cfo', ORG, 'Chief Financial Officer', 'executive', sources=(FIXTURE,)),
             Role('general_counsel', ORG, 'General Counsel', 'executive', sources=(FIXTURE,)),
             Role('unit_olefins', ORG, 'Olefins', 'operating_unit', sources=(FIXTURE,)))

    HOLDERS = (Holder('sec:cik:1001', 'board', dt.date(2019, 1, 1), None, ('Director',), (FIXTURE,)),
               Holder('sec:cik:1011', 'board', dt.date(2019, 1, 1), None, ('Director',), (FIXTURE,)),
               Holder('sec:cik:1012', 'board', dt.date(2020, 6, 1), None, ('Director',), (FIXTURE,)),
               Holder('sec:cik:1002', 'cfo', dt.date(2021, 1, 1), None, ('EVP & CFO',), (FIXTURE,)),
               Holder('sec:cik:1003', 'general_counsel', dt.date(2020, 1, 1), None, ('EVP & GC',), (FIXTURE,)),
               Holder('sec:cik:1004', 'unit_olefins', dt.date(2022, 1, 1), None, ('EVP, Olefins',), (FIXTURE,)))

    #: A large accelerated filer's quarterly deadline, declared with its citation. The deadline is
    #: the regulation; nothing here is fitted.
    PERIODIC = Obligation('periodic:10-q', 'periodic', 'fiscal quarter end', 'regulator', 'filing',
                          deadline_days=40, citation='17 CFR 240.13a-13', filed_form='10-Q',
                          sources=(FIXTURE,))
    #: An obligation that is *named* and whose terms are not declared. It must stay Unknown.
    UNDECLARED = Obligation('material_event:8-k', 'material_event', 'the reportable event',
                            'regulator', 'filing', declared=False, citation='17 CFR 240.13a-11')
    #: Declared, on a business-day clock, so the holiday-slack path can be tested.
    INSIDER = Obligation('insider_transaction:4', 'insider_transaction', 'the transaction',
                         'regulator', 'filing', deadline_business_days=2, owed_by='insider',
                         citation='Exchange Act s.16(a)', filed_form='4')


def fixture_firm(*, authority=STANDARD_DELEGATION if HAVE_TC else None):
    records = FirmRecords(entity_id=ORG, name='Fixture Chemicals N.V.', roles=ROLES, holders=HOLDERS,
                          unknown=('covenant terms: not published in any catalog dataset',))
    return Firm(records, charter_from_records(records, authority=authority))


def fixture_desk(*, obligations=(), **kwargs):
    return DisclosureDesk(fixture_firm(**kwargs), obligations=obligations,
                          predicates=('cash', 'restructuring_charge', 'plant_outage'))


def held_cash(desk, *, value=1.9e9, known_at=Q_END, obligation_id=None):
    return desk.hold('cash', float(value), known_at=known_at, source=FIXTURE,
                     basis='books closed for the quarter', obligation_id=obligation_id)


# --------------------------------------------------------------------------- the act


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class DisclosureAsAnActTests(unittest.TestCase):
    def test_a_disclosure_carries_form_audience_role_dates_and_permanence(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        said = desk.disclose(claim_id, form='filing', audience='public', role_id='general_counsel',
                             effective_date=FILED, accession='0000000042-24-000046', filed_form='10-Q')
        self.assertIsInstance(said, Disclosure)
        self.assertEqual(said.form, 'filing')
        self.assertEqual(sorted(said.audience), ['public'])
        self.assertEqual(said.role_id, 'general_counsel')
        self.assertEqual(said.holder, 'sec:cik:1003')      # attributable to a *role*, seated by record
        self.assertEqual(str(said.effective_date)[:10], '2024-11-01')
        self.assertEqual(str(said.known_at)[:10], '2024-09-30')
        self.assertTrue(said.on_the_record)
        self.assertTrue(said.revisable)                    # a filing has an amendment mechanism
        self.assertEqual(said.cite(), '10-Q 0000000042-24-000046')

    def test_form_fixes_what_the_act_can_do(self):
        self.assertTrue(form_of('filing').discharges_obligation)
        self.assertFalse(form_of('press_release').discharges_obligation)
        self.assertFalse(form_of('press_release').revisable)    # a later release does not amend
        self.assertFalse(form_of('private_briefing').on_the_record)
        with self.assertRaises(ValueError):
            form_of('gossip')

    def test_a_public_filing_reaches_the_regulator_and_a_private_briefing_does_not(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        public = desk.disclose(claim_id, form='filing', audience='public', role_id='cfo',
                               effective_date=FILED)
        private = desk.disclose(claim_id, form='private_briefing', audience='counterparty',
                                recipients=(COUNTERPARTY,), role_id='cfo',
                                effective_date=dt.date(2024, 10, 5))
        self.assertTrue(public.reaches('regulator'))
        self.assertTrue(public.reaches('counterparty'))
        self.assertTrue(public.reaches_receiver(PUBLIC_READER))
        self.assertFalse(private.reaches('public'))
        self.assertTrue(private.reaches_receiver(COUNTERPARTY))
        self.assertFalse(private.reaches_receiver(PUBLIC_READER))
        self.assertEqual(AUDIENCE_REACH['counterparty'], ('counterparty',))

    def test_the_register_is_append_only_and_a_correction_supersedes(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        first = desk.disclose(claim_id, form='filing', audience='public', role_id='cfo',
                              effective_date=FILED, accession='0000000042-24-000046', filed_form='10-Q')
        amended = desk.disclose(claim_id, form='filing', audience='public', role_id='cfo',
                                effective_date=dt.date(2024, 12, 2), supersedes=first.id,
                                accession='0000000042-24-000051', filed_form='10-Q/A')
        self.assertEqual(len(desk.register), 2)
        self.assertEqual(desk.register.superseded(), ((first.id, amended.id),))
        # the first statement is still on the register: an organization cannot unsay a filing
        self.assertEqual([d.id for d in desk.register.about(claim_id)], [first.id, amended.id])
        self.assertEqual(desk.register.latest_about(claim_id).id, amended.id)
        with self.assertRaises(ValueError):
            desk.register.record(first)


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class AuthorityTests(unittest.TestCase):
    def test_an_unauthorized_disclosure_is_refused_and_never_registered(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        refused = desk.disclose(claim_id, form='filing', audience='public', role_id='unit_olefins',
                                effective_date=FILED)
        self.assertIsInstance(refused, DisclosureRefusal)
        self.assertEqual(refused.status, 'fails')
        self.assertTrue(any('approve_disclosure' in reason for reason in refused.reasons))
        self.assertEqual(len(desk.register), 0)                      # nothing was said
        self.assertEqual([r.claim.predicate for r in desk.refusals()], ['ultra_vires'])
        self.assertTrue(desk.firm.log.refusals)                      # and it is on the act log

    def test_an_undeclared_authority_is_unknown_rather_than_a_refusal(self):
        desk = fixture_desk(authority=None)
        claim_id = held_cash(desk)
        refused = desk.disclose(claim_id, form='filing', audience='public', role_id='general_counsel',
                                effective_date=FILED)
        self.assertIsInstance(refused, DisclosureRefusal)
        self.assertEqual(refused.status, 'unknown')
        self.assertTrue(any('not declared' in reason for reason in refused.reasons))
        self.assertEqual(len(desk.register), 0)

    def test_a_statement_about_another_entity_binds_the_declared_jurisdiction(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        # STANDARD_DELEGATION declares no jurisdiction, so speaking about somebody else is Unknown,
        # not permission - the same rule institutional acts run under.
        refused = desk.disclose(claim_id, form='filing', audience='public', role_id='general_counsel',
                                effective_date=FILED, about=COUNTERPARTY)
        self.assertEqual(refused.status, 'unknown')
        self.assertTrue(any('jurisdiction' in reason for reason in refused.reasons))


# --------------------------------------------------------------------------- the gap


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class BeliefDisclosureGapTests(unittest.TestCase):
    def test_an_undisclosed_belief_is_queryable_and_distinct_from_a_disclosed_one(self):
        desk = fixture_desk()
        secret = desk.hold('plant_outage', 'unit 3 down', known_at=dt.date(2024, 10, 1), source=FIXTURE)
        told = held_cash(desk)
        desk.disclose(told, form='filing', audience='public', role_id='cfo', effective_date=FILED)
        undisclosed = desk.undisclosed(audience='public', at=FILED)
        self.assertEqual([gap.claim_id for gap in undisclosed], [secret])
        self.assertEqual([gap.state for gap in undisclosed], ['undisclosed'])
        # the firm holds both; only one of them has been said, and the two are separately queryable
        self.assertEqual(len(desk.held()), 2)
        disclosed = [gap for gap in desk.gaps(at=FILED) if gap.state == 'disclosed']
        self.assertEqual([gap.claim_id for gap in disclosed], [told])
        self.assertNotEqual(secret, told)
        # the undisclosed claim is in the store and explainable, it is simply not on the register
        self.assertTrue(desk.firm.mind._claims[secret].evidence)
        self.assertEqual(desk.register.about(secret), ())

    def test_delay_is_the_distance_between_holding_and_saying_and_is_itself_a_choice(self):
        desk = fixture_desk()
        claim_id = held_cash(desk, known_at=Q_END)
        said = desk.disclose(claim_id, form='filing', audience='public', role_id='cfo',
                             effective_date=FILED)
        self.assertAlmostEqual(said.delay_days, 32.0)
        gap = next(g for g in desk.gaps(at=FILED) if g.claim_id == claim_id)
        self.assertAlmostEqual(gap.delay_days, 32.0)
        # before the filing date the same claim is held and unsaid: the gap is a state, not a summary
        earlier = desk.gaps(at=dt.date(2024, 10, 15))
        self.assertEqual([g.state for g in earlier if g.claim_id == claim_id], ['undisclosed'])

    def test_a_claim_told_only_to_a_counterparty_is_selectively_disclosed_to_the_public(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        desk.disclose(claim_id, form='private_briefing', audience='counterparty',
                      recipients=(COUNTERPARTY,), role_id='cfo', effective_date=dt.date(2024, 10, 5))
        public_gap = next(g for g in desk.gaps(audience='public', at=FILED) if g.claim_id == claim_id)
        counterparty_gap = next(g for g in desk.gaps(audience='counterparty', at=FILED)
                                if g.claim_id == claim_id)
        self.assertEqual(public_gap.state, 'selectively_disclosed')
        self.assertEqual(counterparty_gap.state, 'disclosed')
        self.assertEqual(sorted(public_gap.told_audiences), ['counterparty'])
        self.assertTrue(public_gap.open)

    def test_saying_it_writes_a_disclosed_claim_derived_from_the_claim_it_says(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        desk.disclose(claim_id, form='filing', audience='public', role_id='cfo', effective_date=FILED,
                      accession='0000000042-24-000046', filed_form='10-Q')
        said = desk.firm.mind.claims(desk.ref, 'disclosed', scope=desk.disclosed_scope)
        self.assertEqual(len(said), 1)
        self.assertEqual(said[0].evidence[0].derived_from, (claim_id,))
        self.assertEqual(said[0].evidence[0].locator, '0000000042-24-000046')
        # retracting what it holds withdraws the record that it said it: the two are linked
        desk.firm.mind.apply(tc.Patch((tc.Retract(claim_id, 'restated'),), desk.firm.mind.revision))
        self.assertEqual(desk.firm.mind.claims(desk.ref, 'disclosed', scope=desk.disclosed_scope), [])

    def test_there_is_no_deception_flag_anywhere_in_the_model(self):
        # What is modelled is selection under obligation, not lying. Nothing in a Disclosure or a
        # Gap can mark a statement false, and this pins that the vocabulary stays that way.
        forbidden = {'lie', 'lying', 'deceptive', 'false_statement', 'misleading'}
        for cls in (Disclosure, ComplianceFinding, DisclosureRefusal):
            self.assertEqual(forbidden & set(cls.__dataclass_fields__), set())


# --------------------------------------------------------------------------- obligation


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class ObligationTests(unittest.TestCase):
    def test_a_deadline_met_and_a_deadline_missed(self):
        desk = fixture_desk(obligations=(PERIODIC,))
        claim_id = held_cash(desk, obligation_id=PERIODIC.id)
        on_time = desk.disclose(claim_id, form='filing', audience='public', role_id='cfo',
                                effective_date=FILED, obligation_id=PERIODIC.id)
        self.assertEqual(compliance(PERIODIC, Q_END, on_time).state, 'met')
        self.assertIs(compliance(PERIODIC, Q_END, on_time).compliant, True)
        late = Disclosure('late', ORG, ('x', 'cash', 1.0), 'filing', frozenset({'public'}), 'cfo',
                          dt.date(2025, 1, 15), Q_END)
        finding = compliance(PERIODIC, Q_END, late)
        self.assertEqual(finding.state, 'unmet')
        self.assertIs(finding.compliant, False)
        self.assertGreater(finding.late_days, 60)
        self.assertIn('17 CFR 240.13a-13', finding.basis)

    def test_an_unknown_obligation_is_not_treated_as_compliance(self):
        finding = compliance(UNDECLARED, dt.date(2024, 10, 1), None, now=dt.date(2025, 6, 1))
        self.assertEqual(finding.state, 'unknown')
        self.assertFalse(finding.known)
        self.assertIsInstance(finding.compliant, tc.Unknown)          # not True, and not False
        self.assertNotEqual(finding.compliant, True)
        self.assertIn('not declared', finding.basis)
        # a declared obligation with no declared deadline is Unknown for the same reason
        no_deadline = Obligation('periodic:20-f', 'periodic', 'fiscal year end',
                                 citation='17 CFR 240.13a-1')
        self.assertEqual(compliance(no_deadline, Q_END, None, now=dt.date(2026, 1, 1)).state, 'unknown')
        self.assertIsInstance(compliance(no_deadline, Q_END, None).compliant, tc.Unknown)

    def test_where_no_obligation_is_declared_silence_is_not_a_violation(self):
        finding = compliance(None, dt.date(2024, 10, 1), None, now=dt.date(2026, 1, 1))
        self.assertEqual(finding.state, 'no_obligation')
        self.assertIsInstance(finding.compliant, tc.Unknown)
        self.assertIn('not a violation', finding.basis)
        desk = fixture_desk()
        desk.hold('plant_outage', 'unit 3 down', known_at=dt.date(2024, 10, 1), source=FIXTURE)
        gap = desk.undisclosed(at=dt.date(2026, 1, 1))[0]
        self.assertEqual(gap.compliance.state, 'no_obligation')

    def test_an_unmet_obligation_is_a_recordable_state_even_with_nothing_said(self):
        desk = fixture_desk(obligations=(PERIODIC,))
        held_cash(desk, obligation_id=PERIODIC.id)
        status = dict(desk.obligation_status(now=dt.date(2025, 3, 1)))
        self.assertEqual(status[PERIODIC.id].state, 'unmet')
        self.assertGreater(status[PERIODIC.id].late_days, 100)
        # before the deadline the same silence is 'pending', not a violation
        pending = dict(desk.obligation_status(now=dt.date(2024, 10, 15)))
        self.assertEqual(pending[PERIODIC.id].state, 'pending')

    def test_a_press_release_does_not_discharge_a_filing_obligation(self):
        desk = fixture_desk(obligations=(PERIODIC,))
        claim_id = held_cash(desk, obligation_id=PERIODIC.id)
        release = desk.disclose(claim_id, form='press_release', audience='public', role_id='cfo',
                                effective_date=dt.date(2024, 10, 20), obligation_id=PERIODIC.id)
        finding = compliance(PERIODIC, Q_END, release, now=dt.date(2025, 3, 1))
        self.assertEqual(finding.state, 'unmet')
        self.assertIn('cannot discharge', finding.basis)

    def test_a_private_briefing_does_not_reach_the_obliged_audience(self):
        obligation = Obligation('periodic:public', 'periodic', 'quarter end', audience='public',
                                deadline_days=40, citation='fixture rule')
        briefing = Disclosure('b', ORG, ('x', 'cash', 1.0), 'filing', frozenset({'counterparty'}),
                              'cfo', dt.date(2024, 10, 5), Q_END, recipients=(COUNTERPARTY,))
        finding = compliance(obligation, Q_END, briefing, now=dt.date(2025, 3, 1))
        self.assertEqual(finding.state, 'unmet')
        self.assertIn('does not reach the public', finding.basis)

    def test_a_business_day_miss_inside_the_holiday_slack_is_unknown_not_a_violation(self):
        transaction = dt.date(2024, 12, 24)
        near = Disclosure('n', ORG, ('x', 'insider_transaction', 'a'), 'filing',
                          frozenset({'public'}), 'cfo', dt.date(2024, 12, 30), transaction)
        finding = compliance(INSIDER, transaction, near)
        self.assertEqual(finding.state, 'unknown')
        self.assertIn('holiday calendar', finding.basis)
        self.assertIsInstance(finding.compliant, tc.Unknown)
        far = Disclosure('f', ORG, ('x', 'insider_transaction', 'a'), 'filing',
                         frozenset({'public'}), 'cfo', dt.date(2025, 3, 3), transaction)
        self.assertEqual(compliance(INSIDER, transaction, far).state, 'unmet')

    def test_business_day_arithmetic_is_deterministic_and_weekday_only(self):
        self.assertEqual(business_days_between('2024-12-24', '2024-12-27'), 3)
        self.assertEqual(business_days_between('2024-12-27', '2024-12-24'), -3)
        self.assertEqual(business_days_between('2024-12-27', '2024-12-30'), 1)   # the weekend costs nothing
        self.assertEqual(str(add_business_days('2024-12-27', 2))[:10], '2024-12-31')
        self.assertIsNone(business_days_between(None, '2024-12-27'))
        self.assertEqual(HOLIDAY_SLACK_BUSINESS_DAYS, 3)


# --------------------------------------------------------------------------- selection


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class SelectionTests(unittest.TestCase):
    def test_only_an_obligation_moves_a_claim_out_of_silence(self):
        desk = fixture_desk(obligations=(PERIODIC, UNDECLARED))
        compelled = held_cash(desk, obligation_id=PERIODIC.id)
        unknown = desk.hold('restructuring_charge', 4.2e8, known_at=dt.date(2024, 10, 1),
                            source=FIXTURE, obligation_id=UNDECLARED.id)
        voluntary = desk.hold('plant_outage', 'unit 3 down', known_at=dt.date(2024, 10, 1),
                              source=FIXTURE)
        selection = desk.select(at=dt.date(2025, 3, 1))
        self.assertEqual([row[0] for row in selection.disclose], [compelled])
        self.assertEqual([row[2] for row in selection.disclose], ['compelled_overdue'])
        withheld = {row[0]: row[2] for row in selection.withhold}
        self.assertEqual(withheld[unknown], 'obligation_unknown')
        self.assertEqual(withheld[voluntary], 'no_obligation_declared')
        self.assertEqual(selection.defer, ())

    def test_before_the_deadline_the_same_claim_is_deferred_not_withheld(self):
        desk = fixture_desk(obligations=(PERIODIC,))
        held_cash(desk, obligation_id=PERIODIC.id)
        selection = desk.select(at=dt.date(2024, 10, 15))
        self.assertEqual([row[2] for row in selection.defer], ['not_yet_due'])
        self.assertEqual(selection.disclose, ())

    def test_selection_is_deterministic(self):
        desk = fixture_desk(obligations=(PERIODIC,))
        held_cash(desk, obligation_id=PERIODIC.id)
        desk.hold('plant_outage', 'unit 3 down', known_at=dt.date(2024, 10, 1), source=FIXTURE)
        first = desk.select(at=dt.date(2025, 3, 1))
        second = desk.select(at=dt.date(2025, 3, 1))
        self.assertEqual(first, second)
        self.assertEqual(select((), at=dt.date(2025, 3, 1)).disclose, ())


# --------------------------------------------------------------------------- reception


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class ReceptionTests(unittest.TestCase):
    def test_a_disclosure_that_reaches_an_agent_becomes_a_percept_with_disclosed_provenance(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        said = desk.disclose(claim_id, form='filing', audience='public', role_id='cfo',
                             effective_date=FILED, accession='0000000042-24-000046', filed_form='10-Q')
        reader = tc.Store()
        reception = receive(reader, said, receiver=PUBLIC_READER, at=dt.date(2024, 11, 20))
        self.assertEqual(reception.channel, 'filing')
        self.assertAlmostEqual(reception.lag_days, 19.0)
        record = reader._claims[reception.claim_id]
        self.assertEqual(record.claim.predicate, 'cash')
        self.assertEqual(record.evidence[0].locator, '0000000042-24-000046')
        self.assertEqual(provenance_of(reader, reception.claim_id), (('disclosed', ORG, 'filing'),))
        self.assertTrue(disclosed_not_observed(reader, reception.claim_id))

    def test_the_counterparty_and_the_public_hold_the_same_claim_with_different_dates(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        briefing = desk.disclose(claim_id, form='private_briefing', audience='counterparty',
                                 recipients=(COUNTERPARTY,), role_id='cfo',
                                 effective_date=dt.date(2024, 10, 5))
        filing = desk.disclose(claim_id, form='filing', audience='public', role_id='cfo',
                               effective_date=FILED, accession='0000000042-24-000046',
                               filed_form='10-Q')
        insider, public = tc.Store(), tc.Store()
        heard = receive(insider, briefing, receiver=COUNTERPARTY, at=dt.date(2024, 10, 5))
        read = receive(public, filing, receiver=PUBLIC_READER, at=FILED)
        self.assertEqual(heard.claim_id, read.claim_id)            # same proposition, same id
        self.assertNotEqual(heard.heard_at, read.heard_at)         # different dates
        self.assertEqual(heard.channel, 'briefing')
        self.assertEqual(read.channel, 'filing')
        self.assertNotEqual(insider._claims[heard.claim_id].evidence[0],
                            public._claims[read.claim_id].evidence[0])
        self.assertEqual(provenance_of(insider, heard.claim_id), (('disclosed', ORG, 'briefing'),))
        self.assertEqual(provenance_of(public, read.claim_id), (('disclosed', ORG, 'filing'),))
        self.assertEqual(public._claims[read.claim_id].evidence[0].locator, '0000000042-24-000046')
        self.assertLess(heard.heard_at, read.heard_at)             # the counterparty knew first

    def test_an_agent_outside_the_audience_receives_nothing(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        briefing = desk.disclose(claim_id, form='private_briefing', audience='counterparty',
                                 recipients=(COUNTERPARTY,), role_id='cfo',
                                 effective_date=dt.date(2024, 10, 5))
        outsider = tc.Store()
        result = receive(outsider, briefing, receiver=PUBLIC_READER, at=FILED)
        self.assertIsInstance(result, tc.Unknown)
        self.assertEqual(result.reason, 'not_in_the_audience')
        self.assertEqual(len(outsider._claims), 0)

    def test_being_told_is_distinguishable_from_having_observed(self):
        desk = fixture_desk()
        claim_id = held_cash(desk)
        said = desk.disclose(claim_id, form='filing', audience='public', role_id='cfo',
                             effective_date=FILED)
        reader = tc.Store()
        reception = receive(reader, said, receiver=PUBLIC_READER, at=FILED)
        # the same agent later observes the fact independently; both grounds are kept
        reader.apply(tc.Patch((tc.Tell(reader._claims[reception.claim_id].claim,
                                       (tc.Evidence(tc.Ref('dataset:sec_company_assets/normalized@1'),
                                                    FILED, method='published:sec_company_assets'),)),),
                              reader.revision))
        kinds = [row[0] for row in provenance_of(reader, reception.claim_id)]
        self.assertEqual(sorted(kinds), ['disclosed', 'observed'])
        self.assertFalse(disclosed_not_observed(reader, reception.claim_id))


# --------------------------------------------------------------------------- statements


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class StatementTests(unittest.TestCase):
    def test_a_statement_flattens_refs_so_it_can_travel(self):
        claim = tc.Claim(tc.Ref(ORG), 'controls', tc.Ref(COUNTERPARTY))
        self.assertEqual(statement_of(claim), (ORG, 'controls', COUNTERPARTY))

    def test_a_disclosure_needs_something_to_say(self):
        desk = fixture_desk()
        with self.assertRaises(ValueError):
            desk.disclose(form='filing', audience='public', role_id='cfo', effective_date=FILED)
        with self.assertRaises(ValueError):
            desk.disclose(held_cash(desk), form='filing', audience='shareholders', role_id='cfo',
                          effective_date=FILED)

    def test_the_register_survives_without_a_firm(self):
        register = DisclosureRegister()
        said = Disclosure('d1', ORG, (ORG, 'cash', 1.0), 'filing', frozenset({'public'}), 'cfo',
                          FILED, Q_END, claim_id='claim:abc')
        register.record(said)
        self.assertEqual(register.about('claim:abc'), (said,))
        self.assertEqual(register.reaching('regulator'), (said,))
        self.assertEqual(register.reaching('public', at=dt.date(2024, 10, 1)), ())


# --------------------------------------------------------------------------- real data


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
@unittest.skipUnless(INDEX.is_file(), 'No local unified index; skipping the real-data smoke test')
class RealFilingIndexTests(unittest.TestCase):
    """One real issuer's real filing history: forms, accession numbers, and both dates."""

    def test_filings_carry_real_forms_accessions_and_two_dates(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.disclosure import filer_category_from_index
        with EvidenceIndex(INDEX) as index:
            filings = filings_from_index(index, WORKED_EXAMPLE, forms=('10-Q', '10-K'))
            category = filer_category_from_index(index, WORKED_EXAMPLE)
        self.assertGreaterEqual(len(filings), 20)
        self.assertTrue(all(f.accession and f.form and f.filing_date for f in filings))
        self.assertTrue(all(f.source and f.source.dataset == 'sec_issuer_reference' for f in filings))
        # the belief/disclosure gap is published: a period end and a filing date on one record
        dated = [f for f in filings if f.report_date is not None]
        self.assertTrue(dated)
        self.assertTrue(all(f.lag_days > 0 for f in dated))
        self.assertEqual(category, 'Large accelerated filer')
        # the known 10-Q for the quarter ended 2026-03-31
        one = next(f for f in dated if str(f.report_date)[:10] == '2026-03-31')
        self.assertEqual(one.accession, '0001489393-26-000028')
        self.assertEqual(str(one.filing_date)[:10], '2026-05-01')
        self.assertAlmostEqual(one.lag_days, 31.0)

    def test_a_real_late_form_4_is_an_unmet_obligation(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.disclosure import filer_category_from_index
        with EvidenceIndex(INDEX) as index:
            category = filer_category_from_index(index, WORKED_EXAMPLE)
            forms = filings_from_index(index, WORKED_EXAMPLE, forms=('4',))
        late = [f for f in forms if f.report_date is not None and f.lag_days > 200]
        self.assertTrue(late, 'expected at least one badly late Form 4 on this issuer')
        filing = min(late, key=lambda f: str(f.filing_date))
        obligation = obligation_for_filing(filing, filer_category=category, org=WORKED_EXAMPLE)
        self.assertEqual(obligation.kind, 'insider_transaction')
        self.assertEqual(obligation.owed_by, 'insider')     # not the issuer's duty, and it says so
        self.assertEqual(obligation.deadline_business_days, 2)
        said = Disclosure('real', WORKED_EXAMPLE, (WORKED_EXAMPLE, 'insider_transaction',
                                                   filing.accession),
                          'filing', frozenset({'public'}), 'general_counsel', filing.filing_date,
                          filing.report_date, accession=filing.accession, filed_form=filing.form)
        finding = compliance(obligation, filing.report_date, said, now=filing.filing_date)
        self.assertEqual(finding.state, 'unmet')
        self.assertIs(finding.compliant, False)
        self.assertGreater(finding.late_days, 200)

    def test_a_periodic_deadline_uses_the_published_filer_category(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.disclosure import filer_category_from_index
        with EvidenceIndex(INDEX) as index:
            category = filer_category_from_index(index, WORKED_EXAMPLE)
            filings = filings_from_index(index, WORKED_EXAMPLE, forms=('10-Q',))
        quarterly = next(f for f in filings if str(f.report_date)[:10] == '2026-03-31')
        obligation = obligation_for_filing(quarterly, filer_category=category, org=WORKED_EXAMPLE)
        self.assertEqual(obligation.deadline_days, 40)      # large accelerated filer
        self.assertEqual(obligation.citation, '17 CFR 240.13a-13')
        # with the category absent the deadline is Unknown, never the middle of the table
        blind = obligation_for_filing(quarterly, filer_category=None, org=WORKED_EXAMPLE)
        self.assertIsNone(blind.deadline_days)
        self.assertEqual(compliance(blind, quarterly.report_date, None,
                                    now=quarterly.filing_date).state, 'unknown')


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
@unittest.skipUnless(INDEX.is_file() and SEC_ASSETS.is_dir(),
                     'No local unified index or sec_company_assets build; skipping the real-data '
                     'smoke test')
class RealDisclosureSmokeTests(unittest.TestCase):
    def test_the_worked_example_shows_a_real_belief_disclosure_gap(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.disclosure import worked_example
        from worldmodel.store import Store
        with EvidenceIndex(INDEX) as index:
            desk, report = worked_example(index=index, catalog=Store(DATA_ROOT, raw_verify='size'),
                                          quarters=4)
        self.assertEqual(report['entity_id'], WORKED_EXAMPLE)
        self.assertEqual(report['filer_category'], 'Large accelerated filer')
        self.assertGreaterEqual(len(report['quarters']), 3)
        for row in report['quarters']:
            self.assertTrue(row['accession'].startswith('0001489393-'))
            self.assertIn(row['form'], ('10-Q', '10-K'))
            self.assertGreater(row['held_days_before_disclosure'], 20)   # held weeks before it was said
            self.assertEqual(row['compliance'].state, 'met')
        # the same claim carries both grounds: published at the filing date, held at the period end
        gaps = desk.gaps(at=dt.date(2026, 12, 31))
        self.assertTrue(all(gap.state == 'disclosed' for gap in gaps if gap.statement[1] == 'cash'))
        # ...and at a date between the period end and the filing date it is held and unsaid
        period_end = dt.date.fromisoformat(report['quarters'][-1]['period_end'])
        mid = period_end + dt.timedelta(days=7)
        undisclosed = desk.undisclosed(at=mid)
        self.assertTrue(any(gap.statement[1] == 'cash' for gap in undisclosed))
        self.assertTrue(any('0001489393' in (d.accession or '') for d in desk.register.items))
        # the seat caveat from ``authorize`` rides on the disclosure: the record places a general
        # counsel in the seat up to a date and says nothing about the day after
        self.assertTrue(all(d.verdict_status == 'holds' for d in desk.register.items))
        self.assertTrue(all(d.holder.startswith('sec:cik:') for d in desk.register.items))
        # the most recent statements fall after the seat's published period of report, so they carry
        # the Unknown about continued tenure rather than assuming it away
        self.assertTrue(any('continued tenure is Unknown' in reason
                            for d in desk.register.items for reason in d.verdict_reasons))
        # a real late Form 4, unmet against a declared two-business-day deadline
        self.assertTrue(report['late_insider_filings'])
        worst = report['late_insider_filings'][0]
        self.assertEqual(worst['finding'].state, 'unmet')
        self.assertTrue(desk.unknown())


if __name__ == '__main__':
    unittest.main()
