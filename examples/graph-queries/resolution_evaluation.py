"""Held-out evaluation of *inferred* (name-based) matching against published identifiers.

`unify-resolve` attaches only asserted identity: published ``same_as`` rows and shared unique
identifier values. This script measures what a name-similarity matcher would add, and how often
it would be wrong, using ground truth that already exists in the catalog:

    OpenSanctions / OFAC / other sanctions lists publish an LEI for some listed entities.
    That gives labelled pairs (sanctions entity -> GLEIF LEI entity).

The matcher never sees an identifier: :func:`worldmodel.resolution.engine.inputs_from_records`
reads identifiers only from ``identifier_assignment`` assertions, and this script feeds the
sanctions and GLEIF *entity* records with those assertions withheld. Scoring is therefore
genuinely held out.

  recall    = labelled pairs the matcher put in one cluster / labelled pairs
  precision = labelled sanctions entities the matcher linked to their published LEI /
              labelled sanctions entities the matcher linked to any GLEIF entity

Precision is measured only on the labelled subset. A link from an unlabelled sanctions entity may
be correct or wrong and is not counted either way, so the figure is a proxy, not a full precision.
"""
import json
from pathlib import Path
import random
import sys
import time

PROJECT = Path(__file__).resolve().parents[2]
if str(PROJECT) not in sys.path:
    sys.path.insert(0, str(PROJECT))

import common  # noqa: E402
from worldmodel.catalog import Catalog  # noqa: E402
from worldmodel.resolution.engine import ResolutionEngine, inputs_from_records  # noqa: E402
from worldmodel.resources import resource_roots  # noqa: E402
from worldmodel.store import Store  # noqa: E402
from worldmodel.unify import _lines, inventory, records_path  # noqa: E402

SANCTIONS = ('ofac_sanctions', 'other_sanctions_lists', 'opensanctions', 'opensanctions_graph')
ORG_TYPES = {'organization', 'business', 'institution', 'government_agency', 'investment_fund', 'bank', 'agent'}


def sanctions_population(store, available):
    """Sanctions organization entities, plus the LEI each one publishes (the held-out label)."""
    entities, labels = {}, {}
    for dataset in SANCTIONS:
        if dataset not in available:
            continue
        ref = available[dataset]['ref']
        for line in _lines(records_path(store, ref)):
            if b'"kind":"entity"' not in line and b'"predicate":"identifier"' not in line:
                continue
            record = json.loads(line)
            if record.get('kind') == 'entity' and record.get('entity_type') in ORG_TYPES:
                entities[record.get('entity_id', record['id'])] = record
            elif record.get('predicate') == 'identifier':
                value = record.get('value') or {}
                identifier = str(value.get('id') or '')
                if identifier.startswith('lei:'):
                    labels[record['subject']] = 'lei:' + identifier[4:].strip().upper()
    return entities, {k: v for k, v in labels.items() if k in entities}


def gleif_population(store, available, *, sample, required, seed=20260915):
    """GLEIF legal entities: every labelled partner, plus distractors.

    Distractors are drawn with probability 0.5 while walking the golden copy in LEI order until
    the quota fills, so with ``sample`` below the full 3.43M they come from the low end of the LEI
    range. That is a bounded pool, not a uniform sample; ``--gleif-sample 0`` uses every entity.
    """
    ref = available['sec_gleif']['ref']
    chosen, seen, distractors = {}, 0, 0
    rng = random.Random(seed)
    for line in _lines(records_path(store, ref)):
        if b'"kind":"entity"' not in line:
            continue
        record = json.loads(line)
        if record.get('kind') != 'entity':
            continue
        key = record.get('entity_id', record['id'])
        if not key.startswith('lei:'):
            continue
        seen += 1
        if key in required:
            chosen[key] = record
        elif (not sample or distractors < sample) and rng.random() < 0.5:
            chosen[key] = record
            distractors += 1
    return chosen, seen


