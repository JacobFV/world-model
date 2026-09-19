"""Coverage that is known rather than assumed: what population each dataset claims, and how much of it is held.

A record count is not coverage. 52 million Companies House rows are complete for the UK register
and say nothing about any other country; GLEIF is complete for LEI holders, which most companies
in the world are not. This module states, for each dataset, **the population it claims to cover**
and then measures two fractions wherever a denominator can be sourced:

* ``measured_over_declared``: the entities the published output actually holds, over the count the
  dataset's own declaration states (``dataset.json`` ``coverage.scope``). This is an *acquisition*
  check: a value below 1 means the published stage holds less than was declared.
* ``covered_of_population``: how many members of an independently sourced population list the
  dataset holds, over the size of that list. The lists used are the Census county list and the ISO
  3166-1 table shipped in ``worldmodel/reference`` (both dated), and other catalog datasets whose
  own coverage is a complete published list (TIGER tracts in ``census_geography``, the Nasdaq Trader
  directory in ``nasdaq_listings``).

Where no denominator can be sourced the estimate says so and names the population that would be
needed; it never substitutes a guess. :data:`POPULATIONS` is the curated statement of each
dataset's population, and every declared count in it is checked by the tests against the text of
the declaration it cites.

Measuring streams published outputs (entity records only, prefiltered on raw bytes), so it costs
one pass over each measured dataset. ``measure=False`` returns the statements without reading data.
"""
import csv
from datetime import date
import json
from pathlib import Path
import re
import time

REFERENCE = Path(__file__).resolve().parent / 'reference'

# Population denominators shipped with the package, each a dated published list.
REFERENCES = {
    'us_counties_2020': {
        'description': 'counties and county equivalents of the 50 states, DC and Puerto Rico active on 2020-01-01',
        'source': 'worldmodel/reference/us_counties.csv (Census national_county2020.txt, dated changes)',
    },
    'iso3166_1': {
        'description': 'ISO 3166-1 alpha-3 codes with status "current"',
        'source': 'worldmodel/reference/countries_iso3166.csv (ISO 3166-1, with formerly-used codes dated)',
    },
}


def reference_members(name):
    """The member keys of a shipped reference population."""
    if name == 'us_counties_2020':
        with (REFERENCE / 'us_counties.csv').open(encoding='utf-8') as stream:
            rows = list(csv.DictReader(stream))
        at = '2020-01-01'
        return {row['geoid'] for row in rows
                if (not row['valid_from'] or row['valid_from'] <= at) and (not row['valid_to'] or at < row['valid_to'])
                and row['geoid'][:2] not in ('60', '66', '69', '78')}
    if name == 'iso3166_1':
        with (REFERENCE / 'countries_iso3166.csv').open(encoding='utf-8') as stream:
            return {row['iso3'] for row in csv.DictReader(stream) if row['status'] == 'current' and row['iso3']}
    raise ValueError('Unknown reference population: ' + str(name))


COUNTY = {'prefix': 'geo:US:county:', 'key': 'suffix'}
TRACT = {'prefix': 'geo:US:tract:', 'key': 'suffix'}
COUNTRY = {'prefix': 'iso3:', 'key': 'suffix'}
COUNTIES = {'reference': 'us_counties_2020'}
COUNTRIES = {'reference': 'iso3166_1'}
TRACTS = {'dataset': 'census_geography', 'count': TRACT,
          'basis': 'census tracts in the TIGER/Line 2024 cartographic boundary and Gazetteer files'}


def _county_dataset(population):
    return {'kind': 'subset', 'population': population, 'unit': 'county', 'count': COUNTY,
            'denominator': COUNTIES,
            'not_covered': 'US territories other than Puerto Rico; geography vintages other than 2020 are '
                           'reported as outside the population (Connecticut planning regions from 2022)'}


def _country_dataset(population):
    return {'kind': 'subset', 'population': population, 'unit': 'country or territory', 'count': COUNTRY,
            'denominator': COUNTRIES,
            'not_covered': 'aggregates and historical states the source publishes under non-ISO codes, which '
                           'are reported as outside the population rather than counted'}


