"""FAOSTAT normalized bulk files -> area x item x element x period observations.

Domains are identified by ZIP member name:
  QCL Production_Crops_Livestock   QI Production_Indices   QV Value_of_Production
  PP  Prices                       CP ConsumerPriceIndices
  TM  Trade_DetailedTradeMatrix (bilateral reporter -> partner flows)
  TCL Trade_CropsLivestock (country totals)   TCLI Trade_CropsLivestockIndicators
  TI  Trade_Indices                FBS FoodBalanceSheets
Areas map to ``iso3:XXX`` through the M49 code; FAO aggregates (area code >= 5000)
and historical areas keep ``fao:area:<code>``.
"""
import json
import re
from pathlib import Path

from .evidence import Evidence, month_bounds, num, year_bounds

MONTHS = {name: number for number, name in enumerate(
    ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October',
     'November', 'December'], 1)}
DOMAINS = {'Production_Crops_Livestock': 'QCL', 'Production_Indices': 'QI', 'Value_of_Production': 'QV',
           'Prices': 'PP', 'ConsumerPriceIndices': 'CP', 'Trade_DetailedTradeMatrix': 'TM',
           'Trade_CropsLivestock': 'TCL', 'Trade_CropsLivestockIndicators': 'TCLI', 'Trade_Indices': 'TI',
           'FoodBalanceSheets': 'FBS'}
TRADE = {'5610': 'import_quantity', '5608': 'import_quantity', '5609': 'import_quantity', '5607': 'import_quantity',
         '5622': 'import_value', '5910': 'export_quantity', '5908': 'export_quantity', '5909': 'export_quantity',
         '5907': 'export_quantity', '5922': 'export_value'}
# Element code -> metric (unit comes from the source Unit column, normalized below).
METRICS = {
    'QCL': {'5510': 'production', '5513': 'production', '5312': 'area_harvested', '5412': 'yield', '5413': 'yield',
            '5417': 'carcass_yield', '5424': 'carcass_yield', '5320': 'producing_or_slaughtered_animals',
            '5321': 'producing_or_slaughtered_animals', '5111': 'livestock_stocks', '5112': 'livestock_stocks',
            '5114': 'livestock_stocks', '5318': 'milk_animals', '5313': 'laying_animals'},
    'QI': {'432': 'gross_production_index', '434': 'gross_per_capita_production_index',
           '436': 'net_production_index', '438': 'net_per_capita_production_index'},
    'QV': {'152': 'gross_production_value', '55': 'gross_production_value', '58': 'gross_production_value',
           '56': 'gross_production_value', '57': 'gross_production_value'},
    'PP': {'5530': 'producer_price', '5531': 'producer_price', '5532': 'producer_price',
           '5539': 'producer_price_index'},
    'CP': {'6125': 'consumer_price_index', '6121': 'consumer_price_inflation'},
    'TM': TRADE, 'TCL': TRADE,
    'TI': {'462': 'import_value_index', '465': 'import_quantity_index', '464': 'import_unit_value_index',
           '492': 'export_value_index', '495': 'export_quantity_index', '494': 'export_unit_value_index'},
    'FBS': {'511': 'population', '5511': 'production', '5611': 'import_quantity', '5911': 'export_quantity',
            '5072': 'stock_variation', '5301': 'domestic_supply_quantity', '5521': 'feed', '5527': 'seed',
            '5123': 'losses', '5131': 'processing', '5154': 'other_uses_nonfood', '5142': 'food',
            '5171': 'tourist_consumption', '5170': 'residuals', '664': 'food_supply_kcal_per_capita_day',
            '674': 'protein_supply_per_capita_day', '684': 'fat_supply_per_capita_day',
            '645': 'food_supply_per_capita_year', '661': 'food_supply_kcal', '671': 'protein_supply_quantity',
            '681': 'fat_supply_quantity'},
}
UNITS = {'An': 'head', '1000 An': '1000 head', 'No': 'number', '1000 No': '1000 number', 'kg/An': 'kg/head',
         'g/An': 'g/head', 'No/An': 'number/head', 'g/cap/d': 'g/capita/day', 'kg/cap': 'kg/capita/year',
         'kcal/cap/d': 'kcal/capita/day', 'million Kcal': 'million kcal', '1000 Int$': '1000 international_USD_2014_2016',
         '%': 'percent'}
PRICE_UNITS = {'5530': 'LCU/t', '5531': 'SLC/t', '5532': 'USD/t', '5539': 'index_2014_2016_100'}
# Value of production: element -> (unit, price basis)
QV_UNITS = {'152': ('1000 international_USD_2014_2016', 'constant_2014_2016'), '55': ('1000 SLC', 'constant_2014_2016'),
            '58': ('1000 USD', 'constant_2014_2016'), '56': ('1000 SLC', 'current'), '57': ('1000 USD', 'current')}
INDEX_DOMAINS = {'QI': 'index_2014_2016_100', 'TI': 'index_2014_2016_100'}


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def domain_of(locator):
    member = locator.split('/member:', 1)[1].split('/line:', 1)[0]
    stem = member.rsplit('/', 1)[-1].split('_E_All_Data', 1)[0]
    if stem not in DOMAINS:
        raise ValueError('Unrecognized FAOSTAT member: ' + member)
    return DOMAINS[stem]


