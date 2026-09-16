"""Offline tests for full acquisition, the fair-share budget, sharded storage and raw readers."""
from contextlib import redirect_stdout
from copy import deepcopy
import gzip
import http.server
import io
import json
import os
from pathlib import Path
import re
import tempfile
import threading
import unittest
from urllib.parse import parse_qsl, urlsplit
import zipfile

from worldmodel.acquisition import acquire, normalize_config
from worldmodel.budget import BudgetExhausted, DailyRequestLimit, Ledger, allocate, parse_size, resolve_total, scan_bytes
from worldmodel.catalog import Catalog, validate_stages
from worldmodel.env import load_env_file, load_project_env, parse_env
from worldmodel.pipeline import Runner
from worldmodel.raw_readers import iter_rows
from worldmodel.store import Store
from worldmodel.util import atomic_json

PROJECT = Path(__file__).resolve().parents[1]
SECRET = 'SUPERSECRETVALUE123'
HEADER_SECRET = 'HEADERSECRET987654'


class Fixture:
    """Threaded local HTTP server with per-path route functions and a request log."""

    def __init__(self):
        self.routes, self.log = {}, []
        owner = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def handle_any(self):
                parts = urlsplit(self.path)
                length = int(self.headers.get('Content-Length') or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                query = dict(parse_qsl(parts.query, keep_blank_values=True))
                owner.log.append({'path': parts.path, 'query': query, 'headers': dict(self.headers), 'body': body})
                route = owner.routes.get(parts.path)
                if route is None:
                    return owner.send(self, 404, b'missing')
                route(self, query, body)

            do_GET = do_POST = handle_any

            def log_message(self, *args):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f'http://127.0.0.1:{self.server.server_port}'

    @staticmethod
    def send(handler, status, body, headers=None):
        handler.send_response(status)
        for name, value in (headers or {}).items():
            handler.send_header(name, value)
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def json(self, handler, payload, headers=None):
        self.send(handler, 200, json.dumps(payload).encode(), {'Content-Type': 'application/json', **(headers or {})})

    def static(self, path, content, fail_first_at=None):
        calls = {'n': 0}

        def route(handler, query, body):
            calls['n'] += 1
            start, status, headers = 0, 200, {'ETag': '"v1"'}
            match = re.match(r'bytes=(\d+)-', handler.headers.get('Range') or '')
            if match:
                start, status = int(match[1]), 206
                headers['Content-Range'] = f'bytes {start}-{len(content) - 1}/{len(content)}'
            chunk = content[start:]
            handler.send_response(status)
            for name, value in headers.items():
                handler.send_header(name, value)
            handler.send_header('Content-Length', str(len(chunk)))
            handler.end_headers()
            if fail_first_at is not None and calls['n'] == 1:
                handler.wfile.write(chunk[:fail_first_at])
                handler.wfile.flush()
                handler.close_connection = True
                return
            handler.wfile.write(chunk)
        self.routes[path] = route
        return calls

    def close(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()


class AcquisitionBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')
        self.http = Fixture()
        self.addCleanup(self.http.close)
        self.sleeps = []

    def definition(self, dataset='acq', **acquisition):
        block = {'desired_bytes': 10 * 1024 * 1024, 'rate_limit': {'requests_per_second': 10000},
                 'timeout_seconds': 5, 'retries': 3, 'backoff_seconds': 0, **acquisition}
        return {'id': dataset, 'schema_version': 1, 'kind': 'source', 'source': {'publisher': 'fixture'},
                'acquisition': block}

    def acquire(self, definitions, targets=None, **options):
        definitions = definitions if isinstance(definitions, list) else [definitions]
        options.setdefault('allow_network', True)
        return acquire(self.store, definitions, targets or [definitions[0]['id']], sleep=self.sleeps.append, **options)

    def rows(self, ref, **reader):
        receipt = self.store.artifact(ref)
        config = {**(receipt['source']['acquisition']['reader'] or {}), **reader}
        return list(iter_rows(self.store.raw_shards(ref), config))


class AllocationTests(unittest.TestCase):
    def test_water_filling_redistributes_and_caps_share(self):
        wants = lambda **d: {k: {'desired': v} for k, v in d.items()}
        self.assertEqual(allocate(100, wants(a=10, b=50, c=100, d=100), 0.25), {'a': 10, 'b': 30, 'c': 30, 'd': 30})
        self.assertEqual(allocate(90, wants(a=10, b=50, c=100), 1.0), {'a': 10, 'b': 40, 'c': 40})
        self.assertEqual(allocate(100, wants(a=100, b=100, c=100, d=100, e=100), 0.25), dict.fromkeys('abcde', 20))
        # max_share is exceeded only when leftover would otherwise go unused.
        self.assertEqual(allocate(100, wants(a=100), 0.25), {'a': 100})
        three = allocate(100, wants(a=100, b=100, c=100), 0.25)
        self.assertEqual(sum(three.values()), 100)
        self.assertLessEqual(max(three.values()) - min(three.values()), 1)
        self.assertEqual(allocate(50, wants(a=10, b=20), 0.25), {'a': 10, 'b': 20})

    def test_priority_minimums_and_oversubscribed_guarantees(self):
        self.assertEqual(allocate(100, {'a': {'desired': 100, 'priority': 1}, 'b': {'desired': 100, 'priority': 3}}, 1.0),
                         {'a': 25, 'b': 75})
        guaranteed = allocate(100, {'a': {'desired': 80, 'min': 60}, 'b': {'desired': 80}}, 0.25)
        self.assertGreaterEqual(guaranteed['a'], 60)
        self.assertEqual(sum(guaranteed.values()), 100)
        self.assertEqual(allocate(100, {'a': {'desired': 100, 'min': 80}, 'b': {'desired': 100, 'min': 80}}), {'a': 50, 'b': 50})
        self.assertEqual(allocate(0, {'a': {'desired': 5}}), {'a': 0})

    def test_bytes_held_beyond_a_shrunken_share_still_count_against_the_total(self):
        from worldmodel.budget import budget_table

        class StubLedger:
            held = {'a': 50}
            def usage(self, name):
                return {'used': self.held.get(name, 0), 'reserved': 0, 'scanned_at': 'now', 'complete_digest': None}

        block = lambda: {'acquisition': {'strategy': 'files', 'files': ['https://example.test/x'], 'desired_bytes': 60}}
        definitions = [{'id': name, **block()} for name in 'abc']
        table = budget_table(definitions, store=None, ledger=StubLedger(), total=100, max_share=0.25)
        rows = {row['dataset']: row for row in table['datasets']}
        # 'a' downloaded 50 while alone; later demand from b/c must not push potential usage past 100.
        self.assertEqual(rows['a']['allocated'], 50)
        self.assertEqual(rows['a']['remaining'], 0)
        self.assertEqual({rows['b']['allocated'], rows['c']['allocated']}, {25})
        self.assertLessEqual(sum(max(r['allocated'], r['used']) for r in rows.values()), 100)

    def test_sizes_and_total_resolution(self):
        self.assertEqual(parse_size('20GiB'), 20 * 1024 ** 3)
        self.assertEqual(parse_size('1.5 MB'), 1_500_000)
        self.assertEqual(parse_size(123), 123)
        self.assertEqual(parse_size('4096'), 4096)
        with self.assertRaises(ValueError):
            parse_size('twenty')
        self.assertEqual(resolve_total(None, {}), 100 * 1024 ** 3)
        self.assertEqual(resolve_total(None, {'WORLD_MODEL_DOWNLOAD_BUDGET': '2GiB'}), 2 * 1024 ** 3)
        self.assertEqual(resolve_total('1KiB', {'WORLD_MODEL_DOWNLOAD_BUDGET': '2GiB'}), 1024)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name) / 'data')
        self.ledger = Ledger(self.store.root)

    def test_reservations_daily_limits_and_hardlink_aware_reconcile(self):
        rid = self.ledger.reserve('a', 60, 100)
        with self.assertRaises(BudgetExhausted):
            self.ledger.reserve('a', 50, 100)
        self.ledger.consume(rid, 'a', 60)
        self.assertEqual(self.ledger.usage('a'), {**self.ledger.usage('a'), 'used': 60, 'reserved': 0})
        self.ledger.release(rid)
        with self.assertRaises(BudgetExhausted):
            self.ledger.reserve('a', 41, 100)
        self.ledger.rate_slot('host', None, 2)
        self.ledger.rate_slot('host', None, 2)
        with self.assertRaises(DailyRequestLimit):
            self.ledger.rate_slot('host', None, 2)
        raw = self.store.initialize('a') / 'artifacts' / 'raw' / 'x'
        raw.mkdir(parents=True)
        (raw / 'payload').write_bytes(b'z' * 1000)
        staging = self.store.scratch_dir('a') / 'acquire-abc'
        staging.mkdir()
        os.link(raw / 'payload', staging / 'shard')
        self.assertEqual(scan_bytes(self.store, 'a'), 1000)
        self.assertEqual(self.ledger.reconcile_dataset(self.store, 'a'), 1000)
        self.assertEqual(self.ledger.usage('a')['used'], 1000)


