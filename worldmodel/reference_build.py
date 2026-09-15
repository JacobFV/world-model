"""Reconstruct the reference backbone from pinned, bounded source evidence."""
from copy import deepcopy
from .artifacts import publish_report, load_report
from .graph import Graph
from .market_sources import SOURCE_IDS as MARKETS
from .people_sources import SOURCE_IDS as PEOPLE
from .pipeline import Runner
from .sampling import explore
from .util import digest, read_json

SOURCE_IDS=MARKETS+PEOPLE


def source_status(catalog,store):
    return [{**(explore(store,d) if (store.dataset_dir(d)/'samples/latest.json').exists()
                else {'dataset':d,'status':'not_acquired'}),
             'scope':catalog.get(d)['description']} for d in SOURCE_IDS]


def build_reference(catalog,store,project):
    from .ontology import validate_typed_graph
    runner=Runner(catalog,store,project)
    inputs=[]; coverage=[]; records=[]
    for base in ('strategic_evidence','world_evidence'):
        if (store.dataset_dir(base)/'latest.json').exists():
            ref=store.latest(base); inputs.append(ref)
            coverage.extend(load_report(store,ref).get('coverage',[]) if base=='strategic_evidence' else [])
            # Follow the pinned base input, never a mutable latest pointer, for original source scopes.
            originals=[ref] if base=='world_evidence' else [r for r in store.manifest(ref)['inputs'] if r['dataset']=='world_evidence']
            for original_ref in originals:
                store.verify(original_ref)
                coverage.extend({**item,'status':'normalized','scope':'Original bounded sample'}
                                for item in store.manifest(original_ref).get('parameters',{}).get('coverage',[]))
            break
    for status in source_status(catalog,store):
        dataset=status['dataset']
        if status['status']!='sampled':
            coverage.append({'dataset':dataset,'status':status['status'],'reason':status.get('reason'),'scope':status['scope']})
            continue
        manifest=read_json(store.dataset_dir(dataset)/'samples'/status['sample_id']/'manifest.json')
        ref=runner.run(dataset,raw_refs={dataset:[manifest['artifact']]})
        inputs.append(ref)
        coverage.append({'dataset':dataset,'status':'normalized','output':ref,'sample_id':status['sample_id'],
                         'rows':status['rows'],'scope':status['scope']})
    if not inputs:raise ValueError('Acquire bounded source samples first')
    for ref in inputs:
        for original in store.records(ref):
            record=deepcopy(original)
            if record['kind']=='entity':record['entity_id']=original.get('entity_id',original['id'])
            record['id']='reference:'+digest([ref,original['id']])
            record['evidence']=[{'input':ref,'record_id':original['id']}]
            records.append(record)
            if record['kind']=='entity':
                key=record.get('entity_id',original['id'])
                for prefix,namespace in (('sec:cik:','sec_cik'),('lei:','lei'),('fdic:cert:','fdic_cert'),('fec:candidate:','fec')):
                    if key.startswith(prefix):
                        records.append({'kind':'assertion','id':'reference:id:'+digest([ref,original['id'],namespace]),
                                        'subject':key,'predicate':'identifier_assignment',
                                        'value':{'namespace':namespace,'value':key[len(prefix):]},
                                        'observed_at':record['observed_at'],'evidence':deepcopy(record['evidence']),
                                        'attributes':{'method':'extract explicit source identifier from canonical ID; no name inference',
                                                      'validity_basis':'source does not date identifier assignment'}})
    counts=validate_typed_graph(records)
    from .identity import IdentityIndex
    IdentityIndex(records)
    report={'scope':'Bounded source samples; all retained history, not a complete or current world census',
            'counts':counts,'coverage':coverage,'synthetic_scenarios_included':False}
    ref=publish_report(store,'reference_evidence',report,{'coverage':coverage},inputs=inputs,records=records,
                       entrypoint='worldmodel.reference_build:build_reference')
    index=store.root/'reference_evidence/index.sqlite'; Graph(index).build(store,[ref])
    return {**report,'artifact':ref,'index':str(index)}
