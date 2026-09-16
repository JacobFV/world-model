"""FEC bulk files -> committees, candidate linkages, cycle summaries and aggregated money flows.

Full sharded acquisition (one ZIP per file type and cycle, pipe-delimited, headerless, latin-1):

* ``cmYY``   committee master -> committee entities (newest name) + per-cycle registration assertions
* ``cclYY``  candidate-committee linkages -> ``authorized_committee_of`` assertions
* ``weballYY`` all-candidate summary -> candidate cycle-to-date financial observations
* ``webkYY`` PAC and party summary -> committee cycle-to-date financial observations
* ``pas2YY`` contributions from committees to candidates -> monthly aggregates per committee x candidate x
  transaction type, plus supports/opposes assertions per cycle
* ``othYY``  itemized committee receipts/disbursements with other committees and organizations ->
  monthly aggregates per filer x counterpart (committee ID, else entity type + state) x transaction type
* ``oppexpYY`` operating expenditures -> monthly aggregates per committee x disbursement category x payee state

Itemized records are aggregated in a scratch SQLite file (bounded memory); memo lines (MEMO_CD = X)
are excluded from sums to avoid double counting. Individual names, payees and addresses are not emitted.
FEC use restriction (52 U.S.C. 30111(a)(4)): no solicitation or commercial use.

Legacy JSONL samples from the OpenFEC committees endpoint use ``sample``.
"""
import json
import re
import sqlite3
import tempfile
from worldmodel.raw_readers import iter_rows
from worldmodel.source_helpers import STATE_FIPS
from worldmodel.util import digest

