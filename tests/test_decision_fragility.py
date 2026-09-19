"""Adversarial scenario search: failure regions, refinement, and what the report refuses to claim."""
import json
import unittest

from worldmodel.decision.compile import compile_contract, policies_from
from worldmodel.decision.fragility import failure_regions, stress_test
from test_decision_support import EXAMPLES, economy_contract, monetary_contract

UNDER = {'kind': 'constant', 'actions': {'reserve_margin': -2.0}}
WIDE = {'kind': 'constant', 'actions': {'reserve_margin': 3.0}}
MARGIN = {'kind': 'constant', 'actions': {'reserve_margin': 0.5}}


def narrow(**ranges):
    """The test contract with every dimension pinned except the ones named."""
    contract = monetary_contract()
    contract['parameter_uncertainty'] = {'shock_quantile_bound': 0.0}
    for name, spec in contract['uncertain_inputs'].items():
        low, high = ranges.get(name, (spec['nominal'], spec['nominal']))
        spec.update(minimum=low, maximum=high, nominal=min(max(spec['nominal'], low), high))
    for spec in contract['assumed_parameters'].values():
        spec.update(minimum=spec['nominal'], maximum=spec['nominal'])
    return contract


class SearchTests(unittest.TestCase):
    def setUp(self):
        self.compiled = compile_contract(monetary_contract())
        self.policies = policies_from({'under': UNDER, 'wide': WIDE}, self.compiled.contract)

    def test_the_search_is_seeded_and_reproducible(self):
        first = stress_test(self.compiled, self.policies, evaluations=60, seed=5)
        second = stress_test(self.compiled, self.policies, evaluations=60, seed=5)
        self.assertEqual(json.dumps(first['policies'], sort_keys=True), json.dumps(second['policies'], sort_keys=True))
        other = stress_test(self.compiled, self.policies, evaluations=60, seed=6)
        self.assertNotEqual(json.dumps(first['policies'], sort_keys=True), json.dumps(other['policies'], sort_keys=True))

    def test_the_budget_is_spent_and_bounded(self):
        report = stress_test(self.compiled, self.policies, evaluations=80, refine_share=0.5, seed=1)
        self.assertEqual(report['search']['random_draws'], 39)
        for policy in report['policies'].values():
            self.assertLessEqual(policy['refinement']['evaluations'], 40)

    def test_a_policy_that_must_fail_fails_and_a_generous_one_does_not(self):
        compiled = compile_contract(narrow(inflation_end=(0.0, 3.0)))
        policies = policies_from({'under': UNDER, 'wide': {'kind': 'constant', 'actions': {'reserve_margin': 0.6}}},
                                 compiled.contract)
        report = stress_test(compiled, policies, evaluations=120, seed=2)
        self.assertEqual(report['policies']['under']['random']['failure_rate'], 1.0)
        self.assertGreater(report['policies']['under']['refinement']['worst_severity'], 0)
        self.assertLess(report['policies']['wide']['random']['failure_rate'], 1.0)
        self.assertIn('no failing scenario', report['policies']['wide']['claim'])

    def test_refinement_can_find_a_failure_the_random_draws_missed(self):
        compiled = compile_contract(narrow(inflation_end=(0.0, 9.0)))
        policies = policies_from({'margin': MARGIN}, compiled.contract)
        random_only = stress_test(compiled, policies, evaluations=40, refine_share=0.0, seed=3)['policies']['margin']
        refined = stress_test(compiled, policies, evaluations=40, refine_share=0.9, seed=3)['policies']['margin']
        self.assertEqual(random_only['random']['failures'], 0)          # 39 uniform draws find nothing
        self.assertTrue(refined['refinement']['found_failure'])         # the pattern search does

    def test_the_claim_names_the_driving_dimension_and_its_direction(self):
        compiled = compile_contract(narrow(inflation_end=(0.0, 12.0)))
        policies = policies_from({'margin': MARGIN}, compiled.contract)
        report = stress_test(compiled, policies, evaluations=200, refine_share=0.2, seed=4)
        regions = report['policies']['margin']['regions']
        self.assertEqual(regions['drivers'][:1], ['input.inflation_end'])
        self.assertEqual(regions['driver_directions']['input.inflation_end'], 'higher')
        rates = [row['failure_rate'] for row in regions['per_dimension'][0]['bins'] if row['samples']]
        self.assertGreater(rates[-1], rates[0])

    def test_the_report_states_that_it_is_not_an_optimality_claim(self):
        report = stress_test(self.compiled, self.policies, evaluations=40, seed=0)
        self.assertIn('not an optimality claim', report['not_an_optimality_claim'].lower())
        self.assertIn('not the chance of failure', report['failure_rates_are_not_probabilities'])
        self.assertEqual(report['claim_type'], 'fragility')
        self.assertEqual(report['label']['label'], 'rests_on_unvalidated_mechanisms')
        self.assertTrue(any('optimal only' in line or 'optimal, robust' in line for line in [report['label']['text']]))

    def test_failure_counts_are_reported_per_criterion(self):
        report = stress_test(self.compiled, self.policies, evaluations=60, seed=7)
        counts = report['policies']['under']['regions']['failures_by_criterion']
        self.assertEqual(sorted(counts), ['worst_shortfall'])

    def test_regions_handle_the_no_failure_and_all_failure_cases(self):
        samples = [{'point': {'x': i}, 'success': True, 'failed': []} for i in range(20)]
        dimensions = [{'name': 'x', 'low': 0, 'high': 19, 'nominal': 0, 'kind': 'uncertain_input', 'source': 'test'}]
        regions = failure_regions(samples, dimensions, bins=2, min_bin_samples=2)
        self.assertEqual((regions['failure_rate'], regions['drivers'], regions['worst_cell']), (0.0, [], None))

    def test_a_contract_with_nothing_uncertain_cannot_be_stressed(self):
        contract = narrow()
        contract['uncertain_inputs'] = {}
        contract['environment']['config']['drivers'] = {'source': 'linear_paths'}
        with self.assertRaises(Exception):
            stress_test(compile_contract(contract), self.policies, evaluations=20)


class EconomySearchTests(unittest.TestCase):
    def test_the_economy_example_search_finds_where_the_aggressive_plan_breaks(self):
        compiled = compile_contract(economy_contract())
        policies = policies_from(json.loads((EXAMPLES / 'economy-firm-policies.json').read_text()), compiled.contract)
        report = stress_test(compiled, {'aggressive': policies['aggressive'], 'cautious': policies['cautious']},
                             evaluations=60, seed=11)
        self.assertGreater(report['policies']['aggressive']['random']['failure_rate'],
                           report['policies']['cautious']['random']['failure_rate'])
        self.assertEqual(report['comparison'][0]['policy'], 'cautious')
        self.assertEqual(report['label']['label'], 'rests_on_unvalidated_mechanisms')


if __name__ == '__main__':
    unittest.main()
