"""NASS Quick Stats crops.

Sample path (single JSONL payload): annual state corn yield; suppressions remain unknown,
never zero. Full path (sharded bulk ``qs.crops_*.txt.gz``): streamed and filtered to
SURVEY/CENSUS, NATIONAL/STATE/COUNTY, year >= min_year, TOTAL domain, and
annual / point-in-time / single-month periods; one observation per published value.
"""
from worldmodel.source_records import Emitter,sampled_rows,number
from worldmodel.util import digest


def run(context):
    coverage=getattr(context,'raw_coverage',None)
    if coverage is not None and coverage()['layout']=='shards':
        yield from _full(context)
        return
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        if row['commodity_desc']!='CORN' or row['statisticcat_desc']!='YIELD' or row['unit_desc']!='BU / ACRE' or row['agg_level_desc']!='STATE' or row['freq_desc']!='ANNUAL':
            raise ValueError('Adapter requires explicit annual state corn yield in BU / ACRE')
        year=int(row['year']);state=str(row['state_fips_code']).zfill(2)
        if not 1900<=year<=2100 or len(state)!=2 or not state.isdigit():raise ValueError('Invalid source year/geography')
        emit.at(index,line,row,receipt)
        place=emit.entity('us:state:'+state,'state',row.get('state_name'))
        cohort=emit.entity('nass:cohort:'+digest([state,row['commodity_desc'],row.get('domain_desc'),row.get('domaincat_desc')]),'aggregate_cohort')
        emit.relation(cohort,'within',place)
        value=str(row['Value']).strip();suppressed=value.startswith('(')
        record=emit.observation(cohort,'corn_yield',None if suppressed else number(value),'BU / ACRE')
        if suppressed:record['missing_reason']='NASS suppression '+value
        record.update(valid_from=f'{year}-01-01',valid_to=f'{year+1}-01-01')
        record['attributes'].update(suppression_code=value if suppressed else None,source_series=row.get('short_desc'),
                                    methodology='NASS published aggregate; no individual farms inferred')
        yield from emit.rows


MONTHS = {m: i for i, m in enumerate('JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC'.split(), 1)}
SUPPRESSION = {'(D)': 'withheld_to_avoid_disclosing_individual_operations', '(Z)': 'less_than_half_unit_shown',
               '(S)': 'insufficient_reports_to_publish', '(NA)': 'not_available', '(X)': 'not_applicable',
               '(O)': 'unavailable', '(1)': 'footnote_1', '(DU)': 'withheld_duplicate', '(-)': 'none_or_rounds_to_zero'}
ALL = ('ALL CLASSES', 'ALL PRODUCTION PRACTICES', 'ALL UTILIZATION PRACTICES', '')


def _slug(text):
    import re
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def _period(freq, ref, year, month_bounds, year_bounds):
    """Return (valid_from, valid_to, frequency) or None for unsupported periods."""
    from datetime import date, timedelta
    if freq == 'ANNUAL':
        if ref == 'YEAR' or ref == 'MARKETING YEAR' or ref.startswith('YEAR - '):
            start, end = year_bounds(year)
            return start, end, 'annual'
        return None
    if freq == 'MONTHLY' and ref in MONTHS:
        start, end = month_bounds(year, MONTHS[ref])
        return start, end, 'monthly'
    if freq == 'POINT IN TIME':
        position, _, month = ref.partition(' OF ') if ' OF ' in ref else ref.partition(' ')
        month = month.strip()
        if month not in MONTHS:
            return None
        if position == 'FIRST':
            day = date(year, MONTHS[month], 1)
        elif position == 'MID':
            day = date(year, MONTHS[month], 15)
        elif position == 'END':
            start, end = month_bounds(year, MONTHS[month])
            day = date.fromisoformat(end) - timedelta(days=1)
        else:
            return None
        return day.isoformat(), (day + timedelta(days=1)).isoformat(), 'point_in_time'
    return None


