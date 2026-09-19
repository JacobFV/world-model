"""fred_county_laus_monthly_vintages: monthly LAUS county series with every ALFRED real-time vintage.

Why this dataset exists. Two tracks were blocked on the same gap.
``worldmodel.embedding.county_realtime`` builds the first-release county panel from
``fred_county_vintages``, whose LAUS annual averages only begin at reference year 2019, because that
is when the structured ``LAUCN…`` annual series entered ALFRED -- so the panel's labour features
start in 2019 and nothing earlier can be read as a release. And the causal layer's wave-2 report
named monthly county outcomes as the next registration worth running, which needs a monthly county
series that is dated by publication rather than by revision. ``docs/county-vintages.md`` established
that ALFRED holds exactly that: FRED's **monthly LAUS aliases**, the unemployment rate (``…URN``) and
the civilian labor force (``…LFN``), with 229-264 vintages from 2005-06-08 and 2007-06. They were
left out of ``fred_county_vintages`` only because they measure about 1.4 GiB.

**Why a sibling dataset rather than new families in ``fred_county_vintages``.** Three reasons, and
they are all about not disturbing what already works:

1. ``fred_county_vintages`` is acquired as one raw artifact of 31,452 shards whose declared
   ``parameters.series_id`` is the whole list. Adding families changes that list, so the annual
   dataset would have to be re-acquired and its 5.3 M published records rebuilt, for data its only
   consumer (``county_realtime.py``, which keys on ``dimensions.family``) does not read.
2. The byte envelopes differ by an order of magnitude (800 MiB against 1.8 GiB) and the fair-share
   ledger books them per dataset; kept apart, each dataset's declared bytes stay honest and this one
   can be re-acquired or dropped on its own.
3. The two are joined by nothing but the county code and both emit the same record contract, so a
   consumer that wants both reads both stages; nothing is gained by one artifact.

What is published. One observation per (series, reference **month**, ALFRED real-time period), for
two families: the monthly unemployment rate and the monthly civilian labor force.
``dimensions.vintage`` is the period's ``realtime_start`` and ``attributes.realtime_end`` closes it,
so the value current in any vintage is recoverable by interval lookup. ``dimensions.first_release``
marks the earliest period of each reference month that starts after the series' first ALFRED vintage.
Subjects are ``geo:US:county:<SSCCC>``, and the units are the ones published **in that vintage**:
FRED re-expressed county labor force from thousands to persons on 2016-03-18.

What is **not** published. Monthly *employed* and *unemployed* persons, which exist at county level
only in the structured ``LAUCN…`` form whose archive opens on 2019-08-28 (the annual dataset's LAUS
families have the same limit); the annual averages, which are ``fred_county_vintages``; and any
annual average formed here from monthly data, which belongs to the consumer that knows which vintage
it is standing in.
"""
import json
from pathlib import Path

DATASET = 'fred_county_laus_monthly_vintages'


def load_config():
    config = json.loads(Path(__file__).with_name('config.json').read_text())
    if not config.get('series'):
        raise ValueError(f'{DATASET} config.json has no series; run build_config.py')
    return config


def run(context):
    from .alfred import api_records, is_full
    if not is_full(context):
        raise ValueError(f'{DATASET}: no acquired full artifact; run '
                         f'python3 -m worldmodel acquire {DATASET} --allow-network')
    yield from api_records(context, DATASET, load_config())
