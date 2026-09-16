"""Offline pipeline test: tiny EIA bulk ZIP + nested EIA-860 workbook ZIP through the Runner."""
import gzip
import io
import json
from pathlib import Path
import tempfile
import unittest
from xml.sax.saxutils import escape
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]


def col(n):
    name = ''
    while n:
        n, rem = divmod(n - 1, 26)
        name = chr(65 + rem) + name
    return name


def xlsx(sheets):
    """Minimal xlsx: {sheet name: [row lists]} with a title row then header row (inline strings)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as book:
        entries, rels = [], []
        for number, (name, rows) in enumerate(sheets.items(), 1):
            entries.append(f'<sheet name="{escape(name)}" sheetId="{number}" r:id="rId{number}"/>')
            rels.append(f'<Relationship Id="rId{number}" Type="worksheet" Target="worksheets/sheet{number}.xml"/>')
            xml_rows = ''.join(
                f'<row r="{r}">' + ''.join(f'<c r="{col(c)}{r}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'
                                           for c, v in enumerate(values, 1)) + '</row>'
                for r, values in enumerate(rows, 1))
            book.writestr(f'xl/worksheets/sheet{number}.xml',
                          '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
                          + xml_rows + '</sheetData></worksheet>')
        book.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                      'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets>'
                      + ''.join(entries) + '</sheets></workbook>')
        book.writestr('xl/_rels/workbook.xml.rels', '<Relationships>' + ''.join(rels) + '</Relationships>')
    return buffer.getvalue()


SERIES = [
    {'category_id': '0', 'name': 'root', 'childseries': []},
    {'series_id': 'ELEC.PLANT.GEN.7887-NG-ALL.A', 'name': 'Net generation : Terry Bundy Generating Station (7887) : natural gas : all primemovers : annual',
     'units': 'megawatt-hours', 'f': 'A', 'iso3166': 'USA-NE', 'geography': 'USA-NE', 'lat': '40.9', 'lon': '-96.6',
     'data': [['2026', 1200.5], ['2025', None]]},
    {'series_id': 'ELEC.PLANT.GEN.7887-NG-ALL.M', 'name': 'Net generation : Terry Bundy Generating Station (7887) : natural gas : all primemovers : monthly',
     'units': 'megawatt-hours', 'f': 'M', 'iso3166': 'USA-NE', 'geography': 'USA-NE', 'data': [['202601', 99]]},
    {'series_id': 'SEDS.TEICD.AK.A', 'name': 'Total energy average price in the industrial sector, Alaska', 'units': 'Dollars per million Btu',
     'f': 'A', 'iso3166': 'USA-AK', 'geography': 'USA-AK', 'geoset_id': 'SEDS.TEICD.A', 'data': [['2023', 20.1]]},
    {'series_id': 'PET.WD2IM_R40-Z00_2.W', 'name': 'Rocky Mountain (PADD 4) Imports, Weekly', 'units': 'Thousand Barrels per Day', 'f': 'W',
     'geography': 'USA-CO+USA-ID', 'data': [['20260904', 'W']]},
    {'series_id': 'PET.WD2IM_R40-Z00_2.4', 'name': 'Rocky Mountain (PADD 4) Imports, 4 Week Avg', 'units': 'Thousand Barrels per Day', 'f': '4',
     'geography': 'USA-CO+USA-ID', 'data': [['20260904', 5]]},
    {'series_id': 'INTL.53-1-CHN-TBPD.M', 'name': 'Petroleum and other liquids production, China, Monthly', 'units': 'thousand barrels per day',
     'f': 'M', 'geography': 'CHN', 'geoset_id': 'INTL.53-1-TBPD.M', 'data': [['202605', 5512.9], ['202605', 5512.9], ['201901', 4000.0]]},
    {'series_id': 'INTL.53-1-WLD-TBPD.A', 'name': 'Petroleum and other liquids production, World, Annual', 'units': 'thousand barrels per day',
     'f': 'A', 'geography': 'WLD', 'geoset_id': 'INTL.53-1-TBPD.A', 'data': [['2024', 100000]]},
    {'series_id': 'INTL.53-1-SUN-TBPD.A', 'name': 'Petroleum and other liquids production, Former U.S.S.R., Annual', 'units': 'thousand barrels per day',
     'f': 'A', 'geography': 'SUN', 'geoset_id': 'INTL.53-1-TBPD.A', 'data': [['1990', 11000]]},
    {'series_id': 'INTL.53-1-XKS-TBPD.A', 'name': 'Petroleum and other liquids production, Kosovo, Annual', 'units': 'thousand barrels per day',
     'f': 'A', 'geography': 'XKS', 'geoset_id': 'INTL.53-1-TBPD.A', 'data': [['2024', 0]]},
    {'series_id': 'INTL.53-1-OPSA-TBPD.A', 'name': 'Petroleum and other liquids production, OPEC - South America, Annual', 'units': 'thousand barrels per day',
     'f': 'A', 'geography': 'VEN', 'geoset_id': 'INTL.53-1-TBPD.A', 'data': [['2024', 900]]},
    {'series_id': 'ELEC.PLANT.CONS_EG.7887-NG-ALL.A', 'name': 'Consumption for electricity generation : Terry Bundy (7887) : natural gas : all primemovers : annual',
     'units': 'thousand Mcf', 'f': 'A', 'iso3166': 'USA-NE', 'geography': 'USA-NE', 'data': [['2025', 10]]},
    {'series_id': 'PET.WCESTUS1.W', 'name': 'U.S. Ending Stocks excluding SPR of Crude Oil, Weekly', 'units': 'Thousand Barrels', 'f': 'W',
     'iso3166': 'USA', 'geography': 'USA', 'last_updated': '2026-09-10T18:53:26-04:00', 'data': [['20260904', 420000], ['19820820', 300000]]},
    {'series_id': 'STEO.PAPR_WORLD.A', 'name': 'World production', 'units': 'million barrels per day', 'f': 'A',
     'lastHistoricalPeriod': '2025', 'data': [['2026', 105.1], ['2025', 104.2]]},
]


class EiaEnergyPipelineTest(unittest.TestCase):
    def test_bulk_series_and_eia860(self):
        with tempfile.TemporaryDirectory() as temp:
            temp = Path(temp)
            with zipfile.ZipFile(temp / 'ELEC.zip', 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('ELEC.txt', ''.join(json.dumps(s) + '\n' for s in SERIES))
            plant = xlsx({'Plant': [['title'], ['Utility ID', 'Utility Name', 'Plant Code', 'Plant Name', 'State', 'County', 'Latitude', 'Longitude',
                                                'Balancing Authority Code', 'Balancing Authority Name', 'Sector Name'],
                                               ['63560', 'Sand Point Generating', '7887', 'Terry Bundy', 'NE', 'Lancaster', '40.9', '-96.6', 'SWPP', 'Southwest Power Pool', 'IPP']]})
            header = ['Plant Code', 'Plant Name', 'Generator ID', 'Technology', 'Prime Mover', 'Status', 'Nameplate Capacity (MW)',
                      'Summer Capacity (MW)', 'Winter Capacity (MW)', 'Operating Year', 'Energy Source 1']
            generator = xlsx({'Operable': [['title'], header, ['7887', 'Terry Bundy', 'GT1', 'Natural Gas Fired Combustion Turbine', 'GT', 'OP', '50.5', '45', ' ', '2003', 'NG']],
                              'Proposed': [['title'], header, ['9999', 'New Solar', 'PV1', 'Solar Photovoltaic', 'PV', 'P', '100', '', '', '', 'SUN']],
                              'Retired and Canceled': [['title'], header]})
            owner = xlsx({'Ownership': [['title'], ['Plant Code', 'Plant Name', 'Generator ID', 'Status', 'Owner Name', 'Ownership ID', 'Percent Owned'],
                                        ['7887', 'Terry Bundy', 'GT1', 'OP', 'Lincoln Electric', '11018', '0.6']]})
            with zipfile.ZipFile(temp / 'eia8602025.zip', 'w') as archive:
                archive.writestr('2___Plant_Y2025.xlsx', plant)
                archive.writestr('3_1_Generator_Y2025.xlsx', generator)
                archive.writestr('4___Owner_Y2025.xlsx', owner)
            (temp / 'manifest.txt').write_text('{"dataset": {"PET": {"last_updated": "2026-09-10T18:53:26-04:00"}}}')
            store = Store(temp / 'data')
            store.import_shards('eia_energy', [{'path': temp / 'ELEC.zip'}, {'path': temp / 'eia8602025.zip'}, {'path': temp / 'manifest.txt'}],
                                {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('eia_energy')
            records = [json.loads(line) for line in gzip.open(store.version_dir(ref) / 'records.jsonl.gz', 'rt')]
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        obs = [r for r in records if r['kind'] == 'observation']
        gen = [r for r in obs if r['metric'] == 'eia_elec_plant_gen']
        self.assertEqual({(r['subject'], r['valid_from'], r['value']) for r in gen},
                         {('eia:plant:7887', '2026-01-01', 1200.5), ('eia:plant:7887', '2025-01-01', None)})  # monthly plant series skipped
        self.assertEqual(gen[0]['dimensions']['fuel'], 'ng')
        seds = next(r for r in obs if r['metric'] == 'eia_seds_teicd')
        self.assertEqual((seds['subject'], seds['unit'], seds['valid_to']), ('geo:US:state:02', 'Dollars per million Btu', '2024-01-01'))
        weekly = [r for r in obs if r['dimensions'].get('series_id', '').startswith('PET.WD2IM')]
        self.assertEqual(len(weekly), 1)  # 4-week averages skipped by default
        self.assertEqual((weekly[0]['valid_from'], weekly[0]['valid_to'], weekly[0]['missing_reason']), ('2026-08-29', '2026-09-05', 'source_code:W'))
        self.assertEqual(weekly[0]['subject'], 'eia:series:PET.WD2IM_R40-Z00_2.W')
        intl = [r for r in obs if r['metric'] == 'eia_intl_53_1_tbpd' and r['dimensions']['frequency'] == 'monthly']
        self.assertEqual([(r['subject'], r['valid_from']) for r in intl], [('iso3:CHN', '2026-05-01')])  # pre-2022 monthly dropped
        annual = {r['subject'] for r in obs if r['dimensions'].get('series_id', '').startswith('INTL.') and r['dimensions']['frequency'] == 'annual'}
        self.assertEqual(annual, {'eia:region:WLD', 'eia:historical_country:SUN', 'iso3:XKX', 'eia:region:opec_south_america'})
        self.assertTrue(entities['eia:region:WLD']['attributes']['aggregate'])
        self.assertTrue(entities['eia:historical_country:SUN']['attributes']['historical'])
        self.assertEqual(entities['eia:historical_country:SUN']['entity_type'], 'country')
        self.assertNotIn('iso3:VEN', entities)
        self.assertFalse(any(k.startswith('iso3:') and k[5:] in ('WLD', 'WAK', 'XKS', 'SUN', 'CSK', 'DDR', 'SCG', 'YUG') for k in entities))
        self.assertFalse(any(r['metric'] == 'eia_elec_plant_cons_eg' for r in obs))  # non-default plant family
        stocks = sorted((r for r in obs if r['metric'] == 'crude_oil_commercial_stocks_excl_spr'), key=lambda r: r['valid_from'])
        self.assertEqual([(r['valid_from'], r['valid_to'], r['value']) for r in stocks],
                         [('1982-08-14', '1982-08-21', 300000), ('2026-08-29', '2026-09-05', 420000)])  # full history kept
        self.assertEqual((stocks[0]['subject'], stocks[0]['unit']), ('iso3:USA', 'Thousand Barrels'))
        self.assertEqual({k: stocks[1]['attributes'][k] for k in ('series_id', 'series_last_updated', 'bulk_file_last_updated')},
                         {'series_id': 'PET.WCESTUS1.W', 'series_last_updated': '2026-09-10T18:53:26-04:00',
                          'bulk_file_last_updated': '2026-09-10T18:53:26-04:00'})
        steo = {r['valid_from']: r['dimensions']['estimate_type'] for r in obs if r['metric'] == 'eia_steo_papr_world'}
        self.assertEqual(steo, {'2026-01-01': 'forecast', '2025-01-01': 'history'})
        self.assertEqual(entities['eia:generator:7887:GT1']['entity_type'], 'facility')
        self.assertIn('eia:generator:9999:PV1', entities)
        capacity = {(r['subject'], r['metric']): r['value'] for r in obs if r['unit'] == 'MW'}
        self.assertEqual(capacity[('eia:generator:7887:GT1', 'nameplate_capacity')], 50.5)
        self.assertNotIn(('eia:generator:7887:GT1', 'net_winter_capacity'), capacity)
        relations = {(r['subject'], r['predicate'], r.get('object')) for r in records if r['kind'] == 'assertion'}
        self.assertIn(('eia:plant:7887', 'located_in', 'geo:US:state:31'), relations)
        self.assertIn(('eia:utility:63560', 'operates', 'eia:plant:7887'), relations)
        self.assertIn(('eia:generator:7887:GT1', 'located_in', 'eia:plant:7887'), relations)
        owns = next(r for r in records if r.get('predicate') == 'owns')
        self.assertEqual((owns['subject'], owns['attributes']['ownership_fraction']), ('eia:utility:11018', 0.6))


if __name__ == '__main__':
    unittest.main()
