"""The influence panel: legislator x congress rows joining money, lobbying, committees and votes.

Two stages, both streaming, both built only from published identifiers:

``bills``
    One row per measure: BILLSTATUS sponsor, cosponsors and committee referrals; the Voteview
    and congress.gov roll calls whose *published* bill number names the measure; and the LDA
    activity reports whose specific-issue text cites the measure's number.
``panel``
    One row per (bioguide, congress, chamber): identity crosswalks, roll-call positions and
    party-line defections, FEC receipts by source type, committee-to-candidate money by the
    giving committee's published type and interest-group category, itemized individual money
    by the FEC adapter's occupation proxy and size band, sponsorship counts, lobbying on the
    bills the member sponsored, cosponsored or voted on, and committee assignments.

What counts as a join
---------------------
Every join key is an identifier some publisher asserted:

* ``bioguide`` <-> ``icpsr`` from Voteview's and congress-legislators' ``same_as`` records;
* ``bioguide`` <-> ``fec:candidate`` from congress-legislators' published FEC crosswalk;
* ``fec:candidate`` -> principal committee from the FEC candidate master;
* giving committee type and interest-group category from the FEC committee master;
* LDA client and registrant IDs exactly as LDA publishes them;
* roll call -> measure from the bill number Voteview (and congress.gov) publish on the roll call.

One link is weaker and is labelled wherever it appears: an LDA filing cites a bill *number*
in free text, and filers almost never say which Congress they mean. The number is published
by the filer; the Congress is **inferred** from the filing period (filing years 2025-2026 ->
the 119th) unless the text states it (a public-law number such as ``P.L. 116-136`` or an
ordinal such as ``117th Congress`` right after the citation). Every mention carries its
``congress_basis``. No link anywhere in this module is made by matching names.

Evidence
--------
Each row carries ``evidence``: per input, the number of input records that contributed, a
SHA-256 over their record ids, and a small sample of ids. The digest is over ids in stream
order per contributing key, then over the sorted ``key, digest, count`` lines, so a published
input version always reproduces it. Rows are plain JSON (stage format ``jsonl``) because the
generic evidence audit would re-read every one of ~170M input records.
"""
import gzip
import hashlib
import json
import math
import re

SCHEMA = 'worldmodel.panels.influence/1'
DATASET = 'influence_panel'

PARTY = {'100': 'D', '200': 'R'}
CHAMBER_LETTER = {'House': 'H', 'Senate': 'S'}
YEA = ('yea', 'paired_yea', 'announced_yea')
NAY = ('nay', 'paired_nay', 'announced_nay')
OTHER_POSITIONS = ('present', 'present_paired', 'not_voting')
BILL_TYPES = {'HR': 'hr', 'S': 's', 'HRES': 'hres', 'SRES': 'sres', 'HJRES': 'hjres', 'SJRES': 'sjres',
              'HCONRES': 'hconres', 'SCONRES': 'sconres'}
WEBALL_METRICS = ('total_receipts', 'individual_contributions', 'other_committee_contributions',
                  'party_committee_contributions', 'candidate_self_contributions', 'candidate_loans',
                  'total_disbursements', 'cash_on_hand_end')
COMMITTEE_TYPES = {
    'Q': 'pac_qualified', 'N': 'pac_nonqualified', 'O': 'super_pac_independent_expenditure_only',
    'U': 'single_candidate_independent_expenditure', 'V': 'hybrid_pac_nonqualified', 'W': 'hybrid_pac_qualified',
    'X': 'party_nonqualified', 'Y': 'party_qualified', 'Z': 'national_party_nonfederal', 'H': 'house_candidate_committee',
    'S': 'senate_candidate_committee', 'P': 'presidential_candidate_committee', 'D': 'delegate_committee',
    'E': 'electioneering_communication', 'I': 'independent_expenditor', 'C': 'communication_cost'}
INTEREST_GROUP_CATEGORIES = {'C': 'corporation', 'L': 'labor_organization', 'M': 'membership_organization',
                             'T': 'trade_association', 'V': 'cooperative', 'W': 'corporation_without_capital_stock'}

DEFAULTS = {
    'first_congress': 106,
    'last_congress': 119,
    'complete_through_congress': 118,
    'lobbying_congress': 119,
    'lobbying_filing_years': [2025, 2026],
    'lda_activity_periods': ['first_quarter', 'second_quarter', 'third_quarter', 'fourth_quarter'],
    'lda_excluded_filing_types': ['RR', 'RA'],
    'lda_amount_attribution': 'equal_split_across_distinct_bills_cited_in_the_filing',
    'direct_contribution_transaction_types': ['24K', '24Z'],
    'independent_expenditure_support_types': ['24E'],
    'independent_expenditure_oppose_types': ['24A'],
    'business_interest_group_categories': ['C', 'T', 'V', 'W'],
    'individual_contribution_datasets': {'2022': 'fec_individual_contributions_2022',
                                         '2024': 'fec_individual_contributions_2024',
                                         '2026': 'fec_individual_contributions'},
    'evidence_sample': 3,
}

CONGRESS_BASIS_INFERRED = 'inferred_from_filing_period'
LOBBYING_LINK_BASIS = ('bill number published by the filer in the LD-2 specific-issue text, parsed by '
                       'worldmodel.panels.influence.BILL_CITATION; the Congress is inferred from the filing period '
                       'unless the text states it (congress_basis on each mention)')
COMMITTEE_BASIS = ('congress-legislators committee-membership-current file as retrieved; start dates are not '
                   'published, so assignments are attached to the Congress in session at retrieval only')


def parameters(overrides=None):
    merged = json.loads(json.dumps(DEFAULTS))
    merged.update(overrides or {})
    return merged


# ----------------------------------------------------------------------------- calendar

def congress_start_year(congress):
    return 1787 + 2 * int(congress)


def congress_period(congress):
    """``(start, end)`` of a Congress since the Twentieth Amendment: Jan 3 to Jan 3 two years later."""
    year = congress_start_year(congress)
    return f'{year}-01-03', f'{year + 2}-01-03'