FIELDS = {
    'cm': ['CMTE_ID', 'CMTE_NM', 'TRES_NM', 'CMTE_ST1', 'CMTE_ST2', 'CMTE_CITY', 'CMTE_ST', 'CMTE_ZIP', 'CMTE_DSGN',
           'CMTE_TP', 'CMTE_PTY_AFFILIATION', 'CMTE_FILING_FREQ', 'ORG_TP', 'CONNECTED_ORG_NM', 'CAND_ID'],
    'ccl': ['CAND_ID', 'CAND_ELECTION_YR', 'FEC_ELECTION_YR', 'CMTE_ID', 'CMTE_TP', 'CMTE_DSGN', 'LINKAGE_ID'],
    'weball': ['CAND_ID', 'CAND_NAME', 'CAND_ICI', 'PTY_CD', 'CAND_PTY_AFFILIATION', 'TTL_RECEIPTS', 'TRANS_FROM_AUTH',
               'TTL_DISB', 'TRANS_TO_AUTH', 'COH_BOP', 'COH_COP', 'CAND_CONTRIB', 'CAND_LOANS', 'OTHER_LOANS',
               'CAND_LOAN_REPAY', 'OTHER_LOAN_REPAY', 'DEBTS_OWED_BY', 'TTL_INDIV_CONTRIB', 'CAND_OFFICE_ST',
               'CAND_OFFICE_DISTRICT', 'SPEC_ELECTION', 'PRIM_ELECTION', 'RUN_ELECTION', 'GEN_ELECTION',
               'GEN_ELECTION_PRECENT', 'OTHER_POL_CMTE_CONTRIB', 'POL_PTY_CONTRIB', 'CVG_END_DT', 'INDIV_REFUNDS',
               'CMTE_REFUNDS'],
    'webk': ['CMTE_ID', 'CMTE_NM', 'CMTE_TP', 'CMTE_DSGN', 'CMTE_FILING_FREQ', 'TTL_RECEIPTS', 'TRANSF_FROM_AFF',
             'INDV_CONTRIB', 'OTHER_POL_CMTE_CONTRIB', 'CAND_CONTRIB', 'CAND_LOANS', 'TTL_LOANS_RECEIVED', 'TTL_DISB',
             'TRANSF_TO_AFF', 'INDV_REFUNDS', 'OTHER_POL_CMTE_REFUNDS', 'CAND_LOAN_REPAY', 'LOAN_REPAY', 'COH_BOP',
             'COH_COP', 'DEBTS_OWED_BY', 'NONFED_TRANS_RECEIVED', 'CONTRIB_TO_OTHER_CMTE', 'IND_EXP', 'PTY_COORD_EXP',
             'NONFED_SHARE_EXP', 'CVG_END_DT'],
    'pas2': ['CMTE_ID', 'AMNDT_IND', 'RPT_TP', 'TRANSACTION_PGI', 'IMAGE_NUM', 'TRANSACTION_TP', 'ENTITY_TP', 'NAME',
             'CITY', 'STATE', 'ZIP_CODE', 'EMPLOYER', 'OCCUPATION', 'TRANSACTION_DT', 'TRANSACTION_AMT', 'OTHER_ID',
             'CAND_ID', 'TRAN_ID', 'FILE_NUM', 'MEMO_CD', 'MEMO_TEXT', 'SUB_ID'],
    'oth': ['CMTE_ID', 'AMNDT_IND', 'RPT_TP', 'TRANSACTION_PGI', 'IMAGE_NUM', 'TRANSACTION_TP', 'ENTITY_TP', 'NAME',
            'CITY', 'STATE', 'ZIP_CODE', 'EMPLOYER', 'OCCUPATION', 'TRANSACTION_DT', 'TRANSACTION_AMT', 'OTHER_ID',
            'TRAN_ID', 'FILE_NUM', 'MEMO_CD', 'MEMO_TEXT', 'SUB_ID'],
    'oppexp': ['CMTE_ID', 'AMNDT_IND', 'RPT_YR', 'RPT_TP', 'IMAGE_NUM', 'LINE_NUM', 'FORM_TP_CD', 'SCHED_TP_CD', 'NAME',
               'CITY', 'STATE', 'ZIP_CODE', 'TRANSACTION_DT', 'TRANSACTION_AMT', 'TRANSACTION_PGI', 'PURPOSE', 'CATEGORY',
               'CATEGORY_DESC', 'MEMO_CD', 'MEMO_TEXT', 'ENTITY_TP', 'SUB_ID', 'FILE_NUM', 'TRAN_ID', 'BACK_REF_TRAN_ID'],
}
KIND_ORDER = ('cm', 'ccl', 'weball', 'webk', 'pas2', 'oth', 'oppexp')
CANDIDATE_METRICS = [('TTL_RECEIPTS', 'total_receipts'), ('TTL_DISB', 'total_disbursements'), ('COH_BOP', 'cash_on_hand_beginning'),
                     ('COH_COP', 'cash_on_hand_end'), ('TTL_INDIV_CONTRIB', 'individual_contributions'),
                     ('OTHER_POL_CMTE_CONTRIB', 'other_committee_contributions'), ('POL_PTY_CONTRIB', 'party_committee_contributions'),
                     ('CAND_CONTRIB', 'candidate_self_contributions'), ('CAND_LOANS', 'candidate_loans'), ('OTHER_LOANS', 'other_loans'),
                     ('CAND_LOAN_REPAY', 'candidate_loan_repayments'), ('OTHER_LOAN_REPAY', 'other_loan_repayments'),
                     ('DEBTS_OWED_BY', 'debts_owed'), ('TRANS_FROM_AUTH', 'transfers_from_authorized_committees'),
                     ('TRANS_TO_AUTH', 'transfers_to_authorized_committees'), ('INDIV_REFUNDS', 'individual_refunds'),
                     ('CMTE_REFUNDS', 'committee_refunds')]
