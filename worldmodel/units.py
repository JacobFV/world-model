"""Dimensional units with explicit, provenance-carrying conversions.

Principles:

* Every conversion returns its factor and the steps that produced it.
* Physical definitions (1 lb = 0.45359237 kg exactly) are built in, with sources.
* Anything that varies by date or commodity is never a hidden constant: currency
  conversion and price-level deflation require a dated series object supplied by
  the caller; commodity densities and heat contents require ``commodity=`` and
  approximate factors additionally require ``allow_approximate=True``.
* Calendar months/quarters/years are not a fixed number of seconds; converting
  them to physical time requires an explicit ``day_count`` convention.
* Ambiguous symbols (``ton``, ``MT``, ``oz``, ``therm``, ``hp``, ``cal``) are refused.
* Percent (a ratio) and percentage points (a difference of ratios) are distinct
  dimensions; so are index points with different base periods.

This module has no dependency on the rest of the package.
"""
from dataclasses import dataclass, field
from datetime import date
import math
import re

SOURCES = {
    'si': 'BIPM SI Brochure, 9th edition (2019)',
    'nist811': 'NIST SP 811 (2008) Appendix B conversion factors; exact where marked',
    'intl_yard_pound': 'International yard and pound agreement (1959): 1 yd = 0.9144 m, 1 lb = 0.45359237 kg',
    'usda_ah697': 'USDA ERS Agricultural Handbook 697, Weights, Measures, and Conversion Factors for Agricultural Commodities',
    'eia_heat': 'EIA Monthly Energy Review Appendix A, approximate heat content (varies by year; approximate)',
    'iea_toe': 'IEA definition: 1 tonne of oil equivalent = 41.868 GJ',
    'iso6346': 'ISO 668/6346 container conventions: 1 FEU = 2 TEU',
    'iso4217': 'ISO 4217 currency codes (worldmodel/reference/currencies_iso4217.csv)',
}


@dataclass(frozen=True)
class Unit:
    """A parsed unit: value_in_base = (value + offset) * scale."""
    scale: float
    dims: tuple = ()
    offset: float = 0.0
    tags: frozenset = frozenset()
    text: str = ''
    notes: tuple = field(default=(), compare=False)

    def describe(self):
        return {'text': self.text, 'scale': self.scale, 'dimensions': dict(self.dims),
                'offset': self.offset, 'tags': sorted(self.tags), 'notes': list(self.notes)}


def _dims(**kwargs):
    return tuple(sorted((k, v) for k, v in kwargs.items() if v))


def _mul(a, b, power=1):
    out = dict(a)
    for key, value in b:
        out[key] = out.get(key, 0) + value * power
    return tuple(sorted((k, v) for k, v in out.items() if v))


L, M, T, TH, CUR, AMT, LUM = 'length', 'mass', 'time', 'temperature', 'current', 'amount', 'luminous_intensity'
LENGTH, MASS, TIME = _dims(length=1), _dims(mass=1), _dims(time=1)
AREA, VOLUME = _dims(length=2), _dims(length=3)
ENERGY = _dims(mass=1, length=2, time=-2)
POWER = _dims(mass=1, length=2, time=-3)
CAL = _dims(calendar_month=1)

_REGISTRY = {}
_AMBIGUOUS = {
    'ton': 'short ton (907.18474 kg), long ton (1016.0469088 kg) or metric tonne; use short_ton, long_ton or t',
    'tons': 'short, long or metric ton; use short_ton, long_ton or t',
    'mt': 'metric ton, megatonne or mount; use t or megatonne', 'oz': 'avoirdupois or troy ounce; use avdp_oz or troy_oz',
    'therm': 'US or EC therm; use therm_us or therm_ec', 'hp': 'mechanical, metric or electrical horsepower',
    'cal': 'thermochemical or International Table calorie', 'kcal': 'thermochemical or International Table kilocalorie',
    'cwt': 'short (100 lb) or long (112 lb) hundredweight', 'gallon_imp_or_us': 'gallon', 'pt': 'pint or point',
    'kt': 'knot or kilotonne; use knot or kilotonne', 'bn': 'billion (1e9) in most sources, milliard ambiguity',
    'index': 'index points require a base period, e.g. index_2017_100',
    'k': 'kelvin or thousand; use kelvin or thousand', 'mw': 'megawatt or milliwatt; use megawatt',
    'b': 'barrel, byte or billion', 'd': 'day or penny', 'y': 'year or yocto'
}


def _register(names, scale, dims, source, *, offset=0.0, tags=(), note=None):
    for name in names:
        key = name.lower()
        if key in _REGISTRY:
            raise ValueError('Duplicate unit alias: ' + name)
        _REGISTRY[key] = (float(scale), tuple(dims), float(offset), frozenset(tags), source, note)


_register(['1', 'dimensionless', 'unitless'], 1, (), 'si')
_register(['fraction', 'ratio', 'share_fraction'], 1, (), 'si', tags=['ratio'])
_register(['percent', '%', 'pct', 'per_cent'], 0.01, (), 'si', tags=['ratio'])
_register(['permille', 'per_mille'], 0.001, (), 'si', tags=['ratio'])
_register(['percentage_point', 'percentage_points', 'pp', 'ppt'], 0.01, _dims(ratio_difference=1), 'si')
_register(['basis_point', 'basis_points', 'bp', 'bps'], 0.0001, _dims(ratio_difference=1), 'si',
          note='basis points are treated as differences of ratios (e.g. rate changes)')
