"""The firm panel: issuer x fiscal period rows holding every filed vintage of core financials.

Three stages, all streaming and all joined only on identifiers a publisher asserted:

``links``
    One row per SEC CIK that a published identifier ties to an LEI: GLEIF's registration-authority
    field (authority ``RA000665``, the SEC, whose entity ID is the CIK; read by
    :mod:`worldmodel.resolution.bridges`) or the LEI the SEC's own submissions metadata prints. The
    row also lists the CUSIPs of the securities GLEIF/ANNA say that LEI issued (the CUSIP is the NSIN
    of a US or CA ISIN, ISO 6166). A CIK printed by two LEIs, an LEI claimed for two CIKs and a CUSIP
    issued by two linked LEIs are refused and reported, never merged.
``ownership``
    One row per issuer CIK x 13F report quarter: the long share positions (``SH``, no put/call) that
    13F filers reported in the CUSIPs ``links`` ties to the issuer. The 13F information table
    publishes the CUSIP and a free-text issuer name, never the issuer's CIK, so this link is the only
    way in; CUSIPs without it stay unlinked and are counted.
``panel``
    One row per (CIK, fiscal period end). Revenue, net income, assets, liabilities, equity, cash and
    shares as filed in 10-K/10-Q/20-F/40-F (and amendments). Every value is a list of *vintages*: the
    first filed value and each later filing that reported a different number for the same concept,
    period and duration, each with its filing date as ``available_at``. A restatement is a later
    vintage, never an overwrite. The row carries the LEI, the 13F aggregate for the calendar quarter
    that contains the period end, and a ``federal_contracts`` block that is ``null`` because no
    published crosswalk ties a SAM UEI to a CIK or an LEI in this catalog.

Sources for the financial values
--------------------------------
``sec_financial_statements`` (the SEC Financial Statement Data Sets, 2012Q4 onward) keeps every value
as filed, comparatives included, so a value re-reported in a later filing is visible as a vintage.
``sec_company_assets`` (companyfacts) supplies filings made **before** the first FSDS quarter
(parameter ``fsds_first_filed``); the two are partitioned by filing date so no filing is counted twice.
Dimensional (``segments``) and co-registrant values are excluded: the panel is the consolidated
filer. A fiscal period is a date that some filing of the issuer reports as its balance-sheet date
(or, without a balance sheet, its latest duration end); values dated at other instants are counted
and dropped. Cover-page shares outstanding (dei) are dated at the cover date, so they are attached to
the period of the filing that reports them and keep their own ``as_of``.

Nothing here matches names. Nothing converts currencies: each vintage keeps the filer's unit.
"""
from datetime import date, timedelta
import gzip
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess

from ..resolution.bridges import cusip_from_isin, gleif_registration_authority_claim

SCHEMA = 'worldmodel.panels.firm/1'
DATASET = 'firm_panel'

INSTANTS = ('total_assets', 'total_liabilities', 'stockholders_equity', 'cash_and_equivalents',
            'cash_restricted_cash_and_equivalents', 'shares_outstanding', 'shares_outstanding_cover')
DURATIONS = ('revenue', 'net_income', 'weighted_average_shares_diluted')
#: Instants whose latest date in a filing is taken as that filing's balance-sheet (report) date.
REPORT_PERIOD_METRICS = ('total_assets', 'total_liabilities', 'stockholders_equity', 'cash_and_equivalents',
                         'shares_outstanding')
COVER = 'shares_outstanding_cover'
#: Several standard tags map to one metric; within one filing and period the first tag listed wins,
#: so a filing that reports both ``Revenues`` and ``RevenueFromContractWithCustomer...`` is not double counted.
TAG_PRIORITY = {
    'revenue': ('Revenues', 'RevenueFromContractWithCustomerExcludingAssessedTax', 'SalesRevenueNet', 'Revenue'),
    'net_income': ('NetIncomeLoss', 'ProfitLossAttributableToOwnersOfParent'),
    'cash_and_equivalents': ('CashAndCashEquivalentsAtCarryingValue', 'CashAndCashEquivalents'),
    'stockholders_equity': ('StockholdersEquity',),
}
PERIODIC_FORMS = ('10-K', '10-K/A', '10-Q', '10-Q/A', '10-KT', '10-KT/A', '10-QT', '10-QT/A', '20-F', '20-F/A',
                  '40-F', '40-F/A')

DEFAULTS = {
    'fsds_first_filed': '2012-10-01',
    'instants': list(INSTANTS),
    'durations': list(DURATIONS),
    'duration_quarters': [1, 2, 3, 4],
    'ownership_datasets': ['sec_13f_history', 'sec_ownership_datasets'],
    'ownership_share_class': 'SH',
    'ownership_exclude_options': True,
    'thirteen_f_deadline_days': 45,
    'evidence_sample': 3,
}

