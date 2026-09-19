"""Published-identifier bridges: deterministic identifier claims read out of published
fields that are not themselves identifier assertions.

``shared_identifier_links`` can only join what a publisher printed as an identifier claim or
as a namespaced entity ID. Several catalog datasets publish an identifier from *another*
registry in a differently shaped field - GLEIF prints the registering authority's own entity
ID for every LEI, and its ISIN mapping prints a security identifier whose national part is a
CUSIP. Those fields are published identifiers; they are simply not published as
``identifier_assignment`` rows, so nothing read them.

Each bridge here states:

* the exact published field it reads and the value shape it accepts,
* the :data:`~worldmodel.resolution.deterministic.MAPPING_SPECS` entry that says what a row
  means and at what cardinality, so a violated cardinality is refused rather than merged,
* what the bridge does **not** assert.

Nothing here is a name match, a similarity score or a guess about which register an untyped
number belongs to. A bridge fires only where the publisher names the register.
"""
import re

from .deterministic import MAPPING_SPECS

# GLEIF LEI-CDF 3.1 publishes, for every LEI, the business register that registered the entity
# (``Entity.RegistrationAuthority.RegistrationAuthorityID``, a GLEIF RA code) and that register's
# own identifier for it (``Entity.RegistrationAuthority.RegistrationAuthorityEntityID``). Where the
# register is one this catalog also holds, that pair *is* a published crosswalk row.
#
# Only authorities whose entity IDs this catalog can type are listed. A numeric entity ID from an
# arbitrary authority is not a CIK and not an RSSD: measured on the published golden copy, 30,074
# numeric IDs under RA000063 collide with 2,851 real CIKs purely by coincidence, which is why the
# authority code - not the value shape - decides the namespace.
GLEIF_REGISTRATION_AUTHORITIES = {
    'RA000665': {
        'namespace': 'sec_cik', 'spec': 'gleif_sec_cik',
        'authority': 'U.S. Securities and Exchange Commission (EDGAR)',
        'pattern': re.compile(r'0*([1-9][0-9]{0,9})'),
        'note': ('RA000665 entity IDs are EDGAR identifiers of three shapes: a decimal CIK, an '
                 'S…/C… registered-fund series or class ID, and an 805-… investment-adviser file '
                 'number. Only the decimal shape is a CIK, so only it is read.'),
    },
    'RA000585': {
        'namespace': 'gb_company_number', 'spec': 'gleif_companies_house',
        'authority': 'Companies House (United Kingdom)',
        'pattern': re.compile(r'([A-Z]{2})?0*([0-9]{1,8})'),
        'note': ('Companies House numbers are eight characters: eight digits, or a two-letter '
                 'register prefix (SC, NI, OC, …) and six digits. GLEIF prints them both padded '
                 'and unpadded, so the value is re-padded before it is compared.'),
    },
}


def gleif_registration_authority_claim(attributes):
    """The (namespace, value, bridge) a GLEIF entity's registration-authority fields publish.

    ``None`` when the authority is not one this catalog can type, when the entity ID is absent,
    or when its shape is not the one that authority uses for a company (an SEC series ID is a
    published identifier, but it is not a CIK).
    """
    if not isinstance(attributes, dict):
        return None
    authority = GLEIF_REGISTRATION_AUTHORITIES.get(attributes.get('registration_authority'))
    if authority is None:
        return None
    raw = attributes.get('registration_authority_entity_id')
    if not isinstance(raw, str) or not raw.strip():
        return None
    match = authority['pattern'].fullmatch(raw.strip().upper().replace(' ', ''))
    if match is None:
        return None
    if authority['namespace'] == 'gb_company_number':
        prefix, digits = match.group(1), match.group(2)
        value = (prefix + digits.zfill(6)) if prefix else digits.zfill(8)
    else:
        value = match.group(1)
    return authority['namespace'], value, authority['spec']


