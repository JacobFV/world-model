import unittest
from worldmodel.transport import network_from_osm, route, traveler_events


class TransportTests(unittest.TestCase):
    def network(self):
        return {'nodes': [{'id': x} for x in 'abcd'], 'edges': [
            {'id': 'road', 'source': 'a', 'target': 'b', 'mode': 'road', 'duration_seconds': 60, 'cost': 2, 'capacity': 2},
            {'id': 'flight', 'source': 'b', 'target': 'd', 'mode': 'air', 'duration_seconds': 120, 'cost': 100,
             'departures': ['2026-01-01T00:05:00Z']},
            {'id': 'sea1', 'source': 'a', 'target': 'c', 'mode': 'sea', 'duration_seconds': 300, 'cost': 1},
            {'id': 'sea2', 'source': 'c', 'target': 'd', 'mode': 'sea', 'duration_seconds': 300, 'cost': 1}]}

    def request(self, **kw):
        return dict({'origin': 'a', 'destination': 'd', 'departure_time': '2026-01-01T00:00:00Z'}, **kw)

    def test_schedule_and_cost_objectives(self):
        fastest = route(self.network(), self.request())
        self.assertEqual(fastest['edge_ids'], ['road', 'flight'])
        self.assertEqual(fastest['duration_seconds'], 420)
        self.assertEqual(fastest['legs'][1]['wait_seconds'], 240)
        cheapest = route(self.network(), self.request(objective='least_cost'))
        self.assertEqual(cheapest['edge_ids'], ['sea1', 'sea2'])
        self.assertEqual(cheapest['total_cost'], 2)

    def test_closures_capacity_modes_missed_departure(self):
        for kwargs in [{'closed_edges': ['road']}, {'demand': 3}, {'permitted_modes': ['sea']},
                       {'departure_time': '2026-01-01T00:06:00Z'}]:
            self.assertEqual(route(self.network(), self.request(**kwargs))['edge_ids'], ['sea1', 'sea2'])
        self.assertEqual(route(self.network(), self.request(permitted_modes=['rail']))['status'], 'unreachable')

    def test_osm_real_connectivity_oneway_missing_geometry(self):
        elements = [{'type': 'node', 'id': 1, 'lat': 0, 'lon': 0}, {'type': 'node', 'id': 2, 'lat': 0, 'lon': .01},
                    {'type': 'way', 'id': 10, 'nodes': [1, 2], 'tags': {'highway': 'primary', 'oneway': '-1'}},
                    {'type': 'way', 'id': 11, 'center': {'lat': 0, 'lon': .02}, 'tags': {'highway': 'primary'}}]
        net = network_from_osm(elements)
        self.assertEqual(len(net['edges']), 1)
        self.assertEqual(net['edges'][0]['source'], 'osm:node:2')
        self.assertEqual(net['edges'][0]['target'], 'osm:node:1')
        self.assertEqual(net['coverage']['ways_without_node_sequence'], 1)
        self.assertGreater(net['edges'][0]['duration_seconds'], 0)
        self.assertEqual(route(net, self.request(origin='osm:node:1', destination='osm:node:2'))['status'], 'unreachable')

    def test_embedded_osm_geometry_preserves_shared_nodes(self):
        net = network_from_osm([{'type': 'way', 'id': 7, 'nodes': [1, 2],
             'geometry': [{'lat': 0, 'lon': 0}, {'lat': 0, 'lon': .01}],
             'tags': {'highway': 'primary'}}, {'type': 'way', 'id': 8, 'nodes': [2, 3],
             'geometry': [{'lat': 0, 'lon': .01}, {'lat': 0, 'lon': .02}],
             'tags': {'highway': 'primary'}}])
        self.assertEqual(len(net['nodes']), 3)
        self.assertEqual(len(net['edges']), 4)
        result = route(net, self.request(origin='osm:node:1', destination='osm:node:3'))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['legs']), 2)

    def test_search_budget_reports_incomplete_not_unreachable(self):
        result = route(self.network(), self.request(max_expansions=1))
        self.assertEqual(result['status'], 'budget_exhausted')
        self.assertFalse(result['optimal'])

    def test_traveler_is_planned_even_with_evidence(self):
        result = route(self.network(), self.request())
        events = traveler_events(result, 'person:fictional')
        self.assertTrue(all(x['epistemic_status'] == 'scenario' for x in events))
        self.assertTrue(all(x['event_type'].startswith('planned_') for x in events))
        backed = traveler_events(result, 'person:fictional', [{'source': 'itinerary'}])
        self.assertTrue(all(x['epistemic_status'] == 'evidence_backed_plan' for x in backed))
        self.assertTrue(all(x['actual_boarding_established'] is False for x in backed))

    def test_zero_cost_cycle_and_label_budget(self):
        net = self.network()
        net['edges'].append({'id': 'loop', 'source': 'a', 'target': 'a', 'mode': 'walk', 'duration_seconds': 0, 'cost': 0})
        self.assertEqual(route(net, self.request())['status'], 'ok')
        self.assertEqual(route(net, self.request(max_labels=1))['status'], 'budget_exhausted')
        net['edges'][0]['closed'] = True
        self.assertEqual(route(net, self.request())['edge_ids'], ['sea1', 'sea2'])

    def test_partial_way_does_not_bridge_missing_node(self):
        net = network_from_osm([{'type': 'way', 'id': 1, 'nodes': [1, 2, 3],
              'geometry': [{'lat': 0, 'lon': 0}, None, {'lat': 0, 'lon': 1}],
              'tags': {'highway': 'primary'}}])
        self.assertEqual(net['edges'], [])
        self.assertEqual(net['coverage']['segments_missing_nodes'], 2)

    def test_validation_and_zero_hop(self):
        self.assertEqual(route(self.network(), self.request(destination='a'))['duration_seconds'], 0)
        bad = self.network(); bad['edges'][0]['cost'] = -1
        with self.assertRaises(ValueError): route(bad, self.request())
        with self.assertRaises(ValueError): route(self.network(), self.request(max_expansions=0))
        with self.assertRaises(ValueError): route(self.network(), self.request(departure_time='2026-01-01'))

    def test_cost_search_preserves_early_expensive_label(self):
        net = {'nodes': [{'id': x} for x in 'abd'], 'edges': [
            {'id': 'fast', 'source': 'a', 'target': 'b', 'mode': 'road', 'duration_seconds': 1, 'cost': 10},
            {'id': 'slow', 'source': 'a', 'target': 'b', 'mode': 'walk', 'duration_seconds': 100, 'cost': 0},
            {'id': 'service', 'source': 'b', 'target': 'd', 'mode': 'rail', 'duration_seconds': 1, 'cost': 0,
             'departures': ['2026-01-01T00:00:02Z']}]}
        self.assertEqual(route(net, self.request(objective='least_cost'))['edge_ids'], ['fast', 'service'])


if __name__ == '__main__': unittest.main()
