"""World Bank WDI bulk ZIP -> evidence records.

WDICountry.csv gives economies (ISO3 codes) and aggregates (blank Region);
WDISeries.csv gives indicator names/units/licence; WDICSV.csv is wide
(one row per economy x indicator, one column per year). Each non-empty year cell
becomes an annual observation with metric ``wdi_<indicator code>`` and a unit
derived from the indicator name's trailing parenthetical.
"""
import csv
import io
import re
import zipfile

DATASET = 'wdi'
UNITS = {'current us$': 'USD', 'current lcu': 'national_currency', 'constant lcu': 'national_currency_constant_prices',
         '% of gdp': 'percent_of_gdp', 'annual %': 'percent_annual_growth', '%': 'percent',
         'current international $': 'international_dollar_ppp_current', 'people': 'persons', 'number': 'count',
         'years': 'years', 'per 1,000 people': 'per_1000_persons', 'per 100 people': 'per_100_persons',
         'kt': 'kilotonne', 'sq. km': 'square_kilometre', 'hectares': 'hectare', 'metric tons per capita': 'tonne_per_capita',
         'kwh per capita': 'kilowatt_hour_per_capita', 'per 1,000 live births': 'per_1000_live_births',
         'per 100,000 people': 'per_100000_persons', 'days': 'days', 'current us$ millions': 'USD'}
SCALED = {'current us$ millions': 1e6}


def unit_from_name(name):
    matches = [m.strip().lower() for m in re.findall(r'\(([^()]*)\)', name or '')]
    if not matches:
        return 'as_published', 1
    unitlike = [m for m in matches if m in UNITS or re.search(r'[%$]|\bper\b|\blcu\b|= ?100|^constant ', m)]
    text = (unitlike or matches)[-1]
    if text in UNITS:
        return UNITS[text], SCALED.get(text, 1)
    m = re.fullmatch(r'constant (\d{4}) us\$', text)
    if m:
        return f'USD_constant_{m[1]}', 1
    m = re.fullmatch(r'constant (\d{4}) international \$', text)
    if m:
        return f'international_dollar_ppp_constant_{m[1]}', 1
    m = re.fullmatch(r'(\d{4}) ?= ?100', text)
    if m:
        return f'index_{m[1]}_100', 1
    if text.startswith('% of '):
        return 'percent_of_' + re.sub(r'[^a-z0-9]+', '_', text[5:]).strip('_'), 1
    return re.sub(r'[^a-z0-9$%]+', '_', text).replace('%', 'percent').replace('$', 'dollar').strip('_') or 'as_published', 1


def _reader(archive, member):
    stream = archive.open(member)
    return stream, csv.reader(io.TextIOWrapper(stream, encoding='utf-8-sig', newline=''))


def _member(archive, basename):
    for name in archive.namelist():
        if name.rsplit('/', 1)[-1].lower() == basename.lower():
            return name
    raise ValueError(f'worldbank_wdi: ZIP lacks {basename}')


def _table(archive, member):
    stream, reader = _reader(archive, member)
    with stream:
        header = next(reader)
        for number, row in enumerate(reader, 2):
            if row:
                yield number, dict(zip(header, row))


