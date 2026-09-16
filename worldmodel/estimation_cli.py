"""CLI: wm estimate / wm validate / wm estimation-requirements / wm calibration-status."""
import json
from pathlib import Path
from .util import read_json

COMMANDS = {'estimate', 'validate', 'estimation-requirements', 'calibration-status', 'estimation-load', 'calibrate-all'}
REPORT_DATASET = 'calibration_reports'


def add_commands(sub):
    for name in ('estimate', 'validate'):
        command = sub.add_parser(name, help='Fit process parameters from point-in-time data' if name == 'estimate'
                                 else 'Select on validation, score an untouched holdout and evaluate acceptance criteria')
        command.add_argument('process', help='Process id (all required components) or a component id')
        command.add_argument('--component', action='append', default=[], help='Restrict to component(s)')
        command.add_argument('--data', action='append', default=[], help='DATASET[/stage][@version]; repeatable (series components)')
        command.add_argument('--family-data', type=Path, help='Native JSON data mapping for worldmodel.models family components')
        command.add_argument('--cutoff', required=True, help='Knowledge cutoff: only information available by this time is used')
        command.add_argument('--vintage-policy', choices=['strict', 'retrospective'], default='strict')
        command.add_argument('--series', action='append', default=[], help='NAME.FIELD=VALUE requirement override, e.g. cash.subject=sec:cik:0000320193')
        command.add_argument('--option', action='append', default=[], help='KEY=JSON estimator option')
        command.add_argument('--options-file', type=Path, help='JSON object of estimator options (e.g. field topology)')
        command.add_argument('--dataset', default='estimates' if name == 'estimate' else 'validation_reports')
        command.add_argument('--no-publish', action='store_true', help='Return results without publishing artifacts')
        if name == 'validate':
            command.add_argument('--train-end', required=True)
            command.add_argument('--validation-end', required=True)
            command.add_argument('--horizon', type=int, default=1)
            command.add_argument('--window', choices=['expanding', 'rolling'], default='expanding')
            command.add_argument('--window-size', type=int)
            command.add_argument('--refit-every', type=int, default=1)
            command.add_argument('--interval-level', type=float, default=0.8)
            command.add_argument('--season', type=int)
            command.add_argument('--criteria', type=Path, help='JSON list of acceptance criteria (default: component defaults)')
    requirements = sub.add_parser('estimation-requirements', help='Series, units, sources and parameters each estimator needs')
    requirements.add_argument('process', nargs='?')
    requirements.add_argument('--full', action='store_true', help='Print the complete requirements.json document')
    status = sub.add_parser('calibration-status', help='Verify validation reports and show resulting process validation state')
    status.add_argument('reports', nargs='+', help='validation report DATASET[@version]')
    load = sub.add_parser('estimation-load', help='Build estimator inputs from published catalog datasets and summarize them')
    load.add_argument('target', nargs='?', help='Component id or model family id; omitted lists catalog availability')
    load.add_argument('--option', action='append', default=[], help='KEY=JSON loader option (e.g. issuer="sec:cik:0000320193")')
    load.add_argument('--options-file', type=Path, help='JSON object of loader options')
    load.add_argument('--out', type=Path, help='Write the loaded family data mapping to this JSON file')
    load.add_argument('--limit', type=int, default=5, help='Rows shown per series in the summary')
    plan = sub.add_parser('calibrate-all', help='Run every pre-registered attempt in real_data_plan.json on catalog data')
    plan.add_argument('--attempt', action='append', default=[], help='Attempt id; repeatable (default: all)')
    plan.add_argument('--dataset', default=REPORT_DATASET, help='Derived dataset for published reports and estimates')
    plan.add_argument('--no-publish', action='store_true', help='Run without publishing report artifacts')
    plan.add_argument('--plan', type=Path, help='Alternative pre-registered plan file')
    plan.add_argument('--out', type=Path, help='Write the full result summary to this JSON file')


def _value(text):
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _overrides(values):
    out = {}
    for item in values:
        key, separator, value = item.partition('=')
        name, dot, field = key.partition('.')
        if not separator or not dot or not name or not field:
            raise ValueError('--series expects NAME.FIELD=VALUE')
        out.setdefault(name, {})[field] = _value(value)
    return out


def _options(args):
    options = {}
    if args.options_file:
        options.update(read_json(args.options_file))
        if not isinstance(options, dict):
            raise ValueError('--options-file must contain a JSON object')
    for item in args.option:
        key, separator, value = item.partition('=')
        if not separator or not key:
            raise ValueError('--option expects KEY=JSON')
        options[key] = _value(value)
    return options