def main():
    options = common.parser(__doc__)
    options.add_argument('--workdir', type=Path, required=True)
    options.add_argument('--gleif-sample', type=int, default=250000,
                         help='GLEIF distractors drawn at random; 0 uses every LEI entity')
    options.add_argument('--workers', type=int, default=8)
    options.add_argument('--threshold', type=float, default=0.95)
    options.add_argument('--lower', type=float, default=0.5)
    args = options.parse_args()
    roots = resource_roots()
    catalog, store = Catalog(roots['catalog']), Store(roots['data'])
    available = {item['dataset']: item for item in inventory(catalog, store)[0]}
    missing = [name for name in ('sec_gleif',) if name not in available]
    if missing:
        raise SystemExit('missing published outputs: ' + ', '.join(missing))
    started = time.time()

    def trace(message):
        print('%6.0fs %s' % (time.time() - started, message), file=sys.stderr, flush=True)

    sanctions, labels = sanctions_population(store, available)
    trace('sanctions organizations %d, with a published LEI %d' % (len(sanctions), len(labels)))
    gleif, gleif_total = gleif_population(store, available, sample=args.gleif_sample,
                                          required=set(labels.values()))
    trace('gleif pool %d of %d LEI entities' % (len(gleif), gleif_total))
    resolvable = {key: lei for key, lei in labels.items() if lei in gleif}
    records = list(sanctions.values()) + list(gleif.values())
    trace('labelled pairs resolvable in this pool %d; matching %d records' % (len(resolvable), len(records)))
    args.workdir.mkdir(parents=True, exist_ok=True)
    engine = ResolutionEngine(args.workdir / 'evaluation.sqlite', kind='organization', max_block_size=1000,
                              link_mode='link', fresh=True)
    timings = {}
    try:
        # Identifier assertions are withheld: only entity records reach the matcher.
        engine.add_records(inputs_from_records(records, entity_types=ORG_TYPES))
        trace('loaded')
        engine.candidate_pairs()
        trace('blocked')
        engine.compare(workers=args.workers)
        trace('compared')
        engine.estimate(training='em')
        trace('estimated')
        engine.cluster(threshold=args.threshold, lower=args.lower, max_cluster_size=50)
        trace('clustered')
        view = engine.resolved_view()
        timings = dict(engine.timings)
        canonical_of = {}
        for cluster in engine.clusters(min_size=2):
            for member in cluster['members']:
                canonical_of[member] = cluster['canonical_id']
    finally:
        engine.close()
    hits = wrong = linked = 0
    examples = {'correct': [], 'incorrect': []}
    for key, lei in sorted(resolvable.items()):
        cluster = canonical_of.get(key)
        if cluster is None:
            continue
        partners = [m for m, c in canonical_of.items() if c == cluster and m.startswith('lei:')]
        if not partners:
            continue
        linked += 1
        sample = sorted(partners, key=lambda partner: (partner != lei, partner))[:4]
        row = {'sanctions_entity': key, 'sanctions_label': (sanctions[key].get('label') if key in sanctions else None),
               'published_lei': lei, 'gleif_entities_in_the_same_cluster': len(partners), 'sample': sample,
               'sample_labels': [gleif[p]['label'] for p in sample if p in gleif]}
        if lei in partners:
            hits += 1
            if len(examples['correct']) < 5:
                examples['correct'].append(row)
        else:
            wrong += 1
            if len(examples['incorrect']) < 5:
                examples['incorrect'].append(row)
    result = {
        'population': {'sanctions_organizations': len(sanctions), 'with_a_published_lei': len(labels),
                       'whose_lei_is_in_the_gleif_pool': len(resolvable),
                       'gleif_entities_in_pool': len(gleif), 'gleif_entities_available': gleif_total,
                       'gleif_sample_size': args.gleif_sample,
                       'gleif_pool_selection': ('every labelled partner, plus distractors drawn with probability '
                                                '0.5 in LEI order until the quota fills - a bounded pool, not a '
                                                'uniform sample of all LEI entities')},
        'model': {'training': 'unsupervised EM (no identifier supervision)', 'threshold': args.threshold,
                  'lower': args.lower, 'view_digest': view['view_digest'], 'counts': view['counts']},
        'held_out_scores': {
            'labelled_pairs': len(resolvable),
            'labelled_entities_linked_to_some_gleif_entity': linked,
            'linked_to_the_published_lei': hits,
            'linked_to_a_different_lei': wrong,
            'recall': round(hits / len(resolvable), 4) if resolvable else None,
            'precision_on_labelled_subset': round(hits / linked, 4) if linked else None},
        'examples': examples,
        'datasets_used': sorted(set(SANCTIONS) & set(available)) + ['sec_gleif'],
        'timings': timings, 'seconds': round(time.time() - started, 1),
        'interpretation': [
            'These matches are INFERRED from names and addresses. unify-resolve does not attach them; only '
            'published same_as rows and shared unique identifiers are attached to the index.',
            'Recall is bounded by blocking: a pair whose names share no blocking key is never compared.',
            'Precision here is measured only where a published LEI exists on the sanctions side. It is a proxy.',
            'A smaller --gleif-sample makes precision look better than a full-pool run, because there are fewer '
            'plausible wrong partners. The sample size is reported above; set --gleif-sample 0 for the full pool.'],
    }
    return common.emit('resolution_evaluation', result, save=args.save)


if __name__ == '__main__':
    main()
