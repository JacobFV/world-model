'ACS, decennial census and population estimates'
import json
import re
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS


def run(context):
    """Sample artifacts keep the legacy adapter; full PEP files stream through `_run_full`."""
    if not context.raw_inputs:
        raise ValueError('census_population: no raw artifact supplied')
    if _sampled(context):
        yield from _run_sample(context)
    else:
        yield from _run_full(context)


def _sampled(context, index=0):
    try:
        coverage = context.raw_coverage(index)
    except Exception:
        return True
    return not isinstance(coverage, dict) or bool(coverage.get('sampled'))


# ---------------------------------------------------------------- full PEP files
READER = {'format': 'csv', 'encoding': 'latin-1'}
COMPONENT = re.compile(r'^(R?)(POPESTIMATE|NPOPCHG_?|BIRTHS?|DEATHS?|NATURALCHG|NATURALINC|INTERNATIONALMIG|DOMESTICMIG|NETMIG|RESIDUAL|GQESTIMATES)(\d{4})$')
METRICS = {'POPESTIMATE': 'population', 'NPOPCHG': 'population_change', 'BIRTHS': 'births', 'DEATHS': 'deaths',
           'BIRTH': 'births', 'DEATH': 'deaths',
           'NATURALCHG': 'natural_change', 'NATURALINC': 'natural_change', 'INTERNATIONALMIG': 'net_international_migration',
           'DOMESTICMIG': 'net_domestic_migration', 'NETMIG': 'net_migration', 'RESIDUAL': 'population_change_residual',
           'GQESTIMATES': 'group_quarters_population'}
RACE = {'WA': 'white_alone', 'BA': 'black_alone', 'IA': 'aian_alone', 'AA': 'asian_alone', 'NA': 'nhpi_alone',
        'TOM': 'two_or_more_races'}
AGEGRP = {str(i): (f'{5 * (i - 1)}-{5 * i - 1}' if i < 18 else '85+') for i in range(1, 19)}
REGIONS = {'1': 'Northeast', '2': 'Midwest', '3': 'South', '4': 'West'}


def _value(text):
    text = (text or '').strip()
    if text in ('', 'X', '(X)', 'NA', '.'):
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


class _Out:
    def __init__(self, context, index):
        self.context, self.index = context, index
        self.observed = context.raw_receipt(index)['retrieved_at']
        self.entities = set()
        self.released = None

    def entity(self, key, typ, label, locator, **attrs):
        if key in self.entities:
            return None
        self.entities.add(key)
        return {'kind': 'entity', 'id': 'pep:entity:' + key, 'entity_id': key, 'entity_type': typ, 'label': label or key,
                'observed_at': self.observed, 'evidence': self.context.raw_evidence(locator, self.index), 'attributes': attrs}

    def within(self, child, parent, locator, **attrs):
        return {'kind': 'assertion', 'id': f'pep:within:{child}:{parent}', 'subject': child, 'predicate': 'within',
                'object': parent, 'observed_at': self.observed, 'evidence': self.context.raw_evidence(locator, self.index),
                'attributes': attrs}

    def obs(self, rid, subject, metric, value, unit, start, end, locator, dims, **attrs):
        record = {'kind': 'observation', 'id': rid, 'subject': subject, 'metric': metric, 'value': value, 'unit': unit,
                  'valid_from': start, 'valid_to': end, 'observed_at': self.observed,
                  'evidence': self.context.raw_evidence(locator, self.index), 'dimensions': dims, 'attributes': attrs}
        if self.released:
            attrs['released_at'] = self.released  # vintage availability (real-time knowledge time)
        if value is None:
            record['missing_reason'] = 'source_missing'
        return record


def _component_period(name, year, vintage):
    """PEP components for year Y cover 1 July Y-1 .. 1 July Y; the first year starts at the April 1 census base."""
    if name in ('POPESTIMATE', 'GQESTIMATES'):
        return f'{year}-07-01', f'{year}-07-02'
    first = 2020 if vintage == 2024 else 2010
    if year == first:
        return f'{year}-04-01', f'{year}-07-01'
    return f'{year - 1}-07-01', f'{year}-07-01'


