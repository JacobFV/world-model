"""Deterministic identifier linking from published crosswalks.

Each mapping specification states what a published row means: identity (``same_as``)
or a typed relationship (issuer listing, committee authorization, ...), its expected
cardinality at one point in time, and whether dates are published. Rows violating the
declared cardinality are reported as conflicts and are not linked. Nothing is merged:
the output is explicit, evidence-carrying assertion records.
"""
from copy import deepcopy
from datetime import date

from ..util import digest

# Namespaces whose values identify exactly one real-world entity at a time.
UNIQUE_NAMESPACES = {'lei', 'sec_cik', 'bioguide', 'icpsr', 'fec_candidate', 'fec_committee', 'uei', 'duns', 'fdic_cert',
                     'rssd', 'figi', 'isin', 'ofac_sdn', 'opensanctions', 'eia_plant', 'imo', 'mmsi', 'wikidata', 'mic',
                     # ORCID iDs and ROR IDs are issued one per researcher / organisation; imo_company is IMO's
                     # company and registered-owner number series (see resolution.bridges)
                     'orcid', 'ror', 'imo_company'}

MAPPING_SPECS = {
    'gleif_sec_cik': {'left': 'lei', 'right': 'sec_cik', 'relation': 'same_as', 'cardinality': '1:1',
                      'source': 'GLEIF golden copy registration-authority entity IDs where the authority is SEC EDGAR '
                                '(LEI-CDF 3.1 Entity.RegistrationAuthority, RegistrationAuthorityID RA000665)',
                      'note': 'a CIK may belong to a filer without an LEI; absence is not evidence of non-identity. '
                              'GLEIF records the register of incorporation, so a Delaware-incorporated SEC filer '
                              'carries its Delaware file number here and no CIK'},
    'gleif_companies_house': {'left': 'lei', 'right': 'gb_company_number', 'relation': 'same_as', 'cardinality': '1:1',
                              'source': 'GLEIF golden copy registration-authority entity IDs where the authority is '
                                        'Companies House (LEI-CDF 3.1 RegistrationAuthorityID RA000585)',
                              'note': 'company numbers are eight characters (eight digits, or a two-letter register '
                                      'prefix and six digits); GLEIF publishes them padded and unpadded'},
    'sanctions_register_number': {'left': 'sanctions_party', 'right': 'register_number', 'relation': 'same_as',
                                  'cardinality': '1:1',
                                  'source': 'OFAC SDN_ADVANCED / Consolidated Screening List identity documents whose '
                                            'scheme and issuing country name a register (RU INN, RU OGRN, UK company '
                                            'number), check digits recomputed',
                                  'note': 'held per publishing dataset: OFAC and the CSL copy of an OFAC entry both '
                                          'print the number, which is agreement, not a cardinality break'},
    'opensanctions_uei': {'left': 'opensanctions', 'right': 'uei', 'relation': 'same_as', 'cardinality': '1:1',
                          'source': 'OpenSanctions (FollowTheMoney) uniqueEntityId values: the US SAM Unique Entity ID'},
    'opensanctions_wikidata': {'left': 'opensanctions', 'right': 'wikidata', 'relation': 'same_as',
                               'cardinality': '1:1',
                               'source': 'OpenSanctions canonical entity IDs that are Wikidata QIDs'},
    'isin_cusip': {'left': 'isin', 'right': 'cusip', 'relation': 'same_as', 'cardinality': '1:1',
                   'source': 'ISO 6166: the nine-character NSIN inside a US or CA ISIN is the CUSIP',
                   'note': 'security identity only, never issuer identity; the ISIN check digit is recomputed before '
                           'the CUSIP is read, and a non-US/CA NSIN is a SEDOL or WKN and is refused'},
    'sec_cik_ticker': {'left': 'sec_cik', 'right': 'ticker', 'relation': 'listed_as', 'cardinality': '1:n', 'right_scope': 'mic',
                       'dated': True, 'source': 'SEC company_tickers_exchange.json snapshots (undated; snapshot date = validity evidence)',
                       'note': 'tickers are reused; require MIC scope and snapshot dates'},
    'fec_candidate_committee': {'left': 'fec_candidate', 'right': 'fec_committee', 'relation': 'authorized_committee',
                                'cardinality': '1:n', 'dated': True, 'source': 'FEC ccl (candidate-committee linkage) by election cycle'},
    'bioguide_fec_candidate': {'left': 'bioguide', 'right': 'fec_candidate', 'relation': 'same_as', 'cardinality': '1:n',
                               'source': 'unitedstates/congress-legislators id.fec lists', 'note': 'one person may hold House and Senate candidate IDs'},
    'bioguide_icpsr': {'left': 'bioguide', 'right': 'icpsr', 'relation': 'same_as', 'cardinality': '1:n',
                       'source': 'Voteview HSall_members.csv / congress-legislators id.icpsr',
                       'note': 'Voteview historically assigned new ICPSR IDs on party switches'},
    'uei_lei': {'left': 'uei', 'right': 'lei', 'relation': 'same_as', 'cardinality': '1:1',
                'source': 'published SAM/USAspending recipient records that report an LEI'},
    'ofac_opensanctions': {'left': 'ofac_sdn', 'right': 'opensanctions', 'relation': 'same_as', 'cardinality': '1:1',
                           'source': 'OpenSanctions entity referents (ofac-<uid>)'},
    'fdic_cert_rssd': {'left': 'fdic_cert', 'right': 'rssd', 'relation': 'same_as', 'cardinality': '1:1',
                       'source': 'FDIC BankFind institutions FED_RSSD field'},
    'figi_ticker': {'left': 'figi', 'right': 'ticker', 'relation': 'listed_as', 'cardinality': '1:1', 'right_scope': 'mic',
                    'dated': True, 'source': 'OpenFIGI mapping responses (exchange-level FIGI)'},
}


