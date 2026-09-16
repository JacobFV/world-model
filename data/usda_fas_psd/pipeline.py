"""USDA FAS PSD balance sheets -> country x commodity x marketing-year observations.

Streams ``psd_alldata.csv`` inside the ZIP shard. One observation per PSD attribute
value (the latest estimate in the file). Countries map to ``iso3:XXX`` where a current
ISO 3166 code exists; historical units and aggregates keep ``usda_psd:country:<code>``.
"""
import re

from .evidence import Evidence, num, year_bounds

# PSD (FIPS 10-4 style) country codes -> ISO 3166-1 alpha-3. Absent codes are
# historical states or aggregates and stay in the usda_psd namespace.
ISO3 = dict(pair.split('=') for pair in '''
AC=ATG AF=AFG AG=DZA AJ=AZE AL=ALB AM=ARM AO=AGO AR=ARG AS=AUS AU=AUT BA=BHR BB=BRB BC=BWA BD=BMU BF=BHS
BG=BGD BH=BLZ BK=BIH BL=BOL BM=MMR BO=BLR BP=SLB BR=BRA BT=BTN BU=BGR BX=BRN BY=BDI CA=CAN CB=KHM CD=TCD
CE=LKA CF=COG CG=COD CH=CHN CI=CHL CM=CMR CN=COM CO=COL CS=CRI CT=CAF CU=CUB CV=CPV CY=CYP DA=DNK DJ=DJI
DM=BEN DR=DOM EC=ECU EG=EGY EI=IRL EK=GNQ EN=EST ER=ERI ES=SLV ET=ETH EZ=CZE FI=FIN FJ=FJI FO=FRO FP=PYF
FR=FRA GA=GMB GB=GAB GG=GEO GH=GHA GI=GIB GJ=GRD GL=GRL GM=DEU GP=GLP GR=GRC GT=GTM GU=GIN GY=GUY HA=HTI
HK=HKG HO=HND HR=HRV HU=HUN IC=ISL ID=IDN IN=IND IR=IRN IS=ISR IT=ITA IV=CIV IZ=IRQ JA=JPN JM=JAM JO=JOR
KE=KEN KG=KGZ KN=PRK KS=KOR KU=KWT KZ=KAZ LA=LAO LE=LBN LG=LVA LH=LTU LI=LBR LO=SVK LT=LSO LU=LUX LY=LBY
MA=MDG MB=MTQ MC=MAC MD=MDA MG=MNG MI=MWI MJ=MNE MK=MKD ML=MLI MO=MAR MP=MUS MR=MRT MT=MLT MU=OMN MV=MDV
MX=MEX MY=MYS MZ=MOZ NC=NCL NG=NER NH=VUT NI=NGA NL=NLD NO=NOR NP=NPL NS=SUR NU=NIC NZ=NZL OD=SSD PA=PRY
PE=PER PK=PAK PL=POL PN=PAN PO=PRT PP=PNG PU=GNB QA=QAT RB=SRB RE=REU RH=ZWE RO=ROU RP=PHL RQ=PRI RS=RUS
RW=RWA S8=BEL SA=SAU SC=KNA SE=SYC SF=ZAF SG=SEN SI=SVN SL=SLE SN=SGP SO=SOM SP=ESP ST=LCA SU=SDN SW=SWE
SY=SYR SZ=CHE TC=ARE TD=TTO TH=THA TI=TJK TN=TON TO=TGO TP=STP TS=TUN TU=TUR TW=TWN TX=TKM TZ=TZA UG=UGA
UK=GBR UP=UKR US=USA UV=BFA UY=URY UZ=UZB VE=VEN VM=VNM VO=VIR WA=NAM WS=WSM WZ=SWZ YM=YEM ZA=ZMB
'''.split())
AGGREGATES = {'E2', 'E3', 'E4', 'ZZ', 'BE', 'Y2'}  # EU-15/EU-25/EU, Other, Belgium-Luxembourg, French West Indies