#: Publication rules: every value in the panel is dated by one of these.
RULES = {
    'sec_filing_date': ('A financial value is public on the filing date of the 10-K/10-Q/20-F/40-F (or amendment) '
                        'that reports it: FSDS sub.txt `filed`, companyfacts `filed`. A later filing that reports a '
                        'different number for the same concept, period and duration is a new vintage dated by its '
                        'own filing date.'),
    'thirteen_f_filing_date': ('A 13F position is public on the filing date of the 13F-HR that reports it; the '
                               'issuer-quarter aggregate is dated by the latest filing it includes. Rule 13f-1 sets the '
                               'deadline at 45 days after the quarter end (`due_date`).'),
    'gleif_snapshot': ('CIK-LEI and CUSIP-LEI links are read from the GLEIF golden copy and ISIN-LEI mapping as '
                       'retrieved; they are current-snapshot assertions, not dated history, so a link is known from '
                       'the snapshot date. `initial_registration` says when GLEIF first issued the LEI.'),
}

CONTRACTS_NOT_LINKED = ('usaspending publishes recipients by SAM Unique Entity ID (and CAGE code) only. No dataset '
                        'in the catalog publishes a UEI-to-CIK or UEI-to-LEI crosswalk (the uei_lei mapping spec has '
                        'no source acquired; SAM does not publish CIKs), so federal obligations cannot be attributed '
                        'to an SEC issuer without matching names, which this panel does not do.')


def parameters(overrides=None):
    merged = json.loads(json.dumps(DEFAULTS))
    merged.update(overrides or {})
    return merged


# ----------------------------------------------------------------------------- reading inputs

class LineSource:
    """Raw JSON lines of pinned inputs. Decompression runs in a ``zcat`` child when one exists, so it
    overlaps with parsing; otherwise the stdlib gzip module is used."""

    def __init__(self, store, refs):
        self.store, self.refs = store, {ref['dataset']: dict(ref) for ref in refs}

    def has(self, dataset):
        return dataset in self.refs

    def ref(self, dataset):
        if dataset not in self.refs:
            raise ValueError(f'Undeclared input: {dataset}')
        return dict(self.refs[dataset])

    def path(self, dataset):
        directory = self.store.version_dir(self.ref(dataset))
        plain = directory / 'records.jsonl'
        return plain if plain.exists() else directory / 'records.jsonl.gz'

    def lines(self, dataset):
        yield from read_lines(self.path(dataset))


def read_lines(path):
    path = str(path)
    if not path.endswith('.gz'):
        with open(path, encoding='utf-8') as stream:
            yield from stream
        return
    zcat = shutil.which('zcat')
    if zcat is None:
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            yield from stream
        return
    child = subprocess.Popen([zcat, path], stdout=subprocess.PIPE, bufsize=1 << 20)
    try:
        for raw in child.stdout:
            yield raw.decode('utf-8')
    finally:
        child.stdout.close()
        child.kill() if child.poll() is None else None
        child.wait()


def _field(line, key):
    """The string value of ``"key":"..."`` in a canonical JSON line, or ``None``."""
    start = line.find(f'"{key}":"')
    if start < 0:
        return None
    start += len(key) + 4
    return line[start:line.find('"', start)]


class IdTrail:
    """Count, SHA-256 and a small sample over record ids, in the order they are added."""

    def __init__(self, sample=3):
        self.count, self.hasher, self.sample, self.limit = 0, hashlib.sha256(), [], sample

    def add(self, record_id):
        self.count += 1
        self.hasher.update(record_id.encode('utf-8') + b'\n')
        if len(self.sample) < self.limit:
            self.sample.append(record_id)

    def evidence(self, ref):
        return {'input': dict(ref), 'records': self.count, 'record_ids_sha256': self.hasher.hexdigest(),
                'sample_record_ids': list(self.sample)}


def cik10(subject):
    """``sec:cik:0000320193`` or ``320193`` -> ``0000320193``."""
    text = str(subject).rsplit(':', 1)[-1].strip()
    return text.zfill(10) if text.isdigit() else None


def _number(value):
    if isinstance(value, float) and value.is_integer() and abs(value) < 2 ** 53:
        return int(value)
    return value


def _day(value):
    return date.fromisoformat(str(value)[:10])