# ISO 6166 builds an ISIN from a two-letter country prefix, a nine-character national security
# identifying number and a modulus-10 double-add-double check digit. For the US and Canada the
# NSIN *is* the CUSIP, which is what lets a GLEIF ISIN-to-LEI row meet a 13F information-table
# CUSIP. The check digit is recomputed rather than trusted: on the published GLEIF mapping all
# 2,291,868 US ISINs verify, so a value that fails the check is a malformed row, not a CUSIP.
CUSIP_ISIN_COUNTRIES = ('US', 'CA')
_ISIN = re.compile(r'([A-Z]{2})([A-Z0-9]{9})([0-9])')


def isin_check_digit(body):
    """The ISO 6166 check digit for an ISIN without it (country code plus NSIN)."""
    digits = [int(character) for character in
              ''.join(str(ord(c) - 55) if c.isalpha() else c for c in body)][::-1]
    total = 0
    for position, digit in enumerate(digits):
        if position % 2 == 0:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return str(-total % 10)


def cusip_from_isin(isin):
    """The CUSIP inside a US or CA ISIN, or ``None``.

    Returns ``None`` for any other country prefix (the NSIN is then a SEDOL, WKN, … and not a
    CUSIP) and for a value whose check digit does not recompute.
    """
    if not isinstance(isin, str):
        return None
    match = _ISIN.fullmatch(isin.strip().upper())
    if match is None or match.group(1) not in CUSIP_ISIN_COUNTRIES:
        return None
    if isin_check_digit(match.group(1) + match.group(2)) != match.group(3):
        return None
    return match.group(2)


# -- register numbers a sanctions list prints with the scheme *and* the issuing country ----------
#
# OFAC's SDN_ADVANCED and the Consolidated Screening List print an identity document as
# ``{scheme, issuing_country, number}``. Where the scheme and the country together name one
# national register, the number is that register's identifier: OFAC's "Tax ID No." issued by RUS is
# the Russian taxpayer number (INN), its "Registration Number" issued by RUS is the primary state
# registration number (OGRN), and "Company Number" issued by GBR is a Companies House number. Each
# value is then held to the register's own check digit, so a mistyped or non-register number is
# refused rather than guessed at. A number whose publisher names no register (OpenSanctions'
# ``registrationNumber``, OFAC's "Registration ID" without a country) is never read.

def _digits(value):
    text = str(value or '').replace(' ', '').replace('-', '')
    return text if text.isascii() and text.isdigit() else None


def ru_inn(value):
    """A Russian INN (10 digits for an organisation, 12 for a person) whose check digits hold."""
    digits = _digits(value)
    if digits is None or len(digits) not in (10, 12):
        return None
    d = [int(c) for c in digits]

    def check(weights, count):
        return sum(w * x for w, x in zip(weights, d[:count])) % 11 % 10

    if len(d) == 10:
        return digits if check((2, 4, 10, 3, 5, 9, 4, 6, 8), 9) == d[9] else None
    if check((7, 2, 4, 10, 3, 5, 9, 4, 6, 8), 10) != d[10] or check((3, 7, 2, 4, 10, 3, 5, 9, 4, 6, 8), 11) != d[11]:
        return None
    return digits


def ru_ogrn(value):
    """A Russian OGRN (13 digits, first 1 or 5) or OGRNIP (15 digits, first 3) whose check digit holds."""
    digits = _digits(value)
    if digits is None:
        return None
    if len(digits) == 13 and digits[0] in '15' and int(digits[:12]) % 11 % 10 == int(digits[12]):
        return digits
    if len(digits) == 15 and digits[0] == '3' and int(digits[:14]) % 13 % 10 == int(digits[14]):
        return digits
    return None


_GB_COMPANY = re.compile(r'([A-Z]{2})?0*([0-9]{1,8})')


