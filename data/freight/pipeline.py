"""Freight Analysis Framework (FAF) origin-destination flows.

Full sharded acquisitions contain three ZIP archives:

* ``FAF5.7.1.zip``: FAF zone (CFS area) flows, 2.67M rows;
* ``FAF5.7.1_State.zip``: the same flows at state level;
* ``FAF6.0_State.zip``: FAF6.0 2022-benchmark state flows (tons and value, 2022).

Rows carry domestic origin/destination, domestic mode, SCTG2 commodity, trade
type, distance band and foreign legs. To keep evidence compact and tidy the
normalizer emits aggregate observations (``attributes.aggregate = true``):

1. ``state_od``: FAF5.7.1 state flows summed over trade type, distance band and
   foreign legs per (origin state, destination state, domestic mode, SCTG2):
   tons, constant-2017 value, current value and ton-miles for 2017-2024 and
   baseline forecasts 2030-2050;
2. ``zone_od``: FAF5.7.1 zone flows summed the same way per (origin zone,
   destination zone, domestic mode, SCTG2): tons and constant-2017 value,
   2017-2024;
3. ``foreign``: FAF5.7.1 state import/export flows per (foreign region, US
   state, trade direction, foreign mode, SCTG2): tons and constant-2017 value,
   2017-2024 and forecasts;
4. ``faf6_state``: FAF6.0 2022 state flows per (origin, destination, mode,
   SCTG2, trade type): tons and value;
5. ``state_od_history`` (optional shard): FAF5.7.1 reprocessed 1997, 2002, 2007
   and 2012 state flows, aggregated like ``state_od``;
6. ``state_od_scenarios`` (optional shard): low and high forecast scenarios
   2030-2050 for tons and constant-2017 value (``dimensions.scenario``).

Every observation has ``attributes.projection`` (true for forecast years).

Zero values are omitted: a missing year for a published key means zero flow.
Aggregation state is bounded by the FAF geography x mode x commodity key space
(about one million zone keys), not by row count.
"""
import io
import zipfile
from array import array

from worldmodel.raw_readers import iter_rows
from worldmodel.workbooks import xlsx_rows

HISTORICAL = tuple(range(2017, 2025))
FORECAST = (2030, 2035, 2040, 2045, 2050)
METRICS = {'tons': ('freight_tons', 'thousand_short_tons'), 'value': ('freight_value', 'million_USD_2017'),
           'current_value': ('freight_value_current', 'million_USD_current'), 'tmiles': ('freight_ton_miles', 'million_ton_miles')}
FAF6_UNITS = {'tons': ('freight_tons', 'thousand_short_tons'), 'value': ('freight_value', 'million_USD_2022')}
MODE_SLUGS = {'1': 'truck', '2': 'rail', '3': 'water', '4': 'air', '5': 'multiple_modes_and_mail', '6': 'pipeline',
              '7': 'other_and_unknown', '8': 'no_domestic_mode'}
TRADE = {'1': 'domestic', '2': 'import', '3': 'export'}
ARCHIVES = {'zone': 'FAF5.7.1.zip', 'state': 'FAF5.7.1_State.zip', 'faf6': 'FAF6.0_State.zip',
            'history': 'FAF5.7.1_Reprocessed_1997-2012_State.zip', 'scenarios': 'FAF5.7.1_State_HiLoForecasts.zip'}
REQUIRED_ARCHIVES = ('zone', 'state', 'faf6')
HISTORY_YEARS = (1997, 2002, 2007, 2012)


def run(context):
    if not context.raw_inputs or context.raw_coverage()['layout'] != 'shards':
        raise ValueError('freight: no sample normalizer available; BEA, freight and USDA samples are unavailable')
    yield from _normalize_full(context)


def _columns(kind, years):
    return [f'{kind}_{year}' for year in years]


def _series(metric_keys, scenarios=(None,)):
    """Ordered (column, metric key, year, scenario) for aggregation vectors."""
    out = []
    for scenario in scenarios:
        for key, years in metric_keys:
            suffix = f'_{scenario}' if scenario else ''
            out.extend((f'{key}_{year}{suffix}', key, year, scenario) for year in years)
    return out


STATE_SERIES = _series([('tons', HISTORICAL + FORECAST), ('value', HISTORICAL + FORECAST),
                        ('current_value', HISTORICAL[1:]), ('tmiles', HISTORICAL + FORECAST)])
