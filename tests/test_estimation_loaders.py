"""Catalog loaders: fixtures that mimic the published record shapes, plus a real-data smoke test."""
import json
import tempfile
import unittest
from pathlib import Path

from worldmodel.estimation import estimator_for
from worldmodel.estimation.data import ObservationSet
from worldmodel.estimation.loaders import (COMPONENT_SOURCES, BLOCKED_COMPONENTS, BLOCKED_FAMILIES, FAMILY_LOADERS,
                                           MissingData, assets_data, availability, cash_balance_data,
                                           commodities_data, conflict_data, fdic_noncurrent_loan_rate, load_for,
                                           monetary_data, national_unemployment_rate, observation_set, regional_data)
from worldmodel.estimation_cli import load_plan

DATA_ROOT = Path(__file__).resolve().parent.parent / 'data'


def canonical_line(record):
    return json.dumps(record, sort_keys=True, separators=(',', ':'))


class FixtureStore:
    """Minimal Store surface the loaders use: latest, verify and version_dir."""

    def __init__(self, root):
        self.root = Path(root)

    def write(self, dataset, records, version='fixture'):
        directory = self.root / dataset / version
        directory.mkdir(parents=True, exist_ok=True)
        with (directory / 'records.jsonl').open('w', encoding='utf-8') as stream:
            for record in records:
                stream.write(canonical_line(record) + '\n')
        (self.root / dataset / 'latest').write_text(version, encoding='utf-8')

    def latest(self, dataset, stage=None):
        version = (self.root / dataset / 'latest')
        if not version.exists():
            raise FileNotFoundError(dataset)
        return {'dataset': dataset, 'stage': stage, 'version': version.read_text(encoding='utf-8')}

    def verify(self, ref, recursive=True):
        return True

    def version_dir(self, ref):
        return self.root / ref['dataset'] / ref['version']


def observation(**kwargs):
    record = {'kind': 'observation', 'observed_at': '2026-09-15T00:00:00+00:00', 'attributes': {}, 'dimensions': {}}
    record.update(kwargs)
    return record


def eia_week(metric, unit, ending, value, index):
    """EIA weekly records are period-ending: a value dated D covers [D-6, D+1)."""
    from datetime import date, timedelta
    end = date.fromisoformat(ending)
    return observation(id=f'eia:{metric}:{index}', metric=metric, unit=unit, subject='iso3:USA', value=value,
                       valid_from=(end - timedelta(days=6)).isoformat(), valid_to=(end + timedelta(days=1)).isoformat(),
                       dimensions={'frequency': 'weekly', 'series_id': f'PET.{metric}.W'},
                       attributes={'period': end.strftime('%Y%m%d'), 'series_last_updated': '2026-09-10T18:53:26-04:00'})


class SourceDeclarationTests(unittest.TestCase):
    def test_every_declared_source_maps_to_a_real_requirement_series(self):
        for component, sources in COMPONENT_SOURCES.items():
            names = {r.name for r in estimator_for(component).all_requirements()}
            self.assertEqual({s.requirement for s in sources}, names, component)
            for source in sources:
                self.assertIn(source.availability, ('real_time', 'retrospective'))

    def test_availability_covers_every_component_and_family(self):
        from worldmodel.estimation import ESTIMATORS
        from worldmodel.models import FAMILY_MODULES
        report = availability()
        self.assertEqual(set(report['components']), set(ESTIMATORS))
        self.assertEqual(set(report['families']), set(FAMILY_MODULES))
        for component, entry in report['components'].items():
            if entry['status'] == 'blocked_on_data':
                self.assertTrue(entry['reason'] and entry['missing'], component)

    def test_blocked_components_name_the_missing_series_and_dataset(self):
        for component, blocked in BLOCKED_COMPONENTS.items():
            self.assertTrue(blocked.missing, component)
            for series, dataset in blocked.missing:
                self.assertTrue(series and dataset)
        self.assertIn('freight', BLOCKED_COMPONENTS['bilateral_flow_gravity'].missing[0][1])
        self.assertEqual(BLOCKED_FAMILIES['sanctions'].missing, ())


class SeriesLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FixtureStore(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def _eia_weeks(self, weeks=60):
        from datetime import date, timedelta
        records = []
        start = date(2020, 1, 3)
        for index in range(weeks):
            ending = (start + timedelta(days=7 * index)).isoformat()
            records.append(eia_week('crude_oil_commercial_stocks_excl_spr', 'Thousand Barrels', ending, 400000 + 100 * index, index))
            records.append(eia_week('crude_oil_field_production', 'Thousand Barrels per Day', ending, 11000 + index, index))
            records.append(eia_week('crude_oil_imports', 'Thousand Barrels per Day', ending, 6000 - index, index))
            records.append(eia_week('crude_oil_exports', 'Thousand Barrels per Day', ending, 3000 + index, index))
            records.append(eia_week('refiner_net_input_crude_oil', 'Thousand Barrels per Day', ending, 15000 + index, index))
        records.append(observation(id='eia:other', metric='crude_oil_imports', unit='Thousand Barrels per Day',
                                   subject='iso3:CAN', value=1.0, valid_from='2020-01-01', valid_to='2020-01-02'))
        return records

    def test_inventory_balance_translates_metrics_units_and_keeps_lineage(self):
        self.store.write('eia_energy', self._eia_weeks())
        data, evidence, policy = observation_set('inventory_balance', self.store)
        self.assertEqual(policy, 'retrospective')      # EIA bulk records carry no publication date
        metrics = {record['metric'] for record in data.records}
        self.assertEqual(metrics, {'crude_oil_stocks_excluding_spr', 'crude_oil_field_production', 'crude_oil_imports',
                                   'crude_oil_exports', 'refinery_crude_net_input'})
        self.assertEqual({record['unit'] for record in data.records}, {'thousand_barrels', 'thousand_barrels_per_day'})
        self.assertNotIn('iso3:CAN', {record['subject'] for record in data.records})   # other subjects filtered early
        self.assertEqual(evidence['inputs'][0]['dataset'], 'eia_energy')
        self.assertEqual(evidence['series_counts']['crude_stocks'], 60)
        self.assertTrue(all(record['_input']['version'] == 'fixture' for record in data.records))
        self.assertTrue(evidence['record_ids_digest'])

    def test_inventory_balance_frame_fits_and_audits_under_the_retrospective_policy(self):
        self.store.write('eia_energy', self._eia_weeks())
        data, _, policy = observation_set('inventory_balance', self.store)
        estimator = estimator_for('inventory_balance')
        estimate = estimator.fit(data, cutoff='2021-06-30', vintage_policy=policy)
        audit = estimate.data_audit['series']['crude_stocks']
        self.assertEqual(audit['vintage_modes'], ['retrospective_lagged'])
        self.assertTrue(audit['revision_leakage_possible'])
        self.assertIn('flow_scale', estimate.parameters)
        self.assertTrue(audit['evidence']['record_ids'])

    def test_population_uses_release_dates_and_excludes_estimates_base_rows(self):
        records = []
        for vintage, years in ((2020, range(2010, 2021)), (2024, range(2020, 2025))):
            released = f'{vintage + 1}-01-05T00:00:00Z'
            for year in years:
                records.append(observation(id=f'pep{vintage}:US:population:{year}', metric='population', unit='people',
                                           subject='geo:US', value=310_000_000 + 2_000_000 * (year - 2010),
                                           valid_from=f'{year}-07-01', valid_to=f'{year}-07-02',
                                           dimensions={'vintage': vintage}, attributes={'released_at': released}))
            records.append(observation(id=f'pep{vintage}:US:base', metric='population', unit='people', subject='geo:US',
                                       value=1.0, valid_from=f'{min(years)}-04-01', valid_to=f'{min(years)}-04-02',
                                       dimensions={'vintage': vintage, 'basis': 'estimates_base'},
                                       attributes={'released_at': released}))
        self.store.write('census_population', records)
        data, evidence, policy = observation_set('population_growth_rate', self.store)
        self.assertEqual(policy, 'strict')
        self.assertEqual(len(data.records), 16)      # 11 + 5 July values; the two estimates-base rows are excluded
        estimate = estimator_for('population_growth_rate').fit(data, cutoff='2024-12-31', vintage_policy='strict')
        audit = estimate.data_audit['series']['population']
        self.assertEqual(audit['vintage_modes'], ['real_time'])
        self.assertFalse(audit['revision_leakage_possible'])
        self.assertGreater(estimate.parameters['growth_rate_per_year'], 0)

    def test_missing_series_raises_missing_data_naming_the_series(self):
        self.store.write('eia_energy', [r for r in self._eia_weeks() if r['metric'] != 'crude_oil_exports'])
        with self.assertRaises(MissingData) as error:
            observation_set('inventory_balance', self.store)
        self.assertIn('exports', str(error.exception))

    def test_blocked_component_raises_with_the_declared_reason(self):
        with self.assertRaises(MissingData) as error:
            load_for('bilateral_flow_gravity', self.store)
        self.assertIn('freight', str(error.exception).lower())


def sec_duration(concept, start, end, value, filed, accession, form='10-Q'):
    return observation(id=f'secfacts:0000000001:{concept}:USD:{start}:{end}:{accession}', metric='revenue' if 'Revenues' in concept
                       else 'costs_and_expenses' if 'Costs' in concept else 'capital_expenditures',
                       unit='USD', subject='sec:cik:0000000001', value=value, valid_from=start, valid_to=end,
                       observed_at=filed, dimensions={'concept': concept, 'period_type': 'duration', 'form': form},
                       attributes={'accession': accession, 'period_start': start, 'period_end': end, 'revision': 'first_report'})


class SecQuarterlyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FixtureStore(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def _issuer_records(self):
        """One fiscal year: quarterly revenue and costs, year-to-date capex, annual totals in the 10-K."""
        records = []
        ends = ['2020-03-31', '2020-06-30', '2020-09-30', '2020-12-31']
        filings = ['2020-04-30', '2020-07-30', '2020-10-30', '2021-02-15']
        for index, (end, filed) in enumerate(zip(ends, filings)):
            records.append(observation(id=f'secfacts:cash:{end}', metric='cash_and_equivalents', unit='USD',
                                       subject='sec:cik:0000000001', value=1000.0 + 10 * index, valid_from=end,
                                       valid_to=end, observed_at=filed,
                                       dimensions={'concept': 'us-gaap:CashAndCashEquivalentsAtCarryingValue',
                                                   'period_type': 'instant', 'form': '10-K' if index == 3 else '10-Q'},
                                       attributes={'accession': f'a{index}', 'period_end': end}))
        starts = ['2020-01-01', '2020-04-01', '2020-07-01']
        for index, (start, end, filed) in enumerate(zip(starts, ends[:3], filings[:3])):
            records.append(sec_duration('us-gaap:Revenues', start, end, 500.0 + index, filed, f'a{index}'))
            records.append(sec_duration('us-gaap:CostsAndExpenses', start, end, 400.0 + index, filed, f'a{index}'))
            # Capital expenditure is a cash-flow item: only year to date is filed.
            records.append(sec_duration('us-gaap:PaymentsToAcquirePropertyPlantAndEquipment', '2020-01-01', end,
                                        30.0 * (index + 1), filed, f'a{index}'))
        for concept, annual in (('us-gaap:Revenues', 2100.0), ('us-gaap:CostsAndExpenses', 1700.0),
                                ('us-gaap:PaymentsToAcquirePropertyPlantAndEquipment', 130.0)):
            records.append(sec_duration(concept, '2020-01-01', '2020-12-31', annual, '2021-02-15', 'a3', form='10-K'))
        return records

    def test_quarterly_flows_come_from_cumulative_differences_and_keep_component_ids(self):
        self.store.write('sec_company_assets', self._issuer_records())
        data, evidence, policy = cash_balance_data(self.store, issuer='sec:cik:0000000001')
        self.assertEqual(policy, 'strict')
        rows = {(r['metric'], r['valid_from']): r for r in data.records}
        self.assertEqual(rows[('revenues', '2020-01-01')]['value'], 500.0)
        # Q4 revenue = annual 2100 - (500 + 501 + 502)
        self.assertAlmostEqual(rows[('revenues', '2020-10-01')]['value'], 597.0)
        # Capex quarters differencing year-to-date values: 30, 30, 30, then 130 - 90
        self.assertAlmostEqual(rows[('capital_expenditure', '2020-04-01')]['value'], 30.0)
        self.assertAlmostEqual(rows[('capital_expenditure', '2020-10-01')]['value'], 40.0)
        derived = rows[('capital_expenditure', '2020-10-01')]
        self.assertIn('+', derived['id'])                       # both component record ids are kept
        self.assertEqual(derived['attributes']['basis'], 'cumulative_difference')
        self.assertEqual(rows[('revenues', '2020-10-01')]['attributes']['basis'], 'annual_minus_three_quarters')
        self.assertEqual(derived['attributes']['published_at'], '2021-02-15')
        self.assertEqual(evidence['inputs'][0]['dataset'], 'sec_company_assets')

    def test_filing_dates_are_the_knowledge_time(self):
        self.store.write('sec_company_assets', self._issuer_records())
        data, _, _ = cash_balance_data(self.store, issuer='sec:cik:0000000001')
        estimator = estimator_for('cash_balance')
        series = ObservationSet(data.records).select(estimator.all_requirements()[0], cutoff='2020-12-31', vintage_policy='strict')
        self.assertEqual([point.vintage['mode'] for point in series.points], ['real_time'] * len(series.points))
        # The fourth-quarter cash level was filed in February 2021 and is invisible in 2020.
        self.assertTrue(all(point.time < '2020-10-01' for point in series.points))


class FamilyLoaderTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FixtureStore(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_conflict_counts_fill_quiet_months_and_rank_on_the_training_window_only(self):
        records = []
        for index, month in enumerate(['2000-01', '2000-02', '2000-03']):
            records.append(observation(id=f'ucdp:a:{month}', metric='organized_violence_events', unit='events',
                                       subject='iso3:AFG', value=5.0, valid_from=f'{month}-01', valid_to=f'{month}-28',
                                       dimensions={'frequency': 'monthly', 'release': '26.1', 'type_of_violence': 'state_based'}))
        records.append(observation(id='ucdp:b:2000-03', metric='organized_violence_events', unit='events', subject='iso3:SYR',
                                   value=99.0, valid_from='2000-03-01', valid_to='2000-03-28',
                                   dimensions={'frequency': 'monthly', 'release': '26.1', 'type_of_violence': 'state_based'}))
        records.append(observation(id='ucdp:c:2000-01', metric='organized_violence_events', unit='events', subject='iso3:AFG',
                                   value=1000.0, valid_from='2000-01-01', valid_to='2000-01-28',
                                   dimensions={'frequency': 'monthly', 'release': 'candidate_v26_0_7', 'type_of_violence': 'state_based'}))
        self.store.write('ucdp_conflicts', records)
        vdem = [observation(id=f'vdem:{country}:{year}', metric='vdem_v2x_polyarchy', unit='index_0_1', subject=country,
                            value=0.4, valid_from=f'{year}-01-01', valid_to=f'{year + 1}-01-01')
                for country in ('iso3:AFG', 'iso3:SYR') for year in (1999, 2000)]
        self.store.write('vdem', vdem)
        data, evidence = conflict_data(self.store, start='2000-01', end='2000-03', rank_end='2000-02', country_count=1)
        self.assertEqual({row['country'] for row in data['events']}, {'iso3:AFG'})   # SYR only leads after the ranking window
        self.assertEqual(len(data['events']), 3)
        self.assertEqual(data['covariates'], ['polyarchy'])
        self.assertEqual(data['revisions'], 'major')
        self.assertEqual(evidence['series_counts']['events'], 3)

    def test_assets_factor_symbol_is_excluded_from_scored_bars(self):
        bars = []
        for index, day in enumerate(['2020-01-02', '2020-01-03', '2020-01-06']):
            for symbol, price in (('AAPL', 100.0 + index), ('SPY', 300.0 + index)):
                bars.append(observation(id=f'alpaca:{symbol}:{day}', metric='close_price_total_return_adjusted',
                                        unit='USD/share', subject=f'ticker:US:{symbol}', value=price,
                                        valid_from=day, valid_to=day, dimensions={'frequency': 'daily'}))
        self.store.write('alpaca_daily_bars', bars)
        rates = [observation(id=f'fred:obs:DFF:{day}', metric='policy_rate', unit='percent', subject='geo:US', value=2.52,
                             valid_from=day, valid_to=day, attributes={'realtime_start': day})
                 for day in ('2020-01-02', '2020-01-03', '2020-01-06')]
        self.store.write('fred_policy_rate', rates)
        data, evidence = assets_data(self.store, symbols=['AAPL'], factor_symbol='SPY', start='2020-01-01', end='2020-01-31')
        self.assertEqual({row['symbol'] for row in data['bars']}, {'AAPL'})
        self.assertEqual(data['factor_names'], ['market_excess'])
        self.assertEqual(len(data['factors']), 2)
        self.assertAlmostEqual(data['risk_free'][0]['rf'], 2.52 / 100 / 252)
        self.assertIn('risk_free', evidence['series_counts'])
        with self.assertRaises(ValueError):
            assets_data(self.store, symbols=['AAPL', 'SPY'], factor_symbol='SPY')

    def test_commodities_converts_daily_flows_to_weekly_barrels(self):
        weeks = []
        for index, ending in enumerate(['2020-01-03', '2020-01-10']):
            weeks.append(eia_week('crude_oil_commercial_stocks_excl_spr', 'Thousand Barrels', ending, 400000.0, index))
            weeks.append(eia_week('crude_oil_field_production', 'Thousand Barrels per Day', ending, 100.0, index))
            weeks.append(eia_week('crude_oil_imports', 'Thousand Barrels per Day', ending, 60.0, index))
            weeks.append(eia_week('crude_oil_exports', 'Thousand Barrels per Day', ending, 10.0, index))
            weeks.append(eia_week('refiner_net_input_crude_oil', 'Thousand Barrels per Day', ending, 150.0, index))
        self.store.write('eia_energy', weeks)
        prices = [observation(id=f'fred:obs:DCOILWTICO:{day}', metric='oil_price', unit='USD/barrel', subject='geo:US',
                              value=50.0, valid_from=day, valid_to=day, attributes={'realtime_start': day})
                  for day in ('2019-12-30', '2020-01-02', '2020-01-08')]
        self.store.write('fred_oil_price', prices)
        data, _ = commodities_data(self.store, start='2019-12-01', end='2020-01-31')
        row = data['balances'][0]
        self.assertEqual(row['date'], '2020-01-03')
        self.assertAlmostEqual(row['production'], 700.0)          # thousand barrels per day -> per week
        self.assertAlmostEqual(row['net_imports'], 350.0)
        self.assertAlmostEqual(row['consumption'], 1050.0)
        self.assertAlmostEqual(row['price'], 50.0)
        self.assertEqual(data['supply_lag'], 1)

    def test_regional_keeps_hyphenated_naics_sectors_and_drops_finer_industries(self):
        records = []
        for year in (2019, 2020):
            for industry in ('naics2017:11', 'naics2017:31-33', 'naics2017:1131', 'all'):
                records.append(observation(id=f'cbp:{year}:{industry}', metric='employment', unit='people',
                                           subject='geo:US:state:01', value=1000.0, valid_from=f'{year}-03-12',
                                           valid_to=f'{year}-03-19',
                                           dimensions={'industry': industry, 'legal_form': 'all', 'program': 'cbp'}))
            records.append(observation(id=f'cbp:{year}:county', metric='employment', unit='people',
                                       subject='geo:US:county:01001', value=5.0, valid_from=f'{year}-03-12',
                                       valid_to=f'{year}-03-19',
                                       dimensions={'industry': 'naics2017:11', 'legal_form': 'all', 'program': 'cbp'}))
        self.store.write('census_business', records)
        data, _ = regional_data(self.store, start_year=2019, end_year=2020)
        self.assertEqual({row['industry'] for row in data['employment']}, {'naics2017:11', 'naics2017:31-33'})
        self.assertEqual({row['region'] for row in data['employment']}, {'geo:US:state:01'})
        self.assertEqual({row['year'] for row in data['employment']}, {2019, 2020})


class PreRegisteredPlanTests(unittest.TestCase):
    def test_plan_attempts_are_runnable_declarations(self):
        from worldmodel.estimation.model_families import family_components
        from worldmodel.estimation import ESTIMATORS
        plan = load_plan()
        ids = [attempt['id'] for attempt in plan['attempts']]
        self.assertEqual(len(ids), len(set(ids)))
        for attempt in plan['attempts']:
            target, protocol = attempt['target'], attempt['protocol']
            available = {name for name, entry in availability()['components'].items() if entry['status'] == 'available'}
            if attempt['kind'] == 'component':
                self.assertIn(target, ESTIMATORS)
                self.assertIn(target, available, f'{target} has no catalog loader')
            else:
                self.assertIn(target, FAMILY_LOADERS)
                self.assertIn(f'{target}_model_parameters', family_components())
            self.assertLess(protocol['train_end'], protocol['validation_end'])
            self.assertLess(protocol['validation_end'], protocol['cutoff'])
            self.assertTrue(attempt['rationale'])
            self.assertTrue(attempt['data'])
        for entry in plan['not_run']:
            self.assertTrue(entry['reason'] and entry['note'])
        self.assertIn('calibrate-all', plan['rerun'])

    def test_plan_covers_every_loadable_target(self):
        """Anything the catalog can load must be attempted or explicitly declared not_run."""
        plan = load_plan()
        targets = {attempt['target'] for attempt in plan['attempts']} | {entry['target'] for entry in plan['not_run']}
        report = availability()
        loadable = {name for name, entry in report['components'].items() if entry['status'] == 'available'}
        loadable |= {name for name, entry in report['families'].items() if entry['status'] == 'available'}
        self.assertTrue(loadable <= targets, f'not attempted: {sorted(loadable - targets)}')

    def test_component_overrides_are_declared_for_substituted_series(self):
        """An attempt that swaps in a different underlying series must say so in overrides."""
        attempt = next(a for a in load_plan()['attempts'] if a['id'] == 'default_hazard.fdic_laus_quarterly')
        for name in ('delinquency_rate', 'unemployment_rate'):
            self.assertIn(name, attempt['overrides'])
            self.assertTrue(attempt['overrides'][name]['description'])
            self.assertTrue(attempt['overrides'][name]['source_series'])


class ConstructedSeriesTests(unittest.TestCase):
    """National aggregates and point-in-time vintage reads that no published series provides."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FixtureStore(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    def test_national_unemployment_rate_aggregates_seasonally_adjusted_states_only(self):
        records = []
        for state, unemployed, force in (('01', 60.0, 1000.0), ('02', 40.0, 1000.0)):
            for metric, value in (('unemployed', unemployed), ('labor_force', force)):
                records.append(observation(id=f'bls:{state}:{metric}:sa', metric=metric, unit='persons',
                                           subject=f'geo:US:state:{state}', value=value, valid_from='2020-01-01',
                                           valid_to='2020-02-01',
                                           dimensions={'frequency': 'monthly', 'seasonal_adjustment': 'SA'}))
                records.append(observation(id=f'bls:{state}:{metric}:nsa', metric=metric, unit='persons',
                                           subject=f'geo:US:state:{state}', value=value * 10, valid_from='2020-01-01',
                                           valid_to='2020-02-01',
                                           dimensions={'frequency': 'monthly', 'seasonal_adjustment': 'NSA'}))
        records.append(observation(id='bls:county', metric='unemployed', unit='persons', subject='geo:US:county:01001',
                                   value=999.0, valid_from='2020-01-01', valid_to='2020-02-01',
                                   dimensions={'frequency': 'monthly', 'seasonal_adjustment': 'SA'}))
        self.store.write('bls_labor', records)
        _, rates = national_unemployment_rate(self.store)
        self.assertEqual(sorted(rates), ['2020-01'])
        self.assertAlmostEqual(rates['2020-01']['value'], 5.0)     # (60+40) / (1000+1000)
        self.assertEqual(rates['2020-01']['areas'], 2)             # counties and NSA rows excluded
        self.assertTrue(rates['2020-01']['digest'])

    def test_fdic_rate_uses_stock_rows_and_sums_across_banks(self):
        records = []
        for cert, noncurrent, loans in (('1', 5.0, 100.0), ('2', 15.0, 300.0)):
            for metric, value in (('bank_noncurrent_loans', noncurrent), ('bank_net_loans', loans)):
                records.append(observation(id=f'fdic:{cert}:{metric}', metric=metric, unit='USD',
                                           subject=f'fdic:cert:{cert}', value=value, valid_from='2020-03-31',
                                           valid_to='2020-04-01',
                                           dimensions={'period_basis': 'report_date_stock', 'report_date': '2020-03-31'}))
        records.append(observation(id='fdic:ytd', metric='bank_noncurrent_loans', unit='USD', subject='fdic:cert:1',
                                   value=500.0, valid_from='2020-03-31', valid_to='2020-04-01',
                                   dimensions={'period_basis': 'year_to_date'}))
        self.store.write('fdic_bank_financials', records)
        _, rates = fdic_noncurrent_loan_rate(self.store)
        self.assertAlmostEqual(rates['2020-03-31']['value'], 5.0)   # 100 * 20 / 400, year-to-date rows ignored
        self.assertEqual(rates['2020-03-31']['banks'], 2)

    def test_monetary_inflation_is_read_point_in_time(self):
        """The base month must be the vintage known at the target month's first release."""
        cpi = []
        # The 2019-01 level is revised later (2021), which a row dated 2020-01 must not see.
        for period, vintages in (('2019-01-01', [('2019-02-13', 100.0), ('2021-02-10', 105.0)]),
                                 ('2020-01-01', [('2020-02-12', 110.0)])):
            for release, value in vintages:
                cpi.append(observation(id=f'fred:obs:CPIAUCSL:{period}:{release}', metric='consumer_price_index',
                                       unit='index_1982_1984_100', subject='geo:US', value=value, valid_from=period,
                                       valid_to=period, observed_at=release,
                                       attributes={'realtime_start': release, 'realtime_end': '9999-12-31'},
                                       dimensions={'vintage': release}))
        self.store.write('fred_cpi', cpi)
        rates, unemployment = [], []
        for year in range(2009, 2021):
            for month in range(1, 13):
                day = f'{year}-{month:02d}-01'
                rates.append(observation(id=f'fred:obs:DFF:{day}', metric='policy_rate', unit='percent', subject='geo:US',
                                         value=2.0, valid_from=day, valid_to=day,
                                         attributes={'realtime_start': day}))
                for state, unemployed in (('01', 50.0), ('02', 50.0)):
                    for metric, value in (('unemployed', unemployed), ('labor_force', 1000.0)):
                        unemployment.append(observation(id=f'bls:{state}:{metric}:{day}', metric=metric, unit='persons',
                                                        subject=f'geo:US:state:{state}', value=value, valid_from=day,
                                                        valid_to=day,
                                                        dimensions={'frequency': 'monthly', 'seasonal_adjustment': 'SA'}))
        self.store.write('fred_policy_rate', rates)
        self.store.write('bls_labor', unemployment)
        data, evidence = monetary_data(self.store, start='2020-01', end='2020-12', trend_months=12)
        self.assertEqual(len(data['observations']), 1)
        row = data['observations'][0]
        # 2020-01 first release 110 against the 2019-01 value known then (100), not the 2021 revision to 105.
        self.assertAlmostEqual(row['inflation'], 10.0)
        self.assertAlmostEqual(row['output_gap'], 0.0)             # u equals its own trailing mean
        self.assertEqual(data['information_time'], 'valid_time')   # the gap input carries no vintages
        self.assertEqual(data['revisions'], 'major')
        self.assertIn('inflation', evidence['series_counts'])


class RebasedVintageTests(unittest.TestCase):
    """FRED rebases index and chained-dollar series; ratios must stay inside one base."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FixtureStore(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)

    @staticmethod
    def _fred(series, metric, unit, base, period, release, value):
        return observation(id=f'fred:obs:{series}:{period}:{release}', metric=metric, unit=unit, subject='geo:US',
                           value=value, valid_from=period, valid_to=period, observed_at=release,
                           attributes={'realtime_start': release, 'realtime_end': '9999-12-31', 'series_id': series,
                                       'source_series': series, 'base_period': base},
                           dimensions={'series_id': series, 'vintage': release, 'base_period': base, 'frequency': 'Q'})

    def test_unit_none_accepts_every_base_and_records_what_was_published(self):
        from worldmodel.estimation.loaders import panel, SeriesSource
        source = panel('output', 'INDPRO', 'industrial_production_index', None)
        self.assertIsNone(source.unit)
        old = self._fred('INDPRO', 'industrial_production_index', 'index_1967_100', '1967', '1975-01-01', '1975-02-01', 50.0)
        new = self._fred('INDPRO', 'industrial_production_index', 'index_2017_100', '2017', '1975-01-01', '2022-02-01', 40.0)
        other = self._fred('INDPRO', 'industrial_production_index', 'index_2017_100', '2017', '1975-01-01', '2022-02-01', 40.0)
        other['subject'] = 'geo:US:state:01'
        self.assertTrue(source.matches(old) and source.matches(new))
        self.assertFalse(source.matches(other))
        self.store.write('fred_macro_panel', [old, new])
        requirement = next(r for r in estimator_for('labor_demand').all_requirements() if r.name == 'output')
        data, _, policy = observation_set('labor_demand', self.store, sources=(source,), requirements=[requirement])
        self.assertEqual(policy, 'strict')
        units = {record['unit'] for record in data.records}
        self.assertEqual(units, {requirement.unit})                       # relabelled onto the requirement
        published = {record['attributes'].get('published_unit') for record in data.records}
        self.assertEqual(published, {'index_1967_100', None})             # the 2017 vintage already matches
        self.assertEqual({record['attributes']['base_period'] for record in data.records}, {'1967', '2017'})

    def test_a_point_in_time_frame_uses_one_base_per_cutoff(self):
        """The selection must not mix a 1967-base value with a 2017-base value in one frame."""
        records = []
        for period, level_1967, level_2017 in (('1975-01-01', 50.0, 40.0), ('1975-04-01', 52.0, 41.6)):
            records.append(self._fred('INDPRO', 'industrial_production_index', 'index_1967_100', '1967', period,
                                      '1975-06-01', level_1967))
            records.append(self._fred('INDPRO', 'industrial_production_index', 'index_2017_100', '2017', period,
                                      '2022-02-01', level_2017))
        self.store.write('fred_macro_panel', records)
        from worldmodel.estimation.loaders import panel
        requirement = next(r for r in estimator_for('labor_demand').all_requirements() if r.name == 'output')
        source = panel('output', 'INDPRO', 'industrial_production_index', None)
        data, _, _ = observation_set('labor_demand', self.store, sources=(source,), requirements=[requirement])
        for cutoff, expected in (('1976-01-01', {'1967'}), ('2023-01-01', {'2017'})):
            series = data.select(requirement, cutoff=cutoff, vintage_policy='strict')
            bases = {point.evidence and record['attributes']['base_period']
                     for point in series.points for record in data.records
                     if record['valid_from'] == point.time and record['attributes']['realtime_start'] <= cutoff
                     and record['value'] == point.value}
            self.assertEqual(bases, expected, cutoff)
            growth = [b / a for a, b in zip(series.values, series.values[1:])]
            self.assertTrue(all(abs(g - 1.04) < 1e-9 for g in growth), 'growth must be base-invariant')

    def test_monetary_gap_uses_the_2012_pair_when_2017_potential_is_missing(self):
        from worldmodel.estimation.loaders import _bases
        items = [('2020-04-15', 20100.0, 'a', '2012'), ('2020-05-01', 23115.0, 'b', '2017')]
        capacity = [('2020-01-20', 20000.0, 'c', '2012')]
        shared = sorted(set(_bases(items, '2020-06-01')) & set(_bases(capacity, '2020-06-01')))
        self.assertEqual(shared, ['2012'])        # the 2017 GDP vintage is ignored until potential follows


@unittest.skipUnless((DATA_ROOT / 'census_population' / 'manifests' / 'latest.json').exists(),
                     'No local census_population build; skipping the real-data smoke test')
class RealDataSmokeTests(unittest.TestCase):
    """Loads one small real slice: national PEP population, which is a few dozen records."""

    def test_population_slice_loads_with_real_time_vintages(self):
        from worldmodel.store import Store
        store = Store(DATA_ROOT, raw_verify='size')
        data, evidence, policy = observation_set('population_growth_rate', store)
        self.assertEqual(policy, 'strict')
        self.assertTrue(data.records)
        requirement = estimator_for('population_growth_rate').all_requirements()[0]
        self.assertEqual({record['metric'] for record in data.records}, {requirement.metric})
        self.assertEqual({record['unit'] for record in data.records}, {requirement.unit})
        self.assertEqual(evidence['inputs'][0]['dataset'], 'census_population')
        series = data.select(requirement, cutoff='2024-12-31', vintage_policy='strict')
        self.assertGreaterEqual(len(series), 8)
        self.assertEqual(series.audit()['vintage_modes'], ['real_time'])


if __name__ == '__main__':
    unittest.main()
