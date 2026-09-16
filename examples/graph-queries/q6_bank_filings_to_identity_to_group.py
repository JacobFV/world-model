"""Q6. A US bank from its SEC filing identity to its FDIC charter and its GLEIF corporate group.

  sec_issuer_reference       the SEC filer: CIK, SIC code, venue listings, and any published LEI
    -> (asserted identity: a published LEI, or GLEIF registration authority RA000665)
  sec_gleif                  the GLEIF legal entity: legal name, legal form, jurisdiction, ISINs
    -> gleif_parent_relationships   accounting consolidation parents and fund managers
  fdic_bank_financials       the insured institution: FDIC certificate, FED_RSSD, county, call-report
                             financials
  sec_financial_statements   the filer's XBRL facts (structure only in the default profile)

The CIK-to-LEI hop is now asserted for two populations: the LEIs that sec_issuer_reference
prints for foreign private issuers, and the LEIs whose GLEIF registering authority is the SEC
(``resolution.bridges.gleif_sec_cik``). The FDIC hop is still unpublished, and the measurement
below says why rather than guessing at a name.
"""
import common

OWNERSHIP = ['ultimately_consolidated_by', 'directly_consolidated_by', 'fund_managed_by',
             'parent_reporting_exception']
# SIC codes for depository institutions and the holding companies that own them: if an SEC filer
# in a CIK<->LEI cluster is ever going to have an FDIC certificate, it is one of these.
DEPOSITORY_SIC = ('sic:6020', 'sic:6021', 'sic:6022', 'sic:6029', 'sic:6035', 'sic:6036', 'sic:6199',
                  'sic:6712')


def cik_lei_clusters(connection):
    for row in common.rows(connection, "SELECT canonical_id, GROUP_CONCAT(entity_id, '|') AS members "
                                       'FROM resolved GROUP BY canonical_id'):
        members = row['members'].split('|')
        if any(m.startswith('sec:cik:') for m in members) and any(m.startswith('lei:') for m in members):
            yield sorted(members)


def registration_authority(connection, lei):
    """The GLEIF registering authority published for an LEI, which is what the CIK bridge reads."""
    for row in common.rows(connection, "SELECT body FROM records WHERE entity_id=? AND kind='entity' "
                                       "AND dataset='sec_gleif' LIMIT 1", (lei,)):
        attributes = common.body_of(row).get('attributes') or {}
        return {'registration_authority': attributes.get('registration_authority'),
                'registration_authority_entity_id': attributes.get('registration_authority_entity_id')}
    return {}


def classifications(graph, cik):
    return {edge['object'] for edge in graph.neighborhood(cik, hops=1, predicates=['classified_as'],
                                                          limit=40)['edges']}


