"""UN Comtrade monthly merchandise trade (v1 get API JSON pages) -> evidence.

One observation per (reporter, partner, commodity, flow, month) for each available
measure: ``trade_value`` (USD; primaryValue = CIF for imports, FOB for exports),
``trade_net_weight`` (kg) and ``trade_quantity`` (publisher quantity unit). Subject is
the reporter (``iso3:XXX``; statistical areas such as World ``comtrade:area:0`` or
"Other Asia, nes" ``comtrade:area:490``), dimensions carry partner, commodity
(``hs:TOTAL`` or HS chapter/heading ``hs:NN``/``hs:NNNN``), flow, frequency and the
reporter's HS classification version. Each response is one raw shard read with a byte cap.
"""
import json
import re

from .evidence import Evidence, num, month_bounds

MAX_JSON_BYTES = 256 * 1024 * 1024
FLOWS = {'M': 'import', 'X': 'export', 'RM': 're_import', 'RX': 're_export'}


def _area(ev, code, iso, desc, locator):
    code = int(code)
    iso = (iso or '').strip().upper()
    if re.fullmatch(r'[A-Z]{3}', iso):
        key, typ = 'iso3:' + iso, 'country'
    else:
        key, typ = f'comtrade:area:{code}', 'jurisdiction'
    record = ev.entity(key, typ, desc or iso or f'Comtrade area {code}', locator, m49_code=code,
                       comtrade_iso=iso or None, aggregate_area=typ != 'country')
    return key, record


def _payloads(context):
    shards = context.raw_shards(0) if hasattr(context, 'raw_shards') else [{'index': 0, 'path': context.raw_path(0)}]
    for shard in shards:
        with open(shard['path'], 'rb') as stream:
            content = stream.read(MAX_JSON_BYTES + 1)
        if len(content) > MAX_JSON_BYTES:
            raise ValueError(f'shard:{shard["index"]}: Comtrade response exceeds {MAX_JSON_BYTES} bytes')
        payload = json.loads(content)
        if not isinstance(payload, dict):
            raise ValueError(f'shard:{shard["index"]}: expected a JSON object')
        if payload.get('error'):
            raise ValueError(f'shard:{shard["index"]}: Comtrade returned an error: {str(payload["error"])[:200]}')
        data = payload.get('data') or []
        if not isinstance(data, list):
            raise ValueError(f'shard:{shard["index"]}: data is not an array')
        yield shard, payload, data


def run(context):
    ev = Evidence(context, 'comtrade')
    try:
        for shard, payload, data in _payloads(context):
            truncated = len(data) >= 100000
            for number, row in enumerate(data):
                locator = f'shard:{shard["index"]}/record:{number}'
                out = []
                if row.get('typeCode') != 'C' or row.get('freqCode') != 'M':
                    raise ValueError(f'{locator}: expected monthly commodity trade')
                reporter, record = _area(ev, row['reporterCode'], row.get('reporterISO'), row.get('reporterDesc'), locator)
                out += [record] if record else []
                partner, record = _area(ev, row['partnerCode'], row.get('partnerISO'), row.get('partnerDesc'), locator)
                out += [record] if record else []
                cmd = str(row['cmdCode']).strip().upper()
                if not (cmd == 'TOTAL' or re.fullmatch(r'\d{2}(\d{2}(\d{2})?)?', cmd)):
                    raise ValueError(f'{locator}: unexpected commodity code {cmd!r}')
                product = 'hs:' + cmd
                record = ev.entity(product, 'product', row.get('cmdDesc') or ('All commodities' if cmd == 'TOTAL' else 'HS ' + cmd),
                                   locator, nomenclature='HS (version-independent chapter/heading)',
                                   hs_level=None if cmd == 'TOTAL' else len(cmd), aggregate=cmd == 'TOTAL')
                out += [record] if record else []
                flow = FLOWS.get(row.get('flowCode'))
                if flow is None:
                    raise ValueError(f'{locator}: unexpected flowCode {row.get("flowCode")!r}')
                start, end = month_bounds(row['refYear'], row['refMonth'])
                if str(row.get('period')) != start[:4] + start[5:7]:
                    raise ValueError(f'{locator}: period does not match refYear/refMonth')
                dims = {'partner': partner, 'product': product, 'flow': flow, 'frequency': 'monthly',
                        'classification': row.get('classificationCode'), 'customs_code': row.get('customsCode'),
                        'mode_of_transport': row.get('motCode'), 'partner2': row.get('partner2Code'),
                        'mode_of_supply': row.get('mosCode')}
                common = {'is_reported': row.get('isReported'), 'is_aggregate': row.get('isAggregate'),
                          'response_truncated_at_100k': truncated or None}
                value = num(row.get('primaryValue'))
                record = ev.observation(reporter, 'trade_value', value, 'USD', locator, valid_from=start, valid_to=end,
                                        dimensions=dims, missing_reason='not_reported_by_comtrade', **common,
                                        valuation='CIF' if flow in ('import', 're_import') else 'FOB',
                                        cif_value=num(row.get('cifvalue')), fob_value=num(row.get('fobvalue')))
                out.append(record)
                weight = num(row.get('netWgt'))
                if weight is not None and (weight > 0 or not row.get('isNetWgtEstimated')):
                    out.append(ev.observation(reporter, 'trade_net_weight', weight, 'kg', locator, valid_from=start,
                                              valid_to=end, dimensions=dims, estimated=bool(row.get('isNetWgtEstimated')), **common))
                quantity, unit = num(row.get('qty')), row.get('qtyUnitAbbr')
                if quantity is not None and row.get('qtyUnitCode') not in (None, -1) and unit and unit != 'N/A':
                    out.append(ev.observation(reporter, 'trade_quantity', quantity, str(unit), locator, valid_from=start,
                                              valid_to=end, dimensions=dims, estimated=bool(row.get('isQtyEstimated')),
                                              quantity_unit_code=row.get('qtyUnitCode'), **common))
                yield from out
    finally:
        ev.close()
