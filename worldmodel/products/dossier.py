"""``wm dossier``: everything the unified index holds about one entity, and where it stops.

The entity is resolved through the asserted identity clusters only. The dossier then inventories,
for every member of the cluster: its published descriptions, identifiers and aliases; every edge
grouped by predicate and by the dataset that published it; observations and events recorded
against it; the counterparties it reaches in one and two hops; and the rights of every dataset
that contributed. Every step is bounded, and every bound that bites is reported.
"""
import json
from pathlib import Path

from .base import (ProductError, Timer, bounded_count, cluster, connect, decode, default_index,
                   default_products_index, describe_edges, entity_records, envelope, index_info, labels,
                   members_map, canonical_map, record_sources, resolution_digest, retrying)
from .search import ALIAS_PREDICATES, open_products_index, products_index_info, resolve_input

IDENTITY_PREDICATES = ('identifier_assignment', 'identifier', 'identified_by', 'same_as')
#: Predicates whose object is a classification, a place, a list or a programme rather than a
#: counterparty. They are reported at hop 1 and never expanded: every Russian company is two hops
#: from every other through ``iso3:RUS``, which says nothing about either.
REFERENCE_PREDICATES = (
    'within', 'located_in', 'associated_country', 'citizenship', 'nationality', 'registered_in',
    'classified_as', 'subject_to_sanctions_program', 'listed_on', 'maps_to', 'flag_state',
    'party_affiliation', 'cabinet_member_party', 'mentioned_in_listing_narrative', 'listing_venue',
    'corresponds_to', 'conflict_in', 'describes_location', 'within_balancing_authority', 'published_by',
    'methodology_document', 'contiguous_with', 'part_of', 'contains_resource', 'issued_document_by',
    'referred_to_committee', 'serves_airport', 'mic_operating_venue', 'index_constituent')
#: Datasets the default unify profile leaves out, and what that means for a dossier.
OUT_OF_SCOPE = {
    'sec_13f_history': 'the full quarterly 13F holding history (only the sec_ownership_datasets filings are here)',
    'companies_house_uk': 'the UK company register and persons with significant control',
    'usaspending': 'federal contract transactions',
    'usaspending_assistance': 'federal assistance transactions',
    'gdelt_events': 'news-derived event records',
    'osm_topology': 'the OpenStreetMap routing graph', 'osm_us_south': 'OpenStreetMap US South extract',
    'osm_us_midwest': 'OpenStreetMap US Midwest extract', 'osm_us_northeast': 'OpenStreetMap US Northeast extract'}
SANCTION_PREFIXES = ('ofac:party:', 'us_csl:', 'uk:sanctions:', 'un:sanctions:', 'opensanctions:')


def _literal_assertions(connection, member, cap):
    rows = connection.execute("SELECT rowid, dataset, id, body FROM records WHERE subject=? AND kind='assertion' "
                              'AND object IS NULL LIMIT ?', (member, cap + 1)).fetchall()
    return rows[:cap], len(rows) > cap


def _identifier_values(body):
    """Normalised identifier strings a literal identity assertion carries."""
    value = body.get('value')
    out = set()
    if isinstance(value, str):
        out.add(value)
    elif isinstance(value, dict):
        if value.get('namespace') and value.get('value'):
            out.add('%s:%s' % (value['namespace'], value['value']))
        if isinstance(value.get('id'), str):
            out.add(value['id'])
        if value.get('number') and value.get('scheme'):
            out.add('%s=%s' % (value['scheme'], value['number']))
        if value.get('value') and not value.get('namespace'):
            out.add('%s=%s' % (value.get('scheme') or 'unnamed scheme', value['value']))
    return {item.strip() for item in out if item and item.strip()}