def run(context):
    coverage = context.raw_coverage() if hasattr(context, 'raw_coverage') else None
    if not coverage or coverage['sampled'] or coverage['layout'] != 'shards':
        raise ValueError('worldbank_wdi: requires the full sharded acquisition (wm acquire worldbank_wdi --allow-network)')
    for shard in context.raw_shards():
        observed_at = shard.get('retrieved_at') or context.raw_receipt()['retrieved_at']
        prefix = f'shard:{shard["index"]}'
        with zipfile.ZipFile(shard['path']) as archive:
            country_member, series_member, data_member = (_member(archive, n) for n in ('WDICountry.csv', 'WDISeries.csv', 'WDICSV.csv'))
            economies = {}
            groups = {}
            for number, row in _table(archive, country_member):
                code = row['Country Code'].strip()
                aggregate = not row.get('Region', '').strip()
                entity_id = ('agg:wdi:' if aggregate else 'iso3:') + code
                economies[code] = entity_id
                name = row.get('Table Name') or row.get('Short Name') or code
                evidence = context.raw_evidence(f'{prefix}/member:{country_member}/line:{number}')
                attributes = {'source_dataset': 'worldbank_wdi', 'wdi_code': code, 'aggregate': aggregate}
                for field, key in (('2-alpha code', 'iso2_or_wb2'), ('Region', 'region'), ('Income Group', 'income_group'),
                                   ('Lending category', 'lending_category'), ('Currency Unit', 'currency_unit')):
                    if row.get(field, '').strip():
                        attributes[key] = row[field].strip()
                yield {'kind': 'entity', 'id': 'wdi:entity:' + entity_id, 'entity_id': entity_id,
                       'entity_type': 'aggregate_cohort' if aggregate else 'country', 'label': name.strip(),
                       'observed_at': observed_at, 'evidence': evidence, 'attributes': attributes}
                if not aggregate:
                    for field in ('Region', 'Income Group'):
                        if row.get(field, '').strip():
                            groups.setdefault(row[field].strip(), []).append((entity_id, evidence))
            label_to_agg = {}
            for number, row in _table(archive, country_member):
                if not row.get('Region', '').strip():
                    label_to_agg[(row.get('Table Name') or '').strip()] = economies[row['Country Code'].strip()]
            for group, members in groups.items():
                target = label_to_agg.get(group)
                if not target:
                    continue
                for entity_id, evidence in members:
                    yield {'kind': 'assertion', 'id': f'wdi:member_of:{entity_id}:{target}', 'subject': entity_id,
                           'predicate': 'member_of', 'object': target, 'observed_at': observed_at, 'evidence': evidence,
                           'attributes': {'source_dataset': 'worldbank_wdi', 'grouping': group}}
            indicators = {}
            for number, row in _table(archive, series_member):
                code = row['Series Code'].strip()
                indicators[code] = row
                evidence = context.raw_evidence(f'{prefix}/member:{series_member}/line:{number}')
                subject = 'wdi:indicator:' + code
                unit, scale = unit_from_name(row.get('Indicator Name', ''))
                for predicate, value in (('label', row.get('Indicator Name', '')), ('topic', row.get('Topic', '')),
                                         ('unit_of_measure', row.get('Unit of measure', '')), ('normalized_unit', unit),
                                         ('periodicity', row.get('Periodicity', '')), ('aggregation_method', row.get('Aggregation method', '')),
                                         ('license_type', row.get('License Type', '')), ('source', row.get('Source', ''))):
                    if value and value.strip():
                        yield {'kind': 'assertion', 'id': f'wdi:indicator:{code}:{predicate}', 'subject': subject,
                               'predicate': predicate, 'value': value.strip()[:2000], 'observed_at': observed_at,
                               'evidence': evidence, 'attributes': {'source_dataset': 'worldbank_wdi'}}
            units = {}
            stream, reader = _reader(archive, data_member)
            with stream:
                header = next(reader)
                years = [(i, h.strip()) for i, h in enumerate(header) if re.fullmatch(r'\d{4}', h.strip())]
                col = {h: i for i, h in enumerate(header)}
                last = reader.line_num
                for row in reader:
                    start, last = last + 1, reader.line_num
                    if not row:
                        continue
                    country, indicator = row[col['Country Code']].strip(), row[col['Indicator Code']].strip()
                    subject = economies.get(country)
                    if subject is None:
                        # Rows such as INX ("Not classified") are absent from WDICountry.csv.
                        subject = economies[country] = 'agg:wdi:' + country
                        yield {'kind': 'entity', 'id': 'wdi:entity:' + subject, 'entity_id': subject,
                               'entity_type': 'aggregate_cohort', 'label': row[col['Country Name']].strip() or country,
                               'observed_at': observed_at,
                               'evidence': context.raw_evidence(f'{prefix}/member:{data_member}/line:{start}'),
                               'attributes': {'source_dataset': 'worldbank_wdi', 'wdi_code': country, 'aggregate': True,
                                              'unlisted_in_country_table': True}}
                    if indicator not in units:
                        name = indicators.get(indicator, {}).get('Indicator Name') or row[col['Indicator Name']]
                        units[indicator] = unit_from_name(name)
                    unit, scale = units[indicator]
                    evidence = None
                    metric = 'wdi_' + indicator.lower().replace('.', '_')
                    for index, year in years:
                        if index >= len(row):
                            break
                        cell = row[index].strip()
                        if not cell:
                            continue
                        if evidence is None:
                            evidence = context.raw_evidence(f'{prefix}/member:{data_member}/line:{start}')
                        y = int(year)
                        yield {'kind': 'observation', 'id': f'wdi:{country}:{indicator}:{year}', 'observed_at': observed_at,
                               'subject': subject, 'metric': metric, 'value': float(cell) * scale, 'unit': unit,
                               'valid_from': f'{y:04d}-01-01', 'valid_to': f'{y + 1:04d}-01-01',
                               'dimensions': {'frequency': 'A', 'indicator': indicator}, 'evidence': evidence,
                               'attributes': {'source_flow': 'WDI'}}
