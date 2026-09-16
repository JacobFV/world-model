"""Temporal code systems and weighted, total-conserving crosswalks.

Everything here is explicit:

* A :class:`Crosswalk` row maps a source code to a target code with an optional
  weight, a weight basis (population, land area, employment, ...), an optional
  weight error and a half-open validity interval ``[valid_from, valid_to)``.
* :meth:`Crosswalk.apportion` moves extensive totals and reports the conservation
  residual, unmapped mass, rows split without published weights and a propagated
  error bound. It never silently drops values or invents weights.
* :meth:`Crosswalk.weighted_mean` handles intensive values (rates, prices) using a
  supplied basis instead of apportioning them.
* Code lookups are dated; reused codes (ISO ``CS``) and colliding abbreviations
  (ISO3 ``AUS`` = Australia vs COW ``AUS`` = Austria) are refused without a date or scheme.

Small official tables live in ``worldmodel/reference`` (see README.md there);
larger concordances are loaded from acquired files via :func:`load_concordance_csv`
and the loaders listed in ``reference/acquisition_declarations.json``.
"""
import csv
from datetime import date
import json
import math
from pathlib import Path

REFERENCE = Path(__file__).resolve().parent / 'reference'


class CrosswalkError(ValueError):
    """Raised for ambiguous, undated or non-conserving crosswalk operations."""


def _day(value):
    if value in (None, ''):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError as error:
        raise CrosswalkError('Invalid date: ' + str(value)) from error


def _active(row, at):
    if at is None:
        return True
    return (row['valid_from'] is None or row['valid_from'] <= at) and (row['valid_to'] is None or at < row['valid_to'])


def _read_csv(name):
    with (REFERENCE / name).open(encoding='utf-8', newline='') as stream:
        return list(csv.DictReader(stream))


