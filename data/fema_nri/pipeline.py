"""FEMA National Risk Index (county and census tract) -> geography entities and risk/loss observations.

Accepted inputs:
* acquired ArcGIS feature-service pages (JSONL lines `{"attributes": {...}}`), counties with all per-hazard fields and
  tracts with the composite plus per-hazard EAL/frequency/risk-score subset;
* a manually imported official table ZIP/CSV (NRI_Table_Counties.zip, NRI_Table_CensusTracts.zip) via `wm import`.
Values are model outputs of NRI v1.20 (December 2025). EAL = expected annual loss (USD/year) by consequence
(B buildings, P population equivalence, A agriculture, T total). Scores/percentiles are relative (0-100).
Empty/null values are omitted; hazards not applicable to an area are published as null.
"""
import zipfile

HAZARDS = {'AVLN': 'avalanche', 'CFLD': 'coastal_flooding', 'CWAV': 'cold_wave', 'DRGT': 'drought', 'ERQK': 'earthquake',
           'HAIL': 'hail', 'HWAV': 'heat_wave', 'HRCN': 'hurricane', 'ISTM': 'ice_storm', 'LNDS': 'landslide',
           'LTNG': 'lightning', 'IFLD': 'inland_flooding', 'RFLD': 'inland_flooding', 'SWND': 'strong_wind',
           'TRND': 'tornado', 'TSUN': 'tsunami', 'VLCN': 'volcanic_activity', 'WFIR': 'wildfire', 'WNTW': 'winter_weather'}
COMPOSITE = {
    'POPULATION': ('population_exposure', 'people', {}), 'BUILDVALUE': ('building_value_exposure', 'USD', {}),
    'AGRIVALUE': ('agriculture_value_exposure', 'USD', {}), 'AREA': ('area', 'mi2', {}),
    'RISK_VALUE': ('expected_annual_loss_risk_value', 'USD/year', {}), 'RISK_SCORE': ('national_risk_index_score', 'score_0_100', {}),
    'EAL_VALT': ('expected_annual_loss', 'USD/year', {'consequence': 'total'}),
    'EAL_VALB': ('expected_annual_loss', 'USD/year', {'consequence': 'buildings'}),
    'EAL_VALP': ('expected_annual_loss', 'people/year', {'consequence': 'population'}),
    'EAL_VALPE': ('expected_annual_loss', 'USD/year', {'consequence': 'population_equivalence'}),
    'EAL_VALA': ('expected_annual_loss', 'USD/year', {'consequence': 'agriculture'}),
    'EAL_SCORE': ('expected_annual_loss_score', 'score_0_100', {}), 'SOVI_SCORE': ('social_vulnerability_score', 'score_0_100', {}),
    'RESL_SCORE': ('community_resilience_score', 'score_0_100', {}), 'RESL_VALUE': ('community_resilience_value', 'index', {}),
    'CRF_VALUE': ('community_risk_factor', 'index', {}),
}
PER_HAZARD = {'EVNTS': ('hazard_event_count', 'events', {}), 'AFREQ': ('hazard_annualized_frequency', 'events/year', {}),
              'EXPT': ('hazard_exposure_total', 'USD', {}), 'HLRB': ('historic_loss_ratio', 'ratio', {'consequence': 'buildings'}),
              'HLRP': ('historic_loss_ratio', 'ratio', {'consequence': 'population'}),
              'HLRA': ('historic_loss_ratio', 'ratio', {'consequence': 'agriculture'}),
              'EALB': ('expected_annual_loss', 'USD/year', {'consequence': 'buildings'}),
              'EALP': ('expected_annual_loss', 'people/year', {'consequence': 'population'}),
              'EALA': ('expected_annual_loss', 'USD/year', {'consequence': 'agriculture'}),
              'EALT': ('expected_annual_loss', 'USD/year', {'consequence': 'total'}),
              'EALS': ('expected_annual_loss_score', 'score_0_100', {}), 'RISKV': ('expected_annual_loss_risk_value', 'USD/year', {}),
              'RISKS': ('hazard_risk_index_score', 'score_0_100', {})}
