"""The trade panel: exporter x importer x HS6 x year flows with the importer's MFN tariff and gravity covariates.

Three stages, all streaming and all joined on published codes:

``concordance``
    The UNSD correlation tables HS2022->HS2017 and HS2017->HS1992 as ``trade_concordances``
    publishes them: one row per source code with its targets and UNSD's relationship label.
``tariffs``
    MFN applied tariffs (simple average of the national lines in an HS6 subheading, percent) per
    reporter x year, expressed in the two BACI nomenclatures. WITS/TRAINS publishes each reporter-year
    in the HS revision the reporter used (HS2007, HS2012, HS2017 or HS2022). A rate is carried to a
    BACI code only when the correlation table makes it exact: every source code that maps to the
    target maps to nothing else, and all of those sources carry the same rate (a simple average over
    a union of line sets whose averages are equal is that value). HS2007 and HS2012 schedules have no
    published concordance here and are not translated. The USITC HTS release (HS2022-based 8-digit
    lines, general column) is averaged to HS6 the same way and translated identically.
``panel``
    One record per (nomenclature, exporter, importer, year) holding the HS6 table of that directed
    flow: value (thousand USD), quantity (metric tons) and the importer's MFN applied rate for the
    product. BACI reconciles each flow from the exporter's and the importer's declarations, so there
    is no separate reporter figure: reporter r's exports to partner p are the record r->p, and r's
    imports from p are the record p->r. The record also carries CEPII gravity covariates for the pair
    and year. Records are grouped this way (not one line per HS6 cell) because the 360 million cells
    would otherwise repeat the pair, year, tariff source and gravity block on every line;
    :func:`cells` yields the flat exporter x importer x HS6 x year cells.

Identity: countries join on ISO 3166 alpha-3 codes, which BACI, WITS and CEPII gravity each publish
(BACI areas without an ISO code, such as ``baci:area:490`` "Other Asia, nes", join nothing). The EU
common external tariff (WITS reporter ``918``) is applied to an importer only in years CEPII gravity
publishes ``eu_member = 1`` for it; after gravity's last year the last published membership is
carried forward (declared: no accession or withdrawal occurred between 2021 and 2024).

Every value carries ``available_at`` from a declared rule (:data:`RULES`): BACI flows by CEPII's
release calendar, tariffs by the year they were in force or the HTS release date, gravity covariates
by type. BACI and gravity values are also the vintage of one release, which is recorded.
"""
from collections import defaultdict
import json

from .firm import IdTrail, LineSource, _field  # noqa: F401  (LineSource is the stage source for pipeline.py)

SCHEMA = 'worldmodel.panels.trade/1'
DATASET = 'trade_panel'

NOMENCLATURES = {
    'hs92': {'dataset': 'cepii_baci_hs92', 'revision': 'HS1992'},
    'hs17': {'dataset': 'cepii_baci', 'revision': 'HS2017'},
}
CROSSWALKS = {'hs22_hs17': 'un_hs22_hs17_correlation', 'hs17_hs92': 'un_hs17_hs92_correlation'}
COLUMNS = ('product', 'value_kusd', 'quantity_t', 'importer_mfn_applied_pct')

PAIR_METRICS = {
    'bilateral_distance_population_weighted': ('distance_population_weighted_km', 'static'),
    'bilateral_distance_capitals': ('distance_capitals_km', 'static'),
    'contiguity': ('contiguity', 'static'),
    'common_official_language': ('common_official_language', 'static'),
    'colonial_dependency_ever': ('colonial_dependency_ever', 'static'),
    'common_colonizer': ('common_colonizer', 'static'),
    'common_legal_origin': ('common_legal_origin', 'static'),
    'regional_trade_agreement_in_force': ('rta_in_force', 'annual_status'),
}
COUNTRY_METRICS = {
    'gdp_current_usd': ('gdp_current_thousand_usd', 'annual_macro'),
    'population': ('population_thousands', 'annual_macro'),
    'wto_member': ('wto_member', 'annual_status'),
    'eu_member': ('eu_member', 'annual_status'),
}

