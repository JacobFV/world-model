"""National US transport networks from TIGER, NTAD, USACE, MARAD, NGA and OpenFlights.

* Single-payload samples / imported JSONL keep the original Overpass normalizer.
* Full sharded acquisitions dispatch on the shard ``name`` prefix:

  - ``tiger2024_primaryroads.zip`` / ``tiger2024_rails.zip``: shapefiles (stdlib
    reader in ``helpers.py``) -> ``road`` / rail-line ``infrastructure`` entities
    with length, bounding box and endpoints (no full geometry);
  - ``ntad_rail_lines-pNNN.geojson`` -> ``rail_connects_to`` assertions between
    ``ntad:rail_node:N`` with owner, trackage rights, track count and length;
  - ``ntad_rail_nodes`` -> rail node ``location`` entities;
  - ``ntad_nhfn`` -> National Highway Freight Network ``road`` segments;
  - ``usace_principal_ports`` -> ``port`` entities with CY2023 tonnage observations;
  - ``usace_waterway_nodes`` / ``usace_waterway_links`` -> waterway graph;
  - ``marad_strategic_seaports`` and ``ntad_intermodal_*`` -> facilities;
  - ``nga_wpi_pub150.csv`` -> World Port Index ``port`` entities with depth and
    vessel-size limit observations;
  - ``openflights_routes.dat`` -> ``air_route_to`` assertions between IATA/ICAO
    airport identifiers (topology frozen around 2014).
"""
import csv
import io
import json
import math
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS

FIPS_STATE = {code: state for state, code in STATE_FIPS.items()}
KM_PER_MILE = 1.609344
NHFN_CODES = {1: 'primary_highway_freight_system', 2: 'phfs_intermodal_connector', 3: 'critical_urban_freight_corridor',
              4: 'critical_rural_freight_corridor', 5: 'other_interstate_non_phfs'}
WPI_MEASURES = [('Channel Depth (m)', 'channel_depth'), ('Anchorage Depth (m)', 'anchorage_depth'),
                ('Cargo Pier Depth (m)', 'cargo_pier_depth'), ('Oil Terminal Depth (m)', 'oil_terminal_depth'),
                ('Liquified Natural Gas Terminal Depth (m)', 'lng_terminal_depth'), ('Tidal Range (m)', 'tidal_range'),
                ('Entrance Width (m)', 'entrance_width'), ('Maximum Vessel Length (m)', 'max_vessel_length'),
                ('Maximum Vessel Beam (m)', 'max_vessel_beam'), ('Maximum Vessel Draft (m)', 'max_vessel_draft')]
WPI_FLAGS = ('Facilities - Container', 'Facilities - Solid Bulk', 'Facilities - Liquid Bulk', 'Facilities - Oil Terminal',
             'Facilities - LNG Terminal', 'Facilities - Ro-Ro', 'Facilities - Breakbulk', 'Dry Dock', 'Railway',
             'First Port of Entry', 'Repairs', 'Harbor Use', 'Shelter Afforded', 'Supplies - Fuel Oil', 'Supplies - Diesel Oil')
ROUTE_FIELDS = ['airline', 'airline_id', 'source', 'source_id', 'destination', 'destination_id', 'codeshare', 'stops', 'equipment']
LAYERS = ('tiger2024_primaryroads', 'tiger2024_rails', 'ntad_rail_lines', 'ntad_rail_nodes', 'ntad_nhfn', 'usace_principal_ports',
          'usace_waterway_nodes', 'usace_waterway_links', 'marad_strategic_seaports', 'ntad_intermodal_rail_tofc_cofc',
          'ntad_intermodal_marine_roro', 'ntad_intermodal_liquid_bulk', 'ntad_intermodal_pipeline_terminals',
          'ntad_intermodal_air_to_truck', 'nga_wpi_pub150', 'openflights_routes')


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    else:
        text = str(value).strip().replace(',', '')
        if not text:
            return None
        try:
            number = float(text)
        except ValueError:
            return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else number


def _text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _layer(name):
    for layer in sorted(LAYERS, key=len, reverse=True):
        if name.startswith(layer):
            return layer
    return None


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
        raise ValueError('transport: no sample artifact supplied')
    if _sharded(context):
        yield from _normalize_full(context)
        return
    yield from _normalize_sample(context)


