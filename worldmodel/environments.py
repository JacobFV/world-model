"""Environments are input/output adapters over materializations, independent of actor types.

Evaluator protocol: evaluate(history, seed) -> {snapshots, execution?, artifact?}.
History contains routed input values. Evaluators must replay deterministically or
own equivalent transactional state; the built-in adapter uses bounded replay.
"""
from copy import deepcopy
from contextlib import nullcontext
from datetime import timedelta
import math
from .limits import resolve_limits
from .util import canonical


class BudgetExceeded(ValueError):
    """An evaluator cannot execute another prefix within its declared work budget."""


def _finite(value):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Expected finite numeric value')
    return value


def select_output(result,selector):
    if not {'entity','variable','unit'}<=set(selector):raise ValueError('Output selectors require entity, variable and explicit unit')
    rows=[r for r in result['snapshots'] if r['entity']==selector['entity'] and r['variable']==selector['variable']]
    if not rows:raise ValueError('Missing selected output: '+str(selector))
    # Materializers emit snapshots in chronological order; adapters must preserve this contract.
    last=rows[-1];ties=[r for r in rows if r['time']==last['time']]
    if len({canonical([r['value'],r.get('unit')]) for r in ties})!=1:raise ValueError('Conflicting selected outputs')
    if last.get('unit')!=selector['unit']:raise ValueError('Selected output unit mismatch')
    value=last['value']
    path=selector.get('path',[])
    if not isinstance(path,list) or len(path)>20:raise ValueError('Output path must be a bounded list')
    for part in path:
        if isinstance(value,dict) and isinstance(part,str) and part in value:value=value[part]
        elif isinstance(value,list) and type(part) is int and 0<=part<len(value):value=value[part]
        else:raise ValueError('Missing selected output path')
    if value is None:raise ValueError('Selected output is unknown')
    return deepcopy(value)


