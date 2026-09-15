import json
from pathlib import Path
import tempfile
import unittest
from worldmodel.pipeline import Context
from worldmodel.store import Store
from worldmodel import market_sources

ROOT=Path(__file__).resolve().parents[1]
class MarketSourcesTests(unittest.TestCase):
    def records(self, dataset, rows):
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'input.jsonl'
            path.write_text(''.join(json.dumps(r)+'\n' for r in rows))
            store=Store(Path(td)/'data')
            ref=store.import_file(dataset,path,source={'publisher':'test'},update_latest=False)
            return list(market_sources.normalize(Context(store,{'id':dataset},{},[],[ref])))

    def test_mic_status_is_published_code_not_operational_claim(self):
        row={'MIC':'TXSE','OPERATING MIC':'TXSE','MARKET NAME-INSTITUTION DESCRIPTION':'TEXAS STOCK EXCHANGE','STATUS':'ACTIVE','CREATION DATE':'20251124','LAST UPDATE DATE':'20251124','LEI':'5493001KJTIIGC8Y1R12','LEGAL ENTITY NAME':'Example','ISO COUNTRY CODE (ISO 3166)':'US'}
        rs=self.records('iso_mic_venues',[row])
        venue=next(r for r in rs if r.get('entity_type')=='trading_venue')
        self.assertEqual(venue['entity_id'],'mic:TXSE')
        status=next(r for r in rs if r.get('predicate')=='published_mic_status')
        self.assertEqual(status['value'],'ACTIVE')
        self.assertNotIn('valid_from',status)
        self.assertEqual(status['attributes']['source_last_update_date'],'2025-11-24')
        self.assertFalse(any(r.get('predicate') in ('operational','owns','same_as') for r in rs))
        self.assertIn('lei:5493001KJTIIGC8Y1R12',{r.get('object') for r in rs})
        self.assertTrue(all(r['evidence'][0]['locator']=='line:1' for r in rs))

    def test_listings_keep_instrument_issuer_and_ticker_distinct(self):
        row={'Symbol':'AAPL','Security Name':'Apple Inc. - Common Stock','Market Category':'Q','Test Issue':'N','ETF':'N'}
        rs=self.records('nasdaq_listings',[row])
        entities={r['entity_type']:r for r in rs if r['kind']=='entity'}
        self.assertIn('ticker_listing',entities)
        self.assertIn('security',entities)
        self.assertNotIn('business',entities)
        self.assertFalse(any('sec:cik:' in str(r) for r in rs))
        self.assertTrue(entities['security']['attributes']['unresolved_identity'])
        assignment=next(r for r in rs if r.get('predicate')=='identifier_assignment' and r['value']['namespace']=='ticker')
        self.assertEqual(assignment['subject'],entities['ticker_listing']['entity_id'])
        self.assertEqual(assignment['value'],{'namespace':'ticker','value':'AAPL','scope':'XNAS'})
        self.assertNotIn('valid_from',assignment)
        self.assertFalse(self.records('nasdaq_listings',[{'Symbol':'File Creation Time: 09152026'}]))

    def test_test_issues_are_not_normalized_as_real_listings(self):
        self.assertEqual(self.records('nasdaq_listings',[{'Symbol':'ZVZZT','Test Issue':'Y','Security Name':'Test issue'}]),[])

    def test_gleif_relationship_uses_lei_and_relationship_period(self):
        row={'id':'relationship-1','attributes':{'validFrom':'2026-01-06T00:00:00Z','relationship':{'startNode':{'id':'5493001KJTIIGC8Y1R12','type':'LEI'},'endNode':{'id':'549300B56MD0ZC402L06','type':'LEI'},'type':'IS_DIRECTLY_CONSOLIDATED_BY','status':'ACTIVE','periods':[{'type':'RELATIONSHIP_PERIOD','startDate':'2007-06-05T00:00:00Z'},{'type':'ACCOUNTING_PERIOD','startDate':'2016-01-01T00:00:00Z'}]},'registration':{'status':'PUBLISHED'}}}
        rs=self.records('gleif_parent_relationships',[row])
        edge=next(r for r in rs if r.get('predicate')=='directly_consolidated_by')
        self.assertEqual(edge['subject'],'lei:5493001KJTIIGC8Y1R12')
        self.assertEqual(edge['object'],'lei:549300B56MD0ZC402L06')
        self.assertEqual(edge['valid_from'],'2007-06-05T00:00:00Z')
        self.assertNotIn('ownership_percentage',str(rs))
        self.assertFalse(any(r.get('predicate')=='owns' for r in rs))
        row['attributes']['relationship']['startNode']['type']='OTHER'
        with self.assertRaisesRegex(ValueError,'LEI'):self.records('gleif_parent_relationships',[row])

    def test_sec_reuses_exact_cik_without_inventing_security_identity(self):
        rs=self.records('sec_issuer_reference',[{'cik':'0000320193','name':'Apple Inc.','tickers':['AAPL'],'exchanges':['Nasdaq'],'entityType':'operating','formerNames':[]}])
        entity=next(r for r in rs if r['kind']=='entity')
        self.assertEqual(entity['entity_id'],'sec:cik:0000320193')
        self.assertEqual(entity['entity_type'],'business')
        self.assertEqual(entity['attributes']['published_tickers'],['AAPL'])
        assignment=next(r for r in rs if r.get('predicate')=='identifier_assignment')
        self.assertEqual(assignment['value'],{'namespace':'sec_cik','value':'320193'})
        self.assertFalse(any(r.get('entity_type')=='security' for r in rs))

    def test_all_configs_are_bounded_and_blockers_explicit(self):
        from worldmodel.sampling import limits
        for dataset in market_sources.SOURCE_IDS:
            definition=json.loads((ROOT/'data'/dataset/'dataset.json').read_text())
            config=definition['sampling'];limits(config)
            self.assertLessEqual(config['max_rows'],100)
            self.assertLessEqual(config['max_sample_bytes'],1048576)
            self.assertFalse(config['representative'])
            if config['strategy']=='blocked':self.assertTrue(config['reason'])
        self.assertIn('trading_venue',market_sources.schema()['entity_types'])