class DownloadTests(AcquisitionBase):
    def test_range_resume_after_interrupted_transfer(self):
        content = bytes(range(256)) * 400
        calls = self.http.static('/big.bin', content, fail_first_at=30000)
        definition = self.definition(strategy='files', files=[{'url': self.http.base + '/big.bin',
                                                                'sha256': __import__('hashlib').sha256(content).hexdigest()}])
        result = self.acquire(definition)[0]
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(calls['n'], 2)
        self.assertEqual(self.http.log[1]['headers'].get('Range'), 'bytes=30000-')
        self.assertEqual(self.http.log[1]['headers'].get('If-Range'), '"v1"')
        ref = result['artifact']
        receipt = self.store.artifact(ref)
        self.assertTrue(receipt['complete'])
        self.assertEqual(self.store.raw_shards(ref)[0]['path'].read_bytes(), content)
        self.assertEqual(self.store.latest_raw('acq'), ref)
        self.assertFalse(any(self.store.scratch_dir('acq').glob('acquire-*')))

    def test_retry_after_on_429_and_resume_across_runs(self):
        seen = {'n': 0}

        def limited(handler, query, body):
            seen['n'] += 1
            if seen['n'] == 1:
                return self.http.send(handler, 429, b'slow down', {'Retry-After': '1'})
            self.http.send(handler, 200, f'id,value\n{query["id"]},1\n'.encode())
        self.http.routes['/series.csv'] = limited
        definition = self.definition(strategy='url_list', url_template=self.http.base + '/series.csv?id={series}',
                                     parameters={'series': ['A', 'B', 'C']}, reader={'format': 'csv'})
        first = self.acquire(definition, max_shards=1)[0]
        self.assertEqual(first['status'], 'incomplete')
        self.assertEqual(first['stop_reason'], 'max_shards')
        self.assertIn(1.0, self.sleeps)
        self.assertFalse(self.store.raw_latest_path('acq').exists())
        blocked = self.acquire(definition)[0]
        self.assertEqual(blocked['status'], 'failed')
        self.assertIn('--resume', blocked['error'])
        before = len(self.http.log)
        final = self.acquire(definition, resume=True)[0]
        self.assertEqual(final['status'], 'complete', final)
        self.assertEqual([entry['query']['id'] for entry in self.http.log[before:]], ['B', 'C'])
        rows = self.rows(final['artifact'])
        self.assertEqual([(locator, row['id']) for locator, row in rows],
                         [('shard:0/line:2', 'A'), ('shard:1/line:2', 'B'), ('shard:2/line:2', 'C')])

    def test_budget_exhaustion_publishes_incomplete_artifact_and_resume_completes(self):
        for name in ('a', 'b', 'c'):
            self.http.static(f'/{name}.bin', name.encode() * 4000)
        definition = self.definition(strategy='files', files=[self.http.base + f'/{n}.bin' for n in 'abc'],
                                     desired_bytes=12000)
        partial = self.acquire(definition, total=10000)[0]
        self.assertEqual(partial['status'], 'partial', partial)
        self.assertFalse(partial['complete'])
        self.assertEqual(partial['stop_reason'], 'budget')
        receipt = self.store.artifact(partial['artifact'])
        self.assertFalse(receipt['complete'])
        self.assertEqual(receipt['stop_reason'], 'budget')
        self.assertEqual(receipt['shard_count'], 2)
        self.assertFalse(receipt['source']['acquisition']['complete'])
        self.assertEqual(self.store.latest_raw('acq'), partial['artifact'])
        self.assertEqual([p['index'] for p in self.store.raw_shards(partial['artifact'])], [0, 1])
        # Raising the budget hint does not invalidate resumable staging.
        definition['acquisition']['desired_bytes'] = 100000
        final = self.acquire(definition, total=100000, resume=True)[0]
        self.assertEqual(final['status'], 'complete', final)
        self.assertEqual(self.store.artifact(final['artifact'])['shard_count'], 3)
        self.assertEqual([e['path'] for e in self.http.log].count('/a.bin'), 1)
        # Shared shards are hardlinks: both artifacts together hold 12 KB of data, not 20 KB.
        inodes = {}
        for path in (self.store.dataset_dir('acq') / 'artifacts' / 'raw').glob('*/shards/*'):
            inodes[path.stat().st_ino] = path.stat().st_size
        self.assertEqual(sum(inodes.values()), 12000)
        self.assertEqual(Ledger(self.store.root).usage('acq')['complete_digest'] is not None, True)

    def test_gzip_transfer_enforces_decoded_budget(self):
        big = b'x' * 200000
        compressed = gzip.compress(big)
        self.http.routes['/gz.csv'] = lambda h, q, b: self.http.send(h, 200, compressed, {'Content-Encoding': 'gzip'})
        definition = self.definition(strategy='url_list', url_template=self.http.base + '/gz.csv?n={n}',
                                     parameters={'n': [1]}, desired_bytes=50000, reserve_chunk_bytes=4096)
        result = self.acquire(definition, total=50000)[0]
        self.assertLess(len(compressed), 50000)
        self.assertEqual(result['status'], 'blocked')
        self.assertEqual(result['stop_reason'], 'budget')
        ok = self.definition('ok', strategy='url_list', url_template=self.http.base + '/gz.csv?n={n}',
                             parameters={'n': [1]}, desired_bytes=400000)
        done = self.acquire(ok, total=400000)[0]
        self.assertEqual(done['status'], 'complete')
        self.assertEqual(self.store.raw_shards(done['artifact'])[0]['path'].read_bytes(), big)

    def test_credentials_are_injected_but_never_written(self):
        def page(handler, query, body):
            assert query.get('api_key') == SECRET and handler.headers.get('X-Api-Key') == HEADER_SECRET
            number = int(query.get('p', 1))
            following = f'{self.http.base}/secure?p={number + 1}&api_key={SECRET}' if number < 3 else None
            self.http.json(handler, {'results': [{'n': number}], 'next': following})
        self.http.routes['/secure'] = page
        definition = self.definition(strategy='paged_api', url_template=self.http.base + '/secure',
                                     pagination={'mode': 'next_url', 'next_url_path': ['next']},
                                     records_path=['results'],
                                     credentials=[{'env': 'FIXTURE_KEY', 'query': 'api_key'},
                                                  {'env': 'FIXTURE_HEADER', 'header': 'X-Api-Key'}])
        environ = {'FIXTURE_KEY': SECRET, 'FIXTURE_HEADER': HEADER_SECRET}
        missing = self.acquire(definition, environ={})[0]
        self.assertEqual(missing['status'], 'failed')
        self.assertIn('FIXTURE_KEY', missing['error'])
        result = self.acquire(definition, environ=environ, restart=True)[0]
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(len(self.http.log), 3)
        self.assertEqual([row['n'] for _, row in self.rows(result['artifact'], format='jsonl')], [1, 2, 3])
        # Echoed credentials in next links are stripped, then re-injected on every request.
        self.assertEqual([e['query'].get('api_key') for e in self.http.log], [SECRET] * 3)
        self.assertEqual([e['headers'].get('X-Api-Key') for e in self.http.log], [HEADER_SECRET] * 3)
        self.assert_no_secrets(SECRET, HEADER_SECRET)

    def assert_no_secrets(self, *secrets):
        for path in self.store.root.rglob('*'):
            if path.is_file():
                content = path.read_bytes()
                for secret in secrets:
                    self.assertNotIn(secret.encode(), content, path)

    def test_body_credentials_and_body_redaction(self):
        from worldmodel.fetch import public_body, public_url

        def bls(handler, query, body):
            if (body or {}).get('registrationkey') != SECRET:
                return self.http.send(handler, 403, b'bad key')
            self.http.json(handler, {'status': 'REQUEST_SUCCEEDED',
                                     'Results': {'series': [{'seriesID': body['seriesid'][0]}]}})
        self.http.routes['/bls'] = bls
        definition = self.definition(strategy='url_list', url_template=self.http.base + '/bls', method='POST',
                                     body_template={'seriesid': ['{series}'], 'startyear': '2020'},
                                     parameters={'series': ['CUUR0000SA0', 'LNS14000000']},
                                     credentials=[{'env': 'BLS_API_KEY', 'body': 'registrationkey'}],
                                     reader={'format': 'json', 'records_path': ['Results', 'series']})
        result = self.acquire(definition, environ={'BLS_API_KEY': SECRET})[0]
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual([r['seriesID'] for _, r in self.rows(result['artifact'])], ['CUUR0000SA0', 'LNS14000000'])
        request = self.store.raw_shards(result['artifact'])[0]['request']
        self.assertEqual(request['body'], {'seriesid': ['CUUR0000SA0'], 'startyear': '2020'})
        self.assert_no_secrets(SECRET)
        with self.assertRaisesRegex(ValueError, 'body_template'):
            normalize_config({'strategy': 'files', 'desired_bytes': 1, 'files': ['http://x/'],
                              'credentials': [{'env': 'BLS_API_KEY', 'body': 'registrationkey'}]})
        self.assertEqual(public_body({'registrationkey': 's', 'nested': [{'UserID': 'u', 'Key': 'k', 'year': 2020}]}),
                         {'registrationkey': '[REDACTED]', 'nested': [{'UserID': '[REDACTED]', 'Key': '[REDACTED]', 'year': 2020}]})
        redacted = public_url('https://x/api?UserID=u&key=k&registrationkey=r&subscription-key=s&x-api-key=x&year=1')
        self.assertEqual(redacted.count('%5BREDACTED%5D'), 5)
        self.assertIn('year=1', redacted)


