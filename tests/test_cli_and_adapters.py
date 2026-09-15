import contextlib
import http.server
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store
from worldmodel.fetch import fetch

PROJECT = Path(__file__).resolve().parents[1]


class AdapterTests(unittest.TestCase):
    def test_census_mapping_keeps_suppression_and_leading_zero_geography(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog = root / 'catalog'
            (catalog / 'population').mkdir(parents=True)
            definition = {'id':'population','schema_version':1,'kind':'source',
                          'entrypoint':'worldmodel.transforms:normalize','dependencies':[],
                          'parameters':{'format':'census_json',
                            'constants':{'kind':'observation','metric':'population','unit':'people',
                                         'observed_at':'2025-01-01T00:00:00Z'},
                            'columns':{'value':'POP'}, 'dimensions':{'state':'state'},
                            'numeric_fields':['value'],'null_values':['-999']}}
            (catalog / 'population/dataset.json').write_text(json.dumps(definition))
            raw = root / 'response.json'
            raw.write_text('[["POP","state"],["217","01"],["-999","02"]]')
            store = Store(root / 'data')
            store.import_file('population', raw, {'publisher':'fixture'})
            ref = Runner(Catalog(catalog), store, PROJECT).run('population')
            records = list(store.records(ref))
            self.assertEqual(records[0]['dimensions']['state'], '01')
            self.assertEqual(records[0]['value'], 217)
            self.assertEqual(records[0]['evidence'][0]['locator'], '/1')
            self.assertIsNone(records[1]['value'])
            self.assertTrue(records[1]['missing_reason'])

    def test_fetch_requires_opt_in_and_rejects_bad_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp) / 'data')
            with self.assertRaisesRegex(ValueError, 'allow-network'):
                fetch(store, 'test', 'https://example.invalid/data', {}, allow_network=False)
            class Handler(http.server.BaseHTTPRequestHandler):
                def do_GET(self):
                    self.send_response(200)
                    self.send_header('Content-Length', '4')
                    self.end_headers()
                    self.wfile.write(b'data')
                def log_message(self, *args):
                    pass
            server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f'http://127.0.0.1:{server.server_port}/fixture'
                with self.assertRaisesRegex(ValueError, 'checksum'):
                    fetch(store, 'test', url, {}, allow_network=True, expected_sha256='0' * 64)
                self.assertFalse((store.raw_latest_path('test')).exists())
                with self.assertRaisesRegex(ValueError, 'limit'):
                    fetch(store, 'test', url, {}, allow_network=True, max_bytes=2)
                ref = fetch(store, 'test', url, {}, allow_network=True)
                self.assertEqual(store.artifact(ref)['bytes'], 4)
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_documented_demo_and_lineage_commands_work_offline(self):
        with tempfile.TemporaryDirectory() as tmp:
            def cli(*args):
                result = subprocess.run([sys.executable, '-m', 'worldmodel', '--data-root', tmp, *args],
                                        cwd=PROJECT, capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                return json.loads(result.stdout)
            result = cli('demo')
            self.assertEqual(result['graph']['records'], 7)
            ref = result['output']
            lineage = cli('lineage', f'{ref["dataset"]}/{ref["stage"]}@{ref["version"]}')
            self.assertEqual(len(lineage['versions']), 5)
            self.assertEqual(len(lineage['artifacts']), 2)
            self.assertEqual(cli('verify', 'world_graph')['verified'], True)
            self.assertEqual(len(cli('neighbors', 'org:acme')['assertions']), 2)
            self.assertEqual({r['dimensions']['country']: r['value'] for r in
                              cli('observations', 'rando_joes_happiness_index')}, {'AA': 65, 'BB': 50})


if __name__ == '__main__':
    unittest.main()