_register(['category'], 1, _dims(category=1), 'si', note='categorical values are not convertible')
# Length / area / volume
for names, scale in ((['m', 'meter', 'meters', 'metre', 'metres'], 1), (['km', 'kilometer', 'kilometers', 'kilometre'], 1e3),
                     (['cm', 'centimeter', 'centimeters'], 1e-2), (['mm', 'millimeter', 'millimeters'], 1e-3)):
    _register(names, scale, LENGTH, 'si')
_register(['ft', 'foot', 'feet'], 0.3048, LENGTH, 'intl_yard_pound')
_register(['yd', 'yard', 'yards'], 0.9144, LENGTH, 'intl_yard_pound')
_register(['mi', 'mile', 'miles', 'statute_mile'], 1609.344, LENGTH, 'intl_yard_pound')
_register(['nmi', 'nautical_mile', 'nautical_miles'], 1852, LENGTH, 'si')
_register(['m2', 'sq_m', 'square_meter', 'square_meters'], 1, AREA, 'si')
_register(['km2', 'sq_km', 'square_kilometer', 'square_kilometers'], 1e6, AREA, 'si')
_register(['ha', 'hectare', 'hectares'], 1e4, AREA, 'si')
_register(['acre', 'acres'], 4046.8564224, AREA, 'nist811', note='international acre')
_register(['sq_mi', 'square_mile', 'square_miles'], 1609.344 ** 2, AREA, 'intl_yard_pound')
_register(['sq_ft', 'square_foot', 'square_feet'], 0.3048 ** 2, AREA, 'intl_yard_pound')
_register(['m3', 'cubic_meter', 'cubic_meters'], 1, VOLUME, 'si')
_register(['l', 'liter', 'liters', 'litre', 'litres'], 1e-3, VOLUME, 'si')
_register(['ml', 'milliliter', 'milliliters'], 1e-6, VOLUME, 'si')
_register(['gal', 'us_gal', 'gallon', 'gallons', 'us_gallon'], 3.785411784e-3, VOLUME, 'nist811', note='US liquid gallon')
_register(['imp_gal', 'imperial_gallon'], 4.54609e-3, VOLUME, 'nist811')
_register(['bbl', 'barrel', 'barrels'], 42 * 3.785411784e-3, VOLUME, 'nist811', note='US petroleum barrel = 42 US gallons')
_register(['bu', 'bushel', 'bushels'], 35.23907016688e-3, VOLUME, 'nist811', note='US dry bushel; mass requires commodity')
_register(['cf', 'ft3', 'cubic_foot', 'cubic_feet'], 0.3048 ** 3, VOLUME, 'intl_yard_pound')
_register(['mcf'], 1e3 * 0.3048 ** 3, VOLUME, 'intl_yard_pound', note='thousand cubic feet (M = Roman thousand)')
_register(['mmcf'], 1e6 * 0.3048 ** 3, VOLUME, 'intl_yard_pound', note='million cubic feet')
_register(['bcf'], 1e9 * 0.3048 ** 3, VOLUME, 'intl_yard_pound', note='billion cubic feet')
_register(['tcf'], 1e12 * 0.3048 ** 3, VOLUME, 'intl_yard_pound', note='trillion cubic feet')
# Mass
_register(['kg', 'kilogram', 'kilograms'], 1, MASS, 'si')
_register(['g', 'gram', 'grams'], 1e-3, MASS, 'si')
_register(['mg', 'milligram', 'milligrams'], 1e-6, MASS, 'si')
_register(['t', 'tonne', 'tonnes', 'metric_ton', 'metric_tons', 'metric_tonne'], 1e3, MASS, 'si')
_register(['kilotonne', 'kilotonnes'], 1e6, MASS, 'si')
_register(['megatonne', 'megatonnes'], 1e9, MASS, 'si')
_register(['lb', 'lbs', 'pound', 'pounds'], 0.45359237, MASS, 'intl_yard_pound')
_register(['short_ton', 'short_tons'], 2000 * 0.45359237, MASS, 'intl_yard_pound')
_register(['long_ton', 'long_tons'], 2240 * 0.45359237, MASS, 'intl_yard_pound')
_register(['troy_oz', 'troy_ounce', 'troy_ounces'], 31.1034768e-3, MASS, 'nist811')
_register(['avdp_oz', 'avoirdupois_ounce'], 0.45359237 / 16, MASS, 'intl_yard_pound')
# Time (physical) and calendar
_register(['s', 'sec', 'second', 'seconds'], 1, TIME, 'si')
_register(['min', 'minute', 'minutes'], 60, TIME, 'si')
_register(['h', 'hr', 'hour', 'hours'], 3600, TIME, 'si')
_register(['day', 'days'], 86400, TIME, 'si', note='86400 s; leap seconds ignored')
_register(['week', 'weeks', 'wk'], 604800, TIME, 'si')
_register(['month', 'months', 'mo'], 1, CAL, 'si', note='calendar month; physical length requires day_count')
_register(['quarter', 'quarters', 'qtr'], 3, CAL, 'si')
_register(['year', 'years', 'yr', 'annum'], 12, CAL, 'si', note='calendar year; physical length requires day_count')
# Energy / power
for names, scale in ((['j', 'joule', 'joules'], 1), (['kj'], 1e3), (['mj'], 1e6), (['gj'], 1e9), (['tj'], 1e12),
                     (['pj'], 1e15), (['ej'], 1e18)):
    _register(names, scale, ENERGY, 'si')
