import random
import tempfile
import unittest
from pathlib import Path

from worldmodel.coverage import coverage_report
from worldmodel.identity import IdentityIndex
from worldmodel.model import validate_record
from worldmodel.resolution import (FellegiSunter, ResolutionEngine, TfIdf, inputs_from_records, jaro_winkler, link_mapping,
                                   normalize_organization, normalize_person, shared_identifier_links, soundex, transliterate)
from worldmodel.resolution.synthetic import organizations, true_pairs

EVIDENCE = [{'input': {'dataset': 'fixture', 'version': 'a' * 64}, 'record_id': 'fixture:1'}]


def cluster_pairs(engine):
    pairs = set()
    for cluster in engine.clusters():
        members = cluster['members']
        pairs.update((a, b) for a in members for b in members if a < b)
    return pairs


class NormalizationTests(unittest.TestCase):
    def test_names_suffixes_transliteration(self):
        a = normalize_organization('The Acme Widgets Co., L.L.C.')
        self.assertEqual((a['normalized'], a['legal_forms']), ('acme widgets', ['co', 'llc']))
        self.assertEqual(normalize_organization('ACME WIDGETS COMPANY LLC')['normalized'], 'acme widgets')
        self.assertEqual(normalize_organization('Deutsche Bank Aktiengesellschaft')['legal_forms'], ['ag'])
        self.assertEqual(transliterate('Газпром Нефть'), 'Gazprom Neft')
        self.assertEqual(transliterate('Société Générale'), 'Societe Generale')
        person = normalize_person('Smith, John A. Jr.')
        self.assertEqual((person['given'], person['family'], person['suffixes']), ('john', 'smith', ['jr']))
        self.assertEqual(normalize_person('Sen. Maria Cantwell')['honorifics'], ['sen'])
        self.assertEqual(soundex('Robert'), soundex('Rupert'))

    def test_similarity_reference_values(self):
        self.assertAlmostEqual(jaro_winkler('MARTHA', 'MARHTA'), 0.9611, places=4)
        self.assertAlmostEqual(jaro_winkler('DWAYNE', 'DUANE'), 0.84, places=4)
        self.assertAlmostEqual(jaro_winkler('DIXON', 'DICKSONX'), 0.8133, places=4)
        self.assertEqual(jaro_winkler('', ''), 0.0)
        idf = TfIdf.fit([['acme', 'widgets'], ['acme', 'foods'], ['globex']])
        self.assertGreater(idf.cosine(['acme', 'widgets'], ['widgets', 'acme']), 0.999)
        self.assertLess(idf.cosine(['acme', 'widgets'], ['acme', 'foods']), 0.5)


class FellegiSunterTests(unittest.TestCase):
    def test_em_recovers_generating_parameters(self):
        rng = random.Random(3)
        m, u, lam = [[0.05, 0.15, 0.8], [0.1, 0.9]], [[0.85, 0.1, 0.05], [0.95, 0.05]], 0.2
        counts = {}
        for _ in range(40000):
            probs = m if rng.random() < lam else u
            pattern = tuple(rng.choices(range(len(p)), p)[0] for p in probs)
            if rng.random() < 0.1:
                pattern = (pattern[0], None)
            counts[pattern] = counts.get(pattern, 0) + 1
        model = FellegiSunter([('name', 3), ('postal', 2)]).fit(counts)
        self.assertTrue(model.converged)
        self.assertAlmostEqual(model.lam, lam, delta=0.03)
        self.assertAlmostEqual(model.m[0][2], 0.8, delta=0.05)
        self.assertAlmostEqual(model.u[1][1], 0.05, delta=0.03)
        self.assertGreater(model.probability((2, 1)), 0.95)
        self.assertLess(model.probability((0, 0)), 0.01)
        self.assertEqual(model.weight((2, None)), FellegiSunter.from_dict(model.to_dict()).weight((2, None)))


