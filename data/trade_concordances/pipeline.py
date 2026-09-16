"""Product/industry concordances -> crosswalk assertions with versioned code namespaces.

Namespaces: hts<YYYY>: (US import HTS 10-digit), scheduleb<YYYY>: (US export Schedule B 10-digit),
hs92: hs96: hs02: hs07: hs12: hs17: hs22: (HS 6-digit by revision), naics2022:, sitc3:, sitc4:, isic3:, enduse:.

Crosswalks (attributes.crosswalk):
  census_<hts|scheduleb><YYYY>_<hs rev>       10-digit -> HS6 (hierarchy, weight 1)
  census_<hts|scheduleb><YYYY>_naics<rev>     10-digit -> NAICS (Census assignment, weight 1)
  census_<hts|scheduleb><YYYY>_sitc4 / _enduse
  census_<hs rev>_naics<rev>_<imports|exports><YYYY>  HS6 -> NAICS derived from the 10-digit assignments;
                                              split HS6 codes carry weight null (line shares are attributes, not weights)
  wits_<hs rev>_<sitc3|isic3>                 WITS HS6 conversion tables
  un_<hsA>_<hsB>_conversion / _correlation    UNSD HS correlation and conversion tables
Weights are 1 only where a source code has exactly one target in that table; otherwise null.
"""
import csv
import io
import re
import zipfile
from pathlib import PurePosixPath

CENSUS_LICENCE = 'US Census Bureau; public domain (17 U.S.C. 105)'
WITS_LICENCE = 'World Bank WITS terms of use; derived from UNSD correlation tables; internal use, cite WITS/UNSD'
UN_LICENCE = 'United Nations Statistics Division terms of use; free use with attribution'
WITS_SYSTEMS = {'H0': 'hs92', 'H1': 'hs96', 'H2': 'hs02', 'H3': 'hs07', 'H4': 'hs12', 'H5': 'hs17', 'H6': 'hs22',
                'S1': 'sitc1', 'S2': 'sitc2', 'S3': 'sitc3', 'S4': 'sitc4', 'I2': 'isic2', 'I3': 'isic3', 'I4': 'isic4'}
HS_YEARS = {'1992': 'hs92', '1996': 'hs96', '2002': 'hs02', '2007': 'hs07', '2012': 'hs12', '2017': 'hs17', '2022': 'hs22'}


def run(context):
    if not context.raw_inputs:
        raise ValueError('trade_concordances: no raw artifact; run wm acquire trade_concordances')
    for index, _ in enumerate(context.raw_inputs):
        if context.raw_coverage(index)['sampled']:
            raise ValueError('trade_concordances has no sample adapter; acquire the full files')
        entities = set()  # code entities shared across files (tens of thousands of short strings)
        for shard in context.raw_shards(index):
            url = (shard.get('request') or {}).get('url') or shard.get('url') or ''
            name = PurePosixPath(url.split('?')[0]).name
            emit = _Emitter(context, index, shard, url, entities)
            census = re.fullmatch(r'(imp|exp)concord(\d\d)\.xlsx', name)
            wits = re.fullmatch(r'Concordance_([A-Z]\d)_to_([A-Z]\d)\.zip', name)
            un = re.fullmatch(r'HS(\d{4})toHS(\d{4})ConversionAndCorrelationTables\.xlsx', name)
            if census:
                yield from _census(context, shard, emit, census.group(1), '20' + census.group(2))
            elif wits:
                yield from _wits(shard, emit, WITS_SYSTEMS[wits.group(1)], WITS_SYSTEMS[wits.group(2)])
            elif un:
                yield from _un(shard, emit, HS_YEARS[un.group(1)], HS_YEARS[un.group(2)])
            else:
                raise ValueError('trade_concordances: unrecognized raw file ' + (name or str(shard['path'])))


def _clean(value):
    return ' '.join(str(value or '').split())


def _hs_revision(year):
    return max((y for y in HS_YEARS if int(y) <= int(year)), default=None)


