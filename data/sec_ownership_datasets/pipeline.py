"""SEC DERA ownership data sets -> dated holder->security and insider->issuer evidence.

Shards are ZIP data sets identified by their members:

* Form 13F (INFOTABLE.tsv present): 13F-HR and 13F-HR/A information-table rows are
  aggregated per (accession, CUSIP, put/call, SH/PRN) into ``reported_holding`` assertions from
  the filing manager ``sec:cik:<filer>`` to the security ``cusip:<CUSIP>``, valid for the
  report period end date and known at the filing date. 13F publishes no issuer CIK, so no
  issuer is inferred. VALUE is USD (whole dollars since 2023). One portfolio-value
  observation per filing. 13F-NT notices carry no holdings.
* Forms 3/4/5 (NONDERIV_TRANS.tsv present): ``insider_of`` intervals per (owner, issuer,
  relationship) per quarter, ``insider_transaction`` / ``insider_derivative_transaction``
  events, and ``insider_shares_owned`` holding observations. Derivative holdings and
  footnotes stay in raw only.

Entities are emitted once per data set ZIP (ids include the shard), so a manager or security
appearing in several quarterly 13F data sets has one entity record per quarter. All knowledge times are filing dates. SEC notes these data sets are extracted as filed and
unverified; amendments are kept as separate filings.
"""
import csv
import io
import re
import zipfile
from datetime import date, datetime, timedelta

MONTHS = {m: i for i, m in enumerate(('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'), 1)}
CIK = re.compile(r'[0-9]{1,10}')
CUSIP = re.compile(r'[0-9A-Z*@#]{9}')


def _day(text):
    """'31-JUL-2026' -> '2026-07-31' (None when blank/invalid)."""
    if not text:
        return None
    try:
        d, m, y = text.strip().split('-')
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
    """Yield (locator, row dict) from a tab-separated member (no quoting, bounded fields)."""
    csv.field_size_limit(16 * 1024 * 1024)
    with archive.open(member) as stream:
        text = io.TextIOWrapper(stream, encoding='utf-8', errors='replace', newline='')
        reader = csv.reader(text, delimiter='\t', quoting=csv.QUOTE_NONE)
        header = next(reader)
        last = 1
        for values in reader:
            start, last = last + 1, reader.line_num
            if not values or values == ['']:
                continue
            values = (values + [''] * len(header))[:len(header)]
            yield f'{prefix}/member:{member}/line:{start}', dict(zip(header, values))


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            raise ValueError('sec_ownership_datasets requires a full acquisition artifact (SEC data set ZIPs)')
        for shard in context.raw_shards(index):
            with zipfile.ZipFile(shard['path']) as archive:
                names = set(archive.namelist())
                prefix = f'shard:{shard["index"]}'
                if 'INFOTABLE.tsv' in names:
                    yield from _form13f(context, index, archive, prefix)
                elif 'NONDERIV_TRANS.tsv' in names:
                    yield from _form345(context, index, archive, prefix)
                else:
                    raise ValueError(f'{prefix}: unrecognized SEC data set ZIP')


