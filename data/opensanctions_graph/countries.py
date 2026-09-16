"""ISO 3166-1 alpha-2/alpha-3 table plus common list-publisher country name variants (stdlib only)."""
import re
import unicodedata

_TABLE = """AD AND Andorra|AE ARE United Arab Emirates|AF AFG Afghanistan|AG ATG Antigua and Barbuda|AI AIA Anguilla|AL ALB Albania|AM ARM Armenia|AO AGO Angola|AQ ATA Antarctica|AR ARG Argentina|AS ASM American Samoa|AT AUT Austria|AU AUS Australia|AW ABW Aruba|AX ALA Aland Islands|AZ AZE Azerbaijan
BA BIH Bosnia and Herzegovina|BB BRB Barbados|BD BGD Bangladesh|BE BEL Belgium|BF BFA Burkina Faso|BG BGR Bulgaria|BH BHR Bahrain|BI BDI Burundi|BJ BEN Benin|BL BLM Saint Barthelemy|BM BMU Bermuda|BN BRN Brunei|BO BOL Bolivia|BQ BES Bonaire, Sint Eustatius and Saba|BR BRA Brazil|BS BHS Bahamas|BT BTN Bhutan|BV BVT Bouvet Island|BW BWA Botswana|BY BLR Belarus|BZ BLZ Belize
CA CAN Canada|CC CCK Cocos (Keeling) Islands|CD COD Democratic Republic of the Congo|CF CAF Central African Republic|CG COG Republic of the Congo|CH CHE Switzerland|CI CIV Cote d'Ivoire|CK COK Cook Islands|CL CHL Chile|CM CMR Cameroon|CN CHN China|CO COL Colombia|CR CRI Costa Rica|CU CUB Cuba|CV CPV Cabo Verde|CW CUW Curacao|CX CXR Christmas Island|CY CYP Cyprus|CZ CZE Czechia
DE DEU Germany|DJ DJI Djibouti|DK DNK Denmark|DM DMA Dominica|DO DOM Dominican Republic|DZ DZA Algeria|EC ECU Ecuador|EE EST Estonia|EG EGY Egypt|EH ESH Western Sahara|ER ERI Eritrea|ES ESP Spain|ET ETH Ethiopia|FI FIN Finland|FJ FJI Fiji|FK FLK Falkland Islands|FM FSM Micronesia|FO FRO Faroe Islands|FR FRA France
GA GAB Gabon|GB GBR United Kingdom|GD GRD Grenada|GE GEO Georgia|GF GUF French Guiana|GG GGY Guernsey|GH GHA Ghana|GI GIB Gibraltar|GL GRL Greenland|GM GMB Gambia|GN GIN Guinea|GP GLP Guadeloupe|GQ GNQ Equatorial Guinea|GR GRC Greece|GS SGS South Georgia and the South Sandwich Islands|GT GTM Guatemala|GU GUM Guam|GW GNB Guinea-Bissau|GY GUY Guyana
HK HKG Hong Kong|HM HMD Heard Island and McDonald Islands|HN HND Honduras|HR HRV Croatia|HT HTI Haiti|HU HUN Hungary|ID IDN Indonesia|IE IRL Ireland|IL ISR Israel|IM IMN Isle of Man|IN IND India|IO IOT British Indian Ocean Territory|IQ IRQ Iraq|IR IRN Iran|IS ISL Iceland|IT ITA Italy|JE JEY Jersey|JM JAM Jamaica|JO JOR Jordan|JP JPN Japan
KE KEN Kenya|KG KGZ Kyrgyzstan|KH KHM Cambodia|KI KIR Kiribati|KM COM Comoros|KN KNA Saint Kitts and Nevis|KP PRK North Korea|KR KOR South Korea|KW KWT Kuwait|KY CYM Cayman Islands|KZ KAZ Kazakhstan|LA LAO Laos|LB LBN Lebanon|LC LCA Saint Lucia|LI LIE Liechtenstein|LK LKA Sri Lanka|LR LBR Liberia|LS LSO Lesotho|LT LTU Lithuania|LU LUX Luxembourg|LV LVA Latvia|LY LBY Libya
MA MAR Morocco|MC MCO Monaco|MD MDA Moldova|ME MNE Montenegro|MF MAF Saint Martin (French part)|MG MDG Madagascar|MH MHL Marshall Islands|MK MKD North Macedonia|ML MLI Mali|MM MMR Myanmar|MN MNG Mongolia|MO MAC Macao|MP MNP Northern Mariana Islands|MQ MTQ Martinique|MR MRT Mauritania|MS MSR Montserrat|MT MLT Malta|MU MUS Mauritius|MV MDV Maldives|MW MWI Malawi|MX MEX Mexico|MY MYS Malaysia|MZ MOZ Mozambique
NA NAM Namibia|NC NCL New Caledonia|NE NER Niger|NF NFK Norfolk Island|NG NGA Nigeria|NI NIC Nicaragua|NL NLD Netherlands|NO NOR Norway|NP NPL Nepal|NR NRU Nauru|NU NIU Niue|NZ NZL New Zealand|OM OMN Oman|PA PAN Panama|PE PER Peru|PF PYF French Polynesia|PG PNG Papua New Guinea|PH PHL Philippines|PK PAK Pakistan|PL POL Poland|PM SPM Saint Pierre and Miquelon|PN PCN Pitcairn|PR PRI Puerto Rico|PS PSE Palestine|PT PRT Portugal|PW PLW Palau|PY PRY Paraguay
QA QAT Qatar|RE REU Reunion|RO ROU Romania|RS SRB Serbia|RU RUS Russia|RW RWA Rwanda|SA SAU Saudi Arabia|SB SLB Solomon Islands|SC SYC Seychelles|SD SDN Sudan|SE SWE Sweden|SG SGP Singapore|SH SHN Saint Helena|SI SVN Slovenia|SJ SJM Svalbard and Jan Mayen|SK SVK Slovakia|SL SLE Sierra Leone|SM SMR San Marino|SN SEN Senegal|SO SOM Somalia|SR SUR Suriname|SS SSD South Sudan|ST STP Sao Tome and Principe|SV SLV El Salvador|SX SXM Sint Maarten|SY SYR Syria|SZ SWZ Eswatini
TC TCA Turks and Caicos Islands|TD TCD Chad|TF ATF French Southern Territories|TG TGO Togo|TH THA Thailand|TJ TJK Tajikistan|TK TKL Tokelau|TL TLS Timor-Leste|TM TKM Turkmenistan|TN TUN Tunisia|TO TON Tonga|TR TUR Turkey|TT TTO Trinidad and Tobago|TV TUV Tuvalu|TW TWN Taiwan|TZ TZA Tanzania|UA UKR Ukraine|UG UGA Uganda|UM UMI United States Minor Outlying Islands|US USA United States|UY URY Uruguay|UZ UZB Uzbekistan
VA VAT Holy See|VC VCT Saint Vincent and the Grenadines|VE VEN Venezuela|VG VGB British Virgin Islands|VI VIR U.S. Virgin Islands|VN VNM Vietnam|VU VUT Vanuatu|WF WLF Wallis and Futuna|WS WSM Samoa|XK XKX Kosovo|YE YEM Yemen|YT MYT Mayotte|ZA ZAF South Africa|ZM ZMB Zambia|ZW ZWE Zimbabwe"""

