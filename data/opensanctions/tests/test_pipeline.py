"""Offline acceptance test: tiny OpenSanctions targets.simple.csv through the real Runner."""
from pathlib import Path
import tempfile
import unittest

from worldmodel.catalog import Catalog
from worldmodel.pipeline import Runner
from worldmodel.store import Store

ROOT = Path(__file__).resolve().parents[3]

CSV = '''"id","schema","name","aliases","birth_date","countries","addresses","identifiers","sanctions","phones","emails","program_ids","dataset","first_seen","last_seen","last_change"
"NK-fixture1","Vessel","FICTIONAL TANKER","","","","","IMO9422964","2026-07-24;Article 3s(2)","","","EU-MARE;EU-RUS","EU Council Official Journal Sanctioned Entities;EU Sanctions Map","2026-07-30T08:48:01","2026-09-15T13:11:55","2026-09-15T09:48:57"
"NK-fixture2","Person","Jane Fiction","J. Fiction;Джейн","1970-01-02","ru;ua-cri;suhh","Moscow","","","+7 000","jane@example.test","GB-RUS","UK FCDO Sanctions List","2022-03-01T10:00:00","2026-09-15T13:11:55","2026-01-06T08:22:01"
'''


class OpenSanctionsPipelineTest(unittest.TestCase):
    def test_targets_simple(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / 'targets.simple.csv').write_text(CSV, encoding='utf-8')
            store = Store(root / 'data')
            store.import_shards('opensanctions', [{'path': root / 'targets.simple.csv'}], {'publisher': 'fixture'}, complete=True)
            ref = Runner(Catalog(ROOT / 'data'), store, ROOT).run('opensanctions')
            records = list(store.records(ref))
        entities = {r['entity_id']: r for r in records if r['kind'] == 'entity'}
        self.assertEqual(entities['opensanctions:NK-fixture1']['entity_type'], 'vessel')
        self.assertEqual(entities['opensanctions:NK-fixture2']['entity_type'], 'person')
        self.assertIn('opensanctions:program:EU-MARE', entities)
        facts = [(r['subject'], r['predicate'], r.get('object', r.get('value'))) for r in records if r['kind'] == 'assertion']
        self.assertIn(('opensanctions:NK-fixture1', 'identifier', {'value': 'IMO9422964', 'id': 'imo:9422964'}), facts)
        self.assertIn(('opensanctions:NK-fixture2', 'associated_country', 'iso3:RUS'), facts)
        self.assertIn(('opensanctions:NK-fixture2', 'associated_country', 'iso3:UKR'), facts)
        self.assertIn(('opensanctions:NK-fixture2', 'associated_country', {'code': 'suhh', 'unmapped_country': True}), facts)
        self.assertEqual(len([1 for s, p, v in facts if s == 'opensanctions:NK-fixture2' and p == 'sanctions_alias']), 2)
        event = next(r for r in records if r['kind'] == 'event' and r['participants'][0] == 'opensanctions:NK-fixture2')
        self.assertEqual((event['event_type'], event['occurred_at']), ('sanctions_listing_first_seen', '2022-03-01T10:00:00+00:00'))


if __name__ == '__main__':
    unittest.main()