DEFAULTS = {
    'nomenclatures': ['hs92', 'hs17'],
    'baci_release': 'V202601',
    'baci_release_published': '2026-01-31',
    'baci_first_release_lag_years': 2,
    'gravity_release': 'V202211',
    'gravity_release_published': '2022-11-30',
    'gravity_first_year': 1995,
    'eu_reporter': 'wits:economy:918',
    'eu_membership_carry_forward_through': 2024,
    'tariff_metric': 'mfn_applied_tariff_simple_avg',
    'hts_general_rate_metric': 'general_ad_valorem_rate',
    'evidence_sample': 3,
}

RULES = {
    'baci_release_calendar': ('CEPII releases BACI each January under a version named for that month (V202601 = '
                              'January 2026) with data through two years earlier, so year t is first public in the '
                              'January t+2 release: dated January 31 of t+2. The values are the V202601 vintage, and '
                              'CEPII re-reconciles every past year in each release (revisions: major), so a strict '
                              'as-of reader uses vintage_published instead. For years that predate the BACI series the '
                              'rule date is earlier than any BACI release existed.'),
    'trains_year_in_force': ('A TRAINS MFN applied rate for year t describes the schedule in force in t; dated '
                             'December 31 of t, because a schedule changed during the year is only complete at year end. '
                             'WITS does not publish when UNCTAD ingested it.'),
    'hts_release_date': ('A USITC HTS release is public on the release date its release list publishes; the rates '
                         'hold from its start date.'),
    'gravity_static': ('Time-invariant CEPII gravity covariates (distances, contiguity, common language, colonial '
                       'ties, legal origin) are treated as known before every panel year: a declared exception, since '
                       'they are published in the V202211 release.'),
    'gravity_annual_status': ('RTA in force, WTO membership and EU membership for year t (CEPII coding of legal status '
                              'during t): dated December 31 of t.'),
    'gravity_annual_macro': ('GDP and population for year t (CEPII, from WDI): dated December 31 of t+1, after the '
                             'World Development Indicators update that first publishes year t.'),
    'hs_concordance': ('UNSD correlation tables (HS2022->HS2017, HS2017->HS1992) as trade_concordances publishes them; '
                       'they carry no publication date and are used only to translate tariff codes.'),
}


def parameters(overrides=None):
    merged = json.loads(json.dumps(DEFAULTS))
    merged.update(overrides or {})
    return merged


def flow_available_at(year, params=None):
    lag = (params or DEFAULTS).get('baci_first_release_lag_years', DEFAULTS['baci_first_release_lag_years'])
    return f'{int(year) + lag}-01-31'


def _number(text):
    text = text.strip()
    if text == 'null':
        return None
    return float(text) if any(c in text for c in '.eE') else int(text)


def parse_flow(line):
    """``(year, exporter, importer, product, value, quantity, record_id)`` from a canonical BACI flow line."""
    if '"bilateral_trade_value"' not in line:
        return None
    quantity = None
    head = '{"attributes":{"quantity_t":'
    if line.startswith(head):
        quantity = _number(line[len(head):line.index('}', len(head))])
    importer, product = _field(line, 'importer'), _field(line, 'product')
    record_id, exporter, start = _field(line, 'id'), _field(line, 'subject'), _field(line, 'valid_from')
    at = line.rfind('"value":')
    if None in (importer, product, record_id, exporter, start) or at < 0:
        record = json.loads(line)
        dims = record.get('dimensions') or {}
        return (int(record['valid_from'][:4]), record['subject'], dims['importer'], dims['product'], record['value'],
                (record.get('attributes') or {}).get('quantity_t'), record['id'])
    return (int(start[:4]), exporter, importer, product, _number(line[at + 8:line.rindex('}')]), quantity, record_id)


# ----------------------------------------------------------------------------- concordance

