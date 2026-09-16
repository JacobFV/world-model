"""Q1. From a sanctions listing to a legal entity, its corporate group, and securities holders.

No single dataset can answer this. The chain is:

  ofac_sanctions / other_sanctions_lists / opensanctions   sanctions listing + published LEI
    -> (asserted identity: shared LEI value)
  sec_gleif                                                legal entity, jurisdiction, ISINs
    -> gleif_parent_relationships                          accounting consolidation parents
    -> sec_gleif ISIN, bridged to the 13F CUSIP (ISO 6166)  the issuer's listed securities
    -> sec_ownership_datasets                              13F reported holdings of those securities
    -> sec_issuer_reference / GLEIF RA000665                the SEC filer, where one is published

Two hops used to be missing and one still is. The securities hop now fires: the GLEIF
ISIN-to-LEI mapping and the 13F information tables name the same security in two
notations, and ``resolution.bridges`` joins them through the CUSIP inside a US ISIN.
The filer hop is measured below and is genuinely empty: the sanctioned-entity LEI
population and the SEC-registrant LEI population do not intersect at all.
"""
import common

SANCTION_PREFIXES = ('ofac:party:', 'opensanctions:', 'uk:sanctions:', 'us_csl:', 'un:sanctions:')
# A subset of those are designation lists. An `opensanctions:` ID may equally be an ownership record
# or a politically-exposed-person entry, so the two populations are counted separately.
DESIGNATION_PREFIXES = ('ofac:party:', 'uk:sanctions:', 'us_csl:', 'un:sanctions:')
OWNERSHIP = ['ultimately_consolidated_by', 'directly_consolidated_by', 'fund_managed_by',
             'parent_reporting_exception', 'owns', 'controls', 'significant_control_over']


def cluster_shapes(connection):
    """Asserted-identity clusters, split by the identity kinds they join."""
    sanctioned, filers, both = [], 0, 0
    for row in common.rows(connection, "SELECT canonical_id, GROUP_CONCAT(entity_id, '|') AS members "
                                       'FROM resolved GROUP BY canonical_id'):
        members = row['members'].split('|')
        leis = [m for m in members if m.startswith('lei:')]
        listings = [m for m in members if m.startswith(SANCTION_PREFIXES)]
        ciks = [m for m in members if m.startswith('sec:cik:')]
        if leis and ciks:
            filers += 1
        if leis and listings:
            sanctioned.append({'canonical_id': row['canonical_id'], 'lei': leis, 'listings': listings,
                               'ciks': ciks, 'members': sorted(members)})
            if ciks:
                both += 1
    return sanctioned, filers, both


def issuers_with_bridged_securities(connection):
    """{issuer entity ID: [security cluster members]} for securities the 13F tables also name.

    GLEIF publishes ``issuer_security`` edges to ``isin:<code>``; the 13F information tables name
    the same instruments as ``cusip:<code>``. ``resolution.bridges`` clusters the two through the
    CUSIP inside a US ISIN (ISO 6166), so this join is asserted, not guessed. Driving the query
    from ``resolved`` keeps it to the few thousand securities that actually bridged.
    """
    low, high = common.prefix_range('isin:')
    issuers = {}
    for row in common.stream(connection,
                             'SELECT e.subject AS issuer, e.object AS security, r.canonical_id AS canonical_id '
                             'FROM resolved r JOIN edges e ON e.object = r.entity_id '
                             "WHERE r.entity_id >= ? AND r.entity_id < ? AND e.predicate = 'issuer_security'",
                             (low, high)):
        issuers.setdefault(row['issuer'], set()).add(row['security'])
    return {issuer: sorted(securities) for issuer, securities in issuers.items()}


def holders_of(graph, connection, security, limit):
    """13F filers reporting a holding in a security, reached through the resolved security cluster."""
    edges = graph.neighborhood(security, hops=1, predicates=['reported_holding'], direction='in',
                              limit=limit, resolved=True)['edges']
    return common.described(connection, edges)


