"""Full-file adapters for usgs_resources (imported lazily by pipeline.run so the legacy sample adapter stays standalone).

* MRDS (mrds-csv.zip / mrds.csv): one deposit entity per dep_id with commodity `contains_resource` assertions,
  location observations and development status; country/state geography links. MRDS is a legacy inventory
  (largely pre-2011); development status has no reliable valid time.
* MCS 2026 Commodities_Data.csv: salient U.S. and world statistics by commodity, country and year
  (production, reserves, prices, trade...), values kept in their published unit; withheld (W), not available (NA),
  and '--' markers become missing observations; '>' / '<' bounds and 'e' estimates are flagged.
* MCS 2026 T3 (state value and rank of nonfuel mineral production) and T7 (critical minerals salient statistics).
"""
import re
import zipfile

COMMODITY = re.compile(r'[^a-z0-9]+')
COUNTRIES = {'United States': 'USA'}


def _slug(text):
    return COMMODITY.sub('_', (text or '').lower()).strip('_') or 'unknown'


def _value(text):
    """Return (number or None, flags dict)."""
    raw = (text or '').strip()
    flags = {}
    if raw in ('', 'NA', 'W', '--', '—', 'XX', '(W)', '(NA)', 'Large', 'large', 'Moderate', 'Small'):
        return None, {'source_marker': raw} if raw else {}
    cleaned = raw.replace(',', '').replace('$', '').strip()
    if cleaned[:1] in '<>':
        flags['bound'] = 'upper' if cleaned[0] == '<' else 'lower'
        cleaned = cleaned[1:].strip()
    if cleaned.endswith('e'):
        flags['estimated'] = True
        cleaned = cleaned[:-1]
    try:
        number = float(cleaned)
    except ValueError:
        return None, {'source_marker': raw}
    return (int(number) if number.is_integer() else number), flags


def _year_period(text):
    match = re.match(r'(\d{4})', str(text or ''))
    if not match:
        return None
    year = int(match.group(1))
    return f'{year}-01-01', f'{year + 1}-01-01', ('estimated' in str(text))


def _state_fips():
    from worldmodel.source_helpers import STATE_FIPS
    return STATE_FIPS


NAMES_TO_POSTAL = {'Alabama': 'AL', 'Alaska': 'AK', 'Arizona': 'AZ', 'Arkansas': 'AR', 'California': 'CA', 'Colorado': 'CO',
                   'Connecticut': 'CT', 'Delaware': 'DE', 'District of Columbia': 'DC', 'Florida': 'FL', 'Georgia': 'GA',
                   'Hawaii': 'HI', 'Idaho': 'ID', 'Illinois': 'IL', 'Indiana': 'IN', 'Iowa': 'IA', 'Kansas': 'KS',
                   'Kentucky': 'KY', 'Louisiana': 'LA', 'Maine': 'ME', 'Maryland': 'MD', 'Massachusetts': 'MA',
                   'Michigan': 'MI', 'Minnesota': 'MN', 'Mississippi': 'MS', 'Missouri': 'MO', 'Montana': 'MT',
                   'Nebraska': 'NE', 'Nevada': 'NV', 'New Hampshire': 'NH', 'New Jersey': 'NJ', 'New Mexico': 'NM',
                   'New York': 'NY', 'North Carolina': 'NC', 'North Dakota': 'ND', 'Ohio': 'OH', 'Oklahoma': 'OK',
                   'Oregon': 'OR', 'Pennsylvania': 'PA', 'Rhode Island': 'RI', 'South Carolina': 'SC', 'South Dakota': 'SD',
                   'Tennessee': 'TN', 'Texas': 'TX', 'Utah': 'UT', 'Vermont': 'VT', 'Virginia': 'VA', 'Washington': 'WA',
                   'West Virginia': 'WV', 'Wisconsin': 'WI', 'Wyoming': 'WY', 'Puerto Rico': 'PR'}


def _kind(shard, receipt):
    name = str((shard.get('request') or {}).get('url') or receipt.get('original_name') or '')
    if zipfile.is_zipfile(shard['path']) or 'mrds' in name:
        return 'mrds'
    with open(shard['path'], 'rb') as stream:
        head = stream.read(400).decode('latin-1')
    if head.startswith('MCS chapter') or 'Statistics_detail' in head:
        return 'mcs_commodities'
    if 'State_Rank' in head:
        return 'mcs_state_value'
    if 'Critical_mineral' in head:
        return 'mcs_critical'
    raise ValueError('usgs_resources: unrecognized raw file ' + name)


