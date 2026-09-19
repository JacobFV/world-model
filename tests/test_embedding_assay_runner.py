"""The assay runner's protocol, on a synthetic domain small enough to check by hand.

A domain is a function returning tasks, a template and an audit, so a test can register one and
exercise the parts that decide a verdict: what a fit may train on, that selection happens on the
validation block only, that the test rows are the untouched blocks, and that the report's id
covers its own contents.
"""
import unittest

try:
    import numpy as np
    import torch
    import lightgbm  # noqa: F401
except ImportError:  # the optional ``embed`` extra is not installed
    np = torch = None


def synthetic_domain(periods=8, per_period=60, nodes=4, features=3, history=2, seed=0):
    """Tasks whose label is a noisy function of one feature, so every forecaster has something to find."""
    from worldmodel.embedding import actors
    rng = np.random.default_rng(seed)
    total = periods * per_period
    x = rng.standard_normal((total, nodes, features, history)).astype(np.float16)
    m = np.ones_like(x, dtype=np.uint8)
    quarter = np.repeat(np.arange(periods), per_period)
    signal = x[:, 0, 0, 0].astype(np.float32)
    y = ((signal + 0.3 * rng.standard_normal(total)) > 0).astype(float)[:, None]
    edges = np.array([[0, 1, 0], [1, 0, 1], [0, 2, 0], [2, 0, 1]], dtype=np.int64)
    tasks = actors.TaskSet(x=x, m=m, node_valid=np.ones((total, nodes), dtype=bool),
                           weights=np.ones((total, len(edges)), dtype=np.float32), y=y, quarter=quarter,
                           label_filed=quarter.astype(np.int64),      # a label is public in its own period
                           manager=np.arange(total), security=np.arange(total),
                           base_manager=np.full((total, 1), 0.5), base_security=np.full((total, 1), np.nan),
                           max_feature_filed={q: q for q in range(periods)})
    node_type = np.zeros(nodes, dtype=np.int64)

    def prepare(store, attempt, protocol, config, log):
        return {'tasks': tasks, 'q_of': {q: q for q in range(periods)}, 'first': 0,
                'blocks': [[q_lo, q_hi] for q_lo, q_hi in protocol['blocks']], 'period_end': str,
                'origin_day': lambda q: q, 'label_public_by': lambda label, origin: label < origin,
                'feature_leakage': lambda q: 0, 'template': (nodes, edges, node_type), 'dims': (1, 2),
                'targets': ['up'], 'own_rate': 'own_rate', 'gbdt_nodes': ((0, 1), ((2, nodes),)),
                'inputs': [{'dataset': 'synthetic', 'stage': 'x', 'version': 'b' * 64}], 'component': 'synthetic',
                'target_suffix': 'next_period', 'series': {'synthetic': {
                    'series': 'synthetic', 'revisions': 'none', 'vintage_modes': ['synthetic'],
                    'revision_leakage_possible': False}},
                'audit_extra': {}, 'origin_text': 'period start', 'vintage_policy': 'synthetic',
                'actuals': 'synthetic', 'limitations': ['Synthetic data: this establishes nothing about the world.']}
    return prepare, tasks


def attempt_spec(blocks):
    return {'id': 'synthetic.v1', 'domain': 'synthetic', 'targets': ['up'],
            'protocol': {'history': 2, 'blocks': blocks, 'standardize_through': 1},
            'candidates': {'root_readout': {'seed_only': False},
                           'gbdt_plus_self_supervised_embedding': {'kind': 'gbdt_plus_self_supervised_embedding',
                                                                   'components': 2}},
            'baselines': ['historical_mean', 'own_rate', 'gbdt'],
            'config': {'d': 16, 'layers': 1, 'slots': 2, 'passes': 2, 'top_k': 2, 'out_dim': 8, 'epochs': 1,
                       'batch': 32, 'lr': 0.01, 'weight_decay': 0.0, 'dense_fraction': 0.5, 'gumbel': 0.0,
                       'mask_rate': 0.1, 'recon_weight': 0.1, 'calibration_quarters': 1, 'seed': 0},
            'gbdt': {'n_estimators': 20, 'num_leaves': 7, 'min_child_samples': 5, 'verbose': -1, 'random_state': 0},
            'criteria': [{'id': 'minimum_test_forecasts', 'type': 'min_forecasts', 'minimum': 12},
                         {'id': 'no_timing_leakage', 'type': 'leakage_audit'},
                         {'id': 'no_revision_leakage', 'type': 'revision_leakage'}]}


@unittest.skipIf(torch is None, 'optional embed extra (numpy, torch, lightgbm) not installed')
class RunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from worldmodel.embedding import actors_assay
        prepare, cls.tasks = synthetic_domain()
        actors_assay.DOMAINS['synthetic'] = prepare
        cls.spec = attempt_spec([[4, 5], [6, 6], [7, 7]])
        cls.reports = actors_assay.run_attempt(None, 'synthetic.v1', publish=False, spec=cls.spec, device='cpu',
                                               log=lambda message: None)

    @classmethod
    def tearDownClass(cls):
        from worldmodel.embedding import actors_assay
        actors_assay.DOMAINS.pop('synthetic', None)

    def test_one_report_per_target_with_a_verifiable_id(self):
        from worldmodel.util import digest
        self.assertEqual([r['target'] for r in self.reports], ['up'])
        report = self.reports[0]['report']
        self.assertEqual(report['report_id'], digest({k: v for k, v in report.items() if k != 'report_id'}))
        self.assertEqual(report['component'], 'actors_embedding:synthetic_up')
        self.assertIn('Synthetic data: this establishes nothing about the world.', report['limitations'])

    def test_selection_uses_the_validation_block_and_is_frozen(self):
        selection = self.reports[0]['report']['selection']
        self.assertEqual(sorted(selection['scores']), sorted(self.spec['candidates']))
        self.assertIn(selection['selected'], self.spec['candidates'])
        self.assertFalse(selection['refit_after_selection'])
        self.assertTrue(selection['selection_hash'])

    def test_test_rows_exclude_the_validation_block(self):
        report = self.reports[0]['report']
        # Blocks 6 and 7 are the test blocks; block 4-5 is validation and must not be scored.
        self.assertEqual(report['test']['forecast_count'], 2 * 60)
        self.assertEqual(report['test']['leakage_audit']['violations'], 0)
        self.assertGreater(report['test']['leakage_audit']['origins_checked'], 0)

    def test_every_declared_criterion_is_evaluated(self):
        acceptance = self.reports[0]['report']['acceptance']
        self.assertEqual([r['id'] for r in acceptance['results']],
                         ['minimum_test_forecasts', 'no_timing_leakage', 'no_revision_leakage'])
        self.assertEqual(acceptance['passed'], all(r['passed'] for r in acceptance['results']))

    def test_baselines_present_on_some_rows_are_scored(self):
        metrics = self.reports[0]['report']['test']['metrics']
        self.assertIn('historical_mean', metrics['baselines'])
        self.assertIn('own_rate', metrics['baselines'])
        self.assertIn('gbdt', metrics['baselines'])


if __name__ == '__main__':
    unittest.main()
