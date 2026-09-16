"""Offline fixture test: WPP gzip CSV shards -> evidence records through the Runner."""
import gzip
import shutil
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name

INDICATORS = ('﻿SortOrder,LocID,Notes,ISO3_code,ISO2_code,SDMX_code,LocTypeID,LocTypeName,ParentID,Location,VarID,Variant,Time,'
              'TPopulation1July,TPopulationMale1July,TFR,LEx,Births,NetMigrations,IMR\n'
              ',900,,,,,1,World,,World,2,Medium,2024,8161972.572,4093776.0,2.25,73.3,132000.0,0,26.9\n'
              ',840,,USA,US,840,4,Country/Area,918,United States of America,2,Medium,2024,345426.571,171000.5,1.62,79.3,3600.25,1286.1,5.4\n'
              ',840,,USA,US,840,4,Country/Area,918,United States of America,2,Medium,2023,343477.335,,1.62,,,,\n')
AGES = ('SortOrder,LocID,Notes,ISO3_code,ISO2_code,SDMX_code,LocTypeID,LocTypeName,ParentID,Location,VarID,Variant,Time,MidPeriod,'
        'AgeGrp,AgeGrpStart,AgeGrpSpan,PopMale,PopFemale,PopTotal\n'
        ',840,,USA,US,840,4,Country/Area,918,United States of America,2,Medium,2030,2030.5,0-4,0,5,9500.1,9100.9,18601\n'
        ',918,,,,,25,Region,1833,Northern America,2,Medium,2030,2030.5,0-4,0,5,10000,9600,19600\n')


class UnWppPipelineTest(unittest.TestCase):
    def test_indicators_and_age_detail(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            shards = []
            for name, text in (('indicators.csv.gz', INDICATORS), ('ages.csv.gz', AGES)):
                path = tmp / name
                path.write_bytes(gzip.compress(text.encode('utf-8')))
                shards.append({'path': str(path)})
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        usa = records['unwpp:USA:population:all:2024']
        self.assertEqual((usa['subject'], usa['value'], usa['unit']), ('iso3:USA', 345426571, 'people'))
        self.assertEqual((usa['valid_from'], usa['valid_to']), ('2024-07-01', '2024-07-02'))
        self.assertTrue(records['unwpp:USA:total_fertility_rate:all:2024']['attributes']['projection'])
        self.assertFalse(records['unwpp:USA:population:all:2023']['attributes']['projection'])
        self.assertEqual(records['unwpp:USA:births:all:2024']['value'], 3600250)
        self.assertEqual(records['unwpp:USA:population:male:2024']['dimensions']['sex'], 'male')
        self.assertNotIn('unwpp:USA:life_expectancy_at_birth:all:2023', records)  # blank cells are omitted
        self.assertEqual(records['unwpp:entity:loc900']['attributes']['aggregate'], True)
        self.assertEqual(records['unwpp:within:USA:loc918']['object'], 'unwpp:loc:918')
        age = records['unwpp:USA:pop:female:0-4:2030']
        self.assertEqual((age['value'], age['dimensions']['age_group']), (9100900, '0-4'))
        self.assertFalse(any(key.startswith('unwpp:loc918:pop:') for key in records))  # no age detail for aggregates
        self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:') for r in records.values()))


if __name__ == '__main__':
    unittest.main()
