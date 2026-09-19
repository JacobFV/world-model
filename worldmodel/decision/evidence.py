"""Evidence status of every mechanism, re-derived from the published calibration reports.

A contract *claims* a status for each mechanism. :class:`EvidenceIndex` reads every
validation report in the ``calibration_reports`` dataset, re-verifies it (report and
estimate digests, re-evaluated criteria, through
:func:`worldmodel.estimation.registry.calibration_record`), and joins it to the
pre-registered plan for supersession — the same procedure as ``wm calibration-status
--all``. :func:`resolve_mechanisms` then checks each claim against it. A claim can
only be lowered, never raised:

* ``validated`` stands only if the cited report exists, re-verifies, passes every
  declared criterion, is the counted report for its attempt label, its attempt is in
  the plan and not superseded, and its component is the one the contract names;
* otherwise a report that exists and verifies makes the mechanism
  ``estimated but not validated``, and a missing or unverifiable report makes it
  ``assumed``.

Nothing is hard-coded: which mechanisms are validated is whatever the store says.
"""
from pathlib import Path

STATUSES = ('validated', 'estimated but not validated', 'assumed')
RANK = {'validated': 2, 'estimated but not validated': 1, 'assumed': 0}
PLAN_PATH = Path(__file__).resolve().parents[1] / 'estimation' / 'real_data_plan.json'

DOES_NOT_ESTABLISH = (
    'Holdout forecast skill does not identify an intervention response: a validated mechanism forecast well '
    'on data it had not seen, conditional on realized inputs; it was never tested on what happens when someone intervenes.',
    'No mechanism here is causally identified; estimates are reduced-form and labelled as such by their reports.',
    'Declared bounds on uncertain inputs and assumed parameters are author statements, not probabilities or confidence sets.',
    'A policy that does well under this environment is optimal (or robust) only for the environment\'s assumptions.',
)

VALIDATED_BUT = ('Validated forecast skill is not a validated counterfactual response. Every mechanism used passed its '
                 'pre-registered holdout criteria, so the result is conditional on the fitted dynamics being right about '
                 'responses they were only ever scored on forecasting.')


def _lower(resolution, status, reason):
    if RANK[status] < RANK[resolution['status']]:
        resolution['status'] = status
    resolution['reasons'].append(reason)


class EvidenceIndex:
    """Verified validation reports keyed by ``report_id``.

    ``source`` is ``'store'`` when built by :meth:`from_store`. Entries hold only what
    resolution needs: attempt label, plan state, verdict, component, process, the
    calibration record (parameters, standard errors, process parameters) and the
    report's protocol and family diagnostics.
    """

    def __init__(self, entries, *, validated_processes=(), source='supplied', dataset='calibration_reports', plan_attempts=0):
        self.entries = dict(entries)
        self.validated_processes = sorted(validated_processes)
        self.source = source
        self.dataset = dataset
        self.plan_attempts = plan_attempts

    @classmethod
    def from_store(cls, store, *, plan=None, dataset='calibration_reports'):
        from ..estimation.registry import calibration_record
        from ..estimation.status import _attempt_of, _reports, plan_status
        from ..util import read_json
        plan = plan if plan is not None else read_json(PLAN_PATH)
        attempts = {a['id']: a for a in plan['attempts']}
        entries, newest = {}, {}
        for label, ref, report, mtime in _reports(store, dataset):
            attempt = _attempt_of(label, attempts)
            entry = {'report_id': report.get('report_id'), 'label': label, 'attempt': attempt, 'in_plan': attempt is not None,
                     'current': attempt is not None and not attempts[attempt].get('superseded_by'),
                     'superseded_by': attempts[attempt].get('superseded_by') if attempt else None,
                     'version': ref['version'], 'published': mtime, 'component': report.get('component'),
                     'process_id': report.get('process_id'), 'protocol': report.get('protocol'),
                     'limitations': report.get('limitations', []),
                     'diagnostics': (report.get('final_estimate') or {}).get('diagnostics'),
                     'data_inputs': report.get('data_inputs', [])}
            try:
                record = calibration_record(report, report_ref=ref)
            except ValueError as error:
                entry.update(verified=False, error=str(error), passed=False, failing=[], record=None)
            else:
                entry.update(verified=True, passed=bool(record['validated']), record=record,
                             failing=sorted(r['id'] for r in record['acceptance']['results'] if not r.get('passed')))
            entries[entry['report_id']] = entry
            if label is not None and (label not in newest or mtime > entries[newest[label]]['published']):
                newest[label] = entry['report_id']
        for entry in entries.values():
            entry['counted'] = newest.get(entry['label']) == entry['report_id']
        status = plan_status(store, plan, dataset=dataset)
        return cls(entries, validated_processes=status['validated_processes'], source='store', dataset=dataset,
                   plan_attempts=len(attempts))

    def get(self, report_id):
        return self.entries.get(report_id)

    def siblings(self, component, exclude=None):
        """Current, counted reports on ``component`` other than ``exclude``."""
        return sorted((e for e in self.entries.values()
                       if e['component'] == component and e['current'] and e.get('counted') and e['report_id'] != exclude),
                      key=lambda e: e['label'] or '')

    def summary(self):
        return {'source': self.source, 'dataset': self.dataset, 'reports': len(self.entries),
                'plan_attempts': self.plan_attempts, 'validated_processes': self.validated_processes}


