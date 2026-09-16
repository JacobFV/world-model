"""Offline acceptance test for exiobase3 on a 2-region x 2-industry pymrio-shaped ZIP."""
import io
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
ROWS = [('US', 'Wheat'), ('US', 'Steel'), ('WA', 'Wheat'), ('WA', 'Steel')]


def matrix_header(first, second, columns):
    return (f'{first}\t\t' + '\t'.join(c[0] for c in columns) + '\n' + f'{second}\t\t' + '\t'.join(c[1] for c in columns) + '\n'
            + 'region\tsector' + '\t' * len(columns) + '\n')


def build_zip():
    x = [100.0, 200.0, 0.0, 50.0]
    z = [[0, 0.5, 0, 0], [30, 0, 0, 2], [0, 0, 0, 0], [0, 0.04, 0, 0]]
    y_columns = [('US', 'Households'), ('US', 'Government'), ('WA', 'Households'), ('WA', 'Government')]
    y = [[60, 0, 0.05, 0], [100, 50, 0, 0], [0, 0, 0, 0], [0, 0, 40, 5]]
    files = {
        'metadata.json': json.dumps({'description': 'EXIOBASE version 3.9.4 - ixi for 2022'}),
        'x.txt': 'region\tsector\tindout\n' + ''.join(f'{r}\t{s}\t{v}\n' for (r, s), v in zip(ROWS, x)),
        'Z.txt': matrix_header('region', 'sector', ROWS) + ''.join(f'{r}\t{s}\t' + '\t'.join(str(v) for v in row) + '\n'
                                                                  for (r, s), row in zip(ROWS, z)),
        'Y.txt': matrix_header('region', 'category', y_columns) + ''.join(f'{r}\t{s}\t' + '\t'.join(str(v) for v in row) + '\n'
                                                                         for (r, s), row in zip(ROWS, y)),
    }
    extension_header = 'region\t' + '\t'.join(r for r, _ in ROWS) + '\nsector\t' + '\t'.join(s for _, s in ROWS) + '\nstressor' + '\t' * 4 + '\n'
    files['factor_inputs/F.txt'] = extension_header + 'Operating surplus: Rents on land\t1\t0\t0\t2\n'
    files['factor_inputs/unit.txt'] = '\tunit\nOperating surplus: Rents on land\tM.EUR\n'
    files['employment/F.txt'] = extension_header + 'Employment people: Low-skilled male\t3\t0\t0\t0\n'
    files['employment/unit.txt'] = 'stressor\tunit\nEmployment people: Low-skilled male\t1000 p\n'
    files['air_emissions/F.txt'] = extension_header + 'CO2 - combustion - air\t0\t5e6\t0\t0\nAs - combustion - air\t1\t1\t1\t1\n'
    files['air_emissions/unit.txt'] = 'stressor\tunit\nCO2 - combustion - air\tkg\nAs - combustion - air\tkg\n'
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as archive:
        for name, text in files.items():
            archive.writestr(name, text)
    return buffer.getvalue()


class Exiobase3PipelineTest(unittest.TestCase):
    def test_thresholded_mrio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'iot.zip').write_bytes(build_zip())
            store = Store(root / 'data')
            catalog = Catalog(PROJECT / 'data')
            store.import_shards('exiobase3', [{'path': root / 'iot.zip', 'retrieved_at': '2026-09-15T00:00:00+00:00'}],
                                {'publisher': 'fixture', 'acquisition': catalog.get('exiobase3')['acquisition']}, complete=True)
            records = list(store.records(Runner(catalog, store, PROJECT).run('exiobase3')))
        obs = [r for r in records if r['kind'] == 'observation']
        flows = [r for r in obs if r['metric'] == 'intermediate_supply']
        # With 0.1 M EUR / A >= 0.0001: 30 (A=0.3), 2 (A=0.04) and 0.5 (A=0.0025) pass; 0.04 M EUR does not.
        self.assertEqual(sorted((r['subject'], r['dimensions']['purchaser'], r['value']) for r in flows),
                         [('exiobase:regional_industry:US:steel', 'exiobase:regional_industry:US:wheat', 30e6),
                          ('exiobase:regional_industry:US:steel', 'exiobase:regional_industry:WA:steel', 2e6),
                          ('exiobase:regional_industry:US:wheat', 'exiobase:regional_industry:US:steel', 0.5e6)])
        self.assertAlmostEqual(flows[0]['attributes']['input_coefficient'] if flows[0]['value'] == 30e6 else flows[1]['attributes']['input_coefficient'], 0.3)
        output = {r['subject']: r['value'] for r in obs if r['metric'] == 'gross_output'}
        self.assertEqual(output['exiobase:regional_industry:US:steel'], 200e6)
        self.assertNotIn('exiobase:regional_industry:WA:wheat', output)
        demand = [r for r in obs if r['metric'] == 'final_demand_supply' and r['dimensions']['final_demand_category'] == 'all']
        self.assertEqual(sorted((r['subject'][-8:], r['dimensions']['consumer_region'], r['value']) for r in demand),
                         [('US:steel', 'exiobase:region:US', 150e6), ('US:wheat', 'exiobase:region:US', 60e6),
                          ('WA:steel', 'exiobase:region:WA', 45e6)])
        emissions = [r for r in obs if r['metric'] == 'air_emission']
        self.assertEqual([(r['value'], r['unit']) for r in emissions], [(5e6, 'kg')])
        persons = [r for r in obs if r['metric'] == 'employment_persons']
        self.assertEqual(persons[0]['value'], 3000)
        self.assertEqual(obs[0]['valid_from'], '2022-01-01')
        corresponds = {r['subject']: r['object'] for r in records if r.get('predicate') == 'corresponds_to'}
        self.assertEqual(corresponds, {'exiobase:region:US': 'iso3:USA'})
        self.assertTrue(any(r['kind'] == 'entity' and r['entity_id'] == 'exiobase:region:WA' and r['attributes']['aggregate'] for r in records))


if __name__ == '__main__':
    unittest.main()
