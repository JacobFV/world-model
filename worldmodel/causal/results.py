"""Result records for natural-experiment studies: identification labels, acceptance, verdicts."""
import hashlib
import json

RESULT_SCHEMA = 'worldmodel.causal_result/1'

# Identification labels. Only IDENTIFIED_DID asserts an intervention response, and only under the
# assumptions listed in the record. The repository's reduced-form labels (``correlational``,
# ``predictive_association``, ``descriptive_time_series``) keep their meaning.
IDENTIFIED_DID = 'quasi_experimental_did'
FAILED_DIAGNOSTICS = 'did_failed_diagnostics'
NOT_ESTIMABLE = 'not_estimable'
PREDICTIVE = 'predictive_association'
POWER = 'design_power_analysis'
LABELS = {
    IDENTIFIED_DID: 'Average effect on the treated identified by a staggered difference-in-differences design whose '
                    'pre-registered diagnostics (pre-trends, placebo dates, placebo units) all passed. Causal only '
                    'under the stated assumptions, for the treated population and horizon studied.',
    FAILED_DIAGNOSTICS: 'A difference-in-differences estimate whose pre-registered diagnostics failed. It is reported '
                        'for transparency and must not be read as an intervention response.',
    NOT_ESTIMABLE: 'The pre-registered design could not be estimated with the available data (too few treated '
                   'units, clusters or outcome periods); no estimate is reported as an effect.',
    PREDICTIVE: 'A predictive association scored on held-out events. It says nothing about intervention responses.',
    POWER: 'Operating characteristics of a registered design (size, diagnostic pass rates, minimum detectable effect) '
           'measured on synthetic panels with no effect; says nothing about any real effect.',
}

DID_ASSUMPTIONS = [
    'Parallel trends: absent treatment, the average outcome of treated units would have moved like that of the '
    'comparison units (not-yet-treated or never-treated, within stratum when stratified) from the base period on.',
    'No anticipation beyond the registered anticipation window: outcomes before g - anticipation do not respond.',
    'Absorbing treatment: the first qualifying event defines the cohort; later events are part of the post-period '
    'treatment path, so estimates are effects of entering treatment, not of one isolated event.',
    'SUTVA / no interference: a unit\'s outcome does not depend on other units\' treatment (spillovers to comparison '
    'units bias the estimate toward zero or away from it).',
    'Treatment timing is recorded correctly by the source date the registration names.',
]


def evaluate_acceptance(criteria, facts):
    """Evaluate declared criteria against measured facts; unknown or unmeasurable criteria fail."""
    results = []
    for item in criteria:
        kind, threshold = item['type'], item.get('value')
        value, passed, note = None, False, None
        if kind == 'min_treated_units':
            value = facts.get('treated_units'); passed = value is not None and value >= threshold
        elif kind == 'min_clusters':
            value = facts.get('clusters'); passed = value is not None and value >= threshold
        elif kind == 'min_events':
            value = facts.get('events'); passed = value is not None and value >= threshold
        elif kind == 'pre_trend_wald_p_min':
            value = facts.get('pre_trend_p'); passed = value is not None and value >= threshold
        elif kind == 'placebo_date_p_min':
            value = facts.get('placebo_date_p'); passed = value is not None and value >= threshold
        elif kind == 'placebo_unit_rejection_rate_max':
            value = facts.get('placebo_unit_rejection_rate'); passed = value is not None and value <= threshold
        elif kind == 'placebo_cluster_rejection_rate_max':
            value = facts.get('placebo_cluster_rejection_rate'); passed = value is not None and value <= threshold
        elif kind == 'metric_max':
            value = facts.get(item['metric']); passed = value is not None and value <= threshold
        elif kind == 'robustness_ci_overlap':
            primary, other = facts.get('primary_ci'), facts.get(item.get('against', 'stacked_ci'))
            if primary and other:
                value = [primary, other]
                passed = primary[0] <= other[1] and other[0] <= primary[1]
        elif kind == 'permutation_p_max':
            value = facts.get(item.get('metric', 'permutation_p')); passed = value is not None and value <= threshold
        elif kind == 'metric_min':
            value = facts.get(item['metric']); passed = value is not None and value >= threshold
        if value is None:
            note = 'not measured; an unmeasured criterion fails'
        results.append({'id': item['id'], 'type': kind, 'threshold': threshold, 'value': value, 'passed': bool(passed),
                        'role': item.get('role', 'identification'), **({'note': note} if note else {})})
    return results


def did_verdict(acceptance, overall, *, outcome_label, unit='log points'):
    """Plain-language verdict; the identification label follows only from the acceptance results."""
    if overall is None:
        return NOT_ESTIMABLE, 'Not estimable: the registered design produced no post-period estimate.'
    gating = [a for a in acceptance if a.get('role', 'identification') == 'identification']
    failed = [a['id'] for a in gating if not a['passed']]
    att, lo, hi = overall['att'], overall['ci_low'], overall['ci_high']
    span = f'{att:+.4f} {unit} (95% CI {lo:+.4f} to {hi:+.4f})'
    if any(a['type'] in ('min_treated_units', 'min_clusters', 'min_events') and not a['passed'] for a in gating):
        return NOT_ESTIMABLE, f'Not estimable as registered ({", ".join(failed)} failed). Point estimate {span} is not an effect.'
    if failed:
        return FAILED_DIAGNOSTICS, (f'No causal claim. Pre-registered diagnostics failed ({", ".join(failed)}), so the '
                                    f'parallel-trends design is not credible here. The estimate for {outcome_label}, '
                                    f'{span}, is reported only for transparency.')
    if lo > 0 or hi < 0:
        direction = 'increase' if att > 0 else 'decrease'
        return IDENTIFIED_DID, (f'Diagnostics passed. Under the stated assumptions treatment caused a {direction} in '
                                f'{outcome_label} of {span} averaged over the registered post window.')
    return IDENTIFIED_DID, (f'Diagnostics passed; the effect on {outcome_label} is a null: {span}. The design rules out '
                            f'average effects outside that interval under the stated assumptions.')


def result_id(record):
    body = {k: v for k, v in record.items() if k != 'result_id'}
    return hashlib.sha256(json.dumps(body, sort_keys=True, separators=(',', ':'), default=str).encode()).hexdigest()


def build_result(*, study_id, registration, registration_status, identification_label, verdict, estimates, diagnostics,
                 acceptance, data, assumptions, notes=(), does_not_establish=()):
    record = {
        'schema': RESULT_SCHEMA,
        'study_id': study_id,
        'registration': {'path': registration_status.get('path'), 'sha256': registration_status.get('sha256'),
                         'commit': registration_status.get('commit'),
                         'committed_at': registration_status.get('committed_at'),
                         'committed_clean': registration_status.get('committed_clean')},
        'identification': {'label': identification_label, 'meaning': LABELS.get(identification_label),
                           'strategy': registration.get('identification_strategy'),
                           'estimand': registration.get('estimator', {}).get('estimand'),
                           'assumptions': list(assumptions)},
        'design': {key: registration.get(key) for key in ('question', 'treatment', 'units', 'windows', 'controls',
                                                          'outcomes', 'estimator', 'inference', 'placebo_tests')},
        'estimates': estimates,
        'diagnostics': diagnostics,
        'acceptance': acceptance,
        'verdict': verdict,
        # Present only when a design declares limits of its own: a registration that says an estimand is
        # registered as a bound, not an effect, says so here in the result as well as in its design.
        **({'does_not_establish': list(does_not_establish)} if does_not_establish else {}),
        'data': data,
        'notes': list(notes),
    }
    record['result_id'] = result_id(record)
    return record
