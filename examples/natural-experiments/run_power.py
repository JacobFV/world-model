"""Negative controls and power for every registered natural-experiment design (study design_power_negative_controls).

Three steps, so the simulation can run on a separate host from the exported panels:

    WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_power.py export --dir DIR
    python3 examples/natural-experiments/run_power.py simulate --dir DIR --workers 12
    WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_power.py assemble --dir DIR

``export`` rebuilds each covered design's real primary-outcome panel from the pinned inputs, calibrates the
noise on its untreated cells and computes the real analytic standard error. ``simulate`` produces the null
draws (deterministic given panel, calibration and seed). ``assemble`` rebuilds the panels again, refuses to
continue unless digests and calibration match the exported ones, summarises, writes
``results/design_power_negative_controls.json`` and publishes to ``natural_experiment_reports``. The power
registration must be committed and unmodified.
"""
import argparse
import gzip
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from worldmodel.causal.power import calibrate, null_draws, panel_from_dict, panel_to_dict, summarize_draws  # noqa: E402
from worldmodel.causal.power_designs import (BUILDERS, _with_cohorts, design_spec, exposure_power, power_record,  # noqa: E402
                                             real_standard_error)
from worldmodel.causal.registration import load_registration  # noqa: E402

REGISTRATIONS = ROOT / 'examples/natural-experiments/registrations'
RESULTS = ROOT / 'examples/natural-experiments/results'
STUDY = 'design_power_negative_controls'


def _store():
    from worldmodel.resources import resource_roots
    from worldmodel.store import Store
    return Store(resource_roots()['data'])


def _registration():
    return load_registration(REGISTRATIONS / f'{STUDY}.json')


def _panels(power_reg, store, cache):
    out = {}
    for cfg in power_reg['designs']:
        if cfg.get('kind') != 'did':
            continue
        reg, reg_status = load_registration(REGISTRATIONS / f'{cfg["study_id"]}.json')
        started = time.time()
        panel = BUILDERS[cfg['study_id']](reg, store, cache)
        spec = _with_cohorts(design_spec(reg), panel)
        out[cfg['study_id']] = {'panel': panel, 'spec': spec, 'digest': panel.digest(), 'registration': reg_status,
                                'inputs': reg['data']['inputs'], 'seconds': round(time.time() - started, 1)}
        print(json.dumps({'design': cfg['study_id'], 'digest': out[cfg['study_id']]['digest'], **panel.summary()},
                         default=str), flush=True)
    return out


def cmd_export(args):
    power_reg, _ = _registration()
    store = _store()
    cache = store.scratch_dir('natural_experiment_reports') / 'panels'
    exported = {}
    for sid, item in _panels(power_reg, store, cache).items():
        expected = next(c for c in power_reg['designs'] if c['study_id'] == sid).get('expected_panel_digest')
        if expected and expected != item['digest']:
            raise SystemExit(f'{sid}: panel digest {item["digest"]} differs from the registered {expected}')
        params = calibrate(item['panel'], anticipation=item['spec']['anticipation'])
        exported[sid] = {'panel': panel_to_dict(item['panel']), 'digest': item['digest'], 'spec': item['spec'],
                         'calibration': params, 'real_se': real_standard_error(item['panel'], item['spec'])}
        print(json.dumps({'design': sid, 'real_se': exported[sid]['real_se'],
                          'calibration': {k: v for k, v in params.items() if k != 'unit_innovation_var'}}), flush=True)
    Path(args.dir).mkdir(parents=True, exist_ok=True)
    with gzip.open(Path(args.dir) / 'panels.json.gz', 'wt') as f:
        json.dump(exported, f)


def cmd_simulate(args):
    reg = json.loads((REGISTRATIONS / f'{STUDY}.json').read_text())
    sim = reg['simulation']
    with gzip.open(Path(args.dir) / 'panels.json.gz', 'rt') as f:
        exported = json.load(f)
    out_path = Path(args.dir) / 'draws.json'
    done = json.loads(out_path.read_text()) if out_path.exists() else {}
    for index, cfg in enumerate(reg['designs']):
        sid = cfg['study_id']
        if sid not in exported or (sid in done and done[sid]['digest'] == exported[sid]['digest']):
            continue
        item = exported[sid]
        panel = panel_from_dict(item['panel'])
        if panel.digest() != item['digest']:
            raise SystemExit(f'{sid}: exported panel does not match its digest')
        started = time.time()
        draws = null_draws(panel, item['spec'], item['calibration'], replications=sim['replications'],
                           seed=sim['seed'] + index, workers=args.workers)
        done[sid] = {'digest': item['digest'], 'seed': sim['seed'] + index, 'replications': sim['replications'],
                     'seconds': round(time.time() - started, 1), 'draws': draws}
        out_path.write_text(json.dumps(done))
        print(json.dumps({'design': sid, 'seconds': done[sid]['seconds']}), flush=True)