for names, scale in ((['wh', 'watt_hour', 'watt_hours', 'watthour', 'watthours'], 3600),
                     (['kwh', 'kilowatt_hour', 'kilowatt_hours', 'kilowatthour', 'kilowatthours'], 3.6e6),
                     (['mwh', 'megawatt_hour', 'megawatt_hours', 'megawatthour', 'megawatthours'], 3.6e9),
                     (['gwh', 'gigawatt_hour', 'gigawatt_hours', 'gigawatthour', 'gigawatthours'], 3.6e12),
                     (['twh', 'terawatt_hour', 'terawatt_hours', 'terawatthour', 'terawatthours'], 3.6e15)):
    _register(names, scale, ENERGY, 'si')
_register(['btu', 'british_thermal_unit', 'british_thermal_units'], 1055.05585262, ENERGY, 'nist811',
          note='International Table BTU')
_register(['mmbtu', 'million_btu'], 1055.05585262e6, ENERGY, 'nist811', note='MM = thousand thousand (million) BTU')
_register(['therm_us'], 1.054804e8, ENERGY, 'nist811', note='US therm')
_register(['therm_ec'], 1.05506e8, ENERGY, 'nist811', note='EC therm')
_register(['quad', 'quads', 'quadrillion_btu'], 1055.05585262e15, ENERGY, 'nist811')
_register(['toe', 'tonne_oil_equivalent'], 41.868e9, ENERGY, 'iea_toe')
_register(['ktoe'], 41.868e12, ENERGY, 'iea_toe')
_register(['mtoe'], 41.868e15, ENERGY, 'iea_toe')
for names, scale in ((['w', 'watt', 'watts'], 1), (['kw', 'kilowatt', 'kilowatts'], 1e3), (['megawatt', 'megawatts'], 1e6),
                     (['gw', 'gigawatt', 'gigawatts'], 1e9), (['tw', 'terawatt', 'terawatts'], 1e12)):
    _register(names, scale, POWER, 'si')
_register(['knot', 'knots', 'kn'], 1852 / 3600, _dims(length=1, time=-1), 'si')
_register(['mph'], 1609.344 / 3600, _dims(length=1, time=-1), 'intl_yard_pound')
# Angles and temperature
_register(['rad', 'radian', 'radians'], 1, _dims(angle=1), 'si')
_register(['deg', 'degree', 'degrees', '°'], math.pi / 180, _dims(angle=1), 'si')
_register(['kelvin'], 1, _dims(temperature=1), 'si', tags=['absolute_temperature'])
_register(['degc', 'celsius', '°c'], 1, _dims(temperature=1), 'si', offset=273.15, tags=['absolute_temperature'])
_register(['degf', 'fahrenheit', '°f'], 5 / 9, _dims(temperature=1), 'nist811', offset=459.67, tags=['absolute_temperature'])
# Counted things: distinct dimensions; no implicit equivalence between them.
for dim, names in (('people', ['people', 'persons', 'person', 'individuals']),
                   ('establishments', ['establishments', 'establishment']), ('firms', ['firms', 'firm', 'enterprises']),
                   ('households', ['households', 'household']), ('teu', ['teu', 'teus']), ('shares', ['shares', 'share']),
                   ('vessels', ['vessels', 'vessel']), ('vehicles', ['vehicles', 'vehicle']), ('jobs', ['jobs', 'job']),
                   ('head', ['head']), ('events', ['events', 'event']),
                   ('items', ['items', 'item', 'count', 'units', 'number'])):
    _register(names, 1, _dims(**{dim: 1}), 'si')
_register(['feu', 'feus'], 2, _dims(teu=1), 'iso6346')

_MULTIPLIERS = {'hundred': 1e2, 'thousand': 1e3, 'thousands': 1e3, 'million': 1e6, 'millions': 1e6,
                'billion': 1e9, 'billions': 1e9, 'trillion': 1e12, 'trillions': 1e12, 'quadrillion': 1e15}

# Commodity-specific bridges: (commodity, from_unit, to_unit, factor, status, source)
# status 'standard' is a legal/definitional convention; 'approximate' varies by year/grade.
COMMODITY_FACTORS = [
    ('corn', 'bu', 'lb', 56, 'standard', 'usda_ah697'), ('sorghum', 'bu', 'lb', 56, 'standard', 'usda_ah697'),
    ('rye', 'bu', 'lb', 56, 'standard', 'usda_ah697'), ('flaxseed', 'bu', 'lb', 56, 'standard', 'usda_ah697'),
    ('wheat', 'bu', 'lb', 60, 'standard', 'usda_ah697'), ('soybeans', 'bu', 'lb', 60, 'standard', 'usda_ah697'),
    ('barley', 'bu', 'lb', 48, 'standard', 'usda_ah697'), ('oats', 'bu', 'lb', 32, 'standard', 'usda_ah697'),
    ('rice_rough', 'bu', 'lb', 45, 'standard', 'usda_ah697'),
    ('crude_oil', 'bbl', 'mmbtu', 5.8, 'approximate', 'eia_heat'),
    ('boe', 'bbl', 'mmbtu', 5.8, 'standard', 'eia_heat'),
]


class UnitError(ValueError):
    """Raised for unknown, ambiguous or dimensionally invalid conversions."""


_CURRENCIES = None


def currencies():
    """ISO 4217 alphabetic codes (current and withdrawn) from the tracked reference table."""
    global _CURRENCIES
    if _CURRENCIES is None:
        import csv
        from pathlib import Path
        path = Path(__file__).resolve().parent / 'reference' / 'currencies_iso4217.csv'
        codes = {}
        with path.open(encoding='utf-8') as stream:
            for row in csv.DictReader(stream):
                codes.setdefault(row['code'], row)
        _CURRENCIES = codes
    return _CURRENCIES


