"""Incremental, transactional temporal evaluation with JSON checkpoints.

Checkpoints are integrity checked, not authenticated: accept them only from trusted
sources. Live backend effects cannot be rolled back. A failed backend transition
blocks retries and live-backend checkpoint restore is deliberately unsupported.
"""
from copy import deepcopy
from contextlib import contextmanager
from datetime import timedelta
import math
import random

from . import materialize as temporal
from .model import instant
from .provenance import capture_code
from .processes import combine_pressures, _typed, _pressure
from .process_library import default_registry
from .util import canonical, digest, file_hash
from .view_state import EvidenceState, collect_evidence


def _json_native(value):
    """Reject Python values that JSON serialization would change semantically."""
    pending=[(value,0)];visited=0
    while pending:
        item,depth=pending.pop();visited+=1
        if depth>256 or visited>2000000:
            raise ValueError('Checkpoint JSON-native nesting or item budget exceeded')
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError('Checkpoint values must be JSON-native: object keys must be strings')
            pending.extend((v,depth+1) for v in item.values())
        elif type(item) is list:
            pending.extend((v,depth+1) for v in item)
        elif item is None or type(item) in (str,bool,int):
            continue
        elif type(item) is float and math.isfinite(item):
            continue
        else:
            raise ValueError('Checkpoint values must be finite JSON-native values; tuples and Python objects are unsupported')


