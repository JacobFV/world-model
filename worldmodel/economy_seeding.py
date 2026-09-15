"""Explicit recent-observation parameter seeding, with no inferred causality."""
from copy import deepcopy
from .model import instant
from .util import canonical


def seed_economy(config, records, as_of, known_at, max_age_days=7):
    if type(max_age_days) is not int or not 0<=max_age_days<=365:
        raise ValueError('max_age_days must be in 0..365')
    if config.get('energy_unit') not in (None, 'barrel'):
        raise ValueError('energy_unit must be barrel for WTI seeding; quantities are never silently converted')
    at,known=instant(as_of),instant(known_at)
    result=deepcopy(config)
    seeds=[]
    for metric,unit in [('policy_rate','percent'),('oil_price','USD/barrel')]:
        available=[]
        for row in records:
            if row.get('kind')!='observation' or row.get('metric')!=metric or row.get('value') is None:
                continue
            if row.get('epistemic_status','observed')!='observed' or not row.get('valid_from'):
                continue
            age=(at-instant(row['valid_from'])).total_seconds()/86400
            if 0<=age<=max_age_days and instant(row['observed_at'])<=known:
                available.append(row)
        if not available:
            raise ValueError(f'No recent observed {metric} available by the requested time and knowledge cutoff')
        last=max(instant(row['valid_from']) for row in available)
        selected=[row for row in available if instant(row['valid_from'])==last]
        if len({canonical([row['value'],row['unit']]) for row in selected})!=1:
            raise ValueError('Conflicting source values require explicit reconciliation')
        row=selected[0]
        if row['unit']!=unit:
            raise ValueError('Unexpected source unit for '+metric)
        if metric=='policy_rate':
            bank=result.setdefault('bank',{})
            spread=bank.get('credit_spread',0)
            if type(spread) not in (int,float) or spread<0:
                raise ValueError('credit_spread must be a nonnegative annual fraction')
            value=row['value']/100+spread
            bank['annual_rate']=value
            target='bank.annual_rate'
        else:
            value=row['value']; result['energy_price']=value;target='energy_price'
        seeds.append({'metric':metric,'record_id':row['id'],'evidence':row.get('evidence',[]),
                      'observed_value':row['value'],'unit':unit,'valid_from':row['valid_from'],
                      'target':target,'value':value,'as_of':as_of,'known_at':known_at})
    result['energy_unit']='barrel'
    canonical(result)
    return {'config':result,'seeds':seeds,'assumptions':[
        'Energy quantity is barrels and price is the sampled WTI spot benchmark, not a delivered firm-specific quote.',
        'Loan rate is sampled federal funds rate plus explicit credit_spread; this pass-through is assumed, not fitted.',
        'Latest observations within the allowed age are held at the scenario start; this is an explicit carry assumption.',
        'Initial firms, balance sheets and behavior remain synthetic; revisions use the acquired vintage.']}
