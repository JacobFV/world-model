"""Offline tests for the WTO Timeseries pipeline (no key or network needed)."""
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.acquisition import normalize_config
from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
DATASET = 'wto_timeseries'


def row(**changes):
    base = {'IndicatorCategoryCode': 'TP', 'IndicatorCode': 'TP_A_0010', 'Indicator': 'MFN simple average',
            'ReportingEconomyCode': '840', 'ReportingEconomy': 'United States', 'PartnerEconomyCode': '000',
            'PartnerEconomy': 'World', 'ProductOrSectorClassificationCode': 'HS', 'ProductOrSectorCode': 'TO',
            'ProductOrSector': 'All products', 'PeriodCode': 'A', 'Year': 2023, 'FrequencyCode': 'A',
            'Frequency': 'Annual', 'UnitCode': 'PCT', 'Unit': 'Percent', 'ValueFlagCode': None, 'Value': 3.3}
    base.update(changes)
    return base


class WtoPipelineTests(unittest.TestCase):
    def test_config_is_valid(self):
        definition = json.loads((ROOT / 'data' / DATASET / 'dataset.json').read_text())
        config = normalize_config(definition['acquisition'])
        self.assertEqual(config['credentials'][0]['env'], 'WTO_API_KEY')
        self.assertEqual(definition['status'], 'awaiting_credentials')

    def test_rows_to_observations(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            first, second, empty = root / 'a.json', root / 'b.json', root / 'empty'
            first.write_text(json.dumps({'Dataset': [row(), row(ReportingEconomyCode='918', ReportingEconomy='European Union', Value=None)]}))
            second.write_text(json.dumps({'Dataset': [row(IndicatorCode='ITS_MTV_MX', Unit='US$ million', FrequencyCode='M',
                                                          PeriodCode='M02', Year=2024, Value=250000.5, ReportingEconomyCode='156')]}))
            empty.write_bytes(b'')
            store = Store(root / 'data')
            store.import_shards(DATASET, [{'path': first}, {'path': second}, {'path': empty}], {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run(DATASET)
            records = list(store.records(ref))
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['iso3:USA']['entity_type'], 'country')
        self.assertTrue(entities['wto:economy:918']['attributes']['aggregate'])
        obs = [r for r in records if r['kind'] == 'observation']
        usa = next(r for r in obs if r['subject'] == 'iso3:USA')
        self.assertEqual((usa['metric'], usa['value'], usa['unit'], usa['valid_from'], usa['valid_to']),
                         ('mfn_applied_tariff_simple_avg_all_products', 3.3, 'percent', '2023-01-01', '2024-01-01'))
        eu = next(r for r in obs if r['subject'] == 'wto:economy:918')
        self.assertIsNone(eu['value'])
        self.assertEqual(eu['missing_reason'], 'source_missing')
        monthly = next(r for r in obs if r['subject'] == 'iso3:CHN')
        self.assertEqual((monthly['metric'], monthly['unit'], monthly['valid_from'], monthly['valid_to'], monthly['dimensions']['frequency']),
                         ('wto_its_mtv_mx', 'million_USD', '2024-02-01', '2024-03-01', 'monthly'))


if __name__ == '__main__':
    unittest.main()
