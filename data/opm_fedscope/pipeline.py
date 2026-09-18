"""OPM FedScope: the federal civilian workforce, aggregated from de-identified employee records.

Each raw shard is one FedScope cube archive. A cube holds one fact file with **one row per employee**
(or per accession/separation action) and a set of `DT*.txt` dimension tables that decode its codes.
The fact rows carry agency, duty location, occupation, pay plan and grade, age band, education level,
length of service, supervisory status, type of appointment, work schedule and **annual basic pay** —
and no name, no date of birth, no duty address and no identifier of any kind. OPM's published cube is
the de-identified product; named federal salary rosters come from FOIA aggregators, not from OPM, and
are out of scope for this dataset.

Person-level rows are therefore not a rights problem here. They are a *volume* problem: 63 employment
cubes hold about 137 million employee rows, and re-emitting each as a record would produce an artifact
two orders of magnitude larger than the source for no analytical gain. The pipeline instead aggregates
each cube while streaming into five marginal cells, which is the shape that joins to the rest of the
catalog:

* `agency_location` — agency sub-element by duty state, the geographic join
* `agency_occupation` — agency sub-element by OPM occupational series
* `agency_pay_grade` — agency sub-element by pay plan and grade
* `agency` and `location` — the two totals

Every cell carries the headcount, the sum of published annual salaries, and the number of employees
whose salary was published, so a mean is computable without inventing one: roughly 12.6% of rows in
the June 2022 cube have a blank salary.

The duty location published in the public cube is a **state**, not a county: `DTloc.txt` codes are
two-digit FIPS state codes for the 50 states and DC, and two-letter FIPS 10-4 codes for territories and
foreign countries. Only the numeric state codes are joined to `geo:US:state:<FIPS>`. Territories and
foreign duty stations get their own `opm:duty_location:<code>` entities rather than a guessed FIPS or
ISO code, because neither OPM nor this catalog publishes that crosswalk. FedScope agency codes are OPM
codes, not the Treasury CGAC codes `usaspending` uses, and no published crosswalk between them ships
with either source, so no identity link to `usgov:agency:<CGAC>` is asserted.
"""
import csv
import io
import re
import zipfile

from worldmodel.util import digest

DATASET = 'opm_fedscope'
SHARD = re.compile(r'^fedscope_(employment)_(\d{4})(\d{2})\.zip$|^fedscope_(accessions|separations)_fy(\d{4})_fy(\d{4})\.zip$')
FACT = re.compile(r'^(FACTDATA|ACCDATA|SEPDATA)[^/]*\.TXT$', re.I)
MONEY = re.compile(r'[$,\s]')

#: cube kind -> (count metric, salary-total metric, salary-denominator metric, action column)
METRICS = {'employment': ('federal_employees', 'federal_annual_salary_total', 'federal_employees_with_published_salary', None),
           'accessions': ('federal_accessions', 'federal_accession_salary_total', 'federal_accessions_with_published_salary', 'ACC'),
           'separations': ('federal_separations', 'federal_separation_salary_total', 'federal_separations_with_published_salary', 'SEP')}

#: cell family -> the fact columns whose values form the cell key
FAMILIES = (('agency_location', ('AGYSUB', 'LOC')), ('agency_occupation', ('AGYSUB', 'OCC')),
            ('agency_pay_grade', ('AGYSUB', 'PPGRD')), ('agency', ('AGYSUB',)), ('location', ('LOC',)))
#: flow cubes span five fiscal years, so their cells are keyed by fiscal year as well and drop the
#: pay-grade family, which is dominated by single-action cells.
FLOW_FAMILIES = (('agency_location', ('AGYSUB', 'LOC')), ('agency_occupation', ('AGYSUB', 'OCC')),
                 ('agency_action', ('AGYSUB', 'ACTION')), ('agency', ('AGYSUB',)), ('location', ('LOC',)))

MONTHS = ('JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC')


