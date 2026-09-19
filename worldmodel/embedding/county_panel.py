"""A dated US county panel and county graph assembled from published normalized datasets.

Every value keeps the date on which it became public (``dimensions.available_at``), derived
from a declared publication lag per source, so an as-of reader can reconstruct what was known
on any day. The lags are conservative statements about each publisher's release calendar, not
measured release dates: :data:`SOURCES` cites the rule for each one. Values are the *current*
vintage at retrieval; ``dimensions.revisions`` records which sources revise, which is the
leakage an as-of reader of this panel cannot remove.

Nothing here infers identity. Counties are keyed by the FIPS identifier every source already
publishes (``geo:US:county:SSCCC``); states come from the first two FIPS digits, which is the
FIPS code's own structure; CBSA membership and migration flows are published assertions.

Build and publish::

    python3 -m worldmodel embed-panel

The output is one content-addressed ``county_panel`` version: observation records (county x
feature x year), assertion records (dated county edges), and a ``report.json`` with counts,
the declared lags and every input pin.
"""
from collections import defaultdict
from datetime import date
import gzip
import json
import multiprocessing
import os

from ..estimation.loaders import catalog_ref, _records_path
from ..util import now

DATASET = 'county_panel'
ENTRYPOINT = 'worldmodel.embedding.county_panel:build'
FIRST_YEAR = 1980
MAX_EVIDENCE = 25
COUNTY = 'geo:US:county:'

#: Publication rule per source: months after the end of the reference period at which the value
#: is treated as public, the revision class, and the release fact the lag rests on.
SOURCES = {
    'qcew': {'dataset': 'bls_labor', 'lag_months': 9, 'revisions': 'minor',
             'rule': 'BLS publishes QCEW county annual averages for year y in early September of y+1; '
                     'the panel dates them at the end of September y+1.'},
    'laus': {'dataset': 'bls_labor', 'lag_months': 4, 'revisions': 'major',
             'rule': 'LAUS county annual averages for year y appear with the annual benchmark in April of y+1; '
                     'dated at the end of April y+1. Benchmarks re-estimate prior years, so revisions are major.'},
    'bea': {'dataset': 'bea_national_regional', 'lag_months': 12, 'revisions': 'major',
            'rule': 'BEA releases county personal income for y in November of y+1 and county GDP in December of y+1; '
                    'dated at the end of December y+1. Annual and comprehensive revisions rewrite history.'},
    'climdiv': {'dataset': 'noaa_climdiv', 'lag_months': 1, 'revisions': 'minor',
                'rule': 'nClimDiv county annual values for y are published in early January of y+1; dated at the end of '
                        'January y+1. Each release recomputes the record with the current homogenization.'},
    'storms': {'dataset': 'noaa_storm_events', 'lag_months': 4, 'revisions': 'minor',
               'rule': 'Storm Data events are finalized roughly 75 days after the month ends; county-year totals for y '
                       'are dated at the end of April y+1.'},
    'fema': {'dataset': 'openfema', 'lag_months': 0, 'revisions': 'none',
             'rule': 'A disaster declaration is public on its declaration date; county-year counts for y are dated '
                     'January 1 of y+1.'},
    'migration': {'dataset': 'irs_soi_migration', 'lag_months': 18, 'revisions': 'none',
                  'rule': 'IRS SOI county-to-county migration for filing years y to y+1 is released about 18 months after '
                          'the second filing year ends; dated 18 months after valid_to.'},
    'geography': {'dataset': 'census_geography', 'lag_months': None, 'revisions': 'static',
                  'rule': 'County internal points and land/water areas (2024 vintage) are treated as static geography, '
                          'available from 1980. This is a declared exception: boundary changes (e.g. the 2022 Connecticut '
                          'planning regions) are not versioned here.'},
    'cbsa': {'dataset': 'cbsa_delineations', 'lag_months': None, 'revisions': 'none',
             'rule': 'OMB delineations are public on their bulletin date (2018-09-14, 2023-07-21); no earlier '
                     'delineation is in the catalog, so origins before 2018-09-14 have no CBSA edges.'},
}

