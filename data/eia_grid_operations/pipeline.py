"""EIA-930 bulk (EBA.zip) -> balancing-authority grid operations evidence.

EBA.txt holds one JSON line per series with every hourly point (about 90 million UTC
points in total), so emitting every hour would be many times the raw size. This stage emits:

* daily UTC aggregates for every UTC-hourly (``.H``) series: the sum of reported hours, with
  ``hours_reported`` recorded, plus a daily peak for demand;
* hourly observations for the most recent ``hourly_recent_days`` (parameter, default 35)
  before the artifact retrieval date.

Local-time duplicates (``.HL``) are skipped. EIA-930 timestamps are hour ending (UTC), so
hour ``YYYYMMDDTHH`` covers [HH-1, HH). Daily aggregates use that interval's start date.
Interchange (``ID``) series become ``eia:interchange:<from>:<to>`` resource flows.
"""
from datetime import datetime, timedelta, timezone
import re

from .evidence import Evidence, num

REGIONS = {'US48', 'CAL', 'CAR', 'CENT', 'FLA', 'MIDA', 'MIDW', 'NE', 'NY', 'NW', 'SE', 'SW', 'TEN', 'TEX',
           'CAN', 'MEX'}
KINDS = {
    'D': ('electricity_demand', 'MWh'),
    'DF': ('electricity_demand_forecast_day_ahead', 'MWh'),
    'NG': ('electricity_net_generation', 'MWh'),
    'TI': ('electricity_total_interchange', 'MWh'),
    'ID': ('electricity_interchange', 'MWh'),
    'CO2': ('power_sector_co2_emissions', 't CO2'),
}
UNITS = {'megawatthours': 'MWh', 'MWh': 'MWh', 'CO2 metric tons': 't CO2'}


def parse_series(series_id):
    """Return (respondent, other, kind, fuel or None, frequency) or None if not EIA-930 shaped."""
    parts = series_id.split('.')
    if len(parts) < 4 or parts[0] != 'EBA' or parts[-1] not in ('H', 'HL'):
        return None
    area, kind, rest = parts[1], parts[2], parts[3:-1]
    respondent, _, other = area.partition('-')
    fuel = None
    if kind == 'CO2':  # EBA.CPLW.CO2.EM.OIL.H
        fuel = rest[-1].lower() if rest else 'all'
    elif rest:
        fuel = rest[0].lower()
    return respondent, other or 'ALL', kind, fuel, parts[-1]


def hour_ending(stamp):
    return datetime.strptime(stamp, '%Y%m%dT%H').replace(tzinfo=timezone.utc)


def label_for(name, code):
    match = re.search(r'(?:for|from \w[\w ]*? for) (.+?) \(' + re.escape(code) + r'\)', name)
    return match[1] if match else code


def run(context):
    if context.raw_coverage()['sampled']:
        raise ValueError('eia_grid_operations has no sample adapter; acquire EBA.zip first')
    recent_days = int(context.parameters.get('hourly_recent_days', 35))
    out = Evidence(context, 'eia930')
    retrieved = datetime.fromisoformat(out.observed_at.replace('Z', '+00:00'))
    cutoff = (retrieved - timedelta(days=recent_days)).replace(minute=0, second=0, microsecond=0)
    try:
        for shard in context.raw_shards():
            with open(shard['path'], 'rb') as stream:
                if stream.read(4) != b'PK\x03\x04':
                    continue  # manifest.txt: vintage metadata only
            from worldmodel.raw_readers import iter_rows
            for locator, line in iter_rows([shard], {'format': 'jsonl', 'members': ['EBA.txt']}):
                yield from (r for r in series_records(out, locator, line, cutoff) if r is not None)
    finally:
        out.close()


def ba_entity(out, code, locator, label=None):
    if code in REGIONS:
        key = 'eia:region:' + code
        record = out.entity(key, 'location', label or code, locator, aggregate=True, eia930_region=code)
    else:
        key = 'eia:ba:' + code
        record = out.entity(key, 'organization', label or code, locator, eia930_code=code,
                            role='balancing authority or EIA-930 respondent')
    return key, record


def series_records(out, locator, line, cutoff):
    series_id = line.get('series_id')
    if not series_id:
        return  # category line
    parsed = parse_series(series_id)
    if parsed is None:
        raise ValueError(f'{locator}: unexpected EBA series id {series_id!r}')
    respondent, other, kind, fuel, frequency = parsed
    if frequency != 'H':
        return
    name = line.get('name', '')
    metric, unit = KINDS.get(kind, ('eia930_' + kind.lower(), UNITS.get(line.get('units'), line.get('units') or 'unknown')))
    source_unit = UNITS.get(line.get('units'))
    if source_unit and source_unit != unit:
        raise ValueError(f'{locator}: unit {line.get("units")!r} does not match {metric}')
    subject, record = ba_entity(out, respondent, locator, label_for(name, respondent) if other == 'ALL' or kind == 'ID' else None)
    if record:
        yield record
    dims = {'series_id': series_id}
    if kind == 'ID':
        counterpart, record = ba_entity(out, other, locator, label_for(name, other))
        if record:
            yield record
        flow = f'eia:interchange:{respondent}:{other}'
        record = out.entity(flow, 'resource_flow', f'Electricity interchange {respondent} to {other}', locator,
                            sign_convention='positive = net flow out of the first area into the second')
        if record:
            yield record
            for predicate, target in (('flow_source', subject), ('flow_destination', counterpart),
                                      ('transports_resource', 'commodity:electricity')):
                if target == 'commodity:electricity':
                    entity = out.entity(target, 'electricity', 'Electricity', locator)
                    if entity:
                        yield entity
                yield out.relation(flow, predicate, target, locator)
        subject = flow
    elif other != 'ALL':
        parent = subject
        subject = f'eia:ba:{respondent}:subregion:{other}'
        record = out.entity(subject, 'location', name.split(', hourly')[0], locator, eia930_subregion=other,
                            parent_respondent=respondent)
        if record:
            yield record
            yield out.relation(subject, 'within', parent, locator)
    if fuel:
        dims['fuel'] = fuel
    days = {}
    for stamp, raw in line.get('data') or []:
        end = hour_ending(stamp)
        value = num(raw)
        if end > cutoff:
            yield out.observation(subject, metric, value, unit, locator, valid_from=(end - timedelta(hours=1)).isoformat(),
                                  valid_to=end.isoformat(), dimensions={**dims, 'frequency': 'hourly'},
                                  missing_reason='source_blank')
        day = (end - timedelta(hours=1)).date()
        total, hours, peak = days.get(day, (0.0, 0, None))
        if value is not None:
            total += value
            hours += 1
            peak = value if peak is None or value > peak else peak
        days[day] = (total, hours, peak)
    for day in sorted(days):
        total, hours, peak = days[day]
        start, end = day.isoformat(), (day + timedelta(days=1)).isoformat()
        value = None if hours == 0 else (int(total) if float(total).is_integer() else round(total, 6))
        daily_dims = {**dims, 'frequency': 'daily', 'aggregation': 'sum_of_reported_hours'}
        yield out.observation(subject, metric, value, unit, locator, valid_from=start, valid_to=end,
                              dimensions=daily_dims, missing_reason='no_hours_reported', hours_reported=hours,
                              complete_day=hours == 24)
        if kind == 'D' and peak is not None:
            yield out.observation(subject, 'electricity_demand_peak_hourly', peak, 'MW', locator, valid_from=start,
                                  valid_to=end, dimensions={**dims, 'frequency': 'daily', 'aggregation': 'max_hourly'},
                                  hours_reported=hours)
