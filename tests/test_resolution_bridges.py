"""Published crosswalks added to the asserted identity layer, and the refusals that keep them honest.

Every case is a field a publisher prints: the CSL's OFAC profile ID, an OFAC identity document's
scheme and issuing country, GLEIF's BIC-to-LEI map, an OpenSanctions QID entity ID, an ORCID iD
used as an entity ID. Nothing here may fire on a name, on a number whose register is unnamed, on a
value its own publisher flags as fraudulent, or across IMO's two number series.
"""
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from test_unify_scale import EVIDENCE, Fixture, entity

from worldmodel import unify as U
from worldmodel.graph import Graph
from worldmodel.resolution import bridges
from worldmodel.resolution.history import export_resolution
from worldmodel.resolution.join_coverage import join_coverage, publisher_family, scope_join_coverage


def ident(key, subject, value, predicate='identifier'):
    return {'kind': 'assertion', 'id': 'i:' + key, 'subject': subject, 'predicate': predicate, 'value': value,
            'observed_at': '2026-01-01', 'evidence': EVIDENCE}


def link(key, subject, predicate, obj):
    return {'kind': 'assertion', 'id': 'l:' + key, 'subject': subject, 'predicate': predicate, 'object': obj,
            'observed_at': '2026-01-01', 'evidence': EVIDENCE}


INN, OGRN = '7707083893', '1027700132195'      # a real INN / OGRN pair shape; both check digits hold


class PublishedCrosswalkTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.fixture = Fixture(root)
        self.index = root / 'index.sqlite'
        publish = self.fixture.publish
        publish('ofac_sanctions', [
            entity('ofac:party:1', 'BANK ONE', 'organization'),
            ident('1', 'ofac:party:1', {'scheme': 'Tax ID No.', 'issuing_country': 'RUS', 'number': INN}),
            ident('2', 'ofac:party:1', {'scheme': 'Registration Number', 'issuing_country': 'RUS', 'number': OGRN}),
            ident('3', 'ofac:party:1', {'id': 'swift:HAVIGB2L', 'scheme': 'SWIFT/BIC', 'value': 'HAVIGB2L'}),
            # a bad check digit, and a number whose register is not named: neither is read
            entity('ofac:party:2', 'BANK TWO', 'organization'),
            ident('4', 'ofac:party:2', {'scheme': 'Tax ID No.', 'issuing_country': 'RUS', 'number': '7707083894'}),
            ident('5', 'ofac:party:2', {'scheme': 'Registration ID', 'number': OGRN}),
            # a vessel that used another ship's MMSI fraudulently
            entity('ofac:party:3', 'SHADOW TANKER', 'vessel'),
            ident('6', 'ofac:party:3', {'id': 'mmsi:572469210', 'scheme': 'MMSI', 'validity': 'Fraudulent'}),
            # a ship manager's IMO *company* number, which is not a ship
            entity('ofac:party:4', 'SHIP MANAGER LLC', 'organization'),
            ident('7', 'ofac:party:4', {'id': 'imo:5342883', 'scheme': 'Identification Number', 'validity': 'Valid'})])
        publish('other_sanctions_lists', [
            entity('us_csl:3', 'SHADOW TANKER', 'vessel'),
            # the CSL copy drops OFAC's fraudulent flag
            ident('8', 'us_csl:3', {'id': 'mmsi:572469210', 'scheme': 'MMSI'}),
            link('1', 'us_csl:3', 'same_designation_as', 'ofac:party:3'),
            entity('uk:sanctions:AFG0001', 'A PERSON', 'person'),
            entity('un:sanctions:TAe.010', 'A PERSON', 'person'),
            link('2', 'uk:sanctions:AFG0001', 'same_designation_as', 'un:sanctions:TAe.010'),
            # the UK list prints the registers as labelled free text; KPP and OKPO are not read
            entity('uk:sanctions:RUS0001', 'BANK ONE', 'organization'),
            ident('22', 'uk:sanctions:RUS0001', {'scheme': 'Business Registration Number',
                                                 'value': 'OGRN ' + OGRN + 'KPP: 770701001INN ' + INN + ' OKPO 00044434'}),
            entity('uk:sanctions:RUS0002', 'UNLABELLED', 'organization'),
            ident('23', 'uk:sanctions:RUS0002', {'scheme': 'Business Registration Number', 'value': INN})])
        publish('opensanctions_graph', [
            entity('opensanctions:NK-bank', 'Bank One PJSC', 'business'),
            ident('9', 'opensanctions:NK-bank', {'id': 'ru_inn:' + INN, 'scheme': 'innCode', 'value': INN}),
            entity('opensanctions:NK-mgr', 'Ship Manager', 'business'),
            ident('10', 'opensanctions:NK-mgr', {'id': 'imo:5342883', 'scheme': 'imoNumber', 'value': '5342883'}),
            ident('11', 'opensanctions:NK-mgr', {'scheme': 'uniqueEntityId', 'value': 'GQBPAV1TFF41'}),
            # a placeholder OGRN printed for two unrelated organisations: fails the check digit rule
            ident('20', 'opensanctions:NK-mgr', {'id': 'ru_ogrn:0000000000000', 'scheme': 'ogrnCode'}),
            ident('21', 'opensanctions:NK-bank', {'id': 'ru_ogrn:0000000000000', 'scheme': 'ogrnCode'}),
            entity('opensanctions:Q672671', 'Robert Aderholt', 'person')])
        publish('marine_ais', [
            entity('mmsi:572469210', 'REAL SHIP', 'vessel'),
            ident('12', 'mmsi:572469210', 'imo:5342883', predicate='identified_by')])
        publish('congress_people', [
            entity('bioguide:A000055', 'Robert B. Aderholt', 'person'),
            ident('13', 'bioguide:A000055', {'namespace': 'wikidata', 'value': 'Q672671'},
                  predicate='identifier_assignment')])
        publish('sec_gleif', [
            entity('lei:549300BANKONE000001', 'BANK ONE', 'organization'),
            ident('14', 'lei:549300BANKONE000001', {'namespace': 'bic', 'value': 'HAVIGB2LXXX'},
                  predicate='identifier_assignment')])
        publish('airport_nodes', [
            entity('ourairports:1', 'Kutztown Airport', 'airport'),
            ident('15', 'ourairports:1', 'iata:UTK', predicate='identified_by'),
            # a closed airport keeping a code that now serves another airport
            entity('ourairports:2', 'Old Field', 'airport'),
            ident('16', 'ourairports:2', 'iata:OLD', predicate='identified_by'),
            entity('ourairports:3', 'New Field', 'airport'),
            ident('17', 'ourairports:3', 'iata:OLD', predicate='identified_by')])
        publish('transport', [entity('iata:UTK', 'UTK', 'airport'), entity('iata:OLD', 'OLD', 'airport')])
        publish('crossref_research', [entity('orcid:0000-0002-1825-009x', 'An Author', 'researcher'),
                                      entity('ror:05h992307', 'A Laboratory', 'institution')])
        publish('openalex_people', [
            entity('openalex:A1', 'An Author', 'person'),
            ident('18', 'openalex:A1', {'namespace': 'orcid', 'value': '0000-0002-1825-009X'},
                  predicate='identifier_assignment'),
            entity('openalex:I1', 'A Laboratory', 'institution'),
            ident('19', 'openalex:I1', {'namespace': 'ror', 'value': '05h992307'}, predicate='identifier_assignment')])
        self.datasets = ['ofac_sanctions', 'other_sanctions_lists', 'opensanctions_graph', 'marine_ais',
                         'congress_people', 'sec_gleif', 'airport_nodes', 'transport', 'crossref_research',
                         'openalex_people']

    def tearDown(self):
        self.tmp.cleanup()

    def resolve(self, **kwargs):
        U.unify(self.fixture.catalog, self.fixture.store, index=self.index, datasets=self.datasets,
                publish=False, progress=None)
        report = U.resolve_identities(self.fixture.catalog, self.fixture.store, index=self.index,
                                      workdir=Path(self.tmp.name) / 'resolve', datasets=self.datasets,
                                      progress=None, **kwargs)
        graph = Graph(self.index)
        return report, lambda entity_id: graph.resolved_entity(entity_id)['members']

    def test_register_numbers_named_by_scheme_and_country_join_across_publishers(self):
        report, members = self.resolve()
        self.assertEqual(members('ofac:party:1'), ['lei:549300BANKONE000001', 'ofac:party:1', 'opensanctions:NK-bank',
                                                   'uk:sanctions:RUS0001'])
        self.assertEqual(report['counts']['bridge_claims_sanctions_register_number'], 2)
        self.assertEqual(report['counts']['bridge_claims_labelled_register_number'], 2)
        self.assertEqual(members('uk:sanctions:RUS0002'), ['uk:sanctions:RUS0002'])
        # neither a failed check digit nor an unnamed register is read
        self.assertEqual(members('ofac:party:2'), ['ofac:party:2'])
        # a typed placeholder OGRN is refused too, so the manager and the bank stay apart
        self.assertEqual(report['counts']['check_digit_failures_ru_ogrn'], 2)
        self.assertNotIn('opensanctions:NK-mgr', members('ofac:party:1'))

    def test_gleif_bic_meets_the_sanctions_swift_code(self):
        report, members = self.resolve()
        self.assertIn('lei:549300BANKONE000001', members('ofac:party:1'))
        self.assertEqual(report['counts']['aliased_bic'], 1)

    def test_published_link_predicates_join_republished_designations(self):
        report, members = self.resolve()
        self.assertEqual(members('uk:sanctions:AFG0001'), ['uk:sanctions:AFG0001', 'un:sanctions:TAe.010'])
        self.assertEqual(members('us_csl:3'), ['ofac:party:3', 'us_csl:3'])
        self.assertEqual(report['counts']['link_same_designation_as'], 2)

    def test_a_fraudulent_value_joins_nothing_even_on_the_copy_that_dropped_the_flag(self):
        report, members = self.resolve()
        self.assertEqual(members('mmsi:572469210'), ['mmsi:572469210'])
        self.assertNotIn('mmsi:572469210', members('ofac:party:3'))
        self.assertEqual(report['counts']['fraudulent_claims'], 1)
        self.assertEqual(report['counts']['fraudulent_copies_refused'], 1)

    def test_imo_company_numbers_never_meet_imo_ship_numbers(self):
        report, members = self.resolve()
        self.assertEqual(members('ofac:party:4'), ['ofac:party:4', 'opensanctions:NK-mgr'])
        self.assertEqual(members('mmsi:572469210'), ['mmsi:572469210'])
        self.assertEqual(report['counts']['imo_claims_retyped_imo_company'], 2)

    def test_entity_ids_that_are_published_identifiers_join(self):
        _, members = self.resolve()
        self.assertEqual(members('opensanctions:Q672671'), ['bioguide:A000055', 'opensanctions:Q672671'])
        self.assertEqual(members('iata:UTK'), ['iata:UTK', 'ourairports:1'])
        self.assertEqual(members('openalex:A1'), ['openalex:A1', 'orcid:0000-0002-1825-009x'])
        self.assertEqual(members('openalex:I1'), ['openalex:I1', 'ror:05h992307'])

    def test_a_code_one_dataset_prints_for_two_airports_is_refused(self):
        report, members = self.resolve()
        for entity_id in ('iata:OLD', 'ourairports:2', 'ourairports:3'):
            self.assertEqual(members(entity_id), [entity_id])
        self.assertEqual(report['counts']['duplicate_code_rows_refused_iata'], 2)
        refused = [row for row in report['within_dataset_duplicates'] if row['namespace'] == 'iata']
        self.assertEqual(refused, [{'namespace': 'iata', 'dataset': 'airport_nodes', 'values': 1, 'refused': True}])

    def test_uei_claims_are_read_and_the_bridges_switch_turns_the_new_layer_off(self):
        report, _ = self.resolve()
        self.assertEqual(report['counts']['bridge_claims_opensanctions_uei'], 1)
        report, members = self.resolve(bridges=False)
        self.assertEqual(members('uk:sanctions:AFG0001'), ['uk:sanctions:AFG0001'])
        self.assertEqual(members('ofac:party:1'), ['ofac:party:1'])
        self.assertEqual(report['view']['policy']['link_predicates'], ['same_as'])

    def test_join_coverage_counts_datasets_behind_each_entity(self):
        report, _ = self.resolve()
        scope = report['join_coverage']['totals']
        types = {row['entity_type']: row for row in report['join_coverage']['by_entity_type']}
        self.assertEqual(types['airport']['joined'], 2)                           # iata:UTK + ourairports:1
        index = join_coverage(self.index, workdir=Path(self.tmp.name) / 'jc', mentions=True,
                              families={'ofac_sanctions': 'Treasury', 'other_sanctions_lists': 'Treasury'})
        # the scope measurement (from the resolve work database) and the index measurement agree
        self.assertEqual(scope['distinct_entity_ids'], index['totals']['distinct_entity_ids'])
        self.assertEqual(scope['joined'], index['totals']['joined'])
        by_dataset = {row['dataset']: row for row in index['by_dataset']}
        self.assertEqual(by_dataset['transport']['joined'], 1)                    # iata:UTK, not iata:OLD
        self.assertEqual(by_dataset['sec_gleif']['joined'], 1)
        # OFAC and the CSL are given one publisher here, so us_csl:3 (joined to OFAC only) is not an
        # independent join; uk:sanctions:RUS0001 (joined to GLEIF and OpenSanctions too) is
        self.assertEqual(by_dataset['other_sanctions_lists']['joined'], 2)
        self.assertEqual(by_dataset['other_sanctions_lists']['joined_independent'], 1)
        self.assertGreaterEqual(index['totals']['joined_with_mentions'], index['totals']['joined'])
        unresolved = join_coverage(self.index, workdir=Path(self.tmp.name) / 'jc0', clusters=None, families={})
        self.assertEqual(unresolved['totals']['joined'], 0)
        self.assertEqual(unresolved['totals']['entity_ids_in_a_cluster'], 0)

    def test_the_replaced_resolution_can_be_re_attached(self):
        first, _ = self.resolve()
        second = U.resolve_identities(self.fixture.catalog, self.fixture.store, index=self.index,
                                      workdir=Path(self.tmp.name) / 'again', datasets=self.datasets, progress=None,
                                      bridges=False)
        previous = second['previous_resolution']
        self.assertEqual(previous['view_digest'], first['view']['view_digest'])
        self.assertEqual(previous['resolved_entities'], first['resolved_entities'])
        saved = Path(previous['directory'])
        self.assertNotEqual(second['view']['view_digest'], first['view']['view_digest'])
        # re-attaching the exported directory restores the first resolution exactly
        view = json.loads((saved / 'view.json').read_text())
        clusters = [json.loads(line) for line in (saved / 'clusters.jsonl').read_text().splitlines()]
        restored = Graph(self.index).attach_resolution(clusters, view=view)
        self.assertEqual(restored, {'resolved_entities': first['resolved_entities'],
                                    'view_digest': first['view']['view_digest']})