def _is_currency(token):
    return bool(re.fullmatch(r'[A-Z]{3}', token)) and token in currencies()


_INDEX = re.compile(r'index(?:_points?)?[_ ]?(?P<base>\d{4}(?:[-_]\d{2,4})?(?:[-_]?q[1-4]|m\d{2})?)(?:[_=]+(?P<level>\d+))?$', re.I)


def _atom(token, original):
    """Parse one multiplicative atom, returning (scale, dims, offset, tags, notes)."""
    power = 1
    match = re.fullmatch(r'(.+?)(?:\^(-?\d+)|([23])|²|³)$', token)
    if match and token.lower() not in _REGISTRY:
        base = match.group(1)
        power = int(match.group(2) or match.group(3) or (2 if token.endswith('²') else 3))
        token = base
    if token.lower() in _MULTIPLIERS:
        return _MULTIPLIERS[token.lower()] ** power, (), 0.0, frozenset(), ()
    index = _INDEX.fullmatch(token)
    if index:
        base = index.group('base').replace('_', '-')
        level = index.group('level') or '100'
        return 1.0, _dims(**{f'index:{base}={level}': power}), 0.0, frozenset({'index'}), ()
    if token.startswith('currency:'):
        code = token.split(':', 1)[1]
        if not _is_currency(code.split('@')[0]):
            raise UnitError('Unknown ISO 4217 currency: ' + code)
        return 1.0, _dims(**{'currency:' + code: power}), 0.0, frozenset({'currency'}), ()
    if _is_currency(token):
        return 1.0, _dims(**{'currency:' + token: power}), 0.0, frozenset({'currency'}), ()
    key = token.lower()
    if key in _AMBIGUOUS and key not in _REGISTRY:
        raise UnitError(f'Ambiguous unit {token!r} in {original!r}: {_AMBIGUOUS[key]}')
    if key not in _REGISTRY:
        raise UnitError(f'Unknown unit {token!r} in {original!r}')
    scale, dims, offset, tags, source, note = _REGISTRY[key]
    if offset and power != 1:
        raise UnitError('Offset temperature units cannot be raised to a power')
    return scale ** power, tuple((k, v * power) for k, v in dims), offset, tags, ((note,) if note else ())


_CACHE = {}


def parse_unit(text):
    """Parse strings such as 'thousand_USD', 'USD/barrel', 'million kilowatt hours', 'BU / ACRE',
    'USD_2017' (constant 2017 dollars), 'index_1982_1984_100', 'km/second', 'm^2'."""
    if isinstance(text, Unit):
        return text
    if text is None:
        raise UnitError('Unit is required (None denotes an unspecified port, not dimensionless)')
    if not isinstance(text, str) or not text.strip():
        raise UnitError('Unit must be a nonempty string')
    if text in _CACHE:
        return _CACHE[text]
    original = text
    normalized = re.sub(r'\s*/\s*', '/', text.strip())
    normalized = re.sub(r'\bper\b', '/', normalized, flags=re.I)
    normalized = re.sub(r'\s*/\s*', '/', normalized)
    # Phrase aliases before tokenizing.
    for phrase, alias in (('kilowatt hours?', 'kilowatt_hour'), ('megawatt hours?', 'megawatt_hour'),
                          ('gigawatt hours?', 'gigawatt_hour'), ('terawatt hours?', 'terawatt_hour'),
                          ('watt hours?', 'watt_hour'), ('metric tons?', 'metric_ton'), ('short tons?', 'short_ton'),
                          ('long tons?', 'long_ton'), ('square (kilo)?meters?', r'square_\1meter'),
                          ('square miles?', 'square_mile'), ('cubic feet', 'cubic_feet'), ('nautical miles?', 'nautical_mile'),
                          ('percentage points?', 'percentage_point'), ('basis points?', 'basis_point'),
                          ('british thermal units?', 'btu'), ('index points', 'index_points'),
                          ('chained (\\d{4}) ([A-Z]{3})', r'\2_\1'), ('constant (\\d{4}) ([A-Z]{3})', r'\2_\1'),
                          ('(\\d{4}) ([A-Z]{3})\\b', r'\2_\1'), ('chained_(\\d{4})_([A-Z]{3})', r'\2_\1'),
                          ('(?<![A-Za-z0-9])(\\d{4})_([A-Z]{3})\\b', r'\2_\1')):
        normalized = re.sub(r'\b' + phrase + r'\b' if not phrase.startswith('(') else phrase, alias, normalized,
                            flags=0 if re.search('[A-Z]', phrase) else re.I)
    parts = normalized.split('/')
    if any(not part.strip() for part in parts):
        raise UnitError('Malformed unit expression: ' + original)
    scale, dims, offset, tags, notes = 1.0, (), 0.0, set(), []
    atoms = 0
    for position, part in enumerate(parts):
        sign = 1 if position == 0 else -1
        tokens = [t for t in re.split(r'[\s*·]+', part.strip()) if t]
        expanded = []
        for token in tokens:
            index = _INDEX.fullmatch(token)
            if index or token.lower() in _REGISTRY or _is_currency(token):
                expanded.append(token)
                continue
            pieces = token.split('_')
            # thousand_USD, USD_2017, million_kilowatt_hours
            if len(pieces) > 1 and all(p for p in pieces):
                merged, i = [], 0
                while i < len(pieces):
                    joined = None
                    for j in range(len(pieces), i, -1):
                        candidate = '_'.join(pieces[i:j])
                        if candidate.lower() in _REGISTRY or _INDEX.fullmatch(candidate):
                            joined = candidate
                            merged.append(candidate)
                            i = j
                            break
                    if joined is None:
                        merged.append(pieces[i])
                        i += 1
                expanded.extend(merged)
            else:
                expanded.append(token)
        i = 0
        while i < len(expanded):
            token = expanded[i]
            if _is_currency(token) and i + 1 < len(expanded) and re.fullmatch(r'\d{4}', expanded[i + 1]):
                a = (1.0, _dims(**{f'currency:{token}@{expanded[i + 1]}': 1}), 0.0, frozenset({'currency', 'real'}), ())
                i += 2
            else:
                a = _atom(token, original)
                i += 1
            atom_scale, atom_dims, atom_offset, atom_tags, atom_notes = a
            atoms += 1
            if atom_offset:
                offset = atom_offset
            scale *= atom_scale ** sign
            dims = _mul(dims, atom_dims, sign)
            tags |= set(atom_tags)
            notes.extend(atom_notes)
    if offset and (atoms != 1 or len(parts) != 1):
        raise UnitError('Offset temperature units cannot appear in compound units: ' + original)
    unit = Unit(scale=scale, dims=dims, offset=offset, tags=frozenset(tags), text=original, notes=tuple(notes))
    if len(_CACHE) < 4096:
        _CACHE[text] = unit
    return unit


