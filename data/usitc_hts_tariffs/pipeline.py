"""USITC Harmonized Tariff Schedule: current HTS JSON export and manually imported annual tariff databases.

Inputs (auto-detected per shard by content, stdlib only):
- HTS REST export JSON array (``htsno``, ``general``, ``special``, ``other`` ...), acquired by script;
- HTS release list JSON (``releaseStartDate``), acquired by script, supplies the valid-from date;
- HTS CSV export (``HTS Number``, ``General Rate of Duty`` ...), manual import;
- Annual tariff database (``tariff_database_YYYY.zip`` containing a delimited ``.txt``/``.csv`` with
  ``hts8``, ``mfn_ad_val_rate`` ...), manual import via ``wm import`` (the ZIP is bot-protected).

Ad valorem rates are emitted as unit ``fraction`` (0.068 = 6.8%). Specific rates are converted from
cents to ``USD/<quantity unit>``. Unparseable legal text is kept as ``rate_text`` with a null value
and ``missing_reason``, never guessed.
"""
import csv
import io
import json
import re
import zipfile
from datetime import date, timedelta

from .evidence import Evidence, num

PREFIX = 'usitc'
_PCT = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)\s*%')
_UNIT = r'(?:/\s*([A-Za-z][A-Za-z0-9 .]*?)|\s+(each))(?=\s*(?:\+|$|,|\(|\bon\b|\bof\b|\bfor\b|\bplus\b))'
_CENTS = re.compile(r'(?<![\d.])(\d+(?:\.\d+)?)\s*¢\s*' + _UNIT)
_DOLLARS = re.compile(r'\$\s*(\d+(?:\.\d+)?)\s*' + _UNIT)


def _code(value):
    digits = re.sub(r'\D', '', str(value or ''))
    return digits if len(digits) in (2, 4, 6, 8, 10) else None


def parse_rate(text):
    """Parse a column rate text into {ad_valorem (fraction), specific: [(usd, unit)], free, parsed}."""
    text = (text or '').strip()
    result = {'ad_valorem': None, 'specific': [], 'free': False, 'parsed': False, 'text': text}
    if not text:
        return result
    head = text.split('(')[0].strip()
    if re.fullmatch(r'free', head, re.I):
        result.update(ad_valorem=0.0, free=True, parsed=True)
        return result
    if re.search(r'applicable subheading|no change|see |provided in', head, re.I):
        return result
    percents = _PCT.findall(head)
    cents = _CENTS.findall(head)
    dollars = _DOLLARS.findall(head)
    if len(percents) > 1:
        return result
    if percents:
        result['ad_valorem'] = round(float(percents[0]) / 100.0, 10)
    for value, unit, each in cents:
        result['specific'].append((round(float(value) / 100.0, 10), (unit or each).strip()))
    for value, unit, each in dollars:
        result['specific'].append((float(value), (unit or each).strip()))
    result['parsed'] = bool(percents or cents or dollars)
    return result


def additional_rate(text):
    """Chapter 99 '... + 25%' surcharges: the additional ad valorem fraction, else None."""
    match = re.search(r'\+\s*(\d+(?:\.\d+)?)\s*%', text or '')
    return round(float(match[1]) / 100.0, 10) if match else None


def programs(text):
    match = re.search(r'\(([^)]*)\)', text or '')
    return [p.strip() for p in match[1].split(',') if p.strip()] if match else []


def _us_date(value):
    value = (value or '').strip()
    for pattern in ('%m/%d/%Y', '%Y-%m-%d', '%m/%d/%y'):
        try:
            from datetime import datetime
            return datetime.strptime(value, pattern).date()
        except ValueError:
            continue
    return None


def _sniff(path):
    with open(path, 'rb') as stream:
        head = stream.read(4096)
    if head[:4] == b'PK\x03\x04':
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if 'xl/workbook.xml' in names:
                raise ValueError('XLSX tariff files are not supported; import the delimited .txt/.csv file or ZIP instead')
            members = [n for n in names if n.lower().endswith(('.txt', '.csv')) and not n.startswith('__MACOSX/')]
            members = [n for n in members if 'tariff' in n.lower()] or members
            if not members:
                raise ValueError('ZIP contains no delimited tariff table (.txt/.csv)')
            with archive.open(members[0]) as member:
                first = member.readline().decode('utf-8-sig', 'replace')
            return 'delimited', {'members': [members[0]], 'delimiter': _delimiter(first)}
    text = head.decode('utf-8-sig', 'replace').lstrip()
    if text.startswith('['):
        return ('releases' if '"releaseStartDate"' in text or '"releaseStartDate' in text else 'hts_json'), {}
    if text.startswith('{'):
        return 'hts_json', {}
    first = text.splitlines()[0] if text else ''
    return 'delimited', {'delimiter': _delimiter(first)}