class DeterministicLinkTests(unittest.TestCase):
    def test_mapping_cardinality_normalization_and_scope(self):
        rows = [{'left': '5493001KJTIIGC8Y1R12', 'right': '0000320193'},
                {'left': '5493001KJTIIGC8Y1R12', 'right': '999', 'valid_from': '2010-01-01'},
                {'left': 'lei2lei2lei2lei2lei2', 'right': '42'}]
        result = link_mapping('gleif_sec_cik', rows, observed_at='2026-09-15', evidence=EVIDENCE)
        self.assertEqual(result['linked'], 1)
        self.assertEqual(result['assertions'][0]['object'], 'sec_cik:42')
        self.assertEqual(result['assertions'][0]['subject'], 'lei:LEI2LEI2LEI2LEI2LEI2')
        self.assertEqual(len(result['conflicts']), 1)
        for assertion in result['assertions']:
            validate_record(assertion)
        dated = link_mapping('gleif_sec_cik', [{'left': 'A', 'right': '1', 'valid_to': '2015-01-01'},
                                               {'left': 'A', 'right': '2', 'valid_from': '2015-01-01'}],
                             observed_at='2026-09-15', evidence=EVIDENCE)
        self.assertEqual((dated['linked'], dated['conflicts']), (2, []))
        with self.assertRaisesRegex(ValueError, 'scope'):
            link_mapping('sec_cik_ticker', [{'left': '320193', 'right': 'AAPL'}], observed_at='2026-09-15', evidence=EVIDENCE)
        ticker = link_mapping('sec_cik_ticker', [{'left': '320193', 'right': 'aapl', 'scope': 'XNAS'}],
                              observed_at='2026-09-15', evidence=EVIDENCE)
        self.assertEqual(ticker['assertions'][0]['object'], 'ticker:XNAS:AAPL')
        self.assertEqual(ticker['assertions'][0]['attributes']['temporal_validity'], 'unknown')

    def test_shared_identifier_links_and_conflicts(self):
        def assign(key, subject, value, **extra):
            return {'kind': 'assertion', 'id': key, 'subject': subject, 'predicate': 'identifier_assignment',
                    'value': {'namespace': 'lei', 'value': value}, 'observed_at': '2020-01-01', 'evidence': EVIDENCE, **extra}
        rows = [assign('i:1', 'gleif:a', 'X1'), assign('i:2', 'sec:b', 'x1'), assign('i:3', 'fec:c', 'X1'),
                assign('i:4', 'bad:d', 'X2'), assign('i:5', 'bad:d', 'X3'), assign('i:6', 'ok:e', 'X2')]
        result = shared_identifier_links(rows, observed_at='2026-09-15', evidence=EVIDENCE)
        self.assertEqual(sorted((a['subject'], a['object']) for a in result['assertions']),
                         [('gleif:a', 'fec:c'), ('sec:b', 'fec:c')])
        self.assertEqual(result['conflicts'][0]['subject'], 'bad:d')


class EngineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.items = list(organizations(4000, seed=11))
        cls.truth = true_pairs(cls.items)

    def test_quality_determinism_and_explicit_assertions(self):
        engine = ResolutionEngine(':memory:')
        view = engine.run(self.items)
        self.assertEqual(engine.stats['model']['training'], 'identifier')
        pairs = cluster_pairs(engine)
        true_positive = len(pairs & self.truth)
        self.assertGreater(true_positive / len(pairs), 0.95)
        self.assertGreater(true_positive / len(self.truth), 0.8)
        self.assertLess(engine.stats['blocking']['candidate_pairs'], engine.stats['blocking']['all_pairs'] / 100)
        again = ResolutionEngine(':memory:')
        self.assertEqual(again.run(list(reversed(self.items)))['view_digest'], view['view_digest'])
        assertion = next(engine.match_assertions(observed_at='2026-09-15', evidence=EVIDENCE))
        validate_record(assertion)
        self.assertEqual((assertion['predicate'], assertion['epistemic_status'], assertion['match']['reviewer_status']),
                         ('same_as', 'inferred', 'unreviewed'))
        self.assertIn('features', assertion['match'])

    def test_reviews_constraints_and_file_backed_workers(self):
        with tempfile.TemporaryDirectory() as tmp:
            engine = ResolutionEngine(Path(tmp) / 'r.sqlite', max_block_size=50)
            engine.add_records(self.items)
            blocking = engine.candidate_pairs()
            self.assertGreaterEqual(blocking['oversized_blocks'], 0)
            self.assertEqual(engine.compare(workers=2, chunk_size=500), blocking['candidate_pairs'])
            engine.estimate()
            engine.cluster()
            linked = next(c for c in engine.clusters() if len(c['members']) == 2)
            a, b = linked['members']
            engine.add_reviews([{'a': a, 'b': b, 'status': 'rejected', 'reviewer': 'analyst', 'reason': 'different registrants'}])
            engine.add_reviews([{'a': self.items[0]['entity_id'], 'b': self.items[-1]['entity_id'], 'status': 'accepted',
                                 'reviewer': 'analyst', 'reason': 'fixture'}])
            result = engine.cluster()
            clusters = {m: c['canonical_id'] for c in engine.clusters() for m in c['members']}
            self.assertNotEqual(clusters.get(a, a), clusters.get(b, b))
            self.assertEqual(clusters[self.items[0]['entity_id']], clusters[self.items[-1]['entity_id']])
            self.assertIn('policy', result)
            view = engine.resolved_view()
            self.assertEqual(len(view['reviews']), 2)
            with self.assertRaises(ValueError):
                engine.add_reviews([{'a': a, 'b': b, 'status': 'maybe', 'reviewer': 'x', 'reason': 'y'}])
            engine.close()

    def test_identifier_conflicts_block_merges_and_person_mode(self):
        items = [{'entity_id': 'a:1', 'name': 'Northwind Traders Inc', 'postal': '10001', 'identifiers': {'lei': 'L1'}},
                 {'entity_id': 'b:1', 'name': 'Northwind Traders Incorporated', 'postal': '10001', 'identifiers': {'lei': 'L2'}},
                 {'entity_id': 'c:1', 'name': 'Northwind Traders', 'postal': '10001'}]
        engine = ResolutionEngine(':memory:')
        engine.add_records(items)
        engine.candidate_pairs()
        engine.compare()
        engine.estimate(training='em', u_sample=0)
        engine.cluster(threshold=0.5, lower=0.01)
        clusters = [set(c['members']) for c in engine.clusters()]
        self.assertFalse(any({'a:1', 'b:1'} <= c for c in clusters))
        people = ResolutionEngine(':memory:', kind='person')
        people.add_records([{'entity_id': 'x:1', 'name': 'Cantwell, Maria', 'birth_year': 1958},
                            {'entity_id': 'y:1', 'name': 'Maria Cantwell', 'birth_year': 1958}])
        self.assertEqual(people.candidate_pairs()['candidate_pairs'], 1)
        people.compare()
        self.assertEqual(people.db.execute('SELECT pattern FROM pairs').fetchone()[0], '3221__')

    def test_inputs_from_records(self):
        records = [{'kind': 'entity', 'id': 'r:1', 'entity_id': 'org:a', 'entity_type': 'organization', 'label': 'Acme Inc',
                    'observed_at': '2020-01-01', 'evidence': EVIDENCE, 'attributes': {'postal_code': '10001'}},
                   {'kind': 'assertion', 'id': 'r:2', 'subject': 'org:a', 'predicate': 'identifier_assignment',
                    'value': {'namespace': 'lei', 'value': 'L1'}, 'observed_at': '2020-01-01', 'evidence': EVIDENCE}]
        item = next(inputs_from_records(records))
        self.assertEqual((item['source'], item['postal'], item['identifiers']), ('fixture', '10001', {'lei': ['L1']}))


