"""Voteview CSVs -> legislators (ICPSR), DW-NOMINATE / Nokken-Poole scores, parties, roll calls and vote positions.

* HSall_members.csv: one row per member x congress x chamber (all congresses).
* HSall_parties.csv: party sizes and medians per congress x chamber.
* HSall_rollcalls.csv: one event per roll call (all congresses).
* {H,S}NNN_votes.csv: member positions, emitted as one compact event per roll call with ICPSR ids
  grouped by Voteview cast_code (1-3 yea, 4-6 nay, 7-8 present, 9 not voting, 0 not a member).
"""
from datetime import date
import re
from worldmodel.raw_readers import iter_rows

READER = {'format': 'csv', 'encoding': 'utf-8-sig'}
CHAMBERS = {'House': 'us:congress:house', 'Senate': 'us:congress:senate'}
CAST_CODES = {'0': 'not_member', '1': 'yea', '2': 'paired_yea', '3': 'announced_yea', '4': 'announced_nay',
              '5': 'paired_nay', '6': 'nay', '7': 'present_paired', '8': 'present', '9': 'not_voting'}


def congress_start(n):
    return date(1935 + 2 * (n - 74), 1, 3) if n >= 74 else date(1789 + 2 * (n - 1), 3, 4)


def window(n):
    return {'valid_from': congress_start(n).isoformat(), 'valid_to': congress_start(n + 1).isoformat()}


def number(value):
    value = (value or '').strip()
    if value in ('', 'NA', 'nan'):
        return None
    result = float(value)
    return int(result) if result.is_integer() else result


def integer(value):
    return int(float(str(value).strip()))


def shard_kind(shard):
    name = shard.get('name') or (shard.get('request') or {}).get('url', '').rsplit('/', 1)[-1]
    if name.endswith('HSall_members.csv'):
        return 'members', None
    if name.endswith('HSall_rollcalls.csv'):
        return 'rollcalls', None
    if name.endswith('HSall_parties.csv'):
        return 'parties', None
    match = re.search(r'([HS])(\d{3})_votes\.csv$', name)
    if match:
        return 'votes', (match.group(1), int(match.group(2)))
    raise ValueError(f'Unrecognized Voteview shard: {name!r}')


