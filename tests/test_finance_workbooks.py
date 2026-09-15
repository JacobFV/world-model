import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from worldmodel.ontology import validate_typed_graph
from worldmodel.pipeline import Context
from worldmodel.store import Store

ROOT=Path(__file__).resolve().parents[1]
class FinanceWorkbookTests(unittest.TestCase):
    def records(self,name,row):
        with tempfile.TemporaryDirectory() as tmp:
            source=Path(tmp)/'sample.jsonl';source.write_text(json.dumps(row)+'\n')
            store=Store(Path(tmp)/'data');ref=store.import_file(name,source,{'publisher':'fictional'})
            spec=importlib.util.spec_from_file_location(name,ROOT/'data'/name/'pipeline.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
            return list(module.run(Context(store,{'id':name},{},[],[ref])))
    def test_holdings_are_dated_positions_not_issuer_ownership(self):
        row={'Name':'Fictional Co','Ticker':'ZZZ','Identifier':'UNCLASSIFIED1','SEDOL':'ABC123','Weight':'12.5','Shares Held':'100','Local Currency':'USD','_context':{'fund':'Fictional DIA sample','ticker':'DIA','date':'As of 14-Sep-2026'}}
        records=self.records('ssga_dia_holdings',row);validate_typed_graph(records)
        self.assertFalse(any(r.get('predicate') in ('owns','index_member') for r in records))
        self.assertEqual(next(r['value'] for r in records if r.get('metric')=='portfolio_weight'),12.5)
        row['Identifier']=''
        with self.assertRaises(ValueError):self.records('ssga_dia_holdings',row)
    def test_nav_and_signed_premium_have_distinct_units(self):
        meta={'fund':'Fictional DIA','ticker':'DIA'}
        nav=self.records('ssga_dia_nav',{'Date':'14-Sep-2026','NAV':'500','Shares Outstanding':'100','Total Net Assets':'50000','_context':meta})
        premium=self.records('ssga_dia_premium',{'Date':'14-Sep-2026','Premium/Discount':'-0.02','_context':meta})
        validate_typed_graph(nav);validate_typed_graph(premium)
        self.assertEqual(next(r['unit'] for r in nav if r.get('metric')=='fund_nav'),'USD/share')
        self.assertEqual(premium[-1]['unit'],'percent');self.assertEqual(premium[-1]['value'],-.02)

    def test_published_cash_is_not_a_security_or_shares(self):
        row={'Name':'US DOLLAR','Ticker':'-','Identifier':'999USDZ92','Weight':'0.15','Shares Held':'1000','Local Currency':'USD','_context':{'fund':'Fictional DIA','ticker':'DIA','date':'As of 14-Sep-2026'}}
        records=self.records('ssga_dia_holdings',row);validate_typed_graph(records)
        self.assertFalse(any(r.get('entity_type')=='security' or r.get('metric')=='position_shares' for r in records))
        self.assertEqual(next(r['value'] for r in records if r.get('metric')=='position_cash'),1000)
