"""``wm screen``: sanctions and ownership proximity for a supplied list of names or IDs.

For each input the screen finds the shortest *asserted* paths, within N hops over ownership and
control predicates, to any entity on a restrictive list in the index: OFAC, the US Consolidated
Screening List, the UK and UN lists, and OpenSanctions targets. The search is a bounded
breadth-first search over asserted identity clusters, with the same conventions as
``Graph.paths(resolved=True)`` (cluster members are expanded together, endpoints are reported by
canonical ID, every step names its supporting edge and the dataset that published it, and all
shortest paths are kept). It differs from ``Graph.paths`` in two ways that screening needs: it
has many targets (every listed cluster) rather than one, and it honours a direction per predicate,
so that a 13F manager reaches the *issuer* of a security it holds but not the thousands of other
managers holding the same security, which is not a relationship between them.

Absence of a path is not clearance. The answer says so for every input, and names the coverage
gaps that make a missing path uninformative.
"""
from pathlib import Path

from .base import (ProductError, Timer, canonical_map, chunks, cluster, connect, decode, default_index,
                   default_products_index, envelope, index_info, labels, members_map, record_sources,
                   resolution_digest, retrying)
from .search import open_products_index, products_index_info, resolve_input

PREDICATE_GROUPS = {
    'ownership': ('owns', 'controls', 'directly_consolidated_by', 'ultimately_consolidated_by',
                  'parent_reporting_exception', 'subfund_of', 'feeder_fund_of', 'fund_managed_by', 'branch_of',
                  'international_branch_of', 'regulatory_high_holder', 'property_in_interest_of'),
    'control': ('director_of', 'insider_of', 'significant_role_in', 'significant_control_over',
                'principal_executive_officer_of', 'leader_or_official_of', 'acts_for_or_on_behalf_of'),
    'holdings': ('issuer_security', 'reported_holding'),
    'associates': ('family_member_of', 'associate_of', 'linked_to', 'provides_support_to', 'represents'),
    'lineage': ('successor_entity', 'succeeded_by'),
}
DEFAULT_GROUPS = ('ownership', 'control')
#: Directions each predicate is traversed in; anything absent goes both ways. ``reported_holding``
#: runs holder -> security only: two managers holding one security are not related by it.
DIRECTIONS = {'reported_holding': ('forward',)}
MAX_HOPS = 4
CATEGORY_RANK = {'sanctions designation': 0, 'export control': 1, 'debarment': 2, 'other restrictive list': 3}
COVERAGE_GAPS = [
    'Absence of a path is not clearance. It means no chain of the selected published predicates, within the hop '
    'bound, joins this input to a listed entity in this index.',
    'No beneficial ownership below reporting thresholds: 13D/13G ownership is reported at 5%, UK persons with '
    'significant control at 25%, 13F only by managers with over USD 100M in Section 13(f) securities, and nominee or '
    'trust arrangements are not visible at all.',
    '13F is long-only US equity: a quarter-end snapshot of Section 13(f) securities, with no shorts, derivatives, '
    'bonds, private holdings or non-US listings.',
    'OpenSanctions (opensanctions, opensanctions_graph) is licensed CC BY-NC 4.0: non-commercial use only. A '
    'commercial deployment needs an OpenSanctions data licence before relying on these paths.',
    'GLEIF Level 2 is accounting consolidation, and only for entities that hold an LEI and report parents.',
    'Identity is asserted only. An input given as a name is matched on published label text - INFERRED, not asserted '
    '- and a true match whose publishers share no identifier will not be linked to its listing.',
    'The lists are as of the versions this index pins. Lists change daily; delistings and new designations after '
    'that are not reflected.',
    'The default index scope excludes companies_house_uk (UK persons with significant control), sec_13f_history and '
    'the USAspending transactions.',
]


def _category(source_list):
    text = (source_list or '').lower()
    if 'bureau of industry and security' in text or 'military end user' in text:
        return 'export control'
    if 'debarred' in text or 'debarment' in text:
        return 'debarment'
    if 'nonproliferation' in text or 'treasury' in text or 'sanction' in text:
        return 'sanctions designation'
    return 'other restrictive list'


