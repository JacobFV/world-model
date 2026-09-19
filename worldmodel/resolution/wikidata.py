"""Wikidata external-identifier statements, read as published identifier claims.

``docs/identity-coverage.md`` measures why 96% of this catalog's entities meet no second
publisher, and names the one thing that could change it: **Wikidata QIDs are the only
cross-domain identifier in the catalog**. 980,841 ``wikidata`` claims and 486,342 OpenSanctions
entity IDs that *are* QIDs already sit in the index, and only 2,008 of them meet a second
publisher, because nothing maps a QID to anything else.

The ``wikidata_identifiers`` dataset publishes that map: one ``external_identifier`` assertion
per Wikidata statement, carrying the property that published it. This module says what each
property is, what shape its value has to have, and which of them are refused.

Three rules, the same ones the rest of :mod:`~worldmodel.resolution.bridges` follows:

* **The property names the register.** A value is read only through the property that carries
  it, never by guessing a register from a number's shape. ``wdt:`` is the truthy predicate, so a
  statement Wikidata marks deprecated never reaches here at all.
* **Check digits and formats are recomputed where the standard defines one.** ISO 17442 for the
  LEI, ISO 6166 for the ISIN, the IMO ship-number weighting, the fixed shapes of a Bioguide ID,
  a CIK, an IATA or ICAO code, a MIC, a BIC, a FIPS code, an ISO 3166 code. A value that fails
  is a typo on a wiki, not an identifier.
* **A code its issuer reuses for different things is refused outright**, exactly as the identity
  layer already refuses OGRN, UN/LOCODE and MMSI values one dataset prints twice
  (:data:`REFUSED`). Refusing is not the same as discarding: the statements are still published
  as evidence by the dataset, they are simply never turned into identity.

Everything here is a *bridge*, so every family is additionally held to the cardinality its
mapping specification declares, a value that breaks it is deleted and counted by
``unify.refuse_bridge_cardinality_violations``, and ``unify-resolve --no-bridges`` turns the whole
layer off.
"""
import re

# -- value shapes --------------------------------------------------------------------------------


def _clean(value):
    return value.strip().replace(' ', '') if isinstance(value, str) else ''


def _mod97_10(body):
    """ISO 7064 MOD 97-10 residue of an alphanumeric string (A=10 ... Z=35)."""
    digits = ''.join(str(ord(c) - 55) if c.isalpha() else c for c in body)
    return int(digits) % 97


def lei(value):
    """An ISO 17442 LEI: 20 characters whose MOD 97-10 check digits recompute to 1."""
    text = _clean(value).upper()
    if not re.fullmatch(r'[A-Z0-9]{18}[0-9]{2}', text) or _mod97_10(text) != 1:
        return None
    return text


_ISIN = re.compile(r'([A-Z]{2})([A-Z0-9]{9})([0-9])')


def isin(value):
    """An ISO 6166 ISIN whose modulus-10 double-add-double check digit recomputes."""
    from .bridges import isin_check_digit
    text = _clean(value).upper()
    match = _ISIN.fullmatch(text)
    if match is None or isin_check_digit(match.group(1) + match.group(2)) != match.group(3):
        return None
    return text


def imo(value):
    """An IMO ship identification number: seven digits, last one the weighted check digit.

    IMO Resolution A.600(15): the check digit is the last digit of
    ``7*d1 + 6*d2 + 5*d3 + 4*d4 + 3*d5 + 2*d6``. Wikidata prints the number bare and sometimes
    with the ``IMO`` prefix.
    """
    text = _clean(value).upper().removeprefix('IMO')
    if not re.fullmatch(r'[0-9]{7}', text):
        return None
    total = sum(int(text[i]) * (7 - i) for i in range(6))
    return text if total % 10 == int(text[6]) else None


def _companies_house(value):
    from .bridges import gb_company_number
    return gb_company_number(value)


