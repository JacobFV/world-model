'Economic census, CBP, nonemployers, BDS and AIES'
import json
import re
import zipfile
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS


def run(context):
    """Sample artifacts keep the legacy adapter; full CBP/ZBP/Nonemployer/BDS files use `_run_full`."""
    if not context.raw_inputs:
        raise ValueError(f'census_business: no raw artifact supplied')
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


# ------------------------------------------------------------------ full files
FILE = re.compile(r'(cbp(\d\d)(us|st|co|msa)|zbp(\d\d)detail|nonemp(\d\d)(st|co)|bds(\d{4})_st_sec)\.(txt|csv|zip)$', re.I)
SECTOR_RANGES = {'31': '31-33', '32': '31-33', '33': '31-33', '44': '44-45', '45': '44-45', '48': '48-49', '49': '48-49'}
LFO = {'-': 'all', 'C': 'c_corporation', 'Z': 's_corporation', 'S': 'sole_proprietorship', 'P': 'partnership',
       'N': 'nonprofit', 'G': 'government', 'O': 'other', 'L': 'llc'}
SUPPRESSED = {'D': 'suppressed_disclosure', 'S': 'suppressed_publication_standards', 'N': 'not_available', 'X': 'not_applicable'}
SIZE_CLASSES = ('n<5', 'n5_9', 'n10_19', 'n20_49', 'n50_99', 'n100_249', 'n250_499', 'n500_999', 'n1000')
BDS_METRICS = {'firms': ('firm_count', 'firms'), 'estabs': ('establishment_count', 'establishments'), 'emp': ('employment', 'people'),
               'estabs_entry': ('establishment_entries', 'establishments'), 'estabs_exit': ('establishment_exits', 'establishments'),
               'job_creation': ('job_creation', 'jobs'), 'job_destruction': ('job_destruction', 'jobs'),
               'net_job_creation': ('net_job_creation', 'jobs'), 'firmdeath_firms': ('firm_deaths', 'firms'),
               'reallocation_rate': ('job_reallocation_rate', 'percent')}


def _naics(code, revision):
    """CBP-style code ('------', '31----', '113///', '11331/', '113310') -> (dimension id, digits level)."""
    code = (code or '').strip()
    if not code or set(code) <= {'-'} or code in ('00', '0'):
        return 'all', 0
    digits = code.rstrip('-/')
    if not digits.isdigit():
        return None, None
    if len(digits) == 2:
        digits = SECTOR_RANGES.get(digits, digits)
    return f'naics{revision}:{digits}', 2 if '-' in digits else len(digits)


def _num(text):
    text = (text or '').strip()
    if not text or text in ('N', 'D', 'S', 'X'):
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def _file(context, index, shard, locator=None):
    receipt = context.raw_receipt(index)
    name = str((shard.get('request') or {}).get('url') or receipt.get('original_name') or '')
    match = FILE.search(name.rsplit('/', 1)[-1])
    if not match:
        raise ValueError('census_business: unrecognized raw file ' + name)
    return match


def _run_full(context):
    from worldmodel.raw_readers import iter_rows
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        emitted = set()  # geographies (~45k ZIPs, counties, states, metros) and industry codes

        def entity(key, typ, label, locator, **attrs):
            if key in emitted:
                return []
            emitted.add(key)
            return [{'kind': 'entity', 'id': 'cbiz:entity:' + key, 'entity_id': key, 'entity_type': typ, 'label': label or key,
                     'observed_at': observed, 'evidence': context.raw_evidence(locator, index), 'attributes': attrs}]

        for shard in context.raw_shards(index):
            match = _file(context, index, shard)
            config = {'format': 'csv', 'encoding': 'latin-1', 'strict': False}
            if zipfile.is_zipfile(shard['path']):
                config['members'] = ['*.txt', '*.csv']
            rows = iter_rows([shard], config)
            if match.group(2):
                yield from _cbp(context, index, observed, entity, rows, int('20' + match.group(2)), match.group(3).lower())
            elif match.group(4):
                yield from _zbp(context, index, observed, entity, rows, int('20' + match.group(4)))
            elif match.group(5):
                yield from _nonemployer(context, index, observed, entity, rows, int('20' + match.group(5)), match.group(6).lower())
            else:
                yield from _bds(context, index, observed, entity, rows)


