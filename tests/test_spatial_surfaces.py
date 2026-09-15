import copy
import json
import re
import unittest
from worldmodel.spatial_surfaces import adapt_spatial_result, render_spatial_surface


def result():
    state={'measure_unit':'m2','cells':[{'id':'cell:a','measure':1,'coordinates':[0,0]}, {'id':'cell:b','measure':1}],
           'edges':[{'id':'edge:ab','source':'cell:a','target':'cell:b'}],
           'fields':{'v':{'kind':'intensive','unit':'m/s','value_type':'vector','values':{'cell:a':[-2,3],'cell:b':[0,1]}}}}
    later=copy.deepcopy(state);later['cells']=later['cells'][1:];later['edges']=[];later['fields']['v']['values'].pop('cell:a')
    return {'coordinate_system':{'kind':'cartesian','axes':['x','y'],'unit':'m'},'component_frames':{'v':{'kind':'fixed_global','axes':['x','y']}},
            'frames':[{'time':'2026-09-15T00:00:00Z','state':state,'source':{'history_hash':'abc'}},
                      {'time':'2026-09-15T00:00:01Z','state':later,'source':{'history_hash':'def'}}], 'events':[], 'final_source':{'history_hash':'def'}}


class SpatialSurfaceTests(unittest.TestCase):
    def test_explicit_vector_selection_and_historical_topology(self):
        with self.assertRaises(ValueError):adapt_spatial_result(result(), {'field':'v'})
        data=adapt_spatial_result(result(),{'field':'v','component':0})
        self.assertEqual(data['spatial_frames'][0]['cells'][0]['value'],-2)
        self.assertEqual(len(data['spatial_frames'][0]['edges']),1)
        self.assertEqual(data['spatial_frames'][1]['edges'],[])
        self.assertEqual([c['entity'] for c in data['spatial_frames'][1]['cells']],['cell:b'])
        self.assertEqual(data['spatial_frames'][0]['missing_geometry'],1)
        self.assertEqual(data['spatial_frames'][0]['cells'][0]['source']['history_hash'],'abc')
        self.assertEqual(data['component_frames']['v']['axes'],['x','y'])

    def test_truncation_is_explicit_and_no_input_alias(self):
        original=result();before=copy.deepcopy(original)
        data=adapt_spatial_result(original,{'field':'v','magnitude':True},max_cells=1,max_frames=1,max_edges=1)
        self.assertEqual(data['omitted_frames'],1)
        self.assertEqual(data['spatial_frames'][0]['omitted_cells'],1)
        self.assertEqual(data['spatial_frames'][0]['omitted_edges'],1)
        data['spatial_frames'][0]['cells'][0]['coordinates'][0]=99
        self.assertEqual(original,before)

    def test_finite_values_and_arrow_frame(self):
        bad=result();bad['frames'][0]['state']['fields']['v']['values']['cell:a'][0]=float('inf')
        with self.assertRaises(ValueError):adapt_spatial_result(bad,{'field':'v','magnitude':True})
        bad=result();bad['component_frames']['v']['axes']=['east','north']
        data=adapt_spatial_result(bad,{'field':'v','component':0})
        self.assertFalse(data['spatial_frames'][0]['cells'][0]['arrows_supported'])

    def test_polygon_geometry_and_custom_view_settings_preserved(self):
        original=result()
        crs={'id':'LOCAL:test','kind':'cartesian','axes':['x','y'],'unit':'m'}
        original['frames'][0]['state']['cells'][0]['geometry']={
            'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,1],[0,0]]],
            'crs':crs,'simplified':False}
        spec={'interactive':True,'view':{'time_index':0,'entity':'cell:a','filter':'cell'},
              'panels':[{'kind':'spatial','show_vectors':False,'show_edges':False}]}
        html=render_spatial_surface(original,{'field':'v','magnitude':True},spec=spec)
        payload=json.loads(re.search(r'id="surface-data" type="application/json">(.*?)</script>',html,re.S).group(1))
        self.assertEqual(payload['spec']['view'],spec['view'])
        self.assertFalse(payload['spec']['panels'][0]['show_vectors'])
        self.assertEqual(payload['panels'][0]['frames'][0]['cells'][0]['geometry']['crs'],crs)
        self.assertNotIn('fetch(',html)
        self.assertNotIn('<script src=',html)

    def test_html_escaping_provenance_and_exact_export(self):
        data=result();data['frames'][0]['state']['cells'][0]['tags']=['</script><script>alert(1)</script>']
        html=render_spatial_surface(data,{'field':'v','component':0},materialization_ref={'hash':'materialized'})
        self.assertNotIn('</script><script>alert(1)',html)
        payload=json.loads(re.search(r'id="surface-data" type="application/json">(.*?)</script>',html,re.S).group(1))
        self.assertEqual(payload['materialization_ref'],{'hash':'materialized'})
        self.assertEqual(len(payload['panels'][1]['frames'][1]['nodes']),1)
        self.assertIn('time_index',html)
        self.assertIn('source.textContent',html)