def _geography(row, kind):
    """Return (entity id, type, label, parent id, key) for a PEP row, or None to skip."""
    level = row.get('SUMLEV', '')
    state = row.get('STATE', '')
    if kind == 'cbsa':
        if row.get('STCOU'):
            return None
        if row.get('MDIV'):
            code = row['MDIV']
            return 'geo:US:metdiv:' + code, 'location', row['NAME'], 'geo:US:cbsa:' + row['CBSA'], 'metdiv:' + code
        return 'geo:US:cbsa:' + row['CBSA'], 'location', row['NAME'], None, 'cbsa:' + row['CBSA']
    if level == '010':
        return 'geo:US', 'country', 'United States', None, 'US'
    if level == '020':
        return 'geo:US:region:' + row['REGION'], 'location', row['NAME'], 'geo:US', 'region:' + row['REGION']
    if level == '030':
        return 'geo:US:division:' + row['DIVISION'], 'location', row['NAME'], 'geo:US:region:' + row['REGION'], 'division:' + row['DIVISION']
    if level == '040':
        return 'geo:US:state:' + state, 'state', row.get('NAME') or row.get('STNAME'), 'geo:US', 'state:' + state
    if level == '050':
        geoid = state + row['COUNTY']
        return 'geo:US:county:' + geoid, 'county', row.get('CTYNAME') or row.get('NAME'), 'geo:US:state:' + state, 'county:' + geoid
    if level == '162':
        geoid = state + row['PLACE']
        return 'geo:US:place:' + geoid, 'jurisdiction', row['NAME'], 'geo:US:state:' + state, 'place:' + geoid
    if level == '061':
        geoid = state + row['COUNTY'] + row['COUSUB']
        return ('geo:US:cousub:' + geoid, 'jurisdiction', row['NAME'], 'geo:US:county:' + state + row['COUNTY'],
                'cousub:' + geoid)
    return None


def _file_kind(row):
    if 'AGEGRP' in row:
        return 'characteristics'
    if 'CBSA' in row:
        return 'cbsa'
    if 'PLACE' in row:
        return 'subcounty'
    if 'COUNTY' in row:
        return 'county'
    return 'state'


def _released(context, index):
    """Shard index -> release timestamp (HTTP Last-Modified of the published file, an upper bound on availability)."""
    from email.utils import parsedate_to_datetime
    result = {}
    for shard in context.raw_shards(index):
        stamp = shard.get('last_modified')
        if stamp:
            try:
                result[shard['index']] = parsedate_to_datetime(stamp).strftime('%Y-%m-%dT%H:%M:%SZ')
            except (TypeError, ValueError):
                pass
    return result


def _vintage(row):
    """A PEP vintage is the last POPESTIMATE year in the file (each vintage revises back to the last census)."""
    years = [int(field[-4:]) for field in row if field.startswith('POPESTIMATE') and field[-4:].isdigit()]
    return max(years) if years else None


def _run_full(context):
    for index, _ in enumerate(context.raw_inputs):
        out = _Out(context, index)
        released = _released(context, index)
        for locator, row in context.raw_rows(index, **READER):
            kind = _file_kind(row)
            out.released = released.get(int(locator.split('/', 1)[0].split(':')[1]))
            vintage = _vintage(row) or 2024
            if kind == 'characteristics':
                yield from _characteristics(out, locator, row)
                continue
            if kind == 'county' and row.get('SUMLEV') != '050':
                continue  # state rows repeat the state totals files
            if kind == 'subcounty' and row.get('SUMLEV') not in ('162', '061'):
                continue  # state/county rows repeat other files; place-in-county parts are partial geographies
            geography = _geography(row, kind)
            if geography is None:
                if kind == 'cbsa' and row.get('STCOU'):
                    county = 'geo:US:county:' + row['STCOU']
                    parent = 'geo:US:metdiv:' + row['MDIV'] if row.get('MDIV') else 'geo:US:cbsa:' + row['CBSA']
                    yield out.within(county, parent, locator, relationship='cbsa_delineation_2023', vintage=vintage)
                continue
            subject, typ, label, parent, key = geography
            record = out.entity(subject, typ, label, locator, **({'lsad': row['LSAD']} if row.get('LSAD') else {}))
            if record:
                yield record
                if parent:
                    yield out.within(subject, parent, locator, relationship='census_hierarchy')
            dims = {'vintage': vintage}
            prefix = f'pep{vintage % 100:02d}:{key}'
            for field, text in row.items():
                if field in ('CENSUS2010POP', 'ESTIMATESBASE2020', 'ESTIMATESBASE2010', 'GQESTIMATESBASE2020'):
                    year = 2010 if '2010' in field else 2020
                    metric = 'group_quarters_population' if field.startswith('GQ') else 'population'
                    basis = 'decennial_census' if field.startswith('CENSUS') else 'estimates_base'
                    yield out.obs(f'{prefix}:{metric}:{basis}:{year}', subject, metric, _value(text), 'people',
                                  f'{year}-04-01', f'{year}-04-02', locator, {**dims, 'basis': basis}, source_field=field)
                    continue
                match = COMPONENT.match(field)
                if not match:
                    continue
                rate, name, year = match.group(1), match.group(2).rstrip('_'), int(match.group(3))
                metric = METRICS[name] + ('_rate' if rate else '')
                unit = 'per_1000_population' if rate else 'people'
                start, end = _component_period(name, year, vintage)
                yield out.obs(f'{prefix}:{metric}:{year}', subject, metric, _value(text), unit, start, end, locator, dims,
                              source_field=field)


