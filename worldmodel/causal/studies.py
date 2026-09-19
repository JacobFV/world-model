"""Registered real-data studies: panel construction from the event library and outcome caches.

Each ``run_*`` function takes a loaded registration, its Git status, a ``Store`` on the data
root and a cache directory, reads only the pinned input versions the registration names, and
returns a list of result records (one per registered outcome).
"""
import math

from .panel import Panel
from .results import NOT_ESTIMABLE, build_result, evaluate_acceptance
from .sources import (extract_baci_imports, extract_bls_county, extract_cbp_county, library_events, load_baci_imports,
                      load_bls_county)
from .study import run_did_design


def pinned(registration, dataset):
    for ref in registration['data']['inputs']:
        if ref['dataset'] == dataset:
            if 'PIN' in ref['version']:
                raise ValueError(f'{dataset} is not pinned in the registration')
            return dict(ref)
    raise ValueError(f'registration does not declare {dataset}')


def _log_series(series, lo, hi):
    out = {}
    for period, value in series.items():
        year = int(period)
        if lo <= year <= hi and value is not None and value > 0:
            out[year] = math.log(value)
    return out


# -- Study 1: FEMA major disasters -> county employment and establishments ----------------------------

def fema_cohorts(store, library_ref, registration):
    treatment = registration['treatment']
    qualifying = set(treatment['qualifying_incident_types'])
    first, early, counts = {}, set(), {'declarations_read': 0, 'qualifying_county_incidents': 0}
    for event in library_events(store, library_ref, {'fema_major_disaster_declaration'}):
        counts['declarations_read'] += 1
        a = event['attributes']
        if a.get('incident_type') not in qualifying:
            continue
        fips = a['unit'].rsplit(':', 1)[1]
        if not fips.isdigit() or int(fips[:2]) > 56:
            continue
        year = int((a.get('incident_begin_date') or a['date'])[:4])
        counts['qualifying_county_incidents'] += 1
        if 1990 <= year <= 1994:
            early.add(fips)
        elif 1995 <= year <= 2024:
            first[fips] = min(first.get(fips, 9999), year)
    return first, early, counts


def fema_panel(first, early, series, lo, hi):
    outcomes, cohorts, strata = {}, {}, {}
    for fips, values in series.items():
        if not fips.isdigit() or len(fips) != 5 or fips.endswith('999') or int(fips[:2]) > 56 or fips in early:
            continue
        logged = _log_series(values, lo, hi)
        if not logged:
            continue
        outcomes[fips] = logged
        cohorts[fips] = first.get(fips)
        strata[fips] = fips[:2]
    return Panel(outcomes, cohorts, strata=strata, clusters=strata)


def run_fema(registration, status, store, cache_dir):
    lib = pinned(registration, 'event_library')
    bls = pinned(registration, 'bls_labor')
    cbp = pinned(registration, 'census_business')
    first, early, counts = fema_cohorts(store, lib, registration)
    lo, hi = registration['windows']['calendar_years']
    bls_cache = extract_bls_county(store, bls, cache_dir)
    cbp_cache = extract_cbp_county(store, cbp, cache_dir)
    treatment_facts = {**counts, 'counties_excluded_for_1990_1994_disasters': len(early),
                       'counties_first_treated_1995_2024': len(first)}
    outcomes = [registration['outcomes']['primary']] + registration['outcomes']['secondary']
    results = []
    for outcome in outcomes:
        if outcome['id'] == 'qcew_log_employment':
            series, provenance = load_bls_county(bls_cache['cache'], 'QCEW', 'employment'), bls_cache
        elif outcome['id'] == 'qcew_log_establishments':
            series, provenance = load_bls_county(bls_cache['cache'], 'QCEW', 'establishment_count'), bls_cache
        elif outcome['id'] == 'cbp_log_establishments':
            series, provenance = load_bls_county(cbp_cache['cache'], 'CBP', 'establishment_count'), cbp_cache
        else:
            raise ValueError(f'unknown outcome {outcome["id"]}')
        panel = fema_panel(first, early, series, lo, hi)
        reg = registration
        if outcome.get('windows'):
            reg = dict(registration, windows={**registration['windows'], **outcome['windows']},
                       placebo_tests={**registration['placebo_tests'],
                                      'placebo_date': {**registration['placebo_tests']['placebo_date'],
                                                       'shift': outcome.get('placebo_date_shift',
                                                                            registration['placebo_tests']['placebo_date']['shift'])}})
        role = 'primary' if outcome is outcomes[0] else 'secondary'
        results.append(run_did_design(panel, reg, status, outcome={**outcome, 'role': role},
                                      data={'inputs': [lib, bls, cbp], 'outcome_extraction': _prov(provenance),
                                            'treatment_facts': treatment_facts}))
    return results


