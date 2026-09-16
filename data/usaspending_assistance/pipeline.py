"""USAspending prime financial assistance transactions -> obligations, loan values, awards, recipients and listings.

Full sharded acquisition: award data archive ZIPs of prime *assistance transaction* rows (grants, loans, direct
payments, insurance; FY2026 and FY2025, all agencies). Semantics are kept distinct:

* transaction (``assistance_transaction_unique_key``): ``federal_action_obligation``, and for loans
  ``loan_face_value`` / ``loan_subsidy_cost`` observations dated at ``action_date`` on the award subject (flows).
* award (``usaspending:award:{assistance_award_unique_key}``): entity emitted once with award-level cumulative
  ``award_total_obligated``, ``award_total_loan_face_value``, ``award_total_loan_subsidy_cost`` as of the archive
  snapshot date (stocks; never summed with transactions).
* recipients ``uei:{UEI}`` (``subsidiary_of`` parent UEI). USAspending redacts individual recipients (record type 3)
  and publishes county/state aggregate records (record type 1); those awards get no recipient link and carry
  ``recipient_basis`` instead of a synthetic identity.
* assistance listing ``cfda:{number}`` (``award_assistance_listing``), assistance type, agencies ``usgov:agency:{code}``,
  place of performance ``geo:US:county:{fips5}`` / ``geo:US:state:{fips}`` / ``iso3:{country}``.
First-occurrence dedupe of awards/recipients/agencies uses a scratch SQLite table (bounded memory).
Recipient addresses and highly compensated officer names are never emitted.
"""
from datetime import date, timedelta
import re
import sqlite3
import tempfile
from worldmodel.util import digest

READER = {'format': 'csv', 'members': ['*.csv'], 'encoding': 'utf-8-sig'}
AWARD_VALUES = (('total_obligated_amount', 'award_total_obligated'), ('total_face_value_of_loan', 'award_total_loan_face_value'),
                ('total_loan_subsidy_cost', 'award_total_loan_subsidy_cost'))
AWARD_FIELDS = ('award_id_fain', 'award_id_uri', 'assistance_type_code', 'assistance_type_description', 'cfda_number', 'cfda_title',
                'funding_opportunity_number', 'business_types_code', 'business_types_description', 'record_type_code',
                'record_type_description', 'period_of_performance_start_date', 'period_of_performance_current_end_date',
                'awarding_office_code', 'funding_office_code', 'primary_place_of_performance_scope')
REDACTED_RECORD_TYPES = {'1': 'county/state aggregate record (no recipient)', '3': 'individual recipient redacted for privacy'}


def full_layout(context):
    try:
        coverage = context.raw_coverage(0)
    except Exception:
        return False
    return isinstance(coverage, dict) and coverage.get('layout') == 'shards'


def money(value):
    value = (value or '').strip()
    if not value:
        return None
    result = round(float(value), 2)
    return int(result) if result.is_integer() else result


def snapshot_date(shard):
    name = shard.get('name') or (shard.get('request') or {}).get('url', '')
    match = re.search(r'_(\d{4})(\d{2})(\d{2})\.zip', name)
    return f'{match.group(1)}-{match.group(2)}-{match.group(3)}' if match else None


class Seen:
    def __init__(self, directory):
        self.connection = sqlite3.connect(directory + '/seen.sqlite')
        self.connection.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-262144;'
                                      'CREATE TABLE seen (key TEXT PRIMARY KEY) WITHOUT ROWID;')
        self.pending = 0

    def first(self, key):
        cursor = self.connection.execute('INSERT OR IGNORE INTO seen VALUES (?)', (key,))
        self.pending += 1
        if self.pending >= 200000:
            self.connection.commit()
            self.pending = 0
        return cursor.rowcount == 1

    def close(self):
        self.connection.close()


def place(row):
    country = (row.get('primary_place_of_performance_country_code') or '').strip().upper()
    state_fips = (row.get('prime_award_transaction_place_of_performance_state_fips_code') or '').strip()
    county = (row.get('prime_award_transaction_place_of_performance_county_fips_code') or '').strip()
    if country in ('USA', 'US', ''):
        if re.fullmatch(r'\d{5}', county) and (not state_fips or county[:2] == state_fips.zfill(2)):
            return f'geo:US:county:{county}'
        if re.fullmatch(r'\d{1,2}', state_fips):
            return f'geo:US:state:{state_fips.zfill(2)}'
        return 'geo:US' if country else None
    return 'iso3:' + country if re.fullmatch(r'[A-Z]{3}', country) else None


