"""Ember yearly electricity (long format) and OWID energy panel -> country-year observations.

Both CSVs arrive as shards of one raw artifact; the file is recognised by its header.
Countries use ``iso3:XXX``; Ember/OWID regional or income aggregates and historical
states without an ISO code become ``ember:region:<slug>`` / ``owid:region:<slug>``
locations flagged ``aggregate``. OWID derived columns (year-on-year changes,
per-capita ratios) are skipped because they are recomputable from level series.
"""
import re

from .evidence import Evidence, num, year_bounds

EMBER_METRICS = {
    ('Capacity', 'GW'): ('electricity_capacity', 'GW'),
    ('Electricity generation', 'TWh'): ('electricity_generation', 'TWh'),
    ('Electricity generation', '%'): ('electricity_generation_share', 'percent'),
    ('Electricity demand', 'TWh'): ('electricity_demand', 'TWh'),
    ('Electricity demand', 'MWh'): ('electricity_demand_per_capita', 'MWh/person'),
    ('Electricity imports', 'TWh'): ('electricity_net_imports', 'TWh'),
    ('Power sector emissions', 'mtCO2'): ('power_sector_co2_emissions', 'MtCO2'),
    ('Power sector emissions', 'gCO2/kWh'): ('power_sector_co2_intensity', 'gCO2/kWh'),
}

# OWID level columns -> (metric, unit, fuel dimension or None).
_FUELS = ('biofuel', 'coal', 'fossil', 'gas', 'hydro', 'low_carbon', 'nuclear', 'oil', 'other_renewable',
          'renewables', 'solar', 'wind')


def owid_column(name):
    if name == 'population':
        return 'population', 'people', None
    if name == 'gdp':
        return 'gdp_ppp', 'international_USD_2011', None
    if name == 'primary_energy_consumption':
        return 'primary_energy_consumption', 'TWh', 'total'
    if name == 'energy_per_gdp':
        return 'primary_energy_intensity', 'kWh/international_USD_2011', None
    if name == 'electricity_generation':
        return 'electricity_generation', 'TWh', 'total'
    if name == 'electricity_demand':
        return 'electricity_demand', 'TWh', None
    if name == 'net_elec_imports':
        return 'electricity_net_imports', 'TWh', None
    if name == 'carbon_intensity_elec':
        return 'power_sector_co2_intensity', 'gCO2/kWh', None
    if name == 'greenhouse_gas_emissions':
        return 'power_sector_ghg_emissions', 'MtCO2e', None
    if name == 'electricity_share_energy':
        return 'electricity_share_of_primary_energy', 'percent', None
    match = re.fullmatch(r'(\w+?)_(consumption|production|electricity|share_elec|share_energy)', name)
    if match:
        fuel = match[1]
        fuel = {'other_renewables': 'other_renewable'}.get(fuel, fuel)
        if fuel not in _FUELS:
            return None
        return {'consumption': ('primary_energy_consumption', 'TWh'), 'production': ('energy_production', 'TWh'),
                'electricity': ('electricity_generation', 'TWh'), 'share_elec': ('electricity_generation_share', 'percent'),
                'share_energy': ('primary_energy_share', 'percent')}[match[2]] + (fuel,)
    if name in ('other_renewable_exc_biofuel_electricity',):
        return 'electricity_generation', 'TWh', 'other_renewable_excluding_bioenergy'
    return None


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def run(context):
    out = Evidence(context, 'ember_owid')
    coverage = context.raw_coverage()
    if coverage['sampled']:
        raise ValueError('ember_owid_energy has no sample adapter; acquire the full files first')
    try:
        for locator, row in context.raw_rows(0, format='csv', compression='auto'):
            if 'Variable' in row:
                yield from ember(out, locator, row)
            elif 'iso_code' in row:
                yield from owid(out, locator, row)
            else:
                raise ValueError(f'{locator}: unrecognised energy CSV header')
    finally:
        out.close()


