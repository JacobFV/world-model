"""``wm serve``: the read-only local API over a fictional temp index."""
import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from worldmodel.products.search import build as build_products_index
from worldmodel.products.server import Api, ApiError, make_server

from test_products import LEI, build_fixture


class ServerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)
        cls.index = build_fixture(root)
        cls.products = root / 'world_evidence' / 'products.sqlite'
        build_products_index(cls.index, cls.products)
        cls.server = make_server(cls.index, port=0, products_index=cls.products, data_root=root, catalog_root=root,
                                 log=False)
        cls.base = 'http://127.0.0.1:%d' % cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        cls.root = root

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.tmp.cleanup()

    def get(self, path, method='GET'):
        try:
            with urlopen(Request(self.base + path, method=method), timeout=30) as response:
                body = response.read()
                if method == 'HEAD':
                    return response.status, body
                kind = response.headers['Content-Type']
                return response.status, json.loads(body) if kind == 'application/json' else body.decode()
        except HTTPError as error:
            return error.code, json.loads(error.read() or b'{}')

    def test_bound_to_loopback_by_default(self):
        self.assertEqual(self.server.server_address[0], '127.0.0.1')

    def test_root_and_health(self):
        status, body = self.get('/')
        self.assertEqual(status, 200)
        self.assertTrue(any('/graph/paths' in e for e in body['endpoints']))
        status, body = self.get('/health')
        self.assertEqual((status, body['ok'], body['products_index']['available']), (200, True, True))

    def test_products_as_json(self):
        status, body = self.get('/dossier?q=' + quote('ofac:party:1'))
        self.assertEqual(status, 200)
        self.assertEqual(body['answer']['entity']['canonical_id'], LEI)
        self.assertIn('rights', body)
        status, body = self.get('/screen?q=' + quote('opensanctions:NK-sub2') + '&q=' + quote('Acme Petrol'))
        self.assertEqual(status, 200)
        self.assertEqual(body['answer']['results'][0]['status'], 'path_found')
        self.assertTrue(all(r['absence_of_a_path_is_not_clearance'] for r in body['answer']['results']))
        status, body = self.get('/place-brief?place=01999')
        self.assertEqual((status, body['answer']['storms']['events']), (200, 2))
        status, body = self.get('/search?q=acme')
        self.assertIn('INFERRED', body['match_basis'])

    def test_html_reports(self):
        status, body = self.get('/dossier.html?q=' + quote(LEI))
        self.assertEqual(status, 200)
        self.assertIn('What this does not establish', body)
        status, body = self.get('/place-brief.html?place=01999')
        self.assertIn('Storm events by type', body)

    def test_graph_endpoints_name_datasets(self):
        status, body = self.get('/graph/neighborhood?entity=' + quote('opensanctions:NK-sub') + '&hops=2')
        self.assertEqual(status, 200)
        self.assertTrue(body['edges'])
        self.assertTrue(all(e['from_dataset'] for e in body['edges']))
        status, body = self.get('/graph/paths?source=' + quote('opensanctions:NK-sub2') + '&target=' + quote(LEI)
                                + '&predicates=owns')
        self.assertEqual((status, body['length']), (200, 2))
        self.assertEqual({s['from_dataset'] for s in body['paths'][0]}, {'opensanctions_graph'})

    def test_no_write_paths(self):
        for method in ('POST', 'PUT', 'DELETE', 'PATCH'):
            status, body = self.get('/dossier?q=x:y', method=method)
            self.assertEqual(status, 405, method)
        status, body = self.get('/health', method='HEAD')
        self.assertEqual((status, body), (200, b''))

    def test_bounds_and_errors(self):
        self.assertEqual(self.get('/graph/neighborhood?entity=x:y&hops=9')[0], 400)
        self.assertEqual(self.get('/graph/neighborhood?entity=x:y&limit=100000')[0], 400)
        self.assertEqual(self.get('/graph/paths?source=x:y')[0], 400)
        self.assertEqual(self.get('/screen')[0], 400)
        self.assertEqual(self.get('/screen?' + '&'.join('q=x:%d' % i for i in range(60)))[0], 400)
        self.assertEqual(self.get('/nope')[0], 404)
        self.assertEqual(self.get('/dossier?q=' + 'x' * 600)[0], 400)

    def test_oversized_answer_is_refused_not_truncated(self):
        api = Api(self.index, products_index=self.products, data_root=self.root, catalog_root=self.root,
                  max_response_bytes=200)
        with self.assertRaises(ApiError) as caught:
            api.handle('/dossier', {'q': [LEI]})
        self.assertEqual(caught.exception.status, 413)


if __name__ == '__main__':
    unittest.main()
