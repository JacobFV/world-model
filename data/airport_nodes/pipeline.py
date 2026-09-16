"""OurAirports global aviation reference.

* Single-payload samples / imported JSONL keep the original airport normalizer.
* Full sharded acquisitions (airports, runways, navaids, airport-frequencies,
  countries, regions CSVs) emit airports, runways and navaids as entities with
  coordinate/elevation/runway observations, radio frequencies as observations
  on airports, and country/region jurisdictions keyed ``iso3:XXX`` and
  ``iso3166-2:XX-YY`` (US states additionally linked to ``geo:US:state:NN``).
"""
import json
import math
from worldmodel.util import digest

FILES = ('countries.csv', 'regions.csv', 'airports.csv', 'runways.csv', 'airport-frequencies.csv', 'navaids.csv')
AIRPORT_TYPES = {'large_airport', 'medium_airport', 'small_airport', 'heliport', 'seaplane_base', 'balloonport', 'closed'}
STATE_FIPS = dict(zip('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split(),
                      '01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56'.split()))
# Territories with their own ISO 3166-1 code are listed in OurAirports as countries.
TERRITORY_FIPS = {'AS': '60', 'GU': '66', 'MP': '69', 'PR': '72', 'VI': '78'}


def _number(value):
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    number = float(text)
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _text(value):
    text = (value or '').strip()
    return text or None


def _sharded(context):
    """True only for full sharded acquisitions; samples, imports and test doubles use the legacy path."""
    coverage = getattr(context, 'raw_coverage', None)
    if not callable(coverage):
        return False
    try:
        value = coverage()
    except Exception:
        return False
    return isinstance(value, dict) and value.get('layout') == 'shards'


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source sample artifact required')
    if _sharded(context):
        yield from _normalize_full(context)
        return
    yield from _normalize_sample(context)


