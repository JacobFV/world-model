"""Census ASPEP: state and local government employment and payroll, per government unit and function.

Each raw shard is one survey year's archive, holding two fixed-width ASCII members:

* ``<yy>empid`` — the directory of government units: name, unit type, Census region, county name,
  **FIPS state and FIPS county**, population/enrollment/activity code, school level and the unit's
  probability of selection. 206 characters through 2020, 213 from 2021 (which adds the New
  Individual Unit ID), with every field this pipeline reads at the same position in both.
* ``<yy>empst`` — the data: for each unit and each item code (functional category), full-time and
  part-time employees and payroll, part-time hours and full-time-equivalent employees. Payroll is
  the 31-day monthly equivalent for **March** of the survey year.

The packaging and the data layout both changed several times, and neither is announced in the file:

* Members are ``.txt`` (2014-2024), ``.dat`` inside a subdirectory (2012-2013) or a **nested ZIP**
  holding one ``.DAT`` (1993-2011). Census-of-governments years prefix the name with ``c``.
* The data record is 84 characters before 2007 and carries **no data flags**, so its payroll and
  part-time fields sit two positions earlier than the documented layout. Reading it with the
  documented positions yields plausible wrong numbers instead of an error, so the layout is chosen
  from the record width and then checked against the bytes; an unknown width raises.

The 14-character legacy Individual Unit ID is the longitudinal key: state code, unit type code, county
code, unit identification number, supplement code and sub code. It is *not* a FIPS code; the FIPS state
and county published alongside it in the ID file are what join this dataset to `census_geography`,
`census_population`, `usaspending`, `openfema` and `mit_election_returns`.

Shards are processed newest year first so each unit's entity record carries its most recent published
name. Unit entities and their `within` containment are emitted once at the end of the run, dated by the
span of years the unit was actually observed in, rather than once per unit-year.
"""
import io
import os
import re
import zipfile
from collections import Counter

from worldmodel.util import digest

DATASET = 'census_aspep'
SHARD = re.compile(r'^aspep_(\d{4})\.zip$')

#: Years in which the Census of Governments enumerates *every* government unit. Every other year is a
#: probability sample of local governments (state governments are always fully enumerated), so a
#: non-census year's unit list is a sample and its `probability_of_selection` is below 1.
CENSUS_YEARS = frozenset((1957, 1962, 1967, 1972, 1977, 1982, 1987, 1992, 1997, 2002, 2007, 2012, 2017, 2022))

UNIT_TYPES = {'0': ('state_government', 'state'), '1': ('county_government', 'local'),
              '2': ('municipal_government', 'local'), '3': ('township_government', 'local'),
              '4': ('special_district', 'local'), '5': ('school_district', 'local')}

#: Item Code (Functional Category), section 2.3 of the technical documentation. Codes absent from this
#: table keep their raw value and a null description rather than failing a build.
FUNCTIONS = {
    '000': 'Total - All Government Employment Functions', '001': 'Air Transportation',
    '005': 'Corrections', '012': 'Education - Elementary and Secondary Instructional',
    '016': 'Education - Higher Education Other', '018': 'Education - Higher Education Instructional',
    '021': 'Education - Other', '022': 'Social Insurance Administration', '023': 'Financial Administration',
    '024': 'Fire Protection - Firefighters', '025': 'Judicial and Legal', '029': 'Other Government Administration',
    '032': 'Health', '040': 'Hospitals', '044': 'Highways', '050': 'Housing and Community Development',
    '052': 'Libraries', '059': 'Natural Resources', '061': 'Parks and Recreation', '062': 'Police Protection - Persons with Power of Arrest',
    '079': 'Public Welfare', '080': 'Sewerage', '081': 'Solid Waste Management', '087': 'Sea and Inland Port Facilities',
    '089': 'All other and unallocable', '090': 'State liquor stores', '091': 'Water Supply', '092': 'Electric Power',
    '093': 'Gas Supply', '094': 'Transit', '112': 'Education - Elementary and Secondary Other',
    '124': 'Fire Protection - Other', '162': 'Police Protection - Other',
}

