"""GLEIF Level 1 golden copy + ISIN/BIC mapping files -> LEI entities and identifier links.

Full acquisition shards: 0 = LEI-CDF 3.1 CSV (one row per LEI), 1 = ISIN-LEI mapping,
2 = BIC-LEI mapping (identified by header, not position). Normalization is restricted to
fields simulations need (legal name, jurisdiction, category, legal form, entity and
registration status, key dates, successor LEIs). ISIN links are restricted to US and CA
ISINs (2.3M of 9.3M rows; the rest are mostly DE/CH structured products) and emitted as
``issuer_security`` edges from the issuer LEI to ``isin:<code>``; the full mapping stays in raw.
Legacy JSONL samples (GLEIF API records) keep the original behaviour.
"""
import csv
import io
import json
import re
import zipfile
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS

ISIN_COUNTRIES = ('US', 'CA')
LEI = re.compile(r'[A-Z0-9]{18}[0-9]{2}')
ISIN = re.compile(r'[A-Z]{2}[A-Z0-9]{9}[0-9]')
BIC = re.compile(r'[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?')
CATEGORY_TYPE = {'FUND': 'investment_fund', 'RESIDENT_GOVERNMENT_ENTITY': 'government_agency', 'SOLE_PROPRIETOR': 'business'}


def run(context):
    if not context.raw_inputs:
        raise ValueError('sec_gleif: no raw artifact supplied')
    for index in range(len(context.raw_inputs)):
        receipt = _receipt(context, index)
        if 'acquisition' in (receipt.get('source') or {}):
            yield from _full(context, index, receipt)
        else:
            yield from _sample(context, index, receipt)


def _receipt(context, index):
    # Minimal legacy contexts (sample adapter tests) expose only raw_path.
    if hasattr(context, 'raw_receipt'):
        return context.raw_receipt(index)
    return json.loads(context.raw_path(index).with_name('receipt.json').read_text())


def _csv(path):
    """Yield (member, line number, header, values) from the single CSV inside a ZIP shard."""
    with zipfile.ZipFile(path) as archive:
        members = [n for n in archive.namelist() if n.lower().endswith('.csv')]
        if len(members) != 1:
            raise ValueError('Expected exactly one CSV member in GLEIF ZIP')
        with archive.open(members[0]) as stream:
            text = io.TextIOWrapper(stream, encoding='utf-8-sig', newline='')
            reader = csv.reader(text)
            header = next(reader)
            yield members[0], 1, header, None
            last = 1
            for values in reader:
                start, last = last + 1, reader.line_num
                yield members[0], start, header, values


def _date(value):
    return value[:10] if value else None


def _full(context, index, receipt):
    observed = receipt['retrieved_at']
    geo_emitted = set()
    for shard in context.raw_shards(index):
        rows = _csv(shard['path'])
        member, _, header, _ = next(rows)
        prefix = f'shard:{shard["index"]}/member:{member}'
        if header[:2] == ['LEI', 'Entity.LegalName']:
            col = {name: i for i, name in enumerate(header)}
            yield from _lei_rows(context, index, observed, prefix, rows, col, geo_emitted)
        elif header == ['LEI', 'ISIN']:
            for _, line, _, (lei, isin) in rows:
                if isin[:2] not in ISIN_COUNTRIES:
                    continue
                if not LEI.fullmatch(lei) or not ISIN.fullmatch(isin):
                    raise ValueError(f'{prefix}/line:{line}: invalid LEI/ISIN pair')
                yield {'kind': 'assertion', 'id': f'gleif_isin:{isin}:{lei}', 'subject': 'lei:' + lei, 'predicate': 'issuer_security',
                       'object': 'isin:' + isin, 'observed_at': observed, 'evidence': context.raw_evidence(f'{prefix}/line:{line}', index),
                       'attributes': {'basis': 'GLEIF/ANNA ISIN-to-LEI mapping: LEI of the ISIN issuer', 'validity_basis': 'mapping file snapshot'}}
        elif header == ['LEI', 'BIC']:
            for _, line, _, (lei, bic) in rows:
                if not LEI.fullmatch(lei) or not BIC.fullmatch(bic):
                    raise ValueError(f'{prefix}/line:{line}: invalid LEI/BIC pair')
                yield {'kind': 'assertion', 'id': f'gleif_bic:{lei}:{bic}', 'subject': 'lei:' + lei, 'predicate': 'identifier_assignment',
                       'value': {'namespace': 'bic', 'value': bic}, 'observed_at': observed,
                       'evidence': context.raw_evidence(f'{prefix}/line:{line}', index),
                       'attributes': {'basis': 'GLEIF/SWIFT BIC-to-LEI mapping', 'validity_basis': 'mapping file snapshot'}}
        else:
            raise ValueError(f'{prefix}: unrecognized GLEIF CSV header')


