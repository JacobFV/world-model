'Illustrative computed dataset with country-level record lineage. Not a validated index.'
from worldmodel.model import instant

def run(context):
    """Illustrative formula only, with exact record-level input provenance.

    100 * (w * min(income / scale, 1) + (1-w) * satisfaction / 10).
    This small demo groups in memory; production transforms should partition joins.
    """
    weight = context.parameters.get('income_weight', 0.5)
    scale = context.parameters.get('income_scale', 100000)
    if not isinstance(weight, (int, float)) or not 0 <= weight <= 1 or (not scale > 0):
        raise ValueError('income_weight must be in [0,1] and income_scale positive')
    source = context.parameters.get('input_dataset', 'demo_countries')
    groups = {}
    for record in context.records(source):
        country = record['dimensions']['country']
        metrics = groups.setdefault(country, {})
        if record['metric'] in metrics:
            raise ValueError('Duplicate country/metric; reconcile explicitly before computation')
        metrics[record['metric']] = record
    for country, metrics in sorted(groups.items()):
        income, satisfaction = (metrics['income'], metrics['life_satisfaction'])
        if income['unit'] != 'fictional_currency_per_person' or satisfaction['unit'] != 'points_0_10':
            raise ValueError('Unexpected input units')
        if income['value'] < 0 or not 0 <= satisfaction['value'] <= 10:
            raise ValueError('Input outside expected domain')
        if (income['valid_from'], income['valid_to']) != (satisfaction['valid_from'], satisfaction['valid_to']):
            raise ValueError('Cannot combine different valid periods')
        value = 100 * (weight * min(income['value'] / scale, 1) + (1 - weight) * satisfaction['value'] / 10)
        yield {'kind': 'observation', 'id': f'happiness:{country}', 'metric': 'rando_joes_happiness_index', 'value': round(value, 6), 'unit': 'index_points', 'dimensions': {'country': country}, 'valid_from': income['valid_from'], 'valid_to': income['valid_to'], 'observed_at': max(income['observed_at'], satisfaction['observed_at'], key=instant), 'methodology': 'Illustrative synthetic index; not a validated measure of happiness', 'evidence': [{'input': context.input_ref(source), 'record_id': r['id']} for r in (income, satisfaction)]}
