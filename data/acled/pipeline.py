"""ACLED read-API JSONL pages -> compact events and country-month aggregates.

Raw ACLED rows must not be redistributed; outputs keep identifiers, dates,
types, actors, geography and fatalities only (no notes or source text).
"""
from datetime import date
import re

from .evidence import Evidence, is_full, num


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', (text or '').strip().lower()).strip('_') or 'unknown'


def run(context):
    if not is_full(context):
        raise ValueError('acled: requires a full ACLED API acquisition (awaiting approved access)')
    ev = Evidence(context, 'acled')
    aggregates = {}
    first = None
    for loc, row in context.raw_rows(format='jsonl'):
        event_id = row.get('event_id_cnty')
        day = (row.get('event_date') or '')[:10]
        if not event_id or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', day):
            raise ValueError(f'{loc}: ACLED row lacks event_id_cnty/event_date')
        first = first or loc.split('/line:', 1)[0]
        iso3 = (row.get('iso3') or '').strip().upper()
        place = 'iso3:' + iso3 if re.fullmatch(r'[A-Z]{3}', iso3) else 'iso3166num:' + str(row.get('iso') or 'unknown').zfill(3)
        record = ev.entity(place, 'country', row.get('country') or place, loc, iso_numeric=num(row.get('iso')), iso3=iso3 or None)
        if record is not None:
            yield record
        participants = [place]
        for side in ('actor1', 'actor2'):
            name = (row.get(side) or '').strip()
            if name:
                key = 'acled:actor:' + slug(name)
                record = ev.entity(key, 'organization', name, loc, inter_code=num(row.get('inter' + side[-1])))
                if record is not None:
                    yield record
                if key not in participants:
                    participants.append(key)
        fatalities = num(row.get('fatalities'))
        event_type = slug(row.get('event_type'))
        yield ev.event('acled:' + event_type, day, participants, loc, ['event', event_id],
                       attributes={'event_id': event_id, 'sub_event_type': row.get('sub_event_type'),
                                   'disorder_type': row.get('disorder_type'), 'interaction': num(row.get('interaction')),
                                   'civilian_targeting': row.get('civilian_targeting') or None,
                                   'time_precision': num(row.get('time_precision')), 'geo_precision': num(row.get('geo_precision')),
                                   'admin1': row.get('admin1') or None, 'admin2': row.get('admin2') or None,
                                   'location': row.get('location') or None, 'latitude': num(row.get('latitude')),
                                   'longitude': num(row.get('longitude')), 'fatalities': fatalities,
                                   'source_scale': row.get('source_scale') or None, 'acled_timestamp': num(row.get('timestamp')),
                                   'reporting_status': 'ACLED-coded report; fatalities are conservative source estimates'})
        bucket = aggregates.setdefault((place, day[:7], event_type), [0, 0])
        bucket[0] += 1
        bucket[1] += fatalities or 0
    for (place, month, event_type), (events, fatalities) in sorted(aggregates.items()):
        year, mon = int(month[:4]), int(month[5:7])
        start = f'{year:04d}-{mon:02d}-01'
        end = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1).isoformat()
        common = dict(valid_from=start, valid_to=end, aggregate=True,
                      dimensions={'frequency': 'monthly', 'event_type': event_type}, method='sum over acquired ACLED events')
        yield ev.observation(place, 'acled_events', events, 'events', first, **common)
        yield ev.observation(place, 'acled_fatalities', fatalities, 'deaths', first, **common)
    ev.close()
