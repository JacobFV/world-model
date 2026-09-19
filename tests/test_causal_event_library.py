"""The event library pipeline on fictional source records shaped like the published inputs."""
import importlib.util
import json
import unittest
from pathlib import Path

from worldmodel.model import validate_record

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('event_library_pipeline', ROOT / 'data/event_library/pipeline.py')
pipeline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pipeline)

OBS = '2026-09-15T00:00:00+00:00'


def ev(dataset, locator):
    return [{'input': {'dataset': dataset, 'artifact': 'a' * 64}, 'locator': locator}]


def fixtures():
    fema = [
        {'kind': 'event', 'id': 'openfema:declaration:DR-1-XX:01001:1', 'event_type': 'disaster_declaration',
         'occurred_at': '2005-09-07T00:00:00.000Z', 'observed_at': OBS, 'evidence': ev('openfema', 'shard:0/line:1'),
         'participants': ['fema:disaster:1', 'geo:US:county:01001'],
         'attributes': {'declarationType': 'DR', 'incidentType': 'Hurricane', 'incidentBeginDate': '2005-08-29T00:00:00.000Z',
                        'declarationTitle': 'FICTIONAL', 'paProgramDeclared': True}},
        {'kind': 'event', 'id': 'openfema:declaration:EM-2-XX:statewide', 'event_type': 'disaster_declaration',
         'occurred_at': '2006-01-01T00:00:00.000Z', 'observed_at': OBS, 'evidence': ev('openfema', 'shard:0/line:2'),
         'participants': ['fema:disaster:2'], 'attributes': {'declarationType': 'EM', 'incidentType': 'Flood'}},
        {'kind': 'observation', 'id': 'openfema:web:1:totalObligatedAmountPa', 'metric': 'pa_federal_obligated',
         'subject': 'fema:disaster:1', 'value': 1000.0, 'unit': 'USD', 'dimensions': {'category': 'all'},
         'observed_at': OBS, 'evidence': ev('openfema', 'shard:0/line:3')}]
    ofac = [
        {'kind': 'event', 'id': 'ofac:1', 'event_type': 'sanctions_designation', 'occurred_at': '2014-03-20',
         'observed_at': OBS, 'evidence': ev('ofac_sanctions', 'shard:0/xpath:/x[1]'),
         'participants': ['ofac:party:9', 'ofac:program:X'],
         'attributes': {'date_semantics': 'entry date', 'programs': ['X'], 'list': 'SDN List'}},
        {'kind': 'assertion', 'id': 'ofac:2', 'predicate': 'located_in', 'subject': 'ofac:party:9', 'object': 'iso3:ZZZ',
         'observed_at': OBS, 'evidence': ev('ofac_sanctions', 'shard:0/xpath:/x[1]')}]
    other = [
        {'kind': 'event', 'id': 'sanctions:1', 'event_type': 'sanctions_designation', 'occurred_at': '2012-12-31',
         'observed_at': OBS, 'evidence': ev('other_sanctions_lists', 'shard:0/xpath:/y[1]'),
         'participants': ['un:sanctions:ZZ.001', 'un:regime:zz'],
         'attributes': {'date_semantics': 'LISTED_ON', 'regime': 'ZZ', 'list': 'UN'}}]
    storms = [
        {'kind': 'event', 'id': 'stormev:event:1', 'event_type': 'tornado', 'occurred_at': '2011-04-27T20:00:00Z',
         'observed_at': OBS, 'evidence': ev('noaa_storm_events', 'shard:0/line:2'),
         'participants': ['noaa:storm_event:1', 'geo:US:county:01001'],
         'attributes': {'damage_property': 2e6, 'damage_crops': 0, 'deaths_direct': 1, 'tor_f_scale': 'EF4'}},
        {'kind': 'event', 'id': 'stormev:event:2', 'event_type': 'hail', 'occurred_at': '2011-04-27T20:00:00Z',
         'observed_at': OBS, 'evidence': ev('noaa_storm_events', 'shard:0/line:3'),
         'participants': ['noaa:storm_event:2', 'geo:US:county:01003'], 'attributes': {'damage_property': 10}}]
    quakes = [
        {'kind': 'event', 'id': 'usgseq:event:a', 'event_type': 'earthquake', 'occurred_at': '2011-03-11T05:46:24Z',
         'observed_at': OBS, 'evidence': ev('usgs_earthquakes', 'shard:0/line:2'), 'participants': ['usgs:eq:a'],
         'attributes': {'mag': 9.1, 'mag_type': 'mww', 'latitude': 38.3, 'longitude': 142.4}},
        {'kind': 'event', 'id': 'usgseq:event:b', 'event_type': 'earthquake', 'occurred_at': '2011-03-11T06:00:00Z',
         'observed_at': OBS, 'evidence': ev('usgs_earthquakes', 'shard:0/line:3'), 'participants': ['usgs:eq:b'],
         'attributes': {'mag': 3.0}}]
    ib = [{'kind': 'entity', 'id': 'ibtracs:entity:S1', 'entity_id': 'ibtracs:storm:S1', 'entity_type': 'entity',
           'label': 'Fictional', 'observed_at': OBS, 'evidence': ev('ibtracs', 'shard:0/line:1'),
           'attributes': {'basin': 'NA', 'name': 'FICTIONAL', 'season': 2005}}]
    track = [('2005-08-29T00:00:00Z', 30.0, -88.0, 100), ('2005-08-29T03:00:00Z', 31.0, -88.0, 80),
             ('2005-08-30T00:00:00Z', 34.0, -88.0, 20)]
    for i, (t, lat, lon, wind) in enumerate(track):
        for metric, value in (('latitude', lat), ('longitude', lon), ('max_sustained_wind', wind)):
            ib.append({'kind': 'observation', 'id': f'ibtracs:S1:{i}:{metric}', 'metric': metric, 'subject': 'ibtracs:storm:S1',
                       'value': value, 'unit': 'x', 'valid_from': t, 'observed_at': OBS,
                       'dimensions': {'basin': 'NA', 'track_type': 'main', 'source': 'usa'},
                       'evidence': ev('ibtracs', f'shard:0/line:{i + 2}')})
    geo = []
    for county, lat, lon in (('01001', 30.5, -88.0), ('01003', 33.9, -88.0), ('06001', 37.0, -122.0)):
        for metric, value in (('latitude', lat), ('longitude', lon)):
            geo.append({'kind': 'observation', 'id': f'tiger24:county:{county}:{metric}', 'metric': metric,
                        'subject': f'geo:US:county:{county}', 'value': value, 'unit': 'degrees', 'observed_at': OBS,
                        'dimensions': {'geography_vintage': 2024}, 'evidence': ev('census_geography', 'line:1')})
    wits = []
    for year, rate, rev in ((2018, 5.0, 'HS2017'), (2019, 10.0, 'HS2017'), (2020, 10.2, 'HS2017'), (2022, 2.0, 'HS2022')):
        wits.append({'kind': 'observation', 'id': f'wits:{year}', 'metric': 'mfn_applied_tariff_simple_avg',
                     'subject': 'iso3:ZZZ', 'value': rate, 'unit': 'percent', 'valid_from': f'{year}-01-01',
                     'observed_at': OBS, 'evidence': ev('wits_trains_tariffs', f'shard:0/series:{year}'),
                     'dimensions': {'product': 'hs:010121', 'hs_revision': rev}, 'attributes': {'total_lines': 4}})
    wits.append(dict(wits[-1], id='wits:2021', valid_from='2021-01-01', value=10.0,
                     dimensions={'product': 'hs:010121', 'hs_revision': 'HS2017'}))
    return {'openfema': fema, 'ofac_sanctions': ofac, 'other_sanctions_lists': other, 'noaa_storm_events': storms,
            'usgs_earthquakes': quakes, 'ibtracs': ib, 'census_geography': geo, 'wits_trains_tariffs': wits}


