"""Read-only, bitemporal reference resolution over source-native assertions.

Periods are half-open. Canonical IDs are the lexicographically first visible ID
in an explicitly asserted equivalence component, never an inferred name match.
"""
from copy import deepcopy
from datetime import datetime, timezone

_UPPER = {'mic', 'ticker', 'lei', 'isin', 'bioguide', 'wikidata'}
_UNIQUE = {'mic', 'lei', 'isin', 'bioguide', 'wikidata', 'sec_cik'}


def schema():
    return {'entity_types': {},
            'relations': {'same_as': {'domain': 'entity', 'range': 'entity'},
                          'listed_as': {'domain': ['organization', 'security'], 'range': 'ticker_listing'},
                          'authorized_committee': {'domain': 'person', 'range': 'political_committee'}},
            'variables': {
                'identifier_assignment': {'domain': 'entity', 'unit': None, 'type': 'object'},
                'alias': {'domain': 'entity', 'unit': None, 'type': 'string'}}}


def namespace_rules():
    return {'namespace': 'strip whitespace and lowercase',
            'value': 'strip whitespace; unknown namespaces remain case-sensitive',
            'uppercase_values': sorted(_UPPER), 'decimal_values': ['sec_cik'],
            'unique_namespaces': sorted(_UNIQUE),
            'scope': 'optional, whitespace stripped and uppercase; omitted lookup searches all scopes',
            'ticker': 'not globally unique; confirmed validity requires both explicit bounds; otherwise return temporal_unknown candidates',
            'canonical_id': 'lexicographically first visible explicitly equivalent entity ID'}


def _normalize(namespace, value):
    if not isinstance(namespace, str) or not namespace.strip() or not isinstance(value, str) or not value.strip():
        raise ValueError('Identifier namespace and value must be nonempty strings')
    namespace, value = namespace.strip().lower(), value.strip()
    if namespace in _UPPER:
        value = value.upper()
    if namespace == 'sec_cik':
        if not value.isascii() or not value.isdigit():
            raise ValueError('sec_cik must contain decimal digits')
        value = str(int(value))
    return namespace, value


def _scope(value):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Identifier scope must be a nonempty string')
    return value.strip().upper()


MATCH_REVIEW_STATUSES = ('unreviewed', 'needs_review', 'accepted', 'rejected', 'source_asserted')


def is_equivalence(record, accept_matches='reviewed'):
    """Whether a same_as assertion joins identity components.

    Unmarked same_as claims (no ``match`` block, observed) keep their historical meaning.
    Inferred/probabilistic links join only when reviewed as accepted, or when a published
    mapping asserted them (``source_asserted``). ``accept_matches='all'`` is an explicit
    opt-in to union unreviewed inferred links; rejected links never join.
    """
    if accept_matches not in ('reviewed', 'all'):
        raise ValueError('accept_matches must be reviewed or all')
    match = record.get('match')
    status = match.get('reviewer_status', 'unreviewed') if isinstance(match, dict) else None
    if status == 'rejected':
        return False
    if match is None and record.get('epistemic_status') in (None, 'observed'):
        return True
    if status in ('accepted', 'source_asserted'):
        return True
    return accept_matches == 'all'


