"""``wm place-brief``: one US county across economy, people, hazard, weather, storms and assistance.

This generalises ``examples/graph-queries/q2``. Most legs join on the Census GEOID, because the
publishers record their observations against ``geo:US:county:<GEOID>``. Two legs cannot be
reached that way and come from the products companion index instead:

- storm events and disaster declarations name the county in the event's *participants*
  (the publisher's own field, so this is still asserted by the publisher, not a spatial join);
- OpenFEMA household assistance publishes a county *name* and a state, which is matched to
  FEMA's own label for the GEOID. That leg is a name match and says ``INFERRED, not asserted``.
"""
from pathlib import Path
import re

from .base import (ProductError, Timer, connect, decode, default_index, default_products_index, describe_edges,
                   envelope, index_info, prefix_range, resolution_digest, retrying)
from .search import COUNTY_PREFIX, STATE_FIPS, county_name_key, norm, open_products_index, products_index_info

LEGS = (
    ('geography', 'census_geography', 'Land and water area and the internal point, from the Census gazetteer'),
    ('employment_and_business', 'census_business', 'County Business Patterns and nonemployer statistics'),
    ('jobs', 'lehd_lodes', 'LEHD LODES jobs located in the county'),
    ('population', 'census_population', 'Population Estimates and components of change'),
    ('demographics', 'acs_5yr_tables', 'American Community Survey 5-year estimates'),
    ('agriculture', 'usda_agriculture', 'USDA NASS county estimates and cotton ginnings'),
    ('hazard_risk', 'fema_nri', 'FEMA National Risk Index'),
    ('weather_and_climate', 'noaa_climdiv', 'NOAA nClimDiv monthly county climate'),
)
#: ACS variables worth naming. The index carries whatever tables were acquired; codes absent from
#: this map are still counted and listed.
ACS_NAMES = {
    'acs_b01001_001': 'total population', 'acs_b01001_002': 'male population', 'acs_b01001_026': 'female population',
    'acs_b01002_001': 'median age', 'acs_b19013_001': 'median household income',
    'acs_b19301_001': 'per capita income', 'acs_b17001_002': 'population below the poverty level',
    'acs_b23025_005': 'civilian labour force, unemployed', 'acs_b25077_001': 'median home value',
    'acs_b25001_001': 'housing units', 'acs_b15003_022': 'bachelor\'s degree (age 25+)'}
NRI_HEADLINES = ('national_risk_index_score', 'expected_annual_loss_score', 'social_vulnerability_score',
                 'community_resilience_score', 'building_value_exposure', 'population_exposure',
                 'agriculture_value_exposure')
TOTAL_VALUES = {'00', '000', '0', 'total', 'all', 't', '_t', 'tot', '------', 'all industries', 'total for all sectors'}


def _county_id(text):
    text = str(text).strip()
    if re.fullmatch(r'\d{5}', text):
        return COUNTY_PREFIX + text
    if text.startswith(COUNTY_PREFIX) and re.fullmatch(r'\d{5}', text[len(COUNTY_PREFIX):]):
        return text
    return None


_SUFFIXES = re.compile(r'\b(county|parish|borough|census area|municipality|city and borough)\b')


