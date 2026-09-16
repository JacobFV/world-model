"""OFAC advanced XML (SDN + consolidated non-SDN lists) -> sanctions evidence.

Parties become ``ofac:party:<ProfileID>`` entities (person/organization/vessel/
aircraft); aliases, identifiers, addresses and other published features become
claims; nationality/registration/flag become country assertions; each
SanctionsEntry becomes a ``sanctions_designation`` event with its list, programs
and legal bases; ProfileRelationships become ownership/control/association
assertions. Memory is bounded by lookup tables (locations, ID documents), not by
the number of emitted records.
"""
import re

from .countries import country_entity, iso3_from_iso2, iso3_from_name
from .evidence import Evidence
from .helpers import iter_items, local, sniff, text

GOV = 'gov:USA:treasury_ofac'
WHOLE = ('DateOfIssue', 'ReferenceValueSets')
COUNTRY_PREDICATES = {
    'Nationality Country': 'nationality', 'Citizenship Country': 'citizenship',
    'Nationality of Registration': 'registered_in', 'Registration Country': 'registered_in',
    'Vessel Flag': 'flag_state', 'Former Vessel Flag': 'former_flag_state', 'Other Vessel Flag': 'other_flag_state',
}
IDENTIFIER_FEATURES = {
    'SWIFT/BIC': 'swift', 'ISIN': 'isin', 'D-U-N-S Number': 'duns', 'UN/LOCODE': 'unlocode', 'BIK (RU)': 'bik_ru',
    'Vessel Call Sign': 'callsign', 'Other Vessel Call Sign': 'callsign', 'Aircraft Tail Number': 'aircraft_tail',
    'Aircraft Mode S Transponder Code': 'icao24', 'Equity Ticker': None, 'MICEX Code': None,
    "Aircraft Manufacturer's Serial Number (MSN)": None, 'Previous Aircraft Tail Number': None,
    'Aircraft Construction Number (also called L/N or S/N or F/N)': None,
}
CONTACT_FEATURES = {'Website', 'Email Address', 'Phone Number'}
# (predicate, reverse): reverse=True means the declared "To" profile is the subject.
RELATIONS = {
    'Owned or Controlled By': ('controls', True), 'Owns, controls, or operates': ('controls', False),
    'Associate Of': ('affiliated_with', False), 'Family member of': ('family_member_of', False),
    'Acting for or on behalf of': ('acts_for_or_on_behalf_of', False), 'Providing support to': ('provides_support_to', False),
    'Leader or official of': ('leader_or_official_of', False), 'Principal Executive Officer': ('principal_executive_officer_of', False),
    'playing a significant role in': ('significant_role_in', False), 'Property in the interest of': ('property_in_interest_of', False),
}


def program_id(code):
    return 'ofac:program:' + (re.sub(r'[^A-Za-z0-9]+', '-', code).strip('-').upper() or 'UNKNOWN')


def ymd(element):
    if element is None:
        return None
    year = text(element, '{*}Year')
    if not year:
        return None
    month, day = text(element, '{*}Month') or '1', text(element, '{*}Day') or '1'
    return f'{int(year):04d}-{int(month):02d}-{int(day):02d}'


def date_period(period):
    """Exact date, year ('YYYY') or {'from','to'} range, plus approximate flag."""
    if period is None:
        return None
    start, end = period.find('{*}Start'), period.find('{*}End')
    low = ymd(start.find('{*}From')) if start is not None else None
    high = ymd(end.find('{*}To')) if end is not None else low
    approximate = any(e is not None and e.get('Approximate') == 'true' for e in (start, end))
    if low and high and low == high:
        value = low
    elif low and high and low[:4] == high[:4] and low[5:] == '01-01' and high[5:] == '12-31':
        value = low[:4]
    else:
        value = {'from': low, 'to': high}
    return {'date': value, 'approximate': approximate} if approximate else value