def listing_descriptors(connection, entity_ids):
    """{entity ID: {'listings': [...], 'flags': [...]}} for IDs that a restrictive list names.

    ``listings`` are designations and other restrictive-list entries; ``flags`` are published
    topics that are not a listing (OpenSanctions "sanction.linked", politically exposed persons).
    """
    wanted = [e for e in set(entity_ids) if e.startswith(('ofac:party:', 'us_csl:', 'uk:sanctions:', 'un:sanctions:',
                                                          'opensanctions:'))
              and not e.startswith(('us_csl:list:', 'us_csl:program:', 'opensanctions:program:'))]
    records = {}
    for chunk in chunks(sorted(wanted)):
        marks = ','.join('?' * len(chunk))
        for row in connection.execute('SELECT entity_id, dataset, id, body FROM records WHERE entity_id IN (%s)' % marks,
                                      chunk):
            records.setdefault(row['entity_id'], []).append((row['dataset'], row['id'], decode(row['body'])))
    out = {}
    for entity_id in wanted:
        listings, flags = [], []
        for dataset, record_id, body in records.get(entity_id, []):
            attributes = body.get('attributes') or {}
            base = {'entity_id': entity_id, 'label': body.get('label'), 'from_dataset': dataset, 'record_id': record_id}
            if entity_id.startswith('ofac:party:'):
                listings.append({**base, 'list': 'OFAC Specially Designated Nationals (SDN) List'
                                 if attributes.get('source_file') == 'SDN_ADVANCED' else
                                 'OFAC Consolidated (non-SDN) Sanctions List', 'category': 'sanctions designation',
                                 'list_publication_date': attributes.get('list_publication_date')})
            elif entity_id.startswith('us_csl:'):
                listings.append({**base, 'list': attributes.get('source_list') or 'US Consolidated Screening List',
                                 'category': _category(attributes.get('source_list'))})
            elif entity_id.startswith('uk:sanctions:'):
                listings.append({**base, 'list': attributes.get('list') or 'UK Sanctions List',
                                 'category': 'sanctions designation', 'last_updated': attributes.get('last_updated')})
            elif entity_id.startswith('un:sanctions:'):
                listings.append({**base, 'list': attributes.get('list') or 'UN Security Council Consolidated List',
                                 'category': 'sanctions designation'})
            elif dataset == 'opensanctions_graph':
                topics = set(attributes.get('topics') or [])
                sources = attributes.get('datasets') or []
                if 'sanction' in topics:
                    listings.append({**base, 'list': 'OpenSanctions target with topic "sanction"',
                                     'category': 'sanctions designation', 'opensanctions_sources': sources[:12]})
                elif topics & {'export.control', 'debarment'}:
                    listings.append({**base, 'list': 'OpenSanctions target with topic %s'
                                     % ', '.join(sorted(topics & {'export.control', 'debarment'})),
                                     'category': 'export control' if 'export.control' in topics else 'debarment',
                                     'opensanctions_sources': sources[:12]})
                for topic in sorted(topics & {'sanction.linked', 'sanction.control', 'export.control.linked',
                                              'role.pep', 'role.rca', 'poi', 'crime', 'crime.fin', 'crime.terror'}):
                    flags.append({**base, 'topic': topic,
                                  'meaning': 'an OpenSanctions topic, not a designation of this entity'})
            elif dataset == 'opensanctions':
                listings.append({**base, 'list': 'OpenSanctions sanctions collection',
                                 'category': 'sanctions designation',
                                 'opensanctions_sources': (attributes.get('source_datasets') or [])[:12]})
        if listings or flags:
            # OpenSanctions records an entity in both datasets; keep one sanctions listing per ID.
            if any(l['from_dataset'] == 'opensanctions_graph' for l in listings):
                listings = [l for l in listings if l['from_dataset'] != 'opensanctions']
            out[entity_id] = {'listings': listings, 'flags': flags}
    return out


def _cluster_listings(descriptors, members):
    listings = [l for m in members for l in descriptors.get(m, {}).get('listings', [])]
    flags = [f for m in members for f in descriptors.get(m, {}).get('flags', [])]
    return listings, flags


