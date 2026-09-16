"""FEC itemized individual contributions (indivYY.zip / itcont.txt) -> PII-free aggregates.

Raw rows name individual contributors (name, city, ZIP, employer, occupation). Normalized output keeps only
aggregates, never a contributor-level record:

* ``committee_state_month``: recipient ``fec:committee:C########`` x contributor state x calendar month
* ``zip3_month``: contributor ``geo:US:zip3:NNN`` x calendar month (all recipients)
* ``committee_occupation``: committee x occupation category (keyword proxy, see ``OCCUPATION_RULES``) x cycle
* ``committee_size_band``: committee x per-transaction amount band x cycle

Each aggregate is emitted as ``individual_contributions_amount`` (USD) and ``individual_contribution_count``
(contributions). Memo lines (MEMO_CD = X, e.g. conduit/earmark duplicates) are excluded from sums, as are
non-individual entity types. Transactions with an unusable date use the cycle window (month = null).
FEC use restriction (52 U.S.C. 30111(a)(4)): no solicitation or commercial use of contributor information.
Memory is bounded by the aggregate key space (committees x states x months), not by the row count.
"""
import io
import re
import zipfile

FIELD_COUNT = 21
CMTE, AMNDT, RPT_TP, PGI, IMAGE, TX_TP, ENTITY_TP, NAME, CITY, STATE, ZIP, EMPLOYER, OCCUPATION, TX_DT, TX_AMT, OTHER_ID, TRAN_ID, FILE_NUM, MEMO_CD, MEMO_TEXT, SUB_ID = range(21)
COMMITTEE = re.compile(r'C\d{8}')
INDIVIDUAL_TYPES = {'IND', 'CAN', ''}
SIZE_BANDS = ((200, 'under_200'), (500, '200_to_499'), (1000, '500_to_999'), (3300, '1000_to_3299'), (None, '3300_and_over'))
OCCUPATION_RULES = (
    ('not_reported', ('INFORMATION REQUESTED', 'REQUESTED', 'N/A', 'NONE GIVEN', 'REFUSED')),
    ('retired', ('RETIRED', 'RETIREE')),
    ('not_employed', ('NOT EMPLOYED', 'UNEMPLOYED', 'NONE')),
    ('homemaker', ('HOMEMAKER', 'HOUSEWIFE', 'MOTHER', 'PARENT', 'CAREGIVER')),
    ('student', ('STUDENT',)),
    ('legal', ('ATTORNEY', 'LAWYER', 'LEGAL', 'COUNSEL', 'JUDGE', 'PARALEGAL')),
    ('health', ('PHYSICIAN', 'DOCTOR', 'NURSE', 'DENTIST', 'MEDICAL', 'HEALTH', 'PHARMAC', 'SURGEON', 'THERAPIST', 'PSYCH', 'VETERINAR', 'RN')),
    ('finance', ('FINANC', 'INVEST', 'BANK', 'ACCOUNTANT', 'CPA', 'TRADER', 'INSURANCE', 'PRIVATE EQUITY', 'HEDGE')),
    ('engineering_science_tech', ('ENGINEER', 'SOFTWARE', 'DEVELOPER', 'PROGRAMMER', 'SCIENTIST', 'TECHNOLOG', 'DATA ', 'ANALYST', 'IT ')),
    ('education', ('TEACHER', 'PROFESSOR', 'EDUCAT', 'SCHOOL', 'LIBRARIAN', 'FACULTY', 'RESEARCHER')),
    ('real_estate_construction', ('REAL ESTATE', 'REALTOR', 'DEVELOPER', 'CONTRACTOR', 'CONSTRUCTION', 'BUILDER', 'ARCHITECT')),
    ('executive_owner_management', ('CEO', 'PRESIDENT', 'EXECUTIVE', 'OWNER', 'FOUNDER', 'MANAGER', 'DIRECTOR', 'PARTNER', 'CHAIRMAN', 'PRINCIPAL', 'BUSINESS', 'ENTREPRENEUR', 'OFFICER', 'VP')),
    ('consulting_sales_marketing', ('CONSULT', 'SALES', 'MARKETING', 'ADVERTIS', 'PUBLIC RELATIONS')),
    ('government_public_service', ('GOVERNMENT', 'PUBLIC SERV', 'MILITARY', 'POLICE', 'FIREFIGHTER', 'ELECTED', 'LEGISLAT', 'CIVIL SERV')),
    ('agriculture', ('FARM', 'RANCH', 'AGRICULT')),
    ('arts_media', ('WRITER', 'ARTIST', 'AUTHOR', 'ACTOR', 'MUSICIAN', 'PRODUCER', 'JOURNALIST', 'DESIGNER', 'PHOTOGRAPHER')),
    ('clergy_nonprofit', ('CLERGY', 'PASTOR', 'MINISTER', 'NONPROFIT', 'NON-PROFIT', 'SOCIAL WORKER')),
    ('trades_labor', ('ELECTRICIAN', 'PLUMBER', 'MECHANIC', 'DRIVER', 'TECHNICIAN', 'LABORER', 'CARPENTER', 'WELDER', 'UNION')),
)


