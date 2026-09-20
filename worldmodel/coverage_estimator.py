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

Not every declaration can be given a population, and two other outcomes are reported rather than
guessed at. :data:`DERIVED` holds the datasets whose content is produced inside this repository --
derived panels, reports and graphs, and the offline fixtures. Their coverage is a property of their
inputs, not of any population in the world, so they are reported under ``derived`` with the inputs
named and no population invented. :data:`UNCURATED_REASONS` holds the declarations that pin no
population down or publish no entity records at all; they stay under ``uncurated``, each with the
specific missing thing spelled out.

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


_COUNTY_NOT_COVERED = ('US territories other than Puerto Rico; geography vintages other than 2020 are '
                       'reported as outside the population (Connecticut planning regions from 2022)')
_COUNTRY_NOT_COVERED = ('aggregates and historical states the source publishes under non-ISO codes, which '
                        'are reported as outside the population rather than counted')


def _county_dataset(population, not_covered=None):
    return {'kind': 'subset', 'population': population, 'unit': 'county', 'count': COUNTY,
            'denominator': COUNTIES,
            'not_covered': not_covered or _COUNTY_NOT_COVERED}


def _country_dataset(population, not_covered=None):
    return {'kind': 'subset', 'population': population, 'unit': 'country or territory', 'count': COUNTRY,
            'denominator': COUNTRIES,
            'not_covered': not_covered or _COUNTRY_NOT_COVERED}


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
                        'not_covered': ('every other institution and researcher in OpenAlex. The published output '
                                        'holds more institution records than the declared scope counts, because '
                                        'each institution\'s published lineage ancestors are emitted as entities '
                                        'too; that is why measured_over_declared is above 1')},
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
    'cepii_baci_hs92': _country_dataset(
        'countries reporting trade in BACI HS1992 (V202601), 1995-2024',
        'the HS6 products and the years are separate axes and are not measured here; BACI reconciles the two '
        'sides of each flow, so a value here is CEPII\'s estimate, not either country\'s customs figure'),
    'census_intl_trade': _country_dataset(
        'partner economies with their own published HS6 series in this dataset, acquired for 2024-01..2026-07 '
        '(the declaration calls them the 18 major partners)',
        'every other US trading partner, for which only the all-country total is held; months before 2022-01; '
        'the HS6 products are a separate axis; the all-country total is published under a non-ISO code and so '
        'is reported as outside the population'),
    'ecb_eurostat': _country_dataset(
        'countries with at least one series in the acquired ECB reference rates and Eurostat datasets',
        'economies outside the acquired ECB and Eurostat datasets; the NUTS2/NUTS3 regions in this dataset are '
        'a separate population; a country Eurostat lists but which reports no value in an acquired dataset is '
        'absent here, which is not a zero'),
    'gdelt_events': _country_dataset(
        'countries named by a CAMEO actor code in the GDELT daily exports for 2026-03-25..2026-09-14',
        'GDELT codes what news reports said happened, not what happened; a country absent here had no coded '
        'event in the 174-day window, which is not evidence that nothing occurred there; CAMEO carries some '
        'superseded ISO codes (ROM, TMP), reported as outside the population rather than counted'),
    'wits_trains_tariffs': _country_dataset(
        'reporting economies whose MFN applied tariffs this dataset acquired from WITS/TRAINS for 2018-2023, '
        'the EU counted as one customs union',
        'every economy outside the acquired set; preferential and bound rates, since only MFN applied rates are '
        'acquired; the EU is one reporter, so its member states are not separately covered; national tariff '
        'lines below HS6, which WITS publishes only as simple averages'),
    'epa_aqs_daily': _county_dataset(
        'US counties with at least one EPA AQS monitor reporting a daily summary 2010-2024',
        'counties with no monitor for a pollutant are absent, not clean; values are means over the monitors in '
        'a county, so no county number is a reading any monitor took; pollutants outside the six acquired'),
    'conflict_reference': _country_dataset(
        'states in the COW state system membership list (2024) that this dataset maps to an ISO 3166-1 code',
        'states COW records that have no current ISO 3166-1 code, reported as outside the population; the '
        'alliances, disputes, wars, capabilities and contiguity records are separate populations in this '
        'dataset and are not counted here'),
    'fred_county_vintages:counties': {
        'dataset': 'fred_county_vintages', 'kind': 'subset',
        'population': 'US counties for which FRED publishes at least one of the ten configured county series',
        'unit': 'county', 'count': COUNTY, 'denominator': COUNTIES,
        'not_covered': _COUNTY_NOT_COVERED + '; a county here may carry only one of the ten series, so presence '
                       'is not coverage of any particular measure, and a series FRED does not archive in '
                       'ALFRED is recorded as skipped rather than as missing data'},

    # -- registers acquired since the first curation pass -----------------------------------------
    'classifications': {'kind': 'register', 'population': 'industry codes in the Census NAICS 2022 structure, '
                                                          '2 through 6 digits',
                        'unit': 'NAICS 2022 code', 'count': {'prefix': 'naics2022:'},
                        'not_covered': 'establishments and firms, which a NAICS code classifies but does not '
                                       'list; the NAICS 2017 and 2012 structures, SOC 2018, Schedule B and HTS '
                                       'codes in this dataset are separate code systems with their own prefixes'},
    'classifications:soc2018': {'dataset': 'classifications', 'kind': 'register',
                                'population': 'occupation codes in the SOC 2018 structure',
                                'unit': 'SOC 2018 code', 'count': {'prefix': 'soc2018:'},
                                'not_covered': 'workers and jobs, which a SOC code classifies but does not '
                                               'list; SOC 2010 and earlier revisions'},
    'trade_concordances': {'kind': 'register',
                           'population': '10-digit HTS import codes in the Census 2026 import concordance '
                                         '(impconcord26)',
                           'unit': 'HTS10 code', 'count': {'prefix': 'hts2026:'},
                           'not_covered': 'the Schedule B export codes and the HS, SITC and ISIC code systems '
                                          'in this dataset, each a separate population; trade itself, which a '
                                          'concordance maps codes for but never measures'},
    'usitc_hts_tariffs': {'kind': 'register',
                          'population': 'tariff lines in the current published revision of the U.S. Harmonized '
                                        'Tariff Schedule, including chapter 99 additional duties',
                          'unit': 'HTS code', 'count': {'prefix': 'hts:'},
                          'not_covered': 'earlier revisions and the USITC annual tariff databases, which need a '
                                         'manual import; duty actually collected, which depends on origin, '
                                         'claimed programs and CBP rulings, none of which are here'},
    'bea_input_output': {'kind': 'register',
                         'population': 'industries and commodities of the BEA input-output accounts at the '
                                       'levels this dataset normalizes (sector, summary and detail)',
                         'unit': 'BEA input-output code', 'count': {'prefix': 'bea_io:'},
                         'not_covered': 'firms and establishments: an IO industry is an aggregate over them and '
                                        'never a list of them; the GDP-by-industry codes, which carry their own '
                                        'prefix; zero and "..." cells are omitted, so an absent flow is not a '
                                        'flow of zero'},
    'exiobase3': {'kind': 'register',
                  'population': 'the regions of the EXIOBASE 3.9.4 multi-regional input-output table',
                  'unit': 'EXIOBASE region', 'count': {'prefix': 'exiobase:region:'},
                  'not_covered': 'countries inside a rest-of-world aggregate, which cannot be separated; the '
                                 'industries and the region x industry cells are separate populations here; '
                                 'inter-industry flows below the threshold this pipeline applies are dropped'},
    'bls_prices': {'kind': 'register',
                   'population': 'Producer Price Index series in the BLS commodity (WP) and industry (PC) flat '
                                 'files',
                   'unit': 'PPI series code', 'count': {'prefix': 'bls:ppi:'},
                   'not_covered': 'prices anyone paid: a PPI series is an index BLS computes from a sample of '
                                  'quotes; the CPI, average-price and import/export series in this dataset are '
                                  'separate code systems with their own prefixes'},
    'cbsa_delineations': {'kind': 'register',
                          'population': 'core-based statistical areas OMB delineated in the September 2018, '
                                        'March 2020 and July 2023 List 1 bulletins',
                          'unit': 'CBSA code', 'count': {'prefix': 'geo:US:cbsa:'},
                          'not_covered': 'combined statistical areas and metropolitan divisions, separate code '
                                         'systems also published here; the counties that make up a CBSA; '
                                         'delineation vintages before September 2018 or after July 2023'},
    'acs_pums': {'kind': 'register',
                 'population': 'Public Use Microdata Areas of the 2020 PUMA vintage that the ACS 2020-2024 '
                               '5-year PUMS covers',
                 'unit': 'PUMA (2020 vintage)', 'count': {'prefix': 'geo:US:puma20:'},
                 'not_covered': 'people and households: no microdata row is retained, only weighted PUMA '
                                'aggregates, so nothing below a PUMA (at least 100,000 people) can be read '
                                'here; PUMS is a sample of the ACS, which is itself a sample'},
    'acs_pums_2019': {'kind': 'register',
                      'population': 'Public Use Microdata Areas of the 2010 PUMA vintage that the ACS 2015-2019 '
                                    '5-year PUMS covers',
                      'unit': 'PUMA (2010 vintage)', 'count': {'prefix': 'geo:US:puma10:'},
                      'not_covered': 'people and households, for the same reason as acs_pums; 2010 PUMA codes '
                                     'are not 2020 PUMA codes, so this dataset and acs_pums cannot be joined '
                                     'on the code'},
    'census_aspep': {'kind': 'register',
                     'population': 'state and local government units in the Census ASPEP individual-unit files, '
                                   '1993-2024',
                     'unit': 'ASPEP government unit ID', 'count': {'prefix': 'aspep:unit:'},
                     'not_covered': 'the federal government; only the Census of Governments years (1997, 2002, '
                                    '2007, 2012, 2017, 2022) enumerate all local governments, so a unit absent '
                                    'in a sample year still exists; employees, who are counted but not listed'},
    'opm_fedscope': {'kind': 'register',
                     'population': 'federal civilian agencies and sub-agencies with a FedScope agency code in '
                                   'the 1998-2024 cubes',
                     'unit': 'FedScope agency code', 'count': {'prefix': 'opm:agency:'},
                     'not_covered': 'employees: FedScope rows are de-identified and this dataset publishes only '
                                    'aggregates; the agencies FedScope excludes (the intelligence community, '
                                    'the Postal Service, the legislative and judicial branches); occupational '
                                    'series and duty states, separate populations here'},
    'eia_grid_operations': {'kind': 'register',
                            'population': 'US balancing authorities reporting hourly operating data in the '
                                          'EIA-930 bulk EBA file',
                            'unit': 'balancing authority code', 'count': {'prefix': 'eia:ba:'},
                            'not_covered': 'generating plants (eia_energy) and utilities; the most recent seven '
                                           'days, which the bulk file withholds, and hours before 2015-07; '
                                           'regions and interchange pairs, separate populations here'},
    'freight': {'kind': 'register',
                'population': 'Freight Analysis Framework 5 zones (metropolitan and rest-of-state zones '
                              'covering the 50 states and DC)',
                'unit': 'FAF5 zone', 'count': {'prefix': 'faf5:zone:'},
                'not_covered': 'shipments and firms: a FAF flow is a modeled estimate for a zone pair, not a '
                               'record of anything shipped; the 2030-2050 rows are forecasts, flagged as such; '
                               'foreign regions, states and SCTG2 commodities are separate populations here'},
    'transport': {'kind': 'register',
                  'population': 'nodes of the BTS North American Rail Network as NTAD publishes it',
                  'unit': 'NARN rail node', 'count': {'prefix': 'ntad:rail_node:'},
                  'declared': {'value': 250202, 'field': 'acquisition.description', 'quoted': ['250202']},
                  'not_covered': 'rail links, highways, ports, waterways, seaports and airports in this '
                                 'dataset, each a separate population; a node is a network vertex, not a '
                                 'station or a facility, and carries no traffic'},
    'treasury_debt': {'kind': 'register',
                      'population': 'the Treasury security types the Fiscal Service average-interest-rate '
                                    'tables report',
                      'unit': 'Treasury security type', 'count': {'prefix': 'treasury:security_type:'},
                      'not_covered': 'individual securities (CUSIPs), auctions and holders: the tables report '
                                     'aggregates by type only; Debt to the Penny and the Monthly and Daily '
                                     'Treasury Statement tables are observations about the United States, not '
                                     'about any entity counted here'},
    'fec': {'kind': 'register',
            'population': 'political committees in the FEC bulk committee master files for cycles 2000-2026',
            'unit': 'FEC committee ID', 'count': {'prefix': 'fec:committee:'},
            'not_covered': 'candidates (fec_candidates) and individual contributors; state and local committees '
                           'that do not register federally; spending that never passes through a registered '
                           'committee; a committee re-registered under a new ID is counted twice'},
    'congress_gov_api': {'kind': 'register',
                         'population': 'amendments congress.gov lists for the 117th through 119th Congresses',
                         'unit': 'amendment', 'count': {'prefix': 'congress:amendment:'},
                         'not_covered': 'amendments in earlier Congresses; bills and resolutions '
                                        '(govinfo_billstatus); the nominations, committee reports, treaties, '
                                        'roll-call votes and committees in this dataset, each a separate '
                                        'population'},
    'govinfo_billstatus': {'kind': 'register',
                           'population': 'bills and resolutions introduced in the 113th through 119th '
                                         'Congresses, all eight measure types',
                           'unit': 'measure (congress:bill:<congress>:<type>:<number>)',
                           'count': {'prefix': 'congress:bill:'},
                           'not_covered': 'Congresses before the 113th; amendments (congress_gov_api); the text '
                                          'of a measure, which BILLSTATUS does not carry; state legislatures'},
    'federal_register_documents': {'kind': 'register',
                                   'population': 'documents published in the Federal Register from 2021-01-01 '
                                                 'through 2026-09',
                                   'unit': 'Federal Register document number',
                                   'count': {'prefix': 'federalregister:',
                                             'exclude': ('federalregister:agency:',)},
                                   'not_covered': 'documents published before 2021; effective law, which this '
                                                  'is not: a rule published here may since have been stayed, '
                                                  'amended or repealed, and the CFR is not acquired'},
    'crossref_research': {'kind': 'register',
                          'population': 'Crossref works whose funder list names the U.S. Department of Defense '
                                        '(published 2024 or later) or DARPA (published 2022 or later)',
                          'unit': 'DOI', 'count': {'prefix': 'doi:'},
                          'declared': {'value': 7680 + 825, 'field': 'acquisition.description',
                                       'quoted': ['7,680', '825']},
                          'not_covered': 'work these agencies funded whose Crossref record names no funder, '
                                         'which is most of it; classified and unpublished work; every other '
                                         'funder. The declared counts are the result-set sizes Crossref '
                                         'reported at configuration time on 2026-09-15; the published output '
                                         'holds more distinct DOIs than that, so measured_over_declared is '
                                         'above 1, and which of the two is right is not established here'},
    'nasa_publications': {'kind': 'register',
                          'population': 'posts on the NASA.gov main RSS feed, pages 1-20, at acquisition time',
                          'unit': 'post', 'count': {'prefix': 'nasa:publication:'},
                          'declared': {'value': 200, 'field': 'coverage.scope'},
                          'not_covered': 'NASA\'s work and its research output: this is a press channel, and a '
                                         'post is NASA describing itself; anything older than the 200 most '
                                         'recent posts at acquisition time'},
    'ghcn_daily': {'kind': 'register',
                   'population': 'the U.S. GHCN-Daily stations selected from ghcnd-inventory.txt on 2026-09-15 '
                                 '(reference GSN and HCN first-order stations plus long-record stations)',
                   'unit': 'GHCN station ID', 'count': {'prefix': 'ghcn:station:'},
                   'declared': {'value': 929, 'field': 'description'},
                   'not_covered': 'every other GHCN-Daily station, in the U.S. and worldwide; a selected '
                                  'station\'s record is not continuous, and a missing day is missing, not zero. '
                                  'The declaration\'s own breakdown (133 reference plus 821 long-record '
                                  'stations) sums to 954, not to the 929 it declares'},
    'ghcn_monthly': {'kind': 'register',
                     'population': 'U.S. stations with NOAA Global Summary of the Month values in at least 30 '
                                   'distinct years',
                     'unit': 'GHCN station ID', 'count': {'prefix': 'ghcn:station:'},
                     'not_covered': 'stations outside the U.S. and U.S. stations below the 30-year threshold, '
                                    'both dropped by this pipeline; GSOM is derived from GHCN-Daily and '
                                    'inherits its gaps, so a month is summarised from whatever days reported'},
    'ibtracs': {'kind': 'register',
                'population': 'tropical cyclones with a best-track position in IBTrACS v04r01 since 1980',
                'unit': 'IBTrACS storm ID (SID)', 'count': {'prefix': 'ibtracs:storm:'},
                'not_covered': 'storms before 1980; agencies disagree about intensity and IBTrACS keeps every '
                               'agency\'s report, so a storm here is several claims rather than one '
                               'measurement; damage and exposure, which are not in the best-track file'},
    'wri_aqueduct': {'kind': 'register',
                     'population': 'the HydroBASINS level-6 sub-basin x administrative-unit water-risk units of '
                                   'WRI Aqueduct 4.0',
                     'unit': 'Aqueduct unit', 'count': {'prefix': 'aqueduct:unit:'},
                     'not_covered': 'water withdrawn or used at any site: every Aqueduct indicator is modeled, '
                                    'not measured; the sub-basins and GADM administrative units, separate '
                                    'populations here; the 2030/2050/2080 columns are projections'},
    'ucdp_conflicts': {'kind': 'register',
                       'population': 'armed conflicts in the UCDP/PRIO Armed Conflict Dataset and the UCDP '
                                     'conflict list, version 26.1',
                       'unit': 'UCDP conflict ID', 'count': {'prefix': 'ucdp:conflict:'},
                       'not_covered': 'organized violence below UCDP\'s 25-battle-death threshold; the events, '
                                      'dyads and actors in this dataset, separate populations; the GED '
                                      'Candidate 2026 releases are provisional and may be revised'},
    'parlgov': {'kind': 'register',
                'population': 'political parties in the ParlGov development release (EU and OECD democracies)',
                'unit': 'ParlGov party ID', 'count': {'prefix': 'parlgov:party:'},
                'not_covered': 'parties in countries ParlGov does not cover; parties ParlGov records no vote '
                               'share or seat for; elections and cabinets, separate populations here; the '
                               'left-right score is an expert-survey estimate, not a measurement'},
    'opensanctions': {'kind': 'register',
                      'population': 'targets of the OpenSanctions "sanctions" collection: deduplicated persons, '
                                    'companies and vessels designated on 100+ official lists',
                      'unit': 'OpenSanctions entity ID',
                      'count': {'prefix': 'opensanctions:', 'exclude': ('opensanctions:program:',)},
                      'not_covered': 'parties designated by a body OpenSanctions does not crawl; delisted '
                                     'parties are retained, so this is not a list of who is sanctioned today; '
                                     'first_seen and last_seen are crawl times, not designation dates'},
    'opensanctions_graph': {'kind': 'register',
                            'population': 'entities in the OpenSanctions "default" collection graph scoped to '
                                          'sanctioned, PEP/RCA, POI, wanted, crime and export-control-linked '
                                          'parties and their one-hop counterparties',
                            'unit': 'OpenSanctions entity ID', 'count': {'prefix': 'opensanctions:'},
                            'not_covered': 'the rest of the default collection beyond one hop; a counterparty '
                                           'is here because it is linked to a listed party, which is not '
                                           'itself a designation or an accusation'},
    'other_sanctions_lists': {'kind': 'register',
                              'population': 'parties on the U.S. Consolidated Screening List snapshot (ITA)',
                              'unit': 'screening list entry',
                              'count': {'prefix': 'us_csl:',
                                        'exclude': ('us_csl:list:', 'us_csl:program:')},
                              'not_covered': 'the UN Consolidated List and the UK Sanctions List in this '
                                             'dataset, separate populations with their own prefixes; the OFAC '
                                             'SDN list (ofac_sanctions); this is a current snapshot, so past '
                                             'designations and delistings are not here'},
    'wikidata_identifiers': {'kind': 'register',
                             'population': 'Wikidata items carrying at least one of the 31 declared external '
                                           'identifier properties at truthy rank (OpenCorporates restricted to '
                                           'the gb jurisdiction)',
                             'unit': 'Wikidata QID', 'count': {'prefix': 'wikidata:'},
                             'not_covered': 'Wikidata items with none of those properties, and the properties '
                                            'the declaration excludes (ORCID, GeoNames, VIAF, ISNI, GND, SIREN, '
                                            'non-gb OpenCorporates); Wikidata is volunteered, so an identifier '
                                            'here is an editor\'s claim, not a registrar\'s record'},
    'sec_financial_statements': {'kind': 'register',
                                 'population': 'SEC filers with a 10-K, 10-Q, 20-F or 40-F (including '
                                               'transition and amended filings) in the Financial Statement '
                                               'Data Sets 2012Q4-2026Q2',
                                 'unit': 'CIK', 'count': {'prefix': 'sec:cik:'},
                                 'not_covered': 'quarters 2009Q1-2012Q3, excluded to stay under the per-dataset '
                                                'cap; companies that do not file with the SEC at all; tags '
                                                'outside the selected concept set; values are as filed, so a '
                                                'restated figure appears more than once'},
    'sec_ownership_datasets': {'kind': 'register',
                               'population': 'CIKs appearing as a filer, issuer or reporting owner in the SEC '
                                             'Form 13F data sets (filings 2025-09..2026-08) or the Forms 3/4/5 '
                                             'data sets 2023Q1-2026Q2',
                               'unit': 'CIK', 'count': {'prefix': 'sec:cik:'},
                               'not_covered': 'quarters outside those two windows; holdings below the 13F '
                                              'threshold, short positions and non-13(f) securities; insiders '
                                              'with no CIK; the securities themselves, keyed by CUSIP here'},
    'market_corporate_actions': {'kind': 'subset',
                                 'population': 'the symbols of the US stock and ETF ticker listings in the '
                                               'Massive ticker directory, active and delisted',
                                 'unit': 'symbol', 'count': {'prefix': 'ticker:', 'key': 'symbol'},
                                 'denominator': {'dataset': 'nasdaq_listings',
                                                 'count': {'prefix': 'ticker:', 'key': 'symbol'},
                                                 'basis': 'symbols in the 2026-09-15 Nasdaq Trader directories; '
                                                          'compared as symbol strings for coverage only, never '
                                                          'as identity'},
                                 'not_covered': 'securities the Massive directory never carried; the delisted '
                                                'symbols in this dataset are outside the Nasdaq Trader '
                                                'directory by construction and are reported as outside the '
                                                'population, not as missing; a listing is not an issuer'},
    'market_prices': {'kind': 'subset',
                      'population': 'US stock and ETF tickers with a grouped daily bar on at least one weekday '
                                    'in 2024-09-16..2026-09-14',
                      'unit': 'symbol', 'count': {'prefix': 'ticker:US:', 'key': 'symbol'},
                      'denominator': {'dataset': 'nasdaq_listings',
                                      'count': {'prefix': 'ticker:', 'key': 'symbol'},
                                      'basis': 'symbols in the 2026-09-15 Nasdaq Trader directories; compared '
                                               'as symbol strings for coverage only, never as identity'},
                      'not_covered': 'anything before 2024-09-16 (the free plan\'s two-year horizon); OTC and '
                                     'non-US venues; the trades inside a day, since a bar is a daily aggregate; '
                                     'symbols reused over the period refer to different securities'},
    'nasdaq_index_reference': {'kind': 'register',
                               'population': 'listings in the Nasdaq-100 constituent snapshot taken at '
                                             'acquisition time',
                               'unit': 'listing', 'count': {'prefix': 'ticker:'},
                               'declared': {'value': 101, 'field': 'coverage.scope'},
                               'not_covered': 'index weights and the rebalancing history; membership on any '
                                              'other date, since this is one snapshot; the methodology PDF is '
                                              'retained as evidence and is not parsed'},
    'ssga_dia_holdings': {'kind': 'register',
                          'population': 'positions published in one daily holdings snapshot of each of the 14 '
                                        'SPDR ETFs',
                          'unit': 'fund position',
                          'count': {'prefix': 'ssga:',
                                    'exclude': ('ssga:fund:', 'ssga:instrument:')},
                          'declared': {'value': 1476, 'field': 'coverage.scope'},
                          'not_covered': 'every other fund; history, since this is one snapshot per fund; index '
                                         'membership and issuer ownership, neither of which a fund position is'},
    'ssga_dia_nav': {'kind': 'register',
                     'population': 'the one fund whose published NAV history this dataset holds (SPDR DIA)',
                     'unit': 'fund', 'count': {'prefix': 'ssga:fund:'},
                     'not_covered': 'every other fund; the market price of DIA, which NAV is not; days the '
                                    'published workbook omits. The rows are NAV days of one fund, so a count of '
                                    'entities here is 1 by construction and says nothing about the history\'s '
                                    'length'},
    'ssga_dia_premium': {'kind': 'register',
                         'population': 'the one fund whose published premium/discount history this dataset '
                                       'holds (SPDR DIA)',
                         'unit': 'fund', 'count': {'prefix': 'ssga:fund:'},
                         'not_covered': 'every other fund; the price and the NAV separately, since this is '
                                        'their published difference in percentage points; the same entity count '
                                        'of 1 by construction as ssga_dia_nav'},
    'usaspending': {'kind': 'register',
                    'population': 'organizations that received a prime federal contract obligation in FY2025 or '
                                  'FY2026, keyed by the SAM.gov Unique Entity ID reported on the transaction',
                    'unit': 'SAM.gov Unique Entity ID', 'count': {'prefix': 'uei:'},
                    'not_covered': 'recipients whose transaction reported no UEI, published as unresolved name '
                                   'references with no asserted identity and not counted here; subrecipients; '
                                   'fiscal years before 2025; classified and otherwise unreported spending. An '
                                   'obligation is money committed, not money spent'},
    'usaspending_assistance': {'kind': 'register',
                               'population': 'organizations that received a prime federal financial-assistance '
                                             'obligation (grant, loan, direct payment or insurance) in FY2025 '
                                             'or FY2026, keyed by the SAM.gov Unique Entity ID',
                               'unit': 'SAM.gov Unique Entity ID', 'count': {'prefix': 'uei:'},
                               'not_covered': 'individual recipients, whose identity USAspending masks, and any '
                                              'other recipient reported without a UEI; fiscal years before '
                                              '2025; the face value of a loan is not its subsidy cost'},
    'osm_topology': {'kind': 'register',
                     'population': 'OpenStreetMap road, rail, power-line, pipeline and ferry features in the '
                                   'Geofabrik US West extract, snapshot 2026-09-14',
                     'unit': 'OSM feature', 'count': {'prefix': 'osm:feature:'},
                     'not_covered': 'anything outside the US West extract; OpenStreetMap is volunteered, so '
                                    'coverage varies by place and a feature missing here is missing from OSM, '
                                    'not from the world; geometry is simplified to intersection and endpoint '
                                    'nodes by this pipeline'},
    'osm_us_midwest': {'kind': 'register',
                       'population': 'OpenStreetMap road, rail, power-line, pipeline and ferry features in the '
                                     'Geofabrik US Midwest extract, snapshot 2026-09-14',
                       'unit': 'OSM feature', 'count': {'prefix': 'osm:feature:'},
                       'not_covered': 'anything outside the US Midwest extract; OpenStreetMap is volunteered, '
                                      'so a feature missing here is missing from OSM, not from the world; '
                                      'geometry is simplified by this pipeline'},
    'osm_us_northeast': {'kind': 'register',
                         'population': 'OpenStreetMap road, rail, power-line, pipeline and ferry features in '
                                       'the Geofabrik US Northeast extract, snapshot 2026-09-14',
                         'unit': 'OSM feature', 'count': {'prefix': 'osm:feature:'},
                         'not_covered': 'anything outside the US Northeast extract; OpenStreetMap is '
                                        'volunteered, so a feature missing here is missing from OSM, not from '
                                        'the world; geometry is simplified by this pipeline'},
    'osm_us_south': {'kind': 'register',
                     'population': 'OpenStreetMap road, rail, power-line, pipeline and ferry features in the '
                                   'Geofabrik US South extract, snapshot 2026-09-14',
                     'unit': 'OSM feature', 'count': {'prefix': 'osm:feature:'},
                     'not_covered': 'anything outside the US South extract; OpenStreetMap is volunteered, so a '
                                    'feature missing here is missing from OSM, not from the world; geometry is '
                                    'simplified by this pipeline'},
    # -- FRED/ALFRED series registers: the population is the series list, never the thing measured -
    'fred_breakeven10y': {'kind': 'register',
                          'population': 'the single FRED series T10YIE (10-year breakeven inflation rate) and '
                                        'its ALFRED vintages',
                          'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                          'not_covered': 'every other FRED series; vintages ALFRED does not archive; inflation '
                                         'itself, since a breakeven is the spread between two published yields '
                                         'and includes a risk premium'},
    'fred_cpi': {'kind': 'register',
                 'population': 'the three FRED series CPIAUCSL, CPILFESL and PCEPI, with their ALFRED vintages',
                 'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                 'not_covered': 'every other price series; vintages ALFRED does not archive; prices themselves, '
                                'since an index is BLS or BEA aggregating a basket whose weights change'},
    'fred_oil_price': {'kind': 'register',
                       'population': 'the single FRED series DCOILWTICO (WTI spot price at Cushing) and its '
                                     'ALFRED vintages',
                       'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                       'not_covered': 'every other FRED series and every other crude benchmark; the trades '
                                      'behind the quote; vintages ALFRED does not archive'},
    'fred_policy_rate': {'kind': 'register',
                         'population': 'the single FRED series DFF (federal funds effective rate) and its '
                                       'ALFRED vintages',
                         'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                         'not_covered': 'every other FRED series; the target range, which DFF is not; the '
                                        'individual overnight transactions the rate is computed from'},
    'fred_treasury10y': {'kind': 'register',
                         'population': 'the single FRED series DGS10 (10-year constant-maturity Treasury yield) '
                                       'and its ALFRED vintages',
                         'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                         'not_covered': 'every other FRED series; the yields of individual securities, since a '
                                        'constant-maturity yield is an interpolation the Treasury publishes'},
    'fred_treasury2y': {'kind': 'register',
                        'population': 'the single FRED series DGS2 (2-year constant-maturity Treasury yield) '
                                      'and its ALFRED vintages',
                        'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                        'not_covered': 'every other FRED series; the yields of individual securities, since a '
                                       'constant-maturity yield is an interpolation the Treasury publishes'},
    'fred_deposit_rates': {'kind': 'register',
                           'population': 'the three FRED series SAVNRNJ, MMNRNJ and M2OWN, with their ALFRED '
                                         'vintages',
                           'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                           'not_covered': 'every other deposit-rate series; the rates any individual bank '
                                          'offered; SAVNRNJ and MMNRNJ stop in 2021 and M2OWN in 2019, so none '
                                          'of them says anything about today'},
    'fred_macro_panel': {'kind': 'register',
                         'population': 'the FRED/ALFRED series listed in this dataset\'s config.json (the '
                                       'curated macro-financial and state panel), with their vintages',
                         'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                         'not_covered': 'every FRED series outside the curated list, which is most of FRED; the '
                                        'economy the series describe; vintages ALFRED does not archive'},
    'fred_county_vintages': {'kind': 'register',
                             'population': 'the county-level FRED series configured for this dataset (ten '
                                           'measures across US counties), with their ALFRED vintages',
                             'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                             'declared': {'value': 31452, 'field': 'acquisition.description'},
                             'not_covered': 'series FRED does not archive in ALFRED, recorded as skipped rather '
                                            'than missing; counties are counted separately by '
                                            'fred_county_vintages:counties; the underlying BEA, Census and BLS '
                                            'estimates, which FRED republishes rather than produces'},
    'fred_county_laus_monthly_vintages': {
        'kind': 'register',
        'population': 'the monthly LAUS county alias series configured for this dataset (unemployment rate and '
                      'labour force, one of each per county), with their ALFRED vintages',
        'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
        'declared': {'value': 6282, 'field': 'acquisition.description'},
        'not_covered': 'monthly employed and unemployed *levels*, which FRED publishes only as structured ids '
                       'with twelve vintages from 2025-08-27 and this dataset does not acquire; QCEW county '
                       'employment, which FRED does not archive at all; counties are counted separately by '
                       'fred_county_laus_monthly_vintages:counties. A vintage is what FRED held, not what BLS '
                       'published, and the units of the labour force series change inside the archive '
                       '(Thousands of Persons through 2016-03-17, Persons after)'},
    'fred_county_laus_monthly_vintages:counties': {
        'dataset': 'fred_county_laus_monthly_vintages', 'kind': 'subset',
        'population': 'US counties for which FRED publishes a monthly LAUS unemployment rate or labour force '
                      'series', 'unit': 'county', 'count': COUNTY, 'denominator': COUNTIES,
        'not_covered': _COUNTY_NOT_COVERED + '; a county is present from its first archived vintage, which is '
                       '2005-07-06 for the 339 counties of the Federal Reserve Eighth District and 2007-07-05 '
                       'nationally, so presence in this dataset is not real-time availability in any earlier '
                       'month'},
    'fred_state_employment_vintages': {'kind': 'register',
                                       'population': 'the BLS CES State and Area series configured for this '
                                                     'dataset (state-equivalent x CES supersector, monthly, not '
                                                     'seasonally adjusted), with their ALFRED vintages',
                                       'unit': 'FRED series ID', 'count': {'prefix': 'fred:'},
                                       'not_covered': 'seasonally adjusted series and metropolitan-area series; '
                                                      'vintages before 2007-06-19, the first ALFRED archives; '
                                                      'employees, since CES counts jobs on payrolls'},
}
# Tract coverage of the tract-keyed Census products, against TIGER tracts in census_geography.
for _name in ('acs_5yr_tables', 'fema_nri', 'lehd_lodes'):
    POPULATIONS[_name + ':tracts'] = {
        'dataset': _name, 'kind': 'subset', 'population': 'US census tracts (2020 tract vintage)', 'unit': 'tract',
        'count': TRACT, 'denominator': TRACTS,
        'not_covered': 'tracts suppressed or merged in the source; a tract missing here is absent from the output, '
                       'not zero'}