def quarter_end(day):
    """Last day of the calendar quarter containing ``day`` (a date or ISO string)."""
    day = _day(day) if not isinstance(day, date) else day
    month = ((day.month - 1) // 3 + 1) * 3
    following = date(day.year + (month == 12), month % 12 + 1, 1)
    return (following - timedelta(days=1)).isoformat()


# ----------------------------------------------------------------------------- links

_SNAPSHOT = re.compile(r'(\d{8})-\d{4}-gleif-goldencopy|lei-isin-(\d{8})T')


def _snapshot_date(record):
    for evidence in record.get('evidence') or []:
        match = _SNAPSHOT.search(str(evidence.get('locator', '')))
        if match:
            text = match.group(1) or match.group(2)
            return f'{text[:4]}-{text[4:6]}-{text[6:]}'
    return str(record.get('observed_at') or '')[:10] or None


def build_links(source, params=None):
    """Rows ``firm_panel:link:{cik}``: LEIs and CUSIPs tied to a CIK by published identifiers only."""
    params = parameters(params)
    counts = {'sec_submissions_lei_rows': 0, 'gleif_ra000665_entities': 0, 'gleif_ra000665_cik_shaped': 0,
              'gleif_issuer_security_rows_read': 0, 'gleif_issuer_security_non_cusip_isin': 0}
    sec_lei = {}
    trails = {}

    def trail(cik):
        item = trails.get(cik)
        if item is None:
            item = trails[cik] = {'sec_issuer_reference': IdTrail(params['evidence_sample']),
                                  'sec_gleif': IdTrail(params['evidence_sample'])}
        return item

    if source.has('sec_issuer_reference'):
        for line in source.lines('sec_issuer_reference'):
            if '"namespace":"lei"' not in line:
                continue
            record = json.loads(line)
            value = record.get('value') or {}
            cik = cik10(record.get('subject', ''))
            if record.get('predicate') != 'identifier_assignment' or value.get('namespace') != 'lei' or not cik:
                continue
            counts['sec_submissions_lei_rows'] += 1
            sec_lei.setdefault(cik, set()).add(str(value['value']).upper())
            trail(cik)['sec_issuer_reference'].add(record['id'])
    gleif_cik, lei_meta, snapshot = {}, {}, {}
    for line in source.lines('sec_gleif'):
        if '"RA000665"' not in line:
            continue
        record = json.loads(line)
        if record.get('kind') != 'entity' or not str(record.get('entity_id', '')).startswith('lei:'):
            continue
        counts['gleif_ra000665_entities'] += 1
        claim = gleif_registration_authority_claim(record.get('attributes'))
        if not claim or claim[0] != 'sec_cik':
            continue
        counts['gleif_ra000665_cik_shaped'] += 1
        lei = record['entity_id'][4:]
        attributes = record.get('attributes') or {}
        gleif_cik[lei] = claim[1].zfill(10)
        lei_meta[lei] = {'label': record.get('label'), 'entity_status': attributes.get('entity_status'),
                         'registration_status': attributes.get('registration_status'),
                         'initial_registration': attributes.get('initial_registration'),
                         'category': attributes.get('category'), 'record_id': record['id']}
        snapshot.setdefault('golden_copy', _snapshot_date(record))
    # Cardinality 1:1 (MAPPING_SPECS gleif_sec_cik): a CIK printed by two LEIs is refused.
    by_cik = {}
    for lei, cik in gleif_cik.items():
        by_cik.setdefault(cik, set()).add(lei)
    for cik, leis in sec_lei.items():
        by_cik.setdefault(cik, set())
    accepted, conflicts = {}, []
    lei_claims = {}
    for cik, leis in by_cik.items():
        for lei in leis | sec_lei.get(cik, set()):
            lei_claims.setdefault(lei, set()).add(cik)
    for cik in sorted(by_cik):
        gleif_leis, sec_leis = by_cik[cik], sec_lei.get(cik, set())
        union = gleif_leis | sec_leis
        reasons = []
        if len(gleif_leis) > 1:
            reasons.append('two_or_more_gleif_leis_print_this_cik')
        if len(sec_leis) > 1:
            reasons.append('sec_submissions_print_two_or_more_leis')
        if len(union) > 1 and not reasons:
            reasons.append('gleif_and_sec_submissions_disagree')
        if any(len(lei_claims[lei]) > 1 for lei in union):
            reasons.append('lei_claimed_for_another_cik')
        if reasons:
            conflicts.append({'cik': cik, 'leis': sorted(union), 'reasons': reasons})
            continue
        lei = next(iter(union))
        bases = (['gleif_registration_authority_RA000665'] if lei in gleif_leis else []) + \
                (['sec_submissions_lei'] if lei in sec_leis else [])
        accepted[lei] = (cik, bases)
    cusip_leis, cusip_isin = {}, {}
    for line in source.lines('sec_gleif'):
        if '"issuer_security"' not in line:
            continue
        record = json.loads(line)
        if record.get('predicate') != 'issuer_security':
            continue
        lei = str(record.get('subject', ''))[4:]
        if lei not in accepted:
            continue
        counts['gleif_issuer_security_rows_read'] += 1
        isin = str(record.get('object', ''))[5:]
        cusip = cusip_from_isin(isin)
        if cusip is None:
            counts['gleif_issuer_security_non_cusip_isin'] += 1
            continue
        snapshot.setdefault('isin_mapping', _snapshot_date(record))
        cusip_leis.setdefault(cusip, set()).add(lei)
        cusip_isin[cusip] = isin
        trail(accepted[lei][0])['sec_gleif'].add(record['id'])
    cusips_by_lei, refused_cusips = {}, []
    for cusip, leis in cusip_leis.items():
        if len(leis) > 1:
            refused_cusips.append({'cusip': cusip, 'leis': sorted(leis)})
            continue
        cusips_by_lei.setdefault(next(iter(leis)), []).append(cusip)
    link_available = snapshot.get('golden_copy') or snapshot.get('isin_mapping')
    refs = {name: source.ref(name) for name in ('sec_issuer_reference', 'sec_gleif') if source.has(name)}
    for lei, (cik, bases) in sorted(accepted.items(), key=lambda item: item[1][0]):
        meta = lei_meta.get(lei, {})
        if meta.get('record_id'):
            trail(cik)['sec_gleif'].add(meta['record_id'])
        cusips = sorted(cusips_by_lei.get(lei, []))
        evidence = [trail(cik)[name].evidence(ref) for name, ref in sorted(refs.items()) if trail(cik)[name].count]
        yield {'id': f'{DATASET}:link:{cik}', 'schema': SCHEMA, 'cik': cik, 'unit': f'sec:cik:{cik}',
               'lei': {'lei': lei, 'bases': bases, 'label': meta.get('label'), 'entity_status': meta.get('entity_status'),
                       'registration_status': meta.get('registration_status'),
                       'initial_registration': meta.get('initial_registration'), 'category': meta.get('category')},
               'cusips': [[cusip, cusip_isin[cusip]] for cusip in cusips],
               'available_at': link_available, 'rule': 'gleif_snapshot',
               'basis': ('CIK-LEI: GLEIF LEI-CDF RegistrationAuthority RA000665 entity ID and/or the LEI in SEC '
                         'submissions metadata; CUSIP: NSIN of a US/CA ISIN that the GLEIF/ANNA ISIN-LEI mapping '
                         'assigns to that LEI (check digit recomputed). No name matching.'),
               'evidence': evidence}
    counts.update(ciks_linked_to_lei=len(accepted), cik_conflicts=len(conflicts),
                  cusips_linked=sum(len(v) for v in cusips_by_lei.values()), cusips_refused=len(refused_cusips))
    yield {'id': f'{DATASET}:links:construction', 'schema': SCHEMA, 'cik': None, 'construction': counts,
           'snapshots': snapshot, 'cik_conflicts': conflicts[:500], 'refused_cusips': refused_cusips[:500],
           'evidence': []}


def link_maps(link_rows):
    """``({cik: link row}, {cusip: cik})`` from ``links`` stage rows."""
    by_cik, cusip_cik = {}, {}
    for row in link_rows:
        if not row.get('cik'):
            continue
        by_cik[row['cik']] = row
        for cusip, _isin in row.get('cusips') or []:
            cusip_cik[cusip] = row['cik']
    return by_cik, cusip_cik


# ----------------------------------------------------------------------------- 13F ownership

def resolve_13f_filings(filings):
    """Which accessions count for each (manager, period).

    ``filings``: ``{accession: (manager, period, filed, form, amendment_type)}``. For each manager and
    report period the *base* report is the latest 13F-HR or ``RESTATEMENT`` amendment (a restatement
    replaces the report); ``NEW HOLDINGS`` amendments filed after the base add positions. Everything
    else is superseded. Returns ``{accession: 'base' | 'new_holdings'}`` and counts.
    """
    groups = {}
    for accession, (manager, period, filed, form, amendment) in filings.items():
        groups.setdefault((manager, period), []).append((filed, accession, form, (amendment or '').upper()))
    kept, counts = {}, {'manager_periods': len(groups), 'restatements_applied': 0, 'new_holdings_amendments': 0,
                        'superseded_filings': 0}
    for items in groups.values():
        items.sort()
        bases = [item for item in items if not item[2].endswith('/A') or item[3] == 'RESTATEMENT']
        if not bases:
            bases = [item for item in items if item[3] != 'NEW HOLDINGS'] or items[:1]
        base = bases[-1]
        kept[base[1]] = 'base'
        if base[3] == 'RESTATEMENT':
            counts['restatements_applied'] += 1
        for item in items:
            if item is base:
                continue
            if item[3] == 'NEW HOLDINGS' and item[:2] > base[:2]:
                kept[item[1]] = 'new_holdings'
                counts['new_holdings_amendments'] += 1
            else:
                counts['superseded_filings'] += 1
    return kept, counts


def _holding_parts(record_id):
    """``(accession, cusip, put_call, share_class)`` from ``sec13f*:{acc}:{cusip}:{putcall}:{SH|PRN}``."""
    parts = record_id.split(':')
    if len(parts) != 5:
        return None
    return parts[1], parts[2], parts[3], parts[4]


def build_ownership(source, link_rows, params=None):
    """Rows ``firm_panel:ownership:{cik}:{quarter_end}`` aggregating 13F long share positions per issuer."""
    params = parameters(params)
    _, cusip_cik = link_maps(link_rows)
    datasets = [name for name in params['ownership_datasets'] if source.has(name)]
    filings, counts = {}, {'portfolio_rows': 0, 'holding_rows': 0, 'holdings_superseded': 0, 'holdings_other_class': 0,
                           'holdings_options': 0, 'holdings_unlinked_cusip': 0, 'holdings_linked': 0,
                           'holding_period_differs_from_filing': 0, 'non_contiguous_accessions': 0,
                           'holdings_without_filing_summary': 0}
    for dataset in datasets:
        for line in source.lines(dataset):
            if '"reported_13f_portfolio_value"' not in line:
                continue
            record = json.loads(line)
            if record.get('metric') != 'reported_13f_portfolio_value':
                continue
            counts['portfolio_rows'] += 1
            dims, attributes = record.get('dimensions') or {}, record.get('attributes') or {}
            accession = dims.get('accession')
            if not accession or not str(dims.get('form') or '').startswith('13F-HR'):
                continue
            filings[accession] = (record['subject'], str(record['valid_from'])[:10], str(record['observed_at'])[:10],
                                  dims.get('form') or '', attributes.get('amendment_type'))
    kept, resolution = resolve_13f_filings(filings)
    counts.update(resolution)
    watched = {(filings[a][0], filings[a][1]) for a, role in kept.items() if role == 'new_holdings'}
    deadline = params['thirteen_f_deadline_days']
    share_class, no_options = params['ownership_share_class'], params['ownership_exclude_options']
    aggregates, trails = {}, {}
    base_seen, new_seen = set(), set()
    linked_value, all_value = {}, {}
    finished, current, current_issuers = set(), None, set()
    for dataset in datasets:
        for line in source.lines(dataset):
            if '"reported_holding"' not in line:
                continue
            record_id = _field(line, 'id')
            parts = _holding_parts(record_id or '')
            if parts is None:
                continue
            counts['holding_rows'] += 1
            accession, cusip, put_call, klass = parts
            role = kept.get(accession)
            if role is None:
                if accession in filings:
                    counts['holdings_superseded'] += 1
                else:
                    counts['holdings_without_filing_summary'] += 1
                continue
            if klass != share_class:
                counts['holdings_other_class'] += 1
                continue
            if no_options and put_call != '-':
                counts['holdings_options'] += 1
                continue
            if accession != current:
                if current is not None:
                    finished.add(current)
                if accession in finished:
                    counts['non_contiguous_accessions'] += 1
                current, current_issuers = accession, set()
            record = json.loads(line)
            attributes = record.get('attributes') or {}
            manager, period, filed = filings[accession][:3]
            if str(record.get('valid_from', ''))[:10] != period:
                counts['holding_period_differs_from_filing'] += 1
            value = attributes.get('value_usd') or 0
            all_value[period] = all_value.get(period, 0) + value
            cik = cusip_cik.get(cusip)
            if cik is None:
                counts['holdings_unlinked_cusip'] += 1
                continue
            counts['holdings_linked'] += 1
            linked_value[period] = linked_value.get(period, 0) + value
            key = (cik, period)
            item = aggregates.get(key)
            if item is None:
                due = (_day(period) + timedelta(days=deadline)).isoformat()
                item = aggregates[key] = {'filings': 0, 'managers': 0, 'shares': 0, 'value_usd': 0, 'cusips': set(),
                                          'first_filed': filed, 'last_filed': filed, 'value_usd_filed_by_due_date': 0,
                                          'due_date': due, 'new_holdings_filings': 0}
                trails[key] = {}
            trail = trails[key].get(dataset)
            if trail is None:
                trail = trails[key][dataset] = IdTrail(params['evidence_sample'])
            trail.add(record_id)
            item['shares'] += attributes.get('shares') or 0
            item['value_usd'] += value
            item['cusips'].add(cusip)
            item['first_filed'] = min(item['first_filed'], filed)
            item['last_filed'] = max(item['last_filed'], filed)
            if filed <= item['due_date']:
                item['value_usd_filed_by_due_date'] += value
            if cik in current_issuers:
                continue
            current_issuers.add(cik)
            item['filings'] += 1
            if role == 'base':
                item['managers'] += 1
                if (manager, period) in watched:
                    base_seen.add((manager, period, cik))
            else:
                item['new_holdings_filings'] += 1
                new_seen.add((manager, period, cik))
    for manager, period, cik in new_seen - base_seen:
        aggregates[(cik, period)]['managers'] += 1
    refs = {dataset: source.ref(dataset) for dataset in datasets}
    for (cik, period), item in sorted(aggregates.items()):
        value = item['value_usd']
        yield {'id': f'{DATASET}:ownership:{cik}:{period}', 'schema': SCHEMA, 'cik': cik, 'unit': f'sec:cik:{cik}',
               'quarter_end': period, 'managers': item['managers'], 'filings': item['filings'],
               'new_holdings_filings': item['new_holdings_filings'], 'shares': _number(item['shares']),
               'value_usd': _number(value), 'cusips': sorted(item['cusips']),
               'first_filed': item['first_filed'], 'available_at': item['last_filed'], 'rule': 'thirteen_f_filing_date',
               'due_date': item['due_date'],
               'value_share_filed_by_due_date': round(item['value_usd_filed_by_due_date'] / value, 4) if value else None,
               'basis': (f'13F information-table rows, share class {share_class}'
                         + (', no put/call' if no_options else '') + '; CUSIP -> CIK through the links stage only'),
               'evidence': [trail.evidence(refs[name]) for name, trail in sorted(trails[(cik, period)].items())]}
    by_period = {period: {'all_value_usd': _number(all_value[period]),
                          'linked_value_usd': _number(linked_value.get(period, 0)),
                          'linked_share': round(linked_value.get(period, 0) / all_value[period], 4) if all_value[period] else None}
                 for period in sorted(all_value)}
    counts.update(issuer_quarters=len(aggregates), issuers=len({cik for cik, _ in aggregates}))
    yield {'id': f'{DATASET}:ownership:construction', 'schema': SCHEMA, 'cik': None, 'construction': counts,
           'value_linked_by_quarter': by_period, 'datasets': datasets, 'evidence': []}


# ----------------------------------------------------------------------------- financial vintages

FACT_COLUMNS = ('cik', 'period_end', 'metric', 'qtrs', 'filed', 'accession', 'form', 'fy', 'fp', 'tag_rank', 'concept',
                'value', 'unit', 'source', 'record_id', 'as_of')
SOURCES = ('sec_financial_statements', 'sec_company_assets')


def _tag_rank(metric, concept):
    tag = str(concept).rsplit(':', 1)[-1]
    order = TAG_PRIORITY.get(metric, ())
    return order.index(tag) if tag in order else len(order)


def _qtrs_from_dates(start, end):
    days = (_day(end) - _day(start)).days + 1
    quarters = round(days / 91.3125)
    return quarters if quarters and abs(days - quarters * 91.3125) <= 20 else None


def _fsds_fact(record, wanted):
    dims, attributes = record.get('dimensions') or {}, record.get('attributes') or {}
    if 'segments' in dims or 'coregistrant' in dims:
        return None
    metric = record['metric']
    qtrs = int(dims.get('qtrs') or 0)
    if (qtrs == 0) != (metric in wanted['instants']) or (qtrs and qtrs not in wanted['quarters']):
        return None
    return (int(cik10(record['subject'])), attributes['period_end'], metric, qtrs, str(record['observed_at'])[:10],
            attributes['accession'], dims.get('form'), str(dims.get('fiscal_year') or ''), dims.get('fiscal_period'),
            _tag_rank(metric, dims.get('concept')), dims.get('concept'), record['value'], record.get('unit'), 0,
            record['id'], None)


def _facts_fact(record, wanted):
    dims, attributes = record.get('dimensions') or {}, record.get('attributes') or {}
    metric = record['metric']
    end = attributes.get('period_end')
    if not end or dims.get('form') not in PERIODIC_FORMS:
        return None
    if dims.get('period_type') == 'instant':
        qtrs = 0
    else:
        qtrs = _qtrs_from_dates(attributes.get('period_start') or record.get('valid_from'), end)
        if qtrs is None:
            return None
    if (qtrs == 0) != (metric in wanted['instants']) or (qtrs and qtrs not in wanted['quarters']):
        return None
    return (int(cik10(record['subject'])), end, metric, qtrs, str(record['observed_at'])[:10], attributes.get('accession'),
            dims.get('form'), str(dims.get('fiscal_year') or ''), dims.get('fiscal_period'),
            _tag_rank(metric, dims.get('concept')), dims.get('concept'), record['value'], record.get('unit'), 1,
            record['id'], None)


def _load_facts(source, params, db, counts, issuers):
    wanted = {'instants': set(params['instants']), 'durations': set(params['durations']),
              'quarters': set(params['duration_quarters'])}
    metrics = wanted['instants'] | wanted['durations']
    cutoff = params['fsds_first_filed']
    insert = 'INSERT INTO facts VALUES (' + ','.join('?' * len(FACT_COLUMNS)) + ')'
    for index, dataset in enumerate(SOURCES):
        if not source.has(dataset):
            continue
        batch = []
        for line in source.lines(dataset):
            metric = _field(line, 'metric')
            if metric is None:
                if '"kind":"entity"' in line and '"sec:cik:' in line:
                    record = json.loads(line)
                    cik = cik10(record.get('entity_id', ''))
                    if cik and record.get('entity_id', '').startswith('sec:cik:'):
                        # Prefer the latest FSDS submission's name and SIC; companyfacts only fills gaps.
                        rank = (index == 0, str(record.get('observed_at') or '')[:10])
                        old = issuers.get(cik)
                        if old is None or rank >= old[0]:
                            issuers[cik] = (rank, record.get('label'), (record.get('attributes') or {}).get('sic'))
                continue
            if metric not in metrics:
                continue
            if index == 0 and ('"segments":' in line or '"coregistrant":' in line):
                counts['fsds_dimensional_or_coregistrant_values_skipped'] += 1
                continue
            record = json.loads(line)
            filed = str(record.get('observed_at'))[:10]
            if index == 1 and filed >= cutoff:
                continue
            if index == 0 and filed < cutoff:
                counts['fsds_values_before_cutoff_skipped'] += 1
                continue
            fact = (_fsds_fact if index == 0 else _facts_fact)(record, wanted)
            if fact is None:
                counts[f'{dataset}_values_outside_declared_durations'] += 1
                continue
            counts[f'{dataset}_values_loaded'] += 1
            batch.append(fact)
            if len(batch) >= 50000:
                db.executemany(insert, batch)
                batch = []
        if batch:
            db.executemany(insert, batch)
        db.commit()


def _open_work(workdir):
    os.makedirs(workdir, exist_ok=True)
    path = os.path.join(workdir, 'firm_panel_facts.sqlite')
    for suffix in ('', '-journal', '-wal', '-shm'):
        if os.path.exists(path + suffix):
            os.unlink(path + suffix)
    os.environ['SQLITE_TMPDIR'] = workdir
    db = sqlite3.connect(path)
    db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA temp_store=FILE; PRAGMA cache_size=-262144;'
                     'CREATE TABLE facts (' + ', '.join(FACT_COLUMNS) + ');')
    return db, path


