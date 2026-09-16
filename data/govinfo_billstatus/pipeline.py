"""GovInfo BILLSTATUS XML (one document per measure inside per-congress/type ZIPs) -> legislative graph.

Emits, per measure ``congress:bill:{congress}-{type}-{number}`` (entity_type ``law`` with an explicit
``measure_status``; introduction never implies enactment):
* sponsor / cosponsor assertions from ``bioguide:`` members (cosponsorship dated, withdrawals end validity)
* committee referrals ``congress:committee:{systemCode}`` with activity history
* policy area and legislative subjects, related measures, public law numbers
* one ``legislative_action`` event per action (with recorded roll-call references)
XML is parsed one member at a time with the standard library.
"""
import re
import zipfile
import xml.etree.ElementTree as ET
from worldmodel.util import digest

MAX_MEMBER_BYTES = 64 * 1024 * 1024
TYPES = ('hr', 's', 'hjres', 'sjres', 'hconres', 'sconres', 'hres', 'sres')


def clip(value, limit=600):
    if value is None:
        return None
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + '…'


def text(element, path):
    found = element.find(path)
    return found.text.strip() if found is not None and found.text and found.text.strip() else None


def measure_id(congress, kind, number):
    return f'congress:bill:{congress}-{kind.lower()}-{number}'


def iter_members(shard):
    with zipfile.ZipFile(shard['path']) as archive:
        for info in archive.infolist():
            if info.is_dir() or not info.filename.lower().endswith('.xml'):
                continue
            if info.file_size > MAX_MEMBER_BYTES:
                raise ValueError(f"{info.filename}: XML member exceeds {MAX_MEMBER_BYTES} bytes")
            yield info.filename, archive.read(info)


def run(context):
    dataset = context.definition['id']
    for index, ref in enumerate(context.raw_inputs):
        observed_default = context.raw_receipt(index)['retrieved_at']
        for shard in context.raw_shards(index):
            observed = shard.get('retrieved_at') or observed_default
            for member, content in iter_members(shard):
                locator = f'shard:{shard["index"]}/member:{member}'
                root = ET.fromstring(content)
                bill = root.find('bill')
                if bill is None:
                    raise ValueError(f'{locator}: missing <bill>')
                emitted = set()  # BILLSTATUS can repeat an item (e.g. the same cosponsor and date twice); keep the first
                for record in measure_records(dataset, ref, observed, locator, bill):
                    if record['id'] not in emitted:
                        emitted.add(record['id'])
                        yield record


