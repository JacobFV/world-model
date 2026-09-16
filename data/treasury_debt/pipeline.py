"""treasury_debt: Fiscal Service Debt to the Penny, debt outstanding, average interest rates,
Monthly Treasury Statement (MTS) and Daily Treasury Statement (DTS) tables.

Full acquisitions are ``paged_api`` page shards (one fiscaldata JSON page per shard, request
parameters ``version/group/table`` recorded in shard metadata). The original 100-row sample
adapter is kept for single-payload artifacts.
"""
import json
import math
import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from worldmodel.util import digest
from worldmodel.source_helpers import SERIES, next_day, month_end

DATASET = 'treasury_debt'
AGENCY = 'us:agency:treasury'
DTS_TABLES = {'operating_cash_balance', 'deposits_withdrawals_operating_cash', 'public_debt_transactions',
              'adjustment_public_debt_transactions_cash_basis', 'debt_subject_to_limit', 'federal_tax_deposits',
              'short_term_cash_investments', 'income_tax_refunds_issued', 'inter_agency_tax_transfers'}

# table -> (fields {source field: metric or None for a metric derived from the row}, period, dimension fields)
DAILY = {
    'debt_to_penny': ({'tot_pub_debt_out_amt': 'public_debt', 'debt_held_public_amt': 'debt_held_by_public',
                       'intragov_hold_amt': 'intragovernmental_debt'}, []),
    'debt_outstanding': ({'debt_outstanding_amt': 'public_debt'}, []),
    'operating_cash_balance': ({'close_today_bal': 'treasury_operating_cash_balance_close',
                                'open_today_bal': 'treasury_operating_cash_balance_open'},
                               ['account_type', 'sub_table_name']),
    'deposits_withdrawals_operating_cash': ({'transaction_today_amt': None},
                                            ['account_type', 'transaction_type', 'transaction_catg',
                                             'transaction_catg_desc']),
    'public_debt_transactions': ({'transaction_today_amt': None},
                                 ['transaction_type', 'security_market', 'security_type', 'security_type_desc']),
    'adjustment_public_debt_transactions_cash_basis': ({'adj_today_amt': 'public_debt_cash_basis_adjustment'},
                                                       ['transaction_type', 'adj_type', 'adj_type_desc',
                                                        'sub_table_name']),
    'debt_subject_to_limit': ({'close_today_bal': 'debt_subject_to_limit_close_balance'},
                              ['debt_catg', 'debt_catg_desc', 'sub_table_name']),
    'federal_tax_deposits': ({'tax_deposit_today_amt': 'federal_tax_deposits'},
                             ['tax_deposit_type', 'tax_deposit_type_desc', 'sub_table_name']),
    'short_term_cash_investments': ({'total_amt': 'short_term_cash_investments'},
                                    ['transaction_type', 'transaction_type_desc', 'sub_table_name']),
    'income_tax_refunds_issued': ({'tax_refund_today_amt': 'income_tax_refunds_issued'},
                                  ['tax_refund_type', 'tax_refund_type_desc', 'sub_table_name']),
    'inter_agency_tax_transfers': ({'today_amt': 'inter_agency_tax_transfers'}, ['classification', 'sub_table_name']),
}
MTS = {  # mts table -> {field: (metric, period)}
    'mts_table_1': {'current_month_gross_rcpt_amt': ('federal_receipts', 'month'),
                    'current_month_gross_outly_amt': ('federal_outlays', 'month'),
                    'current_month_dfct_sur_amt': ('federal_deficit_surplus', 'month')},
    'mts_table_2': {'current_month_budget_amt': ('federal_budget_result', 'month'),
                    'current_fytd_budget_amt': ('federal_budget_result', 'fytd'),
                    'current_year_budget_est_amt': ('federal_budget_result_estimate', 'fiscal_year'),
                    'next_year_budget_est_amt': ('federal_budget_result_estimate', 'next_fiscal_year')},
    'mts_table_3': {'current_month_rcpt_outly_amt': ('federal_receipts_outlays', 'month'),
                    'current_fytd_rcpt_outly_amt': ('federal_receipts_outlays', 'fytd'),
                    'current_year_budget_est_amt': ('federal_receipts_outlays_estimate', 'fiscal_year')},
    'mts_table_4': {'current_month_gross_rcpt_amt': ('federal_receipts_gross', 'month'),
                    'current_month_refund_amt': ('federal_receipts_refunds', 'month'),
                    'current_month_net_rcpt_amt': ('federal_receipts_net', 'month'),
                    'current_fytd_net_rcpt_amt': ('federal_receipts_net', 'fytd')},
    'mts_table_5': {'current_month_gross_outly_amt': ('federal_outlays_gross', 'month'),
                    'current_month_app_rcpt_amt': ('federal_outlays_applicable_receipts', 'month'),
                    'current_month_net_outly_amt': ('federal_outlays_net', 'month'),
                    'current_fytd_net_outly_amt': ('federal_outlays_net', 'fytd')},
    'mts_table_6': {'current_month_net_txn_amt': ('federal_financing_net_transactions', 'month'),
                    'fytd_net_txn_amt': ('federal_financing_net_transactions', 'fytd'),
                    'close_month_acct_bal_amt': ('federal_financing_account_balance', 'day')},
    'mts_table_8': {'current_month_rcpt_amt': ('trust_fund_receipts', 'month'),
                    'current_month_outly_amt': ('trust_fund_outlays', 'month'),
                    'current_month_excess_amt': ('trust_fund_excess', 'month'),
                    'close_month_acct_bal_amt': ('trust_fund_balance', 'day')},
    'mts_table_9': {'current_month_rcpt_outly_amt': ('federal_receipts_outlays_by_source_function', 'month'),
                    'current_fytd_rcpt_outly_amt': ('federal_receipts_outlays_by_source_function', 'fytd')},
}
MTS_DIMENSIONS = ['classification_desc', 'line_code_nbr', 'sequence_number_cd', 'data_type_cd']
AGGREGATE = re.compile(r'^\s*(total|sub-?total|net change|equals)', re.I)