def build_concordance(source, params=None):
    """Rows ``trade_panel:concordance:{crosswalk}:{source code}`` from the UNSD correlation tables."""
    params = parameters(params)
    wanted = set(CROSSWALKS.values())
    grouped = defaultdict(lambda: {'targets': set(), 'relationships': set(), 'ids': []})
    for line in source.lines('trade_concordances'):
        if '"maps_to"' not in line or not any(name in line for name in wanted):
            continue
        record = json.loads(line)
        attributes = record.get('attributes') or {}
        crosswalk = attributes.get('crosswalk')
        if record.get('predicate') != 'maps_to' or crosswalk not in wanted or attributes.get('placeholder_code'):
            continue
        item = grouped[(crosswalk, record['subject'])]
        item['targets'].add(record['object'])
        item['relationships'].add(attributes.get('relationship'))
        item['ids'].append(record['id'])
    ref = source.ref('trade_concordances')
    counts = defaultdict(int)
    for (crosswalk, code), item in sorted(grouped.items()):
        counts[crosswalk] += 1
        trail = IdTrail(params['evidence_sample'])
        for record_id in item['ids']:
            trail.add(record_id)
        yield {'id': f'{DATASET}:concordance:{crosswalk}:{code}', 'schema': SCHEMA, 'crosswalk': crosswalk,
               'source_code': code, 'targets': sorted(item['targets']), 'relationships': sorted(r for r in item['relationships'] if r),
               'rule': 'hs_concordance', 'evidence': [trail.evidence(ref)]}
    yield {'id': f'{DATASET}:concordance:construction', 'schema': SCHEMA, 'crosswalk': None,
           'construction': dict(counts), 'evidence': []}


class Translation:
    """Exact code translation through a correlation table (see the module docstring)."""

    def __init__(self, rows, crosswalk):
        self.sources = defaultdict(set)
        self.targets = {}
        for row in rows:
            if row.get('crosswalk') != crosswalk:
                continue
            code = row['source_code'].split(':', 1)[1]
            targets = {t.split(':', 1)[1] for t in row['targets']}
            self.targets[code] = targets
            for target in targets:
                self.sources[target].add(code)

    def exact_sources(self, target):
        """The source codes whose union is exactly ``target``, or ``None`` when some source is split."""
        sources = self.sources.get(target)
        if not sources or any(self.targets[s] != {target} for s in sources):
            return None
        return sources

    def translate(self, rates):
        """``{target: rate}`` for targets whose exact sources all carry one equal rate."""
        out = {}
        for target in self.sources:
            sources = self.exact_sources(target)
            if sources is None or any(s not in rates for s in sources):
                continue
            values = {rates[s] for s in sources}
            if len(values) == 1:
                out[target] = values.pop()
        return out


# ----------------------------------------------------------------------------- tariffs

REVISION_CODES = {'HS2017': 'hs17', 'HS2022': 'hs22', 'HS1992': 'hs92'}


def _in_nomenclatures(rates, native, translations):
    """``{nomenclature: (rates, translation path)}`` for the BACI nomenclatures reachable from ``native``."""
    out = {}
    if native == 'hs22':
        hs17 = translations['hs22_hs17'].translate(rates)
        out['hs17'] = (hs17, [CROSSWALKS['hs22_hs17']])
        out['hs92'] = (translations['hs17_hs92'].translate(hs17), [CROSSWALKS['hs22_hs17'], CROSSWALKS['hs17_hs92']])
    elif native == 'hs17':
        out['hs17'] = (dict(rates), [])
        out['hs92'] = (translations['hs17_hs92'].translate(rates), [CROSSWALKS['hs17_hs92']])
    elif native == 'hs92':
        out['hs92'] = (dict(rates), [])
    return out