class _Emitter:
    def __init__(self, context, index, shard, url, entities):
        self.context, self.index, self.url = context, index, url
        self.observed = shard.get('retrieved_at') or context.raw_receipt(index)['retrieved_at']
        self.entities = entities

    def evidence(self, locator):
        return self.context.raw_evidence(locator, self.index)

    def entity(self, system, code, label, locator, entity_type='product', **attrs):
        key = f'{system}:{code}'
        if key in self.entities:
            return []
        self.entities.add(key)
        return [{'kind': 'entity', 'id': f'xwe:{system}:{code}', 'entity_id': key, 'entity_type': entity_type,
                 'label': label or key, 'observed_at': self.observed, 'evidence': self.evidence(locator),
                 'attributes': {'code': code, 'classification': system, **attrs}}]

    def crosswalk(self, crosswalk, source_system, source, target_system, target, locator, *, weight, weight_basis,
                  relationship, licence, predicate='maps_to', valid=None, **extra):
        record = {'kind': 'assertion', 'id': f'xw:{crosswalk}:{source}:{target}', 'subject': f'{source_system}:{source}',
                  'predicate': predicate, 'object': f'{target_system}:{target}', 'observed_at': self.observed,
                  'evidence': self.evidence(locator),
                  'attributes': {'crosswalk': crosswalk, 'source_system': source_system, 'target_system': target_system,
                                 'source_code': source, 'target_code': target, 'weight': weight,
                                 'weight_key': 'weight' if weight is not None else None, 'weight_basis': weight_basis,
                                 'relationship': relationship, 'source_url': self.url, 'licence': licence, **extra}}
        if valid:
            record['valid_from'], record['valid_to'] = valid
        return record


def _census(context, shard, emit, flow, year):
    from worldmodel.raw_readers import iter_rows
    revisions = context.parameters['census_naics_revision']
    if year not in revisions:
        raise ValueError(f'trade_concordances: declare parameters.census_naics_revision["{year}"] after checking the file')
    naics = 'naics' + revisions[year]
    naics_codes = set()
    if revisions[year] == '2022':
        from worldmodel.crosswalks import naics_2022_codes
        naics_codes = set(naics_2022_codes())
    system = ('hts' if flow == 'imp' else 'scheduleb') + year
    hs = HS_YEARS[_hs_revision(year)]
    valid = (f'{year}-01-01', f'{int(year) + 1}-01-01')
    common = dict(licence=CENSUS_LICENCE, valid=valid, concordance_year=year)
    by_hs6 = {}  # hs6 -> {naics: [10-digit line count, first locator]}; about 5,600 subheadings
    for locator, row in iter_rows([shard], {'format': 'xlsx'}):
        code = _clean(row.get('commodity'))
        if not (code.isdigit() and len(code) == 10):
            continue
        units = [u for u in (_clean(row.get('unit_qy1')), _clean(row.get('unit_qy2'))) if u]
        yield from emit.entity(system, code, _clean(row.get('descriptn')) or _clean(row.get('abbreviatn')), locator,
                               units=units, usda_product=_clean(row.get('usda')) == '1',
                               hitech_code=_clean(row.get('hitech')) or None, trade_flow=flow + 'orts')
        yield emit.crosswalk(f'census_{system}_{hs}', system, code, hs, code[:6], locator, weight=1,
                             weight_basis='hierarchy (exact)', relationship='hs_subheading', predicate='within', **common)
        industry = _clean(row.get('naics'))
        if industry:
            yield emit.crosswalk(f'census_{system}_{naics}', system, code, naics, industry, locator, weight=1,
                                 weight_basis='census_assignment (exact)', relationship='census_trade_concordance',
                                 predicate='classified_as', trade_aggregate_code=not industry.isdigit(),
                                 outside_naics_code_list=bool(naics_codes) and industry.isdigit() and industry not in naics_codes,
                                 **common)
            item = by_hs6.setdefault(code[:6], {}).setdefault(industry, [0, locator])
            item[0] += 1
        for field, target in (('sitc', 'sitc4'), ('end_use', 'enduse')):
            value = _clean(row.get(field))
            if value:
                yield emit.crosswalk(f'census_{system}_{target}', system, code, target, value, locator, weight=1,
                                     weight_basis='census_assignment (exact)', relationship='census_trade_concordance',
                                     predicate='classified_as', **common)
    for hs6, targets in sorted(by_hs6.items()):
        lines = sum(count for count, _ in targets.values())
        for industry, (count, locator) in sorted(targets.items()):
            yield emit.crosswalk(f'census_{hs}_{naics}_{flow}orts{year}', hs, hs6, naics, industry, locator,
                                 weight=1 if len(targets) == 1 else None,
                                 weight_basis='single_target' if len(targets) == 1 else None,
                                 relationship='derived_from_10digit_assignments', aggregate=True,
                                 tendigit_lines=count, tendigit_line_share=round(count / lines, 12), **common)


