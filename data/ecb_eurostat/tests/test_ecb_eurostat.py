"""Offline acceptance test for ecb_eurostat on a tiny ECB ZIP and Eurostat TSV shards."""
import gzip
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
ECB = 'Date,USD,JPY,CYP,\n2026-09-15,1.1539,178.86,N/A,\n2026-09-14,1.1551,178.52,N/A,\n'
GDP = ('freq,unit,na_item,geo\\TIME_PERIOD\t2023 \t2024 \t2025 \n'
       'A,CP_MEUR,B1GQ,EL\t200000.5 \t210000.0 p\t: \n'
       'A,CP_MEUR,B1GQ,EU27_2020\t: c\t17000000 \t: \n'
       'A,PC_GDP,B1GQ,DE\t100 \t100 \t100 \n')
REGIONAL = 'freq,unit,geo\\TIME_PERIOD\t2022 \nA,MIO_EUR,DE11\t1000 \n'
UNEMPLOYMENT = 'freq,s_adj,age,unit,sex,geo\\TIME_PERIOD\t2025-01 \t2025-02 \nM,SA,TOTAL,PC_ACT,T,FR\t7.3 \t7.4 \n'
TRADE = ('freq,stk_flow,indic_et,partner,sitc06,geo\\TIME_PERIOD\t2025-01 \n'
         'M,BAL_RT,TRD_VAL,US,TOTAL,EU27_2020\t12.5 \n'
         'M,EXP,TRD_VAL,US,TOTAL,EU27_2020\t30 \n'
         'M,EXP,SHARE,US,TOTAL,EU27_2020\t9 \n')
RATES = 'freq,int_rt,geo\\TIME_PERIOD\t2025-01 \nM,MCBY,AT\t2.9 \n'


def gz(text):
    return gzip.compress(text.encode())


class EcbEurostatPipelineTest(unittest.TestCase):
    def test_ecb_and_eurostat_shapes(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / 'data')
            catalog = Catalog(PROJECT / 'data')
            definition = catalog.get('ecb_eurostat')
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, 'w') as archive:
                archive.writestr('eurofxref-hist.csv', ECB)
            base = 'https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data/'
            payloads = [('ecb.zip', buffer.getvalue(), 'https://www.ecb.europa.eu/stats/eurofxref/eurofxref-hist.zip'),
                        ('gdp.tsv.gz', gz(GDP), base + 'nama_10_gdp?format=TSV&compressed=true'),
                        ('reg.tsv.gz', gz(REGIONAL), base + 'nama_10r_2gdp?format=TSV&compressed=true'),
                        ('une.tsv.gz', gz(UNEMPLOYMENT), base + 'une_rt_m?format=TSV&compressed=true'),
                        ('ext.tsv.gz', gz(TRADE), base + 'ext_st_eu27_2020sitc?format=TSV&compressed=true'),
                        ('irt.tsv.gz', gz(RATES), base + 'irt_lt_mcby_m?format=TSV&compressed=true')]
            shards = []
            for name, content, url in payloads:
                (root / name).write_bytes(content)
                shards.append({'path': root / name, 'retrieved_at': '2026-09-15T00:00:00+00:00', 'request': {'method': 'GET', 'url': url}})
            store.import_shards('ecb_eurostat', shards, {'publisher': 'fixture', 'acquisition': definition['acquisition']}, complete=True)
            records = list(store.records(Runner(catalog, store, PROJECT).run('ecb_eurostat')))
        obs = {r['id']: r for r in records if r['kind'] == 'observation'}
        usd = obs['ecb:EXR.D.USD.EUR.SP00.A:2026-09-15']
        self.assertEqual((usd['value'], usd['unit'], usd['valid_to']), (1.1539, 'USD_per_EUR', '2026-09-16'))
        self.assertFalse(any('CYP' in key for key in obs))
        greece = obs['eurostat:nama_10_gdp:A,CP_MEUR,B1GQ,EL:2024']
        self.assertEqual((greece['subject'], greece['metric'], greece['unit'], greece['value']), ('iso3:GRC', 'gdp', 'EUR', 210000.0e6))
        self.assertEqual(greece['attributes']['flags'], 'p')
        confidential = obs['eurostat:nama_10_gdp:A,CP_MEUR,B1GQ,EU27_2020:2023']
        self.assertIsNone(confidential['value'])
        self.assertEqual(confidential['missing_reason'], 'confidential')
        self.assertFalse(any(key.endswith(':2025') and 'EL' in key for key in obs))
        self.assertNotIn('eurostat:nama_10_gdp:A,PC_GDP,B1GQ,DE:2023', obs)  # unit filtered by parameters
        region = obs['eurostat:nama_10r_2gdp:A,MIO_EUR,DE11:2022']
        self.assertEqual(region['subject'], 'nuts2021:DE11')
        within = [r for r in records if r['kind'] == 'assertion' and r['subject'] == 'nuts2021:DE11']
        self.assertEqual(within[0]['object'], 'iso3:DEU')
        balance = obs['eurostat:ext_st_eu27_2020sitc:M,BAL_RT,TRD_VAL,US,TOTAL,EU27_2020:2025-01']
        self.assertEqual((balance['metric'], balance['unit'], balance['value'], balance['dimensions']['partner']),
                         ('trade_balance', 'EUR', 12.5e6, 'iso3:USA'))
        self.assertEqual(obs['eurostat:ext_st_eu27_2020sitc:M,EXP,TRD_VAL,US,TOTAL,EU27_2020:2025-01']['metric'], 'exports_value')
        self.assertNotIn('eurostat:ext_st_eu27_2020sitc:M,EXP,SHARE,US,TOTAL,EU27_2020:2025-01', obs)  # indic_et filter
        long_rate = obs['eurostat:irt_lt_mcby_m:M,MCBY,AT:2025-01']
        self.assertEqual((long_rate['metric'], long_rate['unit']), ('long_term_interest_rate', 'percent_per_annum'))
        rate = obs['eurostat:une_rt_m:M,SA,TOTAL,PC_ACT,T,FR:2025-02']
        self.assertEqual((rate['metric'], rate['unit'], rate['valid_from'], rate['valid_to']),
                         ('unemployment_rate', 'percent_of_labour_force', '2025-02-01', '2025-03-01'))


if __name__ == '__main__':
    unittest.main()
