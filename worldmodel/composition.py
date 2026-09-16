"""Bounded reference composition of fiscal policy, purchases and spatial lifecycle.

Synthetic fixed-price trades, no credit, behavioral calibration or external effects.
The unit implementation itemizes the same transfers; it is a work/fidelity comparison,
not a claim of a more accurate economy. Spatial events execute at interval ends.
Size/work ceilings are named limits (composition_*, field_*, spatial_*); the
optional config keys ``spatial_max_work`` (default 100000) and ``spatial_max_cells``
(default max(100, initial supports)) bound each step's audited spatial timeline.

Estimation hook (bilateral_flow_gravity): optional ``gravity_demand`` with
``distance_elasticity``, ``routes`` [{source, target, distance_km}] and fitted
``origin_effects``/``destination_effects`` (log points). A step without explicit
``source``/``target`` allocates its ``demand_kg`` over the routes in proportion to
exp(origin + destination + distance_elasticity * ln distance) using largest
remainders (ties by route order). ``calibration=`` binds
``routes.distance_elasticity`` from an estimate or calibration record.
"""
from copy import deepcopy
from datetime import timedelta
import math
from .limits import resolve_limits
from .model import instant
from .util import canonical, digest
from .spatial_store import SpatialStore, _finite as _store_finite, _json
from .process_contracts import CouplingLedger, finite, select_timed_input, require_execution_eligible


def _integer(value, maximum):
    if type(value) is not int or not 0<=value<=maximum:raise ValueError('Composition integer/work bound exceeded')
    return value


def gravity_allocation(total, routes, distance_elasticity, origin_effects=None, destination_effects=None):
    """Integer kg per route and gravity shares; deterministic largest-remainder rounding."""
    origin_effects, destination_effects = origin_effects or {}, destination_effects or {}
    logs = [origin_effects.get(r['source'], 0.0) + destination_effects.get(r['target'], 0.0) + distance_elasticity * math.log(r['distance_km'])
            for r in routes]
    top = max(logs)
    weights = [math.exp(value - top) for value in logs]
    scale = math.fsum(weights)
    shares = [w / scale for w in weights]
    raw = [total * share for share in shares]
    allocation = [min(total, math.floor(value)) for value in raw]
    remainder = max(0, min(len(routes), total - sum(allocation)))
    for index in sorted(range(len(routes)), key=lambda i: (-(raw[i] - allocation[i]), i))[:remainder]:
        allocation[index] += 1
    return allocation, shares


def _gravity(config):
    gravity = config.get('gravity_demand')
    if gravity is None:
        return None
    if not isinstance(gravity, dict) or set(gravity) - {'distance_elasticity', 'routes', 'origin_effects', 'destination_effects', 'description'}:
        raise ValueError('Invalid gravity_demand fields')
    if not -5 <= finite(gravity.get('distance_elasticity')) <= 0:
        raise ValueError('gravity distance_elasticity must be in -5..0')
    routes = gravity.get('routes')
    if not isinstance(routes, list) or not routes:
        raise ValueError('gravity_demand requires explicit routes')
    seen = set()
    for route in routes:
        if not isinstance(route, dict) or set(route) != {'source', 'target', 'distance_km'} or route['source'] == route['target']:
            raise ValueError('Gravity routes require distinct source, target and distance_km')
        if (route['source'], route['target']) in seen or not finite(route['distance_km']) > 0:
            raise ValueError('Gravity routes must be unique with positive distance_km')
        seen.add((route['source'], route['target']))
    for key in ('origin_effects', 'destination_effects'):
        effects = gravity.get(key, {})
        if not isinstance(effects, dict) or any(not isinstance(k, str) for k in effects):
            raise ValueError(f'{key} must map support IDs to finite log effects')
        for value in effects.values(): finite(value)
    return gravity


def calibrate_composition_config(config, calibration):
    """Bind ``routes.distance_elasticity`` into ``gravity_demand``; returns (config, bindings record)."""
    from .estimation.binding import apply_bindings, parameter_bindings
    return apply_bindings(config, parameter_bindings(calibration), rename={'routes.': 'gravity_demand.'},
                          only=lambda path: path.startswith('gravity_demand.'))