def run(context):
    if not full_layout(context):
        raise ValueError('usaspending_assistance requires a full sharded acquisition (no sample adapter)')
    dataset = context.definition['id']
    scratch_root = str(context.store.scratch_dir(dataset)) if hasattr(context, 'store') else None
    with tempfile.TemporaryDirectory(dir=scratch_root) as scratch:
        seen = Seen(scratch)
        try:
            for index, ref in enumerate(context.raw_inputs):
                observed_default = context.raw_receipt(index)['retrieved_at']
                for shard in context.raw_shards(index):
                    yield from shard_records(dataset, ref, shard, shard.get('retrieved_at') or observed_default, seen)
        finally:
            seen.close()


def shard_records(dataset, ref, shard, observed, seen):
    from worldmodel.raw_readers import iter_rows
    snapshot = snapshot_date(shard) or observed[:10]
    snapshot_window = {'valid_from': snapshot, 'valid_to': (date.fromisoformat(snapshot) + timedelta(days=1)).isoformat()}
    for locator, row in iter_rows([shard], READER):
        transaction = (row.get('assistance_transaction_unique_key') or '').strip()
        award_key = (row.get('assistance_award_unique_key') or '').strip()
        if not transaction or not award_key:
            raise ValueError(f'{locator}: assistance row without transaction/award key')
        if not seen.first('t:' + transaction):
            continue
        award = 'usaspending:award:' + award_key
        evidence = [{'input': ref, 'locator': locator}]

        def rec(record, **attributes):
            record.update(observed_at=observed, evidence=evidence, attributes={'source_dataset': dataset, **attributes})
            return record

        action_date = (row.get('action_date') or '').strip()[:10]
        window = ({'valid_from': action_date, 'valid_to': (date.fromisoformat(action_date) + timedelta(days=1)).isoformat()}
                  if action_date else {})
        dims = {'modification_number': row.get('modification_number') or None, 'action_type_code': row.get('action_type_code') or None,
                'assistance_type_code': row.get('assistance_type_code') or None,
                'fiscal_year': int(row['action_date_fiscal_year']) if (row.get('action_date_fiscal_year') or '').isdigit() else None,
                'record_type_code': row.get('record_type_code') or None}
        for column, metric in (('federal_action_obligation', 'federal_action_obligation'), ('face_value_of_loan', 'loan_face_value'),
                               ('original_loan_subsidy_cost', 'loan_subsidy_cost')):
            value = money(row.get(column))
            if metric != 'federal_action_obligation' and not value:
                continue  # loan columns are 0/blank for non-loan assistance
            record = {'kind': 'observation', 'id': f'{dataset}:{metric}:{transaction}', 'subject': award, 'metric': metric,
                      'value': value, 'unit': 'USD', 'dimensions': dims, **window}
            if value is None:
                record['missing_reason'] = 'source_missing'
            yield rec(record, semantics='transaction-level flow on action_date; do not add award-level totals',
                      correction_delete_indicator=row.get('correction_delete_indicator_code') or None)
        if not seen.first('a:' + award_key):
            continue
        attrs = {field: (row.get(field) or None) for field in AWARD_FIELDS}
        label = row.get('award_id_fain') or row.get('award_id_uri') or award_key
        yield rec({'kind': 'entity', 'id': f'{dataset}:award:{award_key}', 'entity_id': award, 'entity_type': 'award', 'label': label},
                  **attrs, description=(row.get('prime_award_base_transaction_description') or '')[:500] or None,
                  permalink=row.get('usaspending_permalink') or None,
                  semantics='prime financial assistance award; attributes from its first transaction row in this archive snapshot')
        for column, metric in AWARD_VALUES:
            value = money(row.get(column))
            if value is None or (metric != 'award_total_obligated' and not value):
                continue
            yield rec({'kind': 'observation', 'id': f'{dataset}:{metric}:{award_key}', 'subject': award, 'metric': metric, 'value': value,
                       'unit': 'USD', 'dimensions': {'snapshot_date': snapshot}, **snapshot_window},
                      semantics='award-level cumulative value as of the archive snapshot; not a flow')
        for level in ('awarding_agency', 'awarding_sub_agency', 'funding_agency', 'funding_sub_agency'):
            code = (row.get(level + '_code') or '').strip()
            if code and seen.first('g:' + code):
                yield rec({'kind': 'entity', 'id': f'{dataset}:agency:{code}', 'entity_id': 'usgov:agency:' + code,
                           'entity_type': 'government_agency', 'label': row.get(level + '_name') or code}, agency_code=code)
                parent = (row.get(level.replace('_sub_agency', '_agency') + '_code') or '').strip() if '_sub_' in level else ''
                if parent and parent != code:
                    yield rec({'kind': 'assertion', 'id': f'{dataset}:agency_parent:{code}', 'subject': 'usgov:agency:' + code,
                               'predicate': 'part_of', 'object': 'usgov:agency:' + parent})
        sub = (row.get('awarding_sub_agency_code') or row.get('awarding_agency_code') or '').strip()
        if sub:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:awarded_by:{award_key}', 'subject': award, 'predicate': 'awarded_by',
                       'object': 'usgov:agency:' + sub}, awarding_office=row.get('awarding_office_code') or None)
        funder = (row.get('funding_sub_agency_code') or '').strip()
        if funder and funder != sub:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:funded_by:{award_key}', 'subject': award, 'predicate': 'funded_by',
                       'object': 'usgov:agency:' + funder})
        uei = (row.get('recipient_uei') or '').strip().upper()
        record_type = (row.get('record_type_code') or '').strip()
        if uei:
            recipient = 'uei:' + uei
            if seen.first('r:' + uei):
                yield rec({'kind': 'entity', 'id': f'{dataset}:recipient:{uei}', 'entity_id': recipient, 'entity_type': 'organization',
                           'label': row.get('recipient_name') or recipient}, uei=uei,
                          country=row.get('recipient_country_code') or None, state=row.get('recipient_state_code') or None,
                          business_types=row.get('business_types_description') or None,
                          identity_basis='SAM.gov Unique Entity ID as reported on the transaction')
                parent_uei = (row.get('recipient_parent_uei') or '').strip().upper()
                if parent_uei and parent_uei != uei:
                    yield rec({'kind': 'assertion', 'id': f'{dataset}:parent:{uei}', 'subject': recipient, 'predicate': 'subsidiary_of',
                               'object': 'uei:' + parent_uei}, parent_name=row.get('recipient_parent_name') or None)
            yield rec({'kind': 'assertion', 'id': f'{dataset}:awarded_to:{award_key}', 'subject': award, 'predicate': 'awarded_to',
                       'object': recipient})
        else:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:recipient_basis:{award_key}', 'subject': award, 'predicate': 'recipient_basis',
                       'value': REDACTED_RECORD_TYPES.get(record_type, 'no UEI reported')},
                      record_type_code=record_type or None, recipient_state=row.get('recipient_state_code') or None)
        listing = (row.get('cfda_number') or '').strip()
        if re.fullmatch(r'\d{2}\.\d{3}[A-Z]?', listing):
            yield rec({'kind': 'assertion', 'id': f'{dataset}:listing:{award_key}', 'subject': award, 'predicate': 'award_assistance_listing',
                       'object': 'cfda:' + listing}, cfda_title=row.get('cfda_title') or None)
        location = place(row)
        if location:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:place:{award_key}', 'subject': award, 'predicate': 'place_of_performance',
                       'object': location}, scope=row.get('primary_place_of_performance_scope') or None,
                      congressional_district=row.get('prime_award_transaction_place_of_performance_cd_current') or None)
        if not location and (row.get('primary_place_of_performance_scope') or '').strip():
            yield rec({'kind': 'assertion', 'id': f'{dataset}:place_scope:{award_key}', 'subject': award,
                       'predicate': 'place_of_performance_scope', 'value': row['primary_place_of_performance_scope']},
                      basis='no county/state/country code published for this award', digest_key=digest(award_key)[:12])
