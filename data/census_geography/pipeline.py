'TIGER/Line boundaries and GNIS places'
import json
import zipfile
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS

VINTAGE = 2024
VALID = ('2024-01-01', '2025-01-01')  # 2024 boundaries are as of 1 January 2024
LAYERS = {'state': 'state', 'county': 'county', 'cbsa': 'location', 'csa': 'location', 'place': 'jurisdiction',
          'tract': 'location', 'zcta520': 'location', 'zcta': 'location'}


def run(context):
    """Sample artifacts keep the legacy adapter; full Gazetteer/cartographic files use `_run_full`."""
    if not context.raw_inputs:
        raise ValueError('census_geography: no raw artifact supplied')
    if _sampled(context):
        yield from _run_sample(context)
    else:
        yield from _run_full(context)


def _sampled(context, index=0):
    try:
        coverage = context.raw_coverage(index)
    except Exception:
        return True
    return not isinstance(coverage, dict) or bool(coverage.get('sampled'))


def _members(context, index):
    """Yield (shard, zip archive, member names) for every ZIP shard of a raw input."""
    for shard in context.raw_shards(index):
        if not zipfile.is_zipfile(shard['path']):
            continue
        with zipfile.ZipFile(shard['path']) as archive:
            yield shard, archive, [info.filename for info in archive.infolist()]


def _layer(name):
    """cb_2024_us_county_500k.shp -> ('county', 2024); 2024_Gaz_counties_national.txt -> ('county', 2024)."""
    base = name.rsplit('/', 1)[-1]
    if base.startswith('cb_'):
        parts = base.split('_')
        return parts[3], int(parts[1])
    if '_Gaz_' in base:
        kind = base.split('_Gaz_')[1].split('_national')[0]
        return {'counties': 'county', 'place': 'place', 'cbsa': 'cbsa', 'tracts': 'tract', 'zcta': 'zcta520'}.get(kind, kind), int(base[:4])
    return None, None


def _entity_id(layer, geoid):
    return {'zcta520': 'geo:US:zcta:'}.get(layer, f'geo:US:{layer}:') + geoid


def _parent(layer, row):
    geoid = row.get('GEOID', '')
    if layer == 'county':
        return 'geo:US:state:' + geoid[:2]
    if layer == 'tract':
        return 'geo:US:county:' + geoid[:5]
    if layer == 'place':
        return 'geo:US:state:' + geoid[:2]
    if layer == 'cbsa' and str(row.get('CSAFP') or '').strip():
        return 'geo:US:csa:' + str(row['CSAFP']).strip()
    if layer == 'state':
        return 'geo:US'
    return None


def _number(text):
    text = str(text if text is not None else '').strip()
    if not text:
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def _run_full(context):
    """Entities, hierarchy and area/internal-point observations from Gazetteer files; state/CSA from DBF attributes."""
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        emitted = set()
        yield {'kind': 'entity', 'id': 'tiger:entity:geo:US', 'entity_id': 'geo:US', 'entity_type': 'country',
               'label': 'United States', 'observed_at': observed,
               'evidence': context.raw_evidence(f'shard:0', index), 'attributes': {}}
        gazetteer_layers = set()
        for shard, archive, names in _members(context, index):
            for name in names:
                layer, vintage = _layer(name)
                if name.endswith('.txt') and layer:
                    gazetteer_layers.add(layer)
        for shard, archive, names in _members(context, index):
            for name in names:
                layer, vintage = _layer(name)
                if layer is None:
                    continue
                prefix = f'shard:{shard["index"]}/member:{name}'
                if name.endswith('.txt'):
                    from worldmodel.raw_readers import iter_rows
                    rows = iter_rows([shard], {'format': 'tsv', 'members': [name], 'encoding': 'utf-8-sig', 'strict': False})
                    for locator, row in rows:
                        row = {k.strip(): (v.strip() if isinstance(v, str) else v) for k, v in row.items() if k}
                        yield from _gazetteer(context, index, observed, emitted, layer, vintage, locator, row)
                elif name.endswith('.dbf') and layer not in gazetteer_layers:
                    from .helpers import dbf_records
                    with archive.open(name) as stream:
                        for number, row in enumerate(dbf_records(stream), 1):
                            if row is None:
                                continue
                            yield from _gazetteer(context, index, observed, emitted, layer, vintage,
                                                  f'{prefix}/record:{number}', row)


