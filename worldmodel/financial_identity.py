"""Explicit financial crosswalk candidates; issuer, security and listing stay distinct."""
from copy import deepcopy
from .identity import _normalize, _scope


def schema():
    return {'entity_types':{},'relations':{
        'issuer_security':{'domain':'organization','range':'security'},
        'listing_security':{'domain':'ticker_listing','range':'security'}},'variables':{
        'security_price_quote':{'domain':'security','unit':None,'type':'object'},
        'corporate_action_terms':{'domain':'security','unit':None,'type':'object'},
        'contract_review':{'domain':'financial_obligation','unit':None,'type':'object'}}}


def _interval(row):
    from .model import instant
    instant(row['observed_at'])
    for k in ('valid_from','valid_to'):
        if k not in row:raise ValueError('Explicit validity bounds or null required')
        if row[k] is not None:instant(row[k])
    if row['valid_from'] and row['valid_to'] and instant(row['valid_from'])>=instant(row['valid_to']):raise ValueError('Invalid interval')
    if not isinstance(row.get('evidence'),list) or not row['evidence']:raise ValueError('Crosswalk evidence required')


def _visible(row,at,known):
    from .model import instant
    return instant(row['observed_at'])<=known and (row['valid_from'] is None or instant(row['valid_from'])<=at) and (row['valid_to'] is None or at<instant(row['valid_to']))


class FinancialIdentityIndex:
    """Bounded read-only candidate index, with explicit optional review decisions.

    source_local identifiers require a snapshot-specific scope. A ticker requires
    venue MIC scope and quote currency. Unknown dates never become confirmed links.
    Equivalence links are evidence for review, not automatic component unions.
    """
    def __init__(self,entities,assignments,links=()):
        from .model import identifier
        if len(entities)>10000 or len(assignments)>10000 or len(links)>10000:raise ValueError('Financial identity budget exceeded')
        self.entities={};self.assignments=deepcopy(list(assignments));self.links=deepcopy(list(links))
        for e in entities:
            if set(e)!={'id','kind','label'} or e['kind'] not in ('issuer','security','listing') or not isinstance(e['label'],str) or not e['label']:raise ValueError('Invalid financial entity')
            identifier(e['id'])
            if e['id'] in self.entities:raise ValueError('Duplicate entity')
            self.entities[e['id']]=deepcopy(e)
        kinds={'sec_cik':'issuer','lei':'issuer','isin':'security','figi':'security','ticker':'listing'}
        for a in self.assignments:
            if set(a)-{'entity_id','namespace','value','scope','currency','valid_from','valid_to','observed_at','evidence'}:raise ValueError('Unknown assignment field')
            if a.get('entity_id') not in self.entities:raise ValueError('Missing assignment entity')
            ns,_=_normalize(a['namespace'],a['value']);_scope(a.get('scope'));_interval(a)
            if ns not in {*kinds,'source_local'}:raise ValueError('Unsupported financial identifier namespace')
            if ns in kinds and self.entities[a['entity_id']]['kind']!=kinds[ns]:raise ValueError('Identifier belongs to another entity kind')
            if ns in ('ticker','source_local') and not a.get('scope'):raise ValueError('Venue or snapshot scope required')
            if ns=='ticker':
                import re
                if not re.fullmatch('[A-Z0-9]{4}',a['scope']) or not re.fullmatch('[A-Z]{3}',a.get('currency','')):raise ValueError('Ticker requires explicit MIC and currency')
        contracts={'issuer_security':('issuer','security'),'listing_security':('listing','security'),
                   'successor':('issuer','issuer'),'share_class_change':('security','security')}
        for link in self.links:
            if set(link)!={'kind','subject','object','valid_from','valid_to','observed_at','evidence'}:raise ValueError('Invalid relationship fields')
            _interval(link)
            if link['subject'] not in self.entities or link['object'] not in self.entities:raise ValueError('Unresolved link endpoint')
            pair=tuple(self.entities[link[k]]['kind'] for k in ('subject','object'))
            if link['kind']=='equivalent':
                if pair[0]!=pair[1]:raise ValueError('Equivalence must specify the same entity kind')
            elif contracts.get(link['kind'])!=pair:raise ValueError('Invalid financial relationship kinds')

    def resolve(self,namespace,value,*,at,known_at,scope=None,decision=None):
        from .model import instant
        ns,val=_normalize(namespace,value);at_time,known=instant(at),instant(known_at)
        if ns in ('ticker','source_local') and not scope:raise ValueError('Explicit venue or snapshot scope required')
        groups={}
        for row in self.assignments:
            if _normalize(row['namespace'],row['value'])==(ns,val) and (scope is None or _scope(row.get('scope'))==_scope(scope)) and _visible(row,at_time,known):groups.setdefault(row['entity_id'],[]).append(row)
        candidates=[{'entity':deepcopy(self.entities[key]),'assignments':deepcopy(rows),
                     'temporal_validity':'confirmed' if all(r['valid_from'] and r['valid_to'] for r in rows) else 'unknown'} for key,rows in sorted(groups.items())]
        status='not_found' if not candidates else 'ambiguous' if len(candidates)>1 else 'temporal_unknown' if candidates[0]['temporal_validity']=='unknown' else 'resolved'
        conflicts=[]
        for entity_id,matched in groups.items():
            others=[r for r in self.assignments if r['entity_id']==entity_id and _normalize(r['namespace'],r['value'])[0]==ns
                    and any(_scope(r.get('scope'))==_scope(m.get('scope')) for m in matched) and _visible(r,at_time,known)
                    and (_normalize(r['namespace'],r['value'])[1]!=val or any(r.get('currency')!=m.get('currency') for m in matched))]
            if others:conflicts.append({'entity_id':entity_id,'reason':'overlapping_identifier_assignments','assignments':deepcopy(matched+others)})
        if conflicts:status='conflicting_assignments'
        selected=candidates[0]['entity']['id'] if status=='resolved' else None
        if decision is not None:
            if set(decision)!={'selected','reviewer','reason'} or decision['selected'] not in groups or not all(isinstance(decision[k],str) and decision[k].strip() for k in decision):raise ValueError('Review must select an existing candidate with reviewer and reason')
            selected=decision['selected'];status='reviewed'
        return {'namespace':ns,'value':val,'scope':scope,'at':at,'known_at':known_at,'status':status,
                'selected':selected,'candidates':candidates,'conflicts':conflicts,'decision':deepcopy(decision),'policy':'explicit dated candidates; never merge by name or successor'}

    def search(self,name,*,kind=None):
        if not isinstance(name,str) or kind not in (None,'issuer','security','listing'):raise ValueError('Invalid search')
        return deepcopy([e for e in self.entities.values() if name.casefold() in e['label'].casefold() and (kind is None or e['kind']==kind)])

    def relationships(self,entity_id,*,at,known_at):
        from .model import instant
        return deepcopy([r for r in self.links if r['subject']==entity_id and _visible(r,instant(at),instant(known_at))])
