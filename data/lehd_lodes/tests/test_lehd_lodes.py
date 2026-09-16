"""Offline acceptance test: tiny LODES WAC/OD shards aggregated through the Runner."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
WAC_COLUMNS = (['w_geocode', 'C000', 'CA01', 'CA02', 'CA03', 'CE01', 'CE02', 'CE03'] + [f'CNS{i:02d}' for i in range(1, 21)]
               + ['CR01', 'createdate'])
OD_COLUMNS = ['w_geocode', 'h_geocode', 'S000', 'SA01', 'SA02', 'SA03', 'SE01', 'SE02', 'SE03', 'SI01', 'SI02', 'SI03', 'createdate']


def wac_row(block, total, sector_index):
    values = {c: 0 for c in WAC_COLUMNS}
    values.update({'w_geocode': block, 'C000': total, 'CA02': total, 'CE03': total, f'CNS{sector_index:02d}': total,
                   'CR01': total, 'createdate': '20250101'})
    return ','.join(str(values[c]) for c in WAC_COLUMNS)


class LodesTest(unittest.TestCase):
    def run_pipeline(self, directory, od_rows):
        root = Path(directory)
        files = {
            'ca_wac_S000_JT00_2023.csv.gz': '\n'.join([','.join(WAC_COLUMNS), wac_row('060371011101000', 10, 12),
                                                        wac_row('060371011101001', 5, 12), wac_row('060590001001000', 7, 5)]) + '\n',
            'ca_od_main_JT00_2023.csv.gz': '\n'.join([','.join(OD_COLUMNS)] + od_rows) + '\n',
        }
        shards = []
        for name, text in files.items():
            path = root / name
            path.write_bytes(gzip.compress(text.encode()))
            shards.append({'path': path, 'request': {'method': 'GET', 'url': 'https://lehd.ces.census.gov/x/' + name},
                           'retrieved_at': '2026-09-15T00:00:00+00:00', 'complete': True})
        store = Store(root / 'store')
        definition = json.loads((PROJECT / 'data/lehd_lodes/dataset.json').read_text())
        store.import_shards('lehd_lodes', shards, {'publisher': 'fixture', 'acquisition': definition['acquisition']},
                            complete=True)
        ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('lehd_lodes')
        return list(store.records(ref))

    @staticmethod
    def od(work, home, total):
        return ','.join([work, home, str(total), '0', str(total), '0', '0', '0', str(total), '0', '0', str(total), '20250101'])

    def test_aggregates(self):
        rows = [self.od('060371011101000', '060371011102000', 3), self.od('060371011101001', '060371011102005', 2),
                self.od('060371011101001', '060590001001000', 1), self.od('060590001001000', '060371011101000', 4)]
        with tempfile.TemporaryDirectory() as directory:
            records = self.run_pipeline(directory, rows)
        obs = [r for r in records if r['kind'] == 'observation']
        tract_total = next(r for r in obs if r['subject'] == 'geo:US:tract:06037101110' and r['metric'] == 'jobs_count'
                           and r['dimensions']['segment'] == 'all_jobs')
        self.assertEqual((tract_total['value'], tract_total['attributes']['blocks_aggregated']), (15, 2))
        sector = next(r for r in obs if r['subject'] == 'geo:US:tract:06037101110' and r['dimensions']['segment'] == '54')
        self.assertEqual(sector['value'], 15)
        self.assertFalse(any(r['dimensions'].get('segment') == '11' for r in obs if r['metric'] == 'jobs_count'))
        pair = next(r for r in obs if r['metric'] == 'commuting_jobs' and r['dimensions']['aggregation'] == 'census_tract_pair'
                    and r['subject'] == 'geo:US:tract:06037101110' and r['dimensions']['home_geography'] == 'geo:US:tract:06037101110')
        self.assertEqual(pair['value'], 5)
        county = next(r for r in obs if r['dimensions'].get('aggregation') == 'county_pair' and r['subject'] == 'geo:US:county:06037'
                      and r['dimensions']['home_geography'] == 'geo:US:county:06059' and r['dimensions']['segment'] == 'all_jobs')
        self.assertEqual(county['value'], 1)
        self.assertEqual(tract_total['valid_from'], '2023-01-01')
        self.assertTrue(any(r['kind'] == 'assertion' and r['subject'] == 'geo:US:tract:06059000100'
                            and r['object'] == 'geo:US:county:06059' for r in records))

    def test_ungrouped_od_is_rejected(self):
        rows = [self.od('060371011101000', '060371011102000', 3), self.od('060590001001000', '060371011101000', 4),
                self.od('060371011101001', '060371011102005', 2)]
        with tempfile.TemporaryDirectory() as directory, self.assertRaisesRegex(Exception, 'not grouped'):
            self.run_pipeline(directory, rows)


if __name__ == '__main__':
    unittest.main()