def screen(entries, *, index=None, products_index=None, hops=3, groups=DEFAULT_GROUPS, predicates=None,
           limit=20000, max_listings=5, paths_per_listing=3, candidates=3, data_root=None, catalog_root=None):
    """Screen each entry (an entity ID or a name). See the module docstring."""
    if not 1 <= hops <= MAX_HOPS:
        raise ProductError('hops must be 1..%d' % MAX_HOPS)
    if not 1 <= limit <= 100000:
        raise ProductError('limit must be 1..100000 edges per input')
    entries = [str(e).strip() for e in entries if str(e).strip() and not str(e).strip().startswith('#')]
    if not entries:
        raise ProductError('No entries to screen')
    if len(entries) > 500:
        raise ProductError('At most 500 entries per screen; split the file')
    unknown = set(groups) - set(PREDICATE_GROUPS)
    if unknown:
        raise ProductError('Unknown predicate group(s) %s; choose from %s' % (sorted(unknown), sorted(PREDICATE_GROUPS)))
    selected = sorted({p for g in groups for p in PREDICATE_GROUPS[g]} | set(predicates or ()))
    timer = Timer()
    index = Path(index or default_index(data_root))
    connection = connect(index)
    try:
        return retrying(_screen, connection, index, entries, timer=timer,
                        products_path=products_index or default_products_index(index), hops=hops, groups=list(groups),
                        predicates=selected, limit=limit, max_listings=max_listings,
                        paths_per_listing=paths_per_listing, candidates=candidates, data_root=data_root,
                        catalog_root=catalog_root)
    finally:
        connection.close()


def _screen(connection, index, entries, *, timer, products_path, hops, groups, predicates, limit, max_listings,
            paths_per_listing, candidates, data_root, catalog_root):
    with timer.step('open'):
        info = index_info(connection, index)
        at_start = resolution_digest(connection)
        products = open_products_index(products_path, info)
    results = []
    for entry in entries:
        with timer.step('resolve'):
            resolved = resolve_input(connection, products, entry, limit=candidates)
        if resolved['kind'] == 'id':
            subjects = [(resolved['entity_id'], 'exact entity ID supplied by the caller')]
        elif resolved['kind'] == 'name':
            subjects = [(c['canonical_id'], '%s match on published label text - INFERRED, not asserted' % c['match'])
                        for c in resolved['search']['candidates']]
        else:
            results.append({'input': entry, 'status': 'not_resolved', 'resolution': resolved,
                            'absence_of_a_path_is_not_clearance': True,
                            'note': 'The input matched nothing in this index. That is not clearance: the entity may '
                                    'be described under another name or not published in the pinned datasets.'})
            continue
        screened = []
        for subject, basis in subjects:
            with timer.step('screen'):
                screened.append(_screen_one(connection, subject, basis, hops=hops, predicates=predicates,
                                            limit=limit, max_listings=max_listings,
                                            paths_per_listing=paths_per_listing))
        statuses = [s['status'] for s in screened]
        status = next((s for s in ('listed', 'path_found', 'inconclusive_search_truncated') if s in statuses),
                      statuses[0])
        item = {'input': entry, 'input_kind': resolved['kind'], 'status': status,
                'absence_of_a_path_is_not_clearance': True, 'screened': screened}
        if resolved['kind'] == 'name':
            item['name_match_note'] = ('The input is a name. Each of the %d best label-matching clusters was screened '
                                       'separately; none of them is asserted to be the entity you meant.'
                                       % len(screened))
        results.append(item)
    summary = {}
    for item in results:
        summary[item['status']] = summary.get(item['status'], 0) + 1
    answer = {'results': results, 'summary_by_status': summary,
              'search': {'hops': hops, 'predicate_groups': groups, 'predicates': predicates,
                         'directions': {p: list(DIRECTIONS.get(p, ('forward', 'reverse'))) for p in predicates},
                         'edge_limit_per_input': limit, 'identity': 'asserted clusters (resolved=True)'},
              'status_meanings': {
                  'listed': 'the input\'s own asserted cluster contains a restrictive-list entry (hop 0)',
                  'path_found': 'a chain of the selected predicates reaches a listed cluster within the hop bound',
                  'inconclusive_search_truncated': 'no listed cluster was reached before the edge limit; the search did '
                                                   'not finish, so nothing can be said about longer or wider chains',
                  'no_path_within_bound': 'the bounded search finished without reaching a listed cluster. NOT clearance',
                  'not_resolved': 'the input is not an ID or a label in this index. NOT clearance'},
              'coverage_gaps': COVERAGE_GAPS,
              'lists_in_this_index': {name: info['pinned_versions'][name] for name in
                                      ('ofac_sanctions', 'other_sanctions_lists', 'opensanctions', 'opensanctions_graph')
                                      if name in info['pinned_versions']}}
    runs_out = ['Only these predicates were traversed: %s. A relationship published under any other predicate, or not '
                'published, is invisible to this screen.' % ', '.join(predicates),
                'Paths are shown for at most %d listed clusters per input and %d paths each; counts of every listed '
                'cluster reached are in listed_clusters_reached.' % (max_listings, paths_per_listing)]
    truncated = [r['input'] for r in results if r['status'] == 'inconclusive_search_truncated']
    if truncated:
        runs_out.append('The search hit its edge limit before finishing for: %s. Raise --limit or lower --hops.'
                        % ', '.join(truncated))
    not_established = [
        'Not a sanctions determination and not legal advice. A listing applies to the named party on the named list; '
        'ownership or control proximity does not transfer it, although some regimes (for example OFAC\'s 50 percent '
        'rule) extend restrictions to owned entities by rule, which this screen does not evaluate.',
        'A path is a chain of published claims, each as of its own date; the chain as a whole may never have held at '
        'one time.',
        'Absence of a path is not clearance.']
    return envelope('screen', query={'entries': entries}, answer=answer, runs_out=runs_out,
                    not_established=not_established, info=info, timer=timer, resolution_at_start=at_start,
                    resolution_at_end=resolution_digest(connection), data_root=data_root, catalog_root=catalog_root,
                    extra={'products_index': products_index_info(products)})


