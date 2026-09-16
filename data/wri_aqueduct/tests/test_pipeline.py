"""Offline fixture test: Aqueduct ZIP with baseline annual/monthly and future CSV members."""
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
ROOT = 'Aqueduct40_waterrisk_download_Y2023M07D05/CVS/'
ANNUAL = ('string_id,aq30_id,pfaf_id,gid_1,aqid,gid_0,name_0,name_1,area_km2,bws_raw,bws_score,bws_cat,bws_label,'
          'gtd_raw,gtd_score,gtd_cat,gtd_label,udw_raw,udw_score,udw_cat,udw_label,w_awr_def_tot_raw,w_awr_def_tot_score,'
          'w_awr_def_tot_cat,w_awr_def_tot_label\n'
          '111011-EGY.11_1-3365,0,111011,EGY.11_1,3365,EGY,Egypt,Al Qahirah,4.2,9999.0,5.0,4.0,Extremely High (>80%),'
          '0.84,-9999.0,-9999.0,Insignificant Trend,0.01,0.0,0.0,Low (<2.5%),3.11,4.18,4.0,Extremely High (4-5)\n'
          '111011-EGY.12_1-3366,1,111011,EGY.12_1,3366,EGY,Egypt,Giza,9.0,9999.0,5.0,4.0,Extremely High (>80%),'
          '0.84,-9999.0,-9999.0,Insignificant Trend,0.02,0.1,0.0,Low (<2.5%),3.0,4.1,4.0,Extremely High (4-5)\n'
          '111011-EGY.12_1-3366,1,111011,EGY.12_1,3366,EGY,Egypt,Giza,9.0,9999.0,5.0,4.0,Extremely High (>80%),'
          '0.84,-9999.0,-9999.0,Insignificant Trend,0.02,0.1,0.0,Low (<2.5%),3.0,4.1,4.0,Extremely High (4-5)\n')
MONTHLY = 'pfaf_id,bws_01_raw,bws_01_score,bws_01_cat,bws_01_label\n111011,0.5,3.2,3.0,High\n111011,0.5,3.2,3.0,High\n'
FUTURE = 'fid,pfaf_id,bau30_ws_x_r,bau30_ws_x_s,bau30_ws_x_c,bau30_ws_x_l,pes80_ba_x_r,pes80_ba_x_l\n1,111011,0.7,4.1,4,High,246.8,100-300 cm\n'


class AqueductTest(unittest.TestCase):
    def test_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            with zipfile.ZipFile(tmp / 'aq.zip', 'w') as archive:
                archive.writestr(ROOT + 'Aqueduct40_baseline_annual_y2023m07d05.csv', ANNUAL)
                archive.writestr(ROOT + 'Aqueduct40_baseline_monthly_y2023m07d05.csv', MONTHLY)
                archive.writestr(ROOT + 'Aqueduct40_future_annual_y2023m07d05.csv', FUTURE)
                archive.writestr('Aqueduct40_waterrisk_download_Y2023M07D05/GDB/x.gdbtable', b'ignored')
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / 'aq.zip')}], {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        stress = records['aq40:111011:bws']
        self.assertIsNone(stress['value'])
        self.assertIn('9999', stress['missing_reason'])
        self.assertEqual(records['aq40:111011:bws:score']['value'], 5)
        self.assertEqual(records['aq40:111011:gtd']['value'], 0.84)
        self.assertNotIn('aq40:111011:gtd:score', records)  # -9999 no data
        self.assertEqual(records['aq40:EGY.11_1:udw']['subject'], 'gadm36:EGY.11_1')
        self.assertEqual(records['aqueduct:within:gadm36:EGY.11_1:iso3:EGY']['object'], 'iso3:EGY')
        self.assertEqual(records['aq40:111011-EGY.12_1-3366:awr_def_tot:score']['value'], 4.1)
        self.assertEqual(records['aq40:111011:bws:m01']['dimensions']['month'], 1)
        future = records['aq40:111011:bau2030:ws:s']
        self.assertEqual((future['metric'], future['valid_from'], future['dimensions']['scenario']),
                         ('water_stress_score', '2030-01-01', 'business_as_usual_ssp3_rcp70'))
        self.assertEqual(records['aq40:111011:pes2080:ba:r']['unit'], 'cm/year')
        self.assertEqual(records['aqueduct:entity:aqueduct:pfaf:111011']['entity_type'], 'watershed')


if __name__ == '__main__':
    unittest.main()
