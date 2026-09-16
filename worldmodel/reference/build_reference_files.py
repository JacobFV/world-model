"""Regenerate the small tracked reference tables from official downloads.

Run explicitly (never imported by runtime code):

    python3 -m worldmodel.reference.build_reference_files --source-dir DIR [--download]

``--download`` fetches the files listed in ``SOURCES`` into DIR first. Without it the
builder is offline and only reads DIR. Output CSVs are written next to this file and
``manifest.json`` records source URLs, licences, input hashes and row counts.
Hand-curated tables (historical ISO codes, county change events and COW/GW↔ISO links)
are declared in this file so every row has a reviewable origin.
"""
import argparse
import csv
import hashlib
import io
import json
import re
import unicodedata
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent

SOURCES = {
    'country-codes.csv': ('https://raw.githubusercontent.com/datasets/country-codes/main/data/country-codes.csv',
                          'ODC-PDDL-1.0 (datahub.io core dataset compiling ISO 3166 / UN M49 codes)'),
    'currency-codes.csv': ('https://raw.githubusercontent.com/datasets/currency-codes/main/data/codes-all.csv',
                           'ODC-PDDL-1.0 (datahub.io core dataset compiling ISO 4217 lists)'),
    'states2016.csv': ('https://correlatesofwar.org/wp-content/uploads/states2016.csv',
                       'Correlates of War Project State System Membership v2016; free use with citation'),
    'iisystem.dat': ('http://ksgleditsch.com/data/iisystem.dat',
                     'Gleditsch & Ward (1999) independent states list; academic use with citation'),
    'microstatessystem.dat': ('http://ksgleditsch.com/data/microstatessystem.dat',
                              'Gleditsch & Ward microstates list; academic use with citation'),
    'wb_countries.json': ('https://api.worldbank.org/v2/country?format=json&per_page=400',
                          'World Bank API country metadata; CC BY 4.0'),
    'state.txt': ('https://www2.census.gov/geo/docs/reference/state.txt', 'US Census Bureau; public domain (17 U.S.C. 105)'),
    'national_county2020.txt': ('https://www2.census.gov/geo/docs/reference/codes2020/national_county2020.txt',
                                'US Census Bureau; public domain'),
    'gaz_counties_2023.zip': ('https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_counties_national.zip',
                              'US Census Bureau; public domain'),
    'ct_crosswalk.xlsx': ('https://www2.census.gov/geo/docs/reference/ct_change/ct_cou_to_cousub_crosswalk.xlsx',
                          'US Census Bureau; public domain'),
    'naics12_17.xlsx': ('https://www.census.gov/naics/concordances/2012_to_2017_NAICS.xlsx', 'US Census Bureau; public domain'),
    'naics17_22.xlsx': ('https://www.census.gov/naics/concordances/2017_to_2022_NAICS.xlsx', 'US Census Bureau; public domain'),
    'naics2022_2_6.xlsx': ('https://www.census.gov/naics/2022NAICS/2-6%20digit_2022_Codes.xlsx', 'US Census Bureau; public domain'),
}