def gb_company_number(value):
    """A Companies House number, re-padded to its eight-character form."""
    match = _GB_COMPANY.fullmatch(str(value or '').strip().upper().replace(' ', ''))
    if match is None:
        return None
    prefix, digits = match.group(1), match.group(2)
    if prefix:
        return prefix + digits.zfill(6) if len(digits) <= 6 else None
    return digits.zfill(8)


# Check-digit validators applied to *every* claim in these namespaces, typed or bridged: a typed
# ``ru_ogrn:0000000000000`` (which OpenSanctions prints for more than one organisation) is a
# placeholder, not a registration.
REGISTER_VALIDATORS = {'ru_inn': ru_inn, 'ru_ogrn': ru_ogrn}

# (scheme as printed, issuing country as printed) -> (namespace, validator). Only pairs that name a
# register are listed. "Registration ID" and "Government Gazette Number" under RUS are left out:
# OFAC uses the first for more than one Russian register and the second is the OKPO statistical code.
REGISTER_SCHEMES = {
    ('tax id no.', 'RUS'): ('ru_inn', ru_inn),
    ('registration number', 'RUS'): ('ru_ogrn', ru_ogrn),
    ('business registration number', 'RUS'): ('ru_ogrn', ru_ogrn),
    ('company number', 'GBR'): ('gb_company_number', gb_company_number),
}


def register_number_claim(value):
    """The (namespace, value) a sanctions-list identity document names by scheme and country, or None."""
    if not isinstance(value, dict) or value.get('id') or flagged_fraudulent(value):
        return None  # a typed ``id`` is already read as an identifier claim
    key = (str(value.get('scheme') or '').strip().lower(), str(value.get('issuing_country') or '').strip().upper())
    rule = REGISTER_SCHEMES.get(key)
    if rule is None:
        return None
    namespace, validate = rule
    normalized = validate(value.get('number', value.get('value')))
    return None if normalized is None else (namespace, normalized)


# FollowTheMoney's ``uniqueEntityId`` property is the US System for Award Management Unique Entity
# ID: twelve characters, letters and digits, never an O or I, never starting with 0.
_UEI = re.compile(r'[A-HJ-NP-Z1-9][A-HJ-NP-Z0-9]{11}')


def uei(value):
    text = str(value or '').strip().upper()
    return text if _UEI.fullmatch(text) else None


# OpenSanctions gives an entity the Wikidata QID as its canonical ID when it has linked it to
# Wikidata (``opensanctions:Q672671``). The ID *is* the published link, as a GLEIF entity ID is its LEI.
_OPENSANCTIONS_QID = re.compile(r'opensanctions:(Q[1-9][0-9]*)')

# Assertions a publisher uses to say two of its records are one listed party, with the published
# field the link is derived from. ``unify-resolve`` reads them as source-asserted ``same_as`` edges.
LINK_PREDICATES = {
    'same_designation_as': {
        'reads': 'other_sanctions_lists same_designation_as assertions',
        'published_basis': ('the Consolidated Screening List prints, for every entry it republishes from '
                            'OFAC, the OFAC Sanctions List Service profile ID as its entity_number; the UK '
                            'sanctions list prints the UN reference number of a UN designation it implements'),
        'does_not_assert': ('that two listings impose the same measures. It joins the listed party, not the '
                            'legal effect of each designation.'),
    },
}

# A value a designating authority marks as fraudulently used ("validity": "Fraudulent" on an OFAC
# identity document) is not identity evidence for the party that used it: a fraudulent MMSI or IMO
# number belongs to some other vessel. ``unify-resolve`` refuses the claim, and the same claim on a
# record that a LINK_PREDICATES row makes the same designation (the CSL copy drops the flag).
def flagged_fraudulent(value):
    return isinstance(value, dict) and str(value.get('validity') or '').strip().lower() == 'fraudulent'