# Attribute description -> tidy metric name (fallback: slug of the description).
METRICS = {
    'Area Harvested': 'area_harvested', 'Beginning Stocks': 'beginning_stocks', 'Production': 'production',
    'Imports': 'imports', 'Exports': 'exports', 'Total Supply': 'total_supply', 'Total Distribution': 'total_distribution',
    'Domestic Consumption': 'domestic_consumption', 'Ending Stocks': 'ending_stocks', 'Yield': 'yield',
    'TY Imports': 'trade_year_imports', 'TY Exports': 'trade_year_exports', 'TY Imp. from U.S.': 'trade_year_imports_from_us',
    'Feed Dom. Consumption': 'feed_domestic_consumption', 'FSI Consumption': 'food_seed_industrial_consumption',
    'Food Use Dom. Cons.': 'food_use_domestic_consumption', 'Feed Waste Dom. Cons.': 'feed_waste_domestic_consumption',
    'Industrial Dom. Cons.': 'industrial_domestic_consumption', 'Crush': 'crush', 'Extr. Rate, 999.9999': 'extraction_rate',
    'Milling Rate (.9999)': 'milling_rate', 'Seed to Lint Ratio': 'seed_to_lint_ratio', 'Stocks-to-Use': 'stocks_to_use',
    'Refined Imp.(Raw Val)': 'refined_imports_raw_value', 'Refined Exp.(Raw Val)': 'refined_exports_raw_value',
    'Rst,Ground Dom. Consum': 'roast_ground_domestic_consumption', 'Fluid Use Dom. Consum.': 'fluid_use_domestic_consumption',
    'Feed Use Dom. Consum.': 'feed_use_domestic_consumption', 'Fresh Dom. Consumption': 'fresh_domestic_consumption',
    'Human Dom. Consumption': 'human_domestic_consumption', 'Factory Use Consum.': 'factory_use_consumption',
    'Soluble Dom. Cons.': 'soluble_domestic_consumption', 'Non-Comm. Production': 'noncommercial_production',
    'Dairy Cows Beg. Stocks': 'dairy_cows_beginning_stocks', 'Beef Cows Beg. Stocks': 'beef_cows_beginning_stocks',
    'Sow Beginning Stocks': 'sow_beginning_stocks', 'Annual % Change Per Cap. Cons.': 'per_capita_consumption_annual_change',
    'SME': 'soybean_meal_equivalent', 'Loss and Residual': 'loss_and_residual',
}
UNITS = {'(1000 MT)': '1000 t', '(MT)': 't', '(1000 60 KG BAGS)': '1000 60-kg bags', '1000 480 lb. Bales': '1000 480-lb bales',
         '(1000 HA)': '1000 ha', '(1000 HEAD)': '1000 head', '(MT/HA)': 't/ha', '(1000 MT CWE)': '1000 t carcass weight equivalent',
         '(PERCENT)': 'percent', '(KG/HA)': 'kg/ha', '(RATIO)': 'ratio'}


def slug(text):
    return re.sub(r'[^a-z0-9]+', '_', text.lower()).strip('_')


def run(context):
    min_year = int(context.parameters.get('min_year', 2000))
    reader = {'format': 'csv', 'members': ['*.csv'], 'encoding': 'utf-8-sig'}
    for index, _ in enumerate(context.raw_inputs):
        out = Evidence(context, 'psd', index)
        try:
            for locator, row in context.raw_rows(index, **reader):
                year = int(row['Market_Year'])
                if year < min_year:
                    continue
                code, commodity = row['Country_Code'].strip(), row['Commodity_Code'].strip()
                if code in ISO3:
                    country = 'iso3:' + ISO3[code]
                    record = out.entity(country, 'country', row['Country_Name'], locator, usda_psd_country_code=code)
                else:
                    country = 'usda_psd:country:' + code
                    record = out.entity(country, 'jurisdiction', row['Country_Name'], locator, usda_psd_country_code=code,
                                        aggregate=code in AGGREGATES, historical_or_nonstandard=code not in AGGREGATES)
                if record:
                    yield record
                commodity_key = 'usda_psd:commodity:' + commodity
                record = out.entity(commodity_key, 'commodity', row['Commodity_Description'], locator,
                                    usda_psd_commodity_code=commodity)
                if record:
                    yield record
                description = row['Attribute_Description'].strip()
                metric = METRICS.get(description) or slug(description)
                unit = UNITS.get(row['Unit_Description'].strip()) or row['Unit_Description'].strip().strip('()').lower()
                value = num(row['Value'])
                start, end = year_bounds(year)
                month = row['Month'].strip()
                as_of = row['Calendar_Year'] + ('-' + month if month not in ('', '00') else '')
                yield out.observation(
                    country, metric, value, unit, locator, valid_from=start, valid_to=end,
                    identity=f'{code}:{commodity}:{year}:{row["Attribute_ID"]}',
                    dimensions={'commodity': commodity_key, 'marketing_year': year, 'frequency': 'annual'},
                    missing_reason='source_blank', attribute_id=row['Attribute_ID'], estimate_as_of=as_of,
                    period_basis='local marketing year labelled by its starting year; dates are calendar-year approximations',
                    aggregate=code in AGGREGATES or None)
        finally:
            out.close()
