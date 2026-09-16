"""Full-acquisition normalization for eia_energy (bulk series ZIPs + EIA-860 workbooks).

Imported lazily by pipeline.py only for sharded full artifacts, so the sample adapter
keeps working when pipeline.py is loaded without a package.

Bulk series (ELEC, PET, NG, SEDS, INTL, TOTAL, STEO, COAL): every series line yields one
``eia:series:<series_id>`` economic_series entity and one observation per data point.
* metric: ``eia_<dataset>_<family>``. The family is the geography-free ``geoset_id`` when EIA
  publishes one (SEDS/INTL/COAL), otherwise the series code without frequency and, for
  ELEC/COAL, without the dash-joined key. The series entity carries the human name.
* subject: ``eia:plant:<code>`` for plant series, ``iso3:USA``/``iso3:XXX`` for national and
  international series, ``geo:US:state:<fips>`` for single-state series; other multi-area
  series (PADDs, regions, ...) use the series entity itself.
* dimensions: frequency, series_id, plus fuel/prime_mover/series_key where parsed; STEO
  points after ``lastHistoricalPeriod`` are marked ``estimate_type: forecast``.
Frequencies in ``skip_frequencies`` (parameter; default quarterly and 4-week averages, both
derivable from monthly/weekly series) are not emitted.

EIA-860 (plants, generators, owners): facilities with capacity observations and
utility/owner relationships. EIA-923 workbooks and the manifest are retained raw only
(plant-level generation and fuel use are covered by ELEC.PLANT series).
"""
from datetime import date, timedelta
import fnmatch
import io
import itertools
import json
import re
import zipfile

from worldmodel.raw_readers import iter_rows
from worldmodel.source_helpers import STATE_FIPS
from worldmodel.workbooks import xlsx_rows

from .evidence import Evidence, num

FREQUENCIES = {'A': 'annual', 'Q': 'quarterly', 'M': 'monthly', 'W': 'weekly', 'D': 'daily', '4': 'four_week_average',
               'H': 'hourly'}
DEFAULT_SKIP = ['Q', '4']


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def period_bounds(period, frequency):
    """Half-open [from, to) for an EIA bulk period string."""
    if frequency == 'A' and re.fullmatch(r'\d{4}', period):
        return f'{period}-01-01', f'{int(period) + 1:04d}-01-01'
    if frequency == 'Q':
        match = re.fullmatch(r'(\d{4})Q([1-4])', period)
        if match:
            year, quarter = int(match[1]), int(match[2])
            start = date(year, 3 * quarter - 2, 1)
            end = date(year + (quarter == 4), 1 if quarter == 4 else 3 * quarter + 1, 1)
            return start.isoformat(), end.isoformat()
    if frequency == 'M' and re.fullmatch(r'\d{6}', period):
        year, month = int(period[:4]), int(period[4:])
        return date(year, month, 1).isoformat(), date(year + (month == 12), 1 if month == 12 else month + 1, 1).isoformat()
    if frequency in ('D', 'W', '4') and re.fullmatch(r'\d{8}', period):
        day = date(int(period[:4]), int(period[4:6]), int(period[6:]))
        span = {'D': 1, 'W': 7, '4': 28}[frequency]  # weekly and 4-week values are period-ending
        return (day - timedelta(days=span - 1)).isoformat(), (day + timedelta(days=1)).isoformat()
    raise ValueError(f'Unsupported EIA period {period!r} for frequency {frequency!r}')


def value_of(raw):
    try:
        return num(raw), None
    except ValueError:
        return None, 'source_code:' + str(raw).strip()[:20]


# INTL uses a few non-ISO or reused codes; keep them out of the shared iso3: namespace.
INTL_REGIONS = {'WLD': 'World', 'WAK': 'Wake Island'}
INTL_HISTORICAL = {'CSK': 'Former Czechoslovakia', 'DDR': 'Former East Germany', 'SUN': 'Former U.S.S.R.',
                   'SCG': 'Former Serbia and Montenegro', 'YUG': 'Former Yugoslavia'}
INTL_ISO_ALIASES = {'XKS': 'XKX'}  # Kosovo: other datasets use XKX
# Aggregates published under a member country's code, recognised by the series name's area segment.
INTL_NAMED_AGGREGATES = ('OPEC - South America', 'South Korea and other OECD Asia')


