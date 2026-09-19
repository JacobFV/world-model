"""Typed, dated shocks with the affected unit, published intensity and a locator to the source record.

Every output is an ``event`` record whose ``attributes`` follow one schema:

* ``library_type``  - the shock type (see README)
* ``shock_family``  - natural_hazard | disaster_policy | sanctions | trade_policy
* ``unit_type`` / ``unit`` - the affected unit (county FIPS, NWS zone, reporter x HS6, listed party, epicentre, storm)
* ``date`` / ``date_semantics`` - the event date and exactly what the source says it means
* ``intensity``     - ``{value, unit, definition, timing}`` where the source publishes one, else null.
  ``timing`` is ``ex_ante`` (known at the event date) or ``ex_post`` (measured after it; never use
  ex_post intensity to define treatment or exposure)
* ``source``        - ``{dataset, record_id, locator}``: the input record and its raw locator

Record-level ``evidence`` cites the exact input version and record id, which the runner verifies.
The library selects and restates; it does not reinterpret dates.
"""
import bisect
import hashlib
import math

LIBRARY_VERSION = '1'


def _id(kind, *parts):
    return f'evlib:{kind}:' + hashlib.sha256('|'.join(map(str, parts)).encode()).hexdigest()[:32]


def _locator(record):
    for item in record.get('evidence', []):
        if item.get('locator'):
            return item['locator']
        if item.get('record_id'):
            return 'record:' + item['record_id']
    return None


def _light(record):
    """The fields a library event needs from a source record, without keeping the whole record."""
    return {'id': record['id'], 'observed_at': record['observed_at'], 'evidence': [{'locator': _locator(record)}]}


def _event(context, dataset, source, *, library_type, family, unit_type, unit, date, date_semantics, intensity,
           participants, extra=None, sources=None):
    ref = context.input_ref(dataset)
    cited = sources or [source]
    return {
        'kind': 'event', 'id': _id(library_type, *(s['id'] for s in cited), unit),
        'event_type': library_type, 'occurred_at': date, 'observed_at': max(s['observed_at'] for s in cited),
        'participants': participants,
        'attributes': {'library_version': LIBRARY_VERSION, 'library_type': library_type, 'shock_family': family,
                       'unit_type': unit_type, 'unit': unit, 'date': date[:10], 'date_semantics': date_semantics,
                       'intensity': intensity,
                       'source': {'dataset': dataset, 'record_id': source['id'], 'locator': _locator(source)},
                       **(extra or {})},
        'evidence': [{'input': ref, 'record_id': s['id']} for s in cited]}


# -- FEMA declarations ---------------------------------------------------------------------------

FEMA_TYPES = {'DR': 'fema_major_disaster_declaration', 'EM': 'fema_emergency_declaration',
              'FM': 'fema_fire_management_declaration'}


def fema(context):
    ds = 'openfema'
    pa = {}
    declarations = []
    for r in context.records(ds):
        if r['kind'] == 'observation' and r.get('metric') == 'pa_federal_obligated' and \
                r.get('dimensions', {}).get('category') == 'all' and r['id'].startswith('openfema:web:'):
            pa[r['subject']] = r['value']
        elif r['kind'] == 'event' and r.get('event_type') == 'disaster_declaration':
            counties = [p for p in r['participants'] if p.startswith('geo:US:county:')]
            if counties:
                declarations.append((r, counties[0]))
    for r, county in declarations:
        a = r['attributes']
        disaster = next((p for p in r['participants'] if p.startswith('fema:disaster:')), None)
        obligated = pa.get(disaster)
        yield _event(context, ds, r, library_type=FEMA_TYPES.get(a['declarationType'], 'fema_declaration_other'),
                     family='disaster_policy', unit_type='us_county_fips', unit=county, date=r['occurred_at'],
                     date_semantics='FEMA declarationDate for the designated county (the incident began at '
                                    'incident_begin_date, usually earlier)',
                     intensity=None if obligated is None else {
                         'value': obligated, 'unit': 'USD',
                         'definition': 'Public Assistance federal obligation for the whole disaster (all counties), '
                                       'cumulative as of retrieval', 'timing': 'ex_post'},
                     participants=[county] + ([disaster] if disaster else []),
                     extra={'incident_type': a.get('incidentType'), 'declaration_type': a.get('declarationType'),
                            'incident_begin_date': (a.get('incidentBeginDate') or '')[:10] or None,
                            'incident_end_date': (a.get('incidentEndDate') or '')[:10] or None,
                            'disaster': disaster, 'title': a.get('declarationTitle'),
                            'programs': {k: a.get(k) for k in ('iaProgramDeclared', 'ihProgramDeclared',
                                                                'paProgramDeclared', 'hmProgramDeclared')}})


