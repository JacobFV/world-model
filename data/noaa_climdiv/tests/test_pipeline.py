"""Offline fixture test: nClimDiv county and state fixed-width files -> monthly/annual observations."""
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
BASE = 'https://www.ncei.noaa.gov/monitoring-content/data/us/climdiv/monthly/current/'


def line(head, values):
    return head + ''.join(f'{v:7.2f}' for v in values) + '   \n'


class ClimdivTest(unittest.TestCase):
    def test_files(self):
        county = (line('01001021895', [44.0, 38.2, 55.5, 64.1, 70.6, 78.3, 80.4, 80.4, 79.0, 61.4, 54.4, 45.3])
                  + line('49001022025', [70.0] * 12)
                  + line('01001022026', [44.0, 45.0, 50.0, 60.0, 70.0, 75.0, 80.0, 81.0, -99.9, -99.9, -99.9, -99.9]))
        state = (line('0040011991', [1.0] * 12) + line('1100051991', [-1.5] * 12))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            for name in ('dataset.json', 'pipeline.py', 'helpers.py'):
                shutil.copy2(HERE / name, catalog / name)
            (tmp / 'cy').write_text(county)
            (tmp / 'st').write_text(state)
            (tmp / 'readme').write_text('documentation\n')
            shards = [{'path': str(tmp / 'cy'), 'request': {'url': BASE + 'climdiv-tmpccy-v1.0.0-20260904'}},
                      {'path': str(tmp / 'st'), 'request': {'url': BASE + 'climdiv-pcpnst-v1.0.0-20260904'}},
                      {'path': str(tmp / 'readme'), 'request': {'url': BASE + 'climdiv-inv-readme.txt'}}]
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        self.assertNotIn('climdiv:cy01001:tmpc:189501', records)  # county monthly values start in 1991
        annual = records['climdiv:cy01001:tmpc:1895']
        self.assertEqual((annual['subject'], annual['value'], annual['unit'], annual['dimensions']['aggregation']),
                         ('geo:US:county:01001', 62.633, 'degF', 'mean'))
        self.assertEqual(records['climdiv:cy15001:tmpc:202512']['subject'], 'geo:US:county:15001')  # NCEI 49 = Hawaii
        self.assertIn('climdiv:cy01001:tmpc:202608', records)
        self.assertNotIn('climdiv:cy01001:tmpc:202609', records)
        self.assertNotIn('climdiv:cy01001:tmpc:2026', records)  # incomplete year
        precip = records['climdiv:st06:pcpn:1991']
        self.assertEqual((precip['subject'], precip['value'], precip['metric']), ('geo:US:state:06', 12.0, 'precipitation'))
        self.assertEqual(records['climdiv:rg110:pcpn:199107']['subject'], 'noaa:climregion:110')


if __name__ == '__main__':
    unittest.main()