def _shape(pattern, transform=None):
    compiled = re.compile(pattern)

    def check(value):
        text = _clean(value).upper()
        if not compiled.fullmatch(text):
            return None
        return transform(text) if transform else text
    return check


def _digits(width=None):
    def check(value):
        text = _clean(value)
        if not text.isascii() or not text.isdigit() or text == '0' * len(text):
            return None
        if width is not None:
            return text.zfill(width) if len(text) <= width else None
        return str(int(text))
    return check


def opencorporates_gb_company_number(value):
    """The Companies House number inside a ``gb/…`` OpenCorporates ID.

    OpenCorporates keys a company on ``<jurisdiction>/<the register's own number>``; for ``gb``
    the register is Companies House, so the second segment *is* the company number. This reads
    the publisher's own ID structure, exactly as ``gleif_isin_cusip`` reads the CUSIP inside a
    US ISIN; it is not a guess about which register an untyped number belongs to. Any other
    jurisdiction is refused: this catalog holds no other OpenCorporates register.
    """
    from .bridges import gb_company_number
    text = _clean(value)
    jurisdiction, _, number = text.partition('/')
    if jurisdiction.lower() != 'gb' or not number:
        return None
    return gb_company_number(number)


# -- the properties ------------------------------------------------------------------------------
#
# ``spec`` picks the mapping specification, and therefore the cardinality the bridge is held to:
#
#   wikidata_identifier        1:1  one item, one value, and one value on one item.
#   wikidata_identifier_series 1:n  one item may publish several values (a company has several
#                                   ISINs, an exchange several MIC segment codes, a candidate
#                                   several committees), but one value still names one item.
#
# ``validate`` returns the normalized value or ``None``. A property with no namespace is one this
# module refuses; the reason is in :data:`REFUSED`.
PROPERTIES = {
    'P1278': {'label': 'Legal Entity Identifier', 'namespace': 'lei',
              'spec': 'wikidata_identifier', 'validate': lei},
    'P5531': {'label': 'Central Index Key', 'namespace': 'sec_cik',
              'spec': 'wikidata_identifier', 'validate': _digits()},
    'P1157': {'label': 'US Congress Bio ID', 'namespace': 'bioguide',
              'spec': 'wikidata_identifier', 'validate': _shape(r'[A-Z][0-9]{6}')},
    'P12644': {'label': 'GovTrack person ID', 'namespace': 'govtrack',
               'spec': 'wikidata_identifier', 'validate': _digits()},
    'P3344': {'label': 'Vote Smart candidate ID', 'namespace': 'votesmart',
              'spec': 'wikidata_identifier', 'validate': _digits()},
    'P2686': {'label': 'OpenSecrets people ID', 'namespace': 'opensecrets',
              'spec': 'wikidata_identifier', 'validate': _shape(r'N[0-9]{8}')},
    'P7057': {'label': 'FEC Campaign Committee ID', 'namespace': 'fec_committee',
              'spec': 'wikidata_identifier_series', 'validate': _shape(r'C[0-9]{8}')},
    'P946': {'label': 'ISIN', 'namespace': 'isin',
             'spec': 'wikidata_identifier_series', 'validate': isin},
    'P458': {'label': 'IMO ship number', 'namespace': 'imo',
             'spec': 'wikidata_identifier', 'validate': imo},
    'P6782': {'label': 'ROR ID', 'namespace': 'ror',
              'spec': 'wikidata_identifier', 'validate': _shape(r'0[0-9A-Z]{6}[0-9]{2}', str.lower)},
    'P238': {'label': 'IATA airport code', 'namespace': 'iata',
             'spec': 'wikidata_identifier', 'validate': _shape(r'[A-Z]{3}')},
    'P239': {'label': 'ICAO airport code', 'namespace': 'icao',
             'spec': 'wikidata_identifier', 'validate': _shape(r'[A-Z]{4}')},
    'P7534': {'label': 'MIC market code', 'namespace': 'mic',
              'spec': 'wikidata_identifier_series', 'validate': _shape(r'[A-Z][A-Z0-9]{3}')},
    'P2627': {'label': 'ISO 9362 SWIFT/BIC code', 'namespace': 'swift',
              'spec': 'wikidata_identifier_series',
              'validate': _shape(r'[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?')},
    'P3347': {'label': 'PermID', 'namespace': 'permid',
              'spec': 'wikidata_identifier', 'validate': _digits()},
    'P2622': {'label': 'Companies House company ID', 'namespace': 'gb_company_number',
              'spec': 'wikidata_identifier', 'validate': _companies_house},
    'P1320': {'label': 'OpenCorporates organization ID', 'namespace': 'gb_company_number',
              'spec': 'wikidata_identifier', 'validate': opencorporates_gb_company_number},
    'P11175': {'label': 'FDIC Certificate ID', 'namespace': 'fdic_cert',
               'spec': 'wikidata_identifier', 'validate': _digits()},
    'P10682': {'label': 'EIA plant ID', 'namespace': 'eia_plant',
               'spec': 'wikidata_identifier', 'validate': _digits()},
    'P10712': {'label': 'EIA utility ID', 'namespace': 'eia_utility',
               'spec': 'wikidata_identifier', 'validate': _digits()},
    'P2771': {'label': 'D-U-N-S number', 'namespace': 'duns',
              'spec': 'wikidata_identifier', 'validate': _shape(r'[0-9]{9}')},
    'P297': {'label': 'ISO 3166-1 alpha-2 code', 'namespace': 'iso3166_1_alpha2',
             'spec': 'wikidata_identifier', 'validate': _shape(r'[A-Z]{2}')},
    'P298': {'label': 'ISO 3166-1 alpha-3 code', 'namespace': 'iso3166_1_alpha3',
             'spec': 'wikidata_identifier', 'validate': _shape(r'[A-Z]{3}')},
    'P299': {'label': 'ISO 3166-1 numeric code', 'namespace': 'iso3166_1_numeric',
             'spec': 'wikidata_identifier', 'validate': _digits(3)},
    'P300': {'label': 'ISO 3166-2 code', 'namespace': 'iso3166_2',
             'spec': 'wikidata_identifier', 'validate': _shape(r'[A-Z]{2}-[A-Z0-9]{1,3}')},
    'P882': {'label': 'FIPS 6-4 ID', 'namespace': 'fips_county',
             'spec': 'wikidata_identifier', 'validate': _digits(5)},
    'P5087': {'label': 'FIPS 5-2 numeric code (US states)', 'namespace': 'fips_state',
              'spec': 'wikidata_identifier', 'validate': _digits(2)},
    # -- read, published, and never turned into identity -----------------------------------------
    'P587': {'label': 'MMSI', 'namespace': None, 'spec': None, 'validate': None},
    'P1937': {'label': 'UN/LOCODE', 'namespace': None, 'spec': None, 'validate': None},
    'P1297': {'label': 'IRS Employer Identification Number', 'namespace': None, 'spec': None,
              'validate': None},
    'P2390': {'label': 'Ballotpedia ID', 'namespace': None, 'spec': None, 'validate': None},
}