def _hts_hs6(source, params):
    """US HTS general ad valorem rates averaged to HS6 (percent), the release, and construction counts."""
    lines, release, counts = {}, None, defaultdict(int)
    ids = []
    for line in source.lines('usitc_hts_tariffs'):
        if '"hts_release_metadata"' in line:
            record = json.loads(line)
            value = record.get('value') or {}
            if value.get('status') == 'current':
                month, day, year = (value.get('date') or '//').split('/')
                start = (value.get('releaseStartDate') or '//').split('/')
                release = {'name': value.get('name'), 'published': f'{year}-{month}-{day}' if year else None,
                           'start': f'{start[2]}-{start[0]}-{start[1]}' if len(start) == 3 and start[2] else None}
            continue
        if f'"{params["hts_general_rate_metric"]}"' not in line:
            continue
        record = json.loads(line)
        if record.get('metric') != params['hts_general_rate_metric']:
            continue
        code = str(record.get('subject', '')).split(':', 1)[-1]
        if not code.isdigit() or len(code) not in (8, 10) or code.startswith('99') or code.startswith('98'):
            counts['hts_lines_outside_chapters_01_97'] += 1
            continue
        if record.get('value') is None:
            counts['hts_lines_non_numeric_rate'] += 1
            continue
        lines[code] = (float(record['value']) * 100.0, record['id'])
    by_hs6 = defaultdict(list)
    for code, (rate, record_id) in lines.items():
        if len(code) == 10 and code[:8] in lines:
            continue   # the 8-digit tariff line already carries the rate
        by_hs6[code[:6]].append(rate)
        ids.append(record_id)
    counts['hts_rate_lines_used'] = sum(len(v) for v in by_hs6.values())
    return {code: round(sum(v) / len(v), 6) for code, v in by_hs6.items()}, release, counts, sorted(ids)


def build_tariffs(source, concordance_rows, params=None):
    """Rows ``trade_panel:tariff:{nomenclature}:{reporter}:{year}``: MFN applied rates in BACI codes."""
    params = parameters(params)
    rows = list(concordance_rows)
    translations = {key: Translation(rows, name) for key, name in CROSSWALKS.items()}
    schedules = defaultdict(dict)
    trails = {}
    counts = defaultdict(int)
    metric = params['tariff_metric']
    for line in source.lines('wits_trains_tariffs'):
        if f'"{metric}"' not in line:
            continue
        record = json.loads(line)
        dims = record.get('dimensions') or {}
        if record.get('metric') != metric or dims.get('partner') != 'wits:economy:000' or record.get('value') is None:
            continue
        key = (record['subject'], int(str(record['valid_from'])[:4]), dims.get('hs_revision'))
        schedules[key][str(dims.get('product', '')).split(':', 1)[-1]] = float(record['value'])
        trail = trails.get(key)
        if trail is None:
            trail = trails[key] = IdTrail(params['evidence_sample'])
        trail.add(record['id'])
    wits_ref = source.ref('wits_trains_tariffs')
    for (reporter, year, revision), rates in sorted(schedules.items()):
        native = REVISION_CODES.get(revision)
        reachable = _in_nomenclatures(rates, native, translations) if native else {}
        if not reachable:
            counts[f'reporter_years_untranslatable_{revision}'] += 1
        for nomenclature, (translated, path) in sorted(reachable.items()):
            counts[f'rows_{nomenclature}'] += 1
            yield {'id': f'{DATASET}:tariff:{nomenclature}:{reporter}:{year}', 'schema': SCHEMA, 'nomenclature': nomenclature,
                   'reporter': reporter, 'year': year, 'source': 'wits_trains_tariffs', 'native_revision': revision,
                   'translation': path, 'native_codes': len(rates), 'assigned_codes': len(translated),
                   'rates_pct': {code: translated[code] for code in sorted(translated)},
                   'measure': 'MFN applied, simple average of national tariff lines in the HS6 subheading (percent)',
                   'available_at': f'{year}-12-31', 'rule': 'trains_year_in_force',
                   'evidence': [trails[(reporter, year, revision)].evidence(wits_ref)]}
    if source.has('usitc_hts_tariffs'):
        rates, release, hts_counts, ids = _hts_hs6(source, params)
        counts.update(hts_counts)
        if rates and release:
            trail = IdTrail(params['evidence_sample'])
            for record_id in ids:
                trail.add(record_id)
            year = int((release.get('start') or release.get('published') or '0')[:4])
            for nomenclature, (translated, path) in sorted(_in_nomenclatures(rates, 'hs22', translations).items()):
                counts[f'rows_{nomenclature}'] += 1
                yield {'id': f'{DATASET}:tariff:{nomenclature}:iso3:USA:{year}:hts', 'schema': SCHEMA,
                       'nomenclature': nomenclature, 'reporter': 'iso3:USA', 'year': year, 'source': 'usitc_hts_tariffs',
                       'native_revision': 'HS2022 (HTSUS 8-digit lines averaged to their first six digits)',
                       'translation': path, 'native_codes': len(rates), 'assigned_codes': len(translated),
                       'rates_pct': {code: translated[code] for code in sorted(translated)},
                       'measure': ('HTSUS column 1 general ad valorem rate, simple average of 8-digit lines with a numeric '
                                   'ad valorem rate (specific and compound rates and chapter 98/99 provisions excluded)'),
                       'release': release, 'available_at': release.get('published'), 'rule': 'hts_release_date',
                       'evidence': [trail.evidence(source.ref('usitc_hts_tariffs'))]}
    counts['wits_reporter_years'] = len({(r, y) for r, y, _ in schedules})
    yield {'id': f'{DATASET}:tariff:construction', 'schema': SCHEMA, 'nomenclature': None, 'construction': dict(counts),
           'evidence': []}


