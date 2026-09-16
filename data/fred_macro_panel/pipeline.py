"""fred_macro_panel: curated FRED/ALFRED series -> tidy evidence with real-time vintage metadata.

Series metadata (metric, unit, multiplier, frequency, geography, tier) comes from config.json
(tracked; regenerate with build_config.py). Subjects are geographies (`geo:US`,
`geo:US:state:06`); `dimensions.series_id` and `dimensions.vintage` (ALFRED realtime_start)
identify the exact series and vintage.
"""
import json
from pathlib import Path


def load_config():
    config = json.loads(Path(__file__).with_name('config.json').read_text())
    if not config.get('series'):
        raise ValueError('fred_macro_panel config.json has no generated series; run build_config.py')
    for series_id, unit in config.get('unit_overrides', {}).items():
        if series_id in config['series']:
            entry = config['series'][series_id]
            entry['unit'] = entry['estimation_unit'] = unit  # Fixed unit name; wins over per-vintage tokens.
            entry['unit_scale'] = 1
    # Estimation-layer contract (worldmodel/estimation/requirements.json): exact metric/unit, publisher-scale units.
    for series_id, override in config.get('estimation_overrides', {}).items():
        entry = config['series'].get(series_id)
        if entry is None:
            continue
        # fred_alfred.emitted_units applies unit_scale per vintage and substitutes that vintage's base year.
        entry['metric'], entry['estimation_unit'] = override['metric'], override['unit']
        entry['unit_scale'] = override['unit_scale']
        entry['unit'] = override['unit']
        entry['estimation_component'] = override.get('component')
    for fips_entry in config['series'].values():
        geography = fips_entry.get('geography', 'geo:US')
        if geography.startswith('geo:US:state:'):
            fips = geography.rsplit(':', 1)[1]
            abbr = next((a for a, f in config['states'].items() if f == fips), fips)
            fips_entry['geography_label'] = abbr
    return config


def run(context):
    from .fred_alfred import api_records, is_full
    if not is_full(context):
        raise ValueError('fred_macro_panel: No acquired full artifact (sample unavailable); run wm acquire fred_macro_panel')
    config = load_config()
    series = config['series']
    yield from api_records(context, 'fred_macro_panel', series, lambda series_id: series[series_id]['tier'])