# Datasets whose content is produced inside this repository rather than acquired from a population in
# the world: derived panels, reports and graphs, and the offline fixtures. Coverage is a property of
# their inputs, so no population is stated for them and none is invented; ``derived_from`` names the
# catalog datasets they are built from, and the tests check it against each declaration's
# ``dependencies``.
DERIVED = {
    'county_panel': {
        'derived_from': ['bls_labor', 'bea_national_regional', 'noaa_climdiv', 'noaa_storm_events', 'openfema',
                         'census_geography', 'irs_soi_migration', 'cbsa_delineations'],
        'reason': 'assembled by "wm embed-panel" from published normalized datasets; the counties, features and '
                  'years it holds are whatever those inputs publish'},
    'county_realtime_panel': {
        'derived_from': ['fred_county_vintages', 'county_panel'],
        'reason': 'first releases selected from the ALFRED vintages in fred_county_vintages, with static '
                  'geography carried from county_panel; it covers what those two hold and nothing else'},
    'county_monthly_realtime_panel': {
        'derived_from': ['fred_county_laus_monthly_vintages'],
        'reason': 'monthly first releases selected from the ALFRED vintages in '
                  'fred_county_laus_monthly_vintages; it covers the counties and reference months that '
                  'archive released in real time and nothing else, which before reference month 2007-05 '
                  'is the Federal Reserve Eighth District rather than the nation'},
    'firm_panel': {
        'derived_from': ['sec_financial_statements', 'sec_company_assets', 'sec_gleif', 'sec_issuer_reference',
                         'sec_13f_history', 'sec_ownership_datasets'],
        'reason': 'joined from published SEC and GLEIF identifiers; an issuer is present only where its inputs '
                  'publish it, so its coverage is the intersection of theirs, not a population of firms'},
    'influence_panel': {
        'derived_from': ['congress_people', 'voteview_rollcalls', 'govinfo_billstatus', 'congress_gov_api',
                         'lda_lobbying', 'fec', 'fec_candidates', 'fec_individual_contributions',
                         'fec_individual_contributions_2022', 'fec_individual_contributions_2024'],
        'reason': 'joined on published identifiers across the congressional, lobbying and campaign-finance '
                  'inputs; it covers legislators those inputs cover, and lda_lobbying is declared partial'},
    'trade_panel': {
        'derived_from': ['cepii_baci', 'cepii_baci_hs92', 'wits_trains_tariffs', 'usitc_hts_tariffs',
                         'cepii_gravity', 'trade_concordances'],
        'reason': 'built from the BACI flows, the tariff sources and the concordances; country and product '
                  'coverage is exactly what those inputs publish, and a tariff is attached only where the '
                  'correlation table translates the product exactly'},
    'event_library': {
        'derived_from': ['openfema', 'ofac_sanctions', 'other_sanctions_lists', 'noaa_storm_events',
                         'usgs_earthquakes', 'ibtracs', 'census_geography', 'wits_trains_tariffs'],
        'reason': 'typed, dated shocks selected from the published event and designation datasets; the shocks '
                  'it can hold are bounded by what those inputs record'},
    'embedding_reports': {
        'derived_from': ['county_panel'],
        'reason': 'scores of the pre-registered embedding attempts on a holdout of county_panel; it is a record '
                  'of results, and the only population behind it is county_panel\'s'},
    'calibration_reports': {
        'derived_from': [],
        'reason': 'validation reports written by "wm calibrate-all" over whichever normalized catalog datasets '
                  'are published at run time; the declaration lists no dependencies, so the inputs are the run, '
                  'not a fixed list'},
    'natural_experiment_reports': {
        'derived_from': [],
        'reason': 'pre-registered natural-experiment results written by worldmodel.causal from the event '
                  'library and the outcome datasets each study pins; the declaration lists no dependencies, so '
                  'the inputs are named per study rather than per dataset'},
    'world_evidence': {
        'derived_from': [],
        'reason': 'the per-dataset counts and coverage of one streaming unify build over the catalog; it '
                  'reports the scope it was built with and adds no observation of its own'},
    'world_graph': {
        'derived_from': ['demo_graph', 'rando_joes_happiness_index'],
        'reason': 'an evidence-preserving graph materialization over offline example inputs; both inputs are '
                  'fictional, so nothing here describes the world'},
    'rando_joes_happiness_index': {
        'derived_from': ['demo_countries'],
        'reason': 'an illustrative computed index over the two fictional countries of demo_countries; the '
                  'declaration says it is not a validated index'},
    'market_obligations': {
        'derived_from': ['reviewed_obligations'],
        'reason': 'a compatibility view over reviewed_obligations, which itself holds only fictional test '
                  'documents; no counterparty obligation has been acquired'},
    'reviewed_obligations': {
        'derived_from': ['contract_candidates'],
        'reason': 'reviewed mappings over contract_candidates; the declaration states that no external '
                  'documents were acquired and that the content is fictional tests only'},
    'contract_candidates': {
        'derived_from': [],
        'reason': 'bounded document-extraction candidates; the declaration states that no external documents '
                  'were acquired and that the content is fictional tests only'},
    'strategic_scenarios': {
        'derived_from': [],
        'reason': 'explicitly fictional scenario carriers, kept separate from observed evidence; a scenario is '
                  'written here, not observed anywhere'},
    'demo_countries': {
        'derived_from': [],
        'reason': 'an offline pipeline fixture of two fictional countries; the declaration says it is not real '
                  'measurements'},
    'demo_graph': {
        'derived_from': [],
        'reason': 'an offline pipeline fixture of fictional organizations and conflicting claims'},
}

