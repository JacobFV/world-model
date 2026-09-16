"""Offline fixture test: OpenFEMA JSONL records from several entity families in one shard."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

HERE = Path(__file__).resolve().parents[1]
PROJECT = HERE.parents[1]
DATASET = HERE.name

RECORDS = [
    {'femaDeclarationString': 'DR-4611-LA', 'disasterNumber': 4611, 'state': 'LA', 'declarationType': 'DR',
     'declarationDate': '2021-08-29T00:00:00.000Z', 'incidentType': 'Hurricane', 'declarationTitle': 'HURRICANE IDA',
     'ihProgramDeclared': True, 'paProgramDeclared': True, 'incidentBeginDate': '2021-08-26T00:00:00.000Z',
     'fipsStateCode': '22', 'fipsCountyCode': '089', 'placeCode': '99089', 'designatedArea': 'St. Charles (Parish)', 'region': 6},
    {'femaDeclarationString': 'DR-4611-LA', 'disasterNumber': 4611, 'state': 'LA', 'declarationType': 'DR',
     'declarationDate': '2021-08-29T00:00:00.000Z', 'incidentType': 'Hurricane', 'fipsStateCode': '22', 'fipsCountyCode': '000',
     'placeCode': '0', 'designatedArea': 'Statewide', 'region': 6},
    {'disasterNumber': 4611, 'totalNumberIaApproved': 500000, 'totalAmountIhpApproved': 1.2e9, 'totalObligatedAmountPa': None,
     'totalObligatedAmountCatAb': 3.0e8, 'totalObligatedAmountCatC2g': None, 'totalObligatedAmountHmgp': None},
    {'disasterNumber': 4611, 'declarationDate': '2021-08-29T00:00:00.000Z', 'incidentType': 'Hurricane', 'state': 'Louisiana',
     'county': 'St. Charles Parish', 'applicantName': 'St. Charles Parish', 'educationApplicant': False, 'numberOfProjects': 12,
     'federalObligatedAmount': 4010.5},
    {'disasterNumber': 4611, 'state': 'LA', 'county': 'St. Charles (Parish)', 'zipCode': '70057', 'validRegistrations': 900,
     'averageFemaInspectedDamage': 1345.01, 'totalInspected': 300, 'totalDamage': 5380.02, 'noFemaInspectedDamage': 1,
     'approvedForFemaAssistance': 250, 'totalApprovedIhpAmount': 5915.91, 'repairReplaceAmount': 3573.02, 'rentalAmount': 970,
     'otherNeedsAmount': 1372.89, 'totalMaxGrants': 0},
    {'disasterNumber': 4611, 'state': 'LA', 'county': 'St. Charles (Parish)', 'zipCode': '70057', 'validRegistrations': 400,
     'totalInspected': 100, 'approvedForFemaAssistance': 80, 'totalApprovedIhpAmount': 1000, 'repairReplaceAmount': 0,
     'rentalAmount': 1000, 'otherNeedsAmount': 0, 'totalMaxGrants': 0},
]


class OpenFemaTest(unittest.TestCase):
    def test_families(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            catalog = tmp / 'catalog' / DATASET
            catalog.mkdir(parents=True)
            shutil.copy2(HERE / 'dataset.json', catalog / 'dataset.json')
            shutil.copy2(HERE / 'pipeline.py', catalog / 'pipeline.py')
            (tmp / 'records.jsonl').write_text(''.join(json.dumps(r) + '\n' for r in RECORDS))
            store = Store(tmp / 'data')
            store.import_shards(DATASET, [{'path': str(tmp / 'records.jsonl')}], {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(tmp / 'catalog'), store, PROJECT).run(DATASET)
            records = list(store.records(ref))
        by_id = {r['id']: r for r in records}
        declaration = by_id['openfema:declaration:DR-4611-LA:22089:99089']
        self.assertEqual(declaration['participants'], ['fema:disaster:4611', 'geo:US:county:22089'])
        self.assertEqual(by_id['openfema:declaration:DR-4611-LA:22000:0']['participants'][1], 'geo:US:state:22')
        self.assertEqual(by_id['openfema:entity:disaster:4611']['attributes']['incident_type'], 'Hurricane')
        self.assertEqual(by_id['openfema:web:4611:totalAmountIhpApproved']['value'], 1.2e9)
        self.assertNotIn('openfema:web:4611:totalObligatedAmountPa', by_id)
        pa = [r for r in records if r.get('metric') == 'pa_federal_obligated' and 'applicant' in r['dimensions']]
        self.assertEqual((pa[0]['value'], pa[0]['valid_from']), (4010.5, '2021-08-29T00:00:00.000Z'))
        tenures = {r['dimensions']['tenure'] for r in records if r.get('metric') == 'ihp_valid_registrations'}
        self.assertEqual(tenures, {'owner', 'renter'})
        self.assertEqual(sum(1 for r in records if r.get('metric') == 'fema_inspected_damage'), 1)


if __name__ == '__main__':
    unittest.main()