# Why a property that this catalog *does* hold the counterpart namespace for is still refused.
# Each is a code whose issuer reuses it for different things, which is the rule
# ``REFUSE_DUPLICATES_WITHIN_A_DATASET`` already applies to OGRN, UN/LOCODE and MMSI values one
# dataset prints twice. Wikidata publishing them against a QID would carry the reuse across
# publishers, where that rule cannot see it.
REFUSED = {
    'P587': ('An MMSI is a radio identity assigned to a station, not to a hull: it is reassigned '
             'when a ship is re-flagged or scrapped, and the identity layer already refuses MMSI '
             'values one dataset prints for two subjects. A Wikidata item for a 1990s ship and an '
             'AIS record for the hull that now holds the number are not the same vessel.'),
    'P1937': ('A UN/LOCODE names a locality, not a facility. 28 UN/LOCODEs in this catalog are '
              'already printed for two different World Port Index ports, and the identity layer '
              'refuses them for that reason.'),
    'P1297': ('An EIN is a taxpayer ID that a parent and its subsidiaries share: EIN 850019030 is '
              'published in this catalog for both Public Service Co of New Mexico and its holding '
              'company. ``unify.NON_UNIQUE_IN_PRACTICE`` keeps it out of clustering already.'),
    'P2390': ('A Ballotpedia ID is a wiki page title, renamed whenever the page moves and carrying '
              'whatever disambiguator that wiki needed on the day; 4 of the first 4 values sampled '
              'were election pages, not people. A title is not a stable identifier for a person.'),
}