class Areas:
    def __init__(self, out, m49_iso3):
        self.out, self.m49_iso3 = out, m49_iso3

    def resolve(self, code, m49, label, locator):
        """Return (entity id, aggregate flag, entity record or None)."""
        code, m49 = code.strip(), m49.strip().lstrip("'")
        aggregate = not code.isdigit() or int(code) >= 5000
        if not aggregate and m49 in self.m49_iso3 and code != '351':
            key = 'iso3:' + self.m49_iso3[m49]
            return key, False, self.out.entity(key, 'country', label, locator, fao_area_code=code, m49=m49)
        key = 'fao:area:' + code
        return key, aggregate or code == '351', self.out.entity(
            key, 'jurisdiction', label, locator, fao_area_code=code, m49=m49, aggregate=aggregate or code == '351',
            note='FAO aggregate or area without a current ISO 3166 code')


def run(context):
    params = context.parameters
    min_year = int(params.get('min_year', 2000))
    floors = {domain: int(year) for domain, year in params.get('min_year_by_domain', {}).items()}
    elements = {domain: set(codes) for domain, codes in params.get('elements', {}).items()}
    m49_iso3 = json.loads((Path(__file__).with_name('m49_iso3.json')).read_text())
    reader = {'format': 'csv', 'members': ['*_All_Data_(Normalized).csv'], 'encoding': 'utf-8-sig'}
    for index, _ in enumerate(context.raw_inputs):
        out = Evidence(context, 'fao', index)
        areas = Areas(out, m49_iso3)
        try:
            for locator, row in context.raw_rows(index, **reader):
                domain = domain_of(locator)
                element = (row.get('Element Code') or row.get('Indicator Code') or '').strip()
                if domain in elements and element not in elements[domain]:
                    continue
                year_text = row['Year'].strip()
                if not year_text.isdigit() or len(year_text) != 4:
                    continue  # multi-year averages (e.g. 2014-2016) are not period observations
                year = int(year_text)
                if year < floors.get(domain, min_year):
                    continue
                if domain == 'TM':
                    area_code, area, aggregate, record = row['Reporter Country Code'].strip(), *areas.resolve(
                        row['Reporter Country Code'], row['Reporter Country Code (M49)'], row['Reporter Countries'], locator)
                    if record:
                        yield record
                    partner_code = row['Partner Country Code'].strip()
                    partner, partner_aggregate, record = areas.resolve(
                        partner_code, row['Partner Country Code (M49)'], row['Partner Countries'], locator)
                    if record:
                        yield record
                else:
                    area_code = row['Area Code'].strip()
                    area, aggregate, record = areas.resolve(area_code, row['Area Code (M49)'], row['Area'], locator)
                    if record:
                        yield record
                item_code = row['Item Code'].strip()
                if domain == 'FBS':
                    item = 'fao:fbs_item:' + item_code
                    record = out.entity(item, 'commodity', row['Item'], locator, fao_fbs_item_code=item_code,
                                        fbs_code=row.get('Item Code (FBS)', '').lstrip("'"),
                                        item_group=item_code.startswith('29'))
                elif domain == 'CP':
                    item = 'fao:cpi_item:' + item_code
                    record = out.entity(item, 'economic_series', row['Item'], locator, fao_item_code=item_code)
                else:
                    item = 'fao:item:' + item_code
                    record = out.entity(item, 'commodity', row['Item'], locator, fao_item_code=item_code,
                                        cpc=row.get('Item Code (CPC)', '').lstrip("'"))
                if record:
                    yield record
                element_label = row.get('Element') or row.get('Indicator') or ''
                metric = METRICS.get(domain, {}).get(element) or slug(element_label)
                unit_text = row['Unit'].strip()
                dims = {'item': item, 'domain': domain}
                if domain == 'PP':
                    unit = PRICE_UNITS.get(element, unit_text)
                elif domain == 'QV':
                    unit, basis = QV_UNITS.get(element, (UNITS.get(unit_text, unit_text), 'unspecified'))
                    dims['price_basis'] = basis
                elif domain in INDEX_DOMAINS:
                    unit = INDEX_DOMAINS[domain]
                elif domain == 'CP':
                    unit = 'percent' if element == '6121' else 'index_2015_100'
                elif domain == 'TCLI':
                    unit = UNITS.get(unit_text, unit_text) or 'ratio'
                else:
                    unit = UNITS.get(unit_text, unit_text) or 'index'
                months = row.get('Months', 'Annual value').strip() if domain in ('PP', 'CP') else 'Annual value'
                if months == 'Annual value':
                    start, end = year_bounds(year)
                    frequency, suffix = 'annual', ''
                elif months in MONTHS:
                    start, end = month_bounds(year, MONTHS[months])
                    frequency, suffix = 'monthly', f':{MONTHS[months]:02d}'
                else:
                    continue
                dims['frequency'] = frequency
                value = num(row['Value'])
                flag = row.get('Flag', '').strip()
                subject = area
                if domain == 'FBS' and element == '511':
                    dims = {'domain': domain, 'frequency': frequency}
                identity = f'{domain}:{area_code}:{item_code}:{element}:{year}{suffix}'
                if domain == 'TM':
                    dims['partner'] = partner
                    dims['element'] = element
                    identity = f'TM:{area_code}:{partner_code}:{item_code}:{element}:{year}'
                    aggregate = aggregate or partner_aggregate
                attrs = {'element_code': element, 'flag': flag}
                if row.get('Note'):
                    attrs['note'] = row['Note'][:300]
                yield out.observation(subject, metric, value, unit, locator, valid_from=start, valid_to=end,
                                      identity=identity, dimensions=dims, aggregate=True if aggregate else None,
                                      missing_reason='source_missing' + (f'_flag_{flag}' if flag else ''), **attrs)
        finally:
            out.close()
