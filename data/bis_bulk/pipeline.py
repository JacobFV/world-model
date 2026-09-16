"""BIS bulk flat CSVs -> evidence observations.

Each shard is one ``WS_<flow>_csv_flat.zip`` holding a single CSV whose header cells
look like ``CODE:Label`` and whose coded cells look like ``D: Daily``. Rows with an
empty TIME_PERIOD are series-level attribute rows and are skipped. Consolidated
banking (CBS) and international debt securities (DEBT_SEC2) are restricted to the
headline cuts declared in ``parameters`` (all other flows are normalized in full).
"""
import csv
import io
import re
import zipfile
from datetime import date, timedelta

from .iso3166 import ISO2

DATASET = 'bis_bulk'


def _code(value):
    return (value or '').split(':', 1)[0].strip()


def _label(value):
    value = value or ''
    return value.split(':', 1)[1].strip() if ':' in value else value.strip()


def period(value):
    """Return (valid_from, valid_to, frequency) for SDMX time periods."""
    value = value.strip()
    if re.fullmatch(r'\d{4}', value):
        y = int(value)
        return f'{y:04d}-01-01', f'{y + 1:04d}-01-01', 'A'
    m = re.fullmatch(r'(\d{4})-?S([12])', value)
    if m:
        y, s = int(m[1]), int(m[2])
        return (f'{y:04d}-01-01', f'{y:04d}-07-01', 'S') if s == 1 else (f'{y:04d}-07-01', f'{y + 1:04d}-01-01', 'S')
    m = re.fullmatch(r'(\d{4})-?Q([1-4])', value)
    if m:
        y, q = int(m[1]), int(m[2])
        start = date(y, 3 * q - 2, 1)
        end = date(y + 1, 1, 1) if q == 4 else date(y, 3 * q + 1, 1)
        return start.isoformat(), end.isoformat(), 'Q'
    m = re.fullmatch(r'(\d{4})-?M?(\d{2})', value)
    if m and 1 <= int(m[2]) <= 12:
        y, mo = int(m[1]), int(m[2])
        end = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
        return f'{y:04d}-{mo:02d}-01', end.isoformat(), 'M'
    m = re.fullmatch(r'\d{4}-\d{2}-\d{2}', value)
    if m:
        d = date.fromisoformat(value)
        return d.isoformat(), (d + timedelta(days=1)).isoformat(), 'D'
    raise ValueError(f'Unsupported BIS time period: {value!r}')


def geography(code, text):
    if code in ISO2:
        iso3, name = ISO2[code]
        return 'iso3:' + iso3, 'country', name, False
    return 'agg:bis:' + code, 'aggregate_cohort', text or code, True


def unit_of(row, idx, flow):
    measure = row[idx['UNIT_MEASURE']] if 'UNIT_MEASURE' in idx else ''
    code, text = _code(measure), _label(measure).lower()
    if flow == 'WS_XRU':
        return _code(row[idx['CURRENCY']]) + '_per_USD'
    if flow == 'WS_EER':
        return 'index_2020_100'  # BIS EER indices are 2020 = 100; the unit sits on series rows only
    if flow == 'WS_TC':
        unit_type = _code(row[idx['UNIT_TYPE']]) if 'UNIT_TYPE' in idx else ''
        if unit_type == '770':
            return 'percent_of_gdp'
        if unit_type == 'USD':
            return 'USD'
        return code if re.fullmatch(r'[A-Z]{3}', code) else 'national_currency'
    if flow == 'WS_CBPOL' or code == '368':
        return 'percent'
    index = re.search(r'index,?\s*(\d{4})\s*=\s*100', text)
    if index:
        return f'index_{index[1]}_100'
    if code == '771':
        return 'percent_change_year_on_year'
    if code == '770':
        return 'percent_of_gdp'
    if re.fullmatch(r'[A-Z]{3}', code) and code not in ('XDC',):
        return code
    if code in ('XDC', 'XDC_R'):
        return 'national_currency'
    return re.sub(r'[^a-z0-9]+', '_', text).strip('_') or 'as_published'