def _edge_groups(connection, members, *, per_group, exact_cap):
    """Every edge on the cluster, by (direction, predicate), with the datasets that published them."""
    groups = {}
    for member in members:
        for direction, column in (('out', 'subject'), ('in', 'object')):
            for row in connection.execute('SELECT predicate, COUNT(*) AS n FROM edges WHERE %s=? GROUP BY predicate'
                                          % column, (member,)):
                predicate, total = row['predicate'], row['n']
                group = groups.setdefault((direction, predicate), {'direction': direction, 'predicate': predicate,
                                                                   'edges': 0, 'by_dataset': {},
                                                                   'dataset_counts_exact': True, 'examples': []})
                group['edges'] += total
                datasets = connection.execute(
                    'SELECT r.dataset AS dataset, COUNT(*) AS n FROM (SELECT record_rowid FROM edges WHERE %s=? '
                    'AND predicate=? LIMIT ?) e JOIN records r ON r.rowid = e.record_rowid GROUP BY r.dataset'
                    % column, (member, predicate, exact_cap)).fetchall()
                for item in datasets:
                    group['by_dataset'][item['dataset']] = group['by_dataset'].get(item['dataset'], 0) + item['n']
                if total > exact_cap:
                    group['dataset_counts_exact'] = False
                if len(group['examples']) < per_group:
                    rows = connection.execute('SELECT rowid AS eid, * FROM edges WHERE %s=? AND predicate=? LIMIT ?'
                                              % column, (member, predicate, per_group - len(group['examples'])))
                    group['examples'].extend(describe_edges(connection, [dict(r) for r in rows]))
    ordered = sorted(groups.values(), key=lambda g: (-g['edges'], g['direction'], g['predicate']))
    for group in ordered:
        if not group['dataset_counts_exact']:
            group['dataset_counts_note'] = ('datasets counted over the first %d edges of each member; the total is exact'
                                            % exact_cap)
    return ordered


def _observations(connection, members, *, cap, examples):
    out, truncated = [], False
    for member in members:
        groups = connection.execute(
            'SELECT dataset, metric, COUNT(*) AS n, MIN(valid_from) AS first, MAX(valid_from) AS last FROM '
            "(SELECT dataset, metric, valid_from FROM records WHERE subject=? AND kind='observation' LIMIT ?) "
            'GROUP BY dataset, metric ORDER BY dataset, metric', (member, cap + 1)).fetchall()
        if sum(g['n'] for g in groups) > cap:
            truncated = True
        for group in groups[:examples]:
            latest = connection.execute(
                "SELECT id, body FROM records WHERE subject=? AND +kind='observation' AND +dataset=? AND +metric IS ? "
                'ORDER BY valid_from DESC LIMIT 1', (member, group['dataset'], group['metric'])).fetchone()
            body = decode(latest['body']) if latest else {}
            out.append({'subject': member, 'from_dataset': group['dataset'], 'metric': group['metric'],
                        'observations': group['n'], 'first_valid_from': group['first'],
                        'last_valid_from': group['last'],
                        'latest': {'value': body.get('value'), 'unit': body.get('unit'),
                                   'valid_from': body.get('valid_from'), 'dimensions': body.get('dimensions'),
                                   'missing_reason': body.get('missing_reason'),
                                   'record_id': latest['id'] if latest else None}})
        if len(groups) > examples:
            truncated = True
    return out, truncated


def _events(connection, members, *, examples):
    out = []
    for member in members:
        for group in connection.execute("SELECT dataset, COUNT(*) AS n, MIN(valid_from) AS first, MAX(valid_from) AS last "
                                        "FROM records WHERE subject=? AND kind='event' GROUP BY dataset", (member,)):
            rows = connection.execute("SELECT id, body FROM records WHERE subject=? AND kind='event' AND dataset=? "
                                      'ORDER BY observed_at DESC LIMIT ?', (member, group['dataset'], examples))
            out.append({'subject': member, 'from_dataset': group['dataset'], 'events': group['n'],
                        'first_valid_from': group['first'], 'last_valid_from': group['last'],
                        'examples': [{'record_id': r['id'], 'event_type': decode(r['body']).get('event_type'),
                                      'occurred_at': decode(r['body']).get('occurred_at')} for r in rows]})
    return out


def _incident(connection, nodes, *, per_node, exclude_reference=True, degree_cap):
    """Up to ``per_node`` non-reference edges on each node; hubs above ``degree_cap`` are skipped."""
    marks = ','.join('?' * len(REFERENCE_PREDICATES))
    edges, hubs = [], {}
    for node in nodes:
        degree, over = bounded_count(connection, 'SELECT 1 FROM edges WHERE subject=? UNION ALL '
                                                 'SELECT 1 FROM edges WHERE object=?', (node, node), degree_cap)
        if over:
            hubs[node] = degree
            continue
        for column in ('subject', 'object'):
            query = 'SELECT rowid AS eid, * FROM edges WHERE %s=?' % column
            args = [node]
            if exclude_reference:
                query += ' AND predicate NOT IN (%s)' % marks
                args.extend(REFERENCE_PREDICATES)
            edges.extend(dict(r) for r in connection.execute(query + ' LIMIT ?', (*args, per_node)))
    return edges, hubs


