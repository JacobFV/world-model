import contextlib
import io
import json
import math
import tempfile
import unittest
from pathlib import Path

from worldmodel.estimation import ObservationSet, ESTIMATORS, estimator_for, load_requirements, Estimate, validate_process
from worldmodel.estimation.data import SeriesRequirement
from worldmodel.estimation.spec import ParameterSpec
from worldmodel.estimation.synthetic import component_dataset
from worldmodel.process_library import default_registry


class RequirementsDocumentTests(unittest.TestCase):
    def test_document_is_consistent_with_estimators_and_registry(self):
        document = load_requirements()
        registered = {p['id'] for p in default_registry().describe()['processes']}
        from worldmodel.estimation import family_components
        self.assertEqual(set(document['components']), set(ESTIMATORS) | set(family_components()))
        for component, spec in document['components'].items():
            expected = 'ModelFamilyEstimator' if component in family_components() else ESTIMATORS[component].__name__
            self.assertEqual(expected, spec['estimator'])
            self.assertIn(spec['process_id'], registered)
            for parameter in spec['parameters']:
                ParameterSpec.from_dict(parameter)
            for series in spec['series']:
                requirement = SeriesRequirement.from_dict(series)
                self.assertEqual(requirement.frequency, spec['frequency'])
                self.assertTrue(series['sources'])
            if spec['conditional_inputs'] and spec['series']:
                self.assertTrue(set(spec['conditional_inputs']) <= {s['name'] for s in spec['series']})
        for process_id, process in document['processes'].items():
            self.assertIn(process_id, registered)
            for component in process['required_components'] + process.get('optional_components', []):
                self.assertIn(component, document['components'])
        self.assertEqual(document['processes']['banking_ledger']['required_components'], [])

    def test_task_named_public_series_are_declared(self):
        text = json.dumps(load_requirements())
        for series in ('CPIAUCSL', 'DFF', 'DPRIME', 'TOTALSL', 'DRCCLACBS', 'PAYEMS', 'LAUS', 'Population Estimates Program',
                       'WCESTUS1', 'DCOILWTICO', 'fdic_bank_financials', 'FAF5'):
            self.assertIn(series, text)

    def test_default_criteria_overrides_are_explicit(self):
        criteria = {c['id']: c for c in estimator_for('demand_price_elasticity').default_criteria()}
        self.assertEqual(criteria['minimum_test_forecasts']['minimum'], 24)
        self.assertEqual(criteria['strong_instrument']['type'], 'estimate_diagnostic')
        self.assertEqual(criteria['beats_persistence_dm']['max_pvalue'], 0.10)


class RecoveryTests(unittest.TestCase):
    """Every component recovers known synthetic parameters within declared tolerances."""

    def check(self, component, seed=1):
        dataset = component_dataset(component, seed=seed)
        estimator = estimator_for(component, options=dataset['options'])
        estimate = estimator.fit(ObservationSet(dataset['records']), cutoff=dataset['cutoff'], vintage_policy='strict')
        for name, (truth, tolerance) in dataset['truth'].items():
            self.assertAlmostEqual(estimate.parameters[name], truth, delta=tolerance, msg=f'{component}.{name}')
        body = estimate.to_dict()
        self.assertEqual(Estimate.from_dict(body).to_dict()['estimate_id'], body['estimate_id'])
        self.assertEqual(body['cutoff'], dataset['cutoff'])
        self.assertFalse(body['causally_identified'])
        for spec in estimator.parameters:
            if spec.maps_to and estimate.parameters.get(spec.name) is not None:
                self.assertIn(spec.maps_to, body['process_parameters'])
        return estimate

    def test_population_growth(self):
        self.check('population_growth_rate')

    def test_inventory_balance(self):
        self.check('inventory_balance')

    def test_cash_balance(self):
        self.check('cash_balance')

    def test_interest_and_deposit_rate_pass_through(self):
        estimate = self.check('interest_pass_through')
        self.assertAlmostEqual(estimate.process_parameters['mechanisms.interest.adjustment_speed'],
                               1 - (1 - estimate.parameters['adjustment_speed_per_month']) ** (1 / 30.4375), places=12)
        self.check('deposit_rate_pass_through')

    def test_default_hazard(self):
        self.check('default_hazard')

    def test_deposit_and_credit_growth(self):
        self.check('deposit_growth')
        estimate = self.check('credit_growth')
        self.assertEqual(estimate.diagnostics['order'], 2)

    def test_demand_elasticity_uses_instrument(self):
        estimate = self.check('demand_price_elasticity')
        self.assertGreater(estimate.diagnostics['first_stage'][0]['partial_f'], 10)

    def test_price_adjustment_energy_purchasing_and_labor(self):
        self.check('price_adjustment')
        self.check('energy_purchasing')
        self.check('labor_demand')

    def test_policy_rule(self):
        self.check('policy_rule')

    def test_field_diffusion_transport(self):
        estimate = self.check('field_diffusion_transport')
        self.assertTrue(estimate.diagnostics['euler_stable'])

    def test_field_conductance_from_existing_solver(self):
        from worldmodel.fields import FieldWorld
        from worldmodel.estimation.synthetic import observation_records, periods
        from datetime import date
        cells = [{'id': 'cell:a', 'measure': 1e6}, {'id': 'cell:b', 'measure': 1e6}, {'id': 'cell:c', 'measure': 2e6}]
        edges = [{'id': 'ab', 'source': 'cell:a', 'target': 'cell:b', 'conductance': 0.3, 'transport_rate': 0},
                 {'id': 'bc', 'source': 'cell:b', 'target': 'cell:c', 'conductance': 0.3, 'transport_rate': 0}]
        config = {'cells': cells, 'edges': edges, 'fields': {'q': {'kind': 'intensive', 'unit': 'kg/m2', 'values': {'cell:a': 100.0, 'cell:b': 10.0, 'cell:c': 0.0}}}}
        options = {'topology': {'cells': cells, 'edges': edges}, 'include_decay': False, 'include_source': False}
        estimator = estimator_for('field_diffusion_transport', options=options)
        columns = {c['id']: [] for c in cells}
        for day in range(40):  # Pure diffusion from an initial gradient; no unmodeled injections between observations.
            for cell in columns:
                columns[cell].append(config['fields']['q']['values'][cell])
            config = FieldWorld(config).evolve({'duration_seconds': 86400, 'step_seconds': 3600})['state']
        records = observation_records(estimator, columns, periods('daily', 40, date(2024, 1, 1)))
        estimate = estimator.fit(ObservationSet(records), cutoff='2024-03-01')
        self.assertAlmostEqual(estimate.parameters['conductance'], 0.3, delta=0.03)

    def test_gravity(self):
        self.check('bilateral_flow_gravity')

    def test_insufficient_point_in_time_data_is_explicit(self):
        dataset = component_dataset('interest_pass_through', seed=1)
        with self.assertRaisesRegex(ValueError, 'aligned observations'):
            estimator_for('interest_pass_through').fit(ObservationSet(dataset['records']), cutoff='2001-06-30')