class IdentityIndex:
    def __init__(self, records, *, accept_matches='reviewed'):
        # Lazy imports keep schema() safe for ontology initialization.
        from .model import instant
        from .ontology import is_a
        self._instant = instant
        self._records = deepcopy(list(records))
        self._entities, self._assignments, self._equivalences, self._aliases = {}, [], [], []
        self._candidate_links = []
        self.accept_matches = accept_matches
        ids = set()
        for r in self._records:
            if r['id'] in ids:
                raise ValueError('Duplicate record ID: ' + r['id'])
            ids.add(r['id'])
            instant(r['observed_at'])
            for field in ('valid_from', 'valid_to'):
                if r.get(field) is not None:
                    instant(r[field])
            if r.get('valid_from') and r.get('valid_to') and instant(r['valid_from']) >= instant(r['valid_to']):
                raise ValueError('Invalid valid time interval')
            if r.get('kind') == 'entity':
                key = r.get('entity_id', r['id'])
                self._entities.setdefault(key, []).append(r)
            elif r.get('kind') == 'assertion':
                target = {'identifier_assignment': self._assignments, 'same_as': self._equivalences,
                          'alias': self._aliases}.get(r['predicate'])
                if r['predicate'] == 'same_as' and not is_equivalence(r, accept_matches):
                    target = self._candidate_links
                if target is not None:
                    target.append(r)
        for key, rows in self._entities.items():
            types = [r['entity_type'] for r in rows]
            if any(not (is_a(a, b) or is_a(b, a)) for a in types for b in types):
                raise ValueError('Conflicting entity types: ' + key)
        for r in self._assignments + self._equivalences + self._aliases:
            if r['subject'] not in self._entities:
                raise ValueError('Unresolved identity subject: ' + r['subject'])
        for r in self._assignments:
            value = r['value']
            _normalize(value['namespace'], value['value'])
            _scope(value.get('scope'))
        for r in self._aliases:
            if not isinstance(r.get('value'), str) or not r['value'].strip():
                raise ValueError('Alias requires a nonempty string')
        for r in self._equivalences:
            if r.get('object') not in self._entities:
                raise ValueError('Unresolved same_as object')
            for a in self._entities[r['subject']]:
                for b in self._entities[r['object']]:
                    if not (is_a(a['entity_type'], b['entity_type']) or is_a(b['entity_type'], a['entity_type'])):
                        raise ValueError('same_as entity type conflict')
        # Check complete active components, including paths through generic entities.
        boundaries = {datetime.min.replace(tzinfo=timezone.utc)}
        for r in self._equivalences + [x for rows in self._entities.values() for x in rows]:
            for field in ('valid_from', 'valid_to'):
                if r.get(field):
                    boundaries.add(instant(r[field]))
        for at in boundaries:
            groups, _ = self._components(at, datetime.max.replace(tzinfo=timezone.utc))
            components = {}
            for key, root in groups.items():
                components.setdefault(root, []).extend(r['entity_type'] for r in self._entities[key])
            for types in components.values():
                if any(not (is_a(a, b) or is_a(b, a)) for a in types for b in types):
                    raise ValueError('same_as transitive entity type conflict')
        self._validate_unique()

    def _visible(self, record, at, known):
        return (self._instant(record['observed_at']) <= known
                and (not record.get('valid_from') or self._instant(record['valid_from']) <= at)
                and (not record.get('valid_to') or at < self._instant(record['valid_to'])))

    def _components(self, at, known):
        entities = {key: rows for key, rows in self._entities.items()
                    if any(self._visible(r, at, known) for r in rows)}
        parents = {key: key for key in entities}
        def root(key):
            while parents[key] != key:
                key = parents[key]
            return key
        eqs = [r for r in self._equivalences if self._visible(r, at, known)
               and r['subject'] in parents and r['object'] in parents]
        for r in eqs:
            a, b = root(r['subject']), root(r['object'])
            parents[max(a, b)] = min(a, b)
        return {key: root(key) for key in parents}, eqs

    def _validate_unique(self):
        minimum = datetime.min.replace(tzinfo=timezone.utc)
        maximum = datetime.max.replace(tzinfo=timezone.utc)
        # A union over every equivalence (ignoring time) is only a conservative
        # candidate filter. Actual identity decisions still use both time axes.
        parents = {key: key for key in self._entities}
        def root(key):
            while parents[key] != key:
                parents[key] = parents[parents[key]]
                key = parents[key]
            return key
        for r in self._equivalences:
            a, b = root(r['subject']), root(r['object'])
            parents[max(a, b)] = min(a, b)
        ever = {key: root(key) for key in parents}
        relevant = {}
        for key, rows in self._entities.items():
            relevant.setdefault(ever[key], []).extend(rows)
        for r in self._equivalences:
            relevant[ever[r['subject']]].append(r)
        assignments, by_value, by_component = [], {}, {}
        for r in self._assignments:
            ns, value = _normalize(r['value']['namespace'], r['value']['value'])
            if ns not in _UNIQUE:
                continue
            scope = _scope(r['value'].get('scope'))
            pos = len(assignments)
            assignments.append((r, ns, value, scope))
            by_value.setdefault((ns, scope, value), []).append(pos)
            by_component.setdefault((ns, scope, ever[r['subject']]), []).append(pos)
        cache = {}
        for pos, (a, ns, av, scope) in enumerate(assignments):
            candidates = set(by_value[(ns, scope, av)])
            candidates.update(by_component[(ns, scope, ever[a['subject']])])
            for other in sorted(x for x in candidates if x > pos):
                b, _, bv, _ = assignments[other]
                if a['subject'] == b['subject'] and av == bv:
                    continue
                start = max(self._instant(r['valid_from']) if r.get('valid_from') else minimum for r in (a, b))
                end = min(self._instant(r['valid_to']) if r.get('valid_to') else maximum for r in (a, b))
                if start >= end:
                    continue
                known = max(self._instant(r['observed_at']) for r in (a, b))
                ats, knowns = {start}, {known}
                component_ids = {ever[a['subject']], ever[b['subject']]}
                for component_id in component_ids:
                    for r in relevant[component_id]:
                        for field in ('valid_from', 'valid_to'):
                            if r.get(field) and start <= self._instant(r[field]) < end:
                                ats.add(self._instant(r[field]))
                        if self._instant(r['observed_at']) >= known:
                            knowns.add(self._instant(r['observed_at']))
                for at in ats:
                    for cutoff in knowns:
                        key = (at, cutoff)
                        if key not in cache:
                            # Constructor-local and bounded: large histories must not
                            # retain one full entity map per distinct time cell.
                            if len(cache) >= 128:
                                cache.clear()
                            cache[key] = self._components(at, cutoff)[0]
                        groups = cache[key]
                        if a['subject'] not in groups or b['subject'] not in groups:
                            continue
                        same = groups[a['subject']] == groups[b['subject']]
                        if (av == bv and not same) or (av != bv and same):
                            raise ValueError('Contradictory unique identifier assignments: ' + a['id'] + ', ' + b['id'])

    def _match(self, canonical_id, groups, eqs, matched, at, known):
        members = sorted(key for key, root in groups.items() if root == canonical_id)
        rows = [r for key in members for r in self._entities[key] if self._visible(r, at, known)]
        rows += [r for r in eqs if r['subject'] in members]
        rows += matched
        evidence = {r['id']: r for r in rows}
        return {'canonical_id': canonical_id, 'entity_ids': members,
                'labels': sorted({r['label'] for r in rows if r['kind'] == 'entity'}),
                'evidence': deepcopy([evidence[key] for key in sorted(evidence)])}

    def resolve_identifier(self, namespace, value, at, known_at, *, scope=None):
        ns, val = _normalize(namespace, value)
        requested_scope = _scope(scope)
        at_time, known = self._instant(at), self._instant(known_at)
        groups, eqs = self._components(at_time, known)
        matches, unknown = {}, {}
        for r in self._assignments:
            v = r['value']
            if (_normalize(v['namespace'], v['value']) == (ns, val)
                    and (scope is None or _scope(v.get('scope')) == requested_scope)
                    and self._visible(r, at_time, known) and r['subject'] in groups):
                target = unknown if ns == 'ticker' and not (r.get('valid_from') and r.get('valid_to')) else matches
                target.setdefault(groups[r['subject']], []).append(r)
        def describe(rows_by_id):
            result = []
            for key in sorted(rows_by_id):
                rows = rows_by_id[key]
                item = self._match(key, groups, eqs, rows, at_time, known)
                item['temporal_validity'] = ('confirmed' if any(r.get('valid_from') and r.get('valid_to') for r in rows)
                                             else 'unknown')
                item['assignment_validity'] = [
                    {'record_id': r['id'], 'valid_from': r.get('valid_from'), 'valid_to': r.get('valid_to'),
                     'observed_at': r['observed_at'],
                     'temporal_validity': 'confirmed' if r.get('valid_from') and r.get('valid_to') else 'unknown'}
                    for r in rows]
                result.append(item)
            return result
        result, candidates = describe(matches), describe(unknown)
        all_ids = sorted(set(matches) | set(unknown))
        if not result:
            status = 'temporal_unknown' if candidates else 'not_found'
        else:
            status = 'ambiguous' if len(all_ids) > 1 else 'resolved'
        return {'namespace': ns, 'value': val, 'scope': requested_scope, 'at': at, 'known_at': known_at,
                'status': status, 'matches': result, 'temporal_unknown_candidates': candidates,
                'conflicts': ([{'reason': 'multiple_candidate_targets' if candidates else 'multiple_active_targets',
                                'canonical_ids': all_ids}] if len(all_ids) > 1 else [])}

    def candidate_links(self, entity_id, at, known_at):
        """Unaccepted inferred same_as links touching an entity (never unioned into components)."""
        at_time, known = self._instant(at), self._instant(known_at)
        return deepcopy(sorted((r for r in self._candidate_links if entity_id in (r['subject'], r.get('object'))
                                and self._visible(r, at_time, known)), key=lambda r: r['id']))

    def search(self, query, at, known_at, limit=20):
        if not isinstance(query, str) or isinstance(limit, bool) or not isinstance(limit, int) or limit < 0:
            raise ValueError('Search needs a string query and nonnegative integer limit')
        at_time, known = self._instant(at), self._instant(known_at)
        groups, eqs = self._components(at_time, known)
        query = query.strip().casefold()
        matches = {}
        for key in groups:
            rows = [r for r in self._entities[key] if self._visible(r, at_time, known)
                    and query in r['label'].casefold()]
            rows += [r for r in self._aliases if r['subject'] == key and self._visible(r, at_time, known)
                     and query in r['value'].casefold()]
            if rows:
                matches.setdefault(groups[key], []).extend(rows)
        return [self._match(key, groups, eqs, matches[key], at_time, known) for key in sorted(matches)[:limit]]
