"""OpenSanctions 'sanctions' collection targets.simple.csv -> sanctions evidence.

CC BY-NC 4.0: non-commercial use only. Entities ``opensanctions:<id>``; programs
``opensanctions:program:<program_id>``; countries ``iso3:XXX``. ``first_seen`` is
the OpenSanctions crawler's first observation, not an official designation date,
so it is emitted as ``sanctions_listing_first_seen``.
"""
import re

from .countries import country_entity, iso3_from_iso2
from .evidence import Evidence

SCHEMA_TYPES = {'person': 'person', 'organization': 'organization', 'legalentity': 'organization', 'company': 'business',
                'vessel': 'vessel', 'airplane': 'aircraft', 'cryptowallet': 'account', 'security': 'security',
                'asset': 'asset', 'address': 'location'}
# OpenSanctions territory codes that are sub-national or historical.
SPECIAL_COUNTRIES = {'gb-nir': ('GBR', 'Northern Ireland'), 'ua-lpr': ('UKR', 'Luhansk (occupied)'),
                     'ua-dpr': ('UKR', 'Donetsk (occupied)'), 'ua-cri': ('UKR', 'Crimea (occupied)')}
MAX_TEXT = 2000


def split(value):
    return [v.strip() for v in (value or '').split(';') if v.strip()]


def timestamp(value):
    value = (value or '').strip()
    if re.fullmatch(r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}', value):
        return value + '+00:00'
    return value if re.fullmatch(r'\d{4}-\d{2}-\d{2}', value) else None


def run(context):
    ev = Evidence(context, 'opensanctions')
    try:
        for index, _ in enumerate(context.raw_inputs):
            for locator, row in context.raw_rows(index, format='csv', encoding='utf-8-sig'):
                yield from _row(ev, locator, row)
    finally:
        ev.close()


def _row(ev, locator, row):
    source_id = (row.get('id') or '').strip()
    if not source_id:
        return
    key = 'opensanctions:' + source_id
    schema = (row.get('schema') or '').strip()
    entity_type = SCHEMA_TYPES.get(schema.lower(), 'entity')
    record = ev.entity(key, entity_type, (row.get('name') or '').strip() or key, locator, schema=schema,
                       source_datasets=split(row.get('dataset')), last_seen=timestamp(row.get('last_seen')),
                       last_change=timestamp(row.get('last_change')), licence='CC BY-NC 4.0 (non-commercial)')
    if record:
        yield record
    for number, alias in enumerate(split(row.get('aliases'))):
        yield ev.claim(key, 'sanctions_alias', {'name': alias}, locator, identity=[source_id, 'alias', number])
    for number, value in enumerate(split(row.get('birth_date'))):
        yield ev.claim(key, 'birth_date', value, locator, identity=[source_id, 'birth', number])
    for code in split(row.get('countries')):
        special = SPECIAL_COUNTRIES.get(code.lower())
        iso3 = special[0] if special else iso3_from_iso2(code)
        if not iso3:
            yield ev.claim(key, 'associated_country', {'code': code, 'unmapped_country': True}, locator, identity=[source_id, 'country', code])
            continue
        country, country_record = country_entity(ev, iso3, locator)
        if country_record:
            yield country_record
        attrs = {'opensanctions_code': code, 'semantics': 'OpenSanctions countries property (nationality, jurisdiction or address country; not disambiguated)'}
        if special:
            attrs['subdivision'] = special[1]
        relation = ev.relation(key, 'associated_country', country, locator, **attrs)
        if relation:
            yield relation
    for number, address in enumerate(split(row.get('addresses'))):
        yield ev.claim(key, 'address', {'text': address[:MAX_TEXT]}, locator, identity=[source_id, 'address', number])
    for number, identifier in enumerate(split(row.get('identifiers'))):
        value = {'value': identifier}
        imo = re.fullmatch(r'IMO\s*(\d{7})', identifier, re.I)
        if imo:
            value['id'] = 'imo:' + imo[1]
        yield ev.claim(key, 'identifier', value, locator, identity=[source_id, 'identifier', number])
    for column in ('phones', 'emails'):
        for number, contact in enumerate(split(row.get(column))):
            yield ev.claim(key, 'contact', {'type': column[:-1], 'value': contact}, locator, identity=[source_id, column, number])
    sanctions = (row.get('sanctions') or '').strip()
    if sanctions:
        truncated = len(sanctions) > MAX_TEXT
        yield ev.claim(key, 'sanctions_summary', {'text': sanctions[:MAX_TEXT], 'truncated': truncated}, locator, identity=[source_id, 'sanctions'])
    programs = []
    for program in split(row.get('program_ids')):
        pid = 'opensanctions:program:' + re.sub(r'[^A-Za-z0-9._-]+', '-', program)
        programs.append(pid)
        program_record = ev.entity(pid, 'regulation', program, locator, program_id=program)
        if program_record:
            yield program_record
        relation = ev.relation(key, 'subject_to_sanctions_program', pid, locator)
        if relation:
            yield relation
    first_seen = timestamp(row.get('first_seen'))
    if first_seen:
        yield ev.event('sanctions_listing_first_seen', first_seen, [key, *programs], locator, [source_id, 'first_seen'],
                       attributes={'source_datasets': split(row.get('dataset')), 'last_seen': timestamp(row.get('last_seen')),
                                   'date_semantics': 'OpenSanctions crawler first observation; not the official designation date'})