def run(context):
    coverage = context.raw_coverage() if hasattr(context, 'raw_coverage') else {'layout': 'payload'}
    if coverage['layout'] != 'shards':
        yield from sample(context)
        return
    yield from full(context, coverage)


# ----- full sharded acquisition ---------------------------------------------------------------
def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', str(text).lower()).strip('_') or 'blank'


def number(text, scale):
    if text is None:
        return None
    text = str(text).strip()
    if text in ('', 'null', 'NULL', '*', '(*)', 'N/A'):
        return None
    try:
        value = float(Decimal(text.replace(',', '').replace('$', '')) * int(scale))
    except InvalidOperation:
        raise ValueError(f'{DATASET}: unrecognized amount {text[:20]!r}') from None
    if not math.isfinite(value):
        raise ValueError('Nonfinite Treasury value')
    return int(value) if value.is_integer() and abs(value) < 2 ** 53 else round(value, 6)


def scale_for(fmt, table):
    fmt = str(fmt or '')
    if '1,000,000' in fmt:
        return 1_000_000
    if fmt.startswith('$') or '%' in fmt:
        return 1
    return 1_000_000 if table in DTS_TABLES else 1


def period(day, kind):
    if kind == 'day':
        return day.isoformat(), (day + timedelta(days=1)).isoformat()
    if kind == 'month':
        start = day.replace(day=1)
        return start.isoformat(), date(start.year + (start.month == 12), start.month % 12 + 1, 1).isoformat()
    fiscal_start = date(day.year if day.month >= 10 else day.year - 1, 10, 1)
    if kind == 'fytd':
        return fiscal_start.isoformat(), (day + timedelta(days=1)).isoformat()
    offset = 1 if kind == 'next_fiscal_year' else 0
    return (date(fiscal_start.year + offset, 10, 1).isoformat(), date(fiscal_start.year + offset + 1, 10, 1).isoformat())


def security_key(text):
    return 'treasury:security_type:' + re.sub(r'^treasury_', '', slug(text))