def describe(flow, row, idx):
    """Return (subject_field, metric, extra dimensions) for a data row."""
    c = lambda field: _code(row[idx[field]]) if field in idx else ''
    if flow == 'WS_CBPOL':
        return 'REF_AREA', 'policy_rate', {}
    if flow == 'WS_XRU':
        return 'REF_AREA', 'exchange_rate_per_usd', {'currency': c('CURRENCY'), 'collection': c('COLLECTION')}
    if flow == 'WS_EER':
        kind = {'N': 'nominal', 'R': 'real'}.get(c('EER_TYPE'), c('EER_TYPE').lower())
        return 'REF_AREA', f'effective_exchange_rate_{kind}', {'basket': c('EER_BASKET')}
    if flow == 'WS_TC':
        return 'BORROWERS_CTY', 'total_credit', {'borrowing_sector': c('TC_BORROWERS'), 'lending_sector': c('TC_LENDERS'),
                                                'valuation': c('VALUATION'), 'unit_type': c('UNIT_TYPE'), 'adjustment': c('TC_ADJUST')}
    if flow == 'WS_SPP':
        kind = {'N': 'nominal', 'R': 'real'}.get(c('VALUE'), c('VALUE').lower())
        return 'REF_AREA', f'residential_property_price_{kind}', {'unit_measure': c('UNIT_MEASURE')}
    if flow == 'WS_LONG_CPI':
        return 'REF_AREA', 'consumer_prices', {'unit_measure': c('UNIT_MEASURE')}
    if flow == 'WS_CBS_PUB':
        return 'L_REP_CTY', 'bank_consolidated_claims', {
            'measure': c('L_MEASURE'), 'bank_type': c('CBS_BANK_TYPE'), 'basis': c('CBS_BASIS'), 'position': c('L_POSITION'),
            'instrument': c('L_INSTR'), 'remaining_maturity': c('REM_MATURITY'), 'currency_type': c('CURR_TYPE_BOOK'),
            'counterparty_sector': c('L_CP_SECTOR')}
    if flow == 'WS_DEBT_SEC2_PUB':
        return 'ISSUER_RES', 'debt_securities', {
            'measure': c('MEASURE'), 'issuer_sector': c('ISSUER_BUS_IMM'), 'market': c('MARKET'),
            'issuer_nationality': c('ISSUER_NAT'), 'issue_currency': c('ISSUE_CUR')}
    if flow == 'WS_LBS_D_PUB':
        return 'L_REP_CTY', 'bank_locational_positions', {
            'measure': c('L_MEASURE'), 'position': c('L_POSITION'), 'instrument': c('L_INSTR'), 'denomination': c('L_DENOM'),
            'currency_type': c('L_CURR_TYPE'), 'parent_country': c('L_PARENT_CTY'), 'bank_type': c('L_REP_BANK_TYPE'),
            'counterparty_sector': c('L_CP_SECTOR'), 'position_type': c('L_POS_TYPE')}
    raise ValueError(f'{DATASET}: no mapping for BIS flow {flow}')


FILTER_PARAMETERS = {'WS_CBS_PUB': 'cbs_filter', 'WS_DEBT_SEC2_PUB': 'debt_sec_filter', 'WS_LBS_D_PUB': 'lbs_filter'}
# SDMX OBS_STATUS codes for missing cells. Holiday/weekend gaps (H) carry no information and are not emitted.
MISSING = {'M': 'missing_cannot_exist', 'L': 'missing_not_collected', 'Q': 'suppressed', 'N': 'not_significant'}


def _stream(context, shard):
    with zipfile.ZipFile(shard['path']) as archive:
        members = [m for m in archive.namelist() if m.lower().endswith('.csv')]
        if len(members) != 1:
            raise ValueError(f'{DATASET}: expected one CSV in shard {shard["index"]}, found {members}')
        with archive.open(members[0]) as binary:
            text = io.TextIOWrapper(binary, encoding='utf-8-sig', newline='')
            csv.field_size_limit(16 * 1024 * 1024)
            yield members[0], csv.reader(text)