def _full(context):
    import hashlib
    from .evidence import Evidence, month_bounds, num, year_bounds
    params = context.parameters
    min_year = int(params.get('min_year', 2000))
    sources = set(params.get('sources', ['SURVEY', 'CENSUS']))
    levels = set(params.get('agg_levels', ['NATIONAL', 'STATE', 'COUNTY']))
    domains = set(params.get('domains', ['TOTAL']))
    reader = {'format': 'tsv', 'compression': 'gzip', 'quoting': 'none', 'strict': False, 'encoding': 'utf-8'}
    series_cache = {}
    for index, _ in enumerate(context.raw_inputs):
        out = Evidence(context, 'nass', index)
        try:
            for locator, row in context.raw_rows(index, **reader):
                if row['SOURCE_DESC'] not in sources or row['AGG_LEVEL_DESC'] not in levels:
                    continue
                if row['DOMAIN_DESC'] not in domains:
                    continue
                year_text = row['YEAR']
                if not year_text.isdigit() or int(year_text) < min_year:
                    continue
                year = int(year_text)
                period = _period(row['FREQ_DESC'], row['REFERENCE_PERIOD_DESC'], year, month_bounds, year_bounds)
                if period is None:
                    continue
                start, end, frequency = period
                level = row['AGG_LEVEL_DESC']
                us = 'geo:US'
                record = out.entity(us, 'country', 'United States', locator, iso3='USA')
                if record:
                    yield record
                geo = us
                if level in ('STATE', 'COUNTY'):
                    state = row['STATE_FIPS_CODE'].zfill(2)
                    geo = 'geo:US:state:' + state
                    record = out.entity(geo, 'state', row['STATE_NAME'].title(), locator, state_alpha=row['STATE_ALPHA'])
                    if record:
                        yield record
                        yield out.relation(geo, 'within', us, locator, identity='within:' + geo)
                    if level == 'COUNTY':
                        county = row['COUNTY_ANSI'] or row['COUNTY_CODE']
                        parent = geo
                        if county in ('', '998') or not county.isdigit():
                            asd = row['ASD_CODE'] or 'NA'
                            geo = f'nass:geo:US:state:{state}:asd:{asd}:other_counties'
                            record = out.entity(geo, 'jurisdiction', f"{row['ASD_DESC'].title()} other (combined) counties, {row['STATE_ALPHA']}",
                                                locator, aggregate=True, asd_code=asd)
                        else:
                            geo = 'geo:US:county:' + state + county.zfill(3)
                            record = out.entity(geo, 'county', f"{row['COUNTY_NAME'].title()}, {row['STATE_ALPHA']}", locator,
                                                asd_code=row['ASD_CODE'])
                        if record:
                            yield record
                            yield out.relation(geo, 'within', parent, locator, identity='within:' + geo)
                commodity = 'nass:commodity:' + _slug(row['COMMODITY_DESC'])
                record = out.entity(commodity, 'commodity', row['COMMODITY_DESC'].title(), locator,
                                    sector=row['SECTOR_DESC'], group=row['GROUP_DESC'])
                if record:
                    yield record
                series_text = row['SHORT_DESC'] + '|' + row['DOMAINCAT_DESC']
                series = series_cache.get(series_text)
                if series is None:
                    if len(series_cache) > 200_000:
                        series_cache.clear()
                    series = series_cache[series_text] = hashlib.sha256(series_text.encode()).hexdigest()[:20]
                raw_value = row['VALUE'].strip().strip('\x00')
                if raw_value.startswith('('):
                    value, missing = None, 'nass_suppressed_' + SUPPRESSION.get(raw_value, raw_value)
                else:
                    try:
                        value, missing = num(raw_value), 'source_blank'
                    except ValueError:
                        value, missing = None, 'unparseable_source_value'
                metric = _slug(row['STATISTICCAT_DESC'])
                dims = {'commodity': commodity, 'series': 'nass:series:' + series, 'program': row['SOURCE_DESC'].lower(),
                        'frequency': frequency, 'reference_period': row['REFERENCE_PERIOD_DESC'].lower()}
                for field, name in (('CLASS_DESC', 'class'), ('PRODN_PRACTICE_DESC', 'production_practice'),
                                    ('UTIL_PRACTICE_DESC', 'utilization_practice')):
                    if row[field] not in ALL:
                        dims[name] = row[field].lower()
                attrs = {'short_desc': row['SHORT_DESC']}
                if raw_value.startswith('('):
                    attrs['suppression_code'] = raw_value
                try:
                    cv = num((row.get('CV_%') or '').strip().strip('\x00'))
                except ValueError:
                    cv = None  # '(H)'/'(L)' high/low CV markers and malformed cells
                if cv is not None:
                    attrs['cv_percent'] = cv
                identity = f"{row['SOURCE_DESC'][0]}:{geo}:{series}:{year}:{_slug(row['REFERENCE_PERIOD_DESC'])}"
                if not out.keys.add('o|' + identity):
                    continue  # exact duplicate publication of the same series/geo/period
                yield out.observation(geo, metric, value, row['UNIT_DESC'], locator, valid_from=start, valid_to=end,
                                      identity=identity, dimensions=dims, missing_reason=missing,
                                      aggregate=True if level != 'COUNTY' or geo.endswith('other_counties') else None, **attrs)
        finally:
            out.close()
