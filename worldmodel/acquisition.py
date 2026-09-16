"""Full-scale raw acquisition driven by the ``acquisition`` block of dataset.json.

Strategies: ``files`` (explicit URLs), ``url_list`` (parameter grid over a
template) and ``paged_api`` (offset/page/cursor/next_url pagination). Every
byte written is reserved against the dataset's fair-share allocation from
``worldmodel.budget``. Work happens in ``<dataset>/scratch/acquire-<digest>/``
and is published as an immutable sharded raw artifact (hardlinked, no second
copy). See docs/full-acquisition.md for the schema and semantics.
"""
from contextlib import contextmanager
from copy import deepcopy
from email.utils import parsedate_to_datetime
import fcntl
import hashlib
import http.client
import itertools
import json
import os
from pathlib import Path
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import Request, urlopen
import uuid
import zlib

from .budget import (DEFAULT_MAX_SHARE, BudgetExhausted, DailyRequestLimit, Ledger, acquisition_identity,
                     budget_table, demand, resolve_total)
from .fetch import public_body, public_url
from .raw_readers import validate_reader
from .util import atomic_json, canonical, digest, now, read_json

MIB = 1024 * 1024
DEFAULT_USER_AGENT = 'worldmodel-substrate/0.3 full-acquisition'
RETRY_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
TERMINAL_STOPS = frozenset({'budget', 'page_limit'})
STRATEGIES = ('files', 'url_list', 'paged_api')

COMMON_DEFAULTS = {
    'strategy': None, 'desired_bytes': None, 'min_bytes': 0, 'priority': 1,
    'description': None, 'documentation': None, 'reader': None,
    'user_agent': DEFAULT_USER_AGENT, 'user_agent_env': None, 'credentials': [], 'headers': {},
    'rate_limit': None, 'timeout_seconds': 60, 'retries': 5, 'backoff_seconds': 2.0,
    'max_backoff_seconds': 300, 'max_retry_after_seconds': 3600, 'reserve_chunk_bytes': 8 * MIB,
    'accept_encoding': None, 'publish_partial': True,
}
RATE_DEFAULTS = {'requests_per_second': 1.0, 'requests_per_day': None, 'key': None}
STRATEGY_DEFAULTS = {
    'files': {'files': None, 'skip_statuses': []},
    'url_list': {'url_template': None, 'parameters': {}, 'combinations': None, 'method': 'GET',
                 'body_template': None, 'skip_statuses': [], 'max_requests': 1_000_000},
    'paged_api': {'url_template': None, 'parameters': {}, 'combinations': None, 'method': 'GET',
                  'body_template': None, 'pagination': None, 'records_path': None, 'stop': None,
                  'store': 'jsonl', 'rollover_bytes': 256 * MIB, 'max_page_bytes': 64 * MIB,
                  'error_paths': []},
}
PAGINATION_DEFAULTS = {'mode': None, 'param': None, 'size_param': None, 'page_size': None, 'start': None,
                       'location': None, 'cursor_path': None, 'next_url_path': None, 'link_header': None}
STOP_DEFAULTS = {'empty_page': True, 'short_page': True, 'total_path': None, 'total_pages_path': None,
                 'has_more_path': None, 'max_pages': None}


class HttpStatusError(ValueError):
    def __init__(self, status, url):
        super().__init__(f'HTTP {status} from {url}')
        self.status = status


class AcquisitionNetworkError(ValueError):
    pass


class _Stop(Exception):
    def __init__(self, reason, detail=None):
        super().__init__(detail or reason)
        self.reason = reason


def _merge(defaults, value, name):
    if value is None:
        value = {}
    if not isinstance(value, dict):
        raise ValueError(f'{name} must be an object')
    unknown = set(value) - set(defaults)
    if unknown:
        raise ValueError(f'Unknown {name} keys: ' + ', '.join(sorted(unknown)))
    return {**deepcopy(defaults), **deepcopy(value)}


def _positive_int(value, name, allow_none=False):
    if value is None and allow_none:
        return
    if type(value) is not int or value <= 0:
        raise ValueError(f'{name} must be a positive integer')


def _path(value, name):
    if value is None:
        return
    if not isinstance(value, list) or any(type(k) not in (str, int) for k in value):
        raise ValueError(f'{name} must be a JSON path array of keys/indices')