def find_county(connection, text, products=None):
    """County GEOIDs whose published label matches a name such as 'Autauga County, Alabama' or 'Travis, TX'.

    Uses the products index when present; otherwise it reads the county entity records of one state
    (the state is then required, so the scan stays short). A name is matched on published label
    text, so the result says INFERRED, not asserted.
    """
    wanted, state = str(text), None
    if ',' in wanted:
        wanted, state_text = wanted.rsplit(',', 1)
        state_key = state_text.strip().casefold()
        state = next((fips for fips, (usps, name) in STATE_FIPS.items()
                      if state_key in (usps.casefold(), name.casefold())), None)
        if state is None:
            raise ProductError('Unknown state %r in %r' % (state_text.strip(), text))
    key = norm(wanted)
    key_bare = _SUFFIXES.sub('', key).strip()
    prefix = COUNTY_PREFIX + (state or '')
    if products is not None:
        rows = products.execute('SELECT entity_id, dataset, label FROM labels WHERE entity_id >= ? AND entity_id < ? '
                                "AND source = 'label' AND dataset IN ('census_geography', 'acs_5yr_tables')",
                                prefix_range(prefix)).fetchall()
        described = [(r['entity_id'], r['dataset'], r['label']) for r in rows]
    elif state:
        rows = connection.execute("SELECT entity_id, dataset, body FROM records WHERE entity_id >= ? AND entity_id < ? "
                                  "AND +dataset IN ('census_geography', 'acs_5yr_tables')", prefix_range(prefix)).fetchall()
        described = [(r['entity_id'], r['dataset'], decode(r['body']).get('label') or '') for r in rows]
    else:
        raise ProductError('Without the products index a county name needs its state ("%s, ST"), or pass the '
                           '5-digit FIPS code' % text)
    found = {}
    for entity_id, dataset, label in described:
        name = norm(label.split(',')[0])
        if key == name or (key_bare and key_bare == _SUFFIXES.sub('', name).strip()):
            found.setdefault(entity_id, {'entity_id': entity_id, 'label': label, 'from_dataset': dataset,
                                         'exact': key == name})
    return sorted(found.values(), key=lambda c: (not c['exact'], c['entity_id']))


def _headline(observations):
    """The latest, least-disaggregated observation of one metric (the fewest non-total dimensions)."""
    def detail(obs):
        dims = obs.get('dimensions') or {}
        return sum(1 for k, v in dims.items() if str(v).strip().casefold() not in TOTAL_VALUES)
    best = sorted(observations, key=lambda o: (detail(o), -_time_key(o.get('valid_from'))))[0]
    latest_at_detail = [o for o in observations if detail(o) == detail(best)]
    return max(latest_at_detail, key=lambda o: _time_key(o.get('valid_from')))


def _time_key(value):
    try:
        return int(re.sub(r'\D', '', str(value or ''))[:14].ljust(14, '0'))
    except ValueError:
        return 0


def _summarise(rows, *, names=None):
    by_metric = {}
    for row in rows:
        by_metric.setdefault(row['metric'], []).append(row)
    out = {}
    for metric, items in sorted(by_metric.items()):
        head = _headline(items)
        out[metric] = {'name': (names or {}).get(metric), 'observations': len(items),
                       'first_valid_from': min((i.get('valid_from') or '' for i in items), default=None) or None,
                       'last_valid_from': max((i.get('valid_from') or '' for i in items), default=None) or None,
                       'headline': {k: head.get(k) for k in ('value', 'unit', 'valid_from', 'valid_to', 'dimensions',
                                                             'missing_reason', 'record_id')},
                       'headline_rule': 'latest observation with the fewest non-total dimensions'}
    return out


def _series(rows, metric, *, minimal=True, last=None):
    items = [r for r in rows if r['metric'] == metric and isinstance(r.get('value'), (int, float))
             and not isinstance(r.get('value'), bool)]
    if not items:
        return []
    if minimal:
        detail = lambda o: sum(1 for v in (o.get('dimensions') or {}).values()
                               if str(v).strip().casefold() not in TOTAL_VALUES)
        least = min(detail(o) for o in items)
        items = [o for o in items if detail(o) == least]
        dims = {}
        for item in items:  # one dimension set only, so the series is one line
            dims.setdefault(repr(sorted((item.get('dimensions') or {}).items())), []).append(item)
        # The dimension set that reaches the latest date (the newest vintage), then the longest: a plot that
        # mixed Population Estimates vintages would draw a revision as a change.
        items = max(dims.values(), key=lambda g: (max(_time_key(o.get('valid_from')) for o in g), len(g)))
    seen, series = set(), []
    for item in sorted(items, key=lambda o: _time_key(o.get('valid_from'))):
        if item.get('valid_from') in seen:
            continue
        seen.add(item.get('valid_from'))
        series.append({'time': item.get('valid_from'), 'value': item['value'], 'unit': item.get('unit'),
                       'record_id': item.get('record_id'), 'from_dataset': item['from_dataset'],
                       'dimensions': item.get('dimensions')})
    return series[-last:] if last else series