def occupation_category(occupation, employer):
    text = ' ' + (occupation or '').upper().strip() + ' '
    if text.strip() == '':
        return 'not_reported'
    for category, needles in OCCUPATION_RULES:
        for needle in needles:
            if needle in text if len(needle) > 3 else f' {needle} ' in text:
                return category
    if 'SELF' in text or 'SELF' in (employer or '').upper():
        return 'self_employed'
    return 'other'


def size_band(amount):
    for limit, name in SIZE_BANDS:
        if limit is None or amount < limit:
            return name
    return SIZE_BANDS[-1][1]


def shard_cycle(shard):
    name = shard.get('name') or (shard.get('request') or {}).get('url', '').rsplit('/', 1)[-1]
    match = re.fullmatch(r'indiv(\d\d)\.zip', name)
    if not match:
        raise ValueError(f'Unrecognized FEC individual contributions shard: {name!r}')
    yy = int(match.group(1))
    return 1900 + yy if yy >= 76 else 2000 + yy


def month_of(value, cycle):
    text = (value or '').strip()
    if len(text) != 8 or not text.isdigit():
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


def clean(value):
    value = round(value, 2)
    return int(value) if float(value).is_integer() else value


def add(table, key, amount, line):
    entry = table.get(key)
    if entry is None:
        table[key] = [amount, 1, line]
    else:
        entry[0] += amount
        entry[1] += 1


def members(archive):
    names = [n for n in archive.namelist() if n.rsplit('/', 1)[-1].lower().startswith('itcont') and n.lower().endswith('.txt')]
    top = [n for n in names if '/' not in n]  # older archives also ship by_date/ splits of the same rows
    if not (top or names):
        raise ValueError('No itcont*.txt member in FEC individual contributions archive')
    return top or names


def run(context):
    dataset = context.definition['id']
    for index, ref in enumerate(context.raw_inputs):
        observed_default = context.raw_receipt(index)['retrieved_at']
        for shard in context.raw_shards(index):
            cycle = shard_cycle(shard)
            observed = shard.get('retrieved_at') or observed_default
            yield from shard_records(dataset, ref, shard, cycle, observed)


