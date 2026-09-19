"""Headline calibration counts re-derived from the published reports, never from a document.

``plan_status(store, plan)`` reads every validation report published in the
``calibration_reports`` dataset, re-verifies each one (report digest, estimate digest and
a re-evaluation of its declared criteria, through :func:`calibration_record`), keys it by
the ``parameters.attempt`` label its artifact manifest carries, and joins that to
``real_data_plan.json`` for supersession. It then counts:

* attempts registered, current (not superseded) and superseded;
* attempts that pass or fail — an attempt with several entities (``cash_balance``'s
  issuers) passes only if every entity's report passes;
* failing criteria over the current failing attempts, one count per criterion per attempt;
* validated processes: every required component has at least one *current* passing
  attempt, and a process with no required components can never validate.

An attempt with no published report (it raised, or it was never run) is listed and
excluded from the pass/fail counts. When one label has several reports, the most recently
published one counts and the others are listed.
"""
from pathlib import Path

from ..util import read_json
from .families import load_requirements
from .registry import calibration_record, required_components


def _reports(store, dataset):
    """``[(label, manifest, report)]`` for every validation report in ``dataset``."""
    root = Path(store.root) / dataset / 'manifests' / 'final'
    out = []
    for path in sorted(root.glob('*.json')):
        index = read_json(path)
        ref = index.get('reference') or {'dataset': dataset, 'version': index['version']}
        directory = store.version_dir(ref)
        report_path = directory / 'report.json'
        if not report_path.exists():
            continue
        report = read_json(report_path)
        if 'report_id' not in report:          # an estimate artifact, published beside its report
            continue
        manifest = read_json(directory / 'manifest.json')
        label = (manifest.get('parameters') or {}).get('attempt')
        out.append((label, ref, report, report_path.stat().st_mtime))
    return out


def _attempt_of(label, plan_ids):
    if label in plan_ids:
        return label
    base = label.split('[', 1)[0] if label else None
    return base if base in plan_ids else None


def plan_status(store, plan, *, dataset='calibration_reports'):
    attempts = {a['id']: a for a in plan['attempts']}
    runs, unmatched, duplicates = {}, [], []
    for label, ref, report, mtime in _reports(store, dataset):
        attempt_id = _attempt_of(label, attempts)
        if attempt_id is None:
            unmatched.append({'label': label, 'version': ref['version']})
            continue
        record = calibration_record(report, report_ref=ref)      # re-verifies and re-evaluates
        failing = sorted(r['id'] for r in record['acceptance']['results'] if not r.get('passed'))
        entry = {'label': label, 'version': ref['version'], 'report_id': report['report_id'],
                 'passed': record['validated'], 'failing': failing, 'process_id': record['process_id'],
                 'component': record['component'], 'published': mtime, 'record': record}
        previous = runs.get(label)
        if previous is not None and previous['report_id'] != entry['report_id']:
            older, newer = sorted((previous, entry), key=lambda item: item['published'])
            duplicates.append({'label': label, 'counted': newer['version'], 'not_counted': older['version']})
            entry = newer
        runs[label] = entry
    by_attempt = {}
    for entry in runs.values():
        by_attempt.setdefault(_attempt_of(entry['label'], attempts), []).append(entry)
    rows = []
    for attempt_id, attempt in attempts.items():
        entries = sorted(by_attempt.get(attempt_id, []), key=lambda item: item['label'])
        current = not attempt.get('superseded_by')
        row = {'attempt': attempt_id, 'current': current, 'process_id': attempt.get('process_id'),
               'reports': len(entries)}
        if not entries:
            row['verdict'] = 'no_report'
        else:
            row['verdict'] = 'pass' if all(e['passed'] for e in entries) else 'fail'
            row['failing'] = sorted({c for e in entries for c in e['failing']})
            row['entities'] = {e['label']: ('pass' if e['passed'] else e['failing']) for e in entries} if len(entries) > 1 else None
            row['component'] = entries[0]['component']
        rows.append(row)
    scored = [r for r in rows if r['verdict'] != 'no_report']
    current = [r for r in scored if r['current']]
    criteria = {}
    for row in current:
        if row['verdict'] == 'fail':
            for criterion in row['failing']:
                criteria[criterion] = criteria.get(criterion, 0) + 1
    passing_components = {}
    for row in current:
        if row['verdict'] == 'pass':
            passing_components.setdefault(row['process_id'], set()).add(row['component'])
    processes = {}
    for process_id in sorted({r['process_id'] for r in rows if r['process_id']}):
        required = required_components(process_id)
        have = passing_components.get(process_id, set())
        processes[process_id] = {'required_components': required,
                                 'passing_components': sorted(have & set(required)),
                                 'validated': bool(required) and set(required) <= have}
    # Processes that declare another process's component (taylor_rule_policy_rate requires
    # monetary_model_parameters): calibrate-all attaches a record only to its own process, so
    # these are reported beside the count rather than inside it.
    anywhere = set().union(*passing_components.values()) if passing_components else set()
    linked = []
    for process_id in sorted(load_requirements()['processes']):
        required = set(required_components(process_id))
        if process_id not in processes or not processes[process_id]['validated']:
            if required and required <= anywhere:
                linked.append(process_id)
    return {'registered': len(attempts), 'current': sum(1 for r in rows if r['current']),
            'superseded': sum(1 for r in rows if not r['current']),
            'without_report': [r['attempt'] for r in rows if r['verdict'] == 'no_report'],
            'current_scored': len(current),
            'current_pass': sum(1 for r in current if r['verdict'] == 'pass'),
            'current_fail': sum(1 for r in current if r['verdict'] == 'fail'),
            'superseded_pass': sum(1 for r in scored if not r['current'] and r['verdict'] == 'pass'),
            'failing_criteria': dict(sorted(criteria.items(), key=lambda item: (-item[1], item[0]))),
            'validated_processes': sorted(p for p, state in processes.items() if state['validated']),
            'validated_through_linked_components': linked,
            'processes': processes, 'reports': len(runs), 'unmatched_reports': unmatched,
            'duplicate_labels': duplicates,
            'attempts': rows}