def _geo(entity, locator, level, row, year):
    if level == 'us':
        return 'geo:US', 'US', list(entity('geo:US', 'country', 'United States', locator))
    if level == 'st':
        code = (row.get('fipstate') or row.get('ST') or row.get('st') or '').strip().zfill(2)
        return f'geo:US:state:{code}', f'st{code}', list(entity(f'geo:US:state:{code}', 'state', None, locator))
    if level == 'co':
        code = (row.get('fipstate') or row.get('ST')).strip().zfill(2) + (row.get('fipscty') or row.get('CTY')).strip().zfill(3)
        return f'geo:US:county:{code}', f'co{code}', list(entity(f'geo:US:county:{code}', 'county', None, locator))
    if level == 'msa':
        code = row['msa'].strip()
        return f'geo:US:cbsa:{code}', f'msa{code}', list(entity(f'geo:US:cbsa:{code}', 'location', None, locator, delineation='cbp'))
    code = row['zip'].strip().zfill(5)
    label = f'ZIP Code {code} ({(row.get("name") or "").strip()})'
    return f'geo:US:zip:{code}', f'zip{code}', list(entity(f'geo:US:zip:{code}', 'location', label, locator,
                                                          note='USPS ZIP Code (not ZCTA)', state=row.get('stabbr')))


def _obs(context, index, observed, rid, subject, metric, value, unit, period, locator, dims, flag=None, **attrs):
    record = {'kind': 'observation', 'id': rid, 'subject': subject, 'metric': metric, 'value': value, 'unit': unit,
              'valid_from': period[0], 'valid_to': period[1], 'observed_at': observed,
              'evidence': context.raw_evidence(locator, index), 'dimensions': dims, 'attributes': attrs}
    if flag:
        record['attributes']['noise_flag'] = flag
    if flag in SUPPRESSED:
        record['value'] = None
        record['missing_reason'] = SUPPRESSED[flag]
    elif value is None:
        record['missing_reason'] = 'source_missing'
    return record


def _cbp(context, index, observed, entity, rows, year, level):
    revision = '2022' if year >= 2022 else '2017'
    max_level = {'us': 6, 'st': 6, 'co': 3, 'msa': 3}[level]
    periods = {'emp': (f'{year}-03-12', f'{year}-03-19'), 'qp1': (f'{year}-01-01', f'{year}-04-01'),
               'ap': (f'{year}-01-01', f'{year + 1}-01-01'), 'est': (f'{year}-01-01', f'{year + 1}-01-01')}
    for locator, row in rows:
        row = {k.strip(): v for k, v in row.items() if k}
        lfo = (row.get('lfo') or '-').strip()
        if level == 'st' and lfo != '-':
            continue
        industry, digits = _naics(row.get('naics'), revision)
        if industry is None or digits > max_level:
            continue
        subject, key, records = _geo(entity, locator, level, row, year)
        yield from records
        if industry != 'all':
            yield from entity(industry, 'industry', None, locator, revision=revision)
        code = industry.split(':')[-1]
        dims = {'industry': industry, 'legal_form': LFO.get(lfo, lfo), 'program': 'cbp'}
        base = f'cbp{year % 100:02d}:{key}:{code}:{lfo}'
        for field, metric, unit in (('emp', 'employment', 'people'), ('qp1', 'first_quarter_payroll', 'thousand_USD'),
                                    ('ap', 'annual_payroll', 'thousand_USD'), ('est', 'establishment_count', 'establishments')):
            if field not in row:
                continue
            flag = (row.get(field + '_nf') or '').strip() or None
            yield _obs(context, index, observed, f'{base}:{field}', subject, metric, _num(row.get(field)), unit, periods[field],
                       locator, dims, flag if field != 'est' else None)


def _zbp(context, index, observed, entity, rows, year):
    period = (f'{year}-01-01', f'{year + 1}-01-01')
    for locator, row in rows:
        industry, digits = _naics(row.get('naics'), '2022' if year >= 2022 else '2017')
        if industry is None or digits > 2:
            continue
        subject, key, records = _geo(entity, locator, 'zip', row, year)
        yield from records
        code = industry.split(':')[-1]
        dims = {'industry': industry, 'program': 'zbp'}
        yield _obs(context, index, observed, f'zbp{year % 100:02d}:{key}:{code}:est', subject, 'establishment_count',
                   _num(row.get('est')), 'establishments', period, locator, dims)
        if industry == 'all':
            for size in SIZE_CLASSES:
                value = row.get(size)
                if value is None:
                    continue
                flag = 'N' if value.strip() == 'N' else None
                yield _obs(context, index, observed, f'zbp{year % 100:02d}:{key}:all:{size}', subject, 'establishment_count',
                           _num(value), 'establishments', period, locator,
                           {**dims, 'employment_size_class': size[1:].replace('_', '-')}, flag)


