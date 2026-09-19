"""Q7. Who moves on this bill: sponsors, committees of referral, roll-call movers, lobbyists and money.

A descriptive brief for one measure, read from the published ``influence_panel`` derived dataset and
from the exact input versions its manifest pins:

  influence_panel/bills   the measure's sponsor, cosponsors, committees of referral, roll calls and
                          the LD-2 filings whose specific-issue text cites its number
  influence_panel/panel   each member's party, committee assignments and FEC money in the cycle
  voteview_rollcalls      member positions on the measure's roll calls (who broke with their party)
  congress_people         committee names
  lda_lobbying            client and registrant names as LDA publishes them

It does not use the unified index, so it answers from the same versions the panel was built from.
"""
import json

import common
from worldmodel.panels.influence import CONGRESS_BASIS_INFERRED, INTEREST_GROUP_CATEGORIES, PARTY
from worldmodel.store import Store
from worldmodel.resources import resource_roots


def stream(store, ref, needles=()):
    from worldmodel.estimation.loaders import stream_records
    yield from stream_records(store, ref, needles=needles)


def parent_committee(committee):
    return committee[:-2] + '00' if committee and committee[-2:].isdigit() else committee


def pick_bill(store, bills_ref, wanted):
    """The named measure, or the 119th-Congress measure cited by the most LDA clients that has a roll call."""
    best = None
    for row in stream(store, bills_ref, needles=(f'"bill":"{wanted}"',) if wanted else ('"lobbying":{',)):
        if not row.get('bill'):
            continue
        if wanted:
            if row['bill'] == wanted:
                return row
            continue
        if not row.get('in_billstatus') or not row.get('roll_calls') or not row.get('lobbying'):
            continue
        key = (len(row['lobbying']['clients']), row['lobbying']['filings'], row['bill'])
        if best is None or key > best[0]:
            best = (key, row)
    return best[1] if best else None


