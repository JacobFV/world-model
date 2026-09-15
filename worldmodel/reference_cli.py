"""Reference lookup, coverage audits and explicitly assumed financial stress."""
from pathlib import Path
import sys
from .artifacts import publish_report, load_report
from .util import now

COMMANDS={'reference-sources','reference-build','resolve','search-entities','coverage','exposure'}


def add_commands(sub):
    source=sub.add_parser('reference-sources',help='Inspect or acquire bounded market and public-person samples')
    source.add_argument('--sample',action='store_true');source.add_argument('--allow-network',action='store_true')
    sub.add_parser('reference-build',help='Join normalized references to strategic evidence')
    for name in ('resolve','search-entities','coverage'):
        command=sub.add_parser(name)
        command.add_argument('--graph',default='reference_evidence')
        if name!='coverage':
            command.add_argument('--at',required=True);command.add_argument('--known-at',required=True)
        if name=='resolve':
            command.add_argument('namespace');command.add_argument('value');command.add_argument('--scope')
        if name=='search-entities':
            command.add_argument('query');command.add_argument('--limit',type=int,default=20)
    stress=sub.add_parser('exposure',help='Publish a bounded obligation stress scenario')
    stress.add_argument('--request',type=Path,required=True)
    stress.add_argument('--graph',help='Require scenario counterparties to be known graph identities')
    stress.add_argument('--evidence',action='store_true',help='Seed from pinned dated cash/obligation records; requires --graph')
    stress.add_argument('--dataset',default='exposure_scenario')


def execute(args,catalog,store,project,reference):
    from .reference_build import SOURCE_IDS, source_status, build_reference
    if args.command=='reference-sources':
        if not args.sample:return source_status(catalog,store)
        from .sampling import sample_dataset
        results=[]
        for dataset in SOURCE_IDS:
            result=sample_dataset(store,catalog.get(dataset),allow_network=args.allow_network)
            results.append(result)
            print(dataset+': '+result['status'],file=sys.stderr,flush=True)
        return results
    if args.command=='reference-build':return build_reference(catalog,store,project)
    if args.command=='exposure':
        from .strategic_cli import _local_input
        from .exposure import stress_exposures, exposure_from_evidence
        raw,request=_local_input(store,args.dataset,args.request,'explicit assumed obligations and cash')
        inputs=[]; linked=[]
        if args.graph:
            ref=reference(args.graph,store); inputs.append(ref)
            entities={r.get('entity_id',r['id']):r for r in store.records(ref)
                      if r['kind']=='entity' and r.get('epistemic_status') in (None,'observed')}
            for actor in request['entities']:
                if actor['id'] not in entities:raise ValueError('Unknown observed graph counterparty: '+actor['id'])
                r=entities[actor['id']]
                linked.append({'entity_id':actor['id'],'evidence':{'input':ref,'record_id':r['id']}})
        if args.evidence and not args.graph:raise ValueError('--evidence requires --graph')
        computed=exposure_from_evidence(store,inputs[0],request) if args.evidence else stress_exposures(request)
        result={**computed,'identity_links':linked,
                'identity_link_interpretation':'Graph membership does not substantiate assumed obligations or current financial state.'}
        ref=publish_report(store,args.dataset,result,{'request':request},inputs=inputs,raw_inputs=[raw],
                           entrypoint='worldmodel.exposure:exposure_from_evidence' if args.evidence else 'worldmodel.exposure:stress_exposures')
        return {'artifact':ref,**result}
    ref=reference(args.graph,store)
    rows=list(store.records(ref))
    if args.command=='coverage':
        from .coverage import coverage_report
        from .process_library import default_registry
        inputs=[ref]; evaluations=[]
        if (store.latest_path('series_calibration')).exists():
            fit=store.latest('series_calibration');evaluations.append({'artifact':fit,'report':load_report(store,fit)});inputs.append(fit)
        report=coverage_report(rows,default_registry().describe(),load_report(store,ref).get('coverage',[]),evaluations)
        output=publish_report(store,'reference_coverage',report,{'graph':ref},inputs=inputs,
                              entrypoint='worldmodel.coverage:coverage_report')
        return {'artifact':output,**report}
    from .identity import IdentityIndex
    index=IdentityIndex(rows)
    if args.command=='resolve':return index.resolve_identifier(args.namespace,args.value,args.at,args.known_at,scope=args.scope)
    return index.search(args.query,args.at,args.known_at,limit=args.limit)