# ----------------------------------------------------------------------------- gravity

class Gravity:
    """CEPII gravity covariates by pair-year and country-year, as published (run-length windows expanded)."""

    def __init__(self, source, params):
        self.pairs = defaultdict(list)       # (a, b) sorted -> [(first_year, last_year, name, value, record_id)]
        self.countries = {}                  # (iso3, year, name) -> (value, record_id)
        self.last_year = 0
        first = params['gravity_first_year']
        for line in source.lines('cepii_gravity'):
            metric = _field(line, 'metric')
            if metric not in PAIR_METRICS and metric not in COUNTRY_METRICS:
                continue
            record = json.loads(line)
            start, end = int(record['valid_from'][:4]), int(record['valid_to'][:4]) - 1
            self.last_year = max(self.last_year, end)
            if end < first:
                continue
            if metric in PAIR_METRICS:
                partner = (record.get('dimensions') or {}).get('partner')
                if not partner:
                    continue
                key = tuple(sorted((record['subject'], partner)))
                self.pairs[key].append((max(start, first), end, PAIR_METRICS[metric][0], record['value'], record['id']))
            else:
                for year in range(max(start, first), end + 1):
                    self.countries[(record['subject'], year, COUNTRY_METRICS[metric][0])] = (record['value'], record['id'])

    def eu_member(self, country, year, carry_through):
        if year > self.last_year and year <= carry_through:
            year = self.last_year
        found = self.countries.get((country, year, 'eu_member'))
        return bool(found and found[0] == 1)

    def block(self, exporter, importer, year, trail):
        if year > self.last_year:
            return None
        pair = {}
        for first, last, name, value, record_id in self.pairs.get(tuple(sorted((exporter, importer))), ()):
            if first <= year <= last:
                pair[name] = value
                trail.add(record_id)
        sides = {}
        for side, country in (('exporter', exporter), ('importer', importer)):
            values = {}
            for name, _ in COUNTRY_METRICS.values():
                found = self.countries.get((country, year, name))
                if found:
                    values[name] = found[0]
                    trail.add(found[1])
            sides[side] = values
        if not pair and not sides['exporter'] and not sides['importer']:
            return None
        return {'pair': pair, 'exporter': sides['exporter'], 'importer': sides['importer'],
                'available_at': {'static': 'before the panel year (rule gravity_static)',
                                 'annual_status': f'{year}-12-31', 'annual_macro': f'{year + 1}-12-31'},
                'rules': ['gravity_static', 'gravity_annual_status', 'gravity_annual_macro']}


# ----------------------------------------------------------------------------- panel

