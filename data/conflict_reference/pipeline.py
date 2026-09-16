"""COW / ATOP international-system reference data as evidence records.

Shards (by acquisition file name): COW states, NMC v7 (nested ZIP), Direct
Contiguity 3.2, Formal Alliances 4.1, MID 5.0, Inter-State War 4.0, ATOP 5.1.
All files are small; per-file joins (MID participants, war sides) are held in memory.
"""
import csv
from datetime import date, timedelta
import io
import json
from pathlib import Path
import zipfile

from worldmodel.raw_readers import iter_rows

from .evidence import Evidence, is_full, num, year_bounds

ORDER = ['states', 'nmc', 'contiguity', 'alliances', 'mid', 'interstate_war', 'atop']
CONTIGUITY = {1: 'land_or_river', 2: 'water_12mi', 3: 'water_24mi', 4: 'water_150mi', 5: 'water_400mi'}
NMC = {'milex': ('military_expenditure', 'thousand_usd_current'), 'milper': ('military_personnel', 'thousand_people'),
       'irst': ('iron_steel_production', 'thousand_tonnes'), 'pec': ('primary_energy_consumption', 'thousand_coal_ton_equivalent'),
       'tpop': ('total_population', 'thousand_people'), 'upop': ('urban_population', 'thousand_people'),
       'cinc': ('composite_index_national_capability', 'share_of_system')}
HOSTILITY = {1: 'no_militarized_action', 2: 'threat_to_use_force', 3: 'display_of_force', 4: 'use_of_force', 5: 'war'}


def shard_kind(shard):
    name = (shard.get('name') or (shard.get('request') or {}).get('url') or '').rsplit('/', 1)[-1].lower()
    for pattern, kind in (('states', 'states'), ('nmc', 'nmc'), ('contiguity', 'contiguity'), ('version4.1', 'alliances'),
                          ('mid-5', 'mid'), ('inter-statewar', 'interstate_war'), ('atop', 'atop')):
        if pattern in name:
            return kind
    raise ValueError('Unrecognized conflict_reference shard: ' + name)


def make_date(year, month=None, day=None):
    """Return (ISO date, precision) with unknown (-9/0/blank) month/day coarsened to the start of the period."""
    year = num(year)
    if year is None or year <= 0:
        return None, None
    month, day = num(month), num(day)
    precision = 'day'
    if month is None or not 1 <= month <= 12:
        month, day, precision = 1, 1, 'year'
    elif day is None or not 1 <= day <= 31:
        day, precision = 1, 'month'
    while True:
        try:
            return date(int(year), int(month), int(day)).isoformat(), precision
        except ValueError:
            day -= 1


def next_day(value):
    return (date.fromisoformat(value) + timedelta(days=1)).isoformat() if value else None


