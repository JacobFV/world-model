import unittest

try:
    import numpy as np
except ImportError:  # the optional ``embed`` extra is not installed
    np = None


def synthetic_arrays():
    """Two managers, three securities, quarters 0-3 (2013Q2..2014Q1).

    Manager 0 holds s0 and s1 in q0 and exits s1 in q1. In q3 manager 0 more than doubles s0 while
    manager 1 holds s0 flat. Manager 1 files q1 late (after the 45-day deadline), so its q1 rows are
    invisible to features and to the security-wide median of share changes.
    """
    from worldmodel.embedding.holdings import quarter_end
    rows = []

    def add(q, m, s, shares, filed):
        rows.append((q, m, s, shares, shares * 10.0, filed))

    def day(q, days):
        from datetime import date, timedelta
        d = date.fromisoformat(quarter_end(q)) + timedelta(days=days)
        return d.year * 10000 + d.month * 100 + d.day

    add(0, 0, 0, 100, day(0, 30)); add(0, 0, 1, 50, day(0, 30)); add(0, 1, 0, 200, day(0, 40))
    add(1, 0, 0, 100, day(1, 30)); add(1, 1, 0, 200, day(1, 80)); add(1, 1, 2, 10, day(1, 80))
    add(2, 0, 0, 220, day(2, 30)); add(2, 1, 0, 200, day(2, 30))
    add(3, 0, 0, 500, day(3, 30)); add(3, 1, 0, 200, day(3, 30))
    rows.sort()
    q, m, s, sh, v, f = map(np.array, zip(*rows))
    return {'quarter': q.astype(np.int16), 'manager': m.astype(np.int32), 'security': s.astype(np.int32),
            'shares': sh.astype(float), 'value': v.astype(float), 'filed': f.astype(np.int32),
            'manager_ids': np.array(['0000000001', '0000000002']), 'security_ids': np.array(['A', 'B', 'C'])}


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class QuarterTests(unittest.TestCase):
    def test_quarter_index_round_trips(self):
        from worldmodel.embedding.holdings import quarter_end, quarter_index
        self.assertEqual(quarter_end(0), '2013-06-30')
        self.assertEqual(quarter_end(3), '2014-03-31')
        self.assertEqual(quarter_index(2014, 3), 3)
        self.assertEqual(quarter_index(2013, 6), 0)


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class HoldingsTests(unittest.TestCase):
    def setUp(self):
        from worldmodel.embedding.actors import Holdings13F
        self.data = Holdings13F(synthetic_arrays(), top=4)

    def test_late_filings_are_invisible_to_features_but_kept_for_labels(self):
        self.assertEqual(self.data.late_rows_dropped, 2)
        self.assertFalse(self.data.quarters[1].filed_managers[1])
        self.assertTrue(self.data.all_rows[1]['filed_managers'][1])

    def test_manager_exit_rate(self):
        # Manager 0 held s0, s1 at q0 and only s0 at q1: exit rate 0.5.
        self.assertAlmostEqual(self.data.quarters[1].mgr[0, 2], 0.5)

    def test_position_features_mark_absence_only_where_the_manager_filed(self):
        f = self.data.position_features(1, np.array([0, 1]), np.array([1, 0]))
        self.assertEqual(f[0, 3], 0.0)          # manager 0 filed q1 and no longer holds s1
        self.assertTrue(np.isnan(f[1, 3]))      # manager 1's q1 filing is late: unknown, not absent


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class TaskTests(unittest.TestCase):
    def test_exit_and_split_robust_increase_labels(self):
        from worldmodel.embedding.actors import Holdings13F, build_tasks
        data = Holdings13F(synthetic_arrays(), top=4)
        tasks = build_tasks(data, [0, 1, 2], per_quarter=100, per_manager=10, k=2, history=2, seed=0)
        rows = {(int(q), int(m), int(s)): i for i, (q, m, s) in enumerate(zip(tasks.quarter, tasks.manager, tasks.security))}
        self.assertEqual(tasks.y[rows[(0, 0, 1)], 0], 1.0)      # s1 exited at q1
        self.assertEqual(tasks.y[rows[(0, 0, 0)], 0], 0.0)
        # q2 -> q3: manager 0 grows s0 by 127% while the other holder is flat: an increase; the flat
        # holder is not one.
        self.assertEqual(tasks.y[rows[(2, 0, 0)], 1], 1.0)
        self.assertEqual(tasks.y[rows[(2, 1, 0)], 1], 0.0)
        self.assertNotIn((1, 1, 0), rows)                        # late q1 filing: not a visible sample
        self.assertEqual(tasks.x.shape[1], 3 + 3 * 2)

    def test_template_edges_connect_the_query_position(self):
        from worldmodel.embedding.actors import template
        n, edges, node_type = template(2)
        self.assertEqual(n, 9)
        self.assertIn([0, 1], edges[:, :2].tolist())
        self.assertIn([0, 2], edges[:, :2].tolist())
        self.assertEqual(node_type.tolist(), [0, 1, 2, 0, 0, 0, 0, 2, 2])


if __name__ == '__main__':
    unittest.main()