def gravity_effects_from_estimate(estimate, *, region_of=None):
    """Origin/destination log effects from a GravityEstimator Estimate (or its dict), keyed by support via region_of."""
    body = estimate.to_dict() if hasattr(estimate, 'to_dict') else estimate
    coefficients = body['diagnostics']['coefficients']
    region_of = region_of or {}
    effects = {'origin_effects': {}, 'destination_effects': {}}
    levels = body['diagnostics']['levels']
    for kind, names in (('origin', levels['origins']), ('destination', levels['destinations'])):
        for region in names:
            value = coefficients.get(f'{kind}:{region}', 0.0)
            for cell in [c for c, r in region_of.items() if r == region] or ([] if region_of else [region]):
                effects[f'{kind}_effects'][cell] = value
    return dict(effects, distance_elasticity=body['parameters']['distance_elasticity'])


def _count(limits, name, value, label):
    if type(value) is not int or value<0:raise ValueError('Composition integer/work bound exceeded')
    return limits.check(name,value,label)


class CompositionEvaluator:
    def __init__(self, config, *, fidelity='aggregate', known_at=None, limits=None, backend=None, calibration=None):
        resolved=resolve_limits(limits);self._limit_arg=limits;self.backend=backend;self.calibration=None
        if calibration is not None and isinstance(config,dict):config,self.calibration=calibrate_composition_config(config,calibration)
        if not isinstance(config,dict) or fidelity not in ('aggregate','unit'):raise ValueError('Invalid bounded composition config/fidelity')
        resolved.check('composition_max_config_bytes',len(canonical(config)),'Invalid bounded composition config/fidelity: config bytes')
        self.config=deepcopy(config);self.fidelity=fidelity
        cutoffs=[instant(v) for v in (known_at,config.get('known_at')) if v is not None]
        self.known_at=min(cutoffs).isoformat() if cutoffs else None
        self.start=instant(config['start']);self.interval=finite(config['step_seconds'])
        if not 0<self.interval<=86400 or round(self.interval,6)!=self.interval:raise ValueError('Composition cadence must be positive microsecond precision, <= one day')
        steps=config['steps']
        if not isinstance(steps,list) or not steps:raise ValueError(f'Composition requires 1..{resolved.composition_max_steps} steps')
        resolved.check('composition_max_steps',len(steps),'Composition steps')
        work=0;gravity=_gravity(config)
        for step in steps:
            demand=_count(resolved,'composition_max_demand',step['demand_kg'],'Composition step demand_kg')
            if not isinstance(step.get('events',[]),list):raise ValueError('Lifecycle event bound exceeded')
            resolved.check('spatial_max_lifecycle_batch',len(step.get('events',[])),'Lifecycle event bound exceeded')
            routed='source' not in step and 'target' not in step
            if routed and gravity is None:raise ValueError('A step without source/target requires gravity_demand')
            work+=1+2*(demand if fidelity=='unit' else (len(gravity['routes']) if routed else 1))
        resolved.check('composition_max_transfers',work,'Composition transfer budget exceeded before execution')
        self.estimated_work=work
        lifecycle=config.get('actor_lifecycle',{})
        self.lifecycle_work=2*len(steps)*3*(len(lifecycle.get('events',[]))+len(lifecycle.get('entities',[])))
        resolved.check('composition_max_lifecycle_work',self.lifecycle_work,'Composition lifecycle reconstruction work budget exceeded')
        self.spatial_work=_count(resolved,'field_max_work',config.get('spatial_max_work',100000),'Composition spatial_max_work')
        if not self.spatial_work:raise ValueError('spatial_max_work must be positive')
        price=_integer(config['price_cents_per_kg'],1000000)
        if not price:raise ValueError('Price must be positive integer cents per kg')
        if set(config['roles'])!={'government','buyer','producer'} or len(set(config['roles'].values()))!=3:raise ValueError('Three distinct fiscal/trade accounts required')
        if not isinstance(config['accounts'],dict) or len(config['accounts'])<3:raise ValueError('Account bound exceeded')
        resolved.check('composition_max_accounts',len(config['accounts']),'Account bound exceeded')
        for value in config['accounts'].values():_integer(value,10**12)
        if any(account not in config['accounts'] for account in config['roles'].values()):raise ValueError('Unknown role account')
        if set(config['policy_contract'])!={'unit','lag_seconds','max_age_seconds','missing'} or config['policy_contract']['unit']!='cent' or config['policy_contract']['missing']!='error':raise ValueError('Policy requires explicit cent/error temporal contract')
        if set(config['observations'])!={'accounts','cells'} or any(a not in config['accounts'] for a in config['observations']['accounts']):raise ValueError('Explicit restricted observations required')
        world=config['world']
        resolved.check('composition_max_supports',len(world['cells']),'Composition supports')
        if set(world['fields'])!={'resource'} or world['fields']['resource']['kind']!='extensive' or world['fields']['resource']['unit']!='kg' or world['fields']['resource'].get('value_type','scalar')!='scalar':raise ValueError('Composition requires one extensive scalar kg resource field')
        if any(finite(v)<0 for v in world['fields']['resource']['values'].values()):raise ValueError('Trade resource must be nonnegative')
        try:self.initial_resource=finite(math.fsum(world['fields']['resource']['values'].values()))
        except OverflowError as error:raise ValueError('Resource total exceeds finite range') from error
        self.max_cells=min(resolved.composition_max_supports,resolved.field_max_cells,resolved.spatial_max_query_rows)
        # Audited timeline domain bound: explicit config key, else max(100, initial supports).
        self.timeline_cells=_count(resolved,'field_max_cells',config.get('spatial_max_cells',max(100,len(world['cells']))),'Composition spatial_max_cells')
        if not self.timeline_cells:raise ValueError('spatial_max_cells must be positive')
        self.initial_money=sum(config['accounts'].values())
        self.store=SpatialStore(':memory:',limits=limits)
        try:self.store.initialize(world,coordinate_system=config['coordinate_system'])
        except Exception:self.store.close();raise
        self.accounts=deepcopy(config['accounts']);self.frames=[];self.receipts=[];self.completed=0;self.output_bytes=2
        self.identity=digest({'config':config,'fidelity':fidelity,'contract':'composition-v1','known_at':self.known_at})

    def close(self):self.store.close()
    def __enter__(self):return self
    def __exit__(self,*_):self.close()

    def step(self):
        if self.completed>=len(self.config['steps']):raise ValueError('Composition horizon completed')
        limits=resolve_limits(self._limit_arg)
        config=self.config;index=self.completed;step=config['steps'][index]
        start=self.start+timedelta(seconds=index*self.interval);end=start+timedelta(seconds=self.interval)
        policy=select_timed_input(config['policy_observations'],config['policy_contract'],at=start.isoformat(),known_at=self.known_at or start.isoformat())
        subsidy=_integer(policy['value'],10**12);roles=config['roles']
        for account in roles.values():
            require_execution_eligible({'lifecycle':config.get('actor_lifecycle'),'known_at':self.known_at or start.isoformat()},account,start.isoformat(),self.interval)
        with self.store._transaction(write=True):
            state=self.store.select(limit=self.max_cells)['state'];values=state['fields']['resource']['values']
            if set(values)&set(self.accounts):raise ValueError('Spatial support IDs and actor account IDs must be distinct')
            routed='source' not in step and 'target' not in step
            if routed:
                gravity=config['gravity_demand'];routes=[(r['source'],r['target']) for r in gravity['routes']]
                allocation,shares=gravity_allocation(step['demand_kg'],gravity['routes'],gravity['distance_elasticity'],gravity.get('origin_effects'),gravity.get('destination_effects'))
            else:
                routes=[(step['source'],step['target'])];allocation=[step['demand_kg']];shares=None
            for source,target in routes:
                if source not in values or target not in values or source==target:raise ValueError('Trade routes must use currently active distinct supports; stale lifecycle binding rejected')
            balances={a:{'cash':v,'resource':0} for a,v in self.accounts.items()}
            balances.update({c:{'cash':0,'resource':v} for c,v in values.items()})
            interfaces={'subsidy':{'quantity':'cash','sources':[roles['government']],'targets':[roles['buyer']]},
                        'payment':{'quantity':'cash','sources':[roles['buyer']],'targets':[roles['producer']]},
                        'delivery':{'quantity':'resource','sources':sorted({r[0] for r in routes}),'targets':sorted({r[1] for r in routes})}}
            ledger=CouplingLedger({'cash':{'unit':'cent','integer':True},'resource':{'unit':'kg','integer':False}},balances,interfaces)
            ledger.seen={r['id'] for r in self.receipts}
            def transfer(suffix,interface,source,target,q,amount):
                return {'id':f'composition:{index}:{suffix}','interface':interface,'source':source,'target':target,'quantity':q,'unit':'cent' if q=='cash' else 'kg','amount':amount}
            transfers=[transfer('subsidy','subsidy',roles['government'],roles['buyer'],'cash',subsidy)]
            ledger.apply(transfers)
            purchases=[];cash=ledger.balances[roles['buyer']]['cash'];taken={};counts=[]
            for index_route,((source,target),demand) in enumerate(zip(routes,allocation)):
                route_count=min(demand,int(values[source])-taken.get(source,0),cash//config['price_cents_per_kg'])
                route_count=max(0,route_count);taken[source]=taken.get(source,0)+route_count;cash-=route_count*config['price_cents_per_kg'];counts.append(route_count)
                label='' if not routed else f'{index_route}:'
                for part in range(route_count if self.fidelity=='unit' else int(route_count>0)):
                    amount=1 if self.fidelity=='unit' else route_count
                    purchases.extend([transfer(f'payment:{label}{part}','payment',roles['buyer'],roles['producer'],'cash',amount*config['price_cents_per_kg']),
                                      transfer(f'delivery:{label}{part}','delivery',source,target,'resource',amount)])
            count=sum(counts)
            for offset in range(0,len(purchases),1000):ledger.apply(purchases[offset:offset+1000])
            transfers+=purchases
            self.store.db.executemany('INSERT OR REPLACE INTO field_values VALUES(?,?,?)',
                ((cell,'resource',_json(_store_finite(ledger.balances[cell]['resource'],'field value'))) for cell in values))
            from .field_dynamics import _audit
            _audit(self.store,{'events':[{'type':'coupled_transfers','time':start.isoformat(),'transfers':transfers,'policy':policy}], 'requests':{'composition_hash':self.identity,'step':index}})
            timeline=self.store.evolve_timeline({'start':start.isoformat(),'end':end.isoformat(),'sample_seconds':self.interval,'step_seconds':self.interval,
                'boundary':'closed','edge_units':{'conductance':state['measure_unit']+'/second','transport_rate':'1/second'},'component_frames':{},
                'events':[{'time':end.isoformat(),'events':step['events']}] if step.get('events') else [],'max_cells':self.timeline_cells,'max_frames':2,
                'max_snapshots':2*self.timeline_cells,'max_work':self.spatial_work},limits=self._limit_arg,backend=self.backend)
            accounts={a:ledger.balances[a]['cash'] for a in self.accounts}
            final=timeline['state'];resource=math.fsum(final['fields']['resource']['values'].values())
            if sum(accounts.values())!=self.initial_money or not math.isclose(resource,self.initial_resource,rel_tol=1e-12,abs_tol=1e-12):raise ValueError('Cross-domain conservation violation')
            observation={'time':end.isoformat(),'accounts':{a:accounts[a] for a in config['observations']['accounts']},
                         'resources':{c:final['fields']['resource']['values'].get(c) for c in config['observations']['cells']},
                         'units':{'accounts':'cent','resources':'kg'}}
            frame={'time':end.isoformat(),'state':final,'source':timeline['final_source'],'coordinate_system':config['coordinate_system'],
                   'policy_input':policy,'transfers':transfers,'lifecycle':timeline['events'],'observation':observation,
                   'conservation':{'money_cents':sum(accounts.values()),'resource_kg':resource},'purchased_kg':count}
            if routed:
                frame['gravity_allocation']=[{'source':s,'target':t,'share':share,'allocated_kg':a,'purchased_kg':c} for (s,t),share,a,c in zip(routes,shares,allocation,counts)]
            size=len(canonical(frame))+1
            limits.check('composition_max_output_bytes',self.output_bytes+size,'Composition output budget exceeded')
        self.accounts=accounts;self.frames.append(frame);self.receipts+=transfers;self.completed+=1;self.output_bytes+=size
        return deepcopy(frame)

    def result(self):
        selected=self.store.select(limit=self.max_cells)
        extra={'calibration':self.calibration} if self.calibration is not None else {}
        return deepcopy({**extra,'schema_version':1,'config_hash':self.identity,'known_at':self.known_at,'completed_steps':self.completed,
            'accounts':self.accounts,'state':selected['state'],'source':selected['source'],'coordinate_system':self.config['coordinate_system'],
            'frames':self.frames,'observations':[f['observation'] for f in self.frames],'transfers':self.receipts,
            'execution':{'fidelity':self.fidelity,'estimated_max_transfers':self.estimated_work,'estimated_lifecycle_work':self.lifecycle_work,'spatial_work_upper_bound':self.spatial_work*len(self.config['steps']),'transfers':len(self.receipts),
                         'approximation':['Fixed integer-cent price, integer kg purchases, instantaneous delivery, explicit subsidy and routes; no credit or behavioral calibration.']}})

    def checkpoint(self):
        checkpoint={'schema_version':1,'identity':self.identity,'completed_steps':self.completed,'result_hash':digest(self.result())}
        return {**checkpoint,'checksum':digest(checkpoint)}

    def restore(self,checkpoint):
        candidate=deepcopy(checkpoint);checksum=candidate.pop('checksum',None)
        if checksum!=digest(candidate) or set(candidate)!={'schema_version','identity','completed_steps','result_hash'} or candidate['identity']!=self.identity or candidate['schema_version']!=1:raise ValueError('Composition checkpoint identity/checksum mismatch')
        count=_integer(candidate['completed_steps'],len(self.config['steps']))
        replacement=CompositionEvaluator(self.config,fidelity=self.fidelity,known_at=self.known_at,limits=self._limit_arg,backend=self.backend)
        replacement.calibration=deepcopy(self.calibration)
        try:
            for _ in range(count):replacement.step()
            if digest(replacement.result())!=candidate['result_hash']:raise ValueError('Checkpoint replay mismatch')
        except Exception:replacement.close();raise
        self.store.close();self.__dict__.update(replacement.__dict__)
        return self.result()


def materialize_composition(config, *, fidelity='aggregate', steps=None, known_at=None, limits=None, backend=None, calibration=None):
    with CompositionEvaluator(config,fidelity=fidelity,known_at=known_at,limits=limits,backend=backend,calibration=calibration) as evaluator:
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
    result=materialize_composition(config,fidelity=parameters.get('composition_fidelity','aggregate'),steps=count,known_at=context.get('known_at'),calibration=parameters.get('calibration'))
    diagnostics={'conservation':result['frames'][-1]['conservation'],'bounded_prefix_replay':True}
    if 'calibration' in result:diagnostics['calibration']=result['calibration']
    return {'pressures':[{'port':'composition_state','mode':'set','value':result,'unit':'composition-state','strength':1,'confidence':1}],
            'diagnostics':diagnostics}


def register_composition_process(registry):
    registry.register_process({'id':'cross_domain_composition','inputs':{'composition_config':{'type':'object','unit':'composition-config'}},
        'outputs':{'composition_state':{'type':'object','unit':'composition-state'}},'topology':'fiscal accounts -> purchase settlement -> spatial supports',
        'description':'Bounded deterministic composition with explicit resource and money conservation.'})
    registry.register_implementation({'id':'cross_domain_composition.reference','process_id':'cross_domain_composition','fidelity':'deterministic',
        'max_step_seconds':86400,'min_step_seconds':0.000001,'cost_per_call':1,'output_timing':'end_of_step',
        'approximation':['Reference prefix replay (bounded by composition_max_steps), fixed prices and instantaneous delivery; no calibrated behavior.']},_composition_prediction)
