import hashlib
import unittest
from copy import deepcopy
from worldmodel.contract_extraction import extract_candidates,review_obligation

class ContractTests(unittest.TestCase):
    def document(self,text=None):
        text=text or '# Agreement\nBorrower: Fictional Mill\nLender: Fictional Bank\nPrincipal: 1,000\nCurrency: USD\nAnnual rate: 5%\nRate type: fixed\nMaturity: 2028-01-01\nCollateral: unsecured\nSeniority: pari_passu\nSchedule: bullet_act365\n'
        return {'text':text,'sha256':hashlib.sha256(text.encode()).hexdigest(),'accession':'fictional-001','url':None,'observed_at':'2026-01-01'}
    def review(self,claims):
        return {'id':'fiction:loan','candidate_ids':{x['field']:[x['id']] for x in claims},
                'terms':{'borrower':'fiction:mill','lender':'fiction:bank','principal':1000,'currency':'USD','annual_rate':.05,'rate_type':'fixed','maturity':'2028-01-01'},
                'parties':{'borrower':{'entity_id':'fiction:mill','label':'Fictional Mill','resolution_evidence':['review:issuer-a']},'lender':{'entity_id':'fiction:bank','label':'Fictional Bank','resolution_evidence':['review:issuer-b']}},
                'schedule':'bullet_act365','collateral':'unsecured','seniority':'pari_passu','completeness':'complete','disposition':'approved',
                'reviewer':'fictional analyst','reviewed_at':'2026-01-02','valid_from':'2026-01-01','valid_to':'2028-01-01','supersedes':[]}
    def test_exact_locators_and_explicit_review(self):
        doc=self.document();claims=extract_candidates(doc)
        self.assertEqual(len(claims),10)
        for c in claims:self.assertEqual(doc['text'][c['locator']['start']:c['locator']['end']],c['quote'])
        answer=review_obligation(claims,self.review(claims));self.assertTrue(answer['simulation_eligible']);self.assertEqual(answer['terms']['principal'],1000)
        bad=self.review(claims);bad['terms']['principal']=9000
        with self.assertRaises(ValueError):review_obligation(claims,bad)
    def test_missing_amount_uncertain_party_and_unknown_schedule(self):
        claims=extract_candidates(self.document('Borrower: Unknown\nCurrency: EUR\n'))
        incomplete=self.review(claims);incomplete.update(completeness='partial',disposition='incomplete',terms=None,parties={},schedule=None,collateral=None,seniority=None)
        self.assertFalse(review_obligation(claims,incomplete)['simulation_eligible'])
        complete=extract_candidates(self.document());bad=self.review(complete);bad['parties']['lender']['resolution_evidence']=[]
        with self.assertRaises(ValueError):review_obligation(complete,bad)
    def test_tables_amendments_multiple_currencies_and_undated_terms(self):
        doc=self.document('# Amendment\nPrincipal | 2,000\nCurrency | EUR\nCurrency | USD\nSupersedes: original principal\n')
        claims=extract_candidates(doc);self.assertEqual([c['value'] for c in claims if c['field']=='currency'],['EUR','USD'])
        self.assertEqual(claims[0]['locator']['table_row'],2);self.assertIsNone(claims[0]['effective_date'])
        self.assertEqual(claims[-1]['field'],'supersedes')
        doc['sha256']='0'*64
        with self.assertRaises(ValueError):extract_candidates(doc)
    def test_bounds_and_tampered_claim(self):
        with self.assertRaises(ValueError):extract_candidates(self.document(),max_characters=5)
        claims=extract_candidates(self.document());claims[0]['value']='Other'
        with self.assertRaises(ValueError):review_obligation(claims,self.review(claims))
