"""Publish environment episodes and standalone visual materializations."""
from pathlib import Path
from .artifacts import publish_report,load_report
from .util import canonical

COMMANDS={'environment','surface'}


def add_commands(sub):
    env=sub.add_parser('environment',help='Run an explicit action sequence through a materialization environment')
    env.add_argument('reference');env.add_argument('--request',type=Path,required=True)
    env.add_argument('--backend',choices=['checkpoint','replay'],default='checkpoint')
    env.add_argument('--dataset',default='environment_episode')
    surface=sub.add_parser('surface',help='Render a verified materialization or graph as a standalone HTML surface')
    surface.add_argument('reference');surface.add_argument('--spec',type=Path,required=True)
    surface.add_argument('--dataset',default='materialization_surface')
    surface.add_argument('--output',type=Path,required=True)


def execute(args,catalog,store,project,reference):
    from .strategic_cli import _local_input
    from .materialize import load_view
    graph=reference(args.reference,store)
    if args.command=='environment':
        from .environments import Environment,TemporalEvaluator,CheckpointEvaluator
        raw,request=_local_input(store,args.dataset,args.request,'environment input/output/reward specification and action sequence')
        evaluator=CheckpointEvaluator if getattr(args,'backend','replay')=='checkpoint' else TemporalEvaluator
        adapter=evaluator(store,graph,request['materialization'],request['step_seconds'],
                                  max_total_calls=request.get('max_total_calls',10000))
        env=Environment(adapter,request['environment'])
        observation,info=env.reset(request.get('seed',0))
        frames=[{'observation':observation,'info':info,'reward':None}]
        materialization_ref=adapter.publish() if isinstance(adapter,CheckpointEvaluator) else env.materialization['artifact']
        inputs=[graph,materialization_ref]
        if len(request['actions'])>request['environment']['max_steps']:raise ValueError('Action sequence exceeds episode horizon')
        for action in ([] if info.get('terminated') or info.get('truncated') else request['actions']):
            observation,reward,terminated,truncated,info=env.step(action)
            frames.append({'observation':observation,'reward':reward,'terminated':terminated,'truncated':truncated,'info':info})
            materialization_ref=adapter.publish() if isinstance(adapter,CheckpointEvaluator) else env.materialization['artifact']
            inputs.append(materialization_ref)
            if terminated or truncated:break
        inputs=list({canonical(ref):ref for ref in inputs}.values())
        result={'frames':frames,'input_history':env.history,'materialization_ref':materialization_ref,
                'snapshots':env.materialization['snapshots'],'process_calls':adapter.total_calls,'backend':getattr(args,'backend','replay'),
                'epistemic_status':'synthetic_scenario','reward_definition':request['environment']['reward'],
                'limitations':['Rewards reflect explicit objectives, not validated real-world utility.',
                               ('Checkpoint execution is incremental; histories are retained in memory and external effects cannot be rolled back.' if isinstance(adapter,CheckpointEvaluator) else 'Reference execution replays history and may perform quadratic work.'),
                               'Observation selection is explicit; subject selection alone creates no information-access model.']}
        artifact=publish_report(store,args.dataset,result,{'request':request},inputs=inputs,raw_inputs=[raw],
                                entrypoint='worldmodel.environments:Environment')
        return {'artifact':artifact,'frames':frames,'process_calls':adapter.total_calls,'backend':getattr(args,'backend','replay')}
    from .surfaces import render_surface
    raw,spec=_local_input(store,args.dataset,args.spec,'visual surface specification')
    if (store.version_dir(graph)/'view.json').exists():data=load_view(store,graph)
    elif (store.version_dir(graph)/'report.json').exists():data=load_report(store,graph)
    else:data={}
    records=list(store.records(graph))
    # View observations are a serialization of snapshots, not additional measurements.
    if data.get('snapshots'):
        records=[r for r in records if r['kind']!='observation']
    data={**data,'records':records}
    html=render_surface(data,spec)
    artifact=publish_report(store,args.dataset,{'html':html,'source':graph,'spec':spec}, {'source':graph,'spec':spec},
                            inputs=[graph],raw_inputs=[raw],entrypoint='worldmodel.surfaces:render_surface')
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(html,encoding='utf-8')
    return {'artifact':artifact,'html_export':str(args.output.resolve()),'bytes':len(html.encode())}