VALID = ('2025-12-01', '2026-12-01')  # NRI v1.20 release year; inputs span multiple historical periods


def _num(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if not text:
        return None
    number = float(text)
    return int(number) if number.is_integer() else number


def _rows(context, index):
    shards = list(context.raw_shards(index))
    if shards and zipfile.is_zipfile(shards[0]['path']):
        yield from context.raw_rows(index, format='csv', members=['NRI_Table_*.csv', 'NRI_*Counties*.csv', 'NRI_*Tracts*.csv'],
                                    strict=False)
        return
    with open(shards[0]['path'], 'rb') as stream:
        first = stream.read(1)
    if first in (b'{', b'['):
        for locator, row in context.raw_rows(index, format='jsonl'):
            yield locator, row.get('attributes', row)
    else:
        yield from context.raw_rows(index, format='csv', strict=False)


def run(context):
    if not context.raw_inputs:
        raise ValueError('fema_nri: raw acquisition or imported NRI table required')
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        for locator, row in _rows(context, index):
            evidence = context.raw_evidence(locator, index)
            tract = str(row.get('TRACTFIPS') or '').strip()
            county = str(row.get('STCOFIPS') or '').strip().zfill(5)
            if not tract and county == '00000':
                continue  # not an NRI area row (e.g. dictionary or blank line)
            if tract:
                geoid, layer, typ = tract.zfill(11), 'tract', 'location'
                label = f'Census tract {tract.zfill(11)} ({row.get("COUNTY", "")}, {row.get("STATEABBRV", "")})'
                parent = 'geo:US:county:' + tract.zfill(11)[:5]
            else:
                geoid, layer, typ = county, 'county', 'county'
                label = f'{row.get("COUNTY", "")} {row.get("COUNTYTYPE", "") or ""}, {row.get("STATEABBRV", "")}'.replace('  ', ' ')
                parent = 'geo:US:state:' + county[:2]
            subject = f'geo:US:{layer}:{geoid}'
            version = str(row.get('NRI_VER') or '').strip() or None
            yield {'kind': 'entity', 'id': f'nri:entity:{layer}:{geoid}', 'entity_id': subject, 'entity_type': typ,
                   'label': label.strip(', '), 'observed_at': observed, 'evidence': evidence,
                   'attributes': {'nri_id': row.get('NRI_ID'), 'nri_version': version,
                                  'risk_rating': row.get('RISK_RATNG'), 'sovi_rating': row.get('SOVI_RATNG')}}
            yield {'kind': 'assertion', 'id': f'nri:within:{layer}:{geoid}', 'subject': subject, 'predicate': 'within',
                   'object': parent, 'observed_at': observed, 'evidence': evidence, 'attributes': {}}
            base = {'nri_version': version or 'v1.20'}
            for field, value in row.items():
                if field in COMPOSITE:
                    metric, unit, dims = COMPOSITE[field]
                    hazard = None
                else:
                    code, _, part = field.partition('_')
                    if code not in HAZARDS or part not in PER_HAZARD:
                        continue
                    metric, unit, dims = PER_HAZARD[part]
                    hazard = HAZARDS[code]
                number = _num(value)
                if number is None:
                    continue
                dimensions = {**base, **dims, **({'hazard': hazard} if hazard else {'hazard': 'all'})}
                yield {'kind': 'observation', 'id': f'nri:{layer}:{geoid}:{field.lower()}', 'subject': subject, 'metric': metric,
                       'value': number, 'unit': unit, 'valid_from': VALID[0], 'valid_to': VALID[1], 'observed_at': observed,
                       'evidence': evidence, 'dimensions': dimensions, 'attributes': {'source_field': field}}
