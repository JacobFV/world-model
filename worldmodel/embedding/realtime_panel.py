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
"""
from collections import defaultdict
import gzip
import json

from ..estimation.loaders import catalog_ref, _records_path
from ..util import now

ENTRYPOINT = 'worldmodel.embedding.realtime_panel:build'


def first_releases(store, ref, *, subject_prefix, metric_of, log=print):
    """``(unit, feature, year) -> (value, available_at, record_id)`` from the earliest vintage of each value.

    ``metric_of(record)`` returns the panel feature name for a record, or None to skip it. Only
    annual periods are kept: a period is indexed by the calendar year of its ``valid_from``.
    """
    best, counts = {}, defaultdict(int)
    path = _records_path(store, ref)
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if '"kind":"observation"' not in line or subject_prefix not in line:
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
            year = int(str(record['valid_from'])[:4])
            key = (subject, feature, year)
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


def records(values, ref, dataset, observed_at, unit_of):
    """Panel observations in the ``county_panel`` record shape, one per first release."""
    for (subject, feature, year), (value, available_at, record_id) in sorted(values.items()):
        yield {'id': f'{dataset}:{subject}:{feature}:{year}', 'kind': 'observation', 'subject': subject,
               'metric': feature, 'unit': unit_of(feature), 'value': value,
               'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01', 'observed_at': observed_at,
               'dimensions': {'available_at': available_at, 'revisions': 'none', 'source': 'first_release'},
               'evidence': [{'input': dict(ref), 'record_id': record_id}]}


def summary(values, counts, ref, dataset, source_dataset):
    features, units, years = defaultdict(int), {}, defaultdict(set)
    for (subject, feature, year) in values:
        features[feature] += 1
        years[feature].add(year)
    return {'schema': 'worldmodel.realtime_panel/1', 'dataset': dataset, 'source': source_dataset,
            'units': len({s for (s, _, _) in values}), 'values': len(values),
            'features': {f: {'rows': n, 'first_year': min(years[f]), 'last_year': max(years[f])}
                         for f, n in sorted(features.items())},
            'extraction': counts, 'inputs': [dict(ref)],
            'does_not_establish': [
                'Every value is a first release: the number the agency published then, not its current estimate. '
                'A forecast scored on this panel is scored against what was published, which is the point, and it '
                'is not scored against the best later estimate of the same quantity.',
                'available_at is the vintage date the publisher assigned, so it is measured rather than declared; '
                'nothing here establishes that the vintage window itself is complete.',
                'A period with no vintage in the archive is absent, not zero.']}


def build(store, *, dataset, source_dataset, subject_prefix, metric_of, unit_of, publish=True, log=print):
    """Collect first releases from ``source_dataset`` and publish them as ``dataset``."""
    from ..artifacts import publish_report
    ref = catalog_ref(store, source_dataset)
    values, counts = first_releases(store, ref, subject_prefix=subject_prefix, metric_of=metric_of, log=log)
    report = summary(values, counts, ref, dataset, source_dataset)
    if not publish:
        return None, report
    observed_at = now()
    output = publish_report(store, dataset, report, {'source': source_dataset, 'basis': 'first_release'},
                            inputs=[ref], records=records(values, ref, dataset, observed_at, unit_of),
                            entrypoint=ENTRYPOINT)
    return output, report
