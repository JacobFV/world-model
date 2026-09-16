"""Streaming readers over full raw artifacts (single payload or shards).

``iter_rows(shards, config)`` yields ``(locator, row)`` pairs. Locators name the
exact physical position, e.g. ``shard:3/member:foo.csv/line:1234``,
``shard:0/line:17``, ``shard:2/record:5`` or ``shard:0/member:xl/worksheets/sheet1.xml/row:12``.
Memory stays bounded for CSV/TSV/pipe and JSONL (one row at a time), including
inside ZIP members and gzip files. JSON documents and XLSX worksheets are
parsed in memory under explicit byte caps.
"""
import csv
import fnmatch
import gzip
import io
import json
import zipfile

MIB = 1024 * 1024
FORMATS = ('csv', 'tsv', 'psv', 'jsonl', 'json', 'xlsx')
_DELIMITERS = {'csv': ',', 'tsv': '\t', 'psv': '|'}
_MEMBERS = {'csv': ['*.csv', '*.txt'], 'tsv': ['*.tsv', '*.tab', '*.txt'], 'psv': ['*.txt', '*.psv', '*.dat'],
            'jsonl': ['*.jsonl', '*.ndjson', '*.json'], 'json': ['*.json']}
READER_KEYS = {'format', 'delimiter', 'encoding', 'compression', 'members', 'records_path', 'table_header',
               'unwrap_attributes', 'max_json_bytes', 'max_field_bytes', 'strict', 'fieldnames', 'quoting',
               'roles', 'skip_lines', 'archive_member', 'header_row', 'columns', 'context_cells', 'start_row',
               'end_row', 'stop_when_blank', 'stop_when', 'max_uncompressed_bytes'}


def validate_reader(config):
    if not isinstance(config, dict) or set(config) - READER_KEYS:
        raise ValueError('Unknown raw reader keys: ' + ', '.join(sorted(set(config) - READER_KEYS)))
    if config.get('format') not in FORMATS:
        raise ValueError('Raw reader format must be one of ' + ', '.join(FORMATS))
    if config.get('compression', 'auto') not in ('auto', 'none', 'gzip', 'zip'):
        raise ValueError('Raw reader compression must be auto, none, gzip or zip')
    return config


def _get(payload, path, what='records_path'):
    try:
        for key in path or []:
            payload = payload[key]
    except (KeyError, IndexError, TypeError):
        raise ValueError(f'Configured {what} is missing in JSON payload') from None
    return payload


def _members(archive, config):
    names = [info.filename for info in archive.infolist()
             if not info.is_dir() and not info.filename.startswith('__MACOSX/')]
    if len(names) != len(set(names)):
        raise ValueError('Duplicate ZIP archive members')
    patterns = config.get('members') or _MEMBERS[config['format']]
    if isinstance(patterns, str):
        patterns = [patterns]
    selected = [name for name in names
                if any(fnmatch.fnmatch(name.lower(), p.lower()) or fnmatch.fnmatch(name.rsplit('/', 1)[-1].lower(), p.lower())
                       for p in patterns)]
    if not selected:
        raise ValueError(f'No ZIP members match {patterns}')
    return selected


def _delimited(stream, config, prefix):
    csv.field_size_limit(config.get('max_field_bytes', 16 * MIB))
    text = io.TextIOWrapper(stream, encoding=config.get('encoding', 'utf-8-sig'), newline='')
    try:
        yield from _delimited_text(text, config, prefix)
    finally:
        text.detach()  # The caller owns and closes the underlying binary stream.


def _delimited_text(text, config, prefix):
    for _ in range(config.get('skip_lines', 0)):
        text.readline()
    offset = config.get('skip_lines', 0)
    delimiter = config.get('delimiter', _DELIMITERS.get(config['format'], ','))
    quoting = csv.QUOTE_NONE if config.get('quoting') == 'none' else csv.QUOTE_MINIMAL
    reader = csv.reader(text, delimiter=delimiter, quoting=quoting, strict=True)
    header = config.get('fieldnames')
    last = 0
    if header is None:
        header = next(reader, None)
        last = reader.line_num
        if header is None:
            return
    if not header or len(set(header)) != len(header):
        raise ValueError(f'{prefix}: missing or duplicate column headers')
    strict = config.get('strict', True)
    for values in reader:
        start, last = last + 1, reader.line_num
        if not values or values == ['']:
            continue
        locator = f'{prefix}/line:{start + offset}'
        if len(values) != len(header):
            if strict:
                raise ValueError(f'{locator}: row has {len(values)} fields, header has {len(header)}')
            values = (values + [None] * len(header))[:len(header)]
        yield locator, dict(zip(header, values))


