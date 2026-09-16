"""USAspending prime contract transactions -> transaction obligations, award entities, recipients and classifications.

Full sharded acquisition: award data archive ZIP(s) of prime *transaction* rows (e.g. FY2026 DoD 097).
Semantics are kept distinct:
* transaction (``contract_transaction_unique_key``): ``federal_action_obligation`` observation dated at
  ``action_date`` on the award subject; this is the flow.
* award (``usaspending:award:{contract_award_unique_key}``): entity emitted once, with award-level cumulative
  values (``award_total_obligated``, ``award_current_total_value``, ``award_potential_total_value``) as of the
  archive snapshot, never summed with transactions.
* recipients ``uei:{UEI}`` (parent ``uei:{parent UEI}``), agencies ``usgov:agency:{toptier code}`` /
  ``usgov:agency:{subtier code}``, NAICS ``naics2022:{code}``, PSC ``psc:{code}``, place of performance
  ``geo:US:county:{fips}`` / ``geo:US:state:{fips}`` / ``iso3:{country}``.
First-occurrence dedupe of awards/recipients uses a scratch SQLite table (bounded memory).
Highly compensated officer names and contact details are never emitted.

Legacy JSONL samples from the spending_by_award API use ``sample``.
"""
from datetime import date, timedelta
import json
import re
import sqlite3
import tempfile
from worldmodel.source_helpers import STATE_FIPS
from worldmodel.util import digest

READER = {'format': 'csv', 'members': ['*.csv'], 'encoding': 'utf-8-sig'}
AWARD_VALUES = (('total_dollars_obligated', 'award_total_obligated'), ('current_total_value_of_award', 'award_current_total_value'),
                ('potential_total_value_of_award', 'award_potential_total_value'))
AWARD_FIELDS = ('award_id_piid', 'parent_award_id_piid', 'award_type_code', 'award_type', 'idv_type_code', 'type_of_contract_pricing_code',
                'extent_competed_code', 'type_of_set_aside_code', 'number_of_offers_received', 'naics_code', 'product_or_service_code',
                'dod_claimant_program_code', 'dod_acquisition_program_code', 'contracting_officers_determination_of_business_size_code',
                'period_of_performance_start_date', 'period_of_performance_current_end_date', 'period_of_performance_potential_end_date',
                'awarding_office_code', 'funding_office_code', 'national_interest_action_code')


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
    state = (row.get('primary_place_of_performance_state_code') or '').strip().upper()
    county = (row.get('prime_award_transaction_place_of_performance_county_fips_code') or '').strip()
    if country in ('USA', 'US', ''):
        fips = STATE_FIPS.get(state)
        if fips and re.fullmatch(r'\d{5}', county) and county[:2] == fips:
            return f'geo:US:county:{county}'
        if fips:
            return f'geo:US:state:{fips}'
        return 'geo:US' if country else None
    return 'iso3:' + country if re.fullmatch(r'[A-Z]{3}', country) else None