def _components(process, selected):
    from .estimation.families import ESTIMATORS, components_for
    from .estimation.model_families import family_components
    if process in ESTIMATORS or process in family_components():
        components = [process]
    else:
        components = components_for(process)
        if not components:
            raise ValueError(f'Process {process} declares no estimable components')
    if selected:
        unknown = set(selected) - set(components) - set(ESTIMATORS) - set(family_components())
        if unknown:
            raise ValueError(f'Unknown components: {sorted(unknown)}')
        components = [c for c in selected]
    return components


PLAN_PATH = Path(__file__).with_name('estimation') / 'real_data_plan.json'


def load_plan(path=None):
    return read_json(path or PLAN_PATH)


def _loaded_summary(target, data, evidence, policy, limit=5):
    from .estimation.data import ObservationSet
    out = {'target': target, 'vintage_policy': policy, 'evidence': {k: v for k, v in evidence.items() if k != 'record_ids'}}
    if isinstance(data, ObservationSet):
        series = {}
        for record in data.records:
            key = (record.get('metric'), record.get('unit'))
            item = series.setdefault(key, {'count': 0, 'first': None, 'last': None, 'subjects': set(), 'sample': []})
            item['count'] += 1
            valid = record.get('valid_from')
            item['first'] = valid if item['first'] is None or valid < item['first'] else item['first']
            item['last'] = valid if item['last'] is None or valid > item['last'] else item['last']
            if len(item['subjects']) < 5:
                item['subjects'].add(record.get('subject'))
            if len(item['sample']) < limit:
                item['sample'].append({k: record[k] for k in ('id', 'valid_from', 'value', 'observed_at') if k in record})
        out['records'] = len(data.records)
        out['series'] = [{'metric': metric, 'unit': unit, **{k: (sorted(v) if isinstance(v, set) else v) for k, v in item.items()}}
                         for (metric, unit), item in sorted(series.items(), key=str)]
        return out
    out['rows'] = {key: len(value) for key, value in data.items() if isinstance(value, list)}
    out['keys'] = sorted(data)
    out['sample'] = {key: value[:limit] for key, value in data.items() if isinstance(value, list) and value}
    return out


def _attempt_options(attempt, entity=None):
    options = dict((attempt.get('loader') or {}).get('options') or {})
    if entity is not None:
        options[(attempt['loader'].get('per_entity') or 'issuer')] = entity
    return options


def _acceptance(report):
    return [{k: r.get(k) for k in ('id', 'passed', 'observed', 'threshold', 'reason') if k in r} for r in report['acceptance']['results']]


def _run_attempt(attempt, store, *, publish=True, dataset=None):
    """Run one pre-registered attempt: load catalog data, validate, publish, build a calibration record."""
    from .estimation import (validate_process, publish_validation_report, publish_estimate, estimator_for, calibration_record)
    from .estimation.loaders import load_for, MissingData
    protocol = attempt['protocol']
    entities = attempt.get('entities') or [None]
    runs = []
    for entity in entities:
        label = attempt['id'] if entity is None else f"{attempt['id']}[{entity}]"
        run = {'id': label, 'attempt': attempt['id'], 'target': attempt['target'], 'entity': entity}
        try:
            options = _attempt_options(attempt, entity)
            if attempt['kind'] == 'component':
                estimator = estimator_for(attempt['target'], overrides=attempt.get('overrides'))
                options['estimator'] = estimator          # the loader must translate onto the overridden requirements
            else:
                estimator = estimator_for(f"{attempt['target']}_model_parameters")
            data, evidence, policy = load_for(attempt['target'], store, options,
                                              function=(attempt.get('loader') or {}).get('function'))
            declared = protocol.get('vintage_policy')
            if declared not in (None, policy):
                run['vintage_policy_note'] = f'plan declares {declared}; loader supports {policy}'
            inputs = [dict(ref) for ref in evidence['inputs']]
            report = validate_process(estimator, data, train_end=protocol['train_end'], validation_end=protocol['validation_end'],
                                      cutoff=protocol['cutoff'], vintage_policy=declared or policy,
                                      window=protocol.get('window', 'expanding'), window_size=protocol.get('window_size'),
                                      refit_every=protocol.get('refit_every', 1), horizon=protocol.get('horizon', 1),
                                      data_inputs=inputs)
            artifact = None
            if publish:
                # The estimate is published first so the dataset pointer ends on a validation report.
                publish_estimate(store, report['final_estimate'], inputs=inputs, dataset=dataset,
                                 parameters={'attempt': label, 'process_id': report['process_id'], 'component': report['component'],
                                             'cutoff': report['final_estimate']['cutoff'],
                                             'vintage_policy': report['final_estimate']['vintage_policy'],
                                             'options': report['final_estimate'].get('options', {})})
                artifact = publish_validation_report(store, report, inputs=inputs, dataset=dataset,
                                                     parameters={'attempt': label, 'process_id': report['process_id'],
                                                                 'component': report['component'], 'protocol': report['protocol'],
                                                                 'evidence': {k: v for k, v in evidence.items() if k != 'record_ids'}})
            test = report['test']
            run.update({'status': 'ran', 'validated': report['validated'], 'report_id': report['report_id'], 'artifact': artifact,
                        'process_id': report['process_id'], 'component': report['component'],
                        'selection_hash': report['selection']['selection_hash'],
                        'evidence': {k: v for k, v in evidence.items() if k != 'record_ids'},
                        'sample': report['final_estimate']['sample'],
                        'parameters': report['final_estimate']['parameters'],
                        'standard_errors': report['final_estimate']['standard_errors'],
                        'metrics': {'model': test['metrics']['model'],
                                    'baselines': {k: {m: v.get(m) for m in ('count', 'mae', 'rmse', 'mse', 'crps')}
                                                  for k, v in test['metrics']['baselines'].items()}},
                        'diebold_mariano': {k: {'squared': v.get('squared', {}).get('pvalue'),
                                                'mean_loss_difference': v.get('squared', {}).get('mean_loss_difference')}
                                            for k, v in test['diebold_mariano'].items()},
                        'skipped_origins': len(test['skipped']), 'acceptance': _acceptance(report),
                        'calibration_record': calibration_record(report, report_ref=artifact)})
        except MissingData as error:
            run.update({'status': 'blocked_on_data', 'error': str(error)})
        except (ValueError, KeyError) as error:
            run.update({'status': 'failed', 'error': f'{type(error).__name__}: {error}'})
        runs.append(run)
    return runs