def _lei_rows(context, index, observed, prefix, rows, col, geo_emitted):
    def get(row, name):
        return row[col[name]] if name in col else ''
    for _, line, _, row in rows:
        lei = get(row, 'LEI')
        if not LEI.fullmatch(lei):
            raise ValueError(f'{prefix}/line:{line}: invalid LEI')
        base = {'observed_at': observed, 'evidence': context.raw_evidence(f'{prefix}/line:{line}', index)}
        category = get(row, 'Entity.EntityCategory')
        jurisdiction = get(row, 'Entity.LegalJurisdiction')
        subject = 'lei:' + lei
        attributes = {
            'jurisdiction': jurisdiction or None, 'category': category or None,
            'subcategory': get(row, 'Entity.EntitySubCategory') or None,
            'legal_form': get(row, 'Entity.LegalForm.EntityLegalFormCode') or None,
            'legal_form_other': get(row, 'Entity.LegalForm.OtherLegalForm') or None,
            'entity_status': get(row, 'Entity.EntityStatus') or None,
            'registration_status': get(row, 'Registration.RegistrationStatus') or None,
            'legal_country': get(row, 'Entity.LegalAddress.Country') or None, 'legal_region': get(row, 'Entity.LegalAddress.Region') or None,
            'legal_city': get(row, 'Entity.LegalAddress.City') or None,
            'hq_country': get(row, 'Entity.HeadquartersAddress.Country') or None, 'hq_region': get(row, 'Entity.HeadquartersAddress.Region') or None,
            'registration_authority': get(row, 'Entity.RegistrationAuthority.RegistrationAuthorityID') or None,
            'registration_authority_entity_id': get(row, 'Entity.RegistrationAuthority.RegistrationAuthorityEntityID') or None,
            'entity_created': _date(get(row, 'Entity.EntityCreationDate')),
            'entity_expired': _date(get(row, 'Entity.EntityExpirationDate')),
            'expiration_reason': get(row, 'Entity.EntityExpirationReason') or None,
            'initial_registration': _date(get(row, 'Registration.InitialRegistrationDate')),
            'last_update': _date(get(row, 'Registration.LastUpdateDate')),
            'next_renewal': _date(get(row, 'Registration.NextRenewalDate')),
            'managing_lou': get(row, 'Registration.ManagingLOU') or None,
            'validation_sources': get(row, 'Registration.ValidationSources') or None}
        yield {**base, 'kind': 'entity', 'id': f'gleif_lei:{lei}', 'entity_id': subject,
               'entity_type': CATEGORY_TYPE.get(category, 'organization'), 'label': get(row, 'Entity.LegalName') or lei,
               'attributes': {k: v for k, v in attributes.items() if v is not None}}
        target = None
        if jurisdiction == 'US':
            target, label, typ = 'geo:US', 'United States', 'country'
        elif jurisdiction.startswith('US-') and jurisdiction[3:] in STATE_FIPS:
            target, label, typ = 'geo:US:state:' + STATE_FIPS[jurisdiction[3:]], jurisdiction, 'state'
        if target:
            if target not in geo_emitted:
                geo_emitted.add(target)
                yield {**base, 'kind': 'entity', 'id': 'gleif_geo:' + target, 'entity_id': target, 'entity_type': typ, 'label': label,
                       'attributes': {'identity_basis': 'ISO 3166 jurisdiction code in LEI record'}}
            yield {**base, 'kind': 'assertion', 'id': f'gleif_lei:{lei}:registered_in', 'subject': subject, 'predicate': 'registered_in',
                   'object': target, 'attributes': {'source_jurisdiction': jurisdiction}}
        for n in range(1, 6):
            successor = get(row, f'Entity.SuccessorEntity.{n}.SuccessorLEI')
            if successor and LEI.fullmatch(successor):
                yield {**base, 'kind': 'assertion', 'id': f'gleif_lei:{lei}:successor:{n}', 'subject': subject, 'predicate': 'successor_entity',
                       'object': 'lei:' + successor, 'attributes': {'successor_name': get(row, f'Entity.SuccessorEntity.{n}.SuccessorEntityName') or None}}


def _sample(context, index, receipt):
    dataset = 'sec_gleif'
    ref = context.raw_inputs[index]
    acquired = receipt['retrieved_at']
    seen = set()
    for number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        evidence = context.raw_evidence(f'line:{number}', index)
        out = []

        def base(kind, identity, **fields):
            record = {'kind': kind, 'id': 'normalized:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': evidence,
                      'attributes': {'source_row': row, 'source_dataset': dataset}, **fields}
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
            r = base('observation', ['obs', number, subject, metric, start], subject=subject, metric=metric,
                     value=None if value in (None, '') else value, unit=unit, dimensions={'subject': subject})
            r['attributes'].update(source_unit=unit, **attrs)
            if value in (None, ''):
                r['missing_reason'] = 'source_missing_or_suppressed'
            if start:
                r['valid_from'] = start
            if end:
                r['valid_to'] = end
            return r

        data = row['attributes']['entity']
        typ = 'investment_fund' if data.get('category') == 'FUND' else 'organization'
        key = entity('lei:' + row['id'], typ, data['legalName']['name'])
        country = data.get('jurisdiction')
        if country:
            target = geo() if country == 'US' else entity('geo:' + country, 'country', country, synthetic=True)
            rel(key, 'registered_in', target)
        obs(key, 'legal_status', data.get('status'), 'category', acquired, None, validity_basis='status in acquired snapshot')
        yield from out
