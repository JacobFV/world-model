import copy
import tempfile
import unittest
from pathlib import Path

from worldmodel.spatial_geometry import (validate_polygon, point_in_polygon, polygon_intersects_bbox,
    polygons_adjacent, rectangle_refinement, rectangle_coarsening, refinement_candidates)
from worldmodel.spatial_store import SpatialStore


CRS = {'id': 'LOCAL:test-plane', 'kind': 'cartesian', 'axes': ['x', 'y'], 'unit': 'm'}
COORDINATES = {k: v for k, v in CRS.items() if k != 'id'}


def polygon(points):
    return {'type': 'Polygon', 'coordinates': [points], 'crs': CRS, 'simplified': False}


def rectangle(x0, y0, x1, y1):
    return polygon([[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]])


class GeometryTests(unittest.TestCase):
    def test_point_containment_boundary_and_true_bbox_intersection(self):
        triangle = polygon([[0, 0], [2, 0], [0, 2], [0, 0]])
        self.assertTrue(point_in_polygon([.25, .25], triangle, crs=CRS))
        self.assertTrue(point_in_polygon([1, 1], triangle, crs=CRS))
        self.assertFalse(point_in_polygon([1, 1], triangle, crs=CRS, include_boundary=False))
        self.assertFalse(polygon_intersects_bbox(triangle, [1.5, 1.5, 2, 2], crs=CRS))
        self.assertTrue(polygon_intersects_bbox(triangle, [.2, .2, .4, .4], crs=CRS))

    def test_geometry_rejects_self_intersections_holes_and_crs_ambiguity(self):
        invalid = [polygon([[0, 0], [2, 2], [0, 2], [2, 0], [0, 0]]),
                   polygon([[0, 0], [1, 0], [2, 0], [0, 0]])]
        for geometry in invalid:
            with self.assertRaises(ValueError):
                validate_polygon(geometry)
        geometry = rectangle(0, 0, 2, 2)
        geometry['coordinates'].append([[.1, .1], [.2, .1], [.1, .2], [.1, .1]])
        with self.assertRaises(ValueError):
            validate_polygon(geometry)
        with self.assertRaisesRegex(ValueError, 'CRS'):
            point_in_polygon([0, 0], rectangle(0, 0, 1, 1), crs={**CRS, 'id': 'LOCAL:other'})
        geometry = rectangle(0, 0, 1, 1); geometry['simplified'] = True
        with self.assertRaisesRegex(ValueError, 'simplification'):
            validate_polygon(geometry)

    def test_adjacency_requires_shared_edge_not_overlap_or_corner(self):
        a = rectangle(0, 0, 1, 1)
        self.assertTrue(polygons_adjacent(a, rectangle(1, 0, 2, 1), crs=CRS))
        self.assertFalse(polygons_adjacent(a, rectangle(1, 1, 2, 2), crs=CRS))
        self.assertFalse(polygons_adjacent(a, rectangle(.5, 0, 1.5, 1), crs=CRS))
        self.assertFalse(polygons_adjacent(a, a, crs=CRS))

    def test_rectangle_refinement_and_coarsening_are_deterministic_and_conservative(self):
        cell = {'id': 'cell:a', 'measure': 4, 'geometry': rectangle(0, 0, 2, 2)}
        first = rectangle_refinement(cell, axis='x', parts=2, child_ids=['cell:a1', 'cell:a2'], edges=[])
        self.assertEqual(first, rectangle_refinement(cell, axis='x', parts=2, child_ids=['cell:a1', 'cell:a2'], edges=[]))
        children = first['event']['children']
        self.assertEqual([c['measure'] for c in children], [2, 2])
        self.assertEqual(children[0]['geometry']['coordinates'][0][1], [1, 0])
        merged = rectangle_coarsening(children, target_id='cell:merged')
        self.assertEqual(merged['event']['cell']['measure'], 4)
        self.assertEqual(merged['event']['cell']['geometry']['coordinates'], cell['geometry']['coordinates'])
        with self.assertRaises(ValueError):
            rectangle_coarsening([children[0], children[0]], target_id='cell:merged')

    def test_explicit_refinement_criterion_preserves_units_and_order(self):
        data = {'cells': [{'id': 'cell:b'}, {'id': 'cell:a'}], 'fields': {
            'signal': {'unit': 'signal', 'values': {'cell:a': 2, 'cell:b': 3}}}}
        result = refinement_candidates(data, {'field': 'signal', 'unit': 'signal', 'operator': 'gte', 'value': 2}, limit=1)
        self.assertEqual(result['cells'], ['cell:a'])
        self.assertTrue(result['truncated'])
        with self.assertRaises(ValueError):
            refinement_candidates(data, {'field': 'signal', 'unit': 'wrong', 'operator': 'gte', 'value': 2})