def run(context):
    for index, _ in enumerate(context.raw_inputs):
        coverage = context.raw_coverage(index)
        if coverage['sampled'] or coverage['layout'] != 'shards':
            raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire {DATASET})')
    builder = FedScope(context)
    shards = []
    for index, _ in enumerate(context.raw_inputs):
        for shard in context.raw_shards(index):
            name = _name(shard)
            match = SHARD.match(name)
            if not match:
                raise ValueError(f'{DATASET}: unrecognized shard {name!r}')
            employment, year, month, flow, first, last = match.groups()
            if employment:
                shards.append((f'{year}{month}', 'employment', index, shard))
            else:
                shards.append((f'{last}-{flow}', flow, index, shard))
    for _, kind, index, shard in sorted(shards, reverse=True):
        yield from builder.cube(index, shard, kind)
    yield from builder.registry()


def _name(shard):
    request = shard.get('request') or {}
    return shard.get('name') or (request.get('url') or '').split('?')[0].rsplit('/', 1)[-1]


def _money(text):
    text = MONEY.sub('', text or '')
    if not text or not text.replace('.', '', 1).isdigit():
        return None
    return float(text)


def _dimension(archive, member, key):
    """Read one `DT*.txt` dimension table into {code: row}, keyed by `key`. Absent tables are empty."""
    try:
        with archive.open(member) as handle:
            rows = list(csv.DictReader(io.TextIOWrapper(handle, encoding='latin-1', newline='')))
    except KeyError:
        return {}
    return {row[key]: row for row in rows if row.get(key)}


def _period(code):
    """`YYYYMM` -> half-open month interval."""
    year, month = int(code[:4]), int(code[4:6])
    end = (year + 1, 1) if month == 12 else (year, month + 1)
    return f'{year:04d}-{month:02d}-01', f'{end[0]:04d}-{end[1]:02d}-01'


def _fiscal_year(label):
    """`FY 2020` -> the federal fiscal year, 2019-10-01 to 2020-10-01. Unparseable labels stay undated."""
    match = re.search(r'(\d{4})', label or '')
    if not match:
        return None
    year = int(match.group(1))
    return f'{year - 1:04d}-10-01', f'{year:04d}-10-01'


