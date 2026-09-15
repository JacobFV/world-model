import json
from pathlib import Path
import tempfile
import unittest
from worldmodel.pipeline import Context
from worldmodel.store import Store
from worldmodel.strategic_sources import normalize, SOURCE_IDS, schema

ROOT=Path(__file__).resolve().parents[1]
class StrategicSourceTests(unittest.TestCase):
    def records(self, dataset, rows):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'input.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            store=Store(Path(td)/'data')
            ref=store.import_file(dataset,path,source={'publisher':'test'},update_latest=False)
            definition=json.loads((ROOT/'data'/dataset/'dataset.json').read_text())
            return list(normalize(Context(store,definition,{},[],[ref])))
    def test_market_missing_units_and_dates(self):
        rs=self.records('fred_policy_rate',[{'observation_date':'2025-01-02','DFF':'4.33','DGS2':'.','DGS10':'4.45','T10YIE':'2.35','DCOILWTICO':'73.13'}])
        obs={r['metric']:r for r in rs if r['kind']=='observation'}
        self.assertEqual(obs['policy_rate']['unit'],'percent')
        self.assertEqual(obs['oil_price']['unit'],'USD/barrel')
        self.assertIsNone(obs['treasury_yield_2y']['value'])
        self.assertEqual(obs['policy_rate']['valid_to'],'2025-01-03')
        self.assertTrue(all(r['evidence'][0]['locator']=='line:1' for r in rs))
    def test_bank_units_and_no_invented_credit_edges(self):
        rs=self.records('fdic_bank_financials',[{'data':{'CERT':628,'NAME':'Example bank','REPDTE':'20241231','ASSET':100,'DEP':80,'LNLSNET':60,'EQ':10,'NETINC':4}}])
        obs={r['metric']:r for r in rs if r['kind']=='observation'}
        self.assertEqual(obs['total_assets']['value'],100000)
        self.assertEqual(obs['bank_net_income']['valid_from'],'2024-01-01')
        self.assertFalse(any(r.get('predicate')=='lends_to' for r in rs))
    def test_osm_geometry_has_closed_references(self):
        rs=self.records('osm_topology',[{'type':'way','id':123,'nodes':[1,2], 'geometry':[{'lat':37.,'lon':-122.},{'lat':37.01,'lon':-122.01}], 'tags':{'highway':'residential','oneway':'yes'}}])
        entities={r['entity_id'] for r in rs if r['kind']=='entity'}
        edges=[r for r in rs if r.get('predicate')=='road_connects_to']
        self.assertEqual([(r['subject'],r['object']) for r in edges],[('osm:node:1','osm:node:2')])
        self.assertTrue(all(r['subject'] in entities and r['object'] in entities for r in edges))
    def test_osm_rejects_incomplete_geometry(self):
        with self.assertRaisesRegex(ValueError,'geometry'):
            self.records('osm_topology',[{'type':'way','id':1,'nodes':[1,2],'geometry':[]}])
    def test_sec_retains_accession_and_filing_time(self):
        rs=self.records('sec_company_assets',[{'end':'2024-09-28','val':364980000000,'accn':'0000320193-24-000123','filed':'2024-11-01','form':'10-K'}])
        obs=next(r for r in rs if r['kind']=='observation')
        self.assertEqual(obs['attributes']['filing_date'],'2024-11-01')
        self.assertEqual(obs['subject'],'sec:cik:0000320193')
    def test_closed_osm_way_has_unique_records(self):
        rs=self.records('osm_topology',[{'type':'way','id':123,'nodes':[1,2,1], 'geometry':[{'lat':37.,'lon':-122.},{'lat':37.01,'lon':-122.01},{'lat':37.,'lon':-122.}], 'tags':{'highway':'residential','junction':'roundabout'}}])
        self.assertEqual(len(rs),len({r['id'] for r in rs}))
    def test_airport_identity_and_debt_measurements(self):
        rs=self.records('airport_nodes',[{'id':'12','ident':'KSFO','name':'San Francisco International','type':'large_airport','latitude_deg':'37.62','longitude_deg':'-122.38','iata_code':'SFO'}])
        self.assertEqual(rs[0]['entity_id'],'ourairports:12')
        self.assertFalse(any(r.get('entity_type')=='flight' for r in rs))
        rs=self.records('treasury_debt',[{'record_date':'2025-01-02','tot_pub_debt_out_amt':'100','debt_held_public_amt':'80','intragov_hold_amt':'20'}])
        values={r['metric']:r['value'] for r in rs if r['kind']=='observation'}
        self.assertEqual(values['public_debt'],values['debt_held_by_public']+values['intragovernmental_debt'])
    def test_candidate_enriches_existing_identifier_without_travel_claims(self):
        rs=self.records('fec_candidates',[{'candidate_id':'H6WA05023','name':'EXAMPLE, PUBLIC CANDIDATE','office':'H','state':'WA','district':'05','party':'DEM','election_years':[1996,1998]}])
        self.assertEqual(len(rs),1)
        self.assertEqual(rs[0]['entity_id'],'fec:candidate:H6WA05023')
        self.assertEqual(rs[0]['entity_type'],'person')
        self.assertEqual(rs[0]['label'],'EXAMPLE, PUBLIC CANDIDATE')
        self.assertEqual(rs[0]['attributes']['candidate_office'],'H')
        self.assertNotIn('valid_from',rs[0])
    def test_all_limits_and_schema(self):
        from worldmodel.sampling import limits
        for id in SOURCE_IDS:
            config=json.loads((ROOT/'data'/id/'dataset.json').read_text())['sampling']
            limits(config)
            self.assertEqual(config['max_rows'],100)
            self.assertEqual(config['max_sample_bytes'],1048576)
        self.assertIn('bank',schema()['entity_types'])