def _number(value, label):
    if value in (None, ''):
        return None
    if isinstance(value, str):
        value = float(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise CrosswalkError(label + ' must be a nonnegative finite number')
    return float(value)


class Crosswalk:
    """Many-to-many mapping between two code systems with temporal validity."""

    def __init__(self, id, source_system, target_system, rows, *, weight_basis=None, source=None, licence=None,
                 notes=None, tolerance=1e-6):
        if not all(isinstance(x, str) and x for x in (id, source_system, target_system)):
            raise CrosswalkError('Crosswalk id and systems are required')
        self.id, self.source_system, self.target_system = id, source_system, target_system
        self.weight_basis, self.source, self.licence, self.notes, self.tolerance = weight_basis, source, licence, notes, tolerance
        self.rows, self._by_source = [], {}
        for raw in rows:
            row = {'source': str(raw['source']), 'target': str(raw['target']),
                   'weight': _number(raw.get('weight'), 'weight'),
                   'weight_error': _number(raw.get('weight_error'), 'weight_error'),
                   'valid_from': _day(raw.get('valid_from')), 'valid_to': _day(raw.get('valid_to')),
                   'basis': raw.get('basis') or weight_basis, 'note': raw.get('note')}
            if row['valid_from'] and row['valid_to'] and row['valid_from'] >= row['valid_to']:
                raise CrosswalkError('Invalid crosswalk validity interval')
            self.rows.append(row)
            self._by_source.setdefault(row['source'], []).append(row)
        self.temporal = any(r['valid_from'] or r['valid_to'] for r in self.rows)

    def __len__(self):
        return len(self.rows)

    def describe(self):
        split = sum(1 for rows in self._by_source.values() if len(rows) > 1)
        return {'id': self.id, 'source_system': self.source_system, 'target_system': self.target_system,
                'rows': len(self.rows), 'source_codes': len(self._by_source), 'split_source_codes': split,
                'unweighted_rows': sum(r['weight'] is None for r in self.rows), 'weight_basis': self.weight_basis,
                'temporal': self.temporal, 'source': self.source, 'licence': self.licence, 'notes': self.notes}

    def _at(self, at):
        at = _day(at)
        if at is None and self.temporal:
            raise CrosswalkError(f'{self.id} has dated rows; pass at= to select a vintage')
        return at

    def targets(self, code, at=None):
        at = self._at(at)
        rows = [r for r in self._by_source.get(str(code), []) if _active(r, at)]
        return [{k: (v.isoformat() if isinstance(v, date) else v) for k, v in r.items()} for r in rows]

    def check_partition(self, at=None):
        """Sources whose active weights do not sum to 1 (or lack weights while split)."""
        at = self._at(at)
        issues = []
        for code, rows in sorted(self._by_source.items()):
            active = [r for r in rows if _active(r, at)]
            if not active:
                continue
            weights = [r['weight'] for r in active]
            if len(active) == 1 and weights[0] is None:
                continue
            if any(w is None for w in weights):
                issues.append({'source': code, 'issue': 'split_without_weights', 'targets': [r['target'] for r in active]})
            elif abs(math.fsum(weights) - 1) > self.tolerance:
                issues.append({'source': code, 'issue': 'weights_do_not_sum_to_one', 'sum': math.fsum(weights)})
        return issues

    def _weights(self, code, active, weights, unweighted):
        if weights is not None and code in weights:
            supplied = weights[code]
            if set(supplied) != {r['target'] for r in active}:
                raise CrosswalkError(f'Supplied weights for {code} must cover exactly its active targets')
            return [(r, _number(supplied[r['target']], 'weight'), 'supplied', None) for r in active], None
        if len(active) == 1:
            row = active[0]
            weight = 1.0 if row['weight'] is None else row['weight']
            return [(row, weight, 'single_target' if row['weight'] is None else 'declared', row['weight_error'])], None
        if any(r['weight'] is None for r in active):
            if unweighted == 'equal':
                share = 1 / len(active)
                # Worst case: all mass belongs to one target, so each allocation may be off by max(share, 1-share).
                return [(r, share, 'equal_split_assumption', max(share, 1 - share)) for r in active], 'equal_split'
            raise CrosswalkError(f'{self.id}: {code} splits to {len(active)} targets without weights; supply weights= '
                                 'or unweighted="equal" to accept a documented equal-split assumption')
        return [(r, r['weight'], 'declared', r['weight_error']) for r in active], None

    def apportion(self, values, at=None, *, weights=None, unweighted='error', unmapped='error', normalize=True):
        """Allocate extensive totals {source_code: amount} to target codes, conserving totals.

        weights: optional {source: {target: weight}} overriding declared weights.
        unweighted: 'error' (default) or 'equal' for split rows lacking weights.
        unmapped: 'error' (default) or 'report' to keep unmapped mass out of the result.
        normalize: rescale declared weights that do not sum to 1 and report the deviation.
        """
        at = self._at(at)
        if unweighted not in ('error', 'equal') or unmapped not in ('error', 'report'):
            raise CrosswalkError('Invalid apportion policy')
        totals, allocations, missing, assumptions, normalized = {}, [], {}, [], []
        error_bound = 0.0
        amounts_in = []
        for code, amount in sorted(values.items()):
            amount = float(amount)
            if not math.isfinite(amount):
                raise CrosswalkError('Values must be finite')
            amounts_in.append(amount)
            active = [r for r in self._by_source.get(str(code), []) if _active(r, at)]
            if not active:
                if unmapped == 'error':
                    raise CrosswalkError(f'{self.id}: no active mapping for {code} at {at}')
                missing[str(code)] = amount
                continue
            chosen, assumption = self._weights(str(code), active, weights, unweighted)
            if assumption:
                assumptions.append({'source': str(code), 'assumption': assumption, 'targets': [r['target'] for r in active]})
            weight_sum = math.fsum(w for _, w, _, _ in chosen)
            if weight_sum <= 0:
                raise CrosswalkError(f'{self.id}: weights for {code} sum to zero')
            if abs(weight_sum - 1) > self.tolerance:
                if not normalize:
                    raise CrosswalkError(f'{self.id}: weights for {code} sum to {weight_sum}')
                normalized.append({'source': str(code), 'weight_sum': weight_sum, 'deviation': weight_sum - 1})
            for row, weight, how, weight_error in chosen:
                share = weight / weight_sum
                allocated = amount * share
                totals[row['target']] = totals.get(row['target'], 0.0) + allocated
                if weight_error is not None:
                    error_bound += abs(amount) * weight_error
                allocations.append({'source': str(code), 'target': row['target'], 'share': share, 'amount': allocated,
                                    'weight_source': how, 'basis': row['basis'], 'weight_error': weight_error})
        total_in, total_out, total_missing = math.fsum(amounts_in), math.fsum(totals.values()), math.fsum(missing.values())
        residual = total_in - total_out - total_missing
        if abs(residual) > max(1e-9, 1e-9 * abs(total_in)):
            raise CrosswalkError(f'{self.id}: conservation failed (residual {residual})')
        return {'crosswalk': self.id, 'at': at.isoformat() if at else None, 'measure': 'extensive',
                'values': dict(sorted(totals.items())), 'total_in': total_in, 'total_out': total_out,
                'unmapped': missing, 'unmapped_total': total_missing, 'conservation_residual': residual,
                'error_bound': error_bound, 'assumptions': assumptions, 'normalized_sources': normalized,
                'weight_basis': self.weight_basis, 'allocations': allocations}

    def weighted_mean(self, values, basis, at=None, *, weights=None, unweighted='error'):
        """Map intensive values {source: rate} using basis sizes {source: size} (e.g. population)."""
        at = self._at(at)
        numerator, denominator, used = {}, {}, []
        for code, value in sorted(values.items()):
            if code not in basis:
                raise CrosswalkError(f'Missing basis size for {code}; intensive values cannot be apportioned')
            active = [r for r in self._by_source.get(str(code), []) if _active(r, at)]
            if not active:
                raise CrosswalkError(f'{self.id}: no active mapping for {code}')
            chosen, _ = self._weights(str(code), active, weights, unweighted)
            weight_sum = math.fsum(w for _, w, _, _ in chosen)
            for row, weight, how, _ in chosen:
                mass = float(basis[code]) * weight / weight_sum
                numerator[row['target']] = numerator.get(row['target'], 0.0) + mass * float(value)
                denominator[row['target']] = denominator.get(row['target'], 0.0) + mass
                used.append({'source': str(code), 'target': row['target'], 'basis_mass': mass, 'weight_source': how})
        return {'crosswalk': self.id, 'at': at.isoformat() if at else None, 'measure': 'intensive',
                'values': {t: numerator[t] / denominator[t] for t in sorted(numerator) if denominator[t] > 0},
                'basis_totals': dict(sorted(denominator.items())), 'contributions': used}

    def compose(self, other, *, id=None):
        """Chain self (A->B) with other (B->C). Weights multiply; unknown weights stay unknown."""
        if self.target_system != other.source_system:
            raise CrosswalkError('Crosswalk systems do not chain')
        rows = []
        for row in self.rows:
            for nxt in other._by_source.get(row['target'], []):
                start = max([d for d in (row['valid_from'], nxt['valid_from']) if d], default=None)
                end = min([d for d in (row['valid_to'], nxt['valid_to']) if d], default=None)
                if start and end and start >= end:
                    continue
                single_a = len(self._by_source[row['source']]) == 1
                single_b = len(other._by_source[nxt['source']]) == 1
                wa = row['weight'] if row['weight'] is not None else (1.0 if single_a else None)
                wb = nxt['weight'] if nxt['weight'] is not None else (1.0 if single_b else None)
                rows.append({'source': row['source'], 'target': nxt['target'],
                             'weight': None if wa is None or wb is None else wa * wb,
                             'valid_from': start, 'valid_to': end, 'basis': ' x '.join(filter(None, (row['basis'], nxt['basis']))) or None,
                             'note': f"via {row['target']}"})
        merged = {}
        for r in rows:
            key = (r['source'], r['target'], r['valid_from'], r['valid_to'])
            if key in merged:
                old = merged[key]
                old['weight'] = None if old['weight'] is None or r['weight'] is None else old['weight'] + r['weight']
                old['note'] += '; ' + r['note']
            else:
                merged[key] = r
        return Crosswalk(id or f'{self.id}+{other.id}', self.source_system, other.target_system, merged.values(),
                         source=[self.source, other.source], licence=[self.licence, other.licence],
                         notes='composed; unweighted splits propagate as unknown weights')


def load_concordance_csv(path, *, id, source_system, target_system, source_column, target_column,
                         weight_column=None, weight_basis=None, source=None, licence=None, delimiter=',',
                         encoding='utf-8-sig', normalize_code=None):
    """Load an acquired concordance file (HS↔SITC, HS↔NAICS, BEA↔NAICS, ZCTA↔county, ...)."""
    rows = []
    with Path(path).open(encoding=encoding, newline='') as stream:
        for record in csv.DictReader(stream, delimiter=delimiter):
            a, b = record.get(source_column, ''), record.get(target_column, '')
            if not a or not b:
                continue
            if normalize_code:
                a, b = normalize_code(a), normalize_code(b)
            rows.append({'source': a.strip(), 'target': b.strip(),
                         'weight': record.get(weight_column) if weight_column else None})
    return Crosswalk(id, source_system, target_system, rows, weight_basis=weight_basis, source=source, licence=licence)


def zcta_county_crosswalk(path, *, weight='land_area'):
    """Census 2020 ZCTA→county relationship file with land-area weights.

    Land-area shares are a documented approximation for population-based apportionment;
    each row's weight_error is set to max(share, 1-share) as a worst-case bound unless the
    ZCTA lies entirely in one county (error 0).
    """
    if weight != 'land_area':
        raise CrosswalkError('Only land_area weights can be derived from the relationship file; supply population weights separately')
    parts = {}
    with Path(path).open(encoding='utf-8-sig', newline='') as stream:
        for record in csv.DictReader(stream, delimiter='|'):
            zcta, county = record['GEOID_ZCTA5_20'], record['GEOID_COUNTY_20']
            if not zcta or not county:
                continue
            parts.setdefault(zcta, []).append((county, float(record['AREALAND_PART'] or 0)))
    rows = []
    for zcta, items in parts.items():
        total = math.fsum(a for _, a in items)
        for county, area in items:
            share = area / total if total > 0 else 1 / len(items)
            rows.append({'source': zcta, 'target': county, 'weight': share,
                         'weight_error': 0.0 if len(items) == 1 else max(share, 1 - share)})
    return Crosswalk('census_zcta520_county20_land_area', 'zcta5_2020', 'county_2020', rows, weight_basis='land_area_2020',
                     source='https://www2.census.gov/geo/docs/maps-data/data/rel2020/zcta520/tab20_zcta520_county20_natl.txt',
                     licence='US Census Bureau; public domain')


# ---------------------------------------------------------------------------
# Countries


_SCHEMES = ('iso2', 'iso3', 'iso_numeric', 'm49', 'world_bank', 'cow', 'cow_abbrev', 'gw', 'gw_abbrev')


class CountryCodes:
    """Dated country identifiers across ISO 3166, UN M49, World Bank, COW and Gleditsch-Ward."""

    def __init__(self):
        self.iso = []
        for row in _read_csv('countries_iso3166.csv'):
            self.iso.append({**row, 'valid_from': _day(row['valid_from']), 'valid_to': _day(row['valid_to'])})
        self.states = {'cow': [], 'gw': []}
        for system, name, code in (('cow', 'country_states_cow.csv', 'ccode'), ('gw', 'country_states_gw.csv', 'gwcode')):
            for row in _read_csv(name):
                self.states[system].append({'code': row[code], 'abbrev': row['abbrev'], 'name': row['name'],
                                            'valid_from': _day(row['valid_from']), 'valid_to': _day(row['valid_to'])})
        self.links = [{**row, 'valid_from': _day(row['valid_from']), 'valid_to': _day(row['valid_to'])}
                      for row in _read_csv('country_system_links.csv')]

    @staticmethod
    def _out(row, scheme, value, **extra):
        clean = {k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()}
        return {'scheme': scheme, 'value': value, **clean, **extra}

    def lookup(self, value, scheme=None, at=None):
        """Return matching dated records; raises if no scheme is given and several schemes match."""
        if not isinstance(value, str) or not value.strip():
            raise CrosswalkError('Country code must be a nonempty string')
        value, at = value.strip(), _day(at)
        if scheme is None:
            hits = {s: self.lookup(value, s, at) for s in _SCHEMES}
            hits = {s: h for s, h in hits.items() if h}
            if len(hits) > 1 and len({tuple(x.get('iso3') for x in h) for h in hits.values()}) > 1:
                raise CrosswalkError(f'Ambiguous country code {value!r}: matches schemes {sorted(hits)}; pass scheme=')
            return next(iter(hits.values()), [])
        if scheme not in _SCHEMES:
            raise CrosswalkError('Unknown country scheme: ' + str(scheme))
        if scheme in ('iso2', 'iso3', 'world_bank'):
            column = scheme
            rows = [r for r in self.iso if r[column] and r[column].upper() == value.upper() and _active(r, at)]
            return [self._out(r, scheme, value) for r in rows]
        if scheme in ('iso_numeric', 'm49'):
            if not value.isdigit():
                return []
            rows = [r for r in self.iso if r[scheme] and int(r[scheme]) == int(value) and _active(r, at)]
            return [self._out(r, scheme, value) for r in rows]
        system = scheme.split('_')[0]
        field = 'abbrev' if scheme.endswith('abbrev') else 'code'
        states = [s for s in self.states[system] if s[field].upper() == value.upper() and _active(s, at)]
        out = []
        for state in states:
            links = [l for l in self.links if l['system'] == system and l['code'] == state['code'] and _active(l, at)]
            if at is None and len({l['iso3'] for l in links}) > 1:
                links = [{'iso3': None, 'basis': 'undated_multiple_iso_links', 'valid_from': None, 'valid_to': None}]
            for link in links or [{'iso3': None, 'basis': 'no_link', 'valid_from': None, 'valid_to': None}]:
                out.append(self._out({**state, 'state_valid_from': state['valid_from'], 'state_valid_to': state['valid_to']},
                                     scheme, value, iso3=link['iso3'] or None, link_basis=link['basis'],
                                     link_valid_from=link['valid_from'].isoformat() if link['valid_from'] else None,
                                     link_valid_to=link['valid_to'].isoformat() if link['valid_to'] else None))
        return out

    def convert(self, value, from_scheme, to_scheme, at=None):
        """Translate a code; returns {'status': resolved|no_equivalent|not_found, 'value', 'evidence'} or raises if ambiguous."""
        if to_scheme not in _SCHEMES:
            raise CrosswalkError('Unknown target scheme')
        at = _day(at)
        matches = self.lookup(value, from_scheme, at)
        if not matches:
            return {'status': 'not_found', 'value': None, 'from': value, 'from_scheme': from_scheme, 'at': at and at.isoformat()}
        iso3s = {m.get('iso3') for m in matches}
        if len(iso3s) != 1 or (None in iso3s and len(matches) > 1):
            raise CrosswalkError(f'Ambiguous {from_scheme} {value!r}' + (' without a date; pass at=' if at is None else f' at {at}')
                                 + f': {sorted(str(x) for x in iso3s)}')
        iso3 = iso3s.pop()
        if iso3 is None:
            return {'status': 'no_equivalent', 'value': None, 'from': value, 'from_scheme': from_scheme,
                    'at': at and at.isoformat(), 'evidence': matches}
        if to_scheme in ('iso3',):
            return {'status': 'resolved', 'value': iso3, 'from': value, 'from_scheme': from_scheme,
                    'at': at and at.isoformat(), 'evidence': matches}
        if to_scheme in ('cow', 'gw', 'cow_abbrev', 'gw_abbrev'):
            system = to_scheme.split('_')[0]
            links = [l for l in self.links if l['system'] == system and l['iso3'] == iso3 and _active(l, at)]
            codes = set()
            for link in links:
                states = [s for s in self.states[system] if s['code'] == link['code'] and _active(s, at)]
                codes.update(s['abbrev' if to_scheme.endswith('abbrev') else 'code'] for s in states)
        else:
            iso_rows = [r for r in self.iso if r['iso3'] == iso3 and _active(r, at)]
            column = to_scheme
            codes = {r[column] for r in iso_rows if r[column]}
        if not codes:
            return {'status': 'no_equivalent', 'value': None, 'from': value, 'from_scheme': from_scheme,
                    'iso3': iso3, 'at': at and at.isoformat(), 'evidence': matches}
        if len(codes) > 1:
            raise CrosswalkError(f'Ambiguous {to_scheme} for {iso3}' + (' without a date' if at is None else '') + f': {sorted(codes)}')
        return {'status': 'resolved', 'value': codes.pop(), 'iso3': iso3, 'from': value, 'from_scheme': from_scheme,
                'at': at and at.isoformat(), 'evidence': matches}


# ---------------------------------------------------------------------------
# US geography


class UsGeography:
    """State/county FIPS with dated county changes (Census substantial-change pages)."""

    def __init__(self):
        self.states = _read_csv('us_states.csv')
        self._state_by = {}
        for row in self.states:
            for key in (row['fips'], row['usps'], row['name'].casefold()):
                self._state_by[key] = row
        self.counties = [{**r, 'valid_from': _day(r['valid_from']), 'valid_to': _day(r['valid_to'])} for r in _read_csv('us_counties.csv')]
        self._county_by = {}
        for row in self.counties:
            self._county_by.setdefault(row['geoid'], []).append(row)
        self.changes = [{**r, 'effective': _day(r['effective'])} for r in _read_csv('us_county_changes.csv')]
        self.ct = _read_csv('ct_county_planning_region_cousub.csv')

    def state(self, value):
        row = self._state_by.get(value if len(str(value)) <= 2 else str(value).casefold())
        if row is None and isinstance(value, str):
            row = self._state_by.get(value.zfill(2)) or self._state_by.get(value.upper())
        if row is None:
            raise CrosswalkError('Unknown state: ' + str(value))
        return dict(row)

    def county(self, geoid, at=None):
        """County record active at ``at``; undated lookups return the record with its validity."""
        geoid = str(geoid).zfill(5)
        rows = [r for r in self._county_by.get(geoid, []) if _active(r, _day(at))]
        if not rows:
            return {'geoid': geoid, 'status': 'not_active' if geoid in self._county_by else 'not_found',
                    'changes': self.changes_for(geoid)}
        row = rows[0]
        return {**{k: (v.isoformat() if isinstance(v, date) else v) for k, v in row.items()}, 'status': 'active',
                'changes': self.changes_for(geoid)}

    def changes_for(self, geoid):
        return [{**c, 'effective': c['effective'].isoformat()} for c in self.changes if geoid in (c['from_geoid'], c['to_geoid'])]

    def county_crosswalk(self, start, end):
        """Crosswalk from county codes valid at ``start`` to codes valid at ``end`` (start < end).

        recode/merge rows are exact (weight 1). Splits use the published populations as weights
        (basis recorded). Partial transfers (part_to_new, boundary) and Connecticut's 2022 system
        replacement lack published county-level weights: those rows carry weight None and
        apportion() refuses them unless weights are supplied.
        """
        a, b = _day(start), _day(end)
        if not a or not b or a >= b:
            raise CrosswalkError('county_crosswalk needs start < end')
        events = [c for c in self.changes if a < c['effective'] <= b]
        rows, touched = [], set()
        by_source = {}
        for event in events:
            if event['event'] == 'replace_system':
                continue
            by_source.setdefault(event['from_geoid'], []).append(event)
        for source, items in by_source.items():
            touched.add(source)
            kinds = {e['event'] for e in items}
            if kinds <= {'recode', 'merge'} and len(items) == 1:
                rows.append({'source': source, 'target': items[0]['to_geoid'], 'weight': 1, 'note': items[0]['note']})
            elif kinds == {'split'} and all(e['population'] for e in items):
                total = sum(float(e['population']) for e in items)
                for e in items:
                    rows.append({'source': source, 'target': e['to_geoid'], 'weight': float(e['population']) / total,
                                 'basis': e['population_basis'], 'note': e['note']})
            elif kinds <= {'merge'} and all(e['population'] for e in items):
                total = sum(float(e['population']) for e in items)
                for e in items:
                    rows.append({'source': source, 'target': e['to_geoid'],
                                 'weight': float(e['population']) / total if total else None,
                                 'basis': e['population_basis'], 'note': e['note']})
            else:
                targets = {e['to_geoid'] for e in items}
                if self.county(source, b)['status'] == 'active':
                    targets.add(source)
                for target in sorted(targets):
                    rows.append({'source': source, 'target': target, 'weight': None,
                                 'note': 'partial transfer; county-level weights not published: ' + '; '.join(e['note'] for e in items)})
        if any(e['event'] == 'replace_system' for e in events):
            pairs = {}
            for r in self.ct:
                pairs.setdefault(r['old_county'], set()).add(r['new_county'])
            for old, news in pairs.items():
                touched.add(old)
                for new in sorted(news):
                    rows.append({'source': old, 'target': new, 'weight': 1 if len(news) == 1 else None,
                                 'note': 'Connecticut planning regions; apportion via county subdivisions (ct_cousub_crosswalk)'})
        for row in self.counties:
            if row['geoid'] not in touched and _active(row, a) and _active(row, b):
                rows.append({'source': row['geoid'], 'target': row['geoid'], 'weight': 1})
        return Crosswalk(f'us_county_{a.isoformat()}_{b.isoformat()}', f'county@{a.isoformat()}', f'county@{b.isoformat()}',
                         rows, source='US Census Bureau substantial county changes; CT county subdivision crosswalk',
                         licence='public domain', weight_basis='published change populations where available')

    def ct_cousub_crosswalk(self):
        """Exact Connecticut county-subdivision (town) recode from old county-based to planning-region GEOIDs."""
        return Crosswalk('ct_cousub_2020_2022', 'county_subdivision@2020', 'county_subdivision@2022',
                         [{'source': r['old_cousub_geoid'], 'target': r['new_cousub_geoid'], 'weight': 1} for r in self.ct],
                         source='https://www2.census.gov/geo/docs/reference/ct_change/ct_cou_to_cousub_crosswalk.xlsx',
                         licence='public domain', weight_basis='identity (same town, new GEOID)')

    def ct_region_of_cousub(self):
        return {r['new_cousub_geoid']: r['new_county'] for r in self.ct}


# ---------------------------------------------------------------------------
# Industry classifications

_NAICS_FILES = {(2012, 2017): 'naics_2012_2017.csv', (2017, 2022): 'naics_2017_2022.csv'}
_NAICS_SECTOR_RANGES = {'31': '31-33', '32': '31-33', '33': '31-33', '44': '44-45', '45': '44-45', '48': '48-49', '49': '48-49'}


def naics_concordance(from_year, to_year):
    """Census NAICS six-digit concordances. Splits have unknown weights (Census publishes none)."""
    if (from_year, to_year) in _NAICS_FILES:
        rows = [{'source': r['source'], 'target': r['target'], 'note': r['source_piece_title']}
                for r in _read_csv(_NAICS_FILES[(from_year, to_year)])]
        return Crosswalk(f'naics_{from_year}_{to_year}', f'naics{from_year}', f'naics{to_year}', rows,
                         source='https://www.census.gov/naics/concordances/', licence='US Census Bureau; public domain',
                         notes='Relationship-only concordance; supply employment/receipts weights for split industries')
    if (from_year, to_year) == (2012, 2022):
        return naics_concordance(2012, 2017).compose(naics_concordance(2017, 2022), id='naics_2012_2022')
    raise CrosswalkError('Supported NAICS concordances: 2012->2017, 2017->2022, 2012->2022')


_NAICS_CODES = None


def naics_2022_codes():
    global _NAICS_CODES
    if _NAICS_CODES is None:
        _NAICS_CODES = {r['code']: r['title'] for r in _read_csv('naics_2022_codes.csv')}
    return dict(_NAICS_CODES)


def naics_parent(code, level):
    """Aggregate a NAICS code to 2..6 digits, honoring sector ranges 31-33, 44-45, 48-49."""
    code = str(code)
    if not code.isdigit() or not 2 <= len(code) <= 6 or not 2 <= level <= len(code):
        raise CrosswalkError('NAICS code must be 2-6 digits and level <= code length')
    prefix = code[:level]
    return _NAICS_SECTOR_RANGES.get(prefix, prefix) if level == 2 else prefix


def naics_aggregate_crosswalk(codes, level):
    return Crosswalk(f'naics_to_{level}digit', 'naics', f'naics_{level}digit',
                     [{'source': c, 'target': naics_parent(c, level), 'weight': 1} for c in codes],
                     weight_basis='hierarchy (exact)')


def acquisition_declarations():
    """Declarations for reference datasets too large (or restricted) to track in the repository."""
    return json.loads((REFERENCE / 'acquisition_declarations.json').read_text(encoding='utf-8'))


def reference_manifest():
    return json.loads((REFERENCE / 'manifest.json').read_text(encoding='utf-8'))
