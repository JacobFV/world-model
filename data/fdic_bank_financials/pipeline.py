"""FDIC BankFind: Call Report financials, institution directory and Summary of Deposits.

Full acquisitions are CSV pages whose endpoint is recorded in each shard's request
parameters (financials / institutions / sod). Monetary Call Report fields are published
in thousands of USD and emitted in USD. Stocks are valid for the report date
[REPDTE, REPDTE+1); year-to-date flows (net income, net charge-offs) are valid for
[Jan 1, REPDTE+1). Only a documented metric subset is normalized; all fetched fields stay
in raw. Regulatory high holders are emitted as intervals over consecutive report dates.
No bank-to-bank credit edges are inferred. Legacy JSONL samples keep the original mapping.
"""
from datetime import date, timedelta
import json
import math
from urllib.parse import parse_qs, urlsplit
from worldmodel.util import digest
from worldmodel.source_helpers import next_day, STATE_FIPS

# field -> (metric, unit, scale, basis)
FINANCIAL_METRICS = {
    'ASSET': ('total_assets', 'USD', 1000, 'stock'), 'DEP': ('bank_deposits', 'USD', 1000, 'stock'),
    'DEPUNINS': ('bank_uninsured_deposits', 'USD', 1000, 'stock'), 'BRO': ('bank_brokered_deposits', 'USD', 1000, 'stock'),
    'LNLSNET': ('bank_net_loans', 'USD', 1000, 'stock'), 'LNRENRES': ('bank_loans_nonfarm_nonresidential_re', 'USD', 1000, 'stock'),
    'LNREMULT': ('bank_loans_multifamily_re', 'USD', 1000, 'stock'), 'LNRECONS': ('bank_loans_construction_land_development', 'USD', 1000, 'stock'),
    'LNRERES': ('bank_loans_residential_1_4_family', 'USD', 1000, 'stock'), 'LNCI': ('bank_loans_commercial_industrial', 'USD', 1000, 'stock'),
    'LNCON': ('bank_loans_consumer', 'USD', 1000, 'stock'), 'SC': ('bank_securities', 'USD', 1000, 'stock'),
    'CHBAL': ('bank_cash_and_due', 'USD', 1000, 'stock'), 'EQ': ('bank_equity', 'USD', 1000, 'stock'),
    'NCLNLS': ('bank_noncurrent_loans', 'USD', 1000, 'stock'), 'NETINC': ('bank_net_income', 'USD', 1000, 'ytd'),
    'NTLNLS': ('bank_net_charge_offs', 'USD', 1000, 'ytd'), 'RBC1AAJ': ('bank_tier1_leverage_ratio', 'percent', 1, 'stock'),
    'RBCRWAJ': ('bank_total_risk_based_capital_ratio', 'percent', 1, 'stock'), 'NUMEMP': ('bank_employees', 'people', 1, 'stock'),
}
MISSING = ('', '.', 'NA', 'N/A', 'null', None)


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            yield from _full(context, index, receipt)
        else:
            yield from _sample(context, index, receipt)


def _endpoint(shard):
    request = shard.get('request') or {}
    params = request.get('params') or {}
    if params.get('endpoint'):
        return params['endpoint']
    return urlsplit(request.get('url') or '').path.rsplit('/', 1)[-1]


def _number(text):
    if text in MISSING:
        return None
    value = float(text)
    if not math.isfinite(value):
        raise ValueError('Nonfinite FDIC value')
    return value


def _mdy(text):
    if not text or text in ('00/00/0000',):
        return None
    month, day, year = text.split('/')
    if year == '9999':
        return None
    return date(int(year), int(month), int(day)).isoformat()


def _full(context, index, receipt):
    holders = {}
    for shard in context.raw_shards(index):
        endpoint = _endpoint(shard)
        if endpoint not in ('financials', 'institutions', 'sod'):
            raise ValueError(f'Unknown FDIC endpoint in shard {shard["index"]}: {endpoint!r}')
    for shard in context.raw_shards(index):
        endpoint = _endpoint(shard)
        observed = shard.get('retrieved_at') or receipt['retrieved_at']
        if endpoint == 'financials':
            yield from _financials(context, index, observed, shard, holders)
        elif endpoint == 'institutions':
            yield from _institutions(context, index, observed, shard)
        else:
            yield from _branches(context, index, observed, shard)
    for cert, (holder, start, last, locator, observed) in sorted(holders.items()):
        yield _holder_record(context, index, cert, holder, start, last, locator, observed)


def _shard_rows(context, index, shard):
    from worldmodel.raw_readers import iter_rows
    yield from iter_rows([shard], {'format': 'csv'})


