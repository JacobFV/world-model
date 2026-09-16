"""Deterministic name/address normalization used for blocking and comparison.

Normalization never replaces source values: callers keep the original string and
store the normalized form alongside it, with the removed legal-form tokens.
"""
import re
import unicodedata

_SPECIAL = {'ß': 'ss', 'æ': 'ae', 'Æ': 'AE', 'ø': 'o', 'Ø': 'O', 'ł': 'l', 'Ł': 'L', 'đ': 'd', 'Đ': 'D',
            'þ': 'th', 'Þ': 'TH', 'œ': 'oe', 'Œ': 'OE', 'ı': 'i', 'ð': 'd', 'Ð': 'D', '&': ' and ', '@': ' at ',
            '’': "'", '‘': "'", '–': '-', '—': '-'}
# Simplified ISO 9 / BGN-style romanization for Cyrillic and Greek (deterministic, lossy).
_CYRILLIC = dict(zip('абвгдеёжзийклмнопрстуфхцчшщъыьэюяіїєґ',
                     ['a', 'b', 'v', 'g', 'd', 'e', 'e', 'zh', 'z', 'i', 'y', 'k', 'l', 'm', 'n', 'o', 'p', 'r', 's', 't',
                      'u', 'f', 'kh', 'ts', 'ch', 'sh', 'shch', '', 'y', '', 'e', 'yu', 'ya', 'i', 'yi', 'ye', 'g']))
_GREEK = dict(zip('αβγδεζηθικλμνξοπρσςτυφχψω',
                  ['a', 'v', 'g', 'd', 'e', 'z', 'i', 'th', 'i', 'k', 'l', 'm', 'n', 'x', 'o', 'p', 'r', 's', 's', 't',
                   'y', 'f', 'ch', 'ps', 'o']))

# Legal forms: variant token sequences -> canonical form. Matched at the end of names.
_LEGAL = {
    'inc': ['inc', 'incorporated'], 'corp': ['corp', 'corporation'], 'co': ['co', 'company', 'cie', 'compagnie'],
    'llc': ['llc', 'l l c'], 'ltd': ['ltd', 'limited'], 'plc': ['plc', 'public limited company'], 'lp': ['lp', 'l p'],
    'llp': ['llp', 'l l p'], 'lllp': ['lllp'], 'pllc': ['pllc'], 'pc': ['pc', 'professional corporation'],
    'gmbh': ['gmbh', 'gesellschaft mit beschrankter haftung'], 'ag': ['ag', 'aktiengesellschaft'], 'kg': ['kg'],
    'kgaa': ['kgaa'], 'se': ['se', 'societas europaea'], 'sa': ['sa', 's a', 'sociedad anonima', 'societe anonyme'],
    'nv': ['nv', 'n v', 'naamloze vennootschap'], 'bv': ['bv', 'b v', 'besloten vennootschap'],
    'spa': ['spa', 's p a', 'societa per azioni'], 'srl': ['srl', 's r l'], 'sarl': ['sarl', 's a r l'], 'sas': ['sas'],
    'ab': ['ab', 'aktiebolag'], 'as': ['as', 'a s', 'asa'], 'oy': ['oy', 'oyj'], 'kk': ['kk', 'kabushiki kaisha'],
    'pte': ['pte'], 'pty': ['pty', 'proprietary'], 'bhd': ['bhd', 'berhad', 'sdn bhd'], 'ulc': ['ulc'],
    'sl': ['sl', 's l'], 'sab de cv': ['sab de cv', 's a b de c v'], 'sa de cv': ['sa de cv', 's a de c v'],
    'ooo': ['ooo'], 'pjsc': ['pjsc'], 'ojsc': ['ojsc'], 'jsc': ['jsc'], 'tbk': ['tbk'],
    'holdings': ['holdings', 'holding'], 'group': ['group'], 'trust': ['trust'],
}
_LEGAL_SEQUENCES = sorted(((tuple(v.split()), k) for k, vs in _LEGAL.items() for v in vs), key=lambda x: -len(x[0]))
# Descriptors that identify the legal wrapper but not the organization.
_STRUCTURAL = {'holdings', 'group', 'trust'}
_ORG_STOP = {'the', 'of', 'and'}
_HONORIFICS = {'mr', 'mrs', 'ms', 'miss', 'dr', 'prof', 'hon', 'sen', 'rep', 'gov', 'sir', 'dame', 'rev', 'judge'}
_NAME_SUFFIXES = {'jr', 'sr', 'ii', 'iii', 'iv', 'v', 'md', 'phd', 'esq'}
_ADDRESS = {'street': 'st', 'avenue': 'ave', 'boulevard': 'blvd', 'road': 'rd', 'drive': 'dr', 'lane': 'ln',
            'court': 'ct', 'place': 'pl', 'square': 'sq', 'suite': 'ste', 'floor': 'fl', 'building': 'bldg',
            'highway': 'hwy', 'parkway': 'pkwy', 'north': 'n', 'south': 's', 'east': 'e', 'west': 'w',
            'northeast': 'ne', 'northwest': 'nw', 'southeast': 'se', 'southwest': 'sw', 'apartment': 'apt',
            'room': 'rm', 'po': 'po', 'box': 'box', 'terrace': 'ter', 'circle': 'cir', 'center': 'ctr', 'centre': 'ctr'}