class CheckpointEvaluator:
    """Environment evaluator; one new history item advances one observation step.

    reset(seed), checkpoint(), restore(JSON-dict), and publish() are explicit APIs.
    The request's full horizon pins implementation selection and prediction dt;
    observation boundaries never shorten a pending end-of-step prediction.
    """
    def __init__(self, store, graph_ref, request, step_seconds, registry=None,
                 max_total_calls=10000, agent_backend=None):
        _json_native(request);_json_native(graph_ref)
        if type(max_total_calls) is not int or not 1 <= max_total_calls <= 100000:
            raise ValueError('Invalid incremental call budget')
        if request.get('interventions'):
            raise ValueError('Environment history owns intervention schedule')
        self.store=store; self.graph_ref=deepcopy(graph_ref)
        self.step_seconds=temporal.seconds(step_seconds,'environment step_seconds')
        self.request,self.start,self.end,self.samples,self.targets=temporal._request(
            {**deepcopy(request),'step_seconds':self.step_seconds})
        self.duration=round((self.end-self.start).total_seconds(),6)
        self.registry=deepcopy(registry) if registry is not None else default_registry()
        self.backend=agent_backend; self.max_total_calls=max_total_calls
        _json_native(self.registry.describe());_json_native(getattr(agent_backend,'identity',None))
        self.bindings,self.keys,self.plan,self.specs=temporal._plan(
            self.registry,self.request,self.targets,self.duration,self.backend)
        self.live=any(b['implementation']['fidelity']=='agent' for b in self.bindings)
        if self.request.get('lifecycle'):
            from .lifecycle import materialize_lifecycle, require_actor_eligible
            for at in (self.request['start'],self.request['end']):
                lifecycle=materialize_lifecycle(self.request['lifecycle'],at,self.request['known_at'])
                for entity in {k[0] for k in self.keys} & set(lifecycle['entities']):
                    require_actor_eligible(lifecycle,entity)
        self.code=capture_code(temporal.PROJECT,'worldmodel.checkpoints:CheckpointEvaluator')
        self.identity=digest({'graph':self.graph_ref,'request':self.request,
            'registry':self.registry.describe(),'code':self.code['files'],
            'backend':getattr(self.backend,'identity',None),'max_total_calls':max_total_calls})
        self.evidence=EvidenceState(store,graph_ref,self.keys)
        self._data=None; self.audit=[]; self.blocked=False; self.attempted_calls=0

    @property
    def total_calls(self):
        return self._data['calls'] if self._data else 0

    def _verify(self):
        self.store.verify(self.graph_ref)
        if any(file_hash(temporal.PROJECT/name)!=checksum for name,checksum in self.code['files'].items()):
            raise ValueError('Code changed during checkpoint session; restart with stable code')

    def reset(self,seed=0):
        if type(seed) is not int:raise ValueError('Seed must be integer')
        self._verify()
        state,reconciliation=self.evidence.select(self.start,instant(self.request['known_at']),
            self.request.get('initial_state',[]),self.request.get('reconciliation','error'))
        for item in state.values():_json_native(item)
        temporal._compatible(state,self.bindings,self.specs)
        snapshots=temporal._snapshots(state,self.targets,self.start,self.request)
        if len(snapshots)*len(self.samples)>self.request['max_points']:
            raise ValueError('Group snapshots exceed max_points budget')
        data={'seed':seed,'state':state,'elapsed':0.0,'history':[],'memories':{},'held':{},
            'next_due':{b['id']:0.0 for b in self.bindings},
            'rngs':{b['id']:random.Random(int(digest([seed,b['id']]),16)) for b in self.bindings},
            'literals':{b['id']:{p:deepcopy(v) for p,v in b['inputs_resolved'].items() if isinstance(v,dict)} for b in self.bindings},
            'snapshots':snapshots,'trace':[],'interventions':[],'reconciliation':reconciliation,'calls':0,'cost':0.0,'work':[]}
        data['work']=self._preflight_work(data,self.duration)
        self._data=data;self.audit=[];self.blocked=False;self.attempted_calls=0
        return self.result()

    def _preflight_work(self,data,end):
        work=[]
        for b in self.bindings:
            impl=b['implementation'];name=b['id']
            calls=max(0,math.ceil(round((end-data['next_due'][name])*1000000)/round(b['cadence_seconds']*1000000)))
            if impl.get('work_estimator'):
                if impl['work_estimator']=='worldmodel.economy_processes:estimate_process_work':
                    from .economy_processes import estimate_process_work
                elif impl['work_estimator']=='worldmodel.fields:estimate_process_work':
                    from .fields import estimate_process_work
                else:raise ValueError('Unsupported process work estimator')
                item=b['inputs_resolved'][impl['work_input']]
                value=data['state'][item]['value'] if isinstance(item,tuple) else data['literals'][name][impl['work_input']]['value']
                work.append({'binding':name,'unit':impl['work_unit'],**estimate_process_work(value,calls)})
        return work

    def __call__(self,history,seed):
        _json_native(history)
        if not history:return self.reset(seed)
        if self._data is None:raise ValueError('Reset required before incremental evaluation')
        if self.blocked:raise ValueError('Backend transition failed; explicit reset required; inspect audit before retry')
        old=self._data
        if seed!=old['seed'] or len(history)!=len(old['history'])+1 or history[:-1]!=old['history']:
            raise ValueError('Incremental evaluator requires exactly one appended history item and unchanged seed')
        if not isinstance(history[-1],dict) or set(history[-1])!={'inputs'}:
            raise ValueError('History item requires inputs')
        end=round(len(history)*self.step_seconds,6)
        if end>self.duration:raise ValueError('Environment exceeds materialization horizon')
        self._verify()
        data=deepcopy(old)
        changes=[{'time':(self.start+timedelta(seconds=old['elapsed'])).isoformat(),**p} for p in history[-1]['inputs']]
        schedule=temporal._interventions({**self.request,'interventions':changes},self.start,self.end,self.bindings,self.specs)
        for change in schedule.get(old['elapsed'],[]):
            data['literals'][change['binding']][change['port']]={'value':deepcopy(change['value']),'unit':change['unit']}
            data['interventions'].append(change)
        due_counts={b['id']:max(0,math.ceil(round((end-data['next_due'][b['id']])*1000000)/round(b['cadence_seconds']*1000000))) for b in self.bindings}
        calls=sum(due_counts.values());cost=sum(due_counts[b['id']]*b['implementation']['cost_per_call'] for b in self.bindings)
        from .environments import BudgetExceeded
        if self.attempted_calls+calls>self.max_total_calls or data['calls']+calls>self.request['max_calls'] or data['cost']+cost>self.request['budget']:
            raise BudgetExceeded('Incremental process budget exhausted before handler calls')
        data['work']+=self._preflight_work(data,end)
        audit_start=len(self.audit)
        try:
            if self.request['mode']=='observed':
                data['state'],decisions=self.evidence.select(self.start+timedelta(seconds=end),instant(self.request['known_at']),policy=self.request.get('reconciliation','error'))
                data['reconciliation']+=decisions;data['elapsed']=end
            else:
                self._advance(data,end)
            data['history']=deepcopy(history)
            data['snapshots']+=temporal._snapshots(data['state'],self.targets,self.start+timedelta(seconds=end),self.request)
            self._validate_data(data)
            self._verify()
        except Exception:
            if len(self.audit)>audit_start:self.blocked=True
            raise
        self._data=data
        return self.result()

    def _advance(self,data,end):
        while data['elapsed']<end:
            elapsed=data['elapsed'];state=data['state'];pending=[]
            for b in self.bindings:
                name=b['id']
                if data['next_due'][name]>elapsed:continue
                inputs={p:{'value':deepcopy(state[v]['value']),'unit':state[v]['unit']} if isinstance(v,tuple) else deepcopy(data['literals'][name][p]) for p,v in b['inputs_resolved'].items()}
                memory=deepcopy(data['memories'].get(b['entity_id'],{}))
                context={'dt_seconds':min(b['cadence_seconds'],self.duration-elapsed),
                    'time':(self.start+timedelta(seconds=elapsed)).isoformat(),'rng':data['rngs'][name],
                    'state':{p:deepcopy(state[k]['value']) for p,k in b['outputs_resolved'].items()},
                    'entity_id':b['entity_id'],'memory':memory,'agent_backend':self.backend,
                    'budget':self.request['budget'],'remaining_budget':self.request['budget']-data['cost']-sum(x[0]['implementation']['cost_per_call'] for x in pending)}
                entry=None
                if b['implementation']['fidelity']=='agent':
                    entry={'sequence':len(self.audit),'binding':name,'time':context['time'],
                           'inputs_digest':digest(inputs),'status':'attempted'}
                    self.audit.append(entry)
                self.attempted_calls+=1
                prediction=self.registry.predict(b['implementation']['id'],inputs,deepcopy(b.get('parameters',{})),context)
                _json_native(prediction)
                if entry is not None:entry.update(status='returned',prediction_digest=digest(prediction))
                pending.append((b,inputs,memory,prediction))
            for b,inputs,memory,prediction in pending:
                name=b['id'];data['held'][name]=prediction['pressures']
                if b['implementation']['fidelity']=='agent':data['memories'][b['entity_id']]=deepcopy(prediction.get('memory',memory))
                data['next_due'][name]=round(min(self.duration,elapsed+b['cadence_seconds']),6)
                data['calls']+=1;data['cost']+=b['implementation']['cost_per_call']
                data['trace'].append({'time':(self.start+timedelta(seconds=elapsed)).isoformat(),
                    'binding':name,'implementation':b['implementation']['id'],'inputs':inputs,
                    'prediction':prediction,'memory_before':memory,'memory_after':deepcopy(data['memories'].get(b['entity_id'],{}))})
            boundary=min([end]+list(data['next_due'].values()))
            dt=boundary-elapsed
            if dt<=0:raise ValueError('Temporal scheduling made no progress')
            influences={}
            for b in self.bindings:
                if b['implementation'].get('output_timing')=='end_of_step' and boundary<data['next_due'][b['id']]:continue
                for pressure in data['held'].get(b['id'],[]):
                    influences.setdefault(b['outputs_resolved'][pressure['port']],[]).append(pressure)
            new=deepcopy(state);provenance=collect_evidence(state)
            for item in new.values():
                if item['origin']=='observed':item['origin']='persistence_assumption'
            for key,pressures in influences.items():
                new[key]['value']=combine_pressures(state[key]['value'],pressures,dt,value_type=state[key]['type'])
                new[key].update(origin='forecast',evidence=provenance)
            for b in self.bindings:
                for port,key in b['outputs_resolved'].items():
                    spec=self.specs[b['process_id']]['outputs'][port];value=new[key]['value']
                    if type(value) in (int,float) and not spec.get('minimum',-math.inf)<=value<=spec.get('maximum',math.inf):
                        raise ValueError('Forecast violates declared output bounds')
            data['state']=new;data['elapsed']=boundary

    def result(self):
        if self._data is None:raise ValueError('Reset required')
        d=self._data
        return deepcopy({'schema_version':1,'graph':self.graph_ref,'request':{**self.request,'seed':d['seed'],
            'end':(self.start+timedelta(seconds=d['elapsed'])).isoformat()},'plan':{**self.plan,'work':d['work']},
            'snapshots':d['snapshots'],'trace':d['trace'],'input_interventions':d['interventions'],
            'reconciliation':d['reconciliation'],'execution':{'calls':d['calls'],'cost':d['cost'],'agent_memories':d['memories']},
            'backend_identity':getattr(self.backend,'identity',None),'backend_audit':self.audit,
            'limitations':['Process predictions are illustrative and uncalibrated.',
                'Forecast inputs persist as explicit assumptions; temporal sampling adds no observational precision.',
                'Backend side effects cannot be rolled back; failed live transitions block retry and live checkpoint restore is unsupported.',
                'Checkpoint checksums detect corruption, not malicious modification.']})

    def _validate_data(self,data):
        required={'seed','state','elapsed','history','memories','held','next_due','rngs',
                  'literals','snapshots','trace','interventions','reconciliation','calls','cost','work'}
        if set(data)!=required or type(data['seed']) is not int:
            raise ValueError('Checkpoint schema mismatch')
        if not isinstance(data['history'],list) or any(not isinstance(h,dict) or set(h)!={'inputs'} or not isinstance(h['inputs'],list) for h in data['history']):
            raise ValueError('Checkpoint history invalid')
        if not isinstance(data['trace'],list) or len(data['trace'])!=data['calls']:
            raise ValueError('Checkpoint trace accounting mismatch')
        by_name={b['id']:b for b in self.bindings}
        if any(t.get('binding') not in by_name or t.get('implementation')!=by_name[t['binding']]['implementation']['id'] for t in data['trace']):
            raise ValueError('Checkpoint trace binding mismatch')
        expected_cost=sum(by_name[t['binding']]['implementation']['cost_per_call'] for t in data['trace'])
        if not math.isclose(data['cost'],expected_cost,rel_tol=1e-12,abs_tol=1e-12):
            raise ValueError('Checkpoint cost accounting mismatch')
        for b in self.bindings:
            literals=data['literals'][b['id']]
            if set(literals)!={p for p,v in b['inputs_resolved'].items() if isinstance(v,dict)}:
                raise ValueError('Checkpoint literal ports mismatch')
            for port,item in literals.items():
                spec=self.specs[b['process_id']]['inputs'][port]
                if not isinstance(item,dict) or set(item)!={'value','unit'} or item['unit']!=spec.get('unit'):
                    raise ValueError('Checkpoint literal unit mismatch')
                try:_typed(item['value'],spec['type'])
                except ValueError as exc:raise ValueError('Checkpoint literal type mismatch') from exc
        if set(data['state'])!=set(self.keys):raise ValueError('Checkpoint state keys mismatch')
        temporal._compatible(data['state'],self.bindings,self.specs)
        for item in data['state'].values():_typed(item['value'],item['type'])
        if data['elapsed']!=round(len(data['history'])*self.step_seconds,6) or not 0<=data['elapsed']<=self.duration:
            raise ValueError('Checkpoint elapsed/history mismatch')
        names={b['id'] for b in self.bindings}
        if set(data['next_due'])!=names or set(data['rngs'])!=names or set(data['literals'])!=names:
            raise ValueError('Checkpoint scheduler keys mismatch')
        if not set(data['held'])<=names:raise ValueError('Checkpoint held pressure mismatch')
        last_predictions={};scheduled={name:0.0 for name in names}
        for trace in data['trace']:
            binding=by_name[trace['binding']];name=binding['id']
            at=round((instant(trace['time'])-self.start).total_seconds(),6)
            if at!=scheduled[name] or not 0<=at<data['elapsed']:
                raise ValueError('Checkpoint trace scheduling mismatch')
            scheduled[name]=round(min(self.duration,at+binding['cadence_seconds']),6)
            prediction=trace['prediction']
            for field,kind in (('pressures',list),('events',list),('memory',dict),('diagnostics',dict)):
                if type(prediction.get(field)) is not kind:raise ValueError('Checkpoint prediction schema mismatch')
            last_predictions[name]=prediction['pressures']
        if data['next_due']!=scheduled or data['held']!=last_predictions:
            raise ValueError('Checkpoint held pressures or next-due schedule disagree with trace')
        for name,pressures in data['held'].items():
            binding=by_name[name];output_specs=self.specs[binding['process_id']]['outputs']
            for pressure in pressures:
                if not isinstance(pressure,dict) or pressure.get('port') not in output_specs:
                    raise ValueError('Checkpoint held pressure output port mismatch')
                expected=output_specs[pressure['port']]
                _pressure(pressure,expected['type'])
                if pressure['unit']!=expected.get('unit'):
                    raise ValueError('Checkpoint held pressure output unit mismatch')
                if binding['implementation'].get('output_timing')=='end_of_step' and pressure['mode']!='set':
                    raise ValueError('Checkpoint end_of_step pressure must use set mode')
                state_value=data['state'][binding['outputs_resolved'][pressure['port']]]['value']
                if expected['type']=='vector' and len(pressure['value'])!=len(state_value):
                    raise ValueError('Checkpoint held pressure vector dimension mismatch')
        if any(not data['elapsed']<=v<=self.duration for v in data['next_due'].values()):raise ValueError('Checkpoint due time invalid')
        if type(data['calls']) is not int or not 0<=data['calls']<=self.request['max_calls'] or not 0<=data['cost']<=self.request['budget']:
            raise ValueError('Checkpoint budget invalid')

    def checkpoint(self):
        if self._data is None:raise ValueError('Reset required')
        payload=deepcopy(self._data)
        payload['state']=[{'key':list(k),'item':v} for k,v in sorted(payload['state'].items())]
        payload['rngs']={k:[v.getstate()[0],list(v.getstate()[1]),v.getstate()[2]] for k,v in payload['rngs'].items()}
        _json_native(payload)
        checkpoint={'schema_version':1,'identity':self.identity,'payload':payload}
        # canonical JSON normalizes RNG tuples without unsafe pickle deserialization.
        import json
        checkpoint=json.loads(canonical(checkpoint))
        return {**checkpoint,'checksum':digest(checkpoint)}

    def restore(self,checkpoint):
        _json_native(checkpoint)
        if self.live:raise ValueError('Live backend checkpoint restore cannot guarantee exactly-once external effects')
        if self.blocked:raise ValueError('Blocked backend session cannot restore')
        if len(canonical(checkpoint))>32*1024*1024:raise ValueError('Checkpoint exceeds 32 MiB')
        candidate=deepcopy(checkpoint)
        checksum=candidate.pop('checksum',None)
        if set(candidate)!={'schema_version','identity','payload'} or candidate['schema_version']!=1 or candidate['identity']!=self.identity or digest(candidate)!=checksum:
            raise ValueError('Checkpoint integrity or identity mismatch')
        try:
            data=candidate['payload']
            rows=data['state'];data['state']={tuple(row['key']):row['item'] for row in rows}
            if len(data['state'])!=len(rows):raise ValueError('Duplicate checkpoint state keys')
            rngs={}
            for name,value in data['rngs'].items():
                rng=random.Random();rng.setstate((value[0],tuple(value[1]),value[2]));rngs[name]=rng
            data['rngs']=rngs
            self._validate_data(data)
        except (KeyError,TypeError,IndexError,OverflowError) as exc:
            raise ValueError('Invalid checkpoint structure') from exc
        self._verify();self._data=data;self.attempted_calls=max(self.attempted_calls,data['calls'])
        return self.result()

    @contextmanager
    def transaction(self):
        """Also roll back when an Environment rejects outputs or reward selectors."""
        before=deepcopy(self._data);audit_start=len(self.audit)
        try:
            yield
        except Exception:
            self._data=before
            if len(self.audit)>audit_start:self.blocked=True
            raise

    def publish(self):
        """Publish the committed trajectory with source and graph provenance."""
        self._verify()
        result=self.result()
        return temporal._publish(self.store,result,self.code,self.registry.describe())
