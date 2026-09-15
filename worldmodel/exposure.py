"""Bounded USD obligation stress with proportional net clearing, not default forecasting."""
from datetime import date
import math


def stress_exposures(config):
    def number(value, nonnegative=True):
        if isinstance(value, bool) or not isinstance(value, (float,int)) or not math.isfinite(value) or (nonnegative and value<0):
            raise ValueError('Expected finite '+('nonnegative ' if nonnegative else '')+'number')
        return float(value)
    start, end = date.fromisoformat(config['as_of']), date.fromisoformat(config['end'])
    if not start<end or (end-start).days>3650: raise ValueError('Horizon must be positive and at most ten years')
    if config.get('currency')!='USD': raise ValueError('Only explicit USD obligations supported; no implicit FX conversion')
    actors, obligations = config['entities'], config['obligations']
    if not 1<=len(actors)<=1000 or len(obligations)>10000: raise ValueError('Exposure size budget exceeded')
    ids=[r['id'] for r in actors]
    if any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=len(ids):raise ValueError('Unique entity IDs required')
    cash={r['id']:number(r['cash']) for r in actors}
    shock=number(config['shock_bps'],False)/10000
    prepared=[]; seen=set()
    for row in obligations:
        if not isinstance(row['id'],str) or not row['id'] or row['id'] in seen:raise ValueError('Unique obligation IDs required')
        seen.add(row['id'])
        if row['borrower'] not in cash or row['lender'] not in cash or row['borrower']==row['lender']:
            raise ValueError('Distinct borrower and lender must have explicit cash state')
        if row.get('currency',config['currency'])!='USD':raise ValueError('Obligation currency mismatch')
        maturity=date.fromisoformat(row['maturity'])
        if maturity<=start:raise ValueError('Overdue obligations require explicit arrears treatment')
        if row['rate_type'] not in ('fixed','floating'):raise ValueError('Unknown rate type')
        principal, rate=number(row['principal']),number(row['annual_rate'])
        stressed=number(rate+(shock if row['rate_type']=='floating' else 0))
        fraction=(min(end,maturity)-start).days/365
        principal_due=principal if maturity<=end else 0
        prepared.append({**row,'baseline_due':principal_due+principal*rate*fraction,
                         'stressed_due':principal_due+principal*stressed*fraction,
                         'principal_due':principal_due})
    def clear(mode):
        due={key:0.0 for key in ids}
        for row in prepared:due[row['borrower']]+=row[mode+'_due']
        if not all(math.isfinite(v) for v in due.values()) or not math.isfinite(sum(cash.values())):
            raise ValueError('Aggregate financial state overflow')
        paid=due.copy(); work=0
        tolerance=1e-9
        for iteration in range(10000):
            work+=len(prepared)+len(ids)
            if work>2_000_000:raise ValueError('Clearing work budget exceeded before convergence')
            incoming={key:0.0 for key in ids}
            for row in prepared:
                borrower=row['borrower']
                incoming[row['lender']]+=paid[borrower]*(row[mode+'_due']/due[borrower] if due[borrower] else 0)
            updated={key:min(due[key],cash[key]+incoming[key]) for key in ids}
            error=max(abs(updated[key]-paid[key]) for key in ids)
            paid=updated
            if error<=tolerance:break
        else:raise ValueError('Clearing failed to converge')
        incoming={key:0.0 for key in ids}; loans=[]
        for row in prepared:
            borrower=row['borrower']; liability=row[mode+'_due']
            payment=paid[borrower]*(liability/due[borrower] if due[borrower] else 0)
            incoming[row['lender']]+=payment
            loans.append({'id':row['id'],'borrower':borrower,'lender':row['lender'],
                          'due':liability,'principal_due':row['principal_due'],'paid':payment,'shortfall':liability-payment})
        state=[{'id':key,'due':due[key],'paid':paid[key],'received':incoming[key],
                'ending_cash':cash[key]+incoming[key]-paid[key],'shortfall':due[key]-paid[key]} for key in ids]
        residual=sum(r['ending_cash'] for r in state)-sum(cash.values())
        if min(r['ending_cash'] for r in state)<-tolerance*2 or abs(residual)>max(tolerance*len(ids),abs(sum(cash.values()))*1e-12):
            raise ValueError('Clearing conservation or nonnegative cash check failed')
        return {'entities':state,'obligations':loans,'iterations':iteration+1,
                'total_shortfall':sum(r['shortfall'] for r in loans),'cash_conservation_residual':residual}
    baseline, stressed=clear('baseline'),clear('stressed')
    return {'baseline':baseline,'stressed':stressed,'incremental_shortfall':stressed['total_shortfall']-baseline['total_shortfall'],
            'epistemic_status':'synthetic_scenario','causally_calibrated':False,
            'assumptions':['Supplied obligations and initial cash are scenario assumptions.',
                           'ACT/365 simple interest; floating shock applies immediately over entire horizon.',
                           'One terminal proportional net settlement; cyclic obligations may offset without gross cash.',
                           'No collateral priority, bankruptcy recovery, interim payments, FX, or new funding.',
                           'Liquidity shortfall is not a calibrated default probability.']}


