import unittest

try:
    import numpy as np
except ImportError:  # the optional ``embed`` extra is not installed
    np = None


def synthetic_arrays():
    """Three managers over three quarters, with a security that already exists before it is opened.

    q0: m0 holds s0 and s1, m1 holds s0, m2 holds s2 (so s2 exists and is a valid candidate).
    q1: m1 also holds s2 -- the one opened position. q2 repeats q1.
    """
    from worldmodel.embedding.holdings import quarter_end
    from datetime import date, timedelta
    rows = []

    def day(q, days):
        d = date.fromisoformat(quarter_end(q)) + timedelta(days=days)
        return d.year * 10000 + d.month * 100 + d.day

    def add(q, m, s, shares):
        rows.append((q, m, s, shares, shares * 10.0, day(q, 30)))

    for q in (0, 1, 2):
        add(q, 0, 0, 100); add(q, 0, 1, 50); add(q, 1, 0, 200); add(q, 2, 2, 70)
        if q >= 1:
            add(q, 1, 2, 30)
    rows.sort()
    q, m, s, sh, v, f = map(np.array, zip(*rows))
    return {'quarter': q.astype(np.int16), 'manager': m.astype(np.int32), 'security': s.astype(np.int32),
            'shares': sh.astype(float), 'value': v.astype(float), 'filed': f.astype(np.int32),
            'manager_ids': np.array(['a', 'b', 'c']), 'security_ids': np.array(['A', 'B', 'C'])}



@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class CandidateTests(unittest.TestCase):
    def setUp(self):
        from worldmodel.embedding.actors import Holdings13F
        self.data = Holdings13F(synthetic_arrays(), top=4)

    def test_a_candidate_is_never_a_position_the_manager_already_holds(self):
        from worldmodel.embedding.newpos import build_tasks
        tasks = build_tasks(self.data, [0, 1], per_quarter=100, per_manager=8, k=2, history=2, log=None)
        for m, s, q in zip(tasks.manager, tasks.security, tasks.quarter):
            held = self.data.quarters[int(q)]
            key = int(m) * self.data.n_securities + int(s)
            self.assertNotIn(key, held.key.tolist(), f'candidate {m}/{s} was already held at {q}')

    def test_the_label_is_whether_the_position_appears_next_quarter(self):
        from worldmodel.embedding.newpos import build_tasks
        tasks = build_tasks(self.data, [0], per_quarter=100, per_manager=8, k=2, history=2, log=None)
        rows = {(int(m), int(s)): float(y) for m, s, y in zip(tasks.manager, tasks.security, tasks.y[:, 0])}
        # Manager 1 holds only s0 at q0 and reports s2 as well at q1, so (1, 2) is a positive.
        self.assertEqual(rows.get((1, 2)), 1.0)
        self.assertTrue(all(v in (0.0, 1.0) for v in rows.values()))

    def test_the_query_position_node_carries_no_history(self):
        from worldmodel.embedding.newpos import build_tasks
        from worldmodel.embedding.actors import FEATURES
        tasks = build_tasks(self.data, [0, 1], per_quarter=100, per_manager=8, k=2, history=2, log=None)
        shares = FEATURES.index('pos_log_shares')
        # Node 0 is the candidate: the manager does not hold it, so its own shares are never known.
        self.assertEqual(tasks.m[:, 0, shares, 0].max(), 0)
        # The manager and security nodes are populated.
        self.assertGreater(tasks.m[:, 1, FEATURES.index('mgr_log_value'), 0].max(), 0)

    def test_security_entry_rate_is_a_probability_over_non_holders(self):
        from worldmodel.embedding.newpos import security_entry_rates
        rates = security_entry_rates(self.data, 1)
        finite = rates[np.isfinite(rates)]
        self.assertTrue(((finite >= 0) & (finite <= 1)).all())


if __name__ == '__main__':
    unittest.main()