_ALIASES = {
    'ARE': ['UAE', 'U.A.E.'], 'BHS': ['Bahamas, The', 'The Bahamas'], 'BOL': ['Bolivia (Plurinational State of)', 'Plurinational State of Bolivia'],
    'BRN': ['Brunei Darussalam'], 'CIV': ['Ivory Coast', "Côte d'Ivoire"], 'COD': ['Congo, Democratic Republic of the', 'Congo (Democratic Republic)', 'DRC', 'Congo, Dem. Rep.', 'Congo (Kinshasa)', 'Democratic Republic of Congo', 'Zaire'],
    'COG': ['Congo', 'Congo, Republic of the', 'Congo (Brazzaville)', 'Republic of Congo'], 'CPV': ['Cape Verde'], 'CZE': ['Czech Republic'],
    'FSM': ['Micronesia, Federated States of', 'Federated States of Micronesia'], 'GBR': ['United Kingdom of Great Britain and Northern Ireland', 'UK', 'Great Britain', 'Britain'],
    'GMB': ['Gambia, The', 'The Gambia'], 'IRN': ['Iran (Islamic Republic of)', 'Islamic Republic of Iran', 'Iran, Islamic Republic of'],
    'KOR': ['Korea, South', 'Republic of Korea', 'Korea, Republic of', 'Korea (South)'], 'PRK': ["Korea, North", "Democratic People's Republic of Korea", "Korea, Democratic People's Republic of", 'DPRK', 'Korea (North)', 'North Korea (DPRK)'],
    'LAO': ["Lao People's Democratic Republic", 'Lao PDR'], 'MDA': ['Moldova, Republic of', 'Republic of Moldova'], 'MKD': ['Macedonia', 'The former Yugoslav Republic of Macedonia', 'Republic of North Macedonia', 'Macedonia, The Former Yugoslav Republic of', 'North Macedonia, The Republic of'],
    'MMR': ['Burma'], 'PSE': ['State of Palestine', 'Occupied Palestinian Territory', 'Palestinian Territories', 'West Bank', 'Gaza', 'Palestinian Territory, Occupied'],
    'RUS': ['Russian Federation', 'Russian'], 'AFG': ['Afghan'], 'SYR': ['Syrian Arab Republic'], 'SWZ': ['Swaziland'], 'TUR': ['Türkiye', 'Turkiye'], 'TWN': ['Taiwan, Province of China', 'Chinese Taipei'],
    'TZA': ['United Republic of Tanzania', 'Tanzania, United Republic of'], 'USA': ['United States of America', 'U.S.', 'US', 'U.S.A.'], 'VAT': ['Vatican City', 'Vatican'],
    'VEN': ['Venezuela (Bolivarian Republic of)', 'Bolivarian Republic of Venezuela'], 'VNM': ['Viet Nam'], 'HKG': ['Hong Kong SAR', 'Hong Kong, China', 'China, Hong Kong Special Administrative Region'],
    'MAC': ['Macau', 'Macao SAR'], 'KGZ': ['Kyrgyz Republic'], 'SVK': ['Slovak Republic'], 'TLS': ['East Timor'], 'NLD': ['The Netherlands', 'Holland'],
    'VGB': ['Virgin Islands, British'], 'VIR': ['Virgin Islands, U.S.'], 'KNA': ['Saint Kitts & Nevis', 'St. Kitts and Nevis'], 'VCT': ['St. Vincent and the Grenadines'],
    'LCA': ['St. Lucia', 'St Lucia'], 'PLW': ['Republic of Palau'], 'IMN': ['Man, Isle of'],
    'KNA': ['Saint Kitts & Nevis', 'St. Kitts and Nevis', 'St Kitts & Nevis', 'St. Kitts & Nevis', 'St Kitts and Nevis'],
    'VCT': ['St. Vincent and the Grenadines', 'St Vincent', 'St. Vincent and Grenadines', 'St Vincent and the Grenadines', 'Saint Vincent'],
    'STP': ['São Tomé and Príncipe', 'Sao Tome & Principe'], 'BIH': ['Bosnia & Herzegovina', 'Bosnia-Herzegovina'], 'TTO': ['Trinidad & Tobago'], 'ATG': ['Antigua & Barbuda'],
    'REU': ['Réunion'], 'CUW': ['Curaçao'], 'ALA': ['Åland Islands'], 'BLM': ['Saint Barthélemy'], 'SDN': ['Republic of the Sudan'], 'SSD': ['Republic of South Sudan'],
    'CHN': ["People's Republic of China", 'China (mainland)', 'PRC'], 'IRQ': ['Republic of Iraq'], 'LBY': ['Libyan Arab Jamahiriya', 'State of Libya'], 'YEM': ['Republic of Yemen'],
}