class Tables:
    def __init__(self):
        self.refs, self.locations, self.documents = {}, {}, {}

    def ref(self, table, key, default=None):
        entry = self.refs.get(table, {}).get(key)
        return entry[0] if entry else default

    def attr(self, table, key, name):
        entry = self.refs.get(table, {}).get(key)
        return entry[1].get(name) if entry else None

    def load_refs(self, element):
        for value_set in element:
            self.refs[local(value_set.tag)] = {item.get('ID'): ((item.text or '').strip(), dict(item.attrib)) for item in value_set}

    def country(self, country_id):
        iso2 = self.attr('CountryValues', country_id, 'ISO2')
        return iso3_from_iso2(iso2) if iso2 else None, self.ref('CountryValues', country_id)

    def load_location(self, element):
        country = element.find('{*}LocationCountry')
        iso3, name = self.country(country.get('CountryID')) if country is not None else (None, None)
        parts = {}
        for part in element.findall('{*}LocationPart'):
            kind = self.ref('LocPartTypeValues', part.get('LocPartTypeID'), 'part').lower().replace('/', '_').replace(' ', '_')
            values = [text(v, '{*}Value') for v in part.findall('{*}LocationPartValue')]
            values = [v for v in values if v]
            if values:
                parts[kind] = values[0]
        self.locations[element.get('ID')] = {'iso3': iso3, 'country': name, 'parts': parts}

    def load_document(self, element):
        iso3, name = self.country(element.get('IssuedBy-CountryID')) if element.get('IssuedBy-CountryID') else (None, None)
        document = {'type': self.ref('IDRegDocTypeValues', element.get('IDRegDocTypeID'), 'unknown'),
                    'number': text(element, '{*}IDRegistrationNo'),
                    'validity': self.ref('ValidityValues', element.get('ValidityID')),
                    'document_id': element.get('ID')}
        if iso3 or name:
            document['issuing_country'] = iso3 or name
        authority = text(element, '{*}IssuingAuthority')
        if authority:
            document['issuing_authority'] = authority
        for date in element.findall('{*}DocumentDate'):
            label = self.ref('IDRegDocDateTypeValues', date.get('IDRegDocDateTypeID'), 'date').lower().replace(' ', '_')
            document[label] = date_period(date.find('{*}DatePeriod'))
        self.documents.setdefault(element.get('IdentityID'), []).append(document)


def namespaced_identifier(kind, number):
    if not number:
        return None
    compact = re.sub(r'\s+', '', number).upper()
    match = re.search(r'IMO(\d{7})', compact)
    if match:
        return 'imo:' + match[1]
    if kind in ('Legal Entity Number', 'LE Number') and re.fullmatch(r'[A-Z0-9]{18}[0-9]{2}', compact):
        return 'lei:' + compact
    if kind == 'MMSI' and re.fullmatch(r'\d{9}', compact):
        return 'mmsi:' + compact
    return None


def run(context):
    ev = Evidence(context, 'ofac')
    try:
        for shard in context.raw_shards():
            yield from _shard(context, ev, shard)
    finally:
        ev.close()


def _shard(context, ev, shard):
    head = sniff(shard['path'])
    if b'<Sanctions' not in head and b'ADVANCED_XML' not in head:
        raise ValueError(f'shard {shard["index"]}: expected OFAC advanced XML')
    url = str((shard.get('request') or {}).get('url') or shard.get('url') or '')
    source_file = 'SDN_ADVANCED' if 'SDN' in url.upper() else 'CONS_ADVANCED' if 'CONS' in url.upper() else f'shard{shard["index"]}'
    prefix = f'shard:{shard["index"]}/xpath:/Sanctions'
    tables, issued = Tables(), None
    for section, ordinal, element in iter_items(shard['path'], WHOLE):
        name = local(element.tag)
        locator = f'{prefix}/{section}' if ordinal == 0 else f'{prefix}/{section}/{name}[{ordinal}]'
        if section == 'DateOfIssue':
            issued = ymd(element)
            gov = ev.entity(GOV, 'government_agency', 'U.S. Treasury Office of Foreign Assets Control', locator, jurisdiction='USA')
            if gov:
                yield gov
                usa, record = country_entity(ev, 'USA', locator)
                if record:
                    yield record
                rel = ev.relation(GOV, 'registered_in', usa, locator)
                if rel:
                    yield rel
        elif section == 'ReferenceValueSets':
            tables.load_refs(element)
        elif name == 'Location':
            tables.load_location(element)
        elif name == 'IDRegDocument':
            tables.load_document(element)
        elif name == 'DistinctParty':
            yield from _party(ev, tables, element, locator, source_file, issued)
        elif name == 'ProfileRelationship':
            yield from _relationship(ev, tables, element, locator, source_file)
        elif name == 'SanctionsEntry':
            yield from _entry(ev, tables, element, locator, source_file, issued)


