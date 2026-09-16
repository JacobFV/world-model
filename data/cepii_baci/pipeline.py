"""CEPII BACI HS17 bilateral trade flows -> compact evidence.

Flow representation (both stages): one observation per exporter-importer-product-year,
``subject`` = exporter country, ``metric`` = ``bilateral_trade_value`` (unit
``thousand_USD``, current USD, FOB-reconciled by CEPII), ``dimensions`` =
{importer, product, frequency}, valid window = calendar year, and the reconciled
quantity in ``attributes.quantity_t`` (metric tons; null when CEPII has no quantity).

Stage ``normalized``: HS6 product lines (``hs17:NNNNNN``), as published.
Stage ``hs4``: exporter-importer-HS4 aggregates (``hs17:NNNN``) plus country-pair
``resource_flow`` entities (``baci:flow:EXP:IMP``) with ``flow_source`` /
``flow_destination`` relations and yearly ``bilateral_trade_total_value`` observations.

Record IDs are readable deterministic keys (release, year, zero-padded BACI country
codes, product) so they stay unique within a release, compress well and are inserted
in sorted order. Memory is bounded: code tables (~5k products, ~240 areas) and, for
``hs4``, one exporter-importer group at a time (BACI files are sorted by t,i,j,k; an
unsorted input raises instead of silently double-counting).
"""
import re

from .evidence import KeySet, num, year_bounds

FLOW_MEMBERS = ['BACI_HS17_Y*_V*.csv']
COUNTRY_MEMBERS = ['country_codes_V*.csv']
PRODUCT_MEMBERS = ['product_codes_HS17_V*.csv']
MEMBER = re.compile(r'BACI_HS(\d\d)_Y(\d{4})_V(\d{6})\.csv$')


def _text(value):
    value = (value or '').strip()
    try:  # Some BACI releases ship UTF-8 names double-encoded as latin-1.
        fixed = value.encode('latin-1').decode('utf-8')
        return fixed
    except (UnicodeEncodeError, UnicodeDecodeError):
        return value


class Baci:
    def __init__(self, context, stage):
        self.context, self.stage = context, stage
        self.dataset = context.definition['id']
        self.ref = context.raw_inputs[0]
        receipt = context.raw_receipt(0)
        self.observed_at = receipt.get('retrieved_at') or (receipt.get('source') or {}).get('acquisition', {}).get('completed_at')
        self.complete = context.raw_coverage(0)['complete']
        self.keys = KeySet()
        self.countries, self.products = {}, {}

    def base(self, kind, record_id, locator, **fields):
        return {'kind': kind, 'id': record_id, 'observed_at': self.observed_at,
                'evidence': [{'input': self.ref, 'locator': locator}], **fields}

    def entity(self, key, entity_type, label, locator, **attrs):
        if not self.keys.add(key):
            return None
        return self.base('entity', f'baci:{self.stage}:entity:{key}', locator, entity_id=key, entity_type=entity_type,
                         label=label or key, attributes={'source_dataset': self.dataset, **attrs})

    def rows(self, members):
        return self.context.raw_rows(0, format='csv', members=members, compression='auto', encoding='utf-8-sig')

    def load_tables(self, emit_products=True):
        for locator, row in self.rows(COUNTRY_MEMBERS):
            code = str(int(row['country_code']))
            iso3 = (row.get('country_iso3') or '').strip().upper()
            name = _text(row.get('country_name'))
            if re.fullmatch(r'[A-Z]{3}', iso3):
                key, typ = 'iso3:' + iso3, 'country'
            else:  # Statistical areas such as S19 "Other Asia, nes" or R20 "Europe EFTA, nes".
                key, typ = 'baci:area:' + code, 'jurisdiction'
            self.countries[code] = (key, typ, name, locator, iso3, (row.get('country_iso2') or '').strip())
        for locator, row in self.rows(PRODUCT_MEMBERS):
            code = row['code'].strip()
            if not re.fullmatch(r'\d{6}', code):
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
    """Yield (locator, release, year, i, j, k, value, quantity) with validation."""
    for locator, row in baci.rows(FLOW_MEMBERS):
        member = locator.split('/member:', 1)[1].rsplit('/line:', 1)[0]
        match = MEMBER.search(member)
        if not match or match[1] != '17':
            raise ValueError(f'{locator}: unexpected BACI member name')
        year = int(row['t'])
        if year != int(match[2]):
            raise ValueError(f'{locator}: year column does not match member year')
        k = row['k'].strip().zfill(6)
        value = num(row['v'])
        if value is None or value < 0:
            raise ValueError(f'{locator}: invalid trade value {row["v"]!r}')
        quantity = num(row['q'])
        yield locator, 'v' + match[3], year, int(row['i']), int(row['j']), k, value, quantity