def main():
    args = common.parser(__doc__).parse_args()
    graph, connection = common.open_index(args.index)
    pairs = list(cik_lei_clusters(connection))
    if not pairs:
        return common.emit('q6_bank_filings_to_identity_to_group',
                           {'answer': None, 'reason': 'no asserted sec:cik <-> lei cluster in this index; '
                                                      'run "python3 -m worldmodel unify-resolve"'}, save=args.save)
    # Every FDIC-insured institution in the index, by upper-cased published name. There is no
    # published FDIC-to-CIK or FDIC-to-LEI crosswalk, so a name is all there is to try.
    low, high = common.prefix_range('fdic:cert:')
    banks = common.rows(connection, 'SELECT entity_id, body FROM records WHERE entity_id >= ? AND entity_id < ? '
                                    "AND kind='entity' LIMIT 40000", (low, high))
    names = {}
    for row in banks:
        body = common.body_of(row)
        names.setdefault((body.get('label') or '').upper(), []).append(row['entity_id'])
    ranked, depository = [], 0
    for members in pairs:
        lei = next(m for m in members if m.startswith('lei:'))
        cik = next(m for m in members if m.startswith('sec:cik:'))
        sic = classifications(graph, cik)
        is_bank = bool(sic & set(DEPOSITORY_SIC))
        depository += is_bank
        candidates = sorted({cert for key in ((common.label(connection, cik).get('label') or '').upper(),
                                              (common.label(connection, lei).get('label') or '').upper())
                             for cert in names.get(key, [])})
        group = graph.neighborhood(lei, hops=2, predicates=OWNERSHIP, limit=200, resolved=True)
        ranked.append((bool(candidates), is_bank, len(group['edges']), members, group, candidates, sorted(sic)))
    ranked.sort(key=lambda row: (not row[0], not row[1], -row[2]))
    _, _, _, members, group, name_candidates, sic = ranked[0]
    cik = next(m for m in members if m.startswith('sec:cik:'))
    lei = next(m for m in members if m.startswith('lei:'))
    filer = graph.neighborhood(cik, hops=1, limit=200)
    legal = graph.neighborhood(lei, hops=1, limit=200)
    bank_detail = []
    for cert in name_candidates[:3]:
        bank_detail.append({'fdic_entity': common.label(connection, cert),
                            'edges': common.described(connection,
                                                      graph.neighborhood(cert, hops=1, limit=40)['edges'])[:8],
                            'observations': common.observations_for(connection, cert, limit=6),
                            'link_basis': 'name string equality only - INFERRED, not asserted'})
    matched = sum(1 for row in ranked if row[0])
    result = {
        'answer': {
            'sec_filer': common.label(connection, cik),
            'gleif_legal_entity': common.label(connection, lei),
            'asserted_identity_cluster': members,
            'identity_basis': registration_authority(connection, lei),
            'sic_classification': sic,
            'filer_edges': common.described(connection, filer['edges'])[:args.limit],
            'legal_entity_edges': common.described(connection, legal['edges'])[:args.limit],
            'corporate_group_edges': common.described(connection, group['edges'])[:args.limit],
            'candidate_fdic_institutions': bank_detail},
        'counts': {'asserted_cik_lei_clusters': len(pairs),
                   'clusters_whose_filer_carries_a_depository_or_bank_holding_sic': depository,
                   'group_edges': len(group['edges']),
                   'fdic_entities_scanned': len(banks),
                   'clusters_with_any_fdic_name_match': matched,
                   'name_matched_fdic_candidates': len(name_candidates)},
        'which_dataset_supplied_which_edge': {
            'CIK, SIC classification, venue listing, published LEI': 'sec_issuer_reference',
            'CIK <-> LEI identity': 'asserted: sec_issuer_reference publishes the LEI that is the GLEIF entity ID, '
                                    'or GLEIF publishes the SEC (RA000665) as the registering authority and the CIK '
                                    'as its entity ID (worldmodel.resolution.bridges.gleif_sec_cik)',
            'legal name, legal form, jurisdiction, ISIN links': 'sec_gleif',
            'consolidation parents and fund managers': 'gleif_parent_relationships',
            'FDIC certificate, FED_RSSD, county, call-report financials': 'fdic_bank_financials',
            'FDIC <-> SEC/GLEIF link': 'NOT published. The name match shown, if any, is inferred and unreviewed.'},
        'what_this_does_not_establish': [
            'The FDIC leg is the honest failure in this chain, and it is a genuine absence rather than an '
            'extraction gap. fdic_bank_financials publishes an FDIC certificate, a FED_RSSD and a '
            'regulatory_high_holder edge to rssd:<RSSDHCR>; nothing in this catalog publishes an RSSD or an '
            'FDIC certificate for a CIK or an LEI. GLEIF does not close it either: no registration authority in '
            'the golden copy is the Federal Reserve, and the best numeric overlap between any authority\'s '
            'entity IDs and the 26,574 published FED_RSSDs is 646 of 31,564 under RA000484 (2.0%), which is '
            'what coincidence between two dense numeric ID spaces looks like.',
            'Candidate institutions shown here are name-string matches, explicitly inferred and unreviewed. '
            'They must not be treated as identity. On this catalog a held-out measurement of name matching gave '
            'precision 0.0226 and recall 0.0044 (docs/unified-graph.md).',
            'fdic_cert <-> rssd is declared 1:1 by the repo mapping specs but 943 certificate pairs in the '
            'published directory share a FED_RSSD, so unify-resolve deliberately does not cluster on rssd.',
            'GLEIF records the register the entity was incorporated in, not the SEC. A Delaware-incorporated '
            'bank holding company carries its Delaware file number, so the RA000665 bridge reaches SEC-registered '
            'funds and advisers far more often than operating banks.',
            'GLEIF Level 2 records accounting consolidation, not control or beneficial ownership.',
            'sec_financial_statements contributes structure only under the default profile: its 50.5M XBRL facts '
            'need "--datasets sec_financial_statements" (or --profile all) to be queryable here.'],
    }
    return common.emit('q6_bank_filings_to_identity_to_group', result, save=args.save)


if __name__ == '__main__':
    main()
