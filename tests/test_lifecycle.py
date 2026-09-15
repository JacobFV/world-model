import copy
import unittest
from worldmodel.lifecycle import materialize_lifecycle, actor_eligible, require_actor_eligible, graph_records

class LifecycleTests(unittest.TestCase):
    def config(self):
        return {'entities':[{'id':'person:a','entity_type':'person','label':'Fictional A'}], 'events':[
            {'id':'event:birth','type':'birth','entity':'person:a','size':1,'occurred_at':'2000-01-01','observed_at':'2000-01-02'},
            {'id':'event:death','type':'death','entity':'person:a','occurred_at':'2020-01-01','observed_at':'2020-02-01'}]}
    def test_valid_and_known_cutoffs_preserve_history(self):
        c=self.config();before=copy.deepcopy(c)
        late=materialize_lifecycle(c,'2020-01-15','2020-01-15')
        self.assertTrue(actor_eligible(late,'person:a'))
        after=materialize_lifecycle(c,'2020-01-15','2020-02-02')
        self.assertFalse(actor_eligible(after,'person:a'))
        self.assertEqual(after['entities']['person:a']['valid_to'],'2020-01-01')
        self.assertEqual(len(after['history']),2)
        self.assertEqual(c,before)
        with self.assertRaisesRegex(ValueError,'inactive'):require_actor_eligible(after,'person:a')
    def test_growth_after_death_and_unknown_actors_fail(self):
        c=self.config();c['events'].append({'id':'event:g','type':'growth','entity':'person:a','delta':1,'occurred_at':'2021-01-01','observed_at':'2021-01-02'})
        with self.assertRaisesRegex(ValueError,'inactive'):materialize_lifecycle(c,'2022-01-01','2022-01-01')
        c=self.config();c['events'][0]['entity']='person:unknown'
        with self.assertRaisesRegex(ValueError,'Unknown'):materialize_lifecycle(c,'2022-01-01','2022-01-01')
    def test_merge_conserves_size_and_successors(self):
        c={'entities':[{'id':'org:'+x,'entity_type':'business','label':x} for x in 'abc'], 'events':[
           {'id':'event:'+x,'type':'incorporation','entity':'org:'+x,'size':n,'occurred_at':'2020-01-01','observed_at':'2020-01-01'} for x,n in [('a',3),('b',5)]]}
        c['events'].append({'id':'event:merge','type':'merge','sources':['org:a','org:b'],'target':'org:c','occurred_at':'2021-01-01','observed_at':'2021-01-01'})
        result=materialize_lifecycle(c,'2022-01-01','2022-01-01')
        self.assertEqual(result['entities']['org:c']['size'],8)
        self.assertEqual(result['entities']['org:a']['successors'],['org:c'])
        self.assertFalse(actor_eligible(result,'org:a'))
        c['events'][-1]['size']=9
        with self.assertRaisesRegex(ValueError,'conserv'):materialize_lifecycle(c,'2022-01-01','2022-01-01')
    def test_conflicting_same_time_transitions_fail(self):
        c=self.config();c['events'].append({**c['events'][0],'id':'event:otherbirth','size':2})
        with self.assertRaisesRegex(ValueError,'Conflicting'):materialize_lifecycle(c,'2022-01-01','2022-01-01')
    def test_future_known_event_does_not_change_state(self):
        r=materialize_lifecycle(self.config(),'2010-01-01','2021-01-01')
        self.assertTrue(actor_eligible(r,'person:a'))
        self.assertEqual(len(r['pending_known_events']),1)
    def test_unknown_before_birth_and_invalid_type(self):
        r=materialize_lifecycle(self.config(),'1999-01-01','2021-01-01')
        self.assertFalse(actor_eligible(r,'person:a'))
        with self.assertRaisesRegex(ValueError,'Unknown'):actor_eligible(r,'person:b')
        c=self.config();c['events'][0]['type']='incorporation'
        with self.assertRaisesRegex(ValueError,'organization'):materialize_lifecycle(c,'2021-01-01','2021-01-01')
    def test_projection_retains_ended_entity(self):
        r=materialize_lifecycle(self.config(),'2021-01-01','2021-01-01')
        records=list(graph_records(r,[{'input':{'dataset':'example','artifact':'a'*64},'locator':'config'}]))
        entities=[x for x in records if x['kind']=='entity']
        self.assertEqual(len(entities),1)
        self.assertEqual(entities[0]['valid_to'],'2020-01-01')
        self.assertEqual(len([x for x in records if x['kind']=='event']),2)

    def test_institutional_schema_references_declared_types(self):
        from worldmodel.institutional_schema import schema
        from worldmodel.ontology import PARENTS
        s=schema();types=set(PARENTS)|set(s['entity_types'])
        for name,spec in s['entity_types'].items():
            self.assertIn(spec['parent'],types)
        for spec in s['relations'].values():
            for side in ('domain','range'):
                for target in spec[side] if isinstance(spec[side],list) else [spec[side]]:
                    self.assertIn(target,types)
        self.assertIn('territorial_claim',types)
        self.assertIn('disputes_claim',s['relations'])
    def test_impossible_observation_time_rejected(self):
        c=self.config();c['events'][0]['observed_at']='1990-01-01'
        with self.assertRaisesRegex(ValueError,'precede occurrence'):
            materialize_lifecycle(c,'2021-01-01','2021-01-01')
    def test_negative_growth_and_undeclared_merger_size_rejected(self):
        c=self.config();c['events'].insert(1,{'id':'event:shrink','type':'growth','entity':'person:a','delta':-2,'occurred_at':'2010-01-01','observed_at':'2010-01-01'})
        with self.assertRaisesRegex(ValueError,'negative size'):
            materialize_lifecycle(c,'2021-01-01','2021-01-01')
