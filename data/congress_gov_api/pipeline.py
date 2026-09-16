"""congress.gov API v3 list pages -> amendments, nominations, committee reports, treaties, House roll calls, committees.

Raw shards hold one whole JSON page per line (no records_path), so each page is dispatched by its
list key. Identifiers join GovInfo BILLSTATUS and congress_people:
``congress:bill:{congress}-{type}-{number}``, ``congress:amendment:{congress}-{type}-{number}``,
``congress:committee:{systemCode}``.
"""
from worldmodel.util import digest

LISTS = ('amendments', 'nominations', 'reports', 'treaties', 'houseRollCallVotes', 'committees')


def clip(value, limit=1000):
    if not isinstance(value, str):
        return value
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + '…'


def action(item):
    latest = item.get('latestAction') or {}
    if not latest:
        return None
    return {'date': latest.get('actionDate'), 'time': latest.get('actionTime'), 'text': clip(latest.get('text'), 600)}


def run(context):
    dataset = context.definition['id']
    emitted = set()
    for index, ref in enumerate(context.raw_inputs):
        observed_default = context.raw_receipt(index)['retrieved_at']
        observed_by_shard = {s['index']: s.get('retrieved_at') or observed_default for s in context.raw_shards(index)}
        for locator, page in context.raw_rows(index, format='jsonl'):
            shard_index = int(locator.split('/', 1)[0].split(':')[1])
            observed = observed_by_shard.get(shard_index, observed_default)
            for key in LISTS:
                for position, item in enumerate(page.get(key) or []):
                    evidence = [{'input': ref, 'locator': f'{locator}/{key}:{position}'}]
                    for record in convert(dataset, key, item):
                        if record['id'] in emitted:
                            continue
                        emitted.add(record['id'])
                        record['observed_at'] = observed
                        record['evidence'] = evidence
                        record['attributes'] = {'source_dataset': dataset, **record.get('attributes', {})}
                        yield record


def convert(dataset, key, item):
    if key == 'amendments':
        congress, kind, number = item['congress'], item['type'].lower(), str(item['number'])
        entity = f'congress:amendment:{congress}-{kind}-{number}'
        yield {'kind': 'entity', 'id': f'{dataset}:amendment:{congress}-{kind}-{number}', 'entity_id': entity,
               'entity_type': 'law', 'label': f"{item['type']} {number} ({congress}th Congress)",
               'attributes': {'measure_kind': 'amendment', 'congress': congress, 'amendment_type': item['type'],
                              'number': number, 'description': clip(item.get('description')),
                              'purpose': clip(item.get('purpose')), 'latest_action': action(item),
                              'update_date': item.get('updateDate'),
                              'legal_status': 'proposed amendment; adoption only as stated in latest action text'}}
    elif key == 'nominations':
        congress, citation = item['congress'], item['citation']
        received = item.get('receivedDate')
        if not received:
            return
        kind = item.get('nominationType') or {}
        yield {'kind': 'event', 'id': f'{dataset}:nomination:{congress}-{citation}', 'event_type': 'presidential_nomination_received',
               'occurred_at': received, 'participants': ['us:congress:senate'],
               'attributes': {'congress': congress, 'citation': citation, 'number': item.get('number'),
                              'part_number': item.get('partNumber'), 'organization': item.get('organization'),
                              'description': clip(item.get('description')),
                              'nomination_type': 'military' if kind.get('isMilitary') else 'civilian' if kind.get('isCivilian') else None,
                              'latest_action': action(item), 'update_date': item.get('updateDate'),
                              'nominee_basis': 'list record; individual nominees are named only in description when present'}}
    elif key == 'reports':
        congress, kind, number, part = item['congress'], item['type'].lower(), item['number'], item.get('part', 1)
        yield {'kind': 'entity', 'id': f'{dataset}:report:{congress}-{kind}-{number}-{part}',
               'entity_id': f'congress:committee_report:{congress}-{kind}-{number}-{part}', 'entity_type': 'publication',
               'label': item['citation'], 'attributes': {'congress': congress, 'chamber': item.get('chamber'),
                                                          'report_type': item['type'], 'number': number, 'part': part,
                                                          'cmte_rpt_id': item.get('cmte_rpt_id'), 'update_date': item.get('updateDate')}}
    elif key == 'treaties':
        received, number, suffix = item.get('congressReceived'), item['number'], item.get('suffix') or ''
        yield {'kind': 'entity', 'id': f'{dataset}:treaty:{received}-{number}{suffix}',
               'entity_id': f'congress:treaty:{received}-{number}{suffix}', 'entity_type': 'law',
               'label': f'Treaty Document {received}-{number}{suffix}',
               'attributes': {'measure_kind': 'treaty_document', 'congress_received': received,
                              'congress_considered': item.get('congressConsidered'), 'topic': item.get('topic'),
                              'transmitted_date': item.get('transmittedDate'), 'update_date': item.get('updateDate'),
                              'legal_status': 'transmitted treaty document; ratification not inferred'}}
    elif key == 'houseRollCallVotes':
        congress, session, roll = item['congress'], item['sessionNumber'], item['rollCallNumber']
        participants = ['us:congress:house']
        if item.get('legislationType') and item.get('legislationNumber'):
            participants.append(f"congress:bill:{congress}-{item['legislationType'].lower()}-{item['legislationNumber']}")
        if item.get('amendmentType') and item.get('amendmentNumber'):
            participants.append(f"congress:amendment:{congress}-{item['amendmentType'].lower()}-{item['amendmentNumber']}")
        yield {'kind': 'event', 'id': f'{dataset}:house_vote:{congress}-{session}-{roll}', 'event_type': 'house_roll_call_vote',
               'occurred_at': item['startDate'], 'participants': participants,
               'attributes': {'congress': congress, 'session': session, 'roll_call_number': roll,
                              'identifier': item.get('identifier'), 'result': item.get('result'), 'vote_type': item.get('voteType'),
                              'amendment_author': item.get('amendmentAuthor'), 'clerk_source_url': item.get('sourceDataURL'),
                              'update_date': item.get('updateDate'),
                              'join_note': 'Voteview voteview_rollcalls holds member positions for the same House roll call'}}
    elif key == 'committees':
        code = item['systemCode']
        entity = 'congress:committee:' + code
        yield {'kind': 'entity', 'id': f'{dataset}:committee:{code}', 'entity_id': entity, 'entity_type': 'institution',
               'label': item['name'], 'attributes': {'chamber': item.get('chamber'), 'committee_type': item.get('committeeTypeCode'),
                                                     'system_code': code, 'update_date': item.get('updateDate')}}
        parent = (item.get('parent') or {}).get('systemCode')
        if parent:
            yield {'kind': 'assertion', 'id': f'{dataset}:committee_parent:{code}', 'subject': entity, 'predicate': 'part_of',
                   'object': 'congress:committee:' + parent}
    else:  # pragma: no cover - LISTS is closed
        raise ValueError('Unknown congress.gov list: ' + key + digest(item))
