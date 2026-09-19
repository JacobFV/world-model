"""``wm serve``: the products and bounded graph queries as a local, read-only JSON API.

Standard library only (``http.server``). Bound to 127.0.0.1 unless told otherwise. There are no
write paths: only GET and HEAD are served, every other method is refused with 405, and the index
is opened read-only per request. Every parameter is bounded, and a response larger than
``max_response_bytes`` is refused rather than truncated silently.

    GET /                         endpoint list and the index being served
    GET /health                   index provenance and whether the products index is present
    GET /search?q=NAME            asserted clusters whose label or alias matches (INFERRED)
    GET /dossier?q=ID-OR-NAME     wm dossier            (&pick=N; /dossier.html for the report)
    GET /screen?q=A&q=B           wm screen             (&hops=N&groups=ownership,control)
    GET /place-brief?place=FIPS   wm place-brief        (/place-brief.html for the report)
    GET /graph/neighborhood?entity=ID&hops=2&limit=200&predicates=a,b&direction=both&resolved=1
    GET /graph/paths?source=ID&target=ID&max_hops=4&limit=10&predicates=a,b&resolved=1
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import ipaddress
import json
from pathlib import Path
import sys
import threading
import traceback
from urllib.parse import parse_qs, urlsplit

from .base import ProductError, connect, default_products_index, describe_edges, index_info, retrying

BOUNDS = {'hops': (1, 3), 'limit': (1, 2000), 'max_hops': (1, 6), 'paths_limit': (1, 50), 'screen_hops': (1, 4),
          'screen_entries': (1, 50), 'pick': (0, 49)}
MAX_RESPONSE_BYTES = 16 * 1024 * 1024
MAX_CONCURRENT = 4


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status


def _int(params, name, default, bound):
    raw = params.get(name, [None])[-1]
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        raise ApiError(400, '%s must be an integer' % name)
    low, high = BOUNDS[bound]
    if not low <= value <= high:
        raise ApiError(400, '%s must be in %d..%d' % (name, low, high))
    return value


def _one(params, name, required=True):
    values = params.get(name) or []
    if not values or not values[-1].strip():
        if required:
            raise ApiError(400, 'missing parameter: %s' % name)
        return None
    if len(values[-1]) > 500:
        raise ApiError(400, '%s is longer than 500 characters' % name)
    return values[-1].strip()


def _list(params, name):
    value = _one(params, name, required=False)
    return [v.strip() for v in value.split(',') if v.strip()] if value else None


def _flag(params, name, default):
    value = _one(params, name, required=False)
    return default if value is None else value.lower() in ('1', 'true', 'yes')


class Api:
    """The request router; independent of the HTTP plumbing so it can be tested directly."""

    def __init__(self, index, *, products_index=None, data_root=None, catalog_root=None,
                 max_response_bytes=MAX_RESPONSE_BYTES):
        self.index = Path(index)
        self.products_index = Path(products_index or default_products_index(self.index))
        self.data_root, self.catalog_root = data_root, catalog_root
        self.max_response_bytes = max_response_bytes
        connection = connect(self.index)
        try:
            self.info = index_info(connection, self.index)
        finally:
            connection.close()

    def _common(self):
        return {'index': self.index, 'products_index': self.products_index, 'data_root': self.data_root,
                'catalog_root': self.catalog_root}

    def handle(self, path, params):
        """(status, content type, body bytes) for one GET."""
        route = {'/': self.root, '/health': self.health, '/search': self.search, '/dossier': self.dossier,
                 '/dossier.html': self.dossier_html, '/screen': self.screen, '/place-brief': self.place,
                 '/place-brief.html': self.place_html, '/graph/neighborhood': self.neighborhood,
                 '/graph/paths': self.paths}.get(path.rstrip('/') or '/')
        if route is None:
            raise ApiError(404, 'no such endpoint: %s' % path)
        try:
            result = route(params)
        except ApiError:
            raise
        except ProductError as error:
            raise ApiError(400, str(error))
        except ValueError as error:
            raise ApiError(400, str(error))
        if isinstance(result, str):
            body, kind = result.encode('utf-8'), 'text/html; charset=utf-8'
        else:
            body, kind = json.dumps(result, ensure_ascii=False, allow_nan=False).encode('utf-8'), 'application/json'
        if len(body) > self.max_response_bytes:
            raise ApiError(413, 'the answer is %d bytes, over the %d-byte bound; narrow the query (fewer hops, a lower '
                                'limit, or specific predicates)' % (len(body), self.max_response_bytes))
        return 200, kind, body

    def root(self, params):
        return {'service': 'worldmodel evidence products (read-only)', 'index': str(self.index),
                'endpoints': [line.strip() for line in __doc__.splitlines() if line.strip().startswith('GET ')],
                'writes': 'none: GET and HEAD only; the index is opened read-only'}

    def health(self, params):
        from .search import open_products_index, products_index_info
        products = open_products_index(self.products_index, self.info)
        try:
            return {'ok': True, 'index': {k: v for k, v in self.info.items() if k != 'pinned_versions'},
                    'products_index': products_index_info(products)}
        finally:
            if products is not None:
                products.close()

    def search(self, params):
        from .search import open_products_index, search
        connection = connect(self.index)
        products = open_products_index(self.products_index, self.info)
        try:
            found = retrying(search, connection, products, _one(params, 'q'), limit=_int(params, 'limit', 10, 'limit'))
            return {**found, 'match_basis': 'published label or alias text - INFERRED, not asserted',
                    'what_this_does_not_establish': ['A text match says nothing about identity; the candidates are '
                                                     'separate asserted clusters.']}
        finally:
            connection.close()
            if products is not None:
                products.close()

    def dossier(self, params):
        from .dossier import dossier
        return dossier(_one(params, 'q'), pick=_int(params, 'pick', 0, 'pick'), **self._common())

    def dossier_html(self, params):
        from .html import render_dossier
        return render_dossier(self.dossier(params))

    def screen(self, params):
        from .screen import DEFAULT_GROUPS, screen
        entries = [v for v in params.get('q', []) if v.strip()]
        low, high = BOUNDS['screen_entries']
        if not low <= len(entries) <= high:
            raise ApiError(400, 'pass 1..%d q parameters' % high)
        if any(len(e) > 500 for e in entries):
            raise ApiError(400, 'q is longer than 500 characters')
        return screen(entries, hops=_int(params, 'hops', 3, 'screen_hops'),
                      groups=_list(params, 'groups') or DEFAULT_GROUPS, limit=20000, **self._common())

    def place(self, params):
        from .place import place_brief
        return place_brief(_one(params, 'place'), **self._common())

    def place_html(self, params):
        from .html import render_place
        return render_place(self.place(params))

    def neighborhood(self, params):
        from ..graph import Graph
        entity = _one(params, 'entity')
        direction = _one(params, 'direction', required=False) or 'both'
        found = retrying(Graph(self.index).neighborhood, entity, hops=_int(params, 'hops', 1, 'hops'),
                         limit=_int(params, 'limit', 200, 'limit'), predicates=_list(params, 'predicates'),
                         direction=direction, resolved=_flag(params, 'resolved', True),
                         valid_at=_one(params, 'valid_at', required=False),
                         known_at=_one(params, 'known_at', required=False))
        connection = connect(self.index)
        try:
            found['edges'] = [{**edge, 'from_dataset': d['from_dataset'], 'record_id': d['record_id']}
                              for edge, d in zip(found['edges'], describe_edges(connection, found['edges']))]
        finally:
            connection.close()
        return {**found, 'identity': 'asserted clusters' if found['resolved'] else 'raw entity IDs',
                'what_this_does_not_establish': ['An edge states only its predicate, as published by its dataset; a '
                                                 'truncated neighbourhood is a sample, not the whole.']}

    def paths(self, params):
        from ..graph import Graph
        found = retrying(Graph(self.index).paths, _one(params, 'source'), _one(params, 'target'),
                         max_hops=_int(params, 'max_hops', 4, 'max_hops'), limit=_int(params, 'limit', 10, 'paths_limit'),
                         predicates=_list(params, 'predicates'), resolved=_flag(params, 'resolved', True),
                         valid_at=_one(params, 'valid_at', required=False),
                         known_at=_one(params, 'known_at', required=False), max_expansions=100000)
        connection = connect(self.index)
        try:
            for path in found['paths']:
                for step, source in zip(path, describe_edges(connection, [dict(s, subject=s['from'], object=s['to'])
                                                                           for s in path])):
                    step.update({'from_dataset': source['from_dataset'], 'record_id': source['record_id']})
        finally:
            connection.close()
        return {**found, 'what_this_does_not_establish': [
            'No path within the bound is not evidence that none exists; truncated=true means the search stopped early.']}


def make_handler(api, *, gate, log=True):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'worldmodel-products'
        protocol_version = 'HTTP/1.1'

        def _send(self, status, kind, body, head=False):
            self.send_response(status)
            self.send_header('Content-Type', kind)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            if kind.startswith('text/html'):
                self.send_header('Content-Security-Policy', "default-src 'none'; style-src 'unsafe-inline'; "
                                                            "script-src 'unsafe-inline'; img-src data:")
            self.end_headers()
            if not head:
                self.wfile.write(body)

        def _error(self, status, message, head=False):
            body = json.dumps({'error': message, 'status': status}).encode('utf-8')
            self._send(status, 'application/json', body, head)

        def _get(self, head=False):
            parts = urlsplit(self.path)
            if len(self.path) > 4096:
                return self._error(414, 'request line too long', head)
            if not gate.acquire(timeout=30):
                return self._error(503, 'busy: %d requests already running' % MAX_CONCURRENT, head)
            try:
                status, kind, body = api.handle(parts.path, parse_qs(parts.query, keep_blank_values=False))
                self._send(status, kind, body, head)
            except ApiError as error:
                self._error(error.status, str(error), head)
            except Exception as error:  # the service must not die on one bad request
                traceback.print_exc(file=sys.stderr)
                self._error(500, '%s: %s' % (type(error).__name__, error), head)
            finally:
                gate.release()

        def do_GET(self):
            self._get()

        def do_HEAD(self):
            self._get(head=True)

        def _refuse(self):
            self._error(405, 'read-only service: only GET and HEAD are served')

        do_POST = do_PUT = do_PATCH = do_DELETE = do_OPTIONS = _refuse

        def log_message(self, format, *args):
            if log:
                print('%s - %s' % (self.address_string(), format % args), file=sys.stderr, flush=True)

    return Handler


def make_server(index, *, host='127.0.0.1', port=8765, log=True, **options):
    api = Api(index, **options)
    server = ThreadingHTTPServer((host, port), make_handler(api, gate=threading.BoundedSemaphore(MAX_CONCURRENT),
                                                            log=log))
    server.daemon_threads = True
    return server


def serve(index, *, host='127.0.0.1', port=8765, **options):
    """Serve until interrupted; returns a summary when stopped."""
    try:
        loopback = ipaddress.ip_address('127.0.0.1' if host == 'localhost' else host).is_loopback
    except ValueError:
        loopback = False
    server = make_server(index, host=host, port=port, **options)
    print(json.dumps({'serving': 'http://%s:%d/' % (host, server.server_address[1]), 'index': str(index),
                      'bound_to_loopback': loopback,
                      'warning': None if loopback else 'Bound to a non-loopback address: anyone who can reach it can '
                                                       'read the index, including non-commercial-only and '
                                                       'redistribution-restricted evidence.'}),
          file=sys.stderr, flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return {'stopped': True, 'host': host, 'port': server.server_address[1]}