def _delimiter(line):
    return max(('|', ',', '\t'), key=line.count)


def run(context):
    out = Evidence(context, PREFIX)
    shards = list(context.raw_shards())
    kinds = [(shard, *_sniff(shard['path'])) for shard in shards]
    release = {}
    for shard, kind, _ in kinds:
        if kind == 'releases':
            for locator, row in _rows_for(context, shard, {'format': 'json', 'compression': 'none'}):
                if row.get('status') == 'current':
                    start = _us_date(row.get('releaseStartDate'))
                    release = {'name': row.get('name'), 'title': row.get('title'),
                               'valid_from': start.isoformat() if start else None}
                    yield out.claim('usitc:hts:release:' + str(row.get('name')), 'hts_release_metadata',
                                    {k: row.get(k) for k in ('name', 'description', 'title', 'date', 'status',
                                                             'releaseStartDate', 'releaseEndDate')},
                                    locator, identity=row.get('name'))
                    entity = out.entity('usitc:hts:release:' + str(row.get('name')), 'law',
                                        row.get('description') or row.get('name'), locator, kind='hts_release')
                    if entity:
                        yield entity
    for shard, kind, options in kinds:
        if kind == 'hts_json':
            yield from _hts_rows(context, out, shard, release, 'json', {})
        elif kind == 'delimited':
            header = _header(shard['path'], options)
            if 'hts8' in header:
                yield from _database_rows(context, out, shard, options)
            elif 'HTS Number' in header:
                yield from _hts_rows(context, out, shard, release, 'csv', options)
            else:
                raise ValueError('Unrecognised USITC table header: ' + ', '.join(header[:10]))
    out.close()


def _header(path, options):
    if options.get('members'):
        with zipfile.ZipFile(path) as archive, archive.open(options['members'][0]) as member:
            line = member.readline().decode('utf-8-sig', 'replace')
    else:
        with open(path, encoding='utf-8-sig', errors='replace') as stream:
            line = stream.readline()
    return next(csv.reader(io.StringIO(line), delimiter=options['delimiter']))


def _rows_for(context, shard, reader):
    """Stream one shard with its own reader options (shards may have different formats)."""
    from worldmodel.raw_readers import iter_rows
    yield from iter_rows([shard], reader)


CSV_MAP = {'HTS Number': 'htsno', 'Indent': 'indent', 'Description': 'description', 'Unit of Quantity': 'units',
           'General Rate of Duty': 'general', 'Special Rate of Duty': 'special', 'Column 2 Rate of Duty': 'other',
           'Quota Quantity': 'quotaQuantity', 'Additional Duties': 'additionalDuties'}


