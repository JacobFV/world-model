"""Run pre-registered natural-experiment studies and publish their results.

    WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_studies.py --study fema_disasters_county_employment

Each registration must be committed and unmodified (``load_registration`` refuses otherwise). Results
are written to ``examples/natural-experiments/results/<study_id>.json`` and published as a
content-addressed report in the ``natural_experiment_reports`` dataset, pinning every input version.
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from worldmodel.artifacts import publish_report  # noqa: E402
from worldmodel.causal.exposure_study import run_exposure  # noqa: E402
from worldmodel.causal.registration import load_registration  # noqa: E402
from worldmodel.causal.studies import RUNNERS  # noqa: E402
from worldmodel.causal.studies_wave2 import RUNNERS as WAVE2  # noqa: E402
from worldmodel.causal.studies_wave3 import RUNNERS as WAVE3  # noqa: E402
from worldmodel.resources import resource_roots  # noqa: E402
from worldmodel.store import Store  # noqa: E402

REGISTRATIONS = ROOT / 'examples/natural-experiments/registrations'
RESULTS = ROOT / 'examples/natural-experiments/results'
ALL = {**RUNNERS, 'exposure_cyclones_county_employment': run_exposure, **WAVE2, **WAVE3}
MODULES = {**{s: 'studies_wave2' for s in WAVE2}, **{s: 'studies_wave3' for s in WAVE3}}


def summary(result):
    est = result.get('estimates') or {}
    primary = est.get('primary') or {}
    overall = primary.get('overall') if isinstance(primary, dict) else None
    return {'study_id': result['study_id'], 'outcome': (est.get('outcome') or {}).get('id'),
            'label': result['identification']['label'], 'overall': overall and {k: overall[k] for k in ('att', 'se', 'ci_low', 'ci_high', 'p')},
            'acceptance': {a['id']: a['passed'] for a in result['acceptance']}, 'verdict': result['verdict']}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--study', action='append', choices=sorted(ALL), help='study id (repeatable; default all)')
    parser.add_argument('--no-publish', action='store_true', help='write the results file but do not publish a report')
    parser.add_argument('--cache-dir', help='outcome extraction cache (default: <data>/natural_experiment_reports/scratch/panels)')
    args = parser.parse_args(argv)
    store = Store(resource_roots()['data'])
    cache = Path(args.cache_dir) if args.cache_dir else store.scratch_dir('natural_experiment_reports') / 'panels'
    for study in args.study or sorted(ALL):
        started = time.time()
        registration, status = load_registration(REGISTRATIONS / f'{study}.json')
        results = ALL[study](registration, status, store, cache)
        report = {'schema': 'worldmodel.natural_experiment_report/1', 'study_id': study, 'registration': status,
                  'results': results, 'seconds': round(time.time() - started, 1)}
        RESULTS.mkdir(parents=True, exist_ok=True)
        (RESULTS / f'{study}.json').write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + '\n')
        published = None
        if not args.no_publish:
            inputs = [dict(ref) for ref in registration['data']['inputs']]
            published = publish_report(store, 'natural_experiment_reports', json.loads(json.dumps(report, default=str)),
                                       {'study_id': study, 'registration_sha256': status['sha256'],
                                        'registration_commit': status['commit']},
                                       inputs=inputs,
                                       entrypoint=f'worldmodel/causal/{MODULES.get(study, "studies")}.py:{study}')
        print(json.dumps({'study': study, 'published': published, 'seconds': report['seconds'],
                          'results': [summary(r) for r in results]}, indent=1, default=str), flush=True)


if __name__ == '__main__':
    main()
