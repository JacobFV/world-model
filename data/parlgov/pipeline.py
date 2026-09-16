"""ParlGov views -> party entities and positions, election results and cabinet composition.

Inputs are the three UTF-8 CSV views (view_party, view_election, view_cabinet). Countries are
referenced as ``iso3:XXX`` (ParlGov ``country_name_short`` is ISO 3166-1 alpha-3).
"""
from datetime import date, timedelta
from worldmodel.raw_readers import iter_rows

VIEWS = ('view_party', 'view_election', 'view_cabinet')
POSITIONS = {'left_right': 'party_left_right_position', 'state_market': 'party_state_market_position',
             'liberty_authority': 'party_liberty_authority_position', 'eu_anti_pro': 'party_eu_anti_pro_position'}
EXTERNAL_IDS = ('cmp', 'euprofiler', 'ees', 'castles_mair', 'huber_inglehart', 'ray', 'benoit_laver', 'chess')
READER = {'format': 'csv', 'encoding': 'utf-8-sig'}


def view_name(shard):
    name = shard.get('name') or (shard.get('request') or {}).get('url', '')
    for view in VIEWS:
        if view in name:
            return view
    raise ValueError(f'Unrecognized ParlGov shard: {name!r}')


def number(value):
    value = (value or '').strip()
    if value in ('', 'NA', 'None', 'null'):
        return None
    result = float(value)
    return int(result) if result.is_integer() else result


def next_day(value):
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat()