class PaginationTests(AcquisitionBase):
    def test_offset_and_page_modes(self):
        records = [{'id': i} for i in range(10)]
        self.http.routes['/offset'] = lambda h, q, b: self.http.json(
            h, {'results': records[int(q['offset']):int(q['offset']) + int(q['limit'])], 'total': 10})
        offset = self.definition('offset_ds', strategy='paged_api', url_template=self.http.base + '/offset',
                                 pagination={'mode': 'offset', 'size_param': 'limit', 'page_size': 4},
                                 records_path=['results'], stop={'total_path': ['total']})
        result = self.acquire(offset)[0]
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual([e['query']['offset'] for e in self.http.log], ['0', '4', '8'])
        self.assertEqual([r['id'] for _, r in self.rows(result['artifact'], format='jsonl')], list(range(10)))
        roles = [s['role'] for s in self.store.raw_shards(result['artifact'])]
        self.assertEqual(roles, ['data', 'page_log'])

        self.http.log.clear()
        self.http.routes['/pages'] = lambda h, q, b: self.http.json(
            h, {'data': [{'page': int(q['page'])}] * 2, 'meta': {'pages': 3}})
        paged = self.definition('page_ds', strategy='paged_api', url_template=self.http.base + '/pages',
                                pagination={'mode': 'page'}, records_path=['data'],
                                stop={'total_pages_path': ['meta', 'pages'], 'short_page': False},
                                store='pages', reader={'format': 'json', 'records_path': ['data']})
        result = self.acquire(paged)[0]
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual([e['query']['page'] for e in self.http.log], ['1', '2', '3'])
        self.assertEqual(self.store.artifact(result['artifact'])['shard_count'], 3)
        self.assertEqual(self.rows(result['artifact'])[-1], ('shard:2/record:1', {'page': 3}))

    def test_cursor_link_header_body_grid_rollover_and_page_limit(self):
        cursors = {None: ('c2', [1, 2]), 'c2': ('c3', [3, 4]), 'c3': (None, [5])}

        def cursor(handler, query, body):
            following, values = cursors[query.get('cursor')]
            self.http.json(handler, {'items': [{'v': v} for v in values], 'meta': {'next': following}})
        self.http.routes['/cursor'] = cursor
        definition = self.definition('cursor_ds', strategy='paged_api', url_template=self.http.base + '/cursor',
                                     pagination={'mode': 'cursor', 'cursor_path': ['meta', 'next']},
                                     records_path=['items'], rollover_bytes=20)
        result = self.acquire(definition)[0]
        self.assertEqual(result['status'], 'complete', result)
        shards = self.store.raw_shards(result['artifact'], role='data')
        self.assertEqual(len(shards), 3)
        self.assertEqual([(l, r['v']) for l, r in self.rows(result['artifact'], format='jsonl')],
                         [('shard:0/line:1', 1), ('shard:0/line:2', 2), ('shard:1/line:1', 3),
                          ('shard:1/line:2', 4), ('shard:2/line:1', 5)])

        def linked(handler, query, body):
            number = int(query.get('p', 1))
            headers = {'Link': f'<{self.http.base}/linked?p={number + 1}>; rel="next"'} if number < 2 else {}
            self.http.json(handler, [{'p': number}], headers)
        self.http.routes['/linked'] = linked
        link = self.definition('link_ds', strategy='paged_api', url_template=self.http.base + '/linked',
                               pagination={'mode': 'next_url'}, store='pages')
        result = self.acquire(link)[0]
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual(self.store.artifact(result['artifact'])['shard_count'], 2)

        def search(handler, query, body):
            page = body['page']
            self.http.json(handler, {'results': [{'year': body['filters']['year'], 'page': page}],
                                     'page_metadata': {'hasNext': page < 2}})
        self.http.routes['/search'] = search
        grid = self.definition('grid_ds', strategy='paged_api', url_template=self.http.base + '/search', method='POST',
                               body_template={'filters': {'year': '{year}'}, 'limit': 1},
                               parameters={'year': {'range': [2023, 2024]}},
                               pagination={'mode': 'page'}, records_path=['results'],
                               stop={'has_more_path': ['page_metadata', 'hasNext']})
        self.http.log.clear()
        result = self.acquire(grid)[0]
        self.assertEqual(result['status'], 'complete', result)
        self.assertEqual([(e['body']['filters']['year'], e['body']['page']) for e in self.http.log],
                         [(2023, 1), (2023, 2), (2024, 1), (2024, 2)])

        limited = deepcopy(grid)
        limited['id'] = 'limited_ds'
        limited['acquisition']['stop']['max_pages'] = 1
        result = self.acquire(limited)[0]
        self.assertEqual((result['status'], result['stop_reason']), ('partial', 'page_limit'))
        self.assertFalse(self.store.artifact(result['artifact'])['complete'])
        self.assertEqual(len(self.rows(result['artifact'], format='jsonl')), 2)

    def test_acquire_all_runs_datasets_concurrently_within_shares(self):
        self.http.routes['/shared.csv'] = lambda h, q, b: self.http.send(h, 200, f'id\n{q["id"]}\n'.encode())
        definitions = [self.definition(name, strategy='url_list', url_template=self.http.base + '/shared.csv?id={i}',
                                       parameters={'i': list(range(4))}, desired_bytes=10 ** 6, reader={'format': 'csv'})
                       for name in ('first', 'second', 'third')]
        results = self.acquire(definitions, ['first', 'second', 'third'], workers=3, total=3 * 10 ** 6)
        self.assertEqual([r['status'] for r in results], ['complete'] * 3)
        self.assertTrue(all(r['allocated_bytes'] == 10 ** 6 for r in results))
        self.assertEqual(len(self.http.log), 12)

    def test_config_validation_is_strict(self):
        with self.assertRaisesRegex(ValueError, 'Unknown acquisition keys'):
            normalize_config({'strategy': 'files', 'desired_bytes': 1, 'files': ['http://x/'], 'typo': 1})
        with self.assertRaisesRegex(ValueError, 'cursor_path'):
            normalize_config({'strategy': 'paged_api', 'desired_bytes': 1, 'url_template': 'http://x/',
                              'pagination': {'mode': 'cursor'}})
        cfg = normalize_config({'strategy': 'url_list', 'desired_bytes': 1, 'url_template': 'http://x/{a}',
                                'parameters': {'a': [1, 2]}})
        self.assertEqual(cfg['rate_limit'], {'requests_per_second': 1.0, 'requests_per_day': None, 'key': None})
        with self.assertRaises(ValueError):
            self.acquire(self.definition(strategy='files', files=['http://127.0.0.1:1/']), allow_network=False)


class StorageAndReaderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def shard_files(self):
        first, second = self.root / 'one.csv', self.root / 'two.csv'
        first.write_text('value\n1\n2\n')
        second.write_text('value\n3\n')
        return [{'path': first, 'url': 'http://x/one', 'retrieved_at': 't'}, {'path': second, 'url': 'http://x/two'}]

    def test_sharded_artifact_verification_modes_and_link_import(self):
        ref = self.store.import_shards('shardy', self.shard_files(), {'publisher': 'fixture'}, complete=True, method='link')
        receipt = self.store.artifact(ref)
        self.assertEqual((receipt['layout'], receipt['shard_count'], receipt['bytes']), ('shards', 2, 18))
        self.assertEqual(os.stat(self.root / 'one.csv').st_ino, os.stat(self.store.raw_shards(ref)[0]['path']).st_ino)
        index = json.loads((self.store.dataset_dir('shardy') / 'manifests' / 'raw' / (ref['artifact'] + '.json')).read_text())
        self.assertEqual(index['shards_summary']['count'], 2)
        shard = self.store.raw_shards(ref)[1]['path']
        os.unlink(shard)
        shard.write_text('value\n4\n')  # Same size, different bytes.
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.store.artifact(ref)
        self.store.artifact(ref, verify='size')
        self.assertEqual(Store(self.store.root, raw_verify='size').artifact(ref)['artifact'], ref['artifact'])
        shard.write_text('value\n44\n')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            self.store.artifact(ref, verify='size')
        with self.assertRaisesRegex(ValueError, 'stop_reason'):
            self.store.import_shards('shardy', self.shard_files(), {}, complete=False)
        moved = self.root / 'moved.txt'
        moved.write_text('payload')
        single = self.store.import_file('shardy', moved, {}, method='move')
        self.assertFalse(moved.exists())
        self.assertEqual(self.store.raw_shards(single)[0]['path'].read_text(), 'payload')

    def test_streaming_readers_zip_gzip_json_and_locators(self):
        archive_path = self.root / 'bundle.zip'
        with zipfile.ZipFile(archive_path, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('data/a.csv', 'name,note\nx,"multi\nline"\ny,plain\n')
            archive.writestr('b.txt', 'name|note\nz|pipe\n')
            archive.writestr('readme.md', 'ignore me')
        gz_path = self.root / 'c.csv.gz'
        gz_path.write_bytes(gzip.compress(b'name,note\nw,gz\n'))
        shards = [{'index': 0, 'path': archive_path}, {'index': 1, 'path': gz_path}]
        rows = list(iter_rows(shards, {'format': 'csv', 'members': ['*.csv']}))
        self.assertEqual([(l, r['name']) for l, r in rows],
                         [('shard:0/member:data/a.csv/line:2', 'x'), ('shard:0/member:data/a.csv/line:4', 'y'),
                          ('shard:1/line:2', 'w')])
        self.assertEqual(rows[0][1]['note'], 'multi\nline')
        piped = list(iter_rows([shards[0]], {'format': 'psv', 'members': ['b.txt']}))
        self.assertEqual(piped, [('shard:0/member:b.txt/line:2', {'name': 'z', 'note': 'pipe'})])
        census = self.root / 'census.json'
        census.write_text(json.dumps([['NAME', 'POP'], ['A', '1'], ['B', '2']]))
        self.assertEqual(list(iter_rows([{'index': 0, 'path': census}], {'format': 'json', 'table_header': True}))[1],
                         ('shard:0/record:2', {'NAME': 'B', 'POP': '2'}))
        bad = self.root / 'bad.csv'
        bad.write_text('a,b\n1\n')
        with self.assertRaisesRegex(ValueError, 'line:2'):
            list(iter_rows([{'index': 0, 'path': bad}], {'format': 'csv'}))
        self.assertEqual(list(iter_rows([{'index': 0, 'path': bad}], {'format': 'csv', 'strict': False}))[0][1],
                         {'a': '1', 'b': None})

    def test_stage_limits_and_gzip_records_from_sharded_raw(self):
        catalog_root = self.root / 'catalog'
        dataset = catalog_root / 'full_source'
        dataset.mkdir(parents=True)
        stage = {'id': 'parsed', 'entrypoint': 'pipeline.py:parse', 'depends_on': [],
                 'schema': {'format': 'jsonl', 'compression': 'gzip', 'required': {'value': 'integer'}},
                 'validation': {'allow_empty': False, 'max_rows': 2_000_000_000}, 'cache': 'content', 'retention': 'retain'}
        definition = {'id': 'full_source', 'schema_version': 2, 'kind': 'source', 'dependencies': [],
                      'output_stage': 'parsed', 'stages': [stage]}
        validate_stages(definition)
        for change in ({'validation': {'max_rows': 2_000_000_001}}, {'schema': {'format': 'jsonl', 'compression': 'zstd'}}):
            with self.assertRaises(ValueError):
                validate_stages({**definition, 'stages': [{**stage, **change}]})
        atomic_json(dataset / 'dataset.json', definition)
        (dataset / 'pipeline.py').write_text('''
def parse(context):
    coverage = context.raw_coverage()
    for locator, row in context.raw_rows(format='csv'):
        yield {'value': int(row['value']), 'locator': locator, 'complete': coverage['complete']}
''')
        raw = self.store.import_shards('full_source', self.shard_files(), {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(catalog_root), self.store, PROJECT).run('full_source')
        directory = self.store.version_dir(ref)
        self.assertTrue((directory / 'records.jsonl.gz').exists())
        self.assertFalse((directory / 'records.jsonl').exists())
        rows = list(self.store.records(ref))
        self.assertEqual([(r['value'], r['locator']) for r in rows],
                         [(1, 'shard:0/line:2'), (2, 'shard:0/line:3'), (3, 'shard:1/line:2')])
        self.assertTrue(all(r['complete'] for r in rows))
        self.assertEqual(self.store.manifest(ref)['raw_inputs'], [raw])
        again = Runner(Catalog(catalog_root), self.store, PROJECT).run('full_source')
        self.assertEqual(again, ref)


class EnvironmentAndCliTests(AcquisitionBase):
    def test_env_loader_does_not_override(self):
        self.assertEqual(parse_env('# c\nexport A=1\nB="two words"\nC=three # note\nbad line\nD=\'x#y\'\n'
                                   'SEC_USER_AGENT="Jane Doe jane@example.com"\n'),
                         {'A': '1', 'B': 'two words', 'C': 'three', 'D': 'x#y',
                          'SEC_USER_AGENT': 'Jane Doe jane@example.com'})
        env_file = self.root / '.env'
        env_file.write_text('KEEP=file\nNEW=value\n')
        extra = self.root / 'extra.env'
        extra.write_text('EXTRA=1\nNEW=other\n')
        environ = {'KEEP': 'already', 'WORLD_MODEL_ENV_FILE': str(extra)}
        loaded = load_project_env(self.root, environ)
        self.assertEqual(sorted(loaded), ['EXTRA', 'NEW'])
        self.assertEqual((environ['KEEP'], environ['NEW'], environ['EXTRA']), ('already', 'value', '1'))
        self.assertEqual(load_project_env(self.root, {'WORLD_MODEL_NO_DOTENV': '1'}), [])
        self.assertEqual(load_env_file(self.root / 'missing.env', {}), [])

    def test_budget_and_dry_run_commands(self):
        from worldmodel.cli import main
        catalog = self.root / 'catalog'
        for name, desired in (('big', 10 ** 9), ('small', 1000)):
            definition = self.definition(name, strategy='files', files=['http://127.0.0.1:1/x'], desired_bytes=desired)
            atomic_json(catalog / name / 'dataset.json', definition)
        atomic_json(catalog / 'plain' / 'dataset.json', {'id': 'plain', 'schema_version': 1, 'kind': 'source'})
        common = ['--data-root', str(self.store.root), '--catalog-root', str(catalog)]
        output = io.StringIO()
        with redirect_stdout(output):
            self.assertEqual(main(common + ['budget', '--budget', '100KiB']), 0)
        table = json.loads(output.getvalue())
        rows = {row['dataset']: row for row in table['datasets']}
        self.assertEqual((rows['small']['allocated'], rows['big']['allocated']), (1000, 102400 - 1000))
        self.assertEqual(table['totals']['allocated'], 102400)
        output = io.StringIO()
        with redirect_stdout(output), redirect_stdout(output):
            self.assertEqual(main(common + ['acquire', 'small', '--dry-run']), 0)
        plan = json.loads(output.getvalue())
        self.assertEqual((plan['status'], plan['requests_planned'], plan['allocated_bytes']), ('dry_run', 1, 1000))
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(common + ['budget', 'reconcile']), 0)

    def test_fred_cpi_worked_example_end_to_end(self):
        definition = Catalog(PROJECT / 'data').get('fred_cpi')
        block = definition['acquisition']
        self.assertEqual(block['strategy'], 'url_list')
        block['url_template'] = block['url_template'].replace('https://api.stlouisfed.org', self.http.base)
        block['rate_limit'] = {'requests_per_second': 10000}
        # The declaration reads its key from the environment; the fixture accepts any value
        # but asserts one was actually sent, and that it is redacted in recorded metadata.
        self.assertEqual([c['env'] for c in block['credentials']], ['FRED_API_KEY'])
        os.environ['FRED_API_KEY'] = SECRET
        self.addCleanup(os.environ.pop, 'FRED_API_KEY', None)

        def observations(handler, query, body):
            self.assertEqual(query['api_key'], SECRET)
            series = query['series_id']
            payload = {'observations': [
                {'realtime_start': '1947-03-15', 'realtime_end': '9999-12-31',
                 'date': '1947-01-01', 'value': '21.48', 'series_id': series},
                {'realtime_start': '1947-04-15', 'realtime_end': '9999-12-31',
                 'date': '1947-02-01', 'value': '21.62', 'series_id': series}]}
            self.http.json(handler, payload)
        self.http.routes['/fred/series/observations'] = observations
        result = self.acquire([definition])[0]
        self.assertEqual(result['status'], 'complete', result)
        shards = self.store.raw_shards(result['artifact'])
        self.assertEqual([s['request']['params']['series_id'] for s in shards],
                         ['CPIAUCSL', 'CPILFESL', 'PCEPI'])
        self.assertNotIn(SECRET, json.dumps(shards, default=str))
        rows = self.rows(result['artifact'])
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[2][1]['series_id'], 'CPILFESL')
        self.assertEqual((rows[2][1]['date'], rows[2][1]['value']), ('1947-01-01', '21.48'))


if __name__ == '__main__':
    unittest.main()
