"""The monthly county first-release panel: LAUS unemployment rate and labour force, dated by publication.

``county_realtime`` builds the *annual* first-release panel from ``fred_county_vintages``; its LAUS
families start at reference year 2019, because that is when the structured ``LAUCN…`` annual series
entered ALFRED. This module builds the monthly sibling from ``fred_county_laus_monthly_vintages``,
whose two FRED alias families (``…URN``, ``…LFN``) carry the deep half of the county LAUS archive
back to a first vintage of 2005-06-08.

    python3 -m worldmodel embed-panel --realtime-monthly

Each value is the earliest vintage the archive holds for its reference **month**, ``available_at``
is that measured ``realtime_start``, and the value never changes afterwards, so an as-of reader of
this panel sees no revision of any number.

Three properties of the source that this panel has to respect rather than rediscover, all measured
in ``data/fred_county_laus_monthly_vintages/README.md``:

* **The archive's opening snapshot is not a release.** Every series' first ALFRED vintage carries
  already-revised history reaching back to 1990-01. Taking the earliest ``realtime_start`` of a
  reference month would date those months by the day the archive opened. The source marks the real
  thing per row (``dimensions.first_release``), so this panel keeps only those rows.
* **Units changed with the label.** FRED published county labour force as *Thousands of Persons*
  through the 2016-03-17 vintage and as *Persons* from 2016-03-18, and the observations changed with
  it. The source applies the multiplier of the row's *own* vintage, so every value it publishes is
  already in persons; this panel carries that single unit and says so.
* **The early years are one Federal Reserve district, not a national sample.** See
  :data:`DOES_NOT_ESTABLISH`.

One reference month is deliberately out of scope: FRED extended ``DCDIST5URN`` back to 1976 in a
2016 vintage, so 1976-01..1989-12 are first releases of forty-year-old months for one series.
:data:`FIRST_MONTH` starts the panel at the archive's own first real-time reference month instead.
"""
from .county_panel import COUNTY, is_county
from .realtime_panel import MONTHLY, first_releases, records, summary

DATASET = 'county_monthly_realtime_panel'
SOURCE = 'fred_county_laus_monthly_vintages'
ENTRYPOINT = 'worldmodel.embedding.county_monthly_realtime:build'
#: family -> unit. A family absent here is not in the panel.
UNITS = {'laus_monthly_unemployment_rate': 'percent', 'laus_monthly_labor_force': 'persons'}
#: Earliest reference month kept: the first month the archive released in real time (2005-07-06).
FIRST_MONTH = '2005-04'
#: Canonical-JSON fragments a source line must hold; a prefilter, the record check below is the rule.
LINE_CONTAINS = ('"first_release":true',)

DOES_NOT_ESTABLISH = [
    'The early cross-section is one Federal Reserve district, not a national sample. The 339 '
    'county-equivalents with first releases from 2005-07-06 are every county of AR, IL, IN, KY, MS, MO '
    'and TN - the whole Eighth District - because FRED is the St. Louis Fed and archived its own '
    'district first. Seven more counties join from 2006-09-07. National coverage starts with reference '
    'month 2007-05, published 2007-07-05 (3,137 of 3,140 that day), and every county-equivalent in the '
    'source has a first release from reference month 2008-03 on. A panel-wide statistic computed across '
    'reference months before 2007-05 is a statistic about the Eighth District.',
    'Values are carried in the units the source harmonised them to: labour force in persons and the '
    'unemployment rate in percent, in every vintage. FRED itself published county labour force as '
    '"Thousands of Persons" through the 2016-03-17 vintage and as "Persons" from 2016-03-18, changing '
    'the observations with the label; the source applies the multiplier of each row\'s own vintage, so '
    'a value here is comparable across the 2016 break and is not the literal number FRED printed then.',
    'A month is dated by the earliest vintage that released it, which excludes the archive\'s opening '
    'snapshot: every series\' first ALFRED vintage holds already-revised history back to 1990-01, and '
    'those months are not in this panel at all. Absence of a month before a series\' archive opened is '
    'the archive\'s left edge, not a missing publication.',
    'FRED publishes two aliases for Hancock County, KY, whose archives differ. Both map to '
    'geo:US:county:21091, and where both released the same month this panel keeps the earlier release '
    'and drops the other; it does not merge or average them, and it does not establish which alias BLS '
    'would call the county\'s series.',
    'Reference months before ' + FIRST_MONTH + ' are out of scope. They exist in the source for one '
    'series (FRED extended DCDIST5URN back to 1976 in a 2016 vintage), and a first release published '
    'forty years after its reference month is not a contemporaneous one.']


def metric_of(record):
    """Panel feature for a vintaged record: monthly LAUS families, first releases only, in scope."""
    dimensions = record.get('dimensions') or {}
    family = dimensions.get('family')
    if family not in UNITS or not is_county(record.get('subject')):
        return None
    if dimensions.get('frequency') != 'M' or dimensions.get('first_release') is not True:
        return None
    if str(record.get('valid_from'))[:7] < FIRST_MONTH:
        return None
    return f'rt:{family}'


def unit_of(feature):
    return UNITS[feature.split(':', 1)[1]]


def build(store, *, publish=True, log=print):
    """Publish ``county_monthly_realtime_panel``: one first release per county, family and month."""
    from ..artifacts import publish_report
    from ..estimation.loaders import catalog_ref
    from ..util import now
    source = catalog_ref(store, SOURCE)
    values, counts = first_releases(store, source, subject_prefix=COUNTY, metric_of=metric_of, log=log,
                                    period=MONTHLY, line_contains=LINE_CONTAINS)
    report = summary(values, counts, source, DATASET, SOURCE, period=MONTHLY)
    months = sorted({key for (_, _, key) in values})
    report['months_with_a_first_release'] = len(months)
    report['months'] = [months[0], months[-1]] if months else []
    report['counties'] = report['units']
    report['does_not_establish'] = report['does_not_establish'] + DOES_NOT_ESTABLISH
    if not publish:
        return None, report
    observed_at = now()
    ref = publish_report(store, DATASET, report, {'source': SOURCE, 'basis': 'first_release', 'period': 'monthly'},
                         inputs=report['inputs'],
                         records=records(values, source, DATASET, observed_at, unit_of, period=MONTHLY),
                         entrypoint=ENTRYPOINT)
    return ref, report
