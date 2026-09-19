"""The registered power and negative-control study: real panels of every covered design, and result records.

``BUILDERS`` rebuild each covered design's primary-outcome panel exactly as its own runner does (the
runner checks the digest against the one the design's published result recorded), and ``design_spec``
reads the design's estimation spec (windows, anticipation, comparison group, estimated cohorts, placebo
shift) from its registration. ``power_record`` turns calibration, null draws and the real standard error
into one result record per design. Simulation (``power.null_draws``) is deterministic given the panel, calibration and seed, so it can
run on another host from the exported panels.
"""
import json
import math

from .did import event_study
from .panel import Panel
from .power import calibrate, calibration_summary, ranking_test_power, summarize_draws
from .results import POWER, build_result, evaluate_acceptance
from .sources import extract_baci_imports, extract_bls_county, load_baci_imports, load_bls_county
from .sources_wave2 import (extract_baci92_imports, extract_baci92_partner_share, extract_bea_county_population,
                            extract_noaa_county_damage, load_baci92_partner_share, load_bea_county_population,
                            load_noaa_county_damage)
from .studies import fema_cohorts, fema_panel, pinned, tariff_cohorts, tariff_universe
from .studies_wave2 import (fema_declarations, fema_dose_panels, fema_dose_units, first_mfn_changes, mfn_series_hs17,
                            one_to_one, partner_share_panel, programme_waves, tariff_decrease_cohorts,
                            tariff_decrease_panels)


def design_spec(registration):
    windows, estimator = registration['windows'], registration['estimator']
    placebo = registration['placebo_tests'].get('placebo_date', {})
    span = registration['treatment'].get('estimated_cohorts')
    return {'e_min': windows['e_min'], 'e_max': windows['e_max'], 'post': windows.get('post'),
            'anticipation': estimator.get('anticipation', 0), 'control_group': registration['controls']['control_group'],
            'alpha': registration['inference']['alpha'], 'estimated_cohorts': span, 'placebo_shift': placebo.get('shift')}


def _with_cohorts(spec, panel):
    span = spec['estimated_cohorts']
    cohorts = None if not span else [g for g in panel.cohort_sizes() if span[0] <= g <= span[1]]
    return {**spec, 'cohorts': cohorts}


# -- panel builders (primary outcome of each design) ----------------------------------------------------

def panel_fema_first_disaster(reg, store, cache_dir):
    first, early, _ = fema_cohorts(store, pinned(reg, 'event_library'), reg)
    bls = extract_bls_county(store, pinned(reg, 'bls_labor'), cache_dir)
    lo, hi = reg['windows']['calendar_years']
    return fema_panel(first, early, load_bls_county(bls['cache'], 'QCEW', 'employment'), lo, hi)


def panel_tariff_increases(reg, store, cache_dir):
    """The wave-1 tariff panel for log import value, built as ``studies.run_tariff`` builds it."""
    universe = tariff_universe(store, pinned(reg, 'wits_trains_tariffs'))
    cohorts, excluded = tariff_cohorts(store, pinned(reg, 'event_library'), min_increase=2.0)
    importers = sorted({u[0] for u in universe})
    flows = load_baci_imports(extract_baci_imports(store, pinned(reg, 'cepii_baci'), cache_dir, importers)['cache'])
    lo, hi = reg['windows']['calendar_years']
    values, cohort_map, strata, clusters = {}, {}, {}, {}
    for unit in sorted(universe):
        if unit in excluded:
            continue
        base = [flows.get((unit[0], unit[1], y)) for y in (2017, 2018)]
        if any(b is None or b[0] <= 0 for b in base):
            continue
        series = {}
        for year in range(lo, hi + 1):
            f = flows.get((unit[0], unit[1], year))
            if f is not None and f[0] > 0:
                series[year] = math.log(f[0])
        if not series:
            continue
        key = f'{unit[0]}|{unit[1]}'
        values[key], cohort_map[key] = series, cohorts.get(unit)
        strata[key], clusters[key] = unit[0], f'{unit[0]}|{unit[1][:2]}'
    return Panel(values, cohort_map, strata=strata, clusters=clusters)


def panel_fema_dose(reg, store, cache_dir):
    t = reg['treatment']
    units, _ = fema_dose_units(
        fema_declarations(store, pinned(reg, 'event_library')),
        load_noaa_county_damage(extract_noaa_county_damage(store, pinned(reg, 'noaa_storm_events'), cache_dir)['cache']),
        load_bea_county_population(extract_bea_county_population(store, pinned(reg, 'bea_national_regional'), cache_dir)['cache']),
        t)
    bls = extract_bls_county(store, pinned(reg, 'bls_labor'), cache_dir)
    matched, _, _ = fema_dose_panels(units, load_bls_county(bls['cache'], 'QCEW', 'employment'),
                                     calendar=reg['windows']['calendar_years'], match_window=t['match_window'])
    return matched


