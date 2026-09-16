"""Offline acceptance test: tiny OFAC advanced XML through the real Runner."""
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]
NS = 'https://sanctionslistservice.ofac.treas.gov/api/PublicationPreview/exports/ADVANCED_XML'

XML = f'''<?xml version="1.0" encoding="utf-8"?>
<Sanctions xmlns="{NS}">
  <DateOfIssue><Year>2026</Year><Month>9</Month><Day>14</Day></DateOfIssue>
  <ReferenceValueSets>
    <AliasTypeValues><AliasType ID="1400">A.K.A.</AliasType><AliasType ID="1403">Name</AliasType></AliasTypeValues>
    <CountryValues><Country ID="11" ISO2="IR">Iran</Country><Country ID="12" ISO2="PA">Panama</Country></CountryValues>
    <PartySubTypeValues><PartySubType ID="1" PartyTypeID="4">Vessel</PartySubType><PartySubType ID="3" PartyTypeID="2">Unknown</PartySubType><PartySubType ID="4" PartyTypeID="1">Unknown</PartySubType></PartySubTypeValues>
    <PartyTypeValues><PartyType ID="1">Individual</PartyType><PartyType ID="2">Entity</PartyType><PartyType ID="4">Transport</PartyType></PartyTypeValues>
    <FeatureTypeValues><FeatureType ID="8">Birthdate</FeatureType><FeatureType ID="10">Nationality Country</FeatureType><FeatureType ID="25">Location</FeatureType><FeatureType ID="3">Vessel Flag</FeatureType><FeatureType ID="13">SWIFT/BIC</FeatureType></FeatureTypeValues>
    <DetailTypeValues><DetailType ID="1430">DATE</DetailType><DetailType ID="1432">TEXT</DetailType><DetailType ID="1433">COUNTRY</DetailType></DetailTypeValues>
    <RelationTypeValues><RelationType ID="15003">Owned or Controlled By</RelationType></RelationTypeValues>
    <IDRegDocTypeValues><IDRegDocType ID="1626">Vessel Registration Identification</IDRegDocType></IDRegDocTypeValues>
    <ListValues><List ID="1550">SDN List</List></ListValues>
    <SanctionsTypeValues><SanctionsType ID="1705">Block</SanctionsType><SanctionsType ID="1">Program</SanctionsType></SanctionsTypeValues>
    <LegalBasisValues><LegalBasis ID="1828" LegalBasisShortRef="Executive Order 13224 (Terrorism)">Executive Order 13224 (Terrorism)</LegalBasis></LegalBasisValues>
    <LocPartTypeValues><LocPartType ID="1">Unknown</LocPartType><LocPartType ID="1454">CITY</LocPartType></LocPartTypeValues>
    <ScriptValues><Script ID="215" ScriptCode="Latin">Latin</Script></ScriptValues>
    <DocNameStatusValues><DocNameStatus ID="1">Primary Latin</DocNameStatus><DocNameStatus ID="2">Others</DocNameStatus></DocNameStatusValues>
    <ValidityValues><Validity ID="1">Valid</Validity></ValidityValues>
  </ReferenceValueSets>
  <Locations>
    <Location ID="500"><LocationCountry CountryID="11"/><LocationPart LocPartTypeID="1454"><LocationPartValue Primary="true"><Value>Tehran</Value></LocationPartValue></LocationPart></Location>
    <Location ID="501"><LocationPart LocPartTypeID="1"><LocationPartValue Primary="true"><Value>Iranian</Value></LocationPartValue></LocationPart></Location>
    <Location ID="502"><LocationCountry CountryID="12"/></Location>
  </Locations>
  <IDRegDocuments>
    <IDRegDocument ID="900" IDRegDocTypeID="1626" IdentityID="31" ValidityID="1"><IDRegistrationNo>IMO 9123456</IDRegistrationNo></IDRegDocument>
  </IDRegDocuments>
  <DistinctParties>
    <DistinctParty FixedRef="10"><Profile ID="10" PartySubTypeID="3"><Identity ID="11" Primary="true">
      <Alias AliasTypeID="1403" Primary="true"><DocumentedName ID="1" DocNameStatusID="1"><DocumentedNamePart><NamePartValue ScriptID="215">FICTIONAL SHIPPING CO</NamePartValue></DocumentedNamePart></DocumentedName></Alias>
      <Alias AliasTypeID="1400" Primary="false"><DocumentedName ID="2" DocNameStatusID="2"><DocumentedNamePart><NamePartValue ScriptID="215">FSC</NamePartValue></DocumentedNamePart></DocumentedName></Alias>
    </Identity>
    <Feature ID="1" FeatureTypeID="25"><FeatureVersion ID="1"><VersionLocation LocationID="500"/></FeatureVersion></Feature>
    <Feature ID="2" FeatureTypeID="13"><FeatureVersion ID="2"><VersionDetail DetailTypeID="1432">FICTIRTH</VersionDetail></FeatureVersion></Feature>
    </Profile></DistinctParty>
    <DistinctParty FixedRef="20"><Profile ID="20" PartySubTypeID="4"><Identity ID="21" Primary="true">
      <Alias AliasTypeID="1403" Primary="true"><DocumentedName ID="3" DocNameStatusID="1"><DocumentedNamePart><NamePartValue ScriptID="215">DOE</NamePartValue></DocumentedNamePart><DocumentedNamePart><NamePartValue ScriptID="215">Jane</NamePartValue></DocumentedNamePart></DocumentedName></Alias>
    </Identity>
    <Feature ID="3" FeatureTypeID="8"><FeatureVersion ID="3"><DatePeriod><Start><From><Year>1970</Year><Month>1</Month><Day>1</Day></From></Start><End><To><Year>1970</Year><Month>12</Month><Day>31</Day></To></End></DatePeriod><VersionDetail DetailTypeID="1430"/></FeatureVersion></Feature>
    <Feature ID="4" FeatureTypeID="10"><FeatureVersion ID="4"><VersionDetail DetailTypeID="1433"/><VersionLocation LocationID="501"/></FeatureVersion></Feature>
    </Profile></DistinctParty>
    <DistinctParty FixedRef="30"><Profile ID="30" PartySubTypeID="1"><Identity ID="31" Primary="true">
      <Alias AliasTypeID="1403" Primary="true"><DocumentedName ID="4" DocNameStatusID="1"><DocumentedNamePart><NamePartValue ScriptID="215">FICTIONAL STAR</NamePartValue></DocumentedNamePart></DocumentedName></Alias>
    </Identity>
    <Feature ID="5" FeatureTypeID="3"><FeatureVersion ID="5"><VersionDetail DetailTypeID="1433"/><VersionLocation LocationID="502"/></FeatureVersion></Feature>
    </Profile></DistinctParty>
  </DistinctParties>
  <ProfileRelationships>
    <ProfileRelationship ID="1" From-ProfileID="30" To-ProfileID="10" RelationTypeID="15003" Former="false"/>
  </ProfileRelationships>
  <SanctionsEntries>
    <SanctionsEntry ID="100" ProfileID="10" ListID="1550">
      <EntryEvent ID="1" EntryEventTypeID="1" LegalBasisID="1828"><Date><Year>2024</Year><Month>3</Month><Day>5</Day></Date></EntryEvent>
      <SanctionsMeasure ID="1" SanctionsTypeID="1705"/>
      <SanctionsMeasure ID="2" SanctionsTypeID="1"><Comment>SDGT</Comment></SanctionsMeasure>
      <SanctionsMeasure ID="3" SanctionsTypeID="1"><Comment>IRAN-EO13902</Comment></SanctionsMeasure>
    </SanctionsEntry>
  </SanctionsEntries>
</Sanctions>
'''


