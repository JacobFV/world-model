"""Trade panel builder on fixtures shaped like the published BACI, WITS, USITC, gravity and concordance records."""
import json
import unittest

from worldmodel.panels import trade


def line(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':'))


class FixtureSource:
    def __init__(self, records):
        self.records = records

    def has(self, dataset):
        return dataset in self.records

    def ref(self, dataset):
        return {'dataset': dataset, 'stage': 'normalized', 'version': f'v-{dataset}'}

    def lines(self, dataset):
        for record in self.records[dataset]:
            yield line(record) + '\n'


def flow(prefix, year, exporter, importer, product, value, quantity=None):
    attributes = {} if quantity is None else {'quantity_t': quantity}
    return {'kind': 'observation', 'id': f'{prefix}:{year}:{exporter[-3:]}:{importer[-3:]}:{product[-6:]}',
            'metric': 'bilateral_trade_value', 'subject': exporter, 'unit': 'thousand_USD', 'value': value,
            'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01', 'observed_at': '2026-09-15T18:43:43+00:00',
            'dimensions': {'frequency': 'annual', 'importer': importer, 'product': product}, 'attributes': attributes,
            'evidence': [{'input': {'dataset': 'cepii_baci', 'artifact': 'a'}, 'locator': 'line:2'}]}


def crosswalk(name, source, target, relationship):
    s_system, t_system = name.split('_')[1], name.split('_')[2]
    return {'kind': 'assertion', 'id': f'xw:{name}:{source}:{target}', 'predicate': 'maps_to',
            'subject': f'{s_system}:{source}', 'object': f'{t_system}:{target}', 'observed_at': '2026-09-15T00:00:00+00:00',
            'attributes': {'crosswalk': name, 'relationship': relationship, 'placeholder_code': False}}


def tariff(reporter, year, revision, product, rate):
    return {'kind': 'observation', 'id': f'wits:{reporter}:{year}:{product}', 'metric': 'mfn_applied_tariff_simple_avg',
            'subject': reporter, 'value': rate, 'unit': 'percent', 'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01',
            'observed_at': '2026-09-15T00:00:00+00:00',
            'dimensions': {'hs_revision': revision, 'partner': 'wits:economy:000', 'product': f'hs:{product}',
                           'tariff_type': 'MFN', 'frequency': 'annual'}, 'attributes': {}}


def gravity(metric, subject, value, first, last, partner=None):
    dims = {'frequency': 'annual'}
    if partner:
        dims.update(partner=partner, pair='undirected')
    return {'kind': 'observation', 'id': f'gravity:{metric}:{subject}:{partner}:{first}', 'metric': metric, 'subject': subject,
            'value': value, 'valid_from': f'{first}-01-01', 'valid_to': f'{last + 1}-01-01', 'dimensions': dims,
            'observed_at': '2026-09-15T00:00:00+00:00', 'attributes': {}}


def hts(code, rate):
    return {'kind': 'observation', 'id': f'usitc:{code}', 'metric': 'general_ad_valorem_rate', 'subject': f'hts:{code}',
            'value': rate, 'unit': 'fraction', 'valid_from': '2026-09-15', 'observed_at': '2026-09-15T00:00:00+00:00',
            'dimensions': {'release': '2026HTSRev19'}, 'attributes': {}}


