"""Explicit review evidence; only model-compatible complete terms seed exposure."""
from worldmodel.contract_extraction import bounded_rows,review_obligation
from worldmodel.util import digest

def run(context):
    candidates=[]
    for c in context.records('contract_candidates'):
        if len(candidates)>=10000:raise ValueError('Candidate budget exceeded')
        candidates.append(c)
    reference=context.input_ref('contract_candidates');seen=set()
    from worldmodel.model import instant
    imports=list(bounded_rows(context))
    reviews={r['id']:r for _,_,r in imports}
    if len(reviews)!=len(imports):raise ValueError('Duplicate review IDs')
    for _,_,review in imports:
        for prior_id in review.get('supersedes',[]):
            prior=reviews.get(prior_id)
            if prior is None or prior_id==review['id'] or instant(prior['valid_to'])>instant(review['valid_from']):
                raise ValueError('Explicit superseded review and nonoverlapping validity required')
            if instant(prior['reviewed_at'])>instant(review['reviewed_at']):raise ValueError('Review cannot supersede a later review')
    for index,locator,review in imports:
        result=review_obligation(candidates,review)
        if review['id'] in seen:raise ValueError('Duplicate obligation review; use distinct revision IDs and supersedes')
        seen.add(review['id'])
        evidence=context.raw_evidence(locator,index)+[{'input':reference,'record_id':key} for ids in review['candidate_ids'].values() for key in ids]
        base={'observed_at':review['reviewed_at'],'valid_from':review['valid_from'],'valid_to':review['valid_to'],'evidence':evidence}
        yield dict(base,kind='entity',id='contract:'+digest([review['id'],'entity']),entity_id=review['id'],entity_type='financial_obligation',label=review['id'])
        yield dict(base,kind='assertion',id='contract:'+digest([review,'review']),subject=review['id'],predicate='contract_review',unit=None,value=result)
        if result['simulation_eligible']:
            for role,party in review['parties'].items():
                yield dict(base,kind='entity',id='contract:'+digest([review['id'],role]),entity_id=party['entity_id'],entity_type='organization',label=party['label'])
            yield dict(base,kind='assertion',id='contract:'+digest([review,'terms']),subject=review['id'],predicate='obligation_terms',unit=None,value=review['terms'])
