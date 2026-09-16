"""Q4. One legislator across committees, sponsored bills, recorded votes, campaign finance and lobbying.

  congress_people        the person, their terms, party, chamber, committee assignments, and the
                         published same_as links to Voteview ICPSR and FEC candidate IDs
  voteview_rollcalls     roll-call vote positions and ideal-point scores under the ICPSR ID
  govinfo_billstatus     sponsored and cosponsored measures, committee referrals
  congress_gov_api       House roll-call events and amendments
  fec_candidates / fec   candidacies, the principal campaign committee, committee finance
  lda_lobbying           registrants and clients that report contacting the chamber/committee
"""
import common


def best_person(connection):
    """A bioguide person whose asserted-identity cluster also holds an ICPSR and an FEC candidate ID."""
    for row in common.rows(connection, "SELECT canonical_id, GROUP_CONCAT(entity_id, '|') AS members "
                                       'FROM resolved GROUP BY canonical_id'):
        members = row['members'].split('|')
        if (any(m.startswith('bioguide:') for m in members) and any(m.startswith('icpsr:') for m in members)
                and any(m.startswith('fec:candidate:') for m in members)):
            yield sorted(members)


def main():
    options = common.parser(__doc__)
    options.add_argument('--bioguide', help='e.g. P000197')
    args = options.parse_args()
    graph, connection = common.open_index(args.index)
    if args.bioguide:
        members = common.cluster_members(connection, 'bioguide:' + args.bioguide) or ['bioguide:' + args.bioguide]
    else:
        ranked = []
        for candidate in best_person(connection):
            bioguide = next(m for m in candidate if m.startswith('bioguide:'))
            bills = graph.neighborhood(bioguide, hops=1, predicates=['sponsored_measure', 'cosponsored_measure'],
                                       limit=50, resolved=True)['edges']
            if not bills:  # prefer a member who served in a Congress that BILLSTATUS covers
                continue
            edges = graph.neighborhood(bioguide, hops=1, limit=400, resolved=True)['edges']
            ranked.append((len(edges), candidate))
            if len(ranked) >= 40:
                break
        if not ranked:
            return common.emit('q4_legislator_bills_votes_money',
                               {'answer': None, 'reason': 'no resolved legislator cluster in this index; '
                                                          'run "python3 -m worldmodel unify-resolve"'},
                               save=args.save)
        ranked.sort(key=lambda row: -row[0])
        members = ranked[0][1]
    bioguide = next(m for m in members if m.startswith('bioguide:'))
    around = graph.neighborhood(bioguide, hops=1, limit=3000, resolved=True)
    edges = common.described(connection, around['edges'])
    by_dataset = {}
    for edge in edges:
        by_dataset.setdefault(edge['from_dataset'], {}).setdefault(edge['predicate'], []).append(edge)
    finance = []
    committees = {edge['object'] for edge in edges
                  if edge['predicate'] in ('principal_campaign_committee', 'authorized_committee_of')
                  and (edge['object'] or '').startswith('fec:committee:')}
    for member in list(members) + sorted(committees)[:4]:
        if member.startswith(('fec:candidate:', 'fec:committee:')):
            finance.extend(common.observations_for(connection, member, limit=8))
    result = {
        'answer': {
            'person': common.label(connection, bioguide),
            'asserted_identity_cluster': members,
            'edges_by_dataset': {dataset: {predicate: {'edges': len(group), 'example': group[0]}
                                           for predicate, group in sorted(predicates.items())}
                                 for dataset, predicates in sorted(by_dataset.items())},
            'campaign_finance_observations': finance[:args.limit]},
        'counts': {'edges_on_the_resolved_person': len(edges),
                   'datasets_contributing_edges': len(by_dataset),
                   'cluster_members': len(members)},
        'which_dataset_supplied_which_edge': {
            'person, terms, party, committee assignment': 'congress_people',
            'bioguide <-> icpsr and bioguide <-> fec:candidate identity': 'congress_people same_as (published '
                                                                         'congress-legislators ID lists)',
            'roll-call positions and ideal points': 'voteview_rollcalls',
            'sponsored and cosponsored measures, referrals': 'govinfo_billstatus',
            'House roll-call events and amendments': 'congress_gov_api',
            'candidacies and principal campaign committee': 'fec_candidates',
            'committee registration, finance and support': 'fec',
            'lobbying registrants, clients and contacts': 'lda_lobbying'},
        'what_this_does_not_establish': [
            'No influence claim. A lobbying registrant reporting contact with a chamber or committee is not a '
            'contact with this person, and a contribution is not a vote.',
            'Voteview historically issued new ICPSR IDs on party switches, so one person can hold several; the '
            'cluster reflects the published ID lists, not an inference.',
            'FEC candidate IDs are per office, so one person legitimately holds House and Senate IDs.',
            'No published crosswalk links LDA clients or USAspending recipients (UEI) to FEC committees\' '
            'connected organizations; those names remain literals.',
            'usaspending is outside the default profile, so federal awards to a legislator\'s district are not '
            'in this index scope.',
            'Campaign finance *amounts* are absent by scope, not by absence: fec contributes structure only '
            'under the default profile, so its 7.7M observations need "--datasets fec". The committee links '
            'shown here are the structure those amounts hang on.',
            'An OpenSanctions entry for a sitting legislator is a politically-exposed-person record, not a '
            'sanctions designation.'],
    }
    return common.emit('q4_legislator_bills_votes_money', result, save=args.save)


if __name__ == '__main__':
    main()