# The curated statement of what each dataset claims to cover. ``declared`` numbers are quoted from
# the dataset's own declaration; tests check each one appears in the text it cites.
POPULATIONS = {
    # -- registers: the dataset is the published list of its population ------------------------
    'sec_gleif': {'kind': 'register', 'population': 'legal entities that hold a Legal Entity Identifier',
                  'unit': 'LEI', 'count': {'prefix': 'lei:'},
                  'declared': {'value': 3431064, 'field': 'coverage.scope'},
                  'not_covered': 'legal entities without an LEI, which is most companies in the world; GLEIF '
                                 'publishes no count of them, so no share of all companies can be stated'},
    'companies_house_uk': {'kind': 'register', 'population': 'companies on the UK register (Basic Company Data)',
                           'unit': 'company', 'count': {'prefix': 'gb:companies_house:'},
                           'not_covered': 'every non-UK company; UK partnerships and other entities not in Basic '
                                          'Company Data; companies dissolved before the snapshot'},
    'iso_mic_venues': {'kind': 'register', 'population': 'market identifier codes in ISO 10383', 'unit': 'MIC',
                       'count': {'prefix': 'mic:'}, 'declared': {'value': 2883, 'field': 'coverage.scope'},
                       'not_covered': 'trading activity; a MIC is a registration, not evidence a venue operates'},
    'nasdaq_listings': {'kind': 'register', 'population': 'US exchange listings in the Nasdaq Trader symbol '
                                                           'directories (nasdaqlisted + otherlisted)',
                        'unit': 'listing', 'count': {'prefix': 'ticker:'},
                        'declared': {'value': 13199, 'field': 'coverage.scope'},
                        'not_covered': 'OTC securities, delisted symbols and listings before the snapshot date'},
    'fdic_bank_financials': {'kind': 'register', 'population': 'FDIC-insured institutions, active and inactive, '
                                                               'with a certificate number',
                             'unit': 'insured institution (certificate)', 'count': {'prefix': 'fdic:cert:'},
                             'declared': {'value': 27833, 'field': 'coverage.scope'},
                             'not_covered': 'credit unions (NCUA), uninsured banks, bank holding companies as such'},
    'sec_issuer_reference': {'kind': 'register', 'population': 'SEC registrants with a ticker in '
                                                               'company_tickers_exchange.json',
                             'unit': 'CIK', 'count': {'prefix': 'sec:cik:'},
                             'declared': {'value': 8022, 'field': 'coverage.scope'},
                             'not_covered': 'SEC filers without an exchange ticker (most funds, private issuers, '
                                            'individuals)'},
    'sec_company_assets': {'kind': 'register', 'population': 'CIKs in the SEC companyfacts bulk file',
                           'unit': 'CIK', 'count': {'prefix': 'sec:cik:'},
                           'declared': {'value': 20362, 'field': 'coverage.scope'},
                           'not_covered': 'filers that never filed XBRL financial data; concepts outside the '
                                          'selected tags'},
    'openalex_people': {'kind': 'register', 'population': 'OpenAlex US company/facility/government institutions '
                                                          'and authors (works_count>99) at 43 strategic institutions',
                        'unit': 'institution or author', 'count': {'prefix': 'openalex:'},
                        'declared': {'value': 13075 + 12998, 'field': 'coverage.scope',
                                     'quoted': ['13,075', '12,998']},
                        'not_covered': 'every other institution and researcher in OpenAlex'},
    'ofac_sanctions': {'kind': 'register', 'population': 'parties on the OFAC SDN list (SDN_ADVANCED)',
                       'unit': 'listed party', 'count': {'prefix': 'ofac:party:'},
                       'not_covered': 'non-SDN lists (see other_sanctions_lists) and every other jurisdiction'},
    'fec_candidates': {'kind': 'register', 'population': 'candidates registered with the FEC',
                       'unit': 'FEC candidate ID', 'count': {'prefix': 'fec:candidate:'},
                       'not_covered': 'state and local candidates; one person holds one ID per office sought'},
    'congress_people': {'kind': 'register', 'population': 'people who have served in the US Congress, per the '
                                                           'congress-legislators project',
                        'unit': 'bioguide ID', 'count': {'prefix': 'bioguide:'},
                        'not_covered': 'state legislators and every other country\'s legislature'},
    'voteview_rollcalls': {'kind': 'register', 'population': 'members of Congress in Voteview',
                           'unit': 'ICPSR ID', 'count': {'prefix': 'icpsr:'},
                           'not_covered': 'Voteview issues a new ICPSR ID on a party switch, so IDs over-count people'},
    'eia_energy': {'kind': 'register', 'population': 'US electric generating plants reporting to EIA',
                   'unit': 'EIA plant code', 'count': {'prefix': 'eia:plant:'},
                   'not_covered': 'plants below the EIA-860 reporting threshold (1 MW); non-US plants'},
    'airport_nodes': {'kind': 'register', 'population': 'airports, heliports and seaplane bases in the OurAirports '
                                                        'community database',
                      'unit': 'airport', 'count': {'prefix': 'ourairports:', 'exclude': ('ourairports:runway:',
                                                                                     'ourairports:navaid:',
                                                                                     'ourairports:country:')},
                      'not_covered': 'no authoritative worldwide airport count exists to compare against'},
    'usgs_resources': {'kind': 'register', 'population': 'mineral occurrences in the USGS Mineral Resources Data '
                                                         'System (legacy, not maintained)',
                       'unit': 'MRDS record', 'count': {'prefix': 'mrds:', 'exclude': ('mrds:country:',)},
                       'not_covered': 'current operations, reserves and grades'},
    'sec_13f_history': {'kind': 'register', 'population': 'institutional investment managers that filed Form 13F '
                                                          '(US, at least $100M in 13(f) securities), 2013-2025',
                        'unit': 'filer CIK', 'count': {'prefix': 'sec:cik:'},
                        'not_covered': 'managers below the threshold, non-13(f) securities, short positions, and '
                                       'managers obliged to file that did not; the SEC publishes no count of the '
                                       'obliged population'},
    # -- samples: partial by design --------------------------------------------------------------
    'marine_ais': {'kind': 'sample', 'population': 'vessels transmitting AIS in US coastal waters (MarineCadastre), '
                                                   'in the acquired monthly files only',
                   'unit': 'MMSI', 'count': {'prefix': 'mmsi:'},
                   'not_covered': 'vessels outside the acquired months and outside US coastal receivers; AIS is '
                                  'self-reported and can be switched off'},
    'lda_lobbying': {'kind': 'sample', 'population': 'Lobbying Disclosure Act filings (acquisition declared partial)',
                     'unit': 'filing', 'count': {'prefix': 'lda:filing:'},
                     'not_covered': 'filings not yet acquired; the declaration is partial_acquisition_in_progress'},
    'alpaca_daily_bars': {'kind': 'subset', 'population': 'listings in the Nasdaq Trader symbol directories',
                          'unit': 'symbol', 'count': {'prefix': 'ticker:US:', 'key': 'symbol'},
                          'declared': {'value': 11611, 'field': 'coverage.scope'},
                          'denominator': {'dataset': 'nasdaq_listings', 'count': {'prefix': 'ticker:', 'key': 'symbol'},
                                          'basis': 'symbols in the 2026-09-15 Nasdaq Trader directories; compared '
                                                   'as symbol strings for coverage only, never as identity'},
                          'not_covered': 'symbols reused over the period refer to different securities'},
    # -- subsets of a sourced population ---------------------------------------------------------
    'census_geography': _county_dataset('US counties (TIGER/Line and Gazetteer 2024)'),
    'fema_nri': _county_dataset('US counties in the National Risk Index'),
    'noaa_climdiv': _county_dataset('US counties with a nClimDiv county series'),
    'usda_agriculture': _county_dataset('US counties with a NASS county estimate'),
    'bls_labor': _county_dataset('US counties with a BLS county series'),
    'acs_5yr_tables': _county_dataset('US counties in the ACS 5-year tables'),
    'census_business': _county_dataset('US counties in County Business Patterns'),
    'census_population': _county_dataset('US counties in the Population Estimates'),
    'irs_soi_migration': _county_dataset('US counties in IRS SOI migration flows'),
    'openfema': _county_dataset('US counties named in OpenFEMA declarations'),
    'noaa_storm_events': _county_dataset('US counties named in NOAA Storm Events'),
    'bea_national_regional': _county_dataset('US counties in BEA regional accounts'),
    'lehd_lodes': _county_dataset('US counties in LODES'),
    'worldbank_wdi': _country_dataset('countries and territories in the World Development Indicators'),
    'imf_sdmx': _country_dataset('IMF member economies in the acquired SDMX series'),
    'bis_bulk': _country_dataset('economies in the BIS bulk series'),
    'faostat': _country_dataset('countries in FAOSTAT'),
    'un_wpp': _country_dataset('countries in UN World Population Prospects'),
    'cepii_gravity': _country_dataset('countries in the CEPII gravity dataset'),
    'vdem': _country_dataset('countries in V-Dem'),
    'ember_owid_energy': _country_dataset('countries in Ember / OWID energy'),
    'oecd_sdmx': _country_dataset('countries in the acquired OECD series'),
    'usda_fas_psd': _country_dataset('countries in USDA FAS PSD'),
    'cepii_baci': _country_dataset('countries reporting trade in BACI (HS2017)'),
    'un_comtrade': _country_dataset('reporters in the acquired UN Comtrade extract'),
}
# Tract coverage of the tract-keyed Census products, against TIGER tracts in census_geography.
for _name in ('acs_5yr_tables', 'fema_nri', 'lehd_lodes'):
    POPULATIONS[_name + ':tracts'] = {
        'dataset': _name, 'kind': 'subset', 'population': 'US census tracts (2020 tract vintage)', 'unit': 'tract',
        'count': TRACT, 'denominator': TRACTS,
        'not_covered': 'tracts suppressed or merged in the source; a tract missing here is absent from the output, '
                       'not zero'}