def intl_area_name(line):
    parts = (line.get('name') or '').split(', ')
    return parts[-2] if len(parts) >= 3 else ''


def geography_subject(line):
    code = line.get('iso3166') or ''
    geography = line.get('geography') or ''
    if code == 'USA' or geography == 'USA':
        return 'iso3:USA', 'country', 'United States'
    match = re.fullmatch(r'USA-([A-Z]{2})', code or geography)
    if match and match[1] in STATE_FIPS:
        return f'geo:US:state:{STATE_FIPS[match[1]]}', 'state', 'US state ' + match[1]
    if line['series_id'].startswith('INTL.') and re.fullmatch(r'[A-Z]{3}', geography):
        area = intl_area_name(line)
        if area in INTL_NAMED_AGGREGATES:
            return 'eia:region:' + slug(area), 'location', area, {'aggregate': True, 'eia_geography': geography}
        if geography in INTL_REGIONS:
            return 'eia:region:' + geography, 'location', INTL_REGIONS[geography], {'aggregate': True, 'eia_geography': geography}
        if geography in INTL_HISTORICAL:
            return ('eia:historical_country:' + geography, 'country', INTL_HISTORICAL[geography],
                    {'historical': True, 'eia_geography': geography})
        iso = INTL_ISO_ALIASES.get(geography, geography)
        return 'iso3:' + iso, 'country', area or iso, {'eia_geography': geography} if iso != geography else {}
    return None


def geo_entity(out, locator, geo):
    key, entity_type, label = geo[:3]
    return out.entity(key, entity_type, label, locator, **(geo[3] if len(geo) > 3 else {}))


def family(line):
    dataset, *middle, _ = line['series_id'].split('.')
    geoset = line.get('geoset_id')
    key = None
    if geoset:
        middle = geoset.split('.')[1:-1]
    elif dataset in ('ELEC', 'COAL') and middle and '-' in middle[-1]:
        key = middle[-1]
        middle = middle[:-1]
    return dataset, 'eia_' + slug(dataset + '_' + '_'.join(middle)), key


DEFAULT_PLANT_FAMILIES = ['GEN', 'AVG_HEAT']  # fuel input ~= generation x heat rate; CONS_* families stay raw
DEFAULT_SUBANNUAL_SINCE = 2022  # keeps normalized gzip <= 2x raw; annual and PRIORITY_SERIES keep full history
# Series required by the estimation layer (worldmodel/estimation/requirements.json): tidy metric names,
# full history regardless of subannual_since, series_id and release timestamps in attributes.
PRIORITY_SERIES = {
    'PET.WCESTUS1.W': 'crude_oil_commercial_stocks_excl_spr',
    'PET.WCRFPUS2.W': 'crude_oil_field_production',
    'PET.WCRIMUS2.W': 'crude_oil_imports',
    'PET.WCREXUS2.W': 'crude_oil_exports',
    'PET.WCRRIUS2.W': 'refiner_net_input_crude_oil',
    'PET.WGTSTUS1.W': 'motor_gasoline_total_stocks',
    'PET.MGFUPUS2.M': 'motor_gasoline_product_supplied',
    'PET.EMM_EPMR_PTE_NUS_DPG.W': 'gasoline_retail_price_regular',
}


