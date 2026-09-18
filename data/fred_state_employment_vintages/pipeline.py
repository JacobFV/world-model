"""fred_state_employment_vintages: CES State and Area employment by state x supersector, every ALFRED vintage.

Why this dataset exists. ``docs/calibration-status.md`` recorded that ``regional_model`` fails
``no_revision_leakage`` on both its panels and called the failure structural: "no published
employment source in this catalog carries vintages". That was true of the catalog and false of
the world. ALFRED archives the BLS CES State and Area (SAE) state-by-supersector employment
series with 229 real-time vintages from 2007-06-19 onward, so the panel ``regional_model`` needs
can be assembled out of first releases instead of revised levels.

What is published. One observation per (series, observation month, ALFRED real-time period):
``dimensions.vintage`` is the period's ``realtime_start`` and ``attributes.realtime_end`` closes
it, so the value current in any vintage is recoverable by interval lookup. Subjects are state
geographies (``geo:US:state:48``); ``dimensions.industry`` names the CES supersector. Values are
converted from the published thousands of persons to jobs (multiplier 1000).

What is **not** published. Mining/logging/construction has no aliased FRED series for five
state-equivalents (Delaware, DC, Hawaii, Maryland, Nebraska), and the structured
``SMS<fips>0000015000000001`` form that does cover them only enters ALFRED in 2014, which would
cut the real-time window from nineteen years to twelve. So this dataset carries the nine
universally archived supersectors plus total nonfarm, and the tenth industry is formed by the
consumer as the residual ``total_nonfarm - sum(nine)`` **inside one vintage**
(``worldmodel/estimation/loaders.py::ces_sae_regional_data``). Published ``construction`` and
``mining_logging`` series are carried where they exist with
``dimensions.panel_role = 'residual_check'`` so that residual can be checked against published
levels rather than trusted.

Annual averages are not published here either: they are an aggregation of twelve monthly values
drawn from *one* vintage, which is what keeps them real time, so they belong to the consumer that
knows which vintage it is standing in.
"""
import json
from pathlib import Path


def load_config():
    config = json.loads(Path(__file__).with_name('config.json').read_text())
    if not config.get('series'):
        raise ValueError('fred_state_employment_vintages config.json has no series; run build_config.py')
    for entry in config['series'].values():
        entry['geography_label'] = entry.get('state')
    return config


def run(context):
    from .alfred import api_records, is_full
    if not is_full(context):
        raise ValueError('fred_state_employment_vintages: no acquired full artifact; run '
                         'python3 -m worldmodel acquire fred_state_employment_vintages --allow-network')
    series = load_config()['series']
    yield from api_records(context, 'fred_state_employment_vintages', series,
                           lambda series_id: series[series_id]['tier'])