def is_valid_unit(text):
    try:
        parse_unit(text)
        return True
    except UnitError:
        return False


def _diff(a, b):
    return _mul(b, a, -1)


def _factor_step(kind, detail, factor, source, **extra):
    return {'kind': kind, 'detail': detail, 'factor': factor, 'source': source, **extra}


_DAY_COUNTS = {'julian': 365.25, 'act365': 365.0, 'act360': 360.0, 'gregorian_mean': 365.2425}


def _commodity_bridges(commodity, allow_approximate):
    bridges = []
    for name, a, b, factor, status, source in COMMODITY_FACTORS:
        if name != commodity:
            continue
        ua, ub = parse_unit(a), parse_unit(b)
        if status != 'standard' and not allow_approximate:
            raise UnitError(f'{commodity} {a}->{b} factor is approximate ({SOURCES[source]}); pass allow_approximate=True '
                            'or supply an explicit dated factor')
        # One `a` equals factor `b`: ratio quantity in base units with dims b/a.
        bridges.append({'dims': _diff(ua.dims, ub.dims), 'scale': factor * ub.scale / ua.scale,
                        'step': _factor_step('commodity', f'1 {a} {commodity} = {factor} {b}', factor, SOURCES[source],
                                             status=status)})
    return bridges


def conversion_factor(from_unit, to_unit, *, commodity=None, day_count=None, factors=(), allow_approximate=False):
    """Return {'factor','offset_from','offset_to','steps'} such that to = (from + offset_from) * factor - offset_to.

    ``factors`` may contain explicit bridges: {'from': unit, 'to': unit, 'factor': x, 'source': str,
    'valid_at'?: date, 'id'?: str} meaning 1 from-unit equals x to-units.
    """
    a, b = parse_unit(from_unit), parse_unit(to_unit)
    steps = []
    if ('ratio_difference', 1) in a.dims and not b.dims and 'ratio' in b.tags or \
            ('ratio_difference', 1) in b.dims and not a.dims and 'ratio' in a.tags:
        raise UnitError('Percent (ratio) and percentage points (difference of ratios) are not interconvertible')
    if 'index' in a.tags or 'index' in b.tags:
        if a.dims != b.dims:
            raise UnitError('Index points with different or unspecified base periods need rebase_index() with a dated series')
    currency_a = sorted(k for k, _ in a.dims if k.startswith('currency:'))
    currency_b = sorted(k for k, _ in b.dims if k.startswith('currency:'))
    if currency_a != currency_b:
        raise UnitError('Currency or price-basis change requires convert_currency()/deflate() with a dated series; '
                        f'{currency_a} -> {currency_b}')
    diff = _diff(a.dims, b.dims)
    factor = a.scale / b.scale
    steps.append(_factor_step('dimensional', f'{a.text} -> {b.text}', factor, 'unit registry'))
    diff_map = dict(diff)
    if diff_map.get('calendar_month') and diff_map.get('time') is not None:
        months = diff_map['calendar_month']
        if diff_map['time'] != -months:
            raise UnitError('Calendar and physical time exponents do not balance')
        if day_count not in _DAY_COUNTS:
            raise UnitError('Calendar months/quarters/years to physical time need day_count in ' + ', '.join(_DAY_COUNTS))
        seconds_per_month = _DAY_COUNTS[day_count] / 12 * 86400
        step_factor = seconds_per_month ** -months
        factor *= step_factor
        steps.append(_factor_step('calendar', f'1 month = {_DAY_COUNTS[day_count]}/12 days ({day_count})', step_factor,
                                  'explicit day_count convention'))
        diff = _mul(diff, _dims(calendar_month=1, time=-1), -months)
    if diff:
        bridges = []
        if commodity is not None:
            bridges.extend(_commodity_bridges(commodity, allow_approximate))
        for item in factors:
            if not isinstance(item, dict) or not {'from', 'to', 'factor', 'source'} <= set(item):
                raise UnitError('Explicit factors need from, to, factor and source')
            if isinstance(item['factor'], bool) or not isinstance(item['factor'], (int, float)) or not item['factor'] > 0 \
                    or not math.isfinite(item['factor']):
                raise UnitError('Explicit factor must be positive and finite')
            ua, ub = parse_unit(item['from']), parse_unit(item['to'])
            bridges.append({'dims': _diff(ua.dims, ub.dims), 'scale': item['factor'] * ub.scale / ua.scale,
                            'step': _factor_step('explicit', f"1 {item['from']} = {item['factor']} {item['to']}", item['factor'],
                                                 item['source'], **{k: item[k] for k in ('id', 'valid_at') if k in item})})
        used = []
        for bridge in bridges:
            for power in (1, -1, 2, -2, 3, -3):
                if _mul((), bridge['dims'], power) == diff:
                    used.append((bridge, power))
        if not used:
            if not bridges and commodity is None and any(k in dict(diff) for k in ('mass', 'length')):
                raise UnitError(f'Cannot convert {a.text} to {b.text} without commodity= (e.g. bushels to tonnes)')
            raise UnitError(f'Incompatible dimensions: {a.text} {dict(a.dims)} -> {b.text} {dict(b.dims)}')
        if len({(round(math.log(bridge["scale"]) * power, 12)) for bridge, power in used}) > 1:
            raise UnitError('Ambiguous conversion: several supplied factors disagree')
        bridge, power = used[0]
        factor *= bridge['scale'] ** power
        steps.append({**bridge['step'], 'power': power})
    if (a.offset or b.offset) and not (a.dims == b.dims == _dims(temperature=1)):
        raise UnitError('Offset units only convert between absolute temperatures')
    return {'factor': factor, 'offset_from': a.offset, 'offset_to': b.offset / 1.0 if not b.offset else b.offset,
            'steps': steps}


