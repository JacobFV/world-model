"""OpenFIGI mapping answers, read as published identifier claims and published listings.

``openfigi_mappings`` publishes one ``openfigi_mapping`` assertion per row the OpenFIGI v3 mapping
endpoint returned, keyed on the FIGI in the answer and carrying the query it answers. Two different
things are readable in those rows, and they are kept apart on purpose:

* **Identity.** An answer to ``idType: ID_CUSIP`` says which security OpenFIGI holds that CUSIP for.
  Where the answer is the *United States composite* (``exchCode`` ``US``), or an instrument OpenFIGI
  publishes no composite for at all (a bond, a muni, a preferred), the row names one instrument and
  the CUSIP is that instrument's US identifier, so the row is read as a ``cusip`` claim on the FIGI
  and held to 1:1 by :data:`~worldmodel.resolution.deterministic.MAPPING_SPECS`. Every other row is
  a *venue* line of the same security - Frankfurt, Mexico, one US exchange's book - and is never
  read as identity, because a CUSIP does not name a listing.
* **Listing.** An answer to ``idType: TICKER`` carrying a ``micCode`` is OpenFIGI scoping the
  listing to an ISO 10383 MIC itself. That is what makes ``figi_ticker`` assertable without
  translating Bloomberg's two-character exchange codes into MICs, which nobody publishes. A listing
  is a relationship, not identity: it is produced through ``link_mapping('figi_ticker', ...)``, not
  as a bridge claim, so a FIGI never becomes the same thing as a ticker or an issuer.

What is deliberately **not** read:

* **A ticker on its own.** Symbols are reused across venues and reissued over time, and OpenFIGI's
  answer is an undated current snapshot. A ticker is only ever read inside a MIC scope, and the
  resulting rows carry ``temporal_validity: unknown``.
* **A composite outside the United States.** A CUSIP is a North American number; the answer's
  German or Mexican composite is a different security identifier's business.
* **The exchange code as a MIC.** ``UN``, ``UW`` and ``UA`` are Bloomberg's codes. The catalog holds
  ISO 10383 MICs (``iso_mic_venues``), and no publisher here maps one to the other, so the
  translation is not made; the MIC-scoped queries are how a MIC gets into the data.
* **The issuer.** A FIGI names an instrument. Nothing here says who issued it: reaching an issuer
  takes a second published edge (``sec_issuer_reference``'s ``issuer_listing`` to the same
  ``ticker:<MIC>:<symbol>``), and that composition is a crosswalk, not a merge.
"""
import re

# CUSIP: eight characters of issuer and issue, then a modulus-10 double-add-double check digit
# (the "CUSIP Uniform Security Identification Procedures" algorithm). Values are held to it before
# they are sent to OpenFIGI and again before they are read back, because 13F filers construct
# option pseudo-CUSIPs by putting 9 in the seventh position - 037833900 for an Apple call - and
# those are not CUSIPs and belong to nothing.
_CUSIP_SHAPE = re.compile(r'[0-9A-Z]{9}')
_CUSIP_VALUES = {character: index for index, character in enumerate('0123456789')}
_CUSIP_VALUES.update({character: 10 + index for index, character in enumerate('ABCDEFGHIJKLMNOPQRSTUVWXYZ')})
_CUSIP_VALUES.update({'*': 36, '@': 37, '#': 38})

# A CUSIP whose first eight characters are all zero passes the check digit, and filers use exactly
# that as a placeholder for a security they could not identify. It is refused by value.
CUSIP_PLACEHOLDERS = frozenset({'000000000'})

# Levels of the FIGI hierarchy the mapping answer distinguishes. Only the first two name one
# instrument that a CUSIP can be the US identifier of.
IDENTITY_LEVELS = frozenset({'us_composite', 'unlisted'})


def cusip_check_digit(body):
    """The CUSIP check digit for the first eight characters of a CUSIP."""
    total = 0
    for position, character in enumerate(body):
        value = _CUSIP_VALUES[character]
        if position % 2:
            value *= 2
        total += value // 10 + value % 10
    return str((10 - total % 10) % 10)