def _normalize_full(context):
    from worldmodel.raw_readers import iter_rows
    from .countries import country_entity, NAMES
    raw = context.raw_inputs[0]
    shards = {shard.get('name'): shard for shard in context.raw_shards()}
    missing = [name for name in FILES if name not in shards]
    if missing:
        raise ValueError('OurAirports acquisition lacks shards: ' + ', '.join(missing))
    coverage = context.raw_coverage()
    retrieved = context.raw_receipt()['retrieved_at']
    for shard in shards.values():
        shard.setdefault('retrieved_at', retrieved)

    def rows(name):
        shard = shards[name]
        return shard, iter_rows([shard], {'format': 'csv'})

    def record(kind, identity, shard, locator, **fields):
        return {'kind': kind, 'id': identity, 'observed_at': shard['retrieved_at'],
                'evidence': [{'input': raw, 'locator': locator}], **fields}

    def snapshot(shard):
        return shard['retrieved_at'][:10]

    def observation(shard, locator, identity, subject, metric, value, unit, dimensions=None, **attributes):
        value = _number(value)
        if value is None:
            return None
        return record('observation', identity, shard, locator, subject=subject, metric=metric, value=value, unit=unit,
                      valid_from=snapshot(shard), dimensions=dimensions or {},
                      attributes={'validity_basis': 'snapshot_retrieval_date', 'complete_source': coverage['complete'], **attributes})

    countries = set()
    shard, stream = rows('countries.csv')
    for locator, row in stream:
        key = country_entity(row['code'])
        countries.add(row['code'])
        yield record('entity', key, shard, locator, entity_id=key, entity_type='country', label=row['name'] or key,
                     attributes={'iso_alpha2': row['code'], 'continent': _text(row['continent']),
                                 'ourairports_id': _text(row['id']), 'wikipedia': _text(row['wikipedia_link'])})
        if row['code'] in TERRITORY_FIPS:
            yield record('assertion', key + ':same_as:geo', shard, locator, subject=key, predicate='same_as',
                         object='geo:US:state:' + TERRITORY_FIPS[row['code']], attributes={'basis': 'US territory FIPS code'})

    region_ids = set()
    shard, stream = rows('regions.csv')
    for locator, row in stream:
        code = row['code']
        key = 'iso3166-2:' + code
        region_ids.add(code)
        country = country_entity(row['iso_country'])
        yield record('entity', key, shard, locator, entity_id=key, entity_type='jurisdiction', label=row['name'] or key,
                     attributes={'region_code': code, 'local_code': _text(row['local_code']), 'iso_country': row['iso_country'],
                                 'continent': _text(row['continent']), 'ourairports_id': _text(row['id']),
                                 'code_basis': 'ISO 3166-2 or OurAirports local placeholder (suffix -U-A = unassigned)'})
        yield record('assertion', key + ':within', shard, locator, subject=key, predicate='within', object=country)
        suffix = code.split('-', 1)[-1]
        if row['iso_country'] == 'US' and suffix in STATE_FIPS:
            yield record('assertion', key + ':same_as:geo', shard, locator, subject=key, predicate='same_as',
                         object='geo:US:state:' + STATE_FIPS[suffix], attributes={'basis': 'USPS state code to FIPS'})

    idents = {}
    shard, stream = rows('airports.csv')
    for locator, row in stream:
        key = 'ourairports:' + row['id']
        idents[row['ident']] = key
        airport_type = row['type']
        attributes = {'ident': row['ident'], 'airport_type': airport_type, 'scheduled_service': row['scheduled_service'] == 'yes',
                      'municipality': _text(row['municipality']), 'iso_country': row['iso_country'], 'iso_region': row['iso_region'],
                      'continent': _text(row['continent']), 'latitude': _number(row['latitude_deg']),
                      'longitude': _number(row['longitude_deg'])}
        for field in ('icao_code', 'iata_code', 'gps_code', 'local_code'):
            if _text(row.get(field)):
                attributes[field] = row[field].strip()
        if airport_type not in AIRPORT_TYPES:
            attributes['unrecognized_type'] = True
        yield record('entity', key, shard, locator, entity_id=key, entity_type='airport', label=row['name'] or row['ident'],
                     attributes=attributes)
        region = row['iso_region']
        yield record('assertion', key + ':within', shard, locator, subject=key, predicate='within',
                     object='iso3166-2:' + region if region in region_ids else country_entity(row['iso_country']))
        for scheme, field in (('iata', 'iata_code'), ('icao', 'icao_code')):
            if _text(row.get(field)):
                yield record('assertion', f'{key}:identified_by:{scheme}', shard, locator, subject=key,
                             predicate='identified_by', value=f'{scheme}:{row[field].strip()}',
                             attributes={'scheme': scheme.upper() + ' code'})
        for metric, field, unit in (('latitude', 'latitude_deg', 'degrees'), ('longitude', 'longitude_deg', 'degrees'),
                                    ('elevation', 'elevation_ft', 'ft')):
            obs = observation(shard, locator, f'{key}:{metric}', key, metric, row[field], unit)
            if obs:
                yield obs

    shard, stream = rows('runways.csv')
    for locator, row in stream:
        key = 'ourairports:runway:' + row['id']
        airport = 'ourairports:' + row['airport_ref']
        label = f"{row['airport_ident']} runway {row['le_ident'] or '?'}/{row['he_ident'] or '?'}"
        attributes = {'airport_ident': row['airport_ident'], 'surface': _text(row['surface']),
                      'lighted': row['lighted'] == '1', 'closed': row['closed'] == '1',
                      'runway_role': 'runway'}
        for end in ('le', 'he'):
            for field in ('ident', 'latitude_deg', 'longitude_deg', 'elevation_ft', 'heading_degT', 'displaced_threshold_ft'):
                value = row.get(f'{end}_{field}')
                if _text(value):
                    attributes[f'{end}_{field}'] = value.strip() if field == 'ident' else _number(value)
        yield record('entity', key, shard, locator, entity_id=key, entity_type='infrastructure', label=label, attributes=attributes)
        yield record('assertion', key + ':located_in', shard, locator, subject=key, predicate='located_in', object=airport)
        for metric, field in (('runway_length', 'length_ft'), ('runway_width', 'width_ft')):
            obs = observation(shard, locator, f'{key}:{metric}', key, metric, row[field], 'ft')
            if obs:
                yield obs

    shard, stream = rows('airport-frequencies.csv')
    for locator, row in stream:
        airport = 'ourairports:' + row['airport_ref']
        obs = observation(shard, locator, 'ourairports:frequency:' + row['id'], airport, 'radio_frequency', row['frequency_mhz'],
                          'MHz', dimensions={'frequency_type': row['type'] or 'unspecified'},
                          description=_text(row['description']))
        if obs:
            yield obs

    shard, stream = rows('navaids.csv')
    for locator, row in stream:
        key = 'ourairports:navaid:' + row['id']
        attributes = {'ident': row['ident'], 'navaid_type': row['type'], 'iso_country': row['iso_country'],
                      'usage_type': _text(row['usageType']), 'power': _text(row['power']),
                      'latitude': _number(row['latitude_deg']), 'longitude': _number(row['longitude_deg']),
                      'dme_channel': _text(row['dme_channel']), 'magnetic_variation_deg': _number(row['magnetic_variation_deg'])}
        yield record('entity', key, shard, locator, entity_id=key, entity_type='infrastructure',
                     label=f"{row['name'] or row['ident']} {row['type']}".strip(), attributes=attributes)
        associated = _text(row['associated_airport'])
        if associated and associated in idents:
            yield record('assertion', key + ':located_in', shard, locator, subject=key, predicate='serves_airport',
                         object=idents[associated])
        elif associated:
            yield record('assertion', key + ':associated_airport', shard, locator, subject=key, predicate='associated_airport_ident',
                         value=associated, attributes={'unresolved_reason': 'ident_not_in_airports_file'})
        elif row['iso_country']:
            yield record('assertion', key + ':within', shard, locator, subject=key, predicate='within',
                         object=country_entity(row['iso_country']))
        for metric, field, unit in (('navaid_frequency', 'frequency_khz', 'kHz'), ('dme_frequency', 'dme_frequency_khz', 'kHz'),
                                    ('elevation', 'elevation_ft', 'ft')):
            obs = observation(shard, locator, f'{key}:{metric}', key, metric, row[field], unit)
            if obs:
                yield obs
    del NAMES, countries


