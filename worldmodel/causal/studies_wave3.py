"""Wave-3 panel construction: the FEMA damage dose against a **monthly** county outcome.

Wave 2's within-declaration dose contrast used QCEW annual county employment, and the power suite
found it could not have detected the 2% effect its own registration called plausible
(MDE 0.033 against a plausible 0.02). The documented follow-up names the two fixes: a monthly
outcome, and a larger dose contrast. This module builds the panel for both.

Two parts, and the order between them is the discipline:

* **Panel construction and power** (everything up to :func:`null_draw_horizons`) reads no
  treated-versus-control contrast at all. :func:`horizon_standard_errors` returns standard errors and
  discards the point estimate, so a design's power can be measured before it is registered without
  burning the registration.
* **The registered study** (:func:`run_fema_monthly_dose`) estimates the design once it *is*
  registered. ``run_studies.py`` refuses to run a registration that is not committed and unmodified,
  so nothing here can read a contrast before the design that binds it is in the history.

The outcome comes from ``county_monthly_realtime_panel``: for each county and reference month, the
LAUS unemployment rate and civilian labour force as **first published**, dated by the vintage that
published them. Three outcome series are derived from those two features:

``laus_log_employment``
    ``log(labour force x (1 - rate / 100))``. LAUS publishes employment as an identity on the two
    series in this archive, and the monthly county *employed* series with a deep ALFRED archive does
    not exist (the structured ``LAUCN...`` form opens in 2019). This is the monthly analogue of the
    wave-2 primary outcome, so the same plausible effect applies to it. A month enters only when the
    county has a first release of **both** features for it, and the month's availability is the later
    of the two vintages.
``laus_log_labor_force``
    ``log(labour force)``: the same denominator without the rate, so a reader can see how much of
    the employment series' behaviour is participation rather than unemployment.
``laus_unemployment_rate``
    the rate itself, in percentage points, not logged.

Periods are months, indexed ``year * 12 + (month - 1)``, so a lag of one is one month.
"""
import bisect
import math

from .did import _variance, aggregate_event_time, att_gt, event_study, overall_from_event_time, wald_test
from .panel import Panel
from .placebo import placebo_date_test
from .power import simulate_null_panel
from .sources_wave2 import (extract_bea_county_population, extract_noaa_county_damage, load_bea_county_population,
                            load_noaa_county_damage)
from .studies import _prov, pinned
from .studies_wave2 import fema_declarations, split_by_rank
from .study import run_did_design

#: The two features the monthly first-release county panel publishes.
RATE = 'rt:laus_monthly_unemployment_rate'
LABOR_FORCE = 'rt:laus_monthly_labor_force'
OUTCOMES = ('laus_log_employment', 'laus_log_labor_force', 'laus_unemployment_rate')


def month_index(day):
    """``YYYY-MM...`` -> ``year * 12 + (month - 1)``, an integer period in which a lag of 1 is a month."""
    text = str(day)
    return int(text[:4]) * 12 + int(text[5:7]) - 1


def month_label(index):
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def load_monthly_laus(store, panel_ref):
    """``{fips: {month: (rate, labour force, available_at)}}`` from ``county_monthly_realtime_panel``.

    Only months with a first release of both features are kept, and ``available_at`` is the later of
    the two vintages: the day the pair was complete.
    """
    parts = {}
    for record in store.records(panel_ref):
        if record.get('kind') != 'observation' or record.get('value') is None:
            continue
        feature = record.get('metric')
        if feature not in (RATE, LABOR_FORCE):
            continue
        fips = str(record['subject']).rsplit(':', 1)[1]
        month = month_index(record['valid_from'])
        available = (record.get('dimensions') or {}).get('available_at')
        parts.setdefault(fips, {}).setdefault(month, {})[feature] = (float(record['value']), available)
    out = {}
    for fips, months in parts.items():
        kept = {}
        for month, pair in months.items():
            if RATE in pair and LABOR_FORCE in pair:
                kept[month] = (pair[RATE][0], pair[LABOR_FORCE][0],
                               max(pair[RATE][1] or '', pair[LABOR_FORCE][1] or ''))
        if kept:
            out[fips] = kept
    return out