def _round_robin(nodes, count, key=lambda n: (-len(n['edges']), n['id'])):
    """The best-connected nodes, taken in turn from each predicate that reaches them.

    Ranking by edge count alone lets one prolific predicate (246 OpenSanctions ``owns`` edges)
    crowd out a single ``issuer_security`` edge that leads somewhere else entirely.
    """
    by_predicate = {}
    for node in sorted(nodes, key=key):
        by_predicate.setdefault(min(e['predicate'] for e in node['edges']), []).append(node)
    queues = [by_predicate[p] for p in sorted(by_predicate, key=lambda p: (-len(by_predicate[p]), p))]
    out, seen = [], set()
    while len(out) < count and any(queues):
        for queue in queues:
            while queue and queue[0]['id'] in seen:
                queue.pop(0)
            if queue and len(out) < count:
                node = queue.pop(0)
                seen.add(node['id'])
                out.append(node)
    return out


def _counterparties(connection, canonical_id, members, *, first, second, per_node, degree_cap):
    """Counterparties one and two hops out, through non-reference predicates, on asserted clusters."""
    own = set(members)
    marks = ','.join('?' * len(REFERENCE_PREDICATES))
    hop1_edges, reference = [], []
    for member in members:
        for column in ('subject', 'object'):
            hop1_edges.extend(dict(r) for r in connection.execute(
                'SELECT rowid AS eid, * FROM edges WHERE %s=? AND predicate NOT IN (%s) LIMIT ?' % (column, marks),
                (member, *REFERENCE_PREDICATES, per_node)))
            reference.extend(dict(r) for r in connection.execute(
                'SELECT rowid AS eid, * FROM edges WHERE %s=? AND predicate IN (%s) LIMIT 40' % (column, marks),
                (member, *REFERENCE_PREDICATES)))
    endpoints = {e['subject'] for e in hop1_edges} | {e['object'] for e in hop1_edges}
    canon = canonical_map(connection, endpoints)
    to_canonical = lambda x: canon.get(x, x)
    hop1 = {}
    for edge in hop1_edges:
        other = edge['object'] if edge['subject'] in own else edge['subject']
        if other in own or to_canonical(other) == canonical_id:
            continue
        node = hop1.setdefault(to_canonical(other), {'id': to_canonical(other), 'depth': 1, 'edges': []})
        node['edges'].append(edge)
    shown1 = _round_robin(hop1.values(), first)
    expand = [n['id'] for n in shown1[:40]]
    expand_members = members_map(connection, expand)
    hop2_edges, hubs = _incident(connection, sorted({m for ms in expand_members.values() for m in ms}),
                                 per_node=max(5, per_node // 8), degree_cap=degree_cap)
    canon.update(canonical_map(connection, {e['subject'] for e in hop2_edges} | {e['object'] for e in hop2_edges}))
    via = {m: node for node, ms in expand_members.items() for m in ms}
    hop2 = {}
    for edge in hop2_edges:
        near = edge['subject'] if edge['subject'] in via else edge['object']
        other = edge['object'] if near == edge['subject'] else edge['subject']
        target = to_canonical(other)
        if target == canonical_id or other in own or target in hop1:
            continue
        node = hop2.setdefault(target, {'id': target, 'depth': 2, 'via': set(), 'edges': []})
        node['via'].add(via[near])
        node['edges'].append(edge)
    ranked2 = _round_robin(hop2.values(), second, key=lambda n: (-len(n['via']), -len(n['edges']), n['id']))
    named = labels(connection, [n['id'] for n in shown1] + [n['id'] for n in ranked2])
    kinds = lambda edges: sorted({e['predicate'] for e in edges})

    def item(node):
        described = describe_edges(connection, node['edges'][:3])
        out = {'entity_id': node['id'], **named.get(node['id'], {}), 'depth': node['depth'],
               'connecting_edges': len(node['edges']), 'predicates': kinds(node['edges']),
               'from_datasets': sorted({e['from_dataset'] for e in described if e['from_dataset']}),
               'example_edges': described}
        if node['depth'] == 2:
            out['via'] = sorted(node['via'])[:5]
        return out

    return {'hop_1': [item(n) for n in shown1], 'hop_2': [item(n) for n in ranked2],
            'hop_1_total': len(hop1), 'hop_2_total_seen': len(hop2),
            'hop_1_truncated': len(hop1) > first, 'hop_2_truncated': len(hop2) > second,
            'hop_2_expanded_from': len(expand),
            'hubs_not_expanded': [{'entity_id': k, 'degree_at_least': v} for k, v in sorted(hubs.items())],
            'classifications_places_and_programs': describe_edges(connection, reference[:60]),
            'reference_predicates_not_expanded': list(REFERENCE_PREDICATES),
            'per_node_edge_bound': per_node}


def _identity(connection, members, literal):
    """Why the members are one cluster: identifier values they share, and same_as edges between them."""
    carriers = {}
    for member in members:
        carriers.setdefault(member, set()).add(member)
        for item in literal.get(member, []):
            if item['predicate'] in IDENTITY_PREDICATES:
                carriers[member].update(item['identifier_values'])
    shared = {}
    for member, values in carriers.items():
        for value in values:
            shared.setdefault(value, set()).add(member)
    basis = [{'identifier': value, 'carried_by': sorted(who)} for value, who in sorted(shared.items()) if len(who) > 1]
    same_as = []
    if len(members) > 1:
        marks = ','.join('?' * len(members))
        rows = connection.execute("SELECT rowid AS eid, * FROM edges WHERE predicate IN ('same_as', 'same_designation_as') "
                                  'AND subject IN (%s) AND object IN (%s)' % (marks, marks), (*members, *members))
        same_as = describe_edges(connection, [dict(r) for r in rows])
    return {'shared_identifier_values': basis, 'published_links_between_members': same_as,
            'note': ('Clusters are attached by "unify-resolve" from published same_as links, shared unique identifier '
                     'values and published crosswalk fields (GLEIF registration authority, the CUSIP inside a US ISIN). '
                     'The values above are the ones visible on the members\' own records; a link made by a crosswalk '
                     'field on a third record may not appear here.') if len(members) > 1 else
                    'This ID belongs to no asserted cluster: no publisher links it to any other ID in this index.'}


def dossier(query, *, index=None, products_index=None, pick=0, per_group=5, counterparties=40, second_hop=40,
            per_node=200, degree_cap=5000, literal_cap=3000, observation_cap=200000, data_root=None,
            catalog_root=None):
    """Assemble the dossier for an entity ID or a name. See the module docstring."""
    timer = Timer()
    index = Path(index or default_index(data_root))
    connection = connect(index)
    try:
        return retrying(_dossier, connection, index, query, timer=timer,
                        products_path=products_index or default_products_index(index), pick=pick, per_group=per_group,
                        counterparties=counterparties, second_hop=second_hop, per_node=per_node, degree_cap=degree_cap,
                        literal_cap=literal_cap, observation_cap=observation_cap, data_root=data_root,
                        catalog_root=catalog_root)
    finally:
        connection.close()


def _dossier(connection, index, query, *, timer, products_path, pick, per_group, counterparties, second_hop, per_node,
             degree_cap, literal_cap, observation_cap, data_root, catalog_root):
    with timer.step('open'):
        info = index_info(connection, index)
        at_start = resolution_digest(connection)
        products = open_products_index(products_path, info)
    common = dict(info=info, timer=timer, resolution_at_start=at_start, data_root=data_root, catalog_root=catalog_root)
    request = {'query': query, 'pick': pick}
    with timer.step('resolve'):
        resolved = resolve_input(connection, products, query)
    if resolved['kind'] == 'id':
        anchor, selection = resolved['entity_id'], {'basis': resolved['match_basis'], 'found_in': resolved['found_in']}
    elif resolved['kind'] == 'name':
        candidates = resolved['search']['candidates']
        if not 0 <= pick < len(candidates):
            raise ProductError('pick must be 0..%d for %r' % (len(candidates) - 1, query))
        anchor = candidates[pick]['canonical_id']
        selection = {'basis': 'the input is a name. The dossier is about candidate %d of %d, chosen by a text match on '
                              'published labels and aliases - INFERRED, not asserted. It may not be the entity you '
                              'meant; pass --pick N or an entity ID.' % (pick, len(candidates)),
                     'candidates': candidates}
    else:
        return envelope('dossier', query=request, answer={'found': False, 'resolution': resolved},
                        runs_out=['The input did not resolve to an entity in this index.'],
                        not_established=['Not finding an entity is not evidence that it does not exist, nor that '
                                         'nothing is published about it: the index covers the pinned datasets only.'],
                        resolution_at_end=resolution_digest(connection), extra={'products_index':
                                                                                products_index_info(products)},
                        **common)
    with timer.step('cluster'):
        canonical_id, members, asserted = cluster(connection, anchor)
        descriptions = entity_records(connection, members)
    literal, runs_out = {}, []
    with timer.step('identifiers'):
        identifiers, aliases, attributes, seen_identifiers = [], [], {}, {}
        for member in members:
            rows, truncated = _literal_assertions(connection, member, literal_cap)
            if truncated:
                runs_out.append('%s has more than %d literal assertions; only the first %d were read for identifiers '
                                'and aliases.' % (member, literal_cap, literal_cap))
            for row in rows:
                body = decode(row['body'])
                predicate = body.get('predicate')
                item = {'subject': member, 'predicate': predicate, 'value': body.get('value'),
                        'from_dataset': row['dataset'], 'record_id': row['id'],
                        'identifier_values': sorted(_identifier_values(body))}
                literal.setdefault(member, []).append(item)
                if predicate in IDENTITY_PREDICATES:
                    key = (member, predicate, json.dumps(item['value'], sort_keys=True, ensure_ascii=False),
                           row['dataset'])
                    if key in seen_identifiers:
                        seen_identifiers[key]['records'] += 1
                        continue
                    item['records'] = 1
                    seen_identifiers[key] = item
                    identifiers.append(item)
                elif predicate in ALIAS_PREDICATES:
                    aliases.append({k: item[k] for k in ('subject', 'value', 'from_dataset', 'record_id')})
                else:
                    group = attributes.setdefault((predicate, row['dataset']), {'predicate': predicate,
                                                                                 'from_dataset': row['dataset'],
                                                                                 'assertions': 0, 'examples': []})
                    group['assertions'] += 1
                    if len(group['examples']) < 3:
                        group['examples'].append({'subject': member, 'value': body.get('value'),
                                                  'record_id': row['id']})
        identity = _identity(connection, members, literal)
    with timer.step('edges'):
        edge_groups = _edge_groups(connection, members, per_group=per_group, exact_cap=5000)
    with timer.step('observations_and_events'):
        observations, obs_truncated = _observations(connection, members, cap=observation_cap, examples=60)
        events = _events(connection, members, examples=3)
    with timer.step('counterparties'):
        reach = _counterparties(connection, canonical_id, members, first=counterparties, second=second_hop,
                                per_node=per_node, degree_cap=degree_cap)
    with timer.step('labels'):
        others = sorted({e['object'] if e['subject'] in members else e['subject']
                         for g in edge_groups for e in g['examples']} - set(members))
        named = labels(connection, others[:200])
        for group in edge_groups:
            for edge in group['examples']:
                other = edge['object'] if edge['subject'] in members else edge['subject']
                edge['other_label'] = (named.get(other) or {}).get('label')
    answer = {
        'entity': {'query': query, 'selected': anchor, 'selection': selection, 'canonical_id': canonical_id,
                   **_title(descriptions, members)},
        'asserted_identity_cluster': {'members': members, 'asserted': asserted, 'size': len(members),
                                      'identity_basis': identity},
        'descriptions': [d for ds in descriptions.values() for d in ds],
        'identifiers': identifiers[:200],
        'aliases': aliases[:100],
        'other_literal_claims': sorted(attributes.values(), key=lambda g: -g['assertions'])[:60],
        'edges_by_predicate_and_dataset': edge_groups,
        'observations': observations,
        'events': events,
        'counterparties': reach,
        'counts': {'members': len(members), 'descriptions': sum(len(v) for v in descriptions.values()),
                   'identifier_claims': len(identifiers), 'aliases': len(aliases),
                   'edges': sum(g['edges'] for g in edge_groups), 'edge_predicates': len(edge_groups),
                   'observation_series': len(observations), 'event_groups': len(events),
                   'hop_1_counterparties': reach['hop_1_total'], 'hop_2_counterparties_seen': reach['hop_2_total_seen']},
    }
    runs_out.extend(_runs_out(info, members, descriptions, edge_groups, reach, obs_truncated, selection, literal))
    extra = {'products_index': products_index_info(products)}
    return envelope('dossier', query=request, answer=answer, runs_out=runs_out,
                    not_established=_not_established(members, edge_groups, descriptions, literal),
                    resolution_at_end=resolution_digest(connection), extra=extra, **common)


def _title(descriptions, members):
    for member in members:
        for item in descriptions.get(member, []):
            if item.get('label') and item['label'] != member:
                return {'label': item['label'], 'label_from': item['from_dataset'], 'entity_type': item.get('entity_type')}
    return {'label': None, 'label_from': None, 'entity_type': None}


def _has(members, prefix):
    return any(m.startswith(prefix) for m in members)


def _runs_out(info, members, descriptions, edge_groups, reach, obs_truncated, selection, literal):
    out = []
    predicates = {g['predicate'] for g in edge_groups}
    if len(members) == 1:
        out.append('No asserted identity links %s to any other ID. If other publishers describe the same real-world '
                   'entity without sharing an identifier, their records stay separate here.' % members[0])
    if 'candidates' in selection:
        out.append('The entity was chosen by a name match. %d other label-matching clusters were not merged with it, '
                   'because no publisher asserts they are the same.' % (len(selection['candidates']) - 1))
    if not any(descriptions.values()):
        out.append('No publisher describes this ID with an entity record; it appears only as an edge endpoint or a '
                   'subject.')
    if reach['hop_1_truncated'] or reach['hop_2_truncated']:
        out.append('Counterparties were truncated to the most-connected %d at hop 1 and %d at hop 2 (of %d and %d '
                   'seen).' % (len(reach['hop_1']), len(reach['hop_2']), reach['hop_1_total'], reach['hop_2_total_seen']))
    if reach['hubs_not_expanded']:
        out.append('%d hop-1 counterparties are hubs (more edges than the degree bound) and were not expanded to hop 2: '
                   '%s.' % (len(reach['hubs_not_expanded']),
                           ', '.join(h['entity_id'] for h in reach['hubs_not_expanded'][:8])))
    out.append('Nothing beyond two hops was examined, and classifications, places, lists and programmes were not '
               'expanded. For a longer chain use "wm serve" /graph/paths or "python3 -m worldmodel graph-paths".')
    if obs_truncated:
        out.append('Observation series were summarised over a bounded number of rows; see observations.')
    out.append('Events are found here only where the publisher anchors the event on this ID as its subject. Events that '
               'name the entity only among their participants (storm events, disaster declarations, AIS reports) are '
               'not reachable from an entity ID in this index.')
    if _has(members, 'lei:') and not predicates & {'directly_consolidated_by', 'ultimately_consolidated_by'}:
        out.append('GLEIF publishes no Level 2 consolidation relationship for this LEI in the pinned version (the entity '
                   'may have no parent, or may have filed a reporting exception).')
    if _has(members, 'lei:') and not _has(members, 'sec:cik:'):
        out.append('No asserted SEC filer identity: neither sec_issuer_reference nor GLEIF\'s SEC registration '
                   'authority (RA000665) links this LEI to a CIK.')
    if _has(members, 'sec:cik:') and not _has(members, 'lei:'):
        out.append('No asserted LEI for this SEC filer, so its GLEIF record and corporate group are not reachable.')
    if 'reported_holding' in predicates or _has(members, 'cusip:') or _has(members, 'isin:'):
        out.append('13F holdings here are the sec_ownership_datasets filings; the full quarterly history '
                   '(sec_13f_history) is outside this index.')
    missing = sorted(set(OUT_OF_SCOPE) - set(info['pinned_versions']))
    if missing:
        out.append('Outside this index scope entirely: ' + '; '.join('%s (%s)' % (d, OUT_OF_SCOPE[d]) for d in missing)
                   + '.')
    return out


def _not_established(members, edge_groups, descriptions, literal):
    predicates = {g['predicate'] for g in edge_groups}
    out = ['A dossier is an inventory of published claims about the IDs in one asserted cluster. It is not a profile '
           'anyone has verified, and a claim\'s presence is not evidence that it is true or current.',
           'An edge states only its predicate, as published by its dataset. Two entities two hops apart are not '
           'related in any stated way; a shared counterparty is not a relationship.']
    if _has(members, SANCTION_PREFIXES) or 'subject_to_sanctions_program' in predicates:
        out.append('Not a sanctions determination. A listing applies to the named party on the named list at the list '
                   'version pinned here; group membership does not transfer it, and OpenSanctions also carries '
                   'ownership, debarment and politically-exposed-person records that are not designations.')
    if predicates & {'directly_consolidated_by', 'ultimately_consolidated_by'}:
        out.append('GLEIF Level 2 records accounting consolidation, not control or beneficial ownership.')
    if 'reported_holding' in predicates:
        out.append('A 13F reported holding is what a manager had investment discretion over at a past quarter end: '
                   'long-only US-listed equity above the reporting threshold, not beneficial ownership and not a current '
                   'position.')
    types = {d.get('entity_type') for ds in descriptions.values() for d in ds}
    if 'person' in types:
        out.append('This entity is described as a natural person. Person-level records are retained only under each '
                   'source\'s declared rule (docs/use-policy.md); see rights.datasets[].identified_persons.')
    return out
