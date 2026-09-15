"""Bounded exploratory samples. Full downloads are temporary, samples are explicit artifacts."""
import csv
import fcntl
import re
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import zipfile
from xml.etree import ElementTree as ET

from .fetch import public_url
from .provenance import capture_code
from .util import atomic_json, canonical, digest, file_hash, now, read_json

PROJECT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024


def limits(config):
    for name in ('max_rows', 'max_sample_bytes', 'max_download_bytes', 'max_uncompressed_bytes'):
        if type(config.get(name)) is not int or config[name] <= 0:
            raise ValueError(f'Sampling requires a positive integer {name}')
    if config['max_download_bytes'] + 2 * config['max_sample_bytes'] > config.get('disk_budget_bytes', 64 * MIB):
        raise ValueError('Download plus two sample buffers exceeds temporary disk budget')


def download(config, destination):
    """No pagination or retries: one response within an explicit disk/byte budget."""
    url = config['url']
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Sample URL must use HTTP(S)')
    key_env = config.get('api_key_env')
    if key_env:
        key = os.environ.get(key_env)
        if not key:
            raise ValueError(f'Missing {key_env}; source requires an API key')
        parts = urlsplit(url)
        query = parse_qsl(parts.query, keep_blank_values=True)
        query.append((config.get('api_key_parameter', 'api_key'), key))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))
    safe_url = public_url(url)
    headers = {'User-Agent': 'worldmodel-substrate/0.1 sample-exploration', 'Accept-Encoding': 'identity'}
    for header, variable in config.get('headers_env', {}).items():
        if not isinstance(header, str) or not isinstance(variable, str):
            raise ValueError('headers_env requires header-to-environment-variable names')
        value = os.environ.get(variable)
        if not value:
            raise ValueError(f'Missing {variable}; source requires the {header} request header')
        if '\r' in value or '\n' in value:
            raise ValueError(f'Invalid multiline header from {variable}')
        headers[header] = value
    body = canonical(config['body']) if 'body' in config else None
    if body:
        headers['Content-Type'] = 'application/json'
    request = Request(url, data=body, method=config.get('method', 'GET'), headers=headers)
    started = time.monotonic()
    try:
        with urlopen(request, timeout=config.get('timeout_seconds', 30)) as response:
            length = response.headers.get('Content-Length')
            length = int(length) if length is not None else None
            budget = config['max_download_bytes']
            if length is not None and length > budget:
                raise ValueError(f'File is {length:,} bytes; download budget is {budget:,} bytes')
            if response.headers.get('Content-Encoding', 'identity') != 'identity':
                raise ValueError('Unexpected HTTP compression; cannot enforce decoded budget')
            count = 0
            checksum = hashlib.sha256()
            with destination.open('wb') as stream:
                while count < budget:
                    if time.monotonic() - started > config.get('total_timeout_seconds', 120):
                        raise ValueError('Download exceeded total time budget')
                    block = response.read(min(MIB, budget - count))
                    if not block:
                        break
                    stream.write(block)
                    checksum.update(block)
                    count += len(block)
            if length is not None and count != length:
                raise ValueError('Incomplete response: Content-Length mismatch')
            if count == budget and length is None:
                raise ValueError('Unknown-length response reached download budget; completeness cannot be verified')
            return {'url': safe_url, 'resolved_url': public_url(response.url),
                    'method': request.method, 'request_body': config.get('body'),
                    'original_bytes': count, 'original_sha256': checksum.hexdigest(),
                    'original_complete': True, 'content_type': response.headers.get('Content-Type'),
                    'etag': response.headers.get('ETag'), 'last_modified': response.headers.get('Last-Modified')}
    except HTTPError as error:
        status = error.code
        error.close()
        raise ValueError(f'HTTP {status} from {safe_url}') from None
    except (URLError, TimeoutError, ConnectionError):
        raise ValueError(f'Network/timeout failure from {safe_url}') from None