def _scope(entry):
    protocol = entry.get('protocol') or {}
    from ..estimation.families import load_requirements
    spec = load_requirements()['components'].get(entry['component'], {})
    return {'attempt': entry['attempt'], 'horizon': protocol.get('horizon'), 'frequency': spec.get('frequency'),
            'train_end': protocol.get('train_end'), 'validation_end': protocol.get('validation_end'),
            'cutoff': protocol.get('cutoff'), 'conditional_inputs': spec.get('conditional_inputs', []),
            'interval_level': protocol.get('interval_level'), 'data_inputs': entry.get('data_inputs', [])}


def resolve_mechanism(name, mechanism, index):
    evidence = mechanism['evidence']
    declared = evidence['status']
    resolution = {'mechanism': name, 'declared_status': declared, 'status': declared, 'report_id': evidence.get('report_id'),
                  'component': evidence.get('component'), 'attempt': evidence.get('attempt'), 'verified': False,
                  'reasons': [], 'caveats': [], 'scope': None}
    if declared == 'assumed':
        resolution['verified'] = True
        resolution['reasons'].append('declared assumed: its values are written in the contract or kernel configuration')
        return resolution
    report_id = evidence.get('report_id')
    if report_id is None:
        resolution['reasons'].append('no report cited, so the estimate cannot be checked against the store')
        return resolution
    if index is None:
        _lower(resolution, 'estimated but not validated',
               'no calibration_reports store was read, so the cited report could not be confirmed')
        return resolution
    entry = index.get(report_id)
    if entry is None:
        _lower(resolution, 'assumed', f'cited report {report_id[:12]}... is not in {index.dataset}')
        return resolution
    resolution.update(attempt=entry['attempt'] or entry['label'], version=entry['version'])
    if not entry['verified']:
        _lower(resolution, 'assumed', f'cited report failed verification: {entry["error"]}')
        return resolution
    resolution['verified'] = True
    if evidence.get('component') and entry['component'] != evidence['component']:
        _lower(resolution, 'assumed', f'cited report is for component {entry["component"]}, not {evidence["component"]}')
        return resolution
    resolution['component'] = entry['component']
    if evidence.get('attempt') and evidence['attempt'] != entry['attempt']:
        resolution['caveats'].append(f'contract names attempt {evidence["attempt"]}; the store files this report under {entry["attempt"]}')
    resolution['scope'] = _scope(entry)
    if declared == 'validated':
        problems = []
        if not entry['passed']:
            problems.append('the report fails ' + ', '.join(entry['failing']))
        if not entry['in_plan']:
            problems.append('the report is not filed under any pre-registered attempt')
        elif not entry['current']:
            problems.append(f'its attempt is superseded by {entry["superseded_by"]}')
        if not entry.get('counted'):
            problems.append('a newer report under the same attempt label is the one counted')
        if problems:
            _lower(resolution, 'estimated but not validated', 'validated claim not supported: ' + '; '.join(problems))
        else:
            resolution['reasons'].append(f'report re-verified and passes every declared criterion; attempt {entry["attempt"]} is current')
    elif entry['passed'] and entry['current']:
        resolution['caveats'].append('the store shows this report passing; the contract claims less, and the lower claim stands')
    if not entry['passed'] or declared != 'validated':
        resolution['reasons'].append('estimated: parameters come from the cited report' +
                                     (f', which fails {", ".join(entry["failing"])}' if entry['failing'] else ''))
    for sibling in index.siblings(entry['component'], exclude=report_id):
        if not sibling['passed']:
            resolution['caveats'].append(f'current attempt {sibling["attempt"]} on the same component fails '
                                         f'{", ".join(sibling["failing"])} (a different specification or series)')
    if entry['process_id'] and entry['process_id'] not in index.validated_processes:
        resolution['caveats'].append(f'component {entry["component"]} is validated on its own, but its process '
                                     f'{entry["process_id"]} is not')
    return resolution