class _Emitter:
    def __init__(self, context):
        self.raw = context.raw_inputs[0]
        self.retrieved = context.raw_receipt()['retrieved_at']
        self.complete = context.raw_coverage()['complete']
        self.shard = None

    def at(self, shard):
        self.shard = shard
        self.observed = shard.get('retrieved_at') or self.retrieved
        self.snapshot = self.observed[:10]

    def evidence(self, locator):
        return [{'input': self.raw, 'locator': f"shard:{self.shard['index']}/{locator}"}]

    def entity(self, key, entity_type, label, locator, **attributes):
        return {'kind': 'entity', 'id': key, 'entity_id': key, 'entity_type': entity_type, 'label': label or key,
                'observed_at': self.observed, 'evidence': self.evidence(locator),
                'attributes': {k: v for k, v in attributes.items() if v is not None}}

    def link(self, identity, subject, predicate, target, locator, **attributes):
        return {'kind': 'assertion', 'id': identity, 'subject': subject, 'predicate': predicate, 'object': target,
                'observed_at': self.observed, 'evidence': self.evidence(locator),
                'attributes': {k: v for k, v in attributes.items() if v is not None}}

    def literal(self, identity, subject, predicate, value, locator, **attributes):
        return {'kind': 'assertion', 'id': identity, 'subject': subject, 'predicate': predicate, 'value': value,
                'observed_at': self.observed, 'evidence': self.evidence(locator), 'attributes': attributes}

    def measure(self, identity, subject, metric, value, unit, locator, valid_from=None, valid_to=None, dimensions=None, **attributes):
        value = _number(value)
        if value is None:
            return None
        record = {'kind': 'observation', 'id': identity, 'subject': subject, 'metric': metric, 'value': value, 'unit': unit,
                  'dimensions': dimensions or {}, 'observed_at': self.observed, 'evidence': self.evidence(locator),
                  'valid_from': valid_from or self.snapshot,
                  'attributes': {'validity_basis': 'reference_period' if valid_to else 'snapshot_retrieval_date', **attributes}}
        if valid_to:
            record['valid_to'] = valid_to
        return record


def _state(fips):
    code = _text(fips)
    if code and code.isdigit():
        return 'geo:US:state:' + code.zfill(2)
    return None


def _normalize_full(context):
    from .helpers import geojson_features, geometry_parts, line_summary, shapefile_records
    out = _Emitter(context)
    shards = list(context.raw_shards())
    unknown = sorted({s.get('name') for s in shards if _layer(s.get('name') or '') is None})
    if unknown:
        raise ValueError('Unrecognized transport shards: ' + ', '.join(map(str, unknown)))
    route_airports = set()  # Bounded by the number of distinct airport codes (<10k).
    for shard in shards:
        out.at(shard)
        layer = _layer(shard['name'])
        if layer.startswith('tiger2024_'):
            rails = layer == 'tiger2024_rails'
            for number, attributes, shape in shapefile_records(shard['path']):
                summary = line_summary(shape['parts'])
                if summary is None:
                    continue
                key = 'tiger:linearid:' + str(attributes['LINEARID'])
                locator = f'record:{number}'
                yield out.entity(key, 'infrastructure' if rails else 'road',
                                 attributes.get('FULLNAME') or ('TIGER rail line ' if rails else 'TIGER primary road ') + str(attributes['LINEARID']),
                                 locator, network='rail' if rails else 'road', mtfcc=attributes.get('MTFCC'),
                                 route_type=attributes.get('RTTYP'), length_km=summary.get('length_km'), bbox=summary['bbox'],
                                 start=summary.get('start'), end=summary.get('end'), vertices=summary['vertices'],
                                 source_layer='TIGER/Line 2024 ' + ('RAILS' if rails else 'PRIMARYROADS'),
                                 geometry_note='length from source vertices; full geometry in raw shapefile')
                length = out.measure(key + ':length', key, 'rail_line_length' if rails else 'road_segment_length',
                                     summary.get('length_km'), 'km', locator, method='haversine over source vertices')
                if length:
                    yield length
            continue
        if layer == 'nga_wpi_pub150':
            with open(shard['path'], encoding='utf-8-sig', newline='') as text:
                shared = _duplicate_wpi_numbers(csv.DictReader(text))
            with open(shard['path'], encoding='utf-8-sig', newline='') as text:
                yield from _wpi(out, csv.reader(text), shared)
            continue
        if layer == 'openflights_routes':
            with open(shard['path'], encoding='utf-8-sig', newline='') as text:
                yield from _routes(out, csv.reader(text), route_airports)
            continue
        features, _ = geojson_features(shard['path'])
        for position, feature in enumerate(features):
            properties = feature.get('properties') or {}
            locator = f'feature:{position}'
            parts = geometry_parts(feature.get('geometry'))
            polygon = (feature.get('geometry') or {}).get('type') in ('Polygon', 'MultiPolygon')
            summary = line_summary(parts, area=polygon) if parts else None
            yield from _feature(out, layer, properties, summary, locator)
    for code in sorted(route_airports):
        scheme = 'iata' if len(code) == 3 else 'icao'
        key = f'{scheme}:{code}'
        yield {'kind': 'entity', 'id': key, 'entity_id': key, 'entity_type': 'airport', 'label': f'{scheme.upper()} {code}',
               'observed_at': out.retrieved, 'evidence': [{'input': out.raw, 'locator': 'layer:openflights_routes'}],
               'attributes': {'code_scheme': scheme.upper(), 'identity_note': 'code-keyed airport reference; resolve to ourairports entities via their identified_by assertions'}}


