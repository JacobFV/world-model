"""Point-in-time observation selection with explicit vintage and leakage discipline.

Every estimator reads data through :class:`ObservationSet`. Selection is always
relative to a knowledge ``cutoff``: a record is usable only when both its valid
time and its availability time are at or before the cutoff. Availability is
resolved in this order:

1. Real-time vintage fields (ALFRED-style ``realtime_start``, or ``vintage_date``,
   ``published_at``, ``released_at``, ``available_at``) on the record or in its
   ``attributes``.  Mode ``real_time``.
2. Otherwise, under ``vintage_policy='strict'`` (the default) the acquisition time
   ``observed_at``. Current-vintage downloads therefore cannot feed historical
   cutoffs.  Mode ``acquisition_time``.
3. Under ``vintage_policy='retrospective'`` the period end plus the requirement's
   declared ``publication_lag_days``. Mode ``retrospective_lagged``: timing leakage
   is controlled, revision leakage is not, and reports say so.

When several vintages describe the same period, the latest one available by the
cutoff is used. Every selected point retains its vintage and evidence reference.
"""
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
import math
from ..model import instant
from ..util import canonical, digest

VINTAGE_POLICIES = ('strict', 'retrospective')
FREQUENCIES = ('daily', 'weekly', 'monthly', 'quarterly', 'annual')
_ORDER = {name: i for i, name in enumerate(FREQUENCIES)}
REALTIME_FIELDS = ('realtime_start', 'vintage_date', 'published_at', 'released_at', 'available_at')
REVISION_CLASSES = ('none', 'minor', 'major', 'unknown')
MAX_EVIDENCE_IDS = 2000


class LeakageError(ValueError):
    """Information later than the declared knowledge cutoff reached a fit or forecast."""


@dataclass(frozen=True)
class SeriesRequirement:
    """Machine-readable declaration of one observed series an estimator needs."""
    name: str
    metric: str
    unit: str
    frequency: str
    subject: str = None
    source_series: str = None
    geography: str = None
    dimensions: dict = None
    aggregation: str = 'mean'
    publication_lag_days: int = 0
    revisions: str = 'unknown'
    description: str = ''
    sources: tuple = ()
    native_frequency: str = None
    transform_note: str = None

    def __post_init__(self):
        for key in ('name', 'metric', 'unit'):
            if not isinstance(getattr(self, key), str) or not getattr(self, key):
                raise ValueError(f'Series requirement needs nonempty {key}')
        if self.frequency not in FREQUENCIES:
            raise ValueError(f'Unknown frequency: {self.frequency}')
        if self.aggregation not in ('mean', 'last', 'sum'):
            raise ValueError('aggregation must be mean, last or sum')
        if type(self.publication_lag_days) is not int or self.publication_lag_days < 0:
            raise ValueError('publication_lag_days must be a nonnegative integer')
        if self.native_frequency is not None and self.native_frequency not in FREQUENCIES + ('irregular',):
            raise ValueError(f'Unknown native frequency: {self.native_frequency}')
        if self.revisions not in REVISION_CLASSES:
            raise ValueError('revisions must be one of ' + ', '.join(REVISION_CLASSES))

    @classmethod
    def from_dict(cls, value):
        allowed = set(cls.__dataclass_fields__)
        if not isinstance(value, dict) or set(value) - allowed:
            raise ValueError('Unknown series requirement fields: ' + str(sorted(set(value) - allowed)))
        value = dict(value)
        value['sources'] = tuple(value.get('sources', ()))
        return cls(**value)

    def to_dict(self):
        out = asdict(self)
        out['sources'] = list(self.sources)
        return {k: v for k, v in out.items() if v is not None}

    def matches(self, record):
        if record.get('kind') != 'observation' or record.get('metric') != self.metric or record.get('unit') != self.unit:
            return False
        if record.get('epistemic_status', 'observed') != 'observed':
            return False
        attributes = record.get('attributes') or {}
        dimensions = record.get('dimensions') or {}
        if self.subject is not None and record.get('subject') != self.subject:
            return False
        if self.source_series is not None and attributes.get('source_series', dimensions.get('source_series')) != self.source_series:
            return False
        if self.geography is not None and dimensions.get('geography', attributes.get('geography')) != self.geography:
            return False
        for key, expected in (self.dimensions or {}).items():
            if dimensions.get(key) != expected:
                return False
        return True


