"""Q1. From a sanctions listing to a legal entity, its corporate group, and securities holders.

No single dataset can answer this. The chain is:

  ofac_sanctions / other_sanctions_lists / opensanctions   sanctions listing + published LEI
    -> (asserted identity: shared LEI value)
  sec_gleif                                                legal entity, jurisdiction, ISINs
    -> gleif_parent_relationships                          accounting consolidation parents
    -> sec_issuer_reference                                 LEI <-> SEC CIK, venue listings
    -> sec_ownership_datasets                              13F/Forms 3-4-5 reported holdings
"""
import common

SANCTION_PREFIXES = ('ofac:party:', 'opensanctions:', 'uk:sanctions:', 'us_csl:', 'un:sanctions:')
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


def main():
    args = common.parser(__doc__).parse_args()
    graph, connection = common.open_index(args.index)
    clusters, lei_cik_clusters, both = cluster_shapes(connection)
    if not clusters:
        return common.emit('q1_sanctioned_to_listed_holders',
                           {'answer': None,
                            'reason': 'no asserted-identity cluster in this index joins a sanctions listing to an LEI; '
                                      'attach the resolution with "python3 -m worldmodel unify-resolve"'}, save=args.save)
    # Prefer a cluster whose LEI also has ownership edges, i.e. the chain continues past identity.
    ranked = []
    for cluster in clusters:
        lei = cluster['lei'][0]
        ownership = graph.neighborhood(lei, hops=2, predicates=OWNERSHIP, limit=200, resolved=True)
        ranked.append((bool(cluster['ciks']), len(ownership['edges']), cluster, ownership))
    ranked.sort(key=lambda row: (not row[0], -row[1]))
    _, depth, cluster, ownership = ranked[0]
    lei = cluster['lei'][0]
    group = common.described(connection, ownership['edges'])
    # Which of the group's entities are SEC filers, and who reports holding them?
    securities = common.described(connection, graph.neighborhood(
        lei, hops=1, predicates=['issuer_security'], limit=40)['edges'])
    holdings, listings = [], []
    for security in securities[:10]:
        holdings.extend(common.described(connection, graph.neighborhood(
            security['object'], hops=1, predicates=['reported_holding'], direction='in', limit=10)['edges']))
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
    result = {
        'answer': {
            'sanctions_listings': [common.label(connection, listing) for listing in cluster['listings']],
            'legal_entity': common.label(connection, lei),
            'asserted_identity_cluster': cluster['members'],
            'corporate_group_edges': group[:args.limit],
            'securities_issued_by_the_legal_entity': securities[:args.limit],
            'sec_listings_and_securities': listings[:args.limit],
            'reported_holdings_of_group_issuers': holdings[:args.limit]},
        'counts': {'clusters_joining_a_sanctions_listing_to_an_lei': len(clusters),
                   'clusters_joining_an_lei_to_an_sec_cik': lei_cik_clusters,
                   'clusters_joining_all_three': both,
                   'corporate_group_edges': len(group),
                   'securities_issued': len(securities), 'holdings_found': len(holdings)},
        'where_the_evidence_runs_out': (
            'The sanctions-to-securities chain is only as long as the published identifiers. In this index '
            '%d clusters join a sanctions listing to a GLEIF LEI and %d join an LEI to an SEC CIK, but %d join '
            'all three: no published mapping connects these particular sanctioned legal entities to an SEC '
            'filer, so the 13F leg does not fire. That is a gap in the published evidence, not an assertion '
            'that no such holding exists.' % (len(clusters), lei_cik_clusters, both)),
        'which_dataset_supplied_which_edge': {
            'sanctions listing and its published LEI': 'ofac_sanctions / other_sanctions_lists / opensanctions',
            'listing <-> LEI identity': 'asserted: both sides publish the same LEI value (unify-resolve)',
            'legal entity, legal name, jurisdiction': 'sec_gleif (GLEIF LEI-CDF golden copy)',
            'consolidation parents': 'gleif_parent_relationships (GLEIF Level 2)',
            'LEI <-> CIK and venue listings': 'sec_issuer_reference',
            'reported holdings': 'sec_ownership_datasets (SEC structured ownership data sets)'},
        'what_this_does_not_establish': [
            'Not a sanctions determination. A listing applies to the named party on the named list; membership of '
            'a corporate group does not transfer it.',
            'Identity here is asserted by publishers sharing an LEI value, not inferred from names. Where a '
            'sanctions record publishes no LEI, absence of a link is absence of published evidence.',
            'GLEIF Level 2 records accounting consolidation, which is not the same as control or beneficial '
            'ownership, and carries reporting exceptions.',
            'Holdings are as-filed at the filing date and are not current positions.',
            'The default unify profile excludes sec_13f_history and companies_house_uk, so holdings and UK PSC '
            'ownership are only as complete as sec_ownership_datasets and the selected scope.'],
    }
    return common.emit('q1_sanctioned_to_listed_holders', result, save=args.save)


if __name__ == '__main__':
    main()
