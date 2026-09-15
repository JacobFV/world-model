"""Declarative world schema; capabilities are distinct from acquired evidence."""
from collections import Counter
from copy import deepcopy

PARENTS = {'entity': None, 'agent': 'entity', 'person': 'agent', 'household': 'agent',
 'organization': 'agent', 'business': 'organization', 'institution': 'organization',
 'government_agency': 'institution', 'political_committee': 'organization',
 'investment_fund': 'organization', 'aggregate_cohort': 'entity',
 'business_cohort': 'aggregate_cohort', 'population_cohort': 'aggregate_cohort',
 'asset': 'entity', 'facility': 'asset', 'infrastructure': 'facility', 'road': 'infrastructure',
 'airport': 'facility', 'port': 'facility', 'vehicle': 'asset', 'aircraft': 'vehicle',
 'private_jet': 'aircraft', 'vessel': 'vehicle', 'resource': 'entity', 'commodity': 'resource',
 'oil': 'commodity', 'electricity': 'commodity', 'mineral': 'commodity',
 'resource_deposit': 'resource', 'location': 'entity', 'jurisdiction': 'location',
 'country': 'jurisdiction', 'state': 'jurisdiction', 'county': 'jurisdiction',
 'industry': 'entity', 'product': 'entity', 'occupation': 'entity', 'account': 'asset',
 'security': 'asset', 'contract': 'entity', 'award': 'contract', 'law': 'entity',
 'office': 'entity', 'technology': 'entity', 'process_type': 'entity',
 'flow': 'entity', 'resource_flow': 'flow', 'investment': 'flow', 'shipment': 'flow', 'flight': 'entity',
 'pipeline': 'infrastructure', 'refinery': 'facility', 'oil_field': 'resource_deposit'}
ENTITY_TYPES = set(PARENTS)
RELATIONS = {name: {'domain': domain, 'range': target} for name, domain, target in [
 ('within','entity','location'), ('classified_as','aggregate_cohort','industry'),
 ('contains_resource','resource_deposit','commodity'), ('measures_resource','aggregate_cohort','commodity'),
 ('awarded_by','award','government_agency'), ('awarded_to','award','organization'),
 ('registered_in','organization','jurisdiction'), ('affiliated_with','organization','organization'),
 ('supports_candidate','political_committee','person'), ('owns','agent',['asset','organization']),
 ('controls','agent','entity'), ('operates','agent',['asset','process_type']),
 ('located_in','entity',['location','facility']), ('produces',['agent','facility'],['resource','product']),
 ('consumes',['agent','facility'],['resource','product']), ('employed_by','person','organization'),
 ('transports',['vehicle','shipment'],['resource','product']), ('shipment_origin','shipment',['location','facility']),
 ('shipment_destination','shipment',['location','facility']), ('carried_by','shipment','vehicle'),
 ('supplied_by','resource_flow',['agent','facility']), ('received_by','resource_flow',['agent','facility']),
 ('invests_in','agent','entity'), ('investment_source','investment','agent'),
 ('investment_target','investment','entity'), ('operates_aircraft','agent','aircraft'),
 ('uses_aircraft','flight','aircraft'), ('origin','flight',['location','airport']), ('destination','flight',['location','airport']),
 ('flow_source','flow','entity'), ('flow_destination','flow','entity'),
 ('transports_resource','resource_flow','commodity'), ('member_of','agent','aggregate_cohort') ]}
VARIABLES = {}
def _v(name, unit, domain='entity', value_type='number'):
    VARIABLES[name] = {'type': value_type, 'unit': unit, 'domain': domain}
for name, unit, domain in [('population','people','location'), ('employment','people','business_cohort'),
 ('establishment_count','establishments','business_cohort'), ('annual_payroll','thousand_USD','business_cohort'),
 ('first_quarter_payroll','thousand_USD','business_cohort'), ('unemployment_rate','percent','population_cohort'),
 ('electricity_sales','million kilowatt hours','aggregate_cohort'), ('award_amount','USD','award'),
 ('latitude','degrees','entity'), ('longitude','degrees','entity'), ('altitude','m','aircraft'),
 ('speed','m/s','vehicle'), ('heading','degrees','vehicle'), ('inventory','barrel','resource'),
 ('inflow','barrel/second','resource'), ('outflow','barrel/second','resource'),
 ('cash','USD','agent'), ('revenue','USD/second','agent'), ('expenditure','USD/second','agent'), ('investment_amount','USD','investment'), ('ownership_fraction','fraction','agent')]:
    _v(name,unit,domain)
_v('position','km','entity','vector')
_v('velocity','km/second','entity','vector')
_v('flight_position','degrees','aircraft','vector')
_v('development_status','category','resource_deposit','string')
_v('legal_status','category','organization','string')
# None denotes a unitless process/scenario port; observed source categories keep 'category'.
_v('decision',None,'agent','string')
_v('action',None,'agent','string')
_v('current_action',None,'agent','string')
_v('goals',None,'agent','array')
_v('context',None,'agent','object')
_v('enabled',None,'agent','boolean')
_v('memory',None,'agent','object')