class FakeContext:
    def __init__(self, data):
        self.data = data
        self.parameters = json.loads((ROOT / 'data/event_library/dataset.json').read_text())['parameters']

    def records(self, dataset):
        return iter(self.data[dataset])

    def input_ref(self, dataset):
        return {'dataset': dataset, 'stage': 'normalized', 'version': 'b' * 64}


class EventLibraryPipelineTests(unittest.TestCase):
    def setUp(self):
        self.events = list(pipeline.run(FakeContext(fixtures())))
        self.by_type = {}
        for e in self.events:
            self.by_type.setdefault(e['event_type'], []).append(e)

    def test_every_record_is_a_valid_event_with_source_locator(self):
        ids = [e['id'] for e in self.events]
        self.assertEqual(len(ids), len(set(ids)))
        for e in self.events:
            validate_record(e)
            a = e['attributes']
            for key in ('library_type', 'shock_family', 'unit_type', 'unit', 'date', 'date_semantics', 'source'):
                self.assertIn(key, a)
            self.assertTrue(a['source']['locator'])

    def test_selection_rules(self):
        self.assertEqual(len(self.by_type['fema_major_disaster_declaration']), 1)  # statewide row has no county
        fema = self.by_type['fema_major_disaster_declaration'][0]['attributes']
        self.assertEqual(fema['unit'], 'geo:US:county:01001')
        self.assertEqual(fema['intensity']['timing'], 'ex_post')
        self.assertEqual(fema['incident_begin_date'], '2005-08-29')
        self.assertEqual(len(self.by_type['storm_event_tornado']), 1)
        self.assertNotIn('storm_event_hail', self.by_type)  # $10 of damage, no casualties
        self.assertEqual(len(self.by_type['earthquake']), 1)
        designations = self.by_type['sanctions_designation']
        self.assertEqual(sorted(e['attributes']['unit'] for e in designations), ['ofac:party:9', 'un:sanctions:ZZ.001'])
        self.assertEqual(next(e for e in designations if e['attributes']['unit'] == 'ofac:party:9')['attributes']['countries'],
                         ['iso3:ZZZ'])

    def test_cyclone_county_exposure_uses_radius_and_wind_threshold(self):
        exposures = self.by_type['tropical_cyclone_county_exposure']
        # 01001 is 55 km from the first fix (100 kt); 01003 is only near the 20 kt fix; 06001 is far away.
        self.assertEqual([e['attributes']['unit'] for e in exposures], ['geo:US:county:01001'])
        a = exposures[0]['attributes']
        self.assertEqual(a['intensity']['value'], 100)
        self.assertEqual(a['date'], '2005-08-29')
        self.assertEqual(len(self.by_type['tropical_cyclone']), 1)

    def test_tariff_changes_require_same_revision_and_threshold(self):
        ups = self.by_type['tariff_mfn_increase']
        self.assertEqual([(e['attributes']['date'], e['attributes']['intensity']['value']) for e in ups], [('2019-01-01', 5.0)])
        # 2020 (+0.2 pp) is below threshold; 2021->2022 changes revision; 2020->2021 is a -0.2 pp change.
        self.assertNotIn('tariff_mfn_decrease', self.by_type)
        self.assertEqual(len(ups[0]['evidence']), 2)


if __name__ == '__main__':
    unittest.main()