ZONE_SERIES = _series([('tons', HISTORICAL), ('value', HISTORICAL)])
FOREIGN_SERIES = _series([('tons', HISTORICAL + FORECAST), ('value', HISTORICAL + FORECAST)])
HISTORY_SERIES = _series([('tons', HISTORY_YEARS), ('value', HISTORY_YEARS), ('current_value', HISTORY_YEARS), ('tmiles', HISTORY_YEARS)])
SCENARIO_SERIES = _series([('tons', FORECAST), ('value', FORECAST)], scenarios=('low', 'high'))


def _float(value):
    if value is None:
        return 0.0
    text = value.strip()
    return float(text) if text else 0.0


def _aggregate(rows, key_function, series):
    table = {}
    counts = {}
    columns = [column for column, *_ in series]
    width = len(columns)
    for _, row in rows:
        key = key_function(row)
        if key is None:
            continue
        vector = table.get(key)
        if vector is None:
            vector = table[key] = array('d', bytes(8 * width))
            counts[key] = 0
        counts[key] += 1
        for position, column in enumerate(columns):
            value = row[column]
            if value and value != '0':
                vector[position] += float(value)
    return table, counts


def _labels(archive_path):
    with zipfile.ZipFile(archive_path) as archive:
        member = next((n for n in archive.namelist() if n.lower().endswith('.xlsx')), None)
        if member is None:
            return {}
        workbook = zipfile.ZipFile(io.BytesIO(archive.read(member)))
    import re
    names = re.findall(r'<sheet [^>]*name="([^"]+)"', workbook.read('xl/workbook.xml').decode('utf-8'))
    labels = {}
    for position, name in enumerate(names, 1):
        member = f'xl/worksheets/sheet{position}.xml'
        if member not in workbook.namelist() or name == 'Data Dictionary':
            continue
        rows = xlsx_rows(workbook, {'max_uncompressed_bytes': 64 * 1024 * 1024, 'archive_member': member, 'format': 'xlsx'})
        table = {}
        for row in rows:
            code = (row.get('Numeric Label') or '').strip()
            if code:
                table[code] = {'label': (row.get('Short Description') or row.get('Description') or code).strip(),
                               'long': (row.get('Long Description') or row.get('Description') or '').strip()}
        labels[name] = table
    return labels


