"""Exposure ranking for tropical cyclones, scored on held-out events against a naive ranking.

Given a storm track (the shock), rank the US counties it reaches using only information available
before the event: a modelled wind hazard at each county and the county's migration-graph proximity
to hazard. Score each ranking by the Spearman correlation with the county's measured, seasonally
differenced employment change around the storm, per event, and compare with ranking by distance
to the track on held-out storms.
"""
import bisect
import math

from .ranking import compare_rankings, paired_sign_flip, score_event
from .results import PREDICTIVE, build_result, evaluate_acceptance
from .sources import extract_bls_county, library_events, load_bls_county
from .stats import mean, ranks

RADIUS_KM = 300.0
RMAX_KM = 40.0
MIN_WIND_KT = 34.0
LAMBDAS = (0.0, 0.25, 0.5, 1.0)
MIN_COUNTIES = 20


def haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, h)))


def modelled_wind(v_max, distance_km, rmax_km=RMAX_KM):
    """Sustained wind at a distance from the centre: V for d <= Rmax, V (Rmax/d)^0.5 beyond."""
    return v_max * min(1.0, math.sqrt(rmax_km / max(distance_km, 1e-9)))


def storm_tracks(store, ibtracs_ref, storms):
    fixes = {}
    for r in store.records(ibtracs_ref):
        if r['kind'] != 'observation' or r.get('subject') not in storms:
            continue
        if r.get('metric') not in ('latitude', 'longitude', 'max_sustained_wind') or r['value'] is None:
            continue
        d = r.get('dimensions', {})
        if d.get('track_type', 'main') != 'main':
            continue
        fix = fixes.setdefault(r['subject'], {}).setdefault(r['valid_from'], {})
        if r['metric'] == 'max_sustained_wind':
            if d.get('source') == 'usa' or 'wind' not in fix:
                fix['wind'] = r['value']
        else:
            fix[r['metric']] = r['value']
    return {s: sorted((t, f['latitude'], f['longitude'], f['wind']) for t, f in fx.items()
                      if {'latitude', 'longitude', 'wind'} <= set(f)) for s, fx in fixes.items()}


def county_centroids(store, geography_ref):
    best, vintage = {}, {}
    for r in store.records(geography_ref):
        if r['kind'] != 'observation' or r.get('metric') not in ('latitude', 'longitude'):
            continue
        subject = r.get('subject', '')
        if not subject.startswith('geo:US:county:'):
            continue
        fips = subject[14:]
        if not fips.isdigit() or int(fips[:2]) > 56:
            continue
        v = r.get('dimensions', {}).get('geography_vintage') or 0
        if v >= vintage.get((fips, r['metric']), -1):
            vintage[(fips, r['metric'])] = v
            best.setdefault(fips, {})[r['metric']] = r['value']
    return {f: (p['latitude'], p['longitude']) for f, p in best.items() if len(p) == 2}


def migration_graph(store, irs_ref, year='2017'):
    graph = {}
    for r in store.records(irs_ref):
        if r['kind'] != 'observation' or r.get('metric') != 'migration_individuals' or r['value'] is None:
            continue
        d = r.get('dimensions', {})
        if d.get('flow_type') != 'migration' or d.get('perspective') != 'inflow' or r['valid_from'][:4] != year:
            continue
        o, t = d.get('origin', ''), d.get('destination', '')
        if not (o.startswith('geo:US:county:') and t.startswith('geo:US:county:')):
            continue
        o, t = o[14:], t[14:]
        if o == t or r['value'] <= 0:
            continue
        graph.setdefault(o, {})[t] = graph.get(o, {}).get(t, 0.0) + r['value']
        graph.setdefault(t, {})[o] = graph.get(t, {}).get(o, 0.0) + r['value']
    return graph


def _month(ym, delta):
    y, m = int(ym[:4]), int(ym[5:7])
    k = y * 12 + (m - 1) + delta
    return f'{k // 12:04d}-{k % 12 + 1:02d}'


