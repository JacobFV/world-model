"""Census geographic relationship files -> weighted crosswalk assertions.

Crosswalks (attributes.crosswalk):
  census_zcta520_county20              2020 ZCTA5 -> 2020 county; land/total-area shares (reverse: county -> ZCTA)
  census_zcta510_zcta520               2010 ZCTA5 -> 2020 ZCTA5; land/total-area shares
  census_cousub10_cousub20             2010 county subdivision -> 2020 county subdivision; area shares
  census_county10_county20_via_tract   2010 county -> 2020 county, aggregated from the 2020<->2010 tract file
                                       (Census publishes no county20_county10 relationship file)
  census_zcta510_county10              2010 ZCTA5 -> 2010 county; population (primary), housing-unit and area shares
Unassigned parts (area in one vintage with no counterpart) become `land_area_without_counterpart` observations.
"""
import csv
import io
from pathlib import PurePosixPath

LICENCE = 'US Census Bureau; public domain (17 U.S.C. 105)'
DAY = {'2010': '2010-01-01', '2020': '2020-01-01'}

# file basename -> spec. Each 2020 relationship file names both sides by suffix.
FILES = {
    'tab20_zcta520_county20_natl.txt': dict(crosswalk='census_zcta520_county20', source=('ZCTA5_20', 'zcta', '2020'),
                                            target=('COUNTY_20', 'county', '2020')),
    'tab20_zcta510_zcta520_natl.txt': dict(crosswalk='census_zcta510_zcta520', source=('ZCTA5_10', 'zcta', '2010'),
                                           target=('ZCTA5_20', 'zcta', '2020')),
    'tab20_cousub20_cousub10_natl.txt': dict(crosswalk='census_cousub10_cousub20', source=('COUSUB_10', 'cousub', '2010'),
                                             target=('COUSUB_20', 'cousub', '2020')),
    'tab20_tract20_tract10_natl.txt': dict(crosswalk='census_county10_county20_via_tract', source=('TRACT_10', 'county', '2010'),
                                           target=('TRACT_20', 'county', '2020'), aggregate=5),
    'zcta_county_rel_10.txt': dict(crosswalk='census_zcta510_county10', legacy2010=True),
}
SYSTEM = {'zcta': 'zcta5', 'county': 'county', 'cousub': 'county_subdivision'}


def run(context):
    if not context.raw_inputs:
        raise ValueError('census_relationship_files: no raw artifact; run wm acquire census_relationship_files')
    for index, _ in enumerate(context.raw_inputs):
        if context.raw_coverage(index)['sampled']:
            raise ValueError('census_relationship_files has no sample adapter; acquire the full files')
        for shard in context.raw_shards(index):
            name = _name(shard)
            spec = FILES.get(name)
            if spec is None:
                raise ValueError('census_relationship_files: unrecognized raw file ' + name)
            observed = shard.get('retrieved_at') or context.raw_receipt(index)['retrieved_at']
            url = (shard.get('request') or {}).get('url') or shard.get('url')
            emit = _Emitter(context, index, observed, url)
            if spec.get('legacy2010'):
                yield from _legacy_zcta_county(shard, spec, emit)
            elif spec.get('aggregate'):
                yield from _aggregate(shard, spec, emit)
            else:
                yield from _pairs(shard, spec, emit)


def _name(shard):
    request = shard.get('request') or {}
    url = request.get('url') or shard.get('url') or shard.get('name') or ''
    return PurePosixPath(url.split('?')[0]).name


def _lines(shard, delimiter):
    """(physical line, row dict) with a BOM-tolerant header; files are a few MiB to tens of MiB, streamed."""
    with open(shard['path'], 'rb') as raw:
        text = io.TextIOWrapper(raw, encoding='utf-8-sig', newline='')
        reader = csv.reader(text, delimiter=delimiter)
        header = [h.strip() for h in next(reader)]
        last = reader.line_num
        for values in reader:
            start, last = last + 1, reader.line_num
            if not values or values == ['']:
                continue
            if len(values) != len(header):
                raise ValueError(f'shard:{shard["index"]}/line:{start}: {len(values)} fields, header has {len(header)}')
            yield start, dict(zip(header, values))


def _int(value):
    value = (value or '').strip()
    return int(value) if value else 0


def _share(part, total):
    return round(part / total, 12) if total > 0 else None


def _geo(kind, code):
    return f'geo:US:{kind}:{code}'


class _Emitter:
    def __init__(self, context, index, observed, url):
        self.context, self.index, self.observed, self.url = context, index, observed, url

    def evidence(self, locator):
        return self.context.raw_evidence(locator, self.index)

    def crosswalk(self, spec, source, target, locator, *, weights, reverse_weights, primary, extra):
        (_, skind, svint), (_, tkind, tvint) = spec['source'], spec['target']
        weight = weights.get(primary)
        basis = primary
        if weight is None and weights.get('total_area') is not None:
            weight, basis = weights['total_area'], 'total_area'  # no land in the source unit (water-only)
        attrs = {'crosswalk': spec['crosswalk'], 'source_system': f'{SYSTEM[skind]}_{svint}',
                 'target_system': f'{SYSTEM[tkind]}_{tvint}', 'source_code': source, 'target_code': target,
                 'source_vintage': svint, 'target_vintage': tvint, 'weight': weight, 'weight_key': basis,
                 'weight_basis': f'{basis}_{svint}_part_share','weights': weights, 'reverse_weights': reverse_weights,
                 'relationship': 'area_overlap', 'source_url': self.url, 'licence': LICENCE, **extra}
        return {'kind': 'assertion', 'id': f'xw:{spec["crosswalk"]}:{source}:{target}',
                'subject': _geo(skind, source), 'predicate': 'maps_to', 'object': _geo(tkind, target),
                'valid_from': DAY[tvint], 'observed_at': self.observed, 'evidence': self.evidence(locator),
                'attributes': attrs}

    def unmatched(self, spec, kind, code, vintage, other_vintage, land, water, locator, line):
        return {'kind': 'observation', 'id': f'xw:{spec["crosswalk"]}:unmatched:{vintage}:{code}:{line}',
                'subject': _geo(kind, code), 'metric': 'land_area_without_counterpart', 'value': land, 'unit': 'm2',
                'dimensions': {'geography_vintage': vintage, 'counterpart_vintage': other_vintage,
                               'relationship_file': spec['crosswalk']},
                'valid_from': DAY[vintage], 'observed_at': self.observed, 'evidence': self.evidence(locator),
                'attributes': {'water_area_m2': water, 'note': 'part of this unit not covered by any unit of the counterpart vintage'}}


