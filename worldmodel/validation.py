"""Explicit descriptive validation gates and bounded model sensitivity experiments."""
from copy import deepcopy
from itertools import product
import math
from .model import instant
from .util import canonical


def _number(value):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Finite numeric value required')
    return value


def assess_holdout(report,min_count=20,minimum_improvement=0):
    if type(min_count) is not int or min_count<2:raise ValueError('Holdout requires at least two observations')
    if not 0<=_number(minimum_improvement)<=1:raise ValueError('Improvement must be a fraction in [0,1]')
    rows=report['predictions'];cutoff=instant(report['training']['through'])
    if not 1<=len(rows)<=100000:raise ValueError('Prediction budget exceeded or empty')
    times=set();errors=[];baselines=[]
    for row in rows:
        time=instant(row['time']);previous=instant(row['input_time'])
        if time<=cutoff or previous>=time or time in times:raise ValueError('Holdout leakage, duplicate time or invalid lag')
        times.add(time)
        actual,prediction,baseline=(_number(row[k]) for k in ('actual','prediction','persistence'))
        errors.append(_number(abs(actual-prediction)));baselines.append(_number(abs(actual-baseline)))
    mae=_number(math.fsum(e/len(rows) for e in errors));baseline=_number(math.fsum(e/len(rows) for e in baselines))
    relative=_number((baseline-mae)/baseline) if baseline else 0
    reasons=[]
    if len(rows)<min_count:reasons.append('insufficient_holdout_count')
    if not mae<baseline or relative<minimum_improvement:reasons.append('does_not_meet_baseline_improvement')
    return {'status':'passes_descriptive_holdout' if not reasons else 'fails_descriptive_holdout','passes':not reasons,
            'reasons':reasons,'count':len(rows),'mae':mae,'baseline_mae':baseline,'relative_improvement':relative,
            'causally_validated':False,'criteria':{'min_count':min_count,'minimum_improvement':minimum_improvement},
            'limitations':['Rolling holdout accuracy does not identify policy effects or verify real-time data vintages.',
                           'This score must be published with its verified prediction/evidence inputs.']}


def _path(value,path):
    if not isinstance(path,list) or not 1<=len(path)<=20:raise ValueError('Explicit bounded JSON path required')
    for key in path:
        if isinstance(value,dict) and isinstance(key,str) and key in value:value=value[key]
        elif isinstance(value,list) and type(key) is int and 0<=key<len(value):value=value[key]
        else:raise ValueError('Missing JSON path')
    return value


def parameter_sweep(evaluate,baseline,grid,metrics,max_runs=100):
    if type(max_runs) is not int or not 1<=max_runs<=1000 or not 1<=len(grid)<=10:raise ValueError('Invalid sensitivity budget')
    count=1;paths=set()
    for axis in grid:
        key=canonical(axis['path'])
        if key in paths:raise ValueError('Duplicate sensitivity path')
        paths.add(key)
        if not isinstance(axis['values'],list) or not axis['values']:raise ValueError('Empty sensitivity choices')
        count*=len(axis['values']);_path(baseline,axis['path'])
    if count>max_runs:raise ValueError('Sensitivity run budget exceeded')
    canonical([baseline,grid,metrics]);runs=[]
    for values in product(*(axis['values'] for axis in grid)):
        config=deepcopy(baseline)
        for axis,value in zip(grid,values):
            parent=_path(config,axis['path'][:-1]) if len(axis['path'])>1 else config
            parent[axis['path'][-1]]=deepcopy(value)
        result=evaluate(config)
        runs.append({'parameters':[{'path':a['path'],'value':v,'unit':a.get('unit')} for a,v in zip(grid,values)],
                     'metrics':{name:_number(_path(result,spec['path'])) for name,spec in metrics.items()}})
    return {'runs':runs,'metric_units':{name:spec.get('unit') for name,spec in metrics.items()},
            'causally_validated':False,'scope':'Explicit parameter sensitivity; no fitted probability distribution'}