def records():
    return {
        'trade_concordances': [
            # 111111 is unchanged; 222221 and 222222 merge into HS92 222220; 333333 is split in two.
            crosswalk('un_hs17_hs92_correlation', '111111', '111111', '1:1'),
            crosswalk('un_hs17_hs92_correlation', '222221', '222220', 'n:1'),
            crosswalk('un_hs17_hs92_correlation', '222222', '222220', 'n:1'),
            crosswalk('un_hs17_hs92_correlation', '333333', '333331', '1:n'),
            crosswalk('un_hs17_hs92_correlation', '333333', '333332', '1:n'),
            crosswalk('un_hs22_hs17_correlation', '111111', '111111', '1:1'),
            crosswalk('un_hs22_hs17_correlation', '444441', '222221', 'n:1'),
            crosswalk('un_hs22_hs17_correlation', '444442', '222221', 'n:1'),
        ],
        'wits_trains_tariffs': [
            tariff('iso3:USA', 2019, 'HS2017', '111111', 2.5),
            tariff('iso3:USA', 2019, 'HS2017', '222221', 4.0),
            tariff('iso3:USA', 2019, 'HS2017', '222222', 4.0),
            tariff('iso3:USA', 2019, 'HS2017', '333333', 7.0),
            tariff('wits:economy:918', 2019, 'HS2022', '111111', 1.0),
            tariff('wits:economy:918', 2019, 'HS2022', '444441', 3.0),
            tariff('wits:economy:918', 2019, 'HS2022', '444442', 5.0),   # unequal: 222221 gets no EU rate
            tariff('iso3:CHN', 2019, 'HS2012', '111111', 9.0),          # no HS2012 concordance
        ],
        'usitc_hts_tariffs': [
            {'kind': 'assertion', 'id': 'usitc:release', 'predicate': 'hts_release_metadata', 'subject': 'usitc:hts:release:x',
             'value': {'name': '2026HTSRev19', 'status': 'current', 'date': '09/09/2026', 'releaseStartDate': '09/15/2026'},
             'observed_at': '2026-09-15T00:00:00+00:00', 'attributes': {}},
            hts('11111110', 0.02), hts('11111190', 0.04), hts('9903010100', 0.25),
        ],
        'cepii_gravity': [
            gravity('bilateral_distance_population_weighted', 'iso3:DEU', 7000, 1990, 2021, partner='iso3:USA'),
            gravity('regional_trade_agreement_in_force', 'iso3:DEU', 0, 1990, 2021, partner='iso3:USA'),
            gravity('eu_member', 'iso3:DEU', 1, 2019, 2019), gravity('eu_member', 'iso3:DEU', 1, 2021, 2021),
            gravity('eu_member', 'iso3:FRA', 1, 2019, 2019),
            gravity('gdp_current_usd', 'iso3:USA', 21000000000, 2019, 2019),
        ],
        'cepii_baci': [
            flow('baci:v202601', 2019, 'iso3:DEU', 'iso3:USA', 'hs17:111111', 10.0, 1.5),
            flow('baci:v202601', 2019, 'iso3:DEU', 'iso3:USA', 'hs17:222221', 20.0),
            flow('baci:v202601', 2019, 'iso3:DEU', 'iso3:USA', 'hs17:333333', 5),
            flow('baci:v202601', 2019, 'iso3:USA', 'iso3:DEU', 'hs17:111111', 3.0, 0.2),
            flow('baci:v202601', 2019, 'iso3:USA', 'iso3:DEU', 'hs17:222221', 4.0),
            flow('baci:v202601', 2019, 'iso3:FRA', 'iso3:DEU', 'hs17:111111', 1.0),
            flow('baci:v202601', 2019, 'iso3:USA', 'baci:area:490', 'hs17:111111', 1.0),
        ],
        'cepii_baci_hs92': [
            flow('baci92', 2019, 'iso3:DEU', 'iso3:USA', 'hs92:111111', 10.0),
            flow('baci92', 2019, 'iso3:DEU', 'iso3:USA', 'hs92:222220', 20.0),
            flow('baci92', 2019, 'iso3:DEU', 'iso3:USA', 'hs92:333331', 2.0),
            flow('baci92', 2023, 'iso3:USA', 'iso3:DEU', 'hs92:111111', 3.0),
        ],
    }


def build():
    source = FixtureSource(records())
    concordance = list(trade.build_concordance(source))
    tariffs = list(trade.build_tariffs(source, concordance))
    panel = list(trade.build_panel(source, tariffs, tariffs_ref={'dataset': 'trade_panel', 'stage': 'tariffs', 'version': 't'}))
    return concordance, {row['id']: row for row in tariffs}, {row['id']: row for row in panel}


class ConcordanceTest(unittest.TestCase):
    def test_exact_translation_only(self):
        concordance, _, _ = build()
        table = trade.Translation(concordance, 'un_hs17_hs92_correlation')
        self.assertEqual(table.translate({'111111': 1.0, '222221': 3.0, '222222': 3.0, '333333': 2.0}),
                         {'111111': 1.0, '222220': 3.0})
        self.assertEqual(table.translate({'222221': 3.0, '222222': 4.0}), {})     # unequal rates: no value
        self.assertEqual(table.translate({'222221': 3.0}), {})                    # a source without a rate
        self.assertIsNone(table.exact_sources('333331'))                           # split source


class TariffTest(unittest.TestCase):
    def test_native_and_translated_schedules(self):
        _, tariffs, _ = build()
        usa17 = tariffs['trade_panel:tariff:hs17:iso3:USA:2019']
        self.assertEqual(usa17['rates_pct'], {'111111': 2.5, '222221': 4.0, '222222': 4.0, '333333': 7.0})
        self.assertEqual(usa17['available_at'], '2019-12-31')
        usa92 = tariffs['trade_panel:tariff:hs92:iso3:USA:2019']
        self.assertEqual(usa92['rates_pct'], {'111111': 2.5, '222220': 4.0})
        self.assertEqual(usa92['translation'], ['un_hs17_hs92_correlation'])
        eu17 = tariffs['trade_panel:tariff:hs17:wits:economy:918:2019']
        self.assertEqual(eu17['rates_pct'], {'111111': 1.0})
        self.assertNotIn('trade_panel:tariff:hs17:iso3:CHN:2019', tariffs)
        construction = tariffs['trade_panel:tariff:construction']['construction']
        self.assertEqual(construction['reporter_years_untranslatable_HS2012'], 1)

    def test_hts_release_averaged_to_hs6(self):
        _, tariffs, _ = build()
        hts = tariffs['trade_panel:tariff:hs17:iso3:USA:2026:hts']
        self.assertAlmostEqual(hts['rates_pct']['111111'], 3.0)
        self.assertEqual(hts['available_at'], '2026-09-09')
        self.assertEqual(hts['release']['start'], '2026-09-15')