def _by_commodity(rows, metrics=('area_harvested', 'area_planted', 'production', 'sales'), top=8):
    """Latest numeric value per (metric, unit, commodity), the largest ``top`` per metric and unit."""
    latest = {}
    for row in rows:
        value = row.get('value')
        if row['metric'] not in metrics or not isinstance(value, (int, float)) or isinstance(value, bool):
            continue
        dims = row.get('dimensions') or {}
        key = (row['metric'], row.get('unit'), dims.get('commodity'), dims.get('class'), dims.get('program'))
        if key not in latest or _time_key(row.get('valid_from')) > _time_key(latest[key].get('valid_from')):
            latest[key] = row
    out = {}
    for (metric, unit, commodity, klass, program), row in latest.items():
        out.setdefault('%s (%s)' % (metric, unit), []).append(
            {'commodity': commodity, 'class': klass, 'program': program, 'value': row['value'],
             'valid_from': row.get('valid_from'), 'record_id': row.get('record_id')})
    return {key: sorted(items, key=lambda i: -i['value'])[:top] for key, items in sorted(out.items())}


def _annual_climate(rows, metric):
    """Calendar-year means of a monthly nClimDiv series (complete years only), computed here."""
    months = {}
    for row in rows:
        if row['metric'] != metric or not isinstance(row.get('value'), (int, float)):
            continue
        stamp = str(row.get('valid_from') or '')
        if len(stamp) >= 7:
            months.setdefault(stamp[:4], {})[stamp[5:7]] = row['value']
    annual = {year: sum(v.values()) / 12 for year, v in months.items() if len(v) == 12}
    if not annual:
        return None
    normal = [v for y, v in annual.items() if '1991' <= y <= '2020']
    latest = max(annual)
    return {'latest_complete_year': latest, 'latest_annual_mean': round(annual[latest], 3),
            'mean_1991_2020': round(sum(normal) / len(normal), 3) if len(normal) == 30 else None,
            'years': len(annual), 'computed_here': 'mean of the 12 published monthly values per calendar year',
            'annual': [{'time': '%s-01-01' % y, 'value': round(v, 3)} for y, v in sorted(annual.items())]}


def place_brief(place, *, index=None, products_index=None, events=12, data_root=None, catalog_root=None):
    timer = Timer()
    index = Path(index or default_index(data_root))
    connection = connect(index)
    try:
        return retrying(_place_brief, connection, index, place, timer=timer,
                        products_path=products_index or default_products_index(index), events=events,
                        data_root=data_root, catalog_root=catalog_root)
    finally:
        connection.close()


