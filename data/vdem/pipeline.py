"""V-Dem country-year: curated indicators as tidy observations on iso3 country entities.

Only variables listed in variables.json are emitted; blank cells (outside a
series' coverage) are skipped rather than emitted as nulls, because the wide
file has thousands of structurally empty cells per country-year.
"""
import json
from pathlib import Path

from .evidence import Evidence, is_full, num, year_bounds

NON_ISO = {'PSG', 'SML', 'ZZB', 'PSB', 'XKX', 'VDR', 'DDR', 'YMD'}


def run(context):
    if not is_full(context):
        raise ValueError('vdem: requires the full V-Dem CSV acquisition (no sample adapter)')
    config = json.loads(Path(__file__).with_name('variables.json').read_text())
    variables, min_year = config['variables'], config['min_year']
    ev = Evidence(context, 'vdem')
    reader = {'format': 'csv', 'members': ['V-Dem-CY-Full+Others-v16.csv'], 'max_field_bytes': 64 * 1024 * 1024}
    checked = False
    for loc, row in context.raw_rows(**reader):
        if not checked:
            missing = [v['column'] for v in variables if v['column'] not in row]
            if missing:
                raise ValueError('vdem: configured columns missing from source: ' + ', '.join(missing))
            checked = True
        year = int(row['year'])
        if year < min_year:
            continue
        code = row['country_text_id'].strip().upper()
        key = 'iso3:' + code
        cow = num(row.get('COWcode'))
        entity = ev.entity(key, 'country', row['country_name'], loc, vdem_country_id=num(row.get('country_id')),
                           cow_code=cow, iso3=code, code_standard='nonstandard_or_historical' if code in NON_ISO else 'iso3166_1_or_vdem')
        if entity is not None:
            yield entity
        start, end = year_bounds(year)
        for spec in variables:
            value = num(row[spec['column']])
            if value is None:
                continue
            attrs = {}
            if spec.get('bounds'):
                low, high = num(row.get(spec['column'] + '_codelow')), num(row.get(spec['column'] + '_codehigh'))
                if low is not None or high is not None:
                    attrs['interval_68'] = [low, high]
            yield ev.observation(key, spec['metric'], value, spec['unit'], loc, valid_from=start, valid_to=end,
                                 dimensions={'frequency': 'annual'}, **attrs)
    if not checked:
        raise ValueError('vdem: no rows read')
    ev.close()
