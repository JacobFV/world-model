"""Wave-2 registered studies: panel construction and runners.

* ``fema_damage_dose_county_employment``: counties declared in the same FEMA major disaster, heavy NOAA
  damage per capita against negligible damage, matched on pre-period employment growth.
* ``tariff_mfn_decreases_imports``: first MFN decreases of at least 2 pp (HS2022 years through the UN
  HS2022-HS2017 correlation), one year of anticipation, BACI HS1992 imports from 2009, matched on
  2009-2013 import growth.
* ``sanctions_program_waves_us_trade``: OFAC country-programme designation waves against the target
  country's trade with the United States relative to its trade with everyone else, by HS chapter.

Every runner reads only the pinned inputs its registration names and returns result records.
"""
import bisect
import math

from .panel import Panel
from .sources import extract_bls_county, library_events, load_baci_imports, load_bls_county
from .sources_wave2 import (extract_baci92_imports, extract_baci92_partner_share, extract_bea_county_population,
                            extract_noaa_county_damage, load_baci92_partner_share, load_bea_county_population,
                            load_noaa_county_damage)
from .studies import _log_series, _prov, pinned
from .study import run_did_design


def split_by_rank(values, parts):
    """Assign each key of ``values`` to one of ``parts`` near-equal groups by (value, key) rank (0 = lowest)."""
    order = sorted(values, key=lambda k: (values[k], str(k)))
    n = len(order)
    return {k: min(parts - 1, i * parts // n) for i, k in enumerate(order)}


# -- 1. FEMA major disasters x NOAA damage dose ---------------------------------------------------------

def fema_declarations(store, library_ref):
    """``{disaster: {'type', 'begin', 'end', 'counties'}}`` for county-designated DR rows in the 50 states + DC."""
    out = {}
    for event in library_events(store, library_ref, {'fema_major_disaster_declaration'}):
        a = event['attributes']
        fips = a['unit'].rsplit(':', 1)[1]
        if not fips.isdigit() or len(fips) != 5 or int(fips[:2]) > 56:
            continue
        begin = (a.get('incident_begin_date') or a['date'])[:10]
        end = (a.get('incident_end_date') or begin)[:10]
        entry = out.setdefault(a['disaster'], {'type': a.get('incident_type'), 'begin': begin, 'end': end, 'counties': set()})
        entry['counties'].add(fips)
    return out


def fema_dose_units(declarations, damage, population, treatment):
    """Classify (disaster, county) pairs by NOAA county damage per capita during the incident period.

    Returns ``(units, facts)``; each unit is ``{'unit', 'disaster', 'fips', 'g', 'dose', 'role'}`` with role
    ``treated`` (dose >= treated_min), ``control`` (dose < control_max) or ``middle`` (excluded).
    """
    dose_types = set(treatment['dose_incident_types'])
    quiet_types = set(treatment['quiet_period_incident_types'])
    lo, hi = treatment['incident_years']
    quiet = treatment['quiet_years']
    treated_min, control_max = treatment['treated_min_usd_per_capita'], treatment['control_max_usd_per_capita']
    history = {}
    for d, v in declarations.items():
        if v['type'] in quiet_types:
            for fips in v['counties']:
                history.setdefault(fips, []).append((int(v['begin'][:4]), d))
    facts = {'pairs_in_scope': 0, 'excluded_recent_other_disaster': 0, 'excluded_no_population': 0,
             'treated': 0, 'control': 0, 'middle': 0}
    units = []
    for d in sorted(declarations):
        v = declarations[d]
        if v['type'] not in dose_types:
            continue
        g = int(v['begin'][:4])
        if not lo <= g <= hi:
            continue
        for fips in sorted(v['counties']):
            facts['pairs_in_scope'] += 1
            if any(other != d and g - quiet <= year <= g - 1 for year, other in history.get(fips, [])):
                facts['excluded_recent_other_disaster'] += 1
                continue
            pop = population.get(fips, {}).get(g - 1)
            if not pop:
                facts['excluded_no_population'] += 1
                continue
            rows = damage.get(fips, [])
            i = bisect.bisect_left(rows, (v['begin'],))
            total = 0.0
            while i < len(rows) and rows[i][0] <= v['end']:
                total += rows[i][1]
                i += 1
            dose = total / pop
            role = 'treated' if dose >= treated_min else 'control' if dose < control_max else 'middle'
            facts[role] += 1
            units.append({'unit': f'{d}|{fips}', 'disaster': d, 'fips': fips, 'g': g, 'dose': dose, 'role': role})
    return units, facts


def fema_dose_panels(units, series, *, calendar, match_window):
    """Matched panel (strata = disaster x pre-growth half) and the same units unmatched (strata = disaster).

    Pre-growth is log outcome(g + match_window[1]) - log outcome(g + match_window[0]), e.g. (-8, -5); units
    without both years are dropped from both panels.
    """
    lo, hi = calendar
    logged, growth, dropped = {}, {}, 0
    for u in units:
        if u['role'] == 'middle':
            continue
        values = series.get(u['fips'])
        s = _log_series(values, lo, hi) if values else {}
        a, b = u['g'] + match_window[0], u['g'] + match_window[1]
        if a not in s or b not in s:
            dropped += 1
            continue
        logged[u['unit']] = s
        growth[u['unit']] = s[b] - s[a]
    by_disaster = {}
    for u in units:
        if u['unit'] in logged:
            by_disaster.setdefault(u['disaster'], []).append(u)
    outcomes, cohorts, strata, plain, clusters = {}, {}, {}, {}, {}
    for d, members in by_disaster.items():
        halves = split_by_rank({m['unit']: growth[m['unit']] for m in members}, 2) if len(members) > 1 else \
            {members[0]['unit']: 0}
        for m in members:
            key = m['unit']
            outcomes[key] = logged[key]
            cohorts[key] = m['g'] if m['role'] == 'treated' else None
            strata[key] = f'{d}|{"hi" if halves[key] else "lo"}'
            plain[key] = d
            clusters[key] = m['fips'][:2]
    matched = Panel(outcomes, cohorts, strata=strata, clusters=clusters)
    unmatched = Panel(outcomes, cohorts, strata=plain, clusters=clusters)
    return matched, unmatched, {'units_without_match_window_outcome': dropped}


def run_fema_dose(registration, status, store, cache_dir):
    lib = pinned(registration, 'event_library')
    noaa = pinned(registration, 'noaa_storm_events')
    bea = pinned(registration, 'bea_national_regional')
    bls = pinned(registration, 'bls_labor')
    treatment = registration['treatment']
    declarations = fema_declarations(store, lib)
    noaa_cache = extract_noaa_county_damage(store, noaa, cache_dir)
    bea_cache = extract_bea_county_population(store, bea, cache_dir)
    units, facts = fema_dose_units(declarations, load_noaa_county_damage(noaa_cache['cache']),
                                   load_bea_county_population(bea_cache['cache']), treatment)
    bls_cache = extract_bls_county(store, bls, cache_dir)
    outcomes = [registration['outcomes']['primary']] + registration['outcomes']['secondary']
    metric = {'qcew_log_employment': 'employment', 'qcew_log_establishments': 'establishment_count'}
    growth_series = load_bls_county(bls_cache['cache'], 'QCEW', 'employment')
    results = []
    for outcome in outcomes:
        series = load_bls_county(bls_cache['cache'], 'QCEW', metric[outcome['id']])
        # matching always uses employment growth, as registered
        matched, unmatched, drop = fema_dose_panels(units, growth_series, calendar=registration['windows']['calendar_years'],
                                                    match_window=treatment['match_window'])
        if outcome['id'] != 'qcew_log_employment':
            lo, hi = registration['windows']['calendar_years']
            outs = {}
            for u in matched.units:
                values = series.get(u.split('|', 1)[1])
                s = _log_series(values, lo, hi) if values else {}
                if s:
                    outs[u] = s
            matched = matched.replace(outcomes=outs)
            unmatched = unmatched.replace(outcomes=outs)
        role = 'primary' if outcome is outcomes[0] else 'secondary'
        doses = sorted(u['dose'] for u in units if u['role'] == 'treated' and u['unit'] in matched.outcomes)
        data = {'inputs': [lib, noaa, bea, bls],
                'extractions': {'noaa': _prov(noaa_cache), 'bea_population': _prov(bea_cache), 'bls': _prov(bls_cache)},
                'treatment_facts': {**facts, **drop, 'treated_in_panel': sum(1 for u in matched.units if matched.cohorts[u] is not None),
                                    'controls_in_panel': sum(1 for u in matched.units if matched.cohorts[u] is None),
                                    'disasters_in_panel': len({u.split('|', 1)[0] for u in matched.units}),
                                    'treated_dose_usd_per_capita_quartiles': [doses[int(q * (len(doses) - 1))] for q in (0.25, 0.5, 0.75)] if doses else None}}
        results.append(run_did_design(matched, registration, status, outcome={**outcome, 'role': role}, data=data,
                                      extra_robustness={'unmatched_strata_disaster_only': unmatched}))
    return results


# -- 2. MFN tariff decreases -----------------------------------------------------------------------------

def one_to_one(store, concordance_ref, crosswalk):
    """``{source_code: target_code}`` for codes that correspond one-to-one in both directions."""
    forward, backward = {}, {}
    for r in store.records(concordance_ref):
        if r.get('kind') != 'assertion' or r.get('predicate') != 'maps_to':
            continue
        if (r.get('attributes') or {}).get('crosswalk') != crosswalk:
            continue
        s, t = r['subject'].split(':', 1)[1], r['object'].split(':', 1)[1]
        forward.setdefault(s, set()).add(t)
        backward.setdefault(t, set()).add(s)
    return {s: next(iter(ts)) for s, ts in forward.items() if len(ts) == 1 and len(backward[next(iter(ts))]) == 1}


def mfn_series_hs17(store, wits_ref, hs22_to_hs17):
    """``{(reporter, hs17): {year: rate}}`` from HS2017 years directly and HS2022 years through one-to-one codes.

    Also returns the set of units with an HS2017 rate reported for 2018 (the wave-1 universe rule).
    """
    series, universe = {}, set()
    for r in store.records(wits_ref):
        if r.get('kind') != 'observation' or r.get('metric') != 'mfn_applied_tariff_simple_avg' or r['value'] is None:
            continue
        if not r['subject'].startswith('iso3:'):
            continue
        d = r['dimensions']
        year = int(r['valid_from'][:4])
        code = d['product'].split(':', 1)[1]
        if d.get('hs_revision') == 'HS2017':
            key = (r['subject'], code)
            series.setdefault(key, {})[year] = r['value']
            if year == 2018:
                universe.add(key)
        elif d.get('hs_revision') == 'HS2022' and code in hs22_to_hs17:
            series.setdefault((r['subject'], hs22_to_hs17[code]), {}).setdefault(year, r['value'])
    return series, universe


def first_mfn_changes(series, universe, *, min_change=0.5):
    """First change of at least ``min_change`` pp between consecutive reported years, per universe unit.

    Returns ``{unit: None | (year, change, spans_gap)}``.
    """
    out = {}
    for key in universe:
        ys = series[key]
        years = sorted(ys)
        first = None
        for a, b in zip(years, years[1:]):
            change = ys[b] - ys[a]
            if abs(change) >= min_change:
                first = (b, change, b - a > 1)
                break
        out[key] = first
    return out


def tariff_decrease_cohorts(first, *, max_decrease):
    cohorts, excluded, facts = {}, set(), {'never_changed': 0, 'first_change_decrease_2pp': 0,
                                          'first_change_other': 0, 'first_change_spans_reporting_gap': 0}
    for key, f in first.items():
        if f is None:
            facts['never_changed'] += 1
        elif f[2]:
            excluded.add(key)
            facts['first_change_spans_reporting_gap'] += 1
        elif f[1] <= max_decrease:
            cohorts[key] = f[0]
            facts['first_change_decrease_2pp'] += 1
        else:
            excluded.add(key)
            facts['first_change_other'] += 1
    return cohorts, excluded, facts


def tariff_decrease_panels(universe, cohorts, excluded, hs17_to_hs92, flows, *, outcome_id, calendar, positive_years,
                           growth_window, terciles=3):
    lo, hi = calendar
    values, cohort_map, growth, importer, clusters = {}, {}, {}, {}, {}
    facts = {'units_without_one_to_one_hs92': 0, 'units_without_positive_required_years': 0}
    for unit in sorted(universe):
        if unit in excluded:
            continue
        rep, hs17 = unit
        hs92 = hs17_to_hs92.get(hs17)
        if hs92 is None:
            facts['units_without_one_to_one_hs92'] += 1
            continue
        if any(flows.get((rep, hs92, y), (0.0,))[0] <= 0 for y in positive_years):
            facts['units_without_positive_required_years'] += 1
            continue
        series = {}
        for year in range(lo, hi + 1):
            f = flows.get((rep, hs92, year))
            if f is None:
                continue
            if outcome_id == 'baci92_log_import_value':
                v = f[0]
            elif outcome_id == 'baci92_log_import_quantity':
                v = f[1] if f[3] == 0 else None
            else:
                raise ValueError(f'unknown outcome {outcome_id}')
            if v is not None and v > 0:
                series[year] = math.log(v)
        a, b = growth_window
        va, vb = flows[(rep, hs92, a)][0], flows[(rep, hs92, b)][0]
        key = f'{rep}|{hs17}'
        values[key] = series
        cohort_map[key] = cohorts.get(unit)
        growth[key] = math.log(vb) - math.log(va)
        importer[key] = rep
        clusters[key] = f'{rep}|{hs17[:2]}'
    by_importer = {}
    for key, rep in importer.items():
        by_importer.setdefault(rep, {})[key] = growth[key]
    strata = {}
    for rep, g in by_importer.items():
        bins = split_by_rank(g, terciles) if len(g) >= terciles else {k: 0 for k in g}
        for key, b in bins.items():
            strata[key] = f'{rep}|t{b}'
    matched = Panel(values, cohort_map, strata=strata, clusters=clusters)
    unmatched = Panel(values, cohort_map, strata=importer, clusters=clusters)
    return matched, unmatched, facts


def run_tariff_decreases(registration, status, store, cache_dir):
    wits = pinned(registration, 'wits_trains_tariffs')
    conc = pinned(registration, 'trade_concordances')
    baci = pinned(registration, 'cepii_baci_hs92')
    t = registration['treatment']
    hs22_to_hs17 = one_to_one(store, conc, t['hs2022_to_hs2017_crosswalk'])
    hs17_to_hs92 = one_to_one(store, conc, registration['outcomes']['hs2017_to_hs1992_crosswalk'])
    series, universe = mfn_series_hs17(store, wits, hs22_to_hs17)
    first = first_mfn_changes(series, universe, min_change=t['min_change_pp'])
    cohorts, excluded, facts = tariff_decrease_cohorts(first, max_decrease=t['max_decrease_pp'])
    importers = sorted({u[0] for u in universe})
    lo, hi = registration['windows']['calendar_years']
    baci_cache = extract_baci92_imports(store, baci, cache_dir, importers, years=[lo, hi])
    flows = load_baci_imports(baci_cache['cache'])
    importers_in_baci = {k[0] for k in flows}
    universe = {u for u in universe if u[0] in importers_in_baci}
    outcomes = [registration['outcomes']['primary']] + registration['outcomes']['secondary']
    results = []
    for outcome in outcomes:
        matched, unmatched, sample_facts = tariff_decrease_panels(
            universe, cohorts, excluded, hs17_to_hs92, flows, outcome_id=outcome['id'], calendar=(lo, hi),
            positive_years=registration['units']['positive_import_years'], growth_window=registration['units']['growth_window'])
        span = t['estimated_cohorts']
        treated = [u for u in matched.units if matched.cohorts[u] is not None and span[0] <= matched.cohorts[u] <= span[1]]
        role = 'primary' if outcome is outcomes[0] else 'secondary'
        data = {'inputs': [wits, conc, baci], 'outcome_extraction': _prov(baci_cache),
                'treatment_facts': {**facts, **sample_facts, 'universe_units_2018': len(universe),
                                    'importers': len(importers_in_baci & set(importers)),
                                    'hs2022_hs2017_one_to_one_codes': len(hs22_to_hs17),
                                    'hs2017_hs1992_one_to_one_codes': len(hs17_to_hs92),
                                    'treated_units_estimated': len(treated),
                                    'treated_by_cohort': {str(g): sum(1 for u in treated if matched.cohorts[u] == g)
                                                          for g in range(span[0], span[1] + 1)}}}
        results.append(run_did_design(matched, registration, status, outcome={**outcome, 'role': role}, data=data,
                                      extra_robustness={'unmatched_strata_importer_only': unmatched}))
    return results


# -- 3. OFAC country-programme waves -> trade with the United States ------------------------------------

def programme_waves(store, library_ref, targets, *, min_designations):
    """First year with at least ``min_designations`` OFAC designations under each target's programmes.

    ``targets`` maps an ISO3 code to the exact OFAC programme codes aimed at it. Returns ``(first, counts)``.
    """
    program_to_target = {p: c for c, programs in targets.items() for p in programs}
    counts = {}
    for event in library_events(store, library_ref, {'sanctions_designation'}):
        a = event['attributes']
        if a['source']['dataset'] != 'ofac_sanctions':
            continue
        year = int(a['date'][:4])
        for target in {program_to_target[p] for p in a.get('programs') or [] if p in program_to_target}:
            counts.setdefault(target, {})
            counts[target][year] = counts[target].get(year, 0) + 1
    first = {}
    for target, by_year in counts.items():
        years = [y for y, n in by_year.items() if n >= min_designations]
        if years:
            first[target] = min(years)
    return first, counts


def partner_share_panel(shares, first, *, estimated, calendar, partner='iso3:USA'):
    """Units country x HS2; outcome log(trade with partner) - log(trade with everyone else).

    Countries first treated before ``estimated[0]`` are dropped; later cohorts stay as not-yet-treated
    comparisons. Strata: HS chapter. Clusters: country.
    """
    lo, hi = calendar
    outcomes, cohorts, strata, clusters = {}, {}, {}, {}
    facts = {'dropped_first_wave_before_window': sorted(c for c, g in first.items() if g < estimated[0])}
    for (country, hs2), by_year in shares.items():
        if not country.startswith('iso3:') or country == partner:
            continue
        iso = country[5:]
        g = first.get(iso)
        if g is not None and g < estimated[0]:
            continue
        series = {}
        for year, (a, b) in by_year.items():
            if lo <= year <= hi and a > 0 and b > 0:
                series[year] = math.log(a) - math.log(b)
        if not series:
            continue
        key = f'{country}|{hs2}'
        outcomes[key], cohorts[key], strata[key], clusters[key] = series, g, hs2, country
    return Panel(outcomes, cohorts, strata=strata, clusters=clusters), facts


def run_sanctions_trade(registration, status, store, cache_dir):
    lib = pinned(registration, 'event_library')
    baci = pinned(registration, 'cepii_baci_hs92')
    t = registration['treatment']
    first, counts = programme_waves(store, lib, t['programme_targets'], min_designations=t['min_designations_per_year'])
    cache = extract_baci92_partner_share(store, baci, cache_dir, partner='iso3:USA')
    span = t['estimated_cohorts']
    outcomes = [registration['outcomes']['primary']] + registration['outcomes']['secondary']
    results = []
    for outcome in outcomes:
        direction = {'log_us_share_ratio_exports': 'exports', 'log_us_share_ratio_imports': 'imports'}[outcome['id']]
        panel, facts = partner_share_panel(load_baci92_partner_share(cache['cache'], direction), first,
                                           estimated=span, calendar=registration['windows']['calendar_years'])
        treated_countries = sorted({panel.clusters[u] for u in panel.units
                                    if panel.cohorts[u] is not None and span[0] <= panel.cohorts[u] <= span[1]})
        role = 'primary' if outcome is outcomes[0] else 'secondary'
        data = {'inputs': [lib, baci], 'outcome_extraction': _prov(cache),
                'treatment_facts': {**facts, 'first_wave_year': dict(sorted(first.items(), key=lambda x: (x[1], x[0]))),
                                    'designations_by_target_year': {c: dict(sorted(v.items())) for c, v in sorted(counts.items())},
                                    'treated_countries_in_panel': treated_countries,
                                    'treated_units': sum(1 for u in panel.units if panel.cohorts[u] is not None
                                                         and span[0] <= panel.cohorts[u] <= span[1])}}
        results.append(run_did_design(panel, registration, status, outcome={**outcome, 'role': role}, data=data))
    return results


RUNNERS = {'fema_damage_dose_county_employment': run_fema_dose, 'tariff_mfn_decreases_imports': run_tariff_decreases,
           'sanctions_programme_waves_us_trade': run_sanctions_trade}