def _gazetteer(context, index, observed, emitted, layer, vintage, locator, row):
    geoid = str(row.get('GEOID') or row.get('GEOID20') or '').strip()
    subject = _entity_id(layer, geoid)
    evidence = context.raw_evidence(locator, index)
    if subject in emitted:
        return
    emitted.add(subject)
    typ = LAYERS[layer]
    if layer == 'place' and row.get('FUNCSTAT') not in ('A', 'B', 'C', 'G'):
        typ = 'location'  # statistical places (CDPs) are not governments
    label = row.get('NAME') or (('ZCTA5 ' if layer.startswith('zcta') else 'Tract ') + geoid)
    attrs = {'geoid': geoid, 'geography_vintage': vintage, 'layer': layer}
    for field in ('USPS', 'STUSPS', 'LSAD', 'FUNCSTAT', 'ANSICODE', 'CBSA_TYPE'):
        if row.get(field) not in (None, ''):
            attrs[field.lower()] = row[field]
    yield {'kind': 'entity', 'id': 'tiger:entity:' + subject, 'entity_id': subject, 'entity_type': typ, 'label': label,
           'observed_at': observed, 'evidence': evidence, 'attributes': attrs}
    parent = _parent(layer, row)
    if parent:
        yield {'kind': 'assertion', 'id': f'tiger:within:{subject}', 'subject': subject, 'predicate': 'within',
               'object': parent, 'observed_at': observed, 'evidence': evidence,
               'attributes': {'relationship': 'census_geographic_hierarchy', 'geography_vintage': vintage}}
    valid_from, valid_to = f'{vintage}-01-01', f'{vintage + 1}-01-01'
    dims = {'geography_vintage': vintage}
    for field, metric, unit, extra in (('ALAND', 'land_area', 'm2', {}), ('AWATER', 'water_area', 'm2', {}),
                                       ('INTPTLAT', 'latitude', 'degrees', {'point': 'internal_point'}),
                                       ('INTPTLONG', 'longitude', 'degrees', {'point': 'internal_point'})):
        value = _number(row.get(field))
        if value is None:
            continue
        yield {'kind': 'observation', 'id': f'tiger{vintage % 100}:{layer}:{geoid}:{metric}', 'subject': subject,
               'metric': metric, 'value': value, 'unit': unit, 'valid_from': valid_from, 'valid_to': valid_to,
               'observed_at': observed, 'evidence': evidence, 'dimensions': {**dims, **extra},
               'attributes': {'source_field': field}}


def geometry(context):
    """Compact polygons from cartographic boundary shapefiles (separate generic JSONL stage).

    Coordinates are NAD83 lon/lat quantized to 1e-5 degrees and delta-encoded per ring
    (see helpers.encode_ring/decode_ring); ring order and shapefile orientation are preserved.
    """
    from .helpers import SCALE, dbf_records, encode_ring, shp_records
    if _sampled(context):
        raise ValueError('census_geography geometry requires the full cartographic boundary files')
    for index, _ in enumerate(context.raw_inputs):
        for shard, archive, names in _members(context, index):
            shp = next((n for n in names if n.endswith('.shp')), None)
            if not shp:
                continue
            layer, vintage = _layer(shp)
            dbf = shp[:-4] + '.dbf'
            with archive.open(shp) as shapes, archive.open(dbf) as table:
                for (number, bbox, rings), row in zip(shp_records(shapes), dbf_records(table)):
                    if row is None or bbox is None:
                        continue
                    geoid = str(row.get('GEOID') or row.get('GEOID20') or row.get('ZCTA5CE20') or '').strip()
                    if not geoid:
                        raise ValueError(f'{shp}: record {number} has no GEOID field')
                    yield {'id': f'geom:{layer}:{geoid}', 'entity_id': _entity_id(layer, geoid), 'layer': layer,
                           'geoid': geoid, 'geography_vintage': vintage, 'source_file': shp.rsplit('/', 1)[-1],
                           'crs': 'EPSG:4269', 'scale': SCALE, 'encoding': 'delta_quantized_rings',
                           'bbox': [round(v, 5) for v in bbox], 'rings': [encode_ring(r) for r in rings],
                           'raw_index': index, 'locator': f'shard:{shard["index"]}/member:{shp}/record:{number}'}


def _run_sample(context):
    dataset = 'census_geography'
    if not context.raw_inputs:
        raise ValueError(f'{dataset}: no sample artifact supplied')
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

            def year(y):
                return (f'{int(y):04d}-01-01', f'{int(y) + 1:04d}-01-01')
            state = geo(row['STATE'])
            key = entity('geo:US:county:' + row['GEOID'], 'county', row['NAME'])
            rel(key, 'within', state)
            yield from out
