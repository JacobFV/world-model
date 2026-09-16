"""UN Security Council Consolidated List (XML), UK Sanctions List (CSV) and U.S.
Consolidated Screening List (CSV) -> sanctions evidence.

Shards are recognised by content, so manual ``wm import`` of any one file also
works. Namespaces: ``un:sanctions:<REFERENCE_NUMBER>``, ``uk:sanctions:<Unique ID>``,
``us_csl:<_id>``; regimes/programs ``un:regime:*``, ``uk:regime:*``,
``ofac:program:*`` (Treasury programs, shared with ofac_sanctions) or
``us_csl:program:*``; lists ``us_csl:list:*``; countries ``iso3:XXX``.
"""
import re

from worldmodel.raw_readers import iter_rows

from .countries import country_entity, iso3_from_iso2, iso3_from_name
from .evidence import Evidence
from .helpers import iter_items, sniff, text

UNSC = 'org:UN:security_council'
UK_GOV = 'gov:GBR:fcdo_ofsi'
UN_REFERENCE = re.compile(r'\b[A-Z]{2}[ie]\.\d{3}\b')
NAMESPACED = [(re.compile(r'IMO\s*(\d{7})', re.I), 'imo'), (re.compile(r'^\s*([A-Z0-9]{18}[0-9]{2})\s*$'), None)]


def slug(value):
    return re.sub(r'[^a-z0-9]+', '-', str(value).lower()).strip('-')[:80] or 'unknown'


def short(value, limit=500):
    value = (value or '').strip()
    return value[:limit] + '…' if len(value) > limit else value


def dmy(value):
    """UK dd/mm/yyyy (with dd/mm placeholders) -> ISO date or year."""
    value = (value or '').strip()
    match = re.fullmatch(r'(\d{2}|dd)/(\d{2}|mm)/(\d{4})', value)
    if not match:
        return None
    day, month, year = match.groups()
    if day == 'dd' or month == 'mm':
        return year
    return f'{year}-{month}-{day}'


def split_numbered(value):
    """'(1) A (2) B' -> ['A', 'B']; plain values -> [value]."""
    value = (value or '').strip()
    if not value:
        return []
    parts = [p.strip(' ,;') for p in re.split(r'\(\d+\)', value)]
    return [p for p in parts if p]


def run(context):
    ev = Evidence(context, 'sanctions')
    try:
        for shard in context.raw_shards():
            head = sniff(shard['path'], 4096).lstrip(b'\xef\xbb\xbf')
            if head.lstrip().startswith(b'<') and b'CONSOLIDATED_LIST' in head:
                yield from UN(ev).run(shard)
            elif head.startswith(b'Report Date') or b'Unique ID' in head[:2048]:
                yield from UK(ev).run(shard)
            elif head.startswith(b'_id,source') or head.startswith(b'"_id","source"'):
                yield from CSL(ev).run(shard)
            else:
                raise ValueError(f'shard {shard["index"]}: unrecognised sanctions list format')
    finally:
        ev.close()


class Common:
    def __init__(self, ev):
        self.ev = ev

    def entity(self, key, entity_type, label, locator, **attrs):
        record = self.ev.entity(key, entity_type, label, locator, **attrs)
        return [record] if record else []

    def relation(self, subject, predicate, obj, locator, **attrs):
        record = self.ev.relation(subject, predicate, obj, locator, **attrs)
        return [record] if record else []

    def once(self, subject, predicate, value, locator):
        """Claim deduplicated on (subject, predicate, value)."""
        from worldmodel.util import digest
        identity = digest([subject, predicate, value])
        if not self.ev.keys.add('c|' + identity):
            return []
        return [self.ev.claim(subject, predicate, value, locator, identity=identity)]

    def country(self, subject, predicate, iso3, locator, **attrs):
        if not iso3:
            return []
        key, record = country_entity(self.ev, iso3, locator)
        return ([record] if record else []) + self.relation(subject, predicate, key, locator, **attrs)

    def country_by_name(self, subject, predicate, name, locator):
        iso3 = iso3_from_name(name)
        if iso3:
            return self.country(subject, predicate, iso3, locator)
        return self.once(subject, predicate, {'country': name, 'unmapped_country': True}, locator) if name else []


