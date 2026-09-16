"""OMB CBSA delineation List 1 files (2018, 2020, 2023) -> dated county membership assertions.

Per vintage (validity = [bulletin date, next bulletin date)):
  county  within cbsa    crosswalk omb_county_cbsa_<v>    (weight 1; central/outlying + metro/micro attributes)
  county  within metdiv  crosswalk omb_county_metdiv_<v>
  county  within csa     crosswalk omb_county_csa_<v>
  cbsa    within csa     crosswalk omb_cbsa_csa_<v>;  metdiv within cbsa  crosswalk omb_metdiv_cbsa_<v>
  literal assertions: cbsa_type (metropolitan/micropolitan_statistical_area), cbsa_county_status (central/outlying)
  entities geo:US:cbsa:, geo:US:metdiv:, geo:US:csa: labelled with that vintage's titles.
2023 files use Connecticut planning regions (county-equivalents 09110-09190).
"""
from pathlib import PurePosixPath

from .helpers import xls_rows

LICENCE = 'OMB delineations / US Census Bureau; public domain (17 U.S.C. 105)'
HEADER = 'CBSA Code'


def run(context):
    if not context.raw_inputs:
        raise ValueError('cbsa_delineations: no raw artifact; run wm acquire cbsa_delineations')
    vintages = context.parameters['vintages']
    for index, _ in enumerate(context.raw_inputs):
        if context.raw_coverage(index)['sampled']:
            raise ValueError('cbsa_delineations has no sample adapter; acquire the full files')
        for shard in context.raw_shards(index):
            url = (shard.get('request') or {}).get('url') or shard.get('url') or ''
            name = PurePosixPath(url.split('?')[0]).name
            if name not in vintages:
                raise ValueError('cbsa_delineations: unrecognized raw file ' + (name or str(shard['path'])))
            observed = shard.get('retrieved_at') or context.raw_receipt(index)['retrieved_at']
            yield from _vintage(context, index, shard, name, url, vintages[name], observed)


def _text(value):
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return ' '.join(str(value if value is not None else '').split())


def _table(shard, name):
    """Yield (locator, {header: text}) for List 1 data rows from .xlsx (worldmodel.workbooks) or legacy .xls."""
    if name.lower().endswith('.xlsx'):
        from worldmodel.raw_readers import iter_rows
        for header_row in (3, 1, 2, 4, 5):
            try:
                rows = iter_rows([shard], {'format': 'xlsx', 'header_row': header_row})
                locator, row = next(rows)
            except (ValueError, StopIteration):
                continue
            if HEADER in row:
                yield locator, {k: _text(v) for k, v in row.items() if not k.startswith('_')}
                for locator, row in rows:
                    yield locator, {k: _text(v) for k, v in row.items() if not k.startswith('_')}
                return
        raise ValueError(f'{name}: no "{HEADER}" header row')
    header = None
    for number, cells in xls_rows(shard['path']):
        if header is None:
            if _text(cells.get(0)) == HEADER:
                header = {column: _text(value) for column, value in cells.items() if _text(value)}
            continue
        yield (f'shard:{shard["index"]}/sheet:0/row:{number}',
               {title: _text(cells.get(column)) for column, title in header.items()})
    if header is None:
        raise ValueError(f'{name}: no "{HEADER}" header row')


