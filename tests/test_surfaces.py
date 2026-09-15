import copy
import unittest

from worldmodel.surfaces import render_surface


class SurfaceTests(unittest.TestCase):
    def snapshots(self):
        return {'snapshots': [dict(time=t, entity='actor:a', variable='balance',
                                   value={'amount': t + 3}, unit='USD', origin='model',
                                   evidence=['source:<unsafe>']) for t in range(3)]}

    def test_composition_escape_provenance_and_immutability(self):
        data = self.snapshots()
        original = copy.deepcopy(data)
        html = render_surface(data, {'title': '<script>alert(1)</script>', 'panels': [
            {'kind': kind, 'title': '<img src=x>', 'entity': 'actor:a',
             'variable': 'balance', 'path': ['amount']} for kind in ('value', 'table', 'plot')]})
        self.assertIn('<!doctype html>', html)
        self.assertNotIn('<script>', html)
        self.assertNotIn('<img src=x>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('source:&lt;unsafe&gt;', html)
        self.assertIn('USD', html)
        self.assertIn('model', html)
        self.assertIn('<svg', html)
        self.assertIn('<details>', html)
        self.assertIn('class="scalar"', html)
        self.assertIn('Values and sources', html)
        self.assertEqual(data, original)

    def test_rows_and_panels_bounds_are_explicit(self):
        html = render_surface(self.snapshots(), {'panels': [{'kind': 'table', 'limit': 1}]})
        self.assertIn('Truncated', html)
        for spec in ({'panels': [{'kind': 'table'}] * 17},
                     {'panels': [{'kind': 'table', 'limit': 501}]},
                     {'panels': [{'kind': 'table', 'limit': True}]}):
            with self.assertRaises(ValueError):
                render_surface(self.snapshots(), spec)

    def test_invalid_path_missing_selection_and_non_numeric_plot(self):
        for panel in ({'kind': 'value', 'path': 'amount'},
                      {'kind': 'value', 'path': ['missing']},
                      {'kind': 'value', 'entity': 'unknown'},
                      {'kind': 'plot'}):
            with self.assertRaises(ValueError):
                render_surface(self.snapshots(), {'panels': [panel]})

    def coordinates(self, unit='degree'):
        return {'snapshots': [dict(time=0, entity='geo:a', variable=v, value=x,
                                   unit=unit, origin='observed', evidence=['raw:1'])
                              for v, x in [('latitude', 40), ('longitude', -70)]]}

    def test_map_requires_explicit_degree_coordinates(self):
        html = render_surface(self.coordinates(), {'panels': [{'kind': 'map'}]})
        self.assertIn('Latitude / longitude', html)
        self.assertIn('geo:a', html)
        self.assertIn('raw:1', html)
        self.assertIn('Extent (degrees)', html)
        self.assertIn('<title>geo:a', html)
        for unit in ('m', 'rad', '', None):
            with self.assertRaisesRegex(ValueError, 'degree'):
                render_surface(self.coordinates(unit), {'panels': [{'kind': 'map'}]})
        data = self.coordinates()
        data['snapshots'][0]['value'] = 91
        with self.assertRaises(ValueError):
            render_surface(data, {'panels': [{'kind': 'map'}]})
        data = self.coordinates()
        data['snapshots'].pop()
        with self.assertRaisesRegex(ValueError, 'coordinate'):
            render_surface(data, {'panels': [{'kind': 'map'}]})

    def test_graph_layout_dangling_and_truncation(self):
        data = {'records': [
            {'kind': 'entity', 'id': 'r:a', 'entity_id': 'e:a', 'label': '<script>x</script>', 'evidence': ['ref:1']},
            {'kind': 'entity', 'id': 'r:b', 'entity_id': 'e:b', 'label': 'B'},
            {'kind': 'assertion', 'id': 'r:c', 'subject': 'e:a', 'object': 'e:b', 'predicate': 'owns'},
            {'kind': 'assertion', 'id': 'r:d', 'subject': 'e:a', 'object': 'e:missing', 'predicate': 'knows'}]}
        html = render_surface(data, {'panels': [{'kind': 'graph', 'limit': 1}]})
        self.assertIn('not geographic', html)
        self.assertIn('Truncated', html)
        self.assertIn('unresolved', html)
        self.assertIn('e:missing', html)
        self.assertNotIn('<script>', html)
        self.assertEqual(html, render_surface(data, {'panels': [{'kind': 'graph', 'limit': 1}]}))

    def test_record_observations_and_iso_plot(self):
        data = {'records': [dict(kind='observation', id='obs:1', metric='temperature',
                dimensions={'geo': 'geo:a'}, value=17, unit='C', observed_at='2026-09-15',
                evidence=['input:1'])]}
        html = render_surface(data, {'panels': [{'kind': 'plot', 'entity': 'geo:a',
                                               'variable': 'temperature'}]})
        self.assertIn('obs:1', html)
        self.assertIn('2026-09-15', html)
        self.assertIn('input:1', html)

    def test_plot_rejects_mixed_units(self):
        data = self.snapshots()
        data['snapshots'][1]['unit'] = 'EUR'
        with self.assertRaisesRegex(ValueError, 'unit'):
            render_surface(data, {'panels': [{'kind': 'plot', 'path': ['amount']}]})

    def test_oversized_text_is_rejected(self):
        with self.assertRaises(ValueError):
            render_surface(self.snapshots(), {'title': 'x' * 20001, 'panels': []})

    def test_equal_coordinate_sources_merge_without_losing_provenance(self):
        data = self.coordinates()
        duplicate = dict(data['snapshots'][0], evidence=['second:<source>'], origin='other')
        data['snapshots'].append(duplicate)
        original = copy.deepcopy(data)
        html = render_surface(data, {'panels': [{'kind': 'map'}]})
        self.assertIn('raw:1', html)
        self.assertIn('second:&lt;source&gt;', html)
        self.assertIn('other', html)
        self.assertEqual(html.count('<circle'), 1)
        self.assertEqual(data, original)
        duplicate['value'] = 39
        with self.assertRaisesRegex(ValueError, 'conflicting'):
            render_surface(data, {'panels': [{'kind': 'map'}]})

    def test_graph_merges_compatible_entity_sources(self):
        data = {'records': [dict(kind='entity', id='r:1', entity_id='e:1',
                    entity_type='organization', label='First', evidence=['ref:first']),
                dict(kind='entity', id='r:2', entity_id='e:1',
                    entity_type='business', label='Second', evidence=['ref:second'])]}
        html = render_surface(data, {'panels': [{'kind': 'graph'}]})
        self.assertEqual(html.count('<circle'), 1)
        for label in ('First', 'Second', 'ref:first', 'ref:second'):
            self.assertIn(label, html)
        data['records'][1]['entity_type'] = 'country'
        with self.assertRaisesRegex(ValueError, 'incompatible'):
            render_surface(data, {'panels': [{'kind': 'graph'}]})

    def test_seeded_graph_bfs_prioritizes_visible_edges(self):
        records = [dict(kind='entity', id=k, label=k) for k in ('e:a', 'e:z', 'e:y', 'e:x')]
        records += [dict(kind='assertion', id=f'r:{i}', subject='e:a', object='e:missing',
                         predicate='unresolved') for i in range(501)]
        records += [dict(kind='assertion', id='r:link', subject='e:z', object='e:y', predicate='connected')]
        html = render_surface({'records': records}, {'panels': [{'kind': 'graph', 'seeds': ['e:z'], 'limit': 2}]})
        self.assertIn('<line', html)
        self.assertIn('connected', html)
        self.assertIn('2 edge references omitted', html)
        self.assertEqual(html.count('<circle'), 2)
        for seeds in ('e:z', ['e:missing'], ['e:z', 'e:y', 'e:x']):
            with self.assertRaises(ValueError):
                render_surface({'records': records}, {'panels': [{'kind': 'graph', 'seeds': seeds, 'limit': 2}]})

class InteractiveSurfaceTests(unittest.TestCase):
    def test_interactive_controls_safe_payload_and_static_default(self):
        import json
        import re
        data={'snapshots':[dict(time=t,entity='actor:a',variable='stock',value=t,unit='unit',
            origin='forecast',evidence=['</script><img src=x onerror=alert(1)>\u2028']) for t in range(3)]}
        spec={'interactive':True,'panels':[{'kind':'table'},{'kind':'plot','entity':'actor:a','variable':'stock'}]}
        original=copy.deepcopy((data,spec))
        html=render_surface(data,spec)
        self.assertIn('id="surface-controls"',html)
        self.assertIn('id="surface-time"',html)
        self.assertIn('Export view specification',html)
        self.assertIn('Move earlier',html)
        payload=re.search(r'<script id="surface-data" type="application/json">(.*?)</script>',html,re.S).group(1)
        parsed=json.loads(payload)
        self.assertEqual(parsed['panels'][0]['rows'][0]['evidence'],data['snapshots'][0]['evidence'])
        self.assertNotIn('<',payload);self.assertNotIn('\u2028',payload)
        self.assertNotIn('innerHTML',html);self.assertNotIn('eval(',html)
        self.assertNotIn('<img src=x',html)
        self.assertEqual((data,spec),original)
        self.assertNotIn('<script',render_surface(data,{'panels':[{'kind':'table'}]}))

    def test_interactive_validation_and_metadata_for_map_graph(self):
        data=SurfaceTests().coordinates()
        data['records']=[{'kind':'entity','id':'geo:a','label':'A','evidence':['record:1']}]
        html=render_surface(data,{'interactive':True,'panels':[{'kind':'map'},{'kind':'graph'}]})
        for text in ('raw:1','record:1','Linked entity','Source details','Static supplied topology'):
            self.assertIn(text,html)
        with self.assertRaisesRegex(ValueError,'interactive'):
            render_surface(data,{'interactive':'yes','panels':[]})

    def test_interactive_graph_accepts_explicit_entity_id_without_record_id(self):
        html=render_surface({'records':[{'kind':'entity','entity_id':'entity:a','label':'A'}]},
                            {'interactive':True,'panels':[{'kind':'graph'}]})
        self.assertIn('entity:a',html)