def _place_brief(connection, index, place, *, timer, products_path, events, data_root, catalog_root):
    with timer.step('open'):
        info = index_info(connection, index)
        at_start = resolution_digest(connection)
        products = open_products_index(products_path, info)
    common = dict(info=info, timer=timer, resolution_at_start=at_start, data_root=data_root, catalog_root=catalog_root)
    with timer.step('resolve'):
        county = _county_id(place)
        selection = {'basis': 'county GEOID supplied by the caller'}
        if county is None:
            candidates = find_county(connection, place, products)
            if not candidates:
                raise ProductError('No county label matches %r; pass a 5-digit county FIPS code or "Name, ST"' % place)
            if len(candidates) > 1 and not (candidates[0]['exact'] and not candidates[1]['exact']):
                raise ProductError('%r matches %d counties (%s); add the state, e.g. "%s, ST", or pass the FIPS code'
                                   % (place, len(candidates), ', '.join('%s %s' % (c['entity_id'][-5:], c['label'])
                                                                         for c in candidates[:8]), place))
            county = candidates[0]['entity_id']
            selection = {'basis': 'name matched to the Census county label "%s" (%s) - INFERRED, not asserted'
                                  % (candidates[0]['label'], candidates[0]['from_dataset']),
                         'candidates': candidates[:5]}
    geoid = county[len(COUNTY_PREFIX):]
    with timer.step('describe'):
        described = [dict(r) for r in connection.execute('SELECT dataset, body FROM records WHERE entity_id=?', (county,))]
        descriptions = [{'from_dataset': r['dataset'], 'label': decode(r['body']).get('label')} for r in described]
        descriptions = [d for d in descriptions if d['label'] and d['label'] != county]
        if not described and not connection.execute('SELECT 1 FROM records WHERE subject=? LIMIT 1',
                                                       (county,)).fetchone():
            raise ProductError('%s is not in this index' % county)
    with timer.step('observations'):
        rows = []
        for row in connection.execute("SELECT dataset, id, body FROM records WHERE subject=? AND kind='observation' "
                                      'LIMIT 100000', (county,)):
            body = decode(row['body'])
            rows.append({'from_dataset': row['dataset'], 'record_id': row['id'], 'metric': body.get('metric'),
                         'value': body.get('value'), 'unit': body.get('unit'), 'valid_from': body.get('valid_from'),
                         'valid_to': body.get('valid_to'), 'dimensions': body.get('dimensions'),
                         'missing_reason': body.get('missing_reason')})
    by_dataset = {}
    for row in rows:
        by_dataset.setdefault(row['from_dataset'], []).append(row)
    answer = {'county': {'entity_id': county, 'geoid': geoid, 'state': STATE_FIPS.get(geoid[:2], (None, None))[1],
                         'labels': descriptions, 'selection': selection}}
    missing = []
    with timer.step('legs'):
        for leg, dataset, what in LEGS:
            found = by_dataset.get(dataset, [])
            if not found:
                answer[leg] = {'expected_dataset': dataset, 'what': what, 'observations': 0,
                               'note': 'no observation for this county in this index' if dataset in info['pinned_versions']
                               else '%s is not in this index' % dataset}
                missing.append(dataset)
                continue
            names = ACS_NAMES if dataset == 'acs_5yr_tables' else None
            metrics = _summarise(found, names=names)
            if dataset == 'acs_5yr_tables':
                named = {k: v for k, v in metrics.items() if k in ACS_NAMES}
                answer[leg] = {'dataset': dataset, 'what': what, 'observations': len(found),
                               'metrics_published': len(metrics), 'named_metrics': named,
                               'other_metric_codes': sorted(set(metrics) - set(named))[:400]}
            elif dataset == 'fema_nri':
                eal = sorted((r for r in found if r['metric'] == 'expected_annual_loss'
                              and isinstance(r.get('value'), (int, float))),
                             key=lambda r: -r['value'])
                answer[leg] = {'dataset': dataset, 'what': what, 'observations': len(found),
                               'headlines': {k: v for k, v in metrics.items() if k in NRI_HEADLINES},
                               'largest_expected_annual_loss': [{k: r[k] for k in ('value', 'unit', 'dimensions',
                                                                                    'valid_from', 'record_id')}
                                                                for r in eal[:8]],
                               'other_metrics': sorted(set(metrics) - set(NRI_HEADLINES))}
            elif dataset == 'usda_agriculture':
                answer[leg] = {'dataset': dataset, 'what': what, 'observations': len(found),
                               'metrics': {m: {k: v[k] for k in ('observations', 'first_valid_from', 'last_valid_from')}
                                           for m, v in metrics.items()},
                               'largest_by_commodity': _by_commodity(found),
                               'note': 'NASS publishes one series per commodity, practice and program, so no single '
                                       'headline exists; the largest latest values per commodity are listed instead.'}
            else:
                answer[leg] = {'dataset': dataset, 'what': what, 'observations': len(found), 'metrics': metrics}
        climate = by_dataset.get('noaa_climdiv', [])
        if climate:
            answer['weather_and_climate']['annual_means_computed_here'] = {
                metric: _annual_climate(climate, metric) for metric in ('average_temperature', 'precipitation')}
        answer['series'] = {
            'population': _series(by_dataset.get('census_population', []), 'population'),
            'employment': _series(by_dataset.get('census_business', []), 'employment'),
            'average_temperature_monthly': _series(climate, 'average_temperature', last=120)}
    with timer.step('containment_and_migration'):
        from ..graph import Graph
        graph = Graph(index)
        containment = describe_edges(connection, graph.neighborhood(county, hops=1, predicates=['within'],
                                                                   direction='out', limit=20)['edges'])
        flows = graph.neighborhood(county, hops=1, predicates=['flow_source', 'flow_destination'], direction='in',
                                   limit=2000)
        answer['containment'] = containment
        answer['migration_flows'] = {('dataset' if flows['edges'] else 'expected_dataset'): 'irs_soi_migration',
                                     'edges': len(flows['edges']),
                                     'truncated': flows['truncated'],
                                     'examples': describe_edges(connection, flows['edges'][:4])}
    with timer.step('storms_and_assistance'):
        answer['storms'], answer['disaster_assistance'] = _events(connection, products, county, events)
    runs_out = _runs_out(info, missing, answer, products)
    not_established = [
        'Joining on a GEOID joins a geography vintage, not a stable place: Connecticut replaced its counties with '
        'planning regions from 2022, and county splits and renames change what a code covers. Cross-vintage totals '
        'need worldmodel.crosswalks, not a string match.',
        'Periods differ by leg: County Business Patterns is a March pay period, Population Estimates are 1 July '
        'stocks, ACS is a five-year average, nClimDiv is monthly, the NRI is one vintage, LODES an annual snapshot. '
        'Nothing here puts them on one timeline.',
        'Suppressed cells are null with a missing_reason, not zero.',
        'No causal claim: hazard risk, employment, climate, storms and assistance are separate measurements of one '
        'polygon.',
        'Storm damage is nominal USD as reported to NOAA, not inflation-adjusted and not insured loss; storm reports '
        'are not a complete census of events, especially before 1996.',
        'The NRI is modelled expected annual loss and a relative rating, not a forecast or an insurance price.',
        'A headline value is the latest observation with the fewest non-total dimensions; it is a reading aid, and '
        'the dimensions it carries say what it measures.']
    return envelope('place-brief', query={'place': place}, answer=answer, runs_out=runs_out,
                    not_established=not_established, resolution_at_end=resolution_digest(connection),
                    extra={'products_index': products_index_info(products)}, **common)


