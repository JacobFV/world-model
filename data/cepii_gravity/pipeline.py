"""CEPII Gravity V202211 -> tidy country-year and country-pair evidence.

Country-year (subject ``iso3:XXX``): population, GDP (current and PPP), GDP per capita,
GATT/WTO/EU membership, emitted once per Gravity country_id and year from the origin
columns of rows where ``country_exists_o == 1``.

Country pair (subject = origin ``iso3:O`` with ``dimensions.partner`` = ``iso3:D``, one
undirected record per pair using the lexicographically smaller country_id as subject):
distances, contiguity, language, colonial ties, legal origin, religion, RTAs, diplomatic
disagreement and social connectedness. Values are run-length encoded over consecutive
years: one observation per maximal run of an identical value, valid [first year, last
year + 1). Rows where either country does not exist are skipped. Null source cells
produce no observation (absence = not reported), keeping output compact.

Memory is bounded: the file is sorted by (country_id_o, country_id_d, year); one pair's
runs are held at a time, and an unsorted input raises.
"""
import re

from .evidence import Evidence, KeySet, num, year_bounds

DATA_MEMBERS = ['Gravity_V*.csv']
COUNTRY_MEMBERS = ['Countries_V*.csv']

COUNTRY_VARS = [
    ('pop_o', 'population', 'thousand_people'),
    ('gdp_o', 'gdp_current_usd', 'thousand_USD'),
    ('gdpcap_o', 'gdp_per_capita_current_usd', 'thousand_USD_per_person'),
    ('gdp_ppp_o', 'gdp_ppp_current_international_usd', 'thousand_international_USD'),
    ('gdpcap_ppp_o', 'gdp_per_capita_ppp_current_international_usd', 'thousand_international_USD_per_person'),
    ('gatt_o', 'gatt_member', 'indicator_0_1'),
    ('wto_o', 'wto_member', 'indicator_0_1'),
    ('eu_o', 'eu_member', 'indicator_0_1'),
]
PAIR_VARS = [
    ('dist', 'bilateral_distance_most_populated_cities', 'km'),
    ('distw_harmonic', 'bilateral_distance_population_weighted', 'km'),
    ('distcap', 'bilateral_distance_capitals', 'km'),
    ('contig', 'contiguity', 'indicator_0_1'),
    ('comlang_off', 'common_official_language', 'indicator_0_1'),
    ('comlang_ethno', 'common_ethnic_language', 'indicator_0_1'),
    ('comcol', 'common_colonizer', 'indicator_0_1'),
    ('col45', 'colonial_relationship_post_1945', 'indicator_0_1'),
    ('col_dep_ever', 'colonial_dependency_ever', 'indicator_0_1'),
    ('sibling_ever', 'sibling_ever', 'indicator_0_1'),
    ('comleg_posttrans', 'common_legal_origin', 'indicator_0_1'),
    ('comrelig', 'religious_proximity_index', 'index_0_1'),
    ('fta_wto', 'regional_trade_agreement_in_force', 'indicator_0_1'),
    ('rta_type', 'regional_trade_agreement_type', 'category_code'),
    ('rta_coverage', 'regional_trade_agreement_coverage', 'category_code'),
    ('diplo_disagreement', 'diplomatic_disagreement', 'score'),
    ('scaled_sci_2021', 'social_connectedness_index_2021', 'index'),
]


def _labels(context, name):
    try:
        rows = list(context.raw_rows(0, format='csv', members=[f'Label_{name}_V*.csv']))
    except ValueError:
        return {}
    return {str(num(row[name])): row.get(name + '_lbl') for _, row in rows if num(row.get(name)) is not None}


def run(context):
    ev = Evidence(context, 'gravity')
    labels = {'rta_type': _labels(context, 'rta_type'), 'rta_coverage': _labels(context, 'rta_coverage')}
    countries = {}
    try:
        for locator, row in context.raw_rows(0, format='csv', members=COUNTRY_MEMBERS, encoding='utf-8-sig'):
            cid, iso3 = row['country_id'].strip(), (row.get('iso3') or '').strip().upper()
            key = 'iso3:' + iso3 if re.fullmatch(r'[A-Z]{3}', iso3) else 'cepii:country:' + cid
            countries[cid] = key
            record = ev.entity(key, 'country', row.get('country') or cid, locator, cepii_country_id=cid,
                               iso3num=num(row.get('iso3num')), long_name=row.get('countrylong') or None,
                               iso2=(row.get('iso2') or '').strip() or None)
            if record:
                yield record

        def country(cid, locator):
            key = countries.get(cid)
            if key is None:
                key = countries[cid] = 'cepii:country:' + cid
                record = ev.entity(key, 'country', cid, locator, cepii_country_id=cid, unlisted_in_countries_table=True)
                if record:
                    return key, [record]
            return key, []

        finished = KeySet()
        pair, runs = None, {}

        def emit_run(column, metric, unit, state):
            value, first, last, locator = state
            start, end = year_bounds(first)[0], year_bounds(last)[1]
            o, d = pair
            dims = {'partner': countries.get(d, 'cepii:country:' + d), 'pair': 'undirected', 'frequency': 'annual'}
            if countries.get(o, '').split(':', 1)[-1] != o or countries.get(d, '').split(':', 1)[-1] != d:
                dims.update(country_id=o, partner_country_id=d)
            record = ev.observation(countries.get(o, 'cepii:country:' + o), metric, value, unit, locator,
                                    valid_from=start, valid_to=end, dimensions=dims, identity=[o, d, column])
            record['attributes']['years'] = last - first + 1
            if column in labels and labels[column].get(str(value)):
                record['attributes']['label'] = labels[column][str(value)]
            return record

        def flush():
            records = [emit_run(column, metric, unit, runs[column]) for column, metric, unit in PAIR_VARS if column in runs]
            runs.clear()
            return records

        for locator, row in context.raw_rows(0, format='csv', members=DATA_MEMBERS, encoding='utf-8-sig'):
            o, d, year = row['country_id_o'], row['country_id_d'], int(row['year'])
            if o == d:
                continue
            out = []
            if row['country_exists_o'] == '1' and ev.keys.add(f'cy|{o}|{year}'):
                subject, created = country(o, locator)
                out.extend(created)
                start, end = year_bounds(year)
                for column, metric, unit in COUNTRY_VARS:
                    value = num(row.get(column))
                    if value is not None:
                        out.append(ev.observation(subject, metric, value, unit, locator, valid_from=start, valid_to=end,
                                                  dimensions={'frequency': 'annual'}, identity=[o]))
            if o < d:
                if pair != (o, d):
                    if pair is not None:
                        out.extend(flush())
                    if not finished.add(o + '|' + d):
                        raise ValueError(f'{locator}: Gravity rows are not grouped by country pair; cannot run-length encode')
                    pair = (o, d)
                    for cid in (o, d):
                        out.extend(country(cid, locator)[1])
                exists = row['country_exists_o'] == '1' and row['country_exists_d'] == '1'
                for column, metric, unit in PAIR_VARS:
                    value = num(row.get(column)) if exists else None
                    state = runs.get(column)
                    if state is not None and value == state[0] and year == state[2] + 1:
                        state[2] = year
                        continue
                    if state is not None:
                        out.append(emit_run(column, metric, unit, state))
                        del runs[column]
                    if value is not None:
                        runs[column] = [value, year, year, locator]
            yield from out
        if pair is not None:
            yield from flush()
        finished.close()
    finally:
        ev.close()
