"""Offline tests: tiny FAF-shaped ZIP shards through the normalized stage."""
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]
YEARS = list(range(2017, 2025)) + [2030, 2035, 2040, 2045, 2050]


def _sheet(rows):
    cells = []
    for number, values in enumerate(rows, 1):
        row = ''.join(f'<c r="{chr(65 + i)}{number}" t="inlineStr"><is><t>{v}</t></is></c>' for i, v in enumerate(values))
        cells.append(f'<row r="{number}">{row}</row>')
    return ('<?xml version="1.0"?><worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
            + ''.join(cells) + '</sheetData></worksheet>')


def _metadata():
    sheets = [('Data Dictionary', [['Field'], ['fr_orig']]),
              ('State', [['Numeric Label', 'Description'], ['06', 'California'], ['48', 'Texas']]),
              ('FAF Zone (Domestic)', [['Numeric Label', 'Short Description', 'Long Description'], ['061', 'Los Angeles CA', 'LA CFS Area'], ['481', 'Austin TX', 'Austin CFS Area']]),
              ('FAF Zone (Foreign)', [['Numeric Label', 'Description'], ['805', 'Eastern Asia']]),
              ('Commodity (SCTG2)', [['Numeric Label', 'Description'], ['01', 'Live animals/fish'], ['34', 'Machinery']]),
              ('Mode', [['Numeric Label', 'Description'], ['1', 'Truck'], ['3', 'Water']])]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w') as book:
        book.writestr('xl/workbook.xml', '<workbook><sheets>' + ''.join(
            f'<sheet name="{name}" sheetId="{i}" r:id="rId{i}"/>' for i, (name, _) in enumerate(sheets, 1)) + '</sheets></workbook>')
        for i, (_, rows) in enumerate(sheets, 1):
            book.writestr(f'xl/worksheets/sheet{i}.xml', _sheet(rows))
    return buffer.getvalue()


def _faf5(state):
    orig, dest = ('dms_origst', 'dms_destst') if state else ('dms_orig', 'dms_dest')
    o, d = ('06', '48') if state else ('061', '481')
    header = ['fr_orig', orig, dest, 'fr_dest', 'fr_inmode', 'dms_mode', 'fr_outmode', 'sctg2', 'trade_type', 'dist_band']
    header += [f'tons_{y}' for y in YEARS] + [f'value_{y}' for y in YEARS] + [f'current_value_{y}' for y in range(2018, 2025)]
    header += [f'tmiles_{y}' for y in YEARS]

    def row(fr_orig, fr_dest, fr_in, mode, fr_out, trade, tons):
        values = [fr_orig, o, d, fr_dest, fr_in, mode, fr_out, '34', trade, '3']
        values += [str(tons if y != 2019 else 0) for y in YEARS] + [str(tons * 2) for y in YEARS]
        values += [str(tons * 3) for _ in range(2018, 2025)] + [str(tons * 10) for y in YEARS]
        return ','.join(values)

    lines = [','.join(header), row('', '', '', '1', '', '1', 1.5), row('805', '', '3', '1', '', '2', 2.0), row('', '', '', '3', '', '1', 4.0)]
    return '\n'.join(lines) + '\n'


def _zip(path, member, text):
    with zipfile.ZipFile(path, 'w') as archive:
        archive.writestr(member, text)
        archive.writestr('FAF5_metadata.xlsx', _metadata())


