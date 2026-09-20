"""Power for the **draft** wave-3 FEMA design, measured before anything is registered.

Wave 2's follow-up note says a wave 3 must start from the power suite: pick outcomes and horizons
whose minimum detectable effect is below the effect worth finding, or say in advance that the design
can only bound. This script measures that for the monthly within-declaration damage-dose design, the
same way ``run_power.py`` measures it for the committed designs - noise calibrated on **untreated
cells only**, the real analytic standard error, and null draws that carry no effect - and writes a
power table the draft registration then quotes.

It is deliberately *not* wired into ``run_power.py``'s registered ``designs`` list and it reads a
draft under ``drafts/``, not a registration: nothing here is committed as a design, and no study is
run. The one rule it enforces mechanically is that a design's power must be knowable without reading
its effect: :func:`worldmodel.causal.studies_wave3.horizon_standard_errors` returns standard errors
and never the point estimate, so no treated-versus-control contrast is computed, printed or stored at
any point below.

    WORLD_MODEL_DATA=/path/to/data python3 examples/natural-experiments/run_power_wave3_draft.py export --dir DIR
    python3 examples/natural-experiments/run_power_wave3_draft.py simulate --dir DIR --workers 10
    python3 examples/natural-experiments/run_power_wave3_draft.py assemble --dir DIR
"""
import argparse
import gzip
import json
import pickle
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from worldmodel.causal.power import calibrate, calibrate_variogram, panel_from_dict, panel_to_dict, summarize_draws  # noqa: E402
from worldmodel.causal.registration import load_registration  # noqa: E402
from worldmodel.causal.sources_wave2 import (extract_bea_county_population, extract_noaa_county_damage,  # noqa: E402
                                             load_bea_county_population, load_noaa_county_damage)
from worldmodel.causal.studies import pinned  # noqa: E402
from worldmodel.causal.studies_wave2 import fema_declarations  # noqa: E402
from worldmodel.causal.studies_wave3 import (assign_roles, decile_threshold, dose_distribution,  # noqa: E402
                                             horizon_standard_errors, load_monthly_laus, month_index, month_label,
                                             monthly_dose_panel, null_draw_horizons, outcome_series)

DRAFT = ROOT / 'examples/natural-experiments/drafts/fema_monthly_dose_county_employment.draft.json'
MODELS = {'registered': calibrate, 'amended_variogram': calibrate_variogram}


def _store():
    from worldmodel.resources import resource_roots
    from worldmodel.store import Store
    return Store(resource_roots()['data'])


def _draft():
    return json.loads(DRAFT.read_text())


def _laus(store, draft, cache_dir):
    """The monthly first-release county series, cached so repeated runs do not re-read 1.46M records."""
    ref = next(r for r in draft['data']['inputs'] if r['dataset'] == 'county_monthly_realtime_panel')
    path = Path(cache_dir) / f'laus_{ref["version"][:12]}.pickle'
    if path.exists():
        with open(path, 'rb') as handle:
            return pickle.load(handle), ref
    series = load_monthly_laus(store, ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'wb') as handle:
        pickle.dump(series, handle, protocol=4)
    return series, ref


def _units(store, draft, cache_dir):
    """In-scope (disaster, county) pairs with their damage per capita. Treatment side only."""
    reg, _ = load_registration(ROOT / 'examples/natural-experiments/registrations'
                               / f'{draft["relation_to_wave_2"]["wave_2_study_id"]}.json')
    treatment = dict(reg['treatment'])
    t = draft['treatment']
    treatment['incident_months'] = [month_index(t['estimated_cohort_months'][0]),
                                    month_index(t['estimated_cohort_months'][1])]
    treatment['quiet_months'] = t['quiet_months']
    declarations = fema_declarations(store, pinned(reg, 'event_library'))
    damage = load_noaa_county_damage(extract_noaa_county_damage(store, pinned(reg, 'noaa_storm_events'), cache_dir)['cache'])
    population = load_bea_county_population(
        extract_bea_county_population(store, pinned(reg, 'bea_national_regional'), cache_dir)['cache'])
    return dose_distribution(declarations, damage, population, treatment), reg


def _panels(draft, units, laus):
    """``{(dose, outcome): (panel, facts)}`` for every dose definition and outcome the draft measures."""
    t, w = draft['treatment'], draft['windows']
    calendar = [month_index(w['calendar_months'][0]), month_index(w['calendar_months'][1])]
    built = {}
    for dose in draft['power']['dose_variants']:
        threshold = (dose['threshold_usd_per_capita'] if dose.get('threshold_usd_per_capita') else
                     decile_threshold(units, quantile=dose['quantile'], over=dose['over']))
        roles = assign_roles(units, treated_min=threshold, control_max=t['control_max_usd_per_capita'])
        for outcome in draft['power']['outcome_variants']:
            series = {fips: outcome_series(months, outcome, calendar) for fips, months in laus.items()}
            matched, _, drop = monthly_dose_panel(units, series, calendar=calendar,
                                                  match_window=t['match_window'],
                                                  unit_window=(t['match_window'][0], w['e_max']))
            built[(dose['id'], outcome)] = (matched, {'dose_threshold_usd_per_capita': threshold, **roles, **drop})
    return built