def run(context):
    """HS6 exporter-importer-product-year flows."""
    baci = Baci(context, 'hs6')
    try:
        baci.load_tables()
        out = []
        for code, (description, locator) in sorted(baci.products.items()):
            record = baci.entity('hs17:' + code, 'product', description, locator, nomenclature='HS2017',
                                 hs_level=6, hs4='hs17:' + code[:4], hs2='hs17:' + code[:2])
            if record:
                out.append(record)
        yield from out
        for locator, release, year, i, j, k, value, quantity in _flow_rows(baci):
            baci.last_locator = locator
            out = []
            exporter, importer = baci.country(i, out), baci.country(j, out)
            product = 'hs17:' + k
            if k not in baci.products and baci.keys.add(product):
                out.append(baci.base('entity', f'baci:hs6:entity:{product}', locator, entity_id=product, entity_type='product',
                                     label='HS2017 ' + k, attributes={'source_dataset': baci.dataset, 'nomenclature': 'HS2017',
                                                                      'hs_level': 6, 'unlisted_in_product_codes': True}))
            start, end = year_bounds(year)
            out.append(baci.base('observation', f'baci:{release}:{year}:{i:03d}:{j:03d}:{k}', locator,
                                 subject=exporter, metric='bilateral_trade_value', value=value, unit='thousand_USD',
                                 dimensions={'importer': importer, 'product': product, 'frequency': 'annual'},
                                 valid_from=start, valid_to=end, attributes={'quantity_t': quantity}))
            yield from out
    finally:
        baci.close()


def hs4(context):
    """Exporter-importer-HS4-year aggregates plus country-pair flow entities and totals."""
    baci = Baci(context, 'hs4')
    try:
        baci.load_tables()
        out, headings = [], {}
        for code, (description, locator) in sorted(baci.products.items()):
            headings.setdefault(code[:4], locator)
        for heading, locator in sorted(headings.items()):
            record = baci.entity('hs17:' + heading, 'product', 'HS2017 heading ' + heading, locator,
                                 nomenclature='HS2017', hs_level=4, hs2='hs17:' + heading[:2])
            if record:
                out.append(record)
        yield from out
        finished = KeySet()
        group, sums = None, {}

        def flush():
            release, year, i, j, first = group
            records = []
            exporter, importer = baci.country(i, records), baci.country(j, records)
            flow = f'baci:flow:{exporter.split(":", 1)[1]}:{importer.split(":", 1)[1]}' if exporter.startswith('iso3:') and importer.startswith('iso3:') \
                else f'baci:flow:{i}:{j}'
            entity = baci.entity(flow, 'resource_flow', f'Merchandise trade {exporter} -> {importer}', first,
                                 exporter=exporter, importer=importer)
            if entity:
                records.append(entity)
                for predicate, target in (('flow_source', exporter), ('flow_destination', importer)):
                    records.append(baci.base('assertion', f'baci:hs4:rel:{flow}:{predicate}', first, subject=flow,
                                             predicate=predicate, object=target,
                                             attributes={'source_dataset': baci.dataset}))
            start, end = year_bounds(year)
            total_value, total_lines = 0.0, 0
            for heading in sorted(sums):
                value, quantity, lines, missing_q, locator = sums[heading]
                total_value += value
                total_lines += lines
                records.append(baci.base(
                    'observation', f'baci:{release}:hs4:{year}:{i:03d}:{j:03d}:{heading}', locator,
                    subject=exporter, metric='bilateral_trade_value', value=round(value, 3), unit='thousand_USD',
                    dimensions={'importer': importer, 'product': 'hs17:' + heading, 'frequency': 'annual'},
                    valid_from=start, valid_to=end,
                    attributes={'quantity_t': None if missing_q == lines else round(quantity, 3), 'aggregate': 'hs6_to_hs4',
                                'hs6_lines': lines, 'hs6_lines_without_quantity': missing_q}))
            records.append(baci.base(
                'observation', f'baci:{release}:total:{year}:{i:03d}:{j:03d}', first, subject=flow,
                metric='bilateral_trade_total_value', value=round(total_value, 3), unit='thousand_USD',
                dimensions={'exporter': exporter, 'importer': importer, 'frequency': 'annual'},
                valid_from=start, valid_to=end,
                attributes={'aggregate': 'all_hs6_products', 'hs6_lines': total_lines}))
            return records

        for locator, release, year, i, j, k, value, quantity in _flow_rows(baci):
            baci.last_locator = locator
            key = (release, year, i, j)
            if group is None or key != group[:4]:
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
        finished.close()
    finally:
        baci.close()