def _hts_rows(context, out, shard, release, fmt, options):
    reader = {'format': 'json', 'compression': 'none'} if fmt == 'json' else \
        {'format': 'csv', 'delimiter': options['delimiter'], 'compression': 'none', 'strict': False}
    valid_from = release.get('valid_from')
    parents = []  # (indent, code) stack; bounded by the HTS hierarchy depth.
    for locator, source in _rows_for(context, shard, reader):
        row = {CSV_MAP.get(k, k): v for k, v in source.items()} if fmt == 'csv' else source
        try:
            indent = int(row.get('indent') or 0)
        except ValueError:
            indent = 0
        while parents and parents[-1][0] >= indent:
            parents.pop()
        code = _code(row.get('htsno'))
        if not code:
            continue  # Text-only heading rows ("Horses:") carry no code or rate.
        parent = parents[-1][1] if parents else None
        parents.append((indent, code))
        units = row.get('units')
        if isinstance(units, str):
            try:
                units = json.loads(units) if units.startswith('[') else [u for u in [units] if u]
            except ValueError:
                units = [units]
        chapter99 = code.startswith('99')
        key = 'hts:' + code
        entity = out.entity(key, 'regulation' if chapter99 else 'product', (row.get('description') or code).strip(),
                            locator, hts_code=row.get('htsno'), digits=len(code), indent=indent, parent_hts=parent and 'hts:' + parent,
                            chapter=code[:2], quantity_units=units or [],
                            provision_type='chapter99_temporary_or_additional_duty' if chapter99 else 'tariff_line',
                            release=release.get('name'))
        if entity:
            yield entity
        dims = {'frequency': 'release_snapshot', 'schedule': 'HTSUS', 'release': release.get('name')}
        common = dict(valid_from=valid_from, dimensions=dims)
        for column, column_name in (('general', 'general'), ('other', 'column2'), ('special', 'special')):
            text = (row.get(column) or '').strip()
            if not text:
                continue
            rate = parse_rate(text)
            extra = {'rate_text': text[:300]}
            if column == 'special':
                extra['special_programs'] = programs(text)
            if chapter99:
                # Chapter 99 texts are mostly legal cross-references ("The duty provided in the applicable
                # subheading"); only explicit "+ N%" surcharges become observations.
                add = additional_rate(text)
                if add is not None:
                    yield out.observation(key, f'{column_name}_additional_ad_valorem_rate', add, 'fraction', locator,
                                          **common, **extra)
                continue
            if rate['ad_valorem'] is not None or not rate['specific']:
                yield out.observation(key, f'{column_name}_ad_valorem_rate', rate['ad_valorem'], 'fraction', locator,
                                      missing_reason=None if rate['ad_valorem'] is not None else 'rate_text_not_numeric',
                                      **common, **extra)
            for position, (value, unit) in enumerate(rate['specific']):
                yield out.observation(key, f'{column_name}_specific_rate', value, 'USD/' + unit, locator,
                                      identity=position, **common, **extra)
        additional = (row.get('additionalDuties') or '').strip()
        if additional:
            yield out.claim(key, 'additional_duties_text', additional[:500], locator)


DB_RATES = [('mfn_ad_val_rate', 'mfn_ad_valorem_rate', 'fraction'), ('mfn_specific_rate', 'mfn_specific_rate', 'USD/q1'),
            ('mfn_other_rate', 'mfn_other_specific_rate', 'USD/q2'), ('mfn_ave', 'mfn_ad_valorem_equivalent', 'fraction'),
            ('col2_ad_val_rate', 'column2_ad_valorem_rate', 'fraction'), ('col2_specific_rate', 'column2_specific_rate', 'USD/q1'),
            ('col2_other_rate', 'column2_other_specific_rate', 'USD/q2')]
PROGRAM_FIELDS = re.compile(r'.*_(indicator|ind)$')


def _database_rows(context, out, shard, options):
    reader = {'format': 'csv', 'delimiter': options['delimiter'], 'strict': False, 'encoding': 'latin-1'}
    if options.get('members'):
        reader['members'] = options['members']
    for locator, row in _rows_for(context, shard, reader):
        code = _code(row.get('hts8'))
        if not code:
            continue
        begin, end = _us_date(row.get('begin_effect_date')), _us_date(row.get('end_effective_date'))
        valid_from = begin.isoformat() if begin else None
        valid_to = (end + timedelta(days=1)).isoformat() if end and (not begin or end >= begin) else None
        key = 'hts:' + code
        quantity = {'q1': (row.get('quantity_1_code') or '').strip() or 'unit', 'q2': (row.get('quantity_2_code') or '').strip() or 'unit'}
        entity = out.entity(key, 'regulation' if code.startswith('99') else 'product',
                            (row.get('brief_description') or code).strip(), locator, hts_code=code, digits=len(code),
                            chapter=code[:2], quantity_1_code=quantity['q1'], quantity_2_code=quantity['q2'])
        if entity:
            yield entity
        indicators = {k: v.strip() for k, v in row.items() if k and PROGRAM_FIELDS.match(k) and v and v.strip()}
        dims = {'frequency': 'annual_database_effective_period', 'schedule': 'HTSUS',
                'effective_from': valid_from}
        for field, metric, unit in DB_RATES:
            if field not in row:
                continue
            value = num(row.get(field))
            if value is None:
                continue
            if unit.endswith(('/q1', '/q2')):
                unit = 'USD/' + quantity[unit[-2:]]
            attrs = {'rate_type_code': (row.get(field.split('_')[0] + '_rate_type_code') or '').strip() or None}
            if metric == 'mfn_ad_valorem_rate':
                attrs.update(mfn_text_rate=(row.get('mfn_text_rate') or '')[:200], special_text=(row.get('col1_special_text') or '')[:300],
                             program_indicators=indicators)
            yield out.observation(key, metric, value, unit, locator, valid_from=valid_from, valid_to=valid_to,
                                  dimensions=dims, **attrs)
