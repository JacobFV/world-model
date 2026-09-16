"""Offline acceptance test: tiny FtM graph through the real Runner (two-pass scoping)."""
import json
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]


def ftm(id_, schema, properties, caption=None, **extra):
    return {'id': id_, 'caption': caption or id_, 'schema': schema, 'referents': [], 'datasets': ['fixture'],
            'first_seen': '2024-01-01T00:00:00', 'last_seen': '2026-09-15T00:00:00', 'last_change': '2026-01-01T00:00:00',
            'properties': properties, 'target': extra.get('target', False)}


LINES = [
    # Relationship lines appear before their endpoints to prove two-pass scoping.
    ftm('own-1', 'Ownership', {'owner': ['sanctioned-co'], 'asset': ['sub-co'], 'percentage': ['51']}),
    ftm('own-2', 'Ownership', {'owner': ['plain-person'], 'asset': ['sanctioned-co'], 'role': ['beneficial owner']}),
    ftm('dir-1', 'Directorship', {'director': ['pep-1'], 'organization': ['sub-co'], 'role': ['CEO']}),
    ftm('fam-1', 'Family', {'person': ['pep-1'], 'relative': ['relative-1'], 'relationship': ['spouse']}),
    ftm('occ-1', 'Occupancy', {'holder': ['pep-1'], 'post': ['pos-1'], 'startDate': ['2020-01-01']}),
    ftm('link-out', 'UnknownLink', {'subject': ['unrelated-1'], 'object': ['unrelated-2']}),
    ftm('san-1', 'Sanction', {'entity': ['sanctioned-co'], 'authority': ['Fictional Council'], 'programId': ['EU-RUS'],
                              'startDate': ['2022-02-25'], 'reason': ['Fictional reason']}),
    ftm('sanctioned-co', 'Company', {'name': ['Fictional Oil LLC'], 'alias': ['FOL'], 'topics': ['sanction'], 'country': ['ru'],
                                     'leiCode': ['253400V1H6ART1UQ0N98'], 'imoNumber': ['IMO 9123456']}, caption='Fictional Oil LLC', target=True),
    ftm('sub-co', 'Company', {'name': ['Fictional Sub'], 'jurisdiction': ['cy']}),
    ftm('pep-1', 'Person', {'name': ['Jane Official'], 'topics': ['role.pep'], 'nationality': ['ua'], 'birthDate': ['1970']}),
    ftm('relative-1', 'Person', {'name': ['John Relative']}),
    ftm('plain-person', 'Person', {'name': ['Owner Person']}),
    ftm('pos-1', 'Position', {'name': ['Minister of Fiction'], 'country': ['ua']}),
    ftm('unrelated-1', 'Company', {'name': ['Unrelated'], 'topics': ['debarment']}),
    ftm('unrelated-2', 'Company', {'name': ['Unrelated 2']}),
]


class OpenSanctionsGraphTest(unittest.TestCase):
    def test_scoped_graph(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'entities.ftm.json'
            path.write_text(''.join(json.dumps(line) + '\n' for line in LINES), encoding='utf-8')
            store = Store(root / 'data')
            store.import_shards('opensanctions_graph', [{'path': path}], {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('opensanctions_graph')
            records = list(store.records(ref))
            self.assertEqual(list((store.scratch_dir('opensanctions_graph')).glob('osgraph-*')), [])
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertNotIn('opensanctions:unrelated-1', entities)
        self.assertNotIn('opensanctions:unrelated-2', entities)
        for key in ('sanctioned-co', 'sub-co', 'pep-1', 'relative-1', 'plain-person', 'pos-1'):
            self.assertIn('opensanctions:' + key, entities)
        self.assertEqual(entities['opensanctions:pep-1']['attributes']['scope'], 'seed')
        self.assertEqual(entities['opensanctions:sub-co']['attributes']['scope'], 'counterparty')
        self.assertEqual(entities['opensanctions:pos-1']['entity_type'], 'office')
        links = {(r['subject'], r['predicate'], r['object']) for r in records if r['kind'] == 'assertion' and 'object' in r}
        prefix = 'opensanctions:'
        self.assertIn((prefix + 'sanctioned-co', 'owns', prefix + 'sub-co'), links)
        self.assertIn((prefix + 'plain-person', 'controls', prefix + 'sanctioned-co'), links)
        self.assertIn((prefix + 'pep-1', 'director_of', prefix + 'sub-co'), links)
        self.assertIn((prefix + 'pep-1', 'family_member_of', prefix + 'relative-1'), links)
        self.assertIn((prefix + 'pep-1', 'holds_position', prefix + 'pos-1'), links)
        self.assertIn((prefix + 'sanctioned-co', 'associated_country', 'iso3:RUS'), links)
        self.assertIn((prefix + 'sub-co', 'registered_in', 'iso3:CYP'), links)
        self.assertIn((prefix + 'sanctioned-co', 'subject_to_sanctions_program', prefix + 'program:EU-RUS'), links)
        self.assertFalse(any('unrelated' in r.get('subject', '') for r in records if r['kind'] == 'assertion'))
        ownership = next(r for r in records if r.get('predicate') == 'owns')
        self.assertEqual(ownership['attributes']['percentage'], '51')
        event = next(r for r in records if r['kind'] == 'event')
        self.assertEqual((event['event_type'], event['occurred_at'], event['participants']),
                         ('sanctions_designation', '2022-02-25', [prefix + 'sanctioned-co', prefix + 'program:EU-RUS']))
        ids = {v['id'] for r in records if r.get('predicate') == 'identifier' for v in [r['value']] if 'id' in v}
        self.assertTrue({'lei:253400V1H6ART1UQ0N98', 'imo:9123456'} <= ids)


if __name__ == '__main__':
    unittest.main()
