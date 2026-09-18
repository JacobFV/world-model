"""Offline fixtures for the public-sector labor pipelines (Census ASPEP, OPM FedScope).

Each test writes a few fictional rows in the publisher's raw layout — fixed-width ASCII for ASPEP,
a zipped fact file plus its DT*.txt dimension tables for FedScope — publishes them as a sharded raw
artifact in a temporary data root, and runs the dataset-local pipeline through the Runner. No network.
"""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.ontology import validate_typed_graph  # noqa: F401  (import keeps ontology extensions loaded)
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[1]
RETRIEVED = '2026-09-17T00:00:00+00:00'


class FullPipelineBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def tearDown(self):
        self.temp.cleanup()

    def zipped(self, name, members):
        path = self.root / 'fixtures' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
            for member, content in members.items():
                archive.writestr(member, content)
        return path

    def build(self, dataset, files):
        shards = [{'path': str(path), 'name': name, 'retrieved_at': RETRIEVED,
                   'request': {'method': 'GET', 'url': 'https://example.invalid/' + name, 'params': {}}}
                  for name, path in files]
        raw = self.store.import_shards(dataset, shards, {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(PROJECT / 'data'), self.store, PROJECT).run(dataset, raw_refs={dataset: [raw]})
        records = list(self.store.records(ref))
        self.assertEqual(len(records), len({r['id'] for r in records}))
        self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:') for r in records))
        return records

    @staticmethod
    def by(records, **match):
        return [r for r in records if all(r.get(k) == v for k, v in match.items())]


def unit_id(state, unit_type, county, number, supplement='000', sub='00'):
    return f'{state}{unit_type}{county}{number}{supplement}{sub}'


def id_row(unit, name, region, county_name, fips_state, fips_county, population='', population_year='',
           school_level='', probability='1.0000', worksheet='04', new_id=''):
    """Build one ``<yy>empid.txt`` record at the published column positions (213 characters)."""
    line = [' '] * 213
    def put(start, text, width):
        text = str(text)[:width]
        line[start:start + len(text)] = list(text)
    put(0, unit, 14)
    put(14, name.ljust(64), 64)
    put(78, region, 1)
    put(79, county_name.ljust(30), 30)
    put(109, fips_state, 2)
    put(111, fips_county, 3)
    put(125, str(population).rjust(9), 9)
    put(134, population_year, 2)
    put(136, school_level, 2)
    put(145, probability, 6)
    put(204, worksheet, 2)
    put(207, new_id, 6)
    return ''.join(line)


def data_row(unit, item, ft_employees, ft_flag, ft_payroll, ft_pay_flag, pt_employees, pt_flag,
             pt_payroll, pt_pay_flag, pt_hours=None, pt_hours_flag=None, fte=None, new_id=''):
    """Build one flagged ``<yy>empst`` record: 80 characters, or 94 for the 2007-2018 layout."""
    width = 94 if pt_hours is not None else 80
    line = [' '] * width
    def put(start, text, size):
        text = str(text)[:size]
        line[start:start + len(text)] = list(text)
    put(0, unit, 14)
    put(17, item, 3)
    put(20, str(ft_employees).rjust(10), 10)
    put(31, ft_flag, 1)
    put(32, str(ft_payroll).rjust(12), 12)
    put(45, ft_pay_flag, 1)
    put(46, str(pt_employees).rjust(10), 10)
    put(57, pt_flag, 1)
    put(58, str(pt_payroll).rjust(12), 12)
    put(71, pt_pay_flag, 1)
    if pt_hours is None:
        put(74, new_id, 6)
    else:
        put(72, str(pt_hours).rjust(10), 10)
        put(83, pt_hours_flag or ' ', 1)
        put(84, str(fte).rjust(10), 10)
    return ''.join(line)


def unflagged_row(unit, item, ft_employees, ft_payroll, pt_employees, pt_payroll, pt_hours, fte):
    """Build one pre-2007 ``<yy>empst`` record: 84 characters, no data flags, payroll two columns left."""
    line = [' '] * 84
    def put(start, text, size):
        text = str(text)[:size]
        line[start:start + len(text)] = list(text)
    put(0, unit, 14)
    put(17, item, 3)
    put(20, str(ft_employees).rjust(10), 10)
    put(30, str(ft_payroll).rjust(12), 12)
    put(42, str(pt_employees).rjust(10), 10)
    put(52, str(pt_payroll).rjust(12), 12)
    put(64, str(pt_hours).rjust(10), 10)
    put(74, str(fte).rjust(10), 10)
    return ''.join(line)


class CensusAspepTests(FullPipelineBase):
    COUNTY = unit_id('01', '1', '001', '001')
    SCHOOL = unit_id('01', '5', '030', '002')
    DISTRICT = unit_id('01', '4', '007', '003')

    def archive(self, year, id_rows, data_rows, packaging='txt'):
        """Build a year's archive in one of the publisher's three packagings."""
        yy = f'{year % 100:02d}'
        prefix = 'c' if year in (1997, 2002, 2007, 2012, 2017, 2022) else ''
        id_text = '\n'.join(id_rows) + '\n'
        data_text = '\n'.join(data_rows) + '\n'
        if packaging == 'txt':
            members = {f'{yy}{prefix}empid.txt': id_text, f'{yy}{prefix}empst.txt': data_text}
        elif packaging == 'dat_subdirectory':
            members = {f'{year}_downloadable_data/Individual Unit File/{yy}{prefix}empid.dat': id_text,
                       f'{year}_downloadable_data/Individual Unit File/{yy}{prefix}empst.dat': data_text}
        elif packaging == 'nested_zip':
            members = {f'{yy}{prefix}empid.zip': self.inner_zip(f'{yy.upper()}{prefix.upper()}EMPID.DAT', id_text),
                       f'{yy}{prefix}empst.zip': self.inner_zip(f'{yy.upper()}{prefix.upper()}EMPST.DAT', data_text),
                       f'{yy}fedfun.txt': 'unrelated federal function table\n'}
        else:
            raise AssertionError(packaging)
        return self.zipped(f'aspep_{year}.zip', members)

    @staticmethod
    def inner_zip(name, text):
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(name, text)
        return buffer.getvalue()

    def test_units_functions_flags_and_the_fips_geography_join(self):
        rows_2023 = [id_row('00000000000000', 'United States', ' ', '', '00', '   ', 331449281, '20'),
                     id_row(self.COUNTY, 'Autauga', '3', 'Autauga', '01', '001', 59095, '20', new_id='100001'),
                     id_row(self.SCHOOL, 'Example City Schools', '3', 'Franklin', '01', '059', 2507, '', '03',
                            probability='0.2174', worksheet='08', new_id='177905'),
                     id_row(self.DISTRICT, 'Example Water Authority', '3', 'Bibb', '01', '007', '000000091', '',
                            probability='0.5000', new_id='188888')]
        data_2023 = [data_row(self.COUNTY, '000', 120, 'R', 700000, 'R', 40, 'R', 90000, 'R', new_id='100001'),
                     data_row(self.COUNTY, '062', 55, 'R', 400000, 'R', 0, ' ', 0, ' ', new_id='100001'),
                     data_row(self.SCHOOL, '012', 200, 'X', 1100000, 'X', 10, 'X', 12000, 'X', new_id='177905'),
                     data_row(self.DISTRICT, '091', 7, 'R', 35000, 'R', 1, 'R', 2000, 'R', new_id='188888')]
        records = self.build('census_aspep', [('aspep_2023.zip', self.archive(2023, rows_2023, data_2023))])

        # The national aggregate row carries no data and is not made into a government unit.
        self.assertEqual(self.by(records, kind='entity', entity_id='aspep:unit:00000000000000'), [])

        county = self.by(records, kind='entity', entity_id=f'aspep:unit:{self.COUNTY}')
        self.assertEqual(len(county), 1)
        self.assertEqual((county[0]['label'], county[0]['entity_type']), ('Autauga', 'government_agency'))
        self.assertEqual(county[0]['attributes']['government_type'], 'county_government')
        self.assertEqual(county[0]['attributes']['fips_county'], '01001')
        self.assertEqual(county[0]['attributes']['new_individual_unit_id'], '100001')

        # The join is the published FIPS, not the 14-character ID's internal county code.
        within = self.by(records, predicate='within', subject=f'aspep:unit:{self.COUNTY}')
        self.assertEqual([r['object'] for r in within], ['geo:US:county:01001'])
        self.assertEqual((within[0]['valid_from'], within[0]['valid_to']), ('2023-03-01', '2024-03-01'))
        self.assertEqual([r['object'] for r in self.by(records, predicate='within', subject=f'aspep:unit:{self.SCHOOL}')],
                         ['geo:US:county:01059'])
        self.assertEqual({r['entity_id'] for r in records if r['kind'] == 'entity' and r.get('entity_type') == 'county'},
                         {'geo:US:county:01001', 'geo:US:county:01007', 'geo:US:county:01059'})
        self.assertEqual([r['object'] for r in self.by(records, predicate='within', subject='geo:US:county:01001')],
                         ['geo:US:state:01'])

        employees = self.by(records, metric='government_employees', subject=f'aspep:unit:{self.COUNTY}')
        self.assertEqual(sorted((r['dimensions']['function_code'], r['dimensions']['employment_status'], r['value'])
                                for r in employees),
                         [('000', 'full_time', 120), ('000', 'part_time', 40), ('062', 'full_time', 55)])
        police = next(r for r in employees if r['dimensions']['function_code'] == '062')
        self.assertEqual(police['dimensions']['function'], 'Police Protection - Persons with Power of Arrest')
        self.assertEqual((police['valid_from'], police['valid_to'], police['dimensions']['reference_period']),
                         ('2023-03-01', '2023-04-01', 'march'))
        self.assertEqual(police['attributes']['data_flag_class'], 'reported')

        payroll = next(r for r in self.by(records, metric='government_payroll', subject=f'aspep:unit:{self.COUNTY}')
                       if r['dimensions']['employment_status'] == 'full_time'
                       and r['dimensions']['function_code'] == '000')
        self.assertEqual((payroll['value'], payroll['unit']), (700000, 'USD'))
        self.assertEqual(payroll['attributes']['payroll_basis'], '31_day_monthly_equivalent_for_march')

        # An imputed value is kept, and flagged as imputed rather than dropped.
        school = self.by(records, metric='government_employees', subject=f'aspep:unit:{self.SCHOOL}')[0]
        self.assertEqual(school['attributes']['data_flag_class'], 'imputed')

        # Zero part-time counts under a non-total function are omitted; the total is always emitted.
        self.assertEqual([r['value'] for r in employees if r['dimensions']['function_code'] == '062'
                          and r['dimensions']['employment_status'] == 'part_time'], [])

        # Population, enrollment and the special-district activity code are told apart.
        self.assertEqual([(r['value'], r['dimensions']['reference_year']) for r in
                          self.by(records, metric='government_unit_population', subject=f'aspep:unit:{self.COUNTY}')],
                         [(59095, 2020)])
        self.assertEqual([r['value'] for r in self.by(records, metric='school_enrollment')], [2507])
        self.assertEqual(self.by(records, metric='government_unit_population', subject=f'aspep:unit:{self.DISTRICT}'), [])
        district = self.by(records, kind='entity', entity_id=f'aspep:unit:{self.DISTRICT}')[0]
        self.assertEqual(district['attributes']['special_district_activity_code'], '000000091')

    def test_census_and_sample_years_are_labelled_and_a_unit_spans_its_observed_years(self):
        rows = [id_row(self.COUNTY, 'Autauga County', '3', 'Autauga', '01', '001', 54571, '10', probability='0.8403')]
        data = [data_row(self.COUNTY, '000', 100, 'R', 500000, 'R', 30, 'R', 60000, 'R', pt_hours=4000,
                         pt_hours_flag='R', fte=118)]
        newer = [id_row(self.COUNTY, 'Autauga', '3', 'Autauga', '01', '001', 59095, '20', new_id='100001')]
        newer_data = [data_row(self.COUNTY, '000', 120, 'R', 700000, 'R', 40, 'R', 90000, 'R', new_id='100001')]
        records = self.build('census_aspep', [('aspep_2012.zip', self.archive(2012, rows, data)),
                                              ('aspep_2023.zip', self.archive(2023, newer, newer_data))])
        basis = {r['dimensions']['survey_year']: r['dimensions']['collection_basis']
                 for r in self.by(records, metric='government_employees')}
        self.assertEqual(basis, {2012: 'census_of_governments', 2023: 'annual_sample'})
        sampled = next(r for r in self.by(records, metric='government_unit_population')
                       if r['dimensions']['survey_year'] == 2012)
        self.assertEqual((sampled['attributes']['probability_of_selection'],
                          sampled['attributes']['unit_enumerated_not_sampled']), (0.8403, True))

        # One entity for the unit, labelled from its most recent year, spanning both.
        entity = self.by(records, kind='entity', entity_id=f'aspep:unit:{self.COUNTY}')
        self.assertEqual(len(entity), 1)
        self.assertEqual(entity[0]['label'], 'Autauga')
        self.assertEqual((entity[0]['attributes']['first_survey_year'], entity[0]['attributes']['last_survey_year'],
                          entity[0]['attributes']['survey_years_present'], entity[0]['attributes']['label_from_survey_year']),
                         (2012, 2023, 2, 2023))
        within = self.by(records, predicate='within', subject=f'aspep:unit:{self.COUNTY}')
        self.assertEqual([(r['valid_from'], r['valid_to']) for r in within], [('2012-03-01', '2024-03-01')])

        # The pre-2017 layout's extra columns are read only where they exist.
        def measures(year):
            return {(r['metric'], r['dimensions']['employment_status']): r['value'] for r in records
                    if r['kind'] == 'observation' and r['dimensions'].get('survey_year') == year
                    and 'employment_status' in r['dimensions']}
        legacy = measures(2012)
        self.assertEqual(legacy[('government_part_time_hours', 'part_time')], 4000)
        self.assertEqual(legacy[('government_employees', 'full_time_equivalent')], 118)
        self.assertNotIn(('government_part_time_hours', 'part_time'), measures(2023))
        self.assertNotIn(('government_employees', 'full_time_equivalent'), measures(2023))

    def test_an_unrecognised_shard_name_fails_rather_than_guessing_the_year(self):
        archive = self.zipped('23empst.zip', {'23empid.txt': id_row(self.COUNTY, 'X', '3', 'Autauga', '01', '001', 1, '20') + '\n',
                                              '23empst.txt': data_row(self.COUNTY, '000', 1, 'R', 1, 'R', 0, ' ', 0, ' ') + '\n'})
        with self.assertRaisesRegex(Exception, 'aspep_<year>.zip'):
            self.build('census_aspep', [('23empst.zip', archive)])

    def test_nested_zip_dat_and_txt_packagings_all_read(self):
        """1993-2011 nest a ZIP inside the archive, 2012-2013 bury a .dat, 2014+ ship a plain .txt."""
        rows = [id_row(self.COUNTY, 'Autauga County', '3', 'Autauga', '01', '001', 41000, '90', probability='0.4000')]
        cases = [(2001, 'nested_zip', [unflagged_row(self.COUNTY, '000', 90, 400000, 20, 40000, 3000, 105)]),
                 (2013, 'dat_subdirectory', [data_row(self.COUNTY, '000', 100, 'R', 500000, 'R', 25, 'R', 50000, 'R',
                                                      pt_hours=3500, pt_hours_flag='R', fte=118)]),
                 (2023, 'txt', [data_row(self.COUNTY, '000', 120, 'R', 700000, 'R', 40, 'R', 90000, 'R')])]
        files = [(f'aspep_{year}.zip', self.archive(year, rows, data, packaging=packaging))
                 for year, packaging, data in cases]
        records = self.build('census_aspep', files)
        full_time = {r['dimensions']['survey_year']: r['value'] for r in
                     self.by(records, metric='government_employees') if r['dimensions']['employment_status'] == 'full_time'}
        self.assertEqual(full_time, {2001: 90, 2013: 100, 2023: 120})
        locators = {r['dimensions']['survey_year']: r['evidence'][0]['locator'] for r in
                    self.by(records, metric='government_payroll')}
        self.assertIn('member:01empst.zip!01EMPST.DAT', locators[2001])
        self.assertIn('2013_downloadable_data/Individual Unit File/13empst.dat', locators[2013])
        self.assertIn('member:23empst.txt', locators[2023])

    def test_the_pre_2007_unflagged_layout_reads_its_own_columns(self):
        """The 84-character record has no data flags, so payroll sits two positions to the left."""
        rows = [id_row(self.COUNTY, 'Autauga County', '3', 'Autauga', '01', '001', 41000, '90', probability='0.4000')]
        data = [unflagged_row(self.COUNTY, '000', 74510, 252908025, 27864, 26650319, 1941617, 85640)]
        records = self.build('census_aspep', [('aspep_2003.zip', self.archive(2003, rows, data,
                                                                             packaging='nested_zip'))])
        values = {(r['metric'], r['dimensions']['employment_status']): r['value'] for r in records
                  if r['kind'] == 'observation' and 'employment_status' in r['dimensions']}
        self.assertEqual(values[('government_employees', 'full_time')], 74510)
        self.assertEqual(values[('government_payroll', 'full_time')], 252908025)
        self.assertEqual(values[('government_employees', 'part_time')], 27864)
        self.assertEqual(values[('government_payroll', 'part_time')], 26650319)
        self.assertEqual(values[('government_part_time_hours', 'part_time')], 1941617)
        self.assertEqual(values[('government_employees', 'full_time_equivalent')], 85640)
        payroll = next(r for r in self.by(records, metric='government_payroll')
                       if r['dimensions']['employment_status'] == 'full_time')
        self.assertEqual((payroll['attributes']['data_flags_published'], payroll['attributes']['data_flag'],
                          payroll['attributes']['record_layout']), (False, None, 'unflagged_84_character'))

    def test_an_undocumented_record_width_refuses_to_guess(self):
        rows = [id_row(self.COUNTY, 'Autauga County', '3', 'Autauga', '01', '001', 41000, '90')]
        widened = [unflagged_row(self.COUNTY, '000', 90, 400000, 20, 40000, 3000, 105) + '   EXTRA']
        with self.assertRaisesRegex(Exception, 'matches no documented ASPEP layout'):
            self.build('census_aspep', [('aspep_2004.zip', self.archive(2004, rows, widened))])

    def test_a_unit_function_printed_on_two_lines_is_summed_and_says_so(self):
        rows = [id_row(self.SCHOOL, 'Example ISD', '2', 'Dakota', '27', '037', 2153, '90', '03', probability='0.3000')]
        data = [unflagged_row(self.SCHOOL, '000', 92, 246185, 104, 76878, 9608, 144),
                unflagged_row(self.SCHOOL, '012', 48, 148518, 44, 39917, 4886, 76),
                unflagged_row(self.SCHOOL, '012', 25, 54823, 14, 7346, 951, 30)]
        records = self.build('census_aspep', [('aspep_1995.zip', self.archive(1995, rows, data,
                                                                             packaging='nested_zip'))])
        instructional = [r for r in self.by(records, metric='government_employees')
                         if r['dimensions']['function_code'] == '012'
                         and r['dimensions']['employment_status'] == 'full_time']
        self.assertEqual([(r['value'], r['attributes']['source_rows_merged']) for r in instructional], [(73, 2)])
        payroll = [r for r in self.by(records, metric='government_payroll')
                   if r['dimensions']['function_code'] == '012'
                   and r['dimensions']['employment_status'] == 'full_time']
        self.assertEqual([r['value'] for r in payroll], [148518 + 54823])
        total = [r for r in self.by(records, metric='government_employees')
                 if r['dimensions']['function_code'] == '000'
                 and r['dimensions']['employment_status'] == 'full_time']
        self.assertEqual([(r['value'], r['attributes']['source_rows_merged']) for r in total], [(92, 1)])

    def test_a_flagged_width_holding_no_flags_is_rejected_rather_than_mis_read(self):
        """The guard that stops the two-position shift from being read as plausible wrong numbers."""
        rows = [id_row(self.COUNTY, 'Autauga County', '3', 'Autauga', '01', '001', 41000, '90')]
        # 94 characters (a flagged width) but built with the unflagged column positions and no flags.
        mislabelled = [(unflagged_row(self.COUNTY, '000', 90, 400000, 20, 40000, 3000, 105) + ' ' * 10)]
        with self.assertRaisesRegex(Exception, 'the layout table calls flagged'):
            self.build('census_aspep', [('aspep_2009.zip', self.archive(2009, rows, mislabelled))])


AGENCIES = ('AGYTYP,AGYTYPT,AGY,AGYT,AGYSUB,AGYSUBT\n'
            '1,Cabinet Level Agencies,ZZ,ZZ-DEPARTMENT OF EXAMPLES,ZZ01,ZZ01-EXAMPLE FIELD SERVICE\n'
            '1,Cabinet Level Agencies,ZZ,ZZ-DEPARTMENT OF EXAMPLES,ZZ02,ZZ02-EXAMPLE INSPECTION OFFICE\n')
LOCATIONS = ('LOCTYP,LOCTYPT,LOC,LOCT\n'
             '1,United States,01,01-ALABAMA\n'
             '1,United States,11,11-DISTRICT OF COLUMBIA\n'
             '2,U.S. Territories,GQ,GQ-GUAM\n'
             '3,Foreign Countries,IT,IT-ITALY\n')
OCCUPATIONS = ('OCCTYP,OCCTYPT,OCCFAM,OCCFAMT,OCC,OCCT\n'
               '1,White Collar,03,03xx-EXAMPLE GROUP,0340,0340-PROGRAM MANAGEMENT\n'
               '2,Blue Collar,47,47xx-EXAMPLE TRADES,4749,4749-MAINTENANCE MECHANIC\n')
GRADES = ('PPTYP,PPTYPT,PPGROUP,PPGROUPT,PAYPLAN,PAYPLANT,PPGRD\n'
          '1,General Schedule and Equivalently Graded (GSEG) Pay Plans,11,Standard GSEG Pay Plans,GS,GS-GENERAL SCHEDULE,GS-13\n'
          '2,Senior Pay Levels,21,Senior Executive Service,ES,ES-SENIOR EXECUTIVE SERVICE,ES-**\n')
PERIODS = ('FY,FYT,QTR,QTRT,EFDATE,EFDATET\n'
           '1,FY 2024,1,OCT-DEC 2023,202310,OCT 2023\n'
           '1,FY 2024,2,JAN-MAR 2024,202401,JAN 2024\n'
           '2,FY 2025,1,OCT-DEC 2024,202410,OCT 2024\n')
ACTIONS = 'ACC,ACCT\nAC,New Hire - Competitive Service Appointment\nAA,Transfer In - Individual Transfer\n'


class OpmFedScopeTests(FullPipelineBase):
    FACT_HEADER = ('AGYSUB,LOC,AGELVL,EDLVL,GSEGRD,LOSLVL,OCC,PATCO,PP,PPGRD,SALLVL,STEMOCC,SUPERVIS,TOA,'
                   'WORKSCH,WORKSTAT,DATECODE,EMPLOYMENT,SALARY,LOS')

    def employment(self, name, datecode, rows, header=None):
        body = (header or self.FACT_HEADER) + '\n' + '\n'.join(rows) + '\n'
        return self.zipped(name, {f'FACTDATA_{datecode}.TXT': body, 'DTagy.txt': AGENCIES, 'DTloc.txt': LOCATIONS,
                                  'DTocc.txt': OCCUPATIONS, 'DTppgrd.txt': GRADES})

    def test_employee_rows_become_marginal_cells_with_a_published_salary_denominator(self):
        rows = ['ZZ01,01,F,13,13,F,0340,2,GS,GS-13,S,XXXX,2,50,F,1,202206,1,100000,19.0',
                'ZZ01,01,G,13,13,F,0340,2,GS,GS-13,S,XXXX,2,50,F,1,202206,1,120000,21.0',
                'ZZ01,01,H,15,,H,4749,1,ES,ES-**,R,XXXX,2,50,F,1,202206,1,,29.4',
                'ZZ02,11,I,15,,H,0340,1,ES,ES-**,R,XXXX,2,50,F,1,202206,1,180000,30.0',
                'ZZ02,IT,I,15,,H,0340,1,ES,ES-**,R,XXXX,2,50,F,1,202206,1,190000,30.0']
        records = self.build('opm_fedscope', [('fedscope_employment_202206.zip',
                                               self.employment('fedscope_employment_202206.zip', 'JUN2022', rows))])

        agency_state = self.by(records, metric='federal_employees', subject='opm:agency:ZZ01')
        cell = next(r for r in agency_state if r['dimensions']['cell'] == 'agency_location')
        self.assertEqual((cell['value'], cell['unit']), (3, 'people'))
        self.assertEqual(cell['dimensions']['duty_geography'], 'geo:US:state:01')
        self.assertEqual((cell['valid_from'], cell['valid_to']), ('2022-06-01', '2022-07-01'))

        # The blank-salary row counts as an employee and not as salary; the denominator says so.
        salary = next(r for r in self.by(records, metric='federal_annual_salary_total', subject='opm:agency:ZZ01')
                      if r['dimensions']['cell'] == 'agency_location')
        denominator = next(r for r in self.by(records, metric='federal_employees_with_published_salary',
                                              subject='opm:agency:ZZ01')
                           if r['dimensions']['cell'] == 'agency_location')
        self.assertEqual((salary['value'], salary['unit'], denominator['value']), (220000.0, 'USD', 2))

        by_occupation = {r['dimensions']['occupation_code']: (r['value'], r['dimensions']['occupation'])
                         for r in agency_state if r['dimensions']['cell'] == 'agency_occupation'}
        self.assertEqual(by_occupation, {'0340': (2, '0340-PROGRAM MANAGEMENT'), '4749': (1, '4749-MAINTENANCE MECHANIC')})
        by_grade = {r['dimensions']['pay_plan_grade']: (r['value'], r['dimensions']['pay_plan'])
                    for r in agency_state if r['dimensions']['cell'] == 'agency_pay_grade'}
        self.assertEqual(by_grade, {'GS-13': (2, 'GS-GENERAL SCHEDULE'), 'ES-**': (1, 'ES-SENIOR EXECUTIVE SERVICE')})

        state_total = {r['subject']: r['value'] for r in self.by(records, metric='federal_employees')
                       if r['dimensions']['cell'] == 'location'}
        self.assertEqual(state_total, {'geo:US:state:01': 3, 'geo:US:state:11': 1, 'opm:duty_location:IT': 1})

        # A foreign duty station keeps its OPM code rather than being guessed into a FIPS or ISO code.
        italy = self.by(records, kind='entity', entity_id='opm:duty_location:IT')[0]
        self.assertEqual((italy['entity_type'], italy['label'], italy['attributes']['opm_location_type']),
                         ('location', 'IT-ITALY', 'Foreign Countries'))
        self.assertEqual(self.by(records, kind='entity', entity_id='opm:duty_location:01'), [])

        # Agencies are OPM codes, are not claimed to be CGAC codes, and nest into their department.
        agency = self.by(records, kind='entity', entity_id='opm:agency:ZZ01')[0]
        self.assertEqual(agency['label'], 'ZZ01-EXAMPLE FIELD SERVICE')
        self.assertIn('not a Treasury CGAC code', agency['attributes']['code_system'])
        self.assertEqual([r['object'] for r in self.by(records, predicate='part_of', subject='opm:agency:ZZ01')],
                         ['opm:agency:ZZ'])
        self.assertEqual(self.by(records, kind='entity', entity_id='opm:agency:ZZ')[0]['label'],
                         'ZZ-DEPARTMENT OF EXAMPLES')
        self.assertEqual(self.by(records, kind='entity', entity_id='occ:opm:0340')[0]['entity_type'], 'occupation')

        # Nothing in the artifact claims to be about a named person.
        self.assertTrue(all(r['attributes'].get('person_level_rows_not_retained') is True
                            for r in records if r['kind'] == 'observation'))

    def test_the_1998_layout_without_a_pay_plan_column_and_with_formatted_salary_still_parses(self):
        header = ('AGYSUB,LOC,AGELVL,EDLVL,GSEGRD,LOSLVL,OCC,PATCO,PPGRD,SALLVL,STEMOCC,SUPERVIS,TOA,'
                  'WORKSCH,WORKSTAT,DATECODE,EMPLOYMENT,SALARY,LOS')
        rows = ['ZZ01,01,I,04,11,F,0340,2,GS-13,D,XXXX,2,20,F,2,199809,1,"$42,709",18.7',
                'ZZ01,01,I,04,11,F,0340,2,GS-13,D,XXXX,2,20,F,2,199809,1,"$40,291",12.0']
        records = self.build('opm_fedscope', [('fedscope_employment_199809.zip',
                                               self.employment('fedscope_employment_199809.zip', 'SEP1998', rows,
                                                               header=header))])
        salary = next(r for r in self.by(records, metric='federal_annual_salary_total', subject='opm:agency:ZZ01')
                      if r['dimensions']['cell'] == 'agency')
        self.assertEqual(salary['value'], 83000.0)
        self.assertEqual((salary['valid_from'], salary['valid_to']), ('1998-09-01', '1998-10-01'))

    def test_accession_cubes_aggregate_by_fiscal_year_and_action_type(self):
        header = ('AGYSUB,ACC,EFDATE,AGELVL,EDLVL,GSEGRD,LOSLVL,LOC,OCC,PATCO,PPGRD,SALLVL,STEMOCC,TOA,'
                  'WORKSCH,WORKSTAT,COUNT,SALARY,LOS')
        rows = ['ZZ01,AC,202310,F,04,13,E,01,0340,1,GS-13,Q,XXXX,30,F,1,1,90000,0.1',
                'ZZ01,AC,202401,G,15,13,D,01,0340,1,GS-13,M,XXXX,30,F,1,1,95000,0.3',
                'ZZ01,AA,202410,G,15,13,D,01,0340,1,GS-13,M,XXXX,30,F,1,1,99000,5.0']
        archive = self.zipped('fedscope_accessions_fy2020_fy2024.zip',
                              {'ACCDATA_FY2020-2024.TXT': header + '\n' + '\n'.join(rows) + '\n',
                               'DTagy.txt': AGENCIES, 'DTloc.txt': LOCATIONS, 'DTocc.txt': OCCUPATIONS,
                               'DTppgrd.txt': GRADES, 'DTefdate.txt': PERIODS, 'DTacc.txt': ACTIONS})
        records = self.build('opm_fedscope', [('fedscope_accessions_fy2020_fy2024.zip', archive)])
        totals = {(r['dimensions']['period_label'], r['dimensions']['cell']): r['value']
                  for r in self.by(records, metric='federal_accessions', subject='opm:agency:ZZ01')}
        self.assertEqual(totals[('FY 2024', 'agency')], 2)
        self.assertEqual(totals[('FY 2025', 'agency')], 1)
        fiscal = next(r for r in self.by(records, metric='federal_accessions', subject='opm:agency:ZZ01')
                      if r['dimensions']['cell'] == 'agency' and r['dimensions']['period_label'] == 'FY 2024')
        self.assertEqual((fiscal['valid_from'], fiscal['valid_to']), ('2023-10-01', '2024-10-01'))
        actions = {r['dimensions']['action_code']: (r['value'], r['dimensions']['action'])
                   for r in self.by(records, metric='federal_accessions', subject='opm:agency:ZZ01')
                   if r['dimensions']['cell'] == 'agency_action'}
        self.assertEqual(actions, {'AC': (2, 'New Hire - Competitive Service Appointment'),
                                   'AA': (1, 'Transfer In - Individual Transfer')})
        self.assertEqual([r['metric'] for r in records if r['kind'] == 'observation'
                          and r['metric'].startswith('federal_employees')], [])
        self.assertNotIn('federal_annual_salary_total', json.dumps([r.get('metric') for r in records]))

    def test_a_cube_without_a_fact_file_fails_loudly(self):
        archive = self.zipped('fedscope_employment_202203.zip', {'DTagy.txt': AGENCIES, 'DTloc.txt': LOCATIONS})
        with self.assertRaisesRegex(Exception, 'no fact file'):
            self.build('opm_fedscope', [('fedscope_employment_202203.zip', archive)])


if __name__ == '__main__':
    unittest.main()
