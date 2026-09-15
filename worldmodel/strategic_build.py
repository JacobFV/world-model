"""Join successful strategic samples into typed evidence; preserve coverage gaps."""
from copy import deepcopy
from .artifacts import publish_report
from .graph import Graph
from .normalizers import UNAVAILABLE
from .ontology import validate_typed_graph
from .pipeline import Runner
from .sampling import explore
from .strategic_sources import SOURCE_IDS
from .util import digest, read_json


def build_strategic(catalog, store, project):
    runner=Runner(catalog,store,project)
    inputs, coverage, records=[],[],[]
    if (store.dataset_dir('world_evidence')/'latest.json').exists():
        inputs.append(store.latest('world_evidence'))
    for dataset in SOURCE_IDS:
        pointer=store.dataset_dir(dataset)/'samples/latest.json'
        if not pointer.exists():
            coverage.append({'dataset':dataset,'status':'not_acquired'})
            continue
        sample=explore(store,dataset)
        if sample['status']!='sampled':
            coverage.append({'dataset':dataset,'status':sample['status'],'reason':sample.get('reason')})
            continue
        manifest=read_json(store.dataset_dir(dataset)/'samples'/sample['sample_id']/'manifest.json')
        ref=runner.run(dataset,raw_refs={dataset:[manifest['artifact']]})
        inputs.append(ref)
        coverage.append({'dataset':dataset,'status':'normalized','sample_id':sample['sample_id'],'artifact':manifest['artifact'],'output':ref,'rows':sample['rows']})
    if not inputs:
        raise ValueError('No evidence inputs available; acquire bounded samples first')
    for ref in inputs:
        for original in store.records(ref):
            record=deepcopy(original)
            record['id']='joined:'+digest([ref,original['id']])
            record['evidence']=[{'input':ref,'record_id':original['id']}]
            records.append(record)
    counts=validate_typed_graph(records)
    report={'scope':'bounded sampled evidence','counts':counts,'coverage':coverage,
            'unavailable_original_sources':list(UNAVAILABLE),'synthetic_scenarios_included':False}
    ref=publish_report(store,'strategic_evidence',report,{'coverage':coverage},inputs=inputs,
                       records=records,entrypoint='worldmodel.strategic_build:build_strategic')
    index=store.root/'strategic_evidence/index.sqlite'
    Graph(index).build(store,[ref])
    return {**report,'artifact':ref,'index':str(index)}