def _calibrate_all(args, store):
    from .process_library import default_registry
    from .estimation.loaders import availability
    plan = load_plan(getattr(args, 'plan', None))
    selected = set(args.attempt or [])
    attempts = [a for a in plan['attempts'] if not selected or a['id'] in selected]
    if selected - {a['id'] for a in plan['attempts']}:
        raise ValueError(f'Unknown attempt ids: {sorted(selected - {a["id"] for a in plan["attempts"]})}')
    registry, runs = default_registry(), []
    for attempt in attempts:
        runs.extend(_run_attempt(attempt, store, publish=not args.no_publish, dataset=args.dataset))
    processes = {}
    for run in runs:
        record = run.get('calibration_record')
        if record is None:
            continue
        try:
            registry.attach_calibration(record, process_id=record['process_id'])
        except ValueError as error:
            run['attach_error'] = str(error)
            continue
        state = registry.calibration(record['process_id'])
        processes[record['process_id']] = {'required_components': state['required_components'],
                                           'missing_components': state['missing_components'],
                                           'failing_components': state['failing_components'],
                                           'validated': bool(state['required_components']) and not state['missing_components']
                                           and not state['failing_components']}
    output = {'plan': {'schema': plan['schema'], 'declared_at': plan['declared_at'], 'attempts': [a['id'] for a in attempts],
                       'not_run': plan.get('not_run', [])},
              'results': [{k: v for k, v in run.items() if k != 'calibration_record'} for run in runs],
              'processes': processes, 'availability': availability()}
    if getattr(args, 'out', None):
        args.out.write_text(json.dumps(output, indent=2, sort_keys=True), encoding='utf-8')
    return output