def measure_records(dataset, ref, observed, locator, bill):
    # BILLSTATUS 3.x uses <type>/<number>; a few legacy documents still use <billType>/<billNumber>.
    congress = text(bill, 'congress')
    kind = (text(bill, 'type') or text(bill, 'billType') or '').lower()
    number = text(bill, 'number') or text(bill, 'billNumber')
    if not congress or kind not in TYPES or not number:
        raise ValueError(f'{locator}: BILLSTATUS measure lacks congress/type/number')
    measure = measure_id(congress, kind, number)
    key = f'{congress}-{kind}-{number}'
    evidence = [{'input': ref, 'locator': locator}]

    def rec(record, **attributes):
        record.update(observed_at=observed, evidence=evidence, attributes={'source_dataset': dataset, **attributes})
        return record

    laws = [{'type': text(item, 'type'), 'number': text(item, 'number')} for item in bill.findall('laws/item')]
    latest = bill.find('latestAction')
    introduced = text(bill, 'introducedDate')
    policy = text(bill, 'policyArea/name')
    titles = {text(item, 'titleType'): text(item, 'title') for item in bill.findall('titles/item')}
    yield rec({'kind': 'entity', 'id': f'{dataset}:measure:{key}', 'entity_id': measure, 'entity_type': 'law',
               'label': f"{kind.upper()} {number} ({congress}th): {clip(text(bill, 'title') or '', 200)}"},
              measure_kind=kind, congress=int(congress), number=number, title=clip(text(bill, 'title'), 1000),
              short_title=clip(titles.get('Short Title(s) as Enacted') or titles.get('Short Title(s) as Introduced'), 300),
              introduced_date=introduced, origin_chamber=text(bill, 'originChamber'), policy_area=policy,
              latest_action={'date': text(latest, 'actionDate'), 'text': clip(text(latest, 'text'))} if latest is not None else None,
              public_laws=laws or None, update_date=text(bill, 'updateDate'),
              measure_status='enacted as public/private law' if laws else 'not enacted as of source update',
              congress_gov_url=text(bill, 'legislationUrl'))
    for position, sponsor in enumerate(bill.findall('sponsors/item')):
        bioguide = text(sponsor, 'bioguideId')
        if bioguide:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:sponsor:{key}:{bioguide}', 'subject': 'bioguide:' + bioguide,
                       'predicate': 'sponsored_measure', 'object': measure, **({'valid_from': introduced} if introduced else {})},
                      party=text(sponsor, 'party'), state=text(sponsor, 'state'), district=text(sponsor, 'district'),
                      by_request=text(sponsor, 'isByRequest') == 'Y')
    for cosponsor in bill.findall('cosponsors/item'):
        bioguide, start, withdrawn = text(cosponsor, 'bioguideId'), text(cosponsor, 'sponsorshipDate'), text(cosponsor, 'sponsorshipWithdrawnDate')
        if not bioguide:
            continue
        window = {'valid_from': start} if start else {}
        if withdrawn and (not start or withdrawn > start):
            window['valid_to'] = withdrawn
        yield rec({'kind': 'assertion', 'id': f'{dataset}:cosponsor:{key}:{bioguide}:{start}', 'subject': 'bioguide:' + bioguide,
                   'predicate': 'cosponsored_measure', 'object': measure, **window},
                  original_cosponsor=text(cosponsor, 'isOriginalCosponsor') == 'True', party=text(cosponsor, 'party'),
                  state=text(cosponsor, 'state'), district=text(cosponsor, 'district'), withdrawn_date=withdrawn)
    for committee in bill.findall('committees/item') + bill.findall('committees/billCommittees/item'):
        yield from committee_records(dataset, rec, key, measure, committee)
        for sub in committee.findall('subcommittees/item'):
            yield from committee_records(dataset, rec, key, measure, sub, parent=text(committee, 'systemCode'))
    subjects = sorted({text(item, 'name') for item in bill.findall('subjects/legislativeSubjects/item') if text(item, 'name')})
    if policy:
        yield rec({'kind': 'assertion', 'id': f'{dataset}:policy_area:{key}', 'subject': measure, 'predicate': 'policy_area', 'value': policy},
                  classification='CRS policy area')
    if subjects:
        yield rec({'kind': 'assertion', 'id': f'{dataset}:subjects:{key}', 'subject': measure, 'predicate': 'legislative_subjects',
                   'value': subjects}, classification='CRS legislative subject terms')
    for law in laws:
        law_date = next((text(action, 'actionDate') for action in bill.findall('actions/item')
                         if (text(action, 'text') or '').startswith('Became Public Law') or text(action, 'actionCode') in ('36000', 'E40000')), None)
        yield rec({'kind': 'assertion', 'id': f"{dataset}:law:{key}:{law['number']}", 'subject': measure, 'predicate': 'became_law',
                   'value': law, **({'valid_from': law_date} if law_date else {})})
    related = {}
    for item in bill.findall('relatedBills/item'):
        other = measure_id(text(item, 'congress'), text(item, 'type') or '', text(item, 'number'))
        details = sorted({(text(d, 'type'), text(d, 'identifiedBy')) for d in item.findall('relationshipDetails/item')}, key=str)
        related.setdefault(other, set()).update(details)
    for other, details in sorted(related.items()):
        yield rec({'kind': 'assertion', 'id': f'{dataset}:related:{key}:' + other.rsplit(':', 1)[1], 'subject': measure,
                   'predicate': 'related_measure', 'object': other},
                  relationships=[{'type': t, 'identified_by': i} for t, i in sorted(details, key=str)])
    for number_, report in enumerate(bill.findall('committeeReports/committeeReport')):
        citation = text(report, 'citation')
        if citation:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:report:{key}:{number_}', 'subject': measure,
                       'predicate': 'committee_report_citation', 'value': citation})
    seen_actions = set()
    for action in bill.findall('actions/item'):
        day = text(action, 'actionDate')
        if not day:
            continue
        committees = sorted({'congress:committee:' + text(c, 'systemCode') for c in action.findall('committees/item') if text(c, 'systemCode')})
        votes = [{'chamber': text(v, 'chamber'), 'congress': text(v, 'congress'), 'session': text(v, 'sessionNumber'),
                  'roll_number': text(v, 'rollNumber'), 'date': text(v, 'date')} for v in action.findall('recordedVotes/recordedVote')]
        identity = [day, text(action, 'actionTime'), text(action, 'actionCode'), text(action, 'text'), text(action, 'sourceSystem/code')]
        action_key = digest(identity)[:24]
        if action_key in seen_actions:
            continue
        seen_actions.add(action_key)
        yield rec({'kind': 'event', 'id': f'{dataset}:action:{key}:{action_key}', 'event_type': 'legislative_action',
                   'occurred_at': day, 'participants': [measure, *committees]},
                  action_time=text(action, 'actionTime'), action_type=text(action, 'type'), action_code=text(action, 'actionCode'),
                  source_system=text(action, 'sourceSystem/name'), text=clip(text(action, 'text')), recorded_votes=votes or None)


def committee_records(dataset, rec, key, measure, committee, parent=None):
    code = text(committee, 'systemCode')
    if not code:
        return
    activities = sorted(({'name': text(a, 'name'), 'date': text(a, 'date')} for a in committee.findall('activities/item')),
                        key=lambda a: a['date'] or '')
    start = activities[0]['date'][:10] if activities and activities[0]['date'] else None
    yield rec({'kind': 'assertion', 'id': f'{dataset}:committee:{key}:{code}', 'subject': measure,
               'predicate': 'referred_to_committee', 'object': 'congress:committee:' + code, **({'valid_from': start} if start else {})},
              committee_name=text(committee, 'name'), chamber=text(committee, 'chamber'), parent_committee=parent,
              activities=activities, validity_basis='earliest committee activity date; end of referral not published')