CBSA_BULLETINS = {'2018': '2018-09-14', '2023': '2023-07-21'}
QCEW_METRICS = ('employment', 'establishment_count', 'total_annual_wages', 'average_annual_pay')
LAUS_METRICS = ('employment', 'labor_force', 'unemployed', 'unemployment_rate')
BEA_METRICS = ('personal_income', 'population', 'per_capita_personal_income', 'wages_and_salaries', 'gdp', 'real_gdp',
               'earnings_by_place_of_work', 'net_earnings_by_place_of_residence', 'dividends_interest_and_rent',
               'personal_current_transfer_receipts', 'supplements_to_wages_and_salaries', 'proprietors_income',
               'farm_earnings', 'nonfarm_earnings', 'inflows_of_earnings', 'outflows_of_earnings')
#: BEA publishes gdp, real_gdp and earnings by industry under the same metric name; the panel keeps totals only.
BEA_TOTAL_INDUSTRY = 'all_industry_total'
#: Levels that cannot be zero or negative for a county that exists. The normalized BEA records carry 0 where
#: the area did not exist (Broomfield CO before 2001, reorganized Alaska boroughs); the panel treats those as
#: absent. Components that can be negative or zero (farm earnings, proprietors' income) are kept as published.
BEA_POSITIVE = ('population', 'personal_income', 'per_capita_personal_income', 'wages_and_salaries', 'gdp', 'real_gdp',
                'earnings_by_place_of_work', 'supplements_to_wages_and_salaries', 'dividends_interest_and_rent',
                'personal_current_transfer_receipts')
CLIMDIV_METRICS = ('average_temperature', 'precipitation', 'palmer_drought_severity_index')
STORM_METRICS = ('storm_damage_property', 'storm_damage_crops', 'storm_deaths', 'storm_injuries')
GEOGRAPHY_METRICS = ('latitude', 'longitude', 'land_area', 'water_area')

#: The three forecast targets of the places assay, as panel feature ids.
TARGETS = ('qcew:employment', 'qcew:establishment_count', 'bea:population')