def run(context):
    dataset = context.definition['id']
    for index, ref in enumerate(context.raw_inputs):
        groups = {}
        for shard in context.raw_shards(index):
            kind, key = shard_kind(shard)
            groups.setdefault(kind, []).append((key, shard))
        observed_default = context.raw_receipt(index)['retrieved_at']

        def rec(record, shard, locator, **attributes):
            record.update(observed_at=shard.get('retrieved_at') or observed_default,
                          evidence=[{'input': ref, 'locator': locator}], attributes={'source_dataset': dataset, **attributes})
            return record

        people, crosswalk, parties = set(), set(), {}
        for _, shard in groups.get('parties', []):
            for locator, row in iter_rows([shard], READER):
                code, congress, chamber = str(integer(row['party_code'])), integer(row['congress']), row['chamber']
                party = 'voteview:party:' + code
                if code not in parties:
                    parties[code] = row.get('party_name') or code
                    yield rec({'kind': 'entity', 'id': f'{dataset}:party:{code}', 'entity_id': party, 'entity_type': 'organization',
                               'label': parties[code]}, shard, locator, party_code=code, identity_basis='Voteview/ICPSR party code')
                dims = {'congress': congress, 'chamber': chamber}
                for column, metric, unit in (('n_members', 'party_member_count', 'members'),
                                             ('nominate_dim1_median', 'party_nominate_dim1_median', 'dw_nominate_score'),
                                             ('nominate_dim2_median', 'party_nominate_dim2_median', 'dw_nominate_score')):
                    value = number(row.get(column))
                    if value is not None:
                        yield rec({'kind': 'observation', 'id': f'{dataset}:{column}:{congress}:{chamber}:{code}', 'subject': party,
                                   'metric': metric, 'value': value, 'unit': unit, 'dimensions': dims, **window(congress)},
                                  shard, locator)

        for _, shard in groups.get('members', []):
            services = set()
            for locator, row in iter_rows([shard], READER):
                icpsr, congress, chamber = str(integer(row['icpsr'])), integer(row['congress']), row['chamber'].strip()
                person = 'icpsr:' + icpsr
                bioguide = (row.get('bioguide_id') or '').strip()
                if icpsr not in people:
                    people.add(icpsr)
                    yield rec({'kind': 'entity', 'id': f'{dataset}:member:{icpsr}', 'entity_id': person, 'entity_type': 'person',
                               'label': row.get('bioname') or person}, shard, locator,
                              born=number(row.get('born')), died=number(row.get('died')),
                              identity_basis='Voteview ICPSR member id (a party switch can create a new id)')
                if re.fullmatch(r'[A-Z]\d{6}', bioguide) and (icpsr, bioguide) not in crosswalk:
                    crosswalk.add((icpsr, bioguide))
                    yield rec({'kind': 'assertion', 'id': f'{dataset}:same_as:{icpsr}:{bioguide}', 'subject': person,
                               'predicate': 'same_as', 'object': 'bioguide:' + bioguide}, shard, locator,
                              identity_basis='Voteview published bioguide_id')
                service = (congress, chamber, icpsr, row.get('district_code'), row.get('state_abbrev'))
                if service in services:
                    continue
                services.add(service)
                key = f'{congress}:{chamber}:{icpsr}:{row.get("state_abbrev")}:{row.get("district_code")}'
                span = window(congress)
                yield rec({'kind': 'assertion', 'id': f'{dataset}:service:{key}', 'subject': person, 'predicate': 'congressional_service',
                           'value': {'congress': congress, 'chamber': chamber, 'state_abbrev': row.get('state_abbrev') or None,
                                     'state_icpsr': number(row.get('state_icpsr')), 'district_code': number(row.get('district_code')),
                                     'party_code': str(integer(row['party_code'])) if row.get('party_code') else None, 'occupancy': number(row.get('occupancy')),
                                     'last_means': number(row.get('last_means'))}, **span},
                          shard, locator, validity_basis='congress term dates; individual service may be shorter (see occupancy)')
                if row.get('party_code'):
                    yield rec({'kind': 'assertion', 'id': f'{dataset}:party_affiliation:{key}', 'subject': person,
                               'predicate': 'party_affiliation', 'object': 'voteview:party:' + str(integer(row['party_code'])), **span},
                              shard, locator, validity_basis='party code recorded for this congress and chamber')
                dims = {'congress': congress, 'chamber': chamber}
                for column, metric, unit in (('nominate_dim1', 'dw_nominate_dim1', 'dw_nominate_score'),
                                             ('nominate_dim2', 'dw_nominate_dim2', 'dw_nominate_score'),
                                             ('nokken_poole_dim1', 'nokken_poole_dim1', 'nokken_poole_score'),
                                             ('nokken_poole_dim2', 'nokken_poole_dim2', 'nokken_poole_score'),
                                             ('nominate_number_of_votes', 'scaled_roll_call_votes', 'votes')):
                    value = number(row.get(column))
                    if value is None:
                        continue
                    extra = {}
                    if column == 'nominate_dim1':
                        extra = {'log_likelihood': number(row.get('nominate_log_likelihood')),
                                 'geo_mean_probability': number(row.get('nominate_geo_mean_probability')),
                                 'number_of_errors': number(row.get('nominate_number_of_errors')),
                                 'basis': 'DW-NOMINATE career score, repeated on each congress row'}
                    yield rec({'kind': 'observation', 'id': f'{dataset}:{column}:{key}', 'subject': person, 'metric': metric,
                               'value': value, 'unit': unit, 'dimensions': dims, **span}, shard, locator, **extra)

        dates = {}
        vote_congresses = {key[1] for key, _ in groups.get('votes', [])}
        for _, shard in groups.get('rollcalls', []):
            for locator, row in iter_rows([shard], READER):
                congress, chamber, roll = integer(row['congress']), row['chamber'].strip(), integer(row['rollnumber'])
                day = row['date'].strip()
                date.fromisoformat(day)
                code = chamber[0]
                if congress in vote_congresses:
                    dates[(code, congress, roll)] = (day, locator)
                description = (row.get('dtl_desc') or row.get('vote_desc') or '').strip()
                yield rec({'kind': 'event', 'id': f'{dataset}:rollcall:{code}{congress}:{roll}', 'event_type': 'roll_call_vote',
                           'occurred_at': day, 'participants': [CHAMBERS[chamber]]}, shard, locator,
                          rollcall_id=f'{code}{congress:03d}{roll:04d}', congress=congress, chamber=chamber, rollnumber=roll,
                          session=number(row.get('session')), clerk_rollnumber=number(row.get('clerk_rollnumber')),
                          yea_count=number(row.get('yea_count')), nay_count=number(row.get('nay_count')),
                          majority_requirement=row.get('majority_requirement') or None, bill_number=row.get('bill_number') or None,
                          vote_result=row.get('vote_result') or None, vote_question=row.get('vote_question') or None,
                          description=description[:500] or None,
                          nominate={k: number(row.get('nominate_' + k)) for k in ('mid_1', 'mid_2', 'spread_1', 'spread_2', 'log_likelihood')})

        for (code, congress), shard in sorted(groups.get('votes', []), key=lambda item: item[0]):
            chamber = 'House' if code == 'H' else 'Senate'
            positions, first = {}, {}
            for locator, row in iter_rows([shard], READER):
                roll = integer(row['rollnumber'])
                cast = str(integer(row['cast_code']))
                if cast not in CAST_CODES:
                    raise ValueError(f'{locator}: unknown Voteview cast_code {cast!r}')
                positions.setdefault(roll, {}).setdefault(cast, []).append(integer(row['icpsr']))
                first.setdefault(roll, locator)
            for roll in sorted(positions):
                known = dates.get((code, congress, roll))
                if known is None:
                    raise ValueError(f'Voteview votes for {code}{congress} roll {roll} have no roll-call date')
                grouped = {CAST_CODES[c]: sorted(ids) for c, ids in sorted(positions[roll].items())}
                yield rec({'kind': 'event', 'id': f'{dataset}:positions:{code}{congress}:{roll}',
                           'event_type': 'roll_call_member_positions', 'occurred_at': known[0],
                           'participants': [CHAMBERS[chamber]]}, shard, first[roll],
                          rollcall_event=f'{dataset}:rollcall:{code}{congress}:{roll}', congress=congress, chamber=chamber,
                          rollnumber=roll, member_namespace='icpsr', positions=grouped,
                          counts={k: len(v) for k, v in grouped.items()}, rollcall_locator=known[1],
                          aggregation='all member rows of this roll call in the votes file; locator is the first row')