def _form13f(context, index, archive, prefix):
    submissions, cover, summary = {}, {}, {}
    for _, row in _tsv(archive, 'SUBMISSION.tsv', prefix):
        submissions[row['ACCESSION_NUMBER']] = (row['SUBMISSIONTYPE'], _cik(row['CIK']), _day(row['FILING_DATE']), _day(row['PERIODOFREPORT']))
    for _, row in _tsv(archive, 'COVERPAGE.tsv', prefix):
        cover[row['ACCESSION_NUMBER']] = (row.get('FILINGMANAGER_NAME') or '', row.get('AMENDMENTTYPE') or None, row.get('REPORTTYPE') or None)
    for locator, row in _tsv(archive, 'SUMMARYPAGE.tsv', prefix):
        summary[row['ACCESSION_NUMBER']] = (locator, _num(row.get('TABLEVALUETOTAL')), _num(row.get('TABLEENTRYTOTAL')))
    managers, securities = set(), set()
    for accession, (form, filer, filed, period) in sorted(submissions.items()):
        if form not in ('13F-HR', '13F-HR/A') or not filer or not filed or not period:
            continue
        locator, total, entries = summary.get(accession, (None, None, None))
        name, amendment, report_type = cover.get(accession, ('', None, None))
        if filer not in managers:
            managers.add(filer)
            yield {'kind': 'entity', 'id': f'sec13f:{prefix}:manager:{filer}', 'entity_id': filer, 'entity_type': 'organization',
                   'label': name or filer, 'observed_at': filed, 'evidence': context.raw_evidence(f'{prefix}/member:SUBMISSION.tsv', index),
                   'attributes': {'role': 'institutional investment manager (Form 13F filer)'}}
        if locator and total is not None:
            yield {'kind': 'observation', 'id': f'sec13f:{accession}:portfolio_value', 'subject': filer, 'metric': 'reported_13f_portfolio_value',
                   'value': total, 'unit': 'USD', 'valid_from': period, 'valid_to': _next(period), 'observed_at': filed,
                   'evidence': context.raw_evidence(locator, index),
                   'dimensions': {'accession': accession, 'form': form}, 'attributes': {'entries': entries, 'amendment_type': amendment, 'report_type': report_type}}

    def flush(accession, groups):
        form, filer, filed, period = submissions.get(accession, (None, None, None, None))
        if form not in ('13F-HR', '13F-HR/A') or not filer or not filed or not period:
            return
        for (cusip, putcall, kind), g in groups.items():
            security = 'cusip:' + cusip
            if security not in securities:
                securities.add(security)
                yield {'kind': 'entity', 'id': f'sec13f:{prefix}:security:{cusip}', 'entity_id': security, 'entity_type': 'security',
                       'label': (g['issuer'] + ' ' + g['title']).strip() or security, 'observed_at': filed,
                       'evidence': context.raw_evidence(g['locator'], index),
                       'attributes': {'issuer_name': g['issuer'], 'title_of_class': g['title'], 'figi': g['figi'] or None,
                                      'identity_basis': 'CUSIP in 13F information table; issuer CIK not published'}}
            record = {'kind': 'assertion', 'id': f'sec13f:{accession}:{cusip}:{putcall or "-"}:{kind}', 'subject': filer,
                      'predicate': 'reported_holding', 'object': security, 'valid_from': period, 'valid_to': _next(period),
                      'observed_at': filed, 'evidence': context.raw_evidence(g['locator'], index),
                      'attributes': {'shares' if kind == 'SH' else 'principal_amount': g['amount'], 'value_usd': g['value'],
                                     'accession': accession, 'form': form, 'rows': g['rows']}}
            if putcall:
                record['attributes']['put_call'] = putcall
            if g['discretion'] != 'SOLE':
                record['attributes']['investment_discretion'] = g['discretion']
            yield record

    current, groups = None, {}
    for locator, row in _tsv(archive, 'INFOTABLE.tsv', prefix):
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


