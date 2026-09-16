"""BLS labor statistics: SM, LAUS, JOLTS, national CES, OEWS and QCEW.

Two input shapes are supported:

* the legacy bounded API sample (one LNS14000000 JSON row per line), and
* the full sharded ``files`` acquisition (one shard per BLS file), streamed file by file.

Full-data records are compact: one observation per published value, geography as the
subject, industry/occupation/ownership in ``dimensions``; geography and industry
entities are emitted once per shard.
"""
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS
from worldmodel.raw_readers import iter_rows
try:
    from .helpers import (footnotes, mapping, number, open_zip_member, period_bounds, shard_name, tab_rows,
                          sheet_members, xlsx_table)
except ImportError:  # loaded as a standalone module (legacy adapter tests)
    import importlib.util as _util
    from pathlib import Path as _Path
    _spec = _util.spec_from_file_location('_bls_labor_helpers', _Path(__file__).with_name('helpers.py'))
    _helpers = _util.module_from_spec(_spec)
    _spec.loader.exec_module(_helpers)
    footnotes, mapping, number, open_zip_member, period_bounds, shard_name, tab_rows, sheet_members, xlsx_table = (
        _helpers.footnotes, _helpers.mapping, _helpers.number, _helpers.open_zip_member, _helpers.period_bounds,
        _helpers.shard_name, _helpers.tab_rows, _helpers.sheet_members, _helpers.xlsx_table)

DATASET = 'bls_labor'
STATE_NAMES = {}

# data type code -> (metric, unit, scale)
CES_TYPES = {
    '01': ('employment', 'persons', 1000), '02': ('average_weekly_hours', 'hours_per_week', 1),
    '03': ('average_hourly_earnings', 'USD_per_hour', 1), '04': ('average_weekly_overtime_hours', 'hours_per_week', 1),
    '06': ('production_nonsupervisory_employment', 'persons', 1000),
    '07': ('production_nonsupervisory_average_weekly_hours', 'hours_per_week', 1),
    '08': ('production_nonsupervisory_average_hourly_earnings', 'USD_per_hour', 1),
    '09': ('production_nonsupervisory_average_weekly_overtime_hours', 'hours_per_week', 1),
    '10': ('women_employment', 'persons', 1000), '11': ('average_weekly_earnings', 'USD_per_week', 1),
    '12': ('real_average_weekly_earnings', 'USD_1982_1984_per_week', 1),
    '13': ('real_average_hourly_earnings', 'USD_1982_1984_per_hour', 1),
    '15': ('average_hourly_earnings_excluding_overtime', 'USD_per_hour', 1),
    '16': ('aggregate_weekly_hours_index', 'index_2007_100', 1), '17': ('aggregate_weekly_payrolls_index', 'index_2007_100', 1),
    '19': ('average_weekly_hours_quarterly_average', 'hours_per_week', 1),
    '20': ('average_weekly_overtime_hours_quarterly_average', 'hours_per_week', 1),
    '21': ('employment_diffusion_index_1m', 'percent', 1), '22': ('employment_diffusion_index_3m', 'percent', 1),
    '23': ('employment_diffusion_index_6m', 'percent', 1), '24': ('employment_diffusion_index_12m', 'percent', 1),
    '25': ('employment_quarterly_average', 'persons', 1000), '26': ('employment_3m_average_change', 'persons', 1000),
    '30': ('production_nonsupervisory_average_weekly_earnings', 'USD_per_week', 1),
    '31': ('production_nonsupervisory_real_average_weekly_earnings', 'USD_1982_1984_per_week', 1),
    '32': ('production_nonsupervisory_real_average_hourly_earnings', 'USD_1982_1984_per_hour', 1),
    '33': ('production_nonsupervisory_average_hourly_earnings_excluding_overtime', 'USD_per_hour', 1),
    '34': ('production_nonsupervisory_aggregate_weekly_hours_index', 'index_2002_100', 1),
    '35': ('production_nonsupervisory_aggregate_weekly_payrolls_index', 'index_2002_100', 1),
    '36': ('production_nonsupervisory_average_weekly_hours_quarterly_average', 'hours_per_week', 1),
    '37': ('production_nonsupervisory_average_weekly_overtime_hours_quarterly_average', 'hours_per_week', 1),
    '56': ('aggregate_weekly_hours', 'hours_per_week', 1000), '57': ('aggregate_weekly_payrolls', 'USD_per_week', 1000),
    '58': ('aggregate_weekly_overtime_hours', 'hours_per_week', 1000),
    '81': ('production_nonsupervisory_aggregate_weekly_hours', 'hours_per_week', 1000),
    '82': ('production_nonsupervisory_aggregate_weekly_payrolls', 'USD_per_week', 1000),
    '83': ('production_nonsupervisory_aggregate_weekly_overtime_hours', 'hours_per_week', 1000),
    'C1': ('survey_first_closing_collection_rate', 'percent', 1), 'C2': ('survey_second_closing_collection_rate', 'percent', 1),
    'C3': ('survey_third_closing_collection_rate', 'percent', 1), 'RR': ('survey_third_closing_response_rate', 'percent', 1),
}
LAUS_MEASURES = {'03': ('unemployment_rate', 'percent'), '04': ('unemployed', 'persons'), '05': ('employment', 'persons'),
                 '06': ('labor_force', 'persons'), '07': ('employment_population_ratio', 'percent'),
                 '08': ('labor_force_participation_rate', 'percent'),
                 '09': ('civilian_noninstitutional_population', 'persons')}
