import copy
import random
import unittest

from worldmodel.backends import numpy_available
from worldmodel.field_arrays import evolve_field_arrays
from worldmodel.field_dynamics import evolve_fields
from worldmodel.fields import FieldWorld
from worldmodel.limits import LimitExceeded, use_limits
from worldmodel.spatial_store import SpatialStore

COORDINATES = {'kind': 'cartesian', 'axes': ['x', 'y'], 'unit': 'm'}
EDGE_UNITS = {'conductance': 'm2/second', 'transport_rate': '1/second'}


def random_world(seed, cells=60, edges=180, signed=True):
    rng = random.Random(seed)
    ids = [f'cell:{i:04d}' for i in range(cells)]
    world = {'measure_unit': 'm2', 'cells': [{'id': key, 'measure': rng.uniform(.3, 3), 'coordinates': [i, 0]} for i, key in enumerate(ids)],
             'edges': [], 'fields': {}}
    for e in range(edges):
        a, b = rng.sample(range(cells), 2)
        world['edges'].append({'id': f'edge:{e}', 'source': ids[a], 'target': ids[b],
                               'conductance': rng.uniform(0, 2), 'transport_rate': rng.choice([0, rng.uniform(0, .5)])})
    low = -5 if signed else 0
    world['fields']['stock'] = {'kind': 'extensive', 'unit': 'kg', 'values': {k: rng.uniform(low, 5) for k in ids}}
    world['fields']['density'] = {'kind': 'intensive', 'unit': 'kg/m2', 'values': {k: rng.uniform(low, 5) for k in ids}}
    if signed:
        world['fields']['flow'] = {'kind': 'intensive', 'unit': 'm/s', 'value_type': 'vector',
                                   'values': {k: [rng.uniform(-3, 3), rng.uniform(-3, 3), rng.uniform(-3, 3)] for k in ids}}
    return world


@unittest.skipUnless(numpy_available(), 'numpy backend not installed')
class BitIdentityTests(unittest.TestCase):
    def test_signed_vector_evolve_fields_is_bit_identical(self):
        for seed in range(4):
            world = random_world(seed)
            frames = {'flow': {'kind': 'fixed_global', 'axes': ['x', 'y', 'z']}}
            request = {'duration_seconds': 3.5, 'step_seconds': 1, 'boundary': 'closed', 'edge_units': EDGE_UNITS}
            python = evolve_fields(world, request, coordinate_system=COORDINATES, component_frames=frames, backend='python')
            fast = evolve_fields(world, request, coordinate_system=COORDINATES, component_frames=frames, backend='numpy')
            self.assertGreater(python['execution']['substeps'], 3)
            self.assertEqual(python, fast)
            for name, field in python['state']['fields'].items():
                for key, value in field['values'].items():
                    self.assertEqual(repr(value), repr(fast['state']['fields'][name]['values'][key]))

    def test_nonnegative_field_world_is_bit_identical(self):
        for seed in range(4):
            world = random_world(seed, signed=False)
            world.pop('measure_unit')
            request = {'duration_seconds': 20, 'step_seconds': 5}
            self.assertEqual(FieldWorld(world, backend='python').evolve(request), FieldWorld(world, backend='numpy').evolve(request))

    def test_array_api_matches_between_backends(self):
        import numpy as np
        world = random_world(9, cells=200, edges=800)
        index = {c['id']: i for i, c in enumerate(world['cells'])}
        measures = [c['measure'] for c in world['cells']]
        sources = [index[e['source']] for e in world['edges']]; targets = [index[e['target']] for e in world['edges']]
        g = [e['conductance'] for e in world['edges']]; r = [e['transport_rate'] for e in world['edges']]
        fields = {'stock': {'kind': 'extensive', 'values': list(world['fields']['stock']['values'].values())},
                  'flow': {'kind': 'intensive', 'values': list(world['fields']['flow']['values'].values())}}
        python = evolve_field_arrays(measures, fields, sources, targets, g, r, duration_seconds=10, max_work=10**8, backend='python')
        numeric = evolve_field_arrays(np.array(measures), {k: {'kind': v['kind'], 'values': np.array(v['values'])} for k, v in fields.items()},
                                      np.array(sources), np.array(targets), np.array(g), np.array(r), duration_seconds=10, max_work=10**8, backend='numpy')
        self.assertEqual(python['execution'], numeric['execution'])
        self.assertEqual(python['conservation'], numeric['conservation'])
        self.assertEqual(python['fields']['stock'], numeric['fields']['stock'].tolist())
        self.assertEqual(python['fields']['flow'], numeric['fields']['flow'].tolist())

    def test_timeline_backends_are_identical(self):
        results = []
        for backend in ('python', 'numpy'):
            world = random_world(3, cells=20, edges=40, signed=False)
            world['fields'].pop('density')
            with SpatialStore(':memory:') as store:
                store.initialize(world, coordinate_system=COORDINATES)
                result = store.evolve_timeline({'start': '2026-01-01T00:00:00Z', 'end': '2026-01-01T00:00:04Z', 'sample_seconds': 1,
                                                'step_seconds': 1, 'boundary': 'closed', 'edge_units': EDGE_UNITS, 'component_frames': {}},
                                               backend=backend)
                result['execution'].pop('backend')
                results.append(result)
        self.assertEqual(results[0], results[1])