def _form345(context, index, archive, prefix):
    submissions, owners = {}, {}
    for locator, row in _tsv(archive, 'SUBMISSION.tsv', prefix):
        issuer = _cik(row['ISSUERCIK'])
        filed = _day(row['FILING_DATE'])
        if issuer and filed:
            submissions[row['ACCESSION_NUMBER']] = (issuer, filed, _day(row['PERIOD_OF_REPORT']) or filed, row['DOCUMENT_TYPE'],
                                                    (row.get('ISSUERNAME') or '').strip(), (row.get('ISSUERTRADINGSYMBOL') or '').strip().upper())
    for locator, row in _tsv(archive, 'REPORTINGOWNER.tsv', prefix):
        owner = _cik(row['RPTOWNERCIK'])
        if owner:
            owners.setdefault(row['ACCESSION_NUMBER'], []).append((owner, (row.get('RPTOWNERNAME') or '').strip(), row.get('RPTOWNER_RELATIONSHIP') or '',
                                                                    (row.get('RPTOWNER_TITLE') or '').strip()[:80], locator))
    entities, relations = set(), {}
    for accession, (issuer, filed, period, form, issuer_name, symbol) in submissions.items():
        if issuer not in entities:
            entities.add(issuer)
            yield {'kind': 'entity', 'id': f'sec345:{prefix}:issuer:{issuer}', 'entity_id': issuer, 'entity_type': 'business',
                   'label': issuer_name or issuer, 'observed_at': filed, 'evidence': context.raw_evidence(f'{prefix}/member:SUBMISSION.tsv', index),
                   'attributes': {'reported_trading_symbol': symbol or None, 'identity_basis': 'ISSUERCIK in Forms 3/4/5'}}
        for owner, name, relationship, title, locator in owners.get(accession, []):
            if owner not in entities:
                entities.add(owner)
                yield {'kind': 'entity', 'id': f'sec345:{prefix}:owner:{owner}', 'entity_id': owner, 'entity_type': 'agent',
                       'label': name or owner, 'observed_at': filed, 'evidence': context.raw_evidence(locator, index),
                       'attributes': {'identity_basis': 'RPTOWNERCIK in Forms 3/4/5 (person or entity not distinguished)'}}
            key = (owner, issuer, relationship, title)
            first = relations.get(key)
            if first is None:
                relations[key] = [period, period, filed, locator]
            else:
                first[0], first[1] = min(first[0], period), max(first[1], period)
                first[2] = min(first[2], filed)
    for (owner, issuer, relationship, title), (start, end, filed, locator) in sorted(relations.items()):
        yield {'kind': 'assertion', 'id': f'sec345:{prefix}:insider:{owner}:{issuer}:{relationship}:{title}', 'subject': owner,
               'predicate': 'insider_of', 'object': issuer, 'valid_from': start, 'valid_to': _next(end), 'observed_at': filed,
               'evidence': context.raw_evidence(locator, index),
               'attributes': {'relationship': [r for r in relationship.split(',') if r], 'officer_title': title or None,
                              'validity_basis': 'first to last period of report among this quarter\'s filings'}}
    relations.clear()

    def participants(accession):
        sub = submissions.get(accession)
        if not sub:
            return None, None
        return sub, [o[0] for o in owners.get(accession, [])]

    for member, event_type in (('NONDERIV_TRANS.tsv', 'insider_transaction'), ('DERIV_TRANS.tsv', 'insider_derivative_transaction')):
        for locator, row in _tsv(archive, member, prefix):
            sub, people = participants(row['ACCESSION_NUMBER'])
            day = _day(row.get('TRANS_DATE'))
            if not sub or not people or not day:
                continue
            issuer, filed, period, form, _, _ = sub
            attributes = {'accession': row['ACCESSION_NUMBER'], 'form': row.get('TRANS_FORM_TYPE') or form, 'code': row.get('TRANS_CODE') or None,
                          'shares': _num(row.get('TRANS_SHARES')), 'price_per_share_usd': _num(row.get('TRANS_PRICEPERSHARE')),
                          'acquired_disposed': row.get('TRANS_ACQUIRED_DISP_CD') or None,
                          'shares_owned_following': _num(row.get('SHRS_OWND_FOLWNG_TRANS')),
                          'direct_indirect': row.get('DIRECT_INDIRECT_OWNERSHIP') or None, 'security_title': (row.get('SECURITY_TITLE') or '')[:80]}
            if event_type == 'insider_derivative_transaction':
                attributes.update(underlying_title=(row.get('UNDLYNG_SEC_TITLE') or '')[:80], underlying_shares=_num(row.get('UNDLYNG_SEC_SHARES')),
                                  exercise_price_usd=_num(row.get('CONV_EXERCISE_PRICE')), expiration=_day(row.get('EXPIRATION_DATE')))
            yield {'kind': 'event', 'id': f'sec345:{row["ACCESSION_NUMBER"]}:{member[:-4].lower()}:{locator.rsplit(":", 1)[-1]}',
                   'event_type': event_type, 'occurred_at': day, 'participants': people + [issuer], 'observed_at': filed,
                   'evidence': context.raw_evidence(locator, index), 'attributes': {k: v for k, v in attributes.items() if v not in (None, '')}}
    for locator, row in _tsv(archive, 'NONDERIV_HOLDING.tsv', prefix):
        sub, people = participants(row['ACCESSION_NUMBER'])
        shares = _num(row.get('SHRS_OWND_FOLWNG_TRANS'))
        if not sub or not people or shares is None:
            continue
        issuer, filed, period, form, _, _ = sub
        yield {'kind': 'observation', 'id': f'sec345:{row["ACCESSION_NUMBER"]}:holding:{locator.rsplit(":", 1)[-1]}', 'subject': people[0],
               'metric': 'insider_shares_owned', 'value': shares, 'unit': 'shares', 'valid_from': period, 'valid_to': _next(period),
               'observed_at': filed, 'evidence': context.raw_evidence(locator, index),
               'dimensions': {'issuer': issuer, 'security_title': (row.get('SECURITY_TITLE') or '')[:80],
                              'ownership': row.get('DIRECT_INDIRECT_OWNERSHIP') or 'unknown'},
               'attributes': {'accession': row['ACCESSION_NUMBER'], 'form': form, 'joint_reporting_owners': people[1:] or None}}
