"""Offline acceptance test: FAOSTAT normalized ZIP shards -> evidence records through the Runner."""
import tempfile
import unittest
import zipfile
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
QCL = ('Area Code,Area Code (M49),Area,Item Code,Item Code (CPC),Item,Element Code,Element,Year Code,Year,Unit,Value,Flag,Note\n'
       "231,'840,United States of America,56,'0112,Maize (corn),5510,Production,2023,2023,t,389694460.000000,A,\n"
       "5000,'001,World,56,'0112,Maize (corn),5510,Production,2023,2023,t,,M,\n"
       "231,'840,United States of America,56,'0112,Maize (corn),5510,Production,1999,1999,t,1.000000,A,\n"
       "231,'840,United States of America,56,'0112,Maize (corn),9999,Unlisted,2023,2023,t,1.000000,A,\n")
PP = ('Area Code,Area Code (M49),Area,Item Code,Item Code (CPC),Item,Element Code,Element,Year Code,Year,Months Code,Months,Unit,Value,Flag\n'
      "231,'840,United States of America,56,'0112,Maize (corn),5532,Producer Price (USD/tonne),2023,2023,7021,Annual value,USD,183.500000,A\n"
      "231,'840,United States of America,56,'0112,Maize (corn),5532,Producer Price (USD/tonne),2023,2023,7003,March,USD,190.000000,A\n")
FBS = ('Area Code,Area Code (M49),Area,Item Code,Item Code (FBS),Item,Element Code,Element,Year Code,Year,Unit,Value,Flag,Note\n'
       "231,'840,United States of America,2514,'S2514,Maize and products,5611,Import quantity,2022,2022,1000 t,1234.000000,E,\n"
       "231,'840,United States of America,2501,'S2501,Population,511,Total Population - Both sexes,2022,2022,1000 No,333288.000000,X,\n")
TM = ('Reporter Country Code,Reporter Country Code (M49),Reporter Countries,Partner Country Code,Partner Country Code (M49),'
      'Partner Countries,Item Code,Item Code (CPC),Item,Element Code,Element,Year Code,Year,Unit,Value,Flag\n'
      "138,'484,Mexico,231,'840,United States of America,56,'0112,Maize (corn),5610,Import quantity,2023,2023,t,19000000.000000,A\n"
      "138,'484,Mexico,231,'840,United States of America,56,'0112,Maize (corn),5622,Import value,2023,2023,1000 USD,5100000.000000,A\n"
      "231,'840,United States of America,138,'484,Mexico,56,'0112,Maize (corn),5922,Export value,2023,2023,1000 USD,5200000.000000,A\n")
TCL = ('Area Code,Area Code (M49),Area,Item Code,Item Code (CPC),Item,Element Code,Element,Year Code,Year,Unit,Value,Flag,Note\n'
       "231,'840,United States of America,56,'0112,Maize (corn),5910,Export quantity,2023,2023,t,55000000.000000,A,\n"
       "52040,'013.03,Central America (excluding intra-trade),56,'0112,Maize (corn),5610,Import quantity,2023,2023,t,1.000000,E,\n")
TCLI = ('Area Code,Area Code (M49),Area,Item Code,Item Code (CPC),Item,Indicator Code,Indicator,Year Code,Year,Unit,Value,Flag,Note\n'
        "231,'840,United States of America,56,'0112,Maize (corn),501,Import dependency ratio,2023,2023,,0.010000,E,\n")
QV = ('Area Code,Area Code (M49),Area,Item Code,Item Code (CPC),Item,Element Code,Element,Year Code,Year,Unit,Value,Flag\n'
      "231,'840,United States of America,56,'0112,Maize (corn),57,Gross Production Value (current thousand US$),2023,2023,1000 USD,70000000.000000,E\n")
CP = ('Area Code,Area Code (M49),Area,Item Code,Item,Element Code,Element,Months Code,Months,Year Code,Year,Unit,Value,Flag,Note\n'
      "231,'840,United States of America,23013,\"Consumer Prices, Food Indices (2015 = 100)\",6125,Value,7001,January,2024,2024,,130.500000,X,base year is 2015\n")


def archive(path, member, text):
    with zipfile.ZipFile(path, 'w') as z:
        z.writestr(member + '_E_All_Data_(Normalized).csv', text)
        z.writestr(member + '_E_AreaCodes.csv', 'Area Code,M49 Code,Area\n')
    return path