def run_full(context):
    from worldmodel.raw_readers import iter_rows
    postal_to_fips = _state_fips()
    for index, _ in enumerate(context.raw_inputs):
        receipt = context.raw_receipt(index)
        observed = receipt['retrieved_at']
        emitted = set()  # commodities (~200), countries (~250), states: bounded

        def entity(key, typ, label, locator, **attrs):
            if key in emitted:
                return []
            emitted.add(key)
            return [{'kind': 'entity', 'id': 'usgsres:entity:' + key, 'entity_id': key, 'entity_type': typ, 'label': label,
                     'observed_at': observed, 'evidence': context.raw_evidence(locator, index), 'attributes': attrs}]

        for shard in context.raw_shards(index):
            kind = _kind(shard, receipt)
            if kind == 'mrds':
                config = {'format': 'csv', 'encoding': 'latin-1', 'members': ['mrds.csv', '*.csv'], 'strict': False}
                for locator, row in iter_rows([shard], config):
                    yield from _mrds(context, index, observed, locator, row, entity, postal_to_fips)
                continue
            config = {'format': 'csv', 'encoding': 'utf-8-sig' if kind != 'mcs_commodities' else 'cp1252', 'strict': False}
            for locator, row in iter_rows([shard], config):
                if kind == 'mcs_commodities':
                    yield from _mcs(context, index, observed, locator, row, entity)
                elif kind == 'mcs_state_value':
                    yield from _state_value(context, index, observed, locator, row, entity, postal_to_fips)
                else:
                    yield from _critical(context, index, observed, locator, row, entity)


def _mrds(context, index, observed, locator, row, entity, postal_to_fips):
    dep = (row.get('dep_id') or '').strip()
    if not dep:
        return
    evidence = context.raw_evidence(locator, index)
    subject = 'mrds:' + dep
    attrs = {k: (row.get(k) or '').strip() or None for k in ('mrds_id', 'mas_id', 'dev_stat', 'oper_type', 'dep_type', 'prod_size',
                                                             'work_type', 'model', 'ore', 'yr_fst_prd', 'yr_lst_prd', 'disc_yr', 'score', 'com_type')}
    attrs = {k: v for k, v in attrs.items() if v is not None}
    attrs.update(historical_inventory=True, country=row.get('country') or None, state=row.get('state') or None,
                 county=row.get('county') or None)
    yield {'kind': 'entity', 'id': 'usgsres:mrds:' + dep, 'entity_id': subject, 'entity_type': 'resource_deposit',
           'label': (row.get('site_name') or '').strip() or f'MRDS deposit {dep}', 'observed_at': observed, 'evidence': evidence,
           'attributes': attrs}
    commodities = [c.strip() for c in (row.get('commod1'), row.get('commod2'), row.get('commod3')) for c in (c or '').split(',') if c.strip()]
    for rank, name in enumerate(dict.fromkeys(commodities)):
        commodity = 'commodity:usgs:' + _slug(name)
        yield from entity(commodity, 'mineral', name, locator, naming='MRDS commodity name')
        yield {'kind': 'assertion', 'id': f'usgsres:mrds:{dep}:contains:{_slug(name)}', 'subject': subject,
               'predicate': 'contains_resource', 'object': commodity, 'observed_at': observed, 'evidence': evidence,
               'attributes': {'commodity_group': 'primary' if rank < len([c for c in (row.get('commod1') or '').split(',') if c.strip()]) else 'secondary_or_tertiary'}}
    country = (row.get('country') or '').strip()
    state = (row.get('state') or '').strip()
    place = None
    if country == 'United States' and NAMES_TO_POSTAL.get(state) in postal_to_fips:
        place = 'geo:US:state:' + postal_to_fips[NAMES_TO_POSTAL[state]]
        yield from entity(place, 'state', state, locator)
    elif country:
        place = 'mrds:country:' + _slug(country)
        yield from entity(place, 'country', country, locator, naming='MRDS country name (not ISO-resolved)')
    if place:
        yield {'kind': 'assertion', 'id': f'usgsres:mrds:{dep}:located_in', 'subject': subject, 'predicate': 'located_in',
               'object': place, 'observed_at': observed, 'evidence': evidence, 'attributes': {}}
    for field, metric in (('latitude', 'latitude'), ('longitude', 'longitude')):
        value, _ = _value(row.get(field))
        if value is not None:
            yield {'kind': 'observation', 'id': f'usgsres:mrds:{dep}:{metric}', 'subject': subject, 'metric': metric,
                   'value': value, 'unit': 'degrees', 'observed_at': observed, 'evidence': evidence,
                   'dimensions': {'point': 'site'}, 'attributes': {'valid_time_unknown': True}}
    status = (row.get('dev_stat') or '').strip()
    if status:
        yield {'kind': 'observation', 'id': f'usgsres:mrds:{dep}:development_status', 'subject': subject,
               'metric': 'development_status', 'value': status, 'unit': 'category', 'observed_at': observed, 'evidence': evidence,
               'dimensions': {}, 'attributes': {'valid_time_unknown': True, 'historical_inventory': True}}