def _pairs(shard, spec, emit):
    (sfield, skind, svint), (tfield, tkind, tvint) = spec['source'], spec['target']
    for line, row in _lines(shard, '|'):
        locator = f'shard:{shard["index"]}/line:{line}'
        source, target = row['GEOID_' + sfield].strip(), row['GEOID_' + tfield].strip()
        land, water = _int(row['AREALAND_PART']), _int(row['AREAWATER_PART'])
        if not source or not target:
            kind, code, vint, other = (tkind, target, tvint, svint) if target else (skind, source, svint, tvint)
            if code:
                yield emit.unmatched(spec, kind, code, vint, other, land, water, locator, line)
            continue
        sl, sw = _int(row['AREALAND_' + sfield]), _int(row['AREAWATER_' + sfield])
        tl, tw = _int(row['AREALAND_' + tfield]), _int(row['AREAWATER_' + tfield])
        weights = {'land_area': _share(land, sl), 'total_area': _share(land + water, sl + sw)}
        reverse = {'land_area': _share(land, tl), 'total_area': _share(land + water, tl + tw)}
        yield emit.crosswalk(spec, source, target, locator, weights=weights, reverse_weights=reverse, primary='land_area',
                             extra={'part_land_m2': land, 'part_water_m2': water,
                                    'source_name': row.get('NAMELSAD_' + sfield) or None,
                                    'target_name': row.get('NAMELSAD_' + tfield) or None})


def _aggregate(shard, spec, emit):
    """Sum tract parts to county pairs; county totals are sums of parts (tracts tile each county)."""
    (sfield, _, svint), (tfield, _, tvint) = spec['source'], spec['target']
    width = spec['aggregate']
    pairs, totals = {}, {}
    for line, row in _lines(shard, '|'):
        source, target = row['GEOID_' + sfield].strip()[:width], row['GEOID_' + tfield].strip()[:width]
        land, water = _int(row['AREALAND_PART']), _int(row['AREAWATER_PART'])
        for side, code in (('s', source), ('t', target)):
            if code:
                total = totals.setdefault((side, code), [0, 0])
                total[0] += land
                total[1] += water
        if not source or not target:
            continue
        item = pairs.setdefault((source, target), [0, 0, 0, line])
        item[0] += land
        item[1] += water
        item[2] += 1
    for (source, target), (land, water, rows, first) in sorted(pairs.items()):
        sl, sw = totals[('s', source)]
        tl, tw = totals[('t', target)]
        weights = {'land_area': _share(land, sl), 'total_area': _share(land + water, sl + sw)}
        reverse = {'land_area': _share(land, tl), 'total_area': _share(land + water, tl + tw)}
        yield emit.crosswalk(spec, source, target, f'shard:{shard["index"]}/line:{first}', weights=weights,
                             reverse_weights=reverse, primary='land_area',
                             extra={'part_land_m2': land, 'part_water_m2': water, 'aggregated_tract_rows': rows,
                                    'aggregate': True, 'identity': source == target,
                                    'crosswalk_note': 'Aggregated from tab20_tract20_tract10_natl.txt; tiny shares are '
                                                      'usually boundary re-digitization slivers, not real transfers'})


def _legacy_zcta_county(shard, spec, emit):
    spec = {**spec, 'source': ('ZCTA5', 'zcta', '2010'), 'target': ('GEOID', 'county', '2010')}
    for line, row in _lines(shard, ','):
        locator = f'shard:{shard["index"]}/line:{line}'
        zcta, county = row['ZCTA5'].strip(), row['GEOID'].strip()
        if not zcta or not county:
            continue
        pop, hu, area, land = (_int(row[k]) for k in ('POPPT', 'HUPT', 'AREAPT', 'AREALANDPT'))
        weights = {'population': _share(pop, _int(row['ZPOP'])), 'housing_units': _share(hu, _int(row['ZHU'])),
                   'land_area': _share(land, _int(row['ZAREALAND'])), 'total_area': _share(area, _int(row['ZAREA']))}
        reverse = {'population': _share(pop, _int(row['COPOP'])), 'housing_units': _share(hu, _int(row['COHU'])),
                   'land_area': _share(land, _int(row['COAREALAND'])), 'total_area': _share(area, _int(row['COAREA']))}
        record = emit.crosswalk(spec, zcta, county, locator, weights=weights, reverse_weights=reverse, primary='population',
                                extra={'part_population': pop, 'part_housing_units': hu, 'part_land_m2': land,
                                       'part_total_area_m2': area})
        record['valid_to'] = '2020-01-01'
        if weights['population'] is None:
            record['attributes']['crosswalk_note'] = 'ZCTA has zero 2010 population; weight falls back to area'
        yield record
