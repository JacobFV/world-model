"""Standalone HTML reports for the products, rendered with ``worldmodel.surfaces``.

The data panels (tables, plots, the coordinate map and the counterparty graph) are ordinary
``render_surface`` panels, so they inherit its escaping, bounds, omitted-row counts and local-only
rendering (no remote scripts, tiles or fonts). What surfaces has no panel for - the caveats, the
rights inventory and the provenance of the answer - is prepended as escaped static HTML, because a
report that dropped them would not be the same answer.
"""
from html import escape
import json

from ..limits import current_limits
from ..surfaces import render_surface


def _text(value):
    return escape(value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True), quote=True)


def _list(items):
    return '<ul>' + ''.join('<li>' + _text(item) + '</li>' for item in items) + '</ul>'


def _prelude(result, summary):
    rights = result.get('rights') or {}
    index = result.get('index') or {}
    rows = ''.join('<tr><td>%s</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>' % (
        _text(r['dataset']), _text(', '.join(r['license_id'] or r['license']) or 'unspecified'),
        _text(', '.join(r['license_status']) or '-'), _text(', '.join(r['redistribution']) or '-'),
        _text((r.get('identified_persons') or {}).get('policy'))) for r in rights.get('datasets', []))
    parts = [
        '<section class="prelude"><h2>Summary</h2>' + _list(summary) + '</section>',
        '<section class="prelude"><h2>Where the evidence runs out</h2>'
        + _list(result.get('where_the_evidence_runs_out') or []) + '</section>',
        '<section class="prelude"><h2>What this does not establish</h2>'
        + _list(result.get('what_this_does_not_establish') or []) + '</section>',
        '<section class="prelude"><h2>Rights and use policy</h2>' + _list(rights.get('notices') or [])
        + '<p>Declared purpose: ' + _text(rights.get('declared_purpose')) + '. ' + _text(rights.get('interpretation'))
        + '</p><div class="table-scroll"><table><thead><tr><th>dataset</th><th>licence</th><th>licence status</th>'
          '<th>redistribution</th><th>identified persons</th></tr></thead><tbody>' + rows + '</tbody></table></div>'
          '</section>',
        '<section class="prelude"><h2>Provenance</h2><p>Index ' + _text(index.get('path')) + ', schema '
        + _text(index.get('schema_version')) + ', ' + _text(index.get('inputs')) + ' pinned inputs (digest '
        + _text(index.get('inputs_digest')) + '), resolution view ' + _text(index.get('resolution_view_digest'))
        + '. Datasets used: ' + _text(', '.join(result.get('datasets_used') or [])) + '. Timings (s): '
        + _text(result.get('timings_s')) + '</p></section>']
    if result.get('resolution_changed_during_answer'):
        parts.insert(0, '<section class="prelude"><h2>Warning</h2><p>'
                     + _text(result['resolution_changed_during_answer']['note']) + '</p></section>')
    return ('<style>.prelude{background:white;border:1px solid #cbd5e1;border-radius:12px;padding:18px 22px;'
            'margin:0 0 20px}.prelude li{margin:6px 0}</style><div class="preludes">' + ''.join(parts) + '</div>')


def compose(result, summary, data, spec):
    """``render_surface`` for the panels, with the caveats and rights prepended; bounded like it."""
    html = render_surface(data, spec)
    head, marker, tail = html.partition('</h1>')
    html = head + marker + _prelude(result, summary) + tail
    current_limits().check('surface_max_html_bytes', len(html.encode('utf-8')), 'report exceeds HTML byte bound')
    return html


def _rights_rows(result):
    return [{'time': None, 'entity': r['dataset'], 'variable': 'rights',
             'value': {k: r[k] for k in ('license_id', 'license_status', 'redistribution', 'attribution', 'terms_url',
                                         'non_commercial_only', 'redistribution_review_required')},
             'unit': None, 'origin': r['rights_metadata_from'], 'sources': [{'version': r['version']}]}
            for r in (result.get('rights') or {}).get('datasets', [])]


def _only_nonempty(panels, rows):
    variables = {r['variable'] for r in rows}
    return [p for p in panels if p.get('kind') not in ('table', 'plot', 'value') or p.get('variable') in variables]