def _feature(out, layer, p, summary, locator):
    point = (summary or {}).get('point') or (summary or {}).get('centroid')
    common = {'longitude': point[0] if point else None, 'latitude': point[1] if point else None,
              'bbox': (summary or {}).get('bbox'), 'source_layer': layer, 'objectid': p.get('OBJECTID')}
    if layer == 'ntad_rail_lines':
        a, b = p.get('FRFRANODE'), p.get('TOFRANODE')
        if a is None or b is None:
            return
        owners = [p[f'RROWNER{i}'] for i in (1, 2, 3) if _text(p.get(f'RROWNER{i}'))]
        rights = [p[f'TRKRGHTS{i}'] for i in range(1, 10) if _text(p.get(f'TRKRGHTS{i}'))]
        miles = _number(p.get('MILES'))
        yield out.link(f"ntad:rail_link:{p['FRAARCID']}", f'ntad:rail_node:{a}', 'rail_connects_to', f'ntad:rail_node:{b}', locator,
                       bidirectional=True, length_km=round(miles * KM_PER_MILE, 4) if miles is not None else None,
                       length_miles=miles, owners=owners, trackage_rights=rights, tracks=_number(p.get('TRACKS')),
                       network_class=_text(p.get('NET')), passenger=_text(p.get('PASSNGR')), strategic_rail_corridor=_text(p.get('STRACNET')),
                       subdivision=_text(p.get('SUBDIV')), yard=_text(p.get('YARDNAME')), state=_state(p.get('STFIPS')),
                       county_fips=_text(p.get('STCNTYFIPS')), country=_text(p.get('COUNTRY')), bbox=common['bbox'],
                       source_layer=layer)
    elif layer == 'ntad_rail_nodes':
        key = f"ntad:rail_node:{p['FRANODEID']}"
        yield out.entity(key, 'location', f"NARN rail node {p['FRANODEID']}", locator, **common, country=_text(p.get('COUNTRY')),
                         state=_state(p.get('STFIPS')), county_fips=_text(p.get('STCYFIPS')), passenger=_text(p.get('PASSNGR')),
                         passenger_station=_text(p.get('PASSNGRSTN')), boundary=_text(p.get('BNDRY')), network='rail')
    elif layer == 'ntad_nhfn':
        key = f"ntad:nhfn:{p.get('ROUTEID')}:{p.get('BEGMP')}:{p.get('ENDMP')}:{p.get('OBJECTID')}"
        code = _number(p.get('NHFN_CODE'))
        yield out.entity(key, 'road', _text(p.get('LNAME')) or _text(p.get('SIGN1')) or key, locator, **common,
                         route_id=_text(p.get('ROUTEID')), sign=_text(p.get('SIGN1')), nhfn_code=code,
                         nhfn_class=NHFN_CODES.get(code), state=_state(p.get('STATE_CODE')), begin_milepost=_number(p.get('BEGMP')),
                         end_milepost=_number(p.get('ENDMP')), year_recorded=_number(p.get('YEAR_RECOR')), network='road')
        length = out.measure(key + ':length', key, 'road_segment_length', p.get('LENGTH'), 'mile', locator, source_field='LENGTH')
        if length:
            yield length
        if _state(p.get('STATE_CODE')):
            yield out.link(key + ':within', key, 'within', _state(p.get('STATE_CODE')), locator)
    elif layer == 'usace_principal_ports':
        code = _text(p.get('PORT'))
        code = str(int(float(code))) if code and code.replace('.', '').isdigit() else code
        key = f'usace:port:{code}'
        yield out.entity(key, 'port', _text(p.get('PORTNAME')) or key, locator, **common, usace_port_code=code,
                         tonnage_rank_2023=_number(p.get('RANK')), port_type=_text(p.get('TYPE')),
                         location_basis='vertex mean of the port area polygon')
        for field, metric in (('TOTAL', 'port_tonnage_total'), ('DOMESTIC', 'port_tonnage_domestic'), ('FOREIGN_', 'port_tonnage_foreign'),
                              ('IMPORTS', 'port_tonnage_imports'), ('EXPORTS', 'port_tonnage_exports')):
            obs = out.measure(f'{key}:{metric}:2023', key, metric, p.get(field), 'short_ton', locator, '2023-01-01', '2024-01-01',
                              {'calendar_year': 2023}, source='USACE Waterborne Commerce Statistics Center, CY2023')
            if obs:
                yield obs
    elif layer == 'usace_waterway_nodes':
        key = f"usace:waterway_node:{p['NODENUM']}"
        yield out.entity(key, 'location', _text(p.get('PORT_NAME')) or f"Waterway node {p['NODENUM']}", locator, **common,
                         port_id=_text(p.get('PORT_ID')), state=_text(p.get('STATE')), fips=_text(p.get('FIPS')),
                         non_us=_text(p.get('NON_US')), network='waterway')
    elif layer == 'usace_waterway_links':
        a, b = p.get('ANODE'), p.get('BNODE')
        if a is None or b is None:
            return
        yield out.link(f"usace:waterway_link:{p.get('LINKNUM')}:{p.get('OBJECTID')}", f'usace:waterway_node:{a}', 'waterway_connects_to',
                       f'usace:waterway_node:{b}', locator, bidirectional=True, length_miles=_number(p.get('LENGTH')),
                       length_km=round(_number(p.get('LENGTH')) * KM_PER_MILE, 4) if _number(p.get('LENGTH')) is not None else None,
                       link_name=_text(p.get('LINKNAME')), river=_text(p.get('RIVERNAME')), waterway=_number(p.get('WATERWAY')),
                       link_type=_number(p.get('LINKTYPE')), functional_class=_number(p.get('FUNC_CLASS')),
                       waterway_type=_number(p.get('WTWY_TYPE')), geo_class=_text(p.get('GEO_CLASS')), direction_code=_number(p.get('DIR')),
                       a_mile=_number(p.get('AMILE')), b_mile=_number(p.get('BMILE')), state=_text(p.get('STATE')),
                       bbox=common['bbox'], source_layer=layer)
    elif layer == 'marad_strategic_seaports':
        name = _text(p.get('port_name'))
        key = 'marad:strategic_seaport:' + digest([name or p.get('OBJECTID')])[:16]
        yield out.entity(key, 'port', name, locator, **common, designation='National Port Readiness Network commercial strategic seaport')
        for field, metric in (('total', 'port_tonnage_total'), ('domestic', 'port_tonnage_domestic'), ('foreign_', 'port_tonnage_foreign'),
                              ('imports', 'port_tonnage_imports'), ('exports', 'port_tonnage_exports')):
            obs = out.measure(f'{key}:{metric}', key, metric, p.get(field), 'short_ton', locator,
                              reference_year='not stated in layer metadata (compiled 2021-10-04)')
            if obs:
                yield obs
    elif layer.startswith('ntad_intermodal_'):
        kind = layer[len('ntad_intermodal_'):]
        name = (_text(p.get('TERMINAL')) or _text(p.get('TERM_NAME')) or _text(p.get('Facility_Name')) or _text(p.get('LOCID'))
                or f'{kind} facility {p.get("OBJECTID")}')
        key = f"ntad:intermodal:{kind}:{p.get('OBJECTID')}"
        extra = {}
        for field in ('PORT', 'RAIL_CO', 'CITY', 'STATE', 'COMP_NAME', 'OPERATED_BY', 'Operator_Name', 'Transport_Modes', 'SCTG_CODE',
                      'FACILITY_C', 'LOCID', 'NAV_UNIT_ID', 'NAV_UNIT_I', 'TRUCK', 'RAIL', 'WATER', 'CRUDE_OIL', 'REFINED', 'NGL',
                      'Crude_Oil', 'Petroleum_Products', 'Chemical_Products', 'Liquified_Gasses_Products'):
            if _text(p.get(field)) is not None:
                extra[field.lower()] = p[field]
        yield out.entity(key, 'facility', name, locator, **common, facility_class='intermodal_' + kind, **extra)
        for field, metric, unit in (('CAPACITY', 'terminal_storage_capacity', 'barrel'), ('Capacity_Barrels', 'terminal_storage_capacity', 'barrel'),
                                    ('EST_AREA', 'facility_area', 'square_foot')):
            obs = out.measure(f'{key}:{metric}', key, metric, p.get(field), unit, locator, source_field=field)
            if obs and obs['value'] > 0:
                yield obs


