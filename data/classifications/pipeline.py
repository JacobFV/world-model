'NAICS, NAPCS, SIC and industry/commodity crosswalks'
import json
import re
import zipfile
from worldmodel.util import digest
from worldmodel.source_helpers import STATE_FIPS

SECTORS = {'31': '31-33', '32': '31-33', '33': '31-33', '44': '44-45', '45': '44-45', '48': '48-49', '49': '48-49'}
EFFECTIVE = {'2012': ('2012-01-01', '2017-01-01'), '2017': ('2017-01-01', '2022-01-01'), '2022': ('2022-01-01', None)}


def run(context):
    """Sample artifacts keep the legacy adapter; full code lists and concordances use `_run_full`.

    Full-mode namespaces: naics2012:/naics2017:/naics2022: (industry), scheduleb2022:/scheduleb2025: (export
    commodity, HS-based 10-digit), hts2022: (import HTS 10-digit), sitc4: (SITC Rev. 4, 5-digit), enduse: (Census
    end-use 5-digit), soc2018: (occupation). The sample adapter keeps its legacy `naics:2022:` IDs.
    """
    if not context.raw_inputs:
        raise ValueError('classifications: no raw artifact supplied')
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


def _clean(text):
    return re.sub(r'\s+', ' ', str(text or '')).strip()


def _naics_parent(code):
    if len(code) == 2 or '-' in code:
        return None
    if len(code) == 3:
        return SECTORS.get(code[:2], code[:2])
    return code[:-1]


def _xlsx(context, shard, header_row):
    from worldmodel.raw_readers import iter_rows
    return iter_rows([shard], {'format': 'xlsx', 'header_row': header_row})


def _sheet_cells(path):
    """Yield (row number, {column letter: text}) for sheet1 of an XLSX file (used where the header row has blank titles)."""
    from xml.etree import ElementTree as ET
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as archive:
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(t.text or '' for t in item.findall('.//s:t', ns))
                       for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('s:si', ns)]
        for row in ET.fromstring(archive.read('xl/worksheets/sheet1.xml')).findall('.//s:row', ns):
            values = {}
            for cell in row.findall('s:c', ns):
                node = cell.find('s:v', ns)
                text = node.text if node is not None else ''.join(t.text or '' for t in cell.findall('.//s:t', ns))
                if cell.get('t') == 's' and text:
                    text = strings[int(text)]
                values[re.sub(r'\d+', '', cell.get('r', ''))] = text
            yield int(row.get('r')), values


def _file_kind(shard, receipt):
    name = str((shard.get('request') or {}).get('url') or receipt.get('original_name') or '').lower()
    for key, kind in (('2-6%20digit_2022', 'naics2022'), ('2-6 digit_2022', 'naics2022'), ('2-6_digit_2022', 'naics2022'),
                      ('2-6%20digit_2017', 'naics2017'), ('2-6 digit_2017', 'naics2017'), ('2-6_digit_2017', 'naics2017'),
                      ('2012_to_2017', 'concord_2012_2017'), ('2017_to_2022', 'concord_2017_2022'),
                      ('expconcord', 'export_concordance'), ('impconcord', 'import_concordance'), ('exp-code', 'schedule_b'),
                      ('soc_structure', 'soc2018')):
        if key in name:
            return kind, name
    return None, name


def _run_full(context):
    for index, _ in enumerate(context.raw_inputs):
        receipt = context.raw_receipt(index)
        observed = receipt['retrieved_at']
        emitted = set()  # classification codes: tens of thousands of short strings

        def entity(key, typ, label, locator, **attrs):
            if key in emitted:
                return []
            emitted.add(key)
            return [{'kind': 'entity', 'id': 'class:entity:' + key, 'entity_id': key, 'entity_type': typ,
                     'label': label or key, 'observed_at': observed, 'evidence': context.raw_evidence(locator, index),
                     'attributes': attrs}]

        def assertion(rid, subject, predicate, obj, locator, **attrs):
            return {'kind': 'assertion', 'id': rid, 'subject': subject, 'predicate': predicate, 'object': obj,
                    'observed_at': observed, 'evidence': context.raw_evidence(locator, index), 'attributes': attrs}

        for shard in context.raw_shards(index):
            kind, name = _file_kind(shard, receipt)
            if kind is None:
                raise ValueError('classifications: unrecognized raw file ' + name)
            if kind in ('naics2022', 'naics2017'):
                revision = kind[-4:]
                pending = []
                for locator, row in _xlsx(context, shard, 1):
                    code = _clean(row.get(f'{revision} NAICS US   Code') or row.get(f'{revision} NAICS US Code'))
                    title = _clean(row.get(f'{revision} NAICS US Title'))
                    if not code:
                        continue
                    key = f'naics{revision}:{code}'
                    start, end = EFFECTIVE[revision]
                    attrs = {'code': code, 'revision': revision, 'level': 2 if '-' in code else len(code), 'valid_from': start}
                    if end:
                        attrs['superseded_from'] = end
                    yield from entity(key, 'industry', title, locator, **attrs)
                    parent = _naics_parent(code)
                    if parent:
                        pending.append((key, f'naics{revision}:{parent}', locator))
                for key, parent, locator in pending:
                    yield assertion(f'class:within:{key}', key, 'within', parent, locator, relationship='naics_hierarchy')
            elif kind.startswith('concord_'):
                source_rev, target_rev = kind.split('_')[1:]
                for locator, row in _xlsx(context, shard, 3):
                    source = _clean(row.get(f'{source_rev} NAICS Code'))
                    target = _clean(row.get(f'{target_rev} NAICS Code'))
                    if not (source.isdigit() and target.isdigit()):
                        continue
                    source_title = next((_clean(v) for k, v in row.items() if k.startswith(f'{source_rev} NAICS Title')), '')
                    target_title = _clean(row.get(f'{target_rev} NAICS Title'))
                    yield from entity(f'naics{source_rev}:{source}', 'industry', source_title, locator, code=source,
                                      revision=source_rev, level=len(source))
                    yield from entity(f'naics{target_rev}:{target}', 'industry', target_title, locator, code=target,
                                      revision=target_rev, level=len(target))
                    yield assertion(f'class:concord:{source_rev}-{target_rev}:{source}:{target}', f'naics{source_rev}:{source}',
                                    'maps_to', f'naics{target_rev}:{target}', locator, relationship='official_concordance',
                                    identical_code=source == target, source_piece_title=source_title)
            elif kind in ('export_concordance', 'import_concordance'):
                year = re.search(r'concord(\d\d)', name).group(1)
                system = ('scheduleb' if kind == 'export_concordance' else 'hts') + '20' + year
                yield from _trade_rows(_xlsx(context, shard, 1), system, 'naics2017', entity, assertion)
            elif kind == 'schedule_b':
                year = re.search(r'/b/(\d{4})/', name)
                system = 'scheduleb' + (year.group(1) if year else '2025')
                rows = _schedule_b_rows(shard)
                yield from _trade_rows(rows, system, 'naics2022', entity, assertion)
            else:
                yield from _soc(shard, entity, assertion)


