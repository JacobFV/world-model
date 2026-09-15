"""Field/topology, actor-kernel and lifecycle materialization commands."""
import random
from datetime import timedelta
from pathlib import Path
from .artifacts import publish_report
from .ontology import validate_typed_graph
from .util import read_json

COMMANDS={'fields','field-view','actors','lifecycle'}


def add_commands(sub):
    for name in ('fields','field-view','actors','lifecycle'):
        command=sub.add_parser(name,help='Materialize '+name+' with explicit source/scenario provenance')
        command.add_argument('--request',type=Path,required=True)
        command.add_argument('--dataset',default=name.replace('-','_')+'_scenario')
        if name=='field-view':
            command.add_argument('--view',type=Path,required=True)
            command.add_argument('--evolve',action='store_true')
        if name=='actors':
            command.add_argument('--actor',choices=['human','business','government'],required=True)
            command.add_argument('--implementation',required=True)
            command.add_argument('--entity',default='scenario:actor')
            command.add_argument('--seed',type=int,default=0)
            command.add_argument('--lifecycle',type=Path)
        if name in ('actors','lifecycle'):
            command.add_argument('--at',required=True)
            command.add_argument('--known-at',required=True)


def execute(args,catalog,store,project,reference):
    from .strategic_cli import _local_input
    raw,request=_local_input(store,args.dataset,args.request,'explicit field/actor/lifecycle input')
    raws=[raw];records=[]
    if args.command in ('fields','field-view'):
        from .fields import FieldWorld,simulate_fields
        if args.command=='fields':
            result=simulate_fields(request)
            entrypoint='worldmodel.fields:simulate_fields'
            summary={key:result[key] for key in ('execution','conservation','assumptions')}
        else:
            query_ref,query=_local_input(store,args.dataset,args.view,'requested lazy graph projection')
            raws.append(query_ref)
            world=simulate_fields(request)['state'] if args.evolve else request.get('world',request)
            query={**query,'evidence':[{'input':raw,'locator':'world field/topology configuration'}]}
            records=list(FieldWorld(world).project(query))
            counts=validate_typed_graph(records)
            result={'query':query,'evolved':args.evolve,'counts':counts,'epistemic_status':'synthetic_scenario',
                    'records':records,'limitations':['Only requested field cells and relations are projected.',
                    'Projection does not resolve competing territory claims or refine missing spatial information.']}
            entrypoint='worldmodel.fields:FieldWorld.project'
            summary={'counts':counts,'epistemic_status':'synthetic_scenario'}
        parameters={'request':request,'projection':query if args.command=='field-view' else None,
                    'evolve':getattr(args,'evolve',False)}
    elif args.command=='lifecycle':
        from .lifecycle import materialize_lifecycle,graph_records
        result=materialize_lifecycle(request,args.at,args.known_at)
        records=list(graph_records(result,[{'input':raw,'locator':'explicit lifecycle event configuration'}]))
        # Local examples are scenarios; do not promote their event reconstructions to observed facts.
        for record in records:record['epistemic_status']='synthetic_scenario'
        counts=validate_typed_graph(records)
        entrypoint='worldmodel.lifecycle:materialize_lifecycle'
        parameters={'config':request,'at':args.at,'known_at':args.known_at}
        summary={'entities':result['entities'],'counts':counts,'assumptions':result['assumptions']}
    else:
        from .model import identifier,instant
        from .process_library import default_registry
        identifier(args.entity);instant(args.at);instant(args.known_at)
        registry=default_registry();port=args.actor+'_state'
        state=request.get(port,request)
        lifecycle=None
        if args.lifecycle:
            from .lifecycle import materialize_lifecycle,require_actor_eligible
            life_ref,life=_local_input(store,args.dataset,args.lifecycle,'actor lifecycle gate')
            raws.append(life_ref)
            lifecycle=materialize_lifecycle(life,args.at,args.known_at)
            require_actor_eligible(lifecycle,args.entity)
            end_lifecycle=materialize_lifecycle(life,(instant(args.at)+timedelta(days=1)).isoformat(),args.known_at)
            require_actor_eligible(end_lifecycle,args.entity)
        specs=registry.describe()
        implementation=next((row for row in specs['implementations'] if row['id']==args.implementation),None)
        if implementation is None or implementation['process_id']!=args.actor+'_behavior':
            raise ValueError('Implementation does not belong to requested actor process')
        if implementation['fidelity']=='agent':
            raise ValueError('CLI agent kernel requires an injected backend; use the Python API with explicit backend identity')
        result=registry.predict(args.implementation,{port:{'value':state,'unit':None}}, {},
             {'dt_seconds':86400,'rng':random.Random(args.seed),'entity_id':args.entity,'time':args.at,'memory':{},'budget':1000})
        result={'prediction':result,'implementation':implementation,'entity':args.entity,'lifecycle':lifecycle,
                'epistemic_status':'synthetic_scenario','causally_calibrated':False}
        entrypoint='worldmodel.actor_kernels:register_actor_kernels'
        parameters={'request':request,'actor':args.actor,'implementation':args.implementation,'entity':args.entity,
                    'at':args.at,'known_at':args.known_at,'seed':args.seed}
        summary={'prediction':result['prediction'],'implementation':args.implementation}
    artifact=publish_report(store,args.dataset,result,parameters,raw_inputs=raws,records=records,entrypoint=entrypoint)
    return {'artifact':artifact,**summary}
