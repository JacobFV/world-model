"""Offline fixture test: monthly FDSN CSV shards -> earthquake events and observations; window-boundary dedupe."""
import shutil
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name
HEADER = ('time,latitude,longitude,depth,mag,magType,nst,gap,dmin,rms,net,id,updated,place,type,horizontalError,depthError,'
          'magError,magNst,status,locationSource,magSource\n')
JAN = HEADER + ('2024-01-31T23:52:27.000Z,19.36,-155.297,1.72,2.6,ml,31,50,0.02534,0.11,hv,hv74081826,2024-04-13T19:31:01.040Z,'
                '"11 km SW of Volcano, Hawaii",earthquake,0.18,0.27,0.22,16,reviewed,hv,hv\n'
                '2024-02-01T00:00:00.000Z,35.0,-118.0,5.0,3.0,ml,,,,,ci,ci1,2024-02-02T00:00:00.000Z,"boundary",earthquake,,,,,automatic,ci,ci\n')
FEB = HEADER + ('2024-02-01T00:00:00.000Z,35.0,-118.0,5.0,3.0,ml,,,,,ci,ci1,2024-02-02T00:00:00.000Z,"boundary",earthquake,,,,,automatic,ci,ci\n'
                '2024-02-03T10:00:00.000Z,-20.5,-70.1,35.0,,,,,,,us,us2,2024-02-04T00:00:00.000Z,"offshore",quarry blast,,,,,reviewed,us,us\n')


def request(start, end):
    url = f'https://earthquake.usgs.gov/fdsnws/event/1/query?format=csv&starttime={start}&endtime={end}&minmagnitude=2.5'
    return {'method': 'GET', 'url': url, 'params': {'start': start, 'end': end}}


class EarthquakeTest(unittest.TestCase):
    def test_windows(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            (tmp / 'jan.csv').write_text(JAN)
            (tmp / 'feb.csv').write_text(FEB)
            shards = [{'path': str(tmp / 'jan.csv'), 'request': request('2024-01-01T00:00:00', '2024-02-01T00:00:00')},
                      {'path': str(tmp / 'feb.csv'), 'request': request('2024-02-01T00:00:00', '2024-03-01T00:00:00')}]
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        event = records['usgseq:event:hv74081826']
        self.assertEqual((event['event_type'], event['occurred_at'], event['attributes']['status']),
                         ('earthquake', '2024-01-31T23:52:27.000Z', 'reviewed'))
        mag = records['usgseq:hv74081826:earthquake_magnitude']
        self.assertEqual((mag['value'], mag['dimensions']['magnitude_type'], mag['attributes']['uncertainty']), (2.6, 'ml', 0.22))
        self.assertEqual(records['usgseq:hv74081826:latitude']['attributes']['uncertainty_unit'], 'km')
        self.assertIn('/line:2', records['usgseq:event:ci1']['evidence'][0]['locator'])
        self.assertTrue(records['usgseq:event:ci1']['evidence'][0]['locator'].startswith('shard:1/'))  # boundary row kept once
        self.assertEqual(records['usgseq:event:us2']['event_type'], 'quarry_blast')
        self.assertNotIn('usgseq:us2:earthquake_magnitude', records)


if __name__ == '__main__':
    unittest.main()