COMMITTEE_METRICS = [('TTL_RECEIPTS', 'total_receipts'), ('TTL_DISB', 'total_disbursements'), ('COH_BOP', 'cash_on_hand_beginning'),
                     ('COH_COP', 'cash_on_hand_end'), ('INDV_CONTRIB', 'individual_contributions'),
                     ('OTHER_POL_CMTE_CONTRIB', 'other_committee_contributions'), ('CAND_CONTRIB', 'candidate_self_contributions'),
                     ('CAND_LOANS', 'candidate_loans'), ('TTL_LOANS_RECEIVED', 'loans_received'), ('TRANSF_FROM_AFF', 'transfers_from_affiliates'),
                     ('TRANSF_TO_AFF', 'transfers_to_affiliates'), ('INDV_REFUNDS', 'individual_refunds'),
                     ('OTHER_POL_CMTE_REFUNDS', 'committee_refunds'), ('LOAN_REPAY', 'loan_repayments'), ('DEBTS_OWED_BY', 'debts_owed'),
                     ('NONFED_TRANS_RECEIVED', 'nonfederal_transfers_received'), ('CONTRIB_TO_OTHER_CMTE', 'contributions_to_other_committees'),
                     ('IND_EXP', 'independent_expenditures'), ('PTY_COORD_EXP', 'party_coordinated_expenditures'),
                     ('NONFED_SHARE_EXP', 'nonfederal_share_expenditures')]
SUPPORT = {'24K': 'supports_candidate', '24E': 'supports_candidate', '24Z': 'supports_candidate', '24C': 'supports_candidate',
           '24F': 'supports_candidate', '24A': 'opposes_candidate', '24N': 'opposes_candidate'}
DESIGNATIONS = {'A': 'authorized_by_candidate', 'B': 'lobbyist_registrant_pac', 'D': 'leadership_pac', 'J': 'joint_fundraiser',
                'P': 'principal_campaign_committee', 'U': 'unauthorized'}
COMMITTEE = re.compile(r'C\d{8}')
CANDIDATE = re.compile(r'[HSP][0-9A-Z]{8}')
READER = {'format': 'psv', 'members': ['*.txt'], 'encoding': 'latin-1', 'quoting': 'none', 'strict': False}


def full_layout(context):
    try:
        coverage = context.raw_coverage(0)
    except Exception:
        return False
    return isinstance(coverage, dict) and coverage.get('layout') == 'shards'


def shard_kind(shard):
    name = shard.get('name') or (shard.get('request') or {}).get('url', '').rsplit('/', 1)[-1]
    match = re.fullmatch(r'(cm|ccl|weball|webk|pas2|oth|oppexp)(\d\d)\.zip', name)
    if not match:
        raise ValueError(f'Unrecognized FEC bulk shard: {name!r}')
    yy = int(match.group(2))
    return match.group(1), (1900 + yy if yy >= 76 else 2000 + yy)


def money(value):
    text = (value or '').strip()
    if not text:
        return None
    return float(text)


def clean(value):
    number = round(value, 2)
    return int(number) if float(number).is_integer() else number


def us_date(value):
    """MM/DD/YYYY -> ISO date or None."""
    match = re.fullmatch(r'(\d\d)/(\d\d)/(\d{4})', (value or '').strip())
    return f'{match.group(3)}-{match.group(1)}-{match.group(2)}' if match else None


def month_of(value, cycle):
    """MMDDYYYY (pas2/oth) or MM/DD/YYYY (oppexp) -> YYYY-MM within a plausible range, else None."""
    text = (value or '').strip().replace('/', '')
    if not re.fullmatch(r'\d{8}', text):
        return None
    month, year = int(text[:2]), int(text[4:])
    if not 1 <= month <= 12 or not cycle - 12 <= year <= cycle + 2:
        return None
    return f'{year:04d}-{month:02d}'


def month_window(month, cycle):
    if month is None:
        return {'valid_from': f'{cycle - 1}-01-01', 'valid_to': f'{cycle + 1}-01-01'}
    year, number = int(month[:4]), int(month[5:])
    following = f'{year + 1:04d}-01' if number == 12 else f'{year:04d}-{number + 1:02d}'
    return {'valid_from': month + '-01', 'valid_to': following + '-01'}