def build_panel(source, tariff_rows, params=None, tariffs_ref=None):
    """Records ``trade_panel:{nomenclature}:{year}:{exporter}:{importer}`` with the HS6 table of the flow."""
    params = parameters(params)
    tariffs = {}
    for row in tariff_rows:
        if row.get('nomenclature') and row.get('source') == 'wits_trains_tariffs':
            tariffs[(row['nomenclature'], row['reporter'], row['year'])] = row
    gravity = Gravity(source, params) if source.has('cepii_gravity') else None
    gravity_ref = source.ref('cepii_gravity') if gravity else None
    counts = defaultdict(int)
    eu = params['eu_reporter']
    for nomenclature in params['nomenclatures']:
        dataset = NOMENCLATURES[nomenclature]['dataset']
        if not source.has(dataset):
            continue
        ref = source.ref(dataset)
        emitted = set()
        group, key = [], None
        for line in source.lines(dataset):
            flow = parse_flow(line)
            if flow is None:
                continue
            flow_key = flow[:3]
            if flow_key != key:
                if group:
                    yield _record(nomenclature, key, group, ref, tariffs, gravity, gravity_ref, tariffs_ref, eu, params, counts)
                if flow_key in emitted:
                    raise ValueError(f'{dataset}: flows are not grouped by year, exporter, importer at {flow[6]}')
                emitted.add(flow_key)
                key, group = flow_key, []
            group.append(flow)
        if group:
            yield _record(nomenclature, key, group, ref, tariffs, gravity, gravity_ref, tariffs_ref, eu, params, counts)
    yield {'id': f'{DATASET}:panel:construction', 'schema': SCHEMA, 'nomenclature': None, 'construction': dict(counts),
           'rules': RULES, 'gravity_last_year': gravity.last_year if gravity else None, 'evidence': []}


def _tariff_for(tariffs, nomenclature, exporter, importer, year, gravity, eu, params):
    own = tariffs.get((nomenclature, importer, year))
    if own:
        return own, None
    if gravity and (nomenclature, eu, year) in tariffs and \
            gravity.eu_member(importer, year, params['eu_membership_carry_forward_through']):
        via = 'eu_member published by cepii_gravity' if year <= gravity.last_year else \
            f'eu_member published by cepii_gravity for {gravity.last_year}, carried forward'
        return tariffs[(nomenclature, eu, year)], via
    return None, None


def _record(nomenclature, key, group, ref, tariffs, gravity, gravity_ref, tariffs_ref, eu, params, counts):
    year, exporter, importer = key
    tariff, via = _tariff_for(tariffs, nomenclature, exporter, importer, year, gravity, eu, params)
    rates = tariff['rates_pct'] if tariff else {}
    trail = IdTrail(params['evidence_sample'])
    cells, total, with_quantity, with_tariff, tariff_value = [], 0.0, 0, 0, 0.0
    for _, _, _, product, value, quantity, record_id in group:
        code = product.split(':', 1)[1]
        rate = rates.get(code)
        cells.append([code, value, quantity, rate])
        trail.add(record_id)
        total += value
        with_quantity += quantity is not None
        if rate is not None:
            with_tariff += 1
            tariff_value += value
    evidence = [trail.evidence(ref)]
    tariff_block = None
    if tariff:
        tariff_block = {'record_id': tariff['id'], 'reporter': tariff['reporter'], 'source': tariff['source'],
                        'native_revision': tariff['native_revision'], 'translation': tariff['translation'],
                        'applied_via': via, 'available_at': tariff['available_at'], 'rule': tariff['rule']}
        if via and gravity and gravity.eu_member(exporter, year, params['eu_membership_carry_forward_through']):
            tariff_block['intra_customs_union'] = True
        if tariffs_ref:
            evidence.append({'input': dict(tariffs_ref), 'records': 1, 'record_ids': [tariff['id']]})
    gravity_block = None
    if gravity:
        gravity_trail = IdTrail(params['evidence_sample'])
        gravity_block = gravity.block(exporter, importer, year, gravity_trail)
        if gravity_block and gravity_trail.count:
            evidence.append(gravity_trail.evidence(gravity_ref))
    counts[f'{nomenclature}_records'] += 1
    counts[f'{nomenclature}_cells'] += len(cells)
    counts[f'{nomenclature}_cells_with_tariff'] += with_tariff
    counts[f'{nomenclature}_records_with_gravity'] += gravity_block is not None
    return {'id': f'{DATASET}:{nomenclature}:{year}:{exporter}:{importer}', 'schema': SCHEMA, 'nomenclature': nomenclature,
            'year': year, 'exporter': exporter, 'importer': importer,
            'available_at': flow_available_at(year, params), 'rule': 'baci_release_calendar',
            'vintage': {'release': params['baci_release'], 'published': params['baci_release_published']},
            'columns': list(COLUMNS), 'cells': cells,
            'totals': {'value_kusd': round(total, 3), 'cells': len(cells), 'cells_with_quantity': with_quantity,
                       'cells_with_tariff': with_tariff, 'value_kusd_with_tariff': round(tariff_value, 3)},
            'importer_tariff': tariff_block, 'gravity': gravity_block,
            'linked': {'tariff': with_tariff > 0, 'gravity': gravity_block is not None},
            'evidence': evidence}


