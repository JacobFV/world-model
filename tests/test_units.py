import unittest

from worldmodel.model import validate_record
from worldmodel.units import (PriceIndexSeries, RateSeries, UnitError, compatible, convert, convert_currency, deflate,
                              parse_unit, percentage_point_change, rebase_index, validate_conversion)

EVIDENCE = [{'input': {'dataset': 'fixture', 'version': 'a' * 64}, 'record_id': 'fixture:1'}]


class DimensionalTests(unittest.TestCase):
    def test_physical_and_scaled_units(self):
        self.assertAlmostEqual(convert(12, 'million kilowatt hours', 'GWh')['value'], 12)
        self.assertEqual(convert(3, 'thousand_USD', 'USD')['value'], 3000)
        self.assertAlmostEqual(convert(2, 'km/second', 'm/s')['value'], 2000)
        self.assertAlmostEqual(convert(100, 'degC', 'degF')['value'], 212)
        self.assertEqual(convert(5, 'FEU', 'TEU')['value'], 10)
        self.assertAlmostEqual(convert(1, 'MMBtu', 'MWh')['value'], 0.29307107017, places=9)
        self.assertAlmostEqual(convert(1, 'barrel/day', 'm3/day')['value'], 0.158987294928)
        self.assertAlmostEqual(convert(1, 'm^2', 'sq_ft')['value'], 10.7639104167, places=8)
        self.assertEqual(convert(50, 'percent', 'fraction')['value'], 0.5)
        self.assertEqual(convert(25, 'bps', 'percentage_point')['value'], 0.25)
        result = convert(1, 'acre', 'ha')
        self.assertTrue(result['steps'] and result['factor'] == result['value'])

    def test_repository_unit_strings_parse(self):
        for unit in ('USD/second', 'thousand_USD', 'million kilowatt hours', 'BU / ACRE', 'index_1982_1984_100',
                     'USD/barrel', 'people', 'establishments', 'barrel/second', 'km/second', 'shares', 'USD/share'):
            parse_unit(unit)
        self.assertEqual(dict(parse_unit('USD_2017').dims), {'currency:USD@2017': 1})
        self.assertEqual(parse_unit('chained 2017 USD').dims, parse_unit('USD_2017').dims)

    def test_ambiguous_and_invalid_conversions_are_refused(self):
        refusals = [('percent', 'percentage_point', {}), ('ton', 'kg', {}), ('MT', 'kg', {}), ('oz', 'g', {}),
                    ('USD', 'EUR', {}), ('USD', 'USD_2017', {}), ('bu', 'kg', {}), ('USD/year', 'USD/second', {}),
                    ('people', 'establishments', {}), ('index_2017_100', 'index_1982_1984_100', {}),
                    ('barrel', 'MMBtu', {'commodity': 'crude_oil'}), ('degC/s', 'K/s', {}), ('index', 'index', {})]
        for source, target, options in refusals:
            with self.subTest(source=source, target=target):
                with self.assertRaises(UnitError):
                    convert(1, source, target, **options)
        self.assertFalse(compatible('people', 'jobs'))

    def test_commodity_and_calendar_bridges_are_explicit(self):
        corn = convert(1, 'BU / ACRE', 't/ha', commodity='corn')
        self.assertAlmostEqual(corn['value'], 56 * 0.45359237 / 1000 / 0.40468564224, places=9)
        self.assertEqual(corn['method'], 'commodity')
        self.assertIn('USDA', corn['steps'][-1]['source'])
        self.assertNotAlmostEqual(convert(1, 'bu', 'kg', commodity='wheat')['value'], corn['value'])
        crude = convert(1, 'barrel', 'MMBtu', commodity='crude_oil', allow_approximate=True)
        self.assertAlmostEqual(crude['value'], 5.8)
        self.assertEqual(crude['steps'][-1]['status'], 'approximate')
        explicit = convert(2, 'barrel', 'MMBtu', factors=[{'from': 'barrel', 'to': 'MMBtu', 'factor': 5.691, 'source': 'eia:2023', 'valid_at': '2023'}])
        self.assertAlmostEqual(explicit['value'], 11.382)
        self.assertAlmostEqual(convert(365, 'USD/year', 'USD/day', day_count='act365')['value'], 1.0)
        self.assertEqual(convert(1, 'year', 'quarter')['value'], 4)

    def test_percentage_point_change(self):
        self.assertEqual(percentage_point_change(3.5, 4.0), {'value': 0.5, 'unit': 'percentage_point', 'from_unit': 'percent'})


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.eurusd = RateSeries('fred:DEXUSEU', 'EUR', 'USD', [('2024-03-01', 1.08), ('2024-03-04', 1.10)], evidence=EVIDENCE)
        self.usdjpy = RateSeries('fred:DEXJPUS', 'USD', 'JPY', [('2024-03-01', 150.0)])

    def test_currency_requires_dated_series_and_reports_it(self):
        result = convert_currency(100, 'thousand_EUR', 'USD', at='2024-03-01', rates=self.eurusd)
        self.assertAlmostEqual(result['value'], 108000)
        self.assertEqual(result['series'][0], {'id': 'fred:DEXUSEU', 'date': '2024-03-01', 'value': 1.08, 'inverted': False})
        with self.assertRaisesRegex(UnitError, 'exactly'):
            convert_currency(1, 'EUR', 'USD', at='2024-03-02', rates=self.eurusd)
        self.assertAlmostEqual(convert_currency(1, 'EUR', 'USD', at='2024-03-02', rates=self.eurusd, policy='previous',
                                                max_staleness_days=1)['value'], 1.08)
        with self.assertRaisesRegex(UnitError, 'stale'):
            convert_currency(1, 'EUR', 'USD', at='2024-03-03', rates=self.eurusd, policy='previous', max_staleness_days=1)
        with self.assertRaises(UnitError):
            convert_currency(1, 'EUR', 'USD', at='2024-03-01', rates=1.08)
        self.assertAlmostEqual(convert_currency(108, 'USD', 'EUR', at='2024-03-01', rates=[self.eurusd])['value'], 100)

    def test_cross_rate_period_average_and_compound_units(self):
        cross = convert_currency(1, 'EUR/barrel', 'JPY/barrel', at='2024-03-01', rates=[self.eurusd, self.usdjpy])
        self.assertAlmostEqual(cross['value'], 162.0)
        self.assertEqual(len(cross['series']), 2)
        average = convert_currency(1, 'EUR', 'USD', period=('2024-03-01', '2024-04-01'), rates=self.eurusd)
        self.assertAlmostEqual(average['value'], 1.09)
        with self.assertRaises(UnitError):
            convert_currency(1, 'EUR', 'USD', rates=self.eurusd)
        with self.assertRaises(UnitError):
            convert_currency(1, 'EUR/barrel', 'USD/t', at='2024-03-01', rates=self.eurusd)

    def test_deflation_rebasing_and_period_completeness(self):
        monthly = PriceIndexSeries('fred:CPIAUCSL', [(f'2017-{m:02d}-01', 240.0) for m in range(1, 13)]
                                   + [(f'2020-{m:02d}-01', 258.0) for m in range(1, 13)],
                                   base_period='1982-1984=100', currency='USD', frequency='monthly')
        real = deflate(1000, 'USD', from_period='2020', to_period='2017', index=monthly)
        self.assertAlmostEqual(real['value'], 1000 * 240 / 258)
        self.assertEqual(real['unit'], 'USD_2017')
        self.assertEqual([s['period'] for s in real['series']], ['2020', '2017'])
        self.assertEqual(deflate(1, 'thousand_USD/year', from_period='2020', to_period='2017', index=monthly)['unit'], 'thousand_USD_2017/year')
        with self.assertRaisesRegex(UnitError, 'EUR'):
            deflate(1, 'EUR', from_period='2020', to_period='2017', index=monthly)
        partial = PriceIndexSeries('fred:CPI', [('2021-01-01', 260.0), ('2017-01-01', 240.0)], base_period='1982-1984=100',
                                   currency='USD', frequency='monthly')
        with self.assertRaisesRegex(UnitError, 'allow_partial'):
            deflate(1, 'USD', from_period='2021', to_period='2017', index=partial)
        self.assertAlmostEqual(rebase_index(258, 'index_1982_1984_100', to_base='2017', index=monthly)['value'], 107.5)
        with self.assertRaises(UnitError):
            deflate(1, 'USD', from_period='2020', to_period='2017', index={'2017': 240})


