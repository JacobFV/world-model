"""CEPII BACI HS92 (1995-2024) bilateral trade flows -> compact evidence.

Same flow representation as ``cepii_baci`` (HS2017): one observation per
exporter-importer-product-year, ``subject`` = exporter country, ``metric`` =
``bilateral_trade_value`` (unit ``thousand_USD``, current USD, CEPII-reconciled),
``dimensions`` = {importer, product, frequency}, valid window = calendar year, and
reconciled quantity in ``attributes.quantity_t`` (metric tons; the key is omitted when
CEPII publishes no quantity, to keep 30 years of output compact).

Stage ``normalized``: HS6 product lines (``hs92:NNNNNN``).
Stage ``hs4``: exporter-importer-HS4 aggregates (``hs92:NNNN``) plus country-pair
``resource_flow`` entities (``baci92:flow:EXP:IMP``) with ``flow_source`` /
``flow_destination`` relations and yearly ``bilateral_trade_total_value``.

Countries share the ``iso3:XXX`` namespace with cepii_baci; HS92 and HS17 product
codes are distinct nomenclatures and must not be joined without a concordance.
Record IDs are readable deterministic keys (``baci92:<year>:<exp>:<imp>:<hs6>``, zero-padded
BACI country codes). Memory is bounded: code tables, and for ``hs4`` one
exporter-importer group at a time (CEPII files are sorted by t,i,j,k; an unsorted
input raises instead of silently double-counting).
"""
import re

from .evidence import KeySet, num, year_bounds

HS = '92'
NOMENCLATURE = 'HS1992'
PREFIX = 'hs92:'
FLOW_MEMBERS = [f'BACI_HS{HS}_Y*_V*.csv']
COUNTRY_MEMBERS = ['country_codes_V*.csv']
PRODUCT_MEMBERS = [f'product_codes_HS{HS}_V*.csv']
MEMBER = re.compile(r'BACI_HS(\d\d)_Y(\d{4})_V(\d{6})\.csv$')


def _text(value):
    value = (value or '').strip()
    try:  # BACI ships some UTF-8 names double-encoded as latin-1.
        return value.encode('latin-1').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


class Baci:
    def __init__(self, context, stage):
        self.context, self.stage = context, stage
        self.dataset = context.definition['id']
        self.ref = context.raw_inputs[0]
        receipt = context.raw_receipt(0)
        self.observed_at = receipt.get('retrieved_at') or (receipt.get('source') or {}).get('acquisition', {}).get('completed_at')
        self.keys = KeySet()
        self.countries, self.products = {}, {}
        self.last_locator = None

    def base(self, kind, record_id, locator, **fields):
        return {'kind': kind, 'id': record_id, 'observed_at': self.observed_at,
                'evidence': [{'input': self.ref, 'locator': locator}], **fields}

    def entity(self, key, entity_type, label, locator, **attrs):
        if not self.keys.add(key):
            return None
        return self.base('entity', f'baci92:{self.stage}:entity:{key}', locator, entity_id=key, entity_type=entity_type,
                         label=label or key, attributes={'source_dataset': self.dataset, **attrs})

    def rows(self, members):
        return self.context.raw_rows(0, format='csv', members=members, compression='auto', encoding='utf-8-sig')

    def load_tables(self):
        for locator, row in self.rows(COUNTRY_MEMBERS):
            code = str(int(row['country_code']))
            iso3 = (row.get('country_iso3') or '').strip().upper()
            if re.fullmatch(r'[A-Z]{3}', iso3):
                key, typ = 'iso3:' + iso3, 'country'
            else:  # Statistical areas such as S19 "Other Asia, nes".
                key, typ = 'baci:area:' + code, 'jurisdiction'
            self.countries[code] = (key, typ, _text(row.get('country_name')), locator, iso3,
                                    (row.get('country_iso2') or '').strip())
        for locator, row in self.rows(PRODUCT_MEMBERS):
            code = row['code'].strip().upper()
            # HS92 includes special codes such as 9999AA for unclassified goods.
            if not re.fullmatch(r'[0-9A-Z]{6}', code):
                raise ValueError(f'{locator}: invalid HS6 product code {code!r}')
            self.products[code] = (_text(row.get('description')), locator)
        if not self.countries or not self.products:
            raise ValueError('BACI country_codes / product_codes tables are missing from the raw artifact')

    def country(self, code, out):
        code = str(int(code))
        known = self.countries.get(code)
        if known is None:  # Code absent from country_codes: keep it explicit, cite the flow row.
            key, typ, name, locator, iso3, iso2 = 'baci:area:' + code, 'jurisdiction', None, self.last_locator, None, None
        else:
            key, typ, name, locator, iso3, iso2 = known
        record = self.entity(key, typ, name or f'BACI area {code}', locator, baci_country_code=int(code),
                             iso3=iso3 or None, iso2=iso2 or None, unmapped_code=known is None)
        if record is not None:
            out.append(record)
        return key

    def close(self):
        self.keys.close()


def _flow_rows(baci):
    """Yield (locator, year, i, j, k, value, quantity) with validation."""
    for locator, row in baci.rows(FLOW_MEMBERS):
        member = locator.split('/member:', 1)[1].rsplit('/line:', 1)[0]
        match = MEMBER.search(member)
        if not match or match[1] != HS:
            raise ValueError(f'{locator}: unexpected BACI member name')
        year = int(row['t'])
        if year != int(match[2]):
            raise ValueError(f'{locator}: year column does not match member year')
        k = row['k'].strip().upper().zfill(6)
        value = num(row['v'])
        if value is None or value < 0:
            raise ValueError(f'{locator}: invalid trade value {row["v"]!r}')
        yield locator, year, int(row['i']), int(row['j']), k, value, num(row['q'])