def _latest_records(store, dataset):
    ref = store.latest(dataset, stage='normalized')
    directory = store.version_dir(ref)
    for name in ('records.jsonl.gz', 'records.jsonl'):
        if (directory / name).is_file():
            return ref, directory / name
    raise ValueError('%s has no published normalized records' % dataset)


_ENTITY_ID = re.compile(rb'"entity_id":\s*"([^"]+)"')


def entity_keys(store, dataset, rule):
    """Distinct keys of the entity records in a dataset's published output that match ``rule``."""
    from .unify import _lines
    ref, path = _latest_records(store, dataset)
    prefix = rule['prefix'].encode()
    excluded = tuple(p.encode() for p in rule.get('exclude', ()))
    keys, read = set(), 0
    for line in _lines(path):
        read += 1
        if b'"kind":"entity"' not in line and b'"kind": "entity"' not in line:
            continue
        match = _ENTITY_ID.search(line)
        if match is None:
            continue
        entity_id = match.group(1)
        if not entity_id.startswith(prefix) or entity_id.startswith(excluded):
            continue
        text = entity_id.decode()
        key = rule.get('key')
        if key == 'suffix':
            text = text[len(rule['prefix']):]
        elif key == 'symbol':
            text = text.rsplit(':', 1)[-1]
        keys.add(text)
    return keys, {'dataset': ref['dataset'], 'stage': ref.get('stage'), 'version': ref['version'],
                  'records_read': read}