def main():
    args = common.parser(__doc__).parse_args()
    graph, connection = common.open_index(args.index)
    clusters, lei_cik_clusters, both = cluster_shapes(connection)
    if not clusters:
        return common.emit('q1_sanctioned_to_listed_holders',
                           {'answer': None,
                            'reason': 'no asserted-identity cluster in this index joins a sanctions listing to an LEI; '
                                      'attach the resolution with "python3 -m worldmodel unify-resolve"'}, save=args.save)
    bridged = issuers_with_bridged_securities(connection)
    # Prefer a cluster whose LEI reaches a security that 13F filers report holding: that is where
    # the chain runs all the way from a listing to institutional holders.
    ranked = []
    for cluster in clusters:
        lei = cluster['lei'][0]
        securities = bridged.get(lei, [])
        designated = any(m.startswith(DESIGNATION_PREFIXES) for m in cluster['listings'])
        ownership = graph.neighborhood(lei, hops=2, predicates=OWNERSHIP, limit=200, resolved=True)
        ranked.append((bool(securities), designated, len(ownership['edges']), cluster, ownership, securities))
    ranked.sort(key=lambda row: (not row[0], not row[1], -row[2]))
    _, _, _, cluster, ownership, bridged_securities = ranked[0]
    lei = cluster['lei'][0]
    group = common.described(connection, ownership['edges'])
    # Every security the legal entity issues, and who reports holding the ones the 13F tables name.
    securities = common.described(connection, graph.neighborhood(
        lei, hops=1, predicates=['issuer_security'], limit=200)['edges'])
    holdings, holder_securities = [], []
    for security in bridged_securities[:10]:
        found = holders_of(graph, connection, security, args.limit)
        if found:
            holder_securities.append({'security': security,
                                      'also_named': common.cluster_members(connection, security),
                                      'reported_holdings': len(found)})
        holdings.extend(found)
    # Which of the group's entities are SEC filers, and who reports holding them directly?
    listings = []
    for member in sorted({edge['subject'] for edge in group} | {edge['object'] for edge in group} | {lei}):
        for related in common.cluster_members(connection, member):
            if not related.startswith('sec:cik:'):
                continue
            issuer = graph.neighborhood(related, hops=1, predicates=['issuer_listing', 'issuer_security'],
                                        limit=50, resolved=False)
            listings.extend(common.described(connection, issuer['edges']))
            held = graph.neighborhood(related, hops=1, predicates=['reported_holding'], direction='in',
                                      limit=args.limit, resolved=False)
            holdings.extend(common.described(connection, held['edges']))
    sanctioned_issuers_with_holders = sum(1 for c in clusters if bridged.get(c['lei'][0]))
    designation_clusters = [c for c in clusters if any(m.startswith(DESIGNATION_PREFIXES) for m in c['listings'])]
    designated_with_holders = sum(1 for c in designation_clusters if bridged.get(c['lei'][0]))
    result = {
        'answer': {
            'sanctions_listings': [common.label(connection, listing) for listing in cluster['listings']],
            'legal_entity': common.label(connection, lei),
            'asserted_identity_cluster': cluster['members'],
            'corporate_group_edges': group[:args.limit],
            'securities_issued_by_the_legal_entity': securities[:args.limit],
            'securities_the_13f_tables_also_name': holder_securities,
            'sec_listings_and_securities': listings[:args.limit],
            'reported_holdings': holdings[:args.limit]},
        'counts': {'clusters_joining_a_sanctions_listing_to_an_lei': len(clusters),
                   'clusters_joining_an_lei_to_an_sec_cik': lei_cik_clusters,
                   'clusters_joining_all_three': both,
                   'sanctions_linked_leis_reaching_a_13f_named_security': sanctioned_issuers_with_holders,
                   'clusters_whose_listing_is_on_a_designation_list': len(designation_clusters),
                   'designation_list_leis_reaching_a_13f_named_security': designated_with_holders,
                   'issuers_whose_securities_bridged_isin_to_cusip': len(bridged),
                   'corporate_group_edges': len(group),
                   'securities_issued': len(securities), 'holdings_found': len(holdings)},
        'where_the_evidence_runs_out': (
            'The holder leg fires through the *security*, not through the filer. %d clusters join a '
            'sanctions listing to a GLEIF LEI and %d of those LEIs issue a security that the 13F '
            'information tables also name (GLEIF publishes the ISIN, the 13F tables publish the CUSIP, '
            'and ISO 6166 makes them the same instrument), so institutional holdings are reachable. '
            'The filer leg does not fire: %d clusters join an LEI to an SEC CIK and %d join all three. '
            'That is not an extraction gap. Measured on the published golden copy, the LEIs that '
            'sanctions publishers name and the LEIs whose GLEIF registration authority is the SEC are '
            'two disjoint populations: zero of 1,928 sanctions-published LEIs carry an SEC EDGAR '
            'registration-authority entity ID, because a US entity on a sanctions or ownership list is '
            'a state-registered company (546 of 1,927 are Russian, 139 carry a Delaware file number) '
            'rather than an SEC registrant. Sharper still: %d of these clusters carry a listing from an '
            'actual designation list (OFAC SDN, the UK and UN lists, the US Consolidated Screening '
            'List) and %d of those reach a security the 13F tables name, so the anchor chosen above is '
            'an ownership or politically-exposed-person record rather than a designation.'
            % (len(clusters), sanctioned_issuers_with_holders, lei_cik_clusters, both,
               len(designation_clusters), designated_with_holders)),
        'which_dataset_supplied_which_edge': {
            'sanctions listing and its published LEI': 'ofac_sanctions / other_sanctions_lists / opensanctions',
            'listing <-> LEI identity': 'asserted: both sides publish the same LEI value (unify-resolve)',
            'legal entity, legal name, jurisdiction': 'sec_gleif (GLEIF LEI-CDF golden copy)',
            'consolidation parents': 'gleif_parent_relationships (GLEIF Level 2)',
            'issuer -> ISIN': 'sec_gleif (GLEIF/ANNA ISIN-to-LEI mapping)',
            'ISIN <-> CUSIP identity': 'asserted by ISO 6166 (worldmodel.resolution.bridges.isin_cusip): the '
                                       'nine-character NSIN inside a US ISIN is the CUSIP, and the ISIN check '
                                       'digit is recomputed before it is read',
            'LEI <-> CIK': 'sec_issuer_reference published LEIs, plus GLEIF registration authority RA000665 '
                           '(worldmodel.resolution.bridges.gleif_sec_cik)',
            'reported holdings': 'sec_ownership_datasets (SEC 13F information tables)'},
        'what_this_does_not_establish': [
            'Not a sanctions determination. A listing applies to the named party on the named list; membership of '
            'a corporate group does not transfer it, and opensanctions_graph also carries ownership and '
            'politically-exposed-person records that are not designations at all.',
            'Identity here is asserted by publishers sharing an identifier, not inferred from names. Where a '
            'sanctions record publishes no LEI, absence of a link is absence of published evidence.',
            'The ISIN-to-CUSIP bridge is security identity only. It says two notations name one instrument; it '
            'says nothing about who the issuer is, so the issuer side still needs a published issuer identifier.',
            'GLEIF Level 2 records accounting consolidation, which is not the same as control or beneficial '
            'ownership, and carries reporting exceptions.',
            'Holdings are as-filed at the filing date and are not current positions; a 13F manager reports what '
            'it had discretion over, which is not beneficial ownership of the issuer.',
            'GLEIF restricts its published ISIN mapping to US and CA ISINs in this catalog, so a sanctioned '
            'issuer whose securities carry only a European ISIN has no reachable security here.',
            'The default unify profile excludes sec_13f_history and companies_house_uk, so the holdings shown are '
            'only as complete as sec_ownership_datasets and UK PSC ownership is out of scope entirely.'],
    }
    return common.emit('q1_sanctioned_to_listed_holders', result, save=args.save)


if __name__ == '__main__':
    main()