def run(context):
    """HS6 exporter-importer-product-year flows."""
    baci = Baci(context, 'hs6')
    try:
        baci.load_tables()
        out = []
        for code, (description, locator) in sorted(baci.products.items()):
            record = baci.entity(PREFIX + code, 'product', description, locator, nomenclature=NOMENCLATURE,
                                 hs_level=6, hs4=PREFIX + code[:4], hs2=PREFIX + code[:2],
                                 special_code=None if code.isdigit() else 'not_a_wco_hs_code')
            if record:
                out.append(record)
        yield from out
        for locator, year, i, j, k, value, quantity in _flow_rows(baci):
            baci.last_locator = locator
            out = []
            exporter, importer = baci.country(i, out), baci.country(j, out)
            product = PREFIX + k
            if k not in baci.products and baci.keys.add(product):
                out.append(baci.base('entity', f'baci92:hs6:entity:{product}', locator, entity_id=product,
                                     entity_type='product', label=NOMENCLATURE + ' ' + k,
                                     attributes={'source_dataset': baci.dataset, 'nomenclature': NOMENCLATURE,
                                                 'hs_level': 6, 'unlisted_in_product_codes': True}))
            start, end = year_bounds(year)
            record = baci.base('observation', f'baci92:{year}:{i:03d}:{j:03d}:{k}', locator,
                               subject=exporter, metric='bilateral_trade_value', value=value, unit='thousand_USD',
                               dimensions={'importer': importer, 'product': product, 'frequency': 'annual'},
                               valid_from=start, valid_to=end)
            if quantity is not None:
                record['attributes'] = {'quantity_t': quantity}
            out.append(record)
            yield from out
    finally:
        baci.close()


def hs4(context):
    """Exporter-importer-HS4-year aggregates plus country-pair flow entities and totals."""
    baci = Baci(context, 'hs4')
    finished = KeySet()
    try:
        baci.load_tables()
        out, headings = [], {}
        for code, (_, locator) in sorted(baci.products.items()):
            headings.setdefault(code[:4], locator)
        for heading, locator in sorted(headings.items()):
            record = baci.entity(PREFIX + heading, 'product', f'{NOMENCLATURE} heading {heading}', locator,
                                 nomenclature=NOMENCLATURE, hs_level=4, hs2=PREFIX + heading[:2])
            if record:
                out.append(record)
        yield from out
        group, sums = None, {}

        def flush():
            year, i, j, first = group
            records = []
            exporter, importer = baci.country(i, records), baci.country(j, records)
            if exporter.startswith('iso3:') and importer.startswith('iso3:'):
                flow = f'baci92:flow:{exporter[5:]}:{importer[5:]}'
            else:
                flow = f'baci92:flow:{i}:{j}'
            entity = baci.entity(flow, 'resource_flow', f'Merchandise trade {exporter} -> {importer}', first,
                                 exporter=exporter, importer=importer, nomenclature=NOMENCLATURE)
            if entity:
                records.append(entity)
                for predicate, target in (('flow_source', exporter), ('flow_destination', importer)):
                    records.append(baci.base('assertion', f'baci92:hs4:rel:{flow}:{predicate}', first, subject=flow,
                                             predicate=predicate, object=target,
                                             attributes={'source_dataset': baci.dataset}))
            start, end = year_bounds(year)
            total_value, total_lines = 0.0, 0
            for heading in sorted(sums):
                value, quantity, lines, missing_q, locator = sums[heading]
                total_value += value
                total_lines += lines
                records.append(baci.base(
                    'observation', f'baci92:hs4:{year}:{i:03d}:{j:03d}:{heading}', locator,
                    subject=exporter, metric='bilateral_trade_value', value=round(value, 3), unit='thousand_USD',
                    dimensions={'importer': importer, 'product': PREFIX + heading, 'frequency': 'annual'},
                    valid_from=start, valid_to=end,
                    attributes={'quantity_t': None if missing_q == lines else round(quantity, 3), 'aggregate': 'hs6_to_hs4',
                                'hs6_lines': lines, 'hs6_lines_without_quantity': missing_q}))
            records.append(baci.base(
                'observation', f'baci92:total:{year}:{i:03d}:{j:03d}', first, subject=flow,
                metric='bilateral_trade_total_value', value=round(total_value, 3), unit='thousand_USD',
                dimensions={'exporter': exporter, 'importer': importer, 'frequency': 'annual'},
                valid_from=start, valid_to=end, attributes={'aggregate': 'all_hs6_products', 'hs6_lines': total_lines}))
            return records

        for locator, year, i, j, k, value, quantity in _flow_rows(baci):
            baci.last_locator = locator
            key = (year, i, j)
            if group is None or key != group[:3]:
                if group is not None:
                    yield from flush()
                if not finished.add('|'.join(map(str, key))):
                    raise ValueError(f'{locator}: BACI rows are not grouped by year/exporter/importer; cannot stream-aggregate')
                group, sums = (*key, locator), {}
            entry = sums.get(k[:4])
            if entry is None:
                entry = sums[k[:4]] = [0.0, 0.0, 0, 0, locator]
            entry[0] += value
            entry[2] += 1
            if quantity is None:
                entry[3] += 1
            else:
                entry[1] += quantity
        if group is not None:
            yield from flush()
    finally:
        finished.close()
        baci.close()