class OfacPipelineTest(unittest.TestCase):
    def build(self, content):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        path = root / 'SDN_ADVANCED.XML'
        path.write_text(content, encoding='utf-8')
        store = Store(root / 'data')
        store.import_shards('ofac_sanctions', [{'path': path, 'request': {'url': 'https://example.test/SDN_ADVANCED.XML'}}],
                            {'publisher': 'fixture'}, complete=True)
        ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('ofac_sanctions')
        return list(store.records(ref))

    def test_parties_identifiers_relationships_and_designations(self):
        records = self.build(XML)
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['ofac:party:10']['entity_type'], 'organization')
        self.assertEqual(entities['ofac:party:10']['label'], 'FICTIONAL SHIPPING CO')
        self.assertEqual((entities['ofac:party:20']['entity_type'], entities['ofac:party:20']['label']), ('person', 'DOE Jane'))
        self.assertEqual(entities['ofac:party:30']['entity_type'], 'vessel')
        self.assertIn('ofac:program:IRAN-EO13902', entities)
        facts = [(r['subject'], r['predicate'], r.get('object', r.get('value'))) for r in records if r['kind'] == 'assertion']
        self.assertIn(('ofac:party:10', 'sanctions_alias', {'alias_type': 'A.K.A.', 'name': 'FSC', 'script': 'Latin', 'status': 'Others'}), facts)
        self.assertIn(('ofac:party:10', 'located_in', 'iso3:IRN'), facts)
        self.assertIn(('ofac:party:20', 'nationality', 'iso3:IRN'), facts)
        self.assertIn(('ofac:party:30', 'flag_state', 'iso3:PAN'), facts)
        self.assertIn(('ofac:party:20', 'birth_date', '1970'), facts)
        # "30 owned or controlled by 10" means 10 controls 30.
        self.assertIn(('ofac:party:10', 'controls', 'ofac:party:30'), facts)
        identifiers = [v for s, p, v in facts if p == 'identifier']
        self.assertIn('imo:9123456', [v.get('id') for v in identifiers])
        self.assertIn('swift:FICTIRTH', [v.get('id') for v in identifiers])
        event = next(r for r in records if r['kind'] == 'event')
        self.assertEqual((event['event_type'], event['occurred_at']), ('sanctions_designation', '2024-03-05'))
        self.assertEqual(event['participants'], ['ofac:party:10', 'ofac:program:SDGT', 'ofac:program:IRAN-EO13902', 'gov:USA:treasury_ofac'])
        self.assertEqual(event['attributes']['legal_basis'], ['Executive Order 13224 (Terrorism)'])
        self.assertTrue(all(r['evidence'][0]['locator'].startswith('shard:0/xpath:/Sanctions') for r in records))

    def test_dtd_is_rejected(self):
        with self.assertRaises(Exception):
            self.build(XML.replace('<Sanctions ', '<!DOCTYPE Sanctions [<!ENTITY x "y">]><Sanctions ', 1))


if __name__ == '__main__':
    unittest.main()