# IMO issues two independent seven-digit series: ship identification numbers, and company and
# registered-owner identification numbers. The sanctions publishers print both under ``imo`` - OFAC
# as "Vessel Registration Identification" on a vessel and "Identification Number" or "Company
# Number" on an organisation, OpenSanctions as ``imoNumber`` on a Vessel and on a Company. The
# publisher's own entity type is what says which series a number is from, so ``unify-resolve``
# types an ``imo`` claim on a subject that is not a vessel as ``imo_company``.
IMO_SHIP_ENTITY_TYPES = frozenset({'vessel'})


# Two names for one published code system. GLEIF's BIC-to-LEI mapping publishes ISO 9362 business
# identifier codes as ``bic``; the sanctions lists publish the same codes as ``swift`` ("SWIFT/BIC").
NAMESPACE_ALIASES = {'bic': 'swift'}


def normalize_value(namespace, value):
    """Case and form rules for namespaces :func:`worldmodel.identity._normalize` does not know."""
    if namespace in ('iata', 'icao', 'orcid', 'cusip', 'figi', 'uei', 'gb_company_number'):
        return value.upper()
    if namespace == 'ror':
        return value.lower().rsplit('/', 1)[-1]
    if namespace == 'swift':
        # ISO 9362: an eight-character BIC is the primary office, which is the eleven-character
        # form with branch code XXX. Any other branch code names a branch and stays distinct.
        value = value.upper().replace(' ', '')
        return value[:8] if len(value) == 11 and value.endswith('XXX') else value
    return value


# Byte tags unify's raw-line prefilter must keep for these bridges to see their records. They are
# deliberately narrow: ``registration_authority`` appears only in GLEIF entity attributes,
# ``issuer_security`` only on issuer-to-security edges. Identity documents and uniqueEntityId values
# travel on ``identifier`` assertions, which the prefilter already keeps.
BRIDGE_TAGS = (b'"registration_authority"', b'"predicate":"issuer_security"', b'"predicate":"same_designation_as"')

BRIDGES = {
    'gleif_sec_cik': {
        'spec': 'gleif_sec_cik', 'namespace': 'sec_cik',
        'reads': 'sec_gleif entity attributes registration_authority / registration_authority_entity_id',
        'published_basis': ('GLEIF LEI-CDF 3.1 Entity.RegistrationAuthority: authority RA000665 is the '
                            'U.S. SEC and its entity ID for a filer is the EDGAR CIK'),
        'does_not_assert': ('that an LEI without RA000665 has no CIK. GLEIF records the register the '
                            'entity was *incorporated* in, so a Delaware corporation that files with '
                            'the SEC carries the Delaware file number here, not its CIK.'),
    },
    'gleif_companies_house': {
        'spec': 'gleif_companies_house', 'namespace': 'gb_company_number',
        'reads': 'sec_gleif entity attributes registration_authority / registration_authority_entity_id',
        'published_basis': ('GLEIF LEI-CDF 3.1 Entity.RegistrationAuthority: authority RA000585 is '
                            'Companies House and its entity ID is the UK company number'),
        'does_not_assert': ('that the company is still on the register. A number absent from the '
                            'current Companies House snapshot is a dissolved or non-company registration.'),
    },
    'gleif_isin_cusip': {
        'spec': 'isin_cusip', 'namespace': 'cusip',
        'reads': 'issuer_security assertions whose object is isin:<US or CA ISIN>',
        'published_basis': ('ISO 6166: the nine-character NSIN inside a US or CA ISIN is the CUSIP. '
                            'The ISIN check digit is recomputed before the CUSIP is read.'),
        'does_not_assert': ('anything about the issuer. This links a security to the same security, '
                            'so that a GLEIF issuer_security edge and a 13F CUSIP holding meet; the '
                            'issuer identity still has to come from a published issuer identifier.'),
    },
    'sanctions_register_number': {
        'spec': 'sanctions_register_number', 'namespace': 'ru_inn / ru_ogrn / gb_company_number',
        'reads': ('ofac_sanctions and other_sanctions_lists identity documents that print a scheme and an issuing '
                  'country and no typed id'),
        'published_basis': ('OFAC SDN_ADVANCED identity documents: the document type and issuing country name the '
                            'register ("Tax ID No." + RUS is the INN, "Registration Number" + RUS the OGRN, '
                            '"Company Number" + GBR a Companies House number), and each value passes that '
                            "register's check digit"),
        'does_not_assert': ('anything about a number whose publisher names no register: OpenSanctions '
                            'registrationNumber and taxNumber carry no country and are not read. One dataset '
                            'printing one number for two parties (a Russian branch shares its parent\'s INN) is '
                            'refused, not merged'),
    },
    'opensanctions_uei': {
        'spec': 'opensanctions_uei', 'namespace': 'uei',
        'reads': 'opensanctions_graph identifier values with scheme uniqueEntityId',
        'published_basis': ('FollowTheMoney LegalEntity.uniqueEntityId is the US SAM Unique Entity ID; the value '
                            'must have the twelve-character UEI shape'),
        'does_not_assert': 'SAM registration status or award eligibility; a UEI names the registrant only',
    },
    'opensanctions_wikidata': {
        'spec': 'opensanctions_wikidata', 'namespace': 'wikidata',
        'reads': 'opensanctions entity IDs of the form opensanctions:Q<digits>',
        'published_basis': ('OpenSanctions uses the Wikidata QID as the canonical ID of an entity it has linked '
                            'to Wikidata'),
        'does_not_assert': 'that Wikidata is right about the person, only that OpenSanctions linked this record to it',
    },
}


