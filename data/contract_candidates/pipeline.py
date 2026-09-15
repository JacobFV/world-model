"""Document-local candidates, never canonical obligations."""
from worldmodel.contract_extraction import bounded_rows,extract_candidates

def run(context):
    count=0
    for index,locator,document in bounded_rows(context):
        for candidate in extract_candidates(document):
            count+=1
            if count>10000:raise ValueError('Aggregate candidate budget exceeded')
            candidate['evidence']=context.raw_evidence(locator+';chars:'+str(candidate['locator']['start'])+':'+str(candidate['locator']['end']),index)
            yield candidate