SCHOOL_LEVELS = {'01': 'elementary_only', '02': 'secondary_only', '03': 'elementary_and_secondary',
                 '04': 'post_secondary', '05': 'special_or_vocational', '06': 'non_operating',
                 '07': 'education_service_agency'}

REGIONS = {'1': 'Northeast', '2': 'Midwest', '3': 'South', '4': 'West'}

#: Section 2.4 of the technical documentation. A flag outside both sets is recorded as `unknown`.
REPORTED_FLAGS = frozenset('CKRTUVZ')
IMPUTED_FLAGS = frozenset('ABDGJPQX')

#: The data file has two layouts, and they are **not** the same columns with a different tail.
#:
#: `flagged` is the one the 2014 and 2023 technical documentation describe: each value is followed by
#: a one-character data flag. Acquired record widths using it are 72, 80, 94 and 96 characters.
#:
#: `unflagged` is the pre-2007 layout. It publishes no data flags at all, so every payroll and
#: part-time field sits **two positions earlier**. Reading it with the documented positions does not
#: fail loudly — the straddled slices still parse as integers for most rows — it silently returns
#: wrong numbers. The only acquired width using it is 84 characters.
#:
#: Each entry is (value slice, flag position or None, employment status, metric, unit).
FLAGGED_COLUMNS = (((20, 30), 31, 'full_time', 'government_employees', 'people'),
                   ((32, 44), 45, 'full_time', 'government_payroll', 'USD'),
                   ((46, 56), 57, 'part_time', 'government_employees', 'people'),
                   ((58, 70), 71, 'part_time', 'government_payroll', 'USD'),
                   ((72, 82), 83, 'part_time', 'government_part_time_hours', 'hours'),
                   ((84, 94), None, 'full_time_equivalent', 'government_employees', 'people'))
UNFLAGGED_COLUMNS = (((20, 30), None, 'full_time', 'government_employees', 'people'),
                     ((30, 42), None, 'full_time', 'government_payroll', 'USD'),
                     ((42, 52), None, 'part_time', 'government_employees', 'people'),
                     ((52, 64), None, 'part_time', 'government_payroll', 'USD'),
                     ((64, 74), None, 'part_time', 'government_part_time_hours', 'hours'),
                     ((74, 84), None, 'full_time_equivalent', 'government_employees', 'people'))
#: Modal data-record width -> layout. An unseen width raises rather than being parsed on a guess.
DATA_LAYOUTS = {72: 'flagged', 80: 'flagged', 94: 'flagged', 95: 'flagged', 96: 'flagged', 84: 'unflagged'}

#: Members inside a year's archive. Census-of-governments years prefix the name with `c`, the
#: extension is `.txt`, `.dat` or a nested `.zip`, and 2012-2015 bury them in a subdirectory.
ID_MEMBER = re.compile(r'^\d\dc?empid\.(txt|dat|zip)$', re.I)
DATA_MEMBER = re.compile(r'^\d\dc?empst\.(txt|dat|zip)$', re.I)


def run(context):
    for index, _ in enumerate(context.raw_inputs):
        coverage = context.raw_coverage(index)
        if coverage['sampled'] or coverage['layout'] != 'shards':
            raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire {DATASET})')
    builder = Aspep(context)
    shards = []
    for index, _ in enumerate(context.raw_inputs):
        for shard in context.raw_shards(index):
            match = SHARD.match(_name(shard))
            if not match:
                raise ValueError(f'{DATASET}: unrecognized shard {_name(shard)!r}; expected aspep_<year>.zip')
            shards.append((int(match.group(1)), index, shard))
    for year, index, shard in sorted(shards, reverse=True):
        yield from builder.directory(index, shard, year)
        yield from builder.data(index, shard, year)
    yield from builder.registry()


