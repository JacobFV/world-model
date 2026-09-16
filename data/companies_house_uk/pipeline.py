"""UK Companies House bulk products -> companies and dated significant-control assertions.

Shards are ZIPs recognised by member type:

* Basic Company Data (one CSV; some headers carry a leading space, stripped here; a few rows carry an
  extra unescaped delimiter, so rows are padded/truncated to the header rather than failing the build):
  ``gb:companies_house:<number>`` business entities with status, category,
  incorporation/dissolution dates and registered-office post town/postcode, company-number
  identifiers, UK SIC 2007 classifications, and previous names valid until their change date.
* People with Significant Control snapshot (JSON lines ``{"company_number", "data"}``):
  one entity per PSC register entry ``gb:psc:<psc id>`` (individual -> person, corporate or
  legal person -> organization, super-secure -> agent with a protected label) and a
  ``significant_control_over`` assertion to the company, valid [notified_on, ceased_on), with
  natures of control and parsed ownership/voting share bands. PSC ids are per company
  register entry, so the same human or firm controlling several companies is NOT merged.
  Corporate PSCs keep their published registry reference; a UK company number additionally
  yields ``registered_as`` to ``gb:companies_house:<number>`` (published identifier, not a
  name match). Statements and exemptions become dated company claims. Personal data are
  minimised: no birth dates or addresses are normalized.
"""
from datetime import date
import io
import json
import re
import zipfile
from worldmodel.util import digest

NUMBER = re.compile(r'[A-Z0-9]{8}')
BAND = re.compile(r'(ownership-of-shares|voting-rights|right-to-share-surplus-assets)-(\d+)-to-(\d+)-percent')
UK_REGISTERS = ('england', 'wales', 'scotland', 'northern ireland', 'united kingdom', 'uk', 'great britain', 'companies house')
PSC_TYPES = {'individual-person-with-significant-control': 'person', 'corporate-entity-person-with-significant-control': 'organization',
             'legal-person-person-with-significant-control': 'organization', 'super-secure-person-with-significant-control': 'agent',
             'individual-beneficial-owner': 'person', 'corporate-entity-beneficial-owner': 'organization',
             'legal-person-beneficial-owner': 'organization', 'super-secure-beneficial-owner': 'agent'}


def _dmy(text):
    text = (text or '').strip()
    if not text:
        return None
    day, month, year = text.split('/')
    return date(int(year), int(month), int(day)).isoformat()


def _iso(text):
    try:
        return date.fromisoformat(str(text)[:10]).isoformat()
    except (TypeError, ValueError):
        return None


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        observed = receipt['retrieved_at']
        for shard in context.raw_shards(index):
            with zipfile.ZipFile(shard['path']) as archive:
                members = [n for n in archive.namelist() if not n.endswith('/')]
            if any(n.lower().endswith('.csv') for n in members):
                yield from _basic(context, index, observed, shard)
            else:
                yield from _psc(context, index, observed, shard, members)


def _basic(context, index, observed, shard):
    from worldmodel.raw_readers import iter_rows
    for locator, raw in iter_rows([shard], {'format': 'csv', 'members': ['*.csv'], 'strict': False}):
        row = {k.strip(): (v or '').strip() for k, v in raw.items()}
        number = row.get('CompanyNumber', '').upper()
        if not NUMBER.fullmatch(number):
            continue
        subject = 'gb:companies_house:' + number
        base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
        attributes = {k: v for k, v in {
            'category': row.get('CompanyCategory'), 'status': row.get('CompanyStatus'), 'country_of_origin': row.get('CountryOfOrigin'),
            'incorporated': _dmy(row.get('IncorporationDate')), 'dissolved': _dmy(row.get('DissolutionDate')),
            'post_town': row.get('RegAddress.PostTown'), 'postcode': row.get('RegAddress.PostCode'),
            'accounts_category': row.get('Accounts.AccountCategory'), 'accounts_last_made_up': _dmy(row.get('Accounts.LastMadeUpDate'))}.items() if v}
        yield {**base, 'kind': 'entity', 'id': f'ch:{number}', 'entity_id': subject, 'entity_type': 'business',
               'label': row.get('CompanyName') or number, 'attributes': attributes}
        yield {**base, 'kind': 'assertion', 'id': f'ch:{number}:number', 'subject': subject, 'predicate': 'identifier_assignment',
               'value': {'namespace': 'gb_company_number', 'value': number}, 'attributes': {}}
        for n in range(1, 5):
            sic = row.get(f'SICCode.SicText_{n}', '')
            code = sic.split(' - ', 1)[0].strip()
            if code.isdigit():
                yield {**base, 'kind': 'assertion', 'id': f'ch:{number}:sic:{n}', 'subject': subject, 'predicate': 'classified_as',
                       'object': 'uksic2007:' + code, 'attributes': {'label': sic.split(' - ', 1)[-1].strip()}}
        for n in range(1, 11):
            name = row.get(f'PreviousName_{n}.CompanyName', '')
            if name:
                record = {**base, 'kind': 'assertion', 'id': f'ch:{number}:previous_name:{n}', 'subject': subject,
                          'predicate': 'legal_name', 'value': name, 'attributes': {'validity_basis': 'CONDATE is the name change date'}}
                changed = _dmy(row.get(f'PreviousName_{n}.CONDATE'))
                if changed:
                    record['valid_to'] = changed
                yield record