def _vintages(facts):
    """Vintages of one (metric, duration) series: first report, then each filing whose value differs.

    ``facts`` are rows sorted by filing date, accession and tag rank. Within one filing the
    highest-priority tag is used; repeats of an unchanged value count as ``confirmations``.
    """
    vintages, conflicts = [], 0
    current = None
    for fact in facts:
        (_, _, metric, _, filed, accession, form, _, _, _, concept, value, unit, source, record_id, as_of) = fact
        value = _number(value)
        if current is not None and current[0] == accession:
            if current[1] == concept and current[2] != value:
                conflicts += 1
            continue
        current = (accession, concept, value)
        if vintages and vintages[-1]['value'] == value and vintages[-1]['unit'] == unit:
            vintages[-1]['confirmations'] += 1
            continue
        vintage = {'value': value, 'unit': unit, 'available_at': filed, 'accession': accession, 'form': form,
                   'concept': concept, 'source': SOURCES[source],
                   'revision': 'first_report' if not vintages else 'restated', 'confirmations': 0, 'record_id': record_id}
        if as_of:
            vintage['as_of'] = as_of
        vintages.append(vintage)
    return vintages, conflicts


def build_panel(source, link_rows, ownership_rows, params=None, workdir=None, links_ref=None, ownership_ref=None):
    """Rows ``firm_panel:{cik}:{period_end}`` with every filed vintage, the LEI and the 13F aggregate."""
    params = parameters(params)
    links, _ = link_maps(link_rows)
    ownership = {(row['cik'], row['quarter_end']): row for row in ownership_rows if row.get('cik')}
    counts = {key: 0 for key in ('fsds_dimensional_or_coregistrant_values_skipped', 'fsds_values_before_cutoff_skipped',
                                 'sec_financial_statements_values_loaded', 'sec_company_assets_values_loaded',
                                 'sec_financial_statements_values_outside_declared_durations',
                                 'sec_company_assets_values_outside_declared_durations')}
    issuers = {}
    workdir = workdir or os.path.join(os.getcwd(), '.firm_panel_work')
    db, path = _open_work(workdir)
    try:
        _load_facts(source, params, db, counts, issuers)
        yield from _panel_rows(source, db, params, counts, issuers, links, ownership, links_ref, ownership_ref)
    finally:
        db.close()
        for suffix in ('', '-journal'):
            if os.path.exists(path + suffix):
                os.unlink(path + suffix)


