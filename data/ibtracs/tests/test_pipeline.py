"""Offline fixture test: IBTrACS CSV (header + units row) -> storm entity, genesis event and track observations."""
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

CSV = ('SID,SEASON,NUMBER,BASIN,SUBBASIN,NAME,ISO_TIME,NATURE,LAT,LON,WMO_WIND,WMO_PRES,WMO_AGENCY,TRACK_TYPE,DIST2LAND,LANDFALL,IFLAG,'
       'USA_AGENCY,USA_ATCF_ID,USA_WIND,USA_PRES,USA_SSHS,STORM_SPEED,STORM_DIR\n'
       ' ,Year, , , , , , ,degrees_north,degrees_east,kts,mb, , ,km,km, , , ,kts,mb,1,kts,degrees\n'
       '2005236N23285,2005,70,NA,GM,KATRINA,2005-08-29 09:00:00,TS,29.3,-89.6,110,920,hurdat_atl,main,0,0,O_____________,hurdat_atl,AL122005,110,920,3,13,5\n'
       '2005236N23285,2005,70,NA,GM,KATRINA,2005-08-29 11:10:00,TS,29.5,-89.6,110,920,hurdat_atl,main,0,0,P_____________,hurdat_atl,AL122005,110,920,3,13,5\n'
       '2005236N23285,2005,70,NA,GM,KATRINA,2005-08-29 09:00:00,TS,29.4,-89.5, , , ,spur-merge,0,0,I_____________, , , , ,-5,13,5\n')


class IbtracsTest(unittest.TestCase):
    def test_track(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            (tmp / 'tracks.csv').write_text(CSV)
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / 'tracks.csv')}], {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = {r['id']: r for r in store.records(ref)}
        storm = records['ibtracs:entity:2005236N23285']
        self.assertEqual((storm['entity_id'], storm['label']), ('ibtracs:storm:2005236N23285', 'Katrina (2005, NA)'))
        self.assertEqual(records['ibtracs:genesis:2005236N23285']['occurred_at'], '2005-08-29T09:00:00Z')
        wind = records['ibtracs:2005236N23285:200508290900:max_sustained_wind:usa']
        self.assertEqual((wind['value'], wind['unit'], wind['valid_to']), (110, 'kt', '2005-08-29T12:00:00Z'))
        self.assertIn('ibtracs:2005236N23285:200508291110:latitude', records)  # non-synoptic landfall time
        self.assertEqual(records['ibtracs:2005236N23285:200508290900:spur-merge:latitude']['value'], 29.4)
        self.assertTrue(records['ibtracs:2005236N23285:200508291110:latitude']['dimensions']['interpolated'])
        self.assertEqual(records['ibtracs:2005236N23285:200508290900:saffir_simpson_category:usa']['value'], 3)
        self.assertEqual(sum(1 for r in records.values() if r['kind'] == 'entity' and r['entity_type'] == 'entity'), 1)


if __name__ == '__main__':
    unittest.main()