# ISO 3166-3 formerly used codes (subset relevant to post-1970 data). Dates are the
# ISO 3166 Maintenance Agency change dates as commonly published; political dates
# live in the COW/GW tables. Reused alpha-2 "CS" is deliberate: lookups must be dated.
ISO_HISTORICAL = [
    # iso2, iso3, numeric, name, valid_from, valid_to, successor note
    ('SU', 'SUN', '810', 'USSR', '', '1992-08-30', 'split; RU/UA/BY/... ISO codes'),
    ('YU', 'YUG', '891', 'Yugoslavia', '', '2003-07-23', 'renamed Serbia and Montenegro (CS/SCG)'),
    ('CS', 'SCG', '891', 'Serbia and Montenegro', '2003-07-23', '2006-09-26', 'split into RS/SRB and ME/MNE'),
    ('CS', 'CSK', '200', 'Czechoslovakia', '', '1993-06-15', 'split into CZ/CZE and SK/SVK'),
    ('DD', 'DDR', '278', 'German Democratic Republic', '', '1990-10-30', 'merged into DE/DEU'),
    ('ZR', 'ZAR', '180', 'Zaire', '', '1997-07-14', 'renamed Congo, Democratic Republic (CD/COD)'),
    ('TP', 'TMP', '626', 'East Timor', '', '2002-05-20', 'renamed Timor-Leste (TL/TLS)'),
    ('AN', 'ANT', '530', 'Netherlands Antilles', '', '2010-12-15', 'dissolved into CW, SX, BQ'),
    ('BU', 'BUR', '104', 'Burma', '', '1989-12-05', 'renamed Myanmar (MM/MMR)'),
    ('YD', 'YMD', '720', "Yemen, Democratic", '', '1990-08-14', 'merged into YE/YEM'),
    ('VD', 'VDR', '868', 'Viet-Nam, Democratic Republic of', '', '1977-01-01', 'merged into VN/VNM'),
]
# Current codes whose assignment date matters for dated lookups (ISO change dates).
ISO_VALID_FROM = {'SRB': '2006-09-26', 'MNE': '2006-09-26', 'SSD': '2011-08-09', 'TLS': '2002-05-20',
                  'CZE': '1993-06-15', 'SVK': '1993-06-15', 'CUW': '2010-12-15', 'SXM': '2010-12-15',
                  'BES': '2010-12-15', 'COD': '1997-07-14', 'MMR': '1989-12-05'}

