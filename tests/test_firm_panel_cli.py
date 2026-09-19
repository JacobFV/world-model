"""``wm firm-panel`` and ``wm trade-panel`` over a store holding small published panels."""
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.panels import firm, firm_cli, trade, trade_cli
from tests import test_firm_panel as firm_fixture
from tests import test_trade_panel as trade_fixture


class FakeStore:
    """A store whose stage versions are plain ``records.jsonl`` files in a temporary directory."""

    def __init__(self, root, stages):
        self.root = Path(root)
        self.stages = {}
        for (dataset, stage), rows in stages.items():
            directory = self.root / dataset / 'artifacts' / stage / 'v'
            directory.mkdir(parents=True)
            with (directory / 'records.jsonl').open('w', encoding='utf-8') as out:
                for row in rows:
                    out.write(json.dumps(row, sort_keys=True, separators=(',', ':')) + '\n')
            self.stages[(dataset, stage)] = len(rows)

    def latest(self, dataset, stage=None):
        if (dataset, stage) not in self.stages:
            raise ValueError(f'no such stage: {dataset}/{stage}')
        return {'dataset': dataset, 'stage': stage, 'version': 'v'}

    def version_dir(self, ref):
        return self.root / ref['dataset'] / 'artifacts' / ref['stage'] / ref['version']

    def manifest(self, ref):
        return {'outputs': {'records.jsonl': {'rows': self.stages[(ref['dataset'], ref['stage'])], 'bytes': 1}},
                'inputs': [{'dataset': 'sec_gleif', 'version': 'x'}]}


class Args:
    def __init__(self, **fields):
        self.__dict__.update(fields)


class FirmPanelCliTest(unittest.TestCase):
    def setUp(self):
        source = firm_fixture.source()
        links = list(firm.build_links(source))
        ownership = list(firm.build_ownership(source, links))
        self.temporary = tempfile.TemporaryDirectory()
        with tempfile.TemporaryDirectory() as work:
            panel = list(firm.build_panel(source, links, ownership, workdir=work))
        self.store = FakeStore(self.temporary.name, {('firm_panel', 'links'): links,
                                                     ('firm_panel', 'ownership'): ownership,
                                                     ('firm_panel', 'panel'): panel})

    def tearDown(self):
        self.temporary.cleanup()

    def run_command(self, **fields):
        defaults = {'command': 'firm-panel', 'action': 'lookup', 'issuer': None, 'periods': 40, 'full': False,
                    'as_of': None}
        args = Args(**{**defaults, **fields})
        return firm_cli.execute(args, None, self.store, None, None)

    def test_lookup_by_cik_and_by_lei(self):
        result = self.run_command(issuer='320193')
        self.assertEqual(result['cik'], '0000320193')
        self.assertEqual(result['identity']['lei']['lei'], firm_fixture.ACME_LEI)
        latest = result['periods'][-1]
        self.assertEqual(latest['period_end'], '2020-12-31')
        self.assertEqual(latest['durations']['revenue']['4']['value'], 400)
        self.assertEqual(result['periods'][1]['instants']['total_assets']['value'], 460)
        self.assertEqual(len(result['institutional_ownership_quarters']), 1)
        self.assertEqual(self.run_command(issuer=firm_fixture.ACME_LEI)['cik'], '0000320193')

    def test_lookup_as_of_hides_later_vintages_and_later_rows(self):
        result = self.run_command(issuer='sec:cik:0000320193', **{'as_of': '2020-06-30'})
        periods = {row['period_end']: row for row in result['periods']}
        self.assertNotIn('2020-12-31', periods)
        self.assertEqual(periods['2019-12-31']['instants']['total_assets']['value'], 450)
        self.assertEqual(periods['2019-12-31']['instants']['total_assets']['vintages'], 2)

    def test_coverage_and_status(self):
        coverage = firm_cli.coverage(self.store)
        self.assertEqual(coverage['coverage']['issuers'], 1)
        self.assertEqual(coverage['coverage']['issuer_share']['federal_contracts'], 0.0)
        self.assertIn('UEI', coverage['federal_contracts'])
        self.assertEqual(coverage['construction']['links']['ciks_linked_to_lei'], 2)
        self.assertEqual(coverage['construction']['ownership']['issuer_quarters'], 1)
        self.assertEqual(coverage['construction']['panel']['rows'], 4)
        self.assertEqual(firm_cli.status(self.store)['panel']['version'], 'v')

    def test_unknown_issuer_is_an_error(self):
        with self.assertRaises(ValueError):
            self.run_command(issuer='0000000042')


class TradePanelCliTest(unittest.TestCase):
    def setUp(self):
        concordance, tariffs, panel = trade_fixture.build()
        self.temporary = tempfile.TemporaryDirectory()
        self.store = FakeStore(self.temporary.name, {('trade_panel', 'concordance'): concordance,
                                                     ('trade_panel', 'tariffs'): list(tariffs.values()),
                                                     ('trade_panel', 'panel'): list(panel.values())})

    def tearDown(self):
        self.temporary.cleanup()

    def test_lookup_shows_both_directions_and_the_schedules(self):
        args = Args(command='trade-panel', action='lookup', reporter='USA', partner='DEU', product='111111',
                    nomenclature='hs17', tariffs=True)
        result = trade_cli.execute(args, None, self.store, None, None)
        self.assertEqual(result['reporter'], 'iso3:USA')
        year = result['years'][0]
        self.assertEqual(year['exports']['value_kusd'], 3.0)
        self.assertEqual(year['exports']['importer_mfn_applied_pct'], 1.0)      # the EU schedule
        self.assertEqual(year['imports']['value_kusd'], 10.0)
        self.assertEqual(year['imports']['importer_mfn_applied_pct'], 2.5)      # the US schedule
        self.assertEqual({row['reporter'] for row in result['importer_schedules']}, {'iso3:USA', 'wits:economy:918'})

    def test_coverage_and_status(self):
        coverage = trade_cli.coverage(self.store)
        self.assertEqual(coverage['coverage']['hs17']['2019']['records'], 4)
        self.assertEqual(coverage['construction']['tariffs']['wits_reporter_years'], 3)
        self.assertEqual(coverage['construction']['concordance']['un_hs17_hs92_correlation'], 4)
        self.assertIn('baci_release_calendar', coverage['rules'])
        self.assertEqual(trade_cli.status(self.store)['tariffs']['version'], 'v')

    def test_lookup_needs_three_arguments(self):
        args = Args(command='trade-panel', action='lookup', reporter='USA', partner=None, product=None,
                    nomenclature=None, tariffs=False)
        with self.assertRaises(ValueError):
            trade_cli.execute(args, None, self.store, None, None)


if __name__ == '__main__':
    unittest.main()
