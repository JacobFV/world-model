"""Offline acceptance test for the bis_bulk pipeline on tiny flat-CSV ZIP shards."""
import io
from pathlib import Path
import tempfile
import unittest
import zipfile

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

PROJECT = Path(__file__).resolve().parents[3]

CBPOL = '''STRUCTURE,STRUCTURE_ID,ACTION,FREQ:Frequency,REF_AREA:Reference area,TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value,UNIT_MEASURE:Unit of measure,UNIT_MULT:Unit Multiplier,TITLE:Title,OBS_STATUS:Observation Status
dataflow,BIS:WS_CBPOL(1.0): Central bank policy rates,I,D: Daily,US: United States,,,368: Per cent per year,0: Units,"Series row, no period",
dataflow,BIS:WS_CBPOL(1.0): Central bank policy rates,I,D: Daily,US: United States,2025-01-02,4.375,368: Per cent per year,0: Units,Policy,A: Normal value
dataflow,BIS:WS_CBPOL(1.0): Central bank policy rates,I,M: Monthly,XM: Euro area,2025-01,3.15,368: Per cent per year,0: Units,Policy,A: Normal value
dataflow,BIS:WS_CBPOL(1.0): Central bank policy rates,I,D: Daily,US: United States,2025-01-04,,368: Per cent per year,0: Units,Policy,H: Missing value; holiday or weekend
dataflow,BIS:WS_CBPOL(1.0): Central bank policy rates,I,D: Daily,US: United States,2025-01-06,,368: Per cent per year,0: Units,Policy,M: Missing value; data cannot exist
'''
EER = '''STRUCTURE,STRUCTURE_ID,ACTION,FREQ:Frequency,EER_TYPE:Type,EER_BASKET:Basket,REF_AREA:Reference area,TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value,TIME_FORMAT:Time Format,COLLECTION:Collection Indicator,TITLE_TS:Title (tseries level),UNIT_MEASURE:Unit of measure,OBS_STATUS:Observation Status,OBS_CONF:Observation confidentiality,OBS_PRE_BREAK:Pre-Break Observation
dataflow,BIS:WS_EER(1.0): Effective exchange rates,I,,N: Nominal,B: Broad (64 economies),DK: Denmark,,,,,,"882: Index, 2020 = 100",,,
dataflow,BIS:WS_EER(1.0): Effective exchange rates,I,M: Monthly,R: Real,B: Broad (64 economies),DK: Denmark,2025-01,101.2,,,,,A: Normal value,F: Free,
'''
LBS = '''STRUCTURE,STRUCTURE_ID,ACTION,FREQ:Frequency,L_MEASURE:Measure,L_POSITION:Balance sheet position,L_INSTR:Type of instruments,L_DENOM:Currency denomination,L_CURR_TYPE:Currency type of reporting country,L_PARENT_CTY:Parent country,L_REP_BANK_TYPE:Type of reporting institutions,L_REP_CTY:Reporting country,L_CP_SECTOR:Counterparty sector,L_CP_COUNTRY:Counterparty country,L_POS_TYPE:Position type,TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value,UNIT_MEASURE:Unit of measure,UNIT_MULT:Unit Multiplier,OBS_STATUS:Observation Status
dataflow,BIS:WS_LBS_D_PUB(1.0): Locational banking,I,Q: Quarterly,S: Amounts outstanding,C: Total claims,A: All instruments,TO1: All currencies,A: All currencies,5J: All countries,A: All reporting banks,GB: United Kingdom,A: All sectors,US: United States,N: Cross-border,2025-Q1,900.5,USD: US dollar,6: Millions,A: Normal value
dataflow,BIS:WS_LBS_D_PUB(1.0): Locational banking,I,Q: Quarterly,F: FX and break adjusted change,C: Total claims,A: All instruments,TO1: All currencies,A: All currencies,5J: All countries,A: All reporting banks,GB: United Kingdom,A: All sectors,US: United States,N: Cross-border,2025-Q1,12,USD: US dollar,6: Millions,A: Normal value
'''
CBS = '''STRUCTURE,STRUCTURE_ID,ACTION,FREQ:Frequency,L_MEASURE:Measure,L_REP_CTY:Reporting country,CBS_BANK_TYPE:CBS bank type,CBS_BASIS:CBS reporting basis,L_POSITION:Balance sheet position,L_INSTR:Type of instruments,REM_MATURITY:Remaining maturity,CURR_TYPE_BOOK:Currency type of booking location,L_CP_SECTOR:Counterparty sector,L_CP_COUNTRY:Counterparty country,TIME_PERIOD:Time period or range,OBS_VALUE:Observation Value,UNIT_MEASURE:Unit of measure,UNIT_MULT:Unit Multiplier,OBS_STATUS:Observation Status
dataflow,BIS:WS_CBS_PUB(1.0): Consolidated banking,I,Q: Quarterly,S: Amounts outstanding / Stocks,CH: Switzerland,"4R: Domestic banks(4B), excl. domestic positions",F: Immediate counterparty basis,C: Total claims,A: All instruments,A: Total (all maturities),TO1: All currencies,A: All sectors,DE: Germany,2024-Q4,12.5,USD: US dollar,6: Millions,A: Normal value
dataflow,BIS:WS_CBS_PUB(1.0): Consolidated banking,I,Q: Quarterly,B: Break in stocks,CH: Switzerland,"4R: Domestic banks(4B), excl. domestic positions",F: Immediate counterparty basis,C: Total claims,A: All instruments,A: Total (all maturities),TO1: All currencies,A: All sectors,DE: Germany,2024-Q4,1,USD: US dollar,6: Millions,A: Normal value
'''