def execute(args, catalog, store, project, reference):
    from .estimation import (ObservationSet, estimator_for, validate_process, publish_validation_report, publish_estimate,
                             calibration_record, requirements_summary, load_requirements, load_calibrations)
    from .process_library import default_registry
    if args.command == 'estimation-load':
        from .estimation.loaders import availability, load_for
        if not args.target:
            return availability()
        options = _options(args)
        data, evidence, policy = load_for(args.target, store, options, function=options.pop('function', None))
        if args.out:
            from .estimation.data import ObservationSet
            body = {'records': [{k: v for k, v in r.items() if k != '_input'} for r in data.records]} if isinstance(data, ObservationSet) else data
            args.out.write_text(json.dumps(body, indent=2, sort_keys=True), encoding='utf-8')
        return _loaded_summary(args.target, data, evidence, policy, limit=args.limit)
    if args.command == 'calibrate-all':
        return _calibrate_all(args, store)
    if args.command == 'estimation-requirements':
        return load_requirements() if args.full else requirements_summary(args.process)
    if args.command == 'calibration-status':
        registry = default_registry()
        records = load_calibrations(store, registry, [reference(value, store) for value in args.reports])
        processes = {}
        for record in records:
            calibration = registry.calibration(record['process_id'])
            processes[record['process_id']] = {'validated': not calibration['missing_components'] and not calibration['failing_components'] and bool(calibration['required_components']),
                                               'required_components': calibration['required_components'],
                                               'missing_components': calibration['missing_components'],
                                               'failing_components': calibration['failing_components']}
        return {'records': [{k: r[k] for k in ('process_id', 'component', 'validated', 'estimate_id', 'report_id', 'report_ref', 'process_parameters')}
                            for r in records], 'processes': processes}
    refs = [reference(value, store) for value in args.data]
    overrides, options = _overrides(args.series), _options(args)
    estimators = []
    for component in _components(args.process, args.component):
        spec = load_requirements()['components'][component]
        names = {s['name'] for s in spec['series']} | ({'cell'} if 'series_template' in spec else set())
        selected = {k: v for k, v in overrides.items() if k in names}
        estimators.append(estimator_for(component, overrides=selected or None, options=options))
    unused = set(overrides) - {r.name for e in estimators for r in e.all_requirements()} - {'cell'}
    if unused:
        raise ValueError(f'--series overrides match no requirement: {sorted(unused)}')
    metrics = {r.metric for e in estimators for r in e.all_requirements()}
    series_data = ObservationSet.from_store(store, refs, metrics=metrics) if refs else None
    native, raw_inputs = None, []
    if args.family_data:
        if args.family_data.stat().st_size > 64 * 1024 * 1024:
            raise ValueError('--family-data exceeds 64 MiB')
        native = read_json(args.family_data)
        if not args.no_publish:
            raw_inputs = [store.import_file('model_family_inputs', args.family_data, {'publisher': 'user-provided', 'role': 'model family data mapping'}, update_latest=False)]
    results, errors = [], []
    criteria = read_json(args.criteria) if getattr(args, 'criteria', None) else None
    for estimator in estimators:
        family = getattr(estimator, 'family', None)
        data = native if family else series_data
        if data is None:
            errors.append({'component': estimator.component, 'error': '--family-data is required' if family else '--data is required'})
            continue
        inputs = [] if family else refs
        try:
            if args.command == 'estimate':
                estimate = estimator.fit(data, cutoff=args.cutoff, vintage_policy=args.vintage_policy).to_dict()
                artifact = None if args.no_publish else publish_estimate(store, estimate, inputs=inputs, raw_inputs=raw_inputs if family else (), dataset=args.dataset)
                results.append({'component': estimator.component, 'artifact': artifact,
                                **{k: estimate[k] for k in ('process_id', 'estimate_id', 'method', 'parameters', 'standard_errors',
                                                            'process_parameters', 'sample', 'bounds_check', 'cutoff', 'vintage_policy')}})
            else:
                report = validate_process(estimator, data, train_end=args.train_end, validation_end=args.validation_end, cutoff=args.cutoff,
                                          criteria=criteria, horizon=args.horizon, window=args.window, window_size=args.window_size,
                                          refit_every=args.refit_every, vintage_policy=args.vintage_policy,
                                          interval_level=args.interval_level, season=args.season, data_inputs=inputs + list(raw_inputs if family else []))
                artifact = None if args.no_publish else publish_validation_report(store, report, inputs=inputs, raw_inputs=raw_inputs if family else (), dataset=args.dataset)
                record = calibration_record(report, report_ref=artifact)
                test = report['test']
                results.append({'component': estimator.component, 'artifact': artifact, 'report_id': report['report_id'],
                                'validated': report['validated'], 'selection_hash': report['selection']['selection_hash'],
                                'acceptance': [{k: r.get(k) for k in ('id', 'passed', 'observed', 'threshold', 'reason') if k in r}
                                               for r in report['acceptance']['results']],
                                'test_metrics': {'model': test['metrics']['model'], 'persistence': test['metrics']['baselines'].get('persistence')},
                                'diebold_mariano_vs_persistence': test['diebold_mariano'].get('persistence', {}).get('squared'),
                                'skipped_origins': len(test['skipped']), 'final_parameters': report['final_estimate']['parameters'],
                                'calibration_record': record})
        except ValueError as error:
            errors.append({'component': estimator.component, 'error': str(error)})
    if not results:
        raise ValueError('; '.join(f'{e["component"]}: {e["error"]}' for e in errors))
    output = {'process': args.process, 'cutoff': args.cutoff, 'vintage_policy': args.vintage_policy, 'data': refs,
              'results': results, 'errors': errors}
    if args.command == 'validate':
        registry = default_registry()
        process_id = results[0]['calibration_record']['process_id']
        for result in results:
            registry.attach_calibration(result['calibration_record'], process_id=process_id)
        state = registry.calibration(process_id)
        output['process_validation'] = {'process_id': process_id, 'validated': next(p['validated'] for p in registry.describe()['processes'] if p['id'] == process_id),
                                        'missing_components': state['missing_components'], 'failing_components': state['failing_components']}
    return output