def _events(connection, products, county, limit):
    if products is None:
        note = ('Needs the products index (python3 -m worldmodel products-index): these publishers anchor records on '
                'the event, not the county, so a GEOID lookup on the unified index never reaches them.')
        return ({'expected_dataset': 'noaa_storm_events', 'available': False, 'note': note},
                {'expected_dataset': 'openfema', 'available': False, 'note': note})
    storms = [dict(r) for r in products.execute(
        "SELECT * FROM place_events WHERE geoid=? AND dataset='noaa_storm_events' ORDER BY occurred_at", (county,))]
    by_type = {}
    for row in storms:
        item = by_type.setdefault(row['event_type'], {'event_type': row['event_type'], 'events': 0, 'deaths': 0.0,
                                                      'injuries': 0.0, 'damage_property_usd_nominal': 0.0,
                                                      'damage_crops_usd_nominal': 0.0})
        item['events'] += 1
        for key, column in (('deaths', 'deaths'), ('injuries', 'injuries'),
                            ('damage_property_usd_nominal', 'damage_property'),
                            ('damage_crops_usd_nominal', 'damage_crops')):
            item[key] += row[column] or 0.0
    by_decade = {}
    for row in storms:
        decade = (row['occurred_at'] or '????')[:3] + '0s'
        by_decade[decade] = by_decade.get(decade, 0) + 1
    shape = lambda r: {'record_id': r['record_id'], 'event': r['reference'], 'event_type': r['event_type'],
                       'label': r['title'], 'occurred_at': r['occurred_at'], 'deaths': r['deaths'],
                       'injuries': r['injuries'], 'damage_property_usd_nominal': r['damage_property'],
                       'latitude': r['latitude'], 'longitude': r['longitude']}
    storm_leg = {('dataset' if storms else 'expected_dataset'): 'noaa_storm_events', 'available': True,
                 'events': len(storms),
                 'join_basis': 'NOAA publishes the county GEOID among the event\'s participants (asserted by the '
                               'publisher; no spatial join)',
                 'first': storms[0]['occurred_at'] if storms else None,
                 'last': storms[-1]['occurred_at'] if storms else None,
                 'by_event_type': sorted(by_type.values(), key=lambda x: -x['events']),
                 'by_decade': dict(sorted(by_decade.items())),
                 'latest': [shape(r) for r in storms[-limit:][::-1]],
                 'costliest': [shape(r) for r in sorted(storms, key=lambda r: -(r['damage_property'] or 0))[:limit]
                               if (r['damage_property'] or 0) > 0],
                 'located': [shape(r) for r in storms if r['latitude'] is not None and r['longitude'] is not None][-300:]}
    declarations = [dict(r) for r in products.execute(
        "SELECT * FROM place_events WHERE geoid=? AND dataset='openfema' ORDER BY occurred_at", (county,))]
    by_incident = {}
    for row in declarations:
        by_incident[row['event_type']] = by_incident.get(row['event_type'], 0) + 1
    labels = [r['body'] for r in connection.execute("SELECT body FROM records WHERE entity_id=? AND dataset='openfema'",
                                                    (county,))]
    fema_name = next((decode(b).get('label') for b in labels if decode(b).get('label')), None)
    usps = STATE_FIPS.get(county[len(COUNTY_PREFIX):][:2], (None, None))[0]
    named, key = [], county_name_key(fema_name) if fema_name else None
    if key and usps:
        named = [dict(r) for r in products.execute('SELECT * FROM place_named WHERE state=? AND county_key=?',
                                                   (usps, key))]
    totals = {}
    for row in named:
        item = totals.setdefault((row['metric'], row['unit']), {'metric': row['metric'], 'unit': row['unit'],
                                                                'total': 0.0, 'observations': 0, 'disasters': set()})
        item['total'] += row['total']
        item['observations'] += row['observations']
        item['disasters'].add(row['disaster'])
    assistance = {('dataset' if declarations or named else 'expected_dataset'): 'openfema', 'available': True,
                  'declarations': {'count': len(declarations),
                                   'join_basis': 'FEMA publishes the county FIPS among the declaration\'s participants '
                                                 '(asserted by the publisher)',
                                   'by_incident_type': dict(sorted(by_incident.items(), key=lambda x: -x[1])),
                                   'latest': [{'record_id': r['record_id'], 'disaster': r['reference'],
                                               'declaration': (r['title'] or '').strip(), 'incident_type': r['event_type'],
                                               'declared': r['occurred_at']} for r in declarations[-limit:][::-1]]},
                  'household_and_public_assistance': {
                      'matched_county_name': fema_name, 'state': usps,
                      'join_basis': 'OpenFEMA assistance rows publish a county name and a state, not a FIPS. They are '
                                    'matched to FEMA\'s own label for this GEOID ("%s") - a name match within one '
                                    'publisher, INFERRED, not asserted' % fema_name if fema_name else
                                    'OpenFEMA publishes no label for this GEOID, so no name match was attempted',
                      'totals_by_metric': [{**{k: v for k, v in t.items() if k != 'disasters'},
                                            'disasters': len(t['disasters'])}
                                           for t in sorted(totals.values(), key=lambda t: t['metric'])],
                      'computed_here': 'sums of the published per-zip and per-applicant values, across disasters'}}
    return storm_leg, assistance


def _runs_out(info, missing, answer, products):
    out = []
    if missing:
        out.append('No observation for this county from: %s.' % ', '.join(missing))
    if products is None:
        out.append('Storm events and disaster assistance were not read: build the products index.')
    else:
        out.append('Storm events that NOAA files against a forecast zone rather than a county (heat, winter weather, '
                   'drought, coastal and marine events) name no county and are not counted here; nothing maps zones '
                   'to counties.')
        out.append('Disaster assistance amounts are summed from OpenFEMA rows matched by county name; a county whose '
                   'name FEMA spells differently in its assistance files is undercounted, not zero.')
    for dataset, note in (('epa_aqs_daily', 'air quality'), ('usaspending_assistance', 'federal assistance awards')):
        if dataset not in info['pinned_versions']:
            out.append('%s (%s) is not in this index.' % (dataset, note))
    if answer.get('migration_flows', {}).get('truncated'):
        out.append('Migration flow edges were truncated at 2,000.')
    return out