def _panel_rows(source, db, params, counts, issuers, links, ownership, links_ref, ownership_ref):
    db.executescript(f'''
        CREATE TABLE acc AS SELECT accession, MIN(cik) AS cik, MIN(filed) AS filed, MIN(form) AS form, MIN(fy) AS fy,
            MIN(fp) AS fp, MIN(source) AS source,
            MAX(CASE WHEN qtrs = 0 AND metric IN ({", ".join(repr(m) for m in REPORT_PERIOD_METRICS)}) THEN period_end END) AS bs_end,
            MAX(CASE WHEN qtrs > 0 THEN period_end END) AS duration_end
          FROM facts GROUP BY accession;
        CREATE UNIQUE INDEX acc_key ON acc(accession);
        UPDATE facts SET as_of = period_end,
            period_end = (SELECT COALESCE(bs_end, duration_end) FROM acc WHERE acc.accession = facts.accession)
          WHERE metric = '{COVER}';
        CREATE TABLE periods AS SELECT cik, COALESCE(bs_end, duration_end) AS period_end, accession, filed, form, fy, fp
          FROM acc WHERE COALESCE(bs_end, duration_end) IS NOT NULL;
        CREATE INDEX periods_key ON periods(cik, period_end, filed, accession);
        CREATE INDEX facts_key ON facts(cik, period_end, metric, qtrs, filed, accession, tag_rank);
    ''')
    counts['facts_total'] = db.execute('SELECT COUNT(*) FROM facts').fetchone()[0]
    counts['filings'] = db.execute('SELECT COUNT(*) FROM acc').fetchone()[0]
    period_cursor = db.execute('SELECT cik, period_end, accession, filed, form, fy, fp FROM periods '
                               'ORDER BY cik, period_end, filed, accession')
    fact_cursor = db.execute('SELECT ' + ', '.join(FACT_COLUMNS) + ' FROM facts WHERE period_end IS NOT NULL '
                             'ORDER BY cik, period_end, metric, qtrs, filed, accession, tag_rank, record_id')
    refs = {name: source.ref(name) for name in SOURCES if source.has(name)}
    pending_period = next(period_cursor, None)
    group, key = [], None
    stats = {'rows': 0, 'values_not_at_a_fiscal_period': 0, 'restated_series': 0, 'same_filing_conflicts': 0,
             'rows_with_lei': 0, 'rows_with_ownership': 0, 'issuers': set()}

    def periods_for(target):
        nonlocal pending_period
        found = []
        while pending_period is not None and pending_period[:2] < target:
            pending_period = next(period_cursor, None)
        while pending_period is not None and pending_period[:2] == target:
            found.append(pending_period)
            pending_period = next(period_cursor, None)
        return found

    def emit(key, group):
        filings = periods_for(key)
        if not filings:
            stats['values_not_at_a_fiscal_period'] += len(group)
            return None
        return _row(key, group, filings, params, refs, issuers, links, ownership, links_ref, ownership_ref, stats)

    for fact in fact_cursor:
        fact_key = (fact[0], fact[1])
        if fact_key != key:
            if group:
                row = emit(key, group)
                if row:
                    yield row
            key, group = fact_key, []
        group.append(fact)
    if group:
        row = emit(key, group)
        if row:
            yield row
    stats['issuers'] = len(stats['issuers'])
    counts.update(stats)
    counts['issuer_entities_seen'] = len(issuers)
    yield {'id': f'{DATASET}:panel:construction', 'schema': SCHEMA, 'cik': None, 'construction': counts,
           'rules': RULES, 'federal_contracts': {'linked': False, 'reason': CONTRACTS_NOT_LINKED}, 'evidence': []}


