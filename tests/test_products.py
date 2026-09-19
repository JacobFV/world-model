"""Evidence products (dossier, screen, place brief, products index) on a fictional temp index.

Every name, identifier and number below is invented. The shapes mirror what the real publishers
emit into the unified index (see docs/products.md), so the products exercise the same queries.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from worldmodel.graph import Graph
from worldmodel.products.base import ProductError, rights_block
from worldmodel.products.dossier import dossier
from worldmodel.products.place import place_brief
from worldmodel.products.screen import screen
from worldmodel.products.search import build as build_products_index, norm, search, open_products_index
from worldmodel.products.base import connect, index_info

COUNTY = 'geo:US:county:01999'
LEI = 'lei:TESTLEI0000000000001'


def version(n):
    return format(n, 'x').rjust(64, '0')


def evidence(dataset):
    return [{'input': {'dataset': dataset, 'artifact': 'a' * 64}, 'locator': 'fixture'}]


def entity(dataset, key, label, typ='organization', **attributes):
    return {'kind': 'entity', 'id': '%s:entity:%s' % (dataset, key), 'entity_id': key, 'entity_type': typ,
            'label': label, 'observed_at': '2026-09-15', 'evidence': evidence(dataset),
            'attributes': {'source_dataset': dataset, **attributes}}


def edge(dataset, n, subject, predicate, obj, **extra):
    return {'kind': 'assertion', 'id': '%s:edge:%d' % (dataset, n), 'subject': subject, 'predicate': predicate,
            'object': obj, 'observed_at': '2026-09-15', 'evidence': evidence(dataset), **extra}


def literal(dataset, n, subject, predicate, value):
    return {'kind': 'assertion', 'id': '%s:literal:%d' % (dataset, n), 'subject': subject, 'predicate': predicate,
            'value': value, 'observed_at': '2026-09-15', 'evidence': evidence(dataset)}


def observation(dataset, n, subject, metric, value, unit, when, **dimensions):
    return {'kind': 'observation', 'id': '%s:obs:%d' % (dataset, n), 'subject': subject, 'metric': metric,
            'value': value, 'unit': unit, 'valid_from': when, 'dimensions': dimensions,
            'observed_at': '2026-09-15', 'evidence': evidence(dataset)}


def event(dataset, n, event_type, when, participants, **attributes):
    return {'kind': 'event', 'id': '%s:event:%d' % (dataset, n), 'event_type': event_type, 'occurred_at': when,
            'participants': participants, 'observed_at': '2026-09-15', 'evidence': evidence(dataset),
            'attributes': attributes}


def fixture_records():
    groups = {
        'census_business': [observation('census_business', i, COUNTY, 'employment', 1000 + i, 'people',
                                        '%d-03-12' % (2019 + i), industry='all') for i in range(4)]
        + [observation('census_business', 10, COUNTY, 'employment', 40, 'people', '2022-03-12', industry='11')],
        'census_geography': [entity('census_geography', COUNTY, 'Fixture County, Alabama', 'county'),
                             edge('census_geography', 1, COUNTY, 'within', 'geo:US:state:01'),
                             observation('census_geography', 1, COUNTY, 'latitude', 32.5, 'degrees', '2024-01-01'),
                             observation('census_geography', 2, COUNTY, 'longitude', -86.6, 'degrees', '2024-01-01')],
        'census_population': [observation('census_population', i, COUNTY, 'population', 5000 + 10 * i, 'people',
                                          '%d-07-01' % (2018 + i), vintage=2024) for i in range(6)],
        'fema_nri': [observation('fema_nri', 1, COUNTY, 'national_risk_index_score', 42.0, 'score', '2025-12-01'),
                     observation('fema_nri', 2, COUNTY, 'expected_annual_loss', 12345.0, 'USD', '2025-12-01',
                                 hazard='tornado', consequence='total')],
        'gleif_parent_relationships': [
            edge('gleif_parent_relationships', 1, 'lei:TESTLEI0000000000002', 'directly_consolidated_by', LEI)],
        'noaa_climdiv': [observation('noaa_climdiv', m, COUNTY, 'average_temperature', 60.0 + m % 12, 'degF',
                                     '%d-%02d-01' % (2020 + m // 12, m % 12 + 1), frequency='monthly')
                         for m in range(36)],
        'noaa_storm_events': [
            event('noaa_storm_events', 1, 'tornado', '2011-04-27T20:00:00Z', ['noaa:storm_event:1', COUNTY],
                  damage_property=250000, deaths_direct=2, injuries_direct=5, begin_lat=32.4, begin_lon=-86.5,
                  event_type_label='Tornado'),
            event('noaa_storm_events', 2, 'hail', '2015-05-01T20:00:00Z', ['noaa:storm_event:2', COUNTY],
                  damage_property=1000, event_type_label='Hail'),
            event('noaa_storm_events', 3, 'heat', '2016-07-01T20:00:00Z', ['noaa:storm_event:3', 'noaa:zone:01:Z:001'],
                  event_type_label='Heat')],
        'ofac_sanctions': [
            entity('ofac_sanctions', 'ofac:party:1', 'ACME PETROL OAO', source_file='SDN_ADVANCED',
                   list_publication_date='2026-09-14'),
            literal('ofac_sanctions', 1, 'ofac:party:1', 'identifier',
                    {'id': LEI, 'number': 'TESTLEI0000000000001', 'scheme': 'Legal Entity Number'}),
            literal('ofac_sanctions', 2, 'ofac:party:1', 'sanctions_alias', {'name': 'Acme Oil Company'}),
            edge('ofac_sanctions', 3, 'ofac:party:1', 'subject_to_sanctions_program', 'ofac:program:TEST'),
            edge('ofac_sanctions', 4, 'ofac:party:1', 'located_in', 'iso3:RUS')],
        'openfema': [
            entity('openfema', COUNTY, 'Fixture (County)', 'county'),
            event('openfema', 1, 'disaster_declaration', '2011-04-28T00:00:00Z', ['fema:disaster:9001', COUNTY],
                  incidentType='Tornado', femaDeclarationString='DR-9001-AL', declarationTitle='SEVERE STORMS'),
            observation('openfema', 1, 'fema:disaster:9001', 'ihp_amount_approved', 1500.0, 'USD', '2011-05-01',
                        county='Fixture (County)', state='AL', zip_code='36000'),
            observation('openfema', 2, 'fema:disaster:9001', 'ihp_amount_approved', 500.0, 'USD', '2011-05-01',
                        county='Fixture (County)', state='AL', zip_code='36001'),
            observation('openfema', 3, 'fema:disaster:9001', 'ihp_amount_approved', 999.0, 'USD', '2011-05-01',
                        county='Other (County)', state='AL', zip_code='36002')],
        'opensanctions_graph': [
            entity('opensanctions_graph', 'opensanctions:NK-acme', 'Acme Petrol PJSC', topics=['sanction'],
                   datasets=['us_ofac_sdn']),
            entity('opensanctions_graph', 'opensanctions:NK-sub', 'Acme Trading GmbH', topics=['sanction.linked']),
            entity('opensanctions_graph', 'opensanctions:NK-sub2', 'Acme Shipping Ltd', topics=[]),
            entity('opensanctions_graph', 'opensanctions:Q1', 'Jane Fixture', 'person', topics=['role.pep']),
            literal('opensanctions_graph', 1, 'opensanctions:NK-acme', 'identifier', {'id': LEI}),
            edge('opensanctions_graph', 2, 'opensanctions:NK-acme', 'owns', 'opensanctions:NK-sub'),
            edge('opensanctions_graph', 3, 'opensanctions:NK-sub', 'owns', 'opensanctions:NK-sub2'),
            edge('opensanctions_graph', 4, 'opensanctions:Q1', 'director_of', 'opensanctions:NK-sub2')],
        'sec_gleif': [
            entity('sec_gleif', LEI, 'Acme Petrol Public Joint Stock Company', jurisdiction='RU'),
            entity('sec_gleif', 'lei:TESTLEI0000000000002', 'Acme Europe BV', jurisdiction='NL'),
            entity('sec_gleif', 'lei:TESTLEI0000000000009', 'Clean Holdings Inc', jurisdiction='US'),
            edge('sec_gleif', 1, LEI, 'issuer_security', 'isin:US0000000001')],
        'sec_ownership_datasets': [
            entity('sec_ownership_datasets', 'sec:cik:0000000009', 'Fixture Asset Management LLC'),
            entity('sec_ownership_datasets', 'cusip:000000000', 'ACME PETROL COM', 'security'),
            edge('sec_ownership_datasets', 1, 'sec:cik:0000000009', 'reported_holding', 'cusip:000000000'),
            edge('sec_ownership_datasets', 2, 'sec:cik:0000000008', 'reported_holding', 'cusip:000000000')],
    }
    refs = [({'dataset': name, 'stage': 'normalized', 'version': version(i + 1)}, records)
            for i, (name, records) in enumerate(sorted(groups.items()))]
    clusters = [{'canonical_id': LEI, 'members': [LEI, 'ofac:party:1', 'opensanctions:NK-acme']},
                {'canonical_id': 'isin:US0000000001', 'members': ['isin:US0000000001', 'cusip:000000000']}]
    return refs, clusters


def build_fixture(root):
    """A compressed-body schema-3 index with an attached resolution, and a data root with rights."""
    root = Path(root)
    refs, clusters = fixture_records()
    index = root / 'world_evidence' / 'index.sqlite'
    graph = Graph(index)
    graph.build_from_records(refs, compress_bodies=True)
    graph.attach_resolution(clusters, view={'view_digest': 'f' * 64, 'policy': {'inferred_matches_attached': False}})
    for ref, _ in refs:  # compact manifests carrying rights, as the real tracked manifests do
        directory = root / ref['dataset'] / 'manifests' / 'normalized'
        directory.mkdir(parents=True, exist_ok=True)
        non_commercial = ref['dataset'].startswith('opensanctions')
        metadata = {'publisher': 'Fixture publisher', 'license_id': 'CC-BY-NC-4.0' if non_commercial else 'CC0-1.0',
                    'license_status': 'non_commercial_only' if non_commercial else 'open',
                    'redistribution': 'restricted' if non_commercial else 'allowed'}
        (directory / (ref['version'] + '.json')).write_text(json.dumps({'rights': {
            'sources': [{'input': {'dataset': ref['dataset']}, 'metadata': metadata, 'terms_unspecified': False}],
            'redistribution_review_required': non_commercial}}))
    return index


class ProductsFixture(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.index = build_fixture(cls.root)
        cls.products = cls.root / 'world_evidence' / 'products.sqlite'
        cls.built = build_products_index(cls.index, cls.products)
        cls.options = {'index': cls.index, 'products_index': cls.products, 'data_root': cls.root,
                       'catalog_root': cls.root}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()


class ProductsIndexTests(ProductsFixture):
    def test_build_counts_and_pinning(self):
        counts = self.built['counts']
        self.assertGreater(counts['labels'], 10)
        self.assertEqual(counts['aliases'], 1)
        self.assertEqual(counts['storm_events_with_a_county'], 2)
        self.assertEqual(counts['storm_events_on_a_forecast_zone'], 1)
        self.assertEqual(counts['disaster_declarations_with_a_county'], 1)
        self.assertEqual(counts['fema_named_county_groups'], 2)
        connection = connect(self.index)
        self.assertEqual(self.built['inputs_digest'], index_info(connection, self.index)['inputs_digest'])
        connection.close()

    def test_search_groups_by_asserted_cluster_and_labels_it_inferred(self):
        connection = connect(self.index)
        products = open_products_index(self.products, index_info(connection, self.index))
        found = search(connection, products, 'acme petrol')
        top = found['candidates'][0]
        self.assertEqual(top['canonical_id'], LEI)
        self.assertTrue(top['asserted_cluster'])
        self.assertIn('INFERRED', top['match_basis'])
        # The alias the OFAC listing prints is searchable too.
        self.assertEqual(search(connection, products, 'acme oil company')['candidates'][0]['canonical_id'], LEI)
        connection.close()

    def test_stale_products_index_is_refused(self):
        connection = connect(self.index)
        info = dict(index_info(connection, self.index), inputs_digest='0' * 64)
        with self.assertRaises(ProductError):
            open_products_index(self.products, info)
        connection.close()

    def test_norm(self):
        self.assertEqual(norm('  Société  "Générale", S.A. '), 'societe generale s a')


class DossierTests(ProductsFixture):
    def test_dossier_by_id_resolves_the_asserted_cluster(self):
        result = dossier('ofac:party:1', **self.options)
        answer = result['answer']
        self.assertEqual(answer['entity']['canonical_id'], LEI)
        self.assertEqual(answer['asserted_identity_cluster']['members'],
                         [LEI, 'ofac:party:1', 'opensanctions:NK-acme'])
        shared = answer['asserted_identity_cluster']['identity_basis']['shared_identifier_values']
        self.assertEqual(shared[0]['identifier'], LEI)
        groups = {(g['direction'], g['predicate']): g for g in answer['edges_by_predicate_and_dataset']}
        self.assertEqual(groups[('out', 'owns')]['by_dataset'], {'opensanctions_graph': 1})
        self.assertEqual(groups[('in', 'directly_consolidated_by')]['by_dataset'], {'gleif_parent_relationships': 1})
        for group in groups.values():
            for example in group['examples']:
                self.assertTrue(example['from_dataset'])
        hop1 = {n['entity_id'] for n in answer['counterparties']['hop_1']}
        hop2 = {n['entity_id'] for n in answer['counterparties']['hop_2']}
        self.assertIn('opensanctions:NK-sub', hop1)
        self.assertIn('isin:US0000000001', hop1)
        self.assertIn('opensanctions:NK-sub2', hop2)
        self.assertIn('sec:cik:0000000009', hop2)  # a 13F holder, through the ISIN/CUSIP cluster
        # Reference predicates are reported, not expanded.
        self.assertNotIn('iso3:RUS', hop1)
        self.assertTrue(any(e['object'] == 'iso3:RUS'
                            for e in answer['counterparties']['classifications_places_and_programs']))
        self.assertTrue(any('sanctions determination' in s for s in result['what_this_does_not_establish']))
        self.assertTrue(result['where_the_evidence_runs_out'])
        self.assertIn('opensanctions_graph', result['rights']['non_commercial_only'])
        self.assertEqual(result['rights']['declared_purpose'] in ('commercial', 'non_commercial'), True)
        self.assertIn('timings_s', result)

    def test_dossier_by_name_says_the_match_is_inferred(self):
        result = dossier('Acme Petrol', **self.options)
        selection = result['answer']['entity']['selection']
        self.assertIn('INFERRED, not asserted', selection['basis'])
        self.assertEqual(result['answer']['entity']['canonical_id'], LEI)

    def test_dossier_unknown_input_is_not_a_finding(self):
        result = dossier('lei:NOSUCH', **self.options)
        self.assertFalse(result['answer']['found'])
        self.assertTrue(result['what_this_does_not_establish'])

    def test_name_without_products_index_is_explained(self):
        result = dossier('Acme Petrol', **{**self.options, 'products_index': self.root / 'missing.sqlite'})
        self.assertEqual(result['answer']['resolution']['kind'], 'unresolved')
        self.assertIn('products-index', result['answer']['resolution']['reason'])

    def test_html_report_is_standalone_and_carries_the_caveats(self):
        from worldmodel.products.html import render_dossier
        html = render_dossier(dossier(LEI, **self.options))
        self.assertIn('What this does not establish', html)
        self.assertIn('Rights and use policy', html)
        self.assertNotIn('<script src', html)
        self.assertNotIn('http://', html.split('<body>')[1].replace('http://www.w3.org/2000/svg', ''))


class ScreenTests(ProductsFixture):
    def run_screen(self, entries, **kwargs):
        result = screen(entries, **{**self.options, **kwargs})
        return {item['input']: item for item in result['answer']['results']}, result

    def test_statuses_hops_paths_and_datasets(self):
        results, whole = self.run_screen(['ofac:party:1', 'opensanctions:NK-sub2', 'lei:TESTLEI0000000000002',
                                          'lei:TESTLEI0000000000009', 'sec:cik:0000000009'])
        self.assertEqual(results['ofac:party:1']['status'], 'listed')
        self.assertEqual(results['ofac:party:1']['screened'][0]['hops'], 0)
        sub2 = results['opensanctions:NK-sub2']['screened'][0]
        self.assertEqual((sub2['status'], sub2['hops']), ('path_found', 2))
        steps = sub2['nearest_listings'][0]['paths'][0]['steps']
        self.assertEqual([s['predicate'] for s in steps], ['owns', 'owns'])
        self.assertEqual({s['from_dataset'] for s in steps}, {'opensanctions_graph'})
        self.assertEqual(sub2['nearest_listings'][0]['listed_cluster'], LEI)
        europe = results['lei:TESTLEI0000000000002']['screened'][0]
        self.assertEqual((europe['status'], europe['hops']), ('path_found', 1))
        clean = results['lei:TESTLEI0000000000009']
        self.assertEqual(clean['status'], 'no_path_within_bound')
        self.assertTrue(clean['absence_of_a_path_is_not_clearance'])
        # Holdings are not traversed by default ...
        self.assertEqual(results['sec:cik:0000000009']['status'], 'no_path_within_bound')
        # The lists consulted carry their rights even when no path is found.
        self.assertIn('opensanctions_graph', whole['rights']['non_commercial_only'])
        gaps = ' '.join(whole['answer']['coverage_gaps'])
        for phrase in ('not clearance', 'reporting thresholds', 'long-only US equity', 'non-commercial use only'):
            self.assertIn(phrase, gaps)

    def test_holdings_run_holder_to_issuer_only(self):
        results, _ = self.run_screen(['sec:cik:0000000009', 'sec:cik:0000000008'], groups=('holdings',))
        holder = results['sec:cik:0000000009']['screened'][0]
        self.assertEqual((holder['status'], holder['hops']), ('path_found', 2))
        steps = holder['nearest_listings'][0]['paths'][0]['steps']
        self.assertEqual([(s['predicate'], s['direction']) for s in steps],
                         [('reported_holding', 'forward'), ('issuer_security', 'reverse')])
        # Two holders of one security are not related through it.
        search = results['sec:cik:0000000008']['screened'][0]['search']
        self.assertEqual(search['nodes_reached'], 2)

    def test_hop_bound_and_names(self):
        results, _ = self.run_screen(['opensanctions:NK-sub2'], hops=1)
        self.assertEqual(results['opensanctions:NK-sub2']['status'], 'no_path_within_bound')
        results, _ = self.run_screen(['Acme Shipping'])
        item = results['Acme Shipping']
        self.assertEqual(item['input_kind'], 'name')
        self.assertIn('INFERRED', item['screened'][0]['match_basis'])
        results, _ = self.run_screen(['Nobody Whatsoever Ltd'])
        self.assertEqual(results['Nobody Whatsoever Ltd']['status'], 'not_resolved')

    def test_flags_are_not_listings(self):
        results, _ = self.run_screen(['opensanctions:NK-sub'], hops=1)
        screened = results['opensanctions:NK-sub']['screened'][0]
        self.assertEqual(screened['status'], 'path_found')
        self.assertEqual([f['topic'] for f in screened['own_flags']], ['sanction.linked'])

    def test_bounds_are_enforced(self):
        with self.assertRaises(ProductError):
            screen(['x:y'], hops=9, **self.options)
        with self.assertRaises(ProductError):
            screen(['x:y'], groups=('everything',), **self.options)


class PlaceTests(ProductsFixture):
    def test_brief_legs_and_event_joins(self):
        result = place_brief('01999', **self.options)
        answer = result['answer']
        self.assertEqual(answer['employment_and_business']['metrics']['employment']['headline']['value'], 1003)
        self.assertEqual([p['value'] for p in answer['series']['population']][-1], 5050)
        self.assertEqual(answer['storms']['events'], 2)
        tornado = next(t for t in answer['storms']['by_event_type'] if t['event_type'] == 'tornado')
        self.assertEqual((tornado['deaths'], tornado['damage_property_usd_nominal']), (2.0, 250000.0))
        self.assertEqual(answer['disaster_assistance']['declarations']['count'], 1)
        named = answer['disaster_assistance']['household_and_public_assistance']
        self.assertIn('INFERRED', named['join_basis'])
        self.assertEqual(named['totals_by_metric'][0]['total'], 2000.0)  # the other county's 999 is excluded
        self.assertEqual(answer['jobs']['observations'], 0)
        self.assertTrue(any('forecast zone' in s for s in result['where_the_evidence_runs_out']))
        climate = answer['weather_and_climate']['annual_means_computed_here']['average_temperature']
        self.assertEqual(climate['years'], 3)
        self.assertIn('census_business', result['datasets_used'])
        self.assertNotIn('lehd_lodes', result['datasets_used'])  # expected, but contributed nothing

    def test_brief_by_name_and_html(self):
        from worldmodel.products.html import render_place
        result = place_brief('Fixture County, AL', **self.options)
        self.assertEqual(result['answer']['county']['entity_id'], COUNTY)
        self.assertIn('INFERRED', result['answer']['county']['selection']['basis'])
        html = render_place(result)
        self.assertIn('Storm event begin points', html)
        self.assertIn('<svg', html)
        with self.assertRaises(ProductError):
            place_brief('Nowhere County, AL', **self.options)


class RightsTests(unittest.TestCase):
    def test_missing_manifest_is_reported_not_guessed(self):
        with tempfile.TemporaryDirectory() as root:
            block = rights_block(['nosuch'], {'nosuch': {'stage': 'normalized', 'version': 'a' * 64}},
                                 data_root=root, catalog_root=root)
        row = block['datasets'][0]
        self.assertEqual(row['rights_metadata_from'], 'not found')
        self.assertTrue(row['redistribution_review_required'])
        self.assertEqual(row['identified_persons']['policy'], 'prohibited')


class ReadOnlyTests(ProductsFixture):
    def test_products_never_write_the_index(self):
        before = self.index.stat().st_mtime_ns, self.index.read_bytes()
        dossier(LEI, **self.options)
        screen([LEI], **self.options)
        place_brief('01999', **self.options)
        self.assertEqual(before, (self.index.stat().st_mtime_ns, self.index.read_bytes()))
        with self.assertRaises(sqlite3.OperationalError):
            connect(self.index).execute('DELETE FROM resolved')


if __name__ == '__main__':
    unittest.main()