class RecordFieldTests(unittest.TestCase):
    def record(self, **extra):
        return {'kind': 'observation', 'id': 'obs:1', 'metric': 'sales', 'value': 12.0, 'unit': 'GWh', 'dimensions': {},
                'observed_at': '2024-01-01', 'evidence': EVIDENCE, **extra}

    def test_optional_fields_are_validated_and_backward_compatible(self):
        validate_record(self.record())
        validate_record(self.record(conversion={'from_value': 12, 'from_unit': 'million kilowatt hours', 'factor': 1.0, 'method': 'dimensional'}))
        with self.assertRaisesRegex(ValueError, 'disagrees'):
            validate_record(self.record(conversion={'from_value': 12, 'from_unit': 'MWh', 'factor': 1.0, 'method': 'dimensional'}))
        with self.assertRaisesRegex(ValueError, 'series'):
            validate_record(self.record(unit='USD', conversion={'from_value': 1, 'from_unit': 'EUR', 'factor': 1.1, 'method': 'currency'}))
        validate_conversion({'from_value': 1, 'from_unit': 'EUR', 'factor': 1.1, 'method': 'currency',
                             'series': [{'id': 'fred:DEXUSEU', 'date': '2024-03-01'}]}, 'USD')
        with self.assertRaisesRegex(ValueError, 'base_period'):
            validate_record(self.record(unit='USD', price_basis='real'))
        validate_record(self.record(unit='USD_2017', price_basis='real', base_period='2017', vintage='2024-02-01',
                                    supersedes=['obs:0']))
        with self.assertRaises(ValueError):
            validate_record(self.record(retracts=['not namespaced']))


if __name__ == '__main__':
    unittest.main()
