import copy
import unittest
from worldmodel.process_contracts import CouplingLedger, select_timed_input, aggregate_quantity, require_execution_eligible


class ContractTests(unittest.TestCase):
    def ledger(self):
        return CouplingLedger({'cash':{'unit':'cent','integer':True}}, {'a':{'cash':10},'b':{'cash':0}},
                              {'payment':{'quantity':'cash','sources':['a'],'targets':['b']}})

    def test_atomic_conservation_duplicate_transfer_and_interface_bounds(self):
        ledger=self.ledger();transfer={'id':'tx:1','interface':'payment','source':'a','target':'b','quantity':'cash','unit':'cent','amount':3}
        ledger.apply([transfer]);self.assertEqual(ledger.snapshot()['balances'],{'a':{'cash':7},'b':{'cash':3}})
        before=ledger.snapshot()
        for batch in ([transfer],[{**transfer,'id':'tx:2','amount':9}],[{**transfer,'id':'tx:2','unit':'USD'}],
                      [{**transfer,'id':'tx:2','amount':1},{**transfer,'id':'tx:2','amount':1}]):
            with self.assertRaises(ValueError):ledger.apply(batch)
            self.assertEqual(ledger.snapshot(),before)

    def test_information_valid_time_lag_and_explicit_missing(self):
        rows=[{'value':7,'unit':'kg','information_time':'2026-01-01T00:00:02Z','valid_time':'2026-01-01T00:00:00Z'}]
        contract={'unit':'kg','lag_seconds':1,'max_age_seconds':5,'missing':'error'}
        with self.assertRaises(ValueError):select_timed_input(rows,contract,at='2026-01-01T00:00:02Z',known_at='2026-01-01T00:00:02Z')
        got=select_timed_input(rows,contract,at='2026-01-01T00:00:03Z',known_at='2026-01-01T00:00:03Z')
        self.assertEqual(got['value'],7)
        self.assertIsNone(select_timed_input([],dict(contract,missing='omit'),at='2026-01-01T00:00:03Z',known_at='2026-01-01T00:00:03Z'))
        with self.assertRaises(ValueError):select_timed_input(rows,{**contract,'unit':'USD'},at='2026-01-01T00:00:03Z',known_at='2026-01-01T00:00:03Z')

    def test_extensive_and_intensive_aggregation_do_not_average_identities(self):
        rows=[{'value':2,'unit':'kg','measure':1},{'value':8,'unit':'kg','measure':3}]
        self.assertEqual(aggregate_quantity(rows,kind='extensive',unit='kg')['value'],10)
        self.assertEqual(aggregate_quantity(rows,kind='intensive',unit='kg')['value'],6.5)
        with self.assertRaises(ValueError):aggregate_quantity([{'value':'actor:a','unit':'id','measure':1}],kind='intensive',unit='id')
        with self.assertRaises(ValueError):aggregate_quantity(rows,kind='identity',unit='kg')