def _prov(p):
    return {k: p[k] for k in ('input', 'spec', 'rows', 'sha256')}


# -- Study 2: MFN tariff increases -> imports -------------------------------------------------------

def tariff_universe(store, wits_ref):
    universe = set()
    for r in store.records(wits_ref):
        if r['kind'] != 'observation' or r.get('metric') != 'mfn_applied_tariff_simple_avg' or r['value'] is None:
            continue
        d = r['dimensions']
        if d.get('hs_revision') == 'HS2017' and r['valid_from'][:4] == '2018' and r['subject'].startswith('iso3:'):
            universe.add((r['subject'], d['product'].split(':', 1)[1]))
    return universe


def tariff_cohorts(store, library_ref, *, min_increase):
    changes = {}
    for event in library_events(store, library_ref, {'tariff_mfn_increase', 'tariff_mfn_decrease'}):
        a = event['attributes']
        if a.get('hs_revision') != 'HS2017':
            continue
        reporter, product = a['unit'].split('|')
        key = (reporter, product.split(':', 1)[1])
        year = int(a['date'][:4])
        change = a['intensity']['value']
        if key not in changes or year < changes[key][0]:
            changes[key] = (year, change)
    cohorts, excluded = {}, set()
    for key, (year, change) in changes.items():
        if change >= min_increase:
            cohorts[key] = year
        else:
            excluded.add(key)
    return cohorts, excluded


def run_tariff(registration, status, store, cache_dir):
    lib = pinned(registration, 'event_library')
    wits = pinned(registration, 'wits_trains_tariffs')
    baci = pinned(registration, 'cepii_baci')
    universe = tariff_universe(store, wits)
    cohorts, excluded = tariff_cohorts(store, lib, min_increase=2.0)
    importers = sorted({u[0] for u in universe})
    baci_cache = extract_baci_imports(store, baci, cache_dir, importers)
    flows = load_baci_imports(baci_cache['cache'])
    lo, hi = registration['windows']['calendar_years']
    span = registration['treatment']['estimated_cohorts']
    facts = {'universe_units': len(universe), 'units_with_first_change_increase_2pp': len(cohorts),
             'units_excluded_first_change_other': len(excluded), 'importers': len(importers)}
    results = []
    outcomes = [registration['outcomes']['primary']] + registration['outcomes']['secondary']
    for outcome in outcomes:
        values, cohort_map, strata, clusters = {}, {}, {}, {}
        sample, zero_post = 0, 0
        for unit in sorted(universe):
            if unit in excluded:
                continue
            base = [flows.get((unit[0], unit[1], y)) for y in (2017, 2018)]
            if any(b is None or b[0] <= 0 for b in base):
                continue
            series = {}
            for year in range(lo, hi + 1):
                f = flows.get((unit[0], unit[1], year))
                if f is None:
                    continue
                if outcome['id'] == 'baci_log_import_value':
                    v = f[0]
                elif outcome['id'] == 'baci_log_import_quantity':
                    v = f[1] if f[3] == 0 else None
                else:
                    raise ValueError(f'unknown outcome {outcome["id"]}')
                if v is not None and v > 0:
                    series[year] = math.log(v)
            if not series:
                continue
            sample += 1
            key = f'{unit[0]}|{unit[1]}'
            g = cohorts.get(unit)
            if g is not None and span[0] <= g <= span[1]:
                if any(flows.get((unit[0], unit[1], y), (0,))[0] <= 0 for y in range(g, min(hi, g + 3) + 1)):
                    zero_post += 1
            values[key], cohort_map[key] = series, g
            strata[key] = unit[0]
            clusters[key] = f'{unit[0]}|{unit[1][:2]}'
        panel = Panel(values, cohort_map, strata=strata, clusters=clusters)
        treated_estimated = sum(1 for k, g in cohort_map.items() if g is not None and span[0] <= g <= span[1])
        role = 'primary' if outcome is outcomes[0] else 'secondary'
        results.append(run_did_design(panel, registration, status, outcome={**outcome, 'role': role},
                                      data={'inputs': [lib, wits, baci], 'outcome_extraction': _prov(baci_cache),
                                            'treatment_facts': {**facts, 'sample_units': sample,
                                                                'extensive_margin_treated_with_zero_post_import':
                                                                    zero_post,
                                                                'treated_units_estimated': treated_estimated}}))
    return results


