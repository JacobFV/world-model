"""Global Fishing Watch API v3 events (port visits, encounters, loitering).

Raw shards are JSONL event objects written by the ``paged_api`` acquisition
(``records_path: entries``). Each event becomes one evidence ``event``:

* ``vessel_port_visit``: participants vessel + ``gfw:anchorage:ID`` (start anchorage);
* ``vessel_encounter``: participants both vessels;
* ``vessel_loitering``: participant vessel.

Vessels are ``mmsi:NNNNNNNNN`` when the GFW ``ssvid`` is a 9-digit MMSI (matching
``marine_ais``), otherwise ``gfw:vessel:ID``. Licence: CC BY-NC 4.0 (non-commercial).
"""

EVENT_TYPES = {'port_visit': 'vessel_port_visit', 'encounter': 'vessel_encounter', 'loitering': 'vessel_loitering',
               'fishing': 'vessel_fishing', 'gap': 'ais_gap'}


def vessel_key(vessel):
    ssvid = str((vessel or {}).get('ssvid') or '').strip()
    if ssvid.isdigit() and len(ssvid) == 9:
        return 'mmsi:' + ssvid
    identifier = (vessel or {}).get('id')
    if not identifier:
        return None
    return 'gfw:vessel:' + str(identifier)


def run(context):
    if not context.raw_inputs:
        raise ValueError('global_fishing_watch: no acquisition; an approved GFW_API_TOKEN is required')
    for index, _ in enumerate(context.raw_inputs):
        receipt = context.raw_receipt(index)
        complete = context.raw_coverage(index)['complete']
        for locator, event in context.raw_rows(index, format='jsonl'):
            kind = event.get('type')
            if kind not in EVENT_TYPES or not event.get('id') or not event.get('start'):
                raise ValueError(f'{locator}: unrecognized GFW event shape')
            vessel = event.get('vessel') or {}
            participants = []
            main = vessel_key(vessel)
            if main:
                participants.append(main)
            position = event.get('position') or {}
            attributes = {'end': event.get('end'), 'gfw_vessel_id': vessel.get('id'), 'vessel_name': vessel.get('name'),
                          'vessel_flag': vessel.get('flag'), 'vessel_type': vessel.get('type'), 'lat': position.get('lat'),
                          'lon': position.get('lon'), 'bounding_box': event.get('boundingBox'), 'complete_source': complete,
                          'licence': 'CC BY-NC 4.0'}
            if kind == 'port_visit':
                visit = event.get('port_visit') or {}
                anchorage = visit.get('startAnchorage') or visit.get('intermediateAnchorage') or {}
                if anchorage.get('anchorageId'):
                    participants.append('gfw:anchorage:' + str(anchorage['anchorageId']))
                attributes.update(duration_hours=visit.get('durationHrs'), confidence=visit.get('confidence'),
                                  port_name=anchorage.get('name'), port_flag=anchorage.get('flag'),
                                  anchorage_lat=anchorage.get('lat'), anchorage_lon=anchorage.get('lon'),
                                  at_dock=anchorage.get('atDock'))
            elif kind == 'encounter':
                encounter = event.get('encounter') or {}
                other = vessel_key(encounter.get('vessel'))
                if other:
                    participants.append(other)
                attributes.update(median_distance_km=encounter.get('medianDistanceKilometers'),
                                  median_speed_knots=encounter.get('medianSpeedKnots'),
                                  encounter_vessel_flag=(encounter.get('vessel') or {}).get('flag'),
                                  encounter_type=encounter.get('type'))
            elif kind == 'loitering':
                loitering = event.get('loitering') or {}
                attributes.update(total_distance_km=loitering.get('totalDistanceKm'), average_speed_knots=loitering.get('averageSpeedKnots'),
                                  loitering_hours=loitering.get('loiteringHours'), distance_from_shore_km=loitering.get('averageDistanceFromShoreKm'))
            if not participants:
                continue
            yield {'kind': 'event', 'id': 'gfw:event:' + str(event['id']), 'event_type': EVENT_TYPES[kind],
                   'occurred_at': event['start'], 'participants': participants, 'observed_at': receipt['retrieved_at'],
                   'evidence': context.raw_evidence(locator, index),
                   'attributes': {k: v for k, v in attributes.items() if v is not None}}
