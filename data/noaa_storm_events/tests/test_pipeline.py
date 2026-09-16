"""Offline fixture test: Storm Events details/fatalities/locations gzip CSVs (including a malformed quote)."""
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

HEADER = ('BEGIN_YEARMONTH,BEGIN_DAY,BEGIN_TIME,END_YEARMONTH,END_DAY,END_TIME,EPISODE_ID,EVENT_ID,STATE,STATE_FIPS,YEAR,MONTH_NAME,'
          'EVENT_TYPE,CZ_TYPE,CZ_FIPS,CZ_NAME,WFO,BEGIN_DATE_TIME,CZ_TIMEZONE,END_DATE_TIME,INJURIES_DIRECT,INJURIES_INDIRECT,'
          'DEATHS_DIRECT,DEATHS_INDIRECT,DAMAGE_PROPERTY,DAMAGE_CROPS,SOURCE,MAGNITUDE,MAGNITUDE_TYPE,FLOOD_CAUSE,CATEGORY,'
          'TOR_F_SCALE,TOR_LENGTH,TOR_WIDTH,TOR_OTHER_WFO,TOR_OTHER_CZ_STATE,TOR_OTHER_CZ_FIPS,TOR_OTHER_CZ_NAME,BEGIN_RANGE,'
          'BEGIN_AZIMUTH,BEGIN_LOCATION,END_RANGE,END_AZIMUTH,END_LOCATION,BEGIN_LAT,BEGIN_LON,END_LAT,END_LON,EPISODE_NARRATIVE,'
          'EVENT_NARRATIVE,DATA_SOURCE\n')
DETAILS = HEADER + (
    '202404,30,2033,202404,30,2045,189851,1174463,"OKLAHOMA",40,2024,"April","Tornado","C",141,"TILLMAN","OUN",'
    '"30-APR-24 20:33:00","CST-6","30-APR-24 20:45:00","3","0","1","0","1.5M","10.00K","Emergency Manager",,,,,"EF2","5.2","300",'
    ',,,,"1","N","FREDERICK","2","NE","HOLLISTER","34.3444","-98.983","34.40","-98.90","Episode "quoted" text","",CSV\n'
    '202407,1,0,202407,5,900,193486,1195301,"LOUISIANA",22,2024,"July","Excessive Heat","Z",18,"NATCHITOCHES","SHV",'
    '"01-JUL-24 00:00:00","CST-6","05-JUL-24 09:00:00","0","0","0","0","0.00K","0.00K","ASOS",,,,,,,,,,,,,,,,,,,,,,"heat","","CSV"\n')
FATALITIES = ('FAT_YEARMONTH,FAT_DAY,FAT_TIME,FATALITY_ID,EVENT_ID,FATALITY_TYPE,FATALITY_DATE,FATALITY_AGE,FATALITY_SEX,FATALITY_LOCATION\n'
              '202404,30,2040,53724,1174463,"D","04/30/2024 20:40:00",83,"M","Mobile/Trailer Home"\n')
LOCATIONS = ('YEARMONTH,EPISODE_ID,EVENT_ID,LOCATION_INDEX,RANGE,AZIMUTH,LOCATION,LATITUDE,LONGITUDE,LAT2,LON2\n'
             '202404,189851,1174463,1,1,"N","FREDERICK",34.3444,-98.983,3420664,9858980\n')


class StormEventsTest(unittest.TestCase):
    def test_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            shards = []
            for name, text in (('d.csv.gz', DETAILS), ('f.csv.gz', FATALITIES), ('l.csv.gz', LOCATIONS)):
                (tmp / name).write_bytes(gzip.compress(text.encode('latin-1')))
                shards.append({'path': str(tmp / name)})
            store = Store(tmp / 'data')
            store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        event = records['stormev:event:1174463']
        self.assertEqual((event['event_type'], event['occurred_at']), ('tornado', '2024-05-01T02:33:00Z'))
        self.assertEqual(event['participants'], ['noaa:storm_event:1174463', 'geo:US:county:40141'])
        self.assertEqual((event['attributes']['damage_property'], event['attributes']['tor_f_scale']), (1500000, 'EF2'))
        damage = records['stormev:1174463:damage_crops']
        self.assertEqual((damage['metric'], damage['value'], damage['unit']), ('storm_damage_crops', 10000, 'USD'))
        deaths = records['stormev:1174463:deaths_direct']
        self.assertEqual((deaths['metric'], deaths['dimensions']['attribution']), ('storm_deaths', 'direct'))
        self.assertEqual(records['stormev:1174463:injuries_direct']['value'], 3)
        heat = records['stormev:event:1195301']
        self.assertEqual(heat['participants'][1], 'noaa:zone:22:Z:018')
        self.assertEqual(heat['attributes']['damage_property'], 0)
        self.assertNotIn('stormev:1195301:damage_property', records)  # zero values only in event attributes
        self.assertEqual(records['stormev:fatality:1174463:202404:53724']['participants'], ['noaa:storm_event:1174463'])
        self.assertEqual(records['stormev:1174463:loc1:latitude']['valid_from'], '2024-04-01')


if __name__ == '__main__':
    unittest.main()
