"""Stdlib reader for NOAA nClimDiv fixed-width monthly files and NCEI state-code mapping.

County files (e.g. climdiv-tmpccy-v1.0.0-YYYYMMDD):  SS CCC EE YYYY + 12 x value
  SS = NCEI/NCDC state code (alphabetical, NOT FIPS), CCC = county FIPS, EE = element, YYYY = year.
State/region files (climdiv-tmpcst-...):  SSS D EE YYYY + 12 x value
  SSS = 001-050 states (NCEI codes), 101-110 climate regions / CONUS, >110 agricultural/other regions; D = 0.
Missing values: -99.90 (temperature, precipitation, degree days), -99.99 / -9.99 (drought indices).
"""

NCEI_TO_FIPS = {
    '01': '01', '02': '04', '03': '05', '04': '06', '05': '08', '06': '09', '07': '10', '08': '12', '09': '13',
    '10': '16', '11': '17', '12': '18', '13': '19', '14': '20', '15': '21', '16': '22', '17': '23', '18': '24',
    '19': '25', '20': '26', '21': '27', '22': '28', '23': '29', '24': '30', '25': '31', '26': '32', '27': '33',
    '28': '34', '29': '35', '30': '36', '31': '37', '32': '38', '33': '39', '34': '40', '35': '41', '36': '42',
    '37': '44', '38': '45', '39': '46', '40': '47', '41': '48', '42': '49', '43': '50', '44': '51', '45': '53',
    '46': '54', '47': '55', '48': '56', '49': '15', '50': '02'}  # 49 = Hawaii (5 counties), 50 = Alaska
REGIONS = {'101': 'Northeast climate region', '102': 'Upper Midwest (East North Central) climate region',
           '103': 'Ohio Valley (Central) climate region', '104': 'Southeast climate region',
           '105': 'Northern Rockies and Plains (West North Central) climate region', '106': 'South climate region',
           '107': 'Southwest climate region', '108': 'Northwest climate region', '109': 'West climate region',
           '110': 'Contiguous United States'}
ELEMENTS = {'01': ('precipitation', 'inches'), '02': ('average_temperature', 'degF'), '05': ('palmer_drought_severity_index', 'index'),
            '06': ('palmer_hydrological_drought_index', 'index'), '07': ('palmer_z_index', 'index'),
            '08': ('modified_palmer_drought_severity_index', 'index'), '25': ('heating_degree_days', 'degF_days'),
            '26': ('cooling_degree_days', 'degF_days'), '27': ('maximum_temperature', 'degF'), '28': ('minimum_temperature', 'degF')}
MISSING = {-99.9, -99.99, -9.99, -999.9}


def parse_line(line, county):
    """Return (area code tuple, element, year, [12 values or None]) for one fixed-width record."""
    text = line.rstrip('\r\n')
    if not text.strip():
        return None
    head, rest = (text[:11], text[11:]) if county else (text[:10], text[10:])
    if county:
        area, element, year = (head[:2], head[2:5]), head[5:7], int(head[7:11])
    else:
        area, element, year = (head[:3], head[3:4]), head[4:6], int(head[6:10])
    values = []
    for field in rest.split():
        value = float(field)
        values.append(None if round(value, 2) in MISSING else value)
    if len(values) != 12:
        raise ValueError(f'Expected 12 monthly values, found {len(values)}')
    return area, element, year, values
