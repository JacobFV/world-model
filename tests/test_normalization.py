import json
from pathlib import Path
import unittest
from worldmodel.pipeline import Context
from worldmodel.store import Store
from worldmodel.normalizers import normalize_sample, AVAILABLE
from worldmodel.ontology import validate_typed_graph, describe

ROOT = Path(__file__).resolve().parents[1]

class NormalizationTests(unittest.TestCase):
    def samples(self):
        missing = [dataset for dataset in AVAILABLE
                   if not (ROOT / 'data' / dataset / 'samples/latest.json').exists()]
        if missing:
            self.skipTest('Local acquired-sample integration test; missing samples: ' + ', '.join(missing))
        store = Store(ROOT / 'data')
        for dataset in AVAILABLE:
            definition = json.loads((ROOT / 'data' / dataset / 'dataset.json').read_text())
            ref = json.loads((ROOT / 'data' / dataset / 'samples/latest.json').read_text())['artifact']
            yield dataset, list(normalize_sample(Context(store, definition, {}, [], [ref])))

    def test_all_real_samples_form_closed_typed_graph(self):
        combined = []
        for dataset, records in self.samples():
            with self.subTest(dataset=dataset):
                self.assertTrue(records)
                validate_typed_graph(records)
                for record in records:
                    self.assertTrue(record['evidence'][0]['locator'].startswith('line:'))
                    self.assertIn('source_row', record['attributes'])
                    if record['kind'] == 'observation':
                        self.assertIn('subject', record)
                combined.extend(records)
        result = validate_typed_graph(combined)
        self.assertGreater(result['relations'], 100)
        self.assertIn('private_jet', describe()['entity_types'])

    def test_cohorts_and_shared_geographies(self):
        samples = dict(self.samples())
        business = samples['census_business']
        self.assertTrue(any(r.get('entity_type') == 'business_cohort' for r in business))
        self.assertFalse(any(r.get('entity_type') == 'business' for r in business))
        geo = lambda rs: {r.get('entity_id') for r in rs if r['kind'] == 'entity'}
        self.assertIn('geo:US:state:06', geo(samples['eia_energy']) & geo(samples['census_population']) & geo(samples['census_geography']))
        self.assertFalse(any(r.get('predicate') == 'owns' for rs in samples.values() for r in rs))

    def test_rejects_unresolved_reference_and_bad_range(self):
        _, records = next(self.samples())
        relation = next(r for r in records if r['kind'] == 'assertion')
        broken = [dict(r) for r in records]
        next(r for r in broken if r['id'] == relation['id'])['object'] = 'missing:thing'
        with self.assertRaisesRegex(ValueError, 'Unresolved'):
            validate_typed_graph(broken)
        broken = [dict(r) for r in records]
        next(r for r in broken if r['id'] == relation['id'])['predicate'] = 'operates_aircraft'
        with self.assertRaisesRegex(ValueError, 'domain|range'):
            validate_typed_graph(broken)

    def test_measurements_preserve_units_time_and_raw_rows(self):
        samples = dict(self.samples())
        populations = [r for r in samples['census_population'] if r['kind']=='observation']
        national = next(r for r in populations if r['subject']=='geo:US' and r['valid_from']=='2024-07-01')
        self.assertEqual((national['value'],national['unit']),(340110988,'people'))
        self.assertEqual(national['valid_to'],'2024-07-02')
        self.assertEqual(national['attributes']['source_row']['BIRTHS2024'],'3605563')
        electricity = next(r for r in samples['eia_energy'] if r['kind']=='observation')
        self.assertEqual(electricity['unit'],'million kilowatt hours')
        self.assertEqual(electricity['valid_from'],'2023-01-01')
        awards = [r for r in samples['usaspending'] if r['kind']=='observation']
        self.assertTrue(all(r['valid_from']==r['observed_at'] for r in awards))
        minerals = [r for r in samples['usgs_resources'] if r['kind']=='observation']
        self.assertTrue(all('valid_from' not in r and r['attributes']['valid_time_unknown'] for r in minerals))

    def test_unknown_variable_and_wrong_unit_rejected(self):
        records = dict(self.samples())['eia_energy']
        for field,value in [('unit','USD'),('metric','undeclared_metric')]:
            broken = [dict(r) for r in records]
            next(r for r in broken if r['kind']=='observation')[field] = value
            with self.assertRaises(ValueError): validate_typed_graph(broken)

    def test_suppression_is_missing_not_zero(self):
        import tempfile
        from unittest.mock import Mock
        original = dict(self.samples())['census_business']
        row = dict(next(r for r in original if r['kind']=='observation')['attributes']['source_row'])
        row['emp']='0'; row['emp_nf']='D'
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'payload'; path.write_text(json.dumps(row)+'\n')
            path.with_name('receipt.json').write_text(json.dumps({'retrieved_at':'2026-09-15T00:00:00Z'}))
            context=Mock(definition={'id':'census_business'},raw_inputs=[{'dataset':'census_business','artifact':'a'*64}])
            context.raw_path.return_value=path
            context.raw_evidence.return_value=[{'input':context.raw_inputs[0],'locator':'line:1'}]
            records=list(normalize_sample(context)); validate_typed_graph(records)
            employment=next(r for r in records if r.get('metric')=='employment')
            self.assertIsNone(employment['value'])
            self.assertIn('suppressed',employment['missing_reason'])
            self.assertEqual(employment['attributes']['source_row']['emp'],'0')

    def test_default_process_ports_match_ontology(self):
        from worldmodel.process_library import default_registry
        from worldmodel.ontology import VARIABLES
        registry = default_registry().describe()
        processes = registry['processes']
        if isinstance(processes, dict): processes = processes.values()
        for process in processes:
            for direction in ('inputs','outputs'):
                for name, port in process[direction].items():
                    with self.subTest(process=process['id'],port=name):
                        self.assertIn(name,VARIABLES)
                        self.assertEqual(VARIABLES[name]['type'],port['type'])
                        self.assertEqual(VARIABLES[name]['unit'],port.get('unit'))

    def test_ownership_operations_employment_and_resource_transport(self):
        evidence=[{'input':{'dataset':'census_business','artifact':'a'*64},'locator':'line:1'}]
        def record(kind, id, **fields):
            return dict(kind=kind,id=id,evidence=evidence,observed_at='2026-01-01',**fields)
        types={'org:parent':'business','org:subsidiary':'business','person:worker':'person',
               'asset:jet':'private_jet','asset:refinery':'refinery','resource:oil':'oil',
               'shipment:oil':'shipment','vehicle:tanker':'vessel','geo:port':'port'}
        entities=[record('entity',key,entity_type=typ,label=key) for key,typ in types.items()]
        relations=[('org:parent','owns','org:subsidiary'),('org:parent','owns','asset:jet'),
                   ('org:parent','operates','asset:refinery'),('org:parent','controls','org:subsidiary'),
                   ('person:worker','employed_by','org:subsidiary'),
                   ('asset:refinery','produces','resource:oil'),('org:subsidiary','consumes','resource:oil'),
                   ('shipment:oil','transports','resource:oil'),('shipment:oil','carried_by','vehicle:tanker'),
                   ('shipment:oil','shipment_destination','geo:port')]
        claims=[record('assertion','claim:'+str(i),subject=a,predicate=b,object=c) for i,(a,b,c) in enumerate(relations)]
        self.assertEqual(validate_typed_graph(entities+claims)['relations'],len(relations))
        claims[0]=record('assertion','claim:0',subject='org:parent',predicate='owns',object='person:worker')
        with self.assertRaisesRegex(ValueError,'range'): validate_typed_graph(entities+claims)