# ----------------------------------------------------------------------------- reading the published panel

def cells(record):
    """Flat cells ``{nomenclature, year, exporter, importer, product, value_kusd, quantity_t, importer_mfn_applied_pct}``."""
    for cell in record.get('cells') or []:
        yield {'nomenclature': record['nomenclature'], 'year': record['year'], 'exporter': record['exporter'],
               'importer': record['importer'], **dict(zip(record['columns'], cell))}


def coverage(records):
    """Records, cells and trade value by nomenclature and year, and the share joined to a tariff and to gravity."""
    table = {}
    for record in records:
        if not record.get('nomenclature') or 'cells' not in record:
            continue
        slot = table.setdefault((record['nomenclature'], record['year']), defaultdict(float))
        totals = record['totals']
        slot['records'] += 1
        slot['cells'] += totals['cells']
        slot['value_kusd'] += totals['value_kusd']
        slot['cells_with_tariff'] += totals['cells_with_tariff']
        slot['value_kusd_with_tariff'] += totals['value_kusd_with_tariff']
        slot['records_with_tariff_source'] += record['importer_tariff'] is not None
        slot['records_with_gravity'] += record['gravity'] is not None
        slot['cells_with_quantity'] += totals['cells_with_quantity']
    out = {}
    for (nomenclature, year), slot in sorted(table.items()):
        cells_n, value = slot['cells'] or 1, slot['value_kusd'] or 1
        out.setdefault(nomenclature, {})[str(year)] = {
            'records': int(slot['records']), 'cells': int(slot['cells']), 'value_kusd': round(slot['value_kusd'], 1),
            'cell_share_with_tariff': round(slot['cells_with_tariff'] / cells_n, 4),
            'value_share_with_tariff': round(slot['value_kusd_with_tariff'] / value, 4),
            'record_share_with_tariff_source': round(slot['records_with_tariff_source'] / slot['records'], 4),
            'record_share_with_gravity': round(slot['records_with_gravity'] / slot['records'], 4),
            'cell_share_with_quantity': round(slot['cells_with_quantity'] / cells_n, 4)}
    return out


def history(lines, reporter, partner, product, nomenclature=None):
    """Year-by-year exports (reporter->partner) and imports (partner->reporter) of one HS6 product."""
    needles = (f':{reporter}:{partner}","importer":"{partner}"', f':{partner}:{reporter}","importer":"{reporter}"')
    code = product.split(':', 1)[-1]
    years = {}
    for line in lines:
        if not any(needle in line for needle in needles):
            continue
        record = json.loads(line)
        if nomenclature and record['nomenclature'] != nomenclature:
            continue
        direction = 'exports' if record['exporter'] == reporter else 'imports'
        slot = years.setdefault((record['nomenclature'], record['year']), {})
        cell = next((c for c in record['cells'] if c[0] == code), None)
        slot[direction] = None if cell is None else dict(zip(record['columns'][1:], cell[1:]))
        slot[direction + '_available_at'] = record['available_at']
        slot[direction + '_tariff_source'] = record['importer_tariff']
        if direction == 'exports' or 'gravity' not in slot:
            slot['gravity'] = record['gravity']
    return [{'nomenclature': n, 'year': y, **slot} for (n, y), slot in sorted(years.items())]
