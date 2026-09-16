"""Offline acceptance test: tiny UN XML, UK CSV and CSL CSV through the real Runner."""
import csv
import io
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]

UN = '''<?xml version="1.0" encoding="UTF-8"?>
<CONSOLIDATED_LIST dateGenerated="2026-09-14T23:00:00.818Z">
  <INDIVIDUALS><INDIVIDUAL>
    <DATAID>1</DATAID><FIRST_NAME>JOHN</FIRST_NAME><SECOND_NAME>FICTION</SECOND_NAME><UN_LIST_TYPE>DRC</UN_LIST_TYPE>
    <REFERENCE_NUMBER>CDi.901</REFERENCE_NUMBER><LISTED_ON>2012-12-31</LISTED_ON>
    <COMMENTS1>Associate of FICTIONAL GROUP (CDe.902).</COMMENTS1>
    <NATIONALITY><VALUE>Democratic Republic of the Congo</VALUE></NATIONALITY>
    <INDIVIDUAL_ALIAS><QUALITY>Good</QUALITY><ALIAS_NAME>JF</ALIAS_NAME></INDIVIDUAL_ALIAS>
    <INDIVIDUAL_ADDRESS><COUNTRY>Rwanda</COUNTRY></INDIVIDUAL_ADDRESS>
    <INDIVIDUAL_DATE_OF_BIRTH><TYPE_OF_DATE>EXACT</TYPE_OF_DATE><YEAR>1971</YEAR></INDIVIDUAL_DATE_OF_BIRTH>
    <INDIVIDUAL_DOCUMENT><TYPE_OF_DOCUMENT>Passport</TYPE_OF_DOCUMENT><NUMBER>X123</NUMBER></INDIVIDUAL_DOCUMENT>
  </INDIVIDUAL></INDIVIDUALS>
  <ENTITIES><ENTITY>
    <DATAID>2</DATAID><FIRST_NAME>FICTIONAL GROUP</FIRST_NAME><UN_LIST_TYPE>DRC</UN_LIST_TYPE>
    <REFERENCE_NUMBER>CDe.902</REFERENCE_NUMBER><LISTED_ON>2014-06-30</LISTED_ON>
  </ENTITY></ENTITIES>
</CONSOLIDATED_LIST>
'''

UK_HEADER = ['Last Updated', 'Unique ID', 'OFSI Group ID', 'UN Reference Number', 'Name 6', 'Name 1', 'Name 2', 'Name 3', 'Name 4', 'Name 5',
             'Name type', 'Alias strength', 'Title', 'Name non-latin script', 'Non-latin script type', 'Non-latin script language', 'Regime Name',
             'Designation Type', 'Designation source', 'Sanctions Imposed', 'Other Information', 'UK Statement of Reasons', 'Address Line 1',
             'Address Line 2', 'Address Line 3', 'Address Line 4', 'Address Line 5', 'Address Line 6', 'Address Postal Code', 'Address Country',
             'Phone number', 'Website', 'Email address', 'Date Designated', 'D.O.B', 'Nationality(/ies)', 'National Identifier number',
             'National Identifier additional information', 'Passport number', 'Passport additional information', 'Position', 'Gender',
             'Town of birth', 'Country of birth', 'Type of entity', 'Subsidiaries', 'Parent company', 'Business registration number (s)',
             'IMO number', 'Current owner/operator (s)', 'Previous owner/operator (s)', 'Current believed flag of ship', 'Previous flags',
             'Type of ship', 'Tonnage of ship', 'Length of ship', 'Year Built', 'Hull identification number (HIN)']


def uk_csv():
    out = io.StringIO()
    out.write('Report Date: 11-Sep-2026\n')
    writer = csv.DictWriter(out, UK_HEADER)
    writer.writeheader()
    base = {'Last Updated': '04/08/2026', 'Unique ID': 'RUS9999', 'OFSI Group ID': '99999', 'Regime Name': 'The Russia (Sanctions) (EU Exit) Regulations 2019',
            'Designation Type': 'Individual', 'Designation source': 'UK', 'Sanctions Imposed': 'Asset freeze|Travel Ban', 'Date Designated': '24/02/2022',
            'D.O.B': 'dd/mm/1960', 'Nationality(/ies)': '(1) Russia (2) Cyprus', 'Address Country': 'Russia', 'Address Line 6': 'Moscow'}
    writer.writerow({**base, 'Name 1': 'Ivan', 'Name 6': 'FICTIONOV', 'Name type': 'Primary Name'})
    writer.writerow({**base, 'Name 1': 'Vanya', 'Name 6': 'FICTIONOV', 'Name type': 'Alias', 'Alias strength': 'Good quality a.k.a'})
    writer.writerow({'Last Updated': '01/01/2020', 'Unique ID': 'DPR9999', 'UN Reference Number': 'CDe.902', 'Name 6': 'FICTIONAL SHIP',
                     'Name type': 'Primary name', 'Regime Name': "The Democratic People's Republic of Korea (Sanctions) (EU Exit) Regulations 2019",
                     'Designation Type': 'Ship', 'Designation source': 'UN', 'Date Designated': '03/10/2017', 'IMO number': 'IMO9562233',
                     'Current believed flag of ship': 'Comoros', 'Type of ship': 'Bulk Carrier'})
    return out.getvalue()


