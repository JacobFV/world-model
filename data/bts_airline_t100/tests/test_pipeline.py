"""Offline test: a tiny TranStats-shaped T-100 CSV imported like a manual download."""
import tempfile
import unittest
import zipfile
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
CSV = ('"DEPARTURES_SCHEDULED","DEPARTURES_PERFORMED","PAYLOAD","SEATS","PASSENGERS","FREIGHT","MAIL","DISTANCE","UNIQUE_CARRIER",'
       '"UNIQUE_CARRIER_NAME","ORIGIN","DEST","AIRCRAFT_TYPE","CLASS","DATA_SOURCE","YEAR","MONTH",\n'
       '60,59,2360000,10620,9800,125000,300,337,"UA","United Air Lines Inc.","SFO","LAX",694,"F","DU",2025,12,\n'
       '0,4,480000,0,0,90000,,1846,"FX","Federal Express Corporation","MEM","SFO",625,"G","DU",2025,1,\n')


class T100PipelineTests(unittest.TestCase):
    def test_zip_import(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with zipfile.ZipFile(root / 't100.zip', 'w') as archive:
                archive.writestr('T_T100_SEGMENT_ALL_CARRIER.csv', CSV)
            store = Store(root / 'data')
            raw = store.import_file('bts_airline_t100', root / 't100.zip', source={'publisher': 'fixture'})
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('bts_airline_t100', raw_refs={'bts_airline_t100': [raw]})
            records = list(store.records(ref))
            self.assertEqual(len(records), len({r['id'] for r in records}))
            passengers = next(r for r in records if r['metric'] == 'air_passengers' and r['subject'] == 'iata:SFO')
            self.assertEqual((passengers['value'], passengers['valid_from'], passengers['valid_to']), (9800, '2025-12-01', '2026-01-01'))
            self.assertEqual(passengers['dimensions'], {'destination': 'iata:LAX', 'carrier': 'dot:carrier:UA', 'aircraft_type': '694',
                                                        'service_class': 'F', 'data_source': 'DU'})
            freight = next(r for r in records if r['metric'] == 'air_freight' and r['subject'] == 'iata:MEM')
            self.assertEqual((freight['value'], freight['unit'], freight['valid_to']), (90000, 'lb', '2025-02-01'))
            self.assertFalse(any(r['metric'] == 'air_mail' and r['subject'] == 'iata:MEM' for r in records))
            self.assertTrue(records[0]['evidence'][0]['locator'].startswith('shard:0/member:T_T100_SEGMENT_ALL_CARRIER.csv/line:'))


if __name__ == '__main__':
    unittest.main()