def normalize_config(block):
    """Validate an acquisition block and return it with every default filled in."""
    if not isinstance(block, dict):
        raise ValueError('acquisition must be an object')
    strategy = block.get('strategy')
    if strategy not in STRATEGIES:
        raise ValueError('acquisition.strategy must be one of ' + ', '.join(STRATEGIES))
    cfg = _merge({**COMMON_DEFAULTS, **STRATEGY_DEFAULTS[strategy]}, block, 'acquisition')
    demand({'acquisition': cfg})
    cfg['rate_limit'] = _merge(RATE_DEFAULTS, cfg['rate_limit'], 'acquisition.rate_limit')
    rps, rpd = cfg['rate_limit']['requests_per_second'], cfg['rate_limit']['requests_per_day']
    if rps is not None and (type(rps) not in (int, float) or rps <= 0):
        raise ValueError('rate_limit.requests_per_second must be positive or null')
    _positive_int(rpd, 'rate_limit.requests_per_day', allow_none=True)
    for name in ('timeout_seconds', 'backoff_seconds', 'max_backoff_seconds', 'max_retry_after_seconds'):
        if type(cfg[name]) not in (int, float) or cfg[name] < 0:
            raise ValueError(f'acquisition.{name} must be a non-negative number')
    if type(cfg['retries']) is not int or not 0 <= cfg['retries'] <= 20:
        raise ValueError('acquisition.retries must be an integer in 0..20')
    _positive_int(cfg['reserve_chunk_bytes'], 'acquisition.reserve_chunk_bytes')
    if cfg['accept_encoding'] is None:
        cfg['accept_encoding'] = 'identity' if strategy == 'files' else 'gzip'
    if cfg['accept_encoding'] not in ('identity', 'gzip'):
        raise ValueError('acquisition.accept_encoding must be identity or gzip')
    if not isinstance(cfg['headers'], dict) or any(not isinstance(k, str) or not isinstance(v, str) or '\n' in v
                                                    for k, v in cfg['headers'].items()):
        raise ValueError('acquisition.headers must map header names to single-line strings')
    if not isinstance(cfg['credentials'], list):
        raise ValueError('acquisition.credentials must be a list')
    for credential in cfg['credentials']:
        if (not isinstance(credential, dict) or set(credential) - {'env', 'query', 'header', 'body', 'prefix', 'optional'}
                or not isinstance(credential.get('env'), str)
                or sum(name in credential for name in ('query', 'header', 'body')) != 1):
            raise ValueError('Each credential needs env plus exactly one of query, header or body')
        if 'body' in credential and not isinstance(cfg.get('body_template'), dict):
            raise ValueError('body credentials require an object body_template (url_list or paged_api POST)')
    if cfg['user_agent_env'] is not None and not isinstance(cfg['user_agent_env'], str):
        raise ValueError('acquisition.user_agent_env must name an environment variable')
    if cfg['reader'] is not None:
        validate_reader(cfg['reader'])
    if strategy == 'files':
        if not isinstance(cfg['files'], list) or not cfg['files']:
            raise ValueError('files strategy requires a non-empty files list')
        entries = []
        for entry in cfg['files']:
            entry = {'url': entry} if isinstance(entry, str) else entry
            if not isinstance(entry, dict) or set(entry) - {'url', 'sha256', 'name'} or not isinstance(entry.get('url'), str):
                raise ValueError('files entries must be URLs or {url, sha256, name}')
            if entry.get('sha256') is not None and not re.fullmatch(r'[a-f0-9]{64}', entry['sha256']):
                raise ValueError('files sha256 must be 64 lowercase hex characters')
            entries.append(entry)
        cfg['files'] = entries
    else:
        if not isinstance(cfg['url_template'], str):
            raise ValueError(f'{strategy} requires url_template')
        if cfg['method'] not in ('GET', 'POST'):
            raise ValueError('method must be GET or POST')
        expand(cfg['parameters'], cfg['combinations'])
    if strategy in ('files', 'url_list'):
        if not isinstance(cfg['skip_statuses'], list) or any(type(s) is not int for s in cfg['skip_statuses']):
            raise ValueError('skip_statuses must be a list of HTTP status integers')
    if strategy == 'url_list':
        _positive_int(cfg['max_requests'], 'max_requests')
        if len(expand(cfg['parameters'], cfg['combinations'])) > cfg['max_requests']:
            raise ValueError('url_list expands beyond max_requests')
    if strategy == 'paged_api':
        pagination = cfg['pagination'] = _merge(PAGINATION_DEFAULTS, cfg['pagination'], 'pagination')
        stop = cfg['stop'] = _merge(STOP_DEFAULTS, cfg['stop'], 'stop')
        mode = pagination['mode']
        if mode not in ('offset', 'page', 'cursor', 'next_url'):
            raise ValueError('pagination.mode must be offset, page, cursor or next_url')
        if pagination['param'] is None and mode != 'next_url':
            pagination['param'] = mode
        if pagination['start'] is None:
            pagination['start'] = {'offset': 0, 'page': 1}.get(mode)
        if pagination['location'] is None:
            pagination['location'] = 'body' if cfg['body_template'] is not None else 'query'
        if pagination['location'] not in ('query', 'body'):
            raise ValueError('pagination.location must be query or body')
        if pagination['location'] == 'body' and not isinstance(cfg['body_template'], dict):
            raise ValueError('pagination.location=body requires an object body_template')
        _positive_int(pagination['page_size'], 'pagination.page_size', allow_none=True)
        for name in ('cursor_path', 'next_url_path'):
            _path(pagination[name], 'pagination.' + name)
        for name in ('total_path', 'total_pages_path', 'has_more_path'):
            _path(stop[name], 'stop.' + name)
        _path(cfg['records_path'], 'records_path')
        _positive_int(stop['max_pages'], 'stop.max_pages', allow_none=True)
        if mode == 'cursor' and pagination['cursor_path'] is None:
            raise ValueError('cursor pagination requires pagination.cursor_path')
        if mode == 'next_url':
            if pagination['link_header'] is None:
                pagination['link_header'] = pagination['next_url_path'] is None
            if not pagination['link_header'] and pagination['next_url_path'] is None:
                raise ValueError('next_url pagination requires next_url_path or link_header')
        if mode in ('offset', 'page') and cfg['records_path'] is None and pagination['page_size'] is None:
            raise ValueError(f'{mode} pagination needs records_path or page_size')
        if mode in ('offset', 'page') and not (cfg['records_path'] is not None or stop['total_path']
                                              or stop['total_pages_path'] or stop['has_more_path'] or stop['max_pages']):
            raise ValueError(f'{mode} pagination needs a stop signal (records_path, total, has_more or max_pages)')
        if cfg['store'] not in ('jsonl', 'pages'):
            raise ValueError('paged_api store must be jsonl or pages')
        _positive_int(cfg['rollover_bytes'], 'rollover_bytes')
        _positive_int(cfg['max_page_bytes'], 'max_page_bytes')
        for path in cfg['error_paths']:
            _path(path, 'error_paths entry')
    return cfg