def _name(shard):
    request = shard.get('request') or {}
    return shard.get('name') or (request.get('url') or '').split('?')[0].rsplit('/', 1)[-1]


def _int(text):
    text = text.strip()
    if not text or not text.lstrip('-').isdigit():
        return None
    return int(text)


def _county_fips(unit):
    """The 5-digit county FIPS, or None where the ID file publishes no county for this unit."""
    state = (unit or {}).get('fips_state') or ''
    county = (unit or {}).get('fips_county') or ''
    if not (state.isdigit() and state != '00' and county.isdigit() and county != '000'):
        return None
    return state + county


def _flag(line, position):
    if position is None or position >= len(line):
        return None, None
    flag = line[position:position + 1].strip().upper()
    if not flag:
        return None, None
    return flag, ('reported' if flag in REPORTED_FLAGS else 'imputed' if flag in IMPUTED_FLAGS else 'unknown')


def _member(shard, pattern):
    """Read one member of a year's archive, descending a single nested ZIP level when present.

    Returns ``(lines, locator)``. The locator names the outer member, and a nested member as
    ``outer.zip!INNER.DAT``, so a record's evidence points at the exact file its bytes came from.
    A nested archive cannot be streamed, so its member is buffered; the largest acquired one is the
    2012 census data file at roughly 60 MB.
    """
    with zipfile.ZipFile(shard['path']) as archive:
        names = sorted(archive.namelist())
        matches = [name for name in names if pattern.match(os.path.basename(name))]
        if not matches:
            raise ValueError(f'{DATASET}: {_name(shard)} has no member matching {pattern.pattern}; '
                             f'members {[os.path.basename(n) for n in names]}')
        name = matches[0]
        payload = archive.read(name)
        locator = name
        if os.path.basename(name).lower().endswith('.zip'):
            with zipfile.ZipFile(io.BytesIO(payload)) as inner:
                inner_names = sorted(inner.namelist())
                if len(inner_names) != 1:
                    raise ValueError(f'{DATASET}: nested {name} holds {inner_names}, expected one member')
                payload = inner.read(inner_names[0])
                locator = f'{name}!{inner_names[0]}'
    return payload.decode('latin-1').splitlines(), locator


def _data_layout(shard, lines):
    """Pick the data-file layout from the modal record width, then check the choice against the bytes.

    The width alone decides, so the choice is deterministic. The check exists because reading the
    unflagged layout with the documented positions produces plausible wrong numbers rather than an
    error: a flagged file must carry letters where the flags belong, and an unflagged file must not.
    """
    widths = Counter(len(line) for line in lines if line.strip())
    if not widths:
        raise ValueError(f'{DATASET}: {_name(shard)} data member is empty')
    width = widths.most_common(1)[0][0]
    layout = DATA_LAYOUTS.get(width)
    if layout is None:
        raise ValueError(f'{DATASET}: {_name(shard)} data records are {width} characters wide, which matches no '
                         f'documented ASPEP layout ({sorted(DATA_LAYOUTS)}); refusing to guess its columns')
    alphabetic = sampled = 0
    for line in lines[:20000]:
        if len(line) < 72 or line[:2] == '00':
            continue
        sampled += 1
        alphabetic += sum(line[position].isalpha() for position in (31, 45, 57, 71))
    share = alphabetic / max(4 * sampled, 1)
    if layout == 'flagged' and sampled and share < 0.5:
        raise ValueError(f'{DATASET}: {_name(shard)} is {width} characters, which the layout table calls flagged, '
                         f'but only {share:.1%} of flag positions hold a letter')
    if layout == 'unflagged' and sampled and share > 0.05:
        raise ValueError(f'{DATASET}: {_name(shard)} is {width} characters, which the layout table calls unflagged, '
                         f'but {share:.1%} of the documented flag positions hold a letter')
    return layout, width


def _reference_year(two_digit, file_year):
    """Resolve the ID file's two-digit population/enrollment year against the survey year."""
    value = _int(two_digit)
    if value is None:
        return None
    candidate = file_year - file_year % 100 + value
    return candidate - 100 if candidate > file_year else candidate