def _duplicate_wpi_numbers(rows):
    """WPI numbers used by more than one row (Pub. 150 reuses a few numbers for distinct ports)."""
    seen, shared = set(), set()
    for row in rows:
        number = _number(row.get('World Port Index Number'))
        if number is None:
            continue
        if number in seen:
            shared.add(number)
        seen.add(number)
    return shared


def _wpi(out, reader, shared=frozenset()):
    header = next(reader)
    for line, values in enumerate(reader, 2):
        if not values:
            continue
        row = dict(zip(header, values))
        number = _number(row.get('World Port Index Number'))
        if number is None:
            continue
        key = f'wpi:{number}'
        if number in shared:
            key += f":oid:{_number(row.get('OID_'))}"
        locator = f'line:{line}'
        flags = {name.lower().replace(' - ', '_').replace(' ', '_').replace('-', '_'): _text(row.get(name)) for name in WPI_FLAGS
                 if _text(row.get(name)) not in (None, 'Unknown')}
        yield out.entity(key, 'port', _text(row.get('Main Port Name')) or key, locator, wpi_number=number,
                         wpi_number_shared=True if number in shared else None, source_oid=_number(row.get('OID_')),
                         alternate_name=_text(row.get('Alternate Port Name')), unlocode=_text(row.get('UN/LOCODE')),
                         country_name=_text(row.get('Country Code')), region=_text(row.get('Region Name')),
                         water_body=_text(row.get('World Water Body')), harbor_size=_text(row.get('Harbor Size')),
                         harbor_type=_text(row.get('Harbor Type')), latitude=_number(row.get('Latitude')),
                         longitude=_number(row.get('Longitude')), **flags)
        locode = (_text(row.get('UN/LOCODE')) or '').replace(' ', '')
        if len(locode) == 5:
            yield out.literal(key + ':identified_by:unlocode', key, 'identified_by', 'unlocode:' + locode, locator, scheme='UN/LOCODE')
        for field, metric in WPI_MEASURES:
            value = _number(row.get(field))
            # WPI encodes unknown numeric attributes as 0.
            if value:
                yield out.measure(f'{key}:{metric}', key, metric, value, 'm', locator, source_field=field)


