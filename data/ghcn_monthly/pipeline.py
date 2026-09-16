"""NOAA NCEI Global Summary of the Month (GSOM) archive -> monthly climate observations for long-record U.S. stations.

Input: `gsom-latest.tar.gz`, one CSV per station (all stations worldwide). The archive is streamed member by member;
only stations whose ID starts with `US` and that have values in at least `parameters.min_years` (default 30) distinct
years are emitted. GSOM values are metric: temperatures degC, precipitation and snowfall mm, degree days degC-days.
Station entities reuse the GHCN-Daily namespace `ghcn:station:<ID>` so they join with `ghcn_daily`.
The raw GSOM `<ELEMENT>_ATTRIBUTES` string (flags and missing-day counts) is kept as `attributes.flags`.
"""
import csv
import io
import re
import tarfile

ELEMENTS = {'TAVG': ('average_temperature', 'degC'), 'TMAX': ('maximum_temperature', 'degC'),
            'TMIN': ('minimum_temperature', 'degC'), 'PRCP': ('precipitation', 'mm'), 'SNOW': ('snowfall', 'mm'),
            'HTDD': ('heating_degree_days', 'degC_days'), 'CLDD': ('cooling_degree_days', 'degC_days')}
STATE = re.compile(r',\s*([A-Z]{2})\s+US\s*$')


def _number(text):
    text = (text or '').strip()
    if not text:
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def _month_end(year, month):
    return f'{year + (month == 12):04d}-{month % 12 + 1:02d}-01'


def run(context):
    from worldmodel.source_helpers import STATE_FIPS
    if not context.raw_inputs:
        raise ValueError('ghcn_monthly: raw acquisition required')
    min_years = int(context.parameters.get('min_years', 30))
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        for shard in context.raw_shards(index):
            with tarfile.open(shard['path'], 'r|*') as archive:
                for member in archive:
                    base = member.name.rsplit('/', 1)[-1]
                    if not member.isfile() or not base.startswith('US') or not base.endswith('.csv'):
                        continue
                    content = archive.extractfile(member).read().decode('utf-8', errors='replace')
                    yield from _station(context, index, observed, shard, member.name, base[:-4], content, min_years, STATE_FIPS)


def _station(context, index, observed, shard, name, station, content, min_years, state_fips):
    rows = list(csv.DictReader(io.StringIO(content, newline='')))
    years = {row['DATE'][:4] for row in rows
             if row.get('DATE') and any((row.get(element) or '').strip() for element in ('TAVG', 'TMAX', 'TMIN', 'PRCP'))}
    if len(years) < min_years:
        return
    prefix = f'shard:{shard["index"]}/member:{name}'
    subject = 'ghcn:station:' + station
    first = rows[0]
    evidence = context.raw_evidence(f'{prefix}/line:2', index)
    label = (first.get('NAME') or '').strip() or station
    attributes = {'latitude': _number(first.get('LATITUDE')), 'longitude': _number(first.get('LONGITUDE')),
                  'elevation_m': _number(first.get('ELEVATION')), 'years_with_data': len(years),
                  'first_year': int(min(years)), 'last_year': int(max(years))}
    yield {'kind': 'entity', 'id': 'gsom:entity:' + station, 'entity_id': subject, 'entity_type': 'location',
           'label': f'{label} ({station})', 'observed_at': observed, 'evidence': evidence,
           'attributes': {k: v for k, v in attributes.items() if v is not None}}
    match = STATE.search(label)
    if match and match.group(1) in state_fips:
        yield {'kind': 'assertion', 'id': f'gsom:within:{station}', 'subject': subject, 'predicate': 'within',
               'object': 'geo:US:state:' + state_fips[match.group(1)], 'observed_at': observed, 'evidence': evidence,
               'attributes': {}}
    for line, row in enumerate(rows, 2):
        stamp = (row.get('DATE') or '').strip()
        if not re.fullmatch(r'\d{4}-\d{2}', stamp):
            continue
        year, month = int(stamp[:4]), int(stamp[5:7])
        record_evidence = context.raw_evidence(f'{prefix}/line:{line}', index)
        for element, (metric, unit) in ELEMENTS.items():
            value = _number(row.get(element))
            if value is None:
                continue
            flags = (row.get(element + '_ATTRIBUTES') or '').strip()
            yield {'kind': 'observation', 'id': f'gsom:{station}:{element}:{year:04d}{month:02d}', 'subject': subject,
                   'metric': metric, 'value': value, 'unit': unit, 'valid_from': f'{year:04d}-{month:02d}-01',
                   'valid_to': _month_end(year, month), 'observed_at': observed, 'evidence': record_evidence,
                   'dimensions': {'frequency': 'monthly', 'source': 'gsom'},
                   'attributes': {'flags': flags} if flags else {}}