JOLTS_ELEMENTS = {'JO': 'job_openings', 'HI': 'hires', 'TS': 'total_separations', 'QU': 'quits',
                  'LD': 'layoffs_discharges', 'OS': 'other_separations'}
QCEW_METRICS = (('annual_avg_estabs', 'establishment_count', 'establishments'), ('annual_avg_emplvl', 'employment', 'persons'),
                ('total_annual_wages', 'total_annual_wages', 'USD'), ('annual_avg_wkly_wage', 'average_weekly_wage', 'USD_per_week'),
                ('avg_annual_pay', 'average_annual_pay', 'USD'))
QCEW_OWNERSHIP = {'0': 'total_covered', '1': 'federal_government', '2': 'state_government', '3': 'local_government',
                  '5': 'private', '8': 'total_government', '9': 'total_covered_excluding_federal'}
OEWS_METRICS = (('TOT_EMP', 'employment', 'persons'), ('A_MEAN', 'mean_annual_wage', 'USD_per_year'),
                ('A_MEDIAN', 'median_annual_wage', 'USD_per_year'), ('H_MEAN', 'mean_hourly_wage', 'USD_per_hour'),
                ('H_MEDIAN', 'median_hourly_wage', 'USD_per_hour'))
OEWS_MEMBER = re.compile(r'(?:^|/)(all_data_M_|state_M|MSA_M|BOS_M)(\d{4})(?:_dl)?\.xlsx$', re.I)
OEWS_SPECIAL = {'*': 'not_available', '**': 'employment_not_available', '#': 'top_coded_at_or_above_published_maximum',
                '~': 'less_than_half_percent'}
# Estimation-layer contract (worldmodel/estimation/requirements.json): exact metric/unit, publisher scale.
PUBLISHER_SCALE = {'CES0000000001': ('nonfarm_payroll_employment', 'thousand_persons', 1)}
# CPS (ln.data.1.AllData) national headline series normalized; all other CPS series are skipped.
CPS_SERIES = {
    'LNS14000000': ('unemployment_rate', 'percent', 1, {}),
    'LNU04000000': ('unemployment_rate', 'percent', 1, {}),
    'LNS11000000': ('labor_force', 'persons', 1000, {}),
    'LNS12000000': ('employment', 'persons', 1000, {}),
    'LNS13000000': ('unemployed', 'persons', 1000, {}),
    'LNS11300000': ('labor_force_participation_rate', 'percent', 1, {}),
    'LNS12300000': ('employment_population_ratio', 'percent', 1, {}),
    'LNS13327709': ('unemployment_rate_u6', 'percent', 1, {}),
    'LNS13008636': ('long_term_unemployed_27_weeks_plus', 'persons', 1000, {}),
    'LNS13023621': ('unemployed_job_losers', 'persons', 1000, {}),
    'LNS12032194': ('part_time_for_economic_reasons', 'persons', 1000, {}),
    'LNS11300060': ('labor_force_participation_rate', 'percent', 1, {'age': '25-54'}),
    'LNS12300060': ('employment_population_ratio', 'percent', 1, {'age': '25-54'}),
    'LNS14000001': ('unemployment_rate', 'percent', 1, {'sex': 'men'}),
    'LNS14000002': ('unemployment_rate', 'percent', 1, {'sex': 'women'}),
    'LNS14000003': ('unemployment_rate', 'percent', 1, {'race': 'white'}),
    'LNS14000006': ('unemployment_rate', 'percent', 1, {'race': 'black_or_african_american'}),
    'LNS14000009': ('unemployment_rate', 'percent', 1, {'ethnicity': 'hispanic_or_latino'}),
    'LNS14000012': ('unemployment_rate', 'percent', 1, {'age': '16-19'}),
    'LNS14027659': ('unemployment_rate', 'percent', 1, {'age': '25+', 'education': 'less_than_high_school_diploma'}),
    'LNS14027662': ('unemployment_rate', 'percent', 1, {'age': '25+', 'education': 'bachelors_degree_or_higher'}),
}
MAPPING_FILES = {'sm.series', 'sm.industry', 'sm.area', 'sm.state', 'sm.data_type', 'sm.supersector', 'sm.footnote',
                 'la.area', 'la.measure', 'la.area_type', 'la.footnote', 'jt.industry', 'jt.state', 'jt.dataelement',
                 'jt.ratelevel', 'jt.sizeclass', 'jt.area', 'jt.footnote', 'ce.series', 'ce.industry', 'ce.datatype',
                 'ce.supersector', 'ce.footnote'}


