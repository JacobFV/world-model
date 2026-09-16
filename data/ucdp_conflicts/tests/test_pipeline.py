"""Offline acceptance test: UCDP bulk shards (GED, ACD, BRD, actors) -> events, entities and observations."""
import csv
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
GED = ('id,relid,year,active_year,code_status,type_of_violence,conflict_dset_id,conflict_new_id,conflict_name,dyad_dset_id,dyad_new_id,'
       'dyad_name,side_a_dset_id,side_a_new_id,side_a,side_b_dset_id,side_b_new_id,side_b,number_of_sources,source_article,source_office,'
       'source_date,source_headline,source_original,where_prec,where_coordinates,where_description,adm_1,adm_2,latitude,longitude,geom_wkt,'
       'priogrid_gid,country,country_id,region,event_clarity,date_prec,date_start,date_end,deaths_a,deaths_b,deaths_civilians,deaths_unknown,'
       'best,high,low,gwnoa,gwnob').split(',')


def csv_text(header, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(header)
    writer.writerows([[row.get(h, '') for h in header] for row in rows])
    return buffer.getvalue()


class UcdpFullPipelineTest(unittest.TestCase):
    def test_bulk_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            ged_row = dict(id='9', relid='FIC-1', year='2025', type_of_violence='1', conflict_new_id='200', conflict_name='Fictionland: Government',
                           dyad_new_id='300', dyad_name='Government - Rebels', side_a_new_id='10', side_a='Government of Fictionland',
                           side_b_new_id='11', side_b='Rebels', latitude='1.5', longitude='2.5', country='Germany', country_id='260',
                           date_prec='1', date_start='2025-03-01 00:00:00.000', date_end='2025-03-02 00:00:00.000',
                           deaths_a='1', deaths_b='2', deaths_civilians='0', deaths_unknown='0', best='3', low='2', high='5', where_prec='1')
            ged = tmp / 'ged261-csv.zip'
            with zipfile.ZipFile(ged, 'w') as z:
                z.writestr('GEDEvent_v26_1.csv', csv_text(GED, [ged_row, {**ged_row, 'id': '10', 'best': '4', 'low': '4', 'high': '4', 'type_of_violence': '3'}]))
            candidate = tmp / 'GEDEvent_v26_0_7.csv'
            candidate.write_text(csv_text(GED, [{**ged_row, 'id': '11', 'code_status': 'Check geography', 'date_start': '2026-07-01 00:00:00.000',
                                                  'date_end': '2026-07-01 00:00:00.000', 'low': '9', 'best': '1', 'high': '2'}]))
            acd = tmp / 'ucdp-prio-acd-261-csv.zip'
            with zipfile.ZipFile(acd, 'w') as z:
                z.writestr('UcdpPrioConflict_v26_1.csv', csv_text(
                    ['conflict_id', 'location', 'side_a', 'side_a_id', 'side_b', 'side_b_id', 'incompatibility', 'territory_name', 'year',
                     'intensity_level', 'type_of_conflict', 'start_date', 'gwno_loc'],
                    [{'conflict_id': '200', 'location': 'Fictionland', 'side_a': 'Government', 'side_a_id': '10', 'side_b': 'Rebels',
                      'side_b_id': '11, 12', 'incompatibility': '2', 'year': '2025', 'intensity_level': '2', 'type_of_conflict': '3',
                      'start_date': '1990-01-01', 'gwno_loc': '260'}]))
            brd = tmp / 'ucdp-brd-dyadic-261-csv.zip'
            with zipfile.ZipFile(brd, 'w') as z:
                z.writestr('BattleDeaths_v26_1.csv', csv_text(['conflict_id', 'dyad_id', 'side_a', 'side_b', 'year', 'bd_best', 'bd_low', 'bd_high', 'type_of_conflict'],
                                                            [{'conflict_id': '200', 'dyad_id': '300', 'year': '2025', 'bd_best': '1200', 'bd_low': '1000', 'bd_high': '', 'type_of_conflict': '3'}]))
            actors = tmp / 'ucdp-actor-261-csv.zip'
            with zipfile.ZipFile(actors, 'w') as z:
                z.writestr('Actor_v26_1.csv', csv_text(['ActorId', 'NameData', 'Org', 'Location', 'GWNOLoc', 'Region'],
                                                       [{'ActorId': '11', 'NameData': 'Rebeldes del Sur', 'Org': '1', 'Location': 'Fictionland', 'GWNOLoc': '260'}]).encode('latin-1'))
            store = Store(tmp / 'data')
            shards = [{'path': p, 'name': p.name} for p in (ged, candidate, acd, brd, actors)]
            store.import_shards('ucdp_conflicts', shards, {'publisher': 'fixture'}, complete=True)
            records = list(store.records(Runner(Catalog(ROOT / 'data'), store, ROOT).run('ucdp_conflicts')))
            events = {r['attributes']['ged_id']: r for r in records if r['kind'] == 'event'}
            self.assertEqual(set(events), {'9', '10', '11'})
            self.assertEqual(events['9']['event_type'], 'ucdp_ged:state_based')
            self.assertEqual(events['9']['participants'], ['ucdp:conflict:200', 'ucdp:dyad:300', 'ucdp:actor:10', 'ucdp:actor:11', 'iso3:DEU'])
            self.assertEqual(events['9']['valid_to'], '2025-03-03')
            self.assertFalse(events['11']['attributes']['bounds_consistent'])
            self.assertTrue(events['11']['attributes']['release'].startswith('candidate'))
            labels = {r['entity_id']: r['label'] for r in records if r['kind'] == 'entity'}
            self.assertEqual(labels['ucdp:actor:11'], 'Rebeldes del Sur')
            obs = {(r['subject'], r['metric'], r['dimensions'].get('release'), r['dimensions'].get('type_of_violence')): r
                   for r in records if r['kind'] == 'observation'}
            self.assertEqual(obs[('iso3:DEU', 'organized_violence_deaths', '26.1', 'state_based')]['value'], 3)
            self.assertEqual(obs[('ucdp:conflict:200', 'conflict_intensity_level', None, None)]['value'], 2)
            high = obs[('ucdp:dyad:300', 'battle_related_deaths_high', None, None)]
            self.assertIsNone(high['value'])
            self.assertEqual(high['missing_reason'], 'not_reported')
            predicates = {(r['subject'], r['predicate'], r['object']) for r in records if r['kind'] == 'assertion'}
            self.assertIn(('ucdp:actor:12', 'participates_in_conflict', 'ucdp:conflict:200'), predicates)
            self.assertIn(('ucdp:dyad:300', 'dyad_of_conflict', 'ucdp:conflict:200'), predicates)


if __name__ == '__main__':
    unittest.main()
