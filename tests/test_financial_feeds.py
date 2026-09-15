import unittest
from copy import deepcopy
from worldmodel.financial_feeds import validate_feed_row

class FeedTests(unittest.TestCase):
    def row(self):
        return {'source_record_id':'fiction:quote-1','source_published_at':'2026-01-02T21:01:00Z','observed_at':'2026-01-02T21:02:00Z',
            'identity':{'issuer_id':'fiction:issuer','security_id':'fiction:security','listing_id':'fiction:listing',
              'issuer_identifier':{'namespace':'sec_cik','value':'123'},'instrument_identifier':{'namespace':'isin','value':'US0000000001'},
              'venue_mic':'XNYS','ticker':'FICTION','currency':'USD','valid_from':'2026-01-01','valid_to':'2027-01-01',
              'known_at':'2026-01-01','source_snapshot':'fiction:snapshot-1'},
            'quote_at':'2026-01-02T21:00:00Z','valid_to':'2026-01-02T21:01:00Z','price':100.0,'currency':'USD','price_type':'close',
            'session':{'name':'regular','calendar':'fiction-calendar','calendar_version':'1','timezone':'America/New_York',
                       'open_at':'2026-01-02T14:30:00Z','close_at':'2026-01-02T21:00:00Z'},
            'adjustment':{'policy':'unadjusted','as_of':'2026-01-02T21:00:00Z','corporate_action_ids':[],'method':'none','version':'1'}}
    def test_exact_canonical_price_contract(self):
        row=self.row();self.assertEqual(validate_feed_row('prices',row),row)
        for mutate in (lambda r:r['identity'].update(currency=None),lambda r:r['identity']['instrument_identifier'].update(namespace=[]),lambda r:r.update(extra='hidden'),lambda r:r.pop('session'),lambda r:r['identity']['instrument_identifier'].update(namespace='ticker'),
                       lambda r:r['identity'].update(valid_to=None),lambda r:r['session'].update(close_at='2026-01-02T20:00:00Z'),
                       lambda r:r.update(currency='EUR'),lambda r:r['adjustment'].update(as_of='2027-01-01')):
            bad=self.row();mutate(bad)
            with self.assertRaises(ValueError):validate_feed_row('prices',bad)
    def action(self):
        row=self.row();return {k:row[k] for k in ('source_record_id','source_published_at','observed_at','identity')}|{
            'action_type':'split','effective_at':'2026-01-02T21:00:00Z','announced_at':'2026-01-01','status':'confirmed','terms':{'numerator':2,'denominator':1}}
    def test_corporate_actions_preserve_explicit_terms(self):
        row=self.action();self.assertEqual(validate_feed_row('corporate_actions',row)['terms']['numerator'],2)
        row['terms']['denominator']=0
        with self.assertRaises(ValueError):validate_feed_row('corporate_actions',row)
        row=self.action();row.update(action_type='merger',terms={'successor_issuer_id':'fiction:successor','successor_security_id':'fiction:newshare','exchange_ratio':.5})
        self.assertEqual(validate_feed_row('corporate_actions',row)['terms']['successor_issuer_id'],'fiction:successor')
    def test_no_future_identity_or_false_precision(self):
        row=self.row();row['identity']['known_at']='2027-01-01'
        with self.assertRaises(ValueError):validate_feed_row('prices',row)
        row=self.row();row['price']=float('nan')
        with self.assertRaises(ValueError):validate_feed_row('prices',row)