# Demonyms used as nationality values by some publishers (OFAC advanced XML, UK list).
_DEMONYMS = {'PSE': ['Palestinian'], 'IRQ': ['Iraqi'], 'SYR': ['Syrian'], 'IRN': ['Iranian'], 'CHN': ['Chinese'],
             'PRK': ['North Korean'], 'LBN': ['Lebanese'], 'YEM': ['Yemeni'], 'SAU': ['Saudi'], 'PAK': ['Pakistani'],
             'UKR': ['Ukrainian'], 'BLR': ['Belarusian'], 'VEN': ['Venezuelan'], 'CUB': ['Cuban'], 'MEX': ['Mexican'],
             'COL': ['Colombian'], 'EGY': ['Egyptian'], 'JOR': ['Jordanian'], 'KWT': ['Kuwaiti'], 'LBY': ['Libyan'],
             'SOM': ['Somali'], 'SDN': ['Sudanese'], 'TUR': ['Turkish'], 'IND': ['Indian'], 'GBR': ['British']}
for _a3, _variants in _DEMONYMS.items():
    _ALIASES.setdefault(_a3, []).extend(_variants)

_ISO2, _ISO3, _NAMES, _LABELS = {}, set(), {}, {}


def _norm(text):
    text = unicodedata.normalize('NFKD', str(text)).encode('ascii', 'ignore').decode().lower()
    text = re.sub(r"[^a-z0-9]+", ' ', text).strip()
    text = re.sub(r'^the ', '', text)
    return text


for _line in _TABLE.split('\n'):
    for _item in _line.split('|'):
        _a2, _a3, _name = _item.split(' ', 2)
        _ISO2[_a2] = _a3
        _ISO3.add(_a3)
        _LABELS[_a3] = _name
        _NAMES[_norm(_name)] = _a3
        _NAMES[_norm(_a3)] = _a3
for _a3, _variants in _ALIASES.items():
    for _variant in _variants:
        _NAMES[_norm(_variant)] = _a3
# Two-letter normalized names would collide with ISO2 lookups; alpha-3 codes are distinct enough.
_ISO2['UK'] = 'GBR'
_ISO2['EL'] = 'GRC'


def iso3_from_iso2(code):
    return _ISO2.get(str(code or '').strip().upper())


def iso3_from_name(name):
    if not name:
        return None
    key = _norm(name)
    if not key:
        return None
    if key.upper() in _ISO3 and len(key) == 3:
        return key.upper()
    return _NAMES.get(key)


def label(iso3):
    return _LABELS.get(iso3, iso3)


def country_entity(ev, iso3, locator):
    """Emit the shared iso3 country entity once; return its ID."""
    key = 'iso3:' + iso3
    return key, ev.entity(key, 'country', label(iso3), locator, iso3=iso3)