# COW / GW code periods linked to ISO alpha-3 where names do not match or history
# matters. (system, code, valid_from, valid_to, iso3, basis). Empty iso3 = no ISO code.
LINK_OVERRIDES = [
    ('cow', '2', None, None, 'USA', 'name'), ('cow', '200', None, None, 'GBR', 'name'),
    ('cow', '210', None, None, 'NLD', 'name'), ('cow', '225', None, None, 'CHE', 'name'),
    ('cow', '255', None, '1945-05-09', 'DEU', 'territorial_continuity_pre_iso'),
    ('cow', '255', '1990-10-03', None, 'DEU', 'contemporaneous'),
    ('cow', '260', None, '1990-10-03', 'DEU', 'contemporaneous_iso_deu_denoted_frg'),
    ('cow', '265', None, '1990-10-03', 'DDR', 'contemporaneous'),
    ('cow', '300', None, None, '', 'no_iso_code'), ('cow', '305', None, None, 'AUT', 'name'),
    ('cow', '315', None, '1993-01-01', 'CSK', 'contemporaneous'), ('cow', '316', None, None, 'CZE', 'name'),
    ('cow', '317', None, None, 'SVK', 'name'), ('cow', '343', None, None, 'MKD', 'name'),
    ('cow', '345', None, '2003-02-04', 'YUG', 'contemporaneous'),
    ('cow', '345', '2003-02-04', '2006-06-05', 'SCG', 'contemporaneous'),
    ('cow', '345', '2006-06-05', None, 'SRB', 'cow_code_continues_for_serbia'),
    ('cow', '347', None, None, 'XKX', 'user_assigned_code_not_iso'),
    ('cow', '365', '1922-12-30', '1991-12-26', 'SUN', 'contemporaneous'),
    ('cow', '365', None, '1922-12-30', 'RUS', 'territorial_continuity_pre_iso'),
    ('cow', '365', '1991-12-26', None, 'RUS', 'contemporaneous'),
    ('cow', '437', None, None, 'CIV', 'name'), ('cow', '484', None, None, 'COG', 'name'),
    ('cow', '490', None, None, 'COD', 'name'), ('cow', '572', None, None, 'SWZ', 'name'),
    ('cow', '678', None, None, 'YEM', 'contemporaneous_iso_yem_denoted_north_yemen'),
    ('cow', '679', None, None, 'YEM', 'contemporaneous'), ('cow', '680', None, None, 'YMD', 'contemporaneous'),
    ('cow', '713', None, None, 'TWN', 'name'), ('cow', '730', None, None, '', 'no_iso_code'),
    ('cow', '731', None, None, 'PRK', 'name'), ('cow', '732', None, None, 'KOR', 'name'),
    ('cow', '775', None, '1989-06-18', 'BUR', 'contemporaneous'), ('cow', '775', '1989-06-18', None, 'MMR', 'contemporaneous'),
    ('cow', '816', None, '1976-07-02', 'VDR', 'contemporaneous'), ('cow', '816', '1976-07-02', None, 'VNM', 'contemporaneous'),
    ('cow', '817', None, None, '', 'no_unambiguous_iso_code'),
    ('cow', '860', None, None, 'TLS', 'name'), ('cow', '771', None, None, 'BGD', 'name'),
    ('gw', '2', None, None, 'USA', 'name'), ('gw', '200', None, None, 'GBR', 'name'),
    ('gw', '210', None, None, 'NLD', 'name'), ('gw', '225', None, None, 'CHE', 'name'),
    ('gw', '255', None, '1945-05-09', 'DEU', 'territorial_continuity_pre_iso'),
    ('gw', '260', None, None, 'DEU', 'gw_260_continues_as_unified_germany'),
    ('gw', '265', None, None, 'DDR', 'contemporaneous'), ('gw', '300', None, None, '', 'no_iso_code'),
    ('gw', '305', None, None, 'AUT', 'name'), ('gw', '315', None, '1993-01-01', 'CSK', 'contemporaneous'),
    ('gw', '316', None, None, 'CZE', 'name'), ('gw', '317', None, None, 'SVK', 'name'),
    ('gw', '340', '2006-06-05', None, 'SRB', 'contemporaneous'), ('gw', '340', None, '1915-10-02', '', 'no_iso_code'),
    ('gw', '343', None, None, 'MKD', 'name'),
    ('gw', '345', None, '2003-02-04', 'YUG', 'contemporaneous'), ('gw', '345', '2003-02-04', None, 'SCG', 'contemporaneous'),
    ('gw', '347', None, None, 'XKX', 'user_assigned_code_not_iso'),
    ('gw', '365', '1922-12-30', '1991-12-26', 'SUN', 'contemporaneous'),
    ('gw', '365', None, '1922-12-30', 'RUS', 'territorial_continuity_pre_iso'),
    ('gw', '365', '1991-12-26', None, 'RUS', 'contemporaneous'),
    ('gw', '437', None, None, 'CIV', 'name'), ('gw', '484', None, None, 'COG', 'name'),
    ('gw', '490', None, None, 'COD', 'name'), ('gw', '572', None, None, 'SWZ', 'name'),
    ('gw', '678', None, None, 'YEM', 'contemporaneous_iso_yem_denoted_north_yemen'),
    ('gw', '680', None, None, 'YMD', 'contemporaneous'),
    ('gw', '713', None, None, 'TWN', 'name'), ('gw', '730', None, None, '', 'no_iso_code'),
    ('gw', '731', None, None, 'PRK', 'name'), ('gw', '732', None, None, 'KOR', 'name'),
    ('gw', '775', None, '1989-06-18', 'BUR', 'contemporaneous'), ('gw', '775', '1989-06-18', None, 'MMR', 'contemporaneous'),
    ('gw', '816', None, '1976-07-02', 'VDR', 'contemporaneous'), ('gw', '816', '1976-07-02', None, 'VNM', 'contemporaneous'),
    ('gw', '817', None, None, '', 'no_unambiguous_iso_code'), ('gw', '815', None, None, '', 'no_iso_code'),
    ('gw', '860', None, None, 'TLS', 'name'), ('gw', '771', None, None, 'BGD', 'name'),
    ('gw', '711', None, None, '', 'no_iso_code'), ('gw', '89', None, None, '', 'no_iso_code'),
    ('gw', '99', None, None, '', 'no_iso_code'), ('gw', '563', None, None, '', 'no_iso_code'),
    ('gw', '564', None, None, '', 'no_iso_code'),
]
NO_ISO_NAMES = {'hanover', 'bavaria', 'baden', 'saxony', 'wuerttemburg', 'wurttemberg', 'hesse electoral', 'hesse grand ducal',
                'mecklenburg schwerin', 'papal states', 'two sicilies', 'modena', 'parma', 'tuscany', 'zanzibar',
                'abkhazia', 'south ossetia', 'orange free state', 'transvaal', 'tibet', 'korea'}