def resolve_mechanisms(contract, index):
    """``{mechanism: resolution}`` for every mechanism the contract declares."""
    return {name: resolve_mechanism(name, spec, index) for name, spec in sorted(contract['mechanisms'].items())}


def lower(resolution, status, reason):
    """Lower a resolution's status (never raise it) and record why."""
    _lower(resolution, status, reason)
    return resolution


def recommendation_label(used):
    """Automatic label for anything that rests on the mechanisms ``used`` (``{name: resolution}``)."""
    unvalidated = sorted(name for name, r in used.items() if r['status'] != 'validated')
    by_status = {status: sorted(n for n, r in used.items() if r['status'] == status) for status in STATUSES}
    if unvalidated:
        return {'label': 'rests_on_unvalidated_mechanisms', 'unvalidated_mechanisms': unvalidated, 'by_status': by_status,
                'text': ('UNVALIDATED: this rests on ' + ', '.join(f'{n} ({used[n]["status"]})' for n in unvalidated) +
                         '. It is optimal, robust or fragile only under those assumptions, and says nothing about the world '
                         'beyond them.')}
    return {'label': 'conditional_on_validated_fitted_dynamics', 'unvalidated_mechanisms': [], 'by_status': by_status,
            'text': VALIDATED_BUT}


def load_attempt_data(store, index, report_id, *, plan=None):
    """The data a report's attempt was fitted on: the plan's loader and options, pinned to the report's input versions.

    Returns ``{'attempt', 'versions', 'data', 'evidence'}``. Kernels use this for observed
    driver histories and to recompute an estimate's covariance from exactly its inputs.
    """
    from ..estimation.loaders import load_for
    from ..util import read_json
    entry = index.get(report_id) if index is not None else None
    if entry is None or not entry.get('attempt'):
        raise ValueError(f'report {report_id[:12]}... is not filed under a pre-registered attempt in this store')
    plan = plan if plan is not None else read_json(PLAN_PATH)
    attempt = {a['id']: a for a in plan['attempts']}[entry['attempt']]
    loader = attempt.get('loader') or {}
    options = dict(loader.get('options') or {})
    versions = {item['dataset']: item['version'] for item in entry.get('data_inputs', []) if item.get('version')}
    if versions:
        options['versions'] = versions
    data, evidence, _ = load_for(entry['component'], store, options, function=loader.get('function'))
    evidence = {k: v for k, v in (evidence or {}).items() if k != 'record_ids'}
    return {'attempt': entry['attempt'], 'versions': versions, 'data': data, 'evidence': evidence}