def _party(ev, tables, element, locator, source_file, issued):
    profile = element.find('{*}Profile')
    if profile is None:
        return
    profile_id = profile.get('ID')
    key = 'ofac:party:' + profile_id
    sub_type = tables.ref('PartySubTypeValues', profile.get('PartySubTypeID'), 'Unknown')
    party_type = tables.ref('PartyTypeValues', tables.attr('PartySubTypeValues', profile.get('PartySubTypeID'), 'PartyTypeID'), 'Unknown')
    entity_type = {'Vessel': 'vessel', 'Aircraft': 'aircraft'}.get(sub_type) or {
        'Individual': 'person', 'Entity': 'organization', 'Other Entity': 'organization',
        'Transport': 'vehicle', 'Location': 'location'}.get(party_type, 'organization')
    names = []
    identity_ids = []
    for identity in profile.findall('{*}Identity'):
        identity_ids.append(identity.get('ID'))
        for alias in identity.findall('{*}Alias'):
            alias_type = tables.ref('AliasTypeValues', alias.get('AliasTypeID'), 'unknown')
            for documented in alias.findall('{*}DocumentedName'):
                values = documented.findall('.//{*}NamePartValue')
                parts = [(v.text or '').strip() for v in values if (v.text or '').strip()]
                if not parts:
                    continue
                script = next((tables.ref('ScriptValues', v.get('ScriptID')) for v in values), None)
                names.append({'name': ' '.join(parts), 'alias_type': alias_type, 'primary': alias.get('Primary') == 'true',
                              'low_quality': alias.get('LowQuality') == 'true', 'script': script,
                              'status': tables.ref('DocNameStatusValues', documented.get('DocNameStatusID'))})
    primary = next((n for n in names if n['primary'] and n['status'] == 'Primary Latin'), None) \
        or next((n for n in names if n['primary']), None) or (names[0] if names else None)
    label = primary['name'] if primary else key
    record = ev.entity(key, entity_type, label, locator, party_type=party_type, party_sub_type=sub_type,
                       fixed_ref=element.get('FixedRef'), source_file=source_file, list_publication_date=issued,
                       comment=text(element, '{*}Comment'))
    if record:
        yield record
    for number, name in enumerate(names):
        if name is primary:
            continue
        value = {k: v for k, v in name.items() if k not in ('primary',) and v not in (None, False)}
        yield ev.claim(key, 'sanctions_alias', value, locator, identity=[source_file, profile_id, 'alias', number])
    for identity_id in identity_ids:
        for document in tables.documents.pop(identity_id, []):
            value = {k: v for k, v in document.items() if v is not None}
            value['scheme'] = value.pop('type')
            identifier = namespaced_identifier(document['type'], document.get('number'))
            if identifier:
                value['id'] = identifier
            yield ev.claim(key, 'identifier', value, locator, identity=[source_file, profile_id, 'document', document['document_id']])
    for feature in profile.findall('{*}Feature'):
        feature_type = tables.ref('FeatureTypeValues', feature.get('FeatureTypeID'), 'unknown')
        for version in feature.findall('{*}FeatureVersion'):
            yield from _feature(ev, tables, key, feature_type, version, locator, [source_file, profile_id, 'feature', feature.get('ID'), version.get('ID')])