def _spec(draft, panel):
    """The estimation spec the power run uses.

    It estimates the **long** lead window when the draft declares one, so that one simulation measures
    the size of every candidate pre-trend gating window at once. This does not change any post-period
    number: the overall estimand aggregates post cells only, and the analytic standard error is
    identical to twelve decimal places for e_min = -12, -6 and -3.
    """
    w, e = draft['windows'], draft['estimator']
    e_min = w.get('long_lead_window_non_gating', w['e_min'])
    return {'e_min': e_min, 'e_max': w['e_max'], 'anticipation': e['anticipation'],
            'control_group': draft['controls']['control_group'], 'alpha': draft['inference']['alpha'],
            'placebo_shift': draft['placebo_tests']['placebo_date']['shift'],
            'pre_trend_gates': draft['power']['pre_trend_gates'],
            'cohorts': sorted(panel.cohort_sizes())}


def cmd_export(args):
    draft = _draft()
    store = _store()
    cache = store.scratch_dir('natural_experiment_reports') / 'wave3'
    cache.mkdir(parents=True, exist_ok=True)
    laus, laus_ref = _laus(store, draft, cache)
    (units, facts), reg = _units(store, draft, cache)
    print(json.dumps({'laus_counties': len(laus), 'dose_pairs': len(units), **facts}), flush=True)
    horizons = draft['power']['horizons']
    primary = (draft['power']['primary_dose'], draft['outcomes']['primary']['id'])
    exported, table = {}, []
    for key, (panel, panel_facts) in _panels(draft, units, laus).items():
        started = time.time()
        # Standard errors only: the point estimate is never returned by horizon_standard_errors.
        ses = horizon_standard_errors(panel, _spec(draft, panel), horizons)
        row = {'dose': key[0], 'outcome': key[1], 'panel': panel.summary(), 'facts': panel_facts,
               'real_se': {str(h): ses[h] for h in horizons}, 'seconds': round(time.time() - started, 1)}
        table.append(row)
        print(json.dumps(row, default=str), flush=True)
        if key == primary:
            spec = _spec(draft, panel)
            exported['primary'] = {
                'dose': key[0], 'outcome': key[1], 'panel': panel_to_dict(panel), 'digest': panel.digest(),
                'spec': spec, 'horizons': horizons, 'real_se': {str(h): ses[h] for h in horizons},
                'calibration': {name: fn(panel, anticipation=spec['anticipation']) for name, fn in MODELS.items()}}
    if 'primary' not in exported:
        raise SystemExit('the draft names a primary dose and outcome that were not built')
    exported['table'] = table
    exported['inputs'] = reg['data']['inputs'] + [laus_ref]
    Path(args.dir).mkdir(parents=True, exist_ok=True)
    with gzip.open(Path(args.dir) / 'panels.json.gz', 'wt') as handle:
        json.dump(exported, handle, default=str)
    print(json.dumps({'calibration': {name: {k: v for k, v in c.items()
                                             if k not in ('unit_innovation_var', 'unit_scale', 'unit', 'cluster')}
                                      for name, c in exported['primary']['calibration'].items()}}, default=str))


_WORKER = {}


def _worker(seed):
    return null_draw_horizons(_WORKER['panel'], _WORKER['spec'], _WORKER['params'], seed, _WORKER['horizons'])


def cmd_simulate(args):
    draft = _draft()
    simulation = draft['power']['simulation']
    with gzip.open(Path(args.dir) / 'panels.json.gz', 'rt') as handle:
        exported = json.load(handle)
    item = exported['primary']
    panel = panel_from_dict(item['panel'])
    if panel.digest() != item['digest']:
        raise SystemExit('exported panel does not match its digest')
    out_path = Path(args.dir) / 'draws.json'
    done = json.loads(out_path.read_text()) if out_path.exists() else {}
    for offset, name in enumerate(MODELS):
        if name in done and done[name]['digest'] == item['digest']:
            continue
        seed = simulation['seed'] + 1000 * offset
        seeds = [seed * 100003 + r for r in range(simulation['replications'])]
        started = time.time()
        _WORKER.update(panel=panel, spec=item['spec'], params=item['calibration'][name], horizons=item['horizons'])
        try:
            if args.workers <= 1:
                draws = [_worker(s) for s in seeds]
            else:
                import multiprocessing
                with multiprocessing.get_context('fork').Pool(args.workers) as pool:
                    draws = pool.map(_worker, seeds, chunksize=1)
        finally:
            _WORKER.clear()
        done[name] = {'digest': item['digest'], 'seed': seed, 'replications': simulation['replications'],
                      'seconds': round(time.time() - started, 1), 'draws': draws}
        out_path.write_text(json.dumps(done))
        print(json.dumps({'model': name, 'seconds': done[name]['seconds']}), flush=True)