def _holder_record(context, index, cert, holder, start, last, locator, observed):
    return {'kind': 'assertion', 'id': f'fdic:holder:{cert}:{holder}:{start}', 'subject': f'fdic:cert:{cert}',
            'predicate': 'regulatory_high_holder', 'object': f'rssd:{holder}', 'valid_from': start, 'valid_to': next_day(last),
            'observed_at': observed, 'evidence': context.raw_evidence(locator, index),
            'attributes': {'validity_basis': 'first to last consecutive Call Report date naming this RSSD high holder',
                           'source_field': 'RSSDHCR'}}


def _financials(context, index, observed, shard, holders):
    for locator, row in _shard_rows(context, index, shard):
        cert = row['CERT']
        rep = row['REPDTE']
        day = f'{rep[:4]}-{rep[4:6]}-{rep[6:8]}'
        subject = f'fdic:cert:{cert}'
        evidence = context.raw_evidence(locator, index)
        for field, (metric, unit, scale, basis) in FINANCIAL_METRICS.items():
            value = _number(row.get(field))
            record = {'kind': 'observation', 'id': f'fdic:{cert}:{rep}:{field}', 'subject': subject, 'metric': metric, 'unit': unit,
                      'valid_from': day[:4] + '-01-01' if basis == 'ytd' else day, 'valid_to': next_day(day),
                      'observed_at': observed, 'evidence': evidence,
                      'dimensions': {'report_date': day, 'period_basis': 'year_to_date' if basis == 'ytd' else 'report_date_stock'},
                      'attributes': {'source_field': field}}
            if value is None:
                record.update(value=None, missing_reason='source_blank')
            else:
                value = value * scale
                record['value'] = int(value) if float(value).is_integer() else value
            yield record
        holder = row.get('RSSDHCR') or ''
        if holder and holder != '0':
            current = holders.get(cert)
            if current and current[0] == holder:
                holders[cert] = (holder, current[1], day, current[3], current[4])
            else:
                if current:
                    yield _holder_record(context, index, cert, *current)
                holders[cert] = (holder, day, day, locator, observed)
        elif cert in holders:
            yield _holder_record(context, index, cert, *holders.pop(cert))


def _institutions(context, index, observed, shard):
    for locator, row in _shard_rows(context, index, shard):
        cert = row['CERT']
        subject = f'fdic:cert:{cert}'
        base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
        established, ended = _mdy(row.get('ESTYMD')), _mdy(row.get('ENDEFYMD'))
        yield {**base, 'kind': 'entity', 'id': f'fdic:institution:{cert}', 'entity_id': subject, 'entity_type': 'bank',
               'label': row.get('NAME') or subject,
               'attributes': {k: v for k, v in {'active': row.get('ACTIVE') == '1', 'charter_class': row.get('BKCLASS') or None,
                              'regulator': row.get('REGAGNT') or None, 'fdic_region': row.get('FDICREGN') or None,
                              'state': row.get('STALP') or None, 'city': row.get('CITY') or None, 'zip': row.get('ZIP') or None,
                              'latitude': _number(row.get('LATITUDE')), 'longitude': _number(row.get('LONGITUDE')),
                              'established': established, 'insured_since': _mdy(row.get('INSDATE')), 'end_date': ended,
                              'last_structure_change_code': row.get('CHANGEC1') or None, 'website': row.get('WEBADDR') or None}.items()
                              if v is not None}}
        if row.get('FED_RSSD') and row['FED_RSSD'] != '0':
            yield {**base, 'kind': 'assertion', 'id': f'fdic:institution:{cert}:rssd', 'subject': subject, 'predicate': 'identifier_assignment',
                   'value': {'namespace': 'rssd', 'value': row['FED_RSSD']}, 'attributes': {'identity_basis': 'FDIC institution directory FED_RSSD'}}
        status = {**base, 'kind': 'assertion', 'id': f'fdic:institution:{cert}:charter', 'subject': subject, 'predicate': 'insured_charter_period',
                  'value': {'active': row.get('ACTIVE') == '1', 'established': established, 'end_date': ended},
                  'attributes': {'validity_basis': 'ESTYMD to ENDEFYMD (open-ended when active)'}}
        if established:
            status['valid_from'] = established
        if ended and (not established or ended > established):
            status['valid_to'] = ended
        yield status
        county = row.get('STCNTY') or ''
        if county.isdigit() and 4 <= len(county) <= 5:
            yield {**base, 'kind': 'assertion', 'id': f'fdic:institution:{cert}:county', 'subject': subject, 'predicate': 'located_in',
                   'object': 'geo:US:county:' + county.zfill(5), 'attributes': {'basis': 'main office county (FIPS state+county)'}}


