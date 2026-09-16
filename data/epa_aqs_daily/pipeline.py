"""EPA AQS pre-generated daily summary files -> county-day pollutant concentrations.

Inputs (one ZIP per parameter and year): PM2.5 FRM/FEM `daily_88101` (ug/m3 LC), ozone `daily_44201` (ppm, daily
maximum 8-hour average), NO2 `daily_42602` (ppb), SO2 `daily_42401` (ppb), CO `daily_42101` (ppm) and PM10
`daily_81102` (ug/m3). Rows are per monitor (site + POC), day, sample duration and pollutant standard, so the same
measurement repeats once per standard: values are de-duplicated per monitor, day and sample duration, preferring
records not flagged `Excluded` for exceptional events. County-day observations are the mean over monitors, with
the monitor count, county maximum and (for daily means) the mean of monitors' daily maximum hourly values.
Aggregates are computed per yearly file (bounded memory: one year of county-days at a time).
"""
from datetime import date as _date, timedelta

# code -> (metric, unit, value field, pollutant, required sample-duration prefix or None)
PARAMETERS = {'88101': ('pm25_daily_mean', 'ug/m3', 'Arithmetic Mean', 'pm2.5_local_conditions', None),
              '44201': ('ozone_daily_max_8hr', 'ppm', '1st Max Value', 'ozone', '8-HR'),
              '42602': ('no2_daily_mean', 'ppb', 'Arithmetic Mean', 'no2', '1 HOUR'),
              '42401': ('so2_daily_mean', 'ppb', 'Arithmetic Mean', 'so2', '1 HOUR'),
              '42101': ('co_daily_mean', 'ppm', 'Arithmetic Mean', 'co', '1 HOUR'),
              '81102': ('pm10_daily_mean', 'ug/m3', 'Arithmetic Mean', 'pm10_standard_conditions', None)}


def _float(text):
    text = (text or '').strip()
    return float(text) if text else None


def run(context):
    from worldmodel.raw_readers import iter_rows
    if not context.raw_inputs:
        raise ValueError('epa_aqs_daily: raw acquisition required')
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        counties = set()  # < 1,500 monitored counties
        for shard in context.raw_shards(index):
            config = {'format': 'csv', 'encoding': 'latin-1', 'members': ['daily_*.csv', '*.csv'], 'strict': False}
            days = {}  # (parameter, county, day) -> monitor values; one yearly file at a time
            for locator, row in iter_rows([shard], config):
                parameter = (row.get('Parameter Code') or '').strip()
                if parameter not in PARAMETERS:
                    continue
                metric, unit, field, pollutant, duration = PARAMETERS[parameter]
                if duration and not (row.get('Sample Duration') or '').startswith(duration):
                    continue
                value = _float(row.get(field))
                if value is None:
                    continue
                daily_max = _float(row.get('1st Max Value')) if field == 'Arithmetic Mean' else None
                monitor = (row['State Code'], row['County Code'], row['Site Num'], row['POC'], row.get('Sample Duration'))
                excluded = (row.get('Event Type') or '').strip() == 'Excluded'
                geoid = row['State Code'].strip().zfill(2) + row['County Code'].strip().zfill(3)
                slot = days.setdefault((parameter, geoid, row['Date Local']),
                                       {'values': {}, 'maxima': {}, 'excluded': {}, 'locator': locator,
                                        'county': row.get('County Name'), 'state': row.get('State Name')})
                if monitor in slot['values'] and (excluded or not slot['excluded'][monitor]):
                    continue  # same measurement repeated per pollutant standard; prefer a non-excluded record
                slot['values'][monitor] = value
                slot['excluded'][monitor] = excluded
                if daily_max is not None:
                    slot['maxima'][monitor] = daily_max
            for (parameter, geoid, day), slot in sorted(days.items()):
                metric, unit, field, pollutant, duration = PARAMETERS[parameter]
                subject = 'geo:US:county:' + geoid
                evidence = context.raw_evidence(slot['locator'], index)
                if subject not in counties:
                    counties.add(subject)
                    yield {'kind': 'entity', 'id': 'aqs:entity:' + subject, 'entity_id': subject, 'entity_type': 'county',
                           'label': f'{slot["county"]} County, {slot["state"]}', 'observed_at': observed, 'evidence': evidence,
                           'attributes': {}}
                values = list(slot['values'].values())
                end = (_date.fromisoformat(day) + timedelta(days=1)).isoformat()
                attributes = {'monitors': len(values), 'county_max': round(max(values), 4)}
                if slot['maxima']:
                    maxima = list(slot['maxima'].values())
                    attributes['mean_daily_max'] = round(sum(maxima) / len(maxima), 4)
                yield {'kind': 'observation', 'id': f'aqs:{parameter}:{geoid}:{day.replace("-", "")}', 'subject': subject,
                       'metric': metric, 'value': round(sum(values) / len(values), 4), 'unit': unit,
                       'valid_from': day, 'valid_to': end, 'observed_at': observed, 'evidence': evidence,
                       'dimensions': {'pollutant': pollutant, 'parameter_code': parameter, 'aggregation': 'county_mean_of_monitors'},
                       'attributes': attributes}
