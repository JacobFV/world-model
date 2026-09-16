"""Catalog-scale unify: scope selection, streaming, provenance pinning and resolution."""
import gzip
import json
from pathlib import Path
import tempfile
import tracemalloc
import unittest

from worldmodel.catalog import Catalog
from worldmodel.graph import Graph
from worldmodel.store import Store
from worldmodel import unify as U
from worldmodel.util import canonical, digest, file_hash, read_json

EVIDENCE = [{'input': {'dataset': 'fixture_raw', 'artifact': 'a' * 64}, 'locator': 'line:1'}]


def entity(key, label='Thing', typ='organization'):
    return {'kind': 'entity', 'id': 'e:' + key, 'entity_id': key, 'entity_type': typ, 'label': label,
            'observed_at': '2026-01-01', 'evidence': EVIDENCE}


def assertion(key, subject, predicate, obj):
    return {'kind': 'assertion', 'id': 'a:' + key, 'subject': subject, 'predicate': predicate,
            'object': obj, 'observed_at': '2026-01-01', 'evidence': EVIDENCE}


def observation(key, subject, metric='population', value=1.0):
    return {'kind': 'observation', 'id': 'o:' + key, 'subject': subject, 'metric': metric, 'unit': 'people',
            'value': value, 'dimensions': {}, 'observed_at': '2026-01-01', 'evidence': EVIDENCE}


def event(key, participants):
    return {'kind': 'event', 'id': 'v:' + key, 'event_type': 'port_call', 'occurred_at': '2026-01-01',
            'participants': participants, 'observed_at': '2026-01-01', 'evidence': EVIDENCE}


DECLARATION = {
    'schema_version': 2, 'kind': 'source', 'status': 'complete', 'dependencies': [], 'parameters': {},
    'entrypoint': 'pipeline.py:run', 'output_stage': 'normalized',
    'stages': [{'id': 'normalized', 'entrypoint': 'pipeline.py:run', 'depends_on': [],
                'schema': {'format': 'evidence_jsonl', 'compression': 'gzip'},
                'validation': {'allow_empty': False, 'max_rows': 2000000}}]}


class Fixture:
    """A catalog root plus a data root holding published, checksum-verifiable stage outputs."""

    def __init__(self, root):
        self.root = Path(root)
        self.catalog_root = self.root / 'catalog'
        self.data_root = self.root / 'data'
        self.catalog_root.mkdir(parents=True, exist_ok=True)
        self.catalog = Catalog(self.catalog_root)
        self.store = Store(self.data_root)

    def declare(self, dataset, description='fixture'):
        directory = self.catalog_root / dataset
        directory.mkdir(parents=True, exist_ok=True)
        (directory / 'dataset.json').write_text(json.dumps({**DECLARATION, 'id': dataset,
                                                            'description': description}), encoding='utf-8')
        (directory / 'pipeline.py').write_text('def run(context):\n    return iter(())\n', encoding='utf-8')
        self.store.initialize(dataset)

    def publish(self, dataset, records, stage='normalized'):
        """Write a stage artifact the way Runner does, so Store.verify/manifest accept it."""
        self.declare(dataset)
        directory = self.data_root / dataset / 'artifacts' / stage
        directory.mkdir(parents=True, exist_ok=True)
        staging = self.data_root / dataset / 'scratch' / ('publish-' + dataset + '-' + stage)
        staging.mkdir(parents=True, exist_ok=True)
        output = staging / 'records.jsonl.gz'
        rows = 0
        with output.open('wb') as raw, gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as stream:
            for record in records:
                rows += 1
                stream.write(canonical(record) + b'\n')
        identity = {'schema_version': 2, 'dataset': dataset, 'stage': stage, 'inputs': [], 'raw_inputs': [],
                    'rights': {'sources': []}, 'parameters': {},
                    'outputs': {'records.jsonl.gz': {'sha256': file_hash(output), 'bytes': output.stat().st_size,
                                                     'rows': rows}}}
        version = digest(identity)
        ref = {'dataset': dataset, 'stage': stage, 'version': version}
        destination = self.store.version_dir(ref)
        destination.mkdir(parents=True, exist_ok=True)
        (destination / 'records.jsonl.gz').write_bytes(output.read_bytes())
        (destination / 'manifest.json').write_text(json.dumps({**identity, 'version': version}), encoding='utf-8')
        self.store.publish_index(ref)
        return ref


class ScopeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        self.structural = self.fixture.publish('census_geography', [
            entity('geo:US:county:06001', 'Alameda', 'county'), entity('geo:US:state:06', 'California', 'state'),
            assertion('1', 'geo:US:county:06001', 'within', 'geo:US:state:06'),
            observation('1', 'geo:US:county:06001')])
        self.bulk = self.fixture.publish('usaspending', [
            entity('uei:ABC', 'Recipient'), assertion('2', 'uei:ABC', 'within', 'geo:US:state:06'),
            observation('2', 'uei:ABC', metric='population')])
        self.plain = self.fixture.publish('faostat', [
            entity('iso3:USA', 'United States', 'country'), observation('3', 'iso3:USA')])

    def tearDown(self):
        self.tmp.cleanup()

    def scope(self, **kwargs):
        return U.scope(self.fixture.catalog, self.fixture.store, **kwargs)

    def test_inventory_reports_published_outputs_and_reasons(self):
        self.fixture.declare('acled')  # declared but never published
        available, missing = U.inventory(self.fixture.catalog, self.fixture.store)
        self.assertEqual({item['dataset'] for item in available}, {'census_geography', 'usaspending', 'faostat'})
        self.assertEqual([m['reason'] for m in missing if m['dataset'] == 'acled'], ['no published output stage'])

    def test_default_profile_excludes_bulk_and_filters_observations(self):
        plan = self.scope()
        selected = {item['dataset']: item['kinds'] for item in plan['selected']}
        self.assertNotIn('usaspending', selected)  # BULK_DATASETS
        self.assertEqual(set(selected['census_geography']), set(U.KINDS))  # observations are the point
        self.assertEqual(set(selected['faostat']), set(U.STRUCTURE_KINDS))
        self.assertIn('usaspending', {row['dataset'] for row in plan['skipped']})

    def test_all_profile_takes_every_kind_of_every_dataset(self):
        plan = self.scope(profile='all')
        self.assertEqual({item['dataset'] for item in plan['selected']},
                         {'census_geography', 'usaspending', 'faostat'})
        self.assertTrue(all(set(item['kinds']) == set(U.KINDS) for item in plan['selected']))

    def test_explicit_datasets_override_the_profile_exclusions(self):
        plan = self.scope(datasets=['usaspending'])
        self.assertEqual([item['dataset'] for item in plan['selected']], ['usaspending'])

    def test_domain_selection_and_exclude(self):
        plan = self.scope(domains=['demographics'])
        self.assertEqual([item['dataset'] for item in plan['selected']], ['census_geography'])
        plan = self.scope(domains=['demographics'], exclude=['census_geography'])
        self.assertEqual(plan['selected'], [])
        self.assertIn('excluded by --exclude', {row['reason'] for row in plan['skipped']})

    def test_unknown_selections_are_refused(self):
        with self.assertRaises(ValueError):
            self.scope(datasets=['acled'])
        with self.assertRaises(ValueError):
            self.scope(domains=['not_a_domain'])
        with self.assertRaises(ValueError):
            self.scope(profile='not_a_profile')

    def test_dry_run_reports_cost_without_touching_the_index(self):
        result = U.unify(self.fixture.catalog, self.fixture.store, dry_run=True, publish=False, progress=None)
        self.assertTrue(result['dry_run'])
        self.assertFalse((self.fixture.data_root / 'world_evidence' / 'index.sqlite').exists())


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        self.geography = self.fixture.publish('census_geography', [
            entity('geo:US:county:06001', 'Alameda', 'county'), entity('geo:US:state:06', 'California', 'state'),
            assertion('1', 'geo:US:county:06001', 'within', 'geo:US:state:06'),
            observation('1', 'geo:US:county:06001', value=1682353.0)])
        self.gleif = self.fixture.publish('sec_gleif', [
            entity('lei:5493001KJTIIGC8Y1R12', 'Anchor Corp'),
            assertion('2', 'lei:5493001KJTIIGC8Y1R12', 'registered_in', 'geo:US:state:06'),
            observation('2', 'lei:5493001KJTIIGC8Y1R12')])
        self.index = Path(self.tmp.name) / 'index.sqlite'

    def tearDown(self):
        self.tmp.cleanup()

    def build(self, **kwargs):
        return U.unify(self.fixture.catalog, self.fixture.store, index=self.index, progress=None, **kwargs)

    def test_default_build_indexes_structure_and_selected_observations(self):
        result = self.build(publish=False)
        totals = result['totals']
        self.assertEqual(totals['entities'], 3)
        self.assertEqual(totals['assertions'], 2)
        self.assertEqual(totals['observations'], 1)  # census_geography only; sec_gleif is structure-only
        self.assertEqual(totals['records_read'], 7)
        self.assertEqual(result['index']['records'], 6)
        self.assertEqual(result['index']['edges'], 2)
        graph = Graph(self.index)
        self.assertEqual({n['id'] for n in graph.neighborhood('geo:US:state:06', hops=1)['nodes']},
                         {'geo:US:state:06', 'geo:US:county:06001', 'lei:5493001KJTIIGC8Y1R12'})
        self.assertEqual(graph.observations('population')[0]['value'], 1682353.0)

    def test_kind_filter_is_confirmed_after_parsing(self):
        """A record whose text merely contains another kind's tag is still filtered out."""
        self.fixture.publish('faostat', [
            entity('iso3:USA', 'United States', 'country'),
            {**observation('4', 'iso3:USA'), 'attributes': {'note': 'kind":"entity" appears in this text'}}])
        result = self.build(publish=False, datasets=['faostat'])
        self.assertEqual(result['totals'], {'records_read': 2, 'records_indexed': 1, 'entities': 1,
                                            'assertions': 0, 'observations': 0, 'events': 0})

    def test_limit_bounds_each_dataset(self):
        result = self.build(publish=False, limit=1)
        self.assertEqual(result['totals']['records_indexed'], 2)

    def test_published_artifact_pins_every_input_version(self):
        result = self.build(publish=True)
        ref = result['artifact']
        manifest = self.fixture.store.manifest(ref)
        self.assertEqual(sorted(manifest['inputs'], key=lambda r: r['dataset']),
                         [self.geography, self.gleif])
        report = read_json(self.fixture.store.version_dir(ref) / 'report.json')
        self.assertEqual(report['inputs'], manifest['inputs'])
        self.assertEqual(read_json(self.fixture.store.latest_path('world_evidence'))['version'], ref['version'])

    def test_index_metadata_pins_the_same_inputs(self):
        result = self.build(publish=False)
        import sqlite3
        with sqlite3.connect(self.index) as connection:
            pinned = json.loads(connection.execute("SELECT value FROM metadata WHERE key='inputs'").fetchone()[0])
        self.assertEqual(pinned, result['inputs'])

    def test_corrupted_output_is_refused_before_it_is_indexed(self):
        path = U.records_path(self.fixture.store, self.gleif)
        path.write_bytes(path.read_bytes() + b'\n')
        with self.assertRaises(ValueError):
            self.build(publish=False)

    def test_resolution_attaches_to_a_unified_index(self):
        self.build(publish=False)
        graph = Graph(self.index)
        view = {'view_digest': 'd' * 64, 'input_digest': 'e' * 64, 'model_digest': 'f' * 64, 'policy': {}}
        attached = graph.attach_resolution(
            [{'canonical_id': 'geo:US:county:06001', 'members': ['geo:US:county:06001', 'geo:US:state:06']}],
            view=view)
        self.assertEqual(attached['resolved_entities'], 2)
        resolved = graph.resolved_entity('geo:US:state:06')
        self.assertEqual(resolved['canonical_id'], 'geo:US:county:06001')
        self.assertEqual(len(graph.neighborhood('geo:US:state:06', hops=1, resolved=True)['edges']), 2)