def run(context):
    coverage = getattr(context, 'raw_coverage', None)
    if coverage is None:
        yield from _legacy_sample(context)
        return
    for index, _ in enumerate(context.raw_inputs):
        info = context.raw_coverage(index)
        if info['sampled'] or info['layout'] != 'shards':
            yield from _legacy_sample(context, only=index)
        else:
            yield from Full(context, index).run()


def qcew_industry(code, level, year):
    """QCEW industry identifier: totals, domains and supersectors are BLS aggregates; others are NAICS
    codes in the NAICS vintage QCEW used for that year."""
    if code == '10':
        return 'bls:qcew_industry:10'
    if int(level) % 10 in (2, 3):
        return 'bls:qcew_industry:' + code
    # QCEW NAICS-based history for 1990-2001 is published on the NAICS 2002 basis.
    vintage = ('naics2022' if year >= 2022 else 'naics2017' if year >= 2017 else 'naics2012' if year >= 2012
               else 'naics2007' if year >= 2007 else 'naics2002')
    return f'{vintage}:{code}'


class Full:
    def __init__(self, context, index):
        self.context, self.index = context, index
        self.coverage = context.raw_coverage(index)
        self.shards = {shard_name(s): s for s in context.raw_shards(index)}
        self.params = context.parameters

    # ----- emission primitives ----------------------------------------------------------------
    def evidence(self, locator):
        return self.context.raw_evidence(locator, self.index)

    def entity(self, shard, key, typ, label, seen, locator, **attrs):
        if key in seen:
            return key
        seen.add(key)
        attrs.update(source_dataset=DATASET)
        self.out.append({'kind': 'entity', 'id': f'{DATASET}:entity:' + digest([shard['index'], key]), 'entity_id': key,
                         'entity_type': typ, 'label': label or key, 'observed_at': shard['retrieved_at'],
                         'evidence': self.evidence(locator), 'attributes': attrs})
        return key

    def within(self, shard, subject, target, seen, locator):
        if (subject, target) in seen:
            return
        seen.add((subject, target))
        self.out.append({'kind': 'assertion', 'id': f'{DATASET}:within:' + digest([shard['index'], subject, target]),
                         'subject': subject, 'predicate': 'within', 'object': target, 'observed_at': shard['retrieved_at'],
                         'evidence': self.evidence(locator), 'attributes': {'source_dataset': DATASET}})

    def geo_state(self, shard, fips, seen, locator):
        us = self.entity(shard, 'geo:US', 'country', 'United States', seen, locator)
        if fips in ('00', 'US'):
            return us
        key = self.entity(shard, 'geo:US:state:' + fips, 'state', STATE_NAMES.get(fips) or 'US state FIPS ' + fips, seen,
                          locator, state_fips=fips)
        self.within(shard, key, us, seen, locator)
        return key

    def observation(self, shard, identity, locator, subject, metric, value, unit, bounds, dimensions, attrs,
                    missing_reason='source_missing_or_suppressed'):
        # BLS flat files carry only the current vintage: expose release-time bounds for point-in-time filtering.
        attrs = {**attrs, 'vintage': 'current_at_retrieval', 'realtime_start': shard['retrieved_at'][:10], 'realtime_end': None}
        if dimensions.get('series_id'):
            attrs['series_id'] = attrs['source_series'] = dimensions['series_id']
        if subject.startswith('geo:') and 'geography' not in dimensions:
            dimensions = {**dimensions, 'geography': subject}
        record = {'kind': 'observation', 'id': f'{DATASET}:obs:' + digest(identity), 'observed_at': shard['retrieved_at'],
                  'subject': subject, 'metric': metric, 'value': value, 'unit': unit,
                  'valid_from': bounds[0], 'valid_to': bounds[1], 'dimensions': dimensions,
                  'evidence': self.evidence(locator), 'attributes': attrs}
        if value is None:
            record['missing_reason'] = missing_reason
        return record

    def mapping(self, name, key, value=None):
        shard = self.shards.get(name)
        return mapping(shard['path'], key, value) if shard else {}

    # ----- driver ----------------------------------------------------------------------------
    def run(self):
        global STATE_NAMES
        STATE_NAMES = self.mapping('sm.state', 'state_code', 'state_name') or self.mapping('jt.state', 'state_code', 'state_text')
        for name, shard in self.shards.items():
            if name in MAPPING_FILES:
                continue
            if name.startswith('sm.data.'):
                handler = self.sm
            elif name.startswith('la.data.'):
                handler = self.laus
            elif name.startswith('jt.data.'):
                handler = self.jolts
            elif name.startswith('ce.data.'):
                handler = self.ces
            elif name.startswith('ln.data.'):
                handler = self.cps
            elif name.endswith('_qtrly_singlefile.zip'):
                handler = self.qcew_quarterly
            elif name.endswith('_annual_singlefile.zip'):
                handler = self.qcew
            elif name.startswith('oesm') and name.endswith('.zip'):
                handler = self.oews
            else:
                raise ValueError(f'{DATASET}: unrecognized shard file {name}')
            self.out = []
            for record in handler(shard, name):
                yield record
            yield from self.out
            self.out = []

    def _flush(self):
        out, self.out = self.out, []
        return out

    def _time_series(self, shard, name, decode):
        """Common loop for BLS time.series data files. ``decode(series_id, seen, locator)`` returns
        (subject, metric, unit, scale, dimensions, attrs) or None to skip."""
        seen, cache = set(), {}
        for line, row in tab_rows(shard['path']):
            series = row['series_id']
            bounds = period_bounds(row['year'], row['period'])
            if bounds is None:
                continue
            locator = f'shard:{shard["index"]}/line:{line}'
            if series not in cache:
                if len(cache) > 200000:
                    cache.clear()
                cache[series] = decode(series, seen, locator)
            decoded = cache[series]
            if decoded is None:
                continue
            subject, metric, unit, scale, dims, attrs = decoded
            value = number(row['value'])
            if value is not None and scale != 1:
                value = round(value * scale, 6)
                value = int(value) if float(value).is_integer() else value
            notes = footnotes(row.get('footnote_codes'))
            attributes = {'footnote_codes': notes} if notes else {}
            if 'P' in notes:
                attributes['preliminary'] = True
            attributes.update(attrs)
            yield from self._flush()
            yield self.observation(shard, [name, series, row['year'], row['period']], locator, subject, metric, value, unit,
                                   bounds, {**dims, 'survey': attrs.get('survey'), 'frequency': bounds[2], 'period_type': bounds[3]},
                                   attributes,
                                   'source_missing_or_unavailable')

    # ----- SM: state and metro CES ----------------------------------------------------------
    def sm(self, shard, name):
        areas = self.mapping('sm.area', 'area_code', 'area_name')
        industries = self.mapping('sm.industry', 'industry_code', 'industry_name')

        def decode(series, seen, locator):
            if len(series) != 20 or not series.startswith('SM'):
                raise ValueError(f'{locator}: unexpected SM series id {series}')
            seasonal, state, area, industry, dtype = series[2], series[3:5], series[5:10], series[10:18], series[18:20]
            if dtype not in CES_TYPES:
                return None
            metric, unit, scale = CES_TYPES[dtype]
            subject = self.geo_state(shard, state, seen, locator)
            dims = {'series_id': series, 'seasonal_adjustment': 'SA' if seasonal == 'S' else 'NSA', 'state_fips': state}
            if area != '00000':
                subject = self.entity(shard, 'geo:US:msa:' + area, 'jurisdiction', areas.get(area), seen, locator,
                                      cbsa_or_necta_code=area, bls_area_code=area)
                dims['geography_part'] = 'state_portion' if state != '00' else 'whole_area'
            ind = self.entity(shard, 'bls:ces_industry:' + industry, 'industry', industries.get(industry), seen, locator,
                              ces_industry_code=industry)
            dims['industry'] = ind
            return subject, metric, unit, scale, dims, {'survey': 'CES-SM'}
        yield from self._time_series(shard, name, decode)

    # ----- LAUS ------------------------------------------------------------------------------
    def laus(self, shard, name):
        areas = self.mapping('la.area', 'area_code')

        def decode(series, seen, locator):
            if len(series) != 20 or not series.startswith('LA'):
                raise ValueError(f'{locator}: unexpected LAUS series id {series}')
            seasonal, area, measure = series[2], series[3:18], series[18:20]
            if measure not in LAUS_MEASURES:
                return None
            metric, unit = LAUS_MEASURES[measure]
            meta = areas.get(area) or {}
            label = meta.get('area_text')
            prefix = area[:2]
            if prefix == 'ST':
                subject = self.geo_state(shard, area[2:4], seen, locator)
            elif prefix == 'CN':
                state = self.geo_state(shard, area[2:4], seen, locator)
                subject = self.entity(shard, 'geo:US:county:' + area[2:7], 'county', label, seen, locator, county_fips=area[2:7])
                self.within(shard, subject, state, seen, locator)
            elif prefix == 'MT':
                subject = self.entity(shard, 'geo:US:msa:' + area[4:9], 'jurisdiction', label, seen, locator,
                                      cbsa_code=area[4:9], laus_area_code=area)
            else:
                subject = self.entity(shard, 'bls:laus_area:' + area, 'jurisdiction', label, seen, locator,
                                      laus_area_code=area, laus_area_type=meta.get('area_type_code'))
            dims = {'series_id': series, 'seasonal_adjustment': 'SA' if seasonal == 'S' else 'NSA'}
            return subject, metric, unit, 1, dims, {'survey': 'LAUS'}
        yield from self._time_series(shard, name, decode)

    # ----- JOLTS -----------------------------------------------------------------------------
    def jolts(self, shard, name):
        industries = self.mapping('jt.industry', 'industry_code', 'industry_text')
        sizes = self.mapping('jt.sizeclass', 'sizeclass_code', 'sizeclass_text')

        def decode(series, seen, locator):
            if len(series) != 21 or not series.startswith('JT'):
                raise ValueError(f'{locator}: unexpected JOLTS series id {series}')
            seasonal, industry, state, area, size, element, level = (series[2], series[3:9], series[9:11], series[11:16],
                                                                   series[16:18], series[18:20], series[20])
            if element in JOLTS_ELEMENTS:
                metric = JOLTS_ELEMENTS[element] + ('_rate' if level == 'R' else '')
                unit, scale = ('percent', 1) if level == 'R' else ('persons', 1000)
            elif element == 'UO':
                metric, unit, scale = 'unemployed_per_job_opening', 'ratio', 1
            elif element == 'UN':
                metric, unit, scale = 'unemployment_rate', 'percent', 1
            elif element in ('R1', 'R2'):
                metric, unit, scale = ('survey_first_closing_response_rate' if element == 'R1'
                                       else 'survey_second_closing_response_rate'), 'percent', 1
            else:
                return None
            subject = self.geo_state(shard, state, seen, locator)
            ind = self.entity(shard, 'bls:jolts_industry:' + industry, 'industry', industries.get(industry), seen, locator,
                              jolts_industry_code=industry)
            dims = {'series_id': series, 'seasonal_adjustment': 'SA' if seasonal == 'S' else 'NSA', 'industry': ind,
                    'establishment_size_class': sizes.get(size, size)}
            if area != '00000':
                dims['jolts_area_code'] = area
            return subject, metric, unit, scale, dims, {'survey': 'JOLTS'}
        yield from self._time_series(shard, name, decode)

    # ----- national CES ----------------------------------------------------------------------
    def ces(self, shard, name):
        series_map = self.mapping('ce.series', 'series_id')
        industries = self.mapping('ce.industry', 'industry_code')

        def decode(series, seen, locator):
            meta = series_map.get(series)
            if meta is None:
                if len(series) != 13:
                    raise ValueError(f'{locator}: unknown CES series {series}')
                meta = {'industry_code': series[3:11], 'data_type_code': series[11:13], 'seasonal': series[2]}
            dtype = meta['data_type_code']
            if dtype not in CES_TYPES:
                return None
            metric, unit, scale = CES_TYPES[dtype]
            if series in PUBLISHER_SCALE:  # estimation contract: publisher units, exact metric name
                metric, unit, scale = PUBLISHER_SCALE[series]
            industry = meta['industry_code']
            imeta = industries.get(industry) or {}
            us = self.geo_state(shard, '00', seen, locator)
            ind = self.entity(shard, 'bls:ces_industry:' + industry, 'industry', imeta.get('industry_name'), seen, locator,
                              ces_industry_code=industry, naics_code=imeta.get('naics_code'))
            dims = {'series_id': series, 'seasonal_adjustment': 'SA' if meta['seasonal'] == 'S' else 'NSA', 'industry': ind}
            return us, metric, unit, scale, dims, {'survey': 'CES'}
        yield from self._time_series(shard, name, decode)

    # ----- CPS national headline series -----------------------------------------------------
    def cps(self, shard, name):
        def decode(series, seen, locator):
            spec = CPS_SERIES.get(series)
            if spec is None:
                return None
            metric, unit, scale, extra = spec
            us = self.geo_state(shard, '00', seen, locator)
            dims = {'series_id': series, 'seasonal_adjustment': 'SA' if series[2] == 'S' else 'NSA', **extra}
            return us, metric, unit, scale, dims, {'survey': 'CPS'}
        yield from self._time_series(shard, name, decode)

    # ----- QCEW annual singlefiles -----------------------------------------------------------
    def qcew(self, shard, name):
        levels = set(self.params.get('qcew_agglvl_codes') or [])
        seen = set()
        for locator, row in iter_rows([shard], {'format': 'csv', 'members': ['*.csv']}):
            level = row['agglvl_code']
            if levels and level not in levels:
                continue
            if row['qtr'] != 'A' or row['size_code'] != '0':
                continue
            year = int(row['year'])
            area = row['area_fips']
            subject = self._qcew_area(shard, area, seen, locator)
            if subject is None:
                continue
            code = row['industry_code']
            industry = qcew_industry(code, level, year)
            dims = {'industry': industry, 'ownership': QCEW_OWNERSHIP.get(row['own_code'], row['own_code']),
                    'agglvl_code': level, 'survey': 'QCEW', 'frequency': 'annual', 'period_type': 'annual_average'}
            suppressed = row['disclosure_code'] == 'N'
            attrs = {'survey': 'QCEW', 'own_code': row['own_code'], 'area_fips': area, 'industry_code': code}
            if suppressed:
                attrs['disclosure_code'] = 'N'
            bounds = (f'{year:04d}-01-01', f'{year + 1:04d}-01-01')
            for column, metric, unit in QCEW_METRICS:
                value = None if suppressed else number(row[column])
                if suppressed and column == 'annual_avg_estabs':
                    value = number(row[column])  # establishment counts are published even when wages are not
                yield from self._flush()
                yield self.observation(shard, ['qcew', year, area, row['own_code'], code, level, metric], locator, subject,
                                       metric, value, unit, bounds, dims, {**attrs, 'source_field': column},
                                       'suppressed_confidentiality')
        yield from self._flush()

    # ----- QCEW quarterly singlefiles --------------------------------------------------------
    def qcew_quarterly(self, shard, name):
        levels = set(self.params.get('qcew_agglvl_codes') or [])
        seen = set()
        for locator, row in iter_rows([shard], {'format': 'csv', 'members': ['*.csv']}):
            level = row['agglvl_code']
            if levels and level not in levels:
                continue
            if row['size_code'] != '0' or row['qtr'] not in ('1', '2', '3', '4'):
                continue
            year, quarter = int(row['year']), int(row['qtr'])
            area = row['area_fips']
            subject = self._qcew_area(shard, area, seen, locator)
            if subject is None:
                continue
            code = row['industry_code']
            dims = {'industry': qcew_industry(code, level, year),
                    'ownership': QCEW_OWNERSHIP.get(row['own_code'], row['own_code']), 'agglvl_code': level,
                    'survey': 'QCEW'}
            suppressed = row['disclosure_code'] == 'N'
            attrs = {'survey': 'QCEW', 'own_code': row['own_code'], 'area_fips': area, 'industry_code': code}
            if suppressed:
                attrs['disclosure_code'] = 'N'
            quarter_bounds = period_bounds(year, f'Q0{quarter}')
            items = [('qtrly_estabs', 'establishment_count', 'establishments', quarter_bounds, False)]
            items += [(f'month{m}_emplvl', 'employment', 'persons', period_bounds(year, f'M{3 * quarter - 3 + m:02d}'), True)
                      for m in (1, 2, 3)]
            items += [('total_qtrly_wages', 'total_quarterly_wages', 'USD', quarter_bounds, True),
                      ('avg_wkly_wage', 'average_weekly_wage', 'USD_per_week', quarter_bounds, True)]
            for column, metric, unit, bounds, masked in items:
                value = None if suppressed and masked else number(row[column])
                yield from self._flush()
                yield self.observation(shard, ['qcewq', year, quarter, area, row['own_code'], code, level, column], locator,
                                       subject, metric, value, unit, bounds,
                                       {**dims, 'frequency': bounds[2], 'period_type': bounds[3]},
                                       {**attrs, 'source_field': column}, 'suppressed_confidentiality')
        yield from self._flush()

    def _qcew_area(self, shard, area, seen, locator):
        if area == 'US000':
            return self.geo_state(shard, '00', seen, locator)
        if area.startswith('CS'):
            return self.entity(shard, 'geo:US:csa:' + area[2:], 'jurisdiction', None, seen, locator, qcew_area_fips=area)
        if area.startswith('C'):
            return self.entity(shard, 'geo:US:msa:' + area[1:] + '0', 'jurisdiction', None, seen, locator, qcew_area_fips=area)
        if area.isdigit() and len(area) == 5:
            if area.endswith('000'):
                return self.geo_state(shard, area[:2], seen, locator)
            state = self.geo_state(shard, area[:2], seen, locator)
            key = self.entity(shard, 'geo:US:county:' + area, 'county', None, seen, locator, county_fips=area,
                              unknown_or_undefined=area.endswith('999'))
            self.within(shard, key, state, seen, locator)
            return key
        return None

    # ----- OEWS all-data workbook ------------------------------------------------------------
    def oews(self, shard, name):
        """OEWS workbooks inside a ZIP: all_data_M_YYYY (all areas/industries), state_MYYYY_dl, MSA_MYYYY_dl and
        BOS_MYYYY_dl (nonmetropolitan areas). Pre-2019 files lack AREA_TYPE/NAICS and use OCC_GROUP."""
        archive = zipfile.ZipFile(shard['path'])
        members = []
        for member in archive.namelist():
            match = OEWS_MEMBER.search(member)
            if match:
                members.append((member, match[1].lower(), int(match[2])))
        if not members:
            archive.close()
            raise ValueError(f'{name}: no OEWS data workbook found')
        seen = set()
        tmp = tempfile.TemporaryDirectory(prefix='bls_labor_oews_')
        try:
            for member, kind, year in sorted(members):
                yield from self._oews_workbook(shard, archive, member, kind, year, Path(tmp.name), seen)
        finally:
            archive.close()
            tmp.cleanup()
        yield from self._flush()

    def _oews_workbook(self, shard, archive, member, kind, year, tmp, seen):
        bounds = (f'{year:04d}-05-01', f'{year:04d}-06-01')
        soc = 'soc2018' if year >= 2019 else 'soc2010'
        default_type = {'state_m': '2', 'msa_m': '4', 'bos_m': '6'}.get(kind)
        # The workbook is a ZIP inside the ZIP; copy it to a temporary file for random access.
        target = tmp / 'workbook.xlsx'
        with archive.open(member) as source, open(target, 'wb') as output:
            shutil.copyfileobj(source, output, 1 << 20)
        book = zipfile.ZipFile(target)
        try:
            sheet = next(iter(sheet_members(book).values()))
            for row_number, row in xlsx_table(book, sheet):
                row = {k.upper().replace(' ', '_'): (v or '').strip() for k, v in row.items()}
                if not row.get('OCC_CODE'):
                    continue
                row.setdefault('AREA_TYPE', '')
                row['AREA_TYPE'] = row['AREA_TYPE'] or default_type or ''
                row['AREA_TITLE'] = row.get('AREA_TITLE') or row.get('AREA_NAME') or row.get('STATE') or ''
                group = row.get('O_GROUP') or row.get('OCC_GROUP') or ''
                naics = row.get('NAICS') or '000000'
                industry_group = row.get('I_GROUP') or 'cross-industry'
                own = row.get('OWN_CODE') or ''
                locator = f'shard:{shard["index"]}/member:{member}/sheet:{sheet}/row:{row_number}'
                subject = self._oews_area(shard, row, seen, locator)
                if subject is None:
                    continue
                # OEWS repeats some SOC codes as both 'broad' and 'detailed' rows and some NAICS codes at several
                # industry levels; both groups are part of the identity so each published row stays one record.
                occ = self.entity(shard, f'{soc}:' + row['OCC_CODE'], 'occupation', row.get('OCC_TITLE'), seen, locator,
                                  soc_code=row['OCC_CODE'], soc_vintage=soc)
                # OEWS pads NAICS to six characters (e.g. 541500 = 4-digit 5415); keep the OEWS code verbatim.
                industry = 'cross_industry' if naics in ('000000', '000001') else 'bls:oews_industry:' + naics
                dims = {'occupation': occ, 'occupation_group': group, 'industry': industry, 'naics_code': naics,
                        'industry_group': industry_group, 'ownership_code': own, 'survey': 'OEWS',
                        'frequency': 'annual', 'period_type': 'survey_reference_may'}
                attrs = {'survey': 'OEWS', 'estimate_panel': f'May {year - 2}-May {year}', 'oews_file': kind.rstrip('_m') or kind}
                if row.get('EMP_PRSE') not in (None, ''):
                    attrs['employment_rse_percent'] = number(row['EMP_PRSE'])
                for column, metric, unit in OEWS_METRICS:
                    text = row.get(column, '')
                    value = number(text)
                    reason = OEWS_SPECIAL.get(text, 'source_missing_or_suppressed')
                    extra = dict(attrs, source_field=column)
                    if text == '#':
                        extra['top_coded'] = True
                    yield from self._flush()
                    yield self.observation(shard, ['oews', year, row['AREA'], row['AREA_TYPE'], naics, industry_group, own,
                                                   row['OCC_CODE'], group, metric], locator, subject, metric, value, unit,
                                           bounds, dims, extra, reason)
        finally:
            book.close()
            target.unlink()

    def _oews_area(self, shard, row, seen, locator):
        kind, area = row.get('AREA_TYPE'), row.get('AREA', '')
        if kind == '1':
            return self.geo_state(shard, '00', seen, locator)
        if kind in ('2', '3'):
            return self.geo_state(shard, area.zfill(2), seen, locator)
        if kind == '4':
            return self.entity(shard, 'geo:US:msa:' + area.zfill(5), 'jurisdiction', row.get('AREA_TITLE'), seen, locator,
                               cbsa_code=area.zfill(5))
        if kind == '6':
            return self.entity(shard, 'bls:oews_area:' + area, 'jurisdiction', row.get('AREA_TITLE'), seen, locator,
                               oews_area_code=area, nonmetropolitan=True)
        return None