class GeometryStoreTests(unittest.TestCase):
    def test_polygon_queries_are_distinct_from_centroid_queries_and_persist(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'geometry.sqlite'
            config = {'measure_unit': 'm2', 'cells': [
                {'id': 'cell:a', 'measure': 4, 'coordinates': [0, 0], 'geometry': rectangle(0, 0, 2, 2)},
                {'id': 'cell:b', 'measure': 2, 'coordinates': [3, 0], 'geometry': rectangle(2, 0, 3, 2)}],
                'edges': [], 'fields': {'stock': {'kind': 'extensive', 'unit': 'kg', 'values': {'cell:a': 8, 'cell:b': -2}}}}
            with SpatialStore(path) as store:
                store.initialize(config, coordinate_system=COORDINATES)
                self.assertEqual(store.bbox([1, 1, 1.5, 1.5])['cells'], [])
                self.assertEqual([c['id'] for c in store.geometry_bbox([1, 1, 1.5, 1.5], crs=CRS)['cells']], ['cell:a'])
                self.assertEqual([c['id'] for c in store.containing([2.5, 1], crs=CRS)['cells']], ['cell:b'])
                self.assertEqual([c['id'] for c in store.geometry_neighbors('cell:a', crs=CRS)['cells']], ['cell:b'])
                refinement = rectangle_refinement(store.select(cells=['cell:a'])['state']['cells'][0],
                    axis='x', parts=2, child_ids=['cell:a1', 'cell:a2'], edges=[])
                store.apply([refinement['event']])
                self.assertEqual(store.select(cells=['cell:a1', 'cell:a2'])['state']['fields']['stock']['values'], {'cell:a1': 4, 'cell:a2': 4})
            with SpatialStore(path) as store:
                self.assertEqual([c['id'] for c in store.containing([.5, 1], crs=CRS)['cells']], ['cell:a1'])

    def test_invalid_geometry_rolls_back_lifecycle_and_query_bounds(self):
        with SpatialStore(':memory:') as store:
            store.initialize({'measure_unit': 'm2', 'cells': [{'id': 'cell:a', 'measure': 1, 'geometry': rectangle(0, 0, 1, 1)}],
                              'fields': {'x': {'kind': 'extensive', 'unit': 'kg', 'values': {'cell:a': 1}}}}, coordinate_system=COORDINATES)
            before = store.select()
            invalid = rectangle(1, 0, 2, 1); invalid['crs'] = {**CRS, 'id': 'LOCAL:other'}
            with self.assertRaises(ValueError):
                store.apply([{'type': 'birth', 'cell': {'id': 'cell:b', 'measure': 1, 'geometry': invalid}, 'values': {'x': 0}}])
            self.assertEqual(store.select(), before)
            from worldmodel.limits import use_limits
            self.assertEqual(len(store.geometry_bbox([0, 0, 1, 1], crs=CRS, max_candidates=10001)['cells']), 1)
            with use_limits(spatial_max_candidates=10000):
                with self.assertRaisesRegex(ValueError, 'spatial_max_candidates'):
                    store.geometry_bbox([0, 0, 1, 1], crs=CRS, max_candidates=10001)