def main():
    options = common.parser(__doc__)
    options.add_argument('--bill', help='e.g. congress:bill:119-hr-1')
    options.add_argument('--data', default=None, help='data root (default: WORLD_MODEL_DATA or the checkout)')
    args = options.parse_args()
    store = Store(args.data or resource_roots()['data'])
    panel_ref = store.latest('influence_panel', 'panel')
    manifest = store.manifest(panel_ref)
    pins = {ref['dataset'] if ref['dataset'] != 'influence_panel' else 'influence_panel/' + ref['stage']: ref
            for ref in manifest['inputs']}
    bills_ref = pins['influence_panel/bills']
    bill = pick_bill(store, bills_ref, args.bill)
    if bill is None:
        return common.emit('q7_who_moves_on_this_bill', {'answer': None, 'reason': f'no such measure {args.bill}'},
                           save=args.save)
    congress = bill['congress']

    # Members of this Congress from the panel: party, committees and the cycle's money.
    members, by_icpsr = {}, {}
    for row in stream(store, panel_ref, needles=(f'"congress":{congress},',)):
        if row.get('unit') and row['congress'] == congress:
            members[(row['unit'], row['chamber'])] = row
            for icpsr in row['identity']['icpsr']:
                by_icpsr[(icpsr, row['chamber'])] = row

    def who(bioguide, chamber=None):
        rows = [r for (b, c), r in members.items() if b == bioguide and (chamber is None or c == chamber)]
        row = rows[0] if rows else None
        return {'bioguide': bioguide, 'label': row['label'] if row else None, 'party': row['party'] if row else None,
                'state': row['state'] if row else None, 'chamber': row['chamber'] if row else chamber}

    # Committees of referral and the members the current membership file places on them.
    referral = sorted({parent_committee(c) for c in bill['committees']})
    committee_names = {}
    for record in stream(store, pins['congress_people'], needles=('"entity_type":"institution"',)):
        if record.get('entity_id') in referral:
            committee_names[record['entity_id']] = record.get('label')
    seats = []
    for row in members.values():
        for seat in ((row.get('committee_assignments') or {}).get('assignments') or []):
            if seat['committee'] in referral:
                seats.append({**who(row['unit'], row['chamber']), 'committee': seat['committee'],
                              'title': seat.get('title'), 'rank': seat.get('rank'), 'side': seat.get('side')})
    seats.sort(key=lambda s: (s['committee'], s['title'] is None, s['side'] or '', s['rank'] or 999))

    # Roll calls: party tallies and the members who voted against their own party majority.
    roll_ids = {r['rollcall']: r for r in bill['roll_calls']}
    roll_calls = []
    for record in stream(store, pins['voteview_rollcalls'], needles=('"roll_call_member_positions"',)):
        attributes = record.get('attributes') or {}
        meta = roll_ids.get(attributes.get('rollcall_event'))
        if meta is None:
            continue
        chamber = attributes['chamber']
        positions = attributes.get('positions') or {}
        tally = {}
        for name in ('yea', 'nay'):
            for number in positions.get(name) or []:
                row = by_icpsr.get((f'icpsr:{number}', chamber))
                party = row['party'] if row else 'unlinked'
                tally.setdefault(party, {'yea': 0, 'nay': 0})[name] += 1
        majority = {p: ('yea' if t['yea'] > t['nay'] else 'nay') for p, t in tally.items()
                    if p in PARTY.values() and t['yea'] != t['nay']}
        movers = []
        for name in ('yea', 'nay'):
            for number in positions.get(name) or []:
                row = by_icpsr.get((f'icpsr:{number}', chamber))
                if row and row['party'] in majority and majority[row['party']] != name:
                    movers.append({**who(row['unit'], chamber), 'voted': name, 'party_majority': majority[row['party']]})
        roll_calls.append({'rollcall': meta['rollcall'], 'date': meta['date'], 'question': meta['question'],
                           'result': meta['result'], 'party_tally': dict(sorted(tally.items())),
                           'party_majority': majority, 'voted_against_own_party': movers[:args.limit],
                           'voted_against_own_party_count': len(movers), 'from_dataset': 'voteview_rollcalls'})
    roll_calls.sort(key=lambda r: (r['date'] or '', r['rollcall']))

    # Lobbying: who filed reports citing this measure's number.
    lobbying = bill.get('lobbying') or {}
    mentions = lobbying.get('mentions') or []
    clients = {}
    for mention in mentions:
        item = clients.setdefault(mention['client'], {'filings': 0, 'registrants': set(), 'attributed_usd': 0.0,
                                                      'issue_codes': set()})
        item['filings'] += 1
        item['registrants'].add(mention['registrant'])
        item['attributed_usd'] += mention['attributed_usd'] or 0.0
        item['issue_codes'].update(mention['issue_codes'])
    top = sorted(clients.items(), key=lambda kv: (-kv[1]['filings'], -kv[1]['attributed_usd'], kv[0]))[:args.limit]
    wanted = {c for c, _ in top} | {r for _, v in top for r in v['registrants']}
    names = {}
    for record in stream(store, pins['lda_lobbying'], needles=('"entity_type":"organization"',)):
        if record.get('entity_id') in wanted:
            names[record['entity_id']] = record.get('label')
    top_clients = [{'client': c, 'client_label': names.get(c), 'filings': v['filings'],
                    'registrants': [{'registrant': r, 'label': names.get(r)} for r in sorted(v['registrants'])],
                    'attributed_usd': round(v['attributed_usd'], 2), 'issue_codes': sorted(v['issue_codes']),
                    'from_dataset': 'lda_lobbying'} for c, v in top]

    # Money received this cycle by the members positioned on the measure (not money about the measure).
    positioned = {}
    sponsor_chamber = 'House' if bill['bill_type'].startswith('h') else 'Senate'
    for role, people in (('sponsor', [bill['sponsor']] if bill.get('sponsor') else []),
                         ('cosponsor', bill.get('cosponsors') or [])):
        for bioguide in people:
            positioned.setdefault((bioguide, sponsor_chamber), set()).add(role)
    for seat in seats:
        positioned.setdefault((seat['bioguide'], seat['chamber']), set()).add('committee_of_referral')
    money, by_category = [], {}
    for (bioguide, chamber), roles in positioned.items():
        row = members.get((bioguide, chamber))
        committee_money = (row or {}).get('committee_contributions') or {}
        receipts = (row or {}).get('receipts_by_source') or {}
        for code, amount in (committee_money.get('direct_by_interest_group_category') or {}).items():
            by_category[code] = by_category.get(code, 0.0) + amount
        money.append({**who(bioguide, chamber), 'roles': sorted(roles), 'cycle': row['cycle'] if row else None,
                      'total_receipts_usd': receipts.get('total_receipts'),
                      'other_committee_contributions_usd': receipts.get('other_committee_contributions'),
                      'pac_direct_usd': committee_money.get('direct_usd'),
                      'business_pac_direct_usd': committee_money.get('business_pac_direct_usd'),
                      'from_dataset': 'fec'})
    money.sort(key=lambda m: -(m['pac_direct_usd'] or 0.0))

    basis = {}
    for mention in mentions:
        basis[mention['congress_basis']] = basis.get(mention['congress_basis'], 0) + 1
    result = {
        'answer': {
            'measure': {'bill': bill['bill'], 'title': bill['title'], 'policy_area': bill['policy_area'],
                        'introduced_date': bill['introduced_date'], 'status': bill['measure_status'],
                        'from_dataset': 'govinfo_billstatus'},
            'sponsor': who(bill['sponsor'], sponsor_chamber) if bill.get('sponsor') else None,
            'cosponsors': {'count': len(bill.get('cosponsors') or []),
                           'by_party': _count(who(b, sponsor_chamber)['party'] for b in bill.get('cosponsors') or []),
                           'withdrawn': len(bill.get('withdrawn_cosponsors') or []), 'from_dataset': 'govinfo_billstatus'},
            'committees_of_referral': [{'committee': c, 'label': committee_names.get(c)} for c in referral],
            'committee_seats_on_referral_committees': {'count': len(seats), 'leadership': [s for s in seats if s['title']],
                                                       'from_dataset': 'congress_people'},
            'roll_calls': roll_calls,
            'lobbying': {'filings_citing_the_number': lobbying.get('filings', 0), 'clients': len(lobbying.get('clients', [])),
                         'registrants': len(lobbying.get('registrants', [])), 'issue_codes': lobbying.get('issue_codes'),
                         'attributed_usd': lobbying.get('attributed_usd'), 'congress_basis_of_citations': basis,
                         'top_clients': top_clients},
            'money_to_positioned_members': {
                'members': money[:args.limit],
                'pac_direct_by_interest_group_category_usd': {
                    f'{code} ({INTEREST_GROUP_CATEGORIES.get(code, "unpublished code")})': round(v, 2)
                    for code, v in sorted(by_category.items())}}},
        'counts': {'members_in_congress': len(members), 'roll_calls': len(roll_calls),
                   'lda_filings_citing_the_number': lobbying.get('filings', 0),
                   'committee_seats_on_referral_committees': len(seats), 'positioned_members': len(positioned)},
        'pinned_inputs': {'influence_panel/panel': panel_ref, **{k: v for k, v in pins.items()}},
        # Read directly: the panel stages, voteview positions, congress_people committee names, LDA labels.
        # Read through the panel: BILLSTATUS, FEC and the FEC candidate master.
        'datasets_used': ['congress_people', 'fec', 'fec_candidates', 'govinfo_billstatus', 'influence_panel',
                          'lda_lobbying', 'voteview_rollcalls'],
        'which_dataset_supplied_which_edge': {
            'measure, sponsor, cosponsors, committees of referral': 'govinfo_billstatus (via influence_panel/bills)',
            'roll call -> measure': 'voteview_rollcalls published bill_number (via influence_panel/bills)',
            'member positions on those roll calls': 'voteview_rollcalls roll_call_member_positions',
            'icpsr -> bioguide': 'voteview_rollcalls and congress_people same_as (via influence_panel/panel)',
            'committee seats and titles': 'congress_people committee-membership-current',
            'LDA filing -> measure': 'lda_lobbying specific-issue text citing the bill number; Congress inferred from '
                                     'the filing period unless stated (congress_basis)',
            'LDA client and registrant': 'lda_lobbying published IDs and labels',
            'money received by each member': 'fec weball and pas2 via congress_people fec same_as (via influence_panel/panel)'},
        'what_this_does_not_establish': [
            'No influence and no causation. A filing that cites this bill says a registrant lobbied on it for a client; it '
            'does not say whom they met, what they asked for, which side they took, or that any member acted on it.',
            'The money shown is everything a positioned member\'s campaign received from committees in the cycle, not money '
            'about this bill. No published crosswalk links an LDA client to an FEC committee, so lobbying and money are '
            'shown side by side and never joined.',
            'The Congress of a cited bill number is inferred from the filing period for %d of %d citations; a filer '
            'citing an older bill with the same number is counted here.' % (basis.get(CONGRESS_BASIS_INFERRED, 0),
                                                                           len(mentions)),
            'Attributed dollars split each filing\'s reported income or expenses equally across the bills it cites; '
            'filings under $5,000 report no amount.',
            'Committee seats come from the current membership file with no start dates; a seat may postdate the referral.',
            'Voting against one\'s party majority on a roll call is a descriptive fact about that vote, not evidence '
            'about why.',
            'LDA coverage is filing years 2025-2026 only (a partial acquisition), so lobbying on earlier Congresses '
            'is absent, not zero.'],
    }
    return common.emit('q7_who_moves_on_this_bill', result, save=args.save)


def _count(values):
    out = {}
    for value in values:
        out[value] = out.get(value, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: str(kv[0])))


if __name__ == '__main__':
    main()
