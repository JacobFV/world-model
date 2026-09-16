"""Full UCDP bulk release normalization (GED, candidate GED, ACD, dyadic, NSOS, BRD, actors).

Streams each shard; memory holds only entity keys (thousands) and per-shard
country-month aggregates. Source figures are reported estimates, never truth.
"""
from datetime import date, timedelta
import json
from pathlib import Path
import re

from worldmodel.raw_readers import iter_rows

from .evidence import Evidence, num, year_bounds

VIOLENCE = {'1': 'state_based', '2': 'non_state', '3': 'one_sided'}
INCOMPATIBILITY = {'1': 'territory', '2': 'government', '3': 'government_and_territory'}
CONFLICT_TYPE = {'1': 'extrasystemic', '2': 'interstate', '3': 'intrastate', '4': 'internationalized_intrastate'}
ORDER = ['actor', 'acd', 'dyadic', 'brd', 'nonstate', 'onesided', 'ged', 'candidate']


def shard_kind(shard):
    name = (shard.get('name') or (shard.get('request') or {}).get('url') or shard.get('url') or '').rsplit('/', 1)[-1].lower()
    for pattern, kind in (('actor', 'actor'), ('prio-acd', 'acd'), ('brd', 'brd'), ('dyadic', 'dyadic'),
                          ('nonstate', 'nonstate'), ('onesided', 'onesided'), ('ged261', 'ged'), ('gedevent', 'candidate')):
        if pattern in name:
            return kind, name
    raise ValueError('Unrecognized UCDP shard: ' + name)


def release_of(kind, name):
    if kind != 'candidate':
        return '26.1'
    match = re.search(r'gedevent_(v[0-9_]+)\.csv', name)
    return 'candidate_' + (match.group(1) if match else name)


def codes(values):
    return [part.strip() for part in re.split(r'[,;]', values or '') if part.strip()]


