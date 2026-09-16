"""Append-only, bitemporal actor lifecycle reconstruction from explicit event claims.

Events are immutable inputs. Conflicting simultaneous transitions are rejected rather
than resolved by record order. Mergers end predecessors and create a declared successor.
"""
from copy import deepcopy
import math
from .limits import resolve_limits
from .model import identifier, instant
from .ontology import is_a
from .util import canonical, digest

TYPES={'birth','incorporation','growth','merge','death','dissolution'}

def _number(value,name):
    if type(value) not in (float,int) or not math.isfinite(value):raise ValueError(name+' must be finite numeric')
    return value

def materialize_lifecycle(config, at, known_at, *, limits=None):
    """Reconstruct actor states; sizes bounded by lifecycle_max_entities/lifecycle_max_events."""
    limits=resolve_limits(limits)
    canonical(config)
    if set(config)-{'entities','events','conserve_size','description'}:raise ValueError('Unknown lifecycle config field')
    if not isinstance(config.get('entities'),list) or not isinstance(config.get('events'),list):raise ValueError('entities/events must be lists')
    limits.check('lifecycle_max_entities',len(config['entities']),'Lifecycle budget exceeded: entities')
    limits.check('lifecycle_max_events',len(config['events']),'Lifecycle budget exceeded: events')
    if type(config.get('conserve_size',True)) is not bool:raise ValueError('conserve_size must be boolean')
    at_time,known_time=instant(at),instant(known_at)
    states={}
    for entity in config['entities']:
        key=entity['id'];identifier(key)
        if key in states:raise ValueError('Duplicate entity: '+key)
        typ=entity['entity_type']
        if not is_a(typ,'agent'):raise ValueError('Lifecycle entity must be an actor')
        if not isinstance(entity.get('label'),str) or not entity['label']:raise ValueError('Entity label required')
        states[key]={'entity_type':typ,'label':entity['label'],'status':'not_started','valid_from':None,
                     'valid_to':None,'size':None,'successors':[],'history':[]}
    events=[]; ids=set();pending=[]
    for event in config['events']:
        identifier(event['id'])
        if event['id'] in ids:raise ValueError('Duplicate event identifier')
        ids.add(event['id'])
        if event.get('type') not in TYPES:raise ValueError('Unsupported lifecycle event')
        occurred,observed=instant(event['occurred_at']),instant(event['observed_at'])
        if observed<occurred:raise ValueError('Observed lifecycle event cannot precede occurrence; use scenario projection time')
        if event['type']=='merge':
            sources=event.get('sources')
            if not isinstance(sources,list) or len(sources)<2 or len(set(sources))!=len(sources):raise ValueError('Merge needs distinct source actors')
            affected=sources+[event['target']]
            if event['target'] in sources:raise ValueError('Merge successor must differ from predecessors')
        else:affected=[event['entity']]
        if any(key not in states for key in affected):raise ValueError('Unknown lifecycle actor')
        if observed<=known_time:
            if occurred<=at_time:events.append((occurred,deepcopy(event),affected))
            else:pending.append(deepcopy(event))
    events.sort(key=lambda e:(e[0],e[1]['id']))
    transitions=set();history=[]
    for occurred,event,affected in events:
        for key in affected:
            marker=(key,occurred)
            if marker in transitions:raise ValueError('Conflicting simultaneous lifecycle claims for '+key)
            transitions.add(marker)
        kind=event['type']; time=event['occurred_at']
        def active(key):
            if states[key]['status']!='active':raise ValueError('Lifecycle actor inactive: '+key)
        def organization(key):
            if not is_a(states[key]['entity_type'],'organization'):raise ValueError('Transition requires organization')
        if kind in ('birth','incorporation'):
            key=event['entity'];state=states[key]
            if state['status']!='not_started':raise ValueError('Actor already started or ended')
            if kind=='birth' and not is_a(state['entity_type'],'person'):raise ValueError('Birth requires person')
            if kind=='incorporation':organization(key)
            size=event.get('size')
            if size is not None and _number(size,'size')<0:raise ValueError('size must be nonnegative')
            state.update(status='active',valid_from=time,size=size)
        elif kind=='growth':
            key=event['entity'];active(key);state=states[key]
            if state['size'] is None:raise ValueError('Growth requires declared initial size')
            size=state['size']+_number(event['delta'],'delta')
            if size<0:raise ValueError('Growth produces negative size')
            state['size']=size
        elif kind in ('death','dissolution'):
            key=event['entity'];active(key)
            if kind=='death' and not is_a(states[key]['entity_type'],'person'):raise ValueError('Death requires person')
            if kind=='dissolution':organization(key)
            states[key].update(status='ended',valid_to=time)
        else:
            target=event['target'];organization(target)
            if states[target]['status']!='not_started':raise ValueError('Merge target already started')
            for key in event['sources']:organization(key);active(key)
            sizes=[states[key]['size'] for key in event['sources']]
            if config.get('conserve_size',True):
                if any(v is None for v in sizes):raise ValueError('Conservation requires declared predecessor sizes')
                total=sum(sizes)
                if 'size' in event and not math.isclose(_number(event['size'],'size'),total,rel_tol=1e-12,abs_tol=1e-12):raise ValueError('Merge must conserve declared additive size')
            else:
                if 'size' not in event:raise ValueError('Nonconserving merge requires explicit size')
                total=_number(event['size'],'size')
                if total<0:raise ValueError('size must be nonnegative')
            states[target].update(status='active',valid_from=time,size=total)
            for key in event['sources']:states[key].update(status='ended',valid_to=time,successors=[target])
        for key in affected:states[key]['history'].append(event['id'])
        history.append(event)
    result={'at':at,'known_at':known_at,'entities':states,'history':history,'pending_known_events':pending,
            'status':'explicit_event_reconstruction','config_hash':digest(config),
            'assumptions':['Only declared actors and events are reconstructed; absence of death evidence does not establish survival.',
                           'Additive size is a user-declared count, not inferred wealth, employment, or biomass.',
                           'Simultaneous overlapping transitions require reconciliation before materialization.']}
    canonical(result)
    return result

