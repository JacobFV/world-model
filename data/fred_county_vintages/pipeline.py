"""fred_county_vintages: county-level FRED series with every ALFRED real-time vintage.

Why this dataset exists. The places assay of the world-state encoder forecasts next-year county
employment, establishments and population, and is declared to fail ``no_revision_leakage`` because the
county panel holds only current-vintage values: QCEW, LAUS, BEA and the Census estimates all revise.
ALFRED archives county-level series with their real-time vintages, so a first-release county panel can
be built instead of a revised one.

What is published. One observation per (series, reference period, ALFRED real-time period), for ten
families discovered by ``build_config.py`` from FRED's release catalogues: Census resident population;
BEA per capita personal income, personal income, GDP and real GDP; QCEW private establishments; and the
LAUS annual averages of the unemployment rate, unemployed, employed and labor force.
``dimensions.vintage`` is the period's ``realtime_start`` and ``attributes.realtime_end`` closes it, so
the value current in any vintage is recoverable by interval lookup. ``dimensions.first_release`` marks
the earliest period of each reference period that starts after the series' first ALFRED vintage.
Subjects are ``geo:US:county:<SSCCC>`` with codes from GeoFRED's published geography, never from a
title.

What is **not** published. QCEW county employment and wages, which FRED does not carry at county level
at all; the monthly LAUS county series, which ALFRED does archive but which would cost about 2 GB
against this dataset's 1 GiB envelope (see README.md); and annual averages formed from monthly data,
which belong to the consumer that knows which vintage it is standing in.
"""
import json
from pathlib import Path


def load_config():
    config = json.loads(Path(__file__).with_name('config.json').read_text())
    if not config.get('series'):
        raise ValueError('fred_county_vintages config.json has no series; run build_config.py')
    return config


def run(context):
    from .alfred import api_records, is_full
    if not is_full(context):
        raise ValueError('fred_county_vintages: no acquired full artifact; run '
                         'python3 -m worldmodel acquire fred_county_vintages --allow-network')
    yield from api_records(context, 'fred_county_vintages', load_config())
