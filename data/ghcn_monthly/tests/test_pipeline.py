"""Offline fixture test: tiny GSOM tar.gz -> monthly observations for U.S. stations with >= 30 years of data."""
import io
import shutil
import tarfile
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name
HEADER = '"STATION","DATE","LATITUDE","LONGITUDE","ELEVATION","NAME","PRCP","PRCP_ATTRIBUTES","TAVG","TAVG_ATTRIBUTES","SNOW","SNOW_ATTRIBUTES"\n'


def station_csv(station, name, years):
    lines = [HEADER]
    for year in years:
        lines.append(f'"{station}","{year}-01","33.9","-118.4","29.7","{name}","12.5","1,0","14.2","0,","",""\n')
    return ''.join(lines)


class GsomTest(unittest.TestCase):
    def test_long_record_filter(self):
        members = {'USW00023174.csv': station_csv('USW00023174', 'LOS ANGELES INTERNATIONAL AIRPORT, CA US', range(1950, 1980)),
                   'USC00000001.csv': station_csv('USC00000001', 'SHORT RECORD, TX US', range(2000, 2010)),
                   'CA000000001.csv': station_csv('CA000000001', 'TORONTO, CA', range(1900, 1990))}
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            with tarfile.open(tmp / 'gsom.tar.gz', 'w:gz') as archive:
                for name, text in members.items():
                    data = text.encode()
                    info = tarfile.TarInfo(name)
                    info.size = len(data)
                    archive.addfile(info, io.BytesIO(data))
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / 'gsom.tar.gz')}], {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        station = records['gsom:entity:USW00023174']
        self.assertEqual((station['entity_id'], station['attributes']['years_with_data']), ('ghcn:station:USW00023174', 30))
        self.assertEqual(records['gsom:within:USW00023174']['object'], 'geo:US:state:06')
        precip = records['gsom:USW00023174:PRCP:195001']
        self.assertEqual((precip['value'], precip['unit'], precip['valid_to'], precip['attributes']['flags']), (12.5, 'mm', '1950-02-01', '1,0'))
        self.assertEqual(records['gsom:USW00023174:TAVG:197901']['metric'], 'average_temperature')
        self.assertNotIn('gsom:USW00023174:SNOW:195001', records)  # blank values omitted
        self.assertNotIn('gsom:entity:USC00000001', records)  # fewer than 30 years
        self.assertNotIn('gsom:entity:CA000000001', records)  # non-U.S.
        self.assertIn('/member:USW00023174.csv/line:2', precip['evidence'][0]['locator'])


if __name__ == '__main__':
    unittest.main()