def search_listings(connection, subject, *, hops, predicates, limit, per_node=5000):
    """Bounded multi-target BFS from ``subject``'s asserted cluster to listed clusters.

    Returns (depth by canonical node, parents, listed [(depth, rank, -lists, node, listings, flags)],
    edges examined, truncated, capped nodes). Listed clusters are endpoints: the search does not
    expand through them. ``parents`` keeps every same-level parent, as ``Graph.paths`` does, so all
    shortest paths can be recovered.
    """
    forward = [p for p in predicates if 'forward' in DIRECTIONS.get(p, ('forward', 'reverse'))]
    reverse = [p for p in predicates if 'reverse' in DIRECTIONS.get(p, ('forward', 'reverse'))]
    start, _, _ = cluster(connection, subject)
    depth, parents, listed, examined, truncated, capped = {start: 0}, {start: []}, [], 0, False, []
    frontier = [start]
    for level in range(1, hops + 1):
        members = members_map(connection, frontier)
        layer = {}
        rows = []
        for node in frontier:
            for member in members[node]:
                for column, chosen in (('subject', forward), ('object', reverse)):
                    if not chosen:
                        continue
                    found = connection.execute(
                        'SELECT rowid AS eid, subject, predicate, object, weight, record_rowid FROM edges WHERE %s=? '
                        'AND predicate IN (%s) LIMIT ?' % (column, ','.join('?' * len(chosen))),
                        (member, *chosen, per_node + 1)).fetchall()
                    if len(found) > per_node:
                        capped.append(member)
                        found = found[:per_node]
                    rows.extend((node, column, row) for row in found)
                    examined += len(found)
            if examined >= limit:
                truncated = True
                break
        canon = canonical_map(connection, {r['subject'] for _, _, r in rows} | {r['object'] for _, _, r in rows})
        for node, column, row in rows:
            other = row['object'] if column == 'subject' else row['subject']
            there = canon.get(other, other)
            if there in depth and there not in layer:
                continue
            edge = {'from': node, 'to': there, 'predicate': row['predicate'], 'weight': row['weight'],
                    'edge': row['eid'], 'record_rowid': row['record_rowid'],
                    'direction': 'forward' if column == 'subject' else 'reverse'}
            layer.setdefault(there, []).append((node, edge))
        layer.pop(start, None)
        for node, items in layer.items():
            depth[node] = level
            parents[node] = sorted(items, key=lambda x: (x[0], x[1]['edge']))
        node_members = members_map(connection, layer)
        descriptors = listing_descriptors(connection, [m for ms in node_members.values() for m in ms])
        expand = []
        for node in sorted(layer):
            listings, flags = _cluster_listings(descriptors, node_members[node])
            if listings:
                rank = min(CATEGORY_RANK.get(l['category'], 9) for l in listings)
                listed.append((level, rank, -len({l['list'] for l in listings}), node, listings, flags))
            else:
                expand.append(node)
        frontier = expand
        if truncated or not frontier:
            break
    listed.sort(key=lambda x: x[:4])
    return depth, parents, listed, examined, truncated, sorted(set(capped))