def declared_count_supported(declaration, spec):
    """Whether the declared count a POPULATIONS entry quotes appears in the declaration it cites."""
    declared = spec.get('declared')
    if not declared:
        return True
    section, _, field = declared['field'].partition('.')
    text = str((declaration.get(section) or {}).get(field, '')) if field else str(declaration.get(section, ''))
    quoted = declared.get('quoted') or ['{:,}'.format(declared['value'])]
    return all(q in text for q in quoted)


def _denominator(store, spec, cache):
    denominator = spec.get('denominator')
    if not denominator:
        return None, None
    if 'reference' in denominator:
        name = denominator['reference']
        if name not in cache:
            cache[name] = reference_members(name)
        return cache[name], {'basis': REFERENCES[name]['description'], 'source': REFERENCES[name]['source']}
    key = (denominator['dataset'], json.dumps(denominator['count'], sort_keys=True))
    if key not in cache:
        cache[key] = entity_keys(store, denominator['dataset'], denominator['count'])
    members, source = cache[key]
    return members, {'basis': denominator['basis'], 'source': source}


def estimate_one(catalog, store, name, spec, *, measure=True, cache=None):
    cache = {} if cache is None else cache
    dataset = spec.get('dataset', name)
    declaration = catalog.get(dataset)
    declared_scope = declaration.get('coverage') or {}
    row = {'estimate': name, 'dataset': dataset, 'kind': spec['kind'], 'population': spec['population'],
           'unit': spec['unit'], 'status': declaration.get('status'),
           'declared_scope': {key: declared_scope.get(key) for key in ('scope', 'completeness', 'representative')}
           if declared_scope else None,
           'declared_count': dict(spec['declared'], supported_by_declaration=declared_count_supported(declaration, spec))
           if spec.get('declared') else None,
           'not_covered': spec['not_covered']}
    if not measure:
        return row
    try:
        keys, source = entity_keys(store, dataset, spec['count'])
    except (ValueError, OSError) as error:
        return {**row, 'measured': None, 'unmeasured_reason': str(error)}
    row['measured'] = {'value': len(keys), 'source': source,
                       'basis': 'distinct entity IDs with prefix %s in the published normalized output'
                                % spec['count']['prefix']}
    if spec.get('declared'):
        row['measured_over_declared'] = round(len(keys) / spec['declared']['value'], 6)
    members, basis = _denominator(store, spec, cache)
    if members is not None:
        present = len(keys & members)
        row['population_denominator'] = {'value': len(members), **basis}
        row['covered_of_population'] = {'present': present, 'fraction': round(present / len(members), 6)
                                        if members else None, 'outside_population': len(keys - members),
                                        'outside_examples': sorted(keys - members)[:10]}
    elif spec['kind'] != 'subset':
        row['population_denominator'] = None
        row['reason_no_denominator'] = ('the dataset is the published list of its own population, so its share of '
                                        'that population is 1 by construction; its share of any wider population '
                                        'is not sourced here') if spec['kind'] == 'register' else \
            'partial by design; no count of the full population is sourced'
    return row


