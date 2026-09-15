import http.server
import io
import json
import os
from unittest.mock import patch
from pathlib import Path
import tempfile
import threading
import unittest
import zipfile

from worldmodel.sampling import sample_dataset, extract_rows
from worldmodel.store import Store


class SamplingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'data')
        self.payload = b'country,value\nAA,1\nBB,2\nCC,3\n'
        owner = self
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                owner.received_headers = dict(self.headers)
                self.send_response(200)
                self.send_header('Content-Length', str(len(owner.payload)))
                self.end_headers()
                self.wfile.write(owner.payload)
            def log_message(self, *args):
                pass
        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.definition = {'id':'sample_test','kind':'source','source':{'publisher':'fixture'},
            'sampling':{'strategy':'download','format':'csv','max_rows':2,'max_sample_bytes':1000,
                        'max_download_bytes':1000,'max_uncompressed_bytes':1000,
                        'timeout_seconds':5,'criteria':'first two fixture rows',
                        'url':f'http://127.0.0.1:{self.server.server_port}/data.csv'}}

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()
        self.tmp.cleanup()

    def test_whole_file_is_discarded_sample_is_provenanced_and_does_not_replace_full_raw(self):
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'sampled')
        self.assertEqual(result['rows'], 2)
        receipt = self.store.artifact(result['artifact'])
        self.assertEqual(receipt['source']['sampling']['original_bytes'], len(self.payload))
        self.assertTrue(receipt['source']['sampling']['original_complete'])
        rows = [json.loads(line) for line in (self.store.artifact_dir(result['artifact']) / 'payload').read_text().splitlines()]
        self.assertEqual(rows, [{'country':'AA','value':'1'}, {'country':'BB','value':'2'}])
        self.assertFalse((self.store.raw_latest_path('sample_test')).exists())
        self.assertEqual(list(self.store.scratch_dir('sample_test').glob('sample-*')), [])
        self.assertEqual(list(self.store.scratch_dir('sample_test').glob('import-*')), [])

    def test_oversized_download_is_not_retained(self):
        self.definition['sampling']['max_download_bytes'] = 10
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('budget', result['reason'])
        self.assertEqual(list((self.store.root / 'sample_test/artifacts/raw').iterdir()), [])

    def test_archive_extracts_rows_without_extracting_entire_member(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('data.csv', b'a,b\n' + b'1,2\n' * 10000)
        self.payload = stream.getvalue()
        self.definition['sampling'].update(format='zip_csv', archive_member='data.csv', max_uncompressed_bytes=32)
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'sampled')
        self.assertEqual(result['rows'], 2)

    def test_json_errors_are_not_reported_as_successful_samples(self):
        self.payload = b'{"error":{"message":"API key required"}}'
        self.definition['sampling'].update(format='json', records_path=['data'])
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('API key required', result['reason'])

    def test_retained_byte_limit_is_obeyed_without_partial_records(self):
        self.definition['sampling']['max_sample_bytes'] = 35
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['rows'], 1)
        self.assertLessEqual(result['retained_bytes'], 35)
        self.assertEqual(result['stop_reason'], 'sample_byte_limit')

    def test_network_opt_in_is_required(self):
        with self.assertRaisesRegex(ValueError, 'allow-network'):
            sample_dataset(self.store, self.definition)

    def test_invalid_partial_json_cannot_be_sampled(self):
        self.payload = b'[{"x":1},'
        self.definition['sampling'].update(format='json', records_path=[])
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'blocked')

    def test_matching_rows_are_counted_after_filtering_blank_records(self):
        self.payload = b'code,title\n,\n111110,Soybean Farming\n111120,Oilseed Farming\n'
        self.definition['sampling']['required_pattern'] = {'code': '[0-9]{6}'}
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['rows'], 2)
        payload = (self.store.artifact_dir(result['artifact']) / 'payload').read_text()
        self.assertEqual(json.loads(payload.splitlines()[0])['code'], '111110')

    def test_html_missing_key_is_a_clear_blocker(self):
        self.payload = b'<html><title>Missing Key</title></html>'
        self.definition['sampling'].update(format='json', records_path=[])
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('Missing Key', result['reason'])

    def test_budget_includes_download_and_sample_staging(self):
        self.definition['sampling']['disk_budget_bytes'] = 2000
        with self.assertRaisesRegex(ValueError, 'disk budget'):
            sample_dataset(self.store, self.definition, allow_network=True)

    def test_uncompressed_budget_cannot_publish_a_truncated_csv_cell(self):
        self.payload = b'a,b\n1,123456789\n'
        self.definition['sampling'].update(max_uncompressed_bytes=8, max_rows=1)
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('physical line', result['reason'])

    def test_csv_at_real_eof_without_newline_is_complete(self):
        self.payload = b'a,b\n1,123'
        self.definition['sampling'].update(max_rows=1)
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'sampled')
        row = json.loads((self.store.artifact_dir(result['artifact']) / 'payload').read_text())
        self.assertEqual(row['b'], '123')

    def test_csv_html_error_is_not_a_data_sample(self):
        self.payload = b'<html>\n<body>Access Denied</body>\n</html>\n'
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('HTML', result['reason'])

    def test_empty_api_series_is_a_per_dataset_blocker(self):
        self.payload = b'{"status":"REQUEST_SUCCEEDED","Results":{"series":[]}}'
        self.definition['sampling'].update(format='json', records_path=['Results','series',0,'data'])
        result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('records_path', result['reason'])

    def test_required_header_environment_blocks_before_network_and_is_not_persisted(self):
        self.definition['sampling']['headers_env'] = {'User-Agent': 'WORLD_MODEL_TEST_CONTACT'}
        with patch.dict(os.environ, {}, clear=True):
            result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('WORLD_MODEL_TEST_CONTACT', result['reason'])
        with patch.dict(os.environ, {'WORLD_MODEL_TEST_CONTACT': 'test-contact@example.test'}):
            result = sample_dataset(self.store, self.definition, allow_network=True)
        self.assertEqual(result['status'], 'sampled')
        self.assertEqual(self.received_headers['User-Agent'], 'test-contact@example.test')
        self.assertNotIn('test-contact@example.test', json.dumps(self.store.artifact(result['artifact'])))

    def test_explicit_singleton_json_adapter_retains_object_without_implicit_coercion(self):
        self.payload=b'{"data":{"id":"parent:1","relationship":"direct"}}'
        self.definition['sampling'].update(format='json',records_path=['data'],singleton_record=True)
        result=sample_dataset(self.store,self.definition,allow_network=True)
        self.assertEqual(result['status'],'sampled')
        self.assertEqual(result['rows'],1)
        self.definition['sampling']['singleton_record']=False
        result=sample_dataset(self.store,self.definition,allow_network=True)
        self.assertEqual(result['status'],'blocked')
