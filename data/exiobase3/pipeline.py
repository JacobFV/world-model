"""EXIOBASE 3 (IOT_<year>_ixi.zip, pymrio text export) -> compact evidence records.

The full inter-industry matrix Z (49 regions x 163 industries = 7,987 squared cells)
is NOT emitted. Emission rules (all monetary values are M.EUR in the source and are
multiplied by 1e6 to EUR):

* entities: 49 regions (``exiobase:region:<code>``, with ``corresponds_to`` iso3 where
  the region is a single country; WA/WE/WF/WL/WM rest-of-world regions are aggregates),
  163 industries (``exiobase:industry:<slug>``) and 7,987 regional industries
  (``exiobase:regional_industry:<region>:<slug>``, ``classified_as`` industry,
  ``within`` region);
* ``gross_output`` (x) for every regional industry with non-zero output;
* ``intermediate_supply`` Z[i, j] only when Z[i, j] >= ``flow_min_meur`` M.EUR AND the
  input coefficient A[i, j] = Z[i, j] / x[j] >= ``flow_min_coefficient``;
  ``attributes.input_coefficient`` keeps A[i, j];
* ``final_demand_supply`` per (producing regional industry, consuming region), summed
  over the 7 final demand categories, when >= ``final_demand_min_meur`` M.EUR, plus
  per (regional industry, category) totals over all consuming regions;
* ``value_added_component`` (factor_inputs), ``employment_persons`` / ``employment_hours``
  (employment) and ``air_emission`` for the stressors listed in ``emission_stressors``,
  for every non-zero cell.
"""
import io
import json
import re
import zipfile

from .iso3166 import ISO2

DATASET = 'exiobase3'
REST_OF_WORLD = {'WA': 'Rest of World Asia and Pacific', 'WE': 'Rest of World Europe', 'WF': 'Rest of World Africa',
                 'WL': 'Rest of World America', 'WM': 'Rest of World Middle East'}
EXTENSION_UNITS = {'M.EUR': ('EUR', 1e6), 'M.hr': ('hours', 1e6), '1000 p': ('persons', 1e3), 'kg': ('kg', 1),
                   'kt': ('tonne', 1e3), 'Mm3': ('cubic_metre', 1e6), 'TJ': ('terajoule', 1)}


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def _lines(archive, member):
    stream = archive.open(member)
    return stream, io.TextIOWrapper(stream, encoding='utf-8', newline='')


def _year(archive):
    try:
        description = json.loads(archive.read('metadata.json')).get('description', '')
        return int(re.search(r'(19|20)\d{2}', description)[0])
    except (KeyError, TypeError, ValueError):
        return None