def _branches(context, index, observed, shard):
    for locator, row in _shard_rows(context, index, shard):
        uninum, cert, year = row['UNINUMBR'], row['CERT'], row['YEAR']
        branch = f'fdic:branch:{uninum}'
        base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
        as_of = f'{year}-06-30'
        yield {**base, 'kind': 'entity', 'id': f'fdic:sod:{year}:{uninum}', 'entity_id': branch, 'entity_type': 'facility',
               'label': row.get('NAMEBR') or branch,
               'attributes': {k: v for k, v in {'city': row.get('CITYBR') or None, 'state': row.get('STALPBR') or None, 'zip': row.get('ZIPBR') or None,
                              'latitude': _number(row.get('SIMS_LATITUDE')), 'longitude': _number(row.get('SIMS_LONGITUDE')),
                              'main_office': row.get('BKMO') == '1', 'service_type': row.get('BRSERTYP') or None,
                              'branch_number': row.get('BRNUM')}.items() if v is not None}}
        yield {**base, 'kind': 'assertion', 'id': f'fdic:sod:{year}:{uninum}:bank', 'subject': branch, 'predicate': 'branch_of',
               'object': f'fdic:cert:{cert}', 'valid_from': as_of, 'valid_to': next_day(as_of),
               'attributes': {'validity_basis': 'Summary of Deposits survey date (June 30)'}}
        county = row.get('STCNTYBR') or ''
        if county.isdigit() and 4 <= len(county) <= 5:
            yield {**base, 'kind': 'assertion', 'id': f'fdic:sod:{year}:{uninum}:county', 'subject': branch, 'predicate': 'located_in',
                   'object': 'geo:US:county:' + county.zfill(5), 'attributes': {}}
        value = _number(row.get('DEPSUMBR'))
        record = {**base, 'kind': 'observation', 'id': f'fdic:sod:{year}:{uninum}:deposits', 'subject': branch, 'metric': 'branch_deposits',
                  'unit': 'USD', 'valid_from': as_of, 'valid_to': next_day(as_of),
                  'dimensions': {'survey_year': int(year), 'bank': f'fdic:cert:{cert}'}, 'attributes': {'source_field': 'DEPSUMBR', 'source_unit': 'thousand_USD'}}
        if value is None:
            record.update(value=None, missing_reason='source_blank')
        else:
            record['value'] = int(value * 1000)
        yield record


def _sample(context, index, receipt):
    dataset = 'fdic_bank_financials'
    ref = context.raw_inputs[index]
    acquired = receipt['retrieved_at']
    seen = set()
    record_ids = set()
    for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        out = []

        def base(kind, identity, **fields):
            r = {'kind': kind, 'id': 'strategic:' + digest([dataset, ref, identity]), 'observed_at': acquired,
                 'evidence': context.raw_evidence('line:' + str(line_number), index),
                 'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only'}, **fields}
            if r['id'] not in record_ids:
                record_ids.add(r['id'])
                out.append(r)
            return r

        def entity(key, typ, label=None, **attrs):
            if key not in seen:
                seen.add(key)
                r = base('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                r['attributes'].update(attrs)
            return key

        def observation(subject, metric, value, unit, start=None, end=None, scale=1, **attrs):
            missing = value is None or str(value).strip() in ('', '.', 'NA', 'N/A', 'null')
            if not missing:
                value = float(value) * scale
                if not math.isfinite(value):
                    raise ValueError('Nonfinite source measurement')
                if value.is_integer():
                    value = int(value)
            r = base('observation', [line_number, subject, metric, start], subject=subject, metric=metric, value=None if missing else value,
                     unit=unit, dimensions={'subject': subject})
            if start:
                r['valid_from'] = start
            if end:
                r['valid_to'] = end
            if missing:
                r['missing_reason'] = 'source_missing'
            r['attributes'].update(attrs)

        data = row.get('data', row)
        rep = str(data['REPDTE']).replace('-', '')
        day = f'{rep[:4]}-{rep[4:6]}-{rep[6:8]}'
        key = entity('fdic:cert:' + str(data['CERT']), 'bank', data['NAME'], fdic_certificate=data['CERT'])
        for field, metric in [('ASSET', 'total_assets'), ('DEP', 'bank_deposits'), ('LNLSNET', 'bank_net_loans'), ('EQ', 'bank_equity'), ('NETINC', 'bank_net_income')]:
            observation(key, metric, data.get(field), 'USD', rep[:4] + '-01-01' if field == 'NETINC' else day, next_day(day), scale=1000,
                        source_field=field, source_unit='thousand_USD', period_basis='year_to_date' if field == 'NETINC' else 'report_date_stock')
        yield from out