CSL_HEADER = ['_id', 'source', 'entity_number', 'type', 'programs', 'name', 'title', 'addresses', 'federal_register_notice', 'start_date', 'end_date',
              'standard_order', 'license_requirement', 'license_policy', 'call_sign', 'vessel_type', 'gross_tonnage', 'gross_registered_tonnage',
              'vessel_flag', 'vessel_owner', 'remarks', 'source_list_url', 'alt_names', 'citizenships', 'dates_of_birth', 'nationalities',
              'places_of_birth', 'source_information_url', 'ids']


def csl_csv():
    out = io.StringIO()
    writer = csv.DictWriter(out, CSL_HEADER, quoting=csv.QUOTE_ALL)
    writer.writeheader()
    row = {'_id': '17014', 'entity_number': '17014', 'type': 'Entity', 'programs': 'UKRAINE-EO13662; RUSSIA-EO14024', 'name': 'Fictional Agricultural Bank',
           'addresses': '3 Fictional Lane, Moscow, 119034, RU', 'alt_names': 'FAB; Fictional Bank',
           'ids': 'Registration Number, 1027700342890, RU; SWIFT/BIC, FICTRUMM; Legal Entity Number, 253400V1H6ART1UQ0N98', 'remarks': 'Directives apply.'}
    writer.writerow({**row, 'source': 'Sectoral Sanctions Identifications List (SSI) - Treasury Department'})
    writer.writerow({**row, 'source': 'Non-SDN Menu-Based Sanctions List (NS-MBS List) - Treasury Department'})
    writer.writerow({'_id': 'abc123', 'source': 'Entity List (EL) - Bureau of Industry and Security', 'name': 'Fictional Institute',
                     'addresses': 'Fictional Road, Beijing, CN', 'federal_register_notice': '85 FR 1', 'start_date': '2020-12-23'})
    return out.getvalue()


class OtherSanctionsPipelineTest(unittest.TestCase):
    def test_un_uk_and_csl_lists(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = []
            for name, content in (('un.xml', UN), ('uk.csv', uk_csv()), ('csl.csv', csl_csv())):
                (root / name).write_text(content, encoding='utf-8')
                files.append({'path': root / name, 'request': {'url': 'https://example.test/' + name}})
            store = Store(root / 'data')
            store.import_shards('other_sanctions_lists', files, {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('other_sanctions_lists')
            records = list(store.records(ref))
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        facts = [(r['subject'], r['predicate'], r.get('object', r.get('value'))) for r in records if r['kind'] == 'assertion']
        events = {tuple(r['participants'][:1]) + (r['occurred_at'],) for r in records if r['kind'] == 'event'}
        # UN
        self.assertEqual((entities['un:sanctions:CDi.901']['entity_type'], entities['un:sanctions:CDi.901']['label']), ('person', 'JOHN FICTION'))
        self.assertIn(('un:sanctions:CDi.901', 'nationality', 'iso3:COD'), facts)
        self.assertIn(('un:sanctions:CDi.901', 'located_in', 'iso3:RWA'), facts)
        self.assertIn(('un:sanctions:CDi.901', 'mentioned_in_listing_narrative', 'un:sanctions:CDe.902'), facts)
        self.assertIn(('un:sanctions:CDi.901', '2012-12-31'), events)
        # UK
        self.assertEqual(entities['uk:sanctions:RUS9999']['label'], 'Ivan FICTIONOV')
        self.assertIn(('uk:sanctions:RUS9999', 'sanctions_alias', {'name': 'Vanya FICTIONOV', 'alias_type': 'alias', 'quality': 'Good quality a.k.a'}), facts)
        self.assertIn(('uk:sanctions:RUS9999', 'nationality', 'iso3:CYP'), facts)
        self.assertIn(('uk:sanctions:RUS9999', 'birth_date', '1960'), facts)
        self.assertIn(('uk:sanctions:RUS9999', '2022-02-24'), events)
        self.assertEqual(sum(1 for r in records if r['kind'] == 'event' and r['participants'][0] == 'uk:sanctions:RUS9999'), 1)
        self.assertEqual(entities['uk:sanctions:DPR9999']['entity_type'], 'vessel')
        self.assertIn(('uk:sanctions:DPR9999', 'same_designation_as', 'un:sanctions:CDe.902'), facts)
        self.assertIn(('uk:sanctions:DPR9999', 'flag_state', 'iso3:COM'), facts)
        self.assertIn('imo:9562233', [v.get('id') for s, p, v in facts if p == 'identifier' and isinstance(v, dict)])
        # CSL: one entity, two list memberships, Treasury program ids shared with ofac_sanctions
        self.assertEqual(sum(1 for r in records if r.get('entity_id') == 'us_csl:17014'), 1)
        self.assertIn(('us_csl:17014', 'same_designation_as', 'ofac:party:17014'), facts)
        self.assertIn(('us_csl:17014', 'subject_to_sanctions_program', 'ofac:program:RUSSIA-EO14024'), facts)
        self.assertEqual(len([1 for s, p, o in facts if s == 'us_csl:17014' and p == 'listed_on']), 2)
        ids = {v.get('id') for s, p, v in facts if s == 'us_csl:17014' and p == 'identifier'}
        self.assertTrue({'swift:FICTRUMM', 'lei:253400V1H6ART1UQ0N98'} <= ids)
        self.assertIn(('us_csl:abc123', 'located_in', 'iso3:CHN'), facts)
        self.assertIn(('us_csl:abc123', '2020-12-23'), events)


if __name__ == '__main__':
    unittest.main()
