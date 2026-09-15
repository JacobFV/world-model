"""Audited selection over dated claims; source records remain immutable."""
from copy import deepcopy
from .model import instant
from .util import canonical


def reconcile_claims(records,selector,at,known_at,policy='retain',source_priority=()):
    if policy not in ('retain','latest','source_priority'):raise ValueError('Unsupported reconciliation policy')
    if not isinstance(selector,dict) or not {'entity','variable'}<=set(selector) or set(selector)-{'entity','variable','unit','dimensions'}:raise ValueError('Unsupported reconciliation selector')
    if 'dimensions' in selector and not isinstance(selector['dimensions'],dict):raise ValueError('Selector dimensions must be an object')
    at,known=instant(at),instant(known_at)
    candidates=[r for r in records if r.get('kind') in ('observation','assertion') and 'value' in r
                and r.get('subject')==selector['entity'] and r.get('metric',r.get('predicate'))==selector['variable']
                and ('unit' not in selector or r.get('unit')==selector['unit'])
                and all(r.get('dimensions',{}).get(k)==v and k in r.get('dimensions',{}) for k,v in selector.get('dimensions',{}).items())
                and r.get('epistemic_status') in (None,'observed') and instant(r['observed_at'])<=known
                and (not r.get('valid_from') or instant(r['valid_from'])<=at)
                and (not r.get('valid_to') or at<instant(r['valid_to']))]
    if len(candidates)>10000:raise ValueError('Reconciliation candidate budget exceeded')
    available=[r for r in candidates if r['value'] is not None]
    selected=available
    if policy=='latest' and selected:
        latest=max(instant(r['observed_at']) for r in selected)
        selected=[r for r in selected if instant(r['observed_at'])==latest]
    if policy=='source_priority' and selected:
        if len(set(source_priority))!=len(source_priority) or not source_priority:raise ValueError('Distinct explicit source priority required')
        def rank(row):
            source=row.get('attributes',{}).get('source_dataset')
            if source is None:source=(row.get('evidence') or [{}])[0].get('input',{}).get('dataset')
            return source_priority.index(source) if source in source_priority else len(source_priority)
        best=min(map(rank,selected));selected=[r for r in selected if rank(r)==best]
    alternatives={canonical([r['value'],r.get('unit')]) for r in selected}
    status='missing' if not selected else 'resolved' if len(alternatives)==1 else 'conflicting'
    ids=[r['id'] for r in selected]
    return {'status':status,'policy':policy,'selector':deepcopy(selector),'at':at.isoformat(),'known_at':known.isoformat(),
            'selected_record_ids':ids,'rejected_record_ids':[r['id'] for r in candidates if r['id'] not in ids],
            'value':deepcopy(selected[0]['value']) if status=='resolved' else None,
            'unit':selected[0].get('unit') if status=='resolved' else None,'candidates':deepcopy(candidates),
            'temporal_caveat':'Missing validity bounds remain unspecified; selection does not infer a historical interval.'}
