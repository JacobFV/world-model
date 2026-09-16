"""Offline tests for the WITS TRAINS SDMX pipeline."""
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
DATASET = 'wits_trains_tariffs'
NS = ('xmlns:message="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message" '
      'xmlns:generic="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic"')


def series(reporter, product, value, lines=1, tariff='MFN'):
    return (f'<generic:Series><generic:SeriesKey><generic:Value id="FREQ" value="A" /><generic:Value id="DATATYPE" value="Reported" />'
            f'<generic:Value id="PRODUCTCODE" value="{product}" /><generic:Value id="PARTNER" value="000" />'
            f'<generic:Value id="REPORTER" value="{reporter}" /></generic:SeriesKey><generic:Obs>'
            f'<generic:ObsDimension id="TIME_PERIOD" value="2023" /><generic:ObsValue value="{value}" /><generic:Attributes>'
            f'<generic:Value id="TARIFFTYPE" value="{tariff}" /><generic:Value id="OBS_VALUE_MEASURE" value="SimpleAverage" />'
            f'<generic:Value id="TOTALNOOFLINES" value="{lines}" /><generic:Value id="NBR_NA_LINES" value="0" />'
            f'<generic:Value id="MIN_RATE" value="0" /><generic:Value id="MAX_RATE" value="{value}" />'
            '<generic:Value id="NOMENCODE" value="H6" /></generic:Attributes></generic:Obs></generic:Series>')


def message(*parts):
    return (f'<?xml version="1.0" encoding="utf-8"?><message:GenericData {NS}><message:Header><message:ID>x</message:ID>'
            '</message:Header><message:DataSet>' + ''.join(parts) + '</message:DataSet></message:GenericData>')


class WitsPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def run_shards(self, *bodies):
        shards = []
        for number, body in enumerate(bodies):
            path = self.root / f'{number}.xml'
            path.write_text(body)
            shards.append({'path': path})
        self.store.import_shards(DATASET, shards, {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(ROOT / 'data'), self.store, ROOT).run(DATASET)
        return list(self.store.records(ref))

    def test_series_become_reporter_tariff_observations(self):
        records = self.run_shards(message(series('840', '010121', '0'), series('840', '010130', '6.80000019073486', 2)),
                                  message(series('918', '010121', '11.5')))
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['iso3:USA']['entity_type'], 'country')
        self.assertTrue(entities['wits:economy:918']['attributes']['aggregate'])
        self.assertEqual(entities['hs:010121']['entity_type'], 'product')
        obs = {(r['subject'], r['dimensions']['product']): r for r in records if r['kind'] == 'observation'}
        usa = obs[('iso3:USA', 'hs:010130')]
        self.assertEqual((usa['metric'], usa['unit'], usa['valid_from'], usa['valid_to']),
                         ('mfn_applied_tariff_simple_avg', 'percent', '2023-01-01', '2024-01-01'))
        self.assertAlmostEqual(usa['value'], 6.8, places=5)
        self.assertEqual((usa['dimensions']['partner'], usa['dimensions']['hs_revision'], usa['attributes']['total_lines']),
                         ('wits:economy:000', 'HS2022', 2))
        self.assertEqual(obs[('wits:economy:918', 'hs:010121')]['evidence'][0]['locator'], 'shard:1/series:0')
        self.assertEqual(sum(r['kind'] == 'entity' and r['entity_id'] == 'hs:010121' for r in records), 1)

    def test_error_body_is_rejected(self):
        with self.assertRaisesRegex(ValueError, 'GenericData'):
            self.run_shards('<string xmlns="http://schemas.microsoft.com/2003/10/Serialization/">Response too large</string>')


if __name__ == '__main__':
    unittest.main()
