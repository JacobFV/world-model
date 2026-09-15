"""Tests catch lost crosswalks, invented role validity, and affiliation overclaims."""
import importlib
import json
from pathlib import Path
import tempfile
import unittest
from worldmodel.pipeline import Context
from worldmodel.store import Store


class PeopleSourceTests(unittest.TestCase):
    def records(self, dataset, rows):
        module = importlib.import_module('worldmodel.people_sources')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'input.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in rows))
            store = Store(Path(td) / 'data')
            ref = store.import_file(dataset, path, source={'publisher': 'test'}, update_latest=False)
            return list(module.normalize(Context(store, {'id': dataset}, {}, [], [ref])))

    def legislator(self):
        return {'id': {'bioguide': 'C000127', 'wikidata': 'Q22250', 'fec': ['S8WA00194', 'H2WA01054']},
                'name': {'first': 'Maria', 'last': 'Cantwell', 'official_full': 'Maria Cantwell'},
                'other_names': [{'first': 'Maria', 'last': 'Example', 'end': '1990-01-01'}],
                'terms': [{'type': 'rep', 'start': '1993-01-05', 'end': '1995-01-03', 'state': 'WA', 'district': 1, 'party': 'Democrat'},
                          {'type': 'sen', 'start': '2001-01-03', 'end': '2007-01-03', 'state': 'WA', 'party': 'Democrat'}]}

    def test_module_is_available(self):
        self.assertIsNotNone(importlib.util.find_spec('worldmodel.people_sources'))

    def test_published_crosswalk_has_no_role_dates_or_name_merge(self):
        rows = self.records('congress_people', [self.legislator()])
        ids = [r for r in rows if r.get('predicate') == 'identifier_assignment']
        self.assertIn({'namespace': 'wikidata', 'value': 'Q22250'}, [r['value'] for r in ids])
        self.assertTrue(all('valid_from' not in r and 'valid_to' not in r for r in ids))
        links = [r for r in rows if r.get('predicate') == 'same_as']
        self.assertIn('fec:candidate:S8WA00194', [r['object'] for r in links])
        self.assertTrue(all(r['evidence'][0]['locator'] == 'line:1' for r in rows))
        self.assertTrue(all(r.get('observed_at') for r in rows))
        aliases = [r for r in rows if r.get('predicate') == 'alias']
        self.assertIn('Maria Example', [r['value'] for r in aliases])
        self.assertEqual(len(rows), len({r['id'] for r in rows}))

    def test_congress_role_terms_preserve_gaps_and_chambers(self):
        rows = self.records('congress_people', [self.legislator()])
        roles = [r for r in rows if r.get('predicate') == 'holds_role']
        self.assertEqual([(r['valid_from'], r['valid_to']) for r in roles],
                         [('1993-01-05', '1995-01-03'), ('2001-01-03', '2007-01-03')])
        organizations = {r['object'] for r in rows if r.get('predicate') == 'role_in_organization'}
        self.assertEqual(organizations, {'us:congress:house', 'us:congress:senate'})
        self.assertFalse(any(r.get('predicate') == 'located_in' for r in rows))
        self.assertEqual(len([r for r in rows if r.get('predicate') == 'role_affiliation']), 2)

    def test_missing_id_and_invalid_terms_are_rejected(self):
        for mutation in ('id', 'date', 'type'):
            row = self.legislator()
            if mutation == 'id': row['id'] = {}
            elif mutation == 'date': row['terms'][0]['end'] = '1990-01-01'
            else: row['terms'][0]['type'] = 'unknown'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                self.records('congress_people', [row])

    def test_openalex_years_do_not_imply_continuous_employment(self):
        row = {'id': 'https://openalex.org/A123', 'orcid': 'https://orcid.org/0000-0002-1825-0097',
               'display_name': 'Public Author', 'updated_date': '2026-01-01T00:00:00',
               'affiliations': [{'institution': {'id': 'https://openalex.org/I456', 'ror': 'https://ror.org/012345678',
                                                 'display_name': 'University'}, 'years': [2019, 2021]}]}
        rows = self.records('openalex_people', [row])
        ids = [r['value'] for r in rows if r.get('predicate') == 'identifier_assignment']
        self.assertIn({'namespace': 'orcid', 'value': '0000-0002-1825-0097'}, ids)
        self.assertIn({'namespace': 'ror', 'value': '012345678'}, ids)
        roles = [r for r in rows if r.get('predicate') == 'holds_role']
        self.assertEqual([(r['valid_from'], r['valid_to']) for r in roles],
                         [('2019-01-01', '2020-01-01'), ('2021-01-01', '2022-01-01')])
        self.assertTrue(all(r['attributes']['validity_basis'] == 'publication_affiliation_year' for r in roles))
        self.assertFalse(any(r.get('predicate') in ('employed_by', 'located_in') for r in rows))

    def test_openalex_no_years_does_not_invent_affiliation_period(self):
        rows = self.records('openalex_people', [{'id': 'https://openalex.org/A123', 'display_name': 'Author',
             'affiliations': [{'institution': {'id': 'https://openalex.org/I456', 'display_name': 'University'}, 'years': []}]}])
        self.assertFalse(any(r.get('predicate') == 'holds_role' for r in rows))
