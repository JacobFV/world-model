"""FEC candidate master files (cnYY.zip) -> candidate entities, per-cycle candidacies and principal committees.

Two raw layouts are supported:
* full sharded acquisition (one ZIP per two-year cycle, pipe-delimited, headerless);
* legacy JSONL samples from the OpenFEC API (one candidate object per line).
Candidate mailing addresses in the bulk file are never emitted.
"""
import json
import math
import re
from worldmodel.raw_readers import iter_rows
from worldmodel.util import digest

FIELDS = ['CAND_ID', 'CAND_NAME', 'CAND_PTY_AFFILIATION', 'CAND_ELECTION_YR', 'CAND_OFFICE_ST', 'CAND_OFFICE',
          'CAND_OFFICE_DISTRICT', 'CAND_ICI', 'CAND_STATUS', 'CAND_PCC', 'CAND_ST1', 'CAND_ST2', 'CAND_CITY',
          'CAND_ST', 'CAND_ZIP']
READER = {'format': 'psv', 'members': ['*.txt'], 'encoding': 'latin-1', 'quoting': 'none', 'strict': False,
          'fieldnames': FIELDS}
OFFICES = {'H': 'US House', 'S': 'US Senate', 'P': 'US President'}
ICI = {'I': 'incumbent', 'C': 'challenger', 'O': 'open_seat'}
STATUS = {'C': 'statutory_candidate', 'F': 'statutory_candidate_future_election', 'N': 'not_yet_statutory_candidate',
          'P': 'statutory_candidate_prior_cycle'}
CANDIDATE = re.compile(r'[HSP][0-9A-Z]{8}')
COMMITTEE = re.compile(r'C[0-9]{8}')


def full_layout(context):
    try:
        coverage = context.raw_coverage(0)
    except Exception:
        return False
    return isinstance(coverage, dict) and coverage.get('layout') == 'shards'


def shard_cycle(shard):
    name = shard.get('name') or (shard.get('request') or {}).get('url', '')
    match = re.search(r'cn(\d\d)\.zip', name)
    if not match:
        raise ValueError(f'Unrecognized FEC candidate shard: {name!r}')
    yy = int(match.group(1))
    return 1900 + yy if yy >= 76 else 2000 + yy


def run(context):
    if not full_layout(context):
        yield from sample(context)
        return
    dataset = context.definition['id']
    entities = set()
    for index, ref in enumerate(context.raw_inputs):
        # Newest cycle first so each candidate entity carries its most recent published name.
        for shard in sorted(context.raw_shards(index), key=shard_cycle, reverse=True):
            cycle = shard_cycle(shard)
            observed = shard.get('retrieved_at') or context.raw_receipt(index)['retrieved_at']
            window = {'valid_from': f'{cycle - 1}-01-01', 'valid_to': f'{cycle + 1}-01-01'}
            in_shard = set()
            for locator, row in iter_rows([shard], READER):
                cid = (row.get('CAND_ID') or '').strip()
                if not cid:
                    continue
                if not CANDIDATE.fullmatch(cid):
                    raise ValueError(f'{locator}: invalid FEC candidate ID {cid!r}')
                if cid in in_shard:
                    continue
                in_shard.add(cid)
                evidence = [{'input': ref, 'locator': locator}]
                subject = 'fec:candidate:' + cid
                base = {'observed_at': observed, 'evidence': evidence}
                if cid not in entities:
                    entities.add(cid)
                    yield {'kind': 'entity', 'id': f'{dataset}:entity:{cid}', 'entity_id': subject, 'entity_type': 'person',
                           'label': (row.get('CAND_NAME') or cid).strip() or cid, **base,
                           'attributes': {'source_dataset': dataset, 'candidate_id': cid, 'office_code': cid[0],
                                          'label_cycle': cycle,
                                          'identity_basis': 'FEC candidate identifier; one person can hold separate IDs per office sought'}}
                office = (row.get('CAND_OFFICE') or '').strip()
                value = {'cycle': cycle, 'election_year': _int(row.get('CAND_ELECTION_YR')),
                         'office': OFFICES.get(office, office or None), 'office_state': (row.get('CAND_OFFICE_ST') or '').strip() or None,
                         'district': (row.get('CAND_OFFICE_DISTRICT') or '').strip() or None,
                         'party': (row.get('CAND_PTY_AFFILIATION') or '').strip() or None,
                         'incumbent_challenger': ICI.get((row.get('CAND_ICI') or '').strip()),
                         'candidate_status': STATUS.get((row.get('CAND_STATUS') or '').strip(), (row.get('CAND_STATUS') or '').strip() or None)}
                yield {'kind': 'assertion', 'id': f'{dataset}:candidacy:{cycle}:{cid}', 'subject': subject,
                       'predicate': 'fec_candidacy', 'value': value, **window, **base,
                       'attributes': {'source_dataset': dataset, 'validity_basis': 'FEC two-year election cycle of the candidate master file'}}
                pcc = (row.get('CAND_PCC') or '').strip()
                if COMMITTEE.fullmatch(pcc):
                    yield {'kind': 'assertion', 'id': f'{dataset}:pcc:{cycle}:{cid}', 'subject': subject,
                           'predicate': 'principal_campaign_committee', 'object': 'fec:committee:' + pcc, **window, **base,
                           'attributes': {'source_dataset': dataset, 'validity_basis': 'FEC two-year election cycle of the candidate master file'}}


def _int(value):
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def sample(context):
    """Legacy OpenFEC API JSONL sample: one enrichment entity per published candidate."""
    dataset = 'fec_candidates'
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
            entity('fec:candidate:' + row['candidate_id'], 'person', row['name'], candidate_id=row['candidate_id'], candidate_office=row.get('office'), candidate_state=row.get('state'), candidate_district=row.get('district'), candidate_party=row.get('party'), election_years=row.get('election_years', []), identity_basis='published FEC candidate identifier', temporal_scope='historical candidacy record; no current office or location inferred')
            yield from out
