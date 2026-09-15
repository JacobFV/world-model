"""Independent coverage maps: declarations and scenarios never imply observations."""
from collections import Counter


def coverage_report(records, registry, sources, evaluations=()):
    from .ontology import is_a
    rows = list(records)
    evidence = [r for r in rows if r.get('epistemic_status') in (None, 'observed')]
    entities = {}
    for r in evidence:
        if r['kind'] == 'entity':
            entities.setdefault(r.get('entity_id', r['id']), []).append(r)
    observations = [r for r in evidence if r['kind'] == 'observation']
    assertions = [r for r in evidence if r['kind'] == 'assertion']
    definitions = {'public_people': ['person'], 'roles': ['role'], 'organizations': ['organization'],
                   'markets': ['trading_venue', 'exchange', 'security', 'ticker_listing'],
                   'indices': ['market_index'], 'obligations': ['financial_obligation', 'loan'],
                   'law': ['law', 'legal_case'], 'academia': ['research_paper'],
                   'posts': ['post'], 'transport': ['road', 'airport', 'port', 'vessel'],
                   'conflicts': ['conflict'], 'territory': ['territory', 'water_body']}
    domains = {name: {'entities': sum(any(is_a(r['entity_type'], types) for r in group)
                                    for group in entities.values()), 'completeness': 'unknown'}
               for name, types in definitions.items()}
    scoped = sum(any(any(r.get('attributes', {}).get(k) for k in
                        ('synthetic_reference', 'unresolved_identity', 'legal_identity_unresolved')) for r in group)
                 for group in entities.values())
    return {'representative': False, 'scope': 'All retained evidence history; counts do not assert current activity or global coverage.',
            'excluded_scenario_records': len(rows)-len(evidence),
            'identities': {'entities': len(entities), 'count_basis': 'distinct source/canonical IDs before equivalence',
                           'source_scoped_references': scoped, 'universe_size': None, 'completeness': 'unknown',
                           'identifier_assignments': sum(r['predicate']=='identifier_assignment' for r in assertions)},
            'evidence': {'nonmissing_observations': sum(r.get('value') is not None for r in observations),
                         'missing_observations': sum(r.get('value') is None for r in observations),
                         'metrics': dict(Counter(r['metric'] for r in observations)),
                         'relations': dict(Counter(r['predicate'] for r in assertions if 'object' in r)),
                         'sources': list(sources)},
            'validation': {'process_contracts': len(registry.get('processes', [])),
                           'implementations': len(registry.get('implementations', [])),
                           'descriptive_evaluations': list(evaluations), 'causally_validated_models': [],
                           'interpretation': 'Executable kernels and descriptive holdouts do not establish causal predictive validity.'},
            'domains': domains, 'missing_domains': [name for name, value in domains.items() if not value['entities']]}