def _day(value):
    return None if value in (None, '') else date.fromisoformat(str(value)[:10])


def _overlap(a, b):
    start = max([d for d in (_day(a.get('valid_from')), _day(b.get('valid_from'))) if d], default=None)
    end = min([d for d in (_day(a.get('valid_to')), _day(b.get('valid_to'))) if d], default=None)
    return start is None or end is None or start < end


def link_mapping(spec_name, rows, *, observed_at, evidence, entity_ids=None):
    """Turn published mapping rows into explicit link assertions.

    rows: dicts with ``left``, ``right``, optional ``valid_from``/``valid_to``/``scope``/``row_evidence``.
    entity_ids: optional {(namespace, value): entity_id}; defaults to ``namespace:value`` IDs.
    Returns {'assertions', 'conflicts', 'unlinked', 'spec'}.
    """
    if spec_name not in MAPPING_SPECS:
        raise ValueError('Unknown mapping specification: ' + str(spec_name))
    spec = MAPPING_SPECS[spec_name]
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('Mapping links require evidence')
    cleaned = []
    for raw in rows:
        left, right = str(raw.get('left', '')).strip(), str(raw.get('right', '')).strip()
        if not left or not right:
            continue
        if spec['left'] == 'sec_cik':
            left = str(int(left))
        if spec['right'] == 'sec_cik':
            right = str(int(right))
        if spec['left'] in ('lei', 'bioguide', 'figi', 'isin', 'cusip', 'gb_company_number'):
            left = left.upper()
        if spec['right'] in ('lei', 'bioguide', 'figi', 'isin', 'ticker', 'cusip', 'gb_company_number'):
            right = right.upper()
        if spec.get('right_scope') and not raw.get('scope'):
            raise ValueError(f'{spec_name} rows require a {spec["right_scope"]} scope')
        if _day(raw.get('valid_from')) and _day(raw.get('valid_to')) and _day(raw['valid_from']) >= _day(raw['valid_to']):
            raise ValueError('Invalid mapping validity interval')
        cleaned.append({**raw, 'left': left, 'right': right})
    conflicts, bad = [], set()
    left_side, right_side = spec['cardinality'].split(':')
    for side, other, limit in (('left', 'right', right_side), ('right', 'left', left_side)):
        if limit != '1':
            continue
        groups = {}
        for i, row in enumerate(cleaned):
            groups.setdefault((row[side], row.get('scope') if side == 'right' else None), []).append(i)
        for key, members in groups.items():
            for x in members:
                for y in members:
                    a, b = cleaned[x], cleaned[y]
                    if x < y and a[other] != b[other] and _overlap(a, b):
                        conflicts.append({'reason': f'{side} value maps to several {other} values in an overlapping period',
                                          'value': key[0], 'rows': [deepcopy(a), deepcopy(b)]})
                        bad.update((x, y))
    assertions, seen = [], set()
    for i, row in enumerate(cleaned):
        if i in bad:
            continue
        def entity(namespace, value, scope=None):
            key = (namespace, value) if scope is None else (namespace, value, scope)
            if entity_ids is not None:
                return entity_ids.get(key) or entity_ids.get((namespace, value))
            return f'{namespace}:{value}' if scope is None else f'{namespace}:{scope}:{value}'
        subject = entity(spec['left'], row['left'])
        obj = entity(spec['right'], row['right'], row.get('scope'))
        if not subject or not obj:
            continue
        key = (subject, obj, row.get('valid_from'), row.get('valid_to'))
        if key in seen:
            continue
        seen.add(key)
        record = {'kind': 'assertion', 'id': 'link:' + digest([spec_name, key]), 'subject': subject,
                  'predicate': spec['relation'], 'object': obj, 'observed_at': observed_at,
                  'evidence': deepcopy(row.get('row_evidence') or evidence),
                  'match': {'method': 'deterministic:' + spec_name, 'score': 1.0, 'reviewer_status': 'source_asserted',
                            'features': {spec['left']: row['left'], spec['right']: row['right'],
                                         **({'scope': row['scope']} if row.get('scope') else {})}},
                  'attributes': {'mapping_source': spec['source'], 'cardinality': spec['cardinality'],
                                 **({'note': spec['note']} if spec.get('note') else {})}}
        for field in ('valid_from', 'valid_to'):
            if row.get(field):
                record[field] = str(row[field])[:10]
        if spec.get('dated') and not (row.get('valid_from') or row.get('valid_to')):
            record['attributes']['temporal_validity'] = 'unknown'
        assertions.append(record)
    assertions.sort(key=lambda r: r['id'])
    return {'spec': deepcopy(spec), 'assertions': assertions, 'conflicts': conflicts,
            'rows': len(cleaned), 'linked': len(assertions)}