def actor_eligible(result,entity_id):
    if entity_id not in result['entities']:raise ValueError('Unknown lifecycle actor: '+entity_id)
    return result['entities'][entity_id]['status']=='active'

def require_actor_eligible(result,entity_id):
    if not actor_eligible(result,entity_id):raise ValueError('Lifecycle actor inactive: '+entity_id)
    return result['entities'][entity_id]

def graph_records(result,evidence):
    if not evidence:raise ValueError('Lifecycle projection requires input evidence')
    base={'observed_at':result['known_at'],'evidence':deepcopy(evidence),
          'attributes':{'epistemic_basis':'explicit_lifecycle_claims','at':result['at'],'config_hash':result['config_hash']}}
    for key,state in result['entities'].items():
        if state['status']=='not_started':continue
        entity={**deepcopy(base),'kind':'entity','id':'lifecycle:'+digest([result['config_hash'],key,result['at'],result['known_at']]),
                'entity_id':key,'entity_type':state['entity_type'],'label':state['label'],'valid_from':state['valid_from']}
        if state['valid_to']:entity['valid_to']=state['valid_to']
        entity['attributes'].update(lifecycle_status=state['status'],lifecycle_size=state['size'],history=state['history'])
        yield entity
        for successor in state['successors']:
            yield {**deepcopy(base),'kind':'assertion','id':'lifecycle:'+digest([key,successor,result['config_hash']]),
                   'subject':successor,'predicate':'successor_of','object':key,'valid_from':state['valid_to']}
    for event in result['history']:
        participants=event['sources']+[event['target']] if event['type']=='merge' else [event['entity']]
        yield {**deepcopy(base),'kind':'event','id':'lifecycle:'+digest([result['config_hash'],event['id']]),
               'event_type':event['type'],'occurred_at':event['occurred_at'],'observed_at':event['observed_at'],
               'participants':participants,'attributes':{**base['attributes'],'source_event':event}}
