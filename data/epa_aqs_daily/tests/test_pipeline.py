"""Offline fixture test: AQS daily ZIPs -> county-day PM2.5 and ozone observations with de-duplication."""
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
HEADER = ('"State Code","County Code","Site Num","Parameter Code","POC","Latitude","Longitude","Datum","Parameter Name",'
          '"Sample Duration","Pollutant Standard","Date Local","Units of Measure","Event Type","Observation Count",'
          '"Observation Percent","Arithmetic Mean","1st Max Value","1st Max Hour","AQI","Method Code","Method Name",'
          '"Local Site Name","Address","State Name","County Name","City Name","CBSA Name","Date of Last Change"\n')


def row(site, poc, parameter, duration, standard, day, event, mean, first_max):
    return (f'"06","075","{site}","{parameter}",{poc},37.7,-122.4,"NAD83","x","{duration}","{standard}","{day}","u","{event}",'
            f'1,100.0,{mean},{first_max},0,50,"1","m","s","a","California","San Francisco","San Francisco","SF","2024-01-01"\n')


class AqsTest(unittest.TestCase):
    def test_county_days(self):
        pm = HEADER + (row('0001', 1, '88101', '24 HOUR', 'PM25 24-hour 2012', '2020-09-09', 'Excluded', 40.0, 40.0)
                       + row('0001', 1, '88101', '24 HOUR', 'PM25 24-hour 2012', '2020-09-09', 'Included', 60.0, 60.0)
                       + row('0001', 1, '88101', '24 HOUR', 'PM25 Annual 2012', '2020-09-09', 'Included', 60.0, 60.0)
                       + row('0002', 1, '88101', '24 HOUR', 'PM25 24-hour 2012', '2020-09-09', 'None', 20.0, 20.0))
        ozone = HEADER + (row('0005', 1, '44201', '8-HR RUN AVG BEGIN HOUR', 'Ozone 8-hour 2015', '2020-09-09', 'None', 0.03, 0.061)
                          + row('0005', 1, '44201', '1 HOUR', 'Ozone 1-hour 1979', '2020-09-09', 'None', 0.03, 0.07)
                          + row('0006', 1, '42602', '1 HOUR', 'NO2 1-hour 2010', '2020-09-09', 'None', 12.5, 30.0)
                          + row('0006', 1, '42602', '1 HOUR', 'NO2 Annual 1971', '2020-09-09', 'None', 12.5, 30.0)
                          + row('0007', 1, '42101', '8-HR RUN AVG END HOUR', 'CO 8-hour 1971', '2020-09-09', 'None', 0.5, 0.9))
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            for name, text in (('daily_88101_2020', pm), ('daily_44201_2020', ozone)):
                with zipfile.ZipFile(tmp / (name + '.zip'), 'w') as archive:
                    archive.writestr(name + '.csv', text)
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / 'daily_88101_2020.zip')}, {'path': str(tmp / 'daily_44201_2020.zip')}],
                                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        pm25 = records['aqs:88101:06075:20200909']
        self.assertEqual((pm25['value'], pm25['unit'], pm25['attributes']),
                         (40.0, 'ug/m3', {'monitors': 2, 'county_max': 60.0, 'mean_daily_max': 40.0}))
        no2 = records['aqs:42602:06075:20200909']
        self.assertEqual((no2['metric'], no2['value'], no2['unit'], no2['attributes']['monitors'], no2['attributes']['mean_daily_max']),
                         ('no2_daily_mean', 12.5, 'ppb', 1, 30.0))
        self.assertNotIn('aqs:42101:06075:20200909', records)  # CO uses 1-hour rows only
        self.assertEqual((pm25['valid_from'], pm25['valid_to']), ('2020-09-09', '2020-09-10'))
        o3 = records['aqs:44201:06075:20200909']
        self.assertEqual((o3['metric'], o3['value'], o3['attributes']['monitors']), ('ozone_daily_max_8hr', 0.061, 1))
        self.assertEqual(records['aqs:entity:geo:US:county:06075']['entity_type'], 'county')


if __name__ == '__main__':
    unittest.main()
