"""Offline acceptance test: COW/ATOP shards (incl. nested NMC ZIP) -> countries, alliances, capabilities, disputes."""
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]


def zipped(path, members):
    with zipfile.ZipFile(path, 'w') as z:
        for name, content in members.items():
            z.writestr(name, content)
    return path


class ConflictReferenceTest(unittest.TestCase):
    def test_reference_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            states = zipped(tmp / 'States2024.zip', {'States2024/statelist2024.csv':
                'stateabb,ccode,statenme,styear,stmonth,stday,endyear,endmonth,endday,version\n'
                'USA,2,United States of America,1816,1,1,2024,12,31,2024\nAUH,300,Austria-Hungary,1816,1,1,1918,11,3,2024\n'})
            inner = io.BytesIO()
            with zipfile.ZipFile(inner, 'w') as z:
                z.writestr('NMC-70-abridged.csv', 'stateabb,ccode,year,milex,milper,irst,pec,tpop,upop,cinc,version\r\n'
                                                  'USA,2,1816,3823,17,80,254,8659,101,.039697491,2025\r\n')
            nmc = zipped(tmp / 'NMCv7.zip', {'NMCv7/NMC-v7-abridged.zip': inner.getvalue(), '__MACOSX/NMCv7/._NMC-v7-abridged.zip': b'x'})
            contiguity = zipped(tmp / 'DirectContiguity320.zip', {'DirectContiguity320/contdir.csv':
                'dyad,statelno,statelab,statehno,statehab,conttype,begin,end,notes,version\n2020,2,"USA",20,"CAN",1,192001,201612,"x",3.2\n'})
            alliances = zipped(tmp / 'version4.1_csv.zip', {'version4.1_csv/alliance_v4.1_by_member.csv':
                'version4id,ccode,state_name,all_st_day,all_st_month,all_st_year,all_end_day,all_end_month,all_end_year,ss_type,mem_st_day,mem_st_month,mem_st_year,mem_end_day,mem_end_month,mem_end_year,left_censor,right_censor,defense,neutrality,nonaggression,entente,version\n'
                '7,300,"Austria-Hungary",-9,6,1879,,,,"Type I: Defense Pact",-9,6,1879,3,11,1918,0,0,1,0,0,0,4.1\n'})
            mid = zipped(tmp / 'MID-5-Data-and-Supporting-Materials.zip', {
                'MIDA 5.0.csv': 'dispnum,stday,stmon,styear,endday,endmon,endyear,outcome,settle,fatality,fatalpre,maxdur,mindur,hiact,hostlev,recip,numa,numb,ongo2014,version\r\n2,-9,7,1902,24,1,1903,6,1,0,0,208,178,7,3,0,1,1,0,5\r\n',
                'MIDB 5.0.csv': 'dispnum,stabb,ccode,stday,stmon,styear,endday,endmon,endyear,sidea,revstate,revtype1,revtype2,fatality,fatalpre,hiact,hostlev,orig,version\r\n2,"USA",2,-9,7,1902,24,1,1903,1,1,1,-9,0,0,7,3,1,5\r\n2,"AUH",300,-9,7,1902,24,1,1903,0,0,0,-9,0,0,0,1,1,5\r\n'})
            war = tmp / 'Inter-StateWarData_v4.0.csv'
            war.write_bytes(b'WarNum,WarName,WarType,ccode,StateName,Side,StartMonth1,StartDay1,StartYear1,EndMonth1,EndDay1,EndYear1,StartMonth2,StartDay2,StartYear2,EndMonth2,EndDay2,EndYear2,TransFrom,WhereFought,Initiator,Outcome,TransTo,BatDeath,Version\r'
                            b'1,Test War,1,2,USA,1,4,7,1823,11,13,1823,-8,-8,-8,-8,-8,-8,503,2,1,1,-8,-9,4\r'
                            b'1,Test War,1,300,Austria-Hungary,2,4,9,1823,11,13,1823,-8,-8,-8,-8,-8,-8,503,2,2,2,-8,400,4\r')
            atop = zipped(tmp / 'atop_5.1_csv.zip', {
                'ATOP 5.1 (.csv)/atop5_1a.csv': 'atopid,cowid,begyr,begmo,begday,endyr,endmo,endday,ineffect,bilat,defense,offense,neutral,nonagg,consul,active,milaid\n1005,,1815,1,3,1815,2,8,0,0,1,0,0,0,1,1,0\n',
                'ATOP 5.1 (.csv)/atop5_1m.csv': 'atopid,member,yrent,moent,dayent,yrexit,moexit,dayexit,phase\n1005,2,1815,1,3,1815,2,8,1\n'})
            store = Store(tmp / 'data')
            shards = [{'path': p, 'name': p.name} for p in (atop, war, mid, alliances, contiguity, nmc, states)]
            store.import_shards('conflict_reference', shards, {'publisher': 'fixture'}, complete=True)
            records = list(store.records(Runner(Catalog(ROOT / 'data'), store, ROOT).run('conflict_reference')))
            entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
            self.assertEqual(entities['iso3:USA']['label'], 'United States of America')
            self.assertEqual(entities['cow:state:300']['label'], 'Austria-Hungary')
            cinc = next(r for r in records if r.get('metric') == 'composite_index_national_capability')
            self.assertEqual((cinc['subject'], cinc['value'], cinc['valid_from']), ('iso3:USA', 0.039697491, '1816-01-01'))
            self.assertIn('member:NMCv7/NMC-v7-abridged.zip/member:NMC-70-abridged.csv/line:2', cinc['evidence'][0]['locator'])
            memberships = [r for r in records if r.get('predicate') == 'member_of']
            cow = next(r for r in memberships if r['object'] == 'cow:alliance:7')
            self.assertEqual((cow['valid_from'], cow['valid_to'], cow['attributes']['date_precision']), ('1879-06-01', '1918-11-04', 'month'))
            self.assertTrue(any(r['object'] == 'atop:alliance:1005' for r in memberships))
            self.assertEqual(entities['atop:alliance:1005']['entity_type'], 'military_alliance')
            dispute = next(r for r in records if r.get('event_type') == 'cow_mid:dispute')
            self.assertEqual((dispute['occurred_at'], dispute['participants']), ('1902-07-01', ['cow:state:300', 'iso3:USA']))
            war_event = next(r for r in records if r.get('event_type') == 'cow_war:interstate')
            self.assertEqual((war_event['occurred_at'], war_event['attributes']['battle_deaths_total']), ('1823-04-07', 400))
            contiguous = next(r for r in records if r.get('predicate') == 'contiguous_with')
            self.assertTrue(contiguous['attributes']['right_censored'])
            self.assertNotIn('valid_to', contiguous)


if __name__ == '__main__':
    unittest.main()