def _paths_to(parents, start, goal, limit):
    paths = []

    def walk(node, suffix):
        if len(paths) >= limit:
            return
        if node == start:
            paths.append(list(reversed(suffix)))
            return
        for parent, edge in parents.get(node, []):
            walk(parent, suffix + [edge])

    walk(goal, [])
    return paths


def _screen_one(connection, subject, basis, *, hops, predicates, limit, max_listings, paths_per_listing):
    canonical_id, members, asserted = cluster(connection, subject)
    own = listing_descriptors(connection, members)
    own_listings, own_flags = _cluster_listings(own, members)
    subject_label = labels(connection, members[:6])
    title = next((v for v in subject_label.values() if v.get('label')), {})
    result = {'entity_id': subject, 'canonical_id': canonical_id, 'label': title.get('label'),
              'label_from': title.get('label_from'), 'match_basis': basis,
              'asserted_identity_cluster': members}
    if own_listings:
        result.update({'status': 'listed', 'hops': 0, 'own_listings': own_listings, 'own_flags': own_flags,
                       'nearest_listings': []})
        return result
    depth, parents, listed, examined, truncated, capped = search_listings(connection, subject, hops=hops,
                                                                          predicates=predicates, limit=limit)
    nearest = []
    for level, _, _, node, listings, flags in listed[:max_listings]:
        paths = _paths_to(parents, canonical_id, node, paths_per_listing + 1)
        nearest.append({'listed_cluster': node, 'hops': level, 'listings': listings, 'flags': flags,
                        'paths': _describe_paths(connection, paths[:paths_per_listing]),
                        'more_paths_of_this_length': len(paths) > paths_per_listing})
    result.update({'own_flags': own_flags, 'nearest_listings': nearest,
                   'listed_clusters_reached': len(listed),
                   'listed_clusters_by_hops': {str(d): sum(1 for x in listed if x[0] == d) for d in
                                               sorted({x[0] for x in listed})},
                   'search': {'nodes_reached': len(depth) - 1, 'edges_examined': examined, 'truncated': truncated,
                              'nodes_with_capped_expansion': capped[:20],
                              'listed_clusters_are_endpoints': 'the search does not continue through a listed cluster'}})
    if listed:
        result.update({'status': 'path_found', 'hops': listed[0][0]})
    elif truncated or capped:
        result.update({'status': 'inconclusive_search_truncated', 'hops': None})
    else:
        result.update({'status': 'no_path_within_bound', 'hops': None})
    return result


def _describe_paths(connection, paths):
    rowids = [step['record_rowid'] for path in paths for step in path]
    sources = record_sources(connection, rowids)
    nodes = sorted({step[k] for path in paths for step in path for k in ('from', 'to')})
    named = labels(connection, nodes[:60])
    out = []
    for path in paths:
        steps = []
        for step in path:
            source = sources.get(step['record_rowid'], {})
            steps.append({'hop': len(steps) + 1, 'from': step['from'], 'from_label': (named.get(step['from']) or {}).get('label'),
                          'predicate': step['predicate'], 'direction': step['direction'], 'to': step['to'],
                          'to_label': (named.get(step['to']) or {}).get('label'),
                          'from_dataset': source.get('from_dataset'), 'record_id': source.get('record_id')})
        out.append({'hops': len(steps), 'steps': steps})
    return out


def read_entries(path):
    """One name or entity ID per line; blank lines and lines starting with # are ignored."""
    text = Path(path).read_text(encoding='utf-8')
    return [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith('#')]