def cmd_assemble(args):
    from worldmodel.artifacts import publish_report
    power_reg, status = _registration()
    store = _store()
    cache = store.scratch_dir('natural_experiment_reports') / 'panels'
    with gzip.open(Path(args.dir) / 'panels.json.gz', 'rt') as f:
        exported = json.load(f)
    draws = json.loads((Path(args.dir) / 'draws.json').read_text())
    started = time.time()
    built = _panels(power_reg, store, cache)
    results, inputs = [], []
    sim = power_reg['simulation']
    for index, cfg in enumerate(power_reg['designs']):
        sid = cfg['study_id']
        if cfg['kind'] == 'ranking':
            results.append(exposure_power(power_reg, status, store, cfg))
            inputs.append(cfg['report'])
            continue
        if cfg['kind'] == 'not_applicable':
            results.append(power_record(power_registration=power_reg, status=status, design=sid,
                                        summary={'reason': cfg['reason']}, calibration=None, panel_summary=None,
                                        panel_digest=None, data={'inputs': []}, notes=[cfg['reason']]))
            continue
        item = built[sid]
        if not (item['digest'] == exported[sid]['digest'] == draws[sid]['digest']):
            raise SystemExit(f'{sid}: rebuilt panel, exported panel and draws disagree')
        params = calibrate(item['panel'], anticipation=item['spec']['anticipation'])
        if json.dumps(params, sort_keys=True) != json.dumps(exported[sid]['calibration'], sort_keys=True):
            raise SystemExit(f'{sid}: calibration differs from the exported one')
        if draws[sid]['seed'] != sim['seed'] + index or draws[sid]['replications'] != sim['replications']:
            raise SystemExit(f'{sid}: draws were not produced with the registered seed and replications')
        real_se = real_standard_error(item['panel'], item['spec'])
        summary = summarize_draws(draws[sid]['draws'], alpha=item['spec']['alpha'], power=sim['power'], real_se=real_se,
                                  plausible=cfg['plausible_effect'])
        summary['plausible_effect_rationale'] = cfg['plausible_rationale']
        summary['null_draws'] = [{k: d.get(k) for k in ('att', 'se', 'pre_trend_p', 'placebo_date_p')}
                                 for d in draws[sid]['draws']]
        for ref in item['inputs']:
            if ref not in inputs:
                inputs.append(ref)
        results.append(power_record(power_registration=power_reg, status=status, design=sid, summary=summary,
                                    calibration=params, panel_summary=item['panel'].summary(), panel_digest=item['digest'],
                                    data={'inputs': item['inputs'], 'design_registration': item['registration'],
                                          'design_spec': item['spec'], 'simulation_seconds': draws[sid]['seconds']}))
    report = {'schema': 'worldmodel.natural_experiment_report/1', 'study_id': STUDY, 'registration': status,
              'results': results, 'seconds': round(time.time() - started, 1)}
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f'{STUDY}.json').write_text(json.dumps(report, indent=1, sort_keys=True, default=str) + '\n')
    published = None
    if not args.no_publish:
        published = publish_report(store, 'natural_experiment_reports', json.loads(json.dumps(report, default=str)),
                                   {'study_id': STUDY, 'registration_sha256': status['sha256'],
                                    'registration_commit': status['commit']},
                                   inputs=inputs, entrypoint='worldmodel/causal/power_designs.py')
    print(json.dumps({'study': STUDY, 'published': published,
                      'results': [{'design': r['estimates']['design'], 'verdict': r['verdict'],
                                   'acceptance': {a['id']: a['passed'] for a in r['acceptance']}} for r in results]},
                     indent=1, default=str))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('export')
    p.add_argument('--dir', required=True)
    p = sub.add_parser('simulate')
    p.add_argument('--dir', required=True)
    p.add_argument('--workers', type=int, default=1)
    p = sub.add_parser('assemble')
    p.add_argument('--dir', required=True)
    p.add_argument('--no-publish', action='store_true')
    args = parser.parse_args(argv)
    {'export': cmd_export, 'simulate': cmd_simulate, 'assemble': cmd_assemble}[args.command](args)


if __name__ == '__main__':
    main()
