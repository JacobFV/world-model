"""CLI orchestration for bounded evidence, domain kernels and strategic reports."""
import json
from pathlib import Path
import sys
from .artifacts import publish_report, load_report
from .util import read_json, now

from .foundation_cli import COMMANDS as FOUNDATION_COMMANDS

COMMANDS=FOUNDATION_COMMANDS | {'sources','strategic-build','route','economy','banking','strategies','calibrate','report'}


def add_commands(sub):
    from .foundation_cli import add_commands as add_foundation
    add_foundation(sub)
    sources=sub.add_parser('sources',help='Inspect or acquire bounded strategic source samples')
    sources.add_argument('--sample',action='store_true')
    sources.add_argument('--allow-network',action='store_true')
    sub.add_parser('strategic-build',help='Normalize successful strategic samples and join typed evidence')
    route=sub.add_parser('route',help='Plan a bounded multimodal journey')
    inputs=route.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--network',type=Path)
    inputs.add_argument('--sample',help='Existing OSM topology sample dataset')
    route.add_argument('--request',type=Path,required=True)
    route.add_argument('--person',help='Scenario traveler ID; does not assert real travel')
    route.add_argument('--dataset',default='journey_scenario')
    for name in ('economy','banking','strategies'):
        command=sub.add_parser(name,help='Run and publish an explicit '+name+' scenario')
        command.add_argument('--request',type=Path,required=True)
        command.add_argument('--dataset',default=name+'_scenario')
        if name=='economy':
            command.add_argument('--seed-graph',help='Seed rates and WTI price from verified observed graph')
            command.add_argument('--as-of')
            command.add_argument('--known-at')
    calibration=sub.add_parser('calibrate',help='Fit a retrospective one-step model and score chronological holdout')
    calibration.add_argument('reference')
    calibration.add_argument('--metric',required=True)
    calibration.add_argument('--train-end',required=True)
    calibration.add_argument('--dataset',default='series_calibration')
    report=sub.add_parser('report',help='Read an immutable domain report')
    report.add_argument('reference')


def _local_input(store,dataset,path,label):
    if path.stat().st_size>1024*1024:
        raise ValueError('Local scenario input exceeds 1 MiB laptop limit')
    ref=store.import_file(dataset+'_inputs',path,{'publisher':'user-provided','role':label},update_latest=False)
    return ref,read_json(store.artifact_dir(ref)/'payload')


def _sample(store,dataset):
    from .sampling import explore
    profile=explore(store,dataset)
    if profile['status']!='sampled':
        raise ValueError('A successful bounded source sample is required')
    manifest=read_json(store.dataset_dir(dataset)/'samples'/profile['sample_id']/'manifest.json')
    ref=manifest['artifact']
    rows=[json.loads(line) for line in (store.artifact_dir(ref)/'payload').read_text().splitlines() if line.strip()]
    return ref,rows


def execute(args,catalog,store,project,reference):
    command=args.command
    if command in FOUNDATION_COMMANDS:
        from .foundation_cli import execute as execute_foundation
        return execute_foundation(args,catalog,store,project,reference)
    if command=='sources':
        from .strategic_sources import SOURCE_IDS
        from .sampling import sample_dataset
        results=[]
        for dataset in SOURCE_IDS:
            definition=catalog.get(dataset)
            if args.sample:
                result=sample_dataset(store,definition,allow_network=args.allow_network)
                print(f'{dataset}: {result["status"]}',file=sys.stderr,flush=True)
            else:
                path=store.dataset_dir(dataset)/'samples/latest.json'
                result=read_json(path) if path.exists() else {'dataset':dataset,'status':'not_acquired'}
            results.append({**result,'description':definition['description']})
        return results
    if command=='strategic-build':
        from .strategic_build import build_strategic
        return build_strategic(catalog,store,project)
    if command=='report':
        return load_report(store,reference(args.reference,store))
    if command=='calibrate':
        from .calibration import fit_ar1
        graph=reference(args.reference,store)
        rows=[];subjects=set()
        for row in store.records(graph):
            if row.get('kind')=='observation' and row.get('metric')==args.metric and row.get('epistemic_status','observed')=='observed':
                subjects.add(row.get('subject'))
                if not row.get('valid_from'):
                    raise ValueError('Calibration observations require explicit valid_from')
                rows.append({'time':row['valid_from'],'value':row['value'],'unit':row['unit'],
                             'evidence':[{'input':graph,'record_id':row['id']}]})
        if len(subjects)!=1:
            raise ValueError('Calibration requires exactly one entity series for the requested metric')
        result=fit_ar1(rows,args.train_end)
        artifact=publish_report(store,args.dataset,result,{'metric':args.metric,'train_end':args.train_end},
                                inputs=[graph],entrypoint='worldmodel.calibration:fit_ar1')
        return {'artifact':artifact,**{key:result[key] for key in ('parameters','training','holdout','limitations')}}
    raw,request=_local_input(store,args.dataset,args.request,'explicit requested scenario')
    raws=[raw];parents=[]
    if command=='route':
        from .transport import network_from_osm,route,traveler_events
        if args.network:
            network_ref,network=_local_input(store,args.dataset,args.network,'network with explicit provenance class')
        else:
            network_ref,elements=_sample(store,args.sample)
            network=network_from_osm(elements)
            # Every edge traces the original sampled OSM way row.
            by_way={str(row['id']):index for index,row in enumerate(elements,1)}
            for edge in network['edges']:
                way_id=str(edge['evidence'][0]['id'])
                edge.setdefault('evidence',[]).append({'input':network_ref,'locator':'line:'+str(by_way[way_id])})
        raws.append(network_ref)
        result=route(network,request)
        if args.person and result['status']=='ok':
            result['traveler_events']=traveler_events(result,args.person)
        entrypoint='worldmodel.transport:route'
        summary={key:result[key] for key in ('status','arrival_time','duration_seconds','total_cost','edge_ids','reason') if key in result}
    elif command=='economy':
        from .economy import simulate_economy
        seed=None
        if args.seed_graph:
            from .economy_seeding import seed_economy
            if not args.as_of or not args.known_at:
                raise ValueError('Evidence seeding requires explicit --as-of and --known-at')
            graph=reference(args.seed_graph,store);parents.append(graph)
            seed=seed_economy(request,list(store.records(graph)),args.as_of,args.known_at)
            request=seed['config']
        result=simulate_economy(request)
        if seed:result['source_seeding']=seed
        entrypoint='worldmodel.economy:simulate_economy'
        summary={'metrics':result['metrics'],'accounting':result['accounting'],'status':result['status']}
    elif command=='banking':
        from .banking import simulate_banking
        result=simulate_banking(request);entrypoint='worldmodel.banking:simulate_banking'
        summary={'accounting':result['accounting'],'events':result['events']}
    else:
        from .strategy import evaluate_strategies
        result=evaluate_strategies(request);entrypoint='worldmodel.strategy:evaluate_strategies'
        summary={key:result[key] for key in ('runs','steps','ranking','recommended_policy','limitations')}
    artifact=publish_report(store,args.dataset,result,request,inputs=parents,raw_inputs=raws,entrypoint=entrypoint)
    return {'artifact':artifact,**summary}