def _read_csv_member(shard):
    with zipfile.ZipFile(shard['path']) as archive:
        members = [m for m in archive.namelist() if m.lower().endswith('.csv')]
        if len(members) != 1:
            raise ValueError(f'shard:{shard["index"]}: expected one CSV member, found {members}')
        data = archive.read(members[0])
    try:
        text = data.decode('utf-8-sig')
    except UnicodeDecodeError:
        text = data.decode('cp1252')  # some WITS extracts are Windows-1252
    reader = csv.reader(io.StringIO(text, newline=''))
    header = next(reader)
    rows, last = [], reader.line_num
    for values in reader:
        start, last = last + 1, reader.line_num
        if values:
            rows.append((f'shard:{shard["index"]}/member:{members[0]}/line:{start}', values))
    return header, rows


def _wits(shard, emit, source_system, target_system):
    header, rows = _read_csv_member(shard)
    if len(header) < 4 or 'Code' not in header[0] or 'Code' not in header[2]:
        raise ValueError(f'shard:{shard["index"]}: unexpected WITS header {header}')
    parsed = [(locator, _clean(v[0]), _clean(v[1]), _clean(v[2]), _clean(v[3])) for locator, v in rows]
    counts = {}
    for _, source, _, target, _ in parsed:
        counts.setdefault(source, set()).add(target)
    target_type = 'industry' if target_system.startswith('isic') else 'product'
    crosswalk = f'wits_{source_system}_{target_system}'
    for locator, source, source_label, target, target_label in parsed:
        if not source or not target:
            continue
        yield from emit.entity(source_system, source, source_label, locator)
        yield from emit.entity(target_system, target, target_label, locator, entity_type=target_type)
        single = len(counts[source]) == 1
        yield emit.crosswalk(crosswalk, source_system, source, target_system, target, locator,
                             weight=1 if single else None, weight_basis='single_target' if single else None,
                             relationship='wits_conversion', licence=WITS_LICENCE)


def _un(shard, emit, source_system, target_system):
    from worldmodel.raw_readers import iter_rows
    with zipfile.ZipFile(shard['path']) as archive:
        sheets = sorted((m for m in archive.namelist() if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', m)),
                        key=lambda m: int(re.search(r'\d+', m.rsplit('/', 1)[1]).group()))
    found = set()
    for member in sheets:
        table = None
        for header_row in (1, 2):
            try:
                rows = list(iter_rows([shard], {'format': 'xlsx', 'archive_member': member, 'header_row': header_row}))
            except ValueError:
                continue
            keys = [k for k in (rows[0][1] if rows else {}) if not k.startswith('_')]
            names = [re.sub(r'\s+', '', k).lower() for k in keys]
            if any(n == 'relationship' for n in names) and len(keys) >= 3:
                table = ('correlation', keys[0], keys[1], keys[names.index('relationship')], rows)
            elif len(keys) >= 2 and names[0].startswith('from') and names[1].startswith('to'):
                table = ('conversion', keys[0], keys[1], None, rows)
            if table:
                break
        if table is None:
            continue
        kind, source_key, target_key, relation_key, rows = table
        found.add(kind)
        pairs, targets = [], {}
        for locator, row in rows:
            source, target = _clean(row.get(source_key)), _clean(row.get(target_key))
            if not (source.isdigit() and target.isdigit()):
                continue
            source, target = source.zfill(6), target.zfill(6)
            relation = _clean(row.get(relation_key)) if relation_key else 'conversion'
            pairs.append((locator, source, target, relation))
            targets.setdefault(source, set()).add(target)
        crosswalk = f'un_{source_system}_{target_system}_{kind}'
        emitted = set()
        for locator, source, target, relation in pairs:
            if (source, target) in emitted:
                continue
            emitted.add((source, target))
            single = len(targets[source]) == 1
            yield emit.crosswalk(crosswalk, source_system, source, target_system, target, locator,
                                 weight=1 if single else None, weight_basis='single_target' if single else None,
                                 relationship=relation, licence=UN_LICENCE, unsd_table=kind,
                                 placeholder_code=source == '999999' or target == '999999')
    if found != {'conversion', 'correlation'}:
        raise ValueError(f'shard:{shard["index"]}: expected UNSD conversion and correlation sheets, found {sorted(found)}')
