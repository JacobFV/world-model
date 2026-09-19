import unittest

try:
    import numpy as np
except ImportError:  # the optional ``embed`` extra is not installed
    np = None


def synthetic_data():
    """Four banks over four quarters, each number chosen so every marker can be checked by hand.

    Bank 0 (state 0) holds its noncurrent ratio at 2% but loses a quarter of its deposits between
    quarter 2 and quarter 3: the deposit-outflow component fires. Bank 1 (state 0) holds its
    deposits but its noncurrent ratio goes 2% -> 4%: the noncurrent-onset component fires. Bank 2
    (state 1) is clean throughout. Bank 3 (state 1) sits at 4% in *every* quarter, so the onset
    component never fires for it: the marker is a crossing, not a level.
    """
    from worldmodel.embedding.domains.fdic import METRIC_INDEX, METRICS
    banks, quarters = 4, 4
    values = np.full((banks, quarters, len(METRICS)), np.nan, dtype=np.float32)

    def put(metric, rows):
        values[:, :, METRIC_INDEX[metric]] = np.array(rows, dtype=np.float32)

    put('total_assets', [[100e6] * 4, [200e6] * 4, [50e6] * 4, [400e6] * 4])
    put('bank_deposits', [[80e6, 80e6, 80e6, 60e6], [160e6] * 4, [40e6] * 4, [320e6] * 4])
    put('bank_net_loans', [[50e6] * 4] * 4)
    put('bank_noncurrent_loans', [[1e6] * 4, [1e6, 1e6, 1e6, 2e6], [0.0] * 4, [2e6] * 4])
    put('bank_equity', [[10e6] * 4] * 4)
    return {'ref': {'dataset': 'fdic_bank_financials', 'stage': 'normalized', 'version': 'test'},
            'values': values, 'state': np.array([0, 0, 1, 1], dtype=np.int16),
            'cert_ids': ['1', '2', '3', '4'], 'states': ['CA', 'TX'],
            'meta': {'banks': banks, 'quarters': quarters, 'counts': {}}}


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class QuarterArithmeticTests(unittest.TestCase):
    def test_quarter_index_and_period_end(self):
        from worldmodel.embedding.domains.fdic import quarter_end, quarter_index
        self.assertEqual(quarter_index(2010, 1), 0)
        self.assertEqual(quarter_index(2010, 3), 0)
        self.assertEqual(quarter_index(2024, 12), (2024 - 2010) * 4 + 3)
        self.assertEqual(quarter_end(0), '2010-03-31')
        self.assertEqual(quarter_end(3), '2010-12-31')

    def test_a_quarter_is_public_sixty_days_after_its_report_date(self):
        from worldmodel.embedding.domains.fdic import origin_day
        self.assertEqual(origin_day(0), 20100530)     # 2010-03-31 + 60 days
        self.assertEqual(origin_day(3), 20110301)     # 2010-12-31 + 60 days, 2011 not a leap year


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class MarkerTests(unittest.TestCase):
    def setUp(self):
        from worldmodel.embedding.domains.fdic import derive
        self.derived = derive(synthetic_data())

    def test_noncurrent_ratio_is_noncurrent_over_net_loans(self):
        from worldmodel.embedding.domains.fdic import FEATURES
        ratio = self.derived['x'][:, :, FEATURES.index('noncurrent_ratio')]
        self.assertAlmostEqual(float(ratio[0, 0]), 0.02, places=6)
        self.assertAlmostEqual(float(ratio[1, 3]), 0.04, places=6)
        self.assertAlmostEqual(float(ratio[2, 0]), 0.0, places=6)

    def test_the_first_quarter_has_no_marker_because_it_has_no_predecessor(self):
        self.assertTrue(np.isnan(self.derived['marker'][:, 0]).all())

    def test_each_component_fires_exactly_where_it_should(self):
        marker, components = self.derived['marker'], self.derived['components']
        np.testing.assert_array_equal(marker[:, 3], [1.0, 1.0, 0.0, 0.0])
        np.testing.assert_array_equal(components[:, 3, 0], [0.0, 1.0, 0.0, 0.0])   # noncurrent onset
        np.testing.assert_array_equal(components[:, 3, 1], [1.0, 0.0, 0.0, 0.0])   # deposit outflow
        np.testing.assert_array_equal(marker[:, 2], [0.0, 0.0, 0.0, 0.0])

    def test_a_bank_already_above_the_threshold_never_has_an_onset(self):
        # Bank 3 sits at 4% in every quarter: a level, not a crossing.
        np.testing.assert_array_equal(self.derived['components'][3, 1:, 0], [0.0, 0.0, 0.0])


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class BuildTests(unittest.TestCase):
    def setUp(self):
        from worldmodel.embedding.domains.fdic import build_tasks
        self.tasks = build_tasks(synthetic_data(), first_quarter=2, last_quarter=2, per_quarter=10,
                                 history=2, log=None)
        self.rows = {int(b): i for i, b in enumerate(self.tasks.manager)}

    def test_one_sample_per_bank_and_the_label_is_next_quarters_marker(self):
        self.assertEqual(len(self.tasks.y), 4)
        np.testing.assert_array_equal(self.tasks.y[:, 0], [1.0, 1.0, 0.0, 0.0])

    def test_label_is_dated_by_the_label_quarters_public_date(self):
        from worldmodel.embedding.domains.fdic import origin_day
        self.assertTrue((self.tasks.label_filed == origin_day(3)).all())
        self.assertEqual(self.tasks.leakage_violations, 0)

    def test_features_never_reach_into_the_label_quarter(self):
        """Quarter 3 is where every label lives; changing it must not move a single feature."""
        from worldmodel.embedding.domains.fdic import METRIC_INDEX, build_tasks
        data = synthetic_data()
        data['values'] = data['values'].copy()
        data['values'][:, 3, METRIC_INDEX['total_assets']] = 1.0
        other = build_tasks(data, first_quarter=2, last_quarter=2, per_quarter=10, history=2, log=None)
        np.testing.assert_array_equal(self.tasks.x, other.x)
        np.testing.assert_array_equal(self.tasks.m, other.m)

    def test_own_rate_is_the_banks_marker_rate_over_the_public_transitions(self):
        # Quarters 1 and 2 are the only transitions on or before the origin, and neither fired.
        np.testing.assert_array_equal(self.tasks.base_manager[:, 0], [0.0, 0.0, 0.0, 0.0])

    def test_state_peers_are_other_banks_of_the_same_state_largest_first(self):
        from worldmodel.embedding.domains.fdic import K
        row = self.rows[0]
        self.assertTrue(self.tasks.node_valid[row, 1])          # bank 1, the only other bank in state 0
        self.assertFalse(self.tasks.node_valid[row, 2])
        self.assertAlmostEqual(float(self.tasks.weights[row, 0]), 0.5, places=5)   # 100m / 200m
        self.assertEqual(int(self.tasks.node_valid[row, 1:1 + K].sum()), 1)

    def test_size_peers_are_the_nearest_banks_by_log_assets(self):
        from worldmodel.embedding.domains.fdic import K
        row = self.rows[0]
        # Three other banks exist, all within the window of eight around bank 0.
        self.assertEqual(int(self.tasks.node_valid[row, 1 + K:].sum()), 3)

    def test_template_shape_and_node_types(self):
        from worldmodel.embedding.domains.fdic import K, NODE_TYPES, template
        n, edges, node_type = template()
        self.assertEqual(n, 1 + 2 * K)
        self.assertEqual(self.tasks.x.shape[1], n)
        self.assertEqual(int(node_type[0]), NODE_TYPES.index('bank'))
        self.assertEqual(int(node_type[1]), NODE_TYPES.index('state_peer'))
        self.assertEqual(int(node_type[1 + K]), NODE_TYPES.index('size_peer'))
        self.assertEqual(self.tasks.weights.shape[1], len(edges))

    def test_rates_record_both_components(self):
        from worldmodel.embedding.domains.fdic import quarter_end
        rates = self.tasks.rates[quarter_end(2)]
        self.assertEqual(rates['samples'], 4)
        self.assertAlmostEqual(rates['base_rate'], 0.5)
        self.assertAlmostEqual(rates['noncurrent_onset'], 0.25)
        self.assertAlmostEqual(rates['deposit_outflow'], 0.25)


if __name__ == '__main__':
    unittest.main()