def _characteristics(out, locator, row):
    year_code = int(row['YEAR'])
    if year_code < 2 or row.get('SUMLEV') != '050':
        return  # YEAR 1 is the April 2020 estimates base
    year = 2018 + year_code
    geoid = row['STATE'] + row['COUNTY']
    subject = 'geo:US:county:' + geoid
    record = out.entity(subject, 'county', row['CTYNAME'], locator)
    if record:
        yield record
    start, end = f'{year}-07-01', f'{year}-07-02'
    base = {'vintage': 2024}
    prefix = f'pep24:county:{geoid}:pop'
    age = row['AGEGRP']
    if age == '0':
        columns = [('TOT_MALE', {'sex': 'male'}), ('TOT_FEMALE', {'sex': 'female'})]
        for code, race in RACE.items():
            columns += [(f'{code}_MALE', {'sex': 'male', 'race': race}), (f'{code}_FEMALE', {'sex': 'female', 'race': race})]
        columns += [('H_MALE', {'sex': 'male', 'hispanic_origin': 'hispanic'}),
                    ('H_FEMALE', {'sex': 'female', 'hispanic_origin': 'hispanic'}),
                    ('NHWA_MALE', {'sex': 'male', 'hispanic_origin': 'not_hispanic', 'race': 'white_alone'}),
                    ('NHWA_FEMALE', {'sex': 'female', 'hispanic_origin': 'not_hispanic', 'race': 'white_alone'})]
        for field, dims in columns:
            yield out.obs(f'{prefix}:all:{field.lower()}:{year}', subject, 'population', _value(row[field]), 'people',
                          start, end, locator, {**base, 'age_group': 'all', **dims}, source_field=field)
        return
    group = AGEGRP[age]
    for field, sex in (('TOT_MALE', 'male'), ('TOT_FEMALE', 'female')):
        yield out.obs(f'{prefix}:{group}:{sex}:{year}', subject, 'population', _value(row[field]), 'people', start, end,
                      locator, {**base, 'age_group': group, 'sex': sex}, source_field=field)


# ---------------------------------------------------------------- legacy sample adapter
def _run_sample(context):
    dataset = 'census_population'
    if not context.raw_inputs:
        raise ValueError(f'{dataset}: no sample artifact supplied')
    seen = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            evidence = context.raw_evidence(f'line:{number}', index)
            out = []

            def base(kind, identity, **fields):
                record = {'kind': kind, 'id': 'normalized:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': evidence, 'attributes': {'source_row': row, 'source_dataset': dataset}, **fields}
                out.append(record)
                return record

            def entity(key, typ, label=None, synthetic=False, aggregate=False, **attrs):
                if key not in seen:
                    seen.add(key)
                    r = base('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                    r['attributes'].update(synthetic_reference=synthetic, aggregate=aggregate, **attrs)
                return key

            def geo(state=None, label=None):
                us = entity('geo:US', 'country', 'United States', synthetic=not (label and (not state or state in ('US', '00'))))
                if not state or state in ('US', '00'):
                    return us
                code = STATE_FIPS.get(state, state)
                key = entity('geo:US:state:' + code, 'state', label or 'US state FIPS ' + code, synthetic=not bool(label))
                rel(key, 'within', us)
                return key

            def rel(subject, predicate, obj):
                identity = ('relation', subject, predicate, obj)
                if identity not in seen:
                    seen.add(identity)
                    base('assertion', identity, subject=subject, predicate=predicate, object=obj)

            def obs(subject, metric, value, unit, start=None, end=None, **attrs):
                missing = value is None or (isinstance(value, str) and value.strip() in ('', '-', '(D)', 'D', 'S', 'N', 'NA', 'null'))
                if not missing and metric not in ('development_status', 'legal_status'):
                    value = float(value)
                    if value.is_integer():
                        value = int(value)
                r = base('observation', ['obs', number, subject, metric, start], subject=subject, metric=metric, value=None if missing else value, unit=unit, dimensions={'subject': subject})
                r['attributes'].update(source_unit=unit, **attrs)
                if missing:
                    r['missing_reason'] = 'source_missing_or_suppressed'
                if start:
                    r['valid_from'] = start
                if end:
                    r['valid_to'] = end
                return r

            def year(y):
                return (f'{int(y):04d}-01-01', f'{int(y) + 1:04d}-01-01')
            level = row['SUMLEV']
            if level == '010':
                key = geo(label=row['NAME'])
            elif level == '040':
                key = geo(row['STATE'], label=row['NAME'])
            else:
                scope = 'region' if level == '020' else 'division'
                key = entity('geo:US:' + scope + ':' + row[scope.upper()], 'location', row['NAME'])
                rel(key, 'within', geo())
            for field, value in row.items():
                if field.startswith('POPESTIMATE'):
                    start = field[-4:] + '-07-01'
                    obs(key, 'population', value, 'people', start, field[-4:] + '-07-02', estimate_vintage=2024, source_field=field)
            yield from out
