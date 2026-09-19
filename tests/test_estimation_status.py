"""Headline counts re-derived from published reports and joined to the plan (synthetic reports only)."""
from datetime import date
import math
import random
import tempfile
import unittest
from pathlib import Path

from worldmodel.estimation import ObservationSet, validate_process, publish_validation_report
from worldmodel.estimation.families import PopulationGrowthEstimator
from worldmodel.estimation.status import plan_status
from worldmodel.estimation.synthetic import component_dataset, observation_records, periods


def _attempt(identifier, **extra):
    return {'id': identifier, 'kind': 'component', 'target': 'population_growth_rate',
            'process_id': 'population_growth', **extra}


class PlanStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        ds = component_dataset('population_growth_rate', seed=2)
        splits = dict(train_end=ds['train_end'], validation_end=ds['validation_end'], cutoff=ds['cutoff'])
        cls.good = validate_process(PopulationGrowthEstimator(), ObservationSet(ds['records']), **splits)
        rng = random.Random(3)
        walk = [1e6]
        for _ in range(69):
            walk.append(walk[-1] * math.exp(rng.gauss(0, 0.01)))
        records = observation_records(PopulationGrowthEstimator(), {'population': walk}, periods('annual', 70, date(1950, 1, 1)))
        cls.bad = validate_process(PopulationGrowthEstimator(), ObservationSet(records), **splits)
        assert cls.good['validated'] and not cls.bad['validated']

    def _store(self, temp, published):
        from worldmodel.store import Store
        store = Store(Path(temp) / 'data')
        for label, report in published:
            publish_validation_report(store, report, dataset='calibration_reports',
                                      parameters={'attempt': label, 'process_id': report['process_id']})
        return store

    def test_counts_join_reports_to_the_plan(self):
        plan = {'attempts': [_attempt('pop.old', superseded_by='pop.new'), _attempt('pop.new'),
                             _attempt('pop.failing'), _attempt('pop.never_ran')]}
        with tempfile.TemporaryDirectory() as temp:
            store = self._store(temp, [('pop.old', self.bad), ('pop.new', self.good), ('pop.failing', self.bad)])
            status = plan_status(store, plan)
        self.assertEqual((status['registered'], status['current'], status['superseded']), (4, 3, 1))
        self.assertEqual(status['without_report'], ['pop.never_ran'])
        self.assertEqual((status['current_pass'], status['current_fail']), (1, 1))
        self.assertIn('beats_persistence_dm', status['failing_criteria'])
        self.assertEqual(status['failing_criteria']['beats_persistence_dm'], 1)       # the superseded failure is not counted
        self.assertEqual(status['validated_processes'], ['population_growth'])

    def test_an_entity_failure_fails_the_attempt_and_a_failing_process_is_not_validated(self):
        plan = {'attempts': [_attempt('pop.panel')]}
        with tempfile.TemporaryDirectory() as temp:
            store = self._store(temp, [('pop.panel[a]', self.good), ('pop.panel[b]', self.bad)])
            status = plan_status(store, plan)
        row = status['attempts'][0]
        self.assertEqual(row['verdict'], 'fail')
        self.assertEqual(row['entities']['pop.panel[a]'], 'pass')
        self.assertEqual(status['validated_processes'], [])

    def test_reports_outside_the_plan_are_listed_not_counted(self):
        plan = {'attempts': [_attempt('pop.new')]}
        with tempfile.TemporaryDirectory() as temp:
            store = self._store(temp, [('pop.new', self.good), ('something.else', self.bad)])
            status = plan_status(store, plan)
        self.assertEqual([item['label'] for item in status['unmatched_reports']], ['something.else'])
        self.assertEqual(status['current_fail'], 0)


if __name__ == '__main__':
    unittest.main()
