"""The Wikidata identity bridge: what it joins, and everything it refuses to join.

``docs/identity-coverage.md`` measures that Wikidata QIDs are the only cross-domain identifier in
this catalog and that almost none of them meet a second publisher. These cases are the bridge that
changes that, and the guards that keep it from merging different things: a check digit that does
not recompute, a value Wikidata carries on two items, an item carrying two values of a family that
is one-to-one, a code whose issuer reuses it (MMSI, UN/LOCODE, EIN), and an IMO number on something
that is not a ship.

Fixtures only; no network.
"""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_unify_scale import EVIDENCE, Fixture, entity

from worldmodel import unify as U
from worldmodel.graph import Graph
from worldmodel.resolution import bridges
from worldmodel.resolution import wikidata as W

# ISO 17442 LEIs: 18 characters plus MOD 97-10 check digits, verified by W.lei() below.
LEI_ONE = '549300BANKONE0000089'
LEI_TWO = '5493001KJTIIGC8Y1R12'


def statement(key, qid, prop, value):
    return {'kind': 'assertion', 'id': 'w:' + key, 'subject': 'wikidata:' + qid,
            'predicate': 'external_identifier', 'observed_at': '2026-01-01', 'evidence': EVIDENCE,
            'value': {'property': prop, 'property_label': W.PROPERTIES[prop]['label'], 'value': value}}


def ident(key, subject, value, predicate='identifier_assignment'):
    return {'kind': 'assertion', 'id': 'i:' + key, 'subject': subject, 'predicate': predicate,
            'value': value, 'observed_at': '2026-01-01', 'evidence': EVIDENCE}


class ValueShapeTests(unittest.TestCase):
    """Check digits and formats, recomputed rather than trusted."""

    def test_lei_check_digits_iso_17442(self):
        self.assertEqual(W.lei('213800FD9J2IHTA7YX78'), '213800FD9J2IHTA7YX78')
        self.assertEqual(W.lei(' 213800fd9j2ihta7yx78 '), '213800FD9J2IHTA7YX78')
        self.assertIsNone(W.lei('213800FD9J2IHTA7YX79'))       # last digit changed
        self.assertIsNone(W.lei('213800FD9J2IHTA7YX7'))        # 19 characters
        self.assertIsNone(W.lei('213800FD9J2IHTA7YXAB'))       # check digits are digits

    def test_isin_check_digit_iso_6166(self):
        self.assertEqual(W.isin('US2270461096'), 'US2270461096')
        self.assertIsNone(W.isin('US2270461097'))
        self.assertIsNone(W.isin('US227046109'))

    def test_imo_ship_number_check_digit(self):
        self.assertEqual(W.imo('9422964'), '9422964')
        self.assertEqual(W.imo('IMO 9422964'), '9422964')
        self.assertIsNone(W.imo('9422965'))
        self.assertIsNone(W.imo('942296'))

    def test_shapes_the_standards_fix(self):
        claim = W.statement_claim
        self.assertEqual(claim({'property': 'P1157', 'value': 'A000055'})[:2], ('bioguide', 'A000055'))
        self.assertIsNone(claim({'property': 'P1157', 'value': 'A55'}))
        self.assertEqual(claim({'property': 'P5531', 'value': '0000001750'})[:2], ('sec_cik', '1750'))
        self.assertEqual(claim({'property': 'P882', 'value': '13209'})[:2], ('fips_county', '13209'))
        self.assertEqual(claim({'property': 'P5087', 'value': '6'})[:2], ('fips_state', '06'))
        self.assertEqual(claim({'property': 'P299', 'value': '36'})[:2], ('iso3166_1_numeric', '036'))
        self.assertIsNone(claim({'property': 'P300', 'value': 'VI'}))        # not XX-YYY
        self.assertIsNone(claim({'property': 'P7534', 'value': '3579'}))     # a MIC starts with a letter
        self.assertIsNone(claim({'property': 'P7057', 'value': '003043'}))   # not a C-committee ID

    def test_opencorporates_reads_only_the_gb_jurisdiction(self):
        self.assertEqual(W.statement_claim({'property': 'P1320', 'value': 'gb/06527449'})[:2],
                         ('gb_company_number', '06527449'))
        self.assertEqual(W.statement_claim({'property': 'P1320', 'value': 'gb/SC402831'})[:2],
                         ('gb_company_number', 'SC402831'))
        for other in ('fr/810729830', 'us_ca/4835874', 'il/520044132', '06527449'):
            self.assertIsNone(W.statement_claim({'property': 'P1320', 'value': other}), other)

    def test_reused_codes_are_refused_by_property(self):
        for prop in ('P587', 'P1937', 'P1297', 'P2390'):
            self.assertIsNone(W.statement_claim({'property': prop, 'value': '205517000'}), prop)
            self.assertIn(prop, W.REFUSED)
            self.assertIsNone(W.PROPERTIES[prop]['namespace'])

    def test_an_undeclared_property_is_never_read(self):
        self.assertIsNone(W.statement_claim({'property': 'P496', 'value': '0000-0002-1825-0097'}))
        self.assertIsNone(W.statement_claim({'property': 'P214', 'value': '12345'}))
        self.assertIsNone(W.statement_claim('not a statement'))

    def test_every_bridged_property_has_a_declared_mapping_specification(self):
        for prop, rule in W.PROPERTIES.items():
            if rule['namespace'] is None:
                self.assertIsNone(rule['spec'], prop)
                continue
            self.assertIn(rule['spec'], bridges.MAPPING_SPECS, prop)
            self.assertIn(rule['spec'], bridges.BRIDGES, prop)

    def test_the_namespaces_the_bridge_introduces_can_actually_cluster(self):
        from worldmodel.resolution.deterministic import UNIQUE_NAMESPACES
        for namespace in W.NEW_UNIQUE_NAMESPACES:
            self.assertIn(namespace, UNIQUE_NAMESPACES, namespace)
        introduced = {rule['namespace'] for rule in W.PROPERTIES.values() if rule['namespace']}
        clustered = set(UNIQUE_NAMESPACES) | set(U.EXTRA_UNIQUE_NAMESPACES)
        self.assertEqual(introduced - clustered, set())