def run(context):
    if not full_layout(context):
        yield from sample(context)
        return
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
        transaction = (row.get('contract_transaction_unique_key') or '').strip()
        award_key = (row.get('contract_award_unique_key') or '').strip()
        if not transaction or not award_key:
            raise ValueError(f'{locator}: contract row without transaction/award key')
        if not seen.first('t:' + transaction):
            continue
        award = 'usaspending:award:' + award_key
        evidence = [{'input': ref, 'locator': locator}]

        def rec(record, **attributes):
            record.update(observed_at=observed, evidence=evidence, attributes={'source_dataset': dataset, **attributes})
            return record

        action_date = (row.get('action_date') or '').strip()[:10]
        obligation = money(row.get('federal_action_obligation'))
        if action_date:
            window = {'valid_from': action_date, 'valid_to': (date.fromisoformat(action_date) + timedelta(days=1)).isoformat()}
        else:
            window = {}
        record = {'kind': 'observation', 'id': f'{dataset}:txn:{transaction}', 'subject': award, 'metric': 'federal_action_obligation',
                  'value': obligation, 'unit': 'USD', **window,
                  'dimensions': {'modification_number': row.get('modification_number') or None,
                                 'action_type_code': row.get('action_type_code') or None,
                                 'fiscal_year': int(row['action_date_fiscal_year']) if (row.get('action_date_fiscal_year') or '').isdigit() else None,
                                 'awarding_sub_agency': row.get('awarding_sub_agency_code') or None,
                                 'funding_sub_agency': row.get('funding_sub_agency_code') or None}}
        if obligation is None:
            record['missing_reason'] = 'source_missing'
        yield rec(record, semantics='transaction-level obligation (flow on action_date); do not add award-level totals',
                  number_of_actions=money(row.get('number_of_actions')), transaction_description=(row.get('transaction_description') or '')[:300] or None)
        if not seen.first('a:' + award_key):
            continue
        piid = row.get('award_id_piid') or award_key
        attrs = {field: (row.get(field) or None) for field in AWARD_FIELDS}
        yield rec({'kind': 'entity', 'id': f'{dataset}:award:{award_key}', 'entity_id': award, 'entity_type': 'award', 'label': piid},
                  **attrs, award_or_idv=row.get('award_or_idv_flag') or None,
                  description=(row.get('prime_award_base_transaction_description') or '')[:500] or None,
                  permalink=row.get('usaspending_permalink') or None,
                  semantics='prime contract award; attributes from its first transaction row in this archive snapshot')
        for column, metric in AWARD_VALUES:
            value = money(row.get(column))
            if value is not None:
                yield rec({'kind': 'observation', 'id': f'{dataset}:{metric}:{award_key}', 'subject': award, 'metric': metric, 'value': value,
                           'unit': 'USD', 'dimensions': {'snapshot_date': snapshot}, **snapshot_window},
                          semantics='award-level cumulative value as of the archive snapshot; not a flow')
        for level in ('awarding_agency', 'awarding_sub_agency', 'funding_agency', 'funding_sub_agency'):
            code = (row.get(level + '_code') or '').strip()
            if not code:
                continue
            agency = 'usgov:agency:' + code
            if seen.first('g:' + code):
                yield rec({'kind': 'entity', 'id': f'{dataset}:agency:{code}', 'entity_id': agency, 'entity_type': 'government_agency',
                           'label': row.get(level + '_name') or agency}, agency_code=code,
                          code_system='CGAC toptier code' if level.endswith('_agency') and '_sub_' not in level else 'FPDS subtier code')
                parent = (row.get(level.replace('_sub_agency', '_agency') + '_code') or '').strip() if '_sub_' in level else ''
                if parent and parent != code:
                    yield rec({'kind': 'assertion', 'id': f'{dataset}:agency_parent:{code}', 'subject': agency, 'predicate': 'part_of',
                               'object': 'usgov:agency:' + parent})
        sub = (row.get('awarding_sub_agency_code') or row.get('awarding_agency_code') or '').strip()
        if sub:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:awarded_by:{award_key}', 'subject': award, 'predicate': 'awarded_by',
                       'object': 'usgov:agency:' + sub}, awarding_office=row.get('awarding_office_code') or None)
        funder = (row.get('funding_sub_agency_code') or '').strip()
        if funder and funder != sub:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:funded_by:{award_key}', 'subject': award, 'predicate': 'funded_by',
                       'object': 'usgov:agency:' + funder})
        uei = (row.get('recipient_uei') or '').strip().upper()
        if uei:
            recipient = 'uei:' + uei
            if seen.first('r:' + uei):
                yield rec({'kind': 'entity', 'id': f'{dataset}:recipient:{uei}', 'entity_id': recipient, 'entity_type': 'organization',
                           'label': row.get('recipient_name') or recipient}, uei=uei, cage_code=row.get('cage_code') or None,
                          country=row.get('recipient_country_code') or None, state=row.get('recipient_state_code') or None,
                          business_size=row.get('contracting_officers_determination_of_business_size') or None,
                          identity_basis='SAM.gov Unique Entity ID as reported on the transaction')
                parent_uei = (row.get('recipient_parent_uei') or '').strip().upper()
                if parent_uei and parent_uei != uei:
                    yield rec({'kind': 'assertion', 'id': f'{dataset}:parent:{uei}', 'subject': recipient, 'predicate': 'subsidiary_of',
                               'object': 'uei:' + parent_uei}, parent_name=row.get('recipient_parent_name') or None,
                              basis='recipient_parent_uei on the first transaction row seen; SAM immediate/ultimate parent as reported')
        else:
            name = (row.get('recipient_name') or 'unknown recipient').strip()
            recipient = 'reference:usaspending:recipient:' + digest(name)[:32]
            if seen.first('n:' + recipient):
                yield rec({'kind': 'entity', 'id': f'{dataset}:recipient_name:{digest(name)[:32]}', 'entity_id': recipient,
                           'entity_type': 'organization', 'label': name}, identity_basis='unresolved recipient name; no UEI reported', synthetic_reference=True)
        yield rec({'kind': 'assertion', 'id': f'{dataset}:awarded_to:{award_key}', 'subject': award, 'predicate': 'awarded_to', 'object': recipient})
        naics = (row.get('naics_code') or '').strip()
        if re.fullmatch(r'\d{2,6}', naics):
            yield rec({'kind': 'assertion', 'id': f'{dataset}:naics:{award_key}', 'subject': award, 'predicate': 'award_naics',
                       'object': 'naics2022:' + naics}, naics_description=row.get('naics_description') or None)
        psc = (row.get('product_or_service_code') or '').strip().upper()
        if psc:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:psc:{award_key}', 'subject': award, 'predicate': 'award_product_service_code',
                       'object': 'psc:' + psc}, psc_description=row.get('product_or_service_code_description') or None)
        location = place(row)
        if location:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:place:{award_key}', 'subject': award, 'predicate': 'place_of_performance',
                       'object': location}, zip4=row.get('primary_place_of_performance_zip_4') or None,
                      congressional_district=row.get('prime_award_transaction_place_of_performance_cd_current') or None)
        parent_piid = (row.get('parent_award_id_piid') or '').strip()
        parent_agency = (row.get('parent_award_agency_id') or '').strip()
        if parent_piid and parent_agency:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:idv:{award_key}', 'subject': award, 'predicate': 'order_under_idv',
                       'object': f'usaspending:award:CONT_IDV_{parent_piid}_{parent_agency}'},
                      basis='USAspending generated IDV award key convention CONT_IDV_<PIID>_<agency id>')


def sample(context):
    """Legacy spending_by_award API JSONL sample (award summaries)."""
    dataset = 'usaspending'
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
            key = entity('usaspending:award:' + row['generated_internal_id'], 'award', row['Award ID'])
            agency = entity('usaspending:agency:' + str(row['awarding_agency_id']), 'government_agency', row['Awarding Agency'])
            name = row['Recipient Name']
            recipient = entity('reference:usaspending:recipient:' + digest(name), 'organization', name, synthetic=True, identity_basis='unresolved source recipient name; not matched to LEI')
            rel(key, 'awarded_by', agency)
            rel(key, 'awarded_to', recipient)
            rel(agency, 'within', geo())
            obs(key, 'award_amount', row['Award Amount'], 'USD', acquired, None, validity_basis='award summary at acquisition; query window is not award validity')
            yield from out