class FedScope:
    def __init__(self, context):
        self.context = context
        self.agencies = {}      # AGYSUB -> (label, parent code, parent label, agency type)
        self.occupations = {}   # OCC -> (label, family label, collar)
        self.locations = {}     # LOC -> (label, location type)
        self.grades = {}        # PPGRD -> (pay plan, pay plan label, group label)
        self.anchor = None      # (shard index, locator) of the first cube read, for the code tables

    def base(self, shard, index, kind, identity, locator, **fields):
        return {'kind': kind, 'id': f'{DATASET}:{kind}:' + digest(identity), 'observed_at': shard['retrieved_at'],
                'evidence': self.context.raw_evidence(locator, index), **fields}

    # ----- one cube -------------------------------------------------------------------------------
    def cube(self, index, shard, kind):
        count_metric, salary_metric, denominator_metric, action = METRICS[kind]
        families = FAMILIES if kind == 'employment' else FLOW_FAMILIES
        with zipfile.ZipFile(shard['path']) as archive:
            names = archive.namelist()
            fact = next((n for n in names if FACT.match(n.rsplit('/', 1)[-1])), None)
            if fact is None:
                raise ValueError(f'{DATASET}: {_name(shard)} has no fact file; members {sorted(names)}')
            self._dimensions(archive)
            periods = {row['EFDATE']: row for row in _dimension(archive, 'DTefdate.txt', 'EFDATE').values()} \
                if 'DTefdate.txt' in names else {}
            actions = _dimension(archive, f'DT{(action or "").lower()}.txt', action) if action else {}
            cells, rows, dropped = {}, 0, 0
            member = fact.rsplit('/', 1)[-1]
            with archive.open(fact) as handle:
                reader = csv.DictReader(io.TextIOWrapper(handle, encoding='latin-1', newline=''))
                columns = reader.fieldnames or []
                count_column = 'EMPLOYMENT' if 'EMPLOYMENT' in columns else 'COUNT'
                date_column = 'DATECODE' if 'DATECODE' in columns else 'EFDATE'
                if count_column not in columns or date_column not in columns:
                    raise ValueError(f'{DATASET}: {member} lacks {count_column}/{date_column}; columns {columns}')
                for row in reader:
                    rows += 1
                    people = row.get(count_column) or ''
                    people = int(people) if people.strip().isdigit() else 0
                    if not people:
                        dropped += 1
                        continue
                    salary = _money(row.get('SALARY'))
                    stamp = (row.get(date_column) or '').strip()
                    if kind == 'employment':
                        period = stamp
                    else:
                        period = (periods.get(stamp, {}).get('FYT') or '').strip() or f'EFDATE {stamp}'
                    if action:
                        row = {**row, 'ACTION': row.get(action) or ''}
                    for family, keys in families:
                        cell = cells.setdefault((family, period) + tuple((row.get(k) or '').strip() for k in keys),
                                                [0, 0.0, 0])
                        cell[0] += people
                        if salary is not None:
                            cell[1] += salary * people
                            cell[2] += people
            locator = f'shard:{shard["index"]}/member:{member}'
            if self.anchor is None:
                self.anchor = (index, shard['retrieved_at'], locator)
            for key in sorted(cells):
                family, period = key[0], key[1]
                values = dict(zip(dict(families)[family], key[2:]))
                subject, dimensions = self._subject(family, values, actions)
                if subject is None:
                    continue
                count, total, with_salary = cells[key]
                shared = {'dimensions': {**dimensions, 'cell': family, 'cube': kind,
                                         'period_label': period,
                                         'frequency': 'quarterly_snapshot' if kind == 'employment' else 'fiscal_year'},
                          'attributes': {'source_dataset': DATASET, 'fact_file': member,
                                         'rows_in_cube': rows, 'rows_without_headcount': dropped,
                                         'aggregation_method': 'sum_over_de_identified_employee_records',
                                         'person_level_rows_not_retained': True}}
                bounds = _period(period) if kind == 'employment' else _fiscal_year(period)
                if bounds:
                    shared.update(zip(('valid_from', 'valid_to'), bounds))
                for metric, value, unit in ((count_metric, count, 'people'), (salary_metric, total, 'USD'),
                                            (denominator_metric, with_salary, 'people')):
                    if metric == salary_metric and with_salary == 0:
                        continue
                    yield self.base(shard, index, 'observation', [metric, kind, period, family] + list(key[2:]), locator,
                                    subject=subject, metric=metric, value=value, unit=unit, **shared)

    def _dimensions(self, archive):
        for code, row in _dimension(archive, 'DTagy.txt', 'AGYSUB').items():
            self.agencies.setdefault(code, (_label(row.get('AGYSUBT'), code), (row.get('AGY') or '').strip(),
                                            _label(row.get('AGYT'), row.get('AGY')), (row.get('AGYTYPT') or '').strip()))
        for code, row in _dimension(archive, 'DTocc.txt', 'OCC').items():
            self.occupations.setdefault(code, (_label(row.get('OCCT'), code), (row.get('OCCFAMT') or '').strip(),
                                               (row.get('OCCTYPT') or '').strip()))
        for code, row in _dimension(archive, 'DTloc.txt', 'LOC').items():
            self.locations.setdefault(code, (_label(row.get('LOCT'), code), (row.get('LOCTYPT') or '').strip()))
        for code, row in _dimension(archive, 'DTppgrd.txt', 'PPGRD').items():
            self.grades.setdefault(code, ((row.get('PAYPLAN') or '').strip(), _label(row.get('PAYPLANT'), row.get('PAYPLAN')),
                                          (row.get('PPGROUPT') or '').strip()))

    # ----- subjects and dimensions ----------------------------------------------------------------
    def _subject(self, family, values, actions):
        agency = values.get('AGYSUB')
        location = values.get('LOC')
        if family == 'location':
            return self._place(location), {'duty_location_code': location,
                                           'duty_location': (self.locations.get(location) or ('', ''))[0] or None,
                                           'agency_subelement_code': None}
        if not agency:
            return None, {}
        subject = f'opm:agency:{agency}'
        dimensions = {'agency_subelement_code': agency}
        if family == 'agency_location':
            dimensions.update(duty_location_code=location, duty_location=(self.locations.get(location) or ('', ''))[0] or None,
                              duty_geography=self._place(location))
        elif family == 'agency_occupation':
            code = values.get('OCC')
            occupation = self.occupations.get(code) or ('', '', '')
            dimensions.update(occupation_code=code, occupation=occupation[0] or None,
                              occupation_family=occupation[1] or None, occupation_collar=occupation[2] or None)
        elif family == 'agency_pay_grade':
            code = values.get('PPGRD')
            grade = self.grades.get(code) or ('', '', '')
            dimensions.update(pay_plan_grade=code, pay_plan_code=grade[0] or None, pay_plan=grade[1] or None,
                              pay_plan_group=grade[2] or None)
        elif family == 'agency_action':
            code = values.get('ACTION')
            row = actions.get(code) or {}
            dimensions.update(action_code=code,
                              action=next((v for k, v in row.items() if k.endswith('T') and v), None))
        return subject, dimensions

    def _place(self, code):
        """`geo:US:state:<FIPS>` for the 50 states and DC; an OPM duty-location entity otherwise."""
        if code and code.isdigit() and len(code) == 2 and code != '00':
            return f'geo:US:state:{code}'
        return f'opm:duty_location:{code}' if code else None

    # ----- entities -------------------------------------------------------------------------------
    def registry(self):
        if self.anchor is None:
            return
        index, retrieved_at, locator = self.anchor
        shard = {'retrieved_at': retrieved_at}
        emit = lambda *a, **k: self.base(shard, index, *a, **k)
        yield emit('entity', ['place', 'geo:US'], locator, entity_id='geo:US', entity_type='country',
                   label='United States', attributes={'source_dataset': DATASET})
        departments = {}
        for code in sorted(self.agencies):
            label, parent, parent_label, agency_type = self.agencies[code]
            yield emit('entity', ['agency', code], locator, entity_id=f'opm:agency:{code}',
                       entity_type='government_agency', label=label,
                       attributes={'source_dataset': DATASET, 'opm_agency_subelement_code': code,
                                   'opm_agency_code': parent or None, 'opm_agency_type': agency_type or None,
                                   'code_system': 'OPM EHRI agency/sub-element code (not a Treasury CGAC code)',
                                   'crosswalk_to_cgac': 'none published by OPM or Treasury in these files; '
                                                        'no identity link to usgov:agency:<CGAC> is asserted'})
            if parent:
                departments.setdefault(parent, parent_label)
                if parent != code:
                    yield emit('assertion', ['agency_parent', code], locator, subject=f'opm:agency:{code}',
                               predicate='part_of', object=f'opm:agency:{parent}',
                               attributes={'source_dataset': DATASET})
        for code in sorted(departments):
            if code in self.agencies:
                continue
            yield emit('entity', ['agency', code], locator, entity_id=f'opm:agency:{code}',
                       entity_type='government_agency', label=departments[code],
                       attributes={'source_dataset': DATASET, 'opm_agency_code': code,
                                   'code_system': 'OPM EHRI agency code'})
        for code in sorted(self.locations):
            label, location_type = self.locations[code]
            key = self._place(code)
            if key is None:
                continue
            if key.startswith('geo:US:state:'):
                yield emit('entity', ['place', key], locator, entity_id=key, entity_type='state', label=label,
                           attributes={'source_dataset': DATASET, 'fips_state': code, 'code_system': 'FIPS',
                                       'opm_duty_location_code': code})
                yield emit('assertion', ['place_within', key], locator, subject=key, predicate='within',
                           object='geo:US', attributes={'source_dataset': DATASET})
            else:
                yield emit('entity', ['place', key], locator, entity_id=key, entity_type='location', label=label,
                           attributes={'source_dataset': DATASET, 'opm_duty_location_code': code,
                                       'opm_location_type': location_type or None,
                                       'code_system': 'OPM duty-location code (FIPS 10-4 style two-letter code)',
                                       'crosswalk_to_fips_or_iso': 'not published in the cube; left unjoined'})
        for code in sorted(self.occupations):
            label, family, collar = self.occupations[code]
            yield emit('entity', ['occupation', code], locator, entity_id=f'occ:opm:{code}', entity_type='occupation',
                       label=label, attributes={'source_dataset': DATASET, 'opm_occupational_series': code,
                                                'occupation_family': family or None, 'occupation_collar': collar or None,
                                                'code_system': 'OPM occupational series (Handbook of Occupational Groups and Families)'})


def _label(text, fallback):
    text = (text or '').strip()
    return text or (fallback or '').strip() or 'unknown'
