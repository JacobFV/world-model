"""Offline acceptance test for oecd_sdmx on tiny SDMX-CSV (csvfile) shards."""
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
CLI = ('DATAFLOW,REF_AREA,FREQ,MEASURE,UNIT_MEASURE,ACTIVITY,ADJUSTMENT,TRANSFORMATION,TIME_HORIZ,METHODOLOGY,TIME_PERIOD,OBS_VALUE,OBS_STATUS,UNIT_MULT,DECIMALS,BASE_PER\n'
       'OECD.SDD.STES:DSD_STES@DF_CLI(4.1),USA,M,LI,IX,_Z,AA,IX,_Z,H,2025-01,100.2,A,0,2,\n'
       'OECD.SDD.STES:DSD_STES@DF_CLI(4.1),G20,M,LI,IX,_Z,AA,IX,_Z,H,2025-01,,M,0,2,\n')
QNA = ('DATAFLOW,FREQ,ADJUSTMENT,REF_AREA,SECTOR,COUNTERPART_SECTOR,TRANSACTION,INSTR_ASSET,ACTIVITY,EXPENDITURE,UNIT_MEASURE,PRICE_BASE,TRANSFORMATION,TABLE_IDENTIFIER,TIME_PERIOD,OBS_VALUE,REF_YEAR_PRICE,BASE_PER,CONF_STATUS,DECIMALS,OBS_STATUS,UNIT_MULT,CURRENCY\n'
       'OECD.SDD.NAD:DSD_NAMAIN1@DF_QNA(1.1),Q,Y,USA,S1,S1,B1GQ,_Z,_Z,_Z,XDC,V,LA,T0102,2025-Q2,30000.5,,,F,1,A,6,USD\n')


class OecdSdmxPipelineTest(unittest.TestCase):
    def test_csvfile_shards(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'cli.csv').write_text(CLI)
            (root / 'qna.csv').write_text(QNA)
            store = Store(root / 'data')
            catalog = Catalog(PROJECT / 'data')
            shards = [{'path': root / n, 'retrieved_at': '2026-09-15T00:00:00+00:00'} for n in ('cli.csv', 'qna.csv')]
            store.import_shards('oecd_sdmx', shards, {'publisher': 'fixture', 'acquisition': catalog.get('oecd_sdmx')['acquisition']}, complete=True)
            records = list(store.records(Runner(catalog, store, PROJECT).run('oecd_sdmx')))
        obs = {r['id']: r for r in records if r['kind'] == 'observation'}
        cli = obs['oecd:DF_CLI:USA.M.LI.IX._Z.AA.IX._Z.H:2025-01']
        self.assertEqual((cli['subject'], cli['metric'], cli['unit'], cli['valid_to']),
                         ('iso3:USA', 'composite_leading_indicator', 'index', '2025-02-01'))
        g20 = obs['oecd:DF_CLI:G20.M.LI.IX._Z.AA.IX._Z.H:2025-01']
        self.assertEqual((g20['subject'], g20['value'], g20['missing_reason']), ('agg:oecd:G20', None, 'source_status_M'))
        gdp = obs['oecd:DF_QNA:Q.Y.USA.S1.S1.B1GQ._Z._Z._Z.XDC.V.LA.T0102:2025-Q2']
        self.assertEqual((gdp['metric'], gdp['unit'], gdp['value'], gdp['valid_from'], gdp['valid_to']),
                         ('gdp', 'national_currency', 30000.5e6, '2025-04-01', '2025-07-01'))
        self.assertEqual(sum(1 for r in records if r['kind'] == 'entity' and r['entity_id'] == 'iso3:USA'), 2)


if __name__ == '__main__':
    unittest.main()
