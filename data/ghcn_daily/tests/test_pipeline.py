"""Offline fixture test: GHCN-Daily station metadata + one by-station gzip CSV."""
import gzip
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
BASE = 'https://www.ncei.noaa.gov/pub/data/ghcn/daily/'
def station_line(ident, lat, lon, elev, state, name, gsn, network, wmo):
    # columns per readme: ID 1-11, LAT 13-20, LON 22-30, ELEV 32-37, ST 39-40, NAME 42-71, GSN 73-75, HCN/CRN 77-79, WMO 81-85
    return f'{ident:11} {lat:8.4f} {lon:9.4f} {elev:6.1f} {state:2} {name:30} {gsn:3} {network:3} {wmo:5}\n'


STATIONS = (station_line('ACW00011604', 17.1167, -61.7833, 10.1, '', 'ST JOHNS COOLIDGE FLD', '', '', '')
            + station_line('USW00023174', 33.9381, -118.3889, 29.6, 'CA', 'LOS ANGELES INTL AP', 'GSN', 'HCN', '72295'))


class GhcnTest(unittest.TestCase):
    def test_station(self):
        rows = []
        for day in range(1, 31):
            rows.append(f'USW00023174,199006{day:02d},TMAX,{250 + day},,,W,\n')
            rows.append(f'USW00023174,199006{day:02d},PRCP,{day % 3},,,W,\n')
        rows.append('USW00023174,19910101,TMAX,200,,,W,\n')
        rows.append('USW00023174,19910102,TMAX,999,,X,W,\n')  # failed QC
        rows.append('USW00023174,19910103,WT01,1,,,W,\n')  # unsupported element
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            for name in ('dataset.json', 'pipeline.py', 'helpers.py'):
                shutil.copy2(HERE / name, catalog / name)
            (tmp / 'stations.txt').write_text(STATIONS)
            (tmp / 'station.csv.gz').write_bytes(gzip.compress(''.join(rows).encode()))
            shards = [{'path': str(tmp / 'stations.txt'), 'request': {'url': BASE + 'ghcnd-stations.txt'}},
                      {'path': str(tmp / 'station.csv.gz'), 'request': {'url': BASE + 'by_station/USW00023174.csv.gz'}}]
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        station = records['ghcn:entity:USW00023174']
        self.assertEqual((station['entity_type'], station['attributes']['gsn'], station['attributes']['wmo_id']), ('location', True, '72295'))
        self.assertNotIn('ghcn:entity:ACW00011604', records)  # metadata limited to acquired stations
        self.assertEqual(records['ghcn:within:USW00023174']['object'], 'geo:US:state:06')
        self.assertEqual(records['ghcn:USW00023174:elevation']['value'], 29.6)
        self.assertNotIn('ghcn:USW00023174:TMAX:19900601', records)  # daily values start in 1991
        daily = records['ghcn:USW00023174:TMAX:19910101']
        self.assertEqual((daily['value'], daily['unit'], daily['valid_to']), (20.0, 'degC', '1991-01-02'))
        self.assertNotIn('ghcn:USW00023174:TMAX:19910102', records)
        monthly = records['ghcn:USW00023174:TMAX:199006:monthly']
        self.assertEqual((monthly['value'], monthly['attributes']['valid_days']), (26.55, 30))
        self.assertEqual(records['ghcn:USW00023174:PRCP:199006:monthly']['value'], 3.0)
        self.assertNotIn('ghcn:USW00023174:TMAX:199101:monthly', records)  # too few valid days


if __name__ == '__main__':
    unittest.main()
