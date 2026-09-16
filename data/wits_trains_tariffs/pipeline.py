"""WITS / UNCTAD TRAINS applied tariffs at HS6 by reporter (MFN, partner 000 = world), SDMX-ML.

One raw shard per reporter-year response. Each SDMX series is one reporter x partner x HS6 product;
its observation is the simple-average applied ad valorem rate (percent) across national tariff
lines in that HS6 subheading, with line counts and min/max rates kept as attributes.
Reporter numeric codes map to ``iso3:XXX`` via local ``countries.json``; 918 (European Union) and
other groups become ``wits:economy:<code>`` aggregate jurisdictions.
"""
import json
from pathlib import Path

from .evidence import Evidence, num, year_bounds
from .helpers import iter_series

COUNTRIES = json.loads(Path(__file__).with_name('countries.json').read_text(encoding='utf-8'))
M49 = {v['m49']: k for k, v in COUNTRIES.items() if v.get('m49')}
GROUP_LABELS = {'918': 'European Union', '000': 'World'}
# WITS NOMENCODE -> Harmonized System revision.
HS_REVISION = {'H0': 'HS1988', 'H1': 'HS1996', 'H2': 'HS2002', 'H3': 'HS2007', 'H4': 'HS2012', 'H5': 'HS2017', 'H6': 'HS2022'}
METRICS = {'MFN': 'mfn_applied_tariff_simple_avg', 'PRF': 'preferential_applied_tariff_simple_avg',
           'AHS': 'effectively_applied_tariff_simple_avg'}


def economy(code):
    code = str(code or '').strip()
    iso3 = M49.get(code.zfill(3) if code.isdigit() else code)
    if iso3 and code != '000':
        return 'iso3:' + iso3, 'country', COUNTRIES[iso3]['name'], False
    return 'wits:economy:' + code, 'jurisdiction', GROUP_LABELS.get(code, 'WITS economy ' + code), True


def _int(value):
    parsed = num(value)
    return int(parsed) if isinstance(parsed, (int, float)) else None


def run(context):
    out = Evidence(context, 'wits')
    for shard in context.raw_shards():
        if shard.get('bytes') == 0:
            continue
        for ordinal, key, observations in iter_series(shard['path']):
            locator = f'shard:{shard["index"]}/series:{ordinal}'
            reporter, typ, label, aggregate = economy(key.get('REPORTER'))
            entity = out.entity(reporter, typ, label, locator, wits_code=key.get('REPORTER'), aggregate=aggregate)
            if entity:
                yield entity
            partner, _, partner_label, _ = economy(key.get('PARTNER'))
            product = str(key.get('PRODUCTCODE') or '').strip()
            if not product.isdigit():
                continue
            for obs in observations:
                attrs = obs['attributes']
                nomenclature = attrs.get('NOMENCODE')
                product_key = 'hs:' + product
                entity = out.entity(product_key, 'product', 'HS ' + product, locator, hs_code=product,
                                    digits=len(product), first_seen_nomenclature=HS_REVISION.get(nomenclature, nomenclature))
                if entity:
                    yield entity
                if not (obs['time'] or '').isdigit():
                    continue
                start, end = year_bounds(obs['time'])
                tariff_type = attrs.get('TARIFFTYPE') or 'unknown'
                value = num(obs['value'])
                dims = {'frequency': 'annual', 'partner': partner, 'product': product_key, 'tariff_type': tariff_type,
                        'hs_revision': HS_REVISION.get(nomenclature, nomenclature), 'datatype': key.get('DATATYPE'),
                        'measure': attrs.get('OBS_VALUE_MEASURE')}
                yield out.observation(reporter, METRICS.get(tariff_type, 'applied_tariff_simple_avg'), value, 'percent',
                                      locator, valid_from=start, valid_to=end, dimensions=dims,
                                      missing_reason=None if value is not None else 'source_missing',
                                      partner_label=partner_label, min_rate_percent=num(attrs.get('MIN_RATE')),
                                      max_rate_percent=num(attrs.get('MAX_RATE')), total_lines=_int(attrs.get('TOTALNOOFLINES')),
                                      mfn_lines=_int(attrs.get('NBR_MFN_LINES')), preferential_lines=_int(attrs.get('NBR_PREF_LINES')),
                                      non_ad_valorem_lines=_int(attrs.get('NBR_NA_LINES')))
    out.close()
