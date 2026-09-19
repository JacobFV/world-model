"""Evidence claims are re-derived from published reports and can only be lowered."""
import unittest

from worldmodel.decision.contract import validate_contract
from worldmodel.decision.evidence import EvidenceIndex, recommendation_label, resolve_mechanisms
from test_decision_support import BAD, GOOD, OLD, SyntheticStore, monetary_contract


def resolve(contract, index):
    return resolve_mechanisms(validate_contract(contract), index)['policy_rate_reaction']


class EvidenceResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture = SyntheticStore()
        cls.index = cls.fixture.index()

    @classmethod
    def tearDownClass(cls):
        cls.fixture.close()

    def test_index_reads_and_reverifies_every_published_report(self):
        summary = self.index.summary()
        self.assertEqual(summary['source'], 'store')
        self.assertEqual(summary['reports'], 3)
        self.assertEqual(summary['validated_processes'], ['monetary_model'])
        good = self.index.get(self.fixture.good['report_id'])
        self.assertTrue(good['verified'] and good['passed'] and good['current'] and good['counted'])
        self.assertEqual(good['attempt'], GOOD)
        old = self.index.get(self.fixture.old['report_id'])
        self.assertEqual((old['attempt'], old['current'], old['superseded_by']), (OLD, False, GOOD))

    def test_a_validated_claim_stands_only_on_a_current_passing_report(self):
        resolution = resolve(monetary_contract('validated', self.fixture.good['report_id']), self.index)
        self.assertEqual(resolution['status'], 'validated')
        self.assertTrue(resolution['verified'])
        self.assertEqual(resolution['scope']['horizon'], 1)
        self.assertEqual(resolution['scope']['conditional_inputs'], ['inflation', 'output_gap'])
        self.assertTrue(any(BAD in caveat and 'no_revision_leakage' in caveat for caveat in resolution['caveats']))

    def test_a_validated_claim_on_a_failing_report_is_lowered(self):
        resolution = resolve(monetary_contract('validated', self.fixture.bad['report_id']), self.index)
        self.assertEqual(resolution['status'], 'estimated but not validated')
        self.assertTrue(any('fails no_revision_leakage' in reason for reason in resolution['reasons']))

    def test_a_validated_claim_on_a_superseded_attempt_is_lowered(self):
        resolution = resolve(monetary_contract('validated', self.fixture.old['report_id']), self.index)
        self.assertEqual(resolution['status'], 'estimated but not validated')
        self.assertTrue(any('superseded by' in reason for reason in resolution['reasons']))

    def test_a_missing_report_makes_the_mechanism_assumed(self):
        resolution = resolve(monetary_contract('validated', 'f' * 64), self.index)
        self.assertEqual(resolution['status'], 'assumed')
        self.assertFalse(resolution['verified'])

    def test_a_component_mismatch_makes_the_mechanism_assumed(self):
        contract = monetary_contract('validated', self.fixture.good['report_id'])
        contract['mechanisms']['policy_rate_reaction']['evidence']['component'] = 'monetary_model_parameters'
        index = EvidenceIndex(dict(self.index.entries), validated_processes=self.index.validated_processes, source='store')
        entry = dict(index.entries[self.fixture.good['report_id']], component='conflict_model_parameters')
        index.entries[self.fixture.good['report_id']] = entry
        self.assertEqual(resolve(contract, index)['status'], 'assumed')

    def test_a_lower_claim_is_never_raised(self):
        contract = monetary_contract('validated', self.fixture.good['report_id'])
        contract['mechanisms']['policy_rate_reaction']['evidence']['status'] = 'estimated but not validated'
        resolution = resolve(contract, self.index)
        self.assertEqual(resolution['status'], 'estimated but not validated')
        self.assertTrue(any('lower claim stands' in caveat for caveat in resolution['caveats']))

    def test_without_a_store_no_report_backed_claim_is_confirmed(self):
        resolution = resolve(monetary_contract('validated', self.fixture.good['report_id']), None)
        self.assertEqual(resolution['status'], 'estimated but not validated')

    def test_labels_follow_the_weakest_mechanism(self):
        validated = {'a': {'status': 'validated'}}
        mixed = {'a': {'status': 'validated'}, 'b': {'status': 'assumed'}, 'c': {'status': 'estimated but not validated'}}
        self.assertEqual(recommendation_label(validated)['label'], 'conditional_on_validated_fitted_dynamics')
        self.assertIn('not a validated counterfactual response', recommendation_label(validated)['text'])
        label = recommendation_label(mixed)
        self.assertEqual(label['label'], 'rests_on_unvalidated_mechanisms')
        self.assertEqual(label['unvalidated_mechanisms'], ['b', 'c'])
        self.assertTrue(label['text'].startswith('UNVALIDATED'))


if __name__ == '__main__':
    unittest.main()