class UN(Common):
    def run(self, shard):
        ev = self.ev
        for section, ordinal, element in iter_items(shard['path']):
            kind = element.tag
            if kind not in ('INDIVIDUAL', 'ENTITY'):
                continue
            locator = f'shard:{shard["index"]}/xpath:/CONSOLIDATED_LIST/{section}/{kind}[{ordinal}]'
            reference = text(element, 'REFERENCE_NUMBER') or 'DATAID-' + (text(element, 'DATAID') or str(ordinal))
            key = 'un:sanctions:' + reference
            names = [text(element, tag) for tag in ('FIRST_NAME', 'SECOND_NAME', 'THIRD_NAME', 'FOURTH_NAME')]
            label = ' '.join(n for n in names if n) or key
            regime = text(element, 'UN_LIST_TYPE') or 'unknown'
            regime_key = 'un:regime:' + slug(regime)
            yield from self.entity(UNSC, 'institution', 'United Nations Security Council', locator)
            yield from self.entity(key, 'person' if kind == 'INDIVIDUAL' else 'organization', label, locator,
                                   un_reference_number=reference, dataid=text(element, 'DATAID'), list='UN Security Council Consolidated List',
                                   gender=text(element, 'GENDER'), last_updated=[text(v) for v in element.findall('LAST_DAY_UPDATED/VALUE') if text(v)])
            yield from self.entity(regime_key, 'regulation', 'UN sanctions regime: ' + regime, locator, regime_code=regime, issuer=UNSC)
            yield from self.relation(key, 'subject_to_sanctions_program', regime_key, locator)
            original = text(element, 'NAME_ORIGINAL_SCRIPT')
            if original:
                yield from self.once(key, 'sanctions_alias', {'name': original, 'alias_type': 'original script'}, locator)
            for alias in element.findall(kind + '_ALIAS'):
                name = text(alias, 'ALIAS_NAME')
                if name:
                    yield from self.once(key, 'sanctions_alias', {'name': name, 'alias_type': text(alias, 'QUALITY') or 'unspecified'}, locator)
            for value in element.findall('NATIONALITY/VALUE'):
                yield from self.country_by_name(key, 'nationality', text(value), locator)
            for tag, predicate in (('TITLE', 'Title'), ('DESIGNATION', 'Designation')):
                for value in element.findall(tag + '/VALUE'):
                    if text(value):
                        yield from self.once(key, 'sanctions_feature', {'type': predicate, 'value': text(value)}, locator)
            for address in element.findall(kind + '_ADDRESS'):
                parts = {child.tag.lower(): text(child) for child in address if text(child)}
                if not parts:
                    continue
                iso3 = iso3_from_name(parts.get('country'))
                if iso3:
                    parts['country_iso3'] = iso3
                    yield from self.country(key, 'located_in', iso3, locator)
                yield from self.once(key, 'address', parts, locator)
            for birth in element.findall('INDIVIDUAL_DATE_OF_BIRTH'):
                value = {child.tag.lower(): text(child) for child in birth if text(child)}
                if set(value) - {'type_of_date'}:
                    yield from self.once(key, 'birth_date', value, locator)
            for place in element.findall('INDIVIDUAL_PLACE_OF_BIRTH'):
                value = {child.tag.lower(): text(child) for child in place if text(child)}
                if value:
                    yield from self.once(key, 'place_of_birth', value, locator)
            for document in element.findall('INDIVIDUAL_DOCUMENT'):
                value = {child.tag.lower(): text(child) for child in document if text(child)}
                if value.get('number'):
                    value['scheme'] = value.pop('type_of_document', 'unspecified')
                    value['value'] = value.pop('number')
                    yield from self.once(key, 'identifier', value, locator)
            comment = text(element, 'COMMENTS1') or ''
            for mentioned in sorted(set(UN_REFERENCE.findall(comment)) - {reference}):
                yield from self.relation(key, 'mentioned_in_listing_narrative', 'un:sanctions:' + mentioned, locator,
                                         derivation='UN reference number cited in COMMENTS1; association type unspecified')
            listed = text(element, 'LISTED_ON')
            attributes = {'list': 'UN Security Council Consolidated List', 'regime': regime, 'reference_number': reference,
                          'date_semantics': 'LISTED_ON (original UN listing date)', 'narrative': short(comment)}
            if listed and re.fullmatch(r'\d{4}-\d{2}-\d{2}', listed[:10]):
                yield ev.event('sanctions_designation', listed[:10], [key, regime_key, UNSC], locator, ['un', reference], attributes=attributes)
            else:
                yield ev.claim(key, 'sanctions_listing', attributes, locator, identity=['un', reference])


UK_TYPES = {'individual': 'person', 'entity': 'organization', 'ship': 'vessel'}