def run_full(context):
    table = json.loads(Path(__file__).with_name('country_codes.json').read_text())
    gw_iso, names = table['gw_to_iso3'], table['names']
    non_iso = set(table['non_iso_codes'])
    ev = Evidence(context, 'ucdp')
    shards = sorted(context.raw_shards(), key=lambda s: ORDER.index(shard_kind(s)[0]))

    def country(gw, loc):
        gw = str(gw).strip()
        iso = gw_iso.get(gw)
        key = 'iso3:' + iso if iso else 'gw:' + gw
        record = ev.entity(key, 'country', names.get(iso, 'Gleditsch-Ward state ' + gw), loc,
                           gw_code=int(gw), iso3=iso, code_standard='iso3166_1' if iso and iso not in non_iso else 'nonstandard_or_historical')
        return key, record

    def actor(actor_id, label, loc):
        key = 'ucdp:actor:' + str(actor_id).strip()
        return key, ev.entity(key, 'organization', label or key, loc, ucdp_actor_id=str(actor_id).strip())

    def conflict(conflict_id, label, loc, **attrs):
        key = 'ucdp:conflict:' + str(conflict_id).strip()
        return key, ev.entity(key, 'conflict', label or key, loc, ucdp_conflict_id=str(conflict_id).strip(), **attrs)

    def dyad(dyad_id, label, loc):
        key = 'ucdp:dyad:' + str(dyad_id).strip()
        return key, ev.entity(key, 'conflict', label or key, loc, ucdp_dyad_id=str(dyad_id).strip(), unit='dyad')

    def emit(*records):
        for record in records:
            if record is not None:
                yield record

    def deaths(subject, prefix, row, keys, year, loc, dims):
        best, low, high = (num(row.get(k)) for k in keys)
        start, end = year_bounds(year)
        for suffix, value in (('', best), ('_low', low), ('_high', high)):
            yield ev.observation(subject, prefix + suffix, value, 'deaths', loc, valid_from=start, valid_to=end,
                                 dimensions={'frequency': 'annual', **dims}, missing_reason='not_reported',
                                 estimate='best' if not suffix else suffix[1:], release='26.1')

    for shard in shards:
        kind, name = shard_kind(shard)
        release = release_of(kind, name)
        encoding = 'latin-1' if kind == 'actor' else 'utf-8-sig'
        reader = {'format': 'csv', 'encoding': encoding, 'members': ['*.csv']}
        aggregates = {}
        agg_locator = None
        for loc, row in iter_rows([shard], reader):
            if kind == 'actor':
                key, record = actor(row['ActorId'], row.get('NameData'), loc)
                yield from emit(record)
                if record is not None:
                    record['attributes'].update(org_type=row.get('Org'), location=row.get('Location'), region=row.get('Region'))
                for gw in codes(row.get('GWNOLoc')):
                    place, place_record = country(gw, loc)
                    yield from emit(place_record, ev.relation(key, 'located_in', place, loc))
            elif kind in ('acd', 'dyadic'):
                label = (row.get('location') or '') + ': ' + (row.get('side_a') or '') + ' - ' + (row.get('side_b') or '')
                ckey, crec = conflict(row['conflict_id'], label if kind == 'acd' else None, loc,
                                      incompatibility=INCOMPATIBILITY.get(row.get('incompatibility')),
                                      territory_name=row.get('territory_name') or None,
                                      type_of_conflict=CONFLICT_TYPE.get(row.get('type_of_conflict')), start_date=row.get('start_date'))
                yield from emit(crec)
                subject = ckey
                if kind == 'dyadic':
                    subject, drec = dyad(row['dyad_id'], label, loc)
                    yield from emit(drec, ev.relation(subject, 'dyad_of_conflict', ckey, loc))
                for side in ('a', 'b'):
                    for actor_id in codes(row.get('side_' + side + '_id')):
                        akey, arec = actor(actor_id, None, loc)
                        predicate = 'participates_in_conflict' if kind == 'acd' else 'dyad_side_' + side
                        yield from emit(arec, ev.relation(akey, predicate, subject, loc))
                for gw in codes(row.get('gwno_loc')):
                    place, prec = country(gw, loc)
                    yield from emit(prec, ev.relation(subject, 'conflict_in', place, loc))
                start, end = year_bounds(row['year'])
                yield ev.observation(subject, 'conflict_intensity_level', num(row['intensity_level']), 'category_code', loc,
                                     valid_from=start, valid_to=end, missing_reason='not_reported',
                                     dimensions={'frequency': 'annual', 'type_of_conflict': CONFLICT_TYPE.get(row.get('type_of_conflict'))},
                                     scale='1 = 25-999 battle-related deaths in year; 2 = 1000+', release='26.1')
            elif kind == 'brd':
                ckey, crec = conflict(row['conflict_id'], None, loc)
                dkey, drec = dyad(row['dyad_id'], (row.get('side_a') or '') + ' - ' + (row.get('side_b') or ''), loc)
                yield from emit(crec, drec, ev.relation(dkey, 'dyad_of_conflict', ckey, loc))
                yield from deaths(dkey, 'battle_related_deaths', row, ('bd_best', 'bd_low', 'bd_high'), row['year'], loc,
                                  {'type_of_conflict': CONFLICT_TYPE.get(row.get('type_of_conflict'))})
            elif kind == 'nonstate':
                ckey, crec = conflict(row['conflict_id'], (row.get('side_a_name') or '') + ' - ' + (row.get('side_b_name') or ''), loc,
                                      conflict_class='non_state', org_level=row.get('org'))
                yield from emit(crec)
                for side in ('a', 'b'):
                    akey, arec = actor(row['side_' + side + '_id'], row.get('side_' + side + '_name'), loc)
                    yield from emit(arec, ev.relation(akey, 'participates_in_conflict', ckey, loc))
                for gw in codes(row.get('gwno_location')):
                    place, prec = country(gw, loc)
                    yield from emit(prec, ev.relation(ckey, 'conflict_in', place, loc))
                yield from deaths(ckey, 'non_state_conflict_deaths', row,
                                  ('best_fatality_estimate', 'low_fatality_estimate', 'high_fatality_estimate'), row['year'], loc, {})
            elif kind == 'onesided':
                ckey, crec = conflict(row['conflict_id'], (row.get('actor_name') or '') + ' - civilians', loc,
                                      conflict_class='one_sided', is_government_actor=row.get('is_government_actor'))
                akey, arec = actor(row['actor_id'], row.get('actor_name'), loc)
                yield from emit(crec, arec, ev.relation(akey, 'participates_in_conflict', ckey, loc))
                for gw in codes(row.get('gwno_location')):
                    place, prec = country(gw, loc)
                    yield from emit(prec, ev.relation(ckey, 'conflict_in', place, loc))
                yield from deaths(ckey, 'one_sided_violence_deaths', row,
                                  ('best_fatality_estimate', 'low_fatality_estimate', 'high_fatality_estimate'), row['year'], loc, {})
            else:  # ged / candidate events
                agg_locator = agg_locator or loc.rsplit('/line:', 1)[0]
                violence = VIOLENCE.get(row.get('type_of_violence'), 'unknown')
                ckey, crec = conflict(row['conflict_new_id'], row.get('conflict_name'), loc)
                dkey, drec = dyad(row['dyad_new_id'], row.get('dyad_name'), loc)
                akey, arec = actor(row['side_a_new_id'], row.get('side_a'), loc)
                bkey, brec = actor(row['side_b_new_id'], row.get('side_b'), loc)
                place, prec = country(row['country_id'], loc)
                yield from emit(crec, drec, arec, brec, prec)
                started, ended = row['date_start'][:10], row['date_end'][:10]
                low, best, high = num(row.get('low')), num(row.get('best')), num(row.get('high'))
                consistent = None not in (low, best, high) and 0 <= low <= best <= high
                valid_to = (date.fromisoformat(max(started, ended)) + timedelta(days=1)).isoformat()
                event = ev.event('ucdp_ged:' + violence, started, [ckey, dkey, akey, bkey, place], loc, ['ged', release, row['id']],
                                 valid_from=started, valid_to=valid_to,
                                 attributes={'ged_id': row['id'], 'relid': row.get('relid'), 'release': release,
                                             'code_status': row.get('code_status'), 'date_end': ended,
                                             'date_precision': num(row.get('date_prec')), 'where_precision': num(row.get('where_prec')),
                                             'event_clarity': num(row.get('event_clarity')), 'latitude': num(row.get('latitude')),
                                             'longitude': num(row.get('longitude')), 'adm_1': row.get('adm_1') or None,
                                             'adm_2': row.get('adm_2') or None, 'priogrid_gid': num(row.get('priogrid_gid')),
                                             'number_of_sources': num(row.get('number_of_sources')),
                                             'deaths': {'side_a': num(row.get('deaths_a')), 'side_b': num(row.get('deaths_b')),
                                                        'civilians': num(row.get('deaths_civilians')), 'unknown': num(row.get('deaths_unknown')),
                                                        'low': low, 'best': best, 'high': high},
                                             'bounds_consistent': consistent,
                                             'reporting_status': 'UCDP-coded source estimate; not independently verified' +
                                                                 ('; candidate release, subject to revision' if kind == 'candidate' else '')})
                yield event
                bucket = aggregates.setdefault((place, started[:7], violence), [0, 0, 0, 0])
                bucket[0] += 1
                for i, value in ((1, best), (2, low), (3, high)):
                    bucket[i] += value or 0
        for (place, month, violence), (events, best, low, high) in sorted(aggregates.items()):
            year, mon = int(month[:4]), int(month[5:7])
            start = f'{year:04d}-{mon:02d}-01'
            end = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1).isoformat()
            dims = {'frequency': 'monthly', 'type_of_violence': violence, 'release': release}
            common = dict(valid_from=start, valid_to=end, dimensions=dims, aggregate=True,
                          method='sum of GED event estimates by country (country_id) and date_start month')
            yield ev.observation(place, 'organized_violence_events', events, 'events', agg_locator, **common)
            yield ev.observation(place, 'organized_violence_deaths', best, 'deaths', agg_locator, estimate='best', **common)
            yield ev.observation(place, 'organized_violence_deaths_low', low, 'deaths', agg_locator, estimate='low', **common)
            yield ev.observation(place, 'organized_violence_deaths_high', high, 'deaths', agg_locator, estimate='high', **common)
    ev.close()
