"""Q5. A sanctioned vessel, its AIS identity, and the port and waterway network around it.

  ofac_sanctions / other_sanctions_lists / opensanctions   vessel listings with published IMO / MMSI
    -> (asserted identity: shared IMO or MMSI value)
  marine_ais                                               the MMSI vessel entity and its position events
  transport                                                USACE ports, WPI ports with UN/LOCODEs, and the
                                                           waterway node network they sit on
  airport_nodes                                            the same treatment for air nodes, for contrast
"""
import common

VESSEL_PREFIXES = ('mmsi:', 'imo:')
SANCTION_PREFIXES = ('ofac:party:', 'opensanctions:', 'uk:sanctions:', 'us_csl:', 'un:sanctions:')


def sanctioned_vessels(connection):
    for row in common.rows(connection, "SELECT canonical_id, GROUP_CONCAT(entity_id, '|') AS members "
                                       'FROM resolved GROUP BY canonical_id'):
        members = row['members'].split('|')
        vessels = [m for m in members if m.startswith(VESSEL_PREFIXES)]
        listings = [m for m in members if m.startswith(SANCTION_PREFIXES)]
        if vessels and listings:
            yield {'vessels': vessels, 'listings': listings, 'members': sorted(members)}


def assertions_on(connection, subject, limit=8):
    """Literal assertions recorded against one subject (UN/LOCODEs, IMO numbers, ...)."""
    out = []
    for row in common.stream(connection, "SELECT dataset, body FROM records WHERE subject=? AND kind='assertion'",
                             (subject,)):
        body = common.body_of(row)
        out.append({'from_dataset': row['dataset'], 'predicate': body.get('predicate'),
                    'value': body.get('value'), 'object': body.get('object')})
        if len(out) >= limit:
            break
    return out


def port_reference(connection, limit):
    """Published port entities and the identifiers they carry.

    Ports are reference entities: they carry ``identified_by`` literals but sit on no edge in the
    index, so they are *not* nodes of the waterway graph. That is a real gap, not a query failure.
    """
    ports, namespaces = [], {}
    for prefix in ('usace:port:', 'wpi:', 'marad:strategic_seaport:'):
        low, high = common.prefix_range(prefix)
        rows = common.rows(connection, 'SELECT entity_id, dataset, body FROM records WHERE entity_id >= ? '
                                       "AND entity_id < ? AND kind='entity' LIMIT 400", (low, high))
        namespaces[prefix] = len(rows)
        for row in rows[:2]:
            ports.append({'port': {'entity_id': row['entity_id'], 'label': common.body_of(row).get('label'),
                                   'from_dataset': row['dataset']},
                          'identifiers': assertions_on(connection, row['entity_id'], limit=4)})
    return {'ports_by_namespace': namespaces, 'examples': ports[:limit],
            'note': 'ports are reference entities in this index: they carry no graph edge, so they are not '
                    'nodes of the routable networks below. Joining a vessel to a port needs a spatial match.'}


def routable_networks(graph, connection, limit):
    """The two published node graphs in the transport dataset, sampled around one node each."""
    out = {}
    for prefix, name in (('usace:waterway_node:', 'inland_waterway'), ('ntad:rail_node:', 'rail')):
        low, high = common.prefix_range(prefix)
        total = common.rows(connection, 'SELECT COUNT(*) AS edges FROM edges WHERE subject >= ? AND subject < ?',
                            (low, high))[0]['edges']
        seed = common.rows(connection, 'SELECT subject FROM edges WHERE subject >= ? AND subject < ? LIMIT 1',
                           (low, high))
        sample = []
        if seed:
            sample = common.described(connection, graph.neighborhood(
                seed[0]['subject'], hops=2, limit=40)['edges'])[:limit]
        out[name] = {'edges_in_index': total, 'seed': seed[0]['subject'] if seed else None, 'sample': sample}
    return out


def main():
    args = common.parser(__doc__).parse_args()
    graph, connection = common.open_index(args.index)
    matches = []
    for match in sanctioned_vessels(connection):
        vessel = match['vessels'][0]
        events = common.rows(connection, "SELECT COUNT(*) AS events FROM records WHERE subject=? AND kind='event'",
                             (vessel,))
        described = common.rows(connection, "SELECT DISTINCT dataset FROM records WHERE kind='entity' "
                                            'AND entity_id=?', (vessel,))
        matches.append({**match, 'datasets_describing_the_vessel': [r['dataset'] for r in described],
                        'event_records': events[0]['events'] if events else 0})
        if len(matches) >= 2000:
            break
    matches.sort(key=lambda m: (-len(m['datasets_describing_the_vessel']), -m['event_records']))
    ais_seen = [m for m in matches if 'marine_ais' in m['datasets_describing_the_vessel']]
    example = (ais_seen or matches or [None])[0]
    activity = []
    if example:
        for vessel in example['vessels']:
            activity.extend(assertions_on(connection, vessel, limit=6))
            for listing in example['listings']:
                activity.extend(assertions_on(connection, listing, limit=4))
    reflagged = [m for m in matches if len(m['vessels']) > 1]
    result = {
        'answer': {
            'sanctioned_vessel_identities_joined_by_imo_or_mmsi': len(matches),
            'of_which_also_present_in_marine_ais': len(ais_seen),
            'clusters_holding_two_mmsi_values_for_one_hull': len(reflagged),
            'reflagged_examples': reflagged[:3],
            'example': example,
            'example_claims_about_the_vessel': activity[:args.limit * 2],
            'port_reference': port_reference(connection, args.limit),
            'routable_networks': routable_networks(graph, connection, args.limit)},
        'which_dataset_supplied_which_edge': {
            'vessel sanctions listing plus its IMO / MMSI': 'ofac_sanctions / other_sanctions_lists / '
                                                            'opensanctions / opensanctions_graph',
            'listing <-> AIS vessel identity': 'asserted: the same IMO or MMSI value is published on both sides',
            'AIS vessel entity and position events': 'marine_ais',
            'ports, UN/LOCODEs and the waterway node network': 'transport (USACE + World Port Index + USACE '
                                                               'waterway network)',
            'air nodes for contrast': 'airport_nodes'},
        'what_this_does_not_establish': [
            'An MMSI is a radio station identity that is reassigned between vessels over time, so an MMSI match '
            'across datasets with disjoint periods can be two different ships. IMO numbers are stable; MMSI is not.',
            'AIS is self-reported and can be spoofed, gapped or switched off. Absence of a position is not absence '
            'of a voyage.',
            'The transport waterway network and the OSM road graphs are separate graphs with no published spatial '
            'crosswalk, and the OSM extracts are outside the default profile.',
            'No port call is asserted here: proximity in the network is not a visit, and freight volumes (freight, '
            '31.1M FAF rows) are outside the default profile.',
            'Port entities carry identifiers but no edges, so they are not nodes of the waterway or rail graphs. '
            'Joining a vessel to a port would need a spatial match, which nothing here performs.',
            'AIS position reports are event records keyed on the report, not on the vessel, so they are not '
            'reachable from the vessel ID by a graph traversal in this index.'],
    }
    return common.emit('q5_sanctioned_vessel_to_port_network', result, save=args.save)


if __name__ == '__main__':
    main()
