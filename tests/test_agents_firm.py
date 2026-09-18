"""Corporate cognition: roles, authority, procedure, institutional memory, principal-agent divergence.

Fixtures are fully declared and deterministic; the real-data smoke tests skip unless this checkout
has the unified index and the published ``sec_company_assets`` dataset. Everything skips unless the
optional ``agents`` extra (tensorcode) is installed, the same way the numpy tests skip on ``fast``.
"""
import datetime as dt
import unittest
from pathlib import Path

from worldmodel.agents import tensorcode_available

HAVE_TC = tensorcode_available()
DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
INDEX = DATA_ROOT / 'world_evidence' / 'index.sqlite'
SEC_ASSETS = DATA_ROOT / 'sec_company_assets' / 'manifests'
NEEDS_TC = 'corporate cognition requires the optional agents extra (tensorcode)'

if HAVE_TC:
    import tensorcode as tc

    from worldmodel.agents import corporate_affect as ca
    from worldmodel.agents.firm import (Aim, CAPITAL_PROCEDURE, Firm, FirmRecords, Option,
                                        STANDARD_DELEGATION, STATUS_QUO, WORKED_EXAMPLE,
                                        charter_from_records, preferred, role_for_title, utility)
    from worldmodel.agents.roles import (Act, ActLog, Authority, CorporateAct, Delegation, Holder,
                                         Procedure, Role, Source, Step, authorize, run_procedure)

ORG = 'sec:cik:0000000042'
AT = dt.date(2024, 11, 1)
FIXTURE = Source('fixture_filings', 'v1', 'rec:charter:1') if HAVE_TC else None


def quarter(end, cash, revenue, costs, capex, filed):
    return ca.Quarter(dt.date.fromisoformat(end), dt.date.fromisoformat(filed), cash, revenue, costs,
                      capex, (Source('fixture_filings', 'v1', 'filing:%s' % end),))


if HAVE_TC:
    #: A cash trajectory that walks into distress, so the viability gradient has a sign to get right.
    QUARTERS = (quarter('2024-03-31', 3.0e9, 1.00e10, 9.60e9, 5.0e8, '2024-05-01'),
                quarter('2024-06-30', 2.4e9, 9.50e9, 9.40e9, 5.2e8, '2024-08-01'),
                quarter('2024-09-30', 1.9e9, 9.00e9, 9.10e9, 5.4e8, '2024-11-01'))

    ROLES = (Role('board', ORG, 'Board of Directors', 'board', sources=(FIXTURE,)),
             Role('cfo', ORG, 'Chief Financial Officer', 'executive', sources=(FIXTURE,)),
             Role('general_counsel', ORG, 'General Counsel', 'executive', sources=(FIXTURE,)),
             Role('unit_olefins', ORG, 'Olefins', 'operating_unit', sources=(FIXTURE,)))

    #: A board is many seats in one role, which is what makes its declared quorum of 3 meaningful.
    HOLDERS = (Holder('sec:cik:1001', 'board', dt.date(2019, 1, 1), None, ('Director',), (FIXTURE,)),
               Holder('sec:cik:1011', 'board', dt.date(2019, 1, 1), None, ('Director',), (FIXTURE,)),
               Holder('sec:cik:1012', 'board', dt.date(2020, 6, 1), None, ('Director',), (FIXTURE,)),
               Holder('sec:cik:1002', 'cfo', dt.date(2021, 1, 1), None, ('EVP & CFO',), (FIXTURE,)),
               Holder('sec:cik:1003', 'general_counsel', dt.date(2020, 1, 1), None, ('EVP & GC',), (FIXTURE,)),
               Holder('sec:cik:1004', 'unit_olefins', dt.date(2022, 1, 1), None, ('EVP, Olefins',), (FIXTURE,)))

    TIES = (ca.Tie('lei:SUBSIDIARY0001', 'directly_consolidated_by', sources=(FIXTURE,)),
            ca.Tie('lei:SUBSIDIARY0001', 'ultimately_consolidated_by', sources=(FIXTURE,)),
            ca.Tie('lei:SUBSIDIARY0002', 'ultimately_consolidated_by', sources=(FIXTURE,)),
            ca.Tie('sec:cik:9001', 'reported_holding', sources=(FIXTURE,)),
            ca.Tie('sec:cik:9002', 'ten_percent_owner', sources=(FIXTURE,)))

    FIRM_AIMS = (Aim('runway_quarters', 'at_least', 4.0, weight=1.0, scale=1.0),
                 Aim('cash_cover', 'at_least', 0.25, weight=0.5, scale=1.0))
    #: A CFO paid on reported margin. Declared, not fitted, and deliberately in tension with the above.
    CFO_AIMS = (Aim('operating_margin', 'maximize', None, weight=1.0, scale=0.01),)

    BUYBACK = Option('buyback', 'return capital now',
                     (('cash_usd', -1.0e9), ('runway_quarters', -2.5), ('cash_cover', -0.12),
                      ('operating_margin', 0.004)), amount=1.0e9)
    RETAIN = Option('retain', 'hold the cash',
                    (('runway_quarters', 0.8), ('cash_cover', 0.05), ('operating_margin', -0.002)), amount=0.0)
    OPTIONS = (BUYBACK, RETAIN, STATUS_QUO)