def full(context, coverage):
    receipt = context.raw_receipt(0)
    emitted = set()

    def entity(key, entity_type, label, locator, retrieved, **attributes):
        if key in emitted:
            return []
        emitted.add(key)
        return [{'kind': 'entity', 'id': 'treasury:' + digest(['entity', key]), 'entity_id': key,
                 'entity_type': entity_type, 'label': label, 'observed_at': retrieved,
                 'evidence': context.raw_evidence(locator),
                 'attributes': {'source_dataset': DATASET, 'complete_source': coverage['complete'], **attributes}}]

    for shard in context.raw_shards(0):
        request = shard.get('request') or {}
        params = request.get('params') or {}
        table = params.get('table') or str(request.get('url', '')).split('?', 1)[0].rstrip('/').rsplit('/', 1)[-1]
        if table not in DAILY and table not in MTS and table != 'avg_interest_rates':
            raise ValueError(f'{DATASET}: unrecognized fiscaldata table in shard {shard["index"]}')
        retrieved = shard.get('retrieved_at') or receipt['retrieved_at']
        with open(shard['path'], 'rb') as stream:
            payload = json.load(stream)
        if not isinstance(payload, dict) or not isinstance(payload.get('data'), list):
            raise ValueError(f'{DATASET}: fiscaldata error payload in shard {shard["index"]}')
        formats = (payload.get('meta') or {}).get('dataFormats') or {}
        current_date, ids = None, set()
        for position, row in enumerate(payload['data']):
            locator = f'shard:{shard["index"]}/record:{position}'
            yield from entity(AGENCY, 'government_agency', 'US Treasury', locator, retrieved)
            day = date.fromisoformat(row['record_date'])
            if day != current_date:
                current_date, ids = day, set()
            base = {'table': table, 'src_line_nbr': row.get('src_line_nbr')}
            if table == 'avg_interest_rates':
                subject = security_key(row['security_desc'])
                yield from entity(subject, 'security', row['security_desc'], locator, retrieved,
                                  security_type_desc=row.get('security_type_desc'),
                                  aggregate=bool(AGGREGATE.match(row['security_desc'])))
                items = [('avg_interest_rate_amt', 'average_interest_rate', 'month', 'percent', subject,
                          {'security_desc': row['security_desc'], 'security_type_desc': row.get('security_type_desc')})]
            elif table in MTS:
                dims = {name: row.get(name) for name in MTS_DIMENSIONS if row.get(name) not in (None, 'null')}
                items = [(field, metric, kind, 'USD', AGENCY, dims) for field, (metric, kind) in MTS[table].items()]
            else:
                fields, dimension_names = DAILY[table]
                dims = {name: row.get(name) for name in dimension_names if row.get(name) not in (None, 'null')}
                subject = AGENCY
                if table == 'public_debt_transactions':
                    subject = security_key(row.get('security_type') or 'unspecified')
                    yield from entity(subject, 'security', row.get('security_type') or 'unspecified', locator,
                                      retrieved, security_market=row.get('security_market'))
                items = []
                for field, metric in fields.items():
                    if metric is None:
                        prefix = 'public_debt_' if table == 'public_debt_transactions' else 'treasury_cash_'
                        metric = prefix + slug(row.get('transaction_type'))
                    items.append((field, metric, 'day', 'USD', subject, dims))
            for field, metric, kind, unit, subject, dims in items:
                value = number(row.get(field), 1 if unit == 'percent' else scale_for(formats.get(field), table))
                if value is None:
                    continue
                start, end = period(day, kind)
                identity = [table, row['record_date'], row.get('src_line_nbr'), dims, field]
                record_id = 'treasury:' + digest(identity)
                occurrence = 1
                while record_id in ids:
                    occurrence += 1
                    record_id = 'treasury:' + digest(identity + [occurrence])
                ids.add(record_id)
                labels = ' '.join(str(dims.get(k, '')) for k in ('classification_desc', 'transaction_catg',
                                                                   'account_type', 'debt_catg', 'security_desc'))
                attributes = {**base, 'source_field': field}
                if unit == 'USD' and scale_for(formats.get(field), table) != 1:
                    attributes['source_multiplier'] = scale_for(formats.get(field), table)
                if AGGREGATE.match(labels.strip()) or any(AGGREGATE.match(str(v)) for v in dims.values()):
                    attributes['aggregate'] = True
                yield {'kind': 'observation', 'id': record_id, 'observed_at': retrieved,
                       'evidence': context.raw_evidence(locator), 'subject': subject, 'metric': metric,
                       'value': value, 'unit': unit, 'valid_from': start, 'valid_to': end,
                       'dimensions': {'table': table, 'period': kind, **{k: v for k, v in dims.items()}},
                       'attributes': attributes}


# ----- legacy 100-row sample adapter ----------------------------------------------------------
def sample(context):
    dataset = 'treasury_debt'
    if not context.raw_inputs:
        raise ValueError('Source sample artifact required')
    seen = set()
    record_ids = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            out = []

            def base(kind, identity, **fields):
                r = {'kind': kind, 'id': 'strategic:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': context.raw_evidence('line:' + str(line_number), index), 'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only'}, **fields}
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
                r = base('observation', [line_number, subject, metric, start], subject=subject, metric=metric, value=None if missing else value, unit=unit, dimensions={'subject': subject})
                if start:
                    r['valid_from'] = start
                if end:
                    r['valid_to'] = end
                if missing:
                    r['missing_reason'] = 'source_missing'
                r['attributes'].update(attrs)
            key = entity('us:agency:treasury', 'government_agency', 'US Treasury')
            for field, metric in [('tot_pub_debt_out_amt', 'public_debt'), ('debt_held_public_amt', 'debt_held_by_public'), ('intragov_hold_amt', 'intragovernmental_debt')]:
                observation(key, metric, row[field], 'USD', row['record_date'], next_day(row['record_date']), source_field=field)
            yield from out
