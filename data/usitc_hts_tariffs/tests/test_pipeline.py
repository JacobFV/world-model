"""Offline tests: HTS JSON export + release list shards, and a manually imported annual tariff database ZIP."""
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
DATASET = 'usitc_hts_tariffs'

HTS = [
    {'htsno': '0101', 'indent': '0', 'description': 'Live horses:', 'units': [], 'general': '', 'special': '', 'other': ''},
    {'htsno': '', 'indent': '1', 'description': 'Horses:', 'units': [], 'general': '', 'special': '', 'other': ''},
    {'htsno': '0101.30.00.00', 'indent': '1', 'description': 'Asses', 'units': ['No.'], 'general': '6.8%',
     'special': 'Free (A+,AU,CL)', 'other': '15%', 'footnotes': []},
    {'htsno': '0201.10.50', 'indent': '2', 'description': 'Other', 'units': ['kg'], 'general': '4.4¢/kg + 10%',
     'special': '', 'other': 'The duty provided in the applicable subheading', 'footnotes': None},
    {'htsno': '9903.88.03', 'indent': '0', 'description': 'Products of China, list 3', 'units': [],
     'general': 'The duty provided in the applicable subheading + 25%', 'special': '', 'other': '',
     'additionalDuties': ''},
]
RELEASES = [{'name': '2026HTSRev19', 'description': '2026 HTS Revision 19', 'status': 'current', 'releaseStartDate': '09/15/2026'},
            {'name': '2026HTSRev18', 'description': '2026 HTS Revision 18', 'status': 'archived', 'releaseStartDate': '08/27/2026'}]
DATABASE = ('hts8|brief_description|quantity_1_code|mfn_text_rate|mfn_rate_type_code|mfn_ad_val_rate|mfn_specific_rate|'
            'gsp_indicator|col2_ad_val_rate|begin_effect_date|end_effective_date\n'
            '01013000|Asses|NO|6.8%|7|0.068|0|A+|0.15|01/01/2025|12/31/2025\n'
            '02011050|Beef other|KG|4.4 cents/kg + 10%|4|0.1|0.044||.|01/01/2025|12/31/2025\n')


class UsitcPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def build(self):
        ref = Runner(Catalog(ROOT / 'data'), self.store, ROOT).run(DATASET)
        return list(self.store.records(ref))

    def test_hts_export_rates_hierarchy_and_chapter99(self):
        export, releases = self.root / 'export.json', self.root / 'releases.json'
        export.write_text(json.dumps(HTS, ensure_ascii=False))
        releases.write_text(json.dumps(RELEASES))
        self.store.import_shards(DATASET, [{'path': export, 'url': 'http://fixture/export'},
                                           {'path': releases, 'url': 'http://fixture/releases'}],
                                 {'publisher': 'fixture', 'acquisition': {'reader': {'format': 'json'}}}, complete=True)
        records = self.build()
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['hts:0101300000']['entity_type'], 'product')
        self.assertEqual(entities['hts:0101300000']['attributes']['parent_hts'], 'hts:0101')
        self.assertEqual(entities['hts:99038803']['entity_type'], 'regulation')
        obs = {(r['subject'], r['metric'], r['unit']): r for r in records if r['kind'] == 'observation'}
        general = obs[('hts:0101300000', 'general_ad_valorem_rate', 'fraction')]
        self.assertAlmostEqual(general['value'], 0.068)
        self.assertEqual(general['valid_from'], '2026-09-15')
        special = obs[('hts:0101300000', 'special_ad_valorem_rate', 'fraction')]
        self.assertEqual((special['value'], special['attributes']['special_programs']), (0, ['A+', 'AU', 'CL']))
        self.assertAlmostEqual(obs[('hts:02011050', 'general_specific_rate', 'USD/kg')]['value'], 0.044)
        self.assertAlmostEqual(obs[('hts:02011050', 'general_ad_valorem_rate', 'fraction')]['value'], 0.1)
        legal = obs[('hts:02011050', 'column2_ad_valorem_rate', 'fraction')]
        self.assertIsNone(legal['value'])
        self.assertEqual(legal['missing_reason'], 'rate_text_not_numeric')
        self.assertAlmostEqual(obs[('hts:99038803', 'general_additional_ad_valorem_rate', 'fraction')]['value'], 0.25)
        self.assertTrue(any(r.get('predicate') == 'hts_release_metadata' for r in records))

    def test_manual_annual_database_zip_import(self):
        archive = self.root / 'tariff_database_2025.zip'
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('tariff_database_2025.txt', DATABASE)
            bundle.writestr('td-fields.pdf', b'%PDF')
        self.store.import_file(DATASET, archive, {'publisher': 'fixture'})
        records = self.build()
        obs = {(r['subject'], r['metric']): r for r in records if r['kind'] == 'observation'}
        mfn = obs[('hts:01013000', 'mfn_ad_valorem_rate')]
        self.assertEqual((mfn['value'], mfn['unit'], mfn['valid_from'], mfn['valid_to']), (0.068, 'fraction', '2025-01-01', '2026-01-01'))
        self.assertEqual(mfn['attributes']['program_indicators'], {'gsp_indicator': 'A+'})
        self.assertEqual(obs[('hts:02011050', 'mfn_specific_rate')]['unit'], 'USD/KG')
        self.assertNotIn(('hts:02011050', 'column2_ad_valorem_rate'), obs)  # '.' is missing, not zero.
        self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:0/member:tariff_database_2025.txt/line:')
                            for r in records))


if __name__ == '__main__':
    unittest.main()