class BridgeUnitTests(unittest.TestCase):
    def test_register_check_digits(self):
        self.assertEqual(bridges.ru_inn('7707083893'), '7707083893')
        self.assertIsNone(bridges.ru_inn('7707083894'))
        self.assertEqual(bridges.ru_inn('500100732259'), '500100732259')
        self.assertIsNone(bridges.ru_inn('500100732258'))
        self.assertEqual(bridges.ru_ogrn('1027700132195'), '1027700132195')
        self.assertIsNone(bridges.ru_ogrn('1027700132196'))
        self.assertEqual(bridges.ru_ogrn('304500116000157'), '304500116000157')
        self.assertIsNone(bridges.ru_ogrn('2027700132195'))          # 13 digits must start with 1 or 5
        self.assertEqual(bridges.gb_company_number('1074897'), '01074897')
        self.assertEqual(bridges.gb_company_number('SC84330'), 'SC084330')
        self.assertIsNone(bridges.uei('0QBPAV1TFF41'))

    def test_the_scheme_and_the_country_must_both_name_the_register(self):
        claim = bridges.register_number_claim
        self.assertEqual(claim({'scheme': 'Company Number', 'issuing_country': 'GBR', 'number': '01074897'}),
                         ('gb_company_number', '01074897'))
        self.assertIsNone(claim({'scheme': 'Company Number', 'issuing_country': 'HKG', 'number': '1448837'}))
        self.assertIsNone(claim({'scheme': 'Tax ID No.', 'number': INN}))
        self.assertIsNone(claim({'scheme': 'Tax ID No.', 'issuing_country': 'RUS', 'number': INN,
                                 'validity': 'Fraudulent'}))
        self.assertIsNone(claim({'id': 'lei:X', 'scheme': 'Tax ID No.', 'issuing_country': 'RUS', 'number': INN}))

    def test_labelled_register_numbers_in_free_text(self):
        read = bridges.labelled_register_numbers
        self.assertEqual(read('OGRN 1027700035769INN 7708004767 OKPO 00044434'),
                         [('ru_ogrn', '1027700035769'), ('ru_inn', '7708004767')])
        self.assertEqual(read('UK Company Number – OE019729'), [('gb_company_number', 'OE019729')])
        for text in ('7810938831', 'Tax number: 5042120394', 'LINN 7708004767', 'INN 7708004768', None):
            self.assertEqual(read(text), [])

    def test_value_forms(self):
        self.assertEqual(bridges.normalize_value('swift', 'havigb2lxxx'), 'HAVIGB2L')
        self.assertEqual(bridges.normalize_value('swift', 'DEUTDEFF500'), 'DEUTDEFF500')
        self.assertEqual(bridges.normalize_value('ror', 'https://ror.org/05H992307'), '05h992307')
        self.assertEqual(bridges.record_claims({'kind': 'entity', 'entity_id': 'opensanctions:NK-abc'}), [])
        self.assertEqual(publisher_family('U.S. Census Bureau, Geography Division'), 'U.S. Census Bureau')
        self.assertEqual(publisher_family('SEC'), publisher_family('U.S. Securities and Exchange Commission (EDGAR)'))
        self.assertEqual(publisher_family('GLEIF (Global Legal Entity Identifier Foundation)'), 'GLEIF')

    def test_export_of_an_index_without_a_resolution_is_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'g.sqlite'
            with sqlite3.connect(path) as db:
                db.execute('CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT)')
            self.assertIsNone(export_resolution(path, Path(tmp) / 'out'))


if __name__ == '__main__':
    unittest.main()