def run(context):
    coverage = context.raw_coverage() if hasattr(context, 'raw_coverage') else None
    if not coverage or coverage['sampled'] or coverage['layout'] != 'shards':
        raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire {DATASET} --allow-network)')
    p = context.parameters
    flow_min, coefficient_min = float(p.get('flow_min_meur', 1.0)), float(p.get('flow_min_coefficient', 0.001))
    final_min = float(p.get('final_demand_min_meur', 0.1))
    stressors = set(p.get('emission_stressors', []))
    for shard in context.raw_shards():
        observed_at = shard.get('retrieved_at') or context.raw_receipt()['retrieved_at']
        prefix = f'shard:{shard["index"]}'
        with zipfile.ZipFile(shard['path']) as archive:
            year = _year(archive)
            if year is None:
                m = re.search(r'IOT_(\d{4})_', (shard.get('request') or {}).get('url', '') + str(shard.get('name', '')))
                year = int(m[1]) if m else None
            if year is None:
                raise ValueError(f'{DATASET}: cannot determine table year for {prefix}')
            valid = {'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01'}

            def ev(member, line):
                return context.raw_evidence(f'{prefix}/member:{member}/line:{line}')

            def base(kind, identity, evidence, **fields):
                return {'kind': kind, 'id': f'exiobase:{year}:{identity}', 'observed_at': observed_at, 'evidence': evidence,
                        'attributes': {'source_dataset': DATASET}, **fields}

            def obs(identity, evidence, subject, metric, value, unit, **dimensions):
                record = base('observation', identity, evidence, subject=subject, metric=metric, value=value, unit=unit,
                              dimensions={'frequency': 'A', 'year': year, **dimensions}, **valid)
                return record

            # x.txt defines the canonical (region, sector) order used by Z/Y/F columns and rows.
            order, x, regions, industries = [], [], {}, {}
            stream, text = _lines(archive, 'x.txt')
            with stream:
                header = text.readline().rstrip('\n').split('\t')
                if header[:3] != ['region', 'sector', 'indout']:
                    raise ValueError(f'{DATASET}: unexpected x.txt header {header}')
                for line_number, line in enumerate(text, 2):
                    region, sector, value = line.rstrip('\n').split('\t')
                    evidence = ev('x.txt', line_number)
                    if region not in regions:
                        regions[region] = 'exiobase:region:' + region
                        if region in REST_OF_WORLD or region not in ISO2:
                            yield base('entity', 'region:' + region, evidence, entity_id=regions[region], entity_type='aggregate_cohort',
                                       label=REST_OF_WORLD.get(region, region), attributes={'source_dataset': DATASET, 'exiobase_code': region, 'aggregate': True})
                        else:
                            yield base('entity', 'region:' + region, evidence, entity_id=regions[region], entity_type='jurisdiction',
                                       label=ISO2[region][1], attributes={'source_dataset': DATASET, 'exiobase_code': region, 'aggregate': False})
                            yield base('assertion', 'corresponds_to:' + region, evidence, subject=regions[region],
                                       predicate='corresponds_to', object='iso3:' + ISO2[region][0])
                    if sector not in industries:
                        industries[sector] = 'exiobase:industry:' + slug(sector)
                        yield base('entity', 'industry:' + slug(sector), evidence, entity_id=industries[sector], entity_type='industry',
                                   label=sector, attributes={'source_dataset': DATASET, 'classification': 'EXIOBASE 3 ixi industries'})
                    entity_id = f'exiobase:regional_industry:{region}:{slug(sector)}'
                    order.append(entity_id)
                    yield base('entity', f'regional_industry:{region}:{slug(sector)}', evidence, entity_id=entity_id,
                               entity_type='business_cohort', label=f'{sector} ({region})',
                               attributes={'source_dataset': DATASET, 'aggregate': True, 'region': region})
                    yield base('assertion', f'classified_as:{region}:{slug(sector)}', evidence, subject=entity_id,
                               predicate='classified_as', object=industries[sector])
                    yield base('assertion', f'within:{region}:{slug(sector)}', evidence, subject=entity_id,
                               predicate='within', object=regions[region])
                    output = float(value)
                    x.append(output)
                    if output:
                        yield obs(f'x:{len(order) - 1}', evidence, entity_id, 'gross_output', output * 1e6, 'EUR')
            n = len(order)

            # Z: thresholded inter-industry flows (row i supplies column j).
            stream, text = _lines(archive, 'Z.txt')
            with stream:
                for _ in range(3):
                    text.readline()
                for i, line in enumerate(text):
                    cells = line.rstrip('\n').split('\t')
                    if len(cells) != n + 2:
                        raise ValueError(f'{DATASET}: Z.txt row {i} has {len(cells)} cells, expected {n + 2}')
                    evidence = None
                    for j, cell in enumerate(cells[2:]):
                        if not cell or cell == '0' or cell[0] == '-':
                            continue
                        value = float(cell)
                        if value < flow_min or not x[j] or value / x[j] < coefficient_min:
                            continue
                        if evidence is None:
                            evidence = ev('Z.txt', i + 4)
                        record = obs(f'Z:{i}:{j}', evidence, order[i], 'intermediate_supply', value * 1e6, 'EUR', purchaser=order[j])
                        record['attributes']['input_coefficient'] = value / x[j]
                        yield record

            # Y: final demand by consuming region (summed over categories) and by category.
            stream, text = _lines(archive, 'Y.txt')
            with stream:
                columns_region = text.readline().rstrip('\n').split('\t')[2:]
                columns_category = text.readline().rstrip('\n').split('\t')[2:]
                text.readline()
                for i, line in enumerate(text):
                    cells = line.rstrip('\n').split('\t')[2:]
                    by_region, by_category = {}, {}
                    for region, category, cell in zip(columns_region, columns_category, cells):
                        if cell == '0':
                            continue
                        value = float(cell)
                        by_region[region] = by_region.get(region, 0.0) + value
                        by_category[category] = by_category.get(category, 0.0) + value
                    evidence = ev('Y.txt', i + 4)
                    for region, value in by_region.items():
                        if abs(value) >= final_min:
                            yield obs(f'Y:{i}:{region}', evidence, order[i], 'final_demand_supply', value * 1e6, 'EUR',
                                      consumer_region=regions.get(region, 'exiobase:region:' + region), final_demand_category='all')
                    for category, value in by_category.items():
                        if value:
                            yield obs(f'Ycat:{i}:{slug(category)}', evidence, order[i], 'final_demand_supply', value * 1e6, 'EUR',
                                      consumer_region='all', final_demand_category=slug(category))

            # Satellite accounts.
            for folder, select, metric_for in (
                    ('factor_inputs', None, lambda name: 'value_added_component'),
                    ('employment', None, lambda name: 'employment_hours' if name.startswith('Employment hours') else 'employment_persons'),
                    ('air_emissions', stressors, lambda name: 'air_emission')):
                units = {}
                for line in archive.read(f'{folder}/unit.txt').decode('utf-8').splitlines()[1:]:
                    name, _, unit = line.rpartition('\t')
                    units[name] = unit
                stream, text = _lines(archive, f'{folder}/F.txt')
                with stream:
                    for _ in range(3):
                        text.readline()
                    for line_number, line in enumerate(text, 4):
                        cells = line.rstrip('\n').split('\t')
                        name = cells[0]
                        if select is not None and name not in select:
                            continue
                        if len(cells) != n + 1:
                            raise ValueError(f'{DATASET}: {folder}/F.txt line {line_number} has {len(cells)} cells')
                        unit, scale = EXTENSION_UNITS.get(units.get(name, ''), (units.get(name, 'as_published'), 1))
                        evidence = ev(f'{folder}/F.txt', line_number)
                        component = slug(name)
                        for j, cell in enumerate(cells[1:]):
                            if cell == '0':
                                continue
                            value = float(cell)
                            if value:
                                record = obs(f'F:{folder}:{component}:{j}', evidence, order[j], metric_for(name), value * scale, unit,
                                             component=component)
                                record['attributes']['source_unit'] = units.get(name)
                                yield record