class Aspep:
    def __init__(self, context):
        self.context = context
        #: unit_id -> unit record. One entry per government unit across the whole build.
        self.units = {}
        #: geography key -> (entity_type, label, locator, index, parent)
        self.places = {}
        self.retrieved = {}

    def stamp(self, index):
        if index not in self.retrieved:
            self.retrieved[index] = self.context.raw_receipt(index)['retrieved_at']
        return {'retrieved_at': self.retrieved[index]}

    # ----- record helpers -------------------------------------------------------------------------
    def base(self, shard, index, kind, identity, locator, **fields):
        return {'kind': kind, 'id': f'{DATASET}:{kind}:' + digest(identity), 'observed_at': shard['retrieved_at'],
                'evidence': self.context.raw_evidence(locator, index), **fields}

    def place(self, key, entity_type, label, locator, index, parent):
        self.places.setdefault(key, (entity_type, label, locator, index, parent))
        return key

    def geography(self, unit, locator, index):
        """The deterministic geography join: FIPS state and FIPS county published in the ID file."""
        state, county = unit['fips_state'], unit['fips_county']
        if not (state and state.isdigit() and state != '00'):
            return None, None
        self.place('geo:US', 'country', 'United States', locator, index, None)
        state_key = self.place(f'geo:US:state:{state}', 'state', f'US state FIPS {state}', locator, index, 'geo:US')
        if county and county.isdigit() and county != '000':
            name = unit['county_name'] or f'US county FIPS {state}{county}'
            return state_key, self.place(f'geo:US:county:{state}{county}', 'county', name + f' County ({state}{county})',
                                         locator, index, state_key)
        return state_key, None

    # ----- the unit directory ---------------------------------------------------------------------
    def directory(self, index, shard, year):
        """Read the unit ID member: register units and emit their population/enrollment for this year."""
        lines, member = _member(shard, ID_MEMBER)
        for number, line in enumerate(lines, start=1):
            locator = f'shard:{shard["index"]}/member:{member}/line:{number}'
            if len(line) < 114 or not line[:14].isdigit():
                continue
            unit_id = line[0:14]
            unit_type = line[2:3]
            if unit_id[:2] == '00':
                # State code 00 is the national aggregate row. It carries no employment data, has no
                # FIPS geography, and is not a government unit. Its unit type code has also moved:
                # 2023 publishes 00000000000000, 2014 publishes 00600000000000.
                continue
            entry = {'unit_id': unit_id, 'unit_type': unit_type, 'name': line[14:78].strip(),
                     'region': line[78:79].strip(), 'county_name': line[79:109].strip(),
                     'fips_state': line[109:111].strip(), 'fips_county': line[111:114].strip(),
                     'school_level': line[136:138].strip(), 'new_unit_id': line[207:213].strip() or None,
                     'activity_code': line[125:134].strip() if unit_type == '4' else None}
            existing = self.units.get(unit_id)
            if existing is None:
                self.units[unit_id] = existing = {**entry, 'locator': locator, 'index': index,
                                                  'first_year': year, 'last_year': year, 'years': 1,
                                                  'geographies': {}, 'activity_code_year': year if entry['activity_code'] else None}
            else:
                existing['first_year'] = min(existing['first_year'], year)
                existing['last_year'] = max(existing['last_year'], year)
                existing['years'] += 1
                for key in ('fips_state', 'fips_county', 'county_name'):
                    existing[key] = existing[key] or entry[key]
                if not existing['activity_code'] and entry['activity_code']:
                    existing['activity_code'], existing['activity_code_year'] = entry['activity_code'], year
            state_key, county_key = self.geography(entry, locator, index)
            place = county_key or state_key
            if place:
                span = existing['geographies'].setdefault(place, [year, year, locator, index])
                span[0], span[1] = min(span[0], year), max(span[1], year)
            selection = line[145:151].strip()
            yield from self._unit_year(shard, index, locator, unit_id, unit_type, line, year, selection)

    def _unit_year(self, shard, index, locator, unit_id, unit_type, line, year, selection):
        """Population, enrollment or special-district activity code as published for this survey year."""
        subject = f'aspep:unit:{unit_id}'
        raw = line[125:134].strip()
        basis = 'census_of_governments' if year in CENSUS_YEARS else 'annual_sample'
        probability = None
        try:
            probability = float(selection) if selection else None
        except ValueError:
            probability = None
        common = {'dimensions': {'survey_year': year, 'collection_basis': basis,
                                 'government_type': UNIT_TYPES.get(unit_type, ('unknown', 'unknown'))[0]},
                  'valid_from': f'{year:04d}-03-01', 'valid_to': f'{year:04d}-04-01'}
        attributes = {'source_dataset': DATASET, 'individual_unit_id': unit_id,
                      'probability_of_selection': probability, 'unit_enumerated_not_sampled': year in CENSUS_YEARS}
        if unit_type == '4':
            return  # special districts publish an activity code here, not a count; kept on the unit entity
        value = _int(raw)
        if value is None:
            return
        metric = 'school_enrollment' if unit_type == '5' else 'government_unit_population'
        reference = _reference_year(line[134:136], year)
        yield self.base(shard, index, 'observation', [metric, year, unit_id], locator, subject=subject, metric=metric,
                        value=value, unit='people',
                        dimensions={**common['dimensions'], 'reference_year': reference,
                                    'school_level': SCHOOL_LEVELS.get(line[136:138].strip())},
                        valid_from=common['valid_from'], valid_to=common['valid_to'],
                        attributes={**attributes, 'published_field': 'Population/Enrollment/Function Code',
                                    'semantics': 'population or enrollment the Census Bureau attached to the unit, not a survey response'})

    # ----- the employment and payroll data --------------------------------------------------------
    def data(self, index, shard, year):
        """Read the unit data member: one observation per unit, item code and measure.

        A few years print the same unit and item code on two lines with different values — 14 pairs in
        1995 and one in 1999, measured across the 31 acquired years. They are components of one
        unit-function cell, not competing estimates of it, so they are summed and the merge is recorded
        in `attributes.source_rows_merged`. Emitting them separately would leave two observations with
        identical subject, metric and dimensions, which belief materialization reads as a conflict.
        """
        lines, member = _member(shard, DATA_MEMBER)
        layout, width = _data_layout(shard, lines)
        table = FLAGGED_COLUMNS if layout == 'flagged' else UNFLAGGED_COLUMNS
        basis = 'census_of_governments' if year in CENSUS_YEARS else 'annual_sample'
        bounds = {'valid_from': f'{year:04d}-03-01', 'valid_to': f'{year:04d}-04-01'}
        cells = {}
        order = []
        for number, line in enumerate(lines, start=1):
            if len(line) < 64 or not line[:14].isdigit() or line[:2] == '00':
                continue  # short rows and the national aggregate; see `directory`
            unit_id = line[0:14]
            item = line[17:20].strip() or '000'
            key = (unit_id, item)
            cell = cells.get(key)
            if cell is None:
                cells[key] = cell = {'unit_type': line[2:3], 'line': number, 'rows': 0, 'values': {}}
                order.append(key)
            cell['rows'] += 1
            for (start, end), flag_at, status, metric, measure_unit in table:
                if end > len(line):
                    continue
                value = _int(line[start:end])
                if value is None:
                    continue
                flag, flag_class = _flag(line, flag_at)
                current = cell['values'].get((metric, status))
                if current is None:
                    cell['values'][(metric, status)] = [value, measure_unit, flag, flag_class]
                else:
                    current[0] += value
        for key in order:
            unit_id, item = key
            cell = cells[key]
            unit = self.units.get(unit_id)
            kind, level = UNIT_TYPES.get(cell['unit_type'], ('unknown', 'unknown'))
            locator = f'shard:{shard["index"]}/member:{member}/line:{cell["line"]}'
            for (metric, status), (value, measure_unit, flag, flag_class) in cell['values'].items():
                if value == 0 and item != '000':
                    continue
                yield self.base(shard, index, 'observation', [metric, year, unit_id, item, status], locator,
                                subject=f'aspep:unit:{unit_id}', metric=metric, value=value, unit=measure_unit, **bounds,
                                dimensions={'function_code': item, 'function': FUNCTIONS.get(item),
                                            'employment_status': status, 'survey_year': year,
                                            'collection_basis': basis, 'government_type': kind,
                                            'government_level': level,
                                            'reference_period': 'march', 'frequency': 'annual'},
                                attributes={'source_dataset': DATASET, 'individual_unit_id': unit_id,
                                            'data_flag': flag, 'data_flag_class': flag_class,
                                            'data_flags_published': layout == 'flagged',
                                            'record_layout': f'{layout}_{width}_character',
                                            'source_rows_merged': cell['rows'],
                                            'payroll_basis': '31_day_monthly_equivalent_for_march' if measure_unit == 'USD' else None,
                                            'zero_values_omitted_except_total': True,
                                            'fips_state': (unit or {}).get('fips_state') or None,
                                            'fips_county': _county_fips(unit),
                                            'unit_in_directory': unit is not None})

    # ----- units and containment, emitted once ----------------------------------------------------
    def registry(self):
        for key in sorted(self.places):
            entity_type, label, locator, index, parent = self.places[key]
            shard = self.stamp(index)
            yield self.base(shard, index, 'entity', ['place', key], locator, entity_id=key, entity_type=entity_type,
                            label=label, attributes={'source_dataset': DATASET, 'code_system': 'FIPS'})
            if parent:
                yield self.base(shard, index, 'assertion', ['place_within', key], locator, subject=key,
                                predicate='within', object=parent, attributes={'source_dataset': DATASET})
        for unit_id in sorted(self.units):
            unit = self.units[unit_id]
            index, locator = unit['index'], unit['locator']
            shard = self.stamp(index)
            kind, level = UNIT_TYPES.get(unit['unit_type'], ('unknown', 'unknown'))
            yield self.base(shard, index, 'entity', ['unit', unit_id], locator, entity_id=f'aspep:unit:{unit_id}',
                            entity_type='government_agency', label=unit['name'] or f'Government unit {unit_id}',
                            attributes={'source_dataset': DATASET, 'individual_unit_id': unit_id,
                                        'new_individual_unit_id': unit['new_unit_id'],
                                        'government_type': kind, 'government_level': level,
                                        'census_region': REGIONS.get(unit['region']),
                                        'school_level': SCHOOL_LEVELS.get(unit['school_level']),
                                        'fips_state': unit['fips_state'] or None,
                                        'fips_county': _county_fips(unit),
                                        'county_name': unit['county_name'] or None,
                                        'special_district_activity_code': unit['activity_code'] or None,
                                        'special_district_activity_code_year': unit['activity_code_year'],
                                        'first_survey_year': unit['first_year'], 'last_survey_year': unit['last_year'],
                                        'survey_years_present': unit['years'],
                                        'label_from_survey_year': unit['last_year'],
                                        'id_system': 'Census Bureau 14-character Individual Unit ID '
                                                     '(state, unit type, county, unit number, supplement, sub)'})
            for place in sorted(unit['geographies']):
                first, last, place_locator, place_index = unit['geographies'][place]
                yield self.base(self.stamp(place_index), place_index,
                                'assertion', ['unit_within', unit_id, place], place_locator,
                                subject=f'aspep:unit:{unit_id}', predicate='within', object=place,
                                valid_from=f'{first:04d}-03-01', valid_to=f'{last + 1:04d}-03-01',
                                attributes={'source_dataset': DATASET,
                                            'join_basis': 'FIPS state and county published in the ASPEP Individual Unit ID file',
                                            'first_survey_year': first, 'last_survey_year': last})