ALIASES = {'united states of america': 'USA', 'bahamas': 'BHS', 'st lucia': 'LCA', 'saint lucia': 'LCA',
           'st vincent and the grenadines': 'VCT', 'st kitts and nevis': 'KNA', 'antigua barbuda': 'ATG',
           'venezuela': 'VEN', 'bolivia': 'BOL', 'united kingdom': 'GBR', 'moldova': 'MDA', 'russia': 'RUS',
           'cape verde': 'CPV', 'sao tome and principe': 'STP', 'gambia': 'GMB', 'ivory coast': 'CIV',
           'cote divoire': 'CIV', 'tanzania': 'TZA', 'swaziland': 'SWZ', 'libya': 'LBY', 'iran': 'IRN',
           'turkey': 'TUR', 'syria': 'SYR', 'laos': 'LAO', 'brunei': 'BRN', 'vietnam': 'VNM',
           'federated states of micronesia': 'FSM', 'samoa western samoa': 'WSM', 'macedonia': 'MKD',
           'czech republic': 'CZE', 'bosnia and herzegovina': 'BIH', 'kyrgyzstan': 'KGZ', 'korea south': 'KOR',
           'korea north': 'PRK', 'burma myanmar': 'MMR', 'myanmar burma': 'MMR', 'cambodia kampuchea': 'KHM',
           'kampuchea': 'KHM', 'surinam': 'SUR', 'belarus byelorussia': 'BLR', 'zimbabwe rhodesia': 'ZWE',
           'madagascar malagasy': 'MDG', 'yemen north yemen': 'YEM', 'east timor': 'TLS', 'timor leste': 'TLS',
           'sri lanka ceylon': 'LKA', 'burkina faso upper volta': 'BFA', 'benin': 'BEN', 'congo': 'COG',
           'democratic republic of the congo': 'COD', 'rumania': 'ROU', 'romania': 'ROU', 'tonga': 'TON',
           'kiribati': 'KIR', 'tuvalu': 'TUV', 'nauru': 'NRU', 'marshall islands': 'MHL', 'palau': 'PLW',
           'eswatini': 'SWZ', 'north macedonia': 'MKD', 'united states': 'USA',
           'st vincent and grenadines': 'VCT', 'italy sardinia': 'ITA', 'bosnia herzegovina': 'BIH',
           'tanzania tanganyika': 'TZA', 'iran persia': 'IRN', 'turkey ottoman empire': 'TUR', 'kyrgyz republic': 'KGZ',
           'hesse kassel electoral': '', 'hesse darmstadt ducal': ''}


def _norm(name):
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode().lower()
    name = re.sub(r"\([^)]*\)", lambda m: ' ' + m.group(0)[1:-1] + ' ', name)
    name = re.sub(r"['’.]", '', name)
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', name).replace(' the ', ' ').split())