def estimate_coverage(catalog, store, *, datasets=None, measure=True, progress=None):
    """Coverage estimates for every curated dataset (or ``datasets``), plus the uncurated declarations."""
    started = time.time()
    wanted = set(datasets) if datasets else None
    cache, estimates = {}, []
    for name, spec in POPULATIONS.items():
        dataset = spec.get('dataset', name)
        if wanted is not None and dataset not in wanted and name not in wanted:
            continue
        estimates.append(estimate_one(catalog, store, name, spec, measure=measure, cache=cache))
        if progress:
            progress('%s (%.0f s)' % (name, time.time() - started))
    curated = {spec.get('dataset', name) for name, spec in POPULATIONS.items()}
    uncurated = []
    for path in sorted(Path(catalog.root).glob('*/dataset.json')):
        dataset = path.parent.name
        if dataset in curated or (wanted is not None and dataset not in wanted):
            continue
        declaration = json.loads(path.read_text(encoding='utf-8'))
        uncurated.append({'dataset': dataset, 'status': declaration.get('status'),
                          'declared_scope': (declaration.get('coverage') or {}).get('scope'),
                          'reason': 'no population statement curated in worldmodel.coverage_estimator.POPULATIONS'})
    short = [e['estimate'] for e in estimates if e.get('measured_over_declared') is not None
             and e['measured_over_declared'] < 0.99]
    return {'as_of': date.today().isoformat(), 'estimates': estimates, 'uncurated': uncurated,
            'summary': {'curated': len(estimates), 'uncurated': len(uncurated),
                        'by_kind': {kind: sum(e['kind'] == kind for e in estimates)
                                    for kind in ('register', 'subset', 'sample')},
                        'with_population_denominator': sum(bool(e.get('population_denominator')) for e in estimates),
                        'measured_below_declared': short},
            'what_this_does_not_establish': [
                'Representativeness: a dataset holding 99% of US counties can still be biased within them.',
                'Coverage of anything wider than the stated population (GLEIF is complete for LEI holders and says '
                'nothing about companies without one).',
                'That a declared count is right: measured_over_declared checks the published output against the '
                'declaration, not the declaration against the world.'],
            'seconds': round(time.time() - started, 1)}