def run(context):
    if not is_full(context):
        raise ValueError('conflict_reference: requires the full COW/ATOP acquisition (no sample adapter)')
    table = json.loads(Path(__file__).with_name('country_codes.json').read_text())
    cow_iso, names = table['cow_to_iso3'], table['names']
    ev = Evidence(context, 'cowref')
    labels = {}
    shards = sorted(context.raw_shards(), key=lambda s: ORDER.index(shard_kind(s)))
    system = 'cow:system:interstate'

    def out(*records):
        for record in records:
            if record is not None:
                yield record

    def state(ccode, loc, label=None, abbrev=None):
        code = str(int(num(ccode)))
        iso = cow_iso.get(code)
        key = 'iso3:' + iso if iso else 'cow:state:' + code
        record = ev.entity(key, 'country', names.get(iso) or label or labels.get(code) or 'COW state ' + code, loc,
                           cow_code=int(code), cow_abbrev=abbrev, iso3=iso)
        return key, record

    for shard in shards:
        kind = shard_kind(shard)
        if kind == 'states':
            yield from out(ev.entity(system, 'aggregate_cohort', 'COW interstate system members', 'shard:%d' % shard['index']))
            for loc, row in iter_rows([shard], {'format': 'csv', 'members': ['*statelist2024.csv']}):
                labels[row['ccode']] = row['statenme']
                key, record = state(row['ccode'], loc, row['statenme'], row['stateabb'])
                start, _ = make_date(row['styear'], row['stmonth'], row['styear'] and row['stday'])
                end, _ = make_date(row['endyear'], row['endmonth'], row['endday'])
                right_censored = num(row['endyear']) == num(row['version'])
                relation = ev.relation(key, 'member_of', system, loc, identity=[start, loc], once=False,
                                       spell_start=start, spell_end=None if right_censored else end, right_censored=right_censored)
                relation['valid_from'] = start
                if not right_censored:
                    relation['valid_to'] = next_day(end)
                yield from out(record, relation)
        elif kind == 'nmc':
            with zipfile.ZipFile(shard['path']) as outer:
                inner_name = next(n for n in outer.namelist() if n.endswith('NMC-v7-abridged.zip') and not n.startswith('__MACOSX'))
                inner = zipfile.ZipFile(io.BytesIO(outer.read(inner_name)))
                member = next(n for n in inner.namelist() if n.endswith('.csv') and not n.startswith('__MACOSX'))
                text = io.TextIOWrapper(io.BytesIO(inner.read(member)), encoding='utf-8-sig', newline='')
                reader = csv.reader(text)
                header = next(reader)
                last = reader.line_num
                for values in reader:
                    line, last = last + 1, reader.line_num
                    if not values:
                        continue
                    row = dict(zip(header, values))
                    loc = 'shard:%d/member:%s/member:%s/line:%d' % (shard['index'], inner_name, member, line)
                    key, record = state(row['ccode'], loc, abbrev=row['stateabb'])
                    yield from out(record)
                    start, end = year_bounds(row['year'])
                    for column, (metric, unit) in NMC.items():
                        value = num(row[column])
                        missing = value is None or value == -9
                        yield ev.observation(key, metric, None if missing else value, unit, loc, valid_from=start, valid_to=end,
                                             dimensions={'frequency': 'annual'}, missing_reason='source_missing_-9', identity=loc,
                                             source_variable=column, release='NMC v7.0')
        elif kind == 'contiguity':
            for loc, row in iter_rows([shard], {'format': 'csv', 'members': ['*/contdir.csv']}):
                low, lrec = state(row['statelno'], loc, abbrev=row['statelab'])
                high, hrec = state(row['statehno'], loc, abbrev=row['statehab'])
                begin, finish = str(row['begin']), str(row['end'])
                start, _ = make_date(begin[:4], begin[4:6])
                end_month, _ = make_date(finish[:4], finish[4:6])
                ctype = int(num(row['conttype']))
                relation = ev.relation(low, 'contiguous_with', high, loc, identity=[row['conttype'], begin, loc], once=False,
                                       contiguity_type=CONTIGUITY.get(ctype), conttype=ctype, cow_dyad=row['dyad'],
                                       notes=row.get('notes') or None, undirected=True, release='Direct Contiguity v3.2')
                relation['valid_from'] = start
                if finish != '201612':  # 201612 = end of data coverage (right-censored)
                    y, m = int(finish[:4]), int(finish[4:6])
                    relation['valid_to'] = date(y + (m == 12), 1 if m == 12 else m + 1, 1).isoformat()
                else:
                    relation['attributes']['right_censored'] = True
                yield from out(lrec, hrec, relation)
        elif kind == 'alliances':
            for loc, row in iter_rows([shard], {'format': 'csv', 'members': ['version4.1_csv/alliance_v4.1_by_member.csv']}):
                alliance = 'cow:alliance:' + row['version4id']
                all_start, _ = make_date(row['all_st_year'], row['all_st_month'], row['all_st_day'])
                all_end, _ = make_date(row['all_end_year'], row['all_end_month'], row['all_end_day'])
                arec = ev.entity(alliance, 'military_alliance', 'COW alliance %s (%s)' % (row['version4id'], row['ss_type']), loc,
                                 alliance_type=row['ss_type'], alliance_start=all_start, alliance_end=all_end,
                                 defense=num(row['defense']), neutrality=num(row['neutrality']),
                                 nonaggression=num(row['nonaggression']), entente=num(row['entente']), release='COW Formal Alliances v4.1')
                key, srec = state(row['ccode'], loc, row['state_name'])
                start, precision = make_date(row['mem_st_year'], row['mem_st_month'], row['mem_st_day'])
                end, _ = make_date(row['mem_end_year'], row['mem_end_month'], row['mem_end_day'])
                relation = ev.relation(key, 'member_of', alliance, loc, identity=[start, loc], once=False,
                                       left_censored=row['left_censor'] == '1', right_censored=row['right_censor'] == '1',
                                       date_precision=precision)
                if start:
                    relation['valid_from'] = start
                if end and start and end >= start:
                    relation['valid_to'] = next_day(end)
                yield from out(arec, srec, relation)
        elif kind == 'mid':
            participants = {}
            for loc, row in iter_rows([shard], {'format': 'csv', 'members': ['MIDB 5.0.csv']}):
                key, record = state(row['ccode'], loc, abbrev=row['stabb'])
                yield from out(record)
                start, _ = make_date(row['styear'], row['stmon'], row['stday'])
                participants.setdefault(row['dispnum'], []).append(
                    {'state': key, 'side_a': row['sidea'] == '1', 'originator': row['orig'] == '1', 'revisionist': row['revstate'] == '1',
                     'highest_action': num(row['hiact']), 'hostility_level': num(row['hostlev']), 'fatality_level': num(row['fatality']),
                     'joined': start})
            for loc, row in iter_rows([shard], {'format': 'csv', 'members': ['MIDA 5.0.csv']}):
                start, precision = make_date(row['styear'], row['stmon'], row['stday'])
                end, _ = make_date(row['endyear'], row['endmon'], row['endday'])
                sides = participants.get(row['dispnum'], [])
                hostility = int(num(row['hostlev']))
                event = ev.event('cow_mid:dispute', start, sorted({p['state'] for p in sides}), loc, ['mid', row['dispnum']],
                                 attributes={'dispnum': row['dispnum'], 'date_precision': precision, 'end_date': end,
                                             'hostility_level': hostility, 'hostility': HOSTILITY.get(hostility),
                                             'highest_action': num(row['hiact']), 'fatality_level': num(row['fatality']),
                                             'fatalities_precise': num(row['fatalpre']), 'outcome': num(row['outcome']),
                                             'settlement': num(row['settle']), 'reciprocated': row['recip'] == '1',
                                             'max_duration_days': num(row['maxdur']), 'participants': sides, 'release': 'MID 5.0'})
                if end and end >= start:
                    event['valid_from'], event['valid_to'] = start, next_day(end)
                yield event
        elif kind == 'interstate_war':
            wars = {}
            for loc, row in iter_rows([shard], {'format': 'csv'}):
                key, record = state(row['ccode'], loc, row['StateName'])
                yield from out(record)
                start, _ = make_date(row['StartYear1'], row['StartMonth1'], row['StartDay1'])
                end, _ = make_date(row['EndYear1'], row['EndMonth1'], row['EndDay1'])
                deaths = num(row['BatDeath'])
                war = wars.setdefault(row['WarNum'], {'name': row['WarName'], 'loc': loc, 'start': start, 'end': end, 'sides': []})
                war['start'] = min(filter(None, [war['start'], start])) if start or war['start'] else None
                war['end'] = max(filter(None, [war['end'], end])) if end or war['end'] else None
                war['sides'].append({'state': key, 'side': num(row['Side']), 'initiator': row['Initiator'] == '1',
                                     'outcome': num(row['Outcome']), 'battle_deaths': None if deaths is None or deaths < 0 else deaths,
                                     'where_fought': num(row['WhereFought'])})
            for number, war in sorted(wars.items(), key=lambda item: int(item[0])):
                event = ev.event('cow_war:interstate', war['start'], sorted({s['state'] for s in war['sides']}), war['loc'], ['war', number],
                                 attributes={'war_number': int(number), 'war_name': war['name'], 'end_date': war['end'],
                                             'sides': war['sides'], 'release': 'COW Inter-State War v4.0',
                                             'battle_deaths_total': sum(s['battle_deaths'] or 0 for s in war['sides'])})
                if war['end'] and war['end'] >= war['start']:
                    event['valid_from'], event['valid_to'] = war['start'], next_day(war['end'])
                yield event
        elif kind == 'atop':
            for loc, row in iter_rows([shard], {'format': 'csv', 'members': ['atop5_1a.csv']}):
                begin, _ = make_date(row['begyr'], row['begmo'], row['begday'])
                finish, _ = make_date(row['endyr'], row['endmo'], row['endday'])
                yield from out(ev.entity('atop:alliance:' + row['atopid'], 'military_alliance', 'ATOP alliance ' + row['atopid'], loc,
                                         begin=begin, end=finish, in_effect_at_end=row['ineffect'] == '1', bilateral=row['bilat'] == '1',
                                         defense=num(row['defense']), offense=num(row['offense']), neutrality=num(row['neutral']),
                                         nonaggression=num(row['nonagg']), consultation=num(row['consul']), active=num(row['active']),
                                         military_aid=num(row['milaid']), cow_alliance_id=row['cowid'] or None, release='ATOP 5.1'))
            for loc, row in iter_rows([shard], {'format': 'csv', 'members': ['atop5_1m.csv']}):
                alliance = 'atop:alliance:' + row['atopid']
                key, record = state(row['member'], loc)
                start, precision = make_date(row['yrent'], row['moent'], row['dayent'])
                end, _ = make_date(row['yrexit'], row['moexit'], row['dayexit'])
                relation = ev.relation(key, 'member_of', alliance, loc, identity=[row['phase'], start, loc], once=False,
                                       phase=num(row['phase']), date_precision=precision, source='ATOP 5.1 member-phase')
                if start:
                    relation['valid_from'] = start
                if end and start and end >= start and num(row['yrexit']) not in (None, 0):
                    relation['valid_to'] = next_day(end)
                yield from out(record, relation)
    ev.close()
