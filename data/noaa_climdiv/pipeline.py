"""NOAA nClimDiv fixed-width files -> monthly and annual climate observations for counties, states and regions.

Every raw shard is one element/geography file (identified from its request URL or imported file name). County
series are emitted monthly from `parameters.county_monthly_from` (default 1991) and as annual aggregates for the
full record (1895+); state and regional series are emitted monthly and annually for the full record. Annual
aggregates: precipitation and degree days are 12-month sums, temperatures and drought indices 12-month means;
incomplete years are skipped. nClimDiv is recomputed monthly, so values can change between file versions.
"""
import re

FILE = re.compile(r'climdiv-([a-z0-9]{4})(cy|st|dv)-v[0-9.]+-(\d{8})')
SUMMED = {'01', '25', '26'}


def _month_end(year, month):
    return f'{year + (month == 12):04d}-{month % 12 + 1:02d}-01'


def run(context):
    from .helpers import ELEMENTS, NCEI_TO_FIPS, REGIONS, parse_line
    if not context.raw_inputs:
        raise ValueError('noaa_climdiv: raw acquisition required')
    county_from = int(context.parameters.get('county_monthly_from', 1991))
    for index, _ in enumerate(context.raw_inputs):
        receipt = context.raw_receipt(index)
        observed = receipt['retrieved_at']
        areas = set()  # ~3.2k counties + ~100 states/regions
        for shard in context.raw_shards(index):
            name = str((shard.get('request') or {}).get('url') or receipt.get('original_name') or '')
            match = FILE.search(name)
            if not match:
                continue  # readme and other documentation
            code, level, version = match.groups()
            county = level == 'cy'
            if level == 'dv':
                continue  # climate divisions are not configured
            with open(shard['path'], encoding='ascii') as stream:
                for number, line in enumerate(stream, 1):
                    parsed = parse_line(line, county)
                    if parsed is None:
                        continue
                    area, element, year, values = parsed
                    if element not in ELEMENTS:
                        raise ValueError(f'{name}: unknown element code {element}')
                    metric, unit = ELEMENTS[element]
                    locator = f'shard:{shard["index"]}/line:{number}'
                    evidence = context.raw_evidence(locator, index)
                    if county:
                        state = NCEI_TO_FIPS.get(area[0])
                        if state is None:
                            raise ValueError(f'{locator}: unmapped NCEI state code {area[0]}')
                        subject, typ, label, key = f'geo:US:county:{state}{area[1]}', 'county', None, f'cy{state}{area[1]}'
                    elif area[0] <= '050' and area[0][1:] in NCEI_TO_FIPS:
                        state = NCEI_TO_FIPS[area[0][1:]]
                        subject, typ, label, key = f'geo:US:state:{state}', 'state', None, f'st{state}'
                    else:
                        subject, typ = f'noaa:climregion:{area[0]}', 'location'
                        label, key = REGIONS.get(area[0], f'NCEI climate region {area[0]}'), f'rg{area[0]}'
                    if subject not in areas:
                        areas.add(subject)
                        yield {'kind': 'entity', 'id': 'climdiv:entity:' + subject, 'entity_id': subject, 'entity_type': typ,
                               'label': label or subject, 'observed_at': observed, 'evidence': evidence,
                               'attributes': {'ncei_area_code': ''.join(area)}}
                    dims = {'dataset_version': version}
                    if not county or year >= county_from:
                        for month, value in enumerate(values, 1):
                            if value is None:
                                continue
                            yield {'kind': 'observation', 'id': f'climdiv:{key}:{code}:{year}{month:02d}', 'subject': subject,
                                   'metric': metric, 'value': value, 'unit': unit, 'valid_from': f'{year:04d}-{month:02d}-01',
                                   'valid_to': _month_end(year, month), 'observed_at': observed, 'evidence': evidence,
                                   'dimensions': {**dims, 'frequency': 'monthly'}, 'attributes': {}}
                    if all(value is not None for value in values):
                        total = sum(values)
                        annual = round(total if element in SUMMED else total / 12, 3)
                        yield {'kind': 'observation', 'id': f'climdiv:{key}:{code}:{year}', 'subject': subject,
                               'metric': metric, 'value': annual, 'unit': unit, 'valid_from': f'{year:04d}-01-01',
                               'valid_to': f'{year + 1:04d}-01-01', 'observed_at': observed, 'evidence': evidence,
                               'dimensions': {**dims, 'frequency': 'annual',
                                              'aggregation': 'sum' if element in SUMMED else 'mean'},
                               'attributes': {}}
