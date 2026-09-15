"""Coupled kernels, evidence assessment and portable-resource operations."""
from pathlib import Path
from .artifacts import publish_report,load_report
from .util import read_json

COMMANDS={'coupled-economy','rights','evidence-audit','assess-model','reconcile','rl-benchmark','resources','spatial','spatial-timeline','sensitivity','benchmark-scenarios','cross-domain'}


def add_commands(sub):
    sub.add_parser('resources',help='Locate bundled read-only catalogs/examples and writable data')
    sub.add_parser('evidence-audit',help='Inspect source readiness, access gaps and rights metadata')
    for name in ('rights','assess-model','reconcile'):
        command=sub.add_parser(name);command.add_argument('reference')
        if name=='assess-model':command.add_argument('--min-count',type=int,default=20)
        if name=='reconcile':command.add_argument('--request',type=Path,required=True)
    rl=sub.add_parser('rl-benchmark',help='Train and evaluate a tiny fictional tabular benchmark on disjoint seeds')
    rl.add_argument('--dataset',default='rl_benchmark')
    for name in ('coupled-economy','spatial','spatial-timeline','sensitivity','benchmark-scenarios','cross-domain'):
        command=sub.add_parser(name);command.add_argument('--request',type=Path,required=True)
        command.add_argument('--dataset',default=name.replace('-','_')+'_scenario')
        if name=='cross-domain':command.add_argument('--fidelity',choices=['aggregate','unit'],default='aggregate')


def execute(args,catalog,store,project,reference):
    from .strategic_cli import _local_input
    if args.command=='resources':
        from .resources import resource_roots
        return {key:str(value) for key,value in resource_roots().items()}
    if args.command=='rights':
        from .rights import inherited_rights
        ref=reference(args.reference,store);store.verify(ref)
        return inherited_rights(store,[ref])
    if args.command=='evidence-audit':
        from .sampling import explore
        sources=[]
        for definition in catalog.list():
            if definition['kind']!='source':continue
            name=definition['id'];pointer=store.sample_latest_path(name)
            sample=explore(store,name) if pointer.exists() else {'status':'not_acquired'}
            sources.append({'dataset':name,'status':sample['status'],'rows':sample.get('rows'),
                            'reason':sample.get('reason',definition.get('sampling',{}).get('reason')),
                            'scope':definition['description'],'source':definition.get('source',{}),
                            'sampling_criteria':definition.get('sampling',{}).get('criteria'),
                            'representative':False,'complete':False})
        return {'sources':sources,'scope':'Catalog readiness, not a complete universe','causally_validated':False}
    if args.command=='rl-benchmark':
        from .rl import toy_learning_benchmark
        result=toy_learning_benchmark()
        ref=publish_report(store,args.dataset,result,{},entrypoint='worldmodel.rl:toy_learning_benchmark')
        return {'artifact':ref,**result}
    if args.command=='assess-model':
        from .validation import assess_holdout
        source=reference(args.reference,store)
        result=assess_holdout(load_report(store,source),min_count=args.min_count)
        ref=publish_report(store,'model_assessment',result,{'min_count':args.min_count},inputs=[source],
                           entrypoint='worldmodel.validation:assess_holdout')
        return {'artifact':ref,**result}
    if args.command=='reconcile':
        from .reconciliation import reconcile_claims
        source=reference(args.reference,store);raw,request=_local_input(store,'reconciliation',args.request,'explicit reconciliation policy')
        result=reconcile_claims(list(store.records(source)),**request)
        ref=publish_report(store,'reconciliation',result,request,inputs=[source],raw_inputs=[raw],entrypoint='worldmodel.reconciliation:reconcile_claims')
        return {'artifact':ref,**result}
    raw,request=_local_input(store,args.dataset,args.request,'explicit scenario configuration')
    if args.command=='cross-domain':
        from .composition import materialize_composition
        result=materialize_composition(request,fidelity=args.fidelity)
        entrypoint='worldmodel.composition:materialize_composition'
    elif args.command=='benchmark-scenarios':
        from .scenario_benchmark import benchmark_scenarios
        result=benchmark_scenarios(**request)
        entrypoint='worldmodel.scenario_benchmark:benchmark_scenarios'
    elif args.command=='sensitivity':
        from .validation import parameter_sweep
        from .coupled_economy import simulate_coupled_economy
        if request.get('model')!='coupled_economy':raise ValueError('Unknown sensitivity model')
        result=parameter_sweep(simulate_coupled_economy,request['baseline'],request['grid'],request['metrics'],request.get('max_runs',100))
        entrypoint='worldmodel.validation:parameter_sweep'
    elif args.command=='coupled-economy':
        from .coupled_economy import simulate_coupled_economy
        result=simulate_coupled_economy(request);entrypoint='worldmodel.coupled_economy:simulate_coupled_economy'
    elif args.command=='spatial-timeline':
        from .spatial_store import SpatialStore
        import tempfile
        with tempfile.TemporaryDirectory(prefix='worldmodel-timeline-') as tmp:
            with SpatialStore(Path(tmp)/'state.sqlite') as spatial:
                spatial.initialize(request['world'],coordinate_system=request['coordinate_system'])
                result=spatial.evolve_timeline(request['request'])
        entrypoint='worldmodel.spatial_store:SpatialStore.evolve_timeline'
    else:
        from .spatial_store import SpatialStore
        # This command reconstructs an immutable scenario; the Python API opens persistent databases directly.
        import tempfile
        with tempfile.TemporaryDirectory(prefix='worldmodel-spatial-') as tmp:
            with SpatialStore(Path(tmp)/'state.sqlite') as spatial:
                spatial.initialize(request['world'],coordinate_system=request['coordinate_system'])
                transitions=spatial.apply(request.get('events',[])) if request.get('events') else []
                selection={**request.get('selection',{}),**request.get('view',{})}
                if request.get('evolution'):selection['evolution']=request['evolution']
                result={'transitions':transitions,'materialization':spatial.materialize(**selection),
                        'audit':spatial.audit(),'epistemic_status':'synthetic_scenario'}
        entrypoint='worldmodel.spatial_store:SpatialStore.materialize'
    parameters={'request':request}
    if args.command=='cross-domain':parameters['fidelity']=args.fidelity
    ref=publish_report(store,args.dataset,result,parameters,raw_inputs=[raw],entrypoint=entrypoint)
    return {'artifact':ref,**result}