def _bands(natures):
    bands = {}
    for nature in natures:
        match = BAND.match(nature) if isinstance(nature, str) else None
        if match:
            kind, low, high = match.group(1).replace('-', '_'), int(match.group(2)) / 100, int(match.group(3)) / 100
            bands[kind] = [low, high]
    return bands


def _interval(record, start, end):
    if start:
        record['valid_from'] = start
    if end and (not start or end > start):
        record['valid_to'] = end


def _psc(context, index, observed, shard, members):
    with zipfile.ZipFile(shard['path']) as archive:
        for member in members:
            with archive.open(member) as stream:
                for number, line in enumerate(io.TextIOWrapper(stream, encoding='utf-8', errors='replace'), 1):
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    data = item.get('data') or {}
                    kind = data.get('kind') or ''
                    company_number = str(item.get('company_number') or '').upper()
                    if kind.startswith('totals#') or not NUMBER.fullmatch(company_number):
                        continue
                    locator = f'shard:{shard["index"]}/member:{member}/line:{number}'
                    base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
                    company = 'gb:companies_house:' + company_number
                    start, end = _iso(data.get('notified_on')), _iso(data.get('ceased_on'))
                    link = ((data.get('links') or {}).get('self') or '').rstrip('/')
                    psc_id = link.rsplit('/', 1)[-1] if link else digest([company_number, kind, data.get('name'), start])[:32]
                    key = f'chpsc:{company_number}:{psc_id}'
                    if kind in PSC_TYPES:
                        yield from _psc_entry(base, key, company, kind, psc_id, data, start, end)
                    elif kind == 'persons-with-significant-control-statement' or data.get('statement'):
                        record = {**base, 'kind': 'assertion', 'id': key + ':statement', 'subject': company, 'predicate': 'psc_statement',
                                  'value': data.get('statement') or kind, 'attributes': {'linked_psc_name': data.get('linked_psc_name')} if data.get('linked_psc_name') else {}}
                        _interval(record, start, end)
                        yield record
                    elif kind == 'exemptions' or data.get('exemptions'):
                        yield {**base, 'kind': 'assertion', 'id': key + ':exemption', 'subject': company, 'predicate': 'psc_exemption',
                               'value': sorted((data.get('exemptions') or {}).keys()) or [kind], 'attributes': {}}


def _psc_entry(base, key, company, kind, psc_id, data, start, end):
    psc = 'gb:psc:' + re.sub(r'[^A-Za-z0-9_-]', '_', psc_id)
    entity_type = PSC_TYPES[kind]
    identification = data.get('identification') or {}
    attributes = {'psc_kind': kind, 'company': company}
    if data.get('is_sanctioned'):
        attributes['is_sanctioned'] = True
    if data.get('description'):
        attributes['description'] = str(data['description'])[:200]
    if entity_type == 'person':
        attributes.update({k: data.get(k) for k in ('nationality', 'country_of_residence') if data.get(k)})
    elif entity_type == 'organization':
        attributes.update({k: identification.get(k) for k in ('legal_form', 'legal_authority', 'place_registered', 'country_registered', 'registration_number')
                           if identification.get(k)})
    label = data.get('name') or ('protected (super-secure PSC)' if entity_type == 'agent' else psc)
    yield {**base, 'kind': 'entity', 'id': key + ':entity', 'entity_id': psc, 'entity_type': entity_type, 'label': label,
           'attributes': {**attributes, 'identity_basis': 'Companies House PSC register entry id (per company; no cross-company merge)'}}
    natures = [n for n in (data.get('natures_of_control') or []) if isinstance(n, str)]
    record = {**base, 'kind': 'assertion', 'id': key + ':control', 'subject': psc, 'predicate': 'significant_control_over', 'object': company,
              'attributes': {'natures_of_control': natures, 'validity_basis': 'notified_on to ceased_on',
                             **({'ceased': True} if (data.get('ceased') or data.get('ceased_on')) else {})}}
    bands = _bands(natures)
    if bands:
        record['attributes']['share_bands'] = bands
    _interval(record, start, end)
    yield record
    registration = (identification.get('registration_number') or '').strip().upper().replace(' ', '')
    registry = ' '.join(str(identification.get(k) or '') for k in ('place_registered', 'country_registered', 'legal_authority')).lower()
    if entity_type == 'organization' and registration:
        yield {**base, 'kind': 'assertion', 'id': key + ':registration', 'subject': psc, 'predicate': 'identifier_assignment',
               'value': {'namespace': 'registry_number', 'value': registration, 'registry': identification.get('place_registered'),
                         'country': identification.get('country_registered')}, 'attributes': {}}
        candidate = registration.zfill(8) if registration.isdigit() else registration
        if NUMBER.fullmatch(candidate) and any(word in registry for word in UK_REGISTERS):
            yield {**base, 'kind': 'assertion', 'id': key + ':registered_as', 'subject': psc, 'predicate': 'registered_as',
                   'object': 'gb:companies_house:' + candidate,
                   'attributes': {'basis': 'published UK registration number of corporate PSC', 'registry_text': registry.strip()}}
