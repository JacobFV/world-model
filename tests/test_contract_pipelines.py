import json
from pathlib import Path
import tempfile
import unittest
from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store
from tests import test_contract_extraction
from tests import test_financial_feeds

PROJECT=Path(__file__).resolve().parents[1]

class ContractPipelineTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.store=Store(self.root/'data');self.runner=Runner(Catalog(PROJECT/'data'),self.store,PROJECT)
    def imported(self,dataset,rows):
        path=self.root/(dataset+'.jsonl');path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        return self.store.import_file(dataset,path,{'publisher':'fictional fixture','license_id':'MIT'})
    def test_adjacent_candidate_review_and_obligation_lineage(self):
        fixture=test_contract_extraction.ContractTests();raw=self.imported('contract_candidates',[fixture.document()])
        candidate=self.runner.run('contract_candidates',raw_refs={'contract_candidates':[raw]})
        claims=list(self.store.records(candidate));self.assertEqual(len(claims),10)
        review=fixture.review(claims);raw_review=self.imported('reviewed_obligations',[review])
        output=self.runner.run('reviewed_obligations',raw_refs={'reviewed_obligations':[raw_review]},input_refs={'contract_candidates':candidate})
        records=list(self.store.records(output));terms=[r for r in records if r.get('predicate')=='obligation_terms']
        self.assertEqual(len(terms),1);self.assertEqual(terms[0]['value']['principal'],1000)
        self.assertTrue(self.store.verify(output));self.assertTrue(any(e['input']==candidate for e in terms[0]['evidence']))
        alias=self.runner.run('market_obligations',input_refs={'reviewed_obligations':output});self.assertTrue(self.store.verify(alias))
    def test_market_sources_normalize_strict_imports(self):
        fixture=test_financial_feeds.FeedTests()
        for dataset,row,predicate in [('market_prices',fixture.row(),'security_price_quote'),('market_corporate_actions',fixture.action(),'corporate_action_terms')]:
            raw=self.imported(dataset,[row]);ref=self.runner.run(dataset,raw_refs={dataset:[raw]})
            records=list(self.store.records(ref));self.assertTrue(any(r.get('predicate')==predicate for r in records));self.assertTrue(self.store.verify(ref))

    def test_quote_does_not_apply_before_its_timestamp(self):
        fixture=test_financial_feeds.FeedTests();raw=self.imported('market_prices',[fixture.row()]);ref=self.runner.run('market_prices',raw_refs={'market_prices':[raw]})
        quote=next(r for r in self.store.records(ref) if r.get('predicate')=='security_price_quote')
        self.assertEqual(quote['valid_from'],fixture.row()['quote_at'])

    def test_amendment_requires_nonoverlapping_review_validity(self):
        fixture=test_contract_extraction.ContractTests();raw=self.imported('contract_candidates',[fixture.document()])
        candidate=self.runner.run('contract_candidates',raw_refs={'contract_candidates':[raw]})
        claims=list(self.store.records(candidate));first=fixture.review(claims);amended=fixture.review(claims)
        amended.update(id='fiction:loan-amended',supersedes=['fiction:loan'],valid_from='2027-01-01')
        reviews=self.imported('reviewed_obligations',[first,amended])
        with self.assertRaisesRegex(ValueError,'supersed'):
            self.runner.run('reviewed_obligations',raw_refs={'reviewed_obligations':[reviews]},input_refs={'contract_candidates':candidate})

    def test_materialized_review_and_quote_queries_retain_exact_evidence(self):
        from worldmodel.materialize import materialize
        fixture=test_financial_feeds.FeedTests();raw=self.imported('market_prices',[fixture.row()])
        ref=self.runner.run('market_prices',raw_refs={'market_prices':[raw]})
        request={'start':'2026-01-02T21:00:00Z','end':'2026-01-02T21:00:01Z','step_seconds':1,
            'known_at':'2026-01-02T21:02:00Z','budget':1,'seed':0,
            'targets':[{'entity':'fiction:security','variable':'security_price_quote'}],'bindings':[]}
        result=materialize(self.store,ref,request)
        self.assertEqual(result['snapshots'][0]['value']['price'],100)
        self.assertTrue(result['snapshots'][0]['evidence']);self.assertTrue(self.store.verify(result['artifact']))
