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


# Byte tags unify's raw-line prefilter must keep for these bridges to see their records. They are
# deliberately narrow: ``registration_authority`` appears only in GLEIF entity attributes and
# ``issuer_security`` only on issuer-to-security edges.
BRIDGE_TAGS = (b'"registration_authority"', b'"predicate":"issuer_security"')

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
}


def record_claims(record):
    """Bridge identifier claims a published record carries, as ``(subject, ns, value, scope, bridge)``.

    Reads only the fields named in :data:`BRIDGES`. Returns ``[]`` for everything else, so this is
    safe to call on every record of every dataset.
    """
    kind = record.get('kind')
    if kind == 'entity':
        subject = record.get('entity_id', record.get('id'))
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
    return []


def cardinality(bridge):
    """The declared cardinality of the mapping specification a bridge produces rows for."""
    return MAPPING_SPECS[BRIDGES[bridge]['spec']]['cardinality']