def expand(parameters, combinations=None):
    """Expand a parameter grid (declared key order, cartesian product) or explicit combinations."""
    if combinations is not None:
        if not isinstance(combinations, list) or not combinations or any(not isinstance(c, dict) for c in combinations):
            raise ValueError('combinations must be a non-empty list of objects')
        return deepcopy(combinations)
    if not isinstance(parameters, dict):
        raise ValueError('parameters must be an object')
    names, axes = [], []
    for name, values in parameters.items():
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name):
            raise ValueError(f'Invalid parameter name: {name}')
        if isinstance(values, dict):
            if set(values) - {'range', 'step'} or not isinstance(values.get('range'), list) or len(values['range']) != 2:
                raise ValueError(f'Parameter {name} range must be {{"range": [first, last], "step": n}}')
            first, last = values['range']
            step = values.get('step', 1)
            if type(first) is not int or type(last) is not int or type(step) is not int or step <= 0 or last < first:
                raise ValueError(f'Parameter {name} range needs integers with first <= last and positive step')
            values = list(range(first, last + 1, step))
        if not isinstance(values, list) or not values:
            raise ValueError(f'Parameter {name} must be a non-empty list or range')
        names.append(name)
        axes.append(values)
    return [dict(zip(names, combo)) for combo in itertools.product(*axes)]


class _Values(dict):
    def __missing__(self, key):
        raise ValueError(f'Template variable {{{key}}} is not defined by parameters')


def render_url(template, values):
    return template.format_map(_Values({k: quote(str(v), safe='') for k, v in values.items()}))


def render_body(template, values):
    if isinstance(template, dict):
        return {k: render_body(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [render_body(v, values) for v in template]
    if isinstance(template, str):
        exact = re.fullmatch(r'\{([A-Za-z_][A-Za-z0-9_]*)\}', template)
        if exact:
            return _Values(values)[exact[1]]
        return template.format_map(_Values(values))
    return template


def _lookup(payload, path, default=None):
    try:
        for key in path:
            payload = payload[key]
        return payload
    except (KeyError, IndexError, TypeError):
        return default


def _set_query(url, assignments):
    parts = urlsplit(url)
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in assignments]
    query += [(k, str(v)) for k, v in assignments.items()]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ''))


def _set_body(body, assignments):
    body = deepcopy(body)
    for dotted, value in assignments.items():
        target, keys = body, dotted.split('.')
        for key in keys[:-1]:
            target = target.setdefault(key, {})
        target[keys[-1]] = value
    return body


def _link_next(header):
    for part in (header or '').split(','):
        match = re.match(r'\s*<([^>]*)>\s*;(.*)', part)
        if match and re.search(r'rel\s*=\s*"?next"?', match[2]):
            return match[1]
    return None


def _retry_after(value):
    if value is None:
        return None
    value = value.strip()
    if value.isdigit():
        return float(value)
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


class HostLocks:
    """One connection per host (or rate-limit key) within a process."""
    def __init__(self):
        self._guard, self._locks = threading.Lock(), {}

    @contextmanager
    def hold(self, key):
        with self._guard:
            lock = self._locks.setdefault(key, threading.Lock())
        with lock:
            yield


class _Reservation:
    def __init__(self, engine):
        self.engine, self.rid, self.reserved, self.pending = engine, None, 0, 0

    def need(self, nbytes, exact=False):
        available = self.reserved - self.pending
        short = nbytes - available
        if short <= 0:
            return
        amounts = [short] if exact else [max(short, self.engine.cfg['reserve_chunk_bytes']), short]
        ledger, dataset, allowed = self.engine.ledger, self.engine.dataset, self.engine.allowed
        for position, amount in enumerate(dict.fromkeys(amounts)):
            try:
                if self.rid is None:
                    self.rid = ledger.reserve(dataset, amount, allowed)
                else:
                    ledger.extend(self.rid, dataset, amount, allowed)
                self.reserved += amount
                return
            except BudgetExhausted:
                if position == len(dict.fromkeys(amounts)) - 1:
                    raise

    def wrote(self, nbytes):
        self.pending += nbytes
        if self.pending >= self.engine.cfg['reserve_chunk_bytes']:
            self.flush()

    def flush(self):
        if self.rid is not None and self.pending:
            self.engine.ledger.consume(self.rid, self.engine.dataset, self.pending)
            self.reserved -= self.pending
            self.pending = 0

    def close(self):
        if self.rid is not None:
            self.flush()
            self.engine.ledger.release(self.rid)
        self.rid, self.reserved, self.pending = None, 0, 0