# Datasets deliberately left uncurated, each with the specific thing that is missing. A precise
# refusal is the correct outcome where no population or no entity rule can be stated truthfully.
UNCURATED_REASONS = {
    'acled': 'the acquisition is awaiting an approved API tier with a budget of 0, so nothing is acquired or '
             'published and no entity records exist to count',
    'bts_airline_t100': 'the declaration names no population at all, since normalization.coverage is "whatever '
                        'months the user downloads", and nothing has been imported',
    'global_fishing_watch': 'the acquisition is awaiting credentials with a budget of 0, so nothing is acquired '
                            'or published',
    'wto_timeseries': 'the acquisition is awaiting an API key, and the declaration says the indicator codes '
                      'must be re-checked once one exists, so the acquired scope is not yet fixed',
    'census_relationship_files': 'the published output holds crosswalk assertions and weight observations '
                                 'between geographies and emits no entity records, so no entity-key rule can '
                                 'select anything',
    'fec_individual_contributions': 'the published output is aggregated observations only: the pipeline asserts '
                                    'no persistent contributor identity and emits no entity records, so there '
                                    'is no entity-key rule to state',
    'fec_individual_contributions_2022': 'as fec_individual_contributions: aggregated observations only, no '
                                         'persistent contributor identity and no entity records',
    'fec_individual_contributions_2024': 'as fec_individual_contributions: aggregated observations only, no '
                                         'persistent contributor identity and no entity records',
    'mit_election_returns': 'the published output is observations keyed to offices and geographies and emits no '
                            'entity records, so no entity-key rule can select anything',
    'usgs_earthquakes': 'the published output holds event and observation records and emits no entity records; '
                        'an earthquake is published as an event, and this estimator counts entities only',
    'gleif_parent_relationships': 'the published output holds 1,900,906 relationship assertions between LEIs '
                                  'and exactly one entity record, so an entity-key rule on lei: would select '
                                  'almost none of it; the declared count of 487,964 counts relationships, not '
                                  'entities, and so cannot be used as a denominator for an entity count',
}


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
    """Coverage estimates for every curated dataset (or ``datasets``), plus the derived and uncurated ones.

    Every declaration lands in exactly one of three lists: ``estimates`` (a curated population),
    ``derived`` (produced in this repository, coverage inherited from named inputs) and
    ``uncurated`` (no population can be stated yet, with the specific missing thing named).
    """
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
    uncurated, derived = [], []
    for path in sorted(Path(catalog.root).glob('*/dataset.json')):
        dataset = path.parent.name
        if dataset in curated or (wanted is not None and dataset not in wanted):
            continue
        declaration = json.loads(path.read_text(encoding='utf-8'))
        row = {'dataset': dataset, 'status': declaration.get('status'),
               'declared_scope': (declaration.get('coverage') or {}).get('scope')}
        if dataset in DERIVED:
            spec = DERIVED[dataset]
            derived.append({**row, 'derived_from': list(spec['derived_from']),
                            'reason': 'produced inside this repository, not acquired from a population in the '
                                      'world: ' + spec['reason'] + '. Its coverage is inherited from '
                                      + ('its inputs (%s)' % ', '.join(spec['derived_from'])
                                         if spec['derived_from'] else 'whatever it was built from')
                                      + ', so no population is stated for it here'})
            continue
        reason = 'no population statement curated in worldmodel.coverage_estimator.POPULATIONS'
        if dataset in UNCURATED_REASONS:
            reason += ': ' + UNCURATED_REASONS[dataset]
        uncurated.append({**row, 'reason': reason})
    short = [e['estimate'] for e in estimates if e.get('measured_over_declared') is not None
             and e['measured_over_declared'] < 0.99]
    return {'as_of': date.today().isoformat(), 'estimates': estimates, 'uncurated': uncurated,
            'derived': derived,
            'summary': {'curated': len(estimates), 'uncurated': len(uncurated), 'derived': len(derived),
                        'curated_datasets': len({e['dataset'] for e in estimates}),
                        'by_kind': {kind: sum(e['kind'] == kind for e in estimates)
                                    for kind in ('register', 'subset', 'sample')},
                        'with_population_denominator': sum(bool(e.get('population_denominator')) for e in estimates),
                        'measured_below_declared': short},
            'what_this_does_not_establish': [
                'Representativeness: a dataset holding 99% of US counties can still be biased within them.',
                'Coverage of anything wider than the stated population (GLEIF is complete for LEI holders and says '
                'nothing about companies without one).',
                'That a declared count is right: measured_over_declared checks the published output against the '
                'declaration, not the declaration against the world.',
                'Anything about the datasets listed under "derived": their content is produced inside this '
                'repository, their coverage is whatever their inputs hold, and none of it is measured here.',
                'Anything about the datasets listed under "uncurated": each names what is missing before a '
                'population could be stated, and a missing statement is not a claim of full or of no coverage.'],
            'seconds': round(time.time() - started, 1)}
