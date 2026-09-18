"""Census ASPEP: state and local government employment and payroll, per government unit and function.

Each raw shard is one year's *Individual Unit Files* archive, holding two fixed-width ASCII members:

* ``<yy>empid.txt`` — the directory of government units: name, unit type, Census region, county name,
  **FIPS state and FIPS county**, population/enrollment/activity code, school level and the unit's
  probability of selection.
* ``<yy>empst.txt`` — the data: for each unit and each item code (functional category), full-time and
  part-time employees and payroll, each with a data flag saying whether the value was reported or
  imputed. Payroll is the 31-day monthly equivalent for **March** of the survey year. Files through
  2016 also carry part-time hours and full-time-equivalent employees.

The 14-character legacy Individual Unit ID is the longitudinal key: state code, unit type code, county
code, unit identification number, supplement code and sub code. It is *not* a FIPS code; the FIPS state
and county published alongside it in the ID file are what join this dataset to `census_geography`,
`census_population`, `usaspending`, `openfema` and `mit_election_returns`.

Shards are processed newest year first so each unit's entity record carries its most recent published
name. Unit entities and their `within` containment are emitted once at the end of the run, dated by the
span of years the unit was actually observed in, rather than once per unit-year.
"""
import re

from worldmodel.raw_readers import iter_rows
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

#: (value slice, flag position, employment status, metric, unit) for the data file. Positions 1-70 are
#: identical in every published vintage; part-time hours and full-time equivalents appear only
#: through 2016, and the 2021+ layout reuses positions 75-80 for the New Individual Unit ID.
DATA_COLUMNS = (((20, 30), 31, 'full_time', 'government_employees', 'people'),
                ((32, 44), 45, 'full_time', 'government_payroll', 'USD'),
                ((46, 56), 57, 'part_time', 'government_employees', 'people'),
                ((58, 70), 71, 'part_time', 'government_payroll', 'USD'))
LEGACY_COLUMNS = (((72, 82), 83, 'part_time', 'government_part_time_hours', 'hours'),
                  ((84, 94), None, 'full_time_equivalent', 'government_employees', 'people'))

READER = {'format': 'csv', 'encoding': 'latin-1', 'delimiter': '\x01', 'quoting': 'none',
          'fieldnames': ['line'], 'strict': False}


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
        """Read ``<yy>empid.txt``: register units and emit their population/enrollment for this year."""
        config = {**READER, 'members': ['*empid.txt']}
        for locator, row in iter_rows([shard], config):
            line = row['line']
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
        """Read ``<yy>empst.txt``: one observation per unit, item code and measure."""
        config = {**READER, 'members': ['*empst.txt']}
        basis = 'census_of_governments' if year in CENSUS_YEARS else 'annual_sample'
        bounds = {'valid_from': f'{year:04d}-03-01', 'valid_to': f'{year:04d}-04-01'}
        for locator, row in iter_rows([shard], config):
            line = row['line']
            if len(line) < 70 or not line[:14].isdigit():
                continue
            unit_id = line[0:14]
            if unit_id[:2] == '00':
                continue  # national aggregate; see `directory`
            unit_type = line[2:3]
            item = line[17:20].strip() or '000'
            unit = self.units.get(unit_id)
            columns = DATA_COLUMNS + (LEGACY_COLUMNS if len(line) >= 94 else ())
            for (start, end), flag_at, status, metric, measure_unit in columns:
                value = _int(line[start:end])
                if value is None or (value == 0 and item != '000'):
                    continue
                flag, flag_class = _flag(line, flag_at)
                yield self.base(shard, index, 'observation', [metric, year, unit_id, item, status], locator,
                                subject=f'aspep:unit:{unit_id}', metric=metric, value=value, unit=measure_unit, **bounds,
                                dimensions={'function_code': item, 'function': FUNCTIONS.get(item),
                                            'employment_status': status, 'survey_year': year,
                                            'collection_basis': basis,
                                            'government_type': UNIT_TYPES.get(unit_type, ('unknown', 'unknown'))[0],
                                            'government_level': UNIT_TYPES.get(unit_type, ('unknown', 'unknown'))[1],
                                            'reference_period': 'march', 'frequency': 'annual'},
                                attributes={'source_dataset': DATASET, 'individual_unit_id': unit_id,
                                            'data_flag': flag, 'data_flag_class': flag_class,
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