def zipped(name, text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, 'w', zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, text)
    return buffer.getvalue()


class BisBulkPipelineTest(unittest.TestCase):
    def test_flat_csv_normalization(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = Store(root / 'data')
            catalog = Catalog(PROJECT / 'data')
            definition = catalog.get('bis_bulk')
            shards = []
            for name, text in (('WS_CBPOL_csv_flat.csv', CBPOL), ('WS_CBS_PUB_csv_flat.csv', CBS), ('WS_EER_csv_flat.csv', EER),
                               ('WS_LBS_D_PUB_csv_flat.csv', LBS)):
                path = root / (name + '.zip')
                path.write_bytes(zipped(name, text))
                shards.append({'path': path, 'retrieved_at': '2026-09-15T00:00:00+00:00'})
            store.import_shards('bis_bulk', shards, {'publisher': 'fixture', 'acquisition': definition['acquisition']}, complete=True)
            ref = Runner(catalog, store, PROJECT).run('bis_bulk')
            records = list(store.records(ref))
        observations = {r['id']: r for r in records if r['kind'] == 'observation'}
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        us = observations['bis:WS_CBPOL:D.US:2025-01-02']
        self.assertEqual((us['subject'], us['metric'], us['value'], us['unit']), ('iso3:USA', 'policy_rate', 4.375, 'percent'))
        self.assertEqual((us['valid_from'], us['valid_to']), ('2025-01-02', '2025-01-03'))
        self.assertEqual(observations['bis:WS_CBPOL:M.XM:2025-01']['subject'], 'agg:bis:XM')
        self.assertTrue(entities['agg:bis:XM']['attributes']['aggregate'])
        claims = [r for r in observations.values() if r['metric'] == 'bank_consolidated_claims']
        self.assertEqual(len(claims), 1)  # the break-in-stocks row is filtered out
        self.assertEqual((claims[0]['subject'], claims[0]['dimensions']['counterparty'], claims[0]['value'], claims[0]['unit']),
                         ('iso3:CHE', 'iso3:DEU', 12.5e6, 'USD'))
        self.assertEqual(claims[0]['valid_to'], '2025-01-01')
        self.assertIn('shard:1/member:WS_CBS_PUB_csv_flat.csv/line:2', claims[0]['evidence'][0]['locator'])
        self.assertNotIn('bis:WS_CBPOL:D.US:2025-01-04', observations)  # holiday/weekend gap is not emitted
        self.assertEqual(observations['bis:WS_CBPOL:D.US:2025-01-06']['missing_reason'], 'missing_cannot_exist')
        eer = observations['bis:WS_EER:M.R.B.DK:2025-01']
        self.assertEqual((eer['subject'], eer['metric'], eer['unit']), ('iso3:DNK', 'effective_exchange_rate_real', 'index_2020_100'))
        # WS_LBS_D_PUB is not in the acquisition file list (see README), but the mapping stays supported;
        # without an lbs_filter parameter every row is normalized.
        positions = sorted((r['dimensions']['measure'], r['subject'], r['dimensions']['counterparty'], r['value'], r['unit'])
                           for r in observations.values() if r['metric'] == 'bank_locational_positions')
        self.assertEqual(positions, [('F', 'iso3:GBR', 'iso3:USA', 12e6, 'USD'), ('S', 'iso3:GBR', 'iso3:USA', 900.5e6, 'USD')])


if __name__ == '__main__':
    unittest.main()