def period_start(moment, frequency):
    d = moment.date()
    if frequency == 'daily':
        return d
    if frequency == 'weekly':
        return d - timedelta(days=d.weekday())
    if frequency == 'monthly':
        return date(d.year, d.month, 1)
    if frequency == 'quarterly':
        return date(d.year, 3 * ((d.month - 1) // 3) + 1, 1)
    return date(d.year, 1, 1)


def period_end(start, frequency):
    """Exclusive end of a period starting at ``start``."""
    if frequency == 'daily':
        return start + timedelta(days=1)
    if frequency == 'weekly':
        return start + timedelta(days=7)
    months = {'monthly': 1, 'quarterly': 3, 'annual': 12}[frequency]
    total = start.month - 1 + months
    return date(start.year + total // 12, total % 12 + 1, 1)


def _utc(day):
    return datetime(day.year, day.month, day.day, tzinfo=timezone.utc)


def availability(record, requirement, vintage_policy):
    if vintage_policy not in VINTAGE_POLICIES:
        raise ValueError('vintage_policy must be strict or retrospective')
    attributes = record.get('attributes') or {}
    for key in REALTIME_FIELDS:
        value = record.get(key, attributes.get(key))
        if value:
            return instant(value), {'mode': 'real_time', 'vintage': value, 'field': key}
    if vintage_policy == 'strict':
        return instant(record['observed_at']), {'mode': 'acquisition_time', 'vintage': record['observed_at']}
    end = instant(record.get('valid_to') or record['valid_from'])
    return (end + timedelta(days=requirement.publication_lag_days),
            {'mode': 'retrospective_lagged', 'vintage': attributes.get('vintage', 'retrospective_current_vintage'),
             'assumed_publication_lag_days': requirement.publication_lag_days})


@dataclass
class Point:
    time: str
    value: float
    available_at: str
    vintage: dict
    evidence: list = field(default_factory=list)
    first_available_at: str = None
    #: Value of this period's *earliest* vintage available by the cutoff. With
    #: ``value`` (the latest such vintage) it gives the revision the publisher has
    #: already made by the cutoff, which is information a real-time forecaster has.
    first_value: float = None


class Series:
    def __init__(self, requirement, points, *, cutoff, vintage_policy, excluded_missing=0, superseded_vintages=0):
        self.requirement, self.points = requirement, points
        self.cutoff, self.vintage_policy = cutoff, vintage_policy
        self.excluded_missing, self.superseded_vintages = excluded_missing, superseded_vintages

    @property
    def times(self):
        return [p.time for p in self.points]

    @property
    def values(self):
        return [p.value for p in self.points]

    def __len__(self):
        return len(self.points)

    def audit(self):
        cut = instant(self.cutoff)
        for point in self.points:
            if instant(point.available_at) > cut or instant(point.time) > cut:
                raise LeakageError(f'{self.requirement.name}: point {point.time} available {point.available_at} after cutoff {self.cutoff}')
        ids = [e.get('record_id') for p in self.points for e in p.evidence if e.get('record_id')]
        inputs = sorted({canonical(e['input']).decode() for p in self.points for e in p.evidence if 'input' in e})
        modes = sorted({p.vintage['mode'] for p in self.points})
        vintages = sorted({str(p.vintage.get('vintage')) for p in self.points})
        return {'series': self.requirement.name, 'metric': self.requirement.metric, 'unit': self.requirement.unit,
                'frequency': self.requirement.frequency, 'count': len(self.points),
                'first_time': self.points[0].time if self.points else None,
                'last_time': self.points[-1].time if self.points else None,
                'cutoff': self.cutoff, 'vintage_policy': self.vintage_policy, 'vintage_modes': modes,
                'vintages': vintages if len(vintages) <= 200 else {'count': len(vintages), 'digest': digest(vintages)},
                'max_available_at': max((p.available_at for p in self.points), key=instant, default=None),
                'revisions': self.requirement.revisions,
                'revision_leakage_possible': any(m != 'real_time' for m in modes) and self.requirement.revisions != 'none',
                'excluded_missing': self.excluded_missing, 'superseded_vintages': self.superseded_vintages,
                'evidence': {'inputs': [__import__('json').loads(i) for i in inputs], 'record_count': len(ids),
                             'record_ids_digest': digest(ids), **({'record_ids': ids} if len(ids) <= MAX_EVIDENCE_IDS else {})}}


class Frame:
    """Series aligned on shared period starts at a single model frequency."""
    def __init__(self, series, frequency, cutoff, vintage_policy):
        self.series, self.frequency, self.cutoff, self.vintage_policy = series, frequency, cutoff, vintage_policy
        common = None
        indexed = {}
        for name, s in series.items():
            indexed[name] = {p.time: p.value for p in s.points}
            common = set(indexed[name]) if common is None else common & set(indexed[name])
        self.times = sorted(common or [], key=instant)
        self.columns = {name: [indexed[name][t] for t in self.times] for name in series}

    def __len__(self):
        return len(self.times)

    def column(self, name):
        return self.columns[name]

    def restrict(self, indices):
        """A frame holding only the given row indices (and the matching series points)."""
        keep = {self.times[i] for i in indices}
        clone = Frame.__new__(Frame)
        clone.frequency, clone.cutoff, clone.vintage_policy = self.frequency, self.cutoff, self.vintage_policy
        clone.series = {name: Series(s.requirement, [p for p in s.points if p.time in keep], cutoff=s.cutoff,
                                     vintage_policy=s.vintage_policy, excluded_missing=s.excluded_missing,
                                     superseded_vintages=s.superseded_vintages) for name, s in self.series.items()}
        clone.times = [self.times[i] for i in indices]
        clone.columns = {name: [values[i] for i in indices] for name, values in self.columns.items()}
        return clone

    def audit(self):
        return {'frequency': self.frequency, 'cutoff': self.cutoff, 'vintage_policy': self.vintage_policy,
                'aligned_count': len(self.times), 'first_time': self.times[0] if self.times else None,
                'last_time': self.times[-1] if self.times else None,
                'series': {name: s.audit() for name, s in sorted(self.series.items())}}


def _finite(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number or null')
    return float(value)


class ObservationSet:
    """In-memory evidence observations tagged with the dataset references they came from."""
    def __init__(self, records, *, inputs=()):
        self.records = []
        self.inputs = [dict(ref) for ref in inputs]
        for record in records:
            if record.get('kind') == 'observation':
                self.records.append(record)

    @classmethod
    def from_store(cls, store, refs, *, metrics=None):
        records = []
        for ref in refs:
            store.verify(ref)
            for record in store.records(ref, verify=False):
                if record.get('kind') != 'observation' or (metrics is not None and record.get('metric') not in metrics):
                    continue
                record = dict(record)
                record['_input'] = dict(ref)
                records.append(record)
        return cls(records, inputs=refs)

    def select(self, requirement, *, cutoff, vintage_policy='strict'):
        cut = instant(cutoff)
        by_period, first_seen, missing, superseded = {}, {}, 0, 0
        for record in self.records:
            if not requirement.matches(record):
                continue
            if not record.get('valid_from'):
                raise ValueError('Estimation observations require valid_from')
            valid = instant(record['valid_from'])
            available, vintage = availability(record, requirement, vintage_policy)
            if valid > cut or available > cut:
                continue
            key = valid
            evidence = []
            if record.get('_input') is not None:
                evidence.append({'input': record['_input'], 'record_id': record.get('id')})
            elif record.get('id'):
                evidence.append({'record_id': record['id']})
            candidate = (available, record, vintage, evidence)
            current = by_period.get(key)
            if key not in first_seen or available < first_seen[key][0]:
                first_seen[key] = (available, record.get('value'))
            if current is None or available > current[0]:
                superseded += current is not None
                by_period[key] = candidate
            elif available == current[0]:
                if canonical(record.get('value')) != canonical(current[1].get('value')):
                    raise ValueError(f'{requirement.name}: conflicting values for {record["valid_from"]} with equal availability; reconcile explicitly')
            else:
                superseded += 1
        native = []
        for key in sorted(by_period):
            available, record, vintage, evidence = by_period[key]
            if record.get('value') is None:
                missing += 1
                continue
            value = _finite(record['value'], requirement.name)
            earliest, first_value = first_seen[key]
            native.append(Point(record['valid_from'], value, available.isoformat(), vintage, evidence,
                                earliest.isoformat(),
                                _finite(first_value, requirement.name) if first_value is not None else None))
        points = _resample(native, requirement, cut)
        return Series(requirement, points, cutoff=cutoff, vintage_policy=vintage_policy,
                      excluded_missing=missing, superseded_vintages=superseded)

    def subset(self, requirements):
        """Records matching any requirement; avoids rescanning unrelated evidence per origin."""
        kept = [r for r in self.records if any(req.matches(r) for req in requirements)]
        return ObservationSet(kept, inputs=self.inputs)

    def visible(self, cutoff, requirements, *, vintage_policy='strict'):
        """A physically restricted copy holding only matching records available by ``cutoff``.

        Model selection receives this view, so information after the selection cutoff
        cannot influence it even through a coding error.
        """
        cut = instant(cutoff)
        kept = []
        for record in self.records:
            for requirement in requirements:
                if requirement.matches(record):
                    available, _ = availability(record, requirement, vintage_policy)
                    if available <= cut and instant(record['valid_from']) <= cut:
                        kept.append(record)
                    break
        return ObservationSet(kept, inputs=self.inputs)

    def frame(self, requirements, *, cutoff, vintage_policy='strict', frequency=None):
        frequency = frequency or requirements[0].frequency
        names = [r.name for r in requirements]
        if len(set(names)) != len(names):
            raise ValueError('Requirement names must be unique')
        series = {}
        for requirement in requirements:
            if requirement.frequency != frequency:
                raise ValueError('All requirements in one frame must declare the model frequency')
            series[requirement.name] = self.select(requirement, cutoff=cutoff, vintage_policy=vintage_policy)
        return Frame(series, frequency, cutoff, vintage_policy)


def _resample(points, requirement, cutoff):
    """Aggregate native observations to complete requirement periods ending by the cutoff."""
    frequency = requirement.frequency
    groups = {}
    for point in points:
        start = period_start(instant(point.time), frequency)
        groups.setdefault(start, []).append(point)
    out = []
    for start in sorted(groups):
        members = groups[start]
        finer = requirement.native_frequency is not None and requirement.native_frequency != frequency
        if len(members) == 1 and instant(members[0].time) == _utc(start) and not finer:
            native_single = members[0]
        else:
            native_single = None
        if native_single is None and _utc(period_end(start, frequency)) > cutoff:
            continue  # Incomplete period: aggregating it would mix partial information.
        if native_single is not None:
            out.append(Point(start.isoformat(), native_single.value, native_single.available_at,
                             native_single.vintage, native_single.evidence, native_single.first_available_at,
                             native_single.first_value))
            continue
        values = [m.value for m in members]
        aggregate = {'mean': lambda v: math.fsum(v) / len(v), 'last': lambda v: v[-1], 'sum': math.fsum}[requirement.aggregation]
        value = aggregate(values)
        firsts = [m.first_value for m in members]
        first_value = aggregate(firsts) if all(f is not None for f in firsts) else None
        latest = max(members, key=lambda m: instant(m.available_at))
        modes = sorted({m.vintage['mode'] for m in members})
        vintage = {'mode': modes[0] if len(modes) == 1 else 'mixed:' + ','.join(modes),
                   'vintage': latest.vintage.get('vintage'), 'aggregated_from': len(members),
                   'aggregation': requirement.aggregation}
        if len(modes) > 1:
            vintage['mode'] = 'real_time' if modes == ['real_time'] else [m for m in modes if m != 'real_time'][0]
        first = max(instant(m.first_available_at) for m in members)
        complete = _utc(period_end(start, frequency))
        out.append(Point(start.isoformat(), value, latest.available_at, vintage,
                         [e for m in members for e in m.evidence], max(first, complete).isoformat(), first_value))
    return out