def shard_records(dataset, ref, shard, cycle, observed):
    by_state, by_zip3, by_occupation, by_band = {}, {}, {}, {}
    stats = {'rows': 0, 'memo_excluded': 0, 'non_individual_excluded': 0, 'malformed': 0}
    with zipfile.ZipFile(shard['path']) as archive:
        for member in members(archive):
            prefix = f'shard:{shard["index"]}/member:{member}/line:'
            with archive.open(member) as raw:
                for line_number, line in enumerate(io.TextIOWrapper(raw, encoding='latin-1', newline=''), 1):
                    fields = line.rstrip('\r\n').split('|')
                    if len(fields) != FIELD_COUNT:
                        stats['malformed'] += 1
                        continue
                    stats['rows'] += 1
                    if fields[MEMO_CD].strip().upper() == 'X':
                        stats['memo_excluded'] += 1
                        continue
                    if fields[ENTITY_TP].strip().upper() not in INDIVIDUAL_TYPES:
                        stats['non_individual_excluded'] += 1
                        continue
                    cmte = fields[CMTE].strip()
                    try:
                        amount = float(fields[TX_AMT])
                    except ValueError:
                        stats['malformed'] += 1
                        continue
                    if not COMMITTEE.fullmatch(cmte):
                        stats['malformed'] += 1
                        continue
                    locator = (member, line_number)
                    month = month_of(fields[TX_DT], cycle)
                    state = fields[STATE].strip().upper()[:2] or None
                    zip_code = fields[ZIP].strip()
                    add(by_state, (cmte, state, month), amount, locator)
                    if len(zip_code) >= 3 and zip_code[:3].isdigit():
                        add(by_zip3, (zip_code[:3], state, month), amount, locator)
                    add(by_occupation, (cmte, occupation_category(fields[OCCUPATION], fields[EMPLOYER])), amount, locator)
                    add(by_band, (cmte, size_band(abs(amount))), amount, locator)
    base_attributes = {'source_dataset': dataset, 'aggregate': True, 'cycle': cycle,
                       'exclusions': 'memo lines (MEMO_CD=X) and non-individual entity types; contributor identities are not retained'}

    def emit(family, key_id, subject, dims, window, entry, extra=None):
        amount, count, (member, line) = entry
        evidence = [{'input': ref, 'locator': f'shard:{shard["index"]}/member:{member}/line:{line}'}]
        attributes = {**base_attributes, 'aggregation': family, 'locator_basis': 'first raw row of the aggregate group', **(extra or {})}
        for metric, value, unit in (('individual_contributions_amount', clean(amount), 'USD'),
                                    ('individual_contribution_count', count, 'contributions')):
            yield {'kind': 'observation', 'id': f'{dataset}:{cycle}:{family}:{key_id}:{metric}', 'subject': subject, 'metric': metric,
                   'value': value, 'unit': unit, 'dimensions': {'cycle': cycle, 'aggregation': family, **dims}, **window,
                   'observed_at': observed, 'evidence': evidence, 'attributes': attributes}

    for (cmte, state, month), entry in sorted(by_state.items(), key=lambda item: (item[0][0], item[0][1] or '', item[0][2] or '')):
        yield from emit('committee_state_month', f'{cmte}:{state or "none"}:{month or "unknown"}', 'fec:committee:' + cmte,
                        {'contributor_state': state, 'month': month}, month_window(month, cycle), entry)
    for (zip3, state, month), entry in sorted(by_zip3.items(), key=lambda item: (item[0][0], item[0][1] or '', item[0][2] or '')):
        yield from emit('zip3_month', f'{zip3}:{state or "none"}:{month or "unknown"}', 'geo:US:zip3:' + zip3,
                        {'contributor_state': state, 'month': month}, month_window(month, cycle), entry,
                        {'geography_basis': 'first three digits of the contributor ZIP code as reported'})
    cycle_window = month_window(None, cycle)
    for (cmte, category), entry in sorted(by_occupation.items()):
        yield from emit('committee_occupation', f'{cmte}:{category}', 'fec:committee:' + cmte, {'occupation_category': category},
                        cycle_window, entry, {'category_basis': 'keyword proxy on self-reported occupation (OCCUPATION_RULES v1)'})
    for (cmte, band), entry in sorted(by_band.items()):
        yield from emit('committee_size_band', f'{cmte}:{band}', 'fec:committee:' + cmte, {'size_band': band}, cycle_window, entry,
                        {'band_basis': 'absolute amount of each itemized transaction'})
    if stats['rows'] == 0:
        raise ValueError(f'No individual contribution rows in shard {shard["index"]}')
