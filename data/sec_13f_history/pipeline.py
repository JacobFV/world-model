"""SEC Form 13F structured data sets (2013Q2 onward) -> dated manager->security holdings.

Same normalization as sec_ownership_datasets' 13F branch: 13F-HR and 13F-HR/A information
table rows are aggregated per (accession, CUSIP, put/call, SH/PRN) into ``reported_holding``
assertions ``sec:cik:<manager>`` -> ``cusip:<CUSIP>``, valid for the report period end date
and observed at the filing date, plus one ``reported_13f_portfolio_value`` observation per
filing. 13F publishes no issuer CIK, so no issuer is inferred.

Units: VALUE and TABLEVALUETOTAL were reported in thousands of USD for filings made before
2023-01-03 and in whole USD afterwards (SEC 13F FAQ); both are normalized to USD here and the
source convention is kept in ``attributes.value_source_unit``. Entities are emitted once per
data set ZIP (ids include the shard). SEC notes data are as filed and unverified.
"""
import csv
import io
import re
import zipfile
from datetime import date, timedelta

MONTHS = {m: i for i, m in enumerate(('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'), 1)}
CIK = re.compile(r'[0-9]{1,10}')
CUSIP = re.compile(r'[0-9A-Z*@#]{9}')
DOLLAR_REPORTING_START = '2023-01-03'


def _day(text):
    if not text:
        return None
    text = text.strip()
    try:
        if re.fullmatch(r'\d{4}-\d{2}-\d{2}', text[:10]):
            return date.fromisoformat(text[:10]).isoformat()
        d, m, y = text.split('-')
        return date(int(y), MONTHS[m.upper()], int(d)).isoformat()
    except (ValueError, KeyError):
        return None


def _next(day):
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def _num(text):
    if text in (None, ''):
        return None
    try:
        value = float(text)
    except ValueError:
        return None
    return int(value) if value.is_integer() else value


def _cik(text):
    text = (text or '').strip()
    return 'sec:cik:' + text.zfill(10) if CIK.fullmatch(text) and int(text) > 0 else None


def _tsv(archive, member, prefix):
    csv.field_size_limit(16 * 1024 * 1024)
    with archive.open(member) as stream:
        text = io.TextIOWrapper(stream, encoding='utf-8', errors='replace', newline='')
        reader = csv.reader(text, delimiter='\t', quoting=csv.QUOTE_NONE)
        header = [h.strip().upper() for h in next(reader)]
        last = 1
        for values in reader:
            start, last = last + 1, reader.line_num
            if not values or values == ['']:
                continue
            values = (values + [''] * len(header))[:len(header)]
            yield f'{prefix}/member:{member}/line:{start}', dict(zip(header, values))


def _member(names, wanted):
    for name in names:
        if name.rsplit('/', 1)[-1].upper() == wanted:
            return name
    return None


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            raise ValueError('sec_13f_history requires a full acquisition artifact (SEC 13F data set ZIPs)')
        for shard in context.raw_shards(index):
            with zipfile.ZipFile(shard['path']) as archive:
                names = archive.namelist()
                if not _member(names, 'INFOTABLE.TSV'):
                    raise ValueError(f'shard:{shard["index"]}: not a 13F data set ZIP')
                yield from _form13f(context, index, archive, names, f'shard:{shard["index"]}')