def series_records(out, locator, line, skip, plant_frequencies=('A',), plant_families=tuple(DEFAULT_PLANT_FAMILIES),
                   subannual_since=None, bulk_updated=None):
    series_id = line.get('series_id')
    if not series_id:
        return  # category tree line
    frequency = line.get('f') or series_id.rsplit('.', 1)[-1]
    if frequency in skip:
        return
    if frequency not in FREQUENCIES:
        raise ValueError(f'{locator}: unknown EIA frequency {frequency!r}')
    dataset, metric, key = family(line)
    priority = PRIORITY_SERIES.get(series_id)
    if priority:
        metric = priority
    bulk_last_updated = (bulk_updated or {}).get(dataset)
    unit = line.get('units') or line.get('unitsshort') or 'unknown'
    series_key = 'eia:series:' + series_id
    record = out.entity(series_key, 'economic_series', line.get('name') or series_id, locator, series_id=series_id,
                        units=unit, frequency=FREQUENCIES[frequency], geography=line.get('geography'),
                        geoset_id=line.get('geoset_id'), source=line.get('source'), last_updated=line.get('last_updated'),
                        bulk_last_updated=bulk_last_updated, metric=metric)
    if record:
        yield record
    dims = {'frequency': FREQUENCIES[frequency], 'series_id': series_id}
    subject = series_key
    plant = re.fullmatch(r'ELEC\.PLANT\.([A-Z_]+)\.(\d+)-([A-Z0-9]+)-([A-Z0-9]+)\.[A-Z0-9]', series_id)
    if plant and (frequency not in plant_frequencies or plant[1] not in plant_families):
        return  # plant-level monthly series dominate ELEC (~50M points); annual kept by default
    if plant:
        subject = 'eia:plant:' + plant[2]
        dims.update(fuel=plant[3].lower(), prime_mover=plant[4].lower())
        parts = (line.get('name') or '').split(' : ')
        label = parts[1] if len(parts) > 2 else subject
        record = out.entity(subject, 'facility', label, locator, eia_plant_code=plant[2], latitude=num(line.get('lat')),
                            longitude=num(line.get('lon')), geography=line.get('geography'))
        if record:
            yield record
            geo = geography_subject(line)
            if geo:
                entity = geo_entity(out, locator, geo)
                if entity:
                    yield entity
                yield out.relation(subject, 'located_in', geo[0], locator)
    else:
        if key:
            dims['series_key'] = key
        geo = geography_subject(line)
        if geo:
            subject = geo[0]
            entity = geo_entity(out, locator, geo)
            if entity:
                yield entity
    last_history = line.get('lastHistoricalPeriod')
    periods = {}
    for period, raw in line.get('data') or []:
        period = str(period)
        if period in periods:  # ~36 PET/NG series repeat a period (identical values in the 2026-09 vintage)
            if periods[period] != raw:
                raise ValueError(f'{locator}: conflicting duplicate period {period} in {series_id}')
            continue
        periods[period] = raw
        if not priority and subannual_since and frequency != 'A' and int(period[:4]) < subannual_since:
            continue  # sub-annual history before the cutoff stays in the raw artifact
        start, end = period_bounds(period, frequency)
        value, reason = value_of(raw)
        point_dims = dims
        if last_history:
            point_dims = {**dims, 'estimate_type': 'forecast' if (len(period), period) > (len(last_history), last_history) else 'history'}
        extra = {}
        if priority:
            extra = {'series_id': series_id, 'series_last_updated': line.get('last_updated'),
                     'bulk_file_last_updated': bulk_last_updated, 'period': period,
                     'period_convention': 'week ending on period date' if frequency == 'W' else 'calendar period'}
        yield out.observation(subject, metric, value, unit, locator, valid_from=start, valid_to=end, dimensions=point_dims,
                              missing_reason=reason or 'source_blank', **extra)


def nested_rows(shard, member_pattern, sheet_name, header_row=2):
    """Rows of one sheet of an xlsx nested inside the shard ZIP (stdlib only)."""
    with zipfile.ZipFile(shard['path']) as outer:
        names = [n for n in outer.namelist() if fnmatch.fnmatch(n.rsplit('/', 1)[-1], member_pattern)]
        if len(names) != 1:
            raise ValueError(f'Expected one {member_pattern} workbook, found {names}')
        with zipfile.ZipFile(io.BytesIO(outer.read(names[0]))) as inner:
            workbook = inner.read('xl/workbook.xml').decode('utf-8')
            rels = inner.read('xl/_rels/workbook.xml.rels').decode('utf-8')
            sheets = dict(re.findall(r'<sheet [^>]*?name="([^"]+)"[^>]*?r:id="([^"]+)"', workbook))
            targets = {rid: target for rid, target in re.findall(r'Id="([^"]+)"[^>]*?Target="([^"]+)"', rels)}
            targets.update({rid: target for target, rid in re.findall(r'Target="([^"]+)"[^>]*?Id="([^"]+)"', rels)})
            target = targets[sheets[sheet_name]].lstrip('/')
            member = target if target.startswith('xl/') else 'xl/' + target
            rows = xlsx_rows(inner, {'max_uncompressed_bytes': 2 << 30, 'header_row': header_row, 'archive_member': member})
            try:
                first = next(rows)
            except StopIteration:
                return
            except ValueError as error:
                if 'row range' in str(error):  # header-only sheet
                    return
                raise
            for row in itertools.chain([first], rows):
                source = row.pop('_source')
                yield f'shard:{shard["index"]}/member:{names[0]}/sheet:{sheet_name}/row:{source["row"]}', row


