"""U.S. monthly merchandise trade by HS6 x partner country from the Census international trade API.

One raw shard per (flow, partner, month) API response: a JSON array whose first row is the header.
Imports carry general-imports value, imports-for-consumption value, calculated duty and dutiable value;
exports carry total exports value. Rows whose monthly values are all zero (no trade that month) are
skipped. Values are nominal USD, not seasonally adjusted.

Subject is ``iso3:USA``; the partner (``iso3:XXX`` via Census Schedule C ISO codes, or
``census:country:<code>`` for groups/unmapped codes, ``census:partner:all`` for the all-country total)
and the product (``hs:NNNNNN``) are dimensions and entities.
"""
import json
from pathlib import Path

from .evidence import Evidence, month_bounds, num

SCHEDULE_C = json.loads(Path(__file__).with_name('census_countries.json').read_text(encoding='utf-8'))
IMPORT_FIELDS = {'GEN_VAL_MO': 'import_general_value', 'CAL_DUT_MO': 'import_calculated_duty'}


def partner(code):
    code = str(code or '').strip()
    if code in ('-', ''):
        return 'census:partner:all', 'jurisdiction', 'All countries (U.S. total)', True
    entry = SCHEDULE_C.get(code)
    if entry and entry.get('iso3'):
        return 'iso3:' + entry['iso3'], 'country', entry['name'], False
    return 'census:country:' + code, 'jurisdiction', (entry or {}).get('name') or 'Census country ' + code, True


def table(path):
    """Yield ``(record_number, row)`` from a Census JSON table; duplicate header names keep the first column."""
    with open(path, encoding='utf-8') as stream:
        payload = json.load(stream)
    if not isinstance(payload, list) or not payload or not isinstance(payload[0], list):
        raise ValueError(f'{path}: Census response is not a header+rows table')
    header = payload[0]
    positions = {}
    for position, name in enumerate(header):
        positions.setdefault(name, position)
    for number, values in enumerate(payload[1:], 1):
        if not isinstance(values, list) or len(values) != len(header):
            raise ValueError(f'{path}: record {number} does not match header')
        yield number, {name: values[position] for name, position in positions.items()}


def run(context):
    out = Evidence(context, 'census_trade')
    usa = out.entity('iso3:USA', 'country', 'United States', 'shard:0/record:0', census_code='1000')
    usa_pending = usa
    for shard in context.raw_shards():
        if shard.get('bytes', 1) == 0:
            continue  # 204 No Content: no rows for this partner-month.
        for number, row in table(shard['path']):
            locator = f'shard:{shard["index"]}/record:{number}'
            if usa_pending:
                usa_pending['evidence'] = context.raw_evidence(locator)
                yield usa_pending
                usa_pending = None
            imports = 'I_COMMODITY' in row
            commodity = str(row.get('I_COMMODITY' if imports else 'E_COMMODITY') or '').strip()
            month = str(row.get('time') or '')
            if not commodity.isdigit() or len(month) != 7:
                continue
            values = {k: num(row.get(k)) for k in (('GEN_VAL_MO', 'CON_VAL_MO', 'CAL_DUT_MO', 'DUT_VAL_MO') if imports else ('ALL_VAL_MO',))}
            if not any(values.values()):
                continue
            partner_id, typ, label, aggregate = partner(row.get('CTY_CODE'))
            entity = out.entity(partner_id, typ, label, locator, census_country_code=row.get('CTY_CODE'), aggregate=aggregate)
            if entity:
                yield entity
            product = 'hs:' + commodity
            entity = out.entity(product, 'product', 'HS ' + commodity, locator, hs_code=commodity, digits=len(commodity))
            if entity:
                yield entity
            start, end = month_bounds(month[:4], month[5:7])
            dims = {'frequency': 'monthly', 'flow': 'imports' if imports else 'exports', 'partner': partner_id,
                    'product': product, 'hs_level': row.get('COMM_LVL') or 'HS6', 'hs_revision': 'HS2022' if month >= '2022' else 'HS2017'}
            common = dict(valid_from=start, valid_to=end, dimensions=dims, aggregate=aggregate or None)
            if imports:
                yield out.observation('iso3:USA', 'import_general_value', values['GEN_VAL_MO'] or 0, 'USD', locator, **common)
                duty_attrs = {'import_consumption_value_usd': values['CON_VAL_MO'], 'import_dutiable_value_usd': values['DUT_VAL_MO']}
                if values['CON_VAL_MO']:
                    duty_attrs['effective_duty_rate_fraction'] = round((values['CAL_DUT_MO'] or 0) / values['CON_VAL_MO'], 8)
                yield out.observation('iso3:USA', 'import_calculated_duty', values['CAL_DUT_MO'] or 0, 'USD', locator,
                                      **common, **duty_attrs)
            else:
                yield out.observation('iso3:USA', 'export_value', values['ALL_VAL_MO'], 'USD', locator, **common)
    out.close()