def cycle_of(congress):
    """The FEC two-year cycle that runs concurrently with a Congress (118th -> 2024)."""
    return congress_start_year(congress) + 1


def congress_of_year(year):
    return (int(year) - 1789) // 2 + 1


# ----------------------------------------------------------------------------- bill identifiers

BILL_CITATION = re.compile(
    r'(?<![A-Za-z0-9.])'
    r'(H\.?\s?CON\.?\s?RES|S\.?\s?CON\.?\s?RES|H\.?\s?J\.?\s?RES|S\.?\s?J\.?\s?RES|H\.?\s?RES|S\.?\s?RES|H\.?\s?R|S)'
    r'(?:\.\s?|\s)?(\d{1,5})(?![\d-])', re.I)
STATED_CONGRESS = re.compile(r'^[^;]{0,40}?(?:P\.?\s?L\.?|Public\s+Law)\s*(\d{3})-\d+|^[^;]{0,25}?\b(1\d\d)(?:th|st|nd|rd)\s+Congress',
                             re.I)


def bill_id(congress, bill_type, number):
    return f'congress:bill:{int(congress)}-{bill_type}-{int(number)}'


def voteview_bill(congress, bill_number):
    """Measure id for a Voteview ``bill_number`` such as ``HR2`` or ``SJRES7``; ``None`` otherwise."""
    if not bill_number:
        return None
    match = re.fullmatch(r'([A-Z]+)(\d+)', str(bill_number).strip().upper())
    if not match or match.group(1) not in BILL_TYPES:
        return None
    return bill_id(congress, BILL_TYPES[match.group(1)], match.group(2))


def bill_citations(text, default_congress):
    """Distinct measures cited in free text: ``[(bill_id, congress_basis)]`` in first-seen order.

    ``congress_basis`` is ``stated_in_text`` when a public-law number or a Congress ordinal
    follows the citation closely, and ``inferred_from_filing_period`` otherwise.
    """
    out, seen = [], set()
    for match in BILL_CITATION.finditer(text or ''):
        kind = re.sub(r'[.\s]', '', match.group(1)).upper()
        number = int(match.group(2))
        if kind not in BILL_TYPES or not 0 < number < 20000:
            continue
        congress, basis = default_congress, CONGRESS_BASIS_INFERRED
        stated = STATED_CONGRESS.match(text[match.end():])
        if stated:
            congress, basis = int(stated.group(1) or stated.group(2)), 'stated_in_text'
        key = bill_id(congress, BILL_TYPES[kind], number)
        if key not in seen:
            seen.add(key)
            out.append((key, basis))
    return out


def bill_parts(identifier):
    congress, kind, number = identifier.rsplit(':', 1)[1].split('-')
    return int(congress), kind, int(number)


# ----------------------------------------------------------------------------- sources and evidence

class RecordSource:
    """Streams records of pinned inputs, discarding lines by substring before JSON parsing."""

    def __init__(self, store, refs):
        self.store, self.refs = store, {ref['dataset']: dict(ref) for ref in refs}

    def has(self, dataset):
        return dataset in self.refs

    def ref(self, dataset):
        if dataset not in self.refs:
            raise ValueError(f'Undeclared input: {dataset}')
        return dict(self.refs[dataset])

    def stream(self, dataset, needles=()):
        directory = self.store.version_dir(self.ref(dataset))
        path = directory / 'records.jsonl'
        if not path.exists():
            path = directory / 'records.jsonl.gz'
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rt', encoding='utf-8') as lines:
            for line in lines:
                if needles and not any(needle in line for needle in needles):
                    continue
                yield json.loads(line)


class Trails:
    """Per-key record-id trails: a count, a running SHA-256 over ids and a small sample."""

    def __init__(self, sample=3):
        self.sample, self.data = sample, {}

    def add(self, key, record_id):
        if not record_id:
            return
        item = self.data.get(key)
        if item is None:
            item = self.data[key] = [0, hashlib.sha256(), []]
        item[0] += 1
        item[1].update(record_id.encode('utf-8') + b'\n')
        if len(item[2]) < self.sample:
            item[2].append(record_id)

    def summary(self, keys):
        found = sorted((repr(key), self.data[key]) for key in set(keys) if key in self.data)
        if not found:
            return None
        combined = hashlib.sha256()
        samples = []
        for name, (count, hasher, sample) in found:
            combined.update(f'{name}\t{hasher.hexdigest()}\t{count}\n'.encode('utf-8'))
            samples.extend(sample)
        return {'records': sum(item[0] for _, item in found), 'record_ids_sha256': combined.hexdigest(),
                'sample_record_ids': sorted(samples)[:self.sample]}


def _evidence(source, trails, parts):
    """``[{input, records, record_ids_sha256, sample_record_ids}]`` for ``{dataset: keys}``."""
    out = []
    for dataset in sorted(parts):
        summary = trails.summary(parts[dataset])
        if summary:
            out.append({'input': source.ref(dataset), **summary})
    return out


