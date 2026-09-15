import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store
from worldmodel.processes import ProcessRegistry
from worldmodel.materialize import materialize, load_view

PROJECT = Path(__file__).resolve().parents[1]


def constant_rate(inputs, parameters, context):
    return {'pressures':[{'port':'stock','mode':'rate','value':inputs['flow']['value'],
                          'unit':'unit','strength':1,'confidence':1}]}


def feedback(inputs, parameters, context):
    return {'pressures':[{'port':'stock','mode':'rate','value':inputs['other']['value'],
                          'unit':'unit','strength':1,'confidence':1}]}


class MaterializeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=Store(self.root/'data')
        rows=[]
        for name,value in [('a',10),('b',20)]:
            rows += [{'kind':'entity','id':f'asset:{name}','entity_type':'asset','label':name,
                      'observed_at':'2024-01-01T00:00:00Z'},
                     {'kind':'observation','id':f'obs:{name}','subject':f'asset:{name}',
                      'metric':'stock','value':value,'unit':'unit','dimensions':{},
                      'observed_at':'2024-01-01T00:00:00Z','valid_from':'2024-01-01',
                      'valid_to':'2024-01-02'}]
        self.rows=rows
        self.graph=self.publish(rows)
        self.registry=ProcessRegistry()
        self.registry.register_process({'id':'flow','inputs':{'flow':{'type':'number','unit':'unit/second'}},
            'outputs':{'stock':{'type':'number','unit':'unit'}},'topology':'flow into stock','description':'test flow'})
        self.registry.register_implementation({'id':'flow.det','process_id':'flow','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':1,'description':'constant flow'},constant_rate)
        self.request={'start':'2024-01-01T00:00:00Z','end':'2024-01-01T00:00:10Z',
            'step_seconds':5,'known_at':'2024-01-01T00:00:00Z', 'budget':100,'seed':7,
            'targets':[{'entity':'asset:a','variable':'stock'}],
            'bindings':[{'id':'a_flow','process_id':'flow','entity_id':'asset:a','fidelity':'deterministic',
               'inputs':{'flow':{'value':2,'unit':'unit/second'}},
               'outputs':{'stock':{'entity':'asset:a','variable':'stock'}},'parameters':{}}]}

    def publish(self,rows):
        raw=self.root/'graph.jsonl'
        raw.write_text(''.join(json.dumps(row)+'\n' for row in rows))
        self.store.import_file('demo_graph',raw,{'publisher':'fictional test'})
        return Runner(Catalog(PROJECT/'data'),self.store,PROJECT).run('demo_graph')

    def tearDown(self):
        self.temp.cleanup()

    def test_constant_pressure_resolution_and_provenance(self):
        result=materialize(self.store,self.graph,self.request,self.registry)
        self.assertEqual([r['value'] for r in result['snapshots']],[10,20,30])
        self.assertEqual(result['plan']['estimated_calls'],5)
        self.assertEqual(result['execution']['calls'],5)
        self.assertTrue(self.store.verify(result['artifact']))
        self.assertEqual(self.store.manifest(result['artifact'])['inputs'],[self.graph])
        self.assertEqual(load_view(self.store,result['artifact'])['snapshots'],result['snapshots'])
        finer=materialize(self.store,self.graph,{**self.request,'step_seconds':1},self.registry)
        self.assertAlmostEqual(finer['snapshots'][-1]['value'],30)

    def test_unreachable_process_is_not_executed(self):
        unreachable={**self.request['bindings'][0],'id':'b_flow','entity_id':'asset:b',
                     'fidelity':'agent','outputs':{'stock':{'entity':'asset:b','variable':'stock'}}}
        request={**self.request,'bindings':self.request['bindings']+[unreachable]}
        result=materialize(self.store,self.graph,request,self.registry)
        self.assertEqual([b['id'] for b in result['plan']['bindings']],['a_flow'])

    def test_budget_rejected_before_publication(self):
        with self.assertRaisesRegex(ValueError,'budget'):
            materialize(self.store,self.graph,{**self.request,'budget':1},self.registry)
        self.assertFalse((self.store.root/'materialized_view/latest.json').exists())

    def test_conflicting_evidence_requires_explicit_reconciliation(self):
        conflicting={**self.rows[1],'id':'obs:a:other','value':15,'observed_at':'2024-01-01T00:00:01Z'}
        graph=self.publish(self.rows+[conflicting])
        request={**self.request,'known_at':'2024-01-01T00:01:00Z'}
        with self.assertRaisesRegex(ValueError,'Conflicting'):
            materialize(self.store,graph,request,self.registry)
        result=materialize(self.store,graph,{**request,'reconciliation':'latest_observation'},self.registry)
        self.assertEqual(result['snapshots'][0]['value'],15)
        self.assertTrue(result['reconciliation'])

    def test_expired_measurement_does_not_silently_seed_forecast(self):
        request={**self.request,'start':'2024-02-01','end':'2024-02-01T00:00:10Z'}
        with self.assertRaisesRegex(ValueError,'Missing initial state'):
            materialize(self.store,self.graph,request,self.registry)

    def test_group_abstraction_requires_reducer_and_reports_members(self):
        request={**self.request,'targets':[{'entity':'asset:a','variable':'stock'},
                                          {'entity':'asset:b','variable':'stock'}],
                 'abstraction':'group','groups':[{'id':'group:all','members':['asset:a','asset:b']}]}
        with self.assertRaisesRegex(ValueError,'reducer'):
            materialize(self.store,self.graph,request,self.registry)
        result=materialize(self.store,self.graph,{**request,'reducers':{'stock':'sum'}},self.registry)
        self.assertEqual([r['value'] for r in result['snapshots']],[30,40,50])
        self.assertEqual(result['snapshots'][0]['members'],['asset:a','asset:b'])

    def test_feedback_reads_same_old_state_for_both_processes(self):
        self.registry.register_process({'id':'feedback','inputs':{'other':{'type':'number','unit':'unit'}},
            'outputs':{'stock':{'type':'number','unit':'unit'}},'topology':'mutual influence','description':'test'})
        self.registry.register_implementation({'id':'feedback.det','process_id':'feedback','fidelity':'deterministic',
            'max_step_seconds':1,'cost_per_call':1,'description':'test'},feedback)
        bindings=[]
        for entity,other in [('a','b'),('b','a')]:
            bindings.append({'id':entity,'process_id':'feedback','entity_id':f'asset:{entity}',
                'fidelity':'deterministic','inputs':{'other':{'entity':f'asset:{other}','variable':'stock'}},
                'outputs':{'stock':{'entity':f'asset:{entity}','variable':'stock'}},'parameters':{}})
        request={**self.request,'end':'2024-01-01T00:00:01Z','step_seconds':1,'bindings':bindings,
                 'targets':[{'entity':'asset:a','variable':'stock'},{'entity':'asset:b','variable':'stock'}]}
        result=materialize(self.store,self.graph,request,self.registry)
        self.assertEqual([row['value'] for row in result['snapshots'][-2:]],[30,30])

    def test_observed_view_does_not_extrapolate(self):
        request={**self.request,'mode':'observed','bindings':[],'end':'2024-01-03','step_seconds':86400}
        with self.assertRaisesRegex(ValueError,'Missing initial state'):
            materialize(self.store,self.graph,request,self.registry)

    def test_fractional_schedule_respects_budget_and_timestamp_precision(self):
        request={**self.request,'end':'2024-01-01T00:00:01Z','step_seconds':.4999999995,'budget':2,
                 'bindings':[{**self.request['bindings'][0],'cadence_seconds':.5}]}
        with self.assertRaisesRegex(ValueError,'microsecond'):
            materialize(self.store,self.graph,request,self.registry)
        result=materialize(self.store,self.graph,{**request,'step_seconds':.499999},self.registry)
        self.assertEqual(result['plan']['estimated_calls'],2)
        self.assertEqual(result['execution']['calls'],2)
        self.assertEqual(result['execution']['cost'],2)
        times=[row['time'] for row in result['snapshots']]
        self.assertEqual(len(times),len(set(times)))
        self.assertEqual(result['snapshots'][-1]['value'],12)

    def test_different_source_versions_have_distinct_materialized_record_ids(self):
        first=materialize(self.store,self.graph,self.request,self.registry)
        rows=[dict(row) for row in self.rows]
        rows[1]['value']=11
        second=materialize(self.store,self.publish(rows),self.request,self.registry)
        first_ids={row['id'] for row in self.store.records(first['artifact'])}
        second_ids={row['id'] for row in self.store.records(second['artifact'])}
        self.assertFalse(first_ids & second_ids)

    def test_group_preserves_forecast_and_persistence_classification(self):
        request={**self.request,'targets':[{'entity':'asset:a','variable':'stock'},
                  {'entity':'asset:b','variable':'stock'}], 'abstraction':'group',
                 'groups':[{'id':'group:all','members':['asset:a','asset:b']}], 'reducers':{'stock':'sum'}}
        result=materialize(self.store,self.graph,request,self.registry)
        self.assertEqual(result['snapshots'][0]['origin'],'observed')
        self.assertEqual(result['snapshots'][-1]['origin'],'scenario_aggregate')
        self.assertEqual(result['snapshots'][-1]['constituent_origins'],['forecast','persistence_assumption'])

    def test_unitless_scenario_roundtrips_without_inventing_units(self):
        request={**self.request,'mode':'forecast','bindings':[],
                 'targets':[{'entity':'asset:a','variable':'action'}],
                 'initial_state':[{'entity':'asset:a','variable':'action','value':'hold','unit':None}]}
        result=materialize(self.store,self.graph,request,self.registry)
        records=list(self.store.records(result['artifact']))
        self.assertTrue(all(row['unit'] is None and row['kind']=='assertion' for row in records))
        self.assertEqual(records[-1]['predicate'],'action')

    def test_end_of_step_predictions_do_not_arrive_at_earlier_samples(self):
        def predict(inputs,parameters,context):
            return {'pressures':[{'port':'stock','mode':'set','value':99,'unit':'unit','strength':1,'confidence':1}]}
        registry=ProcessRegistry()
        registry.register_process({'id':'settlement','inputs':{},'outputs':{'stock':{'type':'number','unit':'unit'}},'topology':'test','description':'test'})
        registry.register_implementation({'id':'settlement.det','process_id':'settlement','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':1,'output_timing':'end_of_step','description':'test'},predict)
        request={**self.request,'end':'2024-01-01T00:00:02Z','step_seconds':1,
                 'bindings':[{'id':'settlement','process_id':'settlement','inputs':{},'outputs':{'stock':{'entity':'asset:a','variable':'stock'}}}]}
        result=materialize(self.store,self.graph,request,registry)
        self.assertEqual([row['value'] for row in result['snapshots']],[10,10,99])

    def test_binding_can_choose_a_specific_numerical_implementation(self):
        def alternative(inputs,parameters,context):
            return {'pressures':[{'port':'stock','mode':'rate','value':10,'unit':'unit','strength':1,'confidence':1}]}
        self.registry.register_implementation({'id':'flow.alternative','process_id':'flow','fidelity':'deterministic',
            'max_step_seconds':2,'cost_per_call':2,'description':'alternative numerical model'},alternative)
        request={**self.request,'bindings':[{**self.request['bindings'][0],'implementation_id':'flow.alternative'}]}
        result=materialize(self.store,self.graph,request,self.registry)
        self.assertEqual(result['snapshots'][-1]['value'],110)
        self.assertEqual(result['plan']['bindings'][0]['implementation']['id'],'flow.alternative')

    def test_known_lifecycle_end_blocks_forecast_across_death(self):
        lifecycle={'entities':[{'id':'asset:a','entity_type':'person','label':'fictional actor'}],
                   'events':[{'id':'life:birth','type':'birth','entity':'asset:a','occurred_at':'2023-01-01','observed_at':'2023-01-01'},
                             {'id':'life:death','type':'death','entity':'asset:a','occurred_at':'2024-01-01T00:00:05Z','observed_at':'2024-01-01T00:00:05Z'}]}
        request={**self.request,'known_at':'2024-01-02','lifecycle':lifecycle}
        with self.assertRaisesRegex(ValueError,'inactive'):
            materialize(self.store,self.graph,request,self.registry)

    def test_economic_replay_work_is_rejected_before_execution(self):
        from worldmodel.economy import example_economy
        target={'entity':'asset:a','variable':'economy_state'}
        request={'start':'2024-01-01','end':'2026-01-01','known_at':'2024-01-01','step_seconds':86400,'budget':1000,
                 'targets':[target],'initial_state':[{**target,'value':{'config':example_economy(),'step':0},'unit':None}],
                 'bindings':[{'id':'economy','process_id':'bank_energy_business','inputs':{'economy_state':target},'outputs':{'economy_state':target}}]}
        with self.assertRaisesRegex(ValueError,'work|firm-day'):
            materialize(self.store,self.graph,request)