# Former ISO codes and historical states stay out of the shared iso3: namespace.
HISTORICAL_CODES = {'ANT', 'BUR', 'CSK', 'DDR', 'SCG', 'SUN', 'TMP', 'VDR', 'YMD', 'YUG', 'ZAR'}
HISTORICAL_NAMES = {'USSR': 'SUN', 'Czechoslovakia': 'CSK', 'East Germany': 'DDR', 'West Germany': 'DEU_WEST',
                    'Yugoslavia': 'YUG', 'Serbia and Montenegro': 'SCG', 'Netherlands Antilles': 'ANT'}
NAME_ISO = {'Kosovo': 'XKX'}  # OWID publishes Kosovo without iso_code; other datasets use XKX


def place(out, locator, name, iso, publisher, aggregate):
    code = iso if iso and re.fullmatch(r'[A-Z]{3}', iso) else HISTORICAL_NAMES.get(name) or NAME_ISO.get(name)
    if code in HISTORICAL_CODES or name in HISTORICAL_NAMES:
        code = HISTORICAL_NAMES.get(name, code)
        key = f'{publisher}:historical_country:{code}'
        record = out.entity(key, 'country', name, locator, historical=True, source_code=iso or None)
    elif code and not (aggregate and name not in NAME_ISO):
        key = 'iso3:' + code
        record = out.entity(key, 'country', name, locator, iso3=code)
    else:
        key = f'{publisher}:region:{slug(name)}'
        record = out.entity(key, 'location', name, locator, aggregate=True, source_code=iso or None,
                            note='regional, income-group or historical aggregate as defined by the publisher')
    return key, record


def ember(out, locator, row):
    area_type = row['Area type']
    key, record = place(out, locator, row['Area'], row['ISO 3 code'], 'ember', area_type != 'Country or economy')
    if record:
        record['attributes'].update(ember_region=row.get('Ember region') or None, continent=row.get('Continent') or None,
                                    groups=[g for g in ('EU', 'OECD', 'G20', 'G7', 'ASEAN') if row.get(g) == '1.0'])
        yield record
    spec = EMBER_METRICS.get((row['Category'], row['Unit']))
    if spec is None:
        raise ValueError(f'{locator}: unmapped Ember category/unit {row["Category"]!r}/{row["Unit"]!r}')
    metric, unit = spec
    start, end = year_bounds(row['Year'])
    subcategory = row['Subcategory']
    dims = {'frequency': 'annual', 'publisher': 'ember'}
    if subcategory in ('Fuel', 'Aggregate fuel'):
        dims['fuel'] = slug(row['Variable'])
        dims['fuel_level'] = 'fuel' if subcategory == 'Fuel' else 'aggregate_fuel'
    elif subcategory == 'Total':
        dims['fuel'] = 'total'
    value = num(row['Value'])
    yield out.observation(key, metric, value, unit, locator, valid_from=start, valid_to=end, dimensions=dims,
                          missing_reason='source_blank', aggregate=area_type != 'Country or economy',
                          source_variable=row['Variable'])


def owid(out, locator, row):
    iso = row['iso_code']
    aggregate = not iso or iso.startswith('OWID')
    key, record = place(out, locator, row['country'], iso, 'owid', aggregate)
    if record:
        yield record
    start, end = year_bounds(row['year'])
    for column, text in row.items():
        if text in ('', None) or column in ('country', 'year', 'iso_code'):
            continue
        spec = owid_column(column)
        if spec is None:
            continue
        metric, unit, fuel = spec
        value = num(text)
        if value is None:
            continue
        dims = {'frequency': 'annual', 'publisher': 'owid'}
        if fuel:
            dims['fuel'] = fuel
        yield out.observation(key, metric, value, unit, locator, valid_from=start, valid_to=end, dimensions=dims,
                              aggregate=aggregate, source_column=column)
