"""The four decision commands, through the real CLI parser."""
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.cli import execute, parser
from test_decision_support import EXAMPLES, economy_contract, monetary_contract


def run(*argv):
    return execute(parser().parse_args(list(argv)))


class DecisionCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.contract = self.root / 'contract.json'
        self.contract.write_text(json.dumps(monetary_contract()))
        self.economy = self.root / 'economy.json'
        self.economy.write_text(json.dumps(economy_contract()))
        self.policies = self.root / 'policies.json'
        self.policies.write_text(json.dumps({'margin': {'kind': 'constant', 'actions': {'reserve_margin': 0.5}},
                                             'none': {'kind': 'constant', 'actions': {'reserve_margin': 0.0}}}))
        self.data = ['--data-root', str(self.root / 'data')]

    def tearDown(self):
        self.temp.cleanup()

    def test_commands_are_registered_on_the_main_parser(self):
        extra = {'decision-validate': [], 'decision-rollout': ['--policies', str(self.policies)],
                 'decision-stress': ['--policies', str(self.policies)], 'decision-optimize': ['--request', str(self.policies)]}
        for command, arguments in extra.items():
            with self.subTest(command):
                self.assertEqual(parser().parse_args([command, str(self.contract)] + arguments).command, command)

    def test_validate_without_a_store_checks_structure_and_confirms_nothing(self):
        result = run('decision-validate', str(self.contract), '--no-store')
        self.assertTrue(result['valid'])
        self.assertIsNone(result['evidence']['index'])
        self.assertEqual(result['kernel']['id'], 'monetary_policy_rate')
        self.assertIn('nothing was bound or confirmed', result['note'])

    def test_validate_reports_the_kernel_the_scenario_space_and_the_label(self):
        result = run(*self.data, 'decision-validate', str(self.contract))
        self.assertEqual(result['evidence']['index']['source'], 'store')
        self.assertTrue(any(d['kind'] == 'shock' for d in result['scenario_dimensions']))
        self.assertEqual(result['label']['label'], 'rests_on_unvalidated_mechanisms')
        self.assertTrue(result['does_not_establish'])

    def test_rollout_writes_records_and_a_ranked_recommendation(self):
        out = self.root / 'rollout.json'
        result = run(*self.data, 'decision-rollout', str(self.contract), '--policies', str(self.policies), '--out', str(out))
        self.assertEqual(sorted(result['rollouts']), ['margin', 'none'])
        self.assertIn(result['recommendation']['recommended'], ('margin', 'none'))
        self.assertEqual(json.loads(out.read_text())['contract'], 'test-treasury')

    def test_stress_runs_the_search_and_writes_its_report(self):
        out = self.root / 'stress.json'
        result = run(*self.data, 'decision-stress', str(self.contract), '--policies', str(self.policies),
                     '--evaluations', '40', '--seed', '2', '--out', str(out))
        self.assertEqual(result['schema'], 'worldmodel.decision_fragility/1')
        self.assertEqual(sorted(result['policies']), ['margin', 'none'])
        self.assertEqual(json.loads(out.read_text())['claim_type'], 'fragility')

    def test_optimize_returns_a_refusal_rather_than_a_policy(self):
        request = self.root / 'request.json'
        request.write_text(json.dumps({'actions': [{'reserve_margin': 0.0}], 'encoder_bins': {'policy_rate': [2.0]},
                                       'training_seeds': {'start': 0, 'count': 2},
                                       'evaluation_seeds': {'start': 100, 'count': 2}}))
        result = run(*self.data, 'decision-optimize', str(self.economy), '--request', str(request), '--no-store')
        self.assertTrue(result['refused'])
        self.assertFalse(result['gate']['allowed'])
        self.assertIn('No policy was optimized', result['statement'])

    def test_the_example_contracts_validate_structurally_through_the_cli(self):
        for path in sorted(EXAMPLES.glob('*.json')):
            if json.loads(path.read_text()).get('schema') != 'worldmodel.decision_contract/1':
                continue
            with self.subTest(path.name):
                self.assertTrue(run('decision-validate', str(path), '--no-store')['valid'])


if __name__ == '__main__':
    unittest.main()