def cycle_window(cycle):
    return {'valid_from': f'{cycle - 1}-01-01', 'valid_to': f'{cycle + 1}-01-01'}


def to_date_window(cycle, coverage_end):
    start = f'{cycle - 1}-01-01'
    if coverage_end and start < coverage_end:
        from datetime import date, timedelta
        end = (date.fromisoformat(coverage_end) + timedelta(days=1)).isoformat()
        return {'valid_from': start, 'valid_to': min(end, f'{cycle + 1}-01-01')}
    return cycle_window(cycle)


class Aggregator:
    """Disk-backed sum/count by key; rows come back ordered by key."""

    def __init__(self, directory):
        self.connection = sqlite3.connect(directory + '/aggregate.sqlite')
        self.connection.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-262144;'
                                      'CREATE TABLE agg (key TEXT PRIMARY KEY, amount REAL, n INTEGER, locator TEXT) WITHOUT ROWID;')
        self.batch = []

    def add(self, key, amount, locator):
        self.batch.append((key, amount, locator))
        if len(self.batch) >= 50000:
            self.flush()

    def flush(self):
        if self.batch:
            self.connection.executemany('INSERT INTO agg VALUES (?,?,1,?) ON CONFLICT(key) DO UPDATE SET amount=amount+excluded.amount, n=n+1',
                                        self.batch)
            self.batch = []

    def drain(self):
        self.flush()
        for row in self.connection.execute('SELECT key, amount, n, locator FROM agg ORDER BY key'):
            yield json.loads(row[0]), row[1], row[2], row[3]
        self.connection.execute('DELETE FROM agg')

    def close(self):
        self.connection.close()


def run(context):
    if not full_layout(context):
        yield from sample(context)
        return
    dataset = context.definition['id']
    for index, ref in enumerate(context.raw_inputs):
        shards = sorted(context.raw_shards(index), key=lambda s: (KIND_ORDER.index(shard_kind(s)[0]), -shard_kind(s)[1]))
        observed_default = context.raw_receipt(index)['retrieved_at']
        committees, candidates_seen = set(), set()
        with tempfile.TemporaryDirectory(dir=context.store.scratch_dir(dataset) if hasattr(context, 'store') else None) as scratch:
            aggregator = Aggregator(scratch)
            try:
                for shard in shards:
                    kind, cycle = shard_kind(shard)
                    reader = {**READER, 'fieldnames': FIELDS[kind]}
                    observed = shard.get('retrieved_at') or observed_default
                    rows = iter_rows([shard], reader)
                    handler = HANDLERS[kind]
                    yield from handler(dataset, ref, observed, cycle, rows, committees, candidates_seen, aggregator)
            finally:
                aggregator.close()


def _base(dataset, ref, observed, locator, **attributes):
    return {'observed_at': observed, 'evidence': [{'input': ref, 'locator': locator}],
            'attributes': {'source_dataset': dataset, **attributes}}


