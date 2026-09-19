"""Pre-registration of natural-experiment designs.

A registration is a JSON document fixed before any post-period outcome is examined. The runner
records its SHA-256 and the Git commit that contains it, and refuses to run a registration that
is not committed or whose working copy differs from the committed version, unless explicitly
told otherwise (tests use synthetic data).
"""
import hashlib
import json
import subprocess
from pathlib import Path

SCHEMA = 'worldmodel.causal_registration/1'
REQUIRED = ('schema', 'study_id', 'registered_at', 'question', 'identification_strategy', 'treatment', 'units',
            'windows', 'controls', 'outcomes', 'estimator', 'inference', 'placebo_tests', 'acceptance_criteria',
            'assumptions', 'data', 'outcome_data_examined_before_registration')
CRITERION_TYPES = ('min_treated_units', 'min_clusters', 'pre_trend_wald_p_min', 'placebo_date_p_min',
                   'placebo_unit_rejection_rate_max', 'robustness_ci_overlap', 'min_events', 'permutation_p_max',
                   'metric_min')


def validate_registration(document):
    missing = [key for key in REQUIRED if key not in document]
    if missing:
        raise ValueError('registration missing: ' + ', '.join(missing))
    if document['schema'] != SCHEMA:
        raise ValueError(f'registration schema must be {SCHEMA}')
    criteria = document['acceptance_criteria']
    if not isinstance(criteria, list) or not criteria:
        raise ValueError('acceptance_criteria must be a non-empty list')
    ids = set()
    for item in criteria:
        if item.get('type') not in CRITERION_TYPES:
            raise ValueError(f'unknown acceptance criterion type: {item.get("type")}')
        if not item.get('id') or item['id'] in ids:
            raise ValueError('acceptance criteria need unique ids')
        ids.add(item['id'])
    if not isinstance(document['assumptions'], list) or not document['assumptions']:
        raise ValueError('assumptions must be a non-empty list')
    return document


def _git(args, cwd):
    try:
        out = subprocess.run(['git', *args], cwd=cwd, capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None, None
    return out.returncode, out.stdout.strip()


def registration_status(path):
    """SHA-256, committing Git commit and cleanliness of a registration file."""
    path = Path(path).resolve()
    data = path.read_bytes()
    status = {'path': str(path), 'sha256': hashlib.sha256(data).hexdigest(), 'commit': None, 'committed_clean': False}
    code, top = _git(['rev-parse', '--show-toplevel'], path.parent)
    if code != 0 or not top:
        status['note'] = 'not in a git repository'
        return status
    relative = str(path.relative_to(Path(top).resolve()))
    status['path'] = relative
    code, commit = _git(['log', '-1', '--format=%H', '--', relative], top)
    if code == 0 and commit:
        status['commit'] = commit
        tracked, _ = _git(['ls-files', '--error-unmatch', relative], top)
        clean_code, _ = _git(['diff', '--quiet', 'HEAD', '--', relative], top)
        status['committed_clean'] = tracked == 0 and clean_code == 0
        code, when = _git(['log', '-1', '--format=%cI', commit], top)
        status['committed_at'] = when if code == 0 else None
    return status


def load_registration(path, *, require_committed=True):
    """Load and validate a registration; by default it must be committed and unmodified."""
    document = validate_registration(json.loads(Path(path).read_text()))
    status = registration_status(path)
    if require_committed and not status['committed_clean']:
        raise ValueError(f'registration {path} is not committed or differs from its commit; commit it before running')
    return document, status
