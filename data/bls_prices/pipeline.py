"""BLS price statistics (CPI, PPI commodity/industry, average prices, import/export prices)
and the World Bank Pink Sheet monthly commodity prices.

The full ``files`` acquisition stores one shard per source file. Each family (cu, wp, pc,
ap, ei) is processed "Current" file first; history supplement files (e.g. cu.data.1.AllItems)
only contribute periods *before* the first year the Current file publishes for that series,
so no (series, year, period) is emitted twice. Memory is bounded by the series count.
"""
import re
import zipfile
from worldmodel.util import digest
try:
    from .helpers import base_unit, footnotes, mapping, number, period_bounds, shard_name, sheet_members, tab_rows, xlsx_stream
except ImportError:  # standalone module loading
    import importlib.util as _util
    from pathlib import Path as _Path
    _spec = _util.spec_from_file_location('_bls_prices_helpers', _Path(__file__).with_name('helpers.py'))
    _h = _util.module_from_spec(_spec)
    _spec.loader.exec_module(_h)
    base_unit, footnotes, mapping, number, period_bounds, shard_name, sheet_members, tab_rows, xlsx_stream = (
        _h.base_unit, _h.footnotes, _h.mapping, _h.number, _h.period_bounds, _h.shard_name, _h.sheet_members,
        _h.tab_rows, _h.xlsx_stream)

DATASET = 'bls_prices'
FAMILIES = ('cu', 'wp', 'pc', 'ap', 'ei')
EI_METRICS = {'CO': 'import_price_index', 'IP': 'import_price_index', 'IR': 'import_price_index', 'IZ': 'import_price_index',
              'IV': 'import_price_index', 'IS': 'import_price_index', 'CD': 'export_price_index', 'ID': 'export_price_index',
              'IQ': 'export_price_index', 'IY': 'export_price_index', 'IH': 'export_price_index', 'IC': 'export_price_index',
              'CT': 'terms_of_trade_index'}
PINK_UNITS = {'$/bbl': 'USD_per_barrel', '$/mt': 'USD_per_metric_ton', '$/kg': 'USD_per_kg', '$/mmbtu': 'USD_per_mmbtu',
              '$/dmtu': 'USD_per_dry_metric_ton_unit', '$/troy oz': 'USD_per_troy_ounce', '$/cubic meter': 'USD_per_cubic_meter',
              'cents/sheet': 'USD_cents_per_sheet'}


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', (text or '').lower()).strip('_')


def price_unit(item_name):
    """Quantity basis of a BLS average-price item, e.g. 'per lb. (453.6 gm)' -> USD_per_lb."""
    name = item_name or ''
    position = name.lower().rfind('per ')
    quantity = name[position + 4:] if position >= 0 else name.rsplit(',', 1)[-1]
    quantity = quantity.split('(')[0].strip().lower().rstrip('.').strip()
    if quantity.startswith('1/2 gal'):
        return 'USD_per_half_gallon'
    quantity = quantity.split('/')[0].strip().rstrip('.').strip()
    if re.fullmatch(r'pound|lb', quantity):
        return 'USD_per_lb'
    if re.fullmatch(r'(1 )?gal(lon)?', quantity):
        return 'USD_per_gallon'
    found = re.fullmatch(r'(\d+(?:\.\d+)?) (?:ounces?|oz)', quantity)
    if found:
        return f'USD_per_{found[1]}_oz'
    if quantity.startswith('doz'):
        return 'USD_per_dozen'
    if quantity == 'kwh':
        return 'USD_per_kWh'
    return 'USD_per_' + (slug(quantity) or 'item_unit')


def run(context):
    for index, _ in enumerate(context.raw_inputs):
        coverage = context.raw_coverage(index)
        if coverage['sampled'] or coverage['layout'] != 'shards':
            raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire bls_prices)')
        yield from Prices(context, index).run()


