"""Offline tests: tiny OurAirports CSV shards and the legacy sample payload."""
import json
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]

FILES = {
    'airports.csv': '"id","ident","type","name","latitude_deg","longitude_deg","elevation_ft","continent","iso_country","iso_region","municipality","scheduled_service","icao_code","iata_code","gps_code","local_code","home_link","wikipedia_link","keywords"\n'
                    '3878,"KSFO","large_airport","San Francisco International Airport",37.619,-122.375,13,"NA","US","US-CA","San Francisco","yes","KSFO","SFO","KSFO","SFO",,,\n'
                    '9,"XK-0001","small_airport","Fixture Strip",42.5,21.1,,"EU","XK","XK-U-A","","no",,,,,,,\n',
    'runways.csv': '"id","airport_ref","airport_ident","length_ft","width_ft","surface","lighted","closed","le_ident","le_latitude_deg","le_longitude_deg","le_elevation_ft","le_heading_degT","le_displaced_threshold_ft","he_ident","he_latitude_deg","he_longitude_deg","he_elevation_ft","he_heading_degT","he_displaced_threshold_ft"\n'
                   '1,3878,"KSFO",11870,200,"ASP",1,0,"10R",37.6,-122.39,10,117,,"28L",37.61,-122.36,13,297,\n',
    'navaids.csv': '"id","filename","ident","name","type","frequency_khz","latitude_deg","longitude_deg","elevation_ft","iso_country","dme_frequency_khz","dme_channel","dme_latitude_deg","dme_longitude_deg","dme_elevation_ft","slaved_variation_deg","magnetic_variation_deg","usageType","power","associated_airport"\n'
                   '5,"SFO_VOR","SFO","San Francisco","VOR-DME",115800,37.6,-122.4,13,"US",1115,"105X",,,,,14,"BOTH","HIGH","KSFO"\n',
    'airport-frequencies.csv': '"id","airport_ref","airport_ident","type","description","frequency_mhz"\n7,3878,"KSFO","TWR","SFO TWR",120.5\n',
    'countries.csv': '"id","code","name","continent","wikipedia_link","keywords"\n1,"US","United States","NA",,\n2,"XK","Kosovo","EU",,\n3,"PR","Puerto Rico","NA",,\n',
    'regions.csv': '"id","code","local_code","name","continent","iso_country","wikipedia_link","keywords"\n10,"US-CA","CA","California","NA","US",,\n11,"XK-U-A","U-A","(unassigned)","EU","XK",,\n',
}


class AirportNodesPipelineTests(unittest.TestCase):
    def test_full_shards(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            shards = []
            for name, text in FILES.items():
                (root / name).write_text(text)
                shards.append({'path': root / name, 'name': name})
            store = Store(root / 'data')
            raw = store.import_shards('airport_nodes', shards, {'publisher': 'fixture', 'acquisition': {'reader': {'format': 'csv'}}}, complete=True)
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('airport_nodes', raw_refs={'airport_nodes': [raw]})
            records = list(store.records(ref))
            by_id = {r['id']: r for r in records}
            self.assertEqual(len(records), len(by_id))
            sfo = by_id['ourairports:3878']
            self.assertEqual((sfo['entity_type'], sfo['attributes']['iata_code'], sfo['attributes']['scheduled_service']), ('airport', 'SFO', True))
            self.assertEqual(by_id['ourairports:3878:within']['object'], 'iso3166-2:US-CA')
            self.assertEqual(by_id['iso3166-2:US-CA:same_as:geo']['object'], 'geo:US:state:06')
            self.assertEqual(by_id['iso3166-2:US-CA:within']['object'], 'iso3:USA')
            self.assertEqual(by_id['iso3:PRI:same_as:geo']['object'], 'geo:US:state:72')
            self.assertEqual(by_id['ourairports:9:within']['object'], 'iso3166-2:XK-U-A')
            self.assertIn('iso3:XKX', by_id)
            self.assertEqual(by_id['ourairports:3878:identified_by:iata']['value'], 'iata:SFO')
            self.assertEqual((by_id['ourairports:runway:1:runway_length']['value'], by_id['ourairports:runway:1:runway_length']['unit']), (11870, 'ft'))
            self.assertEqual(by_id['ourairports:runway:1:located_in']['object'], 'ourairports:3878')
            frequency = by_id['ourairports:frequency:7']
            self.assertEqual((frequency['subject'], frequency['value'], frequency['unit'], frequency['dimensions']), ('ourairports:3878', 120.5, 'MHz', {'frequency_type': 'TWR'}))
            self.assertEqual(by_id['ourairports:navaid:5:located_in']['object'], 'ourairports:3878')
            self.assertNotIn('ourairports:9:elevation', by_id)  # blank values are not invented
            self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:') for r in records))

    def test_sample_payload_keeps_legacy_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'sample.jsonl').write_text(json.dumps({'id': '12', 'ident': 'KSFO', 'name': 'San Francisco International', 'type': 'large_airport',
                                                           'latitude_deg': '37.62', 'longitude_deg': '-122.38', 'iata_code': 'SFO'}) + '\n')
            store = Store(root / 'data')
            raw = store.import_file('airport_nodes', root / 'sample.jsonl', source={'publisher': 'fixture'})
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('airport_nodes', raw_refs={'airport_nodes': [raw]})
            records = list(store.records(ref))
            self.assertEqual(records[0]['entity_id'], 'ourairports:12')


if __name__ == '__main__':
    unittest.main()
