"""A first-release panel from an ALFRED-vintaged dataset: every value dated by its own publication.

The dated county panel in :mod:`worldmodel.embedding.county_panel` carries the *current* vintage of
each value with a declared publication lag, so an as-of reader still sees later revisions of the
numbers, and every attempt on it fails the repository's `no_revision_leakage` criterion by
declaration. A vintaged FRED/ALFRED dataset publishes each value with the real-time window it was
in force for, which allows the honest alternative:

* the value of a period is its **first release** -- the row with the earliest ``realtime_start``;
* ``available_at`` is that ``realtime_start``, a measured publication date, not a rule;
* the value never changes afterwards in this panel, so ``revisions`` is ``none`` and an as-of view
  of it carries no revision leakage;
* the **target** is a first release too, so a forecast is scored against what the statistical agency
  actually published, not against a number revised later.

What is lost is real: later vintages are more accurate, and a first-release panel is a panel of
what was known, not of what was true. Both are recorded on every record.

The output uses the same record shape as ``county_panel``, so :mod:`worldmodel.embedding.tensors`
reads either one.

A *period* is whatever the source's reference periods are: :data:`ANNUAL` keys a value by the
calendar year of its ``valid_from`` and :data:`MONTHLY` by its calendar month. The scheme is
explicit because it is not a formatting choice -- keying a monthly source by year would collapse
twelve reference months onto one and silently keep only the earliest.
"""
from collections import defaultdict
import gzip
import json

from ..estimation.loaders import catalog_ref, _records_path
from ..util import now

ENTRYPOINT = 'worldmodel.embedding.realtime_panel:build'


class PeriodScheme:
    """How a vintaged record's reference period is keyed, bounded and named.

    ``key(record)`` is the period a value belongs to, ``bounds(key)`` its half-open
    ``(valid_from, valid_to)`` and ``label(key)`` the string that names it inside a record id.
    ``field`` names the period in a summary (``first_year`` for :data:`ANNUAL`). Keys must sort in
    period order, because the panel's coverage is reported as their minimum and maximum.
    """

    def __init__(self, name, field, key, bounds, label=str):
        self.name, self.field, self.key, self.bounds, self.label = name, field, key, bounds, label


def _annual_bounds(year):
    return f'{year}-01-01', f'{year + 1}-01-01'


def _monthly_bounds(month):
    year, index = int(month[:4]), int(month[5:7])
    return f'{month}-01', (f'{year + 1}-01-01' if index == 12 else f'{year}-{index + 1:02d}-01')


#: A period is the calendar year of ``valid_from``: twelve monthly rows would collapse onto one.
ANNUAL = PeriodScheme('annual', 'year', lambda record: int(str(record['valid_from'])[:4]), _annual_bounds)
#: A period is the calendar month of ``valid_from``, keyed ``YYYY-MM`` so it still sorts in time order.
MONTHLY = PeriodScheme('monthly', 'month', lambda record: str(record['valid_from'])[:7], _monthly_bounds)


def first_releases(store, ref, *, subject_prefix, metric_of, log=print, period=ANNUAL, line_contains=()):
    """``(unit, feature, period) -> (value, available_at, record_id)`` from the earliest vintage of each value.

    ``metric_of(record)`` returns the panel feature name for a record, or None to skip it. ``period``
    is the :class:`PeriodScheme` that says which reference period a record belongs to; the default
    keys by calendar year, so a monthly source must pass :data:`MONTHLY` or twelve months of a year
    would collapse onto one and only the earliest survive. ``line_contains`` are canonical-JSON
    fragments every kept line must hold, a prefilter that saves parsing rows the caller will drop.
    """
    best, counts = {}, defaultdict(int)
    path = _records_path(store, ref)
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if '"kind":"observation"' not in line or subject_prefix not in line:
                continue
            if any(fragment not in line for fragment in line_contains):
                continue
            record = json.loads(line)
            subject = record.get('subject') or (record.get('dimensions') or {}).get('geography')
            if not str(subject or '').startswith(subject_prefix) or record.get('value') is None:
                continue
            feature = metric_of(record)
            if feature is None:
                continue
            start = (record.get('attributes') or {}).get('realtime_start')
            if not start:
                counts['no_realtime_start'] += 1
                continue
            key = (subject, feature, period.key(record))
            current = best.get(key)
            if current is None or start < current[1]:
                best[key] = (float(record['value']), start, record['id'])
                counts['kept'] += 1
            else:
                counts['later_vintage_dropped'] += 1
    if log:
        log(f'  first releases: {len(best):,} values, {counts["later_vintage_dropped"]:,} later vintages dropped, '
            f'{counts["no_realtime_start"]:,} rows without a vintage date')
    return best, dict(counts)


def records(values, ref, dataset, observed_at, unit_of, period=ANNUAL):
    """Panel observations in the ``county_panel`` record shape, one per first release."""
    for (subject, feature, key), (value, available_at, record_id) in sorted(values.items()):
        valid_from, valid_to = period.bounds(key)
        yield {'id': f'{dataset}:{subject}:{feature}:{period.label(key)}', 'kind': 'observation', 'subject': subject,
               'metric': feature, 'unit': unit_of(feature), 'value': value,
               'valid_from': valid_from, 'valid_to': valid_to, 'observed_at': observed_at,
               'dimensions': {'available_at': available_at, 'revisions': 'none', 'source': 'first_release'},
               'evidence': [{'input': dict(ref), 'record_id': record_id}]}


def summary(values, counts, ref, dataset, source_dataset, period=ANNUAL):
    features, periods = defaultdict(int), defaultdict(set)
    for (subject, feature, key) in values:
        features[feature] += 1
        periods[feature].add(key)
    first, last = f'first_{period.field}', f'last_{period.field}'
    return {'schema': 'worldmodel.realtime_panel/1', 'dataset': dataset, 'source': source_dataset,
            'period': period.name,
            'units': len({s for (s, _, _) in values}), 'values': len(values),
            'features': {f: {'rows': n, first: min(periods[f]), last: max(periods[f]),
                             f'{period.field}s': len(periods[f])}
                         for f, n in sorted(features.items())},
            'extraction': counts, 'inputs': [dict(ref)],
            'does_not_establish': [
                'Every value is a first release: the number the agency published then, not its current estimate. '
                'A forecast scored on this panel is scored against what was published, which is the point, and it '
                'is not scored against the best later estimate of the same quantity.',
                'available_at is the vintage date the publisher assigned, so it is measured rather than declared; '
                'nothing here establishes that the vintage window itself is complete.',
                'A period with no vintage in the archive is absent, not zero.']}


def build(store, *, dataset, source_dataset, subject_prefix, metric_of, unit_of, publish=True, log=print,
          period=ANNUAL, line_contains=()):
    """Collect first releases from ``source_dataset`` and publish them as ``dataset``."""
    from ..artifacts import publish_report
    ref = catalog_ref(store, source_dataset)
    values, counts = first_releases(store, ref, subject_prefix=subject_prefix, metric_of=metric_of, log=log,
                                    period=period, line_contains=line_contains)
    report = summary(values, counts, ref, dataset, source_dataset, period=period)
    if not publish:
        return None, report
    observed_at = now()
    output = publish_report(store, dataset, report, {'source': source_dataset, 'basis': 'first_release'},
                            inputs=[ref], records=records(values, ref, dataset, observed_at, unit_of, period=period),
                            entrypoint=ENTRYPOINT)
    return output, report