def cmd_assemble(args):
    draft = _draft()
    simulation = draft['power']['simulation']
    plausible = draft['power']['effect_worth_finding']['value']
    with gzip.open(Path(args.dir) / 'panels.json.gz', 'rt') as handle:
        exported = json.load(handle)
    draws = json.loads((Path(args.dir) / 'draws.json').read_text())
    item = exported['primary']
    out = {'schema': 'worldmodel.draft_power_table/1', 'study_id': draft['study_id'], 'status': 'draft, not registered',
           'primary': {'dose': item['dose'], 'outcome': item['outcome'], 'panel_digest': item['digest']},
           'effect_worth_finding': draft['power']['effect_worth_finding'],
           'inputs': exported['inputs'], 'standard_errors': exported['table'], 'horizons': {}}
    for model, record in draws.items():
        if record['digest'] != item['digest']:
            raise SystemExit(f'{model}: draws were produced on a different panel')
        if record['replications'] != simulation['replications']:
            raise SystemExit(f'{model}: draws were not produced with the drafted replication count')
    gates = [str(g) for g in item['spec']['pre_trend_gates']]
    for horizon in item['horizons']:
        real_se = item['real_se'][str(horizon)]
        per_model = {}
        for model, record in draws.items():
            flat = [{'att': d['horizons'][str(horizon)]['att'], 'se': d['horizons'][str(horizon)]['se'],
                     'pre_trend_p': d.get('pre_trend_p'), 'placebo_date_p': d.get('placebo_date_p')}
                    for d in record['draws']]
            summary = summarize_draws(flat, alpha=item['spec']['alpha'], power=simulation['power'],
                                      real_se=real_se, plausible=plausible)
            summary.pop('mde_80_simulated_increase', None)
            summary.pop('mde_80_simulated_decrease', None)
            # How often a correct, no-effect design survives each candidate pre-trend gating window.
            alpha = item['spec']['alpha']
            summary['pre_trend_pass_rate_by_gate'] = {}
            summary['both_diagnostics_pass_rate_by_gate'] = {}
            for gate in gates:
                ps = [(d['pre_trend_p_by_gate'].get(gate), d.get('placebo_date_p')) for d in record['draws']]
                usable = [(a, b) for a, b in ps if a is not None]
                summary['pre_trend_pass_rate_by_gate'][gate] = (
                    sum(1 for a, _ in usable if a >= alpha) / len(usable) if usable else None)
                both = [(a, b) for a, b in usable if b is not None]
                summary['both_diagnostics_pass_rate_by_gate'][gate] = (
                    sum(1 for a, b in both if a >= alpha and b >= alpha) / len(both) if both else None)
            per_model[model] = summary
        out['horizons'][str(horizon)] = per_model
    Path(args.dir).mkdir(parents=True, exist_ok=True)
    (Path(args.dir) / 'power_table.json').write_text(json.dumps(out, indent=1, sort_keys=True, default=str) + '\n')
    rows = []
    for horizon in item['horizons']:
        registered = out['horizons'][str(horizon)]['registered']
        rows.append({'horizon_months': horizon, 'real_se': registered['real_se'],
                     'mde_80': registered['mde_80'], 'mde_source': registered['mde_source'],
                     'mde_over_effect_worth_finding': registered['mde_over_plausible'],
                     'power_at_effect_worth_finding': registered['power_at_plausible'],
                     'null_rejection_rate': registered['null_rejection_rate'],
                     'both_diagnostics_pass_rate': registered['both_diagnostics_pass_rate'],
                     'pre_trend_pass_rate_by_gate': registered['pre_trend_pass_rate_by_gate'],
                     'both_diagnostics_pass_rate_by_gate': registered['both_diagnostics_pass_rate_by_gate'],
                     'se_ratio_simulated_to_real': registered['se_ratio_simulated_to_real'],
                     'calibration_ok': registered['calibration_ok']})
    print(json.dumps({'effect_worth_finding': plausible, 'rows': rows}, indent=1, default=str))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('export', 'simulate', 'assemble'):
        p = sub.add_parser(name)
        p.add_argument('--dir', required=True)
        if name == 'simulate':
            p.add_argument('--workers', type=int, default=1)
    args = parser.parse_args(argv)
    {'export': cmd_export, 'simulate': cmd_simulate, 'assemble': cmd_assemble}[args.command](args)


if __name__ == '__main__':
    main()
