import unittest

from worldmodel.reconciliation import materialize_beliefs, validate_policy


def evidence(dataset):
    return [{'input': {'dataset': dataset, 'version': 'a' * 64}, 'record_id': dataset + ':1'}]


def obs(key, subject, metric, value, dataset, observed_at='2024-01-01', unit='people', **extra):
    return {'id': key, 'kind': 'observation', 'subject': subject, 'metric': metric, 'value': value, 'unit': unit,
            'dimensions': {}, 'observed_at': observed_at, 'evidence': evidence(dataset), **extra}


def claim(key, subject, predicate, value, dataset='ofac', observed_at='2024-01-01', **extra):
    return {'id': key, 'kind': 'assertion', 'subject': subject, 'predicate': predicate, 'value': value,
            'observed_at': observed_at, 'evidence': evidence(dataset), **extra}


class BeliefTests(unittest.TestCase):
    def rows(self):
        period = {'valid_from': '2023-01-01', 'valid_to': '2024-01-01'}
        return [obs('o:1', 'e:a', 'employment', 100, 'bls', **period),
                obs('o:2', 'e:a', 'employment', 120, 'census', observed_at='2024-06-01', **period),
                obs('o:3', 'e:b', 'employment', None, 'bls', missing_reason='suppressed'),
                claim('a:1', 'e:c', 'sanctioned', False), claim('a:2', 'e:d', 'sanctioned', True),
                claim('a:3', 'e:d', 'retraction_notice', 'delisted', retracts=['a:2'], observed_at='2024-03-01'),
                obs('o:4', 'e:e', 'sales', 3, 'eia', unit='MWh', observed_at='2020-01-01'),
                obs('o:5', 'e:e', 'sales', 3000, 'eia2', unit='kWh', observed_at='2020-01-01'),
                obs('o:6', 'e:f', 'employment', 10, 'bls'), obs('o:7', 'e:f', 'employment', 11, 'bls', supersedes=['o:6'],
                                                               observed_at='2024-02-01')]

    def beliefs(self, **kwargs):
        result = materialize_beliefs(self.rows(), known_at='2025-01-01', **kwargs)
        return result, {(b['subject'], b['variable']): b for b in result['beliefs']}

    def test_latest_vintage_retraction_unknown_false_and_supersession(self):
        result, beliefs = self.beliefs()
        self.assertEqual((beliefs['e:a', 'employment']['status'], beliefs['e:a', 'employment']['value']), ('known', 120))
        self.assertEqual(beliefs['e:b', 'employment']['status'], 'unknown')
        self.assertEqual(beliefs['e:b', 'employment']['unknown_record_ids'], ['o:3'])
        self.assertEqual((beliefs['e:c', 'sanctioned']['status'], beliefs['e:c', 'sanctioned']['truth_value']), ('known', False))
        self.assertEqual(beliefs['e:d', 'sanctioned']['status'], 'retracted')
        self.assertEqual(beliefs['e:d', 'sanctioned']['retracted_by'], {'a:2': ['a:3']})
        self.assertEqual(beliefs['e:e', 'sales']['status'], 'conflicting')
        self.assertEqual(beliefs['e:f', 'employment']['value'], 11)
        self.assertEqual(beliefs['e:f', 'employment']['superseded_record_ids'], ['o:6'])
        self.assertEqual(result['conflicts'][0]['subject'], 'e:e')
        before = materialize_beliefs(self.rows(), known_at='2024-02-01')
        self.assertEqual({(b['subject'], b['variable']): b for b in before['beliefs']}['e:d', 'sanctioned']['truth_value'], True)

    def test_reliability_units_staleness_and_determinism(self):
        policy = {'rule': 'reliability_weighted', 'source_reliability': {'bls': 0.9, 'census': 0.3}, 'stale_after_days': 365,
                  'units': {'sales': 'MWh'}}
        result, beliefs = self.beliefs(policy=policy)
        employment = beliefs['e:a', 'employment']
        self.assertEqual(employment['status'], 'conflicting')
        self.assertEqual(employment['leading_value'], 100)
        self.assertAlmostEqual(employment['confidence'], 0.75)
        self.assertEqual((beliefs['e:e', 'sales']['status'], beliefs['e:e', 'sales']['value']), ('known', 3))
        self.assertTrue(beliefs['e:e', 'sales']['stale'])
        lenient = materialize_beliefs(self.rows(), known_at='2025-01-01', policy={**policy, 'conflict_share': 0.3})
        self.assertEqual({(b['subject'], b['variable']): b for b in lenient['beliefs']}['e:a', 'employment']['value'], 100)
        self.assertEqual(materialize_beliefs(list(reversed(self.rows())), known_at='2025-01-01', policy=policy)['digest'], result['digest'])
        priority, beliefs = self.beliefs(policy={'rule': 'source_priority', 'source_priority': ['census', 'bls']})
        self.assertEqual(beliefs['e:a', 'employment']['value'], 120)

    def test_policy_validation_and_inferred_exclusion(self):
        with self.assertRaises(ValueError):
            validate_policy({'rule': 'vibes'})
        with self.assertRaises(ValueError):
            validate_policy({'source_reliability': {'x': 0}})
        with self.assertRaises(ValueError):
            validate_policy({'typo': 1})
        rows = [claim('a:1', 'e:a', 'sanctioned', True, epistemic_status='inferred')]
        self.assertEqual(materialize_beliefs(rows, known_at='2025-01-01')['beliefs'], [])
        self.assertEqual(len(materialize_beliefs(rows, known_at='2025-01-01', policy={'include_inferred': True})['beliefs']), 1)
        # Unbounded validity is not excluded by a world-time filter; bounded 2023 claims are.
        window = materialize_beliefs(self.rows(), known_at='2025-01-01', at='2024-06-01', variables={'employment'})
        self.assertEqual({b['subject'] for b in window['beliefs']}, {'e:b', 'e:f'})


if __name__ == '__main__':
    unittest.main()
