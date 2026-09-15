import unittest
from copy import deepcopy
from worldmodel.transport import route

class DatedTransportTests(unittest.TestCase):
    def network(self):
        return {'nodes':[{'id':x} for x in 'abc'], 'edges':[
            {'id':'road','source':'a','target':'b','mode':'road','duration_seconds':60,'duration_range_seconds':[45,120],'cost':0},
            {'id':'air','source':'b','target':'c','mode':'air','duration_seconds':60,'cost':1,'departures':['2026-01-01T00:02:00Z','2026-01-01T00:05:00Z']}],
            'transfers':[{'node':'b','from_mode':'road','to_mode':'air','minimum_seconds':60,'valid_from':'2026-01-01T00:00:00Z','valid_to':'2026-01-02T00:00:00Z'}]}
    def request(self,**kwargs):return dict(origin='a',destination='c',departure_time='2026-01-01T00:00:00Z',**kwargs)
    def test_uncertainty_and_transfer_continuity(self):
        answer=route(self.network(),self.request());self.assertEqual(answer['arrival_time'],'2026-01-01T00:06:00Z')
        self.assertEqual(answer['legs'][0]['duration_range_seconds'],[45,120])
        self.assertEqual(route(self.network(),self.request(duration_policy='nominal'))['arrival_time'],'2026-01-01T00:03:00Z')
        net=self.network();net['transfers']=[];self.assertEqual(route(net,self.request())['status'],'unreachable')
    def test_closure_entire_traversal_and_validity(self):
        net=self.network();net['edges'][0]['closures']=[{'valid_from':'2026-01-01T00:01:00Z','valid_to':'2026-01-01T00:03:00Z'}]
        self.assertEqual(route(net,self.request())['status'],'unreachable')
        net['edges'][0]['valid_to']='2026-01-01T00:04:00Z';net['edges'][0]['valid_from']='2026-01-01T00:00:00Z'
        self.assertEqual(route(net,self.request())['status'],'unreachable')
    def test_capacity_window_and_bounds(self):
        net=self.network();net['edges'][1]['capacity_windows']=[{'valid_from':'2026-01-01T00:04:00Z','valid_to':'2026-01-01T00:06:00Z','capacity':0}]
        self.assertEqual(route(net,self.request())['status'],'unreachable')
        net['geographic_bounds']=[0,0,1,1]
        with self.assertRaises(ValueError):route(net,self.request())
    def test_mode_state_is_part_of_pareto_frontier(self):
        net=self.network();net['edges'].append({'id':'walk','source':'a','target':'b','mode':'walk','duration_seconds':150,'cost':2})
        net['transfers']=[dict(net['transfers'][0],from_mode='walk',minimum_seconds=0)]
        self.assertEqual(route(net,self.request())['edge_ids'],['walk','air'])
    def test_explicit_same_mode_transfer_cannot_be_skipped(self):
        net=self.network();net['edges'][0]['mode']='air';net['transfers'][0]['from_mode']='air'
        answer=route(net,self.request())
        self.assertEqual(answer['arrival_time'],'2026-01-01T00:06:00Z')