def transliterate(text):
    """ASCII-fold text: special ligatures, Cyrillic/Greek romanization, then NFKD accent removal."""
    if not isinstance(text, str):
        raise ValueError('Text must be a string')
    out = []
    for char in text:
        lower = char.lower()
        if char in _SPECIAL:
            out.append(_SPECIAL[char])
        elif lower in _CYRILLIC:
            value = _CYRILLIC[lower]
            out.append(value.upper() if char != lower and value else value)
        elif lower in _GREEK:
            out.append(_GREEK[lower])
        else:
            out.append(char)
    folded = unicodedata.normalize('NFKD', ''.join(out))
    return ''.join(c for c in folded if not unicodedata.combining(c)).encode('ascii', 'ignore').decode('ascii')


def _tokens(text):
    text = transliterate(text).lower()
    text = re.sub(r"(?<=\b[a-z])\.(?=[a-z]\b)", ' ', text)  # "l.l.c" -> "l l c"
    text = re.sub(r"['`]", '', text)
    return re.sub(r'[^a-z0-9]+', ' ', text).split()


def normalize_organization(name):
    """Return {'normalized','tokens','legal_forms','original'} for an organization name."""
    tokens = _tokens(name)
    legal = []
    changed = True
    while changed and tokens:
        changed = False
        for sequence, canonical in _LEGAL_SEQUENCES:
            n = len(sequence)
            if len(tokens) > n and tuple(tokens[-n:]) == sequence:
                legal.insert(0, canonical)
                tokens = tokens[:-n]
                changed = True
                break
    core = [t for t in tokens if t not in _ORG_STOP] or tokens
    structural = [t for t in legal if t in _STRUCTURAL]
    return {'original': name, 'normalized': ' '.join(core), 'tokens': core,
            'legal_forms': [t for t in legal if t not in _STRUCTURAL], 'descriptors': structural}


def normalize_person(name):
    """Normalize 'Last, First Middle Jr.' or 'First Middle Last' into ordered tokens."""
    if not isinstance(name, str):
        raise ValueError('Name must be a string')
    reordered = name
    if name.count(',') == 1:
        last, first = [p.strip() for p in name.split(',')]
        if _tokens(first) and set(_tokens(first)) <= _NAME_SUFFIXES:
            reordered = last + ' ' + first
        else:
            reordered = first + ' ' + last
    tokens = _tokens(reordered)
    honorifics = [t for t in tokens if t in _HONORIFICS]
    suffixes = [t for t in tokens if t in _NAME_SUFFIXES and len(tokens) > 2]
    core = [t for t in tokens if t not in _HONORIFICS and t not in suffixes]
    return {'original': name, 'normalized': ' '.join(core), 'tokens': core, 'given': core[0] if core else '',
            'family': core[-1] if core else '', 'initials': ''.join(t[0] for t in core[:-1]),
            'honorifics': honorifics, 'suffixes': suffixes}


def normalize_address(address):
    tokens = [_ADDRESS.get(t, t) for t in _tokens(address)]
    postal = None
    for token in reversed(tokens):
        if re.fullmatch(r'\d{5}(\d{4})?', token):
            postal = token[:5]
            break
    return {'original': address, 'normalized': ' '.join(tokens), 'tokens': tokens, 'postal5': postal}


def normalize_name(name, kind='organization'):
    if kind in ('person', 'individual'):
        return normalize_person(name)
    return normalize_organization(name)


def soundex(token):
    """American Soundex of one token (used only as a blocking key)."""
    token = re.sub('[^a-z]', '', transliterate(token).lower())
    if not token:
        return ''
    codes = {**dict.fromkeys('bfpv', '1'), **dict.fromkeys('cgjkqsxz', '2'), **dict.fromkeys('dt', '3'),
             'l': '4', **dict.fromkeys('mn', '5'), 'r': '6'}
    out, last = token[0].upper(), codes.get(token[0], '')
    for char in token[1:]:
        code = codes.get(char, '')
        if code and code != last:
            out += code
        if char not in 'hw':
            last = code
    return (out + '000')[:4]
