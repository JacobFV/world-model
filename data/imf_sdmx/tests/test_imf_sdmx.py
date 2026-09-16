"""Offline acceptance test for imf_sdmx on SDMX-CSV 3.0 and 2.1 shards."""
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
CPI = ('STRUCTURE,STRUCTURE_ID,ACTION,COUNTRY,INDEX_TYPE,COICOP_1999,TYPE_OF_TRANSFORMATION,FREQUENCY,TIME_PERIOD,OBS_VALUE\n'
       'dataflow,IMF.STA:CPI(5.0.0),R,USA,CPI,_T,IX,M,2025-M01,319.1\n'
       'dataflow,IMF.STA:CPI(5.0.0),R,G001,CPI,_T,YOY_PCH_PA_PT,A,2024,5.8\n'
       'dataflow,IMF.STA:CPI(5.0.0),R,USA,CPI,_T,IX,Q,,\n')
IMTS = ('DATAFLOW,COUNTRY,INDICATOR,COUNTERPART_COUNTRY,FREQUENCY,TIME_PERIOD,OBS_VALUE,SCALE,PRECISION\n'
        'IMF.STA:IMTS(1.0.0),USA,XG_FOB_USD,KOS,A,2020,1250000,,\n'
        'IMF.STA:IMTS(1.0.0),USA,MG_CIF_USD,CAN,A,2020,NaN,,\n')


class ImfSdmxPipelineTest(unittest.TestCase):
    def test_sdmx_csv_versions(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cpi.csv').write_text(CPI)
            (root / 'imts.csv').write_text(IMTS)
            store = Store(root / 'data')
            catalog = Catalog(PROJECT / 'data')
            shards = [{'path': root / name, 'retrieved_at': '2026-09-15T00:00:00+00:00'} for name in ('cpi.csv', 'imts.csv')]
            store.import_shards('imf_sdmx', shards, {'publisher': 'fixture', 'acquisition': catalog.get('imf_sdmx')['acquisition']}, complete=True)
            records = list(store.records(Runner(catalog, store, PROJECT).run('imf_sdmx')))
        obs = {r['id']: r for r in records if r['kind'] == 'observation'}
        self.assertEqual(len(obs), 4)
        cpi = obs['imf:CPI:USA.CPI._T.IX.M:2025-M01']
        self.assertEqual((cpi['subject'], cpi['metric'], cpi['unit'], cpi['valid_from'], cpi['valid_to']),
                         ('iso3:USA', 'consumer_price_index', 'index', '2025-01-01', '2025-02-01'))
        world = obs['imf:CPI:G001.CPI._T.YOY_PCH_PA_PT.A:2024']
        self.assertEqual((world['subject'], world['metric']), ('agg:imf:G001', 'cpi_inflation_yoy'))
        exports = obs['imf:IMTS:USA.XG_FOB_USD.KOS.A:2020']
        self.assertEqual((exports['metric'], exports['unit'], exports['dimensions']['counterpart']), ('goods_exports_fob', 'USD', 'iso3:XKX'))
        self.assertIsNone(obs['imf:IMTS:USA.MG_CIF_USD.CAN.A:2020']['value'])


if __name__ == '__main__':
    unittest.main()