def outcome_series(months, outcome_id, calendar):
    """``{month: value}`` for one county and one of :data:`OUTCOMES`, inside ``calendar``."""
    lo, hi = calendar
    out = {}
    for month, (rate, labor_force, _) in months.items():
        if not lo <= month <= hi:
            continue
        if outcome_id == 'laus_unemployment_rate':
            out[month] = rate
            continue
        employed = labor_force * (1.0 - rate / 100.0) if outcome_id == 'laus_log_employment' else labor_force
        if employed > 0:
            out[month] = math.log(employed)
    return out


def dose_distribution(declarations, damage, population, treatment):
    """Every in-scope ``(disaster, county)`` pair's damage per capita, before any threshold is applied.

    Treatment-side only: NOAA damage during the incident period over BEA population in the year before.
    Returns ``(units, facts)`` with ``role`` still unset; :func:`assign_roles` sets it.
    """
    dose_types = set(treatment['dose_incident_types'])
    quiet_types = set(treatment['quiet_period_incident_types'])
    lo, hi = treatment['incident_months']
    quiet = treatment['quiet_months']
    history = {}
    for disaster, value in declarations.items():
        if value['type'] in quiet_types:
            for fips in value['counties']:
                history.setdefault(fips, []).append((month_index(value['begin']), disaster))
    facts = {'pairs_in_scope': 0, 'excluded_recent_other_disaster': 0, 'excluded_no_population': 0}
    units = []
    for disaster in sorted(declarations):
        value = declarations[disaster]
        if value['type'] not in dose_types:
            continue
        g = month_index(value['begin'])
        if not lo <= g <= hi:
            continue
        for fips in sorted(value['counties']):
            facts['pairs_in_scope'] += 1
            if any(other != disaster and g - quiet <= month <= g - 1 for month, other in history.get(fips, [])):
                facts['excluded_recent_other_disaster'] += 1
                continue
            pop = population.get(fips, {}).get(g // 12 - 1)
            if not pop:
                facts['excluded_no_population'] += 1
                continue
            rows = damage.get(fips, [])
            i = bisect.bisect_left(rows, (value['begin'],))
            total = 0.0
            while i < len(rows) and rows[i][0] <= value['end']:
                total += rows[i][1]
                i += 1
            units.append({'unit': f'{disaster}|{fips}', 'disaster': disaster, 'fips': fips, 'g': g,
                          'dose': total / pop, 'role': None})
    return units, facts


def decile_threshold(units, *, quantile, over):
    """The dose at ``quantile`` of the reference set ``over`` (``'damaged'`` or ``'in_scope'``).

    ``damaged`` takes the quantile over pairs with a strictly positive recorded dose, so the
    threshold is a statement about damaged counties and does not move when a declaration adds
    undamaged ones. The quantile is the value at rank ``ceil(q * n) - 1`` of the sorted doses.
    """
    doses = sorted(u['dose'] for u in units if over != 'damaged' or u['dose'] > 0)
    if not doses:
        return None
    return doses[max(0, math.ceil(quantile * len(doses)) - 1)]


def assign_roles(units, *, treated_min, control_max):
    """Set each unit's role from its dose and count the result."""
    facts = {'treated': 0, 'control': 0, 'middle': 0}
    for unit in units:
        unit['role'] = ('treated' if unit['dose'] >= treated_min
                        else 'control' if unit['dose'] < control_max else 'middle')
        facts[unit['role']] += 1
    return facts


def monthly_dose_panel(units, series, *, calendar, match_window, unit_window):
    """Matched monthly panel (strata = disaster x pre-growth half) and the unmatched comparison.

    ``series`` is ``{fips: {month: value}}`` for one outcome. Pre-growth is
    ``value(g + match_window[1]) - value(g + match_window[0])``, a window that must end before every
    tested lead.

    ``unit_window`` clips every unit's series to that range of event months around **its own stack's**
    cohort month. Each unit belongs to exactly one declaration and every unit in a stack shares that
    declaration's month, so months outside the window can enter no group-time cell the design
    estimates; dropping them is exact, not an approximation, and it is what keeps a monthly panel a
    tractable size.
    """
    logged, growth, dropped = {}, {}, 0
    for unit in units:
        if unit['role'] == 'middle':
            continue
        values = series.get(unit['fips']) or {}
        window = {month: value for month, value in values.items()
                  if calendar[0] <= month <= calendar[1] and unit_window[0] <= month - unit['g'] <= unit_window[1]}
        a, b = unit['g'] + match_window[0], unit['g'] + match_window[1]
        if a not in window or b not in window:
            dropped += 1
            continue
        logged[unit['unit']] = window
        growth[unit['unit']] = window[b] - window[a]
    by_disaster = {}
    for unit in units:
        if unit['unit'] in logged:
            by_disaster.setdefault(unit['disaster'], []).append(unit)
    outcomes, cohorts, strata, plain, clusters = {}, {}, {}, {}, {}
    for disaster, members in by_disaster.items():
        halves = (split_by_rank({m['unit']: growth[m['unit']] for m in members}, 2) if len(members) > 1
                  else {members[0]['unit']: 0})
        for member in members:
            key = member['unit']
            outcomes[key] = logged[key]
            cohorts[key] = member['g'] if member['role'] == 'treated' else None
            strata[key] = f'{disaster}|{"hi" if halves[key] else "lo"}'
            plain[key] = disaster
            clusters[key] = member['fips'][:2]
    matched = Panel(outcomes, cohorts, strata=strata, clusters=clusters)
    unmatched = Panel(outcomes, cohorts, strata=plain, clusters=clusters)
    return matched, unmatched, {'units_without_match_window_outcome': dropped}


# -- horizons: one att_gt pass, several post windows ------------------------------------------------------

def horizon_cells(panel, spec):
    """``(event-time cells, cluster count)``: the one pass every horizon's estimand is aggregated from."""
    anticipation = spec.get('anticipation', 0)
    gt = att_gt(panel, control_group=spec['control_group'], anticipation=anticipation, cohorts=spec.get('cohorts'))
    cells = aggregate_event_time(gt['cells'], spec['e_min'], spec['e_max'], reference=-1 - anticipation)
    return cells, panel.n_clusters()


def horizon_standard_errors(panel, spec, horizons):
    """``{horizon: analytic clustered standard error}`` of the mean ATT over event months ``0..horizon``.

    **Only the standard error is returned.** The point estimate is a treated-versus-control contrast,
    and a design's power must be measured before that contrast is read, so this function never
    returns, logs or stores it. That is not tidiness: reading it would burn the registration.
    """
    cells, n_clusters = horizon_cells(panel, spec)
    out = {}
    for horizon in horizons:
        overall = overall_from_event_time(cells, list(range(0, horizon + 1)))
        out[horizon] = None if overall is None else math.sqrt(_variance(overall['psi'], n_clusters))
    return out


def null_draw_horizons(panel, spec, params, seed, horizons):
    """One synthetic no-effect replication, aggregated to every horizon, plus the two diagnostics.

    The panel here carries calibrated noise and no effect, so its estimates are not an effect of
    anything; they are what the estimator returns when there is nothing to find.
    """
    fake = simulate_null_panel(panel, params, seed)
    cells, n_clusters = horizon_cells(fake, spec)
    out = {'seed': seed, 'horizons': {}}
    for horizon in horizons:
        overall = overall_from_event_time(cells, list(range(0, horizon + 1)))
        out['horizons'][horizon] = ({'att': None, 'se': None} if overall is None else
                                    {'att': overall['att'], 'se': math.sqrt(_variance(overall['psi'], n_clusters))})
    reference = -1 - spec.get('anticipation', 0)
    leads = [e for e in sorted(cells) if e < reference]

    def wald(kept):
        return wald_test([cells[e]['att'] for e in kept], [cells[e]['psi'] for e in kept], n_clusters)['p'] if kept else None

    out['pre_trend_p'] = wald(leads)
    # The same leads, gated over shorter windows. A joint Wald test on many leads with few clusters can
    # reject correct designs far more often than its nominal size, and a design has to choose its gating
    # window before it is registered, on null panels, not after seeing a real pre-trend fail.
    out['pre_trend_p_by_gate'] = {gate: wald([e for e in leads if e >= gate])
                                  for gate in spec.get('pre_trend_gates') or []}
    if spec.get('placebo_shift'):
        out['placebo_date_p'] = placebo_date_test(fake, shift=spec['placebo_shift'], cohorts=spec.get('cohorts'),
                                                  control_group=spec['control_group'],
                                                  anticipation=spec.get('anticipation', 0),
                                                  alpha=spec.get('alpha', 0.05))['p']
    return out


# -- the registered wave-3 study: monthly dose -> county LAUS employment ---------------------------------

#: The non-gating robustness variants a wave-3 registration may name, by the id before the colon in
#: ``estimator.non_gating_robustness``. A registration that names anything else is refused rather than
#: quietly reported without the variant it asked for.
UNMATCHED_VARIANT = 'unmatched_strata_disaster_only'
LONG_LEAD_VARIANT = 'long_lead_window'

#: Which acceptance fact each registered robustness estimator produces. An estimator that produces none
#: cannot gate anything: nothing ``evaluate_acceptance`` reads comes from it.
ROBUSTNESS_FACTS = {'stacked_did': 'stacked_ci'}

BOUNDING_ONLY_ROW = (
    'A bound, not an effect. This horizon is registered as bounding only: on synthetic panels with no '
    'effect in them the test rejects a true null on {rate:.1%} of panels against a {limit:.0%} limit, '
    'measured before registration. The interval printed here is therefore narrower than its true '
    'coverage and must be read as wider than it prints, and a rejection at this horizon may not be '
    'reported as an effect.')

BOUNDING_ONLY_STATEMENT = (
    'BOUNDING ONLY, never an effect, at these horizons: {rates}. Those are the rates at which this design '
    'rejects a true null, measured before registration on synthetic panels with no effect in them, '
    'against a {limit:.0%} limit. The intervals printed for those horizons are therefore narrower than '
    'their true coverage and must be read as wider than they print, and a rejection at them is not an '
    'effect and is not reported as one here. The registered primary estimand is the mean ATT over event '
    'months 0..{primary}, the longest horizon at which this design is both powered against the effect '
    'worth finding and correctly sized.')

DERIVATION_NOTES = (
    'The registration dates its cohort and calendar windows as YYYY-MM and states its primary estimand as '
    'a horizon in months. The runner resolves those mechanically - months to period indices '
    'year * 12 + (month - 1), and the primary horizon to the post window 0..primary_horizon_months - and '
    'derives nothing else: treatment.estimated_cohorts and windows.post in the recorded design are those '
    'two resolutions of the registration as written.',
    'estimates.horizons holds every registered horizon on the same cohort set and the same panel. The row '
    'for the primary horizon is the primary estimate; rows the registration registered as bounds carry '
    'bounding_only, the size that made them bounds, and the sentence that says a rejection there is not an '
    'effect. The identification label and the verdict follow from the primary horizon alone.',
    'estimates.robustness_roles says which robustness estimate can gate the identification label. An '
    'estimate gates only where an acceptance criterion reads a fact it produces; the criteria are the '
    "registration's own and the runner adds, removes and alters none. The pre-trend criterion is the "
    'joint Wald test over the gating lead window windows.e_min..-2; the longer lead window is estimated '
    'and reported as a non-gating variant, and no criterion can see it.')


def month_span(pair):
    """``['YYYY-MM', 'YYYY-MM']`` -> the two period indices it names."""
    return [month_index(pair[0]), month_index(pair[1])]


def resolved_registration(registration):
    """The registration with its month-dated windows resolved into what :func:`run_did_design` reads.

    Exactly two fields are derived, both mechanical restatements of the registration as written:
    ``treatment.estimated_cohorts`` is ``treatment.estimated_cohort_months`` as period indices, and
    ``windows.post`` is the primary estimand's event window ``0..windows.primary_horizon_months``.
    Nothing is added, removed or altered.
    """
    treatment, windows = registration['treatment'], registration['windows']
    return {**registration,
            'treatment': {**treatment, 'estimated_cohorts': month_span(treatment['estimated_cohort_months'])},
            'windows': {**windows, 'post': list(range(0, windows['primary_horizon_months'] + 1))}}


def bounding_only_horizons(registration):
    """The horizons the registration registered as bounds only, taken from its own measured power table.

    A horizon is a bound when the size measured on no-effect panels before registration is outside the
    limit the power registration uses (``size_acceptable`` false). The registration also states the same
    set in words under ``power.measured.verdict.can_only_bound_at``; the two must agree, and a
    registration whose prose and measured table disagree is refused rather than reinterpreted here.
    """
    measured = (registration.get('power') or {}).get('measured') or {}
    horizons = measured.get('horizons')
    if not horizons:
        raise ValueError('the registration declares horizons but no measured power table to size them by')
    missing = [h for h in registration['windows']['horizons'] if str(h) not in horizons]
    if missing:
        raise ValueError(f'horizons without a measured size: {missing}')
    derived = sorted(int(h) for h, row in horizons.items() if not row.get('size_acceptable'))
    declared = sorted(int(str(x).split()[0]) for x in (measured.get('verdict') or {}).get('can_only_bound_at', []))
    if derived != declared:
        raise ValueError(f'the registration says it can only bound at {declared} but its measured sizes say {derived}')
    return derived


def bounding_only_statement(registration):
    """The sentence a result carries when some of its horizons are registered as bounds, not effects."""
    bounds = bounding_only_horizons(registration)
    measured = registration['power']['measured']['horizons']
    rows = [measured[str(h)] for h in bounds]
    return BOUNDING_ONLY_STATEMENT.format(
        rates=', '.join(f'{h} months {r["null_rejection_rate"]:.1%}' for h, r in zip(bounds, rows)),
        limit=max(r['size_limit'] for r in rows),
        primary=registration['windows']['primary_horizon_months'])


def horizon_estimates(panel, registration, *, cohorts):
    """Every registered horizon's estimand, each marked with the role the registration gives it.

    The estimand at horizon ``H`` is the equal-weight mean of ATT(e) over event months ``0..H``,
    estimated on the same panel and the same cohort set as the primary, so the horizons are comparable
    rather than several different samples. The row for the primary horizon is computed from the same
    influence functions with the same bootstrap seed as the primary estimate and is identical to it.

    Horizons the registration registered as bounds carry ``bounding_only``, the size that made them
    bounds and the sentence that says a rejection at them is not an effect.
    """
    windows, inference = registration['windows'], registration['inference']
    estimator, controls = registration['estimator'], registration['controls']
    primary = windows['primary_horizon_months']
    bounds = set(bounding_only_horizons(registration))
    if primary not in windows['horizons']:
        raise ValueError(f'the primary horizon {primary} is not one of the registered horizons {windows["horizons"]}')
    measured = registration['power']['measured']['horizons']
    rows = []
    for horizon in windows['horizons']:
        result = event_study(panel, e_min=windows['e_min'], e_max=windows['e_max'],
                             post=list(range(0, horizon + 1)), control_group=controls['control_group'],
                             anticipation=estimator.get('anticipation', 0), alpha=inference['alpha'],
                             balance=tuple(estimator['balance']) if estimator.get('balance') else None,
                             bootstrap=inference.get('bootstrap', 999), seed=inference.get('seed', 0),
                             cohorts=cohorts)
        size = measured[str(horizon)]
        row = {'horizon_months': horizon,
               'estimand': f'equal-weight mean of ATT(e) over event months 0..{horizon}',
               'role': 'primary' if horizon == primary else 'bounding_only' if horizon in bounds else 'reported',
               'bounding_only': horizon in bounds,
               'overall': result['overall'],
               'measured_before_registration': {k: size.get(k) for k in
                                                ('mde_80', 'null_rejection_rate', 'size_limit', 'verdict')}}
        if horizon in bounds:
            row['reported_as'] = BOUNDING_ONLY_ROW.format(rate=size['null_rejection_rate'], limit=size['size_limit'])
        rows.append(row)
    return rows


def robustness_roles(registration):
    """Which robustness estimate can gate the identification label, and which cannot.

    An estimate gates only where one of the registration's own acceptance criteria reads a fact it
    produces. Entries the registration lists under ``estimator.non_gating_robustness`` are estimated with
    the primary estimator and reported; nothing ``evaluate_acceptance`` sees comes from them.
    """
    against = {c.get('against', 'stacked_ci') for c in registration['acceptance_criteria']
               if c['type'] == 'robustness_ci_overlap'}
    roles = {}
    for name in registration['estimator'].get('robustness', []):
        fact = ROBUSTNESS_FACTS.get(name)
        gating = fact is not None and fact in against
        roles[name] = {'registered_under': 'estimator.robustness', 'gating': gating,
                       'note': (f'gating: an acceptance criterion compares the primary interval with {fact}'
                                if gating else 'reported as a comparison: no acceptance criterion reads it')}
    for entry in registration['estimator'].get('non_gating_robustness', []):
        roles[str(entry).split(':', 1)[0].strip()] = {
            'registered_under': 'estimator.non_gating_robustness', 'gating': False, 'declared_as': entry,
            'note': 'reported only; the acceptance criteria never read it'}
    return roles


def non_gating_panels(registration, *, unmatched):
    """The ``extra_robustness`` argument for the variants the registration names, and only those."""
    windows, inference = registration['windows'], registration['inference']
    extra = {}
    for name, role in robustness_roles(registration).items():
        if role['registered_under'] != 'estimator.non_gating_robustness':
            continue
        if name == UNMATCHED_VARIANT:
            extra[name] = unmatched
        elif name == LONG_LEAD_VARIANT:
            # The same design and the same panel over the longer lead window, reported in full with the
            # registered inference. Its pre-trend test is reported; nothing gates on it.
            extra[name] = {'e_min': windows['long_lead_window_non_gating'],
                           'bootstrap': inference.get('bootstrap', 999), 'seed': inference.get('seed', 0)}
        else:
            raise ValueError('the registration names a non-gating robustness variant this runner does not '
                             f'implement: {name}')
    return extra


def monthly_dose_results(registration, status, units, laus, *, data=None):
    """Run the registered design for every registered outcome on prepared treatment-side inputs.

    ``units`` are the in-scope ``(disaster, county)`` pairs :func:`dose_distribution` returns and ``laus``
    the monthly first-release series :func:`load_monthly_laus` returns. Kept separate from
    :func:`run_fema_monthly_dose` so the design can be exercised on fixtures with a planted effect
    without a store and without the real panel.
    """
    treatment, windows = registration['treatment'], registration['windows']
    spec = resolved_registration(registration)
    calendar = month_span(windows['calendar_months'])
    unit_window = (treatment['match_window'][0], windows['e_max'])
    # The threshold is the number the registration pins, measured on treatment-side data before any
    # outcome was read; the quantile of this sample is reported beside it and never used to set roles.
    threshold = treatment['treated_min_usd_per_capita']
    roles = assign_roles(units, treated_min=threshold, control_max=treatment['control_max_usd_per_capita'])
    dose_facts = {'dose_threshold_usd_per_capita': threshold,
                  'dose_threshold_source': 'registration treatment.treated_min_usd_per_capita, pinned before any '
                                           'outcome was read',
                  'dose_quantile_recomputed_on_this_sample': decile_threshold(
                      units, quantile=treatment['dose_quantile'], over=treatment['dose_quantile_over']),
                  **roles}
    base = dict(data or {})
    scope_facts = base.pop('treatment_facts', {})
    variants = robustness_roles(registration)
    statement = bounding_only_statement(registration)
    outcomes = [registration['outcomes']['primary']] + registration['outcomes']['secondary']
    results = []
    for outcome in outcomes:
        # Each outcome is matched on its own pre-growth, as registered: units.sample requires the outcome
        # itself in month g + match_window[0] and in month g + match_window[1].
        series = {fips: outcome_series(months, outcome['id'], calendar) for fips, months in laus.items()}
        matched, unmatched, drop = monthly_dose_panel(units, series, calendar=calendar,
                                                      match_window=treatment['match_window'], unit_window=unit_window)
        span = spec['treatment']['estimated_cohorts']
        cohorts = [g for g in matched.cohort_sizes() if span[0] <= g <= span[1]]
        doses = sorted(u['dose'] for u in units if u['role'] == 'treated' and u['unit'] in matched.outcomes)
        facts = {**scope_facts, **dose_facts, **drop,
                 'treated_in_panel': sum(1 for u in matched.units if matched.cohorts[u] is not None),
                 'controls_in_panel': sum(1 for u in matched.units if matched.cohorts[u] is None),
                 'disasters_in_panel': len({u.split('|', 1)[0] for u in matched.units}),
                 'cohort_months_in_panel': [month_label(min(cohorts)), month_label(max(cohorts))] if cohorts else None,
                 'treated_dose_usd_per_capita_quartiles':
                     [doses[int(q * (len(doses) - 1))] for q in (0.25, 0.5, 0.75)] if doses else None}
        results.append(run_did_design(
            matched, spec, status, outcome={**outcome, 'role': 'primary' if outcome is outcomes[0] else 'secondary'},
            data={**base, 'treatment_facts': facts},
            extra_robustness=non_gating_panels(registration, unmatched=unmatched),
            extra_estimates={'horizons': horizon_estimates(matched, spec, cohorts=cohorts),
                             'robustness_roles': variants},
            does_not_establish=[statement], notes=DERIVATION_NOTES))
    return results


def run_fema_monthly_dose(registration, status, store, cache_dir):
    """The registered wave-3 study: FEMA declarations, the NOAA damage dose, monthly LAUS first releases."""
    lib = pinned(registration, 'event_library')
    noaa = pinned(registration, 'noaa_storm_events')
    bea = pinned(registration, 'bea_national_regional')
    monthly = pinned(registration, 'county_monthly_realtime_panel')
    treatment = {**registration['treatment'],
                 'incident_months': month_span(registration['treatment']['estimated_cohort_months'])}
    declarations = fema_declarations(store, lib)
    noaa_cache = extract_noaa_county_damage(store, noaa, cache_dir)
    bea_cache = extract_bea_county_population(store, bea, cache_dir)
    units, facts = dose_distribution(declarations, load_noaa_county_damage(noaa_cache['cache']),
                                     load_bea_county_population(bea_cache['cache']), treatment)
    laus = load_monthly_laus(store, monthly)
    data = {'inputs': [lib, noaa, bea, monthly],
            'extractions': {'noaa': _prov(noaa_cache), 'bea_population': _prov(bea_cache)},
            'outcome_source': {**monthly, 'counties_with_first_releases': len(laus)},
            'treatment_facts': facts}
    return monthly_dose_results(registration, status, units, laus, data=data)


RUNNERS = {'fema_monthly_dose_county_employment': run_fema_monthly_dose}
