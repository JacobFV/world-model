import unittest

try:
    import numpy as np
except ImportError:  # the optional ``embed`` extra is not installed
    np = None


COMMITTEES = ['C_A', 'C_B', 'C_X', 'C_Y', 'C_Z']       # A and B give; X, Y and Z are candidate committees
A, B, X, Y, Z = 0, 1, 2, 3, 4


def synthetic_data():
    """Five committees over three cycles, with every total small enough to check by hand.

    Cycle 0: A gives 1,000 to X and 500 to Y.
    Cycle 1: A gives 2,000 to X and 300 to Z; B gives 700 to X. B also spends 90 on an independent
             expenditure supporting X, which is *not* giving.
    Cycle 2: A gives 100 to X, and nobody else gives.

    So at cycle 1 there are three samples -- (A, X), (A, Z) and (B, X) -- and only (A, X) has a
    label of 1. A's repeat rate into cycle 1 is 1/2 (it gave to X again but not to Y); X's
    retention into cycle 1 is 1/1.
    """
    from worldmodel.embedding.domains import fec
    rows = [  # (cycle, giver, recipient, class, amount, transactions, office)
        (0, A, X, fec.DIRECT, 1000.0, 2, 0),
        (0, A, Y, fec.DIRECT, 500.0, 1, 0),
        (1, A, X, fec.DIRECT, 2000.0, 3, 0),
        (1, A, Z, fec.DIRECT, 300.0, 1, 0),
        (1, B, X, fec.DIRECT, 700.0, 1, 0),
        (1, B, X, fec.SUPPORT_IE, 90.0, 1, 0),
        (2, A, X, fec.DIRECT, 100.0, 1, 0),
    ]
    nc = len(COMMITTEES)
    registrations = {  # (cycle, committee) -> (party, type, state)
        (c, committee): value
        for c in (0, 1, 2)
        for committee, value in ((A, (2, 3, 7)), (B, (1, 3, 9)), (X, (2, 1, 7)), (Y, (1, 1, 9)), (Z, (2, 1, 7)))
    }
    keys = sorted(c * nc + committee for c, committee in registrations)
    return {
        'ref': {'dataset': 'fec', 'stage': 'normalized', 'version': 'test'},
        'n_committees': nc, 'committee_ids': COMMITTEES,
        'giver': np.array([r[1] for r in rows], dtype=np.int32),
        'recipient': np.array([r[2] for r in rows], dtype=np.int32),
        'cycle': np.array([r[0] for r in rows], dtype=np.int16),
        'klass': np.array([r[3] for r in rows], dtype=np.int8),
        'amount': np.array([r[4] for r in rows], dtype=np.float64),
        'count': np.array([r[5] for r in rows], dtype=np.int32),
        'office': np.array([r[6] for r in rows], dtype=np.int8),
        'reg_key': np.array(keys, dtype=np.int64),
        'reg_party': np.array([registrations[(k // nc, k % nc)][0] for k in keys], dtype=np.int8),
        'reg_type': np.array([registrations[(k // nc, k % nc)][1] for k in keys], dtype=np.int8),
        'reg_state': np.array([registrations[(k // nc, k % nc)][2] for k in keys], dtype=np.int16),
        'meta': {'counts': {}, 'committees': nc, 'candidates': 3, 'cycles': 3},
    }


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class CycleArithmeticTests(unittest.TestCase):
    def test_cycle_index_and_period_end(self):
        from worldmodel.embedding.domains.fec import cycle_end, cycle_index
        self.assertEqual(cycle_index(2000), 0)
        self.assertEqual(cycle_index(2024), 12)
        self.assertEqual(cycle_end(0), '2000-12-31')
        self.assertEqual(cycle_end(12), '2024-12-31')

    def test_a_cycle_is_public_on_31_january_after_it_ends(self):
        from worldmodel.embedding.domains.fec import cycle_public_day
        self.assertEqual(cycle_public_day(0), 20010131)
        self.assertEqual(cycle_public_day(12), 20250131)

    def test_an_odd_year_opens_the_cycle_that_the_following_even_year_closes(self):
        from worldmodel.embedding.domains.fec import cycle_of_month
        self.assertEqual(cycle_of_month(2003, 1), 2004)
        self.assertEqual(cycle_of_month(2004, 12), 2004)
        self.assertEqual(cycle_of_month(2005, 7), 2006)


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class AggregateTests(unittest.TestCase):
    def setUp(self):
        from worldmodel.embedding.domains.fec import aggregate
        self.data = synthetic_data()
        self.agg = aggregate(self.data)

    def pair(self, cycle, giver, recipient):
        key = (cycle * self.agg['nc'] + giver) * self.agg['nc'] + recipient
        return int(np.flatnonzero(self.agg['pair_key'] == key)[0])

    def test_direct_and_independent_amounts_are_kept_apart(self):
        row = self.pair(1, B, X)
        self.assertEqual(self.agg['direct'][row], 700.0)
        self.assertEqual(self.agg['ie_support'][row], 90.0)
        self.assertEqual(self.agg['transactions'][row], 1.0)   # the IE does not count as a contribution

    def test_giver_totals(self):
        key = 1 * self.agg['nc'] + A
        row = int(np.flatnonzero(self.agg['giver_key'] == key)[0])
        self.assertEqual(self.agg['giver_direct'][row], 2300.0)
        self.assertEqual(self.agg['giver_recipients'][row], 2.0)
        self.assertEqual(self.agg['giver_transactions'][row], 4.0)

    def test_repeat_and_retention_rates_are_keyed_by_the_later_cycle(self):
        nc = self.agg['nc']
        row = int(np.flatnonzero(self.agg['giver_repeat_key'] == 1 * nc + A)[0])
        self.assertAlmostEqual(self.agg['giver_repeat_rate'][row], 0.5)     # of X and Y, only X again
        row = int(np.flatnonzero(self.agg['recipient_retention_key'] == 1 * nc + X)[0])
        self.assertAlmostEqual(self.agg['recipient_retention_rate'][row], 1.0)


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class BuildTests(unittest.TestCase):
    def setUp(self):
        from worldmodel.embedding.domains.fec import build_tasks
        self.tasks = build_tasks(synthetic_data(), first_cycle=1, last_cycle=1, per_cycle=100, per_giver=10,
                                 history=2, log=None)
        self.rows = {(int(g), int(r)): i for i, (g, r) in enumerate(zip(self.tasks.manager, self.tasks.security))}

    def test_every_pair_that_gave_in_the_cycle_is_a_sample(self):
        self.assertEqual(set(self.rows), {(A, X), (A, Z), (B, X)})

    def test_label_is_giving_again_in_the_next_cycle(self):
        self.assertEqual(self.tasks.y[self.rows[(A, X)], 0], 1.0)
        self.assertEqual(self.tasks.y[self.rows[(A, Z)], 0], 0.0)
        self.assertEqual(self.tasks.y[self.rows[(B, X)], 0], 0.0)

    def test_label_is_dated_by_the_public_date_of_the_label_cycle(self):
        from worldmodel.embedding.domains.fec import cycle_public_day
        self.assertTrue((self.tasks.label_filed == cycle_public_day(2)).all())
        self.assertEqual(self.tasks.leakage_violations, 0)

    def test_the_own_rate_baseline_is_the_givers_repeat_rate_into_this_cycle(self):
        self.assertAlmostEqual(float(self.tasks.base_manager[self.rows[(A, X)], 0]), 0.5)
        self.assertAlmostEqual(float(self.tasks.base_manager[self.rows[(A, Z)], 0]), 0.5)
        self.assertTrue(np.isnan(self.tasks.base_manager[self.rows[(B, X)], 0]))   # B gave nothing before

    def test_pair_history_reaches_back_a_cycle_and_no_further(self):
        from worldmodel.embedding.domains.fec import FEATURES
        j = FEATURES.index('pair_gave')
        row = self.rows[(A, X)]
        self.assertEqual(self.tasks.x[row, 0, j, 0], 1.0)    # gave in cycle 1
        self.assertEqual(self.tasks.x[row, 0, j, 1], 1.0)    # and in cycle 0
        self.assertEqual(self.tasks.x[self.rows[(A, Z)], 0, j, 1], 0.0)   # A gave nothing to Z in cycle 0

    def test_features_never_reach_into_the_label_cycle(self):
        """Cycle 2 holds one contribution of 100 from A to X; no feature may move when it changes."""
        from worldmodel.embedding.domains.fec import build_tasks
        data = synthetic_data()
        data['amount'] = data['amount'].copy()
        data['amount'][-1] = 999999.0                       # the cycle-2 row
        other = build_tasks(data, first_cycle=1, last_cycle=1, per_cycle=100, per_giver=10, history=2, log=None)
        np.testing.assert_array_equal(self.tasks.x, other.x)
        np.testing.assert_array_equal(self.tasks.m, other.m)

    def test_neighbours_are_the_other_recipients_and_other_givers_of_the_cycle(self):
        from worldmodel.embedding.domains.fec import K
        row = self.rows[(A, X)]
        self.assertTrue(self.tasks.node_valid[row, 3])          # Z, A's only other recipient
        self.assertFalse(self.tasks.node_valid[row, 4])
        self.assertTrue(self.tasks.node_valid[row, 3 + K])      # B, X's only other giver
        self.assertFalse(self.tasks.node_valid[row, 4 + K])
        self.assertAlmostEqual(float(self.tasks.weights[row, 4]), 300 / 2300, places=5)
        self.assertAlmostEqual(float(self.tasks.weights[row, 6]), 700 / 2700, places=5)

    def test_template_shape_and_node_types(self):
        from worldmodel.embedding.domains.fec import K, NODE_TYPES, template
        n, edges, node_type = template()
        self.assertEqual(n, 3 + 2 * K)
        self.assertEqual(self.tasks.x.shape[1], n)
        self.assertEqual(int(node_type[0]), NODE_TYPES.index('pair'))
        self.assertEqual(int(node_type[1]), NODE_TYPES.index('giver'))
        self.assertEqual(int(node_type[2]), NODE_TYPES.index('recipient'))
        self.assertEqual(int(node_type[3]), NODE_TYPES.index('recipient'))
        self.assertEqual(int(node_type[3 + K]), NODE_TYPES.index('giver'))
        self.assertEqual(self.tasks.weights.shape[1], len(edges))

    def test_same_state_and_same_party_come_from_the_cycles_registration(self):
        from worldmodel.embedding.domains.fec import FEATURES
        row = self.rows[(A, X)]
        self.assertEqual(self.tasks.x[row, 0, FEATURES.index('pair_same_state'), 0], 1.0)
        self.assertEqual(self.tasks.x[row, 0, FEATURES.index('pair_same_party'), 0], 1.0)
        self.assertEqual(self.tasks.x[self.rows[(B, X)], 0, FEATURES.index('pair_same_state'), 0], 0.0)


if __name__ == '__main__':
    unittest.main()