def render_dossier(result):
    answer = result['answer']
    if not answer.get('entity'):
        return compose(result, ['The input did not resolve to an entity in this index.'],
                       {'snapshots': []}, {'title': 'Dossier: not found', 'panels': []})
    entity = answer['entity']
    cluster = answer['asserted_identity_cluster']
    canonical = entity['canonical_id']
    rows = []
    for item in answer['descriptions']:
        rows.append({'time': None, 'entity': item['entity_id'], 'variable': 'description', 'value': item['label'],
                     'unit': item.get('entity_type'), 'origin': item['from_dataset'], 'record_id': item['record_id']})
    for item in answer['identifiers']:
        rows.append({'time': None, 'entity': item['subject'], 'variable': 'identifier claim',
                     'value': item['identifier_values'] or item['value'], 'unit': item['predicate'],
                     'origin': item['from_dataset'], 'record_id': item['record_id']})
    for group in answer['edges_by_predicate_and_dataset']:
        rows.append({'time': None, 'entity': '%s %s' % (group['direction'], group['predicate']), 'variable': 'edges',
                     'value': group['edges'], 'unit': 'edges',
                     'origin': ', '.join('%s: %d' % kv for kv in sorted(group['by_dataset'].items())),
                     'sources': [{k: e[k] for k in ('subject', 'object', 'from_dataset', 'record_id')}
                                 for e in group['examples']]})
    for item in answer['observations']:
        latest = item['latest']
        rows.append({'time': item['last_valid_from'], 'entity': item['subject'], 'variable': 'observation series',
                     'value': {'metric': item['metric'], 'observations': item['observations'], 'latest': latest['value']},
                     'unit': latest['unit'], 'origin': item['from_dataset'], 'record_id': latest['record_id']})
    for item in answer['events']:
        rows.append({'time': item['last_valid_from'], 'entity': item['subject'], 'variable': 'events',
                     'value': item['events'], 'unit': 'events', 'origin': item['from_dataset'],
                     'sources': item['examples']})
    reach = answer['counterparties']
    records = [{'kind': 'entity', 'entity_id': canonical, 'label': entity.get('label') or canonical}]
    for depth in ('hop_1', 'hop_2'):
        for node in reach[depth]:
            rows.append({'time': None, 'entity': node['entity_id'], 'variable': 'counterparty (%s)' % depth.replace('_', ' '),
                         'value': node.get('label'), 'unit': ', '.join(node['predicates']),
                         'origin': ', '.join(node['from_datasets']), 'sources': node['example_edges']})
            records.append({'kind': 'entity', 'entity_id': node['entity_id'], 'label': node.get('label') or node['entity_id']})
    for node in reach['hop_1']:
        records.append({'kind': 'assertion', 'id': 'hop1:' + node['entity_id'], 'subject': canonical,
                        'predicate': ', '.join(node['predicates']), 'object': node['entity_id'],
                        'origin': ', '.join(node['from_datasets']),
                        'evidence': [e['record_id'] for e in node['example_edges']]})
    shown = {n['entity_id'] for n in reach['hop_1']}
    for node in reach['hop_2']:
        for via in node.get('via', [])[:2]:
            if via in shown:
                records.append({'kind': 'assertion', 'id': 'hop2:%s:%s' % (via, node['entity_id']), 'subject': via,
                                'predicate': ', '.join(node['predicates']), 'object': node['entity_id'],
                                'origin': ', '.join(node['from_datasets']),
                                'evidence': [e['record_id'] for e in node['example_edges']]})
    rows.extend(_rights_rows(result))
    panels = [
        {'kind': 'table', 'title': 'Descriptions of the cluster members', 'variable': 'description', 'limit': 200},
        {'kind': 'table', 'title': 'Identifier claims', 'variable': 'identifier claim', 'limit': 200},
        {'kind': 'table', 'title': 'Edges by predicate, with the datasets that published them', 'variable': 'edges',
         'limit': 150},
        {'kind': 'graph', 'title': 'Counterparties within two hops (asserted clusters)', 'seeds': [canonical],
         'limit': 80, 'edge_limit': 200},
        {'kind': 'table', 'title': 'Counterparties, hop 1', 'variable': 'counterparty (hop 1)', 'limit': 100},
        {'kind': 'table', 'title': 'Counterparties, hop 2', 'variable': 'counterparty (hop 2)', 'limit': 100},
        {'kind': 'table', 'title': 'Observation series', 'variable': 'observation series', 'limit': 200},
        {'kind': 'table', 'title': 'Events', 'variable': 'events', 'limit': 100},
        {'kind': 'table', 'title': 'Rights of each contributing dataset', 'variable': 'rights', 'limit': 200}]
    panels = _only_nonempty(panels, rows)
    counts = answer['counts']
    summary = ['%s (%s), selected by: %s' % (entity.get('label') or entity['selected'], entity['selected'],
                                              entity['selection']['basis']),
               'Asserted identity cluster of %d: %s' % (cluster['size'], ', '.join(cluster['members'][:12])),
               '%d edges over %d predicates, %d observation series, %d event groups, %d hop-1 and %d hop-2 '
               'counterparties seen.' % (counts['edges'], counts['edge_predicates'], counts['observation_series'],
                                         counts['event_groups'], counts['hop_1_counterparties'],
                                         counts['hop_2_counterparties_seen'])]
    return compose(result, summary, {'snapshots': rows, 'records': records},
                   {'title': 'Dossier: %s' % (entity.get('label') or entity['selected']), 'panels': panels})