def _routes(out, reader, airports):
    for line, values in enumerate(reader, 1):
        if not values:
            continue
        if len(values) != len(ROUTE_FIELDS):
            raise ValueError(f'openflights_routes line {line}: expected {len(ROUTE_FIELDS)} fields')
        row = dict(zip(ROUTE_FIELDS, values))
        codes = []
        for field in ('source', 'destination'):
            code = row[field].strip().upper()
            if len(code) in (3, 4) and code.isalnum():
                codes.append(code)
        if len(codes) != 2:
            continue
        airports.update(codes)
        keys = [('iata:' if len(c) == 3 else 'icao:') + c for c in codes]
        airline = row['airline'].strip()
        yield out.link(f'openflights:route:{line}', keys[0], 'air_route_to', keys[1], f'line:{line}',
                       airline_code=airline or None, airline_openflights_id=None if row['airline_id'] in ('\\N', '') else row['airline_id'],
                       codeshare=row['codeshare'] == 'Y', stops=_number(row['stops']), equipment=row['equipment'].split() or None,
                       temporal_note='OpenFlights route database, last updated around June 2014; not a current schedule')


def _normalize_sample(context):
    dataset = 'transport'
    seen = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
            if not line.strip():
                continue
            row = json.loads(line)
            evidence = context.raw_evidence(f'line:{number}', index)
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
                if not missing and metric not in ('development_status', 'legal_status'):
                    value = float(value)
                    if value.is_integer():
                        value = int(value)
                r = base('observation', ['obs', number, subject, metric, start], subject=subject, metric=metric, value=None if missing else value, unit=unit, dimensions={'subject': subject})
                r['attributes'].update(source_unit=unit, **attrs)
                if missing:
                    r['missing_reason'] = 'source_missing_or_suppressed'
                if start:
                    r['valid_from'] = start
                if end:
                    r['valid_to'] = end
                return r

            key = entity('osm:' + row['type'] + ':' + str(row['id']), 'road', row.get('tags', {}).get('name', 'OSM road ' + str(row['id'])))
            rel(key, 'within', geo('CA'))
            if row.get('tags', {}).get('tiger:county') == 'San Francisco, CA':
                county = entity('geo:US:county:06075', 'county', 'San Francisco County', synthetic=True)
                rel(county, 'within', geo('CA'))
                rel(key, 'within', county)
            for field, metric in [('lat', 'latitude'), ('lon', 'longitude')]:
                if field in row.get('center', {}):
                    obs(key, metric, row['center'][field], 'degrees', acquired, None, spatial_role='way bounding-box center, not full geometry')
            yield from out