def _schedule_b_rows(shard):
    with open(shard['path'], encoding='latin-1') as stream:
        for number, line in enumerate(stream, 1):
            if len(line) < 271 or not line[:10].strip().isdigit():
                continue
            yield (f'shard:{shard["index"]}/line:{number}',
                   {'commodity': line[0:10], 'abbreviatn': line[14:69], 'descriptn': line[69:224], 'unit_qy1': line[224:232],
                    'unit_qy2': line[232:240], 'sitc': line[240:245], 'end_use': line[250:255], 'naics': line[265:271],
                    'hitech': line[276:278]})


def _trade_rows(rows, system, naics_revision, entity, assertion):
    for locator, row in rows:
        code = _clean(row.get('commodity'))
        if not code.isdigit():
            continue
        key = f'{system}:{code}'
        label = _clean(row.get('descriptn')) or _clean(row.get('abbreviatn'))
        units = [u for u in (_clean(row.get('unit_qy1')), _clean(row.get('unit_qy2'))) if u]
        yield from entity(key, 'product', label, locator, code=code, hs6=code[:6], units=units, hitech=_clean(row.get('hitech')) or None)
        hs6 = 'hs:' + code[:6]
        yield from entity(hs6, 'product', 'HS subheading ' + code[:6], locator, code=code[:6], level=6,
                          note='Harmonized System 6-digit subheading (revision in force for the concordance year)')
        yield assertion(f'class:within:{key}', key, 'within', hs6, locator, relationship='hs_hierarchy')
        naics = _clean(row.get('naics'))
        if naics:
            target = f'{naics_revision}:{naics}'
            yield from entity(target, 'industry', f'NAICS {naics_revision[-4:]} {naics}', locator, code=naics,
                              revision=naics_revision[-4:], trade_aggregate_code=not naics.isdigit() or naics.endswith('0000'))
            yield assertion(f'class:classified:{key}:naics', key, 'classified_as', target, locator, relationship='census_trade_concordance')
        sitc = _clean(row.get('sitc'))
        if sitc:
            yield from entity('sitc4:' + sitc, 'product', 'SITC Rev. 4 ' + sitc, locator, code=sitc)
            yield assertion(f'class:classified:{key}:sitc', key, 'classified_as', 'sitc4:' + sitc, locator, relationship='census_trade_concordance')
        end_use = _clean(row.get('end_use'))
        if end_use:
            yield from entity('enduse:' + end_use, 'product', 'Census end-use ' + end_use, locator, code=end_use)
            yield assertion(f'class:classified:{key}:enduse', key, 'classified_as', 'enduse:' + end_use, locator, relationship='census_trade_concordance')


def _soc(shard, entity, assertion):
    levels = {'A': 'major', 'B': 'minor', 'C': 'broad', 'D': 'detailed'}
    for number, values in _sheet_cells(shard['path']):
        column = next((c for c in 'ABCD' if re.fullmatch(r'\d\d-\d{4}', (values.get(c) or '').strip())), None)
        if column is None:
            continue
        code, title = values[column].strip(), _clean(values.get('E'))
        locator = f'shard:{shard["index"]}/member:xl/worksheets/sheet1.xml/row:{number}'
        key = 'soc2018:' + code
        yield from entity(key, 'occupation', title, locator, code=code, level=levels[column])
        parent = {'B': code[:3] + '0000', 'C': code[:5] + '00', 'D': code[:6] + '0'}.get(column)
        if parent and parent != code:
            yield assertion(f'class:within:{key}', key, 'within', 'soc2018:' + parent, locator, relationship='soc_hierarchy')


def _run_sample(context):
    dataset = 'classifications'
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
            code = str(row['2022 NAICS Code']).strip()
            entity('naics:2022:' + code, 'industry', row['2022 NAICS Title'].strip(), classification_revision='2022')
            yield from out