def record_claims(record):
    """Bridge identifier claims a published record carries, as ``(subject, ns, value, scope, bridge)``.

    Reads only the fields named in :data:`BRIDGES`. Returns ``[]`` for everything else, so this is
    safe to call on every record of every dataset.
    """
    kind = record.get('kind')
    if kind == 'entity':
        subject = record.get('entity_id', record.get('id'))
        if isinstance(subject, str) and subject.startswith('opensanctions:Q'):
            match = _OPENSANCTIONS_QID.fullmatch(subject)
            return [(subject, 'wikidata', match.group(1), None, 'opensanctions_wikidata')] if match else []
        if not isinstance(subject, str) or not subject.startswith('lei:'):
            return []
        claim = gleif_registration_authority_claim(record.get('attributes'))
        if claim is None:
            return []
        namespace, value, spec = claim
        bridge = next(name for name, row in BRIDGES.items() if row['spec'] == spec)
        return [(subject, namespace, value, None, bridge)]
    if kind == 'assertion' and record.get('predicate') == 'issuer_security':
        obj = record.get('object')
        if not isinstance(obj, str) or not obj.startswith('isin:'):
            return []
        cusip = cusip_from_isin(obj[5:])
        if cusip is None:
            return []
        # The subject of the claim is the *security*, never the issuer: this bridge says
        # "isin:US14149Y1082 and cusip:14149Y108 are one security", nothing about the issuer.
        return [(obj, 'cusip', cusip, None, 'gleif_isin_cusip')]
    if kind == 'assertion' and record.get('predicate') == 'identifier':
        value, subject = record.get('value'), record.get('subject')
        if not isinstance(value, dict) or not isinstance(subject, str):
            return []
        if value.get('scheme') == 'uniqueEntityId' and not value.get('id'):
            code = uei(value.get('value'))
            return [(subject, 'uei', code, None, 'opensanctions_uei')] if code else []
        claim = register_number_claim(value)
        if claim is not None:
            return [(subject, claim[0], claim[1], None, 'sanctions_register_number')]
    return []


def cardinality(bridge):
    """The declared cardinality of the mapping specification a bridge produces rows for."""
    return MAPPING_SPECS[BRIDGES[bridge]['spec']]['cardinality']