def schema():
    return {'entity_types':{'financial_obligation':{'parent':'contract'}},'relations':{},
            'variables':{'obligation_terms':{'type':'object','unit':None,'domain':'financial_obligation'}}}


def exposure_from_evidence(store, graph_ref, request):
    """Select explicitly pinned dated records from a verified graph; never infer missing terms."""
    from .model import instant
    from .ontology import is_a
    rows=list(store.records(graph_ref))  # Store.records verifies the complete artifact lineage.
    by_id={r['id']:r for r in rows}
    entities={r.get('entity_id',r['id']):r for r in rows if r['kind']=='entity' and r.get('epistemic_status') in (None,'observed')}
    at,known=instant(request['as_of']),instant(request['known_at'])
    evidence=[]
    def select(record_id,subject,metric,unit,domain):
        record=by_id.get(record_id)
        entity=entities.get(subject)
        if not entity or not is_a(entity['entity_type'],domain):raise ValueError('Missing or incompatible evidence entity: '+subject)
        if not record or record.get('kind') not in ('observation','assertion') or record.get('subject')!=subject or record.get('metric',record.get('predicate'))!=metric:
            raise ValueError('Pinned evidence subject or metric mismatch')
        if record.get('unit')!=unit or record.get('value') is None:raise ValueError('Missing state or incompatible evidence unit')
        if record.get('epistemic_status') not in (None,'observed'):raise ValueError('Scenario state cannot seed observed obligations')
        if not record.get('valid_from') or not record.get('valid_to') or not instant(record['valid_from'])<=at<instant(record['valid_to']) or instant(record['observed_at'])>known:
            raise ValueError('Evidence must explicitly cover as_of and be available by known_at')
        evidence.append({'input':graph_ref,'record_id':record_id})
        return record['value']
    actors=[{'id':r['id'],'cash':select(r['cash_record'],r['id'],'cash','USD','agent')} for r in request['entities']]
    loans=[]
    required={'borrower','lender','principal','annual_rate','rate_type','maturity','currency'}
    for row in request['obligations']:
        terms=select(row['terms_record'],row['id'],'obligation_terms',None,'financial_obligation')
        if not isinstance(terms,dict) or set(terms)!=required:raise ValueError('Obligation terms require exact documented fields')
        loans.append({**terms,'id':row['id']})
    config={key:request[key] for key in ('as_of','end','currency','shock_bps')}
    result=stress_exposures({**config,'entities':actors,'obligations':loans})
    result['initial_state_evidence']=evidence
    result['initial_state_origin']='pinned observed records; forecast shock and settlement remain scenario assumptions'
    result['assumptions'][0]='Cash and obligation terms are selected from explicit dated evidence; completeness of the obligation network is unknown.'
    return result
