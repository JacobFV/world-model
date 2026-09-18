"""fred_state_industry_vintages: CES state x supersector employment -> evidence with ALFRED vintages.

Series metadata (metric, unit, per-vintage units history, frequency, geography) comes from
config.json (tracked; regenerate with build_config.py). Subjects are state geographies
(`geo:US:state:06`); `dimensions.series_id` and `dimensions.vintage` (ALFRED realtime_start)
identify the exact series and vintage, and the industry is carried by the metric name
(`employment_manufacturing`, ...) so one (subject, metric) pair is one industry.

Units are resolved **per vintage** by `fred_alfred.emitted_units`: ALFRED observations carry no
units, and FRED restates scales across vintages (TOTALSL went from billions to millions), so a
single present-day multiplier silently corrupts older vintages.
"""
import json
from pathlib import Path


def load_config():
    config = json.loads(Path(__file__).with_name('config.json').read_text())
    if not config.get('series'):
        raise ValueError('fred_state_industry_vintages config.json has no generated series; run build_config.py')
    for entry in config['series'].values():
        if not entry.get('units_history'):
            raise ValueError('Series without units_history: rerun build_config.py; per-vintage units are mandatory')
        fips = entry['geography'].rsplit(':', 1)[1]
        entry['geography_label'] = next((abbr for abbr, code in config['states'].items() if code == fips), fips)
    return config


def run(context):
    from .fred_alfred import api_records, is_full
    if not is_full(context):
        raise ValueError('fred_state_industry_vintages: No acquired full artifact (sample unavailable); '
                         'run wm acquire fred_state_industry_vintages')
    series = load_config()['series']
    yield from api_records(context, 'fred_state_industry_vintages', series, lambda sid: series[sid]['tier'])