def committee_master(dataset, ref, observed, cycle, rows, committees, candidates, aggregator):
    seen = set()
    for locator, row in rows:
        cid = (row.get('CMTE_ID') or '').strip()
        if not COMMITTEE.fullmatch(cid) or cid in seen:
            continue
        seen.add(cid)
        subject = 'fec:committee:' + cid
        if cid not in committees:  # shards are processed newest cycle first
            committees.add(cid)
            yield {'kind': 'entity', 'id': f'{dataset}:committee:{cid}', 'entity_id': subject, 'entity_type': 'political_committee',
                   'label': (row.get('CMTE_NM') or cid).strip() or cid,
                   **_base(dataset, ref, observed, locator, committee_id=cid, label_cycle=cycle, identity_basis='FEC committee ID')}
        state = (row.get('CMTE_ST') or '').strip()
        value = {'cycle': cycle, 'name': (row.get('CMTE_NM') or '').strip() or None,
                 'designation': DESIGNATIONS.get((row.get('CMTE_DSGN') or '').strip(), (row.get('CMTE_DSGN') or '').strip() or None),
                 'committee_type': (row.get('CMTE_TP') or '').strip() or None,
                 'party': (row.get('CMTE_PTY_AFFILIATION') or '').strip() or None,
                 'filing_frequency': (row.get('CMTE_FILING_FREQ') or '').strip() or None,
                 'interest_group_category': (row.get('ORG_TP') or '').strip() or None,
                 'connected_organization': (row.get('CONNECTED_ORG_NM') or '').strip() or None,
                 'mailing_state': state or None, 'mailing_state_fips': STATE_FIPS.get(state)}
        yield {'kind': 'assertion', 'id': f'{dataset}:registration:{cycle}:{cid}', 'subject': subject,
               'predicate': 'fec_committee_registration', 'value': value, **cycle_window(cycle),
               **_base(dataset, ref, observed, locator, validity_basis='committee master file of the two-year cycle')}


def linkages(dataset, ref, observed, cycle, rows, committees, candidates, aggregator):
    seen = set()
    for locator, row in rows:
        cand, cmte, link = (row.get('CAND_ID') or '').strip(), (row.get('CMTE_ID') or '').strip(), (row.get('LINKAGE_ID') or '').strip()
        if not CANDIDATE.fullmatch(cand) or not COMMITTEE.fullmatch(cmte):
            continue
        key = link or digest([cand, cmte, row.get('CAND_ELECTION_YR')])[:16]
        if key in seen:
            continue
        seen.add(key)
        yield {'kind': 'assertion', 'id': f'{dataset}:linkage:{cycle}:{key}', 'subject': 'fec:committee:' + cmte,
               'predicate': 'authorized_committee_of', 'object': 'fec:candidate:' + cand, **cycle_window(cycle),
               **_base(dataset, ref, observed, locator, linkage_id=link or None, candidate_election_year=row.get('CAND_ELECTION_YR') or None,
                       committee_type=row.get('CMTE_TP') or None,
                       designation=DESIGNATIONS.get((row.get('CMTE_DSGN') or '').strip(), row.get('CMTE_DSGN') or None),
                       validity_basis='FEC election cycle of the linkage file')}


def summaries(kind):
    metrics = CANDIDATE_METRICS if kind == 'weball' else COMMITTEE_METRICS
    id_field = 'CAND_ID' if kind == 'weball' else 'CMTE_ID'
    pattern = CANDIDATE if kind == 'weball' else COMMITTEE
    namespace = 'fec:candidate:' if kind == 'weball' else 'fec:committee:'
    basis = 'fec_weball_candidate_summary' if kind == 'weball' else 'fec_webk_pac_party_summary'

    def handler(dataset, ref, observed, cycle, rows, committees, candidates, aggregator):
        seen = set()
        for locator, row in rows:
            ident = (row.get(id_field) or '').strip()
            if not pattern.fullmatch(ident) or ident in seen:
                continue
            seen.add(ident)
            coverage_end = us_date(row.get('CVG_END_DT'))
            window = to_date_window(cycle, coverage_end)
            dims = {'cycle': cycle, 'report_basis': basis, 'coverage_end_date': coverage_end}
            for column, metric in metrics:
                amount = money(row.get(column))
                record = {'kind': 'observation', 'id': f'{dataset}:{kind}:{cycle}:{ident}:{metric}', 'subject': namespace + ident,
                          'metric': metric, 'unit': 'USD', 'dimensions': dims, **window,
                          **_base(dataset, ref, observed, locator, aggregate=True, period='cycle_to_date')}
                if coverage_end is None:
                    record.update(value=None, missing_reason='no_report_coverage_in_cycle')
                elif amount is None:
                    record.update(value=None, missing_reason='source_missing')
                else:
                    record['value'] = clean(amount)
                yield record
            if kind == 'weball':
                percent = money(row.get('GEN_ELECTION_PRECENT'))
                if percent:  # blank or 0 when the candidate had no general election result in the cycle
                    yield {'kind': 'observation', 'id': f'{dataset}:weball:{cycle}:{ident}:general_election_vote_percent',
                           'subject': namespace + ident, 'metric': 'general_election_vote_percent', 'value': clean(percent),
                           'unit': 'percent', 'dimensions': {'cycle': cycle, 'report_basis': basis}, **cycle_window(cycle),
                           **_base(dataset, ref, observed, locator, general_election_result=(row.get('GEN_ELECTION') or '').strip() or None)}
    return handler


