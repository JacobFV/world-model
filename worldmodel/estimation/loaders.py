"""Catalog-driven loaders: published normalized datasets into estimator inputs.

The estimation layer reads either an :class:`~worldmodel.estimation.data.ObservationSet`
(series components) or a family's native data mapping (``worldmodel.models``). This
module builds both from datasets published in the catalog, following the
``catalog_dataset`` declarations in ``requirements.json``.

Three rules shape the implementation:

* **Stream and filter early.** Normalized datasets reach 16M records and 900 MB
  gzip. Every loader scans the published ``records.jsonl.gz`` once, discards
  lines by substring before JSON parsing, and keeps only the records a declared
  source selects. Nothing is materialized except the selected series.
* **Preserve evidence.** Every loaded record keeps its dataset reference
  (``dataset``, ``stage``, ``version``) and its source record id, so estimates and
  reports carry lineage. :class:`Evidence` summarizes record ids by digest when
  there are many.
* **Say which vintage policy applies.** Published metric names, units and
  availability fields rarely match a :class:`SeriesRequirement` exactly. A
  :class:`SeriesSource` declares the translation *and* the availability policy
  it can support (``real_time`` when the records carry publication or vintage
  dates, ``retrospective`` when the dataset only has an acquisition timestamp).
  :func:`observation_set` reports the policy each series needs; callers must pass
  the matching ``vintage_policy``, and the plan records it.

``SeriesSource.metric``/``unit`` name what the *dataset* publishes;
``requirement`` names the series in ``requirements.json``. Values are multiplied
by ``scale`` when the publisher's unit differs from the requirement's unit.
"""
from dataclasses import dataclass
from datetime import date, timedelta
import gzip
import json
import math

from ..util import digest
from .data import ObservationSet, MAX_EVIDENCE_IDS

SCHEMA = 'worldmodel.estimation.loaders/1'
DEFAULT_STAGE = 'normalized'
MAX_EVIDENCE_SAMPLE = MAX_EVIDENCE_IDS


class MissingData(ValueError):
    """A declared catalog dataset, stage or series is not published locally."""


# ----------------------------------------------------------------------------- catalog access

def catalog_ref(store, dataset, stage=DEFAULT_STAGE, version=None):
    """Resolve ``dataset[/stage][@version]`` to a verified reference."""
    try:
        if version is not None:
            ref = {'dataset': dataset, 'stage': stage, 'version': version}
            store.verify(ref)
            return ref
        return store.latest(dataset, stage)
    except (FileNotFoundError, NotADirectoryError) as error:
        raise MissingData(f'{dataset}/{stage} is not published in this catalog') from error


def _records_path(store, ref):
    directory = store.version_dir(ref)
    for name in ('records.jsonl', 'records.jsonl.gz'):
        path = directory / name
        if path.exists():
            return path
    raise MissingData(f'No records output for {ref["dataset"]}@{ref["version"]}')


def stream_records(store, ref, *, needles=(), verify=False):
    """Stream records, discarding lines that contain none of ``needles``.

    ``needles`` are canonical-JSON fragments such as ``'"metric":"revenue"'``.
    Dataset records are written as canonical JSON (sorted keys, no spaces), so a
    substring test is an exact and very cheap prefilter; it only ever admits more
    lines than needed, never fewer, and the caller filters properly afterwards.
    """
    if verify:
        store.verify(ref)
    path = _records_path(store, ref)
    opener = gzip.open if path.suffix == '.gz' else open
    needles = tuple(needles)
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if needles and not any(needle in line for needle in needles):
                continue
            yield json.loads(line)


class Evidence:
    """Dataset inputs and source record ids behind a loaded input."""

    def __init__(self):
        self.inputs = []
        self.record_ids = []
        self.counts = {}

    def add_input(self, ref):
        ref = dict(ref)
        if ref not in self.inputs:
            self.inputs.append(ref)
        return ref

    def add(self, ref, record_id, label=None):
        self.add_input(ref)
        if record_id:
            self.record_ids.append(record_id)
        if label is not None:
            self.counts[label] = self.counts.get(label, 0) + 1

    def reference(self):
        ids = sorted(set(self.record_ids))
        out = {'schema': SCHEMA, 'inputs': [dict(ref) for ref in self.inputs], 'record_count': len(self.record_ids),
               'distinct_record_ids': len(ids), 'record_ids_digest': digest(ids), 'series_counts': dict(sorted(self.counts.items()))}
        if len(ids) <= MAX_EVIDENCE_SAMPLE:
            out['record_ids'] = ids
        return out


# ----------------------------------------------------------------------------- declared sources

@dataclass(frozen=True)
class SeriesSource:
    """One published series mapped onto one ``requirements.json`` series.

    ``unit=None`` accepts whatever unit the vintage published. FRED rebases index and
    chained-dollar series, so a single series carries several unit tokens
    (``index_1967_100`` then ``index_1982_1984_100``; eight base years for GDPC1). A
    point-in-time selection takes, for every period, the latest vintage available at the
    cutoff, and a vintage publishes its whole history on one base, so each frame is
    internally base-consistent. Estimators that read only within-frame ratios and growth
    rates are therefore unaffected; the published unit and base stay in the record's
    attributes, and the requirement's unit token is applied as a label.
    """
    requirement: str
    dataset: str
    metric: str
    unit: str = None
    subject: str = None
    dimensions: tuple = ()          # ((key, value), ...) required dimension values
    absent_dimensions: tuple = ()   # dimension keys that must be absent
    scale: float = 1.0
    availability: str = 'retrospective'   # 'real_time' when records carry a publication/vintage date
    stage: str = DEFAULT_STAGE
    note: str = ''

    def needles(self):
        series_id = dict(self.dimensions).get('series_id')
        return (f'"series_id":"{series_id}"',) if series_id else (f'"metric":"{self.metric}"',)

    def matches(self, record):
        if record.get('kind') != 'observation' or record.get('metric') != self.metric:
            return False
        if self.unit is not None and record.get('unit') != self.unit:
            return False
        if self.subject is not None and record.get('subject') != self.subject:
            return False
        dimensions = record.get('dimensions') or {}
        for key, value in self.dimensions:
            if dimensions.get(key) != value:
                return False
        return all(key not in dimensions for key in self.absent_dimensions)


@dataclass(frozen=True)
class Blocked:
    """A component or family that cannot run until named series are published."""
    reason: str
    missing: tuple = ()             # ((series, dataset), ...)
    available: tuple = ()


def _translate(record, source, requirement, ref):
    """Copy a published record onto the requirement's metric, unit and selectors, keeping lineage.

    ``SeriesRequirement.matches`` also selects on ``attributes.source_series`` and
    ``dimensions.geography`` when the requirement declares them, so the translation
    sets those to the requirement's values and keeps what the dataset published under
    ``published_metric``/``published_source_series``.
    """
    value = record.get('value')
    if value is not None:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            return None
        value = float(value) * source.scale
    attributes = dict(record.get('attributes') or {})
    published = attributes.get('source_series')
    attributes.update({'published_metric': source.metric, 'source_dataset': source.dataset})
    if record.get('unit') is not None and record.get('unit') != requirement.unit:
        attributes['published_unit'] = record['unit']
    base = (record.get('dimensions') or {}).get('base_period')
    if base is not None:
        attributes['base_period'] = base
    if published is not None:
        attributes['published_source_series'] = published
    if requirement.source_series is not None:
        attributes['source_series'] = requirement.source_series
    out = dict(record)
    out['metric'] = requirement.metric
    out['unit'] = requirement.unit
    out['value'] = value
    out['attributes'] = attributes
    if requirement.geography is not None:
        out['dimensions'] = dict(record.get('dimensions') or {}, geography=requirement.geography)
    out['_input'] = dict(ref)
    return out


def observation_set(component, store, *, sources=None, versions=None, requirements=None, estimator=None):
    """Build an ``(ObservationSet, evidence, policy)`` triple for a series component.

    ``policy`` is the weakest vintage policy every selected series supports:
    ``strict`` when all sources carry real-time publication dates, otherwise
    ``retrospective`` (timing leakage controlled, revision leakage possible and
    reported by the estimate's data audit).
    """
    from .families import estimator_for
    declared = sources if sources is not None else COMPONENT_SOURCES.get(component)
    if isinstance(declared, Blocked):
        raise MissingData(f'{component}: {declared.reason}')
    if not declared:
        raise MissingData(f'No catalog sources declared for component {component}')
    estimator = estimator or estimator_for(component)
    requirements = {r.name: r for r in (requirements or estimator.all_requirements())}
    by_dataset = {}
    for source in declared:
        if source.requirement not in requirements:
            raise ValueError(f'{component}: source maps to unknown series {source.requirement}')
        by_dataset.setdefault((source.dataset, source.stage), []).append(source)
    records, evidence = [], Evidence()
    for (dataset, stage), group in sorted(by_dataset.items()):
        ref = catalog_ref(store, dataset, stage, (versions or {}).get(dataset))
        needles = sorted({needle for source in group for needle in source.needles()})
        for record in stream_records(store, ref, needles=needles):
            for source in group:
                if not source.matches(record):
                    continue
                requirement = requirements[source.requirement]
                translated = _translate(record, source, requirement, ref)
                if translated is None:
                    break
                records.append(translated)
                evidence.add(ref, record.get('id'), source.requirement)
                break
    missing = [name for name in requirements if not evidence.counts.get(name)]
    if missing:
        raise MissingData(f'{component}: no records found for {sorted(missing)}')
    policy = 'strict' if all(source.availability == 'real_time' for source in declared) else 'retrospective'
    return ObservationSet(records, inputs=evidence.inputs), evidence.reference(), policy


# ----------------------------------------------------------------------------- component sources

FRED_POLICY_RATE = SeriesSource('policy_rate', 'fred_policy_rate', 'policy_rate', 'percent', subject='geo:US',
                                availability='real_time', note='ALFRED vintages: attributes.realtime_start is the publication date')