def text(row, name):
    value = row.get(name)
    value = value.strip() if isinstance(value, str) else value
    return value or None


def state_entity(out, locator, state):
    if state in STATE_FIPS:
        key = f'geo:US:state:{STATE_FIPS[state]}'
        return key, out.entity(key, 'state', 'US state ' + state, locator)
    return None, None


def eia860_records(out, shard, year):
    valid_from = f'{year}-01-01'
    valid_to = f'{year + 1}-01-01'
    for locator, row in nested_rows(shard, '2___Plant_Y*.xlsx', 'Plant'):
        plant = text(row, 'Plant Code')
        if not plant:
            continue
        key = 'eia:plant:' + plant
        record = out.entity(key, 'facility', text(row, 'Plant Name') or key, locator, eia_plant_code=plant)
        attrs = {'state': text(row, 'State'), 'county': text(row, 'County'), 'city': text(row, 'City'),
                 'latitude': num(text(row, 'Latitude')), 'longitude': num(text(row, 'Longitude')),
                 'nerc_region': text(row, 'NERC Region'), 'balancing_authority': text(row, 'Balancing Authority Code'),
                 'sector': text(row, 'Sector Name'), 'primary_purpose_naics': text(row, 'Primary Purpose (NAICS Code)'),
                 'eia860_year': year}
        if record is None:  # already created from ELEC plant series; publish 860 attributes as a claim
            yield out.claim(key, 'eia860_plant_attributes', attrs, locator, identity=year)
        else:
            record['attributes'].update(attrs)
            yield record
        state, entity = state_entity(out, locator, text(row, 'State'))
        if entity:
            yield entity
        if state:
            yield out.relation(key, 'located_in', state, locator)
        ba = text(row, 'Balancing Authority Code')
        if ba:
            ba_key = 'eia:ba:' + ba
            entity = out.entity(ba_key, 'organization', text(row, 'Balancing Authority Name') or ba, locator, eia930_code=ba)
            if entity:
                yield entity
            yield out.relation(key, 'within_balancing_authority', ba_key, locator)
        utility = text(row, 'Utility ID')
        if utility:
            utility_key = 'eia:utility:' + utility
            entity = out.entity(utility_key, 'business', text(row, 'Utility Name') or utility_key, locator, eia_utility_id=utility)
            if entity:
                yield entity
            yield out.relation(utility_key, 'operates', key, locator)
    for sheet, status_group in (('Operable', 'operable'), ('Proposed', 'proposed'), ('Retired and Canceled', 'retired_or_canceled')):
        for locator, row in nested_rows(shard, '3_1_Generator_Y*.xlsx', sheet):
            plant, generator = text(row, 'Plant Code'), text(row, 'Generator ID')
            if not plant or not generator:
                continue
            plant_key = 'eia:plant:' + plant
            key = f'eia:generator:{plant}:{generator}'
            entity = out.entity(plant_key, 'facility', text(row, 'Plant Name') or plant_key, locator, eia_plant_code=plant)
            if entity:
                yield entity
            record = out.entity(key, 'facility', f'{text(row, "Plant Name") or plant} generator {generator}', locator,
                                eia_plant_code=plant, generator_id=generator, technology=text(row, 'Technology'),
                                prime_mover=text(row, 'Prime Mover'), status=text(row, 'Status'), status_group=status_group,
                                energy_sources=[s for s in (text(row, f'Energy Source {i}') for i in range(1, 7)) if s],
                                operating_year=num(text(row, 'Operating Year')), operating_month=num(text(row, 'Operating Month')),
                                planned_retirement_year=num(text(row, 'Planned Retirement Year')),
                                retirement_year=num(text(row, 'Retirement Year')), eia860_year=year)
            if record is None:
                continue  # generator already emitted from another sheet
            yield record
            yield out.relation(key, 'located_in', plant_key, locator)
            dims = {'frequency': 'annual_inventory', 'status_group': status_group, 'technology': slug(text(row, 'Technology') or 'unknown'),
                    'energy_source': (text(row, 'Energy Source 1') or 'unknown').lower()}
            for column, metric in (('Nameplate Capacity (MW)', 'nameplate_capacity'), ('Summer Capacity (MW)', 'net_summer_capacity'),
                                   ('Winter Capacity (MW)', 'net_winter_capacity')):
                value = num(text(row, column))
                if value is not None:
                    yield out.observation(key, metric, value, 'MW', locator, valid_from=valid_from, valid_to=valid_to, dimensions=dims)
    for locator, row in nested_rows(shard, '4___Owner_Y*.xlsx', 'Ownership'):
        plant, generator, owner = text(row, 'Plant Code'), text(row, 'Generator ID'), text(row, 'Ownership ID')
        if not plant or not generator or not owner:
            continue
        owner_key = 'eia:utility:' + owner
        entity = out.entity(owner_key, 'business', text(row, 'Owner Name') or owner_key, locator, eia_utility_id=owner)
        if entity:
            yield entity
        generator_key = f'eia:generator:{plant}:{generator}'
        entity = out.entity(generator_key, 'facility', f'{text(row, "Plant Name") or plant} generator {generator}', locator,
                            eia_plant_code=plant, generator_id=generator)
        if entity:
            yield entity
        fraction = num(text(row, 'Percent Owned'))
        relation = out.relation(owner_key, 'owns', generator_key, locator, identity=[year, locator], once=False,
                                ownership_fraction=fraction, eia860_year=year, generator_status=text(row, 'Status'))
        relation['valid_from'], relation['valid_to'] = valid_from, valid_to
        yield relation