def panel_tariff_decreases(reg, store, cache_dir):
    t = reg['treatment']
    conc = pinned(reg, 'trade_concordances')
    hs22_to_hs17 = one_to_one(store, conc, t['hs2022_to_hs2017_crosswalk'])
    hs17_to_hs92 = one_to_one(store, conc, reg['outcomes']['hs2017_to_hs1992_crosswalk'])
    series, universe = mfn_series_hs17(store, pinned(reg, 'wits_trains_tariffs'), hs22_to_hs17)
    cohorts, excluded, _ = tariff_decrease_cohorts(first_mfn_changes(series, universe, min_change=t['min_change_pp']),
                                                   max_decrease=t['max_decrease_pp'])
    lo, hi = reg['windows']['calendar_years']
    importers = sorted({u[0] for u in universe})
    flows = load_baci_imports(extract_baci92_imports(store, pinned(reg, 'cepii_baci_hs92'), cache_dir, importers,
                                                     years=[lo, hi])['cache'])
    present = {k[0] for k in flows}
    universe = {u for u in universe if u[0] in present}
    matched, _, _ = tariff_decrease_panels(universe, cohorts, excluded, hs17_to_hs92, flows,
                                           outcome_id=reg['outcomes']['primary']['id'], calendar=(lo, hi),
                                           positive_years=reg['units']['positive_import_years'],
                                           growth_window=reg['units']['growth_window'])
    return matched


def panel_sanctions_trade(reg, store, cache_dir):
    t = reg['treatment']
    first, _ = programme_waves(store, pinned(reg, 'event_library'), t['programme_targets'],
                               min_designations=t['min_designations_per_year'])
    cache = extract_baci92_partner_share(store, pinned(reg, 'cepii_baci_hs92'), cache_dir, partner='iso3:USA')
    panel, _ = partner_share_panel(load_baci92_partner_share(cache['cache'], 'exports'), first,
                                   estimated=t['estimated_cohorts'], calendar=reg['windows']['calendar_years'])
    return panel


BUILDERS = {'fema_disasters_county_employment': panel_fema_first_disaster,
            'tariff_mfn_increases_imports': panel_tariff_increases,
            'fema_damage_dose_county_employment': panel_fema_dose,
            'tariff_mfn_decreases_imports': panel_tariff_decreases,
            'sanctions_programme_waves_us_trade': panel_sanctions_trade}


def real_standard_error(panel, spec):
    """Analytic clustered SE of the registered overall ATT on the real panel (bootstrap off)."""
    s = _with_cohorts(spec, panel)
    res = event_study(panel, e_min=s['e_min'], e_max=s['e_max'], post=s['post'], control_group=s['control_group'],
                      anticipation=s['anticipation'], alpha=s['alpha'], bootstrap=0, cohorts=s['cohorts'])
    return res['overall']['se'] if res['overall'] else None


# -- result records -------------------------------------------------------------------------------------

def _verdict(design, summary, criteria_results):
    if summary.get('reason'):
        return f'{design}: {summary["reason"]}'
    mde, plaus = summary.get('mde_80'), summary.get('plausible_effect')
    powered = next((a['passed'] for a in criteria_results if a['id'] == 'detects_plausible_effect'), False)
    size_ok = next((a['passed'] for a in criteria_results if a['id'] == 'negative_control_size'), False)
    parts = []
    if mde is None:
        parts.append(f'{design}: no minimum detectable effect could be computed.')
    else:
        parts.append(f'{design}: minimum detectable effect at 80% power {mde:.4f} ({summary.get("mde_source")}) against a '
                     f'registered plausible effect of {plaus}; ' + ('the design can detect a plausible effect.' if powered
                                                                   else 'the design CANNOT detect a plausible effect '
                                                                        '(underpowered).'))
    rate = summary.get('null_rejection_rate')
    if rate is not None:
        parts.append(f'With no effect and parallel trends, the test rejects in {rate:.1%} of synthetic panels'
                     + (' (size acceptable).' if size_ok else ' (over-rejects: its stated confidence is too high).'))
    if summary.get('both_diagnostics_pass_rate') is not None:
        parts.append(f'A correct design passes both the pre-trend and placebo-date checks in '
                     f'{summary["both_diagnostics_pass_rate"]:.0%} of null panels.')
    return ' '.join(parts)


def power_record(*, power_registration, status, design, summary, calibration, panel_summary, panel_digest, data, notes=()):
    facts = {'mde_over_plausible': summary.get('mde_over_plausible'), 'null_rejection_rate': summary.get('null_rejection_rate')}
    acceptance = evaluate_acceptance(power_registration['acceptance_criteria'], facts)
    reg = {**power_registration, 'study_id': power_registration['study_id']}
    return build_result(study_id=power_registration['study_id'], registration=reg, registration_status=status,
                        identification_label=POWER, verdict=_verdict(design, summary, acceptance),
                        estimates={'design': design, 'power': summary},
                        diagnostics={'calibration': calibration_summary(calibration) if calibration else None},
                        acceptance=acceptance,
                        data={**data, 'panel': panel_summary, 'panel_digest': panel_digest},
                        assumptions=power_registration['assumptions'], notes=notes)


def exposure_power(power_registration, status, store, design_cfg):
    ref = design_cfg['report']
    report = json.loads((store.version_dir(ref) / 'report.json').read_text())
    held = report['results'][0]['estimates']['held_out_events']
    diffs = [e['exposure']['spearman'] - e['naive']['spearman'] for e in held
             if e['exposure']['spearman'] is not None and e['naive']['spearman'] is not None]
    cfg = power_registration['simulation']
    summary = ranking_test_power(diffs, plausible=design_cfg['plausible_effect'], draws=cfg['ranking_draws'],
                                 seed=cfg['seed'])
    summary['mde_source'] = 'resampled per-storm differences'
    return power_record(power_registration=power_registration, status=status, design=design_cfg['study_id'],
                        summary=summary, calibration=None, panel_summary={'events': len(diffs)}, panel_digest=None,
                        data={'inputs': [ref]})