# -- Sanctions ---------------------------------------------------------------------------------

SANCTIONS_DATES = {
    'ofac_sanctions': 'OFAC SDN entry event date (earliest listing/creation event published for the entry)',
}


def sanctions(context, dataset):
    countries, events = {}, []
    for r in context.records(dataset):
        if r['kind'] == 'assertion' and r.get('predicate') in ('located_in', 'nationality', 'citizenship') \
                and str(r.get('object', '')).startswith('iso3:'):
            countries.setdefault(r['subject'], set()).add(r['object'])
        elif r['kind'] == 'event' and r.get('event_type') == 'sanctions_designation':
            events.append(r)
    for r in events:
        a = r['attributes']
        party = r['participants'][0]
        programs = a.get('programs') or ([a['regime']] if a.get('regime') else [])
        yield _event(context, dataset, r, library_type='sanctions_designation', family='sanctions',
                     unit_type='listed_party', unit=party, date=r['occurred_at'],
                     date_semantics=a.get('date_semantics') or SANCTIONS_DATES.get(dataset, 'published listing date'),
                     intensity=None, participants=[party] + sorted(countries.get(party, ())),
                     extra={'list': a.get('list'), 'programs': programs, 'measures': a.get('measures'),
                            'countries': sorted(countries.get(party, ())),
                            'survivorship': 'current list only: parties delisted before retrieval are absent'})


# -- NOAA storm events --------------------------------------------------------------------------

def storms(context, min_damage):
    ds = 'noaa_storm_events'
    for r in context.records(ds):
        if r['kind'] != 'event':
            continue
        a = r['attributes']
        damage = (a.get('damage_property') or 0) + (a.get('damage_crops') or 0)
        deaths = (a.get('deaths_direct') or 0) + (a.get('deaths_indirect') or 0)
        injuries = (a.get('injuries_direct') or 0) + (a.get('injuries_indirect') or 0)
        if damage < min_damage and deaths + injuries == 0:
            continue
        places = [p for p in r['participants'] if p.startswith('geo:')]
        county = next((p for p in places if p.startswith('geo:US:county:')), None)
        others = [p for p in r['participants'] if not p.startswith('noaa:storm_event:')]
        unit = county or (places or others or r['participants'])[0]
        yield _event(context, ds, r, library_type='storm_event_' + r['event_type'], family='natural_hazard',
                     unit_type='us_county_fips' if county else 'nws_zone_or_location', unit=unit,
                     date=r['occurred_at'], date_semantics='NWS Storm Events begin time',
                     intensity={'value': damage, 'unit': 'USD_nominal',
                                'definition': 'reported property plus crop damage (NWS estimate)', 'timing': 'ex_post'},
                     participants=r['participants'],
                     extra={'deaths': deaths, 'injuries': injuries,
                            'magnitude': a.get('magnitude'), 'tor_f_scale': a.get('tor_f_scale') or None,
                            'selection': f'damage >= {min_damage:g} USD or any death/injury'})


# -- USGS earthquakes -----------------------------------------------------------------------------

def earthquakes(context, min_magnitude):
    ds = 'usgs_earthquakes'
    for r in context.records(ds):
        if r['kind'] != 'event' or r.get('event_type') != 'earthquake':
            continue
        a = r['attributes']
        mag = a.get('mag')
        if mag is None or mag < min_magnitude:
            continue
        quake = r['participants'][0]
        yield _event(context, ds, r, library_type='earthquake', family='natural_hazard', unit_type='epicenter',
                     unit=quake, date=r['occurred_at'], date_semantics='USGS origin time',
                     intensity={'value': mag, 'unit': 'magnitude_' + str(a.get('mag_type')),
                                'definition': 'USGS preferred magnitude', 'timing': 'ex_ante'},
                     participants=r['participants'],
                     extra={'latitude': a.get('latitude'), 'longitude': a.get('longitude'),
                            'depth_km': a.get('depth_km'), 'place': a.get('place')})


