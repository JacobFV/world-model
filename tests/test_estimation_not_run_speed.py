"""The legislative and market-ABM speed-ups change no output.

``tests/legacy_legislative.py`` and ``tests/legacy_market_abm.py`` are frozen copies of the
two family modules as they were when both real-data attempts were recorded ``not_run`` for
compute budget. Every comparison here is exact equality of Python floats (and of whole
validation reports, including their content digests), not a tolerance: the optimized code
performs the same floating-point operations in the same order and only avoids repeating
work. Fixtures are synthetic.
"""
import math
import random
import struct
import unittest

from tests import legacy_legislative
from tests import legacy_market_abm
from worldmodel import models
from worldmodel.estimation import validate_process
from worldmodel.estimation.model_families import ModelFamilyEstimator
from worldmodel.models import legislative, market_abm


def bits(value):
    """Exact representation, so 0.0 and -0.0 or two nearby floats never compare equal by accident."""
    if isinstance(value, float):
        return struct.pack('>d', value)
    if isinstance(value, dict):
        return {k: bits(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [bits(v) for v in value]
    return value


class LegacyFamily:
    """Route ``models.family_module(name)`` to a frozen legacy module for the duration of a test."""

    def __init__(self, case, family, module):
        self.previous = models.FAMILY_MODULES[family]
        models.FAMILY_MODULES[family] = module
        case.addCleanup(models.FAMILY_MODULES.__setitem__, family, self.previous)


class NewtonStepTests(unittest.TestCase):
    def test_specialized_steps_are_bit_identical_to_the_general_loop(self):
        rng = random.Random(3)
        for size in (1, 2, 3):
            for trial in range(40):
                rows = []
                for _ in range(rng.randint(1, 60)):
                    z = [1.0] + [rng.gauss(0, 2) for _ in range(size - 1)] if size > 1 and trial % 2 else \
                        [rng.gauss(0, 2) for _ in range(size)]
                    rows.append((z, rng.randint(0, 1), rng.gauss(0, 3) if trial % 3 else 0.0))
                theta = [rng.gauss(0, 1) * (trial % 4) for _ in range(size)]
                prior = [rng.choice([1e-6, 0.04, 1.0]) for _ in range(size)]
                self.assertEqual(bits(legislative._newton(rows, theta, prior)),
                                 bits(legacy_legislative._newton(rows, theta, prior)), (size, trial))

    def test_dot_matches_sum_of_products(self):
        rng = random.Random(5)
        for size in (1, 2, 3, 4):
            for _ in range(2000):
                b = [rng.gauss(0, 1) * 10 ** rng.randint(-8, 8) for _ in range(size)]
                x = [rng.gauss(0, 1) * 10 ** rng.randint(-8, 8) for _ in range(size)]
                self.assertEqual(bits(legislative._dot(b, x)), bits(sum(bb * xx for bb, xx in zip(b, x))))
        self.assertEqual(bits(legislative._dot([-0.0], [1.0])), bits(sum(bb * xx for bb, xx in zip([-0.0], [1.0]))))


class LegislativeIdentityTests(unittest.TestCase):
    def test_fit_is_identical_on_both_backends_and_in_two_dimensions(self):
        for dims in (1, 2):
            data = legislative.synthetic(2, members=40, rollcalls=90, dims=dims)['data']
            for backend in ('python', 'numpy'):
                if backend == 'numpy' and legislative.NUMPY is None:
                    continue
                data['options']['backend'] = backend
                for cutoff in (None, '2015-06-30'):
                    new, old = legislative.fit(data, cutoff), legacy_legislative.fit(data, cutoff)
                    self.assertEqual(bits(new), bits(old), (dims, backend, cutoff))

    def test_holdout_forecasts_are_identical(self):
        data = legislative.synthetic(4)['data']
        fitted = legislative.fit(data, '2015-12-31')
        parameters = dict(fitted['estimate'])
        history = [r for r in data['rollcalls'] if r['date'] <= '2015-12-31']
        rows = [r for r in data['rollcalls'] if r['date'] == '2016-01-15']
        self.assertTrue(rows)
        self.assertEqual(bits(legislative._holdout_forecast(parameters, history, rows, data)),
                         bits(legacy_legislative._holdout_forecast(parameters, history, rows, data)))

    def test_validation_report_is_identical(self):
        data = models.synthetic('legislative', 1)['data']
        protocol = dict(train_end='2014-12-31', validation_end='2016-12-31', cutoff='2019-12-31', refit_every=4,
                        vintage_policy='family_rows')
        new = validate_process(ModelFamilyEstimator('legislative'), data, **protocol)
        LegacyFamily(self, 'legislative', 'tests.legacy_legislative')
        old = validate_process(ModelFamilyEstimator('legislative'), data, **protocol)
        self.assertGreater(new['test']['metrics']['model']['count'], 200)
        self.assertEqual(new['report_id'], old['report_id'])
        self.assertEqual(bits(new), bits(old))


class MarketAbmIdentityTests(unittest.TestCase):
    def configs(self):
        base = dict(market_abm.example_config(), steps=120)
        few = dict(base, agents=[dict(a, count=3) for a in base['agents']])
        no_fundamentalists = dict(few, agents=[a for a in few['agents'] if a['type'] != 'fundamentalist'])
        path = dict(few, fundamental={'initial_cents': 10000, 'path': [10000 * math.exp(0.01 * math.sin(t)) for t in range(120)]})
        return {'example': base, 'few': few, 'no_fundamentalists': no_fundamentalists, 'fundamental_path': path}

    def test_simulation_is_identical_for_every_clearing_and_mode(self):
        for name, config in self.configs().items():
            for clearing in ('walrasian', 'order_book'):
                for mode in ('deterministic', 'stochastic'):
                    c = dict(config, clearing=clearing)
                    self.assertEqual(bits(market_abm.simulate(c, mode, 11)), bits(legacy_market_abm.simulate(c, mode, 11)),
                                     (name, clearing, mode))

    def test_theta_function_matches_per_agent_theta(self):
        config = self.configs()['few']
        params = market_abm.merged_parameters(market_abm.FAMILY, config.get('parameters'))
        agents = market_abm._agents(config, params)
        returns = [0.01, -0.02, 0.005, 0.0, 0.03, -0.001]
        noise = {a['id']: 0.37 * k for k, a in enumerate(agents)}
        theta_at = market_abm._theta_function(agents, 10321.5, returns, noise)
        for price in (5000, 9999.5, 10321.5, 20000.25):
            self.assertEqual(bits(theta_at(price)),
                             bits([legacy_market_abm._theta(a, price, 10321.5, returns, noise) for a in agents]))

    def test_smm_fit_and_validation_report_are_identical(self):
        fixture = market_abm.synthetic(0, steps=300, agents_per_type=5, window=20)['data']
        self.assertEqual(bits(market_abm.fit(fixture, '2000-06-30')), bits(legacy_market_abm.fit(fixture, '2000-06-30')))
        protocol = dict(train_end='2000-03-03', validation_end='2000-04-12', cutoff='2000-10-29', vintage_policy='family_rows')
        new = validate_process(ModelFamilyEstimator('market_abm'), fixture, **protocol)
        LegacyFamily(self, 'market_abm', 'tests.legacy_market_abm')
        old = validate_process(ModelFamilyEstimator('market_abm'), fixture, **protocol)
        self.assertGreaterEqual(new['test']['metrics']['model']['count'], 10)
        self.assertEqual(new['report_id'], old['report_id'])
        self.assertEqual(bits(new), bits(old))


if __name__ == '__main__':
    unittest.main()