def _xlsx_rows(path, sheet=1):
    ns = {'m': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    archive = zipfile.ZipFile(path)
    strings = []
    if 'xl/sharedStrings.xml' in archive.namelist():
        for item in ET.fromstring(archive.read('xl/sharedStrings.xml')).findall('m:si', ns):
            strings.append(''.join(t.text or '' for t in item.iter('{%s}t' % ns['m'])))
    root = ET.fromstring(archive.read(f'xl/worksheets/sheet{sheet}.xml'))
    for row in root.iter('{%s}row' % ns['m']):
        out = {}
        for cell in row.findall('m:c', ns):
            column = re.match(r'[A-Z]+', cell.get('r')).group()
            value = cell.find('m:v', ns)
            kind = cell.get('t')
            if kind == 's' and value is not None:
                out[column] = strings[int(value.text)]
            elif kind == 'inlineStr':
                out[column] = ''.join(x.text or '' for x in cell.iter('{%s}t' % ns['m']))
            else:
                out[column] = value.text if value is not None else ''
        yield out


def _write(name, header, rows):
    path = HERE / name
    with path.open('w', encoding='utf-8', newline='') as stream:
        writer = csv.writer(stream, lineterminator='\n')
        writer.writerow(header)
        writer.writerows(rows)
    return {'file': name, 'rows': len(rows), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def _dmy(value):
    day, month, year = value.split(':')
    return date(int(year), int(month), int(day)).isoformat()


def build(source_dir, *, report=print):
    src = Path(source_dir)
    outputs = []
    # --- ISO 3166 / M49 ---
    iso = list(csv.DictReader(io.StringIO((src/'country-codes.csv').read_text(encoding='utf-8'))))
    wb = {x['id']: x for x in json.loads((src/'wb_countries.json').read_text(encoding='utf-8'))[1]}
    rows, by_name = [], {}
    for r in iso:
        iso3 = r['ISO3166-1-Alpha-3'].strip()
        if not iso3:
            continue
        name = r['CLDR display name'] or r['official_name_en']
        rows.append([r['ISO3166-1-Alpha-2'].strip(), iso3, r['ISO3166-1-numeric'].strip().zfill(3),
                     r['M49'].strip().zfill(3) if r['M49'].strip() else '', name,
                     r['is_independent'].strip(), r['ISO4217-currency_alphabetic_code'].strip(),
                     iso3 if iso3 in wb else '', ISO_VALID_FROM.get(iso3, ''), '', 'current'])
        for key in (name, r['official_name_en'], r['UNTERM English Short']):
            if key:
                by_name.setdefault(_norm(key), iso3)
    for iso2, iso3, numeric, name, start, end, note in ISO_HISTORICAL:
        rows.append([iso2, iso3, numeric, numeric, name, 'Historical', '', '', start, end, 'formerly_used: ' + note])
    kosovo = wb.get('XKX')
    if kosovo:
        rows.append(['XK', 'XKX', '', '', 'Kosovo', 'Yes', 'EUR', 'XKX', '2008-02-17', '',
                     'user_assigned_code_not_iso; used by World Bank/IMF/EU'])
    rows.sort(key=lambda x: (x[1], x[8]))
    outputs.append(_write('countries_iso3166.csv', ['iso2', 'iso3', 'iso_numeric', 'm49', 'name', 'independent',
                                                    'currency', 'world_bank', 'valid_from', 'valid_to', 'status'], rows))
    wb_rows = [[x['id'], x['iso2Code'], x['name'].strip(), x['region']['value'].strip(), x['incomeLevel']['value'].strip(),
                'aggregate' if x['region']['value'].strip() == 'Aggregates' else 'economy'] for x in wb.values()]
    outputs.append(_write('worldbank_economies.csv', ['wb_code', 'wb_iso2', 'name', 'region', 'income_level', 'kind'],
                          sorted(wb_rows)))
    # --- COW and GW state systems ---
    cow = [[r['ccode'], r['stateabb'], r['statenme'],
            date(int(r['styear']), int(r['stmonth']), int(r['stday'])).isoformat(),
            '' if (r['endyear'], r['endmonth'], r['endday']) == ('2016', '12', '31')
            else date(int(r['endyear']), int(r['endmonth']), int(r['endday'])).isoformat()]
           for r in csv.DictReader(io.StringIO((src/'states2016.csv').read_text(encoding='utf-8')))]
    gw = []
    for filename, micro in (('iisystem.dat', ''), ('microstatessystem.dat', 'microstate')):
        for line in (src/filename).read_text(encoding='latin-1').splitlines():
            parts = line.split('\t')
            if len(parts) >= 5 and parts[0].strip().isdigit():
                end = _dmy(parts[4].strip())
                gw.append([parts[0].strip(), parts[1].strip(), parts[2].strip().replace('S�o Tom�', 'Sao Tome'),
                           _dmy(parts[3].strip()), '' if end == '2017-12-31' else end, micro])
    outputs.append(_write('country_states_cow.csv', ['ccode', 'abbrev', 'name', 'valid_from', 'valid_to'], cow))
    outputs.append(_write('country_states_gw.csv', ['gwcode', 'abbrev', 'name', 'valid_from', 'valid_to', 'list'], gw))
    overrides = {}
    for system, code, start, end, iso3, basis in LINK_OVERRIDES:
        overrides.setdefault((system, code), []).append((start or '', end or '', iso3, basis))
    links, unmatched = [], []
    for system, table in (('cow', cow), ('gw', gw)):
        seen = set()
        for row in table:
            code, name = row[0], row[2]
            if (system, code) in seen:
                continue
            seen.add((system, code))
            if (system, code) in overrides:
                links.extend([system, code, s, e, i, b] for s, e, i, b in overrides[(system, code)])
                continue
            key = _norm(name)
            iso3 = ALIASES[key] if key in ALIASES else by_name.get(key)
            if iso3 == '':
                links.append([system, code, '', '', '', 'no_iso_code'])
            elif iso3:
                links.append([system, code, '', '', iso3, 'name'])
            elif key in NO_ISO_NAMES:
                links.append([system, code, '', '', '', 'no_iso_code'])
            else:
                unmatched.append((system, code, name))
    if unmatched:
        raise ValueError('Unmatched state-system names; add LINK_OVERRIDES or ALIASES: ' + repr(unmatched))
    outputs.append(_write('country_system_links.csv', ['system', 'code', 'valid_from', 'valid_to', 'iso3', 'basis'], links))
    # --- ISO 4217 ---
    currencies = {}
    for r in csv.DictReader(io.StringIO((src/'currency-codes.csv').read_text(encoding='utf-8'))):
        code = r['AlphabeticCode'].strip()
        if not code:
            continue
        item = currencies.setdefault((code, r['WithdrawalDate'].strip()),
                                     [code, r['NumericCode'].strip().split('.')[0].zfill(3) if r['NumericCode'].strip() else '',
                                      r['Currency'].strip(), r['MinorUnit'].strip(), r['WithdrawalDate'].strip(), set()])
        item[5].add(r['Entity'].strip())
    rows = [[c, n, name, minor, withdrawn, '; '.join(sorted(entities))]
            for c, n, name, minor, withdrawn, entities in currencies.values()]
    outputs.append(_write('currencies_iso4217.csv', ['code', 'numeric', 'name', 'minor_unit', 'withdrawn', 'entities'], sorted(rows)))
    # --- US states and counties ---
    lines = (src/'state.txt').read_text(encoding='utf-8').splitlines()
    outputs.append(_write('us_states.csv', ['fips', 'usps', 'name', 'gnis'], [l.split('|') for l in lines[1:] if l.strip()]))
    counties = {}
    for line in (src/'national_county2020.txt').read_text(encoding='utf-8').splitlines()[1:]:
        usps, statefp, countyfp, _, name, classfp, _ = line.split('|')
        counties[statefp + countyfp] = [statefp + countyfp, usps, name, classfp, '', '', '2020']
    gazetteer = zipfile.ZipFile(src/'gaz_counties_2023.zip')
    text = gazetteer.read(gazetteer.namelist()[0]).decode('utf-8')
    current = set()
    for line in text.splitlines()[1:]:
        parts = [p.strip() for p in line.split('\t')]
        if len(parts) < 4:
            continue
        current.add(parts[1])
        if parts[1] not in counties:
            counties[parts[1]] = [parts[1], parts[0], parts[3], '', '2022-06-06', '', '2023']
    for key, row in counties.items():
        if key[:2] == '09' and key not in current and row[6] == '2020':
            row[5] = '2022-06-06'
    for old, new, effective in (('02261', '', '2019-01-02'), ('02270', '', '2015-07-01'), ('46113', '', '2015-05-01'),
                                ('51515', '', '2013-07-01'), ('02232', '', '2007-06-20'), ('02280', '', '2008-06-01'),
                                ('02201', '', '2008-05-19'), ('51560', '', '2001-07-01'), ('12025', '', '1997-07-22'),
                                ('02231', '', '1992-09-22'), ('30113', '', '1997-11-07'), ('51780', '', '1995-06-30')):
        counties.setdefault(old, [old, '', '', '', '', '', 'census_county_changes'])[5] = effective
    starts = {'02063': '2019-01-02', '02066': '2019-01-02', '02158': '2015-07-01', '46102': '2015-05-01',
              '02195': '2008-06-01', '02275': '2008-06-01', '02198': '2008-05-19', '02230': '2007-06-20',
              '02105': '2007-06-20', '08014': '2001-11-15', '12086': '1997-07-22', '02068': '1990-12-07',
              '02282': '1992-09-22'}
    for key, start in starts.items():
        counties[key][4] = start
    names = {'02261': ('AK', 'Valdez-Cordova Census Area'), '02270': ('AK', 'Wade Hampton Census Area'),
             '46113': ('SD', 'Shannon County'), '51515': ('VA', 'Bedford city'), '02232': ('AK', 'Skagway-Hoonah-Angoon Census Area'),
             '02280': ('AK', 'Wrangell-Petersburg Census Area'), '02201': ('AK', 'Prince of Wales-Outer Ketchikan Census Area'),
             '51560': ('VA', 'Clifton Forge city'), '12025': ('FL', 'Dade County'), '02231': ('AK', 'Skagway-Yakutat-Angoon Census Area'),
             '30113': ('MT', 'Yellowstone National Park'), '51780': ('VA', 'South Boston city')}
    for key, (usps, name) in names.items():
        if not counties[key][2]:
            counties[key][1:3] = [usps, name]
    outputs.append(_write('us_counties.csv', ['geoid', 'usps', 'name', 'classfp', 'valid_from', 'valid_to', 'listed_in'],
                          sorted(counties.values())))
    outputs.append(_write('us_county_changes.csv', ['effective', 'event', 'from_geoid', 'to_geoid', 'population', 'population_basis', 'note'],
                          COUNTY_CHANGES))
    ct = list(_xlsx_rows(src/'ct_crosswalk.xlsx'))
    ct_rows = [[r['A'] + r['B'], r['C'], r['A'] + r['D'], r['E'], r['G'], r['H'], r['I']] for r in ct[1:]
               if re.fullmatch(r'[0-9]{2}', r.get('A', '')) and re.fullmatch(r'[0-9]{3}', r.get('B', ''))]
    outputs.append(_write('ct_county_planning_region_cousub.csv', ['old_county', 'old_county_name', 'new_county', 'new_county_name',
                                                                  'old_cousub_geoid', 'new_cousub_geoid', 'cousub_name'], ct_rows))
    # --- NAICS ---
    codes = [[r['B'].strip(), r['C'].strip()] for r in _xlsx_rows(src/'naics2022_2_6.xlsx')
             if r.get('B', '').strip() and re.fullmatch(r'[0-9]{2,6}|[0-9]{2}-[0-9]{2}', r['B'].strip())]
    outputs.append(_write('naics_2022_codes.csv', ['code', 'title'], codes))
    for filename, output in (('naics12_17.xlsx', 'naics_2012_2017.csv'), ('naics17_22.xlsx', 'naics_2017_2022.csv')):
        pairs = [[r['A'].strip(), r['C'].strip(), ' '.join(r.get('B', '').split())] for r in _xlsx_rows(src/filename)
                 if re.fullmatch(r'[0-9]{6}', r.get('A', '').strip()) and re.fullmatch(r'[0-9]{6}', r.get('C', '').strip())]
        outputs.append(_write(output, ['source', 'target', 'source_piece_title'], pairs))
    manifest = {'generated_by': 'worldmodel.reference.build_reference_files',
                'sources': {name: {'url': url, 'licence': licence,
                                   'sha256': hashlib.sha256((src/name).read_bytes()).hexdigest() if (src/name).exists() else None}
                            for name, (url, licence) in SOURCES.items()},
                'hand_curated': {'ISO_HISTORICAL': 'ISO 3166-3 formerly used codes (subset)',
                                 'LINK_OVERRIDES': 'COW/GW code periods to ISO alpha-3 with basis',
                                 'COUNTY_CHANGES': 'US Census Bureau, Substantial Changes to Counties 1970-present pages (1990s-2020s)'},
                'outputs': outputs}
    (HERE/'manifest.json').write_text(json.dumps(manifest, indent=1, sort_keys=True) + '\n', encoding='utf-8')
    report(json.dumps({o['file']: o['rows'] for o in outputs}, indent=1))
    return manifest


# effective, event, from, to, population, population_basis, note
COUNTY_CHANGES = [
    ['1990-12-07', 'part_to_new', '02290', '02068', '1682', '1990 detached population', 'Denali Borough created from Yukon-Koyukuk'],
    ['1990-12-07', 'part_to_new', '02240', '02068', '0', 'unpopulated part', 'Denali Borough created from Southeast Fairbanks'],
    ['1992-09-22', 'split', '02231', '02232', '3679', '1990 population', 'Skagway-Yakutat-Angoon remainder'],
    ['1992-09-22', 'split', '02231', '02282', '725', '1990 population', 'Yakutat City and Borough'],
    ['1995-06-30', 'merge', '51780', '51083', '', '', 'South Boston city added to Halifax County'],
    ['1997-07-22', 'recode', '12025', '12086', '', '', 'Dade County renamed Miami-Dade County'],
    ['1997-07-01', 'boundary', '24033', '24031', '5156', '1990 added population', 'Takoma Park to Montgomery County'],
    ['1997-11-07', 'merge', '30113', '30031', '0', 'unpopulated portion', 'Yellowstone NP annexed to Gallatin'],
    ['1997-11-07', 'merge', '30113', '30067', '52', '1990 added population', 'Yellowstone NP annexed to Park'],
    ['2001-07-01', 'merge', '51560', '51005', '4289', 'estimated added population', 'Clifton Forge city added to Alleghany County'],
    ['2001-11-15', 'part_to_new', '08001', '08014', '15870', 'estimated detached population', 'Broomfield County from Adams'],
    ['2001-11-15', 'part_to_new', '08013', '08014', '21512', 'estimated detached population', 'Broomfield County from Boulder'],
    ['2001-11-15', 'part_to_new', '08059', '08014', '1726', 'estimated detached population', 'Broomfield County from Jefferson'],
    ['2001-11-15', 'part_to_new', '08123', '08014', '69', 'estimated detached population', 'Broomfield County from Weld'],
    ['2007-06-20', 'split', '02232', '02230', '862', 'population', 'Skagway Municipality'],
    ['2007-06-20', 'split', '02232', '02105', '2574', 'population', 'Hoonah-Angoon Census Area'],
    ['2007-07-01', 'boundary', '51199', '51700', '293', 'estimated net detached population', 'York County / Newport News exchange'],
    ['2008-05-19', 'part_to_existing', '02201', '02130', '7', 'estimated added population', 'Outer Ketchikan to Ketchikan Gateway'],
    ['2008-05-19', 'recode', '02201', '02198', '6115', 'estimated population of remainder', 'renamed Prince of Wales-Hyder'],
    ['2008-06-01', 'split', '02280', '02275', '2448', 'estimated population incl. Meyers Chuck area', 'Wrangell City and Borough'],
    ['2008-06-01', 'split', '02280', '02195', '4260', 'estimated population', 'Petersburg Census Area'],
    ['2013-01-03', 'boundary', '02105', '02195', '1', 'estimated detached population', 'part to Petersburg Borough'],
    ['2013-01-03', 'boundary', '02195', '02198', '613', 'estimated added population', 'part of former Petersburg CA to Prince of Wales-Hyder'],
    ['2013-07-01', 'merge', '51515', '51019', '6222', 'estimated net added population', 'Bedford city added to Bedford County'],
    ['2015-05-01', 'recode', '46113', '46102', '', '', 'Shannon County renamed Oglala Lakota County'],
    ['2015-07-01', 'recode', '02270', '02158', '', '', 'Wade Hampton renamed Kusilvak Census Area'],
    ['2019-01-02', 'split', '02261', '02063', '', 'population not published on change page', 'Chugach Census Area'],
    ['2019-01-02', 'split', '02261', '02066', '', 'population not published on change page', 'Copper River Census Area'],
    ['2022-06-06', 'replace_system', '09', '09', '', 'use ct_county_planning_region_cousub.csv', 'Connecticut counties replaced by planning regions (87 FR 34233)'],
]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-dir', required=True, type=Path)
    parser.add_argument('--download', action='store_true')
    args = parser.parse_args(argv)
    args.source_dir.mkdir(parents=True, exist_ok=True)
    if args.download:
        for name, (url, _) in SOURCES.items():
            request = urllib.request.Request(url, headers={'User-Agent': 'worldmodel-reference-build'})
            with urllib.request.urlopen(request, timeout=60) as response:
                (args.source_dir/name).write_bytes(response.read())
    build(args.source_dir)


if __name__ == '__main__':
    main()