class FreightPipelineTests(unittest.TestCase):
    def test_aggregates_state_zone_foreign_and_faf6(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _zip(root / 'FAF5.7.1.zip', 'FAF5.7.1.csv', _faf5(False))
            _zip(root / 'FAF5.7.1_State.zip', 'FAF5.7.1_State.csv', _faf5(True))
            with zipfile.ZipFile(root / 'FAF6.0_State.zip', 'w') as archive:
                archive.writestr('FAF6.0_State.csv', 'fr_orig,dms_origst,dms_destst,fr_dest,fr_inmode,dms_mode,fr_outmode,sctg2,trade_type,tons_2022,value_2022\n'
                                 ',06,48,,,1,,34,1,7.25,10.5\n805,,48,,3,1,,34,2,1,0\n')
            with zipfile.ZipFile(root / 'FAF5.7.1_Reprocessed_1997-2012_State.zip', 'w') as archive:
                archive.writestr('FAF5.7.1_Reprocessed_1997-2012_State.csv',
                                 'fr_orig,dms_origst,dms_destst,fr_dest,fr_inmode,dms_mode,fr_outmode,sctg2,trade_type,'
                                 + ','.join(f'{k}_{y}' for k in ('tons', 'value', 'current_value', 'tmiles') for y in (1997, 2002, 2007, 2012)) + '\n'
                                 + ',06,48,,,1,,34,1,' + ','.join(['5'] * 4 + ['9'] * 4 + ['8'] * 4 + ['50'] * 4) + '\n'
                                 + '805,06,48,,3,1,,34,2,' + ','.join(['1'] * 4 + ['2'] * 4 + ['0'] * 4 + ['0'] * 4) + '\n'
                                 + '805,,,,3,1,,34,2,' + ','.join(['7'] * 16) + '\n')
            scenario_header = ['fr_orig', 'dms_origst', 'dms_destst', 'fr_dest', 'fr_inmode', 'dms_mode', 'fr_outmode', 'sctg2', 'trade_type', 'dist_band', 'tons_2030']
            scenario_header += [f'{k}_{y}_{s}' for k in ('tons', 'value') for s in ('low', 'high') for y in (2030, 2035, 2040, 2045, 2050)]
            with zipfile.ZipFile(root / 'FAF5.7.1_State_HiLoForecasts.zip', 'w') as archive:
                archive.writestr('FAF5.7.1_State_HiLoForecasts.csv', ','.join(scenario_header) + '\n'
                                 + ',06,48,,,1,,34,1,2,99,' + ','.join(['3'] * 5 + ['6'] * 5 + ['4'] * 5 + ['8'] * 5) + '\n')
            store = Store(root / 'data')
            shards = [{'path': root / name, 'name': name} for name in ('FAF5.7.1.zip', 'FAF5.7.1_State.zip', 'FAF6.0_State.zip',
                                                                       'FAF5.7.1_Reprocessed_1997-2012_State.zip', 'FAF5.7.1_State_HiLoForecasts.zip')]
            raw = store.import_shards('freight', shards, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('freight', raw_refs={'freight': [raw]})
            records = list(store.records(ref))
            by_id = {r['id']: r for r in records}
            self.assertEqual(len(by_id), len(records))
            truck = by_id['faf5.7.1:state_od:06:48:truck:34:freight_tons:2024']
            # Domestic (1.5) and the import's domestic leg (2.0) share the truck key.
            self.assertEqual((truck['value'], truck['unit'], truck['attributes']['source_rows']), (3.5, 'thousand_short_tons', 2))
            self.assertEqual(truck['subject'], 'geo:US:state:06')
            self.assertEqual(truck['dimensions']['destination'], 'geo:US:state:48')
            self.assertEqual((truck['valid_from'], truck['valid_to']), ('2024-01-01', '2025-01-01'))
            self.assertNotIn('faf5.7.1:state_od:06:48:truck:34:freight_tons:2019', by_id)  # zero omitted
            forecast = by_id['faf5.7.1:state_od:06:48:truck:34:freight_tons:2050']
            self.assertEqual((forecast['attributes']['series_type'], forecast['attributes']['projection']), ('baseline_forecast', True))
            self.assertEqual((forecast['valid_from'], forecast['valid_to']), ('2050-01-01', '2051-01-01'))
            self.assertIs(truck['attributes']['projection'], False)
            self.assertIs(by_id['faf5.7.1:state_od:06:48:truck:34:freight_value:2050']['attributes']['projection'], True)
            self.assertEqual(by_id['faf5.7.1:state_od:06:48:water:34:freight_ton_miles:2017']['value'], 40.0)
            self.assertEqual(by_id['faf5.7.1:state_od:06:48:truck:34:freight_value_current:2018']['value'], 10.5)
            self.assertEqual(by_id['faf5.7.1:zone_od:061:481:truck:34:freight_value:2020']['value'], 7.0)
            self.assertNotIn('faf5.7.1:zone_od:061:481:truck:34:freight_ton_miles:2020', by_id)
            imported = by_id['faf5.7.1:foreign:import:805:48:water:34:freight_tons:2022']
            self.assertEqual((imported['subject'], imported['dimensions']['destination'], imported['value']),
                             ('faf5:foreign_region:805', 'geo:US:state:48', 2.0))
            faf6 = by_id['faf6.0:faf6_state:06:48:truck:34:domestic:freight_value:2022']
            self.assertEqual((faf6['value'], faf6['unit']), (10.5, 'million_USD_2022'))
            self.assertEqual(by_id['faf6.0:faf6_state:f805:48:truck:34:import:freight_tons:2022']['subject'], 'faf6:foreign_region:805')
            history = by_id['faf5.7.1:state_od_history:06:48:truck:34:freight_tons:1997']
            # Domestic (5) plus the import's domestic leg (1) share the key.
            self.assertEqual((history['value'], history['valid_from'], history['attributes']['series_type'], history['attributes']['projection']),
                             (6.0, '1997-01-01', 'reprocessed_benchmark', False))
            self.assertEqual(by_id['faf5.7.1:state_od_history:06:48:truck:34:freight_ton_miles:2012']['value'], 50.0)
            self.assertFalse(any('geo:US:state:00' in json.dumps(r) for r in records))  # rows without a domestic leg are skipped
            low = by_id['faf5.7.1:state_od_scenarios:06:48:truck:34:freight_tons:low:2040']
            self.assertEqual((low['value'], low['dimensions']['scenario'], low['attributes']['projection'], low['attributes']['series_type']),
                             (3.0, 'low', True, 'low_forecast'))
            self.assertEqual(by_id['faf5.7.1:state_od_scenarios:06:48:truck:34:freight_value:high:2050']['value'], 8.0)
            self.assertFalse(any(':state_od_scenarios:' in r['id'] and r.get('metric') == 'freight_tons' and r['value'] == 99 for r in records))
            entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
            self.assertEqual(entities['faf5:zone:061']['label'], 'Los Angeles CA')
            self.assertEqual(entities['sctg2:34']['entity_type'], 'commodity')
            self.assertTrue(any(r.get('predicate') == 'within' and r['object'] == 'geo:US:state:06' for r in records))
            self.assertTrue(all(r['evidence'][0]['input'] == raw for r in records))

    def test_sample_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'x.jsonl').write_text('{}\n')
            store = Store(root / 'data')
            raw = store.import_file('freight', root / 'x.jsonl', source={'publisher': 'fixture'})
            with self.assertRaisesRegex(Exception, 'no sample normalizer'):
                Runner(Catalog(PROJECT / 'data'), store, PROJECT).run('freight', raw_refs={'freight': [raw]})


if __name__ == '__main__':
    unittest.main()