# unit is left open: the published token changed from 'USD/barrel' to 'USD_per_barrel' in a rebuild
# with identical values, and DCOILWTICO has one unit per vintage anyway.
FRED_OIL_PRICE = SeriesSource('crude_price', 'fred_oil_price', 'oil_price', None, subject='geo:US',
                              availability='real_time', note='ALFRED vintages of DCOILWTICO')

EIA_US = {'subject': 'iso3:USA', 'availability': 'retrospective'}

def panel(requirement, series_id, metric, unit, *, dataset='fred_macro_panel'):
    """One ALFRED-vintage series from the FRED panel, pinned by series id."""
    return SeriesSource(requirement, dataset, metric, unit, subject='geo:US',
                        dimensions=(('series_id', series_id),), availability='real_time',
                        note=f'{series_id}: every observation carries realtime_start/realtime_end')


COMPONENT_SOURCES = {
    'interest_pass_through': (
        panel('loan_rate', 'DPRIME', 'prime_rate_daily', 'percent'),
        FRED_POLICY_RATE,
    ),
    'deposit_rate_pass_through': (
        panel('deposit_rate', 'SNDR', 'national_savings_deposit_rate', 'percent'),
        FRED_POLICY_RATE,
    ),
    'default_hazard': (
        panel('delinquency_rate', 'DRCCLACBS', 'credit_card_delinquency_rate', 'percent'),
        panel('unemployment_rate', 'UNRATE', 'unemployment_rate', 'percent'),
        FRED_POLICY_RATE,
    ),
    'deposit_growth': (
        panel('deposits', 'DPSACBW027SBOG', 'commercial_bank_deposits', 'billion_USD'),
        FRED_POLICY_RATE,
    ),
    'credit_growth': (
        panel('consumer_credit', 'TOTALSL', 'consumer_credit_outstanding', 'billion_USD'),
    ),
    'energy_purchasing': (
        panel('real_sales', 'RRSFS', 'real_retail_sales', 'million_USD_1982_1984_cpi_adjusted'),
        FRED_OIL_PRICE,
        FRED_POLICY_RATE,
    ),
    'labor_demand': (
        panel('employment', 'PAYEMS', 'nonfarm_payroll_employment', 'thousand_persons'),
        panel('output', 'INDPRO', 'industrial_production_index', None),   # rebased 13 times since 1927
    ),
    'policy_rule': (
        panel('policy_rate', 'FEDFUNDS', 'federal_funds_rate', 'percent'),
        panel('price_index', 'CPIAUCSL', 'consumer_price_index', None),   # 1967 base before 1988-02
        panel('real_gdp', 'GDPC1', 'real_gdp', None),                    # eight chained-dollar bases
        panel('potential_gdp', 'GDPPOT', 'potential_gdp', None),
    ),
    'population_growth_rate': (
        SeriesSource('population', 'census_population', 'population', 'people', subject='geo:US',
                     absent_dimensions=('basis',), availability='real_time',
                     note='PEP vintages 2004-2025 plus the 2000-2010 intercensal series; '
                          'attributes.released_at is the file publication date'),
    ),
    'inventory_balance': (
        SeriesSource('crude_stocks', 'eia_energy', 'crude_oil_commercial_stocks_excl_spr', 'Thousand Barrels', **EIA_US),
        SeriesSource('production', 'eia_energy', 'crude_oil_field_production', 'Thousand Barrels per Day', **EIA_US),
        SeriesSource('imports', 'eia_energy', 'crude_oil_imports', 'Thousand Barrels per Day', **EIA_US),
        SeriesSource('exports', 'eia_energy', 'crude_oil_exports', 'Thousand Barrels per Day', **EIA_US),
        SeriesSource('refinery_input', 'eia_energy', 'refiner_net_input_crude_oil', 'Thousand Barrels per Day', **EIA_US),
    ),
    'demand_price_elasticity': (
        SeriesSource('quantity', 'eia_energy', 'motor_gasoline_product_supplied', 'Thousand Barrels per Day', **EIA_US),
        SeriesSource('retail_price', 'eia_energy', 'gasoline_retail_price_regular', 'Dollars per Gallon', **EIA_US),
        FRED_OIL_PRICE,
    ),
    'price_adjustment': (
        SeriesSource('retail_price', 'eia_energy', 'gasoline_retail_price_regular', 'Dollars per Gallon', **EIA_US),
        SeriesSource('inventory', 'eia_energy', 'motor_gasoline_total_stocks', 'Thousand Barrels', **EIA_US),
        FRED_OIL_PRICE,
    ),
}

# ------------------------------------------------- alternative source sets for the same component
#
# A component can be attempted on more than one published series. Each alternative is a named
# loader so a pre-registered attempt can select it by ``loader.function``; the estimator's
# ``overrides`` (declared in the same attempt) rename the series in the requirement, so the
# estimate's data audit says which object was actually fitted.

POPULATION_POPTHM = (
    SeriesSource('population', 'fred_macro_panel', 'resident_population', 'people', subject='geo:US',
                 dimensions=(('series_id', 'POPTHM'),), availability='real_time',
                 note='POPTHM ALFRED vintages (325 vintages from 1999-07-30): monthly national population, '
                      'aggregated to annual with the requirement\'s "last" rule. requirements.json declares '
                      'POPTHM as a source for this series alongside Census PEP.'),
)

DEPOSIT_RATE_SUBSTITUTES = {
    # FDIC National Rates, the programme that publishes SNDR, before the 2021 methodology change.
    'SAVNRNJ': ('national_rate_non_jumbo_savings', 'fred_deposit_rates'),
    'MMNRNJ': ('national_rate_non_jumbo_money_market', 'fred_deposit_rates'),
    # The weighted average rate paid on the deposit components of M2 (Board of Governors).
    'M2OWN': ('m2_own_rate', 'fred_deposit_rates'),
}


def population_popthm_data(store, *, versions=None, estimator=None):
    """``population_growth_rate`` on FRED POPTHM instead of the Census PEP vintage files."""
    return observation_set('population_growth_rate', store, sources=POPULATION_POPTHM, versions=versions,
                           estimator=estimator)


def deposit_rate_substitute_data(store, *, series_id, versions=None, estimator=None):
    """``deposit_rate_pass_through`` on a longer-history deposit rate than SNDR.

    SNDR begins 2021-04, so it cannot supply both the 36 observations the component needs to fit
    and the 24 holdout forecasts it declares. ``series_id`` selects a substitute deposit rate from
    ``fred_deposit_rates``; it is a *different estimand* from the declared SNDR series and the
    attempt that uses it declares that in its overrides.
    """
    if series_id not in DEPOSIT_RATE_SUBSTITUTES:
        raise MissingData(f'Unknown deposit-rate substitute {series_id!r}; '
                          f'known: {sorted(DEPOSIT_RATE_SUBSTITUTES)}')
    metric, dataset = DEPOSIT_RATE_SUBSTITUTES[series_id]
    sources = (SeriesSource('deposit_rate', dataset, metric, 'percent', subject='geo:US',
                            dimensions=(('series_id', series_id),), availability='real_time',
                            note=f'{series_id}: every observation carries realtime_start/realtime_end'),
               FRED_POLICY_RATE)
    return observation_set('deposit_rate_pass_through', store, sources=sources, versions=versions,
                           estimator=estimator)


BLOCKED_COMPONENTS = {
    'field_diffusion_transport': Blocked('No per-cell field panel with a declared topology is published; EPA AQS county PM2.5 '
                                         'is the declared source and epa_aqs_daily is not built.',
                                         missing=(('cell concentrations', 'epa_aqs_daily'),)),
    'bilateral_flow_gravity': Blocked('FAF5 origin-destination freight tonnage and OD distances are not published.',
                                      missing=(('flow FAF5 tons', 'freight'), ('distance OD centroids', 'freight'))),
}


# ----------------------------------------------------------------------------- SEC quarterly flows

SEC_CONCEPTS = {'cash': 'us-gaap:CashAndCashEquivalentsAtCarryingValue', 'revenue': 'us-gaap:Revenues',
                'operating_costs': 'us-gaap:CostsAndExpenses', 'capital_expenditure': 'us-gaap:PaymentsToAcquirePropertyPlantAndEquipment'}
SEC_FORMS = ('10-Q', '10-K')
QUARTER_DAYS = (80, 100)
YEAR_DAYS = (350, 380)


def _day(value):
    return date.fromisoformat(str(value)[:10])