# Domain extensions expose declarative schema only; imports perform no acquisition.
from .strategic_sources import schema as _source_schema
from .banking import schema as _banking_schema
from .fields import schema as _field_schema
from .actor_kernels import schema as _actor_schema
from .institutional_schema import schema as _institutional_schema
from .exposure import schema as _exposure_schema
from .identity import schema as _identity_schema
from .market_sources import schema as _market_schema
from .people_sources import schema as _people_schema
for _extension in (_exposure_schema(), _identity_schema(), _market_schema(), _people_schema(), _source_schema(), _banking_schema(), _field_schema(), _actor_schema(), _institutional_schema()):
    for _name, _descriptor in _extension['entity_types'].items():
        if _name in PARENTS and PARENTS[_name] != _descriptor['parent']:
            raise ValueError('Conflicting entity hierarchy: ' + _name)
        PARENTS[_name] = _descriptor['parent']
    for _target, _field in ((RELATIONS, 'relations'), (VARIABLES, 'variables')):
        for _name, _descriptor in _extension[_field].items():
            if _name in _target and _target[_name] != _descriptor:
                raise ValueError('Conflicting ontology definition: ' + _name)
            _target[_name] = _descriptor
PARENTS['scenario'] = 'entity'
ENTITY_TYPES.update(PARENTS)
_v('economy_state', None, 'entity', 'object')
_v('banking_state', None, 'entity', 'object')
_v('fields_state', None, 'entity', 'object')

def is_a(actual, expected):
    if isinstance(expected, (list, tuple)):
        return any(is_a(actual, parent) for parent in expected)
    while actual is not None:
        if actual == expected: return True
        actual = PARENTS.get(actual)
    return False

def describe():
    return deepcopy({'schema_version': 1, 'entity_types': {k: {'parent': v} for k,v in PARENTS.items()},
                     'relations': RELATIONS, 'variables': VARIABLES,
                     'limitations': ['Schema declarations do not imply observations, ownership or predictive validation.']})

def validate_typed_graph(records):
    from .model import validate_record
    entities, ids = {}, set()
    for r in records:
        validate_record(r)
        if r['id'] in ids: raise ValueError('Duplicate record ID: ' + r['id'])
        ids.add(r['id'])
        if r['kind'] == 'entity':
            key, typ = r.get('entity_id', r['id']), r['entity_type']
            old = entities.get(key)
            if old and not (is_a(old,typ) or is_a(typ,old)):
                raise ValueError('Incompatible entity types for ' + key)
            entities[key] = typ if not old or is_a(typ,old) else old
            entities[r['id']] = typ
    def endpoint(key):
        if key not in entities: raise ValueError('Unresolved entity reference: ' + str(key))
        return entities[key]
    for r in records:
        if r['kind'] == 'observation' or (r['kind'] == 'assertion' and 'value' in r):
            typ = endpoint(r.get('subject'))
            name = r.get('metric',r.get('predicate'))
            if name not in VARIABLES: raise ValueError('Undeclared variable: ' + name)
            spec = VARIABLES[name]
            if not is_a(typ,spec['domain']): raise ValueError('Variable domain mismatch: ' + name)
            if r.get('unit',spec['unit']) != spec['unit']: raise ValueError('Variable unit mismatch: ' + name)
            value = r['value']
            checks = {'number': lambda v: isinstance(v,(int,float)) and not isinstance(v,bool),
                      'boolean': lambda v: isinstance(v,bool), 'string': lambda v: isinstance(v,str), 'vector': lambda v: isinstance(v,list) and all(isinstance(x,(int,float)) and not isinstance(x,bool) for x in v),
                      'array': lambda v: isinstance(v,list), 'object': lambda v: isinstance(v,dict)}
            if value is not None and not checks[spec['type']](value): raise ValueError('Variable type mismatch: ' + name)
        elif r['kind'] == 'assertion':
            name = r['predicate']
            if name not in RELATIONS: raise ValueError('Undeclared relation: ' + name)
            spec = RELATIONS[name]
            if not is_a(endpoint(r['subject']),spec['domain']): raise ValueError('Relation domain mismatch: ' + name)
            if not is_a(endpoint(r['object']),spec['range']): raise ValueError('Relation range mismatch: ' + name)
        elif r['kind'] == 'event':
            for key in r['participants']: endpoint(key)
    counts = Counter(r['kind'] for r in records)
    return {'records': len(records), 'entities': len({r.get('entity_id',r['id']) for r in records if r['kind']=='entity'}),
            'relations': sum(r['kind']=='assertion' and 'object' in r for r in records), 'counts': dict(counts)}