# Namespaces this bridge introduces, i.e. the ones no catalog dataset published before. They are
# added to ``UNIQUE_NAMESPACES`` so the claims can cluster at all.
NEW_UNIQUE_NAMESPACES = ('govtrack', 'votesmart', 'opensecrets', 'eia_utility',
                         'iso3166_1_alpha2', 'iso3166_1_alpha3', 'iso3166_1_numeric', 'iso3166_2',
                         'fips_county', 'fips_state')


def statement_claim(value):
    """The ``(namespace, value, spec)`` a published Wikidata statement carries, or ``None``.

    ``value`` is the literal of an ``external_identifier`` assertion:
    ``{'property': 'P1278', 'property_label': …, 'value': '213800FD9J2IHTA7YX78'}``. Returns
    ``None`` for a property outside :data:`PROPERTIES`, for one :data:`REFUSED` names, and for a
    value whose shape or check digits do not hold.
    """
    if not isinstance(value, dict):
        return None
    rule = PROPERTIES.get(value.get('property'))
    if rule is None or rule['namespace'] is None:
        return None
    normalized = rule['validate'](value.get('value'))
    return None if normalized is None else (rule['namespace'], normalized, rule['spec'])


# -- the other side: entity IDs that are geographic codes ------------------------------------------
#
# ``unify.ENTITY_ID_NAMESPACES`` already reads a namespaced entity ID as a published identifier
# claim, because a source that calls a thing ``lei:5493…`` or ``iata:AAE`` has published that
# identifier. The catalog's geographic entity IDs are the same kind of statement and were not in
# that table: ``census_geography`` and every ACS product key a county on its five-digit FIPS code
# (``geo:US:county:13209``), a state on its two-digit FIPS code, and an ISO 3166-2 subdivision on
# its code; ``airport_nodes`` and ``bis_bulk`` key a country on its ISO 3166-1 alpha-3 code.
#
# On its own this bridge joins almost nothing: those IDs are already shared between the Census
# products that use them. It exists so the Wikidata FIPS and ISO 3166 statements have something to
# meet - a Wikidata county item and a Census county are then two *publishers*, which is the
# measure ``identity-coverage`` reports as 2.44%.
#
# Only the exact shapes below are read. ``geo:US:tract:…``, ``geo:US:zcta:…`` and the rest of the
# ``geo:`` tree are left alone: no property acquired here publishes them.
GEO_ENTITY_IDS = (
    ('fips_county', re.compile(r'geo:US:county:([0-9]{5})')),
    ('fips_state', re.compile(r'geo:US:state:([0-9]{2})')),
    ('iso3166_1_alpha3', re.compile(r'iso3:([A-Z]{3})')),
    ('iso3166_1_alpha2', re.compile(r'geo:([A-Z]{2})')),
    ('iso3166_2', re.compile(r'iso3166-2:([A-Z]{2}-[A-Z0-9]{1,3})')),
)


def geo_entity_id_claim(entity_id):
    """The ``(namespace, value)`` a geographic entity ID publishes, or ``None``."""
    if not isinstance(entity_id, str):
        return None
    for namespace, pattern in GEO_ENTITY_IDS:
        match = pattern.fullmatch(entity_id)
        if match is not None:
            return namespace, match.group(1)
    return None
