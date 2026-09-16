"""Offline tests: tiny TIGER shapefile, ArcGIS GeoJSON pages, WPI CSV and OpenFlights routes."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]


def _helpers():
    spec = importlib.util.spec_from_file_location('transport_helpers_test', HERE / 'helpers.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def page(features):
    return json.dumps({'type': 'FeatureCollection', 'features': features})


def feature(geometry, **properties):
    return {'type': 'Feature', 'geometry': geometry, 'properties': properties}


WPI = ('﻿OID_,World Port Index Number,Region Name,Main Port Name,Alternate Port Name,UN/LOCODE,Country Code,World Water Body,'
       'Channel Depth (m),Maximum Vessel Length (m),Harbor Size,Harbor Type,Facilities - Container,Latitude,Longitude\n'
       '0.0,15590.0,United States W Coast,Los Angeles,, US LAX,United States,North Pacific Ocean,16.0,0.0,Large,Coastal Breakwater,Yes,33.72,-118.27\n'
       '7.0,46135.0,Nigeria,Escravos Oil Terminal,,NG ESC,Nigeria,Gulf of Guinea,0.0,0.0,Small,Open Roadstead,No,5.5,5.0\n'
       '9.0,46135.0,Nigeria,Lekki,,NG LKK,Nigeria,Gulf of Guinea,15.5,0.0,Medium,Coastal Breakwater,Yes,6.42,4.01\n')
ROUTES = 'AA,24,LAX,3484,JFK,3797,,0,321 32B\nZZ,\\N,KSFO,1,SFO,2,Y,0,\n'


class TransportPipelineTests(unittest.TestCase):
    def test_shapefile_roundtrip(self):
        helpers = _helpers()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'roads.zip'
            helpers.write_shapefile_zip(path, [('LINEARID', 'C', 22, 0), ('FULLNAME', 'C', 100, 0), ('RTTYP', 'C', 1, 0), ('MTFCC', 'C', 5, 0)],
                                        [({'LINEARID': '110', 'FULLNAME': 'I- 5', 'RTTYP': 'I', 'MTFCC': 'S1100'}, [[(-122.0, 37.0), (-122.0, 38.0)]])])
            rows = list(helpers.shapefile_records(path))
            self.assertEqual(rows[0][1], {'LINEARID': '110', 'FULLNAME': 'I- 5', 'RTTYP': 'I', 'MTFCC': 'S1100'})
            self.assertEqual(rows[0][2]['parts'], [[(-122.0, 37.0), (-122.0, 38.0)]])
            self.assertAlmostEqual(helpers.line_summary(rows[0][2]['parts'])['length_km'], 111.19, delta=0.05)

    def test_full_layers(self):
        helpers = _helpers()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = {}
            helpers.write_shapefile_zip(root / 'roads.zip', [('LINEARID', 'C', 22, 0), ('FULLNAME', 'C', 100, 0), ('RTTYP', 'C', 1, 0), ('MTFCC', 'C', 5, 0)],
                                        [({'LINEARID': '110', 'FULLNAME': 'I- 5', 'RTTYP': 'I', 'MTFCC': 'S1100'}, [[(-122.0, 37.0), (-122.0, 38.0)]])])
            files['tiger2024_primaryroads.zip'] = root / 'roads.zip'
            pages = {
                'ntad_rail_lines-p000.geojson': page([feature({'type': 'LineString', 'coordinates': [[-100.0, 46.0], [-100.0, 46.01]]},
                                                              OBJECTID=1, FRAARCID=300000, FRFRANODE=348741, TOFRANODE=348746, STFIPS='38',
                                                              RROWNER1='BNSF', TRKRGHTS1='AMTK', TRACKS=2, NET='M', MILES=0.69, COUNTRY='US')]),
                'ntad_rail_lines-p001.geojson': page([]),
                'ntad_rail_nodes-p000.geojson': page([feature({'type': 'Point', 'coordinates': [-100.0, 46.0]}, OBJECTID=1, FRANODEID=348741, STFIPS='38', COUNTRY='US')]),
                'usace_principal_ports-p000.geojson': page([feature({'type': 'Polygon', 'coordinates': [[[-95.0, 29.7], [-95.1, 29.7], [-95.1, 29.8], [-95.0, 29.7]]]},
                                                                    OBJECTID=1, RANK=2, PORT=2012.0, TYPE='Coastal', PORTNAME='Port of Houston, TX', TOTAL=300000000.0,
                                                                    DOMESTIC=100000000.0, FOREIGN_=200000000.0, IMPORTS=80000000.0, EXPORTS=120000000.0)]),
                'usace_waterway_links-p000.geojson': page([feature({'type': 'LineString', 'coordinates': [[-90.0, 30.0], [-90.1, 30.1]]},
                                                                    OBJECTID=5, LINKNUM=77, ANODE=1, BNODE=2, LENGTH=10.0, RIVERNAME='Mississippi River')]),
                'ntad_intermodal_pipeline_terminals-p000.geojson': page([feature({'type': 'Point', 'coordinates': [-94.0, 30.0]}, OBJECTID=9,
                                                                                  TERM_NAME='Fixture Terminal', CAPACITY=500000, STATE='TX')]),
            }
            for name, text in pages.items():
                (root / name).write_text(text)
                files[name] = root / name
            (root / 'wpi.csv').write_text(WPI, encoding='utf-8')
            files['nga_wpi_pub150.csv'] = root / 'wpi.csv'
            (root / 'routes.dat').write_text(ROUTES)
            files['openflights_routes.dat'] = root / 'routes.dat'
            store = Store(root / 'data')
            raw = store.import_shards('transport', [{'path': path, 'name': name} for name, path in files.items()], {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('transport', raw_refs={'transport': [raw]})
            records = list(store.records(ref))
            by_id = {r['id']: r for r in records}
            self.assertEqual(len(by_id), len(records))
            road = by_id['tiger:linearid:110']
            self.assertEqual((road['entity_type'], road['label'], road['attributes']['route_type']), ('road', 'I- 5', 'I'))
            self.assertAlmostEqual(by_id['tiger:linearid:110:length']['value'], 111.19, delta=0.05)
            link = by_id['ntad:rail_link:300000']
            self.assertEqual((link['subject'], link['predicate'], link['object']), ('ntad:rail_node:348741', 'rail_connects_to', 'ntad:rail_node:348746'))
            self.assertEqual((link['attributes']['owners'], link['attributes']['trackage_rights'], link['attributes']['state']), (['BNSF'], ['AMTK'], 'geo:US:state:38'))
            self.assertEqual(by_id['ntad:rail_node:348741']['attributes']['latitude'], 46.0)
            port = by_id['usace:port:2012']
            self.assertEqual((port['entity_type'], port['label']), ('port', 'Port of Houston, TX'))
            tons = by_id['usace:port:2012:port_tonnage_total:2023']
            self.assertEqual((tons['value'], tons['unit'], tons['valid_from'], tons['valid_to']), (300000000, 'short_ton', '2023-01-01', '2024-01-01'))
            self.assertEqual(by_id['usace:waterway_link:77:5']['object'], 'usace:waterway_node:2')
            self.assertEqual(by_id['ntad:intermodal:pipeline_terminals:9:terminal_storage_capacity']['value'], 500000)
            wpi = by_id['wpi:15590']
            self.assertEqual((wpi['label'], wpi['attributes']['unlocode']), ('Los Angeles', 'US LAX'))
            self.assertEqual(by_id['wpi:15590:identified_by:unlocode']['value'], 'unlocode:USLAX')
            self.assertEqual(by_id['wpi:15590:channel_depth']['value'], 16)
            self.assertNotIn('wpi:15590:max_vessel_length', by_id)  # WPI zero means unknown
            # Pub. 150 reuses a WPI number for distinct ports: both get OID-qualified IDs.
            self.assertNotIn('wpi:46135', by_id)
            self.assertEqual((by_id['wpi:46135:oid:7']['label'], by_id['wpi:46135:oid:9']['label']), ('Escravos Oil Terminal', 'Lekki'))
            self.assertTrue(by_id['wpi:46135:oid:9']['attributes']['wpi_number_shared'])
            self.assertEqual(by_id['wpi:46135:oid:9:channel_depth']['value'], 15.5)
            route = by_id['openflights:route:1']
            self.assertEqual((route['subject'], route['object'], route['attributes']['equipment']), ('iata:LAX', 'iata:JFK', ['321', '32B']))
            self.assertEqual((by_id['openflights:route:2']['subject'], by_id['openflights:route:2']['object']), ('icao:KSFO', 'iata:SFO'))
            self.assertEqual(by_id['icao:KSFO']['entity_type'], 'airport')
            self.assertTrue(all(r['evidence'][0]['input'] == raw for r in records))

    def test_unknown_shard_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'x.csv').write_text('a\n1\n')
            store = Store(root / 'data')
            raw = store.import_shards('transport', [{'path': root / 'x.csv', 'name': 'mystery.csv'}], {'publisher': 'fixture'}, complete=True)
            with self.assertRaisesRegex(Exception, 'Unrecognized transport shards'):
                Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('transport', raw_refs={'transport': [raw]})


if __name__ == '__main__':
    unittest.main()