class UK(Common):
    def run(self, shard):
        ev = self.ev
        for locator, row in iter_rows([shard], {'format': 'csv', 'skip_lines': 1, 'encoding': 'utf-8-sig'}):
            uid = (row.get('Unique ID') or '').strip()
            if not uid:
                continue
            key = 'uk:sanctions:' + uid
            name_type = (row.get('Name type') or '').strip().lower()
            full_name = ' '.join(p.strip() for p in [row.get(f'Name {i}') or '' for i in range(1, 7)] if p.strip())
            designation_type = (row.get('Designation Type') or '').strip()
            regime = (row.get('Regime Name') or '').strip() or 'unknown'
            regime_key = 'uk:regime:' + slug(re.sub(r'^The\s+|\s*\(EU Exit\)|\s*Regulations\s+\d{4}$', '', regime))
            yield from self.entity(UK_GOV, 'government_agency', 'UK FCDO / HM Treasury OFSI (UK Sanctions List)', locator, jurisdiction='GBR')
            primary = name_type == 'primary name'
            created = []
            if primary or not name_type:  # 14 designations publish a blank name type on their first row.
                created = self.entity(key, UK_TYPES.get(designation_type.lower(), 'organization'), full_name or key, locator,
                                      unique_id=uid, ofsi_group_id=row.get('OFSI Group ID') or None, designation_type=designation_type,
                                      list='UK Sanctions List', last_updated=dmy(row.get('Last Updated')),
                                      name_type_published=row.get('Name type') or None)
                yield from created
            if not primary and not created and full_name:
                strength = (row.get('Alias strength') or '').strip()
                yield from self.once(key, 'sanctions_alias', {'name': full_name, 'alias_type': name_type or 'alias', **({'quality': strength} if strength else {})}, locator)
            non_latin = (row.get('Name non-latin script') or '').strip()
            if non_latin:
                yield from self.once(key, 'sanctions_alias', {'name': non_latin, 'alias_type': 'non-latin script', 'script': row.get('Non-latin script type') or None}, locator)
            if not ev.keys.add('uk-designation|' + uid + '|' + regime):
                yield from self._row_details(key, row, locator)
                continue
            yield from self.entity(regime_key, 'regulation', regime, locator, regime_name=regime, issuer=UK_GOV, jurisdiction='GBR')
            yield from self.relation(key, 'subject_to_sanctions_program', regime_key, locator)
            un_reference = (row.get('UN Reference Number') or '').strip()
            if un_reference:
                yield from self.relation(key, 'same_designation_as', 'un:sanctions:' + un_reference, locator, derivation='UK list UN Reference Number')
            if row.get('OFSI Group ID'):
                yield from self.once(key, 'identifier', {'scheme': 'OFSI Group ID', 'value': row['OFSI Group ID'].strip()}, locator)
            attributes = {'list': 'UK Sanctions List', 'regime': regime, 'unique_id': uid,
                          'sanctions_imposed': [s for s in (row.get('Sanctions Imposed') or '').split('|') if s.strip()],
                          'designation_source': row.get('Designation source') or None, 'un_reference_number': un_reference or None,
                          'last_updated': dmy(row.get('Last Updated')), 'other_information': short(row.get('Other Information')),
                          'date_semantics': 'Date Designated (UK designation date)'}
            designated = dmy(row.get('Date Designated'))
            if designated and len(designated) == 10:
                yield ev.event('sanctions_designation', designated, [key, regime_key, UK_GOV], locator, ['uk', uid, regime], attributes=attributes)
            else:
                yield ev.claim(key, 'sanctions_listing', attributes, locator, identity=['uk', uid, regime])
            yield from self._row_details(key, row, locator)

    def _row_details(self, key, row, locator):
        get = lambda name: (row.get(name) or '').strip()
        lines = [get(f'Address Line {i}') for i in range(1, 7)]
        address = {f'line{i}': v for i, v in enumerate(lines, 1) if v}
        if get('Address Postal Code'):
            address['postal_code'] = get('Address Postal Code')
        if get('Address Country'):
            address['country'] = get('Address Country')
            iso3 = iso3_from_name(address['country'])
            if iso3:
                address['country_iso3'] = iso3
                yield from self.country(key, 'located_in', iso3, locator)
        if address:
            yield from self.once(key, 'address', address, locator)
        for name in split_numbered(get('Nationality(/ies)')):
            yield from self.country_by_name(key, 'nationality', name, locator)
        for value in split_numbered(get('D.O.B')):
            yield from self.once(key, 'birth_date', dmy(value) or value, locator)
        birth = {k: v for k, v in (('town', get('Town of birth')), ('country', get('Country of birth'))) if v}
        if birth:
            yield from self.once(key, 'place_of_birth', birth, locator)
        for column, scheme in (('Passport number', 'Passport'), ('National Identifier number', 'National Identifier'),
                               ('Business registration number (s)', 'Business Registration Number'), ('Hull identification number (HIN)', 'HIN')):
            for number in split_numbered(get(column)):
                value = {'scheme': scheme, 'value': number}
                info = get(column.replace(' number', ' additional information')) if 'number' in column else ''
                if info:
                    value['additional_information'] = short(info, 300)
                yield from self.once(key, 'identifier', value, locator)
        imo = re.search(r'(\d{7})', get('IMO number'))
        if imo:
            yield from self.once(key, 'identifier', {'scheme': 'IMO number', 'value': get('IMO number'), 'id': 'imo:' + imo[1]}, locator)
        flag = get('Current believed flag of ship')
        if flag:
            yield from self.country_by_name(key, 'flag_state', flag, locator)
        vessel = {k: get(c) for k, c in (('previous_flags', 'Previous flags'), ('type', 'Type of ship'), ('tonnage', 'Tonnage of ship'),
                                         ('length', 'Length of ship'), ('year_built', 'Year Built')) if get(c)}
        if vessel:
            yield from self.once(key, 'vessel_particulars', vessel, locator)
        for column, role in (('Current owner/operator (s)', 'current_owner_operator'), ('Previous owner/operator (s)', 'previous_owner_operator'),
                             ('Parent company', 'parent_company'), ('Subsidiaries', 'subsidiaries')):
            if get(column):
                yield from self.once(key, 'related_party_text', {'role': role, 'text': short(get(column), 1000), 'resolved': False}, locator)
        for column, kind in (('Title', 'Title'), ('Position', 'Position'), ('Gender', 'Gender'), ('Type of entity', 'Type of entity')):
            if get(column):
                yield from self.once(key, 'sanctions_feature', {'type': kind, 'value': short(get(column), 500)}, locator)
        for column in ('Phone number', 'Website', 'Email address'):
            if get(column):
                yield from self.once(key, 'contact', {'type': column, 'value': short(get(column), 500)}, locator)


