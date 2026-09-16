"""Offline acceptance test: GDELT 1.0 daily export ZIP -> events and daily dyad signals."""
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]


def row(**values):
    fields = ['GLOBALEVENTID', 'SQLDATE', 'MonthYear', 'Year', 'FractionDate']
    actor = ['Code', 'Name', 'CountryCode', 'KnownGroupCode', 'EthnicCode', 'Religion1Code', 'Religion2Code', 'Type1Code', 'Type2Code', 'Type3Code']
    geo = ['Type', 'FullName', 'CountryCode', 'ADM1Code', 'Lat', 'Long', 'FeatureID']
    fields += ['Actor1' + f for f in actor] + ['Actor2' + f for f in actor]
    fields += ['IsRootEvent', 'EventCode', 'EventBaseCode', 'EventRootCode', 'QuadClass', 'GoldsteinScale', 'NumMentions', 'NumSources', 'NumArticles', 'AvgTone']
    fields += ['Actor1Geo_' + f for f in geo] + ['Actor2Geo_' + f for f in geo] + ['ActionGeo_' + f for f in geo] + ['DATEADDED', 'SOURCEURL']
    assert len(fields) == 58
    return '\t'.join(str(values.get(f, '')) for f in fields)


class GdeltPipelineTest(unittest.TestCase):
    def test_events_and_daily_dyads(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            lines = [row(GLOBALEVENTID=1, SQLDATE=20260705, Actor1Code='USAGOV', Actor1CountryCode='USA',
                         Actor2Code='CHN', Actor2CountryCode='CHN', IsRootEvent=1, EventCode='0231', EventBaseCode='023',
                         EventRootCode='02', QuadClass=1, GoldsteinScale=3.4, NumMentions=10, NumSources=2, NumArticles=10,
                         AvgTone=-1.5, ActionGeo_Type=1, ActionGeo_CountryCode='CH', ActionGeo_Lat=39.9, ActionGeo_Long=116.4,
                         DATEADDED=20260706, SOURCEURL='https://example.org/a'),
                     row(GLOBALEVENTID=2, SQLDATE=20260706, Actor1Code='USA', Actor1CountryCode='USA', Actor2CountryCode='CHN',
                         IsRootEvent=0, EventCode='190', EventBaseCode='190', EventRootCode='19', QuadClass=4,
                         GoldsteinScale=-10, NumMentions=4, AvgTone=-8, DATEADDED=20260706, SOURCEURL='https://example.org/b'),
                     row(GLOBALEVENTID=3, SQLDATE=20260706, Actor1CountryCode='EUR', QuadClass=3, EventCode='111',
                         GoldsteinScale='42#.5', NumMentions=1, AvgTone=0, DATEADDED=20260706, SOURCEURL='https://example.org/c')]
            archive = tmp / '20260706.export.CSV.zip'
            with zipfile.ZipFile(archive, 'w') as z:
                z.writestr('20260706.export.CSV', '\n'.join(lines) + '\n')
            store = Store(tmp / 'data')
            store.import_shards('gdelt_events', [{'path': archive, 'name': archive.name}], {'publisher': 'fixture'}, complete=True)
            runner = Runner(Catalog(ROOT / 'data'), store, ROOT)
            records = list(store.records(runner.run('gdelt_events')))
            events = [r for r in records if r['kind'] == 'event']
            self.assertEqual(len(events), 3)
            first = next(e for e in events if e['attributes']['gdelt_id'] == 1)
            self.assertEqual((first['event_type'], first['occurred_at'], first['participants']), ('cameo:0231', '2026-07-05', ['iso3:USA', 'iso3:CHN']))
            self.assertEqual(first['attributes']['action_geo']['lat'], 39.9)
            self.assertIn('cameo:region:EUR', {r.get('entity_id') for r in records})
            daily = list(store.records(runner.run('gdelt_events', stage='daily_dyads')))
            counts = [r for r in daily if r.get('metric') == 'gdelt_event_count']
            self.assertEqual(len(counts), 3)
            conflict = next(r for r in daily if r.get('metric') == 'gdelt_goldstein_mean' and r['dimensions']['quad_class'] == 'material_conflict')
            self.assertEqual((conflict['subject'], conflict['value'], conflict['dimensions']['counterpart'], conflict['valid_from']),
                             ('iso3:USA', -10, 'iso3:CHN', '2026-07-06'))


if __name__ == '__main__':
    unittest.main()
