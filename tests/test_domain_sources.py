import unittest
import tests.test_finance_workbooks as fixtures
from worldmodel.ontology import validate_typed_graph

class DomainSourcesTests(unittest.TestCase):
    records=fixtures.FinanceWorkbookTests.records
    def test_legal_proposal_never_infers_effectiveness(self):
        rows=self.records('federal_register_documents',{'title':'Fictional proposed rule','type':'Proposed Rule','document_number':'F-1','publication_date':'2026-01-01','html_url':'https://example.org/doc','agencies':[{'id':1,'name':'Fictional agency'}]})
        validate_typed_graph(rows)
        status=next(r['value'] for r in rows if r.get('predicate')=='document_status')
        self.assertEqual(status['effective_status'],'unknown')
    def test_research_preserves_identifiers_and_unknown_affiliation_dates(self):
        rows=self.records('crossref_research',{'DOI':'10.0000/fictional','title':['Fictional paper'],'author':[{'given':'A','family':'Example','affiliation':[{'name':'Fictional University'}]}],'reference':[{'DOI':'10.0000/other'}]})
        validate_typed_graph(rows)
        self.assertEqual(next(r for r in rows if r.get('predicate')=='research_affiliation')['attributes']['tenure'],'unknown; publication metadata only')
    def test_posts_are_publisher_claims(self):
        rows=self.records('nasa_publications',{'link':'https://example.org/post','title':'Fictional post','published':'Mon, 14 Sep 2026 10:00:00 GMT'})
        validate_typed_graph(rows)
        self.assertIn('not verified',rows[-1]['value']['factual_status'])
    def test_conflict_bounds_and_closed_participant_graph(self):
        source={'id':1,'conflict_new_id':2,'side_a_new_id':3,'side_b_new_id':4,'side_a':'FictionalA','side_b':'FictionalB','country_id':5,'country':'FictionalCountry','low':1,'best':2,'high':3,'date_start':'2026-01-01','date_end':'2026-01-02'}
        rows=self.records('ucdp_conflicts',source);validate_typed_graph(rows)
        self.assertEqual(rows[-1]['attributes']['deaths'],{'low':1,'best':2,'high':3})
        source['low']=5
        with self.assertRaises(ValueError):self.records('ucdp_conflicts',source)
    def test_usda_suppression_is_unknown_and_period_is_explicit(self):
        source={'commodity_desc':'CORN','statisticcat_desc':'YIELD','unit_desc':'BU / ACRE','agg_level_desc':'STATE','freq_desc':'ANNUAL','year':'2023','state_fips_code':'01','Value':'(D)'}
        rows=self.records('usda_agriculture',source);validate_typed_graph(rows)
        self.assertIsNone(rows[-1]['value']);self.assertEqual(rows[-1]['valid_to'],'2024-01-01')
        source['unit_desc']='TONS'
        with self.assertRaises(ValueError):self.records('usda_agriculture',source)