def _form13f(context, index, archive, names, prefix):
    submissions, cover, summary = {}, {}, {}
    for _, row in _tsv(archive, _member(names, 'SUBMISSION.TSV'), prefix):
        submissions[row['ACCESSION_NUMBER']] = (row.get('SUBMISSIONTYPE'), _cik(row.get('CIK')), _day(row.get('FILING_DATE')), _day(row.get('PERIODOFREPORT')))
    if _member(names, 'COVERPAGE.TSV'):
        for _, row in _tsv(archive, _member(names, 'COVERPAGE.TSV'), prefix):
            cover[row['ACCESSION_NUMBER']] = (row.get('FILINGMANAGER_NAME') or '', row.get('AMENDMENTTYPE') or None, row.get('REPORTTYPE') or None)
    if _member(names, 'SUMMARYPAGE.TSV'):
        for locator, row in _tsv(archive, _member(names, 'SUMMARYPAGE.TSV'), prefix):
            summary[row['ACCESSION_NUMBER']] = (locator, _num(row.get('TABLEVALUETOTAL')), _num(row.get('TABLEENTRYTOTAL')))
    managers, securities = set(), set()

    def scale(filed):
        return (1000, 'thousand_USD') if filed < DOLLAR_REPORTING_START else (1, 'USD')

    for accession, (form, filer, filed, period) in sorted(submissions.items()):
        if form not in ('13F-HR', '13F-HR/A') or not filer or not filed or not period:
            continue
        locator, total, entries = summary.get(accession, (None, None, None))
        name, amendment, report_type = cover.get(accession, ('', None, None))
        if filer not in managers:
            managers.add(filer)
            yield {'kind': 'entity', 'id': f'sec13fh:{prefix}:manager:{filer}', 'entity_id': filer, 'entity_type': 'organization',
                   'label': name or filer, 'observed_at': filed, 'evidence': context.raw_evidence(f'{prefix}/member:SUBMISSION.tsv', index),
                   'attributes': {'role': 'institutional investment manager (Form 13F filer)'}}
        if locator and total is not None:
            factor, unit = scale(filed)
            yield {'kind': 'observation', 'id': f'sec13fh:{accession}:portfolio_value', 'subject': filer, 'metric': 'reported_13f_portfolio_value',
                   'value': total * factor, 'unit': 'USD', 'valid_from': period, 'valid_to': _next(period), 'observed_at': filed,
                   'evidence': context.raw_evidence(locator, index), 'dimensions': {'accession': accession, 'form': form},
                   'attributes': {'entries': entries, 'amendment_type': amendment, 'report_type': report_type, 'value_source_unit': unit}}

    def flush(accession, groups):
        form, filer, filed, period = submissions.get(accession, (None, None, None, None))
        if form not in ('13F-HR', '13F-HR/A') or not filer or not filed or not period:
            return
        factor, unit = scale(filed)
        for (cusip, putcall, kind), g in groups.items():
            security = 'cusip:' + cusip
            if security not in securities:
                securities.add(security)
                yield {'kind': 'entity', 'id': f'sec13fh:{prefix}:security:{cusip}', 'entity_id': security, 'entity_type': 'security',
                       'label': (g['issuer'] + ' ' + g['title']).strip() or security, 'observed_at': filed,
                       'evidence': context.raw_evidence(g['locator'], index),
                       'attributes': {'issuer_name': g['issuer'], 'title_of_class': g['title'], 'figi': g['figi'] or None,
                                      'identity_basis': 'CUSIP in 13F information table; issuer CIK not published'}}
            attributes = {'shares' if kind == 'SH' else 'principal_amount': g['amount'], 'value_usd': g['value'] * factor,
                          'accession': accession, 'form': form, 'rows': g['rows']}
            if unit != 'USD':
                attributes['value_source_unit'] = unit
            if putcall:
                attributes['put_call'] = putcall
            if g['discretion'] != 'SOLE':
                attributes['investment_discretion'] = g['discretion']
            yield {'kind': 'assertion', 'id': f'sec13fh:{accession}:{cusip}:{putcall or "-"}:{kind}', 'subject': filer,
                   'predicate': 'reported_holding', 'object': security, 'valid_from': period, 'valid_to': _next(period),
                   'observed_at': filed, 'evidence': context.raw_evidence(g['locator'], index), 'attributes': attributes}

    current, groups = None, {}
    for locator, row in _tsv(archive, _member(names, 'INFOTABLE.TSV'), prefix):
        accession = row['ACCESSION_NUMBER']
        if accession != current:
            if current is not None:
                yield from flush(current, groups)
            current, groups = accession, {}
        cusip = (row.get('CUSIP') or '').strip().upper()
        if not CUSIP.fullmatch(cusip):
            continue
        key = (cusip, (row.get('PUTCALL') or '').strip(), (row.get('SSHPRNAMTTYPE') or 'SH').strip())
        g = groups.get(key)
        if g is None:
            g = groups[key] = {'locator': locator, 'issuer': (row.get('NAMEOFISSUER') or '').strip()[:120],
                               'title': (row.get('TITLEOFCLASS') or '').strip()[:40], 'figi': (row.get('FIGI') or '').strip(),
                               'amount': 0, 'value': 0, 'rows': 0, 'discretion': (row.get('INVESTMENTDISCRETION') or '').strip()}
        g['amount'] += _num(row.get('SSHPRNAMT')) or 0
        g['value'] += _num(row.get('VALUE')) or 0
        g['rows'] += 1
        if (row.get('INVESTMENTDISCRETION') or '').strip() != g['discretion']:
            g['discretion'] = 'MIXED'
    if current is not None:
        yield from flush(current, groups)