def employment_loss(series, month):
    """-(seasonally differenced change in mean log employment, post months +1..+2 vs pre -2..-1)."""
    needed = {k: _month(month, k) for k in (-2, -1, 1, 2, -14, -13, -11, -10)}
    values = {k: series.get(p) for k, p in needed.items()}
    if any(v is None or v <= 0 for v in values.values()):
        return None
    ln = {k: math.log(v) for k, v in values.items()}
    now = (ln[1] + ln[2]) / 2 - (ln[-2] + ln[-1]) / 2
    before = (ln[-11] + ln[-10]) / 2 - (ln[-14] + ln[-13]) / 2
    return -(now - before)


def event_rankings(track, centroids, by_lat, lats, graph, employment):
    fixes = [(t, lat, lon, w) for t, lat, lon, w in track if w >= MIN_WIND_KT]
    if not fixes:
        return None
    dlat = RADIUS_KM / 111.0
    near, first_time = {}, None
    for t, lat, lon, wind in fixes:
        lo, hi = bisect.bisect_left(lats, lat - dlat), bisect.bisect_right(lats, lat + dlat)
        for _, fips in by_lat[lo:hi]:
            clat, clon = centroids[fips]
            d = haversine_km(lat, lon, clat, clon)
            if d > RADIUS_KM:
                continue
            first_time = t if first_time is None or t < first_time else first_time
            h = modelled_wind(wind, d)
            cur = near.get(fips)
            if cur is None:
                near[fips] = [d, h]
            else:
                cur[0], cur[1] = min(cur[0], d), max(cur[1], h)
    if not near:
        return None
    month = first_time[:7]
    outcome = {}
    for fips in near:
        loss = employment_loss(employment.get(fips, {}), month)
        if loss is not None:
            outcome[fips] = loss
    units = sorted(outcome)
    if len(units) < MIN_COUNTIES:
        return {'month': month, 'candidates': len(near), 'units': len(units), 'skipped': True}
    hazard = {f: near[f][1] for f in near}
    graph_score = {}
    for fips in units:
        links = graph.get(fips, {})
        total = sum(w for j, w in links.items() if j != fips)
        graph_score[fips] = (sum(w * hazard.get(j, 0.0) for j, w in links.items() if j != fips) / total) if total else 0.0
    n = len(units)
    rh = dict(zip(units, ranks([hazard[f] for f in units])))
    rg = dict(zip(units, ranks([graph_score[f] for f in units])))
    rankings = {'naive': {f: -near[f][0] for f in units}, 'hazard': {f: hazard[f] for f in units}}
    for lam in LAMBDAS:
        rankings[f'exposure_{lam}'] = {f: rh[f] / n + lam * rg[f] / n for f in units}
    return {'month': month, 'candidates': len(near), 'units': n, 'skipped': False, 'units_list': units,
            'rankings': rankings, 'outcome': outcome}