def render_place(result):
    answer = result['answer']
    county = answer['county']
    name = next((d['label'] for d in county['labels'] if d.get('label') and ',' in d['label']),
                county['entity_id'])
    rows = []
    for key, variable in (('population', 'population'), ('employment', 'employment'),
                          ('average_temperature_monthly', 'average temperature (monthly)')):
        for point in answer['series'][key]:
            rows.append({'time': point['time'], 'entity': county['entity_id'], 'variable': variable,
                         'value': point['value'], 'unit': point['unit'], 'origin': point['from_dataset'],
                         'record_id': point['record_id']})
    annual = ((answer.get('weather_and_climate') or {}).get('annual_means_computed_here') or {}).get('average_temperature')
    unit = next((p['unit'] for p in answer['series']['average_temperature_monthly']), None)
    for point in (annual or {}).get('annual', []):
        rows.append({'time': point['time'], 'entity': county['entity_id'], 'variable': 'annual mean temperature',
                     'value': point['value'], 'unit': unit, 'origin': 'computed here from noaa_climdiv'})
    for group, items in ((answer.get('agriculture') or {}).get('largest_by_commodity') or {}).items():
        for item in items:
            rows.append({'time': item['valid_from'], 'entity': '%s: %s' % (group, item['commodity']),
                         'variable': 'agriculture by commodity', 'value': item['value'],
                         'unit': item['program'], 'origin': 'usda_agriculture', 'record_id': item['record_id']})
    for leg in ('geography', 'employment_and_business', 'jobs', 'population', 'weather_and_climate'):
        for metric, item in (answer.get(leg) or {}).get('metrics', {}).items():
            head = item['headline']
            rows.append({'time': head['valid_from'], 'entity': '%s: %s' % (leg, metric), 'variable': 'headline',
                         'value': head['value'], 'unit': head['unit'],
                         'origin': answer[leg]['dataset'], 'record_id': head['record_id'],
                         'sources': [{'dimensions': head['dimensions'], 'observations': item['observations']}]})
    demographics = answer.get('demographics') or {}
    for metric, item in demographics.get('named_metrics', {}).items():
        head = item['headline']
        rows.append({'time': head['valid_from'], 'entity': 'demographics: %s (%s)' % (item['name'], metric),
                     'variable': 'headline', 'value': head['value'], 'unit': head['unit'],
                     'origin': 'acs_5yr_tables', 'record_id': head['record_id']})
    hazard = answer.get('hazard_risk') or {}
    for metric, item in hazard.get('headlines', {}).items():
        head = item['headline']
        rows.append({'time': head['valid_from'], 'entity': 'hazard: %s' % metric, 'variable': 'headline',
                     'value': head['value'], 'unit': head['unit'], 'origin': 'fema_nri', 'record_id': head['record_id']})
    for item in hazard.get('largest_expected_annual_loss', []):
        rows.append({'time': item['valid_from'], 'entity': json.dumps(item['dimensions'], sort_keys=True),
                     'variable': 'expected annual loss by hazard', 'value': item['value'], 'unit': item['unit'],
                     'origin': 'fema_nri', 'record_id': item['record_id']})
    storms = answer.get('storms') or {}
    for item in storms.get('by_event_type', []):
        rows.append({'time': None, 'entity': item['event_type'], 'variable': 'storm events by type',
                     'value': {k: round(v, 2) if isinstance(v, float) else v for k, v in item.items()
                               if k != 'event_type'}, 'unit': 'events; USD nominal', 'origin': 'noaa_storm_events'})
    for item in storms.get('located', []):
        for axis in ('latitude', 'longitude'):
            rows.append({'time': item['occurred_at'], 'entity': item['event'] or item['record_id'], 'variable': axis,
                         'value': item[axis], 'unit': 'degrees', 'origin': 'noaa_storm_events',
                         'record_id': item['record_id']})
    assistance = answer.get('disaster_assistance') or {}
    for item in (assistance.get('declarations') or {}).get('latest', []):
        rows.append({'time': item['declared'], 'entity': item['disaster'], 'variable': 'disaster declaration',
                     'value': item['declaration'], 'unit': item['incident_type'], 'origin': 'openfema',
                     'record_id': item['record_id']})
    for item in (assistance.get('household_and_public_assistance') or {}).get('totals_by_metric', []):
        rows.append({'time': None, 'entity': item['metric'], 'variable': 'assistance (name-matched)',
                     'value': round(item['total'], 2), 'unit': item['unit'],
                     'origin': 'openfema (county name match - INFERRED)',
                     'sources': [{'observations': item['observations'], 'disasters': item['disasters']}]})
    rows.extend(_rights_rows(result))
    panels = [
        {'kind': 'value', 'title': 'Population, latest estimate', 'entity': county['entity_id'],
         'variable': 'population'},
        {'kind': 'plot', 'title': 'Population', 'entity': county['entity_id'], 'variable': 'population'},
        {'kind': 'plot', 'title': 'Employment (County Business Patterns)', 'entity': county['entity_id'],
         'variable': 'employment'},
        {'kind': 'plot', 'title': 'Annual mean temperature, computed from nClimDiv monthly values',
         'entity': county['entity_id'], 'variable': 'annual mean temperature', 'limit': 200},
        {'kind': 'table', 'title': 'Headline values by leg', 'variable': 'headline', 'limit': 400},
        {'kind': 'table', 'title': 'Agriculture: largest latest values by commodity (NASS)',
         'variable': 'agriculture by commodity', 'limit': 100},
        {'kind': 'table', 'title': 'Largest expected annual loss by hazard (NRI)',
         'variable': 'expected annual loss by hazard'},
        {'kind': 'table', 'title': 'Storm events by type', 'variable': 'storm events by type'},
        {'kind': 'map', 'title': 'Storm event begin points (coordinate plot, no basemap)', 'limit': 150},
        {'kind': 'table', 'title': 'Disaster declarations naming this county', 'variable': 'disaster declaration'},
        {'kind': 'table', 'title': 'Assistance totals matched by county name (INFERRED)',
         'variable': 'assistance (name-matched)'},
        {'kind': 'table', 'title': 'Rights of each contributing dataset', 'variable': 'rights', 'limit': 200}]
    panels = _only_nonempty(panels, rows)
    if not storms.get('located'):
        panels = [p for p in panels if p['kind'] != 'map']
    population = answer['series']['population'][-1] if answer['series']['population'] else None
    summary = ['%s (%s), selected by: %s' % (name, county['entity_id'], county['selection']['basis']),
               'Population %s (%s, %s).' % ((population['value'], population['time'], population['from_dataset'])
                                            if population else ('not published here', '-', '-')),
               'Storm events naming this county: %s; disaster declarations naming it: %s.'
               % (storms.get('events', 'not read'), (assistance.get('declarations') or {}).get('count', 'not read'))]
    return compose(result, summary, {'snapshots': rows}, {'title': 'Place brief: %s' % name, 'panels': panels})