class FaostatPipelineTest(unittest.TestCase):
    def records(self, members):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            shards = [{'path': archive(tmp / f'{n}.zip', member, text), 'url': f'https://example.org/{n}'}
                      for n, (member, text) in enumerate(members)]
            store = Store(tmp / 'data')
            store.import_shards('faostat', shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('faostat')
            return list(store.records(ref))

    def test_domains_areas_items_and_periods(self):
        records = self.records([('Production_Crops_Livestock', QCL), ('Prices', PP), ('FoodBalanceSheets', FBS)])
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['iso3:USA']['entity_type'], 'country')
        self.assertTrue(entities['fao:area:5000']['attributes']['aggregate'])
        self.assertIn('fao:item:56', entities)
        self.assertIn('fao:fbs_item:2514', entities)
        obs = [r for r in records if r['kind'] == 'observation']
        production = [r for r in obs if r['metric'] == 'production']
        self.assertEqual(len(production), 2)  # 1999 excluded by min_year; element 9999 not allowlisted
        usa = next(r for r in production if r['subject'] == 'iso3:USA')
        self.assertEqual((usa['value'], usa['unit'], usa['valid_from'], usa['dimensions']['domain']),
                         (389694460, 't', '2023-01-01', 'QCL'))
        world = next(r for r in production if r['subject'] == 'fao:area:5000')
        self.assertIsNone(world['value'])
        self.assertIn('flag_M', world['missing_reason'])
        prices = sorted((r['dimensions']['frequency'], r['valid_from'], r['valid_to'], r['unit']) for r in obs if r['metric'] == 'producer_price')
        self.assertEqual(prices, [('annual', '2023-01-01', '2024-01-01', 'USD/t'), ('monthly', '2023-03-01', '2023-04-01', 'USD/t')])
        population = next(r for r in obs if r['metric'] == 'population')
        self.assertEqual((population['unit'], population['value'], population['dimensions']), ('1000 number', 333288, {'domain': 'FBS', 'frequency': 'annual'}))
        imports = next(r for r in obs if r['metric'] == 'import_quantity')
        self.assertEqual((imports['unit'], imports['attributes']['flag']), ('1000 t', 'E'))

    def test_trade_matrix_totals_indicators_values_and_cpi(self):
        records = self.records([('Trade_DetailedTradeMatrix', TM), ('Trade_CropsLivestock', TCL),
                                ('Trade_CropsLivestockIndicators', TCLI), ('Value_of_Production', QV),
                                ('ConsumerPriceIndices', CP)])
        obs = [r for r in records if r['kind'] == 'observation']
        flows = [r for r in obs if r['dimensions']['domain'] == 'TM']
        self.assertEqual(len(flows), 3)
        quantity = next(r for r in flows if r['metric'] == 'import_quantity')
        self.assertEqual((quantity['subject'], quantity['dimensions']['partner'], quantity['dimensions']['item'],
                          quantity['dimensions']['element'], quantity['unit'], quantity['value']),
                         ('iso3:MEX', 'iso3:USA', 'fao:item:56', '5610', 't', 19000000))
        value = next(r for r in flows if r['metric'] == 'export_value')
        self.assertEqual((value['subject'], value['dimensions']['partner'], value['unit']), ('iso3:USA', 'iso3:MEX', '1000 USD'))
        totals = [r for r in obs if r['dimensions']['domain'] == 'TCL']
        export = next(r for r in totals if r['metric'] == 'export_quantity')
        self.assertEqual((export['subject'], export['unit'], export['value']), ('iso3:USA', 't', 55000000))
        self.assertNotIn('partner', export['dimensions'])
        central = next(r for r in totals if r['subject'] == 'fao:area:52040')
        self.assertTrue(central['attributes']['aggregate'])
        ratio = next(r for r in obs if r['dimensions']['domain'] == 'TCLI')
        self.assertEqual((ratio['metric'], ratio['unit'], ratio['value']), ('import_dependency_ratio', 'ratio', 0.01))
        gpv = next(r for r in obs if r['dimensions']['domain'] == 'QV')
        self.assertEqual((gpv['metric'], gpv['unit'], gpv['dimensions']['price_basis']), ('gross_production_value', '1000 USD', 'current'))
        cpi = next(r for r in obs if r['dimensions']['domain'] == 'CP')
        self.assertEqual((cpi['metric'], cpi['unit'], cpi['valid_from'], cpi['valid_to'], cpi['dimensions']['item']),
                         ('consumer_price_index', 'index_2015_100', '2024-01-01', '2024-02-01', 'fao:cpi_item:23013'))


if __name__ == '__main__':
    unittest.main()