CSL_TYPES = {'individual': 'person', 'entity': 'organization', 'vessel': 'vessel', 'aircraft': 'aircraft'}


def csl_identifier(chunk):
    scheme, _, rest = chunk.partition(', ')
    value = {'scheme': scheme.strip(), 'value': rest.strip()}
    match = re.fullmatch(r'(.*), ([A-Z]{2})', value['value'])
    if match and iso3_from_iso2(match[2]):
        value['value'], value['issuing_country'] = match[1], iso3_from_iso2(match[2])
    compact = value['value'].replace(' ', '').upper()
    if re.search(r'IMO(\d{7})', compact):
        value['id'] = 'imo:' + re.search(r'IMO(\d{7})', compact)[1]
    elif scheme in ('Legal Entity Number', 'LE Number') and re.fullmatch(r'[A-Z0-9]{18}[0-9]{2}', compact):
        value['id'] = 'lei:' + compact
    elif scheme == 'SWIFT/BIC':
        value['id'] = 'swift:' + compact
    elif scheme == 'ISIN':
        value['id'] = 'isin:' + compact
    elif scheme == 'MMSI' and compact.isdigit():
        value['id'] = 'mmsi:' + compact
    elif scheme.startswith('Digital Currency Address'):
        value['id'] = 'crypto:' + scheme.rsplit('-', 1)[-1].strip().lower() + ':' + value['value'].strip()
    return value