def run_exposure(registration, status, store, cache_dir):
    from .studies import pinned
    lib = pinned(registration, 'event_library')
    refs = {name: pinned(registration, name) for name in ('ibtracs', 'census_geography', 'irs_soi_migration', 'bls_labor')}
    split = registration['treatment']['split']
    storms = {}
    for event in library_events(store, lib, {'tropical_cyclone'}):
        a = event['attributes']
        year = int(a['date'][:4])
        if a.get('basin') == 'NA' and split['development'][0] <= year <= split['held_out'][1]:
            storms[a['unit']] = {'storm': a['unit'], 'name': a.get('name'), 'date': a['date'], 'year': year}
    tracks = storm_tracks(store, refs['ibtracs'], set(storms))
    centroids = county_centroids(store, refs['census_geography'])
    by_lat = sorted((lat, f) for f, (lat, _) in centroids.items())
    lats = [x for x, _ in by_lat]
    graph = migration_graph(store, refs['irs_soi_migration'])
    bls_cache = extract_bls_county(store, refs['bls_labor'], cache_dir)
    employment = load_bls_county(bls_cache['cache'], 'LAUS', 'employment')
    dev, held, skipped = [], [], []
    for sid in sorted(storms, key=lambda s: (storms[s]['date'], s)):
        info = storms[sid]
        ranked = event_rankings(tracks.get(sid, []), centroids, by_lat, lats, graph, employment)
        if ranked is None or ranked['skipped']:
            skipped.append({**info, 'reason': 'no county within 300 km of a >=34 kt fix' if ranked is None
                            else f'{ranked["units"]} counties with outcome (< {MIN_COUNTIES})'})
            continue
        scored = score_event(ranked['units_list'], ranked['rankings'], ranked['outcome'], k_frac=0.1)
        row = {**info, 'month': ranked['month'], 'candidates': ranked['candidates'], **scored}
        (held if info['year'] >= split['held_out'][0] else dev).append(row)
    dev_means = {}
    for lam in LAMBDAS:
        vals = [r[f'exposure_{lam}']['spearman'] for r in dev if r[f'exposure_{lam}']['spearman'] is not None]
        dev_means[lam] = mean(vals) if vals else None
    chosen = max(LAMBDAS, key=lambda lam: (dev_means[lam] if dev_means[lam] is not None else -9, -lam))
    exposure = f'exposure_{chosen}'
    for r in held + dev:
        r['exposure'] = r[exposure]
    inf = registration['inference']
    spearman = compare_rankings(held, 'exposure', 'naive', metric='spearman', replications=inf['replications'],
                                seed=inf['seed'])
    capture = compare_rankings(held, 'exposure', 'naive', metric='top_k_capture', replications=inf['replications'],
                               seed=inf['seed'])
    hazard_vs_naive = compare_rankings(held, 'hazard', 'naive', metric='spearman', replications=inf['replications'],
                                       seed=inf['seed'])
    exposure_vs_zero = paired_sign_flip([r['exposure']['spearman'] for r in held if r['exposure']['spearman'] is not None],
                                        replications=inf['replications'], seed=inf['seed'])
    naive_vs_zero = paired_sign_flip([r['naive']['spearman'] for r in held if r['naive']['spearman'] is not None],
                                     replications=inf['replications'], seed=inf['seed'])
    facts = {'events': len(held), 'held_out_spearman_p': spearman['p'],
             'held_out_exposure_mean_spearman': spearman['candidate_mean']}
    acceptance = evaluate_acceptance(registration['acceptance_criteria'], facts)
    passed = all(a['passed'] for a in acceptance)
    verdict = (f'On {len(held)} held-out storms (2019-2024) the exposure ranking (lambda={chosen}, chosen on {len(dev)} '
               f'development storms) has mean Spearman {spearman["candidate_mean"]:+.3f} with measured employment '
               f'loss versus {spearman["baseline_mean"]:+.3f} for distance to track; difference '
               f'{spearman["mean_difference"]:+.3f}, one-sided sign-flip p = {spearman["p"]:.3f}. '
               + ('The pre-registered test passes: the ranking predicts post-event employment loss better than the '
                  'naive ranking.' if passed else
                  'The pre-registered test fails: the exposure ranking is not shown to beat the naive ranking.')
               + ' This is a predictive association, not an intervention response.') if held else \
        'No held-out storms met the registered inclusion rule; the test could not be run.'
    per_event = [{k: r[k] for k in ('storm', 'name', 'date', 'month', 'candidates', 'n_units', 'k', 'top_k_capture_null')}
                 | {name: r[name] for name in ('naive', 'hazard', 'exposure')} for r in held]
    dev_rows = [{k: r[k] for k in ('storm', 'name', 'date', 'n_units')}
                | {f'exposure_{lam}': r[f'exposure_{lam}']['spearman'] for lam in LAMBDAS}
                | {'naive': r['naive']['spearman']} for r in dev]
    estimates = {'lambda_selection': {'grid': list(LAMBDAS), 'development_mean_spearman': {str(k): v for k, v in dev_means.items()},
                                      'chosen': chosen, 'development_events': len(dev)},
                 'held_out': {'spearman': spearman, 'top_k_capture': capture, 'hazard_vs_naive': hazard_vs_naive,
                              'exposure_spearman_vs_zero': exposure_vs_zero, 'naive_spearman_vs_zero': naive_vs_zero},
                 'held_out_events': per_event, 'development_events': dev_rows}
    record = build_result(study_id=registration['study_id'], registration=registration, registration_status=status,
                          identification_label=PREDICTIVE, verdict=verdict, estimates=estimates,
                          diagnostics={'skipped_events': skipped, 'migration_graph_counties': len(graph),
                                       'centroids': len(centroids)},
                          acceptance=acceptance,
                          data={'inputs': [lib, *refs.values()],
                                'outcome_extraction': {k: bls_cache[k] for k in ('input', 'spec', 'rows', 'sha256')}},
                          assumptions=registration['assumptions'])
    return [record]