# -- Study 3: sanctions designations -> institutional holdings (feasibility-gated) ---------------------

CUSIP_ISIN_PREFIXES = ('US', 'CA', 'KY', 'BM', 'VG', 'PR', 'JE', 'GG', 'IM', 'LR', 'MH', 'PA', 'BS')


def sanctions_feasibility(store, library_ref, ofac_ref):
    designated = {}
    for event in library_events(store, library_ref, {'sanctions_designation'}):
        a = event['attributes']
        if a['source']['dataset'] != 'ofac_sanctions':
            continue
        party = a['unit']
        designated[party] = min(designated.get(party, '9999'), a['date'])
    securities = {}
    for r in store.records(ofac_ref):
        if r['kind'] != 'assertion' or r.get('predicate') != 'identifier' or r['subject'] not in designated:
            continue
        v = r.get('value') or {}
        scheme, number = v.get('scheme'), str(v.get('number') or v.get('value') or '')
        if scheme == 'ISIN' and number[:2] in CUSIP_ISIN_PREFIXES and len(number) == 12:
            securities.setdefault(r['subject'], set()).add('cusip:' + number[2:11])
        elif scheme == 'Equity Ticker' and number.endswith(' US'):
            securities.setdefault(r['subject'], set()).add('ticker:' + number[:-3])
    by_year = {}
    for party in securities:
        by_year[designated[party][:4]] = by_year.get(designated[party][:4], 0) + 1
    return {'designated_ofac_parties': len(designated), 'parties_with_13f_identifier': len(securities),
            'identifiers': sum(len(s) for s in securities.values()), 'by_designation_year': dict(sorted(by_year.items()))}


def run_sanctions(registration, status, store, cache_dir):
    lib = pinned(registration, 'event_library')
    ofac = pinned(registration, 'ofac_sanctions')
    facts = sanctions_feasibility(store, lib, ofac)
    gate = next(c for c in registration['acceptance_criteria'] if c['type'] == 'min_events')
    acceptance = evaluate_acceptance(registration['acceptance_criteria'],
                                     {'events': facts['parties_with_13f_identifier']})
    if facts['parties_with_13f_identifier'] >= gate['value']:
        raise NotImplementedError('feasibility gate passed; the outcome stage of this design is not implemented')
    verdict = (f'Not estimable as registered. Only {facts["parties_with_13f_identifier"]} of '
               f'{facts["designated_ofac_parties"]} OFAC-designated parties carry an identifier that can name a '
               f'13F-reportable security (gate: {gate["value"]}). The outcome data were not read. No sanctions '
               f'effect is identified by this repository.')
    record = build_result(study_id=registration['study_id'], registration=registration, registration_status=status,
                          identification_label=NOT_ESTIMABLE, verdict=verdict, estimates=None,
                          diagnostics={'feasibility': facts}, acceptance=acceptance,
                          data={'inputs': [lib, ofac], 'outcome_read': False},
                          assumptions=registration['assumptions'],
                          notes=['Alternatives considered and rejected are listed in the registration.'])
    return [record]


RUNNERS = {'fema_disasters_county_employment': run_fema, 'tariff_mfn_increases_imports': run_tariff,
           'sanctions_designations_institutional_holdings': run_sanctions}
