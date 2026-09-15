import copy
import tempfile
import unittest
from pathlib import Path

from worldmodel.field_dynamics import evolve_fields
from worldmodel.spatial_store import SpatialStore


COORDINATES = {'kind': 'cartesian', 'axes': ['x', 'y'], 'unit': 'm'}
EDGE_UNITS = {'conductance': 'm2/second', 'transport_rate': '1/second'}
FRAMES = {'vector': {'kind': 'fixed_global', 'axes': ['x', 'y']}}


def world():
    return {'measure_unit': 'm2', 'cells': [
        {'id': 'cell:a', 'measure': 1, 'coordinates': [0, 0]},
        {'id': 'cell:b', 'measure': 1, 'coordinates': [1, 0]}],
        'edges': [{'id': 'edge:ab', 'source': 'cell:a', 'target': 'cell:b', 'conductance': 1, 'transport_rate': 0}],
        'fields': {
            'signed': {'kind': 'extensive', 'unit': 'signal', 'values': {'cell:a': -2, 'cell:b': 2}},
            'vector': {'kind': 'intensive', 'unit': 'm/s', 'value_type': 'vector',
                       'values': {'cell:a': [-2, 4], 'cell:b': [2, -4]}}}}


def request(duration=.25, **kwargs):
    return {'duration_seconds': duration, 'step_seconds': .25, 'boundary': 'closed',
            'edge_units': EDGE_UNITS, **kwargs}


class FieldDynamicsTests(unittest.TestCase):
    def test_signed_vector_diffusion_preserves_integrals_and_direction(self):
        original = world()
        before = copy.deepcopy(original)
        result = evolve_fields(original, request(), coordinate_system=COORDINATES, component_frames=FRAMES)
        self.assertEqual(result['state']['fields']['signed']['values'], {'cell:a': -1, 'cell:b': 1})
        self.assertEqual(result['state']['fields']['vector']['values'], {'cell:a': [-1, 2], 'cell:b': [1, -2]})
        self.assertEqual(result['conservation']['signed']['final_integral'], 0)
        self.assertEqual(result['conservation']['vector']['final_integral'], [0, 0])
        self.assertEqual(original, before)

    def test_advection_uses_declared_source_and_transports_signed_components(self):
        config = world()
        config['edges'][0].update(conductance=0, transport_rate=.5)
        config['fields']['signed']['values'] = {'cell:a': -4, 'cell:b': 0}
        config['fields']['vector']['values'] = {'cell:a': [-4, 2], 'cell:b': [0, 0]}
        result = evolve_fields(config, request(1, step_seconds=1), coordinate_system=COORDINATES, component_frames=FRAMES)
        self.assertEqual(result['state']['fields']['signed']['values'], {'cell:a': -2, 'cell:b': -2})
        self.assertEqual(result['state']['fields']['vector']['values'], {'cell:a': [-2, 1], 'cell:b': [-2, 1]})
        config['edges'][0].update(source='cell:b', target='cell:a')
        reversed_flow = evolve_fields(config, request(1, step_seconds=1), coordinate_system=COORDINATES, component_frames=FRAMES)
        self.assertEqual(reversed_flow['state']['fields']['signed']['values'], {'cell:a': -4, 'cell:b': 0})

    def test_intensive_diffusion_weights_cell_measure(self):
        config = world()
        config['cells'][0]['measure'] = 2
        config['fields'] = {'density': {'kind': 'intensive', 'unit': 'signal/m2',
                                      'values': {'cell:a': -2, 'cell:b': 2}}}
        result = evolve_fields(config, request(), coordinate_system=COORDINATES, component_frames={})
        self.assertEqual(result['state']['fields']['density']['values'], {'cell:a': -1.5, 'cell:b': 1})
        self.assertEqual(result['conservation']['density']['initial_integral'], -2)
        self.assertEqual(result['conservation']['density']['final_integral'], -2)

    def test_unstable_requested_step_is_subdivided_without_new_extrema(self):
        config = world()
        config['edges'][0]['conductance'] = 100
        result = evolve_fields(config, request(1, step_seconds=1), coordinate_system=COORDINATES, component_frames=FRAMES)
        self.assertGreater(result['execution']['substeps'], 100)
        self.assertLessEqual(result['execution']['maximum_outgoing_fraction'], .9)
        self.assertTrue(all(-2 <= v <= 2 for v in result['state']['fields']['signed']['values'].values()))
        self.assertAlmostEqual(result['conservation']['signed']['difference'], 0)

    def test_work_and_substep_limits_fail_before_evolution(self):
        config = world(); before = copy.deepcopy(config)
        config['edges'][0]['conductance'] = 100
        before = copy.deepcopy(config)
        for constraints in ({'max_substeps': 1}, {'max_work': 1}):
            with self.assertRaisesRegex(ValueError, 'budget'):
                evolve_fields(config, request(1, step_seconds=1, **constraints),
                              coordinate_system=COORDINATES, component_frames=FRAMES)
        self.assertEqual(config, before)

    def test_malformed_later_vector_is_rejected(self):
        config = world()
        config['fields']['vector']['values']['cell:b'] = 1
        with self.assertRaises(ValueError):
            evolve_fields(config, request(), coordinate_system=COORDINATES, component_frames=FRAMES)

    def test_explicit_frames_units_boundary_and_finite_values_are_required(self):
        for changes in ({'boundary': 'open'}, {'edge_units': {'conductance': 'm/second', 'transport_rate': '1/second'}}):
            with self.assertRaises(ValueError):
                evolve_fields(world(), request(**changes), coordinate_system=COORDINATES, component_frames=FRAMES)
        for frames in ({}, {'vector': {'kind': 'local_tangent', 'axes': ['east', 'north']}},
                       {'vector': {'kind': 'fixed_global', 'axes': ['x']}}):
            with self.assertRaises(ValueError):
                evolve_fields(world(), request(), coordinate_system=COORDINATES, component_frames=frames)
        config = world(); config['fields']['signed']['values']['cell:a'] = float('nan')
        with self.assertRaises(ValueError):
            evolve_fields(config, request(), coordinate_system=COORDINATES, component_frames=FRAMES)


class SpatialTimelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / 'world.sqlite'
        self.store = SpatialStore(self.path)
        config = world()
        config['fields'] = {'stock': {'kind': 'extensive', 'unit': 'kg', 'values': {'cell:a': 8, 'cell:b': 0}}}
        config['edges'][0].update(conductance=0, transport_rate=.2)
        self.store.initialize(config, coordinate_system=COORDINATES)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def timeline(self, **changes):
        return {'start': '2026-09-15T00:00:00Z', 'end': '2026-09-15T00:00:01Z',
                'sample_seconds': .5, 'step_seconds': .5, 'boundary': 'closed',
                'edge_units': EDGE_UNITS, 'component_frames': {}, **changes}

    def split(self):
        return {'time': '2026-09-15T00:00:00.5Z', 'events': [{
            'type': 'split', 'cell': 'cell:a', 'children': [
                {'id': 'cell:a1', 'measure': .5, 'coordinates': [0, 0]},
                {'id': 'cell:a2', 'measure': .5, 'coordinates': [.1, 0]}],
            'edges': [{'id': 'edge:a2b', 'source': 'cell:a2', 'target': 'cell:b', 'transport_rate': .2}]}]}

    def test_split_happens_at_exact_time_and_evolved_values_persist(self):
        result = self.store.evolve_timeline(self.timeline(events=[self.split()]))
        self.assertEqual(len(result['frames']), 3)
        halfway = result['frames'][1]
        self.assertEqual(halfway['phase'], 'after_events')
        self.assertEqual(halfway['state']['fields']['stock']['values'], {'cell:a1': 3.6, 'cell:a2': 3.6, 'cell:b': .8})
        final = result['state']['fields']['stock']['values']
        self.assertAlmostEqual(final['cell:a1'], 3.6)
        self.assertAlmostEqual(final['cell:a2'], 3.24)
        self.assertAlmostEqual(final['cell:b'], 1.16)
        self.assertAlmostEqual(sum(final.values()), 8)
        self.store.close(); self.store = SpatialStore(self.path)
        self.assertEqual(self.store.select()['state']['fields']['stock']['values'], final)
        self.assertTrue(self.store.audit())

    def test_late_invalid_event_rolls_back_evolution_supports_and_audit(self):
        before = self.store.select()
        invalid = {'time': '2026-09-15T00:00:01Z', 'events': [{'type': 'death', 'cell': 'cell:missing'}]}
        with self.assertRaises(ValueError):
            self.store.evolve_timeline(self.timeline(events=[self.split(), invalid]))
        self.assertEqual(self.store.select(), before)
        self.assertEqual(self.store.audit(), [])

    def test_birth_merge_death_timeline_accounts_explicit_external_input(self):
        result = self.store.evolve_timeline(self.timeline(events=[
            {'time': '2026-09-15T00:00:00Z', 'events': [
                {'type': 'birth', 'cell': {'id': 'cell:new', 'measure': 1}, 'values': {'stock': -2}, 'external_input': True}]},
            {'time': '2026-09-15T00:00:00.5Z', 'events': [
                {'type': 'merge', 'cells': ['cell:a', 'cell:new'], 'cell': {'id': 'cell:merged', 'measure': 2}}]},
            {'time': '2026-09-15T00:00:01Z', 'events': [
                {'type': 'death', 'cell': 'cell:merged', 'transfer_to': 'cell:b'}]}]))
        self.assertAlmostEqual(result['state']['fields']['stock']['values']['cell:b'], 6)
        self.assertEqual(result['conservation']['stock']['external_input'], -2)
        self.assertAlmostEqual(result['conservation']['stock']['difference'], 0)

    def test_cut_boundary_needs_explicit_closed_domain_acceptance(self):
        before = self.store.select()
        with self.assertRaisesRegex(ValueError, 'boundary'):
            self.store.evolve_timeline(self.timeline(cells=['cell:a']))
        self.assertEqual(self.store.select(), before)
        result = self.store.evolve_timeline(self.timeline(cells=['cell:a'], allow_boundary_cut=True))
        self.assertEqual(result['state']['fields']['stock']['values'], {'cell:a': 8})
        self.assertEqual(self.store.select(cells=['cell:b'])['state']['fields']['stock']['values'], {'cell:b': 0})

    def test_cumulative_work_frame_bounds_and_continuation_clock(self):
        before = self.store.select()
        for changes in ({'max_work': 1}, {'max_frames': 2}, {'max_snapshots': 1}):
            with self.assertRaisesRegex(ValueError, 'budget|limit'):
                self.store.evolve_timeline(self.timeline(**changes))
            self.assertEqual(self.store.select(), before)
        self.store.evolve_timeline(self.timeline())
        after = self.store.select()
        with self.assertRaisesRegex(ValueError, 'start|time'):
            self.store.evolve_timeline(self.timeline())
        self.assertEqual(self.store.select(), after)
        continued = self.store.evolve_timeline(self.timeline(start='2026-09-15T00:00:01Z', end='2026-09-15T00:00:02Z'))
        self.assertEqual(continued['end'], '2026-09-15T00:00:02+00:00')


if __name__ == '__main__':
    unittest.main()