class Environment:
    def __init__(self,evaluate,spec):
        self.evaluate=evaluate;self.spec=deepcopy(spec);canonical(spec)
        for field in ('actions','observations','reward','max_steps'):
            if field not in spec:raise ValueError('Missing environment specification: '+field)
        if not isinstance(spec['actions'],dict) or not isinstance(spec['observations'],dict) or not spec['observations']:
            raise ValueError('Named actions and nonempty observations required')
        limits=self.limits=resolve_limits()
        limits.check('environment_max_ports',max(len(spec['actions']),len(spec['observations'])),'Environment port budget exceeded')
        limits.integer('environment_max_steps',spec['max_steps'],'max_steps must be in 1..'+str(limits.environment_max_steps))
        self.spec.setdefault('max_evaluations',spec['max_steps']+1)
        if type(self.spec['max_evaluations']) is not int or self.spec['max_evaluations']<1:raise ValueError('Invalid evaluation budget')
        limits.check('environment_max_steps',self.spec['max_evaluations']-1,'Invalid evaluation budget')
        destinations=set()
        for descriptor in spec['actions'].values():
            if not {'binding','port','type','unit'}<=set(descriptor):raise ValueError('Action requires binding, port, type and unit')
            key=(descriptor['binding'],descriptor['port'])
            if key in destinations:raise ValueError('Duplicate action input destination')
            destinations.add(key)
            if descriptor['type']=='number':
                if _finite(descriptor['minimum'])>_finite(descriptor['maximum']):raise ValueError('Invalid action bounds')
            elif descriptor['type'] not in ('string','boolean','object','array','vector'):raise ValueError('Unsupported action type')
        terms=spec['reward'].get('terms')
        if not isinstance(terms,list):raise ValueError('Explicit bounded reward terms required')
        limits.check('environment_max_ports',len(terms),'Explicit bounded reward terms required')
        for term in terms:
            if term['mode'] not in ('value','delta'):raise ValueError('Unsupported reward mode')
            if _finite(term['scale'])<=0:raise ValueError('Reward normalization scale must be positive')
            _finite(term['weight'])
        self._history=[];self._result=None;self._done=False;self._evaluations=0;self._seed=0

    @property
    def history(self):return deepcopy(self._history)

    @property
    def materialization(self):
        """Privileged inspection; never implicitly included in agent observations/info."""
        return deepcopy(self._result)

    def _outputs(self,result):
        return {name:select_output(result,selector) for name,selector in self.spec['observations'].items()}

    def _terminated(self,result):
        rule=self.spec.get('termination')
        if not rule:return False
        value=_finite(select_output(result,rule['selector']));threshold=_finite(rule['value'])
        if rule['operator']=='gte':return value>=threshold
        if rule['operator']=='lte':return value<=threshold
        raise ValueError('Unsupported termination operator')

    def _reward(self,before,after):
        reward=0.0
        for term in self.spec['reward']['terms']:
            value=_finite(select_output(after,term['selector']))
            if term['mode']=='delta':value-=_finite(select_output(before,term['selector']))
            reward+=term['weight']*value/term['scale']
        return _finite(reward)

    def _call(self,history,seed):
        if self._evaluations>=self.spec['max_evaluations']:raise BudgetExceeded('Evaluation budget exhausted')
        self._evaluations+=1 # Failed evaluations also consume work budget.
        result=self.evaluate(deepcopy(history),seed)
        # Evaluators that bound and JSON-validate their own incremental output (CheckpointEvaluator)
        # skip re-encoding the whole growing result on every step (quadratic work over an episode).
        if not getattr(self.evaluate,'bounded_output',False):
            self.limits.check('environment_max_output_bytes',len(canonical(result)),'Materialization output exceeds environment limit')
        return result

    def reset(self,seed=0):
        if type(seed) is not int:raise ValueError('Seed must be integer')
        self._evaluations=0
        with self.evaluate.transaction() if hasattr(self.evaluate,'transaction') else nullcontext():
            result=self._call([],seed);observation=self._outputs(result)
            self._reward(result,result) # Validate reward selectors even before a transition.
            done=self._terminated(result)
        self._history=[];self._seed=seed;self._result=deepcopy(result);self._done=done or self._evaluations>=self.spec['max_evaluations']
        return observation,{'step':0,'terminated':done,'truncated':self._evaluations>=self.spec['max_evaluations'],'reward_unit':'normalized_score'}

    def step(self,actions):
        if self._result is None or self._done:raise ValueError('Reset required before stepping or after episode end')
        if not isinstance(actions,dict) or set(actions)!=set(self.spec['actions']):raise ValueError('Action names must exactly match declared inputs')
        self.limits.check('environment_max_action_bytes',len(canonical(actions)),'Action payload exceeds limit')
        inputs=[]
        for name,descriptor in self.spec['actions'].items():
            value=deepcopy(actions[name]);kind=descriptor['type']
            checks={'number':lambda x:type(x) in (int,float),'string':lambda x:isinstance(x,str),
                    'boolean':lambda x:type(x) is bool,'object':lambda x:isinstance(x,dict),
                    'array':lambda x:isinstance(x,list),'vector':lambda x:isinstance(x,list) and all(type(v) in (int,float) for v in x)}
            if not checks[kind](value):raise ValueError('Action type mismatch: '+name)
            if kind=='number' and not descriptor['minimum']<=_finite(value)<=descriptor['maximum']:raise ValueError('Action outside declared bounds')
            if 'choices' in descriptor and value not in descriptor['choices']:raise ValueError('Action outside declared choices')
            inputs.append({'binding':descriptor['binding'],'port':descriptor['port'],'value':value,'unit':descriptor['unit']})
        history=self._history+[{'inputs':inputs}]
        try:
            with self.evaluate.transaction() if hasattr(self.evaluate,'transaction') else nullcontext():
                result=self._call(history,self._seed)
                observation=self._outputs(result);reward=self._reward(self._result,result)
                terminated=self._terminated(result)
        except BudgetExceeded as error:
            self._done=True
            return self._outputs(self._result),0.0,False,True,{'step':len(self._history),'reward_unit':'normalized_score','truncation_reason':str(error)}
        truncated=len(history)>=self.spec['max_steps'] or self._evaluations>=self.spec['max_evaluations']
        self._history=history;self._result=deepcopy(result);self._done=terminated or truncated
        return observation,reward,terminated,truncated,{'step':len(history),'reward_unit':'normalized_score'}


