"""Decision contracts: the JSON schema, the stdlib validator and the kernel cross-checks."""
from copy import deepcopy
import json
import unittest

from worldmodel.decision.contract import (ContractError, action_cost, aggregate, criterion_result, load_schema, schema_errors,
                                          validate_contract)
from test_decision_support import EXAMPLES, economy_contract, monetary_contract


def errors_of(contract, **options):
    try:
        validate_contract(contract, **options)
    except ContractError as error:
        return error.errors
    return []


class SchemaTests(unittest.TestCase):
    def test_every_example_contract_validates_against_schema_and_kernel(self):
        for path in sorted(EXAMPLES.glob('*.json')):
            body = json.loads(path.read_text())
            if body.get('schema') != 'worldmodel.decision_contract/1':
                continue
            with self.subTest(path.name):
                normalized = validate_contract(body)
                self.assertEqual(normalized['id'], body['id'])
                self.assertIn('parameter_uncertainty', normalized)

    def test_the_schema_is_a_plain_json_schema_document(self):
        schema = load_schema()
        self.assertEqual(schema['$id'], 'worldmodel.decision_contract/1')
        self.assertIn('https://json-schema.org/draft/2020-12/schema', schema['$schema'])
        self.assertEqual(schema_errors(monetary_contract(), schema), [])

    def test_structural_errors_are_all_reported(self):
        contract = monetary_contract()
        del contract['horizon']
        contract['surprise'] = 1
        contract['mechanisms']['policy_rate_reaction']['evidence']['status'] = 'probably fine'
        contract['id'] = 'Not An Id'
        found = errors_of(contract)
        self.assertTrue(any("missing required field 'horizon'" in e for e in found))
        self.assertTrue(any("unknown field 'surprise'" in e for e in found))
        self.assertTrue(any('evidence.status: must be one of' in e for e in found))
        self.assertTrue(any('$.id: does not match' in e for e in found))

    def test_unsupported_schema_keywords_raise_instead_of_being_ignored(self):
        with self.assertRaisesRegex(ValueError, 'not supported'):
            schema_errors({}, {'type': 'object', 'oneOf': []})


class SemanticTests(unittest.TestCase):
    def test_objective_weights_must_be_explicit_and_sum_to_one(self):
        contract = monetary_contract()
        contract['objectives'][0]['weight'] = 0.5
        self.assertTrue(any('sum to 1' in e for e in errors_of(contract)))

    def test_a_validated_claim_must_cite_a_report_and_component(self):
        contract = monetary_contract()
        contract['mechanisms']['policy_rate_reaction']['evidence'] = {'status': 'validated'}
        found = errors_of(contract)
        self.assertTrue(any('must cite report_id' in e for e in found))
        self.assertTrue(any('must cite component' in e for e in found))

    def test_an_assumed_mechanism_cannot_cite_a_report(self):
        contract = monetary_contract()
        contract['mechanisms']['policy_rate_reaction']['evidence']['report_id'] = 'a' * 64
        self.assertTrue(any('assumed mechanism cites no report' in e for e in errors_of(contract)))

    def test_an_estimated_claim_needs_a_report_or_a_note(self):
        contract = monetary_contract()
        contract['mechanisms']['policy_rate_reaction']['evidence'] = {'status': 'estimated but not validated'}
        self.assertTrue(any('must cite a report_id or explain' in e for e in errors_of(contract, kernel_check=False)))

    def test_a_costly_action_needs_a_budget(self):
        contract = monetary_contract()
        del contract['budget']
        self.assertTrue(any('needs a declared budget' in e for e in errors_of(contract)))

    def test_nominal_values_lie_inside_their_ranges(self):
        contract = monetary_contract()
        contract['uncertain_inputs']['inflation_end']['nominal'] = 99
        self.assertTrue(any('nominal must lie within' in e for e in errors_of(contract)))

    def test_budget_id_is_reserved(self):
        contract = monetary_contract()
        contract['success_criteria'][0]['id'] = 'budget'
        self.assertTrue(any('reserved' in e for e in errors_of(contract)))


class KernelCheckTests(unittest.TestCase):
    def test_every_mechanism_the_kernel_runs_needs_an_evidence_status(self):
        contract = economy_contract()
        del contract['mechanisms']['price_adjustment']
        for name in ('price_adjustment', 'unmet_demand_response'):
            del contract['assumed_parameters'][name]
        self.assertTrue(any('runs exactly the mechanisms' in e for e in errors_of(contract)))

    def test_metrics_actions_and_units_must_match_the_kernel(self):
        contract = monetary_contract()
        contract['objectives'][0]['metric'] = 'happiness'
        contract['actions']['reserve_margin']['maximum'] = 50
        contract['uncertain_inputs']['inflation_end']['unit'] = 'fraction'
        found = errors_of(contract)
        self.assertTrue(any("unknown metric 'happiness'" in e for e in found))
        self.assertTrue(any("bounds must lie within the kernel" in e for e in found))
        self.assertTrue(any('uncertain input inflation_end' in e for e in found))

    def test_horizon_step_must_match_the_kernel(self):
        contract = monetary_contract()
        contract['horizon']['step'] = 'day'
        self.assertTrue(any('steps by quarter' in e for e in errors_of(contract)))

    def test_an_assumed_policy_rule_needs_every_assumed_parameter(self):
        contract = monetary_contract()
        del contract['assumed_parameters']['rho']
        self.assertTrue(any("needs assumed parameters ['rho']" in e for e in errors_of(contract)))

    def test_economy_requires_unpaid_interest_to_be_visible(self):
        contract = economy_contract()
        contract['environment']['config']['initial_state']['mechanisms']['interest']['insufficient'] = 'defer'
        self.assertTrue(any('insufficient must be "bankrupt"' in e for e in errors_of(contract)))

    def test_unknown_kernel(self):
        contract = monetary_contract()
        contract['environment']['kernel'] = 'oracle'
        with self.assertRaisesRegex(ValueError, 'Unknown decision kernel'):
            validate_contract(contract)


class ArithmeticTests(unittest.TestCase):
    def test_action_cost_bases(self):
        contract = validate_contract(monetary_contract())
        self.assertEqual(action_cost(contract, {'reserve_margin': -1.0}), 0.0)
        self.assertEqual(action_cost(contract, {'reserve_margin': 0.5}), 0.5)
        absolute = deepcopy(contract)
        absolute['actions']['reserve_margin']['cost_basis'] = 'absolute'
        self.assertEqual(action_cost(absolute, {'reserve_margin': -1.0}), 1.0)

    def test_aggregates_and_severity_signs(self):
        self.assertEqual([aggregate([1, 3, 2], a) for a in ('sum', 'mean', 'max', 'min', 'final')], [6, 2, 3, 1, 2])
        passed = criterion_result({'id': 'x', 'metric': 'm', 'aggregate': 'max', 'operator': 'lte', 'value': 1.0, 'scale': 0.5}, 0.5)
        failed = criterion_result({'id': 'x', 'metric': 'm', 'aggregate': 'max', 'operator': 'gte', 'value': 1.0, 'scale': 0.5}, 0.5)
        self.assertTrue(passed['passed'])
        self.assertAlmostEqual(passed['severity'], -1.0)
        self.assertFalse(failed['passed'])
        self.assertAlmostEqual(failed['severity'], 1.0)


if __name__ == '__main__':
    unittest.main()
