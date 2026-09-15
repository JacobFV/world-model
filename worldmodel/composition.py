"""Bounded reference composition of fiscal policy, purchases and spatial lifecycle.

Synthetic fixed-price trades, no credit, behavioral calibration or external effects.
The unit implementation itemizes the same transfers; it is a work/fidelity comparison,
not a claim of a more accurate economy. Spatial events execute at interval ends.
"""
from copy import deepcopy
from datetime import timedelta
import math
from .model import instant
from .util import canonical, digest
from .spatial_store import SpatialStore
from .process_contracts import CouplingLedger, finite, select_timed_input, require_execution_eligible


def _integer(value, maximum):
    if type(value) is not int or not 0<=value<=maximum:raise ValueError('Composition integer/work bound exceeded')
    return value


class CompositionEvaluator:
    def __init__(self, config, *, fidelity='aggregate', known_at=None):
        if not isinstance(config,dict) or len(canonical(config))>1_000_000 or fidelity not in ('aggregate','unit'):raise ValueError('Invalid bounded composition config/fidelity')
        self.config=deepcopy(config);self.fidelity=fidelity
        cutoffs=[instant(v) for v in (known_at,config.get('known_at')) if v is not None]
        self.known_at=min(cutoffs).isoformat() if cutoffs else None
        self.start=instant(config['start']);self.interval=finite(config['step_seconds'])
        if not 0<self.interval<=86400 or round(self.interval,6)!=self.interval:raise ValueError('Composition cadence must be positive microsecond precision, <= one day')
        steps=config['steps']
        if not isinstance(steps,list) or not 1<=len(steps)<=20:raise ValueError('Composition requires 1..20 steps')
        work=0
        for step in steps:
            demand=_integer(step['demand_kg'],1000)
            if not isinstance(step.get('events',[]),list) or len(step.get('events',[]))>20:raise ValueError('Lifecycle event bound exceeded')
            work+=1+2*(demand if fidelity=='unit' else 1)
        if work>10000:raise ValueError('Composition transfer budget exceeded before execution')
        self.estimated_work=work
        lifecycle=config.get('actor_lifecycle',{})
        self.lifecycle_work=2*len(steps)*3*(len(lifecycle.get('events',[]))+len(lifecycle.get('entities',[])))
        if self.lifecycle_work>1000000:raise ValueError('Composition lifecycle reconstruction work budget exceeded')
        price=_integer(config['price_cents_per_kg'],1000000)
        if not price:raise ValueError('Price must be positive integer cents per kg')
        if set(config['roles'])!={'government','buyer','producer'} or len(set(config['roles'].values()))!=3:raise ValueError('Three distinct fiscal/trade accounts required')
        if not isinstance(config['accounts'],dict) or not 3<=len(config['accounts'])<=100:raise ValueError('Account bound exceeded')
        for value in config['accounts'].values():_integer(value,10**12)
        if any(account not in config['accounts'] for account in config['roles'].values()):raise ValueError('Unknown role account')
        if set(config['policy_contract'])!={'unit','lag_seconds','max_age_seconds','missing'} or config['policy_contract']['unit']!='cent' or config['policy_contract']['missing']!='error':raise ValueError('Policy requires explicit cent/error temporal contract')
        if set(config['observations'])!={'accounts','cells'} or any(a not in config['accounts'] for a in config['observations']['accounts']):raise ValueError('Explicit restricted observations required')
        world=config['world']
        if len(world['cells'])>100 or set(world['fields'])!={'resource'} or world['fields']['resource']['kind']!='extensive' or world['fields']['resource']['unit']!='kg' or world['fields']['resource'].get('value_type','scalar')!='scalar':raise ValueError('Composition requires <=100 supports and one extensive scalar kg resource field')
        if any(finite(v)<0 for v in world['fields']['resource']['values'].values()):raise ValueError('Trade resource must be nonnegative')
        try:self.initial_resource=finite(math.fsum(world['fields']['resource']['values'].values()))
        except OverflowError as error:raise ValueError('Resource total exceeds finite range') from error
        self.initial_money=sum(config['accounts'].values())
        self.store=SpatialStore(':memory:')
        try:self.store.initialize(world,coordinate_system=config['coordinate_system'])
        except Exception:self.store.close();raise
        self.accounts=deepcopy(config['accounts']);self.frames=[];self.receipts=[];self.completed=0
        self.identity=digest({'config':config,'fidelity':fidelity,'contract':'composition-v1','known_at':self.known_at})

    def close(self):self.store.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

    def step(self):
        if self.completed>=len(self.config['steps']):raise ValueError('Composition horizon completed')
        config=self.config;index=self.completed;step=config['steps'][index]
        start=self.start+timedelta(seconds=index*self.interval);end=start+timedelta(seconds=self.interval)
        policy=select_timed_input(config['policy_observations'],config['policy_contract'],at=start.isoformat(),known_at=self.known_at or start.isoformat())
        subsidy=_integer(policy['value'],10**12);roles=config['roles']
        for account in roles.values():
            require_execution_eligible({'lifecycle':config.get('actor_lifecycle'),'known_at':self.known_at or start.isoformat()},account,start.isoformat(),self.interval)
        with self.store._transaction(write=True):
            state=self.store.select()['state'];values=state['fields']['resource']['values']
            if set(values)&set(self.accounts):raise ValueError('Spatial support IDs and actor account IDs must be distinct')
            if step['source'] not in values or step['target'] not in values or step['source']==step['target']:raise ValueError('Trade routes must use currently active distinct supports; stale lifecycle binding rejected')
            balances={a:{'cash':v,'resource':0} for a,v in self.accounts.items()}
            balances.update({c:{'cash':0,'resource':v} for c,v in values.items()})
            interfaces={'subsidy':{'quantity':'cash','sources':[roles['government']],'targets':[roles['buyer']]},
                        'payment':{'quantity':'cash','sources':[roles['buyer']],'targets':[roles['producer']]},
                        'delivery':{'quantity':'resource','sources':[step['source']],'targets':[step['target']]}}
            ledger=CouplingLedger({'cash':{'unit':'cent','integer':True},'resource':{'unit':'kg','integer':False}},balances,interfaces)
            ledger.seen={r['id'] for r in self.receipts}
            def transfer(suffix,interface,source,target,q,amount):
                return {'id':f'composition:{index}:{suffix}','interface':interface,'source':source,'target':target,'quantity':q,'unit':'cent' if q=='cash' else 'kg','amount':amount}
            transfers=[transfer('subsidy','subsidy',roles['government'],roles['buyer'],'cash',subsidy)]
            ledger.apply(transfers)
            count=min(step['demand_kg'],int(values[step['source']]),ledger.balances[roles['buyer']]['cash']//config['price_cents_per_kg'])
            purchases=[]
            for part in range(count if self.fidelity=='unit' else int(count>0)):
                amount=1 if self.fidelity=='unit' else count
                purchases.extend([transfer(f'payment:{part}','payment',roles['buyer'],roles['producer'],'cash',amount*config['price_cents_per_kg']),
                                  transfer(f'delivery:{part}','delivery',step['source'],step['target'],'resource',amount)])
            for offset in range(0,len(purchases),1000):ledger.apply(purchases[offset:offset+1000])
            transfers+=purchases
            for cell in values:self.store._put_values(cell,{'resource':ledger.balances[cell]['resource']})
            from .field_dynamics import _audit
            _audit(self.store,{'events':[{'type':'coupled_transfers','time':start.isoformat(),'transfers':transfers,'policy':policy}], 'requests':{'composition_hash':self.identity,'step':index}})
            timeline=self.store.evolve_timeline({'start':start.isoformat(),'end':end.isoformat(),'sample_seconds':self.interval,'step_seconds':self.interval,
                'boundary':'closed','edge_units':{'conductance':state['measure_unit']+'/second','transport_rate':'1/second'},'component_frames':{},
                'events':[{'time':end.isoformat(),'events':step['events']}] if step.get('events') else [],'max_cells':100,'max_frames':2,'max_snapshots':200,'max_work':100000})
            accounts={a:ledger.balances[a]['cash'] for a in self.accounts}
            final=timeline['state'];resource=math.fsum(final['fields']['resource']['values'].values())
            if sum(accounts.values())!=self.initial_money or not math.isclose(resource,self.initial_resource,rel_tol=1e-12,abs_tol=1e-12):raise ValueError('Cross-domain conservation violation')
            observation={'time':end.isoformat(),'accounts':{a:accounts[a] for a in config['observations']['accounts']},
                         'resources':{c:final['fields']['resource']['values'].get(c) for c in config['observations']['cells']},
                         'units':{'accounts':'cent','resources':'kg'}}
            frame={'time':end.isoformat(),'state':final,'source':timeline['final_source'],'coordinate_system':config['coordinate_system'],
                   'policy_input':policy,'transfers':transfers,'lifecycle':timeline['events'],'observation':observation,
                   'conservation':{'money_cents':sum(accounts.values()),'resource_kg':resource},'purchased_kg':count}
            if len(canonical(self.frames+[frame]))>8_000_000:raise ValueError('Composition output budget exceeded')
        self.accounts=accounts;self.frames.append(frame);self.receipts+=transfers;self.completed+=1
        return deepcopy(frame)

    def result(self):
        selected=self.store.select()
        return deepcopy({'schema_version':1,'config_hash':self.identity,'known_at':self.known_at,'completed_steps':self.completed,
            'accounts':self.accounts,'state':selected['state'],'source':selected['source'],'coordinate_system':self.config['coordinate_system'],
            'frames':self.frames,'observations':[f['observation'] for f in self.frames],'transfers':self.receipts,
            'execution':{'fidelity':self.fidelity,'estimated_max_transfers':self.estimated_work,'estimated_lifecycle_work':self.lifecycle_work,'spatial_work_upper_bound':100000*len(self.config['steps']),'transfers':len(self.receipts),
                         'approximation':['Fixed integer-cent price, integer kg purchases, instantaneous delivery, explicit subsidy and routes; no credit or behavioral calibration.']}})

    def checkpoint(self):
        checkpoint={'schema_version':1,'identity':self.identity,'completed_steps':self.completed,'result_hash':digest(self.result())}
        return {**checkpoint,'checksum':digest(checkpoint)}

    def restore(self,checkpoint):
        candidate=deepcopy(checkpoint);checksum=candidate.pop('checksum',None)
        if checksum!=digest(candidate) or set(candidate)!={'schema_version','identity','completed_steps','result_hash'} or candidate['identity']!=self.identity or candidate['schema_version']!=1:raise ValueError('Composition checkpoint identity/checksum mismatch')
        count=_integer(candidate['completed_steps'],len(self.config['steps']))
        replacement=CompositionEvaluator(self.config,fidelity=self.fidelity,known_at=self.known_at)
        try:
            for _ in range(count):replacement.step()
            if digest(replacement.result())!=candidate['result_hash']:raise ValueError('Checkpoint replay mismatch')
        except Exception:replacement.close();raise
        self.store.close();self.__dict__.update(replacement.__dict__)
        return self.result()


def materialize_composition(config, *, fidelity='aggregate', steps=None, known_at=None):
    with CompositionEvaluator(config,fidelity=fidelity,known_at=known_at) as evaluator:
        count=len(config['steps']) if steps is None else _integer(steps,len(config['steps']))
        for _ in range(count):evaluator.step()
        return evaluator.result()


def _composition_prediction(inputs,parameters,context):
    config=inputs['composition_config']['value'];previous=context['state']['composition_state']
    completed=_integer(previous.get('completed_steps',0),len(config['steps']))
    expected=instant(config['start'])+timedelta(seconds=completed*config['step_seconds'])
    if context['dt_seconds']!=config['step_seconds'] or instant(context['time'])!=expected:
        raise ValueError('Composition clock/cadence must match outer process time and duration')
    count=completed+1
    result=materialize_composition(config,fidelity=parameters.get('composition_fidelity','aggregate'),steps=count,known_at=context.get('known_at'))
    return {'pressures':[{'port':'composition_state','mode':'set','value':result,'unit':'composition-state','strength':1,'confidence':1}],
            'diagnostics':{'conservation':result['frames'][-1]['conservation'],'bounded_prefix_replay':True}}


def register_composition_process(registry):
    registry.register_process({'id':'cross_domain_composition','inputs':{'composition_config':{'type':'object','unit':'composition-config'}},
        'outputs':{'composition_state':{'type':'object','unit':'composition-state'}},'topology':'fiscal accounts -> purchase settlement -> spatial supports',
        'description':'Bounded deterministic composition with explicit resource and money conservation.'})
    registry.register_implementation({'id':'cross_domain_composition.reference','process_id':'cross_domain_composition','fidelity':'deterministic',
        'max_step_seconds':86400,'min_step_seconds':0.000001,'cost_per_call':1,'output_timing':'end_of_step',
        'approximation':['Reference prefix replay (at most 20 steps per call), fixed prices and instantaneous delivery; no calibrated behavior.']},_composition_prediction)
