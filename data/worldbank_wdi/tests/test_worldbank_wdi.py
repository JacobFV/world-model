"""Offline acceptance test for worldbank_wdi on a tiny WDI-shaped ZIP."""
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
COUNTRY = ('Country Code,Short Name,Table Name,Long Name,2-alpha code,Currency Unit,Special Notes,Region,Income Group\n'
           'USA,United States,United States,United States of America,US,U.S. dollar,,North America,High income\n'
           'HIC,High income,High income,High income,XD,,,,\n')
SERIES = ('Series Code,Topic,Indicator Name,Unit of measure,Periodicity,License Type\n'
          'NY.GDP.MKTP.CD,Economic Policy,GDP (current US$),,Annual,CC BY-4.0\n'
          'SL.UEM.TOTL.ZS,Labor,"Unemployment, total (% of total labor force) (modeled ILO estimate)",,Annual,CC BY-4.0\n')
DATA = ('Country Name,Country Code,Indicator Name,Indicator Code,1960,1961,\n'
        'United States,USA,GDP (current US$),NY.GDP.MKTP.CD,543300000000,,\n'
        'High income,HIC,GDP (current US$),NY.GDP.MKTP.CD,,900000000000,\n'
        'Not classified,INX,GDP (current US$),NY.GDP.MKTP.CD,1,,\n'
        'United States,USA,"Unemployment, total (% of total labor force) (modeled ILO estimate)",SL.UEM.TOTL.ZS,,5.5,\n')


class WorldBankWdiPipelineTest(unittest.TestCase):
    def test_wide_csv_to_annual_observations(self):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w') as archive:
            archive.writestr('WDICSV.csv', DATA)
            archive.writestr('WDICountry.csv', COUNTRY)
            archive.writestr('WDISeries.csv', SERIES)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'wdi.zip').write_bytes(buffer.getvalue())
            store = Store(root / 'data')
            catalog = Catalog(PROJECT / 'data')
            store.import_shards('worldbank_wdi', [{'path': root / 'wdi.zip', 'retrieved_at': '2026-09-15T00:00:00+00:00'}],
                                {'publisher': 'fixture', 'acquisition': catalog.get('worldbank_wdi')['acquisition']}, complete=True)
            records = list(store.records(Runner(catalog, store, PROJECT).run('worldbank_wdi')))
        obs = {r['id']: r for r in records if r['kind'] == 'observation'}
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        gdp = obs['wdi:USA:NY.GDP.MKTP.CD:1960']
        self.assertEqual((gdp['subject'], gdp['metric'], gdp['unit'], gdp['value']), ('iso3:USA', 'wdi_ny_gdp_mktp_cd', 'USD', 543300000000.0))
        self.assertEqual((gdp['valid_from'], gdp['valid_to']), ('1960-01-01', '1961-01-01'))
        self.assertEqual(obs['wdi:USA:SL.UEM.TOTL.ZS:1961']['unit'], 'percent_of_total_labor_force')
        self.assertEqual(obs['wdi:HIC:NY.GDP.MKTP.CD:1961']['subject'], 'agg:wdi:HIC')
        self.assertTrue(entities['agg:wdi:INX']['attributes']['aggregate'])
        self.assertEqual(entities['iso3:USA']['attributes']['income_group'], 'High income')
        self.assertTrue(any(r['kind'] == 'assertion' and r['subject'] == 'iso3:USA' and r['object'] == 'agg:wdi:HIC' for r in records))
        self.assertEqual(len(obs), 4)


if __name__ == '__main__':
    unittest.main()
