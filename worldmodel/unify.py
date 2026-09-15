"""Build a typed graph from existing bounded samples without network access."""
from .normalizers import AVAILABLE, UNAVAILABLE
from .ontology import validate_typed_graph
from .pipeline import Runner
from .graph import Graph
from .sampling import explore
from .util import read_json


def unify(catalog, store, project):
    runner = Runner(catalog, store, project)
    pins, coverage = {}, []
    # Check every required sample before publishing any normalization runs.
    samples = {}
    for dataset in AVAILABLE:
        profile = explore(store, dataset)
        if profile['status'] != 'sampled':
            raise ValueError(f'{dataset}: a successful bounded sample is required before unify')
        manifest = read_json(store.samples_dir(dataset)/profile['sample_id']/'manifest.json')
        samples[dataset] = manifest['artifact']
        coverage.append({'dataset': dataset, 'sample_id': profile['sample_id'],
                         'artifact': manifest['artifact'], 'representative': False})
    records = []
    for dataset, raw in samples.items():
        pins[dataset] = runner.run(dataset, raw_refs={dataset: [raw]})
        records.extend(store.records(pins[dataset]))
    counts = validate_typed_graph(records)
    ref = runner.run('world_evidence', input_refs=pins,
                     parameters={'coverage': coverage, 'unavailable_sources': list(UNAVAILABLE)})
    index = store.root/'world_evidence/index.sqlite'
    indexed = Graph(index).build(store, [ref])
    return {'artifact': ref, 'typed_graph': counts, 'index': str(index), 'indexed': indexed,
            'coverage': coverage, 'unavailable_sources': list(UNAVAILABLE),
            'scope': 'Existing bounded samples only; no network acquisition; not representative.'}