def _nonemployer(context, index, observed, entity, rows, year, level):
    period = (f'{year}-01-01', f'{year + 1}-01-01')
    for locator, row in rows:
        lfo = (row.get('LFO') or '-').strip()
        if lfo != '-':
            continue
        industry, digits = _naics(row.get('NAICS'), '2022' if year >= 2022 else '2017')
        if industry is None or digits > (6 if level == 'st' else 4):
            continue
        subject, key, records = _geo(entity, locator, level, row, year)
        yield from records
        code = industry.split(':')[-1]
        dims = {'industry': industry, 'program': 'nonemployer_statistics'}
        flag = (row.get('ESTAB_F') or '').strip() or None
        yield _obs(context, index, observed, f'nes{year % 100:02d}:{key}:{code}:estab', subject, 'nonemployer_establishments',
                   _num(row.get('ESTAB')), 'establishments', period, locator, dims, flag)
        flag = (row.get('RCPTOT_F') or '').strip() or None
        yield _obs(context, index, observed, f'nes{year % 100:02d}:{key}:{code}:rcptot', subject, 'nonemployer_receipts',
                   _num(row.get('RCPTOT')), 'thousand_USD', period, locator, dims, flag,
                   noise_range=(row.get('RCPTOT_N_F') or '').strip() or None)


def _bds(context, index, observed, entity, rows):
    for locator, row in rows:
        year = int(row['year'])
        state = row['st'].strip().zfill(2)
        sector = row['sector'].strip()
        industry = f'naics2017:{SECTOR_RANGES.get(sector, sector)}'
        subject, key, records = _geo(entity, locator, 'st', {'fipstate': state}, year)
        yield from records
        dims = {'industry': industry, 'program': 'bds', 'industry_basis': 'bds_sector_2017_naics'}
        for field, (metric, unit) in BDS_METRICS.items():
            text = (row.get(field) or '').strip()
            value = None if text in ('', '(D)', 'D', '(S)', 'S', '(X)', 'X', 'N') else float(text)
            if value is not None and value.is_integer():
                value = int(value)
            record = _obs(context, index, observed, f'bds:{key}:{sector}:{year}:{field}', subject, metric, value, unit,
                          (f'{year}-03-12', f'{year}-03-19') if field in ('firms', 'estabs', 'emp') else (f'{year - 1}-03-12', f'{year}-03-12'),
                          locator, dims)
            if value is None and text:
                record['missing_reason'] = 'suppressed_disclosure' if 'D' in text else 'not_available'
            yield record


# ------------------------------------------------------------------ legacy sample adapter
def _run_sample(context):
    dataset = 'census_business'
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
            state = geo(row['fipstate'])
            code = row['naics'].strip().rstrip('/')
            industry = entity('naics:2022:' + code, 'industry', 'NAICS 2022 ' + code, synthetic=True, classification_revision='2022')
            key = entity('cohort:cbp:2023:' + digest([row['fipstate'], code, row['lfo']]), 'business_cohort', '2023 CBP aggregate ' + row['fipstate'] + '/' + code + '/' + row['lfo'], aggregate=True, classification_revision='2022', legal_form=row['lfo'])
            rel(key, 'within', state)
            rel(key, 'classified_as', industry)
            for field, metric, unit in [('emp', 'employment', 'people'), ('est', 'establishment_count', 'establishments'), ('ap', 'annual_payroll', 'thousand_USD'), ('qp1', 'first_quarter_payroll', 'thousand_USD')]:
                value = None if row.get(field + '_nf') == 'D' else row.get(field)
                period = ('2023-03-12', '2023-03-13') if field in ('emp', 'est') else ('2023-01-01', '2023-04-01') if field == 'qp1' else year(2023)
                obs(key, metric, value, unit, *period, noise_flag=row.get(field + '_nf'), source_field=field, reference_basis='CBP March reference period for employment/establishments; reported payroll period otherwise')
            yield from out
