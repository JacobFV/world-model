"""Offline acceptance tests for the CEPII Gravity pipeline (tiny fixture ZIP, real Runner)."""
import csv
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
HEADER = ['year', 'country_id_o', 'country_id_d', 'iso3_o', 'iso3_d', 'country_exists_o', 'country_exists_d', 'dist',
          'distcap', 'contig', 'comlang_off', 'fta_wto', 'rta_type', 'pop_o', 'gdp_o', 'wto_o', 'diplo_disagreement']


def table(rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(HEADER)
    for row in rows:
        writer.writerow([row.get(column, '') for column in HEADER])
    return buffer.getvalue()


ROWS = [
    # Pair DEU-FRA (subject side): distance constant, RTA switches on in 2001, missing year 2003 breaks runs.
    {'year': 1999, 'country_id_o': 'DEU', 'country_id_d': 'FRA', 'country_exists_o': 1, 'country_exists_d': 1, 'dist': 450.5,
     'contig': 1, 'fta_wto': 0, 'pop_o': 82000, 'gdp_o': 2100000000, 'wto_o': 1},
    {'year': 2000, 'country_id_o': 'DEU', 'country_id_d': 'FRA', 'country_exists_o': 1, 'country_exists_d': 1, 'dist': 450.5,
     'contig': 1, 'fta_wto': 0, 'pop_o': 82100, 'wto_o': 1},
    {'year': 2001, 'country_id_o': 'DEU', 'country_id_d': 'FRA', 'country_exists_o': 1, 'country_exists_d': 1, 'dist': 450.5,
     'contig': 1, 'fta_wto': 1, 'rta_type': 2, 'pop_o': 82200, 'wto_o': 1},
    {'year': 2002, 'country_id_o': 'DEU', 'country_id_d': 'FRA', 'country_exists_o': 1, 'country_exists_d': 0, 'dist': 450.5},
    {'year': 2003, 'country_id_o': 'DEU', 'country_id_d': 'FRA', 'country_exists_o': 1, 'country_exists_d': 1, 'dist': 450.5,
     'contig': 1, 'fta_wto': 1, 'rta_type': 2},
    {'year': 1999, 'country_id_o': 'DEU', 'country_id_d': 'DEU', 'country_exists_o': 1, 'country_exists_d': 1, 'pop_o': 1},
    # Reverse direction: country-year values for FRA, no duplicate pair records.
    {'year': 1999, 'country_id_o': 'FRA', 'country_id_d': 'DEU', 'country_exists_o': 1, 'country_exists_d': 1, 'dist': 450.5,
     'contig': 1, 'pop_o': 60000},
]


class GravityPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.store = Store(self.root / 'data')

    def build(self, rows):
        archive = self.root / 'gravity.zip'
        with zipfile.ZipFile(archive, 'w', zipfile.ZIP_DEFLATED) as zf:
            zf.writestr('Countries_V202211.csv', 'country_id,iso3,iso3num,country,countrylong,first_year,last_year,iso2\n'
                                                 'DEU,DEU,276,Germany,Federal Republic of Germany,,,DE\nFRA,FRA,250,France,French Republic,,,FR\n')
            zf.writestr('Gravity_V202211.csv', table(rows))
            zf.writestr('Label_rta_type_V202211.csv', 'rta_type,rta_type_lbl\n2,FTA\n')
        self.store.import_shards('cepii_gravity', [{'path': archive, 'retrieved_at': '2026-09-15T00:00:00+00:00'}],
                                 {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(ROOT / 'data'), self.store, ROOT).run('cepii_gravity')
        return list(self.store.records(ref))

    def test_country_year_and_run_length_pair_variables(self):
        rows = self.build(ROWS)
        obs = [r for r in rows if r['kind'] == 'observation']
        pops = sorted((r['subject'], r['valid_from'], r['value']) for r in obs if r['metric'] == 'population')
        self.assertEqual(pops, [('iso3:DEU', '1999-01-01', 82000), ('iso3:DEU', '2000-01-01', 82100),
                                ('iso3:DEU', '2001-01-01', 82200), ('iso3:FRA', '1999-01-01', 60000)])
        gdp = next(r for r in obs if r['metric'] == 'gdp_current_usd')
        self.assertEqual((gdp['unit'], gdp['dimensions']), ('thousand_USD', {'frequency': 'annual'}))
        distance = sorted((r['valid_from'], r['valid_to']) for r in obs if r['metric'] == 'bilateral_distance_most_populated_cities')
        self.assertEqual(distance, [('1999-01-01', '2002-01-01'), ('2003-01-01', '2004-01-01')])
        rta = sorted((r['valid_from'], r['valid_to'], r['value']) for r in obs if r['metric'] == 'regional_trade_agreement_in_force')
        self.assertEqual(rta, [('1999-01-01', '2001-01-01', 0), ('2001-01-01', '2002-01-01', 1), ('2003-01-01', '2004-01-01', 1)])
        kind = next(r for r in obs if r['metric'] == 'regional_trade_agreement_type')
        self.assertEqual((kind['attributes']['label'], kind['subject'], kind['dimensions']['partner']), ('FTA', 'iso3:DEU', 'iso3:FRA'))
        self.assertFalse(any(r['subject'] == 'iso3:FRA' and 'partner' in r['dimensions'] for r in obs))
        self.assertEqual({r['entity_id'] for r in rows if r['kind'] == 'entity'}, {'iso3:DEU', 'iso3:FRA'})

    def test_unsorted_pairs_are_rejected(self):
        rows = ROWS[:2] + [dict(ROWS[0], country_id_d='ITA')] + [dict(ROWS[1], year=2005)]
        with self.assertRaisesRegex(ValueError, 'not grouped'):
            self.build(rows)


if __name__ == '__main__':
    unittest.main()