def convert(value, from_unit, to_unit, **options):
    """Convert a number; returns value plus factor, steps and the original quantity."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise UnitError('Value must be a finite number')
    spec = conversion_factor(from_unit, to_unit, **options)
    a, b = parse_unit(from_unit), parse_unit(to_unit)
    if a.offset or b.offset:
        result = (value + a.offset) * a.scale / b.scale - b.offset
    else:
        result = value * spec['factor']
    return {'value': result, 'unit': to_unit, 'from_value': value, 'from_unit': from_unit,
            'factor': spec['factor'], 'method': 'commodity' if any(s['kind'] == 'commodity' for s in spec['steps'])
            else 'explicit' if any(s['kind'] == 'explicit' for s in spec['steps']) else 'dimensional', 'steps': spec['steps']}


def compatible(from_unit, to_unit, **options):
    try:
        conversion_factor(from_unit, to_unit, **options)
        return True
    except UnitError:
        return False


# ---------------------------------------------------------------------------
# Dated series: exchange rates and price indexes


def _date(value):
    if isinstance(value, date):
        return value
    if not isinstance(value, str):
        raise UnitError('Dates must be ISO strings')
    try:
        return date.fromisoformat(value[:10])
    except ValueError as error:
        raise UnitError('Invalid date: ' + value) from error


def _namespaced(value):
    if not isinstance(value, str) or ':' not in value or not all(value.split(':', 1)):
        raise UnitError('Series id must be namespaced, e.g. fred:DEXUSEU')


class RateSeries:
    """Dated exchange-rate observations: 1 ``base`` = value ``quote``.

    observations: iterable of (ISO date, positive rate). ``kind`` is 'spot' or
    'period_average'; ``evidence`` is carried into every conversion result.
    """
    def __init__(self, id, base, quote, observations, *, kind='spot', frequency='daily', evidence=None):
        _namespaced(id)
        for code in (base, quote):
            if not _is_currency(code):
                raise UnitError('RateSeries currencies must be ISO 4217 codes')
        if base == quote:
            raise UnitError('RateSeries base and quote must differ')
        if kind not in ('spot', 'period_average'):
            raise UnitError('RateSeries kind must be spot or period_average')
        rows = []
        for when, value in observations:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise UnitError('Exchange rates must be positive finite numbers')
            rows.append((_date(when), float(value)))
        rows.sort()
        if not rows:
            raise UnitError('RateSeries needs observations')
        if len({d for d, _ in rows}) != len(rows):
            raise UnitError('Duplicate RateSeries dates')
        self.id, self.base, self.quote, self.kind, self.frequency = id, base, quote, kind, frequency
        self.evidence, self.observations = evidence, rows

    def lookup(self, at, *, policy='exact', max_staleness_days=0):
        import bisect
        when = _date(at)
        dates = [d for d, _ in self.observations]
        position = bisect.bisect_right(dates, when) - 1
        if position < 0:
            raise UnitError(f'{self.id}: no observation on or before {when}')
        observed, value = self.observations[position]
        if observed != when:
            if policy != 'previous':
                raise UnitError(f'{self.id}: no observation exactly on {when}; use policy="previous" with max_staleness_days')
            if (when - observed).days > max_staleness_days:
                raise UnitError(f'{self.id}: latest observation {observed} is stale for {when}')
        return {'series': self.id, 'date': observed.isoformat(), 'requested': when.isoformat(), 'value': value,
                'base': self.base, 'quote': self.quote, 'kind': self.kind, 'evidence': self.evidence}

    def average(self, start, end):
        lo, hi = _date(start), _date(end)
        values = [v for d, v in self.observations if lo <= d < hi]
        if not values:
            raise UnitError(f'{self.id}: no observations in [{lo}, {hi})')
        return {'series': self.id, 'period': [lo.isoformat(), hi.isoformat()], 'observations': len(values),
                'value': sum(values) / len(values), 'base': self.base, 'quote': self.quote, 'kind': 'period_average_of_' + self.kind,
                'evidence': self.evidence}


def _currency_parts(unit):
    parsed = parse_unit(unit)
    codes = [(k, v) for k, v in parsed.dims if k.startswith('currency:')]
    if len(codes) != 1 or abs(codes[0][1]) != 1:
        raise UnitError('Unit must contain exactly one currency with exponent +/-1: ' + str(unit))
    return parsed, codes[0][0].split(':', 1)[1], codes[0][1]


def convert_currency(value, from_unit, to_unit, *, at=None, period=None, rates, policy='exact', max_staleness_days=0):
    """Convert between currencies using explicitly supplied RateSeries (direct, inverse, or one cross)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise UnitError('Value must be a finite number')
    if (at is None) == (period is None):
        raise UnitError('Currency conversion needs exactly one of at= (stock) or period=(start, end) (flow average)')
    a, source, exponent = _currency_parts(from_unit)
    b, target, exponent_b = _currency_parts(to_unit)
    if '@' in source or '@' in target:
        raise UnitError('Convert nominal values; constant-price units must be deflated/reflated with deflate()')
    if exponent != exponent_b:
        raise UnitError('Currency exponent differs between units')
    series = list(rates) if isinstance(rates, (list, tuple)) else [rates]
    if not series or not all(isinstance(s, RateSeries) for s in series):
        raise UnitError('rates must be RateSeries objects; hidden constant rates are not allowed')

    def edge(x, y):
        for s in series:
            if (s.base, s.quote) == (x, y):
                return s, False
            if (s.base, s.quote) == (y, x):
                return s, True
        return None

    def rate(x, y):
        found = edge(x, y)
        if not found:
            return None
        s, inverse = found
        obs = s.average(*period) if period else s.lookup(at, policy=policy, max_staleness_days=max_staleness_days)
        return (1 / obs['value'] if inverse else obs['value']), {**obs, 'inverted': inverse}

    steps = []
    if source == target:
        multiplier = 1.0
    else:
        direct = rate(source, target)
        if direct:
            multiplier, obs = direct
            steps.append(obs)
        else:
            hubs = {c for s in series for c in (s.base, s.quote)} - {source, target}
            paths = [(hub, rate(source, hub), rate(hub, target)) for hub in sorted(hubs)]
            paths = [p for p in paths if p[1] and p[2]]
            if not paths:
                raise UnitError(f'No supplied series connects {source} and {target}')
            if len(paths) > 1:
                raise UnitError('Ambiguous cross rate: several intermediate currencies available; supply one path')
            _, first, second = paths[0]
            multiplier = first[0] * second[0]
            steps.extend([first[1], second[1]])
    # Replace the currency dimension so the remaining units convert dimensionally.
    a_dims = tuple((('currency:' + target) if k == 'currency:' + source else k, v) for k, v in a.dims)
    if tuple(sorted(a_dims)) != b.dims:
        raise UnitError(f'Non-currency dimensions differ: {from_unit} -> {to_unit}')
    factor = a.scale / b.scale * multiplier ** exponent
    return {'value': value * factor, 'unit': to_unit, 'from_value': value, 'from_unit': from_unit, 'factor': factor,
            'method': 'currency', 'at': at, 'period': list(period) if period else None, 'steps': steps,
            'series': [{'id': s['series'], **({'date': s['date']} if 'date' in s else {'period': s['period']}),
                        'value': s['value'], 'inverted': s['inverted']} for s in steps]}


