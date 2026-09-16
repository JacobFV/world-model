"""Offline pipeline test: a tiny EBA.zip + manifest shard through the Runner."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]


def hours(day, values):
    return [[f'{day}T{h:02d}', v] for h, v in values]


LINES = [
    {'category_id': 1, 'name': 'root', 'childseries': []},
    {'series_id': 'EBA.BANC-ALL.D.H', 'name': 'Demand for Balancing Authority of Northern California (BANC), hourly - UTC time',
     'units': 'megawatthours', 'f': 'H', 'data': hours('20260102', [(1, '100'), (2, '300'), (3, None)]) + hours('20250101', [(5, '50')])},
    {'series_id': 'EBA.BANC-ALL.D.HL', 'name': 'Demand local', 'units': 'megawatthours', 'f': 'HL', 'data': [['20260101T17-08', '100']]},
    {'series_id': 'EBA.BANC-ALL.NG.SUN.H', 'name': 'Net generation from solar for Balancing Authority of Northern California (BANC), hourly - UTC time',
     'units': 'megawatthours', 'f': 'H', 'data': hours('20250101', [(1, '7')])},
    {'series_id': 'EBA.TEC-FPC.ID.H', 'name': 'Actual Net Interchange for Tampa Electric Company (TEC) to Duke Energy Florida, Inc. (FPC), hourly - UTC time',
     'units': 'megawatthours', 'f': 'H', 'data': hours('20250101', [(1, '-867')])},
    {'series_id': 'EBA.PJM-BC.D.H', 'name': 'Demand for PJM Interconnection, LLC (PJM), Baltimore Gas & Electric zone, hourly - UTC time',
     'units': 'megawatthours', 'f': 'H', 'data': hours('20250101', [(1, '3565')])},
    {'series_id': 'EBA.US48-ALL.D.H', 'name': 'Demand for United States Lower 48 (region) (US48), hourly - UTC time',
     'units': 'megawatthours', 'f': 'H', 'data': hours('20250101', [(1, '400000')])},
]


class GridPipelineTest(unittest.TestCase):
    def test_daily_aggregates_recent_hours_and_flows(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with zipfile.ZipFile(temp / 'EBA.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('EBA.txt', ''.join(json.dumps(line) + '\n' for line in LINES))
            (temp / 'manifest.txt').write_text('{"dataset": {}}\n')
            store = Store(temp / 'data')
            store.import_shards('eia_grid_operations', [{'path': temp / 'EBA.zip', 'retrieved_at': '2026-01-10T00:00:00+00:00'},
                                                        {'path': temp / 'manifest.txt'}],
                                {'publisher': 'fixture'}, complete=True)
            # Receipt-level retrieved_at drives the recent-hour window; set it explicitly via parameters-free default.
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('eia_grid_operations', parameters={'hourly_recent_days': 100000})
            records = [json.loads(line) for line in gzip.open(store.version_dir(ref) / 'records.jsonl.gz', 'rt')]
        entities = {r['entity_id']: r['entity_type'] for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['eia:ba:BANC'], 'organization')
        self.assertEqual(entities['eia:region:US48'], 'location')
        self.assertEqual(entities['eia:interchange:TEC:FPC'], 'resource_flow')
        self.assertEqual(entities['eia:ba:PJM:subregion:BC'], 'location')
        obs = [r for r in records if r['kind'] == 'observation']
        self.assertFalse(any(r['dimensions']['series_id'].endswith('.HL') for r in obs))
        daily = [r for r in obs if r['metric'] == 'electricity_demand' and r['subject'] == 'eia:ba:BANC'
                 and r['dimensions']['frequency'] == 'daily']
        self.assertEqual(sorted((r['valid_from'], r['value'], r['attributes']['hours_reported']) for r in daily),
                         [('2025-01-01', 50, 1), ('2026-01-02', 400, 2)])
        peak = next(r for r in obs if r['metric'] == 'electricity_demand_peak_hourly' and r['valid_from'] == '2026-01-02')
        self.assertEqual((peak['value'], peak['unit']), (300, 'MW'))
        hourly = [r for r in obs if r['dimensions']['frequency'] == 'hourly' and r['subject'] == 'eia:ba:BANC' and r['metric'] == 'electricity_demand']
        blank = next(r for r in hourly if r['valid_to'].startswith('2026-01-02T03'))
        self.assertIsNone(blank['value'])
        self.assertEqual(blank['valid_from'], '2026-01-02T02:00:00+00:00')
        interchange = next(r for r in obs if r['metric'] == 'electricity_interchange' and r['dimensions']['frequency'] == 'daily')
        self.assertEqual((interchange['subject'], interchange['value']), ('eia:interchange:TEC:FPC', -867))
        solar = next(r for r in obs if r['metric'] == 'electricity_net_generation')
        self.assertEqual(solar['dimensions']['fuel'], 'sun')
        preds = {(r['subject'], r['predicate'], r['object']) for r in records if r['kind'] == 'assertion'}
        self.assertIn(('eia:interchange:TEC:FPC', 'flow_source', 'eia:ba:TEC'), preds)
        self.assertIn(('eia:ba:PJM:subregion:BC', 'within', 'eia:ba:PJM'), preds)


if __name__ == '__main__':
    unittest.main()
