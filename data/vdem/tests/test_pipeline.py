"""Offline acceptance test: wide V-Dem CSV -> curated country-year observations."""
import csv
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]


class VdemPipelineTest(unittest.TestCase):
    def test_curated_variables(self):
        variables = json.loads((ROOT / 'data/vdem/variables.json').read_text())['variables']
        header = ['country_name', 'country_text_id', 'country_id', 'year', 'COWcode']
        for spec in variables:
            header.append(spec['column'])
            if spec['bounds']:
                header += [spec['column'] + '_codelow', spec['column'] + '_codehigh']
        header.append('v2x_unused')
        rows = [{'country_name': 'Mexico', 'country_text_id': 'MEX', 'country_id': '3', 'year': '2024', 'COWcode': '70',
                 'v2x_polyarchy': '0.5', 'v2x_polyarchy_codelow': '0.4', 'v2x_polyarchy_codehigh': '0.6', 'e_polity2': '8', 'v2x_unused': '1'},
                {'country_name': 'Mexico', 'country_text_id': 'MEX', 'country_id': '3', 'year': '1800', 'COWcode': '70', 'v2x_polyarchy': '0.1'}]
        buffer = io.StringIO()
        writer = csv.writer(buffer)
        writer.writerow(header)
        writer.writerows([[row.get(h, '') for h in header] for row in rows])
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            archive = tmp / 'V-Dem-CY-FullOthers-v16_csv.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('V-Dem-CY-Full+Others-v16.csv', buffer.getvalue())
            store = Store(tmp / 'data')
            store.import_shards('vdem', [{'path': archive, 'name': archive.name}], {'publisher': 'fixture'}, complete=True)
            records = list(store.records(Runner(Catalog(ROOT / 'data'), store, ROOT).run('vdem')))
            observations = {r['metric']: r for r in records if r['kind'] == 'observation'}
            self.assertEqual(set(observations), {'vdem_v2x_polyarchy', 'vdem_e_polity2'})
            polyarchy = observations['vdem_v2x_polyarchy']
            self.assertEqual((polyarchy['subject'], polyarchy['value'], polyarchy['unit'], polyarchy['valid_from']),
                             ('iso3:MEX', 0.5, 'index_0_1', '2024-01-01'))
            self.assertEqual(polyarchy['attributes']['interval_68'], [0.4, 0.6])
            entity = next(r for r in records if r['kind'] == 'entity')
            self.assertEqual(entity['attributes']['cow_code'], 70)


if __name__ == '__main__':
    unittest.main()
