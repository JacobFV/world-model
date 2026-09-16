"""Offline pipeline test: tiny Ember long CSV + OWID CSV shards through the Runner."""
import gzip
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]

EMBER = ('Area,ISO 3 code,Year,Area type,Continent,Ember region,EU,OECD,G20,G7,ASEAN,Category,Subcategory,Variable,Unit,Value,YoY absolute change,YoY % change\n'
         'Germany,DEU,2024,Country or economy,Europe,Europe,1.0,1.0,1.0,1.0,0.0,Electricity generation,Fuel,Coal,TWh,99.5,,\n'
         'Germany,DEU,2024,Country or economy,Europe,Europe,1.0,1.0,1.0,1.0,0.0,Capacity,Fuel,Solar,GW,,,\n'
         'World,,2024,Region,,,0.0,0.0,0.0,0.0,0.0,Power sector emissions,Total,Total emissions,mtCO2,14000,,\n')
OWID = ('country,year,iso_code,population,coal_consumption,coal_cons_change_pct,solar_share_elec\n'
        'Germany,2023,DEU,84000000,500.5,-3.2,12.1\n'
        'World,2023,,8000000000,,,\n'
        'USSR,1985,,280000000,,,\n'
        'Kosovo,2023,,1800000,,,\n'
        'Netherlands Antilles,1995,ANT,200000,,,\n')


class EmberOwidPipelineTest(unittest.TestCase):
    def test_full_shards_normalize(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            (temp / 'ember.csv').write_text(EMBER)
            (temp / 'owid.csv').write_text(OWID)
            store = Store(temp / 'data')
            store.import_shards('ember_owid_energy', [{'path': temp / 'ember.csv'}, {'path': temp / 'owid.csv'}],
                                {'publisher': 'fixture', 'license': 'CC-BY-4.0'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('ember_owid_energy')
            path = store.version_dir(ref) / 'records.jsonl.gz'
            records = [json.loads(line) for line in gzip.open(path, 'rt')]
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(set(entities), {'iso3:DEU', 'ember:region:world', 'owid:region:world', 'owid:historical_country:SUN',
                                         'iso3:XKX', 'owid:historical_country:ANT'})
        self.assertTrue(entities['ember:region:world']['attributes']['aggregate'])
        self.assertTrue(entities['owid:historical_country:SUN']['attributes']['historical'])
        self.assertEqual(entities['owid:historical_country:ANT']['entity_type'], 'country')
        obs = [r for r in records if r['kind'] == 'observation']
        coal = next(r for r in obs if r['metric'] == 'electricity_generation' and r['dimensions'].get('fuel') == 'coal')
        self.assertEqual((coal['subject'], coal['value'], coal['unit'], coal['valid_from'], coal['valid_to']),
                         ('iso3:DEU', 99.5, 'TWh', '2024-01-01', '2025-01-01'))
        solar = next(r for r in obs if r['metric'] == 'electricity_capacity')
        self.assertIsNone(solar['value'])
        self.assertEqual(solar['missing_reason'], 'source_blank')
        owid = [r for r in obs if r['dimensions']['publisher'] == 'owid']
        self.assertEqual(sorted((r['metric'], r['subject']) for r in owid),
                         [('electricity_generation_share', 'iso3:DEU'), ('population', 'iso3:DEU'), ('population', 'iso3:XKX'),
                          ('population', 'owid:historical_country:ANT'), ('population', 'owid:historical_country:SUN'),
                          ('population', 'owid:region:world'), ('primary_energy_consumption', 'iso3:DEU')])
        self.assertFalse(any('change' in r['attributes'].get('source_column', '') for r in owid))
        self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:') for r in records))


if __name__ == '__main__':
    unittest.main()
