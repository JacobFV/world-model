"""Wave-3 panel construction: the FEMA damage dose against a **monthly** county outcome.

Wave 2's within-declaration dose contrast used QCEW annual county employment, and the power suite
found it could not have detected the 2% effect its own registration called plausible
(MDE 0.033 against a plausible 0.02). The documented follow-up names the two fixes: a monthly
outcome, and a larger dose contrast. This module builds the panel for both. It runs no study and
reads no treated-versus-control contrast; it exists so that a power calculation can be made before a
wave-3 design is registered.

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

from .did import _variance, aggregate_event_time, att_gt, overall_from_event_time, wald_test
from .panel import Panel
from .placebo import placebo_date_test
from .power import simulate_null_panel
from .studies_wave2 import split_by_rank

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