class Acquisition:
    def __init__(self, store, definition, *, ledger=None, allowed=None, total=None, max_share=DEFAULT_MAX_SHARE,
                 definitions=None, allow_network=False, resume=False, restart=False, max_shards=None,
                 sleep=time.sleep, opener=urlopen, host_locks=None, environ=None):
        self.store, self.definition, self.dataset = store, definition, definition['id']
        if 'acquisition' not in definition:
            raise ValueError(f'{self.dataset}: no acquisition block declared in dataset.json')
        if definition.get('kind') != 'source':
            raise ValueError('Full acquisition belongs to source datasets')
        self.cfg = normalize_config(definition['acquisition'])
        self.config_digest = digest(self.cfg)
        # Staging, resume state and "settled" budget status key on download content only.
        self.content_digest = acquisition_identity(definition['acquisition'])
        self.ledger = ledger or Ledger(store.root)
        self.allowed, self.total, self.max_share = allowed, total, max_share
        self.definitions = definitions
        self.allow_network, self.resume, self.restart = allow_network, resume, restart
        if max_shards is not None and (type(max_shards) is not int or max_shards <= 0):
            raise ValueError('max_shards must be a positive integer')
        self.max_shards, self.sleep, self.opener = max_shards, sleep, opener
        self.host_locks = host_locks or HostLocks()
        self.environ = os.environ if environ is None else environ
        self.requests, self.new_shards, self.downloaded = 0, 0, 0
        self.secrets, self.cred_query, self.cred_headers, self.cred_body = [], {}, {}, {}
        self.user_agent = self.cfg['user_agent']
        self.staging = store.dataset_dir(self.dataset) / 'scratch' / ('acquire-' + self.content_digest[:16])

    # ----- configuration-derived helpers -------------------------------------------------
    def _resolve_credentials(self):
        for credential in self.cfg['credentials']:
            value = self.environ.get(credential['env'])
            if not value:
                if credential.get('optional'):
                    continue
                raise ValueError(f'Missing {credential["env"]}; set it in the environment or .env')
            if '\r' in value or '\n' in value:
                raise ValueError(f'Invalid multiline credential in {credential["env"]}')
            if 'query' in credential:
                self.cred_query[credential['query']] = value
            elif 'body' in credential:
                self.cred_body[credential['body']] = credential.get('prefix', '') + value
            else:
                self.cred_headers[credential['header']] = credential.get('prefix', '') + value
            self.secrets.append(value)
        if self.cfg['user_agent_env']:
            value = self.environ.get(self.cfg['user_agent_env'])
            if not value:
                raise ValueError(f'Missing {self.cfg["user_agent_env"]}; publisher requires a contact User-Agent')
            if '\r' in value or '\n' in value:
                raise ValueError('Invalid multiline User-Agent')
            self.user_agent = value

    def private_names(self):
        return [c.get('query') or c['body'].split('.')[-1] for c in self.cfg['credentials'] if 'query' in c or 'body' in c]

    def public(self, url):
        return public_url(url, extra_private=self.private_names())

    def guard(self, value):
        """Refuse to persist any credential value in metadata."""
        blob = canonical(value).decode('utf-8')
        for secret in self.secrets:
            if len(secret) >= 4 and (secret in blob or quote(secret, safe='') in blob
                                     or json.dumps(secret)[1:-1] in blob):
                raise ValueError('Refusing to write a credential value into acquisition metadata')
        return value

    def plan_requests(self):
        cfg = self.cfg
        if cfg['strategy'] == 'files':
            return [{'url': f['url'], 'method': 'GET', 'body': None, 'params': {}, 'sha256': f.get('sha256'),
                     'name': f.get('name')} for f in cfg['files']]
        combos = expand(cfg['parameters'], cfg['combinations'])
        return [{'url': render_url(cfg['url_template'], values), 'method': cfg['method'],
                 'body': render_body(cfg['body_template'], values) if cfg['body_template'] is not None else None,
                 'params': values, 'sha256': None, 'name': None} for values in combos]

    def request_meta(self, url, method, body, params, **extra):
        meta = {'method': method, 'url': self.public(url), 'params': params, **extra}
        if body is not None:
            meta['body'] = public_body(body, self.private_names())
        return self.guard(meta)

    # ----- HTTP core ------------------------------------------------------------------------
    def _request(self, url, method, body, headers):
        parts = urlsplit(url)
        if parts.scheme not in ('http', 'https'):
            raise ValueError('Acquisition URLs must use HTTP(S)')
        if self.cred_query:
            url = _set_query(url, self.cred_query)
        merged = {'User-Agent': self.user_agent, 'Accept-Encoding': self.cfg['accept_encoding'],
                  **self.cfg['headers'], **self.cred_headers, **headers}
        data = None
        if body is not None and self.cred_body:
            body = _set_body(body, self.cred_body)  # Injected per request; never recorded.
        if body is not None:
            data = canonical(body)
            merged['Content-Type'] = 'application/json'
        return Request(url, data=data, method=method, headers=merged)

    def _delay(self, attempt, retry_after):
        seconds = _retry_after(retry_after)
        if seconds is not None:
            if seconds > self.cfg['max_retry_after_seconds']:
                raise _Stop('rate_limited', f'Retry-After {seconds:.0f}s exceeds max_retry_after_seconds')
            return seconds
        return min(self.cfg['max_backoff_seconds'], self.cfg['backoff_seconds'] * (2 ** attempt))

    def call(self, url, method, body, handler, headers=lambda: {}):
        """Rate-limited request with retries; ``handler(response)`` consumes the body inside the retry scope."""
        safe = self.public(url)
        limit = self.cfg['rate_limit']
        key = limit['key'] or urlsplit(url).hostname
        for attempt in itertools.count():
            try:
                with self.host_locks.hold(key):
                    wait = self.ledger.rate_slot(key, limit['requests_per_second'], limit['requests_per_day'])
                    if wait > 0:
                        self.sleep(wait)
                    self.requests += 1
                    with self.opener(self._request(url, method, body, headers()), timeout=self.cfg['timeout_seconds']) as response:
                        return handler(response)
            except HTTPError as error:
                status, retry_after = error.code, error.headers.get('Retry-After') if error.headers else None
                error.close()
                if status not in RETRY_STATUSES or attempt >= self.cfg['retries']:
                    raise HttpStatusError(status, safe) from None
                delay = self._delay(attempt, retry_after)
            except (URLError, TimeoutError, ConnectionError, http.client.HTTPException) as error:
                if attempt >= self.cfg['retries']:
                    raise AcquisitionNetworkError(f'Network failure from {safe}: {type(error).__name__}') from None
                delay = self._delay(attempt, None)
            self.sleep(delay)

    @staticmethod
    def _decoder(response):
        encoding = (response.headers.get('Content-Encoding') or 'identity').lower()
        if encoding in ('gzip', 'x-gzip'):
            return zlib.decompressobj(16 + zlib.MAX_WBITS)
        if encoding != 'identity':
            raise ValueError(f'Unsupported Content-Encoding: {encoding}')
        return None

    @staticmethod
    def _decoded_blocks(response, decoder):
        """Yield decoded blocks of at most 1 MiB, so decoded budgets apply before bytes hit disk."""
        while True:
            block = response.read(MIB)
            if not block:
                break
            if decoder is None:
                yield block
                continue
            data = decoder.decompress(block, MIB)
            while data:
                yield data
                data = decoder.decompress(decoder.unconsumed_tail, MIB) if decoder.unconsumed_tail else b''
        if decoder is not None:
            tail = decoder.flush()
            if tail:
                yield tail
            if not decoder.eof:
                raise http.client.IncompleteRead(b'')

    # ----- shard downloads -----------------------------------------------------------------
    def download(self, spec, key, part, final):
        validators = self.state.setdefault('validators', {})
        reservation = _Reservation(self)
        info = {}

        def headers():
            have = part.stat().st_size if part.exists() else 0
            if not have:
                return {}
            extra = {'Range': f'bytes={have}-', 'Accept-Encoding': 'identity'}
            validator = validators.get(key, {})
            if validator.get('etag') or validator.get('last_modified'):
                extra['If-Range'] = validator.get('etag') or validator['last_modified']
            return extra

        def discard():
            if part.exists():
                size = part.stat().st_size
                part.unlink()
                self.ledger.add_staged(self.dataset, -size)

        def handler(response):
            have = part.stat().st_size if part.exists() else 0
            decoder = self._decoder(response)
            total = None
            if have and response.status == 206 and decoder is None:
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+|\*)', response.headers.get('Content-Range', '').strip())
                if not match or int(match[1]) != have:
                    discard()
                    raise http.client.HTTPException('Unusable Content-Range; restarting shard')
                total = None if match[3] == '*' else int(match[3])
                mode = 'ab'
            else:
                if have:
                    discard()
                    have = 0
                mode = 'wb'
            length = response.headers.get('Content-Length')
            if decoder is None and length is not None and length.isdigit():
                total = have + int(length) if total is None else total
                reservation.need(int(length), exact=True)
            validators[key] = {'etag': response.headers.get('ETag'), 'last_modified': response.headers.get('Last-Modified')}
            atomic_json(self.staging / 'state.json', self.guard(self.state))
            checksum = hashlib.sha256()
            if mode == 'ab':
                with part.open('rb') as existing:
                    for block in iter(lambda: existing.read(MIB), b''):
                        checksum.update(block)
            with part.open(mode) as output:
                try:
                    for block in self._decoded_blocks(response, decoder):
                        reservation.need(len(block))
                        output.write(block)
                        checksum.update(block)
                        reservation.wrote(len(block))
                        self.downloaded += len(block)
                finally:
                    output.flush()
                    os.fsync(output.fileno())
            size = part.stat().st_size
            if total is not None and size != total:
                raise http.client.IncompleteRead(b'', total - size)
            info.update(sha256=checksum.hexdigest(), bytes=size, content_type=response.headers.get('Content-Type'),
                        etag=response.headers.get('ETag'), last_modified=response.headers.get('Last-Modified'),
                        resolved_url=self.public(response.url), decoded_content_encoding=decoder is not None)

        try:
            for attempt in range(2):
                try:
                    self.call(spec['url'], spec['method'], spec['body'], handler, headers)
                    break
                except HttpStatusError as error:
                    if error.status == 416 and part.exists() and attempt == 0:
                        discard()
                        continue
                    raise
        finally:
            reservation.close()
        if spec.get('sha256') and info['sha256'] != spec['sha256']:
            discard()
            raise ValueError(f'Checksum mismatch for {self.public(spec["url"])}')
        os.replace(part, final)
        validators.pop(key, None)
        return {'request': self.request_meta(spec['url'], spec['method'], spec['body'], spec['params']),
                **({'name': spec['name']} if spec.get('name') else {}),
                **info, 'retrieved_at': now(), 'complete': True, 'role': 'data', 'staging_index': int(key)}

    def fetch_page(self, url, method, body):
        limit = self.cfg['max_page_bytes']

        def handler(response):
            decoder, chunks, size = self._decoder(response), [], 0
            for block in self._decoded_blocks(response, decoder):
                size += len(block)
                if size > limit:
                    raise ValueError(f'Page exceeds max_page_bytes={limit}: {self.public(url)}')
                chunks.append(block)
            return b''.join(chunks), response.headers, response.url
        return self.call(url, method, body, handler)

    # ----- state ----------------------------------------------------------------------------
    def save(self):
        atomic_json(self.staging / 'state.json', self.guard(self.state))

    def shards_dir(self):
        path = self.staging / 'shards'
        path.mkdir(parents=True, exist_ok=True)
        return path

    def check_max_shards(self):
        if self.max_shards is not None and self.new_shards >= self.max_shards:
            raise _Stop('max_shards')

    # ----- strategies -----------------------------------------------------------------------
    def run_list(self):
        shards, skipped = self.state['shards'], self.state.setdefault('skipped', {})
        for index, spec in enumerate(self.plan_requests()):
            key = str(index)
            if key in shards or key in skipped:
                continue
            self.check_max_shards()
            directory = self.shards_dir()
            try:
                meta = self.download(spec, key, directory / f'{index}.part', directory / key)
            except HttpStatusError as error:
                if error.status not in self.cfg['skip_statuses']:
                    raise
                skipped[key] = {'status': error.status, 'request': self.request_meta(spec['url'], spec['method'], spec['body'], spec['params'])}
                self.save()
                continue
            shards[key] = meta
            self.new_shards += 1
            self.save()

    def page_request(self, values, position):
        cfg, pagination = self.cfg, self.cfg['pagination']
        url = render_url(cfg['url_template'], values)
        body = render_body(cfg['body_template'], values) if cfg['body_template'] is not None else None
        if pagination['mode'] == 'next_url':
            if position is not None:
                if urlsplit(position).hostname != urlsplit(url).hostname or urlsplit(position).scheme not in ('http', 'https'):
                    raise ValueError('next_url pagination left the configured host; refusing to send credentials')
                url = position
            return url, body
        assignments = {}
        if position is not None:
            assignments[pagination['param']] = position
        if pagination['size_param'] and pagination['page_size']:
            assignments[pagination['size_param']] = pagination['page_size']
        if pagination['location'] == 'query':
            url = _set_query(url, assignments) if assignments else url
        else:
            body = _set_body(body, assignments)
        return url, body

    def advance(self, position, payload, records, headers, final_url, pages_in_combo):
        pagination, stop = self.cfg['pagination'], self.cfg['stop']
        count = len(records) if records is not None else None
        size = pagination['page_size']
        if stop['empty_page'] and count == 0:
            return None, True
        if stop['has_more_path'] is not None and not _lookup(payload, stop['has_more_path']):
            return None, True
        mode = pagination['mode']
        if mode in ('offset', 'page'):
            if stop['short_page'] and size and count is not None and count < size:
                return None, True
            fetched_records = (pages_in_combo * size) if size else None
            if mode == 'offset':
                following = position + (size if size else count)
                fetched_records = following - pagination['start']
            else:
                following = position + 1
            total = _lookup(payload, stop['total_path']) if stop['total_path'] else None
            if total is not None and fetched_records is not None and fetched_records >= int(total):
                return None, True
            pages = _lookup(payload, stop['total_pages_path']) if stop['total_pages_path'] else None
            if pages is not None and pages_in_combo >= int(pages):
                return None, True
            return following, False
        if mode == 'cursor':
            following = _lookup(payload, pagination['cursor_path'])
            if following in (None, '') or following == position:
                return None, True
            return following, False
        following = (_lookup(payload, pagination['next_url_path']) if pagination['next_url_path']
                     else _link_next(headers.get('Link')))
        if not following:
            return None, True
        following = urljoin(final_url, following)
        if self.cred_query:  # Echoed credentials are dropped; _request re-injects them.
            parts = urlsplit(following)
            query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in self.cred_query]
            following = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))
        return following, False

    def seal_open_shard(self):
        current = self.state.get('open_shard')
        if not current or not current['bytes']:
            self.state['open_shard'] = None
            return
        directory = self.shards_dir()
        part, final = directory / f'{current["index"]}.part', directory / str(current['index'])
        os.replace(part, final)
        self.state['shards'][str(current['index'])] = {
            'request': current['first_request'], 'last_request': current['last_request'],
            'pages': current['pages'], 'lines': current['lines'], 'sha256': current['sha256'],
            'bytes': current['bytes'], 'format': 'jsonl', 'retrieved_at': current['retrieved_at'],
            'complete': True, 'role': 'data', 'staging_index': current['index']}
        self.state['open_shard'] = None
        self.hasher = None
        self.new_shards += 1
        self.save()

    def open_shard(self):
        current = self.state.get('open_shard')
        directory = self.shards_dir()
        if current is None:
            index = self.state['next_index']
            self.state['next_index'] += 1
            current = self.state['open_shard'] = {'index': index, 'bytes': 0, 'lines': 0, 'pages': 0, 'sha256': None,
                                                  'first_request': None, 'last_request': None, 'retrieved_at': None}
            (directory / f'{index}.part').write_bytes(b'')
            self.hasher = hashlib.sha256()
        elif getattr(self, 'hasher', None) is None:
            part = directory / f'{current["index"]}.part'
            if not part.exists():
                part.write_bytes(b'')
            if part.stat().st_size < current['bytes']:
                raise ValueError('Open acquisition shard is shorter than recorded state; use --restart')
            with part.open('r+b') as stream:  # Drop bytes written after the last saved state.
                stream.truncate(current['bytes'])
            self.hasher = hashlib.sha256()
            with part.open('rb') as stream:
                for block in iter(lambda: stream.read(MIB), b''):
                    self.hasher.update(block)
        return current

    def store_page(self, content, payload, records, meta):
        if self.cfg['store'] == 'pages':
            index = self.state['next_index']
            directory = self.shards_dir()
            part, final = directory / f'{index}.part', directory / str(index)
            reservation = _Reservation(self)
            try:
                reservation.need(len(content), exact=True)
                with part.open('wb') as output:
                    output.write(content)
                    output.flush()
                    os.fsync(output.fileno())
                reservation.wrote(len(content))
            finally:
                reservation.close()
            os.replace(part, final)
            self.downloaded += len(content)
            self.state['next_index'] += 1
            self.state['shards'][str(index)] = {'request': meta, 'records': None if records is None else len(records),
                                                'sha256': hashlib.sha256(content).hexdigest(), 'bytes': len(content),
                                                'format': 'json', 'retrieved_at': now(), 'complete': True,
                                                'role': 'data', 'staging_index': index}
            self.new_shards += 1
            return
        lines = [canonical(r) + b'\n' for r in records] if records is not None else [canonical(payload) + b'\n']
        data = b''.join(lines)
        current = self.open_shard()
        if current['bytes'] and current['bytes'] + len(data) > self.cfg['rollover_bytes']:
            self.seal_open_shard()
            self.check_max_shards()
            current = self.open_shard()
        reservation = _Reservation(self)
        try:
            reservation.need(len(data), exact=True)
            part = self.shards_dir() / f'{current["index"]}.part'
            with part.open('ab') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            reservation.wrote(len(data))
        finally:
            reservation.close()
        self.hasher.update(data)
        self.downloaded += len(data)
        log = {'page': self.state['pages'], 'shard_staging_index': current['index'], 'first_line': current['lines'] + 1,
               'lines': len(lines), 'response_bytes': len(content), 'request': meta, 'retrieved_at': now()}
        log_path = self.staging / 'page_log.jsonl'
        with log_path.open('ab') as stream:
            stream.truncate(self.state['page_log_bytes'])
            stream.write(canonical(self.guard(log)) + b'\n')
            stream.flush()
            os.fsync(stream.fileno())
        self.state['page_log_bytes'] = log_path.stat().st_size
        current.update(bytes=current['bytes'] + len(data), lines=current['lines'] + len(lines), pages=current['pages'] + 1,
                       sha256=self.hasher.hexdigest(), last_request=meta, retrieved_at=current['retrieved_at'] or now())
        current['first_request'] = current['first_request'] or meta

    def run_paged(self):
        cfg = self.cfg
        combos = expand(cfg['parameters'], cfg['combinations'])
        progress = self.state.setdefault('paged', {'combo': 0, 'position': None, 'started': False, 'combo_pages': 0,
                                                   'truncated': []})
        for combo in range(progress['combo'], len(combos)):
            values = combos[combo]
            if not progress['started']:
                progress.update(started=True, combo_pages=0, position=cfg['pagination']['start'])
            while True:
                if cfg['store'] == 'pages':
                    self.check_max_shards()
                position = progress['position']
                url, body = self.page_request(values, position)
                content, headers, final_url = self.fetch_page(url, cfg['method'], body)
                try:
                    payload = json.loads(content)
                except ValueError:
                    raise ValueError(f'Non-JSON page from {self.public(url)}') from None
                for path in cfg['error_paths']:
                    if _lookup(payload, path):
                        raise ValueError(f'Source reported an error at {path} for {self.public(url)}')
                records = None
                if cfg['records_path'] is not None:
                    records = _lookup(payload, cfg['records_path'])
                    if records is None:
                        records = []
                    if isinstance(records, dict):
                        records = [records]
                    if not isinstance(records, list):
                        raise ValueError(f'records_path does not select an array for {self.public(url)}')
                meta = self.request_meta(url, cfg['method'], body, values, page=progress['combo_pages'] + 1)
                self.store_page(content, payload, records, meta)
                self.state['pages'] += 1
                progress['combo_pages'] += 1
                following, done = self.advance(position, payload, records, headers, final_url, progress['combo_pages'])
                if not done and cfg['stop']['max_pages'] and progress['combo_pages'] >= cfg['stop']['max_pages']:
                    progress['truncated'].append(combo)
                    done = True
                progress['position'] = following
                if done:
                    progress.update(combo=combo + 1, started=False, position=None, combo_pages=0)
                self.save()
                if done:
                    break

    # ----- commit ---------------------------------------------------------------------------
    def commit(self, complete, stop_reason, update_latest):
        if self.cfg['strategy'] == 'paged_api' and self.cfg['store'] == 'jsonl':
            self.seal_open_shard()
        entries = sorted(self.state['shards'].values(), key=lambda s: s['staging_index'])
        if not entries:
            return None
        directory = self.shards_dir()
        shards = [{**{k: v for k, v in entry.items() if k != 'staging_index'},
                   'path': directory / str(entry['staging_index'])} for entry in entries]
        log_copy = None
        log_path = self.staging / 'page_log.jsonl'
        if log_path.exists() and log_path.stat().st_size:
            log_copy = self.staging / ('.page_log-' + uuid.uuid4().hex)
            shutil.copyfile(log_path, log_copy)
            shards.append({'path': log_copy, 'role': 'page_log', 'format': 'jsonl', 'complete': True,
                           '_method': 'copy', 'retrieved_at': now()})
        source = {**self.definition.get('source', {}), 'acquisition': {
            'strategy': self.cfg['strategy'], 'config_digest': self.config_digest,
            'content_digest': self.content_digest, 'config': self.cfg,
            'reader': self.cfg['reader'], 'complete': complete, 'stop_reason': stop_reason,
            'started_at': self.state['started_at'], 'completed_at': now(), 'requests': self.state['requests'],
            'skipped': self.state.get('skipped', {}),
            'truncated_combinations': self.state.get('paged', {}).get('truncated', [])}}
        self.guard(source)
        self.guard([{k: v for k, v in s.items() if k != 'path'} for s in shards])
        try:
            return self.store.import_shards(self.dataset, shards, source, complete=complete, stop_reason=stop_reason,
                                            update_latest=update_latest, method='link', trusted_hashes=True)
        finally:
            if log_copy is not None:
                log_copy.unlink(missing_ok=True)

    # ----- orchestration --------------------------------------------------------------------
    def allocation(self):
        if self.allowed is None:
            definitions = self.definitions if self.definitions is not None else [self.definition]
            table = budget_table(definitions, self.store, self.ledger, self.total, self.max_share)
            row = next((r for r in table['datasets'] if r['dataset'] == self.dataset), None)
            if row is None or 'error' in row:
                raise ValueError(f'{self.dataset}: no valid budget allocation ({row and row.get("error")})')
            self.allowed = row['allocated']
        return self.allowed

    def dry_run(self):
        credentials = [{'env': c['env'], 'present': bool(self.environ.get(c['env'])), 'optional': bool(c.get('optional'))}
                       for c in self.cfg['credentials']]
        if self.cfg['user_agent_env']:
            credentials.append({'env': self.cfg['user_agent_env'], 'present': bool(self.environ.get(self.cfg['user_agent_env'])),
                                'optional': False, 'kind': 'user_agent'})
        result = {'dataset': self.dataset, 'status': 'dry_run', 'strategy': self.cfg['strategy'],
                  'config_digest': self.config_digest, 'allocated_bytes': self.allocation(),
                  'usage': self.ledger.usage(self.dataset), 'desired_bytes': self.cfg['desired_bytes'],
                  'environment': credentials, 'staging_exists': self.staging.exists(),
                  'rate_limit': self.cfg['rate_limit']}
        if self.cfg['strategy'] == 'paged_api':
            combos = expand(self.cfg['parameters'], self.cfg['combinations'])
            result.update(combinations=len(combos), pages='unknown until fetched',
                          first_request=self.public(self.page_request(combos[0], self.cfg['pagination']['start'])[0]))
        else:
            planned = self.plan_requests()
            result.update(requests_planned=len(planned), first_requests=[self.public(s['url']) for s in planned[:3]])
        return result

    def run(self, dry_run=False):
        if dry_run:
            return self.dry_run()
        if not self.allow_network:
            raise ValueError('Full acquisition requires --allow-network')
        self.store.initialize(self.dataset)
        scratch = self.store.scratch_dir(self.dataset)
        result = {'dataset': self.dataset, 'strategy': self.cfg['strategy'], 'config_digest': self.config_digest,
                  'started_at': now()}
        with (scratch / '.acquire.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise RuntimeError(f'Another acquisition of {self.dataset} is running') from None
            try:
                return self._run_locked(scratch, result)
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def _run_locked(self, scratch, result):
        others = [p for p in scratch.glob('acquire-*') if p.is_dir() and p != self.staging]
        if self.restart:
            for path in others + ([self.staging] if self.staging.exists() else []):
                shutil.rmtree(path)
            others = []
        if others:
            raise ValueError(f'{self.dataset}: staging from a different acquisition config exists '
                             f'({", ".join(p.name for p in others)}); rerun with --restart to discard it')
        if self.staging.exists() and not self.resume:
            raise ValueError(f'{self.dataset}: an interrupted or partial acquisition exists; rerun with --resume or --restart')
        self._resolve_credentials()
        self.ledger.reconcile_dataset(self.store, self.dataset, holding_lock=True)
        result['allocated_bytes'] = self.allocation()
        result['used_before'] = self.ledger.usage(self.dataset)['used']
        self.staging.mkdir(parents=True, exist_ok=True)
        state_path = self.staging / 'state.json'
        self.state = read_json(state_path) if state_path.exists() else {
            'content_digest': self.content_digest, 'strategy': self.cfg['strategy'], 'started_at': now(),
            'shards': {}, 'next_index': 0, 'pages': 0, 'requests': 0, 'page_log_bytes': 0, 'open_shard': None}
        if self.state.get('content_digest') != self.content_digest:
            raise ValueError('Acquisition state belongs to a different configuration; use --restart')
        self.hasher = None
        stop, error = None, None
        try:
            if self.cfg['strategy'] == 'paged_api':
                self.run_paged()
            else:
                self.run_list()
        except BudgetExhausted as exhausted:
            stop, result['stop_detail'] = 'budget', str(exhausted)
        except DailyRequestLimit as limited:
            stop, result['stop_detail'] = 'daily_request_limit', str(limited)
        except _Stop as stopped:
            stop, result['stop_detail'] = stopped.reason, str(stopped)
        except Exception as failure:  # Recorded below; staging is kept for --resume.
            error = failure
        finally:
            self.state['requests'] += self.requests
            self.save()
        if stop is None and error is None and self.state.get('paged', {}).get('truncated'):
            stop = 'page_limit'
        result.update(requests=self.requests, downloaded_bytes=self.downloaded, new_shards=self.new_shards)
        if error is not None:
            result.update(status='failed', complete=False, error=str(error)[:1000], resumable=True)
        elif stop is None or stop in TERMINAL_STOPS:
            complete = stop is None
            ref = self.commit(complete, stop, update_latest=complete or self.cfg['publish_partial'])
            self.save()
            result.update(complete=complete, stop_reason=stop, artifact=ref,
                          status='complete' if complete else ('partial' if ref else 'blocked'),
                          shards=len(self.state['shards']))
            if complete or stop == 'page_limit':
                shutil.rmtree(self.staging)
                if complete:
                    self.ledger.mark_complete(self.dataset, self.content_digest)
            result['resumable'] = self.staging.exists()
        else:
            result.update(status='incomplete', complete=False, stop_reason=stop, artifact=None, resumable=True,
                          shards=len(self.state['shards']))
        self.ledger.reconcile_dataset(self.store, self.dataset, holding_lock=True)
        result['used_after'] = self.ledger.usage(self.dataset)['used']
        result['completed_at'] = now()
        self.guard(result)
        atomic_json(self.store.dataset_dir(self.dataset) / 'manifests' / 'acquisitions' / 'latest.json', result)
        atomic_json(self.store.runs_dir(self.dataset) / f'acquire-{uuid.uuid4().hex}.json', result)
        return result


def acquire(store, definitions, targets, *, total=None, max_share=DEFAULT_MAX_SHARE, allow_network=False,
            dry_run=False, max_shards=None, resume=False, restart=False, workers=4, sleep=time.sleep,
            opener=urlopen, environ=None):
    """Acquire ``targets`` (dataset IDs) with allocations computed over all ``definitions``.

    Several datasets run concurrently (``workers``) with one connection per host,
    so a giant dataset cannot delay or starve the others; each is capped by its share.
    """
    by_id = {d['id']: d for d in definitions}
    missing = [name for name in targets if name not in by_id]
    if missing:
        raise ValueError('Unknown datasets: ' + ', '.join(missing))
    if not dry_run and not allow_network:
        raise ValueError('Full acquisition requires --allow-network')
    ledger = Ledger(store.root)
    total = resolve_total(total)
    table = budget_table(definitions, store, ledger, total, max_share)
    allowed = {row['dataset']: row['allocated'] for row in table['datasets'] if 'error' not in row}
    locks = HostLocks()

    def one(name):
        try:
            engine = Acquisition(store, by_id[name], ledger=ledger, allowed=allowed.get(name), total=total,
                                 max_share=max_share, definitions=definitions, allow_network=allow_network,
                                 resume=resume, restart=restart, max_shards=max_shards, sleep=sleep,
                                 opener=opener, host_locks=locks, environ=environ)
            if not dry_run and allowed.get(name) is None:
                raise ValueError(next((r['error'] for r in table['datasets'] if r['dataset'] == name), 'no allocation'))
            return engine.run(dry_run=dry_run)
        except (ValueError, OSError, RuntimeError, KeyError, TypeError) as error:
            return {'dataset': name, 'status': 'failed', 'complete': False, 'error': str(error)[:1000]}

    order = sorted(targets, key=lambda n: (allowed.get(n, 0), n))
    if dry_run or workers <= 1 or len(order) <= 1:
        results = {name: one(name) for name in order}
    else:
        with ThreadPoolExecutor(max_workers=min(workers, len(order))) as pool:
            results = dict(zip(order, pool.map(one, order)))
    return [results[name] for name in targets]
