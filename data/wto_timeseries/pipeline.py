"""WTO Timeseries API indicators (tariff profile averages, merchandise and services trade) by economy.

Each raw shard is one ``/timeseries/v1/data`` JSON response ``{"Dataset": [...]}`` (``mode=full``).
Reporting economies use WTO numeric (M49-style) codes, mapped to ``iso3:XXX`` via the local
``countries.json``; groups (e.g. 918 European Union, 000 World) become ``wto:economy:<code>``
aggregate jurisdictions. Metrics are ``wto_<indicator code>`` with friendly aliases for known codes.
"""
import json
from datetime import date
from pathlib import Path
import re

from .evidence import Evidence, month_bounds, num, year_bounds

COUNTRIES = json.loads(Path(__file__).with_name('countries.json').read_text(encoding='utf-8'))
M49 = {v['m49']: k for k, v in COUNTRIES.items() if v.get('m49')}
ALIASES = {
    'TP_A_0010': 'mfn_applied_tariff_simple_avg_all_products',
    'TP_A_0030': 'bound_tariff_simple_avg_all_products',
    'TP_A_0020': 'mfn_applied_tariff_trade_weighted_avg_all_products',
    'ITS_MTV_AX': 'merchandise_exports_value',
    'ITS_MTV_AM': 'merchandise_imports_value',
    'ITS_CS_AX6': 'commercial_services_exports_value',
    'ITS_CS_AM6': 'commercial_services_imports_value',
}
UNITS = {'percent': 'percent', 'us$ million': 'million_USD', 'million us dollar': 'million_USD',
         'us$ billion': 'billion_USD', 'number': 'count', 'index': 'index'}


def economy(code, name):
    code = str(code or '').strip()
    if code.isdigit():
        code = code.zfill(3)
    iso3 = M49.get(code)
    if iso3:
        return 'iso3:' + iso3, 'country', COUNTRIES[iso3]['name'], False
    return 'wto:economy:' + (code or 'unknown'), 'jurisdiction', name or code, True


def unit_of(text):
    key = re.sub(r'\s+', ' ', str(text or '').strip().lower())
    return UNITS.get(key) or (re.sub(r'[^a-z0-9$%]+', '_', key).strip('_') or 'unspecified')


def period(row):
    year = row.get('Year')
    code = str(row.get('PeriodCode') or '').upper()
    freq = str(row.get('FrequencyCode') or 'A').upper()
    if not year:
        return None, None, freq
    year = int(year)
    if freq == 'M' and re.fullmatch(r'M\d\d', code):
        return (*month_bounds(year, int(code[1:])), 'monthly')
    if freq == 'Q' and re.fullmatch(r'Q[1-4]', code):
        quarter = int(code[1])
        start = date(year, 3 * quarter - 2, 1)
        end = date(year + (quarter == 4), 1 if quarter == 4 else 3 * quarter + 1, 1)
        return start.isoformat(), end.isoformat(), 'quarterly'
    return (*year_bounds(year), 'annual')


def run(context):
    out = Evidence(context, 'wto')
    for shard in context.raw_shards():
        if shard['bytes'] == 0:
            continue  # 204 No Content: indicator has no data for the requested scope.
        from worldmodel.raw_readers import iter_rows
        for locator, row in iter_rows([shard], {'format': 'json', 'records_path': ['Dataset'], 'compression': 'none'}):
            indicator = str(row.get('IndicatorCode') or '').strip()
            if not indicator:
                continue
            subject, typ, label, aggregate = economy(row.get('ReportingEconomyCode'), row.get('ReportingEconomy'))
            entity = out.entity(subject, typ, label, locator, wto_economy_code=row.get('ReportingEconomyCode'), aggregate=aggregate)
            if entity:
                yield entity
            partner, _, partner_label, partner_aggregate = economy(row.get('PartnerEconomyCode'), row.get('PartnerEconomy'))
            start, end, frequency = period(row)
            dims = {'frequency': frequency, 'partner': partner, 'product_or_sector': row.get('ProductOrSectorCode') or None,
                    'classification': row.get('ProductOrSectorClassificationCode') or None, 'indicator': indicator}
            value = num(row.get('Value'))
            yield out.observation(subject, ALIASES.get(indicator, 'wto_' + indicator.lower()), value, unit_of(row.get('Unit')),
                                  locator, valid_from=start, valid_to=end, dimensions=dims,
                                  missing_reason=None if value is not None else 'source_missing',
                                  indicator_name=row.get('Indicator'), value_flag=row.get('ValueFlagCode') or None,
                                  partner_label=partner_label, partner_aggregate=partner_aggregate,
                                  product_or_sector_label=row.get('ProductOrSector'))
    out.close()