# ----- legacy bounded API sample ------------------------------------------------------------------

def _legacy_sample(context, only=None):
    dataset = DATASET
    if not context.raw_inputs:
        raise ValueError(f'{dataset}: no sample artifact supplied')
    seen = set()
    for index, ref in enumerate(context.raw_inputs):
        if only is not None and index != only:
            continue
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for number_, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            evidence = context.raw_evidence(f'line:{number_}', index)
            out = []

            def base(kind, identity, **fields):
                record = {'kind': kind, 'id': 'normalized:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': evidence, 'attributes': {'source_row': row, 'source_dataset': dataset}, **fields}
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
                missing = value is None or (isinstance(value, str) and value.strip() in ('', '-', '(D)', 'D', 'S', 'N', 'NA', 'null'))
                if not missing:
                    value = float(value)
                    if value.is_integer():
                        value = int(value)
                r = base('observation', ['obs', number_, subject, metric, start], subject=subject, metric=metric, value=None if missing else value, unit=unit, dimensions={'subject': subject})
                r['attributes'].update(source_unit=unit, **attrs)
                if missing:
                    r['missing_reason'] = 'source_missing_or_suppressed'
                if start:
                    r['valid_from'] = start
                if end:
                    r['valid_to'] = end
                return r

            config = receipt.get('source', {}).get('sampling', {}).get('config', {})
            series = config.get('body', {}).get('seriesid', [])
            if series != ['LNS14000000']:
                raise ValueError('BLS adapter requires receipt identifying LNS14000000')
            key = entity('cohort:US:civilian-labor-force:16plus', 'population_cohort', 'US civilian labor force age 16+', aggregate=True)
            rel(key, 'within', geo())
            month = int(row['period'][1:])
            y = int(row['year'])
            if not 1 <= month <= 12:
                raise ValueError('BLS annual average requires separate adapter')
            start = f'{y:04d}-{month:02d}-01'
            end = f'{y + 1:04d}-01-01' if month == 12 else f'{y:04d}-{month + 1:02d}-01'
            obs(key, 'unemployment_rate', row['value'], 'percent', start, end, series_id=series[0], seasonally_adjusted=True)
            yield from out