def _amount(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


# ----------------------------------------------------------------------------- stage 1: bills

def _lda_filings(source, params, trails):
    """Activity-report filings kept after amendment de-duplication, and the construction counts."""
    periods, excluded = set(params['lda_activity_periods']), set(params['lda_excluded_filing_types'])
    years = set(params['lobbying_filing_years'])
    latest, counts = {}, {'filings': 0, 'activity_reports': 0, 'superseded_by_amendment': 0}
    for record in source.stream('lda_lobbying', needles=('"entity_type":"publication"',)):
        if record.get('kind') != 'entity' or record.get('entity_type') != 'publication':
            continue
        attributes = record.get('attributes') or {}
        if not str(record.get('entity_id', '')).startswith('lda:filing:'):
            continue
        counts['filings'] += 1
        if attributes.get('filing_period') not in periods or attributes.get('filing_type') in excluded:
            continue
        if attributes.get('filing_year') not in years:
            continue
        counts['activity_reports'] += 1
        income, expenses = _amount(attributes.get('income')), _amount(attributes.get('expenses'))
        amount, basis = (income, 'income') if income is not None else ((expenses, 'expenses') if expenses is not None else (None, None))
        filing = {'filing': record['entity_id'], 'record_id': record.get('id'), 'client': attributes.get('client'),
                  'registrant': attributes.get('registrant'), 'filing_year': attributes.get('filing_year'),
                  'filing_period': attributes.get('filing_period'), 'filing_type': attributes.get('filing_type'),
                  'posted_at': attributes.get('posted_at') or '', 'amount_usd': amount, 'amount_basis': basis}
        key = (filing['registrant'], filing['client'], filing['filing_year'], filing['filing_period'])
        previous = latest.get(key)
        if previous is not None:
            counts['superseded_by_amendment'] += 1
            if (previous['posted_at'], previous['filing']) > (filing['posted_at'], filing['filing']):
                continue
        latest[key] = filing
    return {item['filing']: item for item in latest.values()}, counts


def lobbying_mentions(source, params, trails):
    """``{bill_id: [mention]}`` from LD-2 specific-issue text, plus construction counts."""
    filings, counts = _lda_filings(source, params, trails)
    default = int(params['lobbying_congress'])
    cited = {}
    counts.update({'issue_records_in_kept_filings': 0, 'issue_records_citing_a_bill': 0, 'citations': 0,
                   'citations_with_stated_congress': 0})
    for record in source.stream('lda_lobbying', needles=('"lobbying_issue"',)):
        if record.get('predicate') != 'lobbying_issue':
            continue
        filing = filings.get(record.get('subject'))
        if filing is None:
            continue
        counts['issue_records_in_kept_filings'] += 1
        value = record.get('value') or {}
        found = bill_citations(value.get('description') or '', default)
        if not found:
            continue
        counts['issue_records_citing_a_bill'] += 1
        entry = cited.setdefault(filing['filing'], {'bills': {}, 'issue_codes': set()})
        if value.get('general_issue_code'):
            entry['issue_codes'].add(value['general_issue_code'])
        for bill, basis in found:
            entry['bills'].setdefault(bill, [basis, []])[1].append(record.get('id'))
    mentions = {}
    for filing_id, entry in sorted(cited.items()):
        filing = filings[filing_id]
        share = 1.0 / len(entry['bills'])
        for bill, (basis, records) in entry['bills'].items():
            counts['citations'] += 1
            counts['citations_with_stated_congress'] += basis != CONGRESS_BASIS_INFERRED
            mention = {'filing': filing_id, 'client': filing['client'], 'registrant': filing['registrant'],
                       'filing_year': filing['filing_year'], 'filing_period': filing['filing_period'],
                       'filing_type': filing['filing_type'], 'issue_codes': sorted(entry['issue_codes']),
                       'congress_basis': basis, 'bills_cited_in_filing': len(entry['bills']),
                       'amount_usd': filing['amount_usd'], 'amount_basis': filing['amount_basis'],
                       'attributed_usd': None if filing['amount_usd'] is None else filing['amount_usd'] * share}
            mentions.setdefault(bill, []).append(mention)
            trails.add(('lda_lobbying', bill), filing['record_id'])
            for record_id in records:
                trails.add(('lda_lobbying', bill), record_id)
    return mentions, counts


def lobbying_summary(mentions, attribution=DEFAULTS['lda_amount_attribution']):
    if not mentions:
        return None
    clients = sorted({m['client'] for m in mentions if m['client']})
    registrants = sorted({m['registrant'] for m in mentions if m['registrant']})
    codes = {}
    for mention in mentions:
        for code in mention['issue_codes']:
            codes[code] = codes.get(code, 0) + 1
    amounts = [m['attributed_usd'] for m in mentions if m['attributed_usd'] is not None]
    return {'filings': len({m['filing'] for m in mentions}), 'clients': clients, 'registrants': registrants,
            'issue_codes': dict(sorted(codes.items())), 'attributed_usd': sum(amounts),
            'filings_with_reported_amount': len(amounts),
            'congress_basis': sorted({m['congress_basis'] for m in mentions}),
            'link_basis': LOBBYING_LINK_BASIS, 'amount_attribution': attribution,
            'mentions': sorted(mentions, key=lambda m: (m['filing_year'], m['filing_period'], m['filing']))}


def build_bills(source, params=None):
    """Stage ``bills``: one row per measure with its sponsors, committees, roll calls and lobbying."""
    params = parameters(params)
    trails = Trails(params['evidence_sample'])
    bills = {}

    def bill(identifier):
        item = bills.get(identifier)
        if item is None:
            congress, kind, number = bill_parts(identifier)
            item = bills[identifier] = {'bill': identifier, 'congress': congress, 'bill_type': kind, 'number': number,
                                        'in_billstatus': False, 'title': None, 'policy_area': None,
                                        'introduced_date': None, 'measure_status': None, 'sponsor': None,
                                        'cosponsors': [], 'withdrawn_cosponsors': [], 'committees': [],
                                        'roll_calls': [], 'house_roll_calls_congress_gov': []}
        return item

    needles = ('"sponsored_measure"', '"cosponsored_measure"', '"referred_to_committee"', '"entity_type":"law"')
    for record in source.stream('govinfo_billstatus', needles=needles):
        kind, predicate = record.get('kind'), record.get('predicate')
        if kind == 'entity' and record.get('entity_type') == 'law' and str(record.get('entity_id', '')).startswith('congress:bill:'):
            attributes = record.get('attributes') or {}
            item = bill(record['entity_id'])
            item.update(in_billstatus=True, title=attributes.get('short_title') or attributes.get('title'),
                        policy_area=attributes.get('policy_area'), introduced_date=attributes.get('introduced_date'),
                        measure_status=attributes.get('measure_status'))
        elif kind == 'assertion' and predicate == 'sponsored_measure':
            bill(record['object'])['sponsor'] = record['subject']
        elif kind == 'assertion' and predicate == 'cosponsored_measure':
            withdrawn = (record.get('attributes') or {}).get('withdrawn_date') or record.get('valid_to')
            target = bill(record['object'])['withdrawn_cosponsors' if withdrawn else 'cosponsors']
            if record['subject'] not in target:
                target.append(record['subject'])
        elif kind == 'assertion' and predicate == 'referred_to_committee':
            committees = bill(record['subject'])['committees']
            if record['object'] not in committees:
                committees.append(record['object'])
        else:
            continue
        target_bill = record.get('entity_id') if kind == 'entity' else (record['subject'] if predicate == 'referred_to_committee' else record['object'])
        trails.add(('govinfo_billstatus', target_bill), record.get('id'))
    first, last = int(params['first_congress']), int(params['last_congress'])
    for record in source.stream('voteview_rollcalls', needles=('"roll_call_vote"',)):
        if record.get('kind') != 'event' or record.get('event_type') != 'roll_call_vote':
            continue
        attributes = record.get('attributes') or {}
        congress = attributes.get('congress')
        if not isinstance(congress, int) or not first <= congress <= last:
            continue
        identifier = voteview_bill(congress, attributes.get('bill_number'))
        if identifier is None:
            continue
        bill(identifier)['roll_calls'].append({
            'rollcall': record['id'], 'chamber': attributes.get('chamber'), 'rollnumber': attributes.get('rollnumber'),
            'date': record.get('occurred_at'), 'question': attributes.get('vote_question'),
            'result': attributes.get('vote_result'), 'yea': attributes.get('yea_count'), 'nay': attributes.get('nay_count'),
            'bill_number_as_published': attributes.get('bill_number')})
        trails.add(('voteview_rollcalls', identifier), record['id'])
    if source.has('congress_gov_api'):
        for record in source.stream('congress_gov_api', needles=('"house_roll_call_vote"',)):
            if record.get('kind') != 'event' or record.get('event_type') != 'house_roll_call_vote':
                continue
            for participant in record.get('participants') or []:
                if participant.startswith('congress:bill:'):
                    bill(participant)['house_roll_calls_congress_gov'].append({'event': record['id'],
                                                                               'date': record.get('occurred_at')})
                    trails.add(('congress_gov_api', participant), record['id'])
    mentions, counts = lobbying_mentions(source, params, trails)
    for identifier in mentions:
        bill(identifier)
    counts['cited_bills'] = len(mentions)
    counts['cited_bills_not_in_billstatus'] = sum(1 for b in mentions if not bills[b]['in_billstatus'])
    for identifier in sorted(bills, key=bill_parts):
        item = bills[identifier]
        item['cosponsors'].sort()
        item['withdrawn_cosponsors'].sort()
        item['committees'].sort()
        item['roll_calls'].sort(key=lambda r: (r['chamber'] or '', r['rollnumber'] or 0))
        item['house_roll_calls_congress_gov'].sort(key=lambda r: r['event'])
        yield {'id': f'{DATASET}:bill:{identifier.rsplit(":", 1)[1]}', 'schema': SCHEMA, **item,
               'lobbying': lobbying_summary(mentions.get(identifier), params['lda_amount_attribution']),
               'evidence': _evidence(source, trails, {d: [(d, identifier)] for d in
                                                      ('govinfo_billstatus', 'voteview_rollcalls', 'congress_gov_api',
                                                       'lda_lobbying') if source.has(d)})}
    yield {'id': f'{DATASET}:bills:construction', 'schema': SCHEMA, 'bill': None, 'construction': counts,
           'lobbying_link_basis': LOBBYING_LINK_BASIS, 'evidence': []}


# ----------------------------------------------------------------------------- stage 2: panel

def _identity(source, trails):
    """Published crosswalks: icpsr -> bioguide, bioguide -> fec candidate ids, labels, committees."""
    icpsr_to_bioguide, conflicts, bioguide_fec, labels, committees = {}, set(), {}, {}, {}
    committee_as_of = None
    needles = ('"same_as"', '"committee_member"', '"entity_type":"person"')
    for record in source.stream('congress_people', needles=needles):
        predicate, subject = record.get('predicate'), record.get('subject')
        if record.get('kind') == 'entity' and record.get('entity_type') == 'person':
            if str(record.get('entity_id', '')).startswith('bioguide:') and record.get('label'):
                labels[record['entity_id']] = record['label']
            continue
        if record.get('kind') != 'assertion' or not str(subject or '').startswith('bioguide:'):
            continue
        target = record.get('object') or ''
        if predicate == 'same_as' and target.startswith('fec:candidate:'):
            bioguide_fec.setdefault(subject, set()).add(target)
        elif predicate == 'same_as' and target.startswith('icpsr:'):
            icpsr_to_bioguide.setdefault(target, {}).setdefault(subject, set()).add('congress_people')
        elif predicate == 'committee_member':
            attributes = record.get('attributes') or {}
            committees.setdefault(subject, []).append({'committee': target, 'title': attributes.get('title'),
                                                       'rank': attributes.get('rank'), 'side': attributes.get('side')})
            committee_as_of = max(committee_as_of or '', str(record.get('observed_at') or '')[:10])
        else:
            continue
        trails.add(('congress_people', subject), record.get('id'))
    for record in source.stream('voteview_rollcalls', needles=('"same_as"',)):
        if record.get('predicate') != 'same_as' or not str(record.get('object', '')).startswith('bioguide:'):
            continue
        icpsr_to_bioguide.setdefault(record['subject'], {}).setdefault(record['object'], set()).add('voteview_rollcalls')
        trails.add(('voteview_rollcalls', 'identity', record['subject']), record.get('id'))
    resolved = {}
    for icpsr, targets in icpsr_to_bioguide.items():
        if len(targets) == 1:
            bioguide, publishers = next(iter(targets.items()))
            resolved[icpsr] = (bioguide, sorted(publishers))
        else:
            conflicts.add(icpsr)
    return {'icpsr': resolved, 'conflicts': sorted(conflicts), 'fec': bioguide_fec, 'labels': labels,
            'committees': committees, 'committee_as_of': committee_as_of}


def _service(source, params, identity, trails):
    """Member-congress-chamber rows from Voteview service records, with published ideal points."""
    first, last = int(params['first_congress']), int(params['last_congress'])
    rows, party_of, unmatched = {}, {}, 0
    points = {}
    needles = ('"congressional_service"', '"nokken_poole_dim', '"dw_nominate_dim1"')
    for record in source.stream('voteview_rollcalls', needles=needles):
        if record.get('predicate') == 'congressional_service':
            value = record.get('value') or {}
            congress, chamber = value.get('congress'), value.get('chamber')
            if chamber not in CHAMBER_LETTER or not isinstance(congress, int) or not first <= congress <= last:
                continue
            icpsr = record['subject']
            party_of[(icpsr, congress, chamber)] = str(value.get('party_code'))
            resolved = identity['icpsr'].get(icpsr)
            if resolved is None:
                unmatched += 1
                continue
            bioguide = resolved[0]
            row = rows.setdefault((bioguide, congress, chamber), {
                'icpsr': set(), 'party_codes': set(), 'state': value.get('state_abbrev'),
                'district_code': value.get('district_code'), 'identity_publishers': set()})
            row['icpsr'].add(icpsr)
            row['party_codes'].add(str(value.get('party_code')))
            row['identity_publishers'].update(resolved[1])
            trails.add(('voteview_rollcalls', 'service', icpsr, congress, chamber), record.get('id'))
        elif record.get('kind') == 'observation':
            dimensions = record.get('dimensions') or {}
            key = (record.get('subject'), dimensions.get('congress'), dimensions.get('chamber'))
            if key[2] in CHAMBER_LETTER and isinstance(key[1], int) and first <= key[1] <= last:
                points.setdefault(key, {})[record['metric']] = record.get('value')
                trails.add(('voteview_rollcalls', 'service', *key), record.get('id'))
    return rows, party_of, points, unmatched


def _positions(source, params, party_of, trails):
    """Per (icpsr, congress, chamber) position counts and party-line defections; bills voted on."""
    first, last = int(params['first_congress']), int(params['last_congress'])
    lobbying_congress = int(params['lobbying_congress'])
    rollcall_bill = {}
    for record in source.stream('voteview_rollcalls', needles=('"roll_call_vote"',)):
        attributes = record.get('attributes') or {}
        if record.get('event_type') == 'roll_call_vote' and attributes.get('congress') == lobbying_congress:
            identifier = voteview_bill(lobbying_congress, attributes.get('bill_number'))
            if identifier:
                rollcall_bill[record['id']] = identifier
    counts, voted_bills, roll_calls = {}, {}, {}
    for record in source.stream('voteview_rollcalls', needles=('"roll_call_member_positions"',)):
        if record.get('event_type') != 'roll_call_member_positions':
            continue
        attributes = record.get('attributes') or {}
        congress, chamber = attributes.get('congress'), attributes.get('chamber')
        if chamber not in CHAMBER_LETTER or not isinstance(congress, int) or not first <= congress <= last:
            continue
        positions = attributes.get('positions') or {}
        roll_calls[(congress, chamber)] = roll_calls.get((congress, chamber), 0) + 1
        tally = {'D': [0, 0], 'R': [0, 0]}
        for name, side in (('yea', 0), ('nay', 1)):
            for icpsr in positions.get(name) or []:
                party = PARTY.get(party_of.get((f'icpsr:{icpsr}', congress, chamber)))
                if party:
                    tally[party][side] += 1
        majority = {}
        for party, (yea, nay) in tally.items():
            if yea != nay:
                majority[party] = 'yea' if yea > nay else 'nay'
        party_unity = len(majority) == 2 and majority['D'] != majority['R']
        bill = rollcall_bill.get(attributes.get('rollcall_event'))
        for name, members in positions.items():
            for number in members or []:
                icpsr = f'icpsr:{number}'
                key = (icpsr, congress, chamber)
                if key not in party_of:
                    continue
                item = counts.get(key)
                if item is None:
                    item = counts[key] = {'roll_calls_while_member': 0, 'yea': 0, 'nay': 0, 'present': 0, 'not_voting': 0,
                                          'party_unity_votes_cast': 0, 'party_line_defections': 0}
                if name == 'not_member':
                    continue
                item['roll_calls_while_member'] += 1
                if name in YEA:
                    item['yea'] += 1
                elif name in NAY:
                    item['nay'] += 1
                elif name in ('present', 'present_paired'):
                    item['present'] += 1
                else:
                    item['not_voting'] += 1
                party = PARTY.get(party_of[key])
                if party_unity and party and name in ('yea', 'nay'):
                    item['party_unity_votes_cast'] += 1
                    item['party_line_defections'] += name != majority[party]
                if bill and (name in YEA or name in NAY):
                    voted_bills.setdefault(key, set()).add(bill)
                trails.add(('voteview_rollcalls', 'positions', *key), record.get('id'))
    return counts, voted_bills, roll_calls


def _fec(source, params, candidates, trails):
    """Receipts by source type (weball) and committee-to-candidate money by giver type (pas2)."""
    registration = {}
    for record in source.stream('fec', needles=('"fec_committee_registration"',)):
        if record.get('predicate') != 'fec_committee_registration':
            continue
        value = record.get('value') or {}
        registration[(record['subject'], value.get('cycle'))] = (value.get('committee_type'), value.get('interest_group_category'))
    weball, pas2 = {}, {}
    direct = set(params['direct_contribution_transaction_types'])
    support, oppose = set(params['independent_expenditure_support_types']), set(params['independent_expenditure_oppose_types'])
    business = set(params['business_interest_group_categories'])
    needles = ('"fec_weball_candidate_summary"', '"committee_to_candidate_amount"')
    for record in source.stream('fec', needles=needles):
        if record.get('kind') != 'observation':
            continue
        dimensions = record.get('dimensions') or {}
        metric = record.get('metric')
        if dimensions.get('report_basis') == 'fec_weball_candidate_summary':
            if record.get('subject') not in candidates or metric not in WEBALL_METRICS:
                continue
            key = (record['subject'], dimensions.get('cycle'))
            item = weball.setdefault(key, {'coverage_end_date': dimensions.get('coverage_end_date')})
            value = _amount(record.get('value'))
            if value is not None:
                item[metric] = item.get(metric, 0.0) + value
            trails.add(('fec', 'weball', *key), record.get('id'))
        elif metric == 'committee_to_candidate_amount':
            candidate = dimensions.get('candidate')
            if candidate not in candidates:
                continue
            value = _amount(record.get('value'))
            if value is None:
                continue
            cycle, kind = dimensions.get('cycle'), dimensions.get('transaction_type')
            item = pas2.setdefault((candidate, cycle), {
                'direct_usd': 0.0, 'direct_by_committee_type': {}, 'direct_by_interest_group_category': {},
                'business_pac_direct_usd': 0.0, 'independent_expenditures_support_usd': 0.0,
                'independent_expenditures_oppose_usd': 0.0, 'other_transaction_types_usd': {}, 'giving_committees': set(),
                'unregistered_giver_usd': 0.0})
            if kind in direct:
                committee_type, category = registration.get((record['subject'], cycle), (None, None))
                if committee_type is None:
                    item['unregistered_giver_usd'] += value
                item['direct_usd'] += value
                type_key = committee_type or 'unregistered_in_cycle'
                item['direct_by_committee_type'][type_key] = item['direct_by_committee_type'].get(type_key, 0.0) + value
                if category:
                    table = item['direct_by_interest_group_category']
                    table[category] = table.get(category, 0.0) + value
                    if category in business and committee_type in ('Q', 'N', 'V', 'W'):
                        item['business_pac_direct_usd'] += value
                item['giving_committees'].add(record['subject'])
            elif kind in support:
                item['independent_expenditures_support_usd'] += value
            elif kind in oppose:
                item['independent_expenditures_oppose_usd'] += value
            else:
                table = item['other_transaction_types_usd']
                table[kind] = table.get(kind, 0.0) + value
            trails.add(('fec', 'pas2', candidate, cycle), record.get('id'))
    for item in pas2.values():
        item['giving_committees'] = len(item['giving_committees'])
    return weball, pas2


def _principal_committees(source, candidates, trails):
    out = {}
    for record in source.stream('fec_candidates', needles=('"principal_campaign_committee"',)):
        if record.get('predicate') != 'principal_campaign_committee' or record.get('subject') not in candidates:
            continue
        try:
            cycle = int(str(record.get('valid_to'))[:4]) - 1
        except ValueError:
            continue
        out.setdefault((record['subject'], cycle), set()).add(record['object'])
        trails.add(('fec_candidates', record['subject'], cycle), record.get('id'))
    return out


def _individual(source, params, committees, trails):
    """Itemized individual receipts of principal committees by occupation proxy and size band."""
    out = {}
    for cycle_text, dataset in sorted(params['individual_contribution_datasets'].items()):
        if not source.has(dataset):
            continue
        cycle = int(cycle_text)
        wanted = {committee for (candidate, c), items in committees.items() if c == cycle for committee in items}
        needles = ('"aggregation":"committee_occupation"', '"aggregation":"committee_size_band"')
        for record in source.stream(dataset, needles=needles):
            if record.get('kind') != 'observation' or record.get('subject') not in wanted:
                continue
            dimensions = record.get('dimensions') or {}
            if dimensions.get('cycle') != cycle or record.get('metric') != 'individual_contributions_amount':
                continue
            family = dimensions.get('aggregation')
            label = dimensions.get('occupation_category') if family == 'committee_occupation' else dimensions.get('size_band')
            value = _amount(record.get('value'))
            if label is None or value is None:
                continue
            item = out.setdefault((record['subject'], cycle), {'by_occupation_proxy_usd': {}, 'by_size_band_usd': {}})
            table = item['by_occupation_proxy_usd' if family == 'committee_occupation' else 'by_size_band_usd']
            table[label] = table.get(label, 0.0) + value
            trails.add((dataset, record['subject'], cycle), record.get('id'))
    return out


def _sum_tables(tables):
    out = {}
    for table in tables:
        for key, value in table.items():
            out[key] = out.get(key, 0.0) + value
    return dict(sorted(out.items()))


def _chamber_of_bill(bill_type):
    return 'House' if bill_type.startswith('h') else 'Senate'


def build_panel(source, bill_rows, params=None, bills_ref=None):
    """Stage ``panel``: one row per (bioguide, congress, chamber)."""
    params = parameters(params)
    trails = Trails(params['evidence_sample'])
    identity = _identity(source, trails)
    members, party_of, points, unmatched_service = _service(source, params, identity, trails)
    positions, voted_bills, roll_calls = _positions(source, params, party_of, trails)
    candidates = {c for ids in identity['fec'].values() for c in ids}
    weball, pas2 = _fec(source, params, candidates, trails)
    principal = _principal_committees(source, candidates, trails) if source.has('fec_candidates') else {}
    individual = _individual(source, params, principal, trails)
    lobbying_congress = int(params['lobbying_congress'])
    sponsorship, lobbying_bills, billstatus_congresses = {}, {}, set()
    bill_evidence = Trails(params['evidence_sample'])
    for row in bill_rows:
        if not row.get('bill'):
            continue
        congress, chamber = row['congress'], _chamber_of_bill(row['bill_type'])
        if row.get('in_billstatus'):
            billstatus_congresses.add(congress)
        relations = [('sponsored', [row['sponsor']] if row.get('sponsor') else []), ('cosponsored', row.get('cosponsors') or [])]
        for relation, people in relations:
            for bioguide in people:
                item = sponsorship.setdefault((bioguide, congress, chamber), {'sponsored': 0, 'cosponsored': 0})
                item[relation] += 1
                bill_evidence.add((bioguide, congress, chamber), row['id'])
        if congress == lobbying_congress and row.get('lobbying'):
            lobbying_bills[row['bill']] = (row['lobbying'], row['id'], relations)
    member_bills = {}
    for bill, (summary, row_id, relations) in lobbying_bills.items():
        for relation, people in relations:
            for bioguide in people:
                member_bills.setdefault(bioguide, {}).setdefault(relation, set()).add(bill)
    complete = int(params['complete_through_congress'])
    for (bioguide, congress, chamber) in sorted(members):
        member = members[(bioguide, congress, chamber)]
        icpsrs = sorted(member['icpsr'])
        party_codes = sorted(member['party_codes'])
        start, end = congress_period(congress)
        cycle = cycle_of(congress)
        letter = CHAMBER_LETTER[chamber]
        fec_ids = sorted(identity['fec'].get(bioguide, ()))
        chamber_ids = [c for c in fec_ids if c.split(':')[-1].startswith(letter)]
        key_parts = {'congress_people': [('congress_people', bioguide)],
                     'voteview_rollcalls': [('voteview_rollcalls', 'identity', i) for i in icpsrs]
                     + [('voteview_rollcalls', 'service', i, congress, chamber) for i in icpsrs]
                     + [('voteview_rollcalls', 'positions', i, congress, chamber) for i in icpsrs]}
        # roll calls
        counted = [positions[(i, congress, chamber)] for i in icpsrs if (i, congress, chamber) in positions]
        roll = None
        if counted:
            roll = {name: sum(c[name] for c in counted) for name in counted[0]}
            cast = roll['party_unity_votes_cast']
            roll['party_line_defection_rate'] = roll['party_line_defections'] / cast if cast else None
            roll['roll_calls_in_chamber'] = roll_calls.get((congress, chamber))
            roll['definition'] = ('party-unity roll call: a majority of voting Democrats (Voteview party 100) opposed a '
                                  'majority of voting Republicans (200) on yea/nay; a defection is a yea or nay against '
                                  "the member's own party majority")
        ideal = {}
        for icpsr in icpsrs:
            ideal.update(points.get((icpsr, congress, chamber), {}))
        # FEC
        receipts = [weball[(c, cycle)] for c in chamber_ids if (c, cycle) in weball]
        weball_total = None
        if receipts:
            weball_total = {metric: sum(r[metric] for r in receipts if metric in r) for metric in WEBALL_METRICS
                            if any(metric in r for r in receipts)}
            weball_total['coverage_end_date'] = max((r.get('coverage_end_date') or '') for r in receipts) or None
        pacs = [pas2[(c, cycle)] for c in chamber_ids if (c, cycle) in pas2]
        committee_money = None
        if pacs:
            committee_money = {
                'direct_usd': sum(p['direct_usd'] for p in pacs),
                'direct_by_committee_type': _sum_tables(p['direct_by_committee_type'] for p in pacs),
                'direct_by_interest_group_category': _sum_tables(p['direct_by_interest_group_category'] for p in pacs),
                'business_pac_direct_usd': sum(p['business_pac_direct_usd'] for p in pacs),
                'independent_expenditures_support_usd': sum(p['independent_expenditures_support_usd'] for p in pacs),
                'independent_expenditures_oppose_usd': sum(p['independent_expenditures_oppose_usd'] for p in pacs),
                'other_transaction_types_usd': _sum_tables(p['other_transaction_types_usd'] for p in pacs),
                'giving_committees': sum(p['giving_committees'] for p in pacs),
                'unregistered_giver_usd': sum(p['unregistered_giver_usd'] for p in pacs)}
        committees = sorted({m for c in chamber_ids for m in principal.get((c, cycle), ())})
        itemized = [individual[(m, cycle)] for m in committees if (m, cycle) in individual]
        individual_money = None
        if itemized:
            individual_money = {'by_occupation_proxy_usd': _sum_tables(i['by_occupation_proxy_usd'] for i in itemized),
                                'by_size_band_usd': _sum_tables(i['by_size_band_usd'] for i in itemized),
                                'occupation_basis': 'fec_individual_contributions adapter keyword proxy on self-reported '
                                                    'occupation (OCCUPATION_RULES v1); not a publisher industry code'}
        key_parts['fec'] = [('fec', 'weball', c, cycle) for c in chamber_ids] + [('fec', 'pas2', c, cycle) for c in chamber_ids]
        key_parts['fec_candidates'] = [('fec_candidates', c, cycle) for c in chamber_ids]
        for dataset in params['individual_contribution_datasets'].values():
            key_parts[dataset] = [(dataset, m, cycle) for m in committees]
        # sponsorship
        sponsor = sponsorship.get((bioguide, congress, chamber))
        if sponsor is None and congress in billstatus_congresses:
            sponsor = {'sponsored': 0, 'cosponsored': 0}
        # lobbying
        lobbying = None
        if congress == lobbying_congress:
            relations = dict(member_bills.get(bioguide, {}))
            voted = set()
            for icpsr in icpsrs:
                voted |= {b for b in voted_bills.get((icpsr, congress, chamber), ()) if b in lobbying_bills}
            relations['voted'] = voted
            lobbying = {'link_basis': LOBBYING_LINK_BASIS, 'amount_attribution': params['lda_amount_attribution']}
            union = set()
            for relation in ('sponsored', 'cosponsored', 'voted'):
                chosen = sorted(b for b in relations.get(relation, ()) if _chamber_of_bill(bill_parts(b)[1]) == chamber
                                or relation == 'voted')
                union.update(chosen)
                lobbying[relation] = _lobbying_block(chosen, lobbying_bills)
            lobbying['any'] = _lobbying_block(sorted(union), lobbying_bills)
            for bill in union:
                bill_evidence.add((bioguide, congress, chamber), lobbying_bills[bill][1])
        # committees
        assignments = None
        if congress == lobbying_congress and bioguide in identity['committees']:
            chamber_prefix = 'congress:committee:' + letter.lower()
            assignments = sorted((a for a in identity['committees'][bioguide] if a['committee'].startswith(chamber_prefix)
                                  or a['committee'].startswith('congress:committee:j')), key=lambda a: a['committee'])
        evidence = _evidence(source, trails, key_parts)
        stage_summary = bill_evidence.summary([(bioguide, congress, chamber)])
        if stage_summary and bills_ref:
            evidence.append({'input': dict(bills_ref), **stage_summary})
        parties = {PARTY.get(code, code) for code in party_codes}
        yield {
            'id': f'{DATASET}:{bioguide}:{congress}:{chamber}', 'schema': SCHEMA,
            'unit': bioguide, 'label': identity['labels'].get(bioguide), 'congress': congress, 'chamber': chamber,
            'period_start': start, 'period_end': end, 'period_complete': congress <= complete, 'cycle': cycle,
            'state': member['state'], 'district_code': member['district_code'],
            'party_codes': party_codes, 'party': next(iter(parties)) if len(parties) == 1 else 'mixed',
            'identity': {'icpsr': icpsrs, 'fec_candidate_ids': fec_ids, 'fec_candidate_ids_for_chamber': chamber_ids,
                         'principal_committees_in_cycle': committees,
                         'icpsr_bioguide_published_by': sorted(member['identity_publishers']),
                         'basis': 'published same_as crosswalks only (Voteview, congress-legislators); no name matching'},
            'roll_calls': roll, 'ideal_points': ideal or None,
            'receipts_by_source': weball_total, 'committee_contributions': committee_money,
            'individual_itemized': individual_money,
            'sponsorship': sponsor, 'lobbying_on_linked_bills': lobbying,
            'committee_assignments': None if assignments is None else {
                'assignments': assignments, 'as_of': identity['committee_as_of'], 'basis': COMMITTEE_BASIS},
            'linked': {'fec_candidate_id': bool(chamber_ids), 'receipts_by_source': weball_total is not None,
                       'committee_contributions': committee_money is not None,
                       'individual_itemized': individual_money is not None, 'roll_calls': roll is not None,
                       'sponsorship': bool(sponsor and (sponsor['sponsored'] or sponsor['cosponsored'])),
                       'lobbying': bool(lobbying and lobbying['any']['bills']),
                       'committee_assignments': bool(assignments)},
            'evidence': evidence}
    yield {'id': f'{DATASET}:panel:construction', 'schema': SCHEMA, 'unit': None,
           'construction': {'member_congress_chamber_rows': len(members), 'service_rows_without_published_bioguide': unmatched_service,
                            'icpsr_with_conflicting_bioguide': identity['conflicts'],
                            'candidates_crosswalked': len(candidates), 'lobbying_bills_with_citations': len(lobbying_bills),
                            'billstatus_congresses': sorted(billstatus_congresses)},
           'evidence': []}


def _lobbying_block(bills, lobbying_bills):
    filings, clients, registrants, amount = set(), set(), set(), 0.0
    for bill in bills:
        summary = lobbying_bills[bill][0]
        filings.update(m['filing'] for m in summary['mentions'])
        clients.update(summary['clients'])
        registrants.update(summary['registrants'])
        amount += summary['attributed_usd']
    return {'bills': bills, 'filings': len(filings), 'clients': len(clients), 'registrants': len(registrants),
            'attributed_usd': amount}


# ----------------------------------------------------------------------------- coverage and selection

def coverage(rows):
    """Share of rows (and of distinct legislators) with each input linked, overall and per congress."""
    rows = [r for r in rows if r.get('unit')]
    keys = sorted({k for r in rows for k in r['linked']})
    by_congress = {}
    for row in rows:
        by_congress.setdefault((row['congress'], row['chamber']), []).append(row)

    def shares(group):
        return {k: round(sum(bool(r['linked'][k]) for r in group) / len(group), 4) for k in keys} if group else {}

    legislators = {}
    for row in rows:
        entry = legislators.setdefault(row['unit'], {k: False for k in keys})
        for k in keys:
            entry[k] = entry[k] or bool(row['linked'][k])
    return {'rows': len(rows), 'legislators': len(legislators), 'row_share': shares(rows),
            'legislator_share': {k: round(sum(v[k] for v in legislators.values()) / len(legislators), 4) for k in keys}
            if legislators else {},
            'by_congress_chamber': {f'{c}:{ch}': {'rows': len(g), **shares(g)} for (c, ch), g in sorted(by_congress.items())}}


EXPOSURES = ('pac_share_of_receipts_pct', 'log_business_pac_direct_thousands')
OUTCOMES = ('party_defection_rate_pct',)


def panel_observation(row, *, outcome='party_defection_rate_pct', exposure='pac_share_of_receipts_pct',
                      min_party_unity_votes=20):
    """``(values, None)`` or ``(None, reason)`` for one panel row under a declared outcome/exposure."""
    if outcome not in OUTCOMES:
        raise ValueError(f'Unknown outcome {outcome!r}; declared: {OUTCOMES}')
    if exposure not in EXPOSURES:
        raise ValueError(f'Unknown exposure {exposure!r}; declared: {EXPOSURES}')
    if row.get('party') not in ('D', 'R'):
        return None, 'not_a_single_major_party'
    roll = row.get('roll_calls')
    if not roll:
        return None, 'no_member_positions'
    if roll['party_unity_votes_cast'] < min_party_unity_votes:
        return None, 'too_few_party_unity_votes'
    receipts = row.get('receipts_by_source') or {}
    if exposure == 'pac_share_of_receipts_pct':
        total, pacs = receipts.get('total_receipts'), receipts.get('other_committee_contributions')
        if total is None or pacs is None or total <= 0:
            return None, 'no_positive_receipts'
        value = 100.0 * max(pacs, 0.0) / total
    else:
        if not row['linked'].get('fec_candidate_id') or not receipts:
            return None, 'no_positive_receipts'
        money = row.get('committee_contributions') or {}
        value = math.log1p(max(money.get('business_pac_direct_usd', 0.0), 0.0) / 1000.0)
    return {'outcome': 100.0 * roll['party_line_defection_rate'], 'exposure': value}, None