class ValidationFamilyTests(unittest.TestCase):
    def test_conditional_and_cross_sectional_validation_paths(self):
        for component in ('energy_purchasing', 'bilateral_flow_gravity'):
            dataset = component_dataset(component, seed=1)
            estimator = estimator_for(component, options=dataset['options'])
            report = validate_process(estimator, ObservationSet(dataset['records']), train_end=dataset['train_end'],
                                      validation_end=dataset['validation_end'], cutoff=dataset['cutoff'])
            self.assertGreater(report['test']['metrics']['model']['count'], 20)
            self.assertIn('persistence', report['test']['diebold_mariano'])
            self.assertEqual(report['test']['leakage_audit']['violations'], 0)
            self.assertTrue(report['validated'], [r for r in report['acceptance']['results'] if not r['passed']])


class ProcessHookTests(unittest.TestCase):
    def test_inventory_and_cash_flow_hooks_default_to_existing_behavior(self):
        registry = default_registry()
        context = {'dt_seconds': 60, 'time': '2024-01-01T00:00:00Z'}
        inputs = {'inventory': {'value': 100, 'unit': 'barrel'}, 'inflow': {'value': 3, 'unit': 'barrel/second'}, 'outflow': {'value': 1, 'unit': 'barrel/second'}}
        self.assertEqual(registry.predict('resource_inventory.deterministic', inputs, {}, context)['pressures'][0]['value'], 2)
        scaled = registry.predict('resource_inventory.deterministic', inputs, {'flow_scale': 0.5, 'unmeasured_net_flow': 0.25}, context)
        self.assertEqual(scaled['pressures'][0]['value'], 1.25)
        cash = {'cash': {'value': 0, 'unit': 'USD'}, 'revenue': {'value': 10, 'unit': 'USD/second'}, 'expenditure': {'value': 4, 'unit': 'USD/second'}}
        self.assertEqual(registry.predict('investment_cash_flow.deterministic', cash, {'cash_conversion': 0.5}, context)['pressures'][0]['value'], 3)


class EstimationCliTests(unittest.TestCase):
    def run_cli(self, argv):
        from worldmodel.cli import main
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = main(argv)
        self.assertEqual(code, 0, stderr.getvalue())
        return json.loads(stdout.getvalue())

    def test_requirements_estimate_validate_and_status(self):
        from worldmodel.store import Store
        from worldmodel.artifacts import publish_report
        summary = self.run_cli(['estimation-requirements', 'coupled_economy'])
        self.assertIn('interest_pass_through', summary['coupled_economy']['required_components'])
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / 'data'
            store = Store(root)
            dataset = component_dataset('interest_pass_through', seed=4)
            raw_path = Path(temp) / 'synthetic.jsonl'
            raw_path.write_text('\n'.join(json.dumps(r) for r in dataset['records']))
            raw = store.import_file('synthetic_rates_raw', raw_path, {'publisher': 'fictional synthetic fixture'})
            records = [dict(r, evidence=[{'input': raw, 'locator': f'line:{i + 1}'}]) for i, r in enumerate(dataset['records'])]
            publish_report(store, 'synthetic_rates', {'fictional': True}, {}, raw_inputs=[raw], records=records,
                           entrypoint='worldmodel.estimation.synthetic:component_dataset')
            estimate = self.run_cli(['--data-root', str(root), 'estimate', 'interest_pass_through', '--data', 'synthetic_rates',
                                     '--cutoff', dataset['cutoff']])
            result = estimate['results'][0]
            self.assertAlmostEqual(result['parameters']['pass_through'], 1.0, delta=0.05)
            self.assertTrue(store.verify(result['artifact']))
            validation = self.run_cli(['--data-root', str(root), 'validate', 'coupled_economy', '--component', 'interest_pass_through',
                                       '--data', 'synthetic_rates', '--cutoff', dataset['cutoff'], '--train-end', dataset['train_end'],
                                       '--validation-end', dataset['validation_end']])
            component = validation['results'][0]
            self.assertTrue(component['validated'])
            self.assertFalse(validation['process_validation']['validated'])
            self.assertIn('labor_demand', validation['process_validation']['missing_components'])
            status = self.run_cli(['--data-root', str(root), 'calibration-status',
                                   f"{component['artifact']['dataset']}@{component['artifact']['version']}"])
            self.assertTrue(status['records'][0]['validated'])
            self.assertFalse(status['processes']['coupled_economy']['validated'])


if __name__ == '__main__':
    unittest.main()