def shared_identifier_links(records, *, namespaces=None, observed_at, evidence):
    """Link entities from different sources that carry the same unique identifier value.

    Reads ``identifier_assignment`` assertions. Entities sharing a unique namespace value
    get a ``same_as`` assertion (method shared_identifier); an entity holding two values in a
    unique namespace in overlapping periods is reported as a conflict instead.
    """
    from ..identity import _normalize, _scope
    namespaces = set(namespaces or UNIQUE_NAMESPACES)
    by_value, by_subject = {}, {}
    for record in records:
        if record.get('kind') != 'assertion' or record.get('predicate') != 'identifier_assignment':
            continue
        value = record['value']
        ns, normalized = _normalize(value['namespace'], value['value'])
        if ns not in namespaces:
            continue
        key = (ns, _scope(value.get('scope')), normalized)
        by_value.setdefault(key, []).append(record)
        by_subject.setdefault((record['subject'], ns, _scope(value.get('scope'))), []).append((normalized, record))
    conflicts = []
    for (subject, ns, scope), items in sorted(by_subject.items()):
        values = {}
        for normalized, record in items:
            values.setdefault(normalized, []).append(record)
        if len(values) > 1:
            pairs = [(a, b) for va, ra in values.items() for vb, rb in values.items() if va < vb for a in ra for b in rb if _overlap(a, b)]
            if pairs:
                conflicts.append({'subject': subject, 'namespace': ns, 'values': sorted(values),
                                  'record_ids': sorted({r['id'] for pair in pairs for r in pair})})
    conflicted = {c['subject'] for c in conflicts}
    assertions = []
    for (ns, scope, value), items in sorted(by_value.items()):
        subjects = sorted({r['subject'] for r in items} - conflicted)
        for other in subjects[1:]:
            anchor = subjects[0]
            support = [r['id'] for r in items if r['subject'] in (anchor, other)]
            assertions.append({'kind': 'assertion', 'id': 'link:' + digest(['shared', ns, scope, value, anchor, other]),
                               'subject': other, 'predicate': 'same_as', 'object': anchor, 'observed_at': observed_at,
                               'evidence': deepcopy(evidence),
                               'match': {'method': 'deterministic:shared_identifier', 'score': 1.0,
                                         'reviewer_status': 'source_asserted',
                                         'features': {'namespace': ns, 'value': value, **({'scope': scope} if scope else {}),
                                                      'identifier_record_ids': sorted(support)}}})
    return {'assertions': assertions, 'conflicts': conflicts}