class IdentityPolicyTests(unittest.TestCase):
    def entity(self, key):
        return {'id': key, 'kind': 'entity', 'entity_type': 'organization', 'label': 'Same', 'observed_at': '2020-01-01',
                'evidence': EVIDENCE}

    def link(self, key, status, **extra):
        return {'id': key, 'kind': 'assertion', 'subject': 'org:a', 'predicate': 'same_as', 'object': 'org:b',
                'observed_at': '2020-01-01', 'evidence': EVIDENCE, 'epistemic_status': 'inferred',
                'match': {'method': 'fellegi_sunter_em', 'score': 0.99, 'reviewer_status': status}, **extra}

    def test_inferred_links_join_only_when_accepted(self):
        base = [self.entity('org:a'), self.entity('org:b'),
                {'id': 'id:1', 'kind': 'assertion', 'subject': 'org:b', 'predicate': 'identifier_assignment',
                 'value': {'namespace': 'lei', 'value': 'L1'}, 'observed_at': '2020-01-01', 'evidence': EVIDENCE}]
        for status, joined in (('unreviewed', False), ('needs_review', False), ('rejected', False), ('accepted', True),
                               ('source_asserted', True)):
            index = IdentityIndex(base + [self.link('m:1', status)])
            result = index.resolve_identifier('lei', 'L1', '2021-01-01', '2021-01-01')
            self.assertEqual(result['matches'][0]['entity_ids'] == ['org:a', 'org:b'], joined, status)
        unreviewed = IdentityIndex(base + [self.link('m:1', 'unreviewed')])
        self.assertEqual([r['id'] for r in unreviewed.candidate_links('org:a', '2021-01-01', '2021-01-01')], ['m:1'])
        opted = IdentityIndex(base + [self.link('m:1', 'unreviewed')], accept_matches='all')
        self.assertEqual(opted.resolve_identifier('lei', 'L1', '2021-01-01', '2021-01-01')['matches'][0]['canonical_id'], 'org:a')

    def test_coverage_reports_link_review_status_and_units(self):
        rows = [self.entity('org:a'), self.entity('org:b'), self.link('m:1', 'unreviewed'),
                {'kind': 'observation', 'id': 'o:1', 'metric': 'x', 'value': 1, 'unit': 'furlongs'},
                {'kind': 'observation', 'id': 'o:2', 'metric': 'x', 'value': 1, 'unit': 'USD'}]
        report = coverage_report(rows, {}, [])
        self.assertEqual(report['identity_links']['same_as_by_review_status'], {'unreviewed': 1})
        self.assertEqual(report['units']['unparseable'], {'furlongs': 1})


if __name__ == '__main__':
    unittest.main()
