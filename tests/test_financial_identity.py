import unittest
from worldmodel.financial_identity import FinancialIdentityIndex

class FinancialIdentityTests(unittest.TestCase):
    def index(self,extra=(),links=()):
        entities=[{'id':f'fiction:{x}','kind':kind,'label':'Same name'} for x,kind in [('a','issuer'),('b','issuer'),('s','security'),('t','security'),('l','listing'),('m','listing')]]
        assignments=[{'entity_id':'fiction:l','namespace':'ticker','value':'REUSE','scope':'XNYS','currency':'USD',
            'valid_from':'2020-01-01','valid_to':'2022-01-01','observed_at':'2020-01-01','evidence':['filing:1']},
            {'entity_id':'fiction:m','namespace':'ticker','value':'REUSE','scope':'XNYS','currency':'USD',
            'valid_from':'2022-01-01','valid_to':'2024-01-01','observed_at':'2022-01-01','evidence':['filing:2']}]
        return FinancialIdentityIndex(entities,assignments+list(extra),links)
    def test_ticker_reuse_and_no_name_merge(self):
        index=self.index()
        self.assertEqual(index.resolve('ticker','REUSE',at='2021-01-01',known_at='2023-01-01',scope='XNYS')['selected'],'fiction:l')
        self.assertEqual(index.resolve('ticker','REUSE',at='2023-01-01',known_at='2023-01-01',scope='XNYS')['selected'],'fiction:m')
        self.assertEqual(len(index.search('Same name',kind='issuer')),2)
    def test_contradiction_unknown_and_review_policy_preserve_candidates(self):
        row={'entity_id':'fiction:l','namespace':'ticker','value':'REUSE','scope':'XNYS','currency':'USD',
            'valid_from':None,'valid_to':None,'observed_at':'2022-01-01','evidence':['filing:3']}
        index=self.index([row]);result=index.resolve('ticker','REUSE',at='2023-01-01',known_at='2023-01-01',scope='XNYS')
        self.assertEqual(result['status'],'ambiguous');self.assertIsNone(result['selected']);self.assertEqual(len(result['candidates']),2)
        result=index.resolve('ticker','REUSE',at='2023-01-01',known_at='2023-01-01',scope='XNYS',decision={'selected':'fiction:m','reviewer':'analyst','reason':'dated listing notice'})
        self.assertEqual(result['selected'],'fiction:m');self.assertEqual(len(result['candidates']),2)
    def test_snapshot_scope_and_entity_kind(self):
        row={'entity_id':'fiction:s','namespace':'source_local','value':'17','scope':'snapshot-one',
             'valid_from':'2020-01-01','valid_to':'2024-01-01','observed_at':'2020-01-01','evidence':['filing:4']}
        index=self.index([row]);self.assertEqual(index.resolve('source_local','17',at='2021-01-01',known_at='2021-01-01',scope='snapshot-two')['status'],'not_found')
        row['namespace']='lei'
        with self.assertRaises(ValueError):self.index([row])
        link={'kind':'equivalent','subject':'fiction:a','object':'fiction:s','valid_from':'2020-01-01','valid_to':'2024-01-01','observed_at':'2020-01-01','evidence':['filing:5']}
        with self.assertRaises(ValueError):self.index(links=[link])
    def test_successor_preserves_distinct_issuers(self):
        link={'kind':'successor','subject':'fiction:a','object':'fiction:b','valid_from':'2022-01-01','valid_to':'2024-01-01','observed_at':'2022-01-01','evidence':['merger:1']}
        index=self.index(links=[link]);self.assertEqual(index.relationships('fiction:a',at='2023-01-01',known_at='2023-01-01'),[link]);self.assertEqual(len(index.search('Same name',kind='issuer')),2)
    def test_conflicting_issuer_identifier_assignments_are_reported_not_merged(self):
        assignments=[{'entity_id':'fiction:a','namespace':'sec_cik','value':value,'scope':None,
            'valid_from':'2020-01-01','valid_to':'2024-01-01','observed_at':'2020-01-01','evidence':['filing:'+value]} for value in ('1','2')]
        result=self.index(assignments).resolve('sec_cik','1',at='2021-01-01',known_at='2021-01-01')
        self.assertEqual(result['status'],'conflicting_assignments');self.assertIsNone(result['selected']);self.assertEqual(len(result['conflicts']),1)