def rows_of_shard(context, shard):
    """Yield (locator, flow, header index, row) for every data row of a shard."""
    for member, reader in _stream(context, shard):
        header = next(reader)
        names = [h.split(':', 1)[0].strip() for h in header]
        idx = {name: i for i, name in enumerate(names)}
        for required in ('STRUCTURE_ID', 'TIME_PERIOD', 'OBS_VALUE', 'FREQ'):
            if required not in idx:
                raise ValueError(f'{DATASET}: shard {shard["index"]} lacks column {required}')
        series_fields = names[idx['ACTION'] + 1:idx['TIME_PERIOD']]
        last = reader.line_num
        for row in reader:
            start, last = last + 1, reader.line_num
            if not row:
                continue
            if len(row) != len(names):
                raise ValueError(f'{DATASET}: shard:{shard["index"]}/member:{member}/line:{start} has {len(row)} fields')
            yield f'shard:{shard["index"]}/member:{member}/line:{start}', idx, series_fields, row


def run(context):
    coverage = context.raw_coverage() if hasattr(context, 'raw_coverage') else None
    if not coverage or coverage['sampled'] or coverage['layout'] != 'shards':
        raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire {DATASET} --allow-network)')
    parameters = context.parameters
    for shard in context.raw_shards():
        observed_at = shard.get('retrieved_at') or context.raw_receipt()['retrieved_at']
        emitted = set()
        flow = None
        filters = {}
        for locator, idx, series_fields, row in rows_of_shard(context, shard):
            if flow is None:
                flow = re.match(r'(?:BIS:)?(WS_[A-Z0-9_]+)', row[idx['STRUCTURE_ID']])[1]
                filters = {field: set(values) for field, values in parameters.get(FILTER_PARAMETERS.get(flow, ''), {}).items()}
            time_period = row[idx['TIME_PERIOD']].strip()
            if not time_period:
                continue  # series-level attribute row
            if filters and any(_code(row[idx[field]]) not in allowed for field, allowed in filters.items() if field in idx):
                continue
            subject_field, metric, extra = describe(flow, row, idx)
            evidence = context.raw_evidence(locator)
            entities = [(row[idx[subject_field]], None)]
            raw_value = row[idx['OBS_VALUE']].strip()
            status = _code(row[idx['OBS_STATUS']]) if 'OBS_STATUS' in idx else ''
            if raw_value in ('', 'NaN', 'nan') and status == 'H':
                continue
            if flow in ('WS_CBS_PUB', 'WS_LBS_D_PUB'):
                entities.append((row[idx['L_CP_COUNTRY']], 'counterparty'))
            subject = None
            for cell, role in entities:
                code = _code(cell)
                entity_id, entity_type, label, aggregate = geography(code, _label(cell))
                if entity_id not in emitted:
                    emitted.add(entity_id)
                    yield {'kind': 'entity', 'id': f'bis:entity:{flow}:{entity_id}', 'entity_id': entity_id,
                           'entity_type': entity_type, 'label': label, 'observed_at': observed_at, 'evidence': evidence,
                           'attributes': {'source_dataset': DATASET, 'bis_code': code, 'aggregate': aggregate}}
                if role is None:
                    subject = entity_id
                else:
                    extra[role] = entity_id
            valid_from, valid_to, _ = period(time_period)
            frequency = _code(row[idx['FREQ']])
            series_key = '.'.join(_code(row[idx[f]]) for f in series_fields)
            unit = unit_of(row, idx, flow)
            multiplier = _code(row[idx['UNIT_MULT']]) if 'UNIT_MULT' in idx else ''
            record = {'kind': 'observation', 'id': f'bis:{flow}:{series_key}:{time_period}', 'observed_at': observed_at,
                      'subject': subject, 'metric': metric, 'unit': unit, 'valid_from': valid_from, 'valid_to': valid_to,
                      'dimensions': {'frequency': frequency, 'series_key': f'{flow}:{series_key}', **extra},
                      'evidence': evidence, 'attributes': {'source_flow': flow}}
            if raw_value in ('', 'NaN', 'nan'):
                record['value'] = None
                record['missing_reason'] = MISSING.get(status, 'source_status_' + status if status else 'source_missing')
            else:
                value = float(raw_value)
                if multiplier and multiplier not in ('0',):
                    value *= 10 ** int(multiplier)
                    record['attributes']['unit_multiplier'] = int(multiplier)
                record['value'] = value
            if status and status != 'A':
                record['attributes']['obs_status'] = status
            yield record