def run(context):
    dataset = context.definition['id']
    for index, ref in enumerate(context.raw_inputs):
        shards = {view_name(s): s for s in context.raw_shards(index)}
        missing = set(VIEWS) - set(shards)
        if missing:
            raise ValueError('ParlGov artifact lacks views: ' + ', '.join(sorted(missing)))
        observed = context.raw_receipt(index)['retrieved_at']

        def base(record, locator, **attributes):
            record.update(observed_at=observed, evidence=[{'input': ref, 'locator': locator}],
                          attributes={'source_dataset': dataset, **attributes})
            return record

        for locator, row in iter_rows([shards['view_party']], READER):
            pid = row['party_id'].strip()
            party = 'parlgov:party:' + pid
            iso3 = row['country_name_short'].strip()
            yield base({'kind': 'entity', 'id': f'{dataset}:party:{pid}', 'entity_id': party, 'entity_type': 'organization',
                        'label': row.get('party_name_english') or row.get('party_name') or party}, locator,
                       party_name=row.get('party_name'), party_name_short=row.get('party_name_short'),
                       party_family=row.get('family_name'), party_family_short=row.get('family_name_short'),
                       country_iso3=iso3, identity_basis='ParlGov party_id')
            yield base({'kind': 'assertion', 'id': f'{dataset}:party_country:{pid}', 'subject': party,
                        'predicate': 'registered_in', 'object': 'iso3:' + iso3}, locator)
            for column, metric in POSITIONS.items():
                value = number(row.get(column))
                if value is None:
                    continue
                yield base({'kind': 'observation', 'id': f'{dataset}:{column}:{pid}', 'subject': party, 'metric': metric,
                            'value': value, 'unit': 'parlgov_scale_0_10', 'dimensions': {'country': iso3}}, locator,
                           valid_time_unknown=True, basis='ParlGov time-invariant mean of expert survey placements')
            for namespace in EXTERNAL_IDS:
                value = (row.get(namespace) or '').strip()
                if value:
                    yield base({'kind': 'assertion', 'id': f'{dataset}:party_id:{pid}:{namespace}', 'subject': party,
                                'predicate': 'identifier_assignment', 'value': {'namespace': namespace, 'value': value}}, locator,
                               identity_basis='ParlGov published external dataset identifier')

        elections = set()
        for locator, row in iter_rows([shards['view_election']], READER):
            eid, pid, iso3, day = row['election_id'].strip(), row['party_id'].strip(), row['country_name_short'].strip(), row['election_date'].strip()
            date.fromisoformat(day)
            dims = {'election_id': eid, 'election_type': row['election_type'], 'country': iso3}
            window = {'valid_from': day, 'valid_to': next_day(day)}
            if eid not in elections:
                elections.add(eid)
                yield base({'kind': 'event', 'id': f'{dataset}:election:{eid}', 'event_type': row['election_type'] + '_election',
                            'occurred_at': day, 'participants': ['iso3:' + iso3]}, locator,
                           election_id=eid, previous_parliament_election_id=row.get('previous_parliament_election_id') or None,
                           previous_cabinet_id=row.get('previous_cabinet_id') or None)
                seats_total = number(row.get('seats_total'))
                if seats_total is not None:
                    yield base({'kind': 'observation', 'id': f'{dataset}:seats_total:{eid}', 'subject': 'iso3:' + iso3,
                                'metric': 'parliament_seats_total', 'value': seats_total, 'unit': 'seats', 'dimensions': dims,
                                **window}, locator)
            party = 'parlgov:party:' + pid
            for column, metric, unit in (('vote_share', 'election_vote_share', 'percent'), ('seats', 'election_seats_won', 'seats')):
                value = number(row.get(column))
                record = {'kind': 'observation', 'id': f'{dataset}:{column}:{eid}:{pid}', 'subject': party, 'metric': metric,
                          'value': value, 'unit': unit, 'dimensions': dims, **window}
                if value is None:
                    record['missing_reason'] = 'source_missing'
                yield base(record, locator)

        cabinets = list(iter_rows([shards['view_cabinet']], READER))
        successor = {}
        for _, row in cabinets:
            previous = (row.get('previous_cabinet_id') or '').strip()
            if previous:
                successor.setdefault(previous, row['start_date'].strip())
        merged = {}
        for locator, row in cabinets:
            key = (row['cabinet_id'].strip(), row['party_id'].strip())
            if key not in merged:
                merged[key] = (locator, dict(row), 1)
            else:  # ParlGov repeats e.g. 'no party affiliation' groups within one cabinet: add their seats
                first, kept, count = merged[key]
                seats = [number(kept.get('seats')), number(row.get('seats'))]
                kept['seats'] = '' if all(v is None for v in seats) else str(sum(v or 0 for v in seats))
                kept['cabinet_party'] = '1' if '1' in (kept.get('cabinet_party'), row.get('cabinet_party')) else kept.get('cabinet_party')
                kept['prime_minister'] = '1' if '1' in (kept.get('prime_minister'), row.get('prime_minister')) else kept.get('prime_minister')
                merged[key] = (first, kept, count + 1)
        seen = set()
        for locator, row, rows_merged in merged.values():
            cid, pid, iso3, start = row['cabinet_id'].strip(), row['party_id'].strip(), row['country_name_short'].strip(), row['start_date'].strip()
            date.fromisoformat(start)
            cabinet = 'parlgov:cabinet:' + cid
            end = successor.get(cid)
            if end and end <= start:
                end = None
            if cid not in seen:
                seen.add(cid)
                yield base({'kind': 'entity', 'id': f'{dataset}:cabinet:{cid}', 'entity_id': cabinet, 'entity_type': 'institution',
                            'label': f"{row['cabinet_name']} cabinet ({iso3}, {start})"}, locator,
                           cabinet_name=row['cabinet_name'], start_date=start, end_date_basis='start of successor cabinet' if end else 'unknown',
                           caretaker=row.get('caretaker') == '1', election_id=row.get('election_id') or None,
                           previous_cabinet_id=row.get('previous_cabinet_id') or None)
                yield base({'kind': 'assertion', 'id': f'{dataset}:cabinet_country:{cid}', 'subject': cabinet,
                            'predicate': 'registered_in', 'object': 'iso3:' + iso3}, locator)
            party = 'parlgov:party:' + pid
            window = {'valid_from': start, **({'valid_to': end} if end else {})}
            if row.get('cabinet_party') == '1':
                yield base({'kind': 'assertion', 'id': f'{dataset}:cabinet_party:{cid}:{pid}', 'subject': party,
                            'predicate': 'cabinet_member_party', 'object': cabinet, **window}, locator,
                           prime_minister_party=row.get('prime_minister') == '1',
                           validity_basis='cabinet start date to successor cabinet start date')
            seats = number(row.get('seats'))
            if seats is not None:
                yield base({'kind': 'observation', 'id': f'{dataset}:cabinet_seats:{cid}:{pid}', 'subject': party,
                            'metric': 'parliament_seats_held', 'value': seats, 'unit': 'seats',
                            'dimensions': {'cabinet_id': cid, 'country': iso3, 'in_cabinet': row.get('cabinet_party') == '1'},
                            'valid_from': start, 'valid_to': next_day(start)}, locator,
                           election_seats_total=number(row.get('election_seats_total')), basis='seats at cabinet formation',
                           source_rows_merged=rows_merged)
