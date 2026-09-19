"""Synthetic staggered-adoption panels with known effects (fictional data for tests)."""
import random

from .panel import Panel


def staggered_panel(*, n_units=300, periods=range(2000, 2016), cohorts=None, never_share=0.3, effect=None,
                    noise=0.1, unit_sd=1.0, time_sd=0.3, treated_trend=0.0, cohort_trend=None, strata=1,
                    stratum_shock_sd=0.0, selection_on_stratum=0.0, clusters_per=1, anticipation_effect=0.0,
                    seed=0):
    """Build a panel and return ``(panel, truth)``.

    ``effect(e, g)`` is the treatment effect at event time ``e >= 0`` for cohort ``g``.
    ``treated_trend`` adds a linear trend (per period) to ever-treated units only, which violates
    parallel trends. ``cohort_trend`` maps a cohort to its own trend. With ``strata > 1`` each
    stratum gets its own period shocks (sd ``stratum_shock_sd``); ``selection_on_stratum`` makes
    treatment more likely in high-numbered strata, so an unstratified comparison is confounded.
    ``anticipation_effect`` shifts outcomes in the period just before treatment.
    ``truth`` holds the true ATT(e) averaged over treated units and the true overall ATT over
    the event times present in the panel.
    """
    rng = random.Random(seed)
    periods = list(periods)
    cohorts = cohorts or {periods[len(periods) // 3]: 0.35, periods[2 * len(periods) // 3]: 0.35}
    effect = effect or (lambda e, g: 1.0)
    time_fe = {t: rng.gauss(0, time_sd) for t in periods}
    stratum_fe = {(s, t): rng.gauss(0, stratum_shock_sd) for s in range(strata) for t in periods}
    cohort_list = list(cohorts.items())
    outcomes, assigned, strata_map, clusters = {}, {}, {}, {}
    for i in range(n_units):
        unit = f'u{i:05d}'
        s = i % strata
        tilt = selection_on_stratum * (s / max(strata - 1, 1) - 0.5)
        never = max(0.0, min(1.0, never_share - tilt))
        g = None
        if rng.random() >= never:
            x = rng.random() * sum(w for _, w in cohort_list)
            for cg, w in cohort_list:
                x -= w
                if x <= 0:
                    g = cg
                    break
            if g is None:
                g = cohort_list[-1][0]
        a = rng.gauss(0, unit_sd)
        series = {}
        for k, t in enumerate(periods):
            y = a + time_fe[t] + stratum_fe[(s, t)] + rng.gauss(0, noise)
            if g is not None:
                y += treated_trend * k
                if cohort_trend and g in cohort_trend:
                    y += cohort_trend[g] * k
                if t >= g:
                    y += effect(t - g, g)
                elif t == g - 1:
                    y += anticipation_effect
            series[t] = y
        outcomes[unit], assigned[unit], strata_map[unit] = series, g, f's{s}'
        clusters[unit] = f'c{i // clusters_per}'
    panel = Panel(outcomes, assigned, strata_map if strata > 1 else None, clusters if clusters_per > 1 else None)
    truth = {}
    for unit, g in assigned.items():
        if g is None:
            continue
        for t in periods:
            if t >= g:
                truth.setdefault(t - g, []).append(effect(t - g, g))
    att_e = {e: sum(v) / len(v) for e, v in truth.items()}
    return panel, {'att_e': att_e}