def _normalize_sample(context):
    dataset = 'airport_nodes'
    seen = set()
    record_ids = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            out = []

            def base(kind, identity, **fields):
                r = {'kind': kind, 'id': 'strategic:' + digest([dataset, ref, identity]), 'observed_at': acquired, 'evidence': context.raw_evidence('line:' + str(line_number), index), 'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only'}, **fields}
                if r['id'] not in record_ids:
                    record_ids.add(r['id'])
                    out.append(r)
                return r

            def entity(key, typ, label=None, **attrs):
                if key not in seen:
                    seen.add(key)
                    r = base('entity', ['entity', key], entity_id=key, entity_type=typ, label=label or key)
                    r['attributes'].update(attrs)
                return key

            def observation(subject, metric, value, unit, start=None, end=None, scale=1, **attrs):
                missing = value is None or str(value).strip() in ('', '.', 'NA', 'N/A', 'null')
                if not missing:
                    value = float(value) * scale
                    if not math.isfinite(value):
                        raise ValueError('Nonfinite source measurement')
                    if value.is_integer():
                        value = int(value)
                r = base('observation', [line_number, subject, metric, start], subject=subject, metric=metric, value=None if missing else value, unit=unit, dimensions={'subject': subject})
                if start:
                    r['valid_from'] = start
                if end:
                    r['valid_to'] = end
                if missing:
                    r['missing_reason'] = 'source_missing'
                r['attributes'].update(attrs)
            key = entity('ourairports:' + str(row['id']), 'airport', row['name'], ident=row['ident'], iata_code=row.get('iata_code'), icao_code=row.get('icao_code'), airport_type=row['type'])
            for field, metric in [('latitude_deg', 'latitude'), ('longitude_deg', 'longitude')]:
                observation(key, metric, row[field], 'degrees', acquired, validity_basis='acquired_snapshot')
            yield from out