def bounded_lines(stream, budget, encoding='utf-8-sig'):
    consumed = 0
    while consumed < budget:
        remaining = budget - consumed
        line = stream.readline(remaining)
        if len(line) == remaining and not line.endswith(b'\n'):
            raise ValueError('Uncompressed budget ends inside a physical line; refusing truncated record')
        if not line:
            return
        consumed += len(line)
        yield line.decode(encoding)
    raise ValueError('Uncompressed parsing budget exhausted before requested rows completed')


def csv_rows(stream, config):
    lines = bounded_lines(stream, config['max_uncompressed_bytes'], config.get('encoding', 'utf-8-sig'))
    reader = csv.DictReader(lines, delimiter=config.get('delimiter', ','), strict=True)
    if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
        raise ValueError('Missing or duplicate CSV headers')
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError('CSV record does not match header')
        yield row


def json_rows(path, config):
    if path.stat().st_size > config['max_uncompressed_bytes']:
        raise ValueError('JSON exceeds in-memory parsing budget')
    with path.open('rb') as stream:
        prefix = stream.read(4096)
    if prefix.lstrip().startswith(b'<'):
        title = re.search(br'<title[^>]*>(.*?)</title>', prefix, re.I | re.S)
        detail = title.group(1).decode('utf-8', errors='replace') if title else 'HTML response'
        raise ValueError(f'Expected data but received {detail}; check source access requirements')
    payload = read_json(path)
    if isinstance(payload, dict):
        for key in ('error', 'errors', 'errorMessage'):
            if payload.get(key):
                raise ValueError(f'Source returned an error: {str(payload[key])[:300]}')
        if payload.get('status') not in (None, 'REQUEST_SUCCEEDED'):
            raise ValueError(f'Source status: {payload.get("status")}; {str(payload.get("message"))[:300]}')
        if payload.get('remark'):
            raise ValueError(f'Source returned incomplete query: {str(payload["remark"])[:300]}')
        if config.get('format') == 'bea_json' and payload.get('BEAAPI', {}).get('Results', {}).get('Error'):
            raise ValueError('BEA API returned an error')
    try:
        for key in config.get('records_path', []):
            payload = payload[key]
    except (KeyError, IndexError, TypeError):
        raise ValueError('Configured records_path is missing or empty in source response') from None
    if config.get('singleton_record'):
        if not isinstance(payload, dict):
            raise ValueError('singleton_record requires one JSON object')
        payload = [payload]
    if config['format'] == 'census_json':
        if not isinstance(payload, list) or not payload:
            raise ValueError('Census response needs a header and rows')
        header, values = payload[0], payload[1:]
        if not isinstance(header, list) or len(set(header)) != len(header):
            raise ValueError('Invalid Census header')
        for row in values:
            if len(row) != len(header):
                raise ValueError('Census row length mismatch')
            yield dict(zip(header, row))
    else:
        if not isinstance(payload, list):
            raise ValueError('Configured records_path must select an array')
        for row in payload:
            if not isinstance(row, dict):
                raise ValueError('Expected JSON object records')
            yield row.get('attributes', row) if config.get('unwrap_attributes') else row