class LimitTests(unittest.TestCase):
    def test_lowered_limits_reject_with_named_limit(self):
        world = random_world(1, cells=10, edges=10, signed=False)
        world.pop('measure_unit')
        with use_limits(field_max_cells=9):
            with self.assertRaisesRegex(LimitExceeded, 'field_max_cells=9') as caught:
                FieldWorld(world)
        self.assertIn('WORLD_MODEL_LIMITS', str(caught.exception))
        with self.assertRaisesRegex(LimitExceeded, 'field_max_edges'):
            FieldWorld(world, limits={'field_max_edges': 5})
        with self.assertRaisesRegex(LimitExceeded, 'field_max_substeps'):
            FieldWorld(world, limits={'field_max_substeps': 100}).evolve({'duration_seconds': 1, 'max_substeps': 101})
        signed = random_world(2, cells=10, edges=10)
        frames = {'flow': {'kind': 'fixed_global', 'axes': ['x', 'y', 'z']}}
        request = {'duration_seconds': 1, 'boundary': 'closed', 'edge_units': EDGE_UNITS}
        with self.assertRaisesRegex(LimitExceeded, 'field_max_values'):
            evolve_fields(signed, request, coordinate_system=COORDINATES, component_frames=frames, limits={'field_max_values': 49})
        # Raised ceilings admit work above the old hard maxima (1,000 supports / 10,000 edges).
        big = random_world(4, cells=1200, edges=12000, signed=False)
        result = evolve_fields(big, {**request, 'duration_seconds': 0}, coordinate_system=COORDINATES, component_frames={}, backend='python')
        self.assertEqual(len(result['state']['fields']['stock']['values']), 1200)

    def test_geometry_limits(self):
        from worldmodel.spatial_geometry import validate_polygon
        crs = {'id': 'LOCAL:p', **COORDINATES}
        import math
        ring = [[math.cos(2 * math.pi * i / 500), math.sin(2 * math.pi * i / 500)] for i in range(500)]
        polygon = {'type': 'Polygon', 'coordinates': [ring + [ring[0]]], 'crs': crs, 'simplified': False}
        validate_polygon(polygon)
        with self.assertRaisesRegex(LimitExceeded, 'geometry_max_vertices'):
            validate_polygon(polygon, limits={'geometry_max_vertices': 128})


class SpatialBulkTests(unittest.TestCase):
    def world(self, cells):
        ids = [f'cell:{i:06d}' for i in range(cells)]
        return {'measure_unit': 'm2', 'cells': [{'id': key, 'measure': 1 + i % 3, 'coordinates': [i, i % 7]} for i, key in enumerate(ids)],
                'edges': [{'id': f'edge:{i}', 'source': ids[i], 'target': ids[i + 1], 'conductance': .1} for i in range(cells - 1)],
                'fields': {'stock': {'kind': 'extensive', 'unit': 'kg', 'values': {k: float(i % 5) for i, k in enumerate(ids)}},
                           'density': {'kind': 'intensive', 'unit': 'kg/m2', 'values': {k: 1.5 for k in ids}}}}

    def test_selection_beyond_sql_variable_limits_and_array_roundtrip(self):
        world = self.world(34000)
        with SpatialStore(':memory:') as store:
            store.initialize(world, coordinate_system=COORDINATES)
            chosen = [c['id'] for c in world['cells'][:33500]]
            selected = store.select(cells=chosen, limit=40000)
            self.assertEqual(selected['selection']['boundary_edges'], 1)
            self.assertEqual(len(selected['state']['edges']), 33499)
            loaded = store.load_arrays(cells=chosen, backend='python')
            self.assertEqual(loaded['ids'], sorted(chosen))
            self.assertEqual(loaded['boundary_edges'], 1)
            self.assertEqual(loaded['core'].reported('stock')[:5], [0.0, 1.0, 2.0, 3.0, 4.0])
            core = loaded['core']
            core.fields['stock']['amounts'][0] = [v + 1 for v in core.fields['stock']['amounts'][0]]
            store.put_value_arrays(loaded['ids'], core)
            self.assertEqual(store.select(cells=['cell:000003'])['state']['fields']['stock']['values'], {'cell:000003': 4.0})
            everything = store.load_arrays(backend='python')
            self.assertEqual((everything['core'].n, everything['core'].m, everything['boundary_edges']), (34000, 33999, 0))

    def test_timeline_history_modes_retain_less_and_conserve_identically(self):
        request = {'start': '2026-01-01T00:00:00Z', 'end': '2026-01-01T00:00:10Z', 'sample_seconds': 1, 'step_seconds': 1,
                   'boundary': 'closed', 'edge_units': EDGE_UNITS, 'component_frames': {}}
        outputs = {}
        for mode, extra in (('full', {}), ('every_n', {'history': 'every_n', 'history_every': 4}), ('summary', {'history': 'summary'})):
            with SpatialStore(':memory:') as store:
                store.initialize(self.world(50), coordinate_system=COORDINATES)
                outputs[mode] = store.evolve_timeline({**request, **extra})
                outputs[mode]['stored'] = store.select()['state']
        full, every, summary = outputs['full'], outputs['every_n'], outputs['summary']
        self.assertEqual(len(full['frames']), 11)
        self.assertEqual([f['state'] is not None for f in every['frames']], [i % 4 == 0 or i == 10 for i in range(11)])
        self.assertEqual(len(every['snapshots']), 4 * 50 * 2)
        self.assertTrue(all(f['state'] is None for f in summary['frames']))
        self.assertEqual(summary['snapshots'], [])
        self.assertIsNone(summary['state'])
        self.assertEqual(summary['completion']['events'][0]['state_hash_kind'], 'arrays-sha256-v1')
        self.assertEqual(full['conservation'], summary['conservation'])
        self.assertEqual(full['stored'], summary['stored'])
        self.assertEqual(full['state'], every['state'])
        self.assertEqual(summary['state_summary']['integrals'], {k: v['final_integral'] for k, v in full['conservation'].items()})


if __name__ == '__main__':
    unittest.main()