class TemporalEvaluator:
    """Adapt any current temporal view with declared literal input ports by full-prefix replay.

Observed views support read-only trajectories (empty actions). To control a forecast,
expose process literal input ports. Group outputs work unchanged. External agent
backends are unsupported because replay would repeat their side effects/costs.
"""
    def __init__(self,store,graph_ref,request,step_seconds,registry=None,max_total_calls=10000):
        from .materialize import seconds
        from .model import instant
        self.store=store;self.graph_ref=deepcopy(graph_ref);self.request=deepcopy(request)
        self.step_seconds=seconds(step_seconds,'environment step_seconds');self.start=instant(request['start'])
        self.end=instant(request['end']);self.registry=registry
        resolve_limits().integer('materialize_max_calls',max_total_calls,'Invalid replay call budget')
        if request.get('interventions'):raise ValueError('Environment history owns intervention schedule')
        self.max_total_calls=max_total_calls;self.total_calls=0
        # Pin implementation/cadence once; cost-based selection must not change with prefix length.
        from .materialize import _request, _plan
        from .process_library import default_registry
        self.registry=deepcopy(registry) if registry is not None else default_registry()
        template={**self.request,'step_seconds':self.step_seconds}
        parsed,start,finish,_,targets=_request(template)
        bindings,_,_,_=_plan(self.registry,parsed,targets,(finish-start).total_seconds(),None)
        pinned={b['id']:b for b in bindings}
        for binding in self.request.get('bindings',[]):
            if binding['id'] in pinned:
                selected=pinned[binding['id']]
                if round(self.step_seconds*1000000)%round(selected['cadence_seconds']*1000000):
                    raise ValueError('Environment step must align with every active process cadence')
                binding['implementation_id']=selected['implementation']['id']
                binding['cadence_seconds']=selected['cadence_seconds']

    def __call__(self,history,seed):
        from .materialize import materialize, _request, _plan
        from .process_library import default_registry
        if not history:self.total_calls=0
        end=self.start+timedelta(seconds=len(history)*self.step_seconds)
        if end>self.end:raise ValueError('Environment exceeds materialization horizon')
        request=deepcopy(self.request)
        request.update(end=end.isoformat(),step_seconds=self.step_seconds,seed=seed)
        request['interventions']=[{'time':(self.start+timedelta(seconds=i*self.step_seconds)).isoformat(),**port}
                                  for i,item in enumerate(history) for port in item['inputs']]
        registry=self.registry or default_registry()
        parsed,start,finish,_,targets=_request(request)
        bindings,_,plan,_=_plan(registry,parsed,targets,(finish-start).total_seconds(),None)
        if any(b['implementation']['fidelity']=='agent' for b in bindings):raise ValueError('Replay adapter cannot execute live agent backends')
        # _plan preflights process calls; reserve before invoking the materializer.
        calls=sum((round((finish-start).total_seconds()*1000000)+round(b['cadence_seconds']*1000000)-1)//round(b['cadence_seconds']*1000000) for b in bindings)
        if self.total_calls+calls>self.max_total_calls:raise BudgetExceeded('Cumulative environment replay call budget exceeded')
        self.total_calls+=calls
        return materialize(self.store,self.graph_ref,request,registry=registry)


# Stateful alternative; TemporalEvaluator remains the replay reference.
from .checkpoints import CheckpointEvaluator