class CSL(Common):
    def run(self, shard):
        ev = self.ev
        for locator, row in iter_rows([shard], {'format': 'csv', 'encoding': 'utf-8-sig'}):
            get = lambda name: (row.get(name) or '').strip()
            if not get('_id'):
                continue
            key = 'us_csl:' + get('_id')
            source = get('source') or 'unknown'
            abbreviation = re.search(r'\(([^)]+)\)', source)
            list_key = 'us_csl:list:' + slug(abbreviation[1] if abbreviation else source)
            treasury = 'Treasury Department' in source
            record_type = get('type')
            yield from self.entity(list_key, 'regulation', source, locator, list_name=source, source_list_url=get('source_list_url') or None, jurisdiction='USA')
            yield from self.entity(key, CSL_TYPES.get(record_type.lower(), 'organization'), get('name') or key, locator,
                                   csl_id=get('_id'), source_list=source, record_type=record_type or 'unspecified',
                                   entity_number=get('entity_number') or None)
            yield from self.relation(key, 'listed_on', list_key, locator)
            if treasury and get('entity_number'):
                yield from self.relation(key, 'same_designation_as', 'ofac:party:' + get('entity_number'), locator,
                                         derivation='CSL entity_number equals OFAC Sanctions List Service profile ID')
            program_keys = []
            for program in [p.strip() for p in get('programs').split(';') if p.strip()]:
                if treasury:
                    pid = 'ofac:program:' + (re.sub(r'[^A-Za-z0-9]+', '-', program).strip('-').upper() or 'UNKNOWN')
                else:
                    pid = 'us_csl:program:' + slug(program)
                program_keys.append(pid)
                yield from self.entity(pid, 'regulation', program, locator, program_code=program, jurisdiction='USA')
                yield from self.relation(key, 'subject_to_sanctions_program', pid, locator)
            for alias in [a.strip() for a in get('alt_names').split(';') if a.strip()]:
                yield from self.once(key, 'sanctions_alias', {'name': alias, 'alias_type': 'alt_name'}, locator)
            for address in [a.strip() for a in get('addresses').split('; ') if a.strip()]:
                value = {'text': address}
                match = re.search(r',\s*([A-Z]{2})$', address)
                iso3 = iso3_from_iso2(match[1]) if match else None
                if iso3:
                    value['country_iso3'] = iso3
                    yield from self.country(key, 'located_in', iso3, locator)
                yield from self.once(key, 'address', value, locator)
            for column, predicate in (('nationalities', 'nationality'), ('citizenships', 'citizenship')):
                for code in [c.strip() for c in re.split(r'[;,]', get(column)) if c.strip()]:
                    iso3 = iso3_from_iso2(code) or iso3_from_name(code)
                    if iso3:
                        yield from self.country(key, predicate, iso3, locator)
                    else:
                        yield from self.once(key, predicate, {'country': code, 'unmapped_country': True}, locator)
            for chunk in [c.strip() for c in get('ids').split('; ') if c.strip()]:
                yield from self.once(key, 'identifier', csl_identifier(chunk), locator)
            for value in [v.strip() for v in get('dates_of_birth').split(';') if v.strip()]:
                yield from self.once(key, 'birth_date', value, locator)
            for value in [v.strip() for v in get('places_of_birth').split(';') if v.strip()]:
                yield from self.once(key, 'place_of_birth', value, locator)
            if get('vessel_flag'):
                flag = get('vessel_flag')
                flag_iso3 = iso3_from_iso2(flag) or iso3_from_name(flag)
                if flag_iso3:
                    yield from self.country(key, 'flag_state', flag_iso3, locator)
                else:
                    yield from self.once(key, 'flag_state', {'country': flag, 'unmapped_country': True}, locator)
            vessel = {k: get(k) for k in ('call_sign', 'vessel_type', 'gross_tonnage', 'gross_registered_tonnage', 'vessel_owner') if get(k)}
            if vessel:
                yield from self.once(key, 'vessel_particulars', vessel, locator)
            if get('title'):
                yield from self.once(key, 'sanctions_feature', {'type': 'Title', 'value': short(get('title'), 500)}, locator)
            attributes = {'list': source, 'programs': [p.strip() for p in get('programs').split(';') if p.strip()],
                          'federal_register_notice': get('federal_register_notice') or None, 'end_date': get('end_date') or None,
                          'license_requirement': short(get('license_requirement'), 300) or None, 'license_policy': short(get('license_policy'), 300) or None,
                          'standard_order': get('standard_order') or None, 'remarks': short(get('remarks'), 300) or None,
                          'date_semantics': 'CSL start_date (effective/listing date as published by the list owner)'}
            # The same CSL _id can appear once per source list (e.g. SSI and NS-MBS), so identities include the list.
            start = get('start_date')[:10]
            identity = ['csl', get('_id'), list_key, locator]
            if re.fullmatch(r'\d{4}-\d{2}-\d{2}', start):
                yield ev.event('sanctions_designation', start, [key, list_key, *program_keys], locator, identity, attributes=attributes)
            elif any(v for k, v in attributes.items() if k not in ('list', 'programs', 'date_semantics')):
                yield ev.claim(key, 'sanctions_listing', attributes, locator, identity=identity)