def fixture_records(**overrides):
    kwargs = {'entity_id': ORG, 'name': 'Fixture Chemicals N.V.', 'quarters': QUARTERS,
              'roles': ROLES, 'holders': HOLDERS, 'ties': TIES,
              'unknown': ('covenant terms: not published in any catalog dataset',)}
    kwargs.update(overrides)
    return FirmRecords(**kwargs)


_DEFAULT = object()


def fixture_firm(*, authority=_DEFAULT, objectives=True, perceive=True, **overrides):
    authority = STANDARD_DELEGATION if authority is _DEFAULT else authority
    records = fixture_records(**overrides)
    firm = Firm(records, charter_from_records(records, authority=authority))
    if perceive:
        for item in firm.quarters:
            firm.perceive(item)
    if objectives:
        firm.set_objective(FIRM_AIMS, at=AT)
        firm.set_objective(CFO_AIMS, role_id='cfo', at=AT)
    return firm


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class RoleAndAuthorityTests(unittest.TestCase):
    def test_each_role_is_its_own_claim_scope(self):
        firm = fixture_firm()
        scopes = {role.scope.id for role in firm.charter.roles.values()}
        self.assertEqual(len(scopes), 4)
        self.assertIn('role:%s:cfo' % ORG, scopes)
        # the firm has no single self scope: what a person keeps in `me` is spread over the roles
        self.assertNotIn(firm.scope, firm.role_scopes())

    def test_an_unregistered_value_is_not_an_act(self):
        firm = fixture_firm()
        verdict = authorize(Act(power='allocate_capital'), role=firm.charter.role('board'),
                            charter=firm.charter, seat_policy='ignore')
        self.assertEqual(verdict.status, 'fails')
        self.assertIn('not a registered action', verdict.reasons)

    def test_a_role_without_the_power_is_refused_and_recorded(self):
        firm = fixture_firm()
        log = ActLog()
        verdict = authorize(CorporateAct(power='allocate_capital', subject=ORG),
                            role=firm.charter.role('cfo'), charter=firm.charter, log=log,
                            seat_policy='ignore')
        self.assertEqual(verdict.status, 'fails')
        self.assertEqual(len(log), 1)
        self.assertEqual(log.refusals[0].status, 'refused')
        self.assertIn('holds no power', log.refusals[0].reasons[0])

    def test_a_spending_limit_binds_and_an_undeclared_one_is_unknown(self):
        firm = fixture_firm()
        over = authorize(CorporateAct(power='propose_capital', subject=ORG, amount=5.0e8),
                         role=firm.charter.role('cfo'), charter=firm.charter, seat_policy='ignore')
        self.assertEqual(over.status, 'fails')
        self.assertIn('exceeds the declared limit', over.reasons[0])
        bare = fixture_firm(authority=None, objectives=False, perceive=False)
        unknown = authorize(CorporateAct(power='propose_capital', subject=ORG, amount=1.0),
                            role=bare.charter.role('cfo'), charter=bare.charter, seat_policy='ignore')
        self.assertEqual(unknown.status, 'unknown')      # silence is not permission and not refusal
        self.assertIn('not declared', unknown.reasons[0])

    def test_delegation_widens_powers_without_editing_the_role(self):
        records = fixture_records()
        charter = charter_from_records(records, authority=STANDARD_DELEGATION,
                                      delegations=(Delegation('board', 'cfo', 'allocate_capital'),))
        self.assertIn('allocate_capital', charter.powers_of('cfo'))
        self.assertNotIn('allocate_capital', charter.role('cfo').authority.powers)

    def test_a_published_observation_window_is_not_an_appointment(self):
        holder = Holder('sec:cik:1002', 'cfo', dt.date(2023, 2, 20), dt.date(2025, 2, 28), (), (FIXTURE,))
        self.assertEqual(holder.seat_status(dt.date(2024, 6, 1)), 'observed')
        self.assertEqual(holder.seat_status(dt.date(2025, 6, 1)), 'unobserved')
        appointed = Holder('sec:cik:1002', 'cfo', dt.date(2023, 2, 20), dt.date(2025, 2, 28), (),
                           (FIXTURE,), basis='appointment')
        self.assertEqual(appointed.seat_status(dt.date(2025, 6, 1)), 'after')
        firm = fixture_firm(holders=(appointed,) + HOLDERS[:1], objectives=False, perceive=False)
        verdict = authorize(CorporateAct(power='propose_capital', subject=ORG),
                            role=firm.charter.role('cfo'), charter=firm.charter,
                            holder=appointed, at=dt.date(2025, 6, 1))
        self.assertEqual(verdict.status, 'fails')
        self.assertIn('had left', verdict.reasons[0])

    def test_seat_policy_can_require_a_positive_observation(self):
        lapsed = Holder('sec:cik:1002', 'cfo', dt.date(2023, 2, 20), dt.date(2025, 2, 28), (), (FIXTURE,))
        strict = authorize(CorporateAct(power='propose_capital', subject=ORG),
                           role=Role('cfo', ORG, 'CFO', 'executive', STANDARD_DELEGATION['cfo']),
                           holder=lapsed, at=dt.date(2025, 6, 1), seat_policy='observed')
        lenient = authorize(CorporateAct(power='propose_capital', subject=ORG),
                            role=Role('cfo', ORG, 'CFO', 'executive', STANDARD_DELEGATION['cfo']),
                            holder=lapsed, at=dt.date(2025, 6, 1))
        self.assertEqual(strict.status, 'fails')
        self.assertEqual(lenient.status, 'holds')
        self.assertIn('continued tenure is Unknown', lenient.reasons[0])


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class ProcedureTests(unittest.TestCase):
    def test_a_decision_is_a_procedure_over_roles_not_one_choose(self):
        firm = fixture_firm()
        decision = firm.decide(CAPITAL_PROCEDURE.id, OPTIONS, at=AT)
        kinds = [outcome.step.kind for outcome in decision.outcome.outcomes]
        actors = [outcome.step.role_id for outcome in decision.outcome.outcomes]
        self.assertEqual(kinds, ['propose', 'review', 'approve'])
        self.assertEqual(actors, ['cfo', 'general_counsel', 'board'])
        self.assertTrue(decision.outcome.completed)
        self.assertEqual(len(firm.log.grants), 3)

    def test_the_proposing_role_sets_the_agenda_under_its_own_objective(self):
        firm = fixture_firm()
        decision = firm.decide(CAPITAL_PROCEDURE.id, OPTIONS, at=AT)
        # the CFO proposes what *its* objective ranks first, which is not what the firm prefers
        self.assertEqual(decision.proposed.id, 'buyback')
        self.assertEqual(decision.firm_preferred.id, 'retain')
        self.assertEqual(decision.chosen.id, 'status_quo')
        self.assertGreater(decision.agenda_cost, 0.0)
        self.assertIn('below its own first preference', decision.agenda_line())

    def test_a_step_without_authority_blocks_everything_downstream(self):
        records = fixture_records()
        authority = dict(STANDARD_DELEGATION)
        authority['cfo'] = Authority(powers=frozenset(), spending_limit=0.0)
        firm = Firm(records, charter_from_records(records, authority=authority))
        for item in firm.quarters:
            firm.perceive(item)
        firm.set_objective(FIRM_AIMS, at=AT)
        decision = firm.decide(CAPITAL_PROCEDURE.id, OPTIONS, at=AT)
        self.assertEqual(decision.outcome.blocked_at, 'propose')
        self.assertIsNone(decision.chosen)
        self.assertEqual(len(decision.outcome.outcomes), 1)     # review and approve never ran
        self.assertIsNone(decision.decided_claim)

    def test_the_decision_is_deterministic_and_repeats_exactly(self):
        first = fixture_firm().decide(CAPITAL_PROCEDURE.id, OPTIONS, at=AT)
        second = fixture_firm().decide(CAPITAL_PROCEDURE.id, OPTIONS, at=AT)
        self.assertEqual(first.chosen.id, second.chosen.id)
        self.assertEqual(first.decided_claim, second.decided_claim)
        self.assertEqual(first.agenda_cost, second.agenda_cost)

    def test_the_decision_explains_down_to_the_filings_it_rested_on(self):
        firm = fixture_firm()
        decision = firm.decide(CAPITAL_PROCEDURE.id, OPTIONS, at=AT)
        lines = firm.explain(decision.decided_claim, depth=3)
        self.assertTrue(lines[0].startswith('%s decided' % ORG))
        self.assertTrue(any('filing:2024-09-30' in line for line in lines))
        self.assertTrue(any('dataset:fixture_filings' in line for line in lines))

    def test_a_board_below_its_declared_quorum_cannot_settle(self):
        thin = [h for h in HOLDERS if h.role_id != 'board'] + [HOLDERS[0]]
        firm = fixture_firm(holders=tuple(thin))
        decision = firm.decide(CAPITAL_PROCEDURE.id, OPTIONS, at=AT)
        self.assertEqual(decision.outcome.blocked_at, 'approve')
        self.assertTrue(any('quorum of 3 not met' in reason
                            for o in decision.outcome.outcomes for reason in o.verdict.reasons))
        self.assertIsNone(decision.chosen)

    def test_concurrence_is_required_when_the_step_declares_it(self):
        records = fixture_records()
        procedure = Procedure('joint', (Step('approve', 'cfo', 'approve', 'propose_capital',
                                             concurring=('unit_olefins',)),))
        charter = charter_from_records(records, authority=STANDARD_DELEGATION,
                                      procedures={'joint': procedure})
        outcome = run_procedure(procedure, charter=charter, holders=HOLDERS,
                                act_for=lambda step, state: CorporateAct(power='allocate_capital', subject=ORG),
                                at=AT)
        self.assertFalse(outcome.completed)
        self.assertTrue(any('concurrence unit_olefins' in reason
                            for o in outcome.outcomes for reason in o.verdict.reasons))


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class PrincipalAgentTests(unittest.TestCase):
    def test_divergence_is_detected_and_quantified(self):
        firm = fixture_firm()
        divergences = firm.divergences(OPTIONS, at=AT)
        self.assertEqual([d.role_id for d in divergences], ['cfo'])
        found = divergences[0]
        self.assertEqual(found.role_choice, 'buyback')
        self.assertEqual(found.firm_choice, 'retain')
        self.assertGreater(found.loss, 0.0)
        self.assertIn('cash_cover', found.contested)
        self.assertIn("cfo's objective diverged from the firm's", found.line())

    def test_divergence_is_a_claim_whose_premises_are_both_objective_sets(self):
        firm = fixture_firm()
        found = firm.divergences(OPTIONS, at=AT)[0]
        lines = firm.explain(found.claim_id, depth=3)
        self.assertIn('diverges_from', lines[0])
        self.assertTrue(any("measure='operating_margin'" in line for line in lines))
        self.assertTrue(any("measure='runway_quarters'" in line for line in lines))
        # the divergence claim lives in its own scope, so it is queryable and nothing averages it
        self.assertEqual([r.claim.predicate for r in firm.claims_in(firm.divergence_scope)],
                         ['diverges_from'])

    def test_the_two_objective_sets_stay_in_separate_scopes(self):
        firm = fixture_firm()
        self.assertEqual({aim.measure for aim in firm.aims()}, {'runway_quarters', 'cash_cover'})
        self.assertEqual({aim.measure for aim in firm.aims('cfo')}, {'operating_margin'})
        self.assertFalse(set(firm.aim_claims()) & set(firm.aim_claims('cfo')))

    def test_no_divergence_when_the_role_objective_agrees(self):
        firm = fixture_firm(objectives=False)
        firm.set_objective(FIRM_AIMS, at=AT)
        firm.set_objective(FIRM_AIMS, role_id='cfo', at=AT)
        self.assertEqual(firm.divergences(OPTIONS, at=AT), ())

    def test_an_unevaluable_objective_is_not_scored_as_zero(self):
        firm = fixture_firm(objectives=False)
        firm.set_objective((Aim('emissions_intensity', 'minimize'),), at=AT)
        self.assertIsNone(utility(firm.aims(), BUYBACK, firm.measures()))
        self.assertEqual(preferred(firm.aims(), OPTIONS, firm.measures()), (None, None))


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class InstitutionalMemoryTests(unittest.TestCase):
    def test_a_departure_forgets_the_person_and_keeps_the_firm(self):
        firm = fixture_firm()
        policy = firm.adopt_commitment('policy', ('dividend', 'maintained'), source=FIXTURE, at=AT)
        firm.remember_personally('sec:cik:1002', (('believes', ('buyback_is_safe', True)),
                                                  ('trusts', 'sec:cik:9001')))
        before_firm = {r.id for r in firm.claims_in(firm.scope)}
        before_office = {r.id for r in firm.claims_in(firm.charter.role('cfo').scope)}
        succession = firm.vacate('cfo', at=dt.date(2025, 2, 28))
        after_firm = {r.id for r in firm.claims_in(firm.scope)}
        after_office = {r.id for r in firm.claims_in(firm.charter.role('cfo').scope)}
        self.assertEqual(len(succession.forgotten), 2)
        self.assertTrue(before_firm.issubset(after_firm))       # nothing of the firm was lost
        self.assertEqual(before_office, after_office)           # the office kept its mandate
        self.assertIn(policy, after_firm)
        self.assertEqual(firm.mind.claims(scope=tc.Ref('holder:sec:cik:1002')), [])

    def test_the_firm_remembers_that_the_office_changed_hands(self):
        firm = fixture_firm()
        succession = firm.vacate('cfo', at=dt.date(2025, 2, 28))
        record = firm.mind.claim(succession.precedent_claim)
        self.assertEqual(record.claim.predicate, 'precedent')
        self.assertEqual(record.claim.object[:3], ('role_vacated', 'cfo', 'sec:cik:1002'))
        self.assertEqual(record.claim.scope, firm.scope)

    def test_derivations_resting_only_on_the_departing_holder_are_withdrawn(self):
        firm = fixture_firm()
        personal = firm.remember_personally('sec:cik:1002', (('believes', ('supplier_is_weak', True)),))
        derived = tc.Claim(firm.charter.role('cfo').scope, 'appraises_corporate',
                           ('supplier_risk', 'high'), scope=firm.charter.role('cfo').scope)
        firm.mind.apply(tc.Patch((tc.Tell(derived, (tc.Evidence(
            tc.Ref('rule:supplier'), dt.datetime(2024, 11, 1, tzinfo=dt.timezone.utc),
            method='derive@1', derived_from=tuple(personal)),)),), firm.mind.revision))
        succession = firm.vacate('cfo', at=dt.date(2025, 2, 28))
        self.assertIn(derived.id, succession.withdrawn)
        self.assertNotIn(derived.id, {r.id for r in firm.claims_in(firm.charter.role('cfo').scope)})

    def test_an_appointed_departure_makes_later_acts_fail(self):
        firm = fixture_firm()
        firm.vacate('cfo', at=dt.date(2025, 2, 28))
        decision = firm.decide(CAPITAL_PROCEDURE.id, OPTIONS, at=dt.date(2025, 6, 30))
        self.assertEqual(decision.outcome.blocked_at, 'propose')
        self.assertTrue(any('had left cfo' in reason
                            for o in decision.outcome.outcomes for reason in o.verdict.reasons))

    def test_filed_facts_survive_later_filings_and_never_decay(self):
        firm = fixture_firm()
        cash = [r for r in firm.claims_in(firm.scope) if r.claim.predicate == 'cash']
        self.assertEqual(len(cash), 3)                      # one per filed quarter, all still live
        self.assertEqual({r.claim.object for r in cash}, {3.0e9, 2.4e9, 1.9e9})
        self.assertEqual(Firm.decay_policy, 'none')
        self.assertIn('documentary', Firm.decay_rationale)

    def test_only_the_current_view_is_a_snapshot(self):
        firm = fixture_firm()
        current = {r.claim.predicate: r.claim.object for r in firm.claims_in(firm.percept_scope)}
        self.assertAlmostEqual(current['cash_usd'], 1.9e9)   # the latest filing only
        self.assertEqual(len([r for r in firm.claims_in(firm.percept_scope)
                              if r.claim.predicate == 'cash_usd']), 1)


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class CorporateReadingTests(unittest.TestCase):
    def test_the_viability_gradient_is_the_real_cash_trajectory(self):
        firm = fixture_firm()
        reading = firm.read(as_of=AT)
        self.assertTrue(reading.viability_gradient.known)
        self.assertLess(reading.viability_gradient.value, 0.0)     # cash and margin both fell
        self.assertIn('both as filed', reading.viability_gradient.basis)
        self.assertEqual(reading.regime, 'distressed')

    def test_a_gradient_needs_two_filed_quarters(self):
        firm = fixture_firm(quarters=QUARTERS[:1])
        self.assertFalse(firm.read().viability_gradient.known)
        self.assertIn('needs two', firm.read().viability_gradient.basis)

    def test_arousal_counts_commitment_revisions_not_percepts(self):
        firm = fixture_firm(objectives=False, perceive=False)
        firm.adopt_commitment('policy', ('capex', 'held flat'), source=FIXTURE, at=AT)
        for item in firm.quarters:                                 # three filings, many new claims
            firm.perceive(item)
        quiet = firm.read(as_of=AT).commitment_revision_rate
        self.assertEqual(quiet.value, 0.0)                         # no commitment was revised
        firm.adopt_commitment('guidance', ('revenue', 'lowered'), source=FIXTURE, at=AT)
        self.assertGreater(firm.read(as_of=AT).commitment_revision_rate.value, 0.0)

    def test_a_withdrawn_commitment_is_retracted_and_not_forgotten(self):
        firm = fixture_firm()
        policy = firm.adopt_commitment('policy', ('dividend', 'maintained'), source=FIXTURE, at=AT)
        firm.withdraw_commitment(policy, 'superseded by the 2025 policy')
        self.assertNotIn(policy, {r.id for r in firm.claims_in(firm.scope)})
        record = firm.mind.claim(policy)
        self.assertIsNotNone(record.retracted)               # the history is kept, not erased
        self.assertEqual(record.retracted.reason, 'superseded by the 2025 policy')
        self.assertGreater(firm.read(as_of=AT).commitment_revision_rate.value, 0.0)

    def test_liquidity_pressure_reports_that_no_covenant_is_published(self):
        firm = fixture_firm()
        reading = firm.read(as_of=AT)
        self.assertTrue(reading.liquidity_pressure.known)
        self.assertFalse(reading.covenant_proximity.known)
        self.assertIn('no covenant terms declared', reading.covenant_proximity.basis)

    def test_a_declared_covenant_moves_the_pressure_reading(self):
        firm = fixture_firm()
        tight = Firm(fixture_records(), firm.charter,
                     covenants=(ca.Covenant('leverage', 'cash_cover', 0.30, 'at_least', (FIXTURE,)),))
        self.assertGreater(tight.read().liquidity_pressure.value, firm.read().liquidity_pressure.value)
        self.assertTrue(tight.read().covenant_proximity.known)

    def test_exposure_distinguishes_nothing_found_from_not_looked(self):
        firm = fixture_firm()
        clean = firm.read(as_of=AT).exposure
        self.assertEqual(clean.value, 0.0)
        self.assertIn('found 0 live item(s)', clean.basis)
        firm.mind.apply(tc.Patch((tc.Tell(
            tc.Claim(firm.ref, 'enforcement_action', ('epa', 'consent decree'), scope=firm.scope),
            (FIXTURE.evidence(AT),)),), firm.mind.revision))
        self.assertGreater(firm.read(as_of=AT).exposure.value, 0.0)

    def test_coupling_takes_the_strongest_tie_per_counterparty(self):
        firm = fixture_firm()
        reading = firm.read(as_of=AT)
        self.assertEqual(reading.coupling['lei:SUBSIDIARY0001'],
                         ca.COUPLING_WEIGHTS['ultimately_consolidated_by'])
        self.assertEqual(len(reading.coupling), 4)          # five ties, four counterparties
        self.assertAlmostEqual(reading.group_self_coupling.value,
                               2.0 / (2.0 + ca.COUPLING_WEIGHTS['reported_holding']
                                      + ca.COUPLING_WEIGHTS['ten_percent_owner']), places=5)

    def test_adopting_an_objective_makes_the_appraisal_rules_fire(self):
        firm = fixture_firm(objectives=False)
        self.assertEqual(firm.mind.claims(predicate='appraises_corporate'), [])
        firm.set_objective(FIRM_AIMS, at=AT)          # a new premise, so the firm reasons about it
        appraisals = firm.mind.claims(predicate='appraises_corporate')
        tags = {r.claim.object[0] for r in appraisals}
        self.assertIn('liquidity_pressure', tags)      # the CFO sees the runway shortfall
        self.assertIn('escalated', tags)               # and the board hears it
        scopes = {r.claim.scope.id for r in appraisals}
        self.assertEqual(scopes, {'role:%s:cfo' % ORG, 'role:%s:board' % ORG})

    def test_integration_counts_only_derivations_that_span_two_role_scopes(self):
        firm = fixture_firm()
        reading = firm.read(as_of=AT)
        self.assertTrue(reading.cross_role_integration.known)
        self.assertGreater(reading.cross_role_integration.value, 0.0)
        self.assertLess(reading.cross_role_integration.value, 1.0)   # not everything crosses
        # the escalation is the crossing: its premises are a CFO appraisal and the board's office
        escalated = [r for r in firm.mind.claims(predicate='appraises_corporate')
                     if r.claim.object[0] == 'escalated']
        premises = escalated[0].evidence[0].derived_from
        self.assertEqual({firm.mind.claim(p).claim.scope.id for p in premises},
                         {'role:%s:cfo' % ORG, 'role:%s:board' % ORG})

    def test_integration_is_indexed_by_role_and_excludes_projections(self):
        firm = fixture_firm()
        firm.decide(CAPITAL_PROCEDURE.id, OPTIONS, at=AT)
        reading = firm.read(as_of=AT)
        self.assertTrue(reading.cross_role_integration.known)
        self.assertGreater(reading.cross_role_integration.value, 0.0)
        self.assertGreater(reading.contingency_weight.value, 0.0)
        self.assertIn('hypothetical scope', reading.contingency_weight.basis)

    def test_no_reading_is_a_person_reading(self):
        reading = fixture_firm().read(as_of=AT)
        for name in reading.values():
            self.assertNotIn(name, ('valence', 'arousal', 'fear', 'shame', 'attachment'))
        self.assertFalse(hasattr(reading, 'motif'))
        self.assertEqual(set(ca.CorporateReading.PERSON_ANALOGUE) & set(reading.values()),
                         {'viability_gradient', 'commitment_revision_rate', 'liquidity_pressure',
                          'covenant_proximity', 'exposure', 'cross_role_integration',
                          'contingency_weight', 'group_self_coupling'})

    def test_ascription_is_agentive_and_not_phenomenal(self):
        ascription = fixture_firm().ascription()
        self.assertEqual(ascription.template, 'agent')
        self.assertGreater(ascription.agency, 0.5)
        self.assertEqual(ascription.phenomenality, 0.0)
        self.assertIsNone(ascription.harm_constraint)
        self.assertEqual(ascription.to_json()['harm_constraint'], None)

    def test_a_firm_with_no_filings_reads_unknown_not_zero(self):
        firm = fixture_firm(quarters=(), objectives=False, perceive=False)
        reading = firm.read()
        self.assertIsNone(reading.viability.value)
        self.assertIsNone(reading.viability_gradient.value)


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class SoftViabilityTests(unittest.TestCase):
    def test_the_firm_uses_the_same_soft_minimum_as_a_person(self):
        from worldmodel.agents.affect import soft_viability
        boundaries = ca.DistressBoundaries()
        parts = ca.margins(QUARTERS[-1], boundaries)
        self.assertEqual(round(soft_viability(parts, sharpness=boundaries.sharpness), 6),
                         ca.viability(QUARTERS[-1], boundaries).value)
        self.assertEqual(set(parts), {'operating_margin', 'free_cash_margin', 'cash_cover', 'runway'})

    def test_a_firm_that_is_not_burning_cash_has_a_capped_runway(self):
        healthy = quarter('2025-03-31', 3.0e9, 1.0e10, 8.0e9, 5.0e8, '2025-05-01')
        self.assertEqual(ca.cash_runway(healthy), ca.DistressBoundaries().runway_cap)
        self.assertEqual(ca.regime(ca.viability(healthy)), 'solvent')

    def test_an_incomplete_filing_reads_unknown(self):
        partial = ca.Quarter(dt.date(2025, 3, 31), dt.date(2025, 5, 1), cash=1.0e9)
        self.assertIsNone(ca.cash_runway(partial))
        self.assertIsNone(ca.viability_gradient(partial, QUARTERS[-1]).value)


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class TitleMappingTests(unittest.TestCase):
    def test_published_titles_map_to_declared_seats(self):
        self.assertEqual(role_for_title('Chief Executive Officer', ('Officer',)), ('ceo', 'executive'))
        self.assertEqual(role_for_title('EVP & Chief Financial Officer', ('Officer',)), ('cfo', 'executive'))
        self.assertEqual(role_for_title('EVP & CFO', ('Officer',)), ('cfo', 'executive'))
        self.assertEqual(role_for_title('EVP and General Counsel', ('Officer',)),
                         ('general_counsel', 'executive'))
        self.assertEqual(role_for_title(None, ('Director',)), ('board', 'board'))
        self.assertEqual(role_for_title('EVP, Adv Polymer Solutions', ('Officer',)),
                         ('unit_evp_adv_polymer_solutions', 'operating_unit'))
        self.assertIsNone(role_for_title(None, ('TenPercentOwner',)))    # an owner is not a seat


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
@unittest.skipUnless(INDEX.is_file() and SEC_ASSETS.is_dir(),
                     'No local unified index or sec_company_assets build; skipping the real-data smoke test')