def committee_to_candidate(dataset, ref, observed, cycle, rows, committees, candidates, aggregator):
    for locator, row in rows:
        if (row.get('MEMO_CD') or '').strip().upper() == 'X':
            continue
        cmte, cand = (row.get('CMTE_ID') or '').strip(), (row.get('CAND_ID') or '').strip()
        amount = money(row.get('TRANSACTION_AMT'))
        if not COMMITTEE.fullmatch(cmte) or not CANDIDATE.fullmatch(cand) or amount is None:
            continue
        other = (row.get('OTHER_ID') or '').strip()
        key = [cmte, cand, (row.get('TRANSACTION_TP') or '').strip(), month_of(row.get('TRANSACTION_DT'), cycle),
               other if COMMITTEE.fullmatch(other) else None]
        aggregator.add(json.dumps(key, separators=(',', ':')), amount, locator)
    relations = set()
    for (cmte, cand, tx, month, other), amount, count, locator in aggregator.drain():
        dims = {'candidate': 'fec:candidate:' + cand, 'transaction_type': tx, 'cycle': cycle,
                'month': month, 'recipient_committee': 'fec:committee:' + other if other else None}
        yield {'kind': 'observation', 'id': f'{dataset}:pas2:{cycle}:' + digest([cmte, cand, tx, month, other])[:32],
               'subject': 'fec:committee:' + cmte, 'metric': 'committee_to_candidate_amount', 'value': clean(amount), 'unit': 'USD',
               'dimensions': dims, **month_window(month, cycle),
               **_base(dataset, ref, observed, locator, aggregate=True, transaction_count=count,
                       aggregation='sum of non-memo itemized pas2 rows; locator is the first row of the group',
                       period='calendar_month' if month else 'cycle_date_unknown')}
        predicate = SUPPORT.get(tx)
        if predicate and (cmte, cand, predicate) not in relations:
            relations.add((cmte, cand, predicate))
            yield {'kind': 'assertion', 'id': f'{dataset}:{predicate}:{cycle}:{cmte}:{cand}', 'subject': 'fec:committee:' + cmte,
                   'predicate': predicate, 'object': 'fec:candidate:' + cand, **cycle_window(cycle),
                   **_base(dataset, ref, observed, locator, basis='at least one itemized committee-to-candidate transaction of this direction in the cycle')}


