"""Offline fixture test for full CBP/ZBP/Nonemployer/BDS files (legacy sample path is covered by tests/test_normalizer_contracts.py)."""
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name
BASE = 'https://www2.census.gov/programs-surveys/'

FILES = {
    'cbp23co.zip': ('cbp23co.txt', '"fipstate","fipscty","naics","emp_nf","emp","qp1_nf","qp1","ap_nf","ap","est","n<5"\n'
                                   '"06","075","------","G",700000,"G",20000000,"H",90000000,60000,40000\n'
                                   '"06","075","54----","D",0,"D",0,"D",0,5000,3000\n'
                                   '"06","075","5415//","G",1,"G",1,"G",1,1,1\n'),
    'cbp23st.zip': ('cbp23st.txt', '"fipstate","naics","lfo","emp_nf","emp","qp1_nf","qp1","ap_nf","ap","est"\n'
                                   '"06","311111","-","G",1200,"G",20000,"G",80000,40\n'
                                   '"06","311111","C","G",1000,"G",18000,"G",70000,30\n'),
    'cbp19us.zip': ('cbp19us.txt', '"uscode","naics","lfo","emp_nf","emp","qp1_nf","qp1","ap_nf","ap","est"\n'
                                   '"98","31----","C","G",100,"G",10,"G",40,5\n'),
    'zbp23detail.zip': ('zbp23detail.txt', '"zip","name","naics","est","n<5","n5_9","n10_19","n20_49","n50_99","n100_249","n250_499","n500_999","n1000","city","stabbr","cty_name"\n'
                                           '"94103","SAN FRANCISCO, CA","------",5000,3000,1000,"N","N","N","N","N","N","N","SAN FRANCISCO","CA","SAN FRANCISCO"\n'
                                           '"94103","SAN FRANCISCO, CA","722511",50,,,,,,,,,,"SAN FRANCISCO","CA","SAN FRANCISCO"\n'),
    'nonemp22co.zip': ('nonemp22co.txt', '"ST","CTY","NAICS","ESTAB_F","ESTAB","RCPTOT_N_F","RCPTOT_F","RCPTOT"\n'
                                         '"06","075","00",,90000,"G",,5000000\n"06","075","485310","D",,"G","D",\n'),
}
BDS = 'year,st,sector,firms,estabs,emp,estabs_entry,estabs_exit,job_creation,job_destruction,net_job_creation,firmdeath_firms,reallocation_rate\n2023,06,31,30000,35000,1200000,(D),1000,50000,45000,5000,800,20.5\n'


class CensusBusinessFullTest(unittest.TestCase):
    def test_full_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            shards = []
            for name, (member, text) in FILES.items():
                with zipfile.ZipFile(tmp / name, 'w') as archive:
                    archive.writestr(member, text)
                shards.append({'path': str(tmp / name), 'request': {'url': BASE + 'cbp/datasets/x/' + name}})
            (tmp / 'bds2023_st_sec.csv').write_text(BDS)
            shards.append({'path': str(tmp / 'bds2023_st_sec.csv'), 'request': {'url': BASE + 'bds/tables/time-series/2023/bds2023_st_sec.csv'}})
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        total = records['cbp23:co06075:all:-:emp']
        self.assertEqual((total['subject'], total['value'], total['unit'], total['valid_from']), ('geo:US:county:06075', 700000, 'people', '2023-03-12'))
        self.assertEqual(records['cbp23:co06075:all:-:ap']['attributes']['noise_flag'], 'H')
        suppressed = records['cbp23:co06075:54:-:emp']
        self.assertIsNone(suppressed['value'])
        self.assertEqual((suppressed['missing_reason'], suppressed['dimensions']['industry']), ('suppressed_disclosure', 'naics2022:54'))
        self.assertEqual(records['cbp23:co06075:54:-:est']['value'], 5000)
        self.assertNotIn('cbp23:co06075:5415:-:emp', records)  # county detail limited to 3-digit NAICS
        self.assertIn('cbp23:st06:311111:-:emp', records)
        self.assertNotIn('cbp23:st06:311111:C:emp', records)  # state legal-form detail skipped
        us = records['cbp19:US:31-33:C:qp1']
        self.assertEqual((us['dimensions']['industry'], us['dimensions']['legal_form'], us['unit']), ('naics2017:31-33', 'c_corporation', 'thousand_USD'))
        size = records['zbp23:zip94103:all:n5_9']
        self.assertEqual((size['subject'], size['dimensions']['employment_size_class'], size['value']), ('geo:US:zip:94103', '5-9', 1000))
        self.assertEqual(records['zbp23:zip94103:all:n10_19']['missing_reason'], 'not_available')
        self.assertNotIn('zbp23:zip94103:722511:est', records)
        self.assertEqual(records['nes22:co06075:all:rcptot']['value'], 5000000)
        self.assertNotIn('nes22:co06075:485310:estab', records)  # county nonemployer detail limited to 4-digit NAICS
        entry = records['bds:st06:31:2023:estabs_entry']
        self.assertEqual((entry['value'], entry['missing_reason']), (None, 'suppressed_disclosure'))
        self.assertEqual(records['bds:st06:31:2023:emp']['dimensions']['industry'], 'naics2017:31-33')


if __name__ == '__main__':
    unittest.main()