# -- IBTrACS tropical cyclones -------------------------------------------------------------------

def _haversine_km(lat1, lon1, lat2, lon2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * 6371.0088 * math.asin(math.sqrt(min(1.0, h)))


def county_points(context):
    points, vintage = {}, {}
    for r in context.records('census_geography'):
        if r['kind'] != 'observation' or r.get('metric') not in ('latitude', 'longitude'):
            continue
        subject = r.get('subject', '')
        if not subject.startswith('geo:US:county:'):
            continue
        v = r.get('dimensions', {}).get('geography_vintage') or 0
        key = (subject, r['metric'])
        if v >= vintage.get(key, -1):
            vintage[key] = v
            points.setdefault(subject, {})[r['metric']] = (r['value'], r['id'])
    return {c: p for c, p in points.items() if 'latitude' in p and 'longitude' in p}


def cyclones(context, basins, radius_km, min_wind):
    ds = 'ibtracs'
    storms_meta, fixes = {}, {}
    for r in context.records(ds):
        if r['kind'] == 'entity' and r['entity_id'].startswith('ibtracs:storm:'):
            if r['attributes'].get('basin') in basins:
                storms_meta[r['entity_id']] = r
        elif r['kind'] == 'observation' and r.get('metric') in ('latitude', 'longitude', 'max_sustained_wind'):
            d = r.get('dimensions', {})
            if d.get('basin') not in basins or d.get('track_type', 'main') != 'main' or r['value'] is None:
                continue
            fix = fixes.setdefault(r['subject'], {}).setdefault(r['valid_from'], {})
            if r['metric'] == 'max_sustained_wind':
                source = d.get('source')
                if source == 'usa' or 'wind' not in fix:
                    fix['wind'] = (r['value'], _light(r))
            else:
                fix[r['metric']] = (r['value'], _light(r))
    centroids = county_points(context)
    by_lat = sorted((p['latitude'][0], c) for c, p in centroids.items())
    lats = [x for x, _ in by_lat]
    dlat = radius_km / 111.0
    geo_ref = context.input_ref('census_geography')
    for storm_id in sorted(fixes):
        meta = storms_meta.get(storm_id)
        if meta is None:
            continue
        track = sorted((t, f) for t, f in fixes[storm_id].items() if 'latitude' in f and 'longitude' in f and 'wind' in f)
        if not track:
            continue
        peak_t, peak = max(track, key=lambda item: item[1]['wind'][0])
        storm_event = _event(context, ds, meta, library_type='tropical_cyclone', family='natural_hazard',
                             unit_type='storm', unit=storm_id, date=track[0][0],
                             date_semantics='first IBTrACS main-track fix with position and wind',
                             intensity={'value': peak['wind'][0], 'unit': 'kt',
                                        'definition': 'lifetime maximum sustained wind (USA agency where reported, '
                                                      'else WMO)', 'timing': 'ex_post'},
                             participants=[storm_id], sources=[meta, peak['wind'][1]],
                             extra={'basin': meta['attributes'].get('basin'), 'name': meta['attributes'].get('name'),
                                    'season': meta['attributes'].get('season'), 'peak_time': peak_t})
        yield storm_event
        hits = {}
        for t, f in track:
            wind = f['wind'][0]
            if wind < min_wind:
                continue
            lat, lon = f['latitude'][0], f['longitude'][0]
            lo, hi = bisect.bisect_left(lats, lat - dlat), bisect.bisect_right(lats, lat + dlat)
            for _, county in by_lat[lo:hi]:
                c = centroids[county]
                d = _haversine_km(lat, lon, c['latitude'][0], c['longitude'][0])
                if d > radius_km:
                    continue
                best = hits.get(county)
                if best is None:
                    hits[county] = {'first': t, 'min_d': d, 'max_wind': wind, 'fix': f}
                else:
                    if wind > best['max_wind']:
                        best['max_wind'], best['fix'] = wind, f
                    best['min_d'] = min(best['min_d'], d)
        for county in sorted(hits):
            h = hits[county]
            c = centroids[county]
            record = _event(context, ds, meta, library_type='tropical_cyclone_county_exposure', family='natural_hazard',
                            unit_type='us_county_fips', unit=county, date=h['first'],
                            date_semantics=f'first main-track fix with wind >= {min_wind} kt within {radius_km:g} km '
                                           'of the county internal point',
                            intensity={'value': h['max_wind'], 'unit': 'kt',
                                       'definition': f'maximum sustained wind of fixes within {radius_km:g} km',
                                       'timing': 'ex_ante'},
                            participants=[county, storm_id],
                            sources=[meta, h['fix']['wind'][1], h['fix']['latitude'][1], h['fix']['longitude'][1]],
                            extra={'min_distance_km': round(h['min_d'], 1), 'storm': storm_id,
                                   'basin': meta['attributes'].get('basin'), 'name': meta['attributes'].get('name')})
            record['evidence'] += [{'input': geo_ref, 'record_id': c['latitude'][1]},
                                   {'input': geo_ref, 'record_id': c['longitude'][1]}]
            yield record


# -- WITS MFN tariff changes ---------------------------------------------------------------------

def tariffs(context, min_change_pp):
    ds = 'wits_trains_tariffs'
    series = {}
    for r in context.records(ds):
        if r['kind'] != 'observation' or r.get('metric') != 'mfn_applied_tariff_simple_avg' or r['value'] is None:
            continue
        d = r['dimensions']
        series.setdefault((r['subject'], d['product']), {})[int(r['valid_from'][:4])] = \
            (r['value'], d.get('hs_revision'), r['id'], r['observed_at'], _locator(r),
             {k: r.get('attributes', {}).get(k) for k in ('total_lines', 'non_ad_valorem_lines')})
    ref = context.input_ref(ds)
    for (reporter, product), years in sorted(series.items()):
        for year in sorted(years):
            if year - 1 not in years:
                continue
            new, old = years[year], years[year - 1]
            if new[1] != old[1]:
                continue
            change = new[0] - old[0]
            if abs(change) < min_change_pp:
                continue
            unit = f'{reporter}|{product}'
            yield {'kind': 'event', 'id': _id('tariff_mfn_change', reporter, product, year),
                   'event_type': 'tariff_mfn_increase' if change > 0 else 'tariff_mfn_decrease',
                   'occurred_at': f'{year}-01-01', 'observed_at': max(new[3], old[3]),
                   'participants': [reporter, product],
                   'attributes': {'library_version': LIBRARY_VERSION,
                                  'library_type': 'tariff_mfn_increase' if change > 0 else 'tariff_mfn_decrease',
                                  'shock_family': 'trade_policy', 'unit_type': 'reporter_x_hs6', 'unit': unit,
                                  'date': f'{year}-01-01',
                                  'date_semantics': 'first day of the first TRAINS reporting year with the new MFN '
                                                    'rate (annual data; the legal effective date within the year is '
                                                    'not published here)',
                                  'intensity': {'value': round(change, 6), 'unit': 'percentage_points',
                                                'definition': 'change in the simple average MFN applied rate of the '
                                                              'HS6 subheading from the previous reporting year',
                                                'timing': 'ex_ante'},
                                  'previous_rate_percent': old[0], 'rate_percent': new[0], 'hs_revision': new[1],
                                  'lines': new[5].get('total_lines'), 'non_ad_valorem_lines': new[5].get('non_ad_valorem_lines'),
                                  'source': {'dataset': ds, 'record_id': new[2], 'locator': new[4],
                                             'previous_record_id': old[2], 'previous_locator': old[4]}},
                   'evidence': [{'input': ref, 'record_id': old[2]}, {'input': ref, 'record_id': new[2]}]}


def run(context):
    p = context.parameters
    yield from fema(context)
    yield from sanctions(context, 'ofac_sanctions')
    yield from sanctions(context, 'other_sanctions_lists')
    yield from storms(context, float(p['storm_min_damage_usd']))
    yield from earthquakes(context, float(p['earthquake_min_magnitude']))
    yield from cyclones(context, set(p['cyclone_basins']), float(p['cyclone_radius_km']), float(p['cyclone_min_wind_kt']))
    yield from tariffs(context, float(p['tariff_min_change_pp']))
