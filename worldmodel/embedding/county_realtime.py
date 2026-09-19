"""The county first-release panel: which county vintage families enter it, and how.

`county_panel` carries the current vintage of every value, so every places attempt on it fails
`no_revision_leakage` by declaration. This module builds the alternative from
``fred_county_vintages``: each value is the earliest vintage the ALFRED archive holds for its
period, dated by that vintage, so nothing is ever revised. Static county geography is carried over
from ``county_panel`` with its own evidence, because an internal point is not a time series.

    python3 -m worldmodel embed-panel --realtime

What the archive does and does not have, measured (docs/county-vintages.md): population from
reference year 2004, BEA income from 2013, QCEW private establishments from 2016, LAUS annual
averages from 2019 — and **no county employment or wages from QCEW at all**, which is why the one
target the encoder won on in `places.county_root_readout_v3` has no real-time source.
"""
from .county_panel import COUNTY, DATASET as DATED_PANEL, is_county, load as load_dated
from .realtime_panel import first_releases, records, summary

DATASET = 'county_realtime_panel'
SOURCE = 'fred_county_vintages'
ENTRYPOINT = 'worldmodel.embedding.county_realtime:build'
#: family -> unit. A family absent here is not in the panel.
UNITS = {'population': 'persons', 'personal_income': 'USD', 'per_capita_personal_income': 'USD',
         'gdp': 'USD', 'real_gdp': 'USD', 'laus_employed': 'persons', 'laus_labor_force': 'persons',
         'laus_unemployed': 'persons', 'laus_unemployment_rate': 'percent',
         'private_establishments': 'establishments'}


def metric_of(record):
    """Panel feature for a vintaged record: annual families as they are, quarterly by their first quarter."""
    dimensions = record.get('dimensions') or {}
    family, frequency = dimensions.get('family'), dimensions.get('frequency')
    if family not in UNITS or not is_county(record.get('subject')):
        return None
    if frequency == 'A':
        return f'rt:{family}'
    if frequency == 'Q' and str(record.get('valid_from'))[5:7] == '01':
        return f'rt:{family}:q1'
    return None


def unit_of(feature):
    return UNITS[feature.split(':')[1]]


def build(store, *, publish=True, log=print):
    """Publish ``county_realtime_panel``: first releases, plus static geography from the dated panel."""
    from ..artifacts import publish_report
    from ..estimation.loaders import catalog_ref
    from ..util import now
    source = catalog_ref(store, SOURCE)
    values, counts = first_releases(store, source, subject_prefix=COUNTY, metric_of=metric_of, log=log)
    report = summary(values, counts, source, DATASET, SOURCE)
    panel_ref, panel_values, panel_available, panel_units, _ = load_dated(store, store.latest(DATED_PANEL))
    geography = {k: v for k, v in panel_values.items() if k[1].startswith('geography:')}
    report['inputs'].append(dict(panel_ref))
    report['static_geography'] = {'rows': len(geography), 'source': dict(panel_ref),
                                  'note': 'Internal point and land/water area, carried from county_panel; static, so '
                                          'first-release semantics do not apply to them.'}
    report['features'].update({f: {'rows': sum(1 for k in geography if k[1] == f)}
                               for f in sorted({k[1] for k in geography})})
    report['does_not_establish'].append(
        'A value dated by its earliest vintage in the archive is dated no earlier than it was actually public: where a '
        'series entered ALFRED late, the panel treats it as known later than it was, never sooner.')
    report['does_not_establish'].append(
        'FRED archives no QCEW county employment or wages, so the target that places.county_root_readout_v3 won on has '
        'no real-time counterpart here.')
    if not publish:
        return None, report
    observed_at = now()

    def all_records():
        yield from records(values, source, DATASET, observed_at, unit_of)
        for (county, feature, year), value in sorted(geography.items()):
            yield {'id': f'{DATASET}:{county}:{feature}:{year}', 'kind': 'observation', 'subject': county,
                   'metric': feature, 'unit': panel_units.get(feature) or 'unknown', 'value': value,
                   'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01', 'observed_at': observed_at,
                   'dimensions': {'available_at': panel_available[(county, feature, year)], 'revisions': 'static',
                                  'source': 'county_panel_geography'},
                   'evidence': [{'input': dict(panel_ref), 'record_id': f'{DATED_PANEL}:{county}:{feature}:{year}'}]}

    ref = publish_report(store, DATASET, report, {'source': SOURCE, 'basis': 'first_release'},
                         inputs=report['inputs'], records=all_records(), entrypoint=ENTRYPOINT)
    return ref, report