def _quarter_label(end):
    """Calendar quarter containing a fiscal period end: fiscal calendars are labeled, not re-dated."""
    start = date(end.year, 3 * ((end.month - 1) // 3) + 1, 1)
    months = start.month - 1 + 3
    return start, date(start.year + months // 12, months % 12 + 1, 1)


def sec_quarterly_observations(store, issuer, *, version=None, forms=SEC_FORMS, concepts=None):
    """Quarterly cash levels and discrete quarterly flows for one issuer, with filing-date knowledge time.

    companyfacts durations are as filed. Income-statement items usually appear as
    three-month durations, while cash-flow items (capital expenditure) appear only
    year-to-date, and the fourth quarter appears only inside the annual duration.
    Discrete quarters are therefore recovered by differencing consecutive durations
    that share a fiscal-year start (``YTD_i - YTD_(i-1)``, which turns FY into Q4),
    and a period reported on its own as three months is used directly. A derived
    quarter's availability is the latest filing date of its components, and it keeps
    every component record id.
    """
    concepts = concepts or SEC_CONCEPTS
    ref = catalog_ref(store, 'sec_company_assets', DEFAULT_STAGE, version)
    wanted = {value: key for key, value in concepts.items()}
    instants, durations = {}, {}
    for record in stream_records(store, ref, needles=(f'"subject":"{issuer}"',)):
        if record.get('kind') != 'observation' or record.get('subject') != issuer or record.get('unit') != 'USD':
            continue
        dimensions = record.get('dimensions') or {}
        name = wanted.get(dimensions.get('concept'))
        if name is None or dimensions.get('form') not in forms or record.get('value') is None:
            continue
        attributes = record.get('attributes') or {}
        filed = str(record.get('observed_at'))[:10]
        if dimensions.get('period_type') == 'instant':
            end = _day(record['valid_from'])
            key = (name, end)
            if key not in instants or filed > instants[key]['filed']:
                instants[key] = {'value': float(record['value']), 'filed': filed, 'id': record.get('id')}
            continue
        start, end = attributes.get('period_start'), attributes.get('period_end')
        if not start or not end:
            continue
        start, end = _day(start), _day(end)
        if not QUARTER_DAYS[0] <= (end - start).days + 1 <= YEAR_DAYS[1]:
            continue
        key = (name, start, end)
        if key not in durations or filed > durations[key]['filed']:
            durations[key] = {'value': float(record['value']), 'filed': filed, 'id': record.get('id')}
    return ref, instants, _derive_quarters(durations)


def _derive_quarters(durations):
    """Discrete quarterly flows from as-filed durations.

    Durations that share a fiscal-year start are cumulative, so consecutive
    differences are quarters (the annual duration minus the nine-month duration is
    the fourth quarter). A duration reported on its own as roughly three months is a
    quarter already; a reported quarter always wins over a derived one.
    """
    groups = {}
    for (name, start, end), item in durations.items():
        groups.setdefault((name, start), []).append((end, item))
    quarters = {}

    def offer(name, end, value, filed, ids, basis):
        key = (name, end)
        current = quarters.get(key)
        if current is None or (current['basis'] != 'reported_quarter' and basis == 'reported_quarter'):
            quarters[key] = {'value': value, 'filed': filed, 'ids': [i for i in ids if i], 'basis': basis}

    for (name, start), entries in sorted(groups.items(), key=str):
        entries.sort(key=lambda entry: entry[0])
        previous = None
        for end, item in entries:
            span = (end - start).days + 1
            if previous is None:
                if QUARTER_DAYS[0] <= span <= QUARTER_DAYS[1]:
                    offer(name, end, item['value'], item['filed'], [item['id']], 'reported_quarter')
            else:
                previous_end, previous_item = previous
                if QUARTER_DAYS[0] <= (end - previous_end).days <= QUARTER_DAYS[1]:
                    offer(name, end, item['value'] - previous_item['value'], max(item['filed'], previous_item['filed']),
                          [item['id'], previous_item['id']], 'cumulative_difference')
            previous = (end, item)
    for (name, start, end), item in sorted(durations.items(), key=str):
        if not YEAR_DAYS[0] <= (end - start).days + 1 <= YEAR_DAYS[1]:
            continue
        members = [key for key in quarters if key[0] == name and start <= key[1] < end
                   and quarters[key]['basis'] == 'reported_quarter']
        if len(members) != 3 or (name, end) in quarters:
            continue
        offer(name, end, item['value'] - sum(quarters[key]['value'] for key in members),
              max([item['filed']] + [quarters[key]['filed'] for key in members]),
              [item['id']] + [i for key in members for i in quarters[key]['ids']], 'annual_minus_three_quarters')
    return quarters


def cash_balance_data(store, issuer, *, version=None, estimator=None):
    """``(ObservationSet, evidence, 'strict')`` for ``cash_balance`` on one issuer."""
    from .families import estimator_for
    estimator = estimator or estimator_for('cash_balance')
    requirements = {r.name: r for r in estimator.all_requirements()}
    ref, instants, quarters = sec_quarterly_observations(store, issuer, version=version)
    evidence = Evidence()
    rows = {}
    for (name, end), item in list(instants.items()) + list(quarters.items()):
        label, label_end = _quarter_label(end)
        current = rows.get((name, label))
        if current is None or end > current['end']:
            rows[(name, label)] = {'end': end, 'label_end': label_end, **item}
    records = []
    for (name, label), item in sorted(rows.items(), key=str):
        requirement = requirements[name]
        ids = item.get('ids') or [item.get('id')]
        records.append({'kind': 'observation', 'id': '+'.join(i for i in ids if i), 'subject': issuer,
                        'metric': requirement.metric, 'unit': requirement.unit, 'value': item['value'],
                        'valid_from': label.isoformat(), 'valid_to': item['label_end'].isoformat(),
                        'observed_at': item['filed'],
                        'attributes': {'published_at': item['filed'], 'fiscal_period_end': item['end'].isoformat(),
                                       'basis': item.get('basis', 'reported_instant'), 'source_dataset': 'sec_company_assets'},
                        'dimensions': {'concept': SEC_CONCEPTS[name]}, '_input': dict(ref)})
        evidence.add(ref, records[-1]['id'], name)
    missing = [name for name in requirements if not evidence.counts.get(name)]
    if missing:
        raise MissingData(f'cash_balance {issuer}: no records for {sorted(missing)}')
    return ObservationSet(records, inputs=[ref]), evidence.reference(), 'strict'


# ----------------------------------------------------------------------------- family data mappings

def _month(value):
    return str(value)[:7]


def conflict_data(store, *, countries=None, country_count=20, start='2000-01', end=None, rank_end=None, release='26.1',
                  violence='state_based', versions=None):
    """UCDP GED country-month event counts (+ V-Dem polyarchy) as the ``conflict`` family mapping.

    ``neighbors`` is omitted: no contiguity dataset (CShapes) is published, so the
    neighbour-excitation term is identified only from the panel's common shocks.
    """
    versions = versions or {}
    ucdp = catalog_ref(store, 'ucdp_conflicts', DEFAULT_STAGE, versions.get('ucdp_conflicts'))
    evidence = Evidence()
    counts, totals = {}, {}
    for record in stream_records(store, ucdp, needles=('"metric":"organized_violence_events"',)):
        if record.get('kind') != 'observation' or record.get('metric') != 'organized_violence_events':
            continue
        dimensions = record.get('dimensions') or {}
        if dimensions.get('release') != release or dimensions.get('type_of_violence') != violence:
            continue
        country, month = record.get('subject'), _month(record.get('valid_from'))
        if not country.startswith('iso3:') or record.get('value') is None:
            continue
        if month < start or (end is not None and month > end):
            continue
        counts[(country, month)] = {'count': int(record['value']), 'id': record.get('id')}   # GED event counts are integers
        if rank_end is None or month <= rank_end:
            totals[country] = totals.get(country, 0.0) + float(record['value'])
    if not counts:
        raise MissingData('No UCDP monthly country event counts matched the declared release and violence type')
    if countries is None:
        countries = [c for _, c in sorted(((-total, c) for c, total in totals.items()))][:country_count]
    countries = sorted(countries)
    months = sorted({month for _, month in counts})
    polyarchy, vdem_ref = {}, None
    vdem_ref = catalog_ref(store, 'vdem', DEFAULT_STAGE, versions.get('vdem'))
    for record in stream_records(store, vdem_ref, needles=('"metric":"vdem_v2x_polyarchy"',)):
        if record.get('kind') != 'observation' or record.get('metric') != 'vdem_v2x_polyarchy':
            continue
        if record.get('subject') not in set(countries) or record.get('value') is None:
            continue
        polyarchy[(record['subject'], str(record['valid_from'])[:4])] = {'value': float(record['value']), 'id': record.get('id')}
    events = []
    for month in months:
        year = str(int(month[:4]) - 1)   # previous year: the current year's V-Dem is not published during the year
        for country in countries:
            item = counts.get((country, month))
            covariate = polyarchy.get((country, year))
            if covariate is None:
                continue
            events.append({'country': country, 'month': month, 'count': item['count'] if item else 0,
                           'polyarchy': covariate['value']})
            if item:
                evidence.add(ucdp, item['id'], 'events')
            evidence.add(vdem_ref, covariate['id'], 'polyarchy')
    data = {'events': events, 'covariates': ['polyarchy'], 'model': 'hawkes',
            'information_time': 'valid_time', 'revisions': 'major'}
    return data, evidence.reference()


def _daily_bars(store, symbols, *, start, end, versions=None):
    ref = catalog_ref(store, 'alpaca_daily_bars', DEFAULT_STAGE, (versions or {}).get('alpaca_daily_bars'))
    wanted = {f'ticker:US:{symbol}': symbol for symbol in symbols}
    rows, ids = [], []
    for record in stream_records(store, ref, needles=tuple(f'"subject":"{key}"' for key in wanted)):
        if record.get('kind') != 'observation' or record.get('metric') != 'close_price_total_return_adjusted':
            continue
        symbol = wanted.get(record.get('subject'))
        day = str(record.get('valid_from'))[:10]
        if symbol is None or record.get('value') is None or not start <= day <= end:
            continue
        rows.append({'symbol': symbol, 'date': day, 'close': float(record['value'])})
        ids.append(record.get('id'))
    return ref, rows, ids


def _daily_policy_rate(store, *, start, end, versions=None):
    """Latest-vintage daily DFF, as a decimal daily rate (percent per year / 36000 * 100)."""
    ref = catalog_ref(store, 'fred_policy_rate', DEFAULT_STAGE, (versions or {}).get('fred_policy_rate'))
    best = {}
    for record in stream_records(store, ref, needles=('"metric":"policy_rate"',)):
        if record.get('kind') != 'observation' or record.get('metric') != 'policy_rate' or record.get('value') is None:
            continue
        day = str(record.get('valid_from'))[:10]
        if not start <= day <= end:
            continue
        vintage = str((record.get('attributes') or {}).get('realtime_start') or record.get('observed_at'))
        current = best.get(day)
        if current is None or vintage > current['vintage']:
            best[day] = {'value': float(record['value']), 'vintage': vintage, 'id': record.get('id')}
    return ref, best


def assets_data(store, *, symbols, factor_symbol='SPY', start='2016-01-01', end='2024-12-31', versions=None):
    """Alpaca adjusted daily bars with a market factor (the factor symbol's excess return) and a DFF risk-free rate."""
    if factor_symbol in symbols:
        raise ValueError('The factor symbol must not be one of the scored symbols')
    evidence = Evidence()
    ref, rows, ids = _daily_bars(store, list(symbols) + [factor_symbol], start=start, end=end, versions=versions)
    for record_id in ids:
        evidence.add(ref, record_id, 'bars')
    rate_ref, rates = _daily_policy_rate(store, start=start, end=end, versions=versions)
    factor_closes = sorted((r['date'], r['close']) for r in rows if r['symbol'] == factor_symbol)
    bars = [r for r in rows if r['symbol'] != factor_symbol]
    risk_free, factors = [], []
    for (previous_day, previous), (day, close) in zip(factor_closes, factor_closes[1:]):
        rate = rates.get(day)
        if rate is None or close <= 0 or previous <= 0:
            continue
        daily = rate['value'] / 100.0 / 252.0
        risk_free.append({'date': day, 'rf': daily})
        factors.append({'date': day, 'market_excess': math.log(close / previous) - daily})
        evidence.add(rate_ref, rate['id'], 'risk_free')
    data = {'bars': bars, 'factors': factors, 'factor_names': ['market_excess'], 'risk_free': risk_free,
            'information_time': 'valid_time', 'revisions': 'minor'}
    return data, evidence.reference()


def commodities_data(store, *, start='2010-01-01', end='2024-12-31', versions=None):
    """Weekly U.S. crude balance sheet (EIA WPSR) with the WTI spot price as the ``commodities`` mapping.

    Flows published as thousand barrels per day are converted to thousand barrels per
    week. Refiner net input is used as consumption, and net imports are imports minus
    exports, so the balance identity is the published one (its discrepancy is reported
    by the fit).
    """
    versions = versions or {}
    evidence = Evidence()
    eia = catalog_ref(store, 'eia_energy', DEFAULT_STAGE, versions.get('eia_energy'))
    metrics = {'crude_oil_commercial_stocks_excl_spr': ('ending_stocks', 1.0),
               'crude_oil_field_production': ('production', 7.0),
               'crude_oil_imports': ('imports', 7.0),
               'crude_oil_exports': ('exports', 7.0),
               'refiner_net_input_crude_oil': ('consumption', 7.0)}
    weeks = {}
    for record in stream_records(store, eia, needles=tuple(f'"metric":"{m}"' for m in metrics)):
        if record.get('kind') != 'observation' or record.get('subject') != 'iso3:USA':
            continue
        target = metrics.get(record.get('metric'))
        if target is None or record.get('value') is None:
            continue
        ending = (_day(record['valid_to']) - timedelta(days=1)).isoformat()
        if not start <= ending <= end:
            continue
        weeks.setdefault(ending, {})[target[0]] = float(record['value']) * target[1]
        evidence.add(eia, record.get('id'), target[0])
    price_ref = catalog_ref(store, 'fred_oil_price', DEFAULT_STAGE, versions.get('fred_oil_price'))
    daily = {}
    for record in stream_records(store, price_ref, needles=('"metric":"oil_price"',)):
        if record.get('kind') != 'observation' or record.get('metric') != 'oil_price' or record.get('value') is None:
            continue
        day = str(record.get('valid_from'))[:10]
        if not start <= day <= end:
            continue
        vintage = str((record.get('attributes') or {}).get('realtime_start') or record.get('observed_at'))
        current = daily.get(day)
        if current is None or vintage > current['vintage']:
            daily[day] = {'value': float(record['value']), 'vintage': vintage, 'id': record.get('id')}
    balances = []
    for ending in sorted(weeks):
        row = weeks[ending]
        if not all(key in row for key in ('ending_stocks', 'production', 'imports', 'exports', 'consumption')):
            continue
        window = [(day, item) for day, item in daily.items() if (_day(ending) - timedelta(days=6)).isoformat() <= day <= ending]
        if not window:
            continue
        price = math.fsum(item['value'] for _, item in window) / len(window)
        for _, item in window:
            evidence.add(price_ref, item['id'], 'price')
        balances.append({'date': ending, 'price': price, 'production': row['production'], 'consumption': row['consumption'],
                         'ending_stocks': row['ending_stocks'], 'net_imports': row['imports'] - row['exports']})
    data = {'balances': balances, 'supply_lag': 1, 'information_time': 'valid_time', 'revisions': 'minor'}
    return data, evidence.reference()


def _is_sector(code):
    """NAICS sector codes are two digits, or two hyphenated two-digit codes (31-33, 44-45, 48-49)."""
    parts = code.split('-')
    return len(parts) <= 2 and all(len(part) == 2 and part.isdigit() for part in parts)


#: QCEW aggregation level 54 is "state, by NAICS sector", one row per state x sector x ownership.
QCEW_SECTOR_AGGLVL = '54'


def _qcew_regional_data(store, *, level, start_year, end_year, versions, ownership, agglvl_code):
    """QCEW annual average employment by state and NAICS sector from ``bls_labor``.

    The annual-average rows of the QCEW singlefiles cover 1990 onward, which is the
    longer panel the CBP attempt named as its fix: three usable growth years there
    made the between-year component of the predictive spread unestimable. Monthly rows
    from the quarterly files carry the same aggregation level, so ``period_type`` must be
    ``annual_average`` or every year is counted thirteen times.
    """
    if level != 'state':
        raise ValueError('The QCEW regional loader is declared at state level only')
    ref = catalog_ref(store, 'bls_labor', DEFAULT_STAGE, (versions or {}).get('bls_labor'))
    evidence = Evidence()
    rows, namespaces = {}, set()
    for record in stream_records(store, ref, needles=(f'"agglvl_code":"{agglvl_code}"',)):
        if record.get('kind') != 'observation' or record.get('metric') != 'employment' or record.get('value') is None:
            continue
        dimensions = record.get('dimensions') or {}
        if dimensions.get('survey') != 'QCEW' or dimensions.get('agglvl_code') != agglvl_code:
            continue
        if dimensions.get('ownership') != ownership or dimensions.get('period_type') != 'annual_average':
            continue
        if dimensions.get('frequency') != 'annual':
            continue
        region, published = str(dimensions.get('geography') or ''), str(dimensions.get('industry') or '')
        if not region.startswith('geo:US:state:') or region.count(':') != 3 or not published.startswith('naics'):
            continue
        code = published.split(':')[-1]
        if not _is_sector(code):
            continue
        # QCEW labels each year with the NAICS revision in force (naics2002, 2007, 2012, 2017, 2022).
        # Two-digit sectors are comparable across those revisions, so the panel uses the bare sector
        # code; keeping the published namespace would split every sector into five unrelated industries
        # and make the base-year shares zero for every later year.
        industry = f'naics_sector:{code}'
        namespaces.add(published.split(':')[0])
        year = int(str(record['valid_from'])[:4])
        if (start_year is not None and year < start_year) or (end_year is not None and year > end_year):
            continue
        key = (region, industry, year)
        if key in rows:      # one annual average per state-sector-year; a duplicate means the filter is wrong
            raise MissingData(f'QCEW published two annual averages for {key}; refine the declared selection')
        rows[key] = {'employment': float(record['value']), 'id': record.get('id')}
    employment = []
    for (region, industry, year), item in sorted(rows.items(), key=str):
        employment.append({'region': region, 'industry': industry, 'year': year, 'date': f'{year}-12-31',
                           'employment': item['employment']})
        evidence.add(ref, item['id'], 'employment')
    if len(employment) < 500:
        raise MissingData(f'Only {len(employment)} QCEW state-sector-year rows matched the declared selection')
    data = {'employment': employment, 'design': f'shift_share_correlational_qcew_state_naics2_{ownership}',
            'information_time': 'valid_time', 'revisions': 'minor',
            'construction': {'rows': len(employment), 'years': sorted({r['year'] for r in employment}),
                             'regions': len({r['region'] for r in employment}),
                             'industries': len({r['industry'] for r in employment}),
                             'ownership': ownership, 'agglvl_code': agglvl_code,
                             'published_naics_namespaces': sorted(namespaces)}}
    return data, evidence.reference()


def regional_data(store, *, program='cbp', level='state', start_year=None, end_year=None, versions=None,
                  ownership='private', agglvl_code=QCEW_SECTOR_AGGLVL):
    """Employment by state and NAICS sector as the ``regional`` mapping (CBP or QCEW)."""
    if program == 'qcew':
        return _qcew_regional_data(store, level=level, start_year=start_year, end_year=end_year, versions=versions,
                                   ownership=ownership, agglvl_code=agglvl_code)
    ref = catalog_ref(store, 'census_business', DEFAULT_STAGE, (versions or {}).get('census_business'))
    evidence = Evidence()
    rows = {}
    for record in stream_records(store, ref, needles=('"metric":"employment"',)):
        if record.get('kind') != 'observation' or record.get('metric') != 'employment' or record.get('value') is None:
            continue
        dimensions = record.get('dimensions') or {}
        subject, industry = record.get('subject', ''), str(dimensions.get('industry') or '')
        if dimensions.get('program') != program or dimensions.get('legal_form') not in (None, 'all'):
            continue
        if level == 'state' and ':state:' not in subject:
            continue
        code = industry.split(':')[-1]
        if not _is_sector(code):
            continue
        year = int(str(record['valid_from'])[:4])
        if (start_year is not None and year < start_year) or (end_year is not None and year > end_year):
            continue
        key = (subject, industry, year)
        rows[key] = {'employment': float(record['value']), 'id': record.get('id')}
    employment = []
    for (region, industry, year), item in sorted(rows.items(), key=str):
        employment.append({'region': region, 'industry': industry, 'year': year, 'date': f'{year}-12-31',
                           'employment': item['employment']})
        evidence.add(ref, item['id'], 'employment')
    if not employment:
        raise MissingData('No CBP employment rows matched the declared program, level and industry depth')
    data = {'employment': employment, 'design': 'shift_share_correlational_cbp_state_naics2',
            'information_time': 'valid_time', 'revisions': 'minor'}
    return data, evidence.reference()


# ----------------------------------------------------------------------------- constructed national series

def national_unemployment_rate(store, *, start=None, end=None, versions=None):
    """National SA unemployment rate aggregated from LAUS state labour-force levels.

    ``bls_labor`` publishes no national CPS series (LNS14000000 is absent from this
    build), so the national rate is built as ``100 * sum(unemployed) / sum(labour force)``
    over the seasonally adjusted state series (LASST*, 1976 onward, 51 areas). This is a
    construction, not the published CPS headline rate, and every attempt that uses it says
    so. LAUS carries no vintages, so the result is current-vintage only.
    """
    ref = catalog_ref(store, 'bls_labor', DEFAULT_STAGE, (versions or {}).get('bls_labor'))
    totals = {}
    for record in stream_records(store, ref, needles=('"metric":"unemployed"', '"metric":"labor_force"')):
        if record.get('kind') != 'observation' or record.get('value') is None:
            continue
        metric, subject = record.get('metric'), str(record.get('subject') or '')
        dimensions = record.get('dimensions') or {}
        if metric not in ('unemployed', 'labor_force') or not subject.startswith('geo:US:state:'):
            continue
        if dimensions.get('seasonal_adjustment') != 'SA' or dimensions.get('frequency') != 'monthly':
            continue
        month = str(record['valid_from'])[:7]
        if (start is not None and month < start) or (end is not None and month > end):
            continue
        item = totals.setdefault(month, {'unemployed': 0.0, 'labor_force': 0.0, 'ids': [], 'areas': set()})
        item[metric] += float(record['value'])
        item['ids'].append(record.get('id'))
        item['areas'].add(subject)
    out = {}
    for month, item in sorted(totals.items()):
        if item['labor_force'] <= 0 or not item['unemployed']:
            continue
        out[month] = {'value': 100.0 * item['unemployed'] / item['labor_force'], 'areas': len(item['areas']),
                      'ids': item['ids'], 'digest': digest(sorted(i for i in item['ids'] if i))}
    return ref, out


def national_cps_unemployment_rate(store, *, start=None, end=None, versions=None, series_id='LNS14000000'):
    """The published national CPS unemployment rate (BLS ``LNS14000000``) from ``bls_labor``.

    The earlier partial ``bls_labor`` build omitted every national CPS series, which is
    why :func:`national_unemployment_rate` had to aggregate the 51 LAUS state series. The
    completed build carries ``LNS14000000`` (1948 onward), so the declared series is
    available directly. It is still a current-vintage download with no ALFRED history, so
    the attempt using it runs under the retrospective policy.
    """
    ref = catalog_ref(store, 'bls_labor', DEFAULT_STAGE, (versions or {}).get('bls_labor'))
    out = {}
    for record in stream_records(store, ref, needles=(f'"series_id":"{series_id}"',)):
        if record.get('kind') != 'observation' or record.get('metric') != 'unemployment_rate' or record.get('value') is None:
            continue
        dimensions = record.get('dimensions') or {}
        if dimensions.get('series_id') != series_id or record.get('subject') != 'geo:US':
            continue
        if dimensions.get('seasonal_adjustment') != 'SA' or dimensions.get('frequency') != 'monthly':
            continue
        month = str(record['valid_from'])[:7]
        if (start is not None and month < start) or (end is not None and month > end):
            continue
        out[month] = {'value': float(record['value']), 'ids': [record.get('id')],
                      'digest': digest([record.get('id')]), 'areas': 1, 'series_id': series_id}
    return ref, out


def fdic_noncurrent_loan_rate(store, *, start=None, end=None, versions=None):
    """Aggregate noncurrent-loan rate of FDIC-insured banks: 100 * sum(noncurrent) / sum(net loans)."""
    ref = catalog_ref(store, 'fdic_bank_financials', DEFAULT_STAGE, (versions or {}).get('fdic_bank_financials'))
    totals = {}
    for record in stream_records(store, ref, needles=('"metric":"bank_noncurrent_loans"', '"metric":"bank_net_loans"')):
        if record.get('kind') != 'observation' or record.get('value') is None:
            continue
        metric = record.get('metric')
        if metric not in ('bank_noncurrent_loans', 'bank_net_loans'):
            continue
        if (record.get('dimensions') or {}).get('period_basis') != 'report_date_stock':
            continue
        day = str(record['valid_from'])[:10]
        if (start is not None and day < start) or (end is not None and day > end):
            continue
        item = totals.setdefault(day, {'bank_noncurrent_loans': 0.0, 'bank_net_loans': 0.0, 'ids': [], 'banks': set()})
        item[metric] += float(record['value'])
        item['ids'].append(record.get('id'))
        item['banks'].add(record.get('subject'))
    out = {}
    for day, item in sorted(totals.items()):
        if item['bank_net_loans'] <= 0:
            continue
        out[day] = {'value': 100.0 * item['bank_noncurrent_loans'] / item['bank_net_loans'], 'banks': len(item['banks']),
                    'ids': item['ids'], 'digest': digest(sorted(i for i in item['ids'] if i))}
    return ref, out


def _derived_record(requirement, subject, valid_from, valid_to, value, ref, *, source, components, extra=None):
    """One constructed observation carrying the requirement's selectors and its components' digest."""
    attributes = {'source_series': requirement.source_series, 'source_dataset': ref['dataset'],
                  'construction': source, 'component_records': components['count'],
                  'component_record_digest': components['digest']}
    attributes.update(extra or {})
    return {'kind': 'observation', 'id': f'derived:{requirement.metric}:{valid_from}', 'subject': subject,
            'metric': requirement.metric, 'unit': requirement.unit, 'value': value,
            'valid_from': valid_from, 'valid_to': valid_to, 'observed_at': '2026-09-15T00:00:00+00:00',
            'attributes': attributes, 'dimensions': {}, '_input': dict(ref)}


UNEMPLOYMENT_SOURCES = ('laus_state_aggregate', 'cps_national')


def default_hazard_data(store, *, start='2010-01-01', end=None, versions=None, overrides=None, estimator=None,
                        unemployment_source='laus_state_aggregate'):
    """``default_hazard`` on the FDIC aggregate noncurrent-loan rate, a constructed national
    unemployment rate and the effective federal funds rate.

    The declared primary series (FRED DRCCLACBS credit-card delinquency) is not published;
    ``requirements.json`` names FDIC call reports as the panel alternative, so the attempt
    must override the delinquency series. Both overrides are declared in the plan, and the
    estimate's audit shows the substituted ``source_series``.
    """
    from .families import estimator_for
    estimator = estimator or estimator_for('default_hazard', overrides=overrides)
    requirements = {r.name: r for r in estimator.all_requirements()}
    evidence, records = Evidence(), []
    fdic_ref, rates = fdic_noncurrent_loan_rate(store, start=start, end=end, versions=versions)
    requirement = requirements['delinquency_rate']
    for day, item in sorted(rates.items()):
        end_day = (_day(day) + timedelta(days=1)).isoformat()
        records.append(_derived_record(requirement, 'us:banks:fdic_insured', day, end_day, item['value'], fdic_ref,
                                       source='100 * sum(bank_noncurrent_loans) / sum(bank_net_loans) over FDIC-insured banks',
                                       components={'count': len(item['ids']), 'digest': item['digest']},
                                       extra={'banks': item['banks']}))
        evidence.add(fdic_ref, records[-1]['id'], 'delinquency_rate')
        evidence.record_ids.extend(i for i in item['ids'] if i)
    if unemployment_source not in UNEMPLOYMENT_SOURCES:
        raise ValueError(f'unemployment_source must be one of {list(UNEMPLOYMENT_SOURCES)}')
    window = dict(start=str(start)[:7], end=None if end is None else str(end)[:7], versions=versions)
    if unemployment_source == 'cps_national':
        bls_ref, unemployment = national_cps_unemployment_rate(store, **window)
        construction = 'BLS CPS LNS14000000 national unemployment rate as published (no aggregation)'
    else:
        bls_ref, unemployment = national_unemployment_rate(store, **window)
        construction = '100 * sum(unemployed) / sum(labor_force) over LAUS seasonally adjusted states'
    requirement = requirements['unemployment_rate']
    for month, item in sorted(unemployment.items()):
        first = date(int(month[:4]), int(month[5:7]), 1)
        records.append(_derived_record(requirement, 'geo:US', first.isoformat(), period_end_day(first).isoformat(),
                                       item['value'], bls_ref, source=construction,
                                       components={'count': len(item['ids']), 'digest': item['digest']},
                                       extra={'areas': item['areas']}))
        evidence.add(bls_ref, records[-1]['id'], 'unemployment_rate')
        evidence.record_ids.extend(i for i in item['ids'] if i)
    rate_source = SeriesSource('policy_rate', 'fred_policy_rate', 'policy_rate', 'percent', subject='geo:US',
                               availability='real_time')
    policy_ref = catalog_ref(store, 'fred_policy_rate', DEFAULT_STAGE, (versions or {}).get('fred_policy_rate'))
    requirement = requirements['policy_rate']
    for record in stream_records(store, policy_ref, needles=rate_source.needles()):
        if not rate_source.matches(record):
            continue
        day = str(record['valid_from'])[:10]
        if (start is not None and day < start) or (end is not None and day > end):
            continue
        translated = _translate(record, rate_source, requirement, policy_ref)
        if translated is not None:
            records.append(translated)
            evidence.add(policy_ref, record.get('id'), 'policy_rate')
    missing = [name for name in requirements if not evidence.counts.get(name)]
    if missing:
        raise MissingData(f'default_hazard: no records for {sorted(missing)}')
    return ObservationSet(records, inputs=evidence.inputs), evidence.reference(), 'retrospective'


def period_end_day(start, months=1):
    total = start.month - 1 + months
    return date(start.year + total // 12, total % 12 + 1, 1)


def _first_published(records_by_period):
    """(period -> ordered [(realtime_start, value, id)]) with the earliest vintage first."""
    return {period: sorted(items) for period, items in records_by_period.items()}


def _vintage_series(store, dataset, metric, *, subject='geo:US', versions=None, start=None, end=None, with_base=False,
                    series_id=None):
    """ALFRED-style vintages: ``{period: [(realtime_start, value, record_id[, base_period]), ...]}``."""
    ref = catalog_ref(store, dataset, DEFAULT_STAGE, (versions or {}).get(dataset))
    periods = {}
    needles = (f'"series_id":"{series_id}"',) if series_id else (f'"metric":"{metric}"',)
    for record in stream_records(store, ref, needles=needles):
        if record.get('kind') != 'observation' or record.get('metric') != metric or record.get('value') is None:
            continue
        if subject is not None and record.get('subject') != subject:
            continue
        if series_id is not None and (record.get('dimensions') or {}).get('series_id') != series_id:
            continue
        attributes = record.get('attributes') or {}
        vintage = str(attributes.get('realtime_start') or (record.get('dimensions') or {}).get('vintage') or record.get('observed_at'))[:10]
        period = str(record['valid_from'])[:10]
        if (start is not None and period < start) or (end is not None and period > end):
            continue
        entry = (vintage, float(record['value']), record.get('id'))
        if with_base:
            entry = entry + (str((record.get('dimensions') or {}).get('base_period')),)
        periods.setdefault(period, []).append(entry)
    return ref, _first_published(periods)


def monetary_data(store, *, start='1990-01', end='2024-12', trend_months=120, okun=2.0, inflation_target=2.0,
                  elb=0.125, versions=None):
    """``monetary`` family rows: policy rate, real-time CPI inflation and an Okun output-gap proxy.

    * ``policy_rate``: monthly mean of the first published DFF vintage of each day.
    * ``inflation``: 12-month CPIAUCSL change read point-in-time — the value of month t is
      its first ALFRED release, and the base month t-12 is the latest vintage available at
      that same release date, so no later revision enters the row.
    * ``output_gap``: ``-okun * (u_t - u*_t)`` with u the constructed national LAUS rate and
      u* its trailing ``trend_months``-month mean, computed only from months before t. This is
      an Okun proxy, **not** the declared GDPC1/GDPPOT gap, and the unemployment input carries
      no vintages, so the row set is declared ``valid_time`` with major revisions.
    """
    evidence = Evidence()
    cpi_ref, cpi = _vintage_series(store, 'fred_cpi', 'consumer_price_index', versions=versions)
    rate_ref, rates = _vintage_series(store, 'fred_policy_rate', 'policy_rate', versions=versions)
    bls_ref, unemployment = national_unemployment_rate(store, versions=versions)
    monthly_rate = {}
    for day, items in rates.items():
        vintage, value, record_id = items[0]
        month = day[:7]
        item = monthly_rate.setdefault(month, {'values': [], 'ids': []})
        item['values'].append(value)
        item['ids'].append(record_id)
    months = sorted(m for m in monthly_rate if start <= m <= end)
    ordered_unemployment = sorted(unemployment)
    rows = []
    for month in months:
        period = f'{month}-01'
        base_month = f'{int(month[:4]) - 1}{month[4:]}-01'
        if period not in cpi or base_month not in cpi or month not in unemployment:
            continue
        release, level, cpi_id = cpi[period][0]
        base = [item for item in cpi[base_month] if item[0] <= release]
        if not base or level <= 0 or base[-1][1] <= 0:
            continue
        history = [unemployment[m]['value'] for m in ordered_unemployment if m < month][-trend_months:]
        if len(history) < trend_months:
            continue
        trend = math.fsum(history) / len(history)
        rate = monthly_rate[month]
        rows.append({'date': period, 'policy_rate': math.fsum(rate['values']) / len(rate['values']),
                     'inflation': 100.0 * (level / base[-1][1] - 1.0),
                     'output_gap': -okun * (unemployment[month]['value'] - trend)})
        evidence.add(cpi_ref, cpi_id, 'inflation')
        evidence.add(cpi_ref, base[-1][2], 'inflation_base')
        for record_id in rate['ids']:
            evidence.add(rate_ref, record_id, 'policy_rate')
        evidence.record_ids.extend(i for i in unemployment[month]['ids'] if i)
        evidence.add(bls_ref, None, 'unemployment')
    if not rows:
        raise MissingData('No monetary rows could be built from fred_cpi, fred_policy_rate and bls_labor')
    data = {'observations': rows, 'inflation_target': inflation_target, 'elb': elb, 'exclude_elb': True,
            'information_time': 'valid_time', 'revisions': 'major'}
    return data, evidence.reference()


def _quarter(day):
    return f'{day[:4]}Q{(int(day[5:7]) - 1) // 3 + 1}'


def _first_release(items, *, not_before=None):
    """The earliest vintage of a period, optionally the earliest published on/after a date.

    Items are ``(realtime_start, value, record_id)`` or, when the caller asked for bases,
    ``(realtime_start, value, record_id, base_period)``; they are returned unchanged.
    """
    for item in items:
        if not_before is None or item[0] >= not_before:
            return item
    return None


def _snapshot(series, as_of):
    """Point-in-time view: each period's latest value published on or before ``as_of``."""
    out = {}
    for period, items in series.items():
        usable = [item for item in items if item[0] <= as_of]
        if usable:
            out[period] = (usable[-1][1], usable[-1][2])
    return out


def _previous_quarters(period, count):
    year, quarter = int(period[:4]), (int(period[5:7]) - 1) // 3
    out = []
    for _ in range(count):
        quarter -= 1
        if quarter < 0:
            year, quarter = year - 1, 3
        out.append(f'{year}-{quarter * 3 + 1:02d}-01')
    return out


def _bases(items, as_of):
    """Bases this series has published on or before ``as_of``, newest vintage first."""
    out = {}
    for item in items:
        if item[0] <= as_of:
            out.setdefault(item[3], []).append(item)
    return out


def monetary_realtime_data(store, *, start='1995-01-01', end='2024-10-01', inflation_target=2.0, elb=0.125,
                           versions=None):
    """``monetary`` family rows at the rule's own quarterly frequency, from first-release vintages
    with every ratio taken inside one base year.

    Each input is read as its initial release: the policy rate is the first published FEDFUNDS
    vintage of each month averaged over the quarter, inflation is the four-quarter CPIAUCSL
    change, and the output gap is ``100 * (GDPC1 / GDPPOT - 1)``.

    FRED rebases chained-dollar and index series, and since the panel rebuild every vintage
    carries its own ``dimensions.base_period``. Both ratios are therefore formed **within one
    base**: the gap uses the newest base on which *both* GDPC1 and GDPPOT have published by the
    target quarter's first-release date (CBO lags BEA by months at a rebasing, so the newest
    common base is often the previous one), and inflation uses CPI vintages of one base for both
    endpoints. This replaces the trailing-mean workaround the earlier run needed when every
    vintage carried a single mislabelled unit, so the gap is now a true level.

    No row contains a later revision, so ``revisions`` is declared ``'none'``; the one
    publication lag sits inside the one-step-ahead horizon the family scores.
    """
    versions = versions or {}
    evidence = Evidence()
    cpi_ref, cpi = _vintage_series(store, 'fred_cpi', 'consumer_price_index', versions=versions, with_base=True)
    panel = dict(versions=versions, subject='geo:US', with_base=True)
    gdp_ref, gdp = _vintage_series(store, 'fred_macro_panel', 'real_gdp', **panel)
    potential_ref, potential = _vintage_series(store, 'fred_macro_panel', 'potential_gdp', **panel)
    rate_ref, rates = _vintage_series(store, 'fred_macro_panel', 'federal_funds_rate', **panel)
    rows, skipped = [], {'no_first_release': 0, 'no_common_base': 0, 'missing_input': 0}
    for period in sorted(gdp):
        if not start <= period <= end:
            continue
        release = _first_release(gdp[period])
        base_period = f'{int(period[:4]) - 1}{period[4:]}'
        if release is None or period not in potential or period not in cpi or base_period not in cpi:
            skipped['no_first_release'] += 1
            continue
        as_of = release[0]
        # Newest base on which both GDP measures have published by the GDP first release.
        actual_bases, capacity_bases = _bases(gdp[period], as_of), _bases(potential[period], as_of)
        shared = sorted(set(actual_bases) & set(capacity_bases), key=lambda base: max(i[0] for i in actual_bases[base]))
        if not shared:
            skipped['no_common_base'] += 1
            continue
        base = shared[-1]
        level, capacity = actual_bases[base][-1], capacity_bases[base][-1]
        # Inflation endpoints from one CPI base.
        cpi_now = _first_release(cpi[period])
        if cpi_now is None:
            skipped['missing_input'] += 1
            continue
        earlier = [item for item in cpi[base_period] if item[0] <= cpi_now[0] and item[3] == cpi_now[3]]
        months = [period] + [f'{period[:4]}-{int(period[5:7]) + offset:02d}-01' for offset in (1, 2)
                             if int(period[5:7]) + offset <= 12]
        values, rate_ids = [], []
        for month in months:
            item = _first_release(rates[month]) if month in rates else None
            if item:
                values.append(item[1])
                rate_ids.append(item[2])
        if not earlier or not values or capacity[1] <= 0 or earlier[-1][1] <= 0:
            skipped['missing_input'] += 1
            continue
        rows.append({'date': period, 'policy_rate': math.fsum(values) / len(values),
                     'inflation': 100.0 * (cpi_now[1] / earlier[-1][1] - 1.0),
                     'output_gap': 100.0 * (level[1] / capacity[1] - 1.0)})
        evidence.add(gdp_ref, level[2], f'real_gdp:base_{base}')
        evidence.add(potential_ref, capacity[2], f'potential_gdp:base_{base}')
        evidence.add(cpi_ref, cpi_now[2], 'inflation')
        evidence.add(cpi_ref, earlier[-1][2], 'inflation_base')
        for record_id in rate_ids:
            evidence.add(rate_ref, record_id, 'policy_rate')
    if len(rows) < 12:
        raise MissingData(f'Only {len(rows)} real-time monetary quarters could be built (skipped {skipped})')
    data = {'observations': rows, 'inflation_target': inflation_target, 'elb': elb, 'exclude_elb': True,
            'information_time': 'valid_time', 'revisions': 'none'}
    return data, evidence.reference()


# ----------------------------------------------------------------------------- elections

#: Democratic (+1) or Republican (-1) president in office on each House election day.
#: A public constitutional fact, not fitted data; kept here so the regressor is auditable.
PRESIDENT_PARTY = {1976: -1, 1978: 1, 1980: 1, 1982: -1, 1984: -1, 1986: -1, 1988: -1, 1990: -1, 1992: -1,
                   1994: 1, 1996: 1, 1998: 1, 2000: 1, 2002: -1, 2004: -1, 2006: -1, 2008: -1, 2010: 1,
                   2012: 1, 2014: 1, 2016: 1, 2018: -1, 2020: -1, 2022: 1, 2024: 1}
MEDSL_PARTY = {'DEMOCRAT': 'dem', 'REPUBLICAN': 'rep'}
#: FEC ``CAND_PTY_AFFILIATION`` codes that are the two major parties (DFL is Minnesota's Democrats).
FEC_PARTY = {'DEM': 'dem', 'DFL': 'dem', 'REP': 'rep', 'Rep': 'rep'}


def _house_returns(store, *, start_year, end_year, versions, evidence):
    """Per district-cycle two-party House votes from MIT MEDSL general-election returns."""
    ref = catalog_ref(store, 'mit_election_returns', DEFAULT_STAGE, (versions or {}).get('mit_election_returns'))
    races = {}
    for record in stream_records(store, ref, needles=('"office":"US HOUSE"',)):
        dimensions = record.get('dimensions') or {}
        if record.get('kind') != 'observation' or dimensions.get('office') != 'US HOUSE':
            continue
        if dimensions.get('stage') != 'gen' or dimensions.get('special'):
            continue
        year = dimensions.get('election_year')
        district = dimensions.get('district')
        state = (record.get('attributes') or {}).get('state_po')
        if year is None or district is None or state is None or record.get('value') is None:
            continue
        year = int(year)
        if not start_year <= year <= end_year:
            continue
        key = (f'{state}-{int(district):02d}', year)
        item = races.setdefault(key, {'dem': 0.0, 'rep': 0.0, 'total': None, 'date': None, 'other': 0.0})
        item['date'] = item['date'] or str(record['valid_from'])[:10]
        if record.get('metric') == 'total_votes_cast':
            item['total'] = float(record['value'])
        elif record.get('metric') == 'votes_received' and not dimensions.get('writein'):
            side = MEDSL_PARTY.get(dimensions.get('party'))
            item[side if side else 'other'] += float(record['value'])
        else:
            continue
        evidence.add(ref, record.get('id'), 'house_returns')
    return ref, races


def _house_fundamentals(store, *, cycles, versions, evidence):
    """FEC incumbency status and cycle receipts per district-cycle and party."""
    candidates = catalog_ref(store, 'fec_candidates', DEFAULT_STAGE, (versions or {}).get('fec_candidates'))
    seats, by_candidate = {}, {}
    for record in stream_records(store, candidates, needles=('"fec_candidacy"',)):
        if record.get('predicate') != 'fec_candidacy':
            continue
        value = record.get('value') or {}
        if value.get('office') != 'US House' or value.get('cycle') not in cycles:
            continue
        state, district = value.get('office_state'), value.get('district')
        side = FEC_PARTY.get(value.get('party'))
        if not state or district is None or side is None:
            continue
        try:
            key = (f'{state}-{int(district):02d}', int(value['cycle']))
        except (TypeError, ValueError):
            continue
        item = seats.setdefault(key, {'incumbent': set(), 'dem': 0.0, 'rep': 0.0, 'candidates': 0})
        item['candidates'] += 1
        if value.get('incumbent_challenger') == 'incumbent':
            item['incumbent'].add(side)
        by_candidate[(record['subject'], key[1])] = (key, side)
        evidence.add(candidates, record.get('id'), 'candidacy')
    finance = catalog_ref(store, 'fec', DEFAULT_STAGE, (versions or {}).get('fec'))
    coverage = {}
    for record in stream_records(store, finance, needles=('"metric":"total_receipts"',)):
        if record.get('kind') != 'observation' or record.get('metric') != 'total_receipts' or record.get('value') is None:
            continue
        dimensions = record.get('dimensions') or {}
        if dimensions.get('report_basis') != 'fec_weball_candidate_summary':
            continue
        target = by_candidate.get((record.get('subject'), dimensions.get('cycle')))
        if target is None:
            continue
        key, side = target
        seats[key][side] += float(record['value'])
        end = str(dimensions.get('coverage_end_date') or '')[:10]
        if end:
            coverage.setdefault(key[1], []).append(end)
        evidence.add(finance, record.get('id'), 'receipts')
    return seats, {cycle: max(ends) for cycle, ends in coverage.items()}


def _economy_growth(store, *, dates, series, metric, months, versions, evidence):
    """Point-in-time national real income growth as published on each election day.

    For every election date the latest vintage of each month available *on that date*
    is taken, and growth is measured over ``months`` inside one base period, so no
    later revision and no rebasing enters the regressor.
    """
    ref, vintages = _vintage_series(store, 'fred_macro_panel', metric, versions=versions, with_base=True,
                                    series_id=series)
    out = {}
    for date_text in sorted(dates):
        snapshot = {}
        for period, items in vintages.items():
            usable = [item for item in items if item[0] <= date_text]
            if usable:
                snapshot[period] = usable[-1]
        if not snapshot:
            continue
        latest = max(snapshot)
        earlier = f'{int(latest[:4]) - months // 12}-{latest[5:]}'
        current, base = snapshot.get(latest), snapshot.get(earlier)
        if current is None or base is None or base[1] <= 0 or current[3] != base[3]:
            continue
        out[date_text] = {'econ': 100.0 * (current[1] / base[1] - 1.0), 'as_of': date_text,
                          'latest_month': latest, 'base_month': earlier, 'base_period': current[3],
                          'vintage': current[0]}
        evidence.add(ref, current[2], 'economy')
        evidence.add(ref, base[2], 'economy_base')
    return ref, out


def elections_data(store, *, start_year=2000, end_year=2024, lean_lookback=3, economy_series='DSPIC96',
                   economy_metric='real_disposable_personal_income', economy_months=12, versions=None):
    """U.S. House district races as the ``elections`` family mapping.

    One row per district-cycle general election (no specials, no primaries):

    * ``dem_share`` — two-party Democratic share of the DEMOCRAT- and
      REPUBLICAN-labelled votes. Fusion-party lines (New York, Connecticut) are not
      credited to the major party they endorse, which is how MEDSL publishes them.
    * ``uncontested`` — ``'D'``/``'R'`` when only one major party received votes.
      The family excludes these from estimation and counts them as safe seats.
    * ``pvi`` — the district's partisan lean: its two-party Democratic share in the
      most recent *contested* cycle within ``lean_lookback`` cycles, minus the
      national two-party Democratic House share in that same cycle. This substitutes
      for the family's declared ``district_presidential_lean`` requirement, which no
      published dataset in this catalog provides (presidential returns are county and
      statewide; counties do not nest inside congressional districts).
    * ``incumbent`` — +1 when the FEC candidate master lists a Democratic incumbent
      and no Republican incumbent for the seat, -1 in the mirror case, 0 for an open
      seat or an ambiguous one (both parties flagged, which happens after
      redistricting).
    * ``dem_receipts`` / ``rep_receipts`` — FEC cycle-to-date total receipts summed
      over that party's candidates. These are *cycle* totals whose coverage ends after
      election day, which is why the family declares receipts a conditional input: the
      holdout tests the mechanism given realized fundraising, not the ability to
      forecast fundraising.
    * ``econ`` — national real disposable personal income growth over
      ``economy_months`` months, read from the vintage available **on election day**
      with both endpoints on one base period.
    * ``president_party``, ``midterm`` — the declared national regressors.

    Only cycles with every input are kept, so the window is bounded by FEC receipts
    (2000 onward) rather than by the returns (1976 onward).
    """
    evidence = Evidence()
    # Returns are read back far enough to build every row's partisan lean from earlier cycles.
    returns_ref, races = _house_returns(store, start_year=start_year - 2 * lean_lookback, end_year=end_year,
                                        versions=versions, evidence=evidence)
    if not races:
        raise MissingData('No MEDSL U.S. House general-election district returns matched the declared window')
    shares, national = {}, {}
    for (district, year), item in races.items():
        dem, rep = item['dem'], item['rep']
        total = national.setdefault(year, {'dem': 0.0, 'rep': 0.0})
        total['dem'] += dem
        total['rep'] += rep
        if dem + rep > 0:
            shares[(district, year)] = {'share': dem / (dem + rep), 'contested': dem > 0 and rep > 0,
                                        'date': item['date'], 'votes': dem + rep, 'total': item['total']}
    national = {year: total['dem'] / (total['dem'] + total['rep']) for year, total in national.items()
                if total['dem'] + total['rep'] > 0}
    cycles = sorted({year for _, year in shares if year >= start_year})
    seats, receipt_coverage = _house_fundamentals(store, cycles=set(cycles), versions=versions, evidence=evidence)
    dates = {item['date'] for (_, year), item in shares.items() if item['date'] and year >= start_year}
    economy_ref, economy = _economy_growth(store, dates=dates, series=economy_series, metric=economy_metric,
                                           months=economy_months, versions=versions, evidence=evidence)
    rows, skipped = [], {'no_lean': 0, 'no_economy': 0, 'no_fec_cycle': 0}
    clamped = {'negative_party_receipts': 0}
    for (district, year), item in sorted(shares.items()):
        if year < start_year:
            continue
        date_text = item['date']
        if date_text not in economy:
            skipped['no_economy'] += 1
            continue
        lean = None
        for back in range(1, lean_lookback + 1):
            earlier = shares.get((district, year - 2 * back))
            if earlier is not None and earlier['contested'] and (year - 2 * back) in national:
                lean = earlier['share'] - national[year - 2 * back]
                break
        if lean is None:
            skipped['no_lean'] += 1
            continue
        seat = seats.get((district, year))
        if seat is None:
            skipped['no_fec_cycle'] += 1
            continue
        incumbent = 0
        if seat['incumbent'] == {'dem'}:
            incumbent = 1
        elif seat['incumbent'] == {'rep'}:
            incumbent = -1
        # A weball cycle total can be negative when a candidate's refunds and adjustments exceed
        # receipts, and a party total can inherit that. Money raised is not negative, and the family
        # requires a nonnegative amount, so such a total is clamped to zero and counted.
        receipts = {side: max(0.0, seat[side]) for side in ('dem', 'rep')}
        clamped['negative_party_receipts'] += sum(seat[side] < 0 for side in ('dem', 'rep'))
        row = {'district': district, 'cycle': year, 'date': date_text, 'pvi': lean, 'incumbent': incumbent,
               'dem_receipts': receipts['dem'], 'rep_receipts': receipts['rep'], 'econ': economy[date_text]['econ'],
               'president_party': PRESIDENT_PARTY[year], 'midterm': 0 if year % 4 == 0 else 1,
               'total_votes': item['total'], 'two_party_votes': item['votes']}
        if item['contested']:
            row['dem_share'] = item['share']
        else:
            row['uncontested'] = 'D' if item['share'] > 0.5 else 'R'
            row['dem_share'] = item['share']
        rows.append(row)
    if len(rows) < 100:
        raise MissingData(f'Only {len(rows)} district-cycle rows could be built (skipped {skipped})')
    data = {'races': rows, 'information_time': 'valid_time', 'revisions': 'none',
            'design': 'predictive_association_house_district_two_party_share',
            'national_two_party_share': {str(year): value for year, value in sorted(national.items())},
            'economy': {'series': economy_series, 'metric': economy_metric, 'months': economy_months,
                        'as_published_on_election_day': {date: economy[date] for date in sorted(economy)}},
            'receipt_coverage_end_by_cycle': {str(cycle): end for cycle, end in sorted(receipt_coverage.items())},
            'construction': {'rows': len(rows), 'skipped': skipped, 'clamped': clamped,
                             'cycles': sorted({r['cycle'] for r in rows}),
                             'contested': sum('uncontested' not in r for r in rows),
                             'lean_lookback_cycles': lean_lookback}}
    return data, evidence.reference()


FAMILY_LOADERS = {'conflict': conflict_data, 'assets': assets_data, 'commodities': commodities_data, 'regional': regional_data,
                  'monetary': monetary_data, 'elections': elections_data}

BLOCKED_FAMILIES = {
    'trade': Blocked('The next-year bilateral-flow holdout needs several consecutive years of bilateral flows; un_comtrade '
                     'covers 2024-01 onward only and cepii_baci is not built.',
                     missing=(('annual bilateral flows', 'cepii_baci'),),
                     available=(('monthly flows 2024+', 'un_comtrade'), ('distance, contiguity', 'cepii_gravity'),
                                ('tariffs', 'wits_trains_tariffs'))),
    'influence': Blocked('No unit-period panel with an exposure measure and an outcome is published: LDA filings and FEC '
                         'flows are normalized, but the client/registrant-to-legislator attribution panel that the family '
                         'fit contract needs is not built, and no published crosswalk links LDA clients to FEC committees.',
                         missing=(('panel(unit, period, exposure, outcome)', 'lda_lobbying + fec + voteview_rollcalls'),)),
    'market_abm': Blocked('Declared but not run: the SMM grid fit at every holdout origin exceeds this run\'s compute budget '
                          'on real bars. Daily closes are available (alpaca_daily_bars), so this is a compute limit, not a data gap.',
                          available=(('daily closes', 'alpaca_daily_bars'),)),
    'sanctions': Blocked('worldmodel.models.sanctions declares NON_ESTIMABLE: a legal-rule determination with no held-out '
                         'observable, so it can never be validated.'),
    'legislative': Blocked('Roll-call member positions are published (voteview_rollcalls, Congresses 110-119), but the '
                           'ideal-point refit at every holdout origin exceeds this run\'s compute budget.',
                           available=(('roll_call_member_positions', 'voteview_rollcalls'),)),
}


def family_data(family, store, options=None):
    loader = FAMILY_LOADERS.get(family)
    if loader is None:
        blocked = BLOCKED_FAMILIES.get(family)
        raise MissingData(f'{family}: {blocked.reason}' if blocked else f'No catalog loader for model family {family}')
    return loader(store, **dict(options or {}))


LOADER_FUNCTIONS = {}      # filled at the end of the module: name -> loader callable


def load_for(target, store, options=None, function=None):
    """``(data, evidence, vintage_policy)`` for a component id or a model family id.

    ``function`` selects a named loader explicitly (the ``loader.function`` a
    pre-registered attempt declares), which is how two attempts on the same component
    can read different sources.
    """
    from .model_families import family_components
    families = family_components()
    if function:
        loader = LOADER_FUNCTIONS.get(function)
        if loader is None:
            raise MissingData(f'Unknown loader function {function!r}')
        if loader is observation_set:
            return observation_set(target, store, **dict(options or {}))
        result = loader(store, **dict(options or {}))
        return result if len(result) == 3 else (result[0], result[1], 'family_rows')
    if target in FAMILY_LOADERS or target in BLOCKED_FAMILIES:
        data, evidence = family_data(target, store, options)
        return data, evidence, 'family_rows'
    if target in families:
        data, evidence = family_data(families[target], store, options)
        return data, evidence, 'family_rows'
    if target == 'cash_balance':
        return cash_balance_data(store, **dict(options or {}))
    if target == 'default_hazard':
        return default_hazard_data(store, **dict(options or {}))
    if target in BLOCKED_COMPONENTS:
        raise MissingData(f'{target}: {BLOCKED_COMPONENTS[target].reason}')
    return observation_set(target, store, **dict(options or {}))


LOADER_FUNCTIONS.update({'observation_set': observation_set, 'cash_balance_data': cash_balance_data,
                         'default_hazard_data': default_hazard_data, 'conflict_data': conflict_data,
                         'assets_data': assets_data, 'commodities_data': commodities_data,
                         'regional_data': regional_data, 'monetary_data': monetary_data,
                         'monetary_realtime_data': monetary_realtime_data, 'elections_data': elections_data,
                         'population_popthm_data': population_popthm_data,
                         'deposit_rate_substitute_data': deposit_rate_substitute_data})


def availability():
    """Declared catalog availability for every component and family this module knows about."""
    out = {'schema': SCHEMA, 'components': {}, 'families': {}}
    for component, sources in COMPONENT_SOURCES.items():
        out['components'][component] = {'status': 'available',
                                        'series': [{'requirement': s.requirement, 'dataset': s.dataset, 'metric': s.metric,
                                                    'unit': s.unit, 'availability': s.availability} for s in sources]}
    out['components']['default_hazard']['alternative'] = {
        'loader': 'default_hazard_data', 'policy': 'retrospective',
        'reason': 'Kept for the superseded override-based attempt: the FDIC aggregate noncurrent-loan rate and a '
                  'constructed LAUS national unemployment rate, used while DRCCLACBS and UNRATE were unpublished.',
        'series': [{'requirement': 'delinquency_rate', 'dataset': 'fdic_bank_financials', 'metric': 'aggregate noncurrent-loan rate'},
                   {'requirement': 'unemployment_rate', 'dataset': 'bls_labor', 'metric': 'LAUS state aggregate'}]}
    out['components']['population_growth_rate']['alternative'] = {
        'loader': 'population_popthm_data', 'policy': 'strict',
        'reason': 'A longer real-time annual series than the PEP vintage files can supply. POPTHM carries 325 '
                  'ALFRED vintages from 1999-07-30 over monthly periods 1959-2026, so annual origins exist from '
                  '2000 onward; requirements.json names POPTHM as a source for this series.',
        'series': [{'requirement': 'population', 'dataset': 'fred_macro_panel', 'metric': 'resident_population',
                    'unit': 'people', 'availability': 'real_time'}]}
    out['components']['deposit_rate_pass_through']['alternative'] = {
        'loader': 'deposit_rate_substitute_data', 'policy': 'strict',
        'reason': 'SNDR begins 2021-04 and cannot supply 36 fitting observations plus 24 holdout forecasts with '
                  'room to spare. Each substitute is a different deposit rate, declared as such in its attempt.',
        'series': [{'requirement': 'deposit_rate', 'dataset': dataset, 'metric': metric, 'unit': 'percent',
                    'availability': 'real_time', 'series_id': series_id}
                   for series_id, (metric, dataset) in sorted(DEPOSIT_RATE_SUBSTITUTES.items())]}
    out['components']['cash_balance'] = {'status': 'available', 'series': [
        {'requirement': name, 'dataset': 'sec_company_assets', 'metric': concept, 'unit': 'USD', 'availability': 'real_time'}
        for name, concept in sorted(SEC_CONCEPTS.items())]}
    for component, blocked in BLOCKED_COMPONENTS.items():
        out['components'][component] = {'status': 'blocked_on_data', 'reason': blocked.reason,
                                        'missing': [{'series': s, 'dataset': d} for s, d in blocked.missing],
                                        'available': [{'series': s, 'dataset': d} for s, d in blocked.available]}
    for family in FAMILY_LOADERS:
        out['families'][family] = {'status': 'available'}
    for family, blocked in BLOCKED_FAMILIES.items():
        out['families'][family] = {'status': 'non_estimable' if family == 'sanctions' else 'blocked_on_data',
                                   'reason': blocked.reason,
                                   'missing': [{'series': s, 'dataset': d} for s, d in blocked.missing],
                                   'available': [{'series': s, 'dataset': d} for s, d in blocked.available]}
    return out