def cusip(value):
    """``value`` upper-cased when it is a CUSIP whose check digit recomputes, else ``None``.

    Rejects anything that is not nine characters of digits and capitals (13F filers also write
    ``*``, ``@`` and ``#``, which CUSIP reserves and which no issue actually uses), the all-zero
    placeholder, and every value whose check digit does not hold.
    """
    value = str(value or '').strip().upper()
    if not _CUSIP_SHAPE.fullmatch(value) or value in CUSIP_PLACEHOLDERS:
        return None
    return value if cusip_check_digit(value[:8]) == value[8] else None


def _statement(value):
    """The parts of an ``openfigi_mapping`` assertion value, or ``None`` if it is not one."""
    if not isinstance(value, dict):
        return None
    query, published = value.get('query'), value.get('published')
    if not isinstance(query, dict) or not isinstance(published, dict):
        return None
    return query, published, value.get('figi_level')


def identity_claim(value):
    """The ``(namespace, value, spec)`` an OpenFIGI answer publishes about its subject FIGI.

    ``None`` unless the answer is to a CUSIP query, at a level that names one instrument, with a
    CUSIP that passes its own check digit.
    """
    parts = _statement(value)
    if parts is None:
        return None
    query, _, figi_level = parts
    if query.get('id_type') != 'ID_CUSIP' or figi_level not in IDENTITY_LEVELS:
        return None
    number = cusip(query.get('id_value'))
    return None if number is None else ('cusip', number, 'openfigi_cusip_figi')


def listing_row(subject, value, *, record_id=None, evidence=None):
    """A ``link_mapping('figi_ticker', ...)`` row from a MIC-scoped OpenFIGI answer, or ``None``.

    Only an answer whose request carried a ``micCode`` is read: that MIC is OpenFIGI's own scoping
    of the listing. The ticker published on the row is used, not the ticker that was asked for, so
    a symbol OpenFIGI answers under a different form is not silently renamed.
    """
    parts = _statement(value)
    if parts is None or not isinstance(subject, str) or not subject.startswith('figi:'):
        return None
    query, published, _ = parts
    mic = str(query.get('mic_code') or '').strip().upper()
    ticker = str(published.get('ticker') or '').strip().upper()
    if query.get('id_type') != 'TICKER' or not re.fullmatch(r'[A-Z0-9]{4}', mic) or not ticker:
        return None
    row = {'left': subject[5:], 'right': ticker, 'scope': mic}
    if record_id and evidence:
        row['row_evidence'] = [{'input': evidence, 'record_id': record_id}]
    return row


# Why each refusal exists, so a reader of the published output can see what was left on the table.
REFUSED = {
    'venue_level_rows': ('A row whose FIGI is not its own composite is one exchange\'s line of the security. '
                         'A CUSIP does not name a listing, so those rows are published as statements and never '
                         'become identity.'),
    'non_us_composite': ('A composite outside the United States is another national numbering agency\'s '
                         'security. The CUSIP asked about is not its identifier.'),
    'exchange_code_as_mic': ('OpenFIGI publishes Bloomberg exchange codes (UN, UW, UA, ...), not MICs, and no '
                             'publisher in this catalog maps one to the other. A MIC enters the data only '
                             'because the request carried a micCode and OpenFIGI answered inside it.'),
    'bare_ticker': ('A ticker with no venue is not an identifier: symbols are reused across venues and reissued '
                    'over time. The US composite answers are published, and are read as a listing only where a '
                    'MIC-scoped answer says which venue.'),
    'option_pseudo_cusip': ('13F filers construct a CUSIP for an option by putting 9 in the seventh position. '
                            'Those values fail the CUSIP check digit and are never sent or read.'),
    'all_zero_cusip': ('000000000 passes the check digit and is a filer placeholder for an unidentified '
                       'security, so it is refused by value.'),
    'issuer_identity': ('A FIGI names an instrument, never its issuer. Reaching an issuer takes a second '
                        'published edge to the same ticker@MIC, which is a crosswalk and not a merge.'),
}
