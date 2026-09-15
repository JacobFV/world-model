"""Chronological model selection with an untouched final test partition."""
from copy import deepcopy
from decimal import Decimal,localcontext
import math
from .model import instant
from .util import canonical,digest

MODELS=('persistence','mean','drift','ar1')

def _number(value):
    if type(value) not in (int,float) or not math.isfinite(value):raise ValueError('Expected finite series number')
    return Decimal(str(value))

def _finite(value):
    result=float(value)
    if not math.isfinite(result):raise ValueError('Benchmark result is not representable as a finite number')
    return result

def _mean(values):return sum(values,Decimal(0))/len(values)

def _fit(name,training):
    y=[_number(r['value']) for r in training]
    if name=='persistence':return {'coefficient':Decimal(1),'intercept':Decimal(0)}
    if name=='mean':return {'coefficient':Decimal(0),'intercept':_mean(y)}
    if name=='drift':
        # Clamp extrapolated change to the observed training range per step.
        bound=max(y)-min(y);change=(y[-1]-y[0])/(len(y)-1)
        return {'coefficient':Decimal(1),'intercept':max(-bound,min(bound,change))}
    x,y=y[:-1],y[1:];xm,ym=_mean(x),_mean(y)
    variance=sum((v-xm)**2 for v in x)
    slope=sum((a-xm)*(b-ym) for a,b in zip(x,y))/variance if variance else Decimal(0)
    slope=max(Decimal(-1),min(Decimal(1),slope))
    return {'coefficient':slope,'intercept':ym-slope*xm}

def _predict(parameters,row):return parameters['intercept']+parameters['coefficient']*_number(row['value'])

def _score(errors):
    absolute=sorted(abs(v) for v in errors)
    def quantile(q):return absolute[min(len(absolute)-1,math.ceil(q*len(absolute))-1)]
    return {'count':len(errors),'mae':_finite(_mean(absolute)),'bias':_finite(_mean(errors)),
            'absolute_error_quantiles':{str(q):_finite(quantile(q)) for q in (.5,.9,1)}}

def benchmark_series(rows,train_end,validation_end,*,candidates=None,max_rows=100000):
    if type(max_rows) is not int or not 8<=max_rows<=100000 or len(rows)>max_rows:raise ValueError('Benchmark row budget exceeded')
    canonical(rows)
    candidates=list(MODELS if candidates is None else candidates)
    if not candidates or len(candidates)!=len(set(candidates)) or set(candidates)-set(MODELS):raise ValueError('Invalid benchmark candidates')
    identities={(r.get('series'),r.get('unit')) for r in rows}
    if len(identities)!=1 or any(not isinstance(v,str) or not v for v in next(iter(identities),())):raise ValueError('Require one explicit series identity and unit')
    train_cut,validation_cut=instant(train_end),instant(validation_end)
    if train_cut>=validation_cut:raise ValueError('Training cutoff must precede validation cutoff')
    ordered=sorted(deepcopy(rows),key=lambda r:instant(r['time']));times=set();valid=[];excluded=[]
    for row in ordered:
        at=instant(row['time'])
        if at in times:raise ValueError('Duplicate series time; reconcile first')
        times.add(at)
        if row['value'] is None:excluded.append(row);continue
        _number(row['value'])
        row.setdefault('available_at',row['time']);instant(row['available_at'])
        valid.append(row)
    train=[r for r in valid if instant(r['time'])<=train_cut]
    validation=[i for i,r in enumerate(valid) if train_cut<instant(r['time'])<=validation_cut]
    test=[i for i,r in enumerate(valid) if instant(r['time'])>validation_cut]
    if len(train)<4 or len(validation)<2 or len(test)<2:raise ValueError('Require >=4 training,2 validation,2 final test observations')
    if any(instant(r['available_at'])>train_cut for r in train):raise ValueError('Training input unavailable by information cutoff')
    if any(instant(valid[i]['available_at'])>validation_cut for i in validation):raise ValueError('Validation target unavailable by model-selection cutoff')
    for i in validation+test:
        if instant(valid[i-1]['available_at'])>=instant(valid[i]['time']):raise ValueError('Future input leakage at target time')
    with localcontext() as context:
        context.prec=50
        parameters={name:_fit(name,train) for name in set(candidates)|{'persistence'}}
        scores={name:_score([_predict(parameters[name],valid[i-1])-_number(valid[i]['value']) for i in validation]) for name in candidates}
        selected=min(candidates,key=lambda n:(scores[n]['mae'],MODELS.index(n)))
        residuals=sorted(abs(_predict(parameters[selected],valid[i-1])-_number(valid[i]['value'])) for i in validation)
        radius=residuals[min(len(residuals)-1,math.ceil(.9*len(residuals))-1)]
        predictions=[];errors=[];baseline=[]
        for i in test:
            source,target=valid[i-1],valid[i];prediction=_predict(parameters[selected],source)
            errors.append(prediction-_number(target['value']));baseline.append(_number(source['value'])-_number(target['value']))
            predictions.append({'time':target['time'],'actual':target['value'],'prediction':_finite(prediction),'persistence':source['value'],
                'input_time':source['time'],'information_cutoff':source['available_at'],'input_evidence':source.get('evidence',[]),'target_evidence':target.get('evidence',[]),
                'interval':[_finite(prediction-radius),_finite(prediction+radius)]})
        result={'status':'descriptive_heldout_benchmark','causally_calibrated':False,'selected_model':selected,
                'parameters':{name:{k:_finite(v) for k,v in p.items()} for name,p in parameters.items()},
                'training':{'through':train_end,'rows':len(train),'evidence':[r.get('evidence',[]) for r in train]},
                'validation':{'through':validation_end,'scores':scores},'test':_score(errors),'baseline':_score(baseline),
                'selection_hash':digest([selected,train_end,validation_end,scores]),'predictions':predictions,'excluded_missing':excluded,
                'series':valid[0]['series'],'unit':valid[0]['unit'],'refit_after_selection':False,
                'vintage_policy':sorted({r.get('vintage','retrospective; availability defaults to observation time') for r in rows}),
                'interval_method':'validation absolute-error90th-percentile radius; descriptive, no coverage guarantee',
                'forecast_protocol':'rolling one-step next available observation, not fixed-horizon or real-time-vintage validation'}
        result['test']['beats_persistence']=result['test']['mae']<result['baseline']['mae']
        canonical(result);return result
