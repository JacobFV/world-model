import tempfile
import unittest
from pathlib import Path

from worldmodel.spatial_store import SpatialStore


def world():
    return {'measure_unit': 'km2', 'cells': [
        {'id': 'cell:a', 'measure': 2, 'coordinates': [0, 0]},
        {'id': 'cell:b', 'measure': 1, 'coordinates': [1, 0]},
        {'id': 'cell:c', 'measure': 3, 'coordinates': [2, 0]}],
        'edges': [
            {'id': 'edge:ab', 'source': 'cell:a', 'target': 'cell:b', 'conductance': .1},
            {'id': 'edge:bc', 'source': 'cell:b', 'target': 'cell:c', 'conductance': .2}],
        'fields': {
            'stock': {'kind': 'extensive', 'unit': 'kg', 'values': {'cell:a': 12, 'cell:b': 3, 'cell:c': 0}},
            'density': {'kind': 'intensive', 'unit': 'kg/km2', 'values': {'cell:a': 6, 'cell:b': 3, 'cell:c': 0}}},
        'claims': [{'id': 'claim:x', 'claimant': 'actor:x', 'cells': ['cell:a'], 'label': 'Unresolved claim'}]}


CARTESIAN = {'kind': 'cartesian', 'axes': ['x', 'y'], 'unit': 'km'}


class SpatialStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'world.sqlite'
        self.store = SpatialStore(self.path)
        self.store.initialize(world(), coordinate_system=CARTESIAN)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_bounded_disk_selection_survives_reopen(self):
        self.store.close()
        self.store = SpatialStore(self.path)
        result = self.store.bbox([0, 0, 2, 0], limit=2)
        self.assertEqual([r['id'] for r in result['cells']], ['cell:a', 'cell:b'])
        self.assertTrue(result['truncated'])
        selected = self.store.select(cells=['cell:b'], fields=['stock'])
        self.assertEqual(selected['state']['fields']['stock']['values'], {'cell:b': 3})
        self.assertEqual(selected['selection']['boundary_edges'], 2)
        self.assertEqual(selected['coordinate_system'], CARTESIAN)

    def test_neighborhood_hops_and_edge_budget_are_honest(self):
        first = self.store.neighborhood('cell:a', hops=1, limit=10)
        self.assertEqual([r['id'] for r in first['cells']], ['cell:a', 'cell:b'])
        self.assertFalse(first['truncated'])
        second = self.store.neighborhood('cell:a', hops=2, limit=10)
        self.assertEqual([r['id'] for r in second['cells']], ['cell:a', 'cell:b', 'cell:c'])
        bounded = self.store.neighborhood('cell:b', hops=1, max_edges=1)
        self.assertTrue(bounded['truncated'])
        self.assertLessEqual(bounded['edges_examined'], 1)

    def test_selected_diffusion_requires_explicit_closed_boundary(self):
        with self.assertRaisesRegex(ValueError, 'boundary'):
            self.store.materialize(cells=['cell:a', 'cell:b'])
        result = self.store.materialize(cells=['cell:a', 'cell:b'], allow_boundary_cut=True,
                                        evolution={'duration_seconds': 1, 'step_seconds': 1})
        self.assertEqual(result['selection']['boundary_edges'], 1)
        self.assertAlmostEqual(result['evolution']['conservation']['stock']['final_integral'], 15)
        self.assertEqual(result['state']['claims'][0]['cells'], ['cell:a'])

    def test_merge_preserves_extensive_and_measure_weighted_intensive(self):
        result = self.store.apply([{'type': 'merge', 'cells': ['cell:a', 'cell:b'],
                                   'cell': {'id': 'cell:ab', 'measure': 3, 'coordinates': [.5, 0]}}])
        selected = self.store.select(cells=['cell:ab', 'cell:c'])['state']
        self.assertEqual(selected['fields']['stock']['values']['cell:ab'], 15)
        self.assertEqual(selected['fields']['density']['values']['cell:ab'], 5)
        self.assertEqual(selected['edges'][0]['source'], 'cell:ab')
        self.assertEqual(selected['edges'][0]['target'], 'cell:c')
        self.assertEqual(selected['claims'][0]['cells'], ['cell:ab'])
        self.assertEqual(result['events'][0]['conservation']['stock']['difference'], 0)

    def test_split_conserves_values_and_requires_explicit_incident_topology(self):
        event = {'type': 'split', 'cell': 'cell:a', 'children': [
            {'id': 'cell:a1', 'measure': .5, 'coordinates': [0, 0]},
            {'id': 'cell:a2', 'measure': 1.5, 'coordinates': [.1, 0]}]}
        with self.assertRaisesRegex(ValueError, 'edges'):
            self.store.apply([event])
        event['edges'] = [{'id': 'edge:a2b', 'source': 'cell:a2', 'target': 'cell:b', 'conductance': .1}]
        self.store.apply([event])
        data = self.store.select(cells=['cell:a1', 'cell:a2'])['state']
        self.assertEqual(data['fields']['stock']['values'], {'cell:a1': 3, 'cell:a2': 9})
        self.assertEqual(data['fields']['density']['values'], {'cell:a1': 6, 'cell:a2': 6})
        self.assertEqual(data['claims'][0]['cells'], ['cell:a1', 'cell:a2'])

    def test_invalid_lifecycle_batch_rolls_back_all_changes_and_audit(self):
        before = self.store.select()['state']
        with self.assertRaises(ValueError):
            self.store.apply([
                {'type': 'birth', 'cell': {'id': 'cell:d', 'measure': 1},
                 'values': {'stock': 0, 'density': 0}},
                {'type': 'split', 'cell': 'cell:b', 'children': [
                    {'id': 'cell:b1', 'measure': 4}], 'edges': []}])
        self.assertEqual(self.store.select()['state'], before)
        self.assertEqual(self.store.audit(), [])

    def test_birth_external_input_is_explicit_and_death_transfers_integrals(self):
        birth = {'type': 'birth', 'cell': {'id': 'cell:d', 'measure': 1},
                 'values': {'stock': 4, 'density': 2}}
        with self.assertRaisesRegex(ValueError, 'external_input'):
            self.store.apply([birth])
        birth['external_input'] = True
        report = self.store.apply([birth])
        self.assertEqual(report['events'][0]['external_input']['stock'], 4)
        with self.assertRaisesRegex(ValueError, 'transfer'):
            self.store.apply([{'type': 'death', 'cell': 'cell:a'}])
        self.store.apply([{'type': 'death', 'cell': 'cell:a', 'transfer_to': 'cell:b'}])
        data = self.store.select()['state']
        self.assertEqual(data['fields']['stock']['values']['cell:b'], 15)
        self.assertEqual(data['fields']['density']['values']['cell:b'], 15)
        self.assertTrue(all(e['source'] != 'cell:a' and e['target'] != 'cell:a' for e in data['edges']))
        self.assertEqual(len(self.store.audit()), 2)

    def test_signed_vectors_store_merge_but_do_not_claim_scalar_diffusion(self):
        self.store.close()
        self.path.unlink()
        data = world()
        data['fields']['velocity'] = {'kind': 'intensive', 'unit': 'm/s', 'value_type': 'vector',
                                     'values': {'cell:a': [-2, 4], 'cell:b': [1, -2], 'cell:c': [0, 0]}}
        self.store = SpatialStore(self.path)
        self.store.initialize(data, coordinate_system=CARTESIAN)
        with self.assertRaisesRegex(ValueError, 'nonnegative scalar'):
            self.store.materialize()
        self.store.apply([{'type': 'merge', 'cells': ['cell:a', 'cell:b'],
                           'cell': {'id': 'cell:ab', 'measure': 3}}])
        self.assertEqual(self.store.select(cells=['cell:ab'])['state']['fields']['velocity']['values']['cell:ab'], [-1, 2])
        result = self.store.materialize(fields=['stock', 'density'])
        self.assertEqual(result['state']['fields']['stock']['values']['cell:ab'], 15)

    def test_geodetic_distance_requires_degree_lat_lon_and_checks_ranges(self):
        with self.assertRaisesRegex(ValueError, 'geodetic'):
            self.store.geodetic_distance('cell:a', 'cell:b')
        self.store.close()
        self.path.unlink()
        self.store = SpatialStore(self.path)
        self.store.initialize(world(), coordinate_system={'kind': 'geodetic', 'axes': ['latitude', 'longitude'], 'unit': 'degree'})
        distance = self.store.geodetic_distance('cell:a', 'cell:b')
        self.assertAlmostEqual(distance['distance_m'], 111195.0802, places=3)
        self.assertIn('sphere', distance['model'])
        with self.assertRaises(ValueError):
            self.store.apply([{'type': 'birth', 'cell': {'id': 'cell:bad', 'measure': 1, 'coordinates': [91, 0]},
                               'values': {'stock': 0, 'density': 0}}])

    def test_invalid_endpoints_and_oversized_queries_rejected(self):
        with self.assertRaises(ValueError):
            self.store.apply([{'type': 'split', 'cell': 'cell:a', 'children': [
                {'id': 'cell:a1', 'measure': 2}], 'edges': [
                {'id': 'edge:bad', 'source': 'cell:a1', 'target': 'cell:missing'}]}])
        self.assertEqual(self.store.select(cells=['cell:a'])['state']['fields']['stock']['values'], {'cell:a': 12})
        for bounds in ([2, 0, 1, 0], [0, 0, float('nan'), 1]):
            with self.assertRaises(ValueError):
                self.store.bbox(bounds)
        from worldmodel.limits import LimitExceeded, use_limits
        self.assertEqual(len(self.store.select(limit=1001)['state']['cells']), 3)
        with use_limits(spatial_max_query_rows=1000):
            with self.assertRaisesRegex(LimitExceeded, 'spatial_max_query_rows'):
                self.store.select(limit=1001)
        with self.assertRaises(ValueError):
            SpatialStore(':memory:', limits={'spatial_max_query_rows': 2}).bbox([0, 0, 1, 1], limit=3)

    def test_tiny_support_measure_cannot_double_during_split(self):
        self.store.apply([{'type': 'birth', 'cell': {'id': 'cell:tiny', 'measure': 1e-15},
                           'values': {'stock': 0, 'density': 0}}])
        with self.assertRaisesRegex(ValueError, 'measure'):
            self.store.apply([{'type': 'split', 'cell': 'cell:tiny', 'children': [
                {'id': 'cell:tiny1', 'measure': 1e-15}, {'id': 'cell:tiny2', 'measure': 1e-15}]}])
        self.assertEqual(self.store.select(cells=['cell:tiny'])['state']['cells'][0]['measure'], 1e-15)

    def test_lifecycle_history_and_selection_lineage_persist_after_reopen(self):
        original = self.store.select()['source']
        report = self.store.apply([{'type': 'merge', 'cells': ['cell:a', 'cell:b'],
                                   'cell': {'id': 'cell:ab', 'measure': 3}}])
        self.store.close()
        self.store = SpatialStore(self.path)
        selected = self.store.select()
        self.assertEqual(selected['source']['initial_hash'], original['initial_hash'])
        self.assertEqual(selected['source']['revision'], 1)
        self.assertEqual(selected['source']['history_hash'], report['hash'])
        self.assertNotEqual(selected['source']['selection_hash'], original['selection_hash'])
        self.assertEqual(self.store.audit()[0]['events'][0]['conservation']['density']['after'], 15)

    def test_failed_initial_import_leaves_reusable_empty_database(self):
        with SpatialStore(Path(self.temp.name) / 'bad.sqlite') as other:
            config = world()
            config['edges'][0]['target'] = 'cell:missing'
            with self.assertRaises(ValueError):
                other.initialize(config, coordinate_system=CARTESIAN)
            other.initialize(world(), coordinate_system=CARTESIAN)
            self.assertEqual(len(other.select()['state']['cells']), 3)

    def test_signed_scalar_can_be_selected_but_not_diffused(self):
        self.store.apply([{'type': 'birth', 'cell': {'id': 'cell:negative', 'measure': 1},
                           'values': {'stock': -2, 'density': -1}, 'external_input': True}])
        self.assertEqual(self.store.select(cells=['cell:negative'])['state']['fields']['stock']['values']['cell:negative'], -2)
        with self.assertRaisesRegex(ValueError, 'nonnegative scalar'):
            self.store.materialize(cells=['cell:negative'], evolution={'duration_seconds': 1})

    def test_split_rejects_explosive_claim_expansion_before_changes(self):
        with SpatialStore(Path(self.temp.name) / 'claims.sqlite') as other:
            config = world()
            config['claims'] = [{'id': f'claim:{i}', 'claimant': 'actor:x', 'cells': ['cell:a']} for i in range(101)]
            other.initialize(config, coordinate_system=CARTESIAN)
            other._limit_overrides = {'field_max_claim_memberships': 100000}
            with self.assertRaisesRegex(ValueError, 'budget'):
                other.apply([{'type': 'split', 'cell': 'cell:a', 'children': [
                    {'id': f'cell:child{i}', 'measure': .002} for i in range(1000)], 'edges': []}])
            self.assertEqual(other.select(cells=['cell:a'])['state']['fields']['stock']['values']['cell:a'], 12)

    def test_claim_entities_cannot_alias_support_entities(self):
        with SpatialStore(Path(self.temp.name) / 'alias.sqlite') as other:
            config = world()
            config['claims'][0]['claimant'] = 'cell:a'
            with self.assertRaises(ValueError):
                other.initialize(config, coordinate_system=CARTESIAN)
        for key in ('claim:x', 'actor:x'):
            with self.assertRaises(ValueError):
                self.store.apply([{'type': 'birth', 'cell': {'id': key, 'measure': 1},
                                   'values': {'stock': 0, 'density': 0}}])


if __name__ == '__main__':
    unittest.main()