def _mcs(context, index, observed, locator, row, entity):
    commodity_name = (row.get('Commodity') or '').strip()
    statistic = (row.get('Statistics_detail') or row.get('Statistics') or '').strip()
    period = _year_period(row.get('Year'))
    if not commodity_name or not statistic or period is None:
        return
    commodity = 'commodity:usgs:' + _slug(commodity_name)
    country_name = (row.get('Country') or '').strip() or 'World'
    yield from entity(commodity, 'commodity', commodity_name, locator, critical_mineral_2025=(row.get('Is critical mineral 2025') == 'Yes'))
    if country_name == 'United States':
        place = 'geo:US'
        yield from entity(place, 'country', 'United States', locator)
    elif country_name.lower().startswith('world'):
        place = 'mcs:world'
        yield from entity(place, 'location', 'World', locator)
    else:
        place = 'mcs:country:' + _slug(country_name)
        yield from entity(place, 'country', country_name, locator, naming='MCS country label (not ISO-resolved)')
    value, flags = _value(row.get('Value'))
    start, end, estimated = period
    kind = (row.get('Statistics') or '').strip()
    metric = 'mineral_' + _slug(kind if kind else statistic)
    dims = {'commodity': commodity, 'location': place, 'statistic': statistic, 'section': (row.get('Section') or '').strip() or None}
    record = {'kind': 'observation', 'id': f'usgsres:mcs:{digest_key(commodity, place, statistic, row.get("Year"), row.get("Unit"), locator)}',
              'subject': place, 'metric': metric, 'value': value, 'unit': (row.get('Unit') or '').strip() or 'unspecified',
              'valid_from': start, 'valid_to': end, 'observed_at': observed, 'evidence': context.raw_evidence(locator, index),
              'dimensions': {k: v for k, v in dims.items() if v}, 'attributes': {**flags, 'year_label': row.get('Year')}}
    if estimated:
        record['attributes']['estimated'] = True
    if value is None:
        record['missing_reason'] = 'withheld_or_not_available' if flags.get('source_marker') else 'source_missing'
    yield record


def digest_key(*parts):
    from worldmodel.util import digest
    return digest([str(p) for p in parts])[:24]


def _state_value(context, index, observed, locator, row, entity, postal_to_fips):
    state = (row.get('State') or '').strip()
    postal = NAMES_TO_POSTAL.get(state)
    if not postal or postal not in postal_to_fips:
        return
    subject = 'geo:US:state:' + postal_to_fips[postal]
    yield from entity(subject, 'state', state, locator)
    period = _year_period(row.get('Year'))
    for field, metric, unit in (('Value _millions_prelim_2025', 'nonfuel_mineral_production_value', 'million_USD'),
                                ('State_Rank_prelim_2025', 'nonfuel_mineral_production_rank', 'rank'),
                                ('State_percent_total_prelim_2025', 'nonfuel_mineral_production_share', 'percent')):
        value, flags = _value(row.get(field))
        if value is None:
            continue
        yield {'kind': 'observation', 'id': f'usgsres:mcs_t3:{postal}:{metric}', 'subject': subject, 'metric': metric,
               'value': value, 'unit': unit, 'valid_from': period[0], 'valid_to': period[1], 'observed_at': observed,
               'evidence': context.raw_evidence(locator, index), 'dimensions': {'preliminary': True},
               'attributes': {**flags, 'principal_commodities': row.get('Principal_commodities')}}


def _critical(context, index, observed, locator, row, entity):
    name = (row.get('Critical_mineral') or '').strip()
    if not name:
        return
    commodity = 'commodity:usgs:' + _slug(name)
    yield from entity(commodity, 'commodity', name, locator, critical_mineral=True)
    period = _year_period(row.get('Year'))
    unit = (row.get('Units') or '').strip() or 'unspecified'
    for field, metric, place, value_unit in (('Primary_prod', 'mineral_primary_production', 'geo:US', unit),
                                             ('Secondary_prod', 'mineral_secondary_production', 'geo:US', unit),
                                             ('Apparent_Consumption', 'mineral_apparent_consumption', 'geo:US', unit),
                                             ('Net_Import_Reliance', 'net_import_reliance', 'geo:US', 'percent'),
                                             ('World_total_prod', 'mineral_production', 'mcs:world', unit),
                                             ('Leading_source_precent_world', 'leading_country_world_share', 'mcs:world', 'percent')):
        value, flags = _value(row.get(field))
        yield from entity(place, 'country' if place == 'geo:US' else 'location', 'United States' if place == 'geo:US' else 'World', locator)
        record = {'kind': 'observation', 'id': f'usgsres:mcs_t7:{_slug(name)}:{metric}', 'subject': place, 'metric': metric,
                  'value': value, 'unit': value_unit, 'valid_from': period[0], 'valid_to': period[1], 'observed_at': observed,
                  'evidence': context.raw_evidence(locator, index), 'dimensions': {'commodity': commodity, 'estimated': True},
                  'attributes': {**flags, 'leading_source_country': row.get('Leading_source_country') or None}}
        if value is None:
            record['missing_reason'] = 'withheld_or_not_available'
        yield record
