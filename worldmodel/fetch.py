"""Explicit, bounded raw acquisition. No downloads occur as part of pipeline runs."""
import hashlib
from pathlib import Path
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from urllib.request import Request, urlopen
from .util import hash_id


def public_url(url):
    parts = urlsplit(url)
    if parts.username or parts.password:
        raise ValueError('Use request headers instead of credentials in URLs')
    private = {'key', 'api_key', 'apikey', 'token', 'access_token', 'signature', 'userid'}
    query = [(key, '[REDACTED]' if key.lower() in private else value)
             for key, value in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


def fetch(store, dataset, url, source, *, allow_network=False, expected_sha256=None,
          max_bytes=1024 * 1024, timeout=60, retries=2, headers=None):
    if not allow_network:
        raise ValueError('Network acquisition requires --allow-network')
    if urlsplit(url).scheme not in ('http', 'https'):
        raise ValueError('Fetch supports only HTTP(S); use import for local files')
    safe_url = public_url(url)
    if max_bytes <= 0 or timeout <= 0 or not 0 <= retries <= 5:
        raise ValueError('Invalid download limits')
    if expected_sha256:
        hash_id(expected_sha256)
    base = store.initialize(dataset)
    with tempfile.TemporaryDirectory(prefix='fetch-', dir=base / 'processing') as temporary:
        payload = Path(temporary) / 'download'
        for attempt in range(retries + 1):
            try:
                request = Request(url, headers={'User-Agent': 'worldmodel-substrate/0.1', **(headers or {})})
                with urlopen(request, timeout=timeout) as response, payload.open('wb') as output:
                    declared = response.headers.get('Content-Length')
                    if declared and int(declared) > max_bytes:
                        raise ValueError('Download exceeds byte limit')
                    checksum, total = hashlib.sha256(), 0
                    while block := response.read(1024 * 1024):
                        total += len(block)
                        if total > max_bytes:
                            raise ValueError('Download exceeds byte limit')
                        checksum.update(block)
                        output.write(block)
                    if declared and total != int(declared):
                        raise ValueError('Download length does not match Content-Length')
                    if expected_sha256 and checksum.hexdigest() != expected_sha256:
                        raise ValueError('Downloaded checksum mismatch')
                    acquisition = {**source, 'url': safe_url, 'resolved_url': public_url(response.url),
                                   'etag': response.headers.get('ETag'),
                                   'last_modified': response.headers.get('Last-Modified'),
                                   'content_type': response.headers.get('Content-Type')}
                return store.import_file(dataset, payload, acquisition)
            except HTTPError as error:
                retryable = error.code in (408, 429, 500, 502, 503, 504)
                error.close()
                if not retryable or attempt == retries:
                    raise ValueError(f'HTTP acquisition failed with status {error.code}: {safe_url}') from None
            except (URLError, TimeoutError, ConnectionError):
                if attempt == retries:
                    raise ValueError(f'Network acquisition failed: {safe_url}') from None
            time.sleep(2 ** attempt)
    raise RuntimeError('Unreachable acquisition state')