class GeoEntityIdTests(unittest.TestCase):
    """Entity IDs that are geographic codes: the other side the Wikidata codes meet."""

    def test_only_the_five_declared_shapes_are_read(self):
        self.assertEqual(W.geo_entity_id_claim('geo:US:county:13209'), ('fips_county', '13209'))
        self.assertEqual(W.geo_entity_id_claim('geo:US:state:13'), ('fips_state', '13'))
        self.assertEqual(W.geo_entity_id_claim('iso3:USA'), ('iso3166_1_alpha3', 'USA'))
        self.assertEqual(W.geo_entity_id_claim('geo:US'), ('iso3166_1_alpha2', 'US'))
        self.assertEqual(W.geo_entity_id_claim('iso3166-2:US-GA'), ('iso3166_2', 'US-GA'))
        for other in ('geo:US:tract:01001020100', 'geo:US:zcta:00601', 'geo:US:cbsa:10100',
                      'geo:US:place:0100100', 'lei:001GPB6A9XPE8XJICC14', 'gadm36:AFG.10_1',
                      'geo:US:county:132090', 'owid:historical_country:ANT'):
            self.assertIsNone(W.geo_entity_id_claim(other), other)


class BridgeResolutionTests(unittest.TestCase):
    """The bridge end to end, over a published fixture catalog."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.fixture = Fixture(root)
        self.index = root / 'index.sqlite'
        publish = self.fixture.publish
        publish('wikidata_identifiers', [
            # a company Wikidata gives an LEI, a CIK and a UK company number
            entity('wikidata:Q102673', 'Bank One', 'business'),
            statement('1', 'Q102673', 'P1278', LEI_TWO),
            statement('2', 'Q102673', 'P5531', '0000001750'),
            statement('3', 'Q102673', 'P1320', 'gb/06527449'),
            # a member of Congress
            entity('wikidata:Q672671', 'Robert Aderholt', 'person'),
            statement('4', 'Q672671', 'P1157', 'A000055'),
            # a ship, joined to the AIS hull by its IMO number
            entity('wikidata:Q900001', 'MV Real Ship', 'vessel'),
            statement('5', 'Q900001', 'P458', '9422964'),
            # a scrapped ship whose MMSI the AIS hull now carries: the MMSI is refused, so they
            # never meet
            entity('wikidata:Q900008', 'MV Scrapped Ship', 'vessel'),
            statement('6', 'Q900008', 'P587', '572469210'),
            # a ship *manager*: IMO's company series, not its ship series
            entity('wikidata:Q900002', 'Ship Manager LLC', 'business'),
            statement('7', 'Q900002', 'P458', '9380738'),
            # a county and the country it is in
            entity('wikidata:Q491382', 'Montgomery County', 'county'),
            statement('8', 'Q491382', 'P882', '13209'),
            entity('wikidata:Q30', 'United States of America', 'country'),
            statement('9', 'Q30', 'P297', 'US'),
            statement('10', 'Q30', 'P298', 'USA'),
            # a bad check digit is not an identifier
            entity('wikidata:Q900003', 'Typo Corp', 'business'),
            statement('11', 'Q900003', 'P1278', '213800FD9J2IHTA7YX79'),
            # two items carrying one BIC: Wikidata does this, and it is refused
            entity('wikidata:Q900004', 'A Bank Branch', 'business'),
            statement('12', 'Q900004', 'P2627', 'OKOYFIHH'),
            entity('wikidata:Q900005', 'Another Bank Branch', 'business'),
            statement('13', 'Q900005', 'P2627', 'OKOYFIHH'),
            # one item carrying two LEIs, which the 1:1 specification forbids
            entity('wikidata:Q900006', 'Double Registered', 'business'),
            statement('14', 'Q900006', 'P1278', LEI_ONE),
            statement('15', 'Q900006', 'P1278', '213800FD9J2IHTA7YX78'),
            # one item, several ISINs: legitimate, and kept
            entity('wikidata:Q900007', 'Multi Class Corp', 'business'),
            statement('16', 'Q900007', 'P946', 'US2270461096'),
            statement('17', 'Q900007', 'P946', 'US0378331005')])
        publish('sec_gleif', [
            entity('lei:' + LEI_TWO, 'BANK ONE', 'organization'),
            entity('lei:' + LEI_ONE, 'DOUBLE ONE', 'organization'),
            entity('lei:213800FD9J2IHTA7YX78', 'DOUBLE TWO', 'organization'),
            entity('isin:US2270461096', 'A SECURITY', 'security'),
            entity('lei:549300BANKBIC0000088', 'BIC BANK', 'organization'),
            ident('1', 'lei:549300BANKBIC0000088', {'namespace': 'bic', 'value': 'OKOYFIHHXXX'})])
        publish('sec_issuer_reference', [
            entity('sec:cik:0000001750', 'BANK ONE INC', 'business'),
            ident('2', 'sec:cik:0000001750', {'namespace': 'sec_cik', 'value': '1750'})])
        publish('companies_house_uk', [entity('gb:companies_house:06527449', 'BANK ONE UK LTD', 'business')])
        publish('congress_people', [entity('bioguide:A000055', 'Robert B. Aderholt', 'person')])
        publish('marine_ais', [
            entity('mmsi:572469210', 'REAL SHIP', 'vessel'),
            ident('3', 'mmsi:572469210', 'imo:9422964', predicate='identified_by')])
        publish('acs_5yr_tables', [entity('geo:US:county:13209', 'Montgomery County, Georgia', 'county')])
        publish('census_geography', [entity('geo:US', 'United States', 'country')])
        publish('airport_nodes', [entity('iso3:USA', 'United States', 'country')])
        self.datasets = ['wikidata_identifiers', 'sec_gleif', 'sec_issuer_reference', 'companies_house_uk',
                         'congress_people', 'marine_ais', 'acs_5yr_tables', 'census_geography', 'airport_nodes']

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

    def test_a_qid_bridges_three_publishers_that_never_meet_otherwise(self):
        report, members = self.resolve()
        self.assertEqual(members('wikidata:Q102673'),
                         ['gb:companies_house:06527449', 'lei:' + LEI_TWO, 'sec:cik:0000001750',
                          'wikidata:Q102673'])
        self.assertGreaterEqual(report['counts']['bridge_claims_wikidata_identifier'], 8)

    def test_the_bridge_is_the_only_thing_joining_them(self):
        report, members = self.resolve(bridges=False)
        self.assertEqual(members('wikidata:Q102673'), ['wikidata:Q102673'])
        self.assertEqual(members('bioguide:A000055'), ['bioguide:A000055'])
        self.assertFalse(report['bridges'])

    def test_a_person_qid_meets_the_bioguide_entity(self):
        _, members = self.resolve()
        self.assertEqual(members('wikidata:Q672671'), ['bioguide:A000055', 'wikidata:Q672671'])

    def test_a_failed_check_digit_joins_nothing(self):
        _, members = self.resolve()
        self.assertEqual(members('wikidata:Q900003'), ['wikidata:Q900003'])

    def test_one_value_on_two_items_is_refused_not_merged(self):
        report, members = self.resolve()
        self.assertEqual(members('wikidata:Q900004'), ['wikidata:Q900004'])
        self.assertEqual(members('wikidata:Q900005'), ['wikidata:Q900005'])
        self.assertNotIn('wikidata:Q900004', members('lei:549300BANKBIC0000088'))
        refused = [row for row in report['bridge_conflicts']
                   if row['bridge'] == 'wikidata_identifier_series' and row.get('namespace') == 'swift']
        self.assertTrue(refused, report['bridge_conflicts'])

    def test_one_item_with_two_values_of_a_one_to_one_family_is_refused(self):
        report, members = self.resolve()
        self.assertEqual(members('wikidata:Q900006'), ['wikidata:Q900006'])
        self.assertEqual(members('lei:' + LEI_ONE), ['lei:' + LEI_ONE])
        self.assertTrue([row for row in report['bridge_conflicts']
                         if row['bridge'] == 'wikidata_identifier' and row.get('namespace') == 'lei'])

    def test_a_one_to_many_family_keeps_both_values(self):
        _, members = self.resolve()
        self.assertEqual(members('wikidata:Q900007'), ['isin:US2270461096', 'wikidata:Q900007'])

    def test_an_mmsi_is_never_read_from_wikidata(self):
        report, members = self.resolve()
        self.assertEqual(members('wikidata:Q900008'), ['wikidata:Q900008'])
        self.assertNotIn('wikidata:Q900008', members('mmsi:572469210'))
        self.assertNotIn('bridge_claims_wikidata_mmsi', report['counts'])

    def test_an_imo_number_on_something_that_is_not_a_ship_is_the_company_series(self):
        report, members = self.resolve()
        self.assertEqual(members('wikidata:Q900001'), ['mmsi:572469210', 'wikidata:Q900001'])
        self.assertEqual(members('wikidata:Q900002'), ['wikidata:Q900002'])
        self.assertGreaterEqual(report['counts']['imo_claims_retyped_imo_company'], 1)

    def test_geographic_codes_join_census_to_wikidata_and_the_iso_publishers(self):
        report, members = self.resolve()
        self.assertEqual(members('wikidata:Q491382'), ['geo:US:county:13209', 'wikidata:Q491382'])
        self.assertEqual(members('wikidata:Q30'), ['geo:US', 'iso3:USA', 'wikidata:Q30'])
        self.assertGreaterEqual(report['counts']['bridge_claims_geo_entity_id'], 3)


class PipelineTests(unittest.TestCase):
    """The dataset pipeline's own guards: entity typing and truncated pages."""

    @staticmethod
    def _pipeline():
        import importlib.util
        path = (Path(__file__).resolve().parent.parent / 'data' / 'wikidata_identifiers' / 'pipeline.py')
        spec = importlib.util.spec_from_file_location('wikidata_identifiers_pipeline', path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_most_specific_class_an_item_carries_decides_its_type(self):
        entity_type = self._pipeline().entity_type
        self.assertEqual(entity_type(['Q891723', 'Q43229']), 'business')   # public company, organization
        self.assertEqual(entity_type(['Q43229']), 'organization')
        self.assertEqual(entity_type(['Q25653', 'Q11446']), 'vessel')
        self.assertEqual(entity_type(['Q5']), 'person')
        self.assertEqual(entity_type(['Q999999999']), 'entity')            # unmapped stays generic
        self.assertEqual(entity_type([]), 'entity')
        self.assertIsNone(entity_type(['Q4167410']))                       # a disambiguation page

    def test_a_short_page_followed_by_a_full_one_is_a_truncated_response(self):
        check = self._pipeline().check_pages
        self.assertEqual(check([(0, 10000, 10000), (10000, 10000, 3403), (20000, 10000, 0)]), 13403)
        self.assertEqual(check([(0, 10000, 0)]), 0)
        with self.assertRaises(ValueError):
            check([(0, 10000, 9999), (10000, 10000, 10000)])
        with self.assertRaises(ValueError):
            check([(0, 10000, 10001)])

    def test_the_declaration_and_the_resolution_layer_agree_on_the_properties(self):
        path = Path(__file__).resolve().parent.parent / 'data' / 'wikidata_identifiers' / 'dataset.json'
        declared = json.loads(path.read_text())['parameters']['properties']
        self.assertEqual({row['property'] for row in declared}, set(W.PROPERTIES))
        for row in declared:
            self.assertEqual(row['property_label'], W.PROPERTIES[row['property']]['label'], row['property'])


if __name__ == '__main__':
    unittest.main()
