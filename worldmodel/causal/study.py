"""Run a registered staggered difference-in-differences design on a panel."""
from .did import event_study, stacked_did, twfe_static
from .placebo import placebo_cluster_test, placebo_date_test, placebo_unit_test
from .results import DID_ASSUMPTIONS, build_result, did_verdict, evaluate_acceptance


def _strip(result):
    return {k: v for k, v in result.items() if k != 'att_gt'} if result else result


def run_did_design(panel, registration, status, *, outcome, data=None, notes=(), include_att_gt=False,
                   extra_robustness=None):
    """Estimate, diagnose and judge one outcome exactly as the registration specifies.

    ``outcome`` is ``{'id', 'label', 'unit'}`` naming the registered outcome being analysed.
    ``extra_robustness`` maps a name to an alternative panel (e.g. without matching strata); each is
    estimated with the primary estimator and reported, never used by the acceptance criteria.
    Returns a result record (``worldmodel.causal_result/1``).
    """
    windows, estimator = registration['windows'], registration['estimator']
    inference, placebo = registration['inference'], registration['placebo_tests']
    controls = registration['controls']
    anticipation = estimator.get('anticipation', 0)
    common = dict(control_group=controls['control_group'], anticipation=anticipation, alpha=inference['alpha'])
    post = windows.get('post')
    span = registration['treatment'].get('estimated_cohorts')
    cohorts = None if not span else [g for g in panel.cohort_sizes() if span[0] <= g <= span[1]]
    balance = tuple(estimator['balance']) if estimator.get('balance') else None
    primary = event_study(panel, e_min=windows['e_min'], e_max=windows['e_max'], post=post, balance=balance,
                          bootstrap=inference.get('bootstrap', 999), seed=inference.get('seed', 0), cohorts=cohorts,
                          **common)
    robustness = {}
    for name in estimator.get('robustness', []):
        if name == 'stacked_did':
            robustness[name] = stacked_did(panel, e_min=windows['e_min'], e_max=windows['e_max'], post=post,
                                           anticipation=anticipation, alpha=inference['alpha'], cohorts=cohorts)
        elif name == 'twfe_static':
            robustness[name] = twfe_static(panel)
        elif name == 'never_treated_controls':
            alt = dict(common, control_group='never_treated')
            robustness[name] = _strip(event_study(panel, e_min=windows['e_min'], e_max=windows['e_max'], post=post,
                                                  balance=balance, bootstrap=0, cohorts=cohorts, **alt))
        else:
            raise ValueError(f'unknown robustness estimator {name}')
    for name, alt_panel in (extra_robustness or {}).items():
        robustness[name] = _strip(event_study(alt_panel, e_min=windows['e_min'], e_max=windows['e_max'], post=post,
                                              balance=balance, bootstrap=0, cohorts=cohorts, **common))
    diagnostics = {'pre_trend': primary['pre_trend']}
    if 'placebo_date' in placebo:
        cfg = placebo['placebo_date']
        diagnostics['placebo_date'] = placebo_date_test(panel, shift=cfg['shift'], cohorts=cohorts, **common)
    if 'placebo_unit' in placebo:
        cfg = placebo['placebo_unit']
        observed = primary['overall']['att'] if primary['overall'] else None
        diagnostics['placebo_unit'] = placebo_unit_test(panel, replications=cfg['replications'], seed=cfg['seed'],
                                                        e_min=windows['e_min'], e_max=windows['e_max'], post=post,
                                                        observed=observed, cohorts=cohorts,
                                                        min_never_treated=cfg.get('min_never_treated', 20), **common)
    if 'placebo_cluster' in placebo:
        cfg = placebo['placebo_cluster']
        observed = primary['overall']['att'] if primary['overall'] else None
        diagnostics['placebo_cluster'] = placebo_cluster_test(
            panel, replications=cfg['replications'], seed=cfg['seed'], e_min=windows['e_min'], e_max=windows['e_max'],
            post=post, observed=observed, cohorts=cohorts,
            min_never_treated_clusters=cfg.get('min_never_treated_clusters', 10), **common)
    overall = primary['overall']
    estimated = [u for u in panel.treated_units() if cohorts is None or panel.cohorts[u] in set(cohorts)]
    facts = {'treated_units': len(estimated), 'clusters': panel.n_clusters(),
             'pre_trend_p': primary['pre_trend']['wald']['p'],
             'placebo_date_p': diagnostics.get('placebo_date', {}).get('p'),
             'placebo_unit_rejection_rate': diagnostics.get('placebo_unit', {}).get('rejection_rate'),
             'placebo_cluster_rejection_rate': diagnostics.get('placebo_cluster', {}).get('rejection_rate'),
             'events': len({panel.clusters[u] for u in estimated}),
             'primary_ci': [overall['ci_low'], overall['ci_high']] if overall else None}
    stacked = robustness.get('stacked_did', {}).get('overall') if robustness.get('stacked_did') else None
    if stacked:
        facts['stacked_ci'] = [stacked['ci_low'], stacked['ci_high']]
    acceptance = evaluate_acceptance(registration['acceptance_criteria'], facts)
    label, verdict = did_verdict(acceptance, overall, outcome_label=outcome['label'], unit=outcome.get('unit', 'log points'))
    estimates = {'outcome': outcome, 'primary': primary if include_att_gt else _strip(primary), 'robustness': robustness}
    assumptions = list(DID_ASSUMPTIONS) + list(registration.get('assumptions', []))
    return build_result(study_id=registration['study_id'], registration=registration, registration_status=status,
                        identification_label=label, verdict=verdict, estimates=estimates, diagnostics=diagnostics,
                        acceptance=acceptance, data={**(data or {}), 'panel': panel.summary(), 'panel_digest': panel.digest()},
                        assumptions=assumptions, notes=notes)