class PriceIndexSeries:
    """Dated price-index levels (e.g. CPI, GDP deflator) with an explicit base and currency area."""
    def __init__(self, id, observations, *, base_period, currency, frequency, evidence=None):
        _namespaced(id)
        if not _is_currency(currency):
            raise UnitError('Price index currency must be ISO 4217')
        if frequency not in ('monthly', 'quarterly', 'annual'):
            raise UnitError('frequency must be monthly, quarterly or annual')
        rows = []
        for when, value in observations:
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise UnitError('Index levels must be positive finite numbers')
            rows.append((_date(when), float(value)))
        rows.sort()
        if not rows or len({d for d, _ in rows}) != len(rows):
            raise UnitError('Price index needs unique dated observations')
        self.id, self.base_period, self.currency, self.frequency = id, str(base_period), currency, frequency
        self.observations, self.evidence = rows, evidence

    def level(self, period, *, allow_partial=False):
        """Period 'YYYY' (annual mean of available sub-periods), 'YYYY-MM', 'YYYY-Qn' or ISO date."""
        text = str(period)
        expected = {'monthly': 12, 'quarterly': 4, 'annual': 1}[self.frequency]
        if re.fullmatch(r'\d{4}', text):
            values = [v for d, v in self.observations if d.year == int(text)]
            if len(values) < expected and not allow_partial:
                raise UnitError(f'{self.id}: {len(values)} of {expected} observations for {text}; pass allow_partial=True')
        elif re.fullmatch(r'\d{4}-Q[1-4]', text):
            year, quarter = int(text[:4]), int(text[-1])
            months = {3 * quarter - 2, 3 * quarter - 1, 3 * quarter}
            values = [v for d, v in self.observations if d.year == year and d.month in months]
            need = {'monthly': 3, 'quarterly': 1}.get(self.frequency)
            if need is None:
                raise UnitError('Annual index cannot provide quarterly levels')
            if len(values) < need and not allow_partial:
                raise UnitError(f'{self.id}: incomplete quarter {text}')
        elif re.fullmatch(r'\d{4}-\d{2}', text) or re.fullmatch(r'\d{4}-\d{2}-\d{2}', text):
            when = _date(text + '-01' if len(text) == 7 else text)
            values = [v for d, v in self.observations if d == when]
        else:
            raise UnitError('Unsupported period: ' + text)
        if not values:
            raise UnitError(f'{self.id}: no index level for {text}')
        return {'series': self.id, 'period': text, 'value': sum(values) / len(values), 'observations': len(values),
                'base_period': self.base_period, 'evidence': self.evidence}