def _row(key, group, filings, params, refs, issuers, links, ownership, links_ref, ownership_ref, stats):
    cik_int, period_end = key
    cik = str(cik_int).zfill(10)
    series = {}
    for fact in group:
        series.setdefault((fact[2], fact[3]), []).append(fact)
    instants, durations = {}, {}
    trails = {name: IdTrail(params['evidence_sample']) for name in refs}
    for (metric, qtrs), facts in sorted(series.items()):
        vintages, conflicts = _vintages(facts)
        stats['same_filing_conflicts'] += conflicts
        if len(vintages) > 1:
            stats['restated_series'] += 1
        for vintage in vintages:
            trails[vintage['source']].add(vintage['record_id'])
        if qtrs == 0:
            instants[metric] = vintages
        else:
            durations.setdefault(metric, {})[str(qtrs)] = vintages
    origin = filings[0]
    issuer = issuers.get(cik) or (None, None, None)
    stats['issuers'].add(cik)
    link = links.get(cik)
    held = ownership.get((cik, quarter_end(period_end)))
    evidence = [trail.evidence(refs[name]) for name, trail in sorted(trails.items()) if trail.count]
    if link and links_ref:
        evidence.append({'input': dict(links_ref), 'records': 1, 'record_ids': [link['id']]})
    if held and ownership_ref:
        evidence.append({'input': dict(ownership_ref), 'records': 1, 'record_ids': [held['id']]})
    stats['rows'] += 1
    stats['rows_with_lei'] += bool(link)
    stats['rows_with_ownership'] += bool(held)
    return {
        'id': f'{DATASET}:{cik}:{period_end}', 'schema': SCHEMA, 'cik': cik, 'unit': f'sec:cik:{cik}',
        'label': issuer[1], 'sic': issuer[2], 'period_end': period_end,
        'fiscal_year': int(origin[5]) if str(origin[5]).isdigit() else (origin[5] or None),
        'fiscal_period': origin[6], 'form': origin[4], 'first_filed': origin[3], 'first_accession': origin[2],
        'filings_for_period': len(filings), 'available_at': min(f[3] for f in filings),
        'instants': instants, 'durations': durations,
        'lei': None if not link else {'lei': link['lei']['lei'], 'bases': link['lei']['bases'],
                                      'available_at': link['available_at'], 'rule': 'gleif_snapshot'},
        'institutional_ownership': None if not held else {
            'quarter_end': held['quarter_end'], 'managers': held['managers'], 'filings': held['filings'],
            'shares': held['shares'], 'value_usd': held['value_usd'], 'available_at': held['available_at'],
            'due_date': held['due_date'], 'rule': 'thirteen_f_filing_date', 'record_id': held['id'],
            'alignment': 'calendar quarter containing period_end'},
        'federal_contracts': None,
        'linked': {'financials': True, 'lei': bool(link), 'institutional_ownership': bool(held), 'federal_contracts': False},
        'evidence': evidence}


