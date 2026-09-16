"""Opt-in throughput benchmark: WORLDMODEL_SCALE_TEST=1 [WORLDMODEL_SCALE_RECORDS=1000000] [WORLDMODEL_SCALE_WORKERS=8]."""
import json
import os
import resource
import tempfile
import time
import unittest
from pathlib import Path

from worldmodel.graph import Graph
from worldmodel.resolution.engine import ResolutionEngine
from worldmodel.resolution.synthetic import organizations

ENABLED = os.environ.get('WORLDMODEL_SCALE_TEST') == '1'


@unittest.skipUnless(ENABLED, 'set WORLDMODEL_SCALE_TEST=1 to run the 1M-record resolution/graph benchmark')
class ResolutionScaleTests(unittest.TestCase):
    def test_million_record_resolution_and_graph_queries(self):
        n = int(os.environ.get('WORLDMODEL_SCALE_RECORDS', '1000000'))
        workers = int(os.environ.get('WORLDMODEL_SCALE_WORKERS', str(min(8, os.cpu_count() or 1))))
        report = {'records': n, 'workers': workers}
        with tempfile.TemporaryDirectory(dir=os.environ.get('WORLDMODEL_SCALE_DIR')) as tmp:
            engine = ResolutionEngine(Path(tmp) / 'resolution.sqlite', max_block_size=1000)
            truth = {}
            def stream():
                for item in organizations(n, seed=2026):
                    truth[item['entity_id']] = item.pop('truth')
                    yield item
            started = time.perf_counter()
            engine.add_records(stream())
            engine.candidate_pairs()
            engine.compare(workers=workers)
            engine.estimate()
            engine.cluster()
            view = engine.resolved_view()
            elapsed = time.perf_counter() - started
            true_positive = predicted = 0
            for cluster in engine.clusters():
                members = cluster['members']
                for i, a in enumerate(members):
                    for b in members[i + 1:]:
                        predicted += 1
                        true_positive += truth[a] == truth[b]
            groups = {}
            for key, label in truth.items():
                groups[label] = groups.get(label, 0) + 1
            actual = sum(k * (k - 1) // 2 for k in groups.values())
            report.update({'seconds': round(elapsed, 1), 'records_per_second': round(n / elapsed),
                           'timings': {k: round(v, 1) for k, v in engine.timings.items()},
                           'candidate_pairs': engine.stats['blocking']['candidate_pairs'],
                           'reduction_ratio': engine.stats['blocking']['reduction_ratio'],
                           'oversized_blocks': engine.stats['blocking']['oversized_blocks'],
                           'training': engine.stats['model']['training'], 'clusters': view['counts']['clusters'],
                           'precision': round(true_positive / max(predicted, 1), 4), 'recall': round(true_positive / max(actual, 1), 4),
                           'max_rss_gib': round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024 ** 2, 2)})
            self.assertGreater(report['precision'], 0.9)
            self.assertLess(report['candidate_pairs'], 50 * n)
            # Graph: one edge per resolved member link plus a synthetic supply chain over all records.
            ref = {'dataset': 'scale_fixture', 'version': 'c' * 64}
            evidence = [{'input': {'dataset': 'scale_fixture', 'version': 'a' * 64}, 'record_id': 'scale:1'}]
            ids = [row[0] for row in engine.db.execute('SELECT entity_id FROM records ORDER BY rid')]
            def records():
                for i, key in enumerate(ids):
                    yield {'kind': 'assertion', 'id': f'edge:{i}', 'subject': key, 'predicate': 'supplied_by',
                           'object': ids[(i * 7919 + 1) % len(ids)], 'observed_at': '2024-01-01', 'evidence': evidence,
                           'attributes': {'weight': (i % 100) / 10}}
            graph = Graph(Path(tmp) / 'graph.sqlite')
            started = time.perf_counter()
            built = graph.build_from_records([(ref, records())], validate=False)
            report['graph_build_seconds'] = round(time.perf_counter() - started, 1)
            report['graph_edges'] = built['edges']
            started = time.perf_counter()
            attached = graph.attach_resolution(engine.clusters(), view=view)
            report['attach_resolution_seconds'] = round(time.perf_counter() - started, 1)
            report['resolved_entities'] = attached['resolved_entities']
            started = time.perf_counter()
            hood = graph.neighborhood(ids[0], hops=3, limit=5000, resolved=True)
            report['neighborhood_3hop_ms'] = round((time.perf_counter() - started) * 1000, 1)
            started = time.perf_counter()
            path = graph.paths(ids[0], ids[len(ids) // 2], max_hops=6, limit=3)
            report['path_query_ms'] = round((time.perf_counter() - started) * 1000, 1)
            started = time.perf_counter()
            graph.degree_centrality(limit=10)
            report['degree_centrality_seconds'] = round(time.perf_counter() - started, 1)
            report['neighborhood_edges'] = len(hood['edges'])
            report['path_found'] = path['length']
            engine.close()
        print('\nSCALE ' + json.dumps(report, sort_keys=True))


if __name__ == '__main__':
    unittest.main()
