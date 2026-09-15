"""Chronological descriptive AR(1) fitting; no causal identification claims."""
from copy import deepcopy
import math
from .model import instant
from .util import canonical


def fit_ar1(rows, train_end):
    cutoff = instant(train_end)
    if len(rows) > 100000:
        raise ValueError('Calibration row budget exceeded')
    canonical(rows)
    signatures = {canonical([row.get('series'), row.get('subject'), row.get('metric')]) for row in rows}
    if len(signatures)>1:
        raise ValueError('Calibration requires a single series identity')
    valid, times, units, excluded = [], set(), set(), 0
    for row in rows:
        at = instant(row['time'])
        if at in times:
            raise ValueError('Duplicate observation time; reconcile explicitly')
        times.add(at)
        units.add(row.get('unit'))
        if row['value'] is None:
            excluded += 1
            continue
        if type(row['value']) not in (int, float) or not math.isfinite(row['value']):
            raise ValueError('Calibration values must be finite numbers or null')
        valid.append((at, deepcopy(row)))
    if len(units) > 1:
        raise ValueError('Calibration requires a single unit and series')
    valid.sort(key=lambda item: item[0])
    train = [(valid[i-1][1], valid[i][1]) for i in range(1,len(valid)) if valid[i][0] <= cutoff]
    test = [(valid[i-1][1], valid[i][1]) for i in range(1,len(valid)) if valid[i][0] > cutoff]
    if len(train) < 3:
        raise ValueError('At least three training pairs are required')
    if len(test) < 2:
        raise ValueError('At least two holdout observations are required')
    xmean = sum(a['value'] for a,b in train)/len(train)
    ymean = sum(b['value'] for a,b in train)/len(train)
    variance = sum((a['value']-xmean)**2 for a,b in train)
    if variance == 0:
        raise ValueError('Training inputs have no variation')
    coefficient = sum((a['value']-xmean)*(b['value']-ymean) for a,b in train)/variance
    intercept = ymean-coefficient*xmean
    predictions = [{'time':b['time'], 'actual':b['value'], 'prediction':intercept+coefficient*a['value'],
                    'persistence':a['value'], 'input_time':a['time'],
                    'input_evidence':a.get('evidence',[]), 'target_evidence':b.get('evidence',[])} for a,b in test]
    mae = sum(abs(p['prediction']-p['actual']) for p in predictions)/len(predictions)
    baseline = sum(abs(p['persistence']-p['actual']) for p in predictions)/len(predictions)
    result = {'model':'ar1_next_observation', 'status':'descriptive_fit', 'causally_calibrated':False,
              'parameters':{'intercept':intercept,'coefficient':coefficient}, 'unit':next(iter(units)),
              'training':{'through':train_end,'pairs':len(train),'first_time':train[0][0]['time'],
                          'last_time':train[-1][1]['time'],
                          'evidence':[{'input':a.get('evidence',[]),'target':b.get('evidence',[])} for a,b in train]},
              'holdout':{'count':len(test),'mae':mae,'persistence_mae':baseline,'beats_persistence':mae<baseline},
              'predictions':predictions,'excluded_missing':excluded,
              'limitations':['Fixed coefficients, rolling one-step evaluation using previous observed value.',
                             'Steps mean next available observation, not fixed calendar duration.',
                             'Retrospective data vintages do not establish real-time backtest validity.',
                             'This fit does not estimate responses to policy interventions.']}
    canonical(result)
    return result