class SchedulerContractTests(unittest.TestCase):
    def test_registry_resolves_temporal_literal_and_records_selected_input(self):
        from worldmodel.processes import ProcessRegistry
        registry=ProcessRegistry()
        registry.register_process({'id':'timed','inputs':{'x':{'type':'number','unit':'kg','temporal':{'lag_seconds':1,'max_age_seconds':10,'missing':'error'}}},
                                   'outputs':{},'topology':'explicit','description':'timed input'})
        registry.register_implementation({'id':'timed.det','process_id':'timed','fidelity':'deterministic','max_step_seconds':1,'cost_per_call':1},lambda inputs,p,c:{'diagnostics':{'selected':inputs['x']['value']}})
        item={'value':[{'value':4,'unit':'kg','information_time':'2026-01-01T00:00:00Z','valid_time':'2026-01-01T00:00:00Z'}],'unit':'kg'}
        with self.assertRaises(ValueError):registry.predict('timed.det',{'x':item},{},{'dt_seconds':1,'time':'2026-01-01T00:00:00Z'})
        result=registry.predict('timed.det',{'x':item},{},{'dt_seconds':1,'time':'2026-01-01T00:00:01Z'})
        self.assertEqual(result['diagnostics']['selected'],4)
        self.assertEqual(result['input_receipts']['x']['valid_time'],'2026-01-01T00:00:00Z')

    def test_lifecycle_budget_rejected_before_reconstruction(self):
        from unittest.mock import patch
        from tests.test_materialize import MaterializeTests
        from worldmodel.materialize import materialize
        fixture=MaterializeTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        request={**fixture.request,'end':'2024-01-01T00:33:20Z','max_points':10000,'budget':10000,
                 'lifecycle':{'entities':[],'events':[{}]*10000}}
        with patch('worldmodel.lifecycle.materialize_lifecycle') as reconstruct:
            with self.assertRaisesRegex(ValueError,'Lifecycle.*budget'):
                materialize(fixture.store,fixture.graph,request,fixture.registry)
            reconstruct.assert_not_called()

    def test_actor_eligibility_reconstructs_at_most_two_boundaries(self):
        from unittest.mock import patch
        lifecycle={'entities':[{'id':'org:a','entity_type':'organization','label':'A'}],
                   'events':[{'id':f'event:{i}','occurred_at':f'2026-01-01T00:00:00.{i:06d}Z'} for i in range(100)]}
        with patch('worldmodel.lifecycle.materialize_lifecycle',return_value={'entities':{'org:a':{'status':'active'}}}) as reconstruct:
            require_execution_eligible({'lifecycle':lifecycle,'known_at':'2026-01-01T00:00:02Z'},'org:a','2026-01-01T00:00:00Z',1)
            self.assertLessEqual(reconstruct.call_count,2)

    def test_actor_cannot_execute_across_dissolution(self):
        lifecycle={'entities':[{'id':'org:a','entity_type':'organization','label':'A'}],
                   'events':[{'id':'event:start','type':'incorporation','entity':'org:a','occurred_at':'2026-01-01T00:00:00Z','observed_at':'2026-01-01T00:00:00Z'},
                             {'id':'event:end','type':'dissolution','entity':'org:a','occurred_at':'2026-01-01T00:00:01Z','observed_at':'2026-01-01T00:00:01Z'}]}
        request={'lifecycle':lifecycle,'known_at':'2026-01-01T00:00:02Z'}
        require_execution_eligible(request,'org:a','2026-01-01T00:00:00Z',1)
        with self.assertRaises(ValueError):require_execution_eligible(request,'org:a','2026-01-01T00:00:00Z',2)
        require_execution_eligible(request,'cell:a','2026-01-01T00:00:00Z',2)

    def test_declared_conservation_rejects_double_writers_and_nonconserving_outputs(self):
        from tests.test_materialize import MaterializeTests
        from worldmodel.processes import ProcessRegistry
        from worldmodel.materialize import materialize
        fixture=MaterializeTests();fixture.setUp();self.addCleanup(fixture.tearDown)
        registry=ProcessRegistry()
        registry.register_process({'id':'exchange','inputs':{},'outputs':{'a':{'type':'number','unit':'unit'},'b':{'type':'number','unit':'unit'}},
            'conserved_quantities':[{'id':'stock','unit':'unit','ports':['a','b']}],'topology':'exchange','description':'conserved'})
        registry.register_implementation({'id':'exchange.det','process_id':'exchange','fidelity':'deterministic','max_step_seconds':1,'cost_per_call':1},
            lambda i,p,c:{'pressures':[{'port':'a','mode':'rate','value':1,'unit':'unit','strength':1,'confidence':1}]})
        binding={'id':'exchange','process_id':'exchange','inputs':{},'outputs':{'a':{'entity':'asset:a','variable':'stock'},'b':{'entity':'asset:b','variable':'stock'}}}
        request={**fixture.request,'bindings':[binding]}
        with self.assertRaisesRegex(ValueError,'conserved'):materialize(fixture.store,fixture.graph,request,registry)
        with self.assertRaisesRegex(ValueError,'multiple writers'):materialize(fixture.store,fixture.graph,{**request,'bindings':[binding,{**binding,'id':'duplicate'}]},registry)


class AgentInvocationContractTests(unittest.TestCase):
    def test_explicit_model_prompt_tools_and_observations_bound_to_request(self):
        from worldmodel.process_contracts import ContractAgentBackend
        class Mock:
            identity={'provider':'deterministic-test'}
            def predict(self,request):
                self.received=copy.deepcopy(request)
                return {'value':'hold'}
        provider=Mock()
        backend=ContractAgentBackend(provider,model='mock-v1',configuration={'temperature':0},prompt='Use only supplied observations.',tool_policy={'allowed':[]})
        request={'observations':{'inventory':2},'memory':{}}
        response=backend.predict(request)
        self.assertEqual(response,{'value':'hold'})
        self.assertEqual(provider.received['execution_contract']['model'],'mock-v1')
        self.assertEqual(provider.received['execution_contract']['tool_policy'],{'allowed':[]})
        self.assertEqual(provider.received['observations'],{'inventory':2})
        self.assertNotIn('execution_contract',request)
        self.assertEqual(backend.identity['execution_contract']['prompt'],'Use only supplied observations.')