def _json_records(payload, config, prefix):
    if isinstance(payload, dict):
        for key in ('error', 'errors', 'errorMessage'):
            if payload.get(key) and not config.get('records_path'):
                raise ValueError(f'{prefix}: source returned an error payload')
    records = _get(payload, config.get('records_path'))
    if config.get('table_header'):
        if not isinstance(records, list) or not records or not isinstance(records[0], list):
            raise ValueError(f'{prefix}: table_header requires an array whose first row is the header')
        header = records[0]
        if len(set(header)) != len(header):
            raise ValueError(f'{prefix}: duplicate table headers')
        for number, values in enumerate(records[1:], 1):
            if not isinstance(values, list) or len(values) != len(header):
                raise ValueError(f'{prefix}/record:{number}: row does not match header')
            yield f'{prefix}/record:{number}', dict(zip(header, values))
        return
    if isinstance(records, dict):
        records = [records]
    if not isinstance(records, list):
        raise ValueError(f'{prefix}: records_path must select an array')
    for number, row in enumerate(records):
        if not isinstance(row, dict):
            raise ValueError(f'{prefix}/record:{number}: expected JSON object')
        yield f'{prefix}/record:{number}', row.get('attributes', row) if config.get('unwrap_attributes') else row


def _parse(stream, config, prefix):
    format_name = config['format']
    if format_name in _DELIMITERS:
        yield from _delimited(stream, config, prefix)
    elif format_name == 'jsonl':
        for number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            value = json.loads(line)
            if config.get('records_path') is not None or config.get('table_header'):
                yield from _json_records(value, config, f'{prefix}/line:{number}')
            elif isinstance(value, dict):
                yield f'{prefix}/line:{number}', value
            else:
                raise ValueError(f'{prefix}/line:{number}: expected JSON object')
    elif format_name == 'json':
        limit = config.get('max_json_bytes', 256 * MIB)
        content = stream.read(limit + 1)
        if len(content) > limit:
            raise ValueError(f'{prefix}: JSON document exceeds max_json_bytes={limit}; use paged JSONL shards')
        yield from _json_records(json.loads(content), config, prefix)
    else:
        raise ValueError(f'Unsupported streaming format: {format_name}')


def _kind(path, compression):
    if compression != 'auto':
        return compression
    with open(path, 'rb') as stream:
        magic = stream.read(4)
    if magic[:2] == b'\x1f\x8b':
        return 'gzip'
    if magic == b'PK\x03\x04':
        return 'zip'
    return 'none'


def iter_rows(shards, config):
    """Yield ``(locator, row)`` for every data row across ``shards`` in order.

    ``shards`` are dicts with ``index`` and ``path`` (as from ``Store.raw_shards``).
    """
    config = validate_reader(dict(config))
    roles = config.get('roles', ['data'])
    for shard in shards:
        if shard.get('role', 'data') not in roles:
            continue
        prefix = f'shard:{shard["index"]}'
        path = shard['path']
        kind = 'zip' if config['format'] == 'xlsx' else _kind(path, config.get('compression', 'auto'))
        if kind == 'zip':
            with zipfile.ZipFile(path) as archive:
                if config['format'] == 'xlsx':
                    from .workbooks import xlsx_rows
                    options = {'max_uncompressed_bytes': 512 * MIB, **config}
                    for row in xlsx_rows(archive, options):
                        source = row['_source']
                        yield f'{prefix}/member:{source["member"]}/row:{source["row"]}', row
                    continue
                for member in _members(archive, config):
                    with archive.open(member) as stream:
                        yield from _parse(stream, config, f'{prefix}/member:{member}')
        elif kind == 'gzip':
            with gzip.open(path, 'rb') as stream:
                yield from _parse(stream, config, prefix)
        else:
            with open(path, 'rb') as stream:
                yield from _parse(stream, config, prefix)