def _normalize_full(context):
    raw = context.raw_inputs[0]
    shards = {shard.get('name'): shard for shard in context.raw_shards()}
    missing = [ARCHIVES[key] for key in REQUIRED_ARCHIVES if ARCHIVES[key] not in shards]
    if missing:
        raise ValueError('FAF acquisition lacks shards: ' + ', '.join(missing))
    complete = context.raw_coverage()['complete']
    retrieved = context.raw_receipt()['retrieved_at']
    for shard in shards.values():
        shard.setdefault('retrieved_at', retrieved)
    labels = _labels(shards[ARCHIVES['zone']]['path'])
    states = labels.get('State', {})
    zones = labels.get('FAF Zone (Domestic)', {})
    foreign = labels.get('FAF Zone (Foreign)', {})
    commodities = labels.get('Commodity (SCTG2)', {})
    modes = labels.get('Mode', {})

    def evidence(archive, member=None):
        shard = shards[ARCHIVES[archive]]
        locator = f"shard:{shard['index']}" + (f'/member:{member}' if member else '')
        return shard, [{'input': raw, 'locator': locator}]

    def entity(archive, member, key, entity_type, label, **attributes):
        shard, ev = evidence(archive, member)
        return {'kind': 'entity', 'id': key, 'entity_id': key, 'entity_type': entity_type, 'label': label,
                'observed_at': shard['retrieved_at'], 'evidence': ev, 'attributes': attributes}

    def assertion(archive, member, identity, subject, predicate, target):
        shard, ev = evidence(archive, member)
        return {'kind': 'assertion', 'id': identity, 'observed_at': shard['retrieved_at'], 'evidence': ev,
                'subject': subject, 'predicate': predicate, 'object': target}

    # Reference entities from the FAF5 metadata workbook.
    meta = 'FAF5_metadata.xlsx'
    for code, item in sorted(states.items()):
        key = 'geo:US:state:' + code.zfill(2)
        yield entity('zone', meta, key, 'state', item['label'], fips=code.zfill(2), faf_code=code)
    for code, item in sorted(zones.items()):
        key = 'faf5:zone:' + code
        yield entity('zone', meta, key, 'location', item['label'], faf_zone=code, description=item['long'],
                     geography='FAF5 domestic zone (CFS area or state remainder)')
        yield assertion('zone', meta, key + ':within', key, 'within', 'geo:US:state:' + code[:2])
    for code, item in sorted(foreign.items()):
        key = 'faf5:foreign_region:' + code
        yield entity('zone', meta, key, 'location', item['label'], faf_zone=code, geography='FAF5 foreign region')
    for code, item in sorted(commodities.items()):
        key = 'sctg2:' + code.zfill(2)
        yield entity('zone', meta, key, 'commodity', item['label'], sctg2=code.zfill(2),
                     classification='Standard Classification of Transported Goods, 2-digit')

    def observations(archive, member, scope, key_dims, vectors, counts, series, release):
        shard, ev = evidence(archive, member)
        observed = shard['retrieved_at']
        for key in sorted(vectors):
            vector = vectors[key]
            subject, dimensions, identity = key_dims(key)
            for position, (_, metric_key, year, scenario) in enumerate(series):
                value = vector[position]
                if value == 0.0:
                    continue
                metric, unit = (FAF6_UNITS if release == 'FAF6.0' else METRICS)[metric_key]
                forecast = year > 2024
                suffix = f':{scenario}' if scenario else ''
                yield {'kind': 'observation', 'id': f'{release.lower()}:{scope}:{identity}:{metric}{suffix}:{year}',
                       'observed_at': observed, 'evidence': ev, 'subject': subject, 'metric': metric,
                       'value': round(value, 6), 'unit': unit, 'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01',
                       'dimensions': {**dimensions, 'scenario': scenario} if scenario else dimensions,
                       'attributes': {'release': release, 'aggregation': scope, 'aggregate': True, 'source_rows': counts[key],
                                      'projection': forecast,
                                      'series_type': (f'{scenario}_forecast' if scenario else 'baseline_forecast') if forecast else
                                                     ('reprocessed_benchmark' if year < 2017 else
                                                      'benchmark_year' if year in (2017, 2022) and release == 'FAF5.7.1' or release == 'FAF6.0' else 'annual_estimate'),
                                      'estimate_basis': 'modeled FAF estimate', 'frequency': 'annual',
                                      'complete_source': complete}}

    def domestic_mode(row):
        return MODE_SLUGS.get(row['dms_mode'].strip(), 'mode_' + row['dms_mode'].strip())

    # 1. State OD by domestic mode and commodity (FAF5.7.1_State).
    shard = shards[ARCHIVES['state']]
    member = 'FAF5.7.1_State.csv'
    rows = iter_rows([shard], {'format': 'csv', 'members': [member]})
    vectors, counts = _aggregate(rows, lambda r: (r['dms_origst'].zfill(2), r['dms_destst'].zfill(2), domestic_mode(r), r['sctg2'].zfill(2)), STATE_SERIES)
    yield from observations('state', member, 'state_od',
                            lambda k: (f'geo:US:state:{k[0]}', {'destination': f'geo:US:state:{k[1]}', 'mode': k[2], 'commodity': f'sctg2:{k[3]}',
                                                                 'geography': 'state', 'includes': 'domestic legs of domestic, import and export flows'},
                                       f'{k[0]}:{k[1]}:{k[2]}:{k[3]}'),
                            vectors, counts, STATE_SERIES, 'FAF5.7.1')
    del vectors, counts

    # 3. Foreign legs at state level (imports: foreign origin -> US destination state; exports: US origin -> foreign).
    rows = iter_rows([shard], {'format': 'csv', 'members': [member]})

    def foreign_key(row):
        trade = row['trade_type'].strip()
        if trade == '2':
            return ('import', row['fr_orig'].strip(), row['dms_destst'].zfill(2), MODE_SLUGS.get(row['fr_inmode'].strip(), 'mode_' + row['fr_inmode'].strip()), row['sctg2'].zfill(2))
        if trade == '3':
            return ('export', row['fr_dest'].strip(), row['dms_origst'].zfill(2), MODE_SLUGS.get(row['fr_outmode'].strip(), 'mode_' + row['fr_outmode'].strip()), row['sctg2'].zfill(2))
        return None

    vectors, counts = _aggregate(rows, foreign_key, FOREIGN_SERIES)

    def foreign_dims(k):
        direction, region, state, mode, sctg = k
        region_key, state_key = f'faf5:foreign_region:{region}', f'geo:US:state:{state}'
        subject, other = (region_key, state_key) if direction == 'import' else (state_key, region_key)
        return subject, {'destination': other, 'trade_direction': direction, 'foreign_mode': mode, 'commodity': f'sctg2:{sctg}',
                         'geography': 'foreign_region_to_state' if direction == 'import' else 'state_to_foreign_region'}, f'{direction}:{region}:{state}:{mode}:{sctg}'

    yield from observations('state', member, 'foreign', foreign_dims, vectors, counts, FOREIGN_SERIES, 'FAF5.7.1')
    del vectors, counts

    # 2. Zone OD (FAF5.7.1).
    shard = shards[ARCHIVES['zone']]
    member = 'FAF5.7.1.csv'
    rows = iter_rows([shard], {'format': 'csv', 'members': [member]})
    vectors, counts = _aggregate(rows, lambda r: (r['dms_orig'].strip(), r['dms_dest'].strip(), domestic_mode(r), r['sctg2'].zfill(2)), ZONE_SERIES)
    yield from observations('zone', member, 'zone_od',
                            lambda k: (f'faf5:zone:{k[0]}', {'destination': f'faf5:zone:{k[1]}', 'mode': k[2], 'commodity': f'sctg2:{k[3]}',
                                                             'geography': 'faf5_zone', 'includes': 'domestic legs of domestic, import and export flows'},
                                       f'{k[0]}:{k[1]}:{k[2]}:{k[3]}'),
                            vectors, counts, ZONE_SERIES, 'FAF5.7.1')
    del vectors, counts

    def state_key(r):
        origin, destination = r['dms_origst'].strip(), r['dms_destst'].strip()
        if not origin or not destination:
            return None  # no domestic leg to attribute
        return (origin.zfill(2), destination.zfill(2), domestic_mode(r), r['sctg2'].zfill(2))

    def state_dims(k):
        return (f'geo:US:state:{k[0]}', {'destination': f'geo:US:state:{k[1]}', 'mode': k[2], 'commodity': f'sctg2:{k[3]}',
                                          'geography': 'state', 'includes': 'domestic legs of domestic, import and export flows'},
                f'{k[0]}:{k[1]}:{k[2]}:{k[3]}')

    # 5. Reprocessed FAF5 history (1997, 2002, 2007, 2012) at state level, same aggregation as state_od.
    if ARCHIVES['history'] in shards:
        member = 'FAF5.7.1_Reprocessed_1997-2012_State.csv'
        rows = iter_rows([shards[ARCHIVES['history']]], {'format': 'csv', 'members': [member]})
        vectors, counts = _aggregate(rows, state_key, HISTORY_SERIES)
        yield from observations('history', member, 'state_od_history', state_dims, vectors, counts, HISTORY_SERIES, 'FAF5.7.1')
        del vectors, counts

    # 6. Low and high forecast scenarios at state level (baseline forecasts are in state_od).
    if ARCHIVES['scenarios'] in shards:
        member = 'FAF5.7.1_State_HiLoForecasts.csv'
        rows = iter_rows([shards[ARCHIVES['scenarios']]], {'format': 'csv', 'members': [member]})
        vectors, counts = _aggregate(rows, state_key, SCENARIO_SERIES)
        yield from observations('scenarios', member, 'state_od_scenarios', state_dims, vectors, counts, SCENARIO_SERIES, 'FAF5.7.1')
        del vectors, counts

    # 4. FAF6.0 state 2022 benchmark, full dimensional detail.
    shard = shards[ARCHIVES['faf6']]
    member = 'FAF6.0_State.csv'
    series = [('tons_2022', 'tons', 2022, None), ('value_2022', 'value', 2022, None)]
    rows = iter_rows([shard], {'format': 'csv', 'members': [member]})
    vectors, counts = _aggregate(rows, lambda r: (r['dms_origst'].zfill(2) if r['dms_origst'].strip() else 'foreign:' + r['fr_orig'].strip(),
                                                  r['dms_destst'].zfill(2) if r['dms_destst'].strip() else 'foreign:' + r['fr_dest'].strip(),
                                                  domestic_mode(r), r['sctg2'].zfill(2), TRADE.get(r['trade_type'].strip(), r['trade_type'].strip())),
                                 series)

    def place(code):
        return f'faf6:foreign_region:{code[8:]}' if code.startswith('foreign:') else f'geo:US:state:{code}'

    yield from observations('faf6', member, 'faf6_state',
                            lambda k: (place(k[0]), {'destination': place(k[1]), 'mode': k[2], 'commodity': f'sctg2:{k[3]}', 'trade_type': k[4],
                                                     'geography': 'state'}, f'{k[0]}:{k[1]}:{k[2]}:{k[3]}:{k[4]}'.replace('foreign:', 'f')),
                            vectors, counts, series, 'FAF6.0')