class RealDataSmokeTests(unittest.TestCase):
    """One real issuer: LyondellBasell, whose filings, seats and ownership are all published here."""

    def test_the_worked_example_runs_on_published_records(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.firm import worked_example
        from worldmodel.store import Store
        with EvidenceIndex(INDEX) as index:
            firm, decision = worked_example(index=index, catalog=Store(DATA_ROOT, raw_verify='size'))
        self.assertEqual(firm.entity_id, WORKED_EXAMPLE)
        self.assertIn('LyondellBasell', firm.name)
        # real quarters, each citing the accession number it was filed under
        self.assertGreaterEqual(len(firm.quarters), 4)
        self.assertTrue(all(q.complete for q in firm.quarters))
        self.assertTrue(all(q.sources and q.sources[0].dataset == 'sec_company_assets'
                            for q in firm.quarters))
        self.assertTrue(any('0001489393-26' in s.record_id
                            for q in firm.quarters for s in q.sources))
        # real seats from real insider filings, including the board and the CFO
        self.assertIn('board', firm.charter.roles)
        self.assertIn('cfo', firm.charter.roles)
        self.assertTrue(any(role.tier == 'operating_unit' for role in firm.charter.roles.values()))
        cfos = sorted((h.person, h.since) for h in firm.holders if h.role_id == 'cfo')
        self.assertGreaterEqual(len(cfos), 2)               # the seat changed hands on the record
        self.assertTrue(all(h.sources for h in firm.holders))
        # real ownership: the consolidated group dominates the coupling field
        reading = firm.read(as_of=decision.at)
        self.assertTrue(reading.group_self_coupling.known)
        self.assertGreater(reading.group_self_coupling.value, 0.5)
        self.assertTrue(reading.viability_gradient.known)
        self.assertIn(reading.regime, ('solvent', 'pressured', 'distressed'))
        # the decision is a procedure, it settled, and it explains to a filing record id
        self.assertEqual([o.step.role_id for o in decision.outcome.outcomes],
                         ['cfo', 'general_counsel', 'board'])
        self.assertTrue(decision.settled)
        lines = firm.explain(decision.decided_claim, depth=3)
        self.assertTrue(any('sec_company_assets' in line for line in lines))
        self.assertTrue(any('secfacts:0001489393' in line for line in lines))
        # the divergence is detectable and names the CFO
        self.assertEqual([d.role_id for d in decision.divergences], ['cfo'])
        # what the catalog does not publish stays Unknown, with the reason
        self.assertTrue(any('covenant' in item for item in firm.unknown()))
        self.assertTrue(any('bylaws' in item for item in firm.unknown()))
        self.assertFalse(firm.read(as_of=decision.at).covenant_proximity.known)

    def test_seeding_is_idempotent_on_the_same_records(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.firm import firm_records
        with EvidenceIndex(INDEX) as index:
            first = firm_records(WORKED_EXAMPLE, index=index)
            second = firm_records(WORKED_EXAMPLE, index=index)
        self.assertEqual([r.id for r in first.roles], [r.id for r in second.roles])
        self.assertEqual([(h.person, h.role_id) for h in first.holders],
                         [(h.person, h.role_id) for h in second.holders])
        self.assertEqual([(t.counterparty, t.relation) for t in first.ties],
                         [(t.counterparty, t.relation) for t in second.ties])


if __name__ == '__main__':
    unittest.main()