def committee_transactions(dataset, ref, observed, cycle, rows, committees, candidates, aggregator):
    for locator, row in rows:
        if (row.get('MEMO_CD') or '').strip().upper() == 'X':
            continue
        cmte = (row.get('CMTE_ID') or '').strip()
        amount = money(row.get('TRANSACTION_AMT'))
        if not COMMITTEE.fullmatch(cmte) or amount is None:
            continue
        other = (row.get('OTHER_ID') or '').strip()
        counterpart = ('fec:committee:' + other if COMMITTEE.fullmatch(other)
                       else 'fec:candidate:' + other if CANDIDATE.fullmatch(other) else None)
        entity_type = (row.get('ENTITY_TP') or '').strip() or None
        state = (row.get('STATE') or '').strip().upper()[:2] or None
        key = [cmte, (row.get('TRANSACTION_TP') or '').strip(), month_of(row.get('TRANSACTION_DT'), cycle), counterpart,
               None if counterpart else entity_type, None if counterpart else state]
        aggregator.add(json.dumps(key, separators=(',', ':')), amount, locator)
    for (cmte, tx, month, counterpart, entity_type, state), amount, count, locator in aggregator.drain():
        direction = 'receipt' if tx[:1] == '1' else 'disbursement' if tx[:1] == '2' else 'other'
        metric = {'receipt': 'committee_itemized_receipts_amount', 'disbursement': 'committee_itemized_disbursements_amount'}.get(
            direction, 'committee_itemized_other_transactions_amount')
        dims = {'transaction_type': tx, 'cycle': cycle, 'month': month, 'counterpart': counterpart,
                'counterpart_entity_type': entity_type, 'counterpart_state': state}
        yield {'kind': 'observation', 'id': f'{dataset}:oth:{cycle}:' + digest([cmte, tx, month, counterpart, entity_type, state])[:32],
               'subject': 'fec:committee:' + cmte, 'metric': metric, 'value': clean(amount), 'unit': 'USD', 'dimensions': dims,
               **month_window(month, cycle),
               **_base(dataset, ref, observed, locator, aggregate=True, transaction_count=count,
                       aggregation='sum of non-memo itemized oth rows; counterparts without an FEC ID are grouped by entity type and state',
                       period='calendar_month' if month else 'cycle_date_unknown')}


def operating_expenditures(dataset, ref, observed, cycle, rows, committees, candidates, aggregator):
    for locator, row in rows:
        if (row.get('MEMO_CD') or '').strip().upper() == 'X':
            continue
        cmte = (row.get('CMTE_ID') or '').strip()
        amount = money(row.get('TRANSACTION_AMT'))
        if not COMMITTEE.fullmatch(cmte) or amount is None:
            continue
        key = [cmte, (row.get('CATEGORY') or '').strip() or None, month_of(row.get('TRANSACTION_DT'), cycle),
               (row.get('STATE') or '').strip().upper()[:2] or None, (row.get('CATEGORY_DESC') or '').strip() or None]
        aggregator.add(json.dumps(key, separators=(',', ':')), amount, locator)
    for (cmte, category, month, state, description), amount, count, locator in aggregator.drain():
        dims = {'category': category, 'category_description': description, 'payee_state': state, 'cycle': cycle, 'month': month}
        yield {'kind': 'observation', 'id': f'{dataset}:oppexp:{cycle}:' + digest([cmte, category, month, state, description])[:32],
               'subject': 'fec:committee:' + cmte, 'metric': 'operating_expenditures_amount', 'value': clean(amount), 'unit': 'USD',
               'dimensions': dims, **month_window(month, cycle),
               **_base(dataset, ref, observed, locator, aggregate=True, transaction_count=count,
                       aggregation='sum of non-memo operating expenditure rows by category and payee state; payee names are not retained',
                       period='calendar_month' if month else 'cycle_date_unknown')}


HANDLERS = {'cm': committee_master, 'ccl': linkages, 'weball': summaries('weball'), 'webk': summaries('webk'),
            'pas2': committee_to_candidate, 'oth': committee_transactions, 'oppexp': operating_expenditures}


def sample(context):
    """Legacy OpenFEC committees API JSONL sample."""
    dataset = 'fec'
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
            key = entity('fec:committee:' + row['committee_id'], 'political_committee', row['name'])
            if row.get('state') in STATE_FIPS:
                rel(key, 'within', geo(row['state']))
            if row.get('affiliated_committee_name'):
                name = row['affiliated_committee_name']
                target = entity('reference:fec:affiliate:' + digest(name), 'organization', name, synthetic=True, identity_basis='unresolved source name')
                rel(key, 'affiliated_with', target)
            for candidate in row.get('candidate_ids') or []:
                target = entity('fec:candidate:' + candidate, 'person', 'FEC candidate ' + candidate, synthetic=True)
                rel(key, 'supports_candidate', target)
            yield from out