class Prices:
    def __init__(self, context, index):
        self.context, self.index = context, index
        self.shards = {shard_name(s): s for s in context.raw_shards(index)}

    def evidence(self, locator):
        return self.context.raw_evidence(locator, self.index)

    def entity(self, shard, key, typ, label, locator, seen, **attrs):
        if key not in seen:
            seen.add(key)
            self.pending.append({'kind': 'entity', 'id': f'{DATASET}:entity:' + digest([shard['index'], key]), 'entity_id': key,
                                 'entity_type': typ, 'label': label or key, 'observed_at': shard['retrieved_at'],
                                 'evidence': self.evidence(locator), 'attributes': {'source_dataset': DATASET, **attrs}})
        return key

    def mapping(self, name, key, value=None):
        shard = self.shards.get(name)
        if shard is None:
            raise ValueError(f'{DATASET}: mapping file {name} missing from acquisition')
        return mapping(shard['path'], key, value)

    def run(self):
        self.pending = []
        for family in FAMILIES:
            data = sorted((n for n in self.shards if n.startswith(family + '.data.')),
                          key=lambda n: (0 if '.data.0.' in n else 1, n))
            if not data:
                continue
            decode = getattr(self, 'decode_' + family)()
            covered = {}
            for name in data:
                first = {}
                yield from self.series_file(self.shards[name], name, decode, covered, first)
                for series, year in first.items():
                    covered[series] = min(year, covered.get(series, year))
        pink = [n for n in self.shards if n.lower().endswith('.xlsx')]
        for name in pink:
            yield from self.pink_sheet(self.shards[name], name)

    def series_file(self, shard, name, decode, covered, first):
        seen, cache = set(), {}
        for line, row in tab_rows(shard['path']):
            series, year = row['series_id'], int(row['year'])
            if series in covered and year >= covered[series]:
                continue  # already emitted from an earlier (Current) file
            if year < first.get(series, 10 ** 6):
                first[series] = year
            bounds = period_bounds(year, row['period'])
            if bounds is None:
                continue
            locator = f'shard:{shard["index"]}/line:{line}'
            if series not in cache:
                cache[series] = decode(shard, series, locator, seen)
            decoded = cache[series]
            if decoded is None:
                continue
            subject, metric, unit, dims = decoded
            notes = footnotes(row.get('footnote_codes'))
            attrs = {'source_file': name, 'series_id': series, 'source_series': series, 'vintage': 'current_at_retrieval',
                     'realtime_start': shard['retrieved_at'][:10], 'realtime_end': None}
            if notes:
                attrs['footnote_codes'] = notes
            if 'P' in notes:
                attrs['preliminary'] = True
            value = number(row['value'])
            record = {'kind': 'observation', 'id': f'{DATASET}:obs:' + digest([series, year, row['period']]),
                      'observed_at': shard['retrieved_at'], 'subject': subject, 'metric': metric, 'value': value, 'unit': unit,
                      'valid_from': bounds[0], 'valid_to': bounds[1],
                      'dimensions': {**dims, 'frequency': bounds[2], 'period_type': bounds[3]},
                      'evidence': self.evidence(locator), 'attributes': attrs}
            if value is None:
                record['missing_reason'] = 'source_missing_or_unavailable'
            if self.pending:
                yield from self.pending
                self.pending = []
            yield record
        yield from self.pending
        self.pending = []

    @staticmethod
    def seasonal(code):
        return 'SA' if code == 'S' else 'NSA'

    def area(self, shard, code, names, locator, seen, prefix):
        if code == '0000':
            return self.entity(shard, 'geo:US', 'country', 'United States', locator, seen)
        return self.entity(shard, f'bls:{prefix}_area:{code}', 'jurisdiction', names.get(code), locator, seen,
                           bls_area_code=code)

    # ----- family decoders: return fn(shard, series, locator, seen) -> (subject, metric, unit, dims) | None
    def decode_cu(self):
        series_map = self.mapping('cu.series', 'series_id')
        items = self.mapping('cu.item', 'item_code', 'item_name')
        areas = self.mapping('cu.area', 'area_code', 'area_name')

        def decode(shard, series, locator, seen):
            meta = series_map.get(series)
            if meta is None:
                return None
            item = meta['item_code']
            name = items.get(item)
            subject = self.entity(shard, 'bls:cpi_item:' + item, 'product', name, locator, seen, cpi_item_code=item)
            area = self.area(shard, meta['area_code'], areas, locator, seen, 'cpi')
            metric = 'consumer_dollar_purchasing_power' if 'purchasing power' in (name or '').lower() else 'consumer_price_index'
            return subject, metric, base_unit(meta.get('base_period')), {
                'series_id': series, 'area': area, 'seasonal_adjustment': self.seasonal(meta['seasonal']),
                'index_population': 'CPI-U', 'base_period': meta.get('base_period')}
        return decode

    def decode_wp(self):
        series_map = self.mapping('wp.series', 'series_id')
        items = self.mapping('wp.item', ('group_code', 'item_code'), 'item_name')

        def decode(shard, series, locator, seen):
            meta = series_map.get(series)
            if meta is None:
                return None
            root = series[3:]
            label = items.get((meta['group_code'], meta['item_code'])) or meta.get('series_title')
            subject = self.entity(shard, 'bls:ppi:wp:' + root, 'product', label, locator, seen, ppi_type='commodity',
                                  group_code=meta['group_code'], item_code=meta['item_code'])
            return subject, 'producer_price_index', base_unit(meta.get('base_date')), {
                'series_id': series, 'seasonal_adjustment': self.seasonal(meta['seasonal']), 'ppi_type': 'commodity',
                'base_period': meta.get('base_date')}
        return decode

    def decode_pc(self):
        series_map = self.mapping('pc.series', 'series_id')
        products = self.mapping('pc.product', ('industry_code', 'product_code'), 'product_name')

        def decode(shard, series, locator, seen):
            meta = series_map.get(series)
            if meta is None:
                return None
            industry, product = meta['industry_code'], meta['product_code']
            label = products.get((industry, product)) or meta.get('series_title')
            subject = self.entity(shard, f'bls:ppi:pc:{industry}{product}', 'product', label, locator, seen,
                                  ppi_type='industry', naics_industry_code=industry.rstrip('-'), product_code=product)
            return subject, 'producer_price_index', base_unit(meta.get('base_date')), {
                'series_id': series, 'seasonal_adjustment': self.seasonal(meta['seasonal']), 'ppi_type': 'industry',
                'naics_industry_code': industry.rstrip('-'), 'base_period': meta.get('base_date')}
        return decode

    def decode_ap(self):
        series_map = self.mapping('ap.series', 'series_id')
        items = self.mapping('ap.item', 'item_code', 'item_name')
        areas = self.mapping('ap.area', 'area_code', 'area_name')

        def decode(shard, series, locator, seen):
            meta = series_map.get(series)
            if meta is None:
                return None
            item = meta['item_code']
            name = items.get(item)
            subject = self.entity(shard, 'bls:ap_item:' + item, 'product', name, locator, seen, ap_item_code=item)
            area = self.area(shard, meta['area_code'], areas, locator, seen, 'ap')
            return subject, 'average_price', price_unit(name), {'series_id': series, 'area': area,
                                                                 'seasonal_adjustment': 'NSA'}
        return decode

    def decode_ei(self):
        series_map = self.mapping('ei.series', 'series_id')
        indexes = self.mapping('ei.index', 'index_code', 'index_name')

        def decode(shard, series, locator, seen):
            meta = series_map.get(series)
            if meta is None:
                return None
            code = meta['index_code']
            metric = EI_METRICS.get(code, 'import_export_price_index')
            subject = self.entity(shard, 'bls:mxp:' + series[3:], 'product', meta.get('series_name') or meta.get('series_title'),
                                  locator, seen, index_code=code, index_name=indexes.get(code))
            return subject, metric, base_unit(meta.get('base_period')), {
                'series_id': series, 'seasonal_adjustment': self.seasonal(meta['seasonal']), 'index_code': code,
                'base_period': meta.get('base_period')}
        return decode

    # ----- World Bank Pink Sheet -------------------------------------------------------------------
    def pink_sheet(self, shard, name):
        seen = set()
        with zipfile.ZipFile(shard['path']) as book:
            member = sheet_members(book).get('Monthly Prices')
            if member is None:
                raise ValueError(f'{name}: worksheet "Monthly Prices" not found')
            names, units = {}, {}
            for number_, values in xlsx_stream(book, member):
                locator = f'shard:{shard["index"]}/member:{member}/row:{number_}'
                period = (values.get('A') or '').strip()
                match = re.fullmatch(r'(\d{4})M(\d{2})', period)
                if not match:
                    texts = {c: v.strip() for c, v in values.items() if c != 'A' and v and v.strip()}
                    if texts and all(re.fullmatch(r'\(.*\)', v) for v in texts.values()):
                        units = {c: v.strip('()') for c, v in texts.items()}
                    elif texts and not units:
                        names = texts
                    continue
                if not names or not units:
                    raise ValueError(f'{name}: commodity header/unit rows not found before data')
                bounds = period_bounds(int(match[1]), 'M' + match[2])
                for column, label in names.items():
                    raw_unit = units.get(column, '')
                    index = re.fullmatch(r'(\d{4})=100', raw_unit)
                    unit = f'index_{index[1]}_100' if index else PINK_UNITS.get(raw_unit, 'USD_per_' + slug(raw_unit))
                    key = 'worldbank:cmo:' + slug(label.replace('*', ''))
                    subject = self.entity(shard, key, 'commodity', label.replace('*', '').strip(), locator, seen,
                                          source_unit=raw_unit, publisher='World Bank', license='CC BY 4.0')
                    value = number(values.get(column))
                    record = {'kind': 'observation', 'id': f'{DATASET}:pink:' + digest([key, period]),
                              'observed_at': shard['retrieved_at'], 'subject': subject,
                              'metric': 'commodity_price_index' if index else 'commodity_price', 'value': value, 'unit': unit,
                              'valid_from': bounds[0], 'valid_to': bounds[1],
                              'dimensions': {'frequency': 'monthly', 'period_type': 'month', 'price_basis': 'nominal'},
                              'evidence': self.evidence(locator),
                              'attributes': {'source_file': name, 'source': 'World Bank Commodity Price Data (Pink Sheet)',
                                             'vintage': 'current_at_retrieval', 'realtime_start': shard['retrieved_at'][:10],
                                             'realtime_end': None}}
                    if value is None:
                        record['missing_reason'] = 'source_missing_or_unavailable'
                    yield from self.pending
                    self.pending = []
                    yield record