def _feature(ev, tables, key, feature_type, version, locator, identity):
    reliability = tables.ref('ReliabilityValues', version.get('ReliabilityID'))
    location_ref = version.find('{*}VersionLocation')
    location = tables.locations.get(location_ref.get('LocationID')) if location_ref is not None else None
    detail, period = version.find('{*}VersionDetail'), version.find('{*}DatePeriod')
    value = None
    if period is not None:
        value = date_period(period)
    elif detail is not None:
        value = tables.ref('DetailReferenceValues', detail.get('DetailReferenceID')) if detail.get('DetailReferenceID') else text(detail)
    extra = {'reliability': reliability} if reliability and reliability != 'Unknown' else {}
    if feature_type == 'Location':
        if not location:
            return
        address = {**location['parts'], **({'country': location['country']} if location['country'] else {}), **extra}
        if location['iso3']:
            address['country_iso3'] = location['iso3']
            country, record = country_entity(ev, location['iso3'], locator)
            if record:
                yield record
            rel = ev.relation(key, 'located_in', country, locator, source_feature='Location')
            if rel:
                yield rel
        yield ev.claim(key, 'address', address, locator, identity=identity)
    elif feature_type in COUNTRY_PREDICATES:
        # Nationality/flag locations usually carry only an unnamed part holding a country name or demonym.
        iso3 = (location['iso3'] or next((iso3_from_name(v) for v in location['parts'].values() if iso3_from_name(v)), None)) if location else None
        if location and not location['country'] and location['parts']:
            location = {**location, 'country': next(iter(location['parts'].values()))}
        if iso3:
            country, record = country_entity(ev, iso3, locator)
            if record:
                yield record
            rel = ev.relation(key, COUNTRY_PREDICATES[feature_type], country, locator, source_feature=feature_type, **extra)
            if rel:
                yield rel
        elif location and location['country']:
            yield ev.claim(key, COUNTRY_PREDICATES[feature_type], {'country': location['country'], 'unmapped_country': True}, locator, identity=identity)
    elif value in (None, ''):
        return
    elif feature_type == 'Birthdate':
        yield ev.claim(key, 'birth_date', value, locator, identity=identity)
    elif feature_type == 'Place of Birth':
        yield ev.claim(key, 'place_of_birth', value, locator, identity=identity)
    elif feature_type in IDENTIFIER_FEATURES or feature_type.startswith('Digital Currency Address'):
        claim = {'scheme': feature_type, 'value': value}
        namespace = IDENTIFIER_FEATURES.get(feature_type)
        if feature_type.startswith('Digital Currency Address') and isinstance(value, str):
            namespace = 'crypto:' + feature_type.rsplit('-', 1)[-1].strip().lower()
        if namespace and isinstance(value, str):
            claim['id'] = namespace + ':' + re.sub(r'\s+', '', value)
        yield ev.claim(key, 'identifier', claim, locator, identity=identity)
    elif feature_type in CONTACT_FEATURES:
        yield ev.claim(key, 'contact', {'type': feature_type, 'value': value}, locator, identity=identity)
    else:
        yield ev.claim(key, 'sanctions_feature', {'type': feature_type.rstrip(' :-'), 'value': value, **extra}, locator, identity=identity)


def _relationship(ev, tables, element, locator, source_file):
    kind = tables.ref('RelationTypeValues', element.get('RelationTypeID'), 'unknown')
    predicate, reverse = RELATIONS.get(kind, ('sanctions_relationship', False))
    source, target = 'ofac:party:' + element.get('From-ProfileID'), 'ofac:party:' + element.get('To-ProfileID')
    subject, obj = (target, source) if reverse else (source, target)
    record = ev.relation(subject, predicate, obj, locator, identity=[source_file, element.get('ID')], once=False,
                         source_relation=kind, former=element.get('Former') == 'true',
                         quality=tables.ref('RelationQualityValues', element.get('RelationQualityID')),
                         relationship_id=element.get('ID'), sanctions_entry_id=element.get('SanctionsEntryID'))
    yield record


def _entry(ev, tables, element, locator, source_file, issued):
    party = 'ofac:party:' + element.get('ProfileID')
    list_name = tables.ref('ListValues', element.get('ListID'), 'unknown')
    dates, bases = [], []
    for event in element.findall('{*}EntryEvent'):
        date = ymd(event.find('{*}Date'))
        if date:
            dates.append(date)
        basis = tables.attr('LegalBasisValues', event.get('LegalBasisID'), 'LegalBasisShortRef')
        if basis and basis != 'Unknown' and basis not in bases:
            bases.append(basis)
    programs, measures = [], []
    for measure in element.findall('{*}SanctionsMeasure'):
        kind = tables.ref('SanctionsTypeValues', measure.get('SanctionsTypeID'), 'unknown')
        comment = text(measure, '{*}Comment')
        if kind == 'Program':
            if comment and comment not in programs:
                programs.append(comment)
        elif kind not in measures:
            measures.append(kind)
    program_ids = []
    for code in programs:
        pid = program_id(code)
        program_ids.append(pid)
        record = ev.entity(pid, 'regulation', code, locator, program_code=code, issuer=GOV, jurisdiction='USA')
        if record:
            yield record
        rel = ev.relation(party, 'subject_to_sanctions_program', pid, locator, list=list_name)
        if rel:
            yield rel
    attributes = {'list': list_name, 'programs': programs, 'measures': measures, 'legal_basis': bases,
                  'entry_id': element.get('ID'), 'source_file': source_file, 'list_publication_date': issued,
                  'date_semantics': 'OFAC entry event date (listing/creation); earliest event date used'}
    if dates:
        yield ev.event('sanctions_designation', min(dates), [party, *program_ids, GOV], locator,
                       [source_file, 'entry', element.get('ID')], attributes={**attributes, 'event_dates': sorted(set(dates))})
    else:
        yield ev.claim(party, 'sanctions_listing', attributes, locator, identity=[source_file, 'entry', element.get('ID')])
