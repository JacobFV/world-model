"""Offline fixture test for full MRDS + MCS files (legacy sample path is covered by tests/test_normalizer_contracts.py)."""
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

MRDS = ('dep_id,url,mrds_id,mas_id,site_name,latitude,longitude,region,country,state,county,com_type,commod1,commod2,commod3,'
        'oper_type,dep_type,prod_size,dev_stat,ore,score\n'
        '10009306,"https://mrdata.usgs.gov/mrds/show-mrds.php?dep_id=10009306",D002208,,"Horse Mountain",39.69999,-106.20061,NA,'
        '"United States",Colorado,Eagle,M,"Uranium, Vanadium",Gold,,Unknown,,N,Occurrence,,E\n'
        '10000001,,W000001,,"Mina Norte",-23.5,-70.4,SA,Chile,,,M,Copper,,,Surface,,L,Producer,,B\n')
MCS = ('MCS chapter,Section,Commodity,Country,Statistics,Statistics_detail,Unit,Year,Value,Notes,Is critical mineral 2025,Other notes\n'
       'COPPER,World Mine Production and Reserves,Copper,Chile,Production,Mine production,thousand metric tons,2025_estimated,"5,300",,Yes,\n'
       'COPPER,World Mine Production and Reserves,Copper,Chile,Reserves,Reserves,thousand metric tons,2025_estimated,"190,000",,Yes,\n'
       'COPPER,Salient Statistics—United States,Copper,United States,Production,Mine production,thousand metric tons,2021,W,,Yes,\n')
T3 = ('﻿Source,Year,State,Value _millions_prelim_2025,State_Rank_prelim_2025,State_percent_total_prelim_2025,Principal_commodities,State_Notes\n'
      'MCS2026,2025_estimated,Alabama,2480,15,2.22,"Cement, lime",Alphabetical order.\n')


class UsgsResourcesFullTest(unittest.TestCase):
    def test_full_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            for name in ('dataset.json', 'pipeline.py', 'pipeline_full.py'):
                shutil.copy2(HERE / name, catalog / name)
            with zipfile.ZipFile(tmp / 'mrds.zip', 'w') as archive:
                archive.writestr('mrds.csv', MRDS)
                archive.writestr('mrds.met', 'metadata')
            (tmp / 'mcs.csv').write_bytes(MCS.encode('cp1252'))
            (tmp / 't3.csv').write_text(T3, encoding='utf-8')
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / n)} for n in ('mrds.zip', 'mcs.csv', 't3.csv')],
                                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = list(store.records(ref))
        by_id = {r['id']: r for r in records}
        deposit = by_id['usgsres:mrds:10009306']
        self.assertEqual((deposit['entity_id'], deposit['entity_type']), ('mrds:10009306', 'resource_deposit'))
        contains = {r['object'] for r in records if r.get('predicate') == 'contains_resource' and r['subject'] == 'mrds:10009306'}
        self.assertEqual(contains, {'commodity:usgs:uranium', 'commodity:usgs:vanadium', 'commodity:usgs:gold'})
        self.assertEqual(by_id['usgsres:mrds:10009306:located_in']['object'], 'geo:US:state:08')
        self.assertEqual(by_id['usgsres:mrds:10000001:located_in']['object'], 'mrds:country:chile')
        self.assertTrue(by_id['usgsres:mrds:10009306:latitude']['attributes']['valid_time_unknown'])
        reserves = next(r for r in records if r.get('metric') == 'mineral_reserves')
        self.assertEqual((reserves['subject'], reserves['value'], reserves['unit'], reserves['valid_from']),
                         ('mcs:country:chile', 190000, 'thousand metric tons', '2025-01-01'))
        self.assertTrue(reserves['attributes']['estimated'])
        withheld = next(r for r in records if r.get('metric') == 'mineral_production' and r['subject'] == 'geo:US')
        self.assertIsNone(withheld['value'])
        self.assertEqual(withheld['missing_reason'], 'withheld_or_not_available')
        self.assertEqual(by_id['usgsres:mcs_t3:AL:nonfuel_mineral_production_value']['value'], 2480)


if __name__ == '__main__':
    unittest.main()