class StreamingTests(unittest.TestCase):
    """The build must hold a bounded window, never the record set."""

    def test_peak_memory_is_bounded_by_the_batch_not_the_dataset(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(tmp)
            count = 120000
            fixture.publish('faostat', (entity('org:%06d' % i, 'Organisation %06d' % i + ' ' * 200)
                                        for i in range(count)))
            index = Path(tmp) / 'index.sqlite'
            tracemalloc.start()
            result = U.unify(fixture.catalog, fixture.store, index=index, datasets=['faostat'],
                             publish=False, progress=None, batch_size=2000, cache_mb=8)
            peak = tracemalloc.get_traced_memory()[1]
            tracemalloc.stop()
            self.assertEqual(result['index']['records'], count)
            # Holding all 120k records would cost well over 40 MB; the batch window costs a fraction.
            self.assertLess(peak, 24 * 2 ** 20, 'unify accumulated records instead of streaming')

    def test_stream_dataset_is_lazy(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Fixture(tmp)
            ref = fixture.publish('faostat', [entity('org:%d' % i) for i in range(2000)])
            item = {'dataset': 'faostat', 'stage': 'normalized', 'ref': ref, 'rows': 2000, 'bytes': 0,
                    'kinds': U.STRUCTURE_KINDS}
            stats = {}
            stream = U.stream_dataset(fixture.store, item, stats=stats)
            self.assertEqual(stats, {})  # nothing read before the first pull
            next(stream)
            self.assertEqual(stats, {})  # still streaming
            self.assertEqual(sum(1 for _ in stream), 1999)
            self.assertEqual(stats['faostat']['records_indexed'], 2000)


class GraphIndexTests(unittest.TestCase):
    """Regression cover for the two graph.py options unify needs at catalog scale."""

    REF = {'dataset': 'fixture', 'stage': 'normalized', 'version': 'b' * 64}

    def records(self):
        return [entity('org:a', 'A'), entity('org:b', 'B'), assertion('1', 'org:a', 'owns', 'org:b'),
                observation('1', 'org:a')]

    def test_compressed_and_plain_bodies_decode_identically(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain, packed = Path(tmp) / 'plain.sqlite', Path(tmp) / 'packed.sqlite'
            Graph(plain).build_from_records([(self.REF, self.records())], compress_bodies=False)
            Graph(packed).build_from_records([(self.REF, self.records())], compress_bodies=True)
            self.assertLess(packed.stat().st_size, plain.stat().st_size + 2 ** 20)
            for query in (lambda g: g.neighbors('org:a'), lambda g: g.observations('population')):
                self.assertEqual(query(Graph(plain)), query(Graph(packed)))
            edges = Graph(packed).neighborhood('org:a', hops=1)['edges']
            self.assertEqual(Graph(packed).edge_records(edges)[0]['predicate'], 'owns')

    def test_cache_size_is_validated(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                Graph(Path(tmp) / 'g.sqlite').build_from_records([(self.REF, self.records())], cache_mb=0)
            built = Graph(Path(tmp) / 'g.sqlite').build_from_records([(self.REF, self.records())], cache_mb=16)
            self.assertEqual(built['records'], 4)


def identifier(key, subject, namespace, value, predicate='identifier_assignment'):
    payload = {'identifier_assignment': {'namespace': namespace, 'value': value},
               'identifier': {'id': namespace + ':' + value, 'value': value},
               'identified_by': namespace + ':' + value}[predicate]
    return {'kind': 'assertion', 'id': 'i:' + key, 'subject': subject, 'predicate': predicate,
            'value': payload, 'observed_at': '2026-01-01', 'evidence': EVIDENCE}


class ResolutionTests(unittest.TestCase):
    """Asserted identity across real predicate shapes; nothing name-based is attached."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        self.index = Path(self.tmp.name) / 'index.sqlite'
        # sanctions listing publishes an LEI with the 'identifier' shape; GLEIF's entity ID *is* the LEI
        self.fixture.publish('ofac_sanctions', [
            entity('ofac:party:1', 'ACME TRADING LLC'),
            identifier('1', 'ofac:party:1', 'lei', '5493001kjtiigc8y1r12', predicate='identifier'),
            identifier('2', 'ofac:party:1', 'ein', '850019030', predicate='identifier')])
        self.fixture.publish('sec_gleif', [entity('lei:5493001KJTIIGC8Y1R12', 'Acme Trading LLC')])
        # registry publishes the same CIK in an unpadded form; normalization has to make them meet
        self.fixture.publish('sec_issuer_reference', [
            entity('sec:cik:0000001750', 'AAR CORP'),
            identifier('3', 'sec:cik:0000001750', 'lei', '5493001KJTIIGC8Y1R12'),
            identifier('4', 'sec:cik:0000001750', 'ein', '850019030')])
        # an unrelated filer sharing only the EIN must never be merged
        self.fixture.publish('sec_company_assets', [
            entity('sec:cik:0001108426', 'TXNM ENERGY INC'),
            identifier('5', 'sec:cik:0001108426', 'ein', '850019030')])
        self.fixture.publish('congress_people', [
            entity('bioguide:P000197', 'Nancy Pelosi', 'person'),
            {'kind': 'assertion', 'id': 'a:sameas', 'subject': 'bioguide:P000197', 'predicate': 'same_as',
             'object': 'icpsr:15448', 'observed_at': '2026-01-01', 'evidence': EVIDENCE}])
        self.fixture.publish('voteview_rollcalls', [entity('icpsr:15448', 'PELOSI', 'person')])
        self.datasets = ['ofac_sanctions', 'sec_gleif', 'sec_issuer_reference', 'sec_company_assets',
                         'congress_people', 'voteview_rollcalls']

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, **kwargs):
        U.unify(self.fixture.catalog, self.fixture.store, index=self.index, datasets=self.datasets,
                publish=False, progress=None)
        return U.resolve_identities(self.fixture.catalog, self.fixture.store, index=self.index,
                                    workdir=Path(self.tmp.name) / 'resolve', datasets=self.datasets,
                                    progress=None, **kwargs)

    def test_three_identifier_shapes_and_published_same_as_all_cluster(self):
        report = self.resolve()
        members = {}
        for line in (Path(self.tmp.name) / 'resolve' / 'clusters.jsonl').read_text().splitlines():
            cluster = json.loads(line)
            members[cluster['canonical_id']] = cluster['members']
        joined = [m for m in members.values() if 'lei:5493001KJTIIGC8Y1R12' in m]
        self.assertEqual(joined, [['lei:5493001KJTIIGC8Y1R12', 'ofac:party:1', 'sec:cik:0000001750']])
        self.assertIn(['bioguide:P000197', 'icpsr:15448'], list(members.values()))
        self.assertEqual(report['counts']['same_as'], 1)

    def test_namespaces_that_are_not_unique_in_practice_do_not_merge(self):
        self.resolve()
        graph = Graph(self.index)
        # TXNM shares only the EIN, which the published data shows is not one-to-one.
        self.assertEqual(graph.resolved_entity('sec:cik:0001108426')['members'], ['sec:cik:0001108426'])
        self.assertIn('ein', U.NON_UNIQUE_IN_PRACTICE)

    def test_resolution_is_attached_and_drives_resolved_queries(self):
        report = self.resolve()
        self.assertEqual(report['attached']['resolved_entities'], report['resolved_entities'])
        graph = Graph(self.index)
        resolved = graph.resolved_entity('ofac:party:1')
        self.assertEqual(resolved['canonical_id'], 'lei:5493001KJTIIGC8Y1R12')
        self.assertEqual(graph.resolution()['view']['view_digest'], report['view']['view_digest'])
        self.assertFalse(graph.resolution()['view']['policy']['inferred_matches_attached'])

    def test_view_digest_is_reproducible(self):
        first = self.resolve()
        second = self.resolve()
        self.assertEqual(first['view']['view_digest'], second['view']['view_digest'])

    def test_oversized_components_are_reported_not_merged(self):
        report = self.resolve(max_cluster_size=1)
        self.assertTrue(report['oversized_components'])
        self.assertEqual(report['clusters'], 0)

    def test_identifier_value_shapes(self):
        self.assertEqual(U._identifier_value({'predicate': 'identified_by', 'value': 'iata:UTK'}),
                         ('iata', 'UTK', None))
        self.assertEqual(U._identifier_value({'predicate': 'identifier', 'value': {'id': 'imo:9422964',
                                                                                   'value': 'IMO9422964'}}),
                         ('imo', '9422964', None))
        self.assertIsNone(U._identifier_value({'predicate': 'identifier_assignment', 'value': {'namespace': 'lei'}}))
        self.assertEqual(U._entity_identifier('sec:cik:0000320193'), ('sec_cik', '0000320193', None))
        self.assertIsNone(U._entity_identifier('geo:US:county:06001'))

    def test_clusters_are_deterministic_components_with_the_smallest_id_canonical(self):
        clusters, oversized = U.identity_clusters([('b', 'a'), ('c', 'b'), ('z', 'y')])
        self.assertEqual(clusters, [{'canonical_id': 'a', 'members': ['a', 'b', 'c']},
                                    {'canonical_id': 'y', 'members': ['y', 'z']}])
        self.assertEqual(oversized, [])


def gleif_entity(lei, label, authority=None, authority_id=None):
    """A sec_gleif entity record, with the registration-authority attributes GLEIF publishes."""
    attributes = {'jurisdiction': 'US'}
    if authority:
        attributes['registration_authority'] = authority
    if authority_id:
        attributes['registration_authority_entity_id'] = authority_id
    return {'kind': 'entity', 'id': 'gleif_lei:' + lei, 'entity_id': 'lei:' + lei, 'entity_type': 'organization',
            'label': label, 'observed_at': '2026-01-01', 'evidence': EVIDENCE, 'attributes': attributes}


def gleif_isin(lei, isin):
    """A sec_gleif ISIN-to-LEI mapping row, published as an issuer_security edge."""
    return {'kind': 'assertion', 'id': 'gleif_isin:%s:%s' % (isin, lei), 'subject': 'lei:' + lei,
            'predicate': 'issuer_security', 'object': 'isin:' + isin, 'observed_at': '2026-01-01',
            'evidence': EVIDENCE}


class BridgeTests(unittest.TestCase):
    """Published identifiers that live in fields other than an identifier assertion.

    Each case is a field a publisher documents: the GLEIF registration authority and its entity ID,
    and the CUSIP inside a US ISIN. Nothing here may fire on a name, on a bare number whose register
    is unnamed, or on a value shape alone.
    """

    # 0000320193 is Apple's CIK; 5468637 is a Delaware file number that happens to look like one.
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        self.index = Path(self.tmp.name) / 'index.sqlite'
        self.fixture.publish('sec_gleif', [
            # the SEC is the registering authority and prints the CIK
            gleif_entity('HWUPKR0MPOU8FGXBT394', 'Apple Inc.', 'RA000665', '0000320193'),
            # same authority, but the entity ID is a registered-fund series ID, not a CIK
            gleif_entity('001GPB6A9XPE8XJICC14', 'A Fund', 'RA000665', 'S000005113'),
            # Delaware, whose file numbers are numeric but are not CIKs
            gleif_entity('5493002789CX3L0CJP65', 'Delaware Co', 'RA000602', '1750'),
            # two LEIs printing one CIK: the declared 1:1 is broken, so neither may link
            gleif_entity('AAAAAAAAAAAAAAAAAA01', 'Twin A', 'RA000665', '1000045'),
            gleif_entity('AAAAAAAAAAAAAAAAAA02', 'Twin B', 'RA000665', '0001000045'),
            gleif_entity('BBBBBBBBBBBBBBBBBB03', 'UK Co', 'RA000585', '3909510'),
            gleif_entity('CCCCCCCCCCCCCCCCCC04', 'No authority', None, None),
            gleif_isin('HWUPKR0MPOU8FGXBT394', 'US0378331005'),       # Apple common stock
            gleif_isin('HWUPKR0MPOU8FGXBT394', 'GB0002634946'),       # a UK ISIN: the NSIN is a SEDOL
            gleif_isin('5493002789CX3L0CJP65', 'US0378331006')])      # check digit does not recompute
        self.fixture.publish('sec_issuer_reference', [entity('sec:cik:0000320193', 'Apple Inc.'),
                                                     entity('sec:cik:0000001750', 'AAR CORP'),
                                                     entity('sec:cik:0001000045', 'NICHOLAS FINANCIAL INC')])
        self.fixture.publish('sec_ownership_datasets', [entity('cusip:037833100', 'APPLE INC COM', 'security')])
        self.fixture.publish('companies_house_uk', [
            entity('gb:companies_house:03909510', 'A UK COMPANY LTD'),
            {'kind': 'assertion', 'id': 'ch:03909510:number', 'subject': 'gb:companies_house:03909510',
             'predicate': 'identifier_assignment', 'value': {'namespace': 'gb_company_number', 'value': '03909510'},
             'observed_at': '2026-01-01', 'evidence': EVIDENCE}])
        self.datasets = ['sec_gleif', 'sec_issuer_reference', 'sec_ownership_datasets', 'companies_house_uk']

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, **kwargs):
        U.unify(self.fixture.catalog, self.fixture.store, index=self.index, datasets=self.datasets,
                publish=False, progress=None)
        report = U.resolve_identities(self.fixture.catalog, self.fixture.store, index=self.index,
                                      workdir=Path(self.tmp.name) / 'resolve', datasets=self.datasets,
                                      progress=None, **kwargs)
        graph = Graph(self.index)
        return report, graph, lambda entity_id: graph.resolved_entity(entity_id)['members']

    def test_gleif_registration_authority_publishes_the_cik_and_the_uk_company_number(self):
        report, _, members = self.resolve()
        self.assertEqual(members('lei:HWUPKR0MPOU8FGXBT394'), ['lei:HWUPKR0MPOU8FGXBT394', 'sec:cik:0000320193'])
        self.assertEqual(members('lei:BBBBBBBBBBBBBBBBBB03'),
                         ['gb:companies_house:03909510', 'lei:BBBBBBBBBBBBBBBBBB03'])
        self.assertEqual(report['counts']['bridge_claims_gleif_sec_cik'], 3)
        self.assertEqual(report['counts']['bridge_claims_gleif_companies_house'], 1)
        self.assertEqual(report['bridges']['gleif_sec_cik']['spec']['cardinality'], '1:1')

    def test_the_authority_code_decides_the_namespace_not_the_value_shape(self):
        # A Delaware file number is numeric, and 1750 is a real CIK. The authority is not the SEC,
        # so nothing is claimed: a coincidence of shape must never become identity.
        _, _, members = self.resolve()
        self.assertEqual(members('lei:5493002789CX3L0CJP65'), ['lei:5493002789CX3L0CJP65'])
        self.assertEqual(members('sec:cik:0000001750'), ['sec:cik:0000001750'])
        # An SEC series ID is a published SEC identifier, but it is not a CIK.
        self.assertEqual(members('lei:001GPB6A9XPE8XJICC14'), ['lei:001GPB6A9XPE8XJICC14'])
        self.assertEqual(members('lei:CCCCCCCCCCCCCCCCCC04'), ['lei:CCCCCCCCCCCCCCCCCC04'])

    def test_a_bridge_value_that_is_not_unique_is_refused_for_clustering(self):
        report, _, members = self.resolve()
        # Two LEIs print CIK 1000045. The spec declares 1:1, so the rows are refused and reported
        # instead of merging two legal entities and a filer into one cluster.
        for lei in ('lei:AAAAAAAAAAAAAAAAAA01', 'lei:AAAAAAAAAAAAAAAAAA02'):
            self.assertEqual(members(lei), [lei])
        self.assertEqual(members('sec:cik:0001000045'), ['sec:cik:0001000045'])
        refusals = [row for row in report['bridge_conflicts'] if row['bridge'] == 'gleif_sec_cik']
        self.assertEqual([row['value'] for row in refusals], ['1000045'])
        self.assertEqual(refusals[0]['collided_with'], ['lei:AAAAAAAAAAAAAAAAAA01', 'lei:AAAAAAAAAAAAAAAAAA02'])
        self.assertEqual(report['counts']['bridge_cardinality_refusals'], 1)

    def test_the_cusip_inside_a_us_isin_joins_a_security_and_never_its_issuer(self):
        _, _, members = self.resolve()
        self.assertEqual(members('cusip:037833100'), ['cusip:037833100', 'isin:US0378331005'])
        # The issuer is a different thing from the security it issued.
        self.assertNotIn('cusip:037833100', members('lei:HWUPKR0MPOU8FGXBT394'))
        # A UK ISIN's NSIN is a SEDOL, and a bad check digit is a malformed row.
        self.assertEqual(members('isin:GB0002634946'), ['isin:GB0002634946'])
        self.assertEqual(members('isin:US0378331006'), ['isin:US0378331006'])

    def test_bridges_can_be_turned_off_and_then_nothing_joins(self):
        report, _, members = self.resolve(bridges=False)
        self.assertEqual(members('lei:HWUPKR0MPOU8FGXBT394'), ['lei:HWUPKR0MPOU8FGXBT394'])
        self.assertEqual(report['bridges'], {})
        self.assertEqual(report['view']['policy']['bridges'], [])
        self.assertFalse([k for k in report['counts'] if k.startswith('bridge_claims')])

    def test_the_default_resolution_attaches_no_inferred_link(self):
        report, graph, _ = self.resolve()
        self.assertFalse(report['view']['policy']['inferred_matches_attached'])
        self.assertFalse(graph.resolution()['view']['policy']['inferred_matches_attached'])
        # Every edge that built a cluster carries a deterministic or source-asserted method.
        import sqlite3
        work = sqlite3.connect(str(Path(self.tmp.name) / 'resolve' / 'identity.sqlite'))
        methods = {row[0] for row in work.execute('SELECT DISTINCT method FROM edges')}
        work.close()
        self.assertTrue(methods)
        self.assertFalse([m for m in methods if not m.startswith(('deterministic:', 'source_asserted:'))])

    def test_bridge_units_in_isolation(self):
        from worldmodel.resolution import bridges
        self.assertEqual(bridges.cusip_from_isin('US0378331005'), '037833100')
        self.assertIsNone(bridges.cusip_from_isin('US0378331006'))   # check digit
        self.assertIsNone(bridges.cusip_from_isin('GB0002634946'))   # NSIN is a SEDOL
        self.assertIsNone(bridges.cusip_from_isin('US03783310'))     # not an ISIN
        self.assertEqual(bridges.gleif_registration_authority_claim(
            {'registration_authority': 'RA000665', 'registration_authority_entity_id': '0000320193'}),
            ('sec_cik', '320193', 'gleif_sec_cik'))
        self.assertEqual(bridges.gleif_registration_authority_claim(
            {'registration_authority': 'RA000585', 'registration_authority_entity_id': 'SC84330'}),
            ('gb_company_number', 'SC084330', 'gleif_companies_house'))
        self.assertEqual(bridges.gleif_registration_authority_claim(
            {'registration_authority': 'RA000585', 'registration_authority_entity_id': '8230688'}),
            ('gb_company_number', '08230688', 'gleif_companies_house'))
        for attributes in ({'registration_authority': 'RA000665', 'registration_authority_entity_id': 'S000005113'},
                           {'registration_authority': 'RA000665', 'registration_authority_entity_id': '805-6204242689'},
                           {'registration_authority': 'RA000602', 'registration_authority_entity_id': '1750'},
                           {'registration_authority': 'RA000665'}, {}):
            self.assertIsNone(bridges.gleif_registration_authority_claim(attributes))
        self.assertEqual(bridges.record_claims({'kind': 'assertion', 'predicate': 'owns', 'object': 'isin:US0378331005'}), [])
        self.assertEqual(bridges.record_claims({'kind': 'entity', 'entity_id': 'sec:cik:0000320193', 'attributes': {
            'registration_authority': 'RA000665', 'registration_authority_entity_id': '1'}}), [])


class CommandLineTests(unittest.TestCase):
    """`wm unify`, `wm unify-scope` and `wm unify-resolve` must stay wired into the CLI."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fixture = Fixture(self.tmp.name)
        self.fixture.publish('census_geography', [entity('geo:US:state:06', 'California', 'state'),
                                                  observation('1', 'geo:US:state:06')])
        self.fixture.publish('usaspending', [entity('uei:ABC', 'Recipient')])

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *argv):
        from worldmodel.cli import execute, parser
        args = parser().parse_args(['--data-root', str(self.fixture.data_root),
                                    '--catalog-root', str(self.fixture.catalog_root), *argv])
        return execute(args)

    def test_unify_scope_inventory_and_selection(self):
        listed = self.run_cli('unify-scope', '--inventory')
        self.assertEqual({row['dataset'] for row in listed['published']}, {'census_geography', 'usaspending'})
        plan = self.run_cli('unify-scope')
        self.assertEqual([row['dataset'] for row in plan['selected']], ['census_geography'])
        self.assertEqual({row['dataset'] for row in plan['skipped']}, {'usaspending'})
        self.assertEqual([row['dataset'] for row in self.run_cli('unify-scope', '--all')['selected']],
                         ['census_geography', 'usaspending'])

    def test_unify_builds_publishes_and_then_resolves(self):
        index = Path(self.tmp.name) / 'cli.sqlite'
        built = self.run_cli('unify', '--index', str(index), '--progress', '0')
        self.assertEqual(built['index']['records'], 2)
        self.assertTrue(index.is_file())
        self.assertEqual(built['artifact']['dataset'], 'world_evidence')
        resolved = self.run_cli('unify-resolve', '--index', str(index), '--progress', '0',
                                '--workdir', str(Path(self.tmp.name) / 'cli-resolve'))
        self.assertEqual(resolved['clusters'], 0)  # nothing to join in this fixture
        self.assertIn('attached', resolved)

    def test_unify_publish_recovers_a_summary_from_an_existing_index(self):
        index = Path(self.tmp.name) / 'cli.sqlite'
        built = self.run_cli('unify', '--index', str(index), '--progress', '0', '--no-publish')
        recovered = self.run_cli('unify-publish', '--index', str(index))
        self.assertTrue(recovered['reconstructed_from_index'])
        self.assertEqual(recovered['inputs'], built['inputs'])
        self.assertEqual(recovered['totals']['records_indexed'], built['totals']['records_indexed'])
        self.assertEqual(self.fixture.store.manifest(recovered['artifact'])['inputs'], built['inputs'])

    def test_unify_rejects_an_unknown_dataset_selection(self):
        with self.assertRaises(ValueError):
            self.run_cli('unify', '--datasets', 'not_a_dataset', '--progress', '0')


class RealDataSmokeTests(unittest.TestCase):
    """Runs against the checkout's real data root when it has published outputs; skips otherwise."""

    @classmethod
    def setUpClass(cls):
        from worldmodel.resources import resource_roots
        roots = resource_roots()
        cls.catalog, cls.store = Catalog(roots['catalog']), Store(roots['data'])
        try:
            available, _ = U.inventory(cls.catalog, cls.store)
        except (OSError, ValueError):
            available = []
        cls.available = {item['dataset']: item for item in available}
        if len(cls.available) < 2:
            raise unittest.SkipTest('no local dataset outputs; run the dataset pipelines first')

    def test_scope_covers_the_real_catalog(self):
        plan = U.scope(self.catalog, self.store)
        self.assertTrue(plan['selected'])
        self.assertEqual({item['dataset'] for item in plan['selected']} & U.BULK_DATASETS, set())
        self.assertGreaterEqual(plan['catalog_rows'], plan['selected_rows'])

    def test_bounded_build_from_the_smallest_real_datasets(self):
        smallest = sorted(self.available.values(), key=lambda item: item['rows'] or 0)[:2]
        with tempfile.TemporaryDirectory() as tmp:
            index = Path(tmp) / 'index.sqlite'
            result = U.unify(self.catalog, self.store, datasets=[item['dataset'] for item in smallest],
                             index=index, publish=False, progress=None, limit=500)
            self.assertEqual(result['index']['records'], result['totals']['records_indexed'])
            self.assertLessEqual(result['totals']['records_indexed'], 1000)
            self.assertEqual([ref['dataset'] for ref in result['inputs']],
                             sorted(item['dataset'] for item in smallest))


if __name__ == '__main__':
    unittest.main()