# ----------------------------------------------------------------------------- reading the published panel

def value_as_of(vintages, known_at):
    """The latest vintage public on or before ``known_at`` (ISO date), or ``None``."""
    chosen = None
    for vintage in vintages or []:
        if vintage['available_at'] <= str(known_at)[:10]:
            chosen = vintage
    return chosen


def coverage(rows, ownership_rows=()):
    """Issuers, fiscal periods and the share of issuers (and rows) with each block linked."""
    issuers, rows_n, periods = {}, 0, set()
    blocks = ('lei', 'institutional_ownership', 'federal_contracts')
    row_links = {block: 0 for block in blocks}
    restated = 0
    by_year = {}
    for row in rows:
        if not row.get('cik') or 'linked' not in row:
            continue
        rows_n += 1
        periods.add(row['period_end'])
        entry = issuers.setdefault(row['cik'], {block: False for block in blocks})
        for block in blocks:
            linked = bool(row['linked'].get(block))
            entry[block] = entry[block] or linked
            row_links[block] += linked
        restated += any(len(v) > 1 for v in row['instants'].values()) or \
            any(len(v) > 1 for d in row['durations'].values() for v in d.values())
        year = row['period_end'][:4]
        slot = by_year.setdefault(year, {'rows': 0, 'issuers': set(), 'lei': 0, 'institutional_ownership': 0})
        slot['rows'] += 1
        slot['issuers'].add(row['cik'])
        slot['lei'] += bool(row['linked'].get('lei'))
        slot['institutional_ownership'] += bool(row['linked'].get('institutional_ownership'))
    owned = {row['cik'] for row in ownership_rows if row.get('cik')}
    n = len(issuers) or 1
    return {
        'rows': rows_n, 'issuers': len(issuers), 'distinct_period_ends': len(periods),
        'rows_with_a_restated_value': restated,
        'issuer_share': {block: round(sum(e[block] for e in issuers.values()) / n, 4) for block in blocks},
        'row_share': {block: round(row_links[block] / (rows_n or 1), 4) for block in blocks},
        'ownership_issuers_without_financial_rows': len(owned - set(issuers)) if owned else None,
        'by_period_end_year': {year: {'rows': s['rows'], 'issuers': len(s['issuers']),
                                      'lei_share': round(s['lei'] / s['rows'], 4),
                                      'ownership_share': round(s['institutional_ownership'] / s['rows'], 4)}
                               for year, s in sorted(by_year.items())},
    }