def run_full(context):
    # relation()/entity() return None for already-emitted keys; drop those here.
    return (record for record in _run_full(context) if record is not None)


def _run_full(context):
    skip = set(context.parameters.get('skip_frequencies', DEFAULT_SKIP))
    plant_frequencies = set(context.parameters.get('plant_frequencies', ['A']))
    plant_families = set(context.parameters.get('plant_families', DEFAULT_PLANT_FAMILIES))
    subannual_since = context.parameters.get('subannual_since', DEFAULT_SUBANNUAL_SINCE)
    out = Evidence(context, 'eia')
    bulk_updated = {}
    for shard in context.raw_shards():  # manifest.txt: per-bulk-file last_updated vintages
        with open(shard['path'], 'rb') as stream:
            if stream.read(4) == b'PK\x03\x04':
                continue
        with open(shard['path'], encoding='utf-8-sig') as stream:
            try:
                manifest = json.load(stream)
            except ValueError:
                continue
        for code, entry in (manifest.get('dataset') or {}).items():
            if isinstance(entry, dict) and entry.get('last_updated'):
                bulk_updated[code] = entry['last_updated']
    try:
        for shard in context.raw_shards():
            with open(shard['path'], 'rb') as stream:
                is_zip = stream.read(4) == b'PK\x03\x04'
            if not is_zip:
                continue  # manifest.txt (read above)
            with zipfile.ZipFile(shard['path']) as archive:
                names = archive.namelist()
            if any(re.search(r'3_1_Generator_Y(\d{4})\.xlsx$', n) for n in names):
                year = int(next(re.search(r'Y(\d{4})\.xlsx$', n)[1] for n in names if '3_1_Generator' in n))
                yield from eia860_records(out, shard, year)
            elif any(n.startswith('EIA923_') for n in names):
                continue  # retained raw; see module docstring
            elif len(names) == 1 and names[0].endswith('.txt'):
                for locator, line in iter_rows([shard], {'format': 'jsonl', 'members': names}):
                    yield from series_records(out, locator, line, skip, plant_frequencies, plant_families, subannual_since,
                                              bulk_updated)
            else:
                raise ValueError(f'Unrecognised eia_energy shard {shard["index"]}: {names[:5]}')
    finally:
        out.close()