def _vintage(context, index, shard, name, url, spec, observed):
    vintage, start, end = spec['vintage'], spec['valid_from'], spec.get('valid_to')
    common = {'delineation_vintage': vintage, 'bulletin': spec['bulletin'], 'source_url': url, 'licence': LICENCE}
    seen = set()  # a few thousand CBSA/CSA/division codes per vintage

    def record(kind, rid, locator, **fields):
        out = {'kind': kind, 'id': f'omb{vintage}:{rid}', 'observed_at': observed, 'valid_from': start,
               'evidence': context.raw_evidence(locator, index), **fields}
        if end:
            out['valid_to'] = end
        return out

    def member(crosswalk, source_kind, source, target_kind, target, locator, **extra):
        attrs = {'crosswalk': f'{crosswalk}_{vintage}', 'source_system': source_kind,
                 'target_system': f'{target_kind}_{vintage}', 'source_code': source, 'target_code': target,
                 'weight': 1, 'weight_key': 'membership', 'weight_basis': 'membership (exact)',
                 'relationship': 'delineation_membership', **common, **extra}
        return record('assertion', f'{crosswalk}:{source}:{target}', locator, subject=f'geo:US:{source_kind}:{source}',
                      predicate='within', object=f'geo:US:{target_kind}:{target}', attributes=attrs)

    def entity(kind, code, label, locator, **attrs):
        if (kind, code) in seen:
            return []
        seen.add((kind, code))
        return [record('entity', f'entity:{kind}:{code}', locator, entity_id=f'geo:US:{kind}:{code}', entity_type='location',
                       label=label or f'{kind.upper()} {code}', attributes={'geography_level': kind, **common, **attrs})]

    rows = 0
    for locator, row in _table(shard, name):
        cbsa = row.get(HEADER, '')
        if not cbsa.isdigit():
            continue  # notes and source footers below the table
        state, county_code = row['FIPS State Code'], row['FIPS County Code']
        if not (state.isdigit() and county_code.isdigit()):
            raise ValueError(f'{locator}: invalid county FIPS')
        rows += 1
        cbsa, county = cbsa.zfill(5), state.zfill(2) + county_code.zfill(3)
        area_type = row['Metropolitan/Micropolitan Statistical Area']
        cbsa_type = 'metropolitan' if area_type.lower().startswith('metropolitan') else 'micropolitan'
        status = row['Central/Outlying County'].lower()
        metdiv, csa = row.get('Metropolitan Division Code', ''), row.get('CSA Code', '')
        yield member('omb_county_cbsa', 'county', county, 'cbsa', cbsa, locator, cbsa_type=cbsa_type,
                     central_outlying=status, county_name=row['County/County Equivalent'], state_name=row['State Name'],
                     cbsa_title=row['CBSA Title'])
        yield record('assertion', f'status:{county}', locator, subject=f'geo:US:county:{county}', predicate='cbsa_county_status',
                     value=status, attributes={'cbsa': f'geo:US:cbsa:{cbsa}', **common})
        yield from entity('cbsa', cbsa, row['CBSA Title'], locator, cbsa_type=cbsa_type)
        if ('type', cbsa) not in seen:
            seen.add(('type', cbsa))
            yield record('assertion', f'type:{cbsa}', locator, subject=f'geo:US:cbsa:{cbsa}', predicate='cbsa_type',
                         value=f'{cbsa_type}_statistical_area', attributes=dict(common))
        if metdiv.isdigit():
            metdiv = metdiv.zfill(5)
            yield member('omb_county_metdiv', 'county', county, 'metdiv', metdiv, locator, central_outlying=status)
            yield from entity('metdiv', metdiv, row.get('Metropolitan Division Title'), locator, cbsa=f'geo:US:cbsa:{cbsa}')
            if ('metdiv_cbsa', metdiv) not in seen:
                seen.add(('metdiv_cbsa', metdiv))
                yield member('omb_metdiv_cbsa', 'metdiv', metdiv, 'cbsa', cbsa, locator)
        if csa.isdigit():
            csa = csa.zfill(3)
            yield member('omb_county_csa', 'county', county, 'csa', csa, locator)
            yield from entity('csa', csa, row.get('CSA Title'), locator)
            if ('cbsa_csa', cbsa) not in seen:
                seen.add(('cbsa_csa', cbsa))
                yield member('omb_cbsa_csa', 'cbsa', cbsa, 'csa', csa, locator)
    if rows == 0:
        raise ValueError(f'{name}: no delineation rows')
