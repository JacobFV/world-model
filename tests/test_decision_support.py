"""Shared fixtures for the decision-layer tests: contracts and a temporary calibration store.

Everything here is synthetic: the monetary reports are fitted on the family's synthetic
panel, so no test reads the checkout's data payloads.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

EXAMPLES = Path(__file__).resolve().parents[1] / 'examples' / 'decision'

GOOD, BAD, OLD = 'mon.synthetic_realtime', 'mon.synthetic_revised', 'mon.synthetic_old'


def monetary_contract(status='assumed', report_id=None, drivers=None, **overrides):
    """A small treasury contract on the monetary kernel; assumed parameters when ``status`` is assumed."""
    contract = {
        'schema': 'worldmodel.decision_contract/1', 'id': 'test-treasury',
        'decision_maker': {'id': 'org:test-treasury', 'role': 'floating-rate borrower treasury'},
        'environment': {'kernel': 'monetary_policy_rate',
                        'config': {'initial': {'policy_rate': 4.0, 'inflation': 2.5, 'output_gap': 0.5},
                                   'drivers': drivers or {'source': 'linear_paths'}}},
        'actions': {'reserve_margin': {'type': 'number', 'unit': 'percentage_points', 'minimum': -2.0, 'maximum': 3.0,
                                       'cost_per_unit': 1.0, 'cost_basis': 'positive_part'}},
        'budget': {'limit': 4.0, 'unit': 'pp-quarters'},
        'objectives': [{'id': 'under', 'metric': 'shortfall', 'aggregate': 'sum', 'direction': 'minimize', 'weight': 0.75, 'scale': 1.0},
                       {'id': 'idle', 'metric': 'excess', 'aggregate': 'sum', 'direction': 'minimize', 'weight': 0.25, 'scale': 1.0}],
        'horizon': {'steps': 6, 'step': 'quarter'},
        'success_criteria': [{'id': 'worst_shortfall', 'metric': 'shortfall', 'aggregate': 'max', 'operator': 'lte',
                              'value': 0.5, 'scale': 0.25}],
        'mechanisms': {'policy_rate_reaction': {'description': 'Taylor rule', 'evidence': {'status': status}}},
        'uncertain_inputs': {name: {'unit': 'percent', 'minimum': low, 'maximum': high, 'nominal': nominal}
                             for name, low, high, nominal in (('inflation_start', 1.0, 5.0, 2.5), ('inflation_end', 0.0, 6.0, 2.0),
                                                              ('output_gap_start', -3.0, 3.0, 0.5), ('output_gap_end', -4.0, 3.0, 0.0))},
    }
    if status == 'assumed':
        contract['assumed_parameters'] = {
            name: {'mechanism': 'policy_rate_reaction', 'unit': unit, 'minimum': low, 'maximum': high, 'nominal': nominal}
            for name, unit, low, high, nominal in (('rho', 'per_quarter', 0.7, 0.95, 0.85), ('phi_pi', 'dimensionless', 0.0, 1.5, 0.5),
                                                   ('phi_y', 'dimensionless', 0.0, 1.5, 0.5), ('r_star', 'percent', -1.0, 2.0, 0.5),
                                                   ('policy_shock_sd', 'percent', 0.1, 0.6, 0.3))}
    else:
        contract['mechanisms']['policy_rate_reaction']['evidence'].update(report_id=report_id, component='monetary_model_parameters')
    if (drivers or {}).get('source') == 'historical_resample':
        contract['uncertain_inputs'] = {}
    contract.update(deepcopy(overrides))
    return contract


def economy_contract():
    """The economy example with every mechanism declared assumed, so it compiles without a store."""
    import json
    contract = json.loads((EXAMPLES / 'economy-firm-stress.json').read_text())
    for mechanism in contract['mechanisms'].values():
        mechanism['evidence'] = {'status': 'assumed'}
    return contract


class SyntheticStore:
    """A temporary data root holding synthetic monetary validation reports and a matching plan.

    ``good`` passes every criterion (real-time rows), ``bad`` fails no_revision_leakage
    (valid-time rows), ``old`` passes but is filed under a superseded attempt.
    """

    def __init__(self):
        from worldmodel import models
        from worldmodel.estimation import estimator_for, publish_validation_report, validate_process
        from worldmodel.store import Store
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.temp.name) / 'data')
        estimator = estimator_for('monetary_model_parameters')
        self.rows = models.synthetic('monetary', 0)['data']['observations']
        splits = dict(train_end='2000-12-31', validation_end='2004-12-31', cutoff='2009-12-01')
        self.good = validate_process(estimator, {'observations': self.rows, 'information_time': 'real_time'}, **splits)
        self.bad = validate_process(estimator, {'observations': self.rows}, **splits)
        self.old = validate_process(estimator, {'observations': self.rows, 'information_time': 'real_time'},
                                    **dict(splits, cutoff='2009-06-01'))
        assert self.good['validated'] and not self.bad['validated'] and self.old['validated']
        for label, report in ((GOOD, self.good), (BAD, self.bad), (OLD, self.old)):
            publish_validation_report(self.store, report, dataset='calibration_reports',
                                      parameters={'attempt': label, 'process_id': 'monetary_model'})
        attempt = {'kind': 'family', 'target': 'monetary', 'process_id': 'monetary_model'}
        self.plan = {'attempts': [dict(attempt, id=GOOD), dict(attempt, id=BAD), dict(attempt, id=OLD, superseded_by=GOOD)]}

    def index(self):
        from worldmodel.decision.evidence import EvidenceIndex
        return EvidenceIndex.from_store(self.store, plan=self.plan)

    def history(self):
        return {'rows': deepcopy(self.rows), 'evidence': {'fixture': 'worldmodel.models.monetary.synthetic(seed=0)'}}

    def close(self):
        self.temp.cleanup()


class SupportTests(unittest.TestCase):
    def test_example_contracts_exist(self):
        names = {p.name for p in EXAMPLES.glob('*.json')}
        self.assertTrue({'monetary-treasury-stress.json', 'monetary-treasury-optimize.json', 'economy-firm-stress.json'} <= names)


if __name__ == '__main__':
    unittest.main()