class PanelTest(unittest.TestCase):
    def test_directed_records_with_importer_tariff_and_gravity(self):
        _, _, panel = build()
        record = panel['trade_panel:hs17:2019:iso3:DEU:iso3:USA']
        self.assertEqual(record['cells'], [['111111', 10.0, 1.5, 2.5], ['222221', 20.0, None, 4.0], ['333333', 5, None, 7.0]])
        self.assertEqual(record['available_at'], '2021-01-31')
        self.assertEqual(record['importer_tariff']['reporter'], 'iso3:USA')
        self.assertEqual(record['gravity']['pair']['distance_population_weighted_km'], 7000)
        self.assertEqual(record['gravity']['importer']['gdp_current_thousand_usd'], 21000000000)
        self.assertEqual(record['gravity']['available_at']['annual_macro'], '2020-12-31')

    def test_eu_tariff_applies_through_published_membership(self):
        _, _, panel = build()
        record = panel['trade_panel:hs17:2019:iso3:USA:iso3:DEU']
        self.assertEqual(record['importer_tariff']['reporter'], 'wits:economy:918')
        self.assertEqual(record['importer_tariff']['applied_via'], 'eu_member published by cepii_gravity')
        self.assertEqual(record['cells'][0][3], 1.0)
        self.assertIsNone(record['cells'][1][3])
        intra = panel['trade_panel:hs17:2019:iso3:FRA:iso3:DEU']
        self.assertTrue(intra['importer_tariff']['intra_customs_union'])

    def test_hs92_uses_translated_rates_and_no_tariff_outside_published_years(self):
        _, _, panel = build()
        record = panel['trade_panel:hs92:2019:iso3:DEU:iso3:USA']
        self.assertEqual([cell[3] for cell in record['cells']], [2.5, 4.0, None])
        late = panel['trade_panel:hs92:2023:iso3:USA:iso3:DEU']
        self.assertIsNone(late['importer_tariff'])
        self.assertIsNone(late['gravity'])      # gravity is published through 2021 only
        area = panel['trade_panel:hs17:2019:iso3:USA:baci:area:490']
        self.assertFalse(area['linked']['tariff'])

    def test_ungrouped_flows_fail_loudly(self):
        data = records()
        data['cepii_baci'].append(flow('baci:v202601', 2019, 'iso3:DEU', 'iso3:USA', 'hs17:999999', 1.0))
        source = FixtureSource(data)
        with self.assertRaises(ValueError):
            list(trade.build_panel(source, []))

    def test_cells_history_and_coverage(self):
        _, _, panel = build()
        flat = list(trade.cells(panel['trade_panel:hs17:2019:iso3:DEU:iso3:USA']))
        self.assertEqual(flat[0], {'nomenclature': 'hs17', 'year': 2019, 'exporter': 'iso3:DEU', 'importer': 'iso3:USA',
                                   'product': '111111', 'value_kusd': 10.0, 'quantity_t': 1.5,
                                   'importer_mfn_applied_pct': 2.5})
        lines = [line(row) for row in panel.values()]
        story = trade.history(lines, 'iso3:USA', 'iso3:DEU', '111111', nomenclature='hs17')
        self.assertEqual(len(story), 1)
        self.assertEqual(story[0]['exports']['value_kusd'], 3.0)
        self.assertEqual(story[0]['imports']['importer_mfn_applied_pct'], 2.5)
        report = trade.coverage(panel.values())
        self.assertEqual(report['hs17']['2019']['records'], 4)
        self.assertEqual(report['hs92']['2023']['record_share_with_gravity'], 0.0)

    def test_fast_parse_matches_json(self):
        record = flow('baci:v202601', 2019, 'iso3:DEU', 'iso3:USA', 'hs17:111111', 10.25, 1.5)
        self.assertEqual(trade.parse_flow(line(record)),
                         (2019, 'iso3:DEU', 'iso3:USA', 'hs17:111111', 10.25, 1.5, record['id']))


if __name__ == '__main__':
    unittest.main()
