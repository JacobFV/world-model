import copy
import unittest
from worldmodel.identity import IdentityIndex


def entity(key, typ='person', label='Shared name'):
    return dict(id=key, kind='entity', entity_type=typ, label=label,
                observed_at='2020-01-01', evidence=[{'source': 'fixture'}])


def claim(key, subject, predicate, value=None, **extra):
    r = dict(id=key, kind='assertion', subject=subject, predicate=predicate,
             observed_at='2020-01-01', evidence=[{'source': key}])
    r.update({'object': extra.pop('object')} if 'object' in extra else {'value': value})
    r.update(extra)
    return r


def assignment(key, subject, namespace, value, **extra):
    return claim(key, subject, 'identifier_assignment', dict(namespace=namespace, value=value), **extra)


class IdentityTests(unittest.TestCase):
    def test_ticker_reuse_boundary_and_ambiguity(self):
        records = [entity('listing:a', 'listing'), entity('listing:b', 'listing'),
                   assignment('id:a', 'listing:a', 'ticker', 'abc', valid_from='2000-01-01', valid_to='2021-01-01'),
                   assignment('id:b', 'listing:b', 'ticker', 'ABC', valid_from='2021-01-01', valid_to='2030-01-01')]
        idx = IdentityIndex(records)
        self.assertEqual(idx.resolve_identifier('TICKER', ' abc ', '2020-06-01', '2022-01-01')['matches'][0]['canonical_id'], 'listing:a')
        self.assertEqual(idx.resolve_identifier('ticker', 'ABC', '2021-01-01', '2022-01-01')['matches'][0]['canonical_id'], 'listing:b')
        records.append(assignment('id:c', 'listing:a', 'ticker', 'ABC', valid_from='2021-01-01', valid_to='2030-01-01'))
        self.assertEqual(IdentityIndex(records).resolve_identifier('ticker', 'ABC', '2022-01-01', '2022-01-01')['status'], 'ambiguous')

    def test_knowledge_cutoff_equivalence_and_evidence_immutability(self):
        records = [entity('person:b'), entity('person:a'),
                   assignment('id:a', 'person:b', 'bioguide', 'a000001'),
                   claim('eq:a', 'person:a', 'same_as', object='person:b', observed_at='2022-01-01')]
        original = copy.deepcopy(records)
        idx = IdentityIndex(records)
        before = idx.resolve_identifier('bioguide', 'A000001', '2020-01-01', '2021-01-01')
        after = idx.resolve_identifier('bioguide', 'A000001', '2020-01-01', '2023-01-01')
        self.assertEqual(before['matches'][0]['canonical_id'], 'person:b')
        self.assertEqual(after['matches'][0]['canonical_id'], 'person:a')
        self.assertIn('eq:a', [r['id'] for r in after['matches'][0]['evidence']])
        after['matches'][0]['evidence'][0]['id'] = 'mutated'
        self.assertEqual(records, original)
        self.assertEqual(idx.resolve_identifier('bioguide', 'A000001', '2020-01-01', '2023-01-01')['matches'][0]['canonical_id'], 'person:a')

    def test_alias_search_does_not_merge_and_filters_time(self):
        idx = IdentityIndex([entity('person:a'), entity('person:b'),
                            claim('alias:a', 'person:a', 'alias', 'Old nickname', valid_to='2021-01-01')])
        self.assertEqual(len(idx.search('shared', '2020-01-01', '2020-01-01')), 2)
        self.assertEqual(len(idx.search('nickname', '2020-01-01', '2020-01-01')), 1)
        self.assertEqual(idx.search('nickname', '2022-01-01', '2022-01-01'), [])

    def test_reject_type_conflicting_equivalence_and_unique_conflicts(self):
        with self.assertRaisesRegex(ValueError, 'type'):
            IdentityIndex([entity('person:a'), entity('issuer:a', 'issuer'),
                           claim('eq:a', 'person:a', 'same_as', object='issuer:a')])
        with self.assertRaisesRegex(ValueError, 'unique'):
            IdentityIndex([entity('person:a'), entity('person:b'),
                           assignment('id:a', 'person:a', 'bioguide', 'A000001'),
                           assignment('id:b', 'person:b', 'bioguide', 'a000001')])

    def test_explicit_equivalence_permits_shared_unique_id(self):
        idx = IdentityIndex([entity('person:a'), entity('person:b'),
                             assignment('id:a', 'person:a', 'bioguide', 'A000001'),
                             assignment('id:b', 'person:b', 'bioguide', 'a000001'),
                             claim('eq:a', 'person:a', 'same_as', object='person:b')])
        self.assertEqual(idx.resolve_identifier('bioguide', 'A000001', '2020-01-01', '2020-01-01')['status'], 'resolved')

    def test_namespace_normalization_unknown_case_and_missing(self):
        idx = IdentityIndex([entity('issuer:a', 'issuer'), assignment('id:a', 'issuer:a', 'sec_cik', '0000123'),
                             assignment('id:b', 'issuer:a', 'local', 'AbC')])
        self.assertEqual(idx.resolve_identifier(' SEC_CIK ', '123', '2020-01-01', '2020-01-01')['status'], 'resolved')
        self.assertEqual(idx.resolve_identifier('local', 'abc', '2020-01-01', '2020-01-01')['status'], 'not_found')
        self.assertEqual(idx.search('', '2020-01-01', '2020-01-01', limit=0), [])

    def test_scope_filters_ambiguous_ticker(self):
        a = assignment('id:a', 'listing:a', 'ticker', 'ABC', valid_from='2000-01-01', valid_to='2030-01-01')
        b = assignment('id:b', 'listing:b', 'ticker', 'ABC', valid_from='2000-01-01', valid_to='2030-01-01')
        a['value']['scope'], b['value']['scope'] = 'XNYS', 'XNAS'
        idx = IdentityIndex([entity('listing:a', 'listing'), entity('listing:b', 'listing'), a, b])
        self.assertEqual(len(idx.resolve_identifier('ticker', 'ABC', '2020-01-01', '2020-01-01')['conflicts']), 1)
        self.assertEqual(idx.resolve_identifier('ticker', 'ABC', '2020-01-01', '2020-01-01', scope='xnas')['matches'][0]['canonical_id'], 'listing:b')

    def test_transitive_type_conflict_rejected(self):
        with self.assertRaisesRegex(ValueError, 'type'):
            IdentityIndex([entity('person:a'), entity('entity:a', 'entity'), entity('org:a', 'organization'),
                           claim('eq:a', 'person:a', 'same_as', object='entity:a'),
                           claim('eq:b', 'entity:a', 'same_as', object='org:a')])

    def test_unique_conflict_cannot_be_hidden_by_future_equivalence(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            IdentityIndex([entity('person:a'), entity('person:b'),
                           assignment('id:a', 'person:a', 'bioguide', 'A000001'),
                           assignment('id:b', 'person:b', 'bioguide', 'A000001'),
                           claim('eq:a', 'person:a', 'same_as', object='person:b', observed_at='2022-01-01')])

    def test_one_entity_cannot_hold_two_unique_ids_concurrently(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            IdentityIndex([entity('person:a'), assignment('id:a', 'person:a', 'bioguide', 'A000001'),
                           assignment('id:b', 'person:a', 'bioguide', 'A000002')])

    def test_independent_identifiers_do_not_rebuild_temporal_graph_per_pair(self):
        # Profiling real execution guards the scaling contract without mocking resolution.
        import cProfile
        import pstats
        rows = []
        for i in range(40):
            key = f'person:{i}'
            rows.extend([entity(key), assignment(f'id:{i}', key, 'bioguide', f'A{i:06d}'),
                         assignment(f'duplicate:{i}', key, 'bioguide', f'A{i:06d}')])
        profiler = cProfile.Profile()
        idx = profiler.runcall(IdentityIndex, rows)
        calls = sum(stats[1] for (_, _, name), stats in pstats.Stats(profiler).stats.items()
                    if name == '_components')
        self.assertLessEqual(calls, 2)
        self.assertEqual(idx.resolve_identifier('bioguide', 'A000001', '2020-01-01', '2020-01-01')['status'], 'resolved')

    def test_future_equivalence_cannot_hide_conflicting_distinct_identifiers(self):
        with self.assertRaisesRegex(ValueError, 'unique'):
            IdentityIndex([entity('person:a'), entity('person:b'),
                           assignment('id:a', 'person:a', 'bioguide', 'A000001'),
                           assignment('id:b', 'person:b', 'bioguide', 'A000002'),
                           claim('eq:a', 'person:a', 'same_as', object='person:b', observed_at='2022-01-01')])

    def test_undated_ticker_is_not_confirmed_in_past_or_future(self):
        idx = IdentityIndex([entity('listing:a', 'listing'), assignment('id:a', 'listing:a', 'ticker', 'ABC')])
        for at in ('1900-01-01', '2020-01-01', '2099-01-01'):
            result = idx.resolve_identifier('ticker', 'ABC', at, '2021-01-01')
            self.assertEqual(result['status'], 'temporal_unknown')
            self.assertEqual(result['matches'], [])
            self.assertEqual(result['temporal_unknown_candidates'][0]['temporal_validity'], 'unknown')
        self.assertEqual(idx.resolve_identifier('ticker', 'ABC', '1900-01-01', '1900-01-01')['status'], 'not_found')

    def test_two_undated_snapshots_retain_candidates_and_knowledge_cutoff(self):
        idx = IdentityIndex([entity('listing:a', 'listing'), entity('listing:b', 'listing'),
                             assignment('id:a', 'listing:a', 'ticker', 'ABC'),
                             assignment('id:b', 'listing:b', 'ticker', 'ABC', observed_at='2022-01-01')])
        old = idx.resolve_identifier('ticker', 'ABC', '1900-01-01', '2021-01-01')
        new = idx.resolve_identifier('ticker', 'ABC', '2099-01-01', '2023-01-01')
        self.assertEqual(len(old['temporal_unknown_candidates']), 1)
        self.assertEqual(len(new['temporal_unknown_candidates']), 2)
        self.assertEqual(new['status'], 'temporal_unknown')
        self.assertEqual(new['matches'], [])
        self.assertTrue(new['conflicts'])

    def test_partial_ticker_bounds_exclude_outside_and_unknown_inside(self):
        idx = IdentityIndex([entity('listing:a', 'listing'),
                             assignment('id:a', 'listing:a', 'ticker', 'ABC', valid_from='2020-01-01')])
        self.assertEqual(idx.resolve_identifier('ticker', 'ABC', '1900-01-01', '2021-01-01')['status'], 'not_found')
        self.assertEqual(idx.resolve_identifier('ticker', 'ABC', '2099-01-01', '2021-01-01')['status'], 'temporal_unknown')

    def test_undated_durable_identifier_discloses_unknown_validity(self):
        idx = IdentityIndex([entity('person:a'), assignment('id:a', 'person:a', 'bioguide', 'A000001')])
        result = idx.resolve_identifier('bioguide', 'A000001', '1900-01-01', '2021-01-01')
        self.assertEqual(result['status'], 'resolved')
        self.assertEqual(result['matches'][0]['temporal_validity'], 'unknown')

    def test_confirmed_ticker_with_unknown_alternative_is_ambiguous(self):
        idx = IdentityIndex([entity('listing:a', 'listing'), entity('listing:b', 'listing'),
                             assignment('id:a', 'listing:a', 'ticker', 'ABC', valid_from='2000-01-01', valid_to='2030-01-01'),
                             assignment('id:b', 'listing:b', 'ticker', 'ABC')])
        result = idx.resolve_identifier('ticker', 'ABC', '2020-01-01', '2020-01-01')
        self.assertEqual(result['status'], 'ambiguous')
        self.assertEqual(result['matches'][0]['temporal_validity'], 'confirmed')
        self.assertEqual(len(result['matches']), 1)
        self.assertEqual(len(result['temporal_unknown_candidates']), 1)