def xlsx_rows(archive, config):
    """Read a small worksheet, with a total uncompressed XML budget. Never extract files."""
    budget = config['max_uncompressed_bytes']
    consumed = 0
    def xml(member):
        nonlocal consumed
        info = archive.getinfo(member)
        if consumed + info.file_size > budget:
            raise ValueError('XLSX XML exceeds uncompressed budget')
        content = archive.read(member)
        consumed += len(content)
        return ET.fromstring(content)
    ns = {'s':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    strings = []
    if 'xl/sharedStrings.xml' in archive.namelist():
        strings = [''.join(element.itertext()) for element in xml('xl/sharedStrings.xml').findall('s:si', ns)]
    sheet = config.get('archive_member', 'xl/worksheets/sheet1.xml')
    header = None
    for element in xml(sheet).findall('.//s:row', ns):
        values = {}
        for cell in element.findall('s:c', ns):
            column = ''.join(char for char in cell.attrib.get('r', '') if char.isalpha())
            value = cell.find('s:v', ns)
            text = value.text if value is not None else ''.join(cell.itertext())
            if cell.attrib.get('t') == 's' and value is not None:
                text = strings[int(text)]
            values[column] = text
        if header is None:
            header = {key: value for key, value in values.items() if value}
            continue
        if values:
            yield {name: values.get(column) for column, name in header.items()}


def extract_rows(path, config):
    format_name = config['format']
    with path.open('rb') as stream:
        prefix = stream.read(4096).lstrip().lower()
    if prefix.startswith((b'<html', b'<!doctype html', b'<head', b'<body')):
        detail = 'Missing Key' if b'missing key' in prefix else 'HTML error/login response'
        raise ValueError(f'Expected data but received {detail}')
    if format_name in ('json', 'census_json', 'bea_json'):
        yield from json_rows(path, config)
    elif format_name == 'csv':
        with path.open('rb') as stream:
            yield from csv_rows(stream, config)
    elif format_name == 'jsonl':
        with path.open('rb') as stream:
            for line in bounded_lines(stream, config['max_uncompressed_bytes']):
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError('Expected JSON object records')
                    yield value
    elif format_name in ('zip_csv', 'xlsx'):
        with zipfile.ZipFile(path) as archive:
            if format_name == 'xlsx':
                yield from xlsx_rows(archive, config)
            else:
                member = config.get('archive_member')
                if not member:
                    names = sorted(name for name in archive.namelist() if name.lower().endswith('.csv'))
                    if len(names) != 1:
                        raise ValueError('ZIP needs an explicit archive_member when it does not contain exactly one CSV')
                    member = names[0]
                with archive.open(member) as stream:
                    yield from csv_rows(stream, config)
    else:
        raise ValueError(f'Unsupported sample format: {format_name}')


def profile(rows):
    fields = sorted({key for row in rows for key in row})
    columns = {}
    for key in fields:
        values = [row.get(key) for row in rows]
        scalar = [value for value in values if isinstance(value, (str, int, float, bool))]
        columns[key] = {'types': sorted({type(v).__name__ for v in values}),
                        'missing': sum(v is None or v == '' for v in values),
                        'distinct_scalar_values': len({canonical(v) for v in scalar})}
    return {'rows': len(rows), 'fields': fields, 'columns': columns,
            'examples': rows[:3], 'representative': False}


def _sample_dataset(store, definition, *, allow_network=False):
    config = definition.get('sampling', {})
    if config.get('strategy') == 'download' and not allow_network:
        raise ValueError('Sampling acquisition requires --allow-network')
    limits(config)
    dataset = definition['id']
    base = store.initialize(dataset)
    started = now()
    result = {'dataset': dataset, 'started_at': started, 'criteria': config.get('criteria'),
              'max_rows': config['max_rows'], 'max_sample_bytes': config['max_sample_bytes'],
              'max_download_bytes': config['max_download_bytes']}
    try:
        if config.get('strategy') == 'blocked':
            raise ValueError(config['reason'])
        if config.get('strategy') == 'derived':
            # Use only an already published version. Sampling never triggers a build.
            ref = store.latest(dataset)
            source_path = store.version_dir(ref) / 'records.jsonl'
            acquisition = {'input_version': ref, 'original_bytes': source_path.stat().st_size,
                           'original_sha256': file_hash(source_path), 'original_complete': True}
        else:
            source_path = None
            acquisition = None
        with tempfile.TemporaryDirectory(prefix='sample-', dir=base / 'processing') as temporary:
            temporary = Path(temporary)
            if config['strategy'] == 'download':
                source_path = temporary / 'original'
                acquisition = download(config, source_path)
            elif config['strategy'] == 'local':
                source_path = PROJECT / config['path']
                if source_path.stat().st_size > config['max_download_bytes']:
                    raise ValueError('Local fixture exceeds budget')
                acquisition = {'local_fixture': config['path'], 'original_bytes': source_path.stat().st_size,
                               'original_sha256': file_hash(source_path), 'original_complete': True}
            if source_path is None:
                raise ValueError('No sampling source configured')
            sample = temporary / 'sample.jsonl'
            retained, rows = 0, []
            stop = 'source_exhausted'
            iterator = extract_rows(source_path, config)
            try:
                with sample.open('wb') as output:
                    for row in iterator:
                        if any(not re.fullmatch(pattern, str(row.get(field, '')))
                               for field, pattern in config.get('required_pattern', {}).items()):
                            continue
                        encoded = canonical(row) + b'\n'
                        if retained + len(encoded) > config['max_sample_bytes']:
                            stop = 'sample_byte_limit'
                            break
                        output.write(encoded)
                        retained += len(encoded)
                        rows.append(row)
                        if len(rows) >= config['max_rows']:
                            stop = 'row_limit'
                            break
            finally:
                iterator.close()
            if not rows:
                raise ValueError('No complete records fit the sample (empty result or oversized first record)')
            # Full source is discarded; retained payload is explicitly a parsed excerpt.
            sampling = {**acquisition, 'config': config, 'sampled': True, 'format': 'jsonl',
                        'retained_rows': len(rows), 'stop_reason': stop,
                        'original_retained': False, 'selection': 'first matching records; not a random sample'}
            metadata = {**definition.get('source', {}), 'sampling': sampling}
            artifact = store.import_file(dataset, sample, metadata, update_latest=False)
            identity = {'schema_version': 1, 'dataset': dataset, 'artifact': artifact,
                        'definition': definition, 'sampling': sampling,
                        'code': capture_code(PROJECT, 'worldmodel.sampling:sample_dataset'),
                        'profile': profile(rows)}
            sample_id = digest(identity)
            manifest_path = base / 'samples' / sample_id / 'manifest.json'
            atomic_json(manifest_path, {**identity, 'sample_id': sample_id})
            result.update(status='sampled', artifact=artifact, sample_id=sample_id,
                          rows=len(rows), retained_bytes=retained,
                          downloaded_bytes=acquisition['original_bytes'] if config['strategy'] == 'download' else 0,
                          stop_reason=stop, fields=identity['profile']['fields'],
                          manifest=str(manifest_path.relative_to(store.root)))
    except (ValueError, OSError, KeyError, TypeError, csv.Error, zipfile.BadZipFile, ET.ParseError) as error:
        result.update(status='blocked', reason=str(error)[:1000])
    result['completed_at'] = now()
    atomic_json(base / 'samples' / 'latest.json', result)
    return result


def explore(store, dataset):
    result = read_json(store.dataset_dir(dataset) / 'samples/latest.json')
    if result['status'] != 'sampled':
        return result
    manifest = read_json(store.dataset_dir(dataset) / 'samples' / result['sample_id'] / 'manifest.json')
    if digest({k:v for k,v in manifest.items() if k != 'sample_id'}) != result['sample_id']:
        raise ValueError('Sample manifest hash mismatch')
    store.artifact(manifest['artifact'])
    return {**result, 'profile': manifest['profile'], 'sampling': manifest['sampling']}


def sample_dataset(store, definition, *, allow_network=False):
    """Serialize downloads across processes sharing a data root, not just within CLI."""
    store.root.mkdir(parents=True, exist_ok=True)
    with (store.root / '.sampling.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise RuntimeError('Another sampler holds the shared disk buffer') from error
        try:
            stale = list(store.root.glob('*/processing/sample-*'))
            if stale:
                raise ValueError('Stale sampling buffer exists after interrupted run; inspect and remove it before sampling')
            return _sample_dataset(store, definition, allow_network=allow_network)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)
