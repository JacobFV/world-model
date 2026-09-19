import unittest
from datetime import date

try:
    import numpy as np
except ImportError:  # the optional ``embed`` extra is not installed
    np = None


def synthetic_data():
    """Three roll calls in one chamber: five members, one of whom defects on the second.

    Members 1, 2 and 5 are Democrats, 3 and 4 Republicans. Roll call A (2019-01-10) is party-unity and every
    member votes with their party. Roll call B (2019-05-10) is party-unity (Democrats mostly nay, Republicans
    yea) and member 2 defects by voting yea. Roll call C (2019-05-20) splits the Republicans evenly, so
    neither party has a majority and it is not a party-unity vote: it produces no samples.
    """
    from worldmodel.embedding.votes import DEMOCRAT, REPUBLICAN
    positions = {
        'H116:1': (date(2019, 1, 10), 'House', 116, [1, 2, 5], [3, 4]),
        'H116:2': (date(2019, 5, 10), 'House', 116, [2, 3, 4], [1, 5]),
        'H116:3': (date(2019, 5, 20), 'House', 116, [1, 2, 5, 3], [4]),
    }
    rollcalls = {'H116:1': ('On Passage', '1/2', 'HR1'), 'H116:2': ('On Motion to Recommit', '1/2', 'HR2'),
                 'H116:3': ('On Passage', '1/2', 'HR3')}
    party, state = {}, {}
    for m, p in ((1, DEMOCRAT), (2, DEMOCRAT), (5, DEMOCRAT), (3, REPUBLICAN), (4, REPUBLICAN)):
        party[(116, 'House', m)] = p
        state[(116, 'House', m)] = 'CA' if m in (1, 2, 5) else 'TX'
    return {'ref': {'dataset': 'voteview_rollcalls'}, 'bills_ref': {'dataset': 'influence_panel'},
            'rollcalls': rollcalls, 'positions': positions, 'party': party, 'state': state,
            'bill_of': {'H116:2': (3, date(2019, 4, 1))}}


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class CategoryTests(unittest.TestCase):
    def test_question_categories(self):
        from worldmodel.embedding.votes import category
        self.assertEqual(category('On Passage of the Bill', 'HR1'), 'passage')
        self.assertEqual(category('On the Cloture Motion', 'S100'), 'cloture')
        self.assertEqual(category('On the Nomination', 'PN123'), 'nomination')
        self.assertEqual(category('On Motion to Suspend the Rules and Pass', 'HR2'), 'suspension')
        self.assertEqual(category('Something else entirely', None), 'other')

    def test_quarter_arithmetic(self):
        from worldmodel.embedding.votes import quarter_end, quarter_of, quarter_start_ordinal
        self.assertEqual(quarter_of(date(2007, 1, 5)), 0)
        self.assertEqual(quarter_end(0), '2007-03-31')
        self.assertEqual(quarter_of(date(2019, 5, 10)), (2019 - 2007) * 4 + 1)
        self.assertEqual(date.fromordinal(quarter_start_ordinal(quarter_of(date(2019, 5, 10)))), date(2019, 4, 1))


@unittest.skipIf(np is None, 'optional embed extra (numpy) not installed')
class BuildTests(unittest.TestCase):
    def setUp(self):
        from worldmodel.embedding.votes import build_tasks, quarter_of
        self.first = quarter_of(date(2019, 1, 1))
        self.tasks = build_tasks(synthetic_data(), first_quarter=self.first, last_quarter=self.first + 1,
                                 per_quarter=100, per_member=10, history=2, log=None)

    def test_only_party_unity_votes_become_samples(self):
        # Five samples on roll call A and five on B; roll call C is not a unity vote.
        self.assertEqual(len(self.tasks.y), 10)
        self.assertEqual(set(self.tasks.security.tolist()), {0, 1})

    def test_defection_label(self):
        from worldmodel.embedding.votes import FEATURES
        rows = {(int(m), int(r)): i for i, (m, r) in enumerate(zip(self.tasks.manager, self.tasks.security))}
        self.assertEqual(self.tasks.y[rows[(2, 1)], 0], 1.0)      # member 2 voted with the Republicans
        self.assertEqual(self.tasks.y[rows[(1, 1)], 0], 0.0)
        self.assertEqual(self.tasks.y[rows[(2, 0)], 0], 0.0)
        del FEATURES

    def test_labels_are_dated_on_the_vote_and_features_stop_before_it(self):
        from worldmodel.embedding.votes import FEATURES
        rows = {(int(m), int(r)): i for i, (m, r) in enumerate(zip(self.tasks.manager, self.tasks.security))}
        self.assertEqual(self.tasks.label_filed[rows[(2, 1)]], 20190510)
        self.assertEqual(self.tasks.leakage_violations, 0)
        # The member's own rate at roll call A has no prior unity vote; at B it reflects roll call A only.
        self.assertTrue(np.isnan(self.tasks.base_manager[rows[(2, 0)], 0]))
        self.assertEqual(self.tasks.base_manager[rows[(2, 1)], 0], 0.0)
        j = FEATURES.index('mem_defect_rate')
        self.assertEqual(self.tasks.m[rows[(2, 0)], 1, j, 0], 0)   # nothing known before the first vote

    def test_sponsor_node_is_valid_only_when_the_roll_call_has_one(self):
        rows = {(int(m), int(r)): i for i, (m, r) in enumerate(zip(self.tasks.manager, self.tasks.security))}
        self.assertTrue(self.tasks.node_valid[rows[(1, 1)], 3])    # roll call B is on a bill sponsored by member 3
        self.assertFalse(self.tasks.node_valid[rows[(1, 0)], 3])   # roll call A is not linked to a bill

    def test_template_shape(self):
        from worldmodel.embedding.votes import K, template
        n, edges, node_type = template()
        self.assertEqual(n, 4 + 2 * K)
        self.assertEqual(self.tasks.x.shape[1], n)
        self.assertEqual(node_type[0], 0)
        self.assertEqual(node_type[2], 2)


if __name__ == '__main__':
    unittest.main()