def add_months(day, months):
    """Last day of the month ``months`` after the month containing ``day``."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    following = date(year + (month + 1) // 12, (month + 1) % 12 + 1, 1)
    return date.fromordinal(following.toordinal() - 1)


def year_available(source, year):
    """Public date of an annual value for ``year`` under the declared lag of ``source``."""
    return add_months(date(int(year), 12, 31), SOURCES[source]['lag_months']).isoformat()


def is_county(value):
    """A five-digit county FIPS id; ``SS000`` (state totals) and ``SS999`` (QCEW's unknown county) are not counties."""
    value = str(value or '')
    code = value[len(COUNTY):]
    return (value.startswith(COUNTY) and len(code) == 5 and code.isdigit()
            and not code.endswith('000') and not code.endswith('999'))


def state_of(county):
    return 'geo:US:state:' + county[len(COUNTY):len(COUNTY) + 2]


def _lines(path, required):
    """Decoded records whose raw line contains *every* fragment in ``required`` (canonical JSON)."""
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if all(fragment in line for fragment in required):
                yield json.loads(line)


class Collector:
    """Accumulates one value per (county, feature, year) and keeps its source record ids."""

    def __init__(self, source, ref):
        self.source, self.ref = source, ref
        self.values, self.ids, self.units = {}, defaultdict(list), {}
        self.duplicates = 0
        self.excluded = defaultdict(int)     # reason -> source values deliberately not used

    def unique(self, county, feature, year, value, unit, record_id):
        key = (county, feature, int(year))
        if key in self.values:
            self.duplicates += 1
            return
        self.values[key] = float(value)
        self.ids[key].append(record_id)
        self.units[feature] = unit

    def add(self, county, feature, year, value, unit, record_id):
        key = (county, feature, int(year))
        self.values[key] = self.values.get(key, 0.0) + float(value)
        if len(self.ids[key]) < MAX_EVIDENCE:
            self.ids[key].append(record_id)
        self.units[feature] = unit

    def result(self):
        return {'source': self.source, 'ref': self.ref, 'values': self.values, 'ids': dict(self.ids),
                'units': self.units, 'duplicates': self.duplicates, 'excluded': dict(self.excluded)}


def _collect_qcew(data_root):
    from ..store import Store
    store = Store(data_root)
    ref = catalog_ref(store, 'bls_labor')
    out = Collector('qcew', ref)
    required = ('"agglvl_code":"70"', '"period_type":"annual_average"', '"ownership":"total_covered"')
    for record in _lines(_records_path(store, ref), required):
        dims = record.get('dimensions') or {}
        county, metric = dims.get('geography'), record.get('metric')
        if record.get('kind') != 'observation' or metric not in QCEW_METRICS or not is_county(county):
            continue
        if dims.get('frequency') != 'annual' or record.get('value') is None:
            continue
        if (record.get('attributes') or {}).get('disclosure_code') == 'N':
            out.excluded['qcew_not_disclosed'] += 1      # BLS publishes 0 with code N for suppressed cells
            continue
        year = int(str(record['valid_from'])[:4])
        if year >= FIRST_YEAR:
            out.unique(county, f'qcew:{metric}', year, record['value'], record.get('unit'), record['id'])
    return out.result()


def _collect_qcew_sectors(data_root):
    """Private employment by two-digit NAICS sector (QCEW aggregation level 74, ownership private)."""
    from ..store import Store
    store = Store(data_root)
    ref = catalog_ref(store, 'bls_labor')
    out = Collector('qcew', ref)
    required = ('"agglvl_code":"74"', '"period_type":"annual_average"', '"metric":"employment"')
    for record in _lines(_records_path(store, ref), required):
        dims = record.get('dimensions') or {}
        county = dims.get('geography')
        if record.get('kind') != 'observation' or not is_county(county) or record.get('value') is None:
            continue
        if dims.get('frequency') != 'annual' or dims.get('ownership') != 'private':
            continue
        if (record.get('attributes') or {}).get('disclosure_code') == 'N':
            out.excluded['qcew_not_disclosed'] += 1
            continue
        code = str(dims.get('industry') or '').split(':')[-1]
        if not code or not all(len(p) == 2 and p.isdigit() for p in code.split('-')):
            continue
        year = int(str(record['valid_from'])[:4])
        if year >= FIRST_YEAR:
            out.unique(county, f'qcew_sector_employment:{code}', year, record['value'], record.get('unit'), record['id'])
    return out.result()


def _collect_laus(data_root):
    from ..store import Store
    store = Store(data_root)
    ref = catalog_ref(store, 'bls_labor')
    out = Collector('laus', ref)
    required = ('"survey":"LAUS"', '"period_type":"annual_average"', COUNTY)
    for record in _lines(_records_path(store, ref), required):
        dims = record.get('dimensions') or {}
        county, metric = dims.get('geography'), record.get('metric')
        if record.get('kind') != 'observation' or metric not in LAUS_METRICS or not is_county(county):
            continue
        if dims.get('frequency') != 'annual' or record.get('value') is None:
            continue
        year = int(str(record['valid_from'])[:4])
        if year >= FIRST_YEAR:
            out.unique(county, f'laus:{metric}', year, record['value'], record.get('unit'), record['id'])
    return out.result()


def _collect_bea(data_root):
    from ..store import Store
    store = Store(data_root)
    ref = catalog_ref(store, 'bea_national_regional')
    out = Collector('bea', ref)
    for record in _lines(_records_path(store, ref), (COUNTY, '"kind":"observation"')):
        county, metric = record.get('subject'), record.get('metric')
        if metric not in BEA_METRICS or not is_county(county) or record.get('value') is None:
            continue
        industry = (record.get('dimensions') or {}).get('industry')
        if industry is not None and industry != BEA_TOTAL_INDUSTRY:
            continue
        if metric in BEA_POSITIVE and float(record['value']) <= 0:
            out.excluded['bea_nonpositive_level'] += 1   # e.g. population 0 before a county existed
            continue
        year = int(str(record['valid_from'])[:4])
        if year >= FIRST_YEAR:
            out.unique(county, f'bea:{metric}', year, record['value'], record.get('unit'), record['id'])
    return out.result()


def _collect_climdiv(data_root):
    from ..store import Store
    store = Store(data_root)
    ref = catalog_ref(store, 'noaa_climdiv')
    out = Collector('climdiv', ref)
    for record in _lines(_records_path(store, ref), (COUNTY, '"frequency":"annual"')):
        county, metric = record.get('subject'), record.get('metric')
        if record.get('kind') != 'observation' or metric not in CLIMDIV_METRICS or not is_county(county):
            continue
        if record.get('value') is None:
            continue
        year = int(str(record['valid_from'])[:4])
        if year >= FIRST_YEAR:
            out.unique(county, f'climdiv:{metric}', year, record['value'], record.get('unit'), record['id'])
    return out.result()


def _collect_storms(data_root):
    from ..store import Store
    store = Store(data_root)
    ref = catalog_ref(store, 'noaa_storm_events')
    out = Collector('storms', ref)
    events = defaultdict(set)
    for record in _lines(_records_path(store, ref), (COUNTY, '"kind":"observation"')):
        county, metric = (record.get('dimensions') or {}).get('location'), record.get('metric')
        if metric not in STORM_METRICS or not is_county(county) or record.get('value') is None:
            continue
        year = int(str(record['valid_from'])[:4])
        if year < FIRST_YEAR:
            continue
        out.add(county, f'storms:{metric}', year, record['value'], record.get('unit'), record['id'])
        events[(county, year)].add(record.get('subject'))
    for (county, year), subjects in events.items():
        key = (county, 'storms:event_count', year)
        out.values[key] = float(len(subjects))
        out.ids[key] = sorted(s for s in subjects if s)[:MAX_EVIDENCE]
    out.units['storms:event_count'] = 'events'
    return out.result()


def _collect_fema(data_root):
    from ..store import Store
    store = Store(data_root)
    ref = catalog_ref(store, 'openfema')
    out = Collector('fema', ref)
    seen = set()
    for record in _lines(_records_path(store, ref), ('"event_type":"disaster_declaration"',)):
        attributes = record.get('attributes') or {}
        declaration = attributes.get('femaDeclarationString')
        year = int(str(record['occurred_at'])[:4])
        if year < FIRST_YEAR:
            continue
        for county in record.get('participants') or ():
            if not is_county(county) or (county, declaration) in seen:
                continue
            seen.add((county, declaration))
            out.add(county, 'fema:declarations', year, 1, 'declarations', record['id'])
            if attributes.get('declarationType') == 'DR':
                out.add(county, 'fema:major_disasters', year, 1, 'declarations', record['id'])
    return out.result()


def _collect_geography(data_root):
    from ..store import Store
    store = Store(data_root)
    ref = catalog_ref(store, 'census_geography')
    out = Collector('geography', ref)
    for record in _lines(_records_path(store, ref), (COUNTY, '"kind":"observation"')):
        county, metric = record.get('subject'), record.get('metric')
        if metric in GEOGRAPHY_METRICS and is_county(county) and record.get('value') is not None:
            out.unique(county, f'geography:{metric}', FIRST_YEAR, record['value'], record.get('unit'), record['id'])
    return out.result()


def _collect_edges(data_root):
    """Dated county edges: IRS migration flows and CBSA membership."""
    from ..store import Store
    store = Store(data_root)
    migration_ref = catalog_ref(store, 'irs_soi_migration')
    flows = {}
    required = ('"metric":"migration_individuals"', '"flow_type":"migration"', '"perspective":"inflow"')
    for record in _lines(_records_path(store, migration_ref), required):
        dims = record.get('dimensions') or {}
        origin, destination = dims.get('origin'), dims.get('destination')
        if not is_county(origin) or not is_county(destination) or origin == destination:
            continue
        if record.get('value') is None or float(record['value']) <= 0:
            continue
        end = date.fromisoformat(str(record['valid_to'])[:10])
        available = add_months(date.fromordinal(end.toordinal() - 1), SOURCES['migration']['lag_months'])
        key = (origin, destination, str(record['valid_from'])[:4])
        if key not in flows:
            flows[key] = (float(record['value']), available.isoformat(), record['id'],
                          str(record['valid_from'])[:10], str(record['valid_to'])[:10])
    cbsa_ref = catalog_ref(store, 'cbsa_delineations')
    members = {}
    for record in _lines(_records_path(store, cbsa_ref), ('"predicate":"within"', COUNTY)):
        county, target = record.get('subject'), record.get('object')
        if not is_county(county) or not str(target or '').startswith('geo:US:cbsa:'):
            continue
        year = str(record.get('valid_from') or '')[:4]
        if year not in CBSA_BULLETINS:
            continue
        members[(county, target, year)] = (CBSA_BULLETINS[year], record['id'])
    return {'migration_ref': migration_ref, 'flows': flows, 'cbsa_ref': cbsa_ref, 'cbsa': members}


COLLECTORS = (_collect_qcew, _collect_qcew_sectors, _collect_laus, _collect_bea, _collect_climdiv, _collect_storms,
              _collect_fema, _collect_geography, _collect_edges)


def _run(task):
    function, data_root = task
    return function.__name__, function(data_root)


def collect(data_root, *, processes=6):
    """Run every collector (in parallel processes) and return their results by name."""
    tasks = [(function, str(data_root)) for function in COLLECTORS]
    if processes <= 1:
        return dict(_run(task) for task in tasks)
    context = multiprocessing.get_context('spawn')
    with context.Pool(min(processes, len(tasks))) as pool:
        return dict(pool.map(_run, tasks, chunksize=1))


def records(results, observed_at):
    """Panel observations and edge assertions with evidence pointing at the source records."""
    for name, result in results.items():
        if name == '_collect_edges':
            continue
        source, ref = result['source'], result['ref']
        spec = SOURCES[source]
        for (county, feature, year), value in sorted(result['values'].items()):
            available = FIRST_YEAR_DATE if spec['lag_months'] is None else year_available(source, year)
            valid_from, valid_to = f'{year}-01-01', f'{year + 1}-01-01'
            yield {'id': f'{DATASET}:{county}:{feature}:{year}', 'kind': 'observation', 'subject': county,
                   'metric': feature, 'unit': result['units'].get(feature) or 'unknown', 'value': value,
                   'valid_from': valid_from, 'valid_to': valid_to, 'observed_at': observed_at,
                   'dimensions': {'available_at': available, 'revisions': spec['revisions'], 'source': source},
                   'evidence': [{'input': dict(ref), 'record_id': rid} for rid in result['ids'][(county, feature, year)]]}
    edges = results['_collect_edges']
    for (origin, destination, year), (value, available, rid, start, end) in sorted(edges['flows'].items()):
        yield {'id': f'{DATASET}:migration:{origin}:{destination}:{year}', 'kind': 'assertion', 'subject': origin,
               'predicate': 'migration_flow', 'object': destination, 'weight': value, 'valid_from': start,
               'valid_to': end, 'observed_at': observed_at,
               'attributes': {'available_at': available, 'unit': 'people', 'revisions': 'none'},
               'evidence': [{'input': dict(edges['migration_ref']), 'record_id': rid}]}
    for (county, cbsa, year), (available, rid) in sorted(edges['cbsa'].items()):
        yield {'id': f'{DATASET}:within_cbsa:{county}:{cbsa}:{year}', 'kind': 'assertion', 'subject': county,
               'predicate': 'within_cbsa', 'object': cbsa, 'valid_from': f'{year}-01-01', 'observed_at': observed_at,
               'attributes': {'available_at': available, 'delineation': year, 'revisions': 'none'},
               'evidence': [{'input': dict(edges['cbsa_ref']), 'record_id': rid}]}


FIRST_YEAR_DATE = f'{FIRST_YEAR}-01-01'


def summary(results):
    features, counties, inputs = {}, set(), []
    for name, result in results.items():
        if name == '_collect_edges':
            continue
        if result['ref'] not in inputs:
            inputs.append(result['ref'])
        for (county, feature, year) in result['values']:
            counties.add(county)
            entry = features.setdefault(feature, {'source': result['source'], 'unit': result['units'].get(feature),
                                                  'rows': 0, 'first_year': year, 'last_year': year})
            entry['rows'] += 1
            entry['first_year'] = min(entry['first_year'], year)
            entry['last_year'] = max(entry['last_year'], year)
    edges = results['_collect_edges']
    inputs += [ref for ref in (edges['migration_ref'], edges['cbsa_ref']) if ref not in inputs]
    duplicates = {name: result['duplicates'] for name, result in results.items() if name != '_collect_edges'}
    excluded = {name: result.get('excluded', {}) for name, result in results.items()
                if name != '_collect_edges' and result.get('excluded')}
    return {'schema': 'worldmodel.county_panel/1', 'counties': len(counties), 'features': dict(sorted(features.items())),
            'edges': {'migration_flow': len(edges['flows']), 'within_cbsa': len(edges['cbsa'])},
            'targets': list(TARGETS), 'sources': SOURCES, 'first_year': FIRST_YEAR, 'duplicates_dropped': duplicates,
            'excluded': excluded,
            'inputs': inputs,
            'does_not_establish': [
                'Values are the current vintage at retrieval; sources marked revisions=major or minor were revised after '
                'their available_at date, so an as-of view of this panel still carries revision leakage.',
                'available_at is a declared publication rule per source, not a measured release timestamp.',
                'Static geography is attached to every year; county boundary changes are not versioned.',
                'State membership is read from the FIPS code structure; no other identity is inferred.']}


def build(data_root, *, processes=6, publish=True, cache=None):
    """Collect, then publish one ``county_panel`` version. Returns ``(ref or None, summary)``.

    ``cache`` names a pickle of the collected results: it is read if it exists and written after a
    fresh collection, so a publication that fails (for example because the package changed while
    collecting, which the provenance check refuses) does not repeat the scan of the inputs. The
    inputs are re-verified at publication either way.
    """
    import pickle
    from ..artifacts import publish_report
    from ..store import Store
    if cache is not None and os.path.exists(cache):
        with open(cache, 'rb') as handle:
            results = pickle.load(handle)
    else:
        results = collect(data_root, processes=processes)
        if cache is not None:
            with open(cache, 'wb') as handle:
                pickle.dump(results, handle, protocol=pickle.HIGHEST_PROTOCOL)
    report = summary(results)
    if not publish:
        return None, report
    store = Store(data_root)
    observed_at = now()
    ref = publish_report(store, DATASET, report, {'first_year': FIRST_YEAR, 'sources': SOURCES, 'targets': list(TARGETS)},
                         inputs=report['inputs'], records=records(results, observed_at), entrypoint=ENTRYPOINT)
    return ref, report


def load(store, ref=None, *, stream=None):
    """Read a published panel back as ``(values, available, units, edges)``.

    ``values[(county, feature, year)] = value``; ``available[(county, feature, year)]`` its public date;
    ``edges`` lists ``(subject, predicate, object, weight, valid_from, available_at)``.
    """
    ref = ref or store.latest(DATASET)
    path = store.version_dir(ref) / 'records.jsonl'
    values, available, units, edges = {}, {}, {}, []
    with (stream or open)(path, 'rt', encoding='utf-8') as handle:
        for line in handle:
            record = json.loads(line)
            if record['kind'] == 'observation':
                key = (record['subject'], record['metric'], int(record['valid_from'][:4]))
                values[key] = record['value']
                available[key] = record['dimensions']['available_at']
                units[record['metric']] = record['unit']
            else:
                edges.append((record['subject'], record['predicate'], record['object'], record.get('weight', 1.0),
                              record.get('valid_from'), record['attributes']['available_at']))
    return ref, values, available, units, edges


if __name__ == '__main__':  # pragma: no cover
    ref, report = build(os.environ.get('WORLD_MODEL_DATA', 'data'))
    print(json.dumps({'ref': ref, 'counties': report['counties'], 'features': len(report['features'])}))