def deflate(value, unit, *, from_period, to_period, index, allow_partial=False):
    """Re-express a nominal (or constant-price) amount in constant ``to_period`` prices.

    real = value * P(to_period) / P(from_period). The output unit is CUR_<to_period>.
    """
    if not isinstance(index, PriceIndexSeries):
        raise UnitError('deflate() requires a PriceIndexSeries; hidden deflators are not allowed')
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise UnitError('Value must be a finite number')
    parsed, code, exponent = _currency_parts(unit)
    currency, _, basis = code.partition('@')
    if exponent != 1:
        raise UnitError('deflate() supports amounts with currency in the numerator')
    if currency != index.currency:
        raise UnitError(f'Price index {index.id} is for {index.currency}, not {currency}; convert currency first')
    if basis and basis != str(from_period):
        raise UnitError(f'Unit is constant {basis} prices but from_period={from_period}')
    if not re.fullmatch(r'\d{4}', str(to_period)):
        raise UnitError('to_period must be a year for constant-price units (e.g. 2017)')
    start, end = index.level(from_period, allow_partial=allow_partial), index.level(to_period, allow_partial=allow_partial)
    factor = end['value'] / start['value']
    rest = [(k, v) for k, v in parsed.dims if not k.startswith('currency:')]
    suffix = parsed.text.replace(currency + ('_' + basis if basis else ''), f'{currency}_{to_period}', 1) \
        if currency in parsed.text else f'{currency}_{to_period}'
    if rest and suffix == parsed.text:
        raise UnitError('Could not construct the constant-price unit string')
    return {'value': value * factor, 'unit': suffix, 'from_value': value, 'from_unit': unit, 'factor': factor,
            'method': 'deflation', 'price_basis': 'real', 'base_period': str(to_period),
            'series': [{'id': index.id, 'period': start['period'], 'value': start['value']},
                       {'id': index.id, 'period': end['period'], 'value': end['value']}],
            'steps': [start, end]}


def rebase_index(value, unit, *, to_base, index):
    """Rebase an index level: new = value / P(to_base) * 100 using the supplied index series."""
    parsed = parse_unit(unit)
    if 'index' not in parsed.tags:
        raise UnitError('rebase_index() requires an index unit such as index_1982_1984_100')
    if not isinstance(index, PriceIndexSeries):
        raise UnitError('rebase_index() requires a PriceIndexSeries')
    level = index.level(to_base)
    return {'value': value / level['value'] * 100, 'unit': f'index_{to_base}_100', 'from_value': value, 'from_unit': unit,
            'factor': 100 / level['value'], 'method': 'rebase', 'series': [{'id': index.id, 'period': level['period'],
                                                                             'value': level['value']}], 'steps': [level]}


def percentage_point_change(before, after, unit='percent'):
    """Difference between two ratios expressed in percentage points (never 'percent')."""
    scale = convert(1, unit, 'percent')['factor']
    return {'value': (after - before) * scale, 'unit': 'percentage_point', 'from_unit': unit}


def validate_conversion(conversion, unit):
    """Validate an optional record ``conversion`` block against the record's unit."""
    if not isinstance(conversion, dict):
        raise UnitError('conversion must be an object')
    required = {'from_value', 'from_unit', 'factor', 'method'}
    if not required <= set(conversion):
        raise UnitError('conversion requires ' + ', '.join(sorted(required)))
    method = conversion['method']
    if method not in ('dimensional', 'commodity', 'explicit', 'currency', 'deflation', 'rebase'):
        raise UnitError('Unknown conversion method')
    factor = conversion['factor']
    if isinstance(factor, bool) or not isinstance(factor, (int, float)) or not math.isfinite(factor) or factor == 0:
        raise UnitError('conversion factor must be a nonzero finite number')
    parse_unit(conversion['from_unit'])
    parse_unit(unit)
    if method == 'dimensional':
        expected = conversion_factor(conversion['from_unit'], unit)['factor']
        if not math.isclose(expected, factor, rel_tol=1e-9):
            raise UnitError('conversion factor disagrees with the unit registry')
    if method in ('currency', 'deflation', 'rebase', 'explicit'):
        refs = conversion.get('series') if method != 'explicit' else conversion.get('steps')
        if not isinstance(refs, list) or not refs:
            raise UnitError(method + ' conversion requires dated series/factor references')
        if method != 'explicit':
            for ref in refs:
                if not isinstance(ref, dict):
                    raise UnitError('series references must be objects')
                _namespaced(ref.get('id'))
                if not ('date' in ref or 'period' in ref):
                    raise UnitError('series references need a date or period')
    return True


def describe_registry():
    return {'units': sorted(_REGISTRY), 'ambiguous': dict(_AMBIGUOUS), 'multipliers': dict(_MULTIPLIERS),
            'commodity_factors': [dict(zip(('commodity', 'from', 'to', 'factor', 'status', 'source'), row))
                                  for row in COMMODITY_FACTORS], 'sources': dict(SOURCES), 'day_counts': dict(_DAY_COUNTS)}
