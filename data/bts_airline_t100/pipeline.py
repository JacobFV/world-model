"""BTS T-100 Segment (All Carriers) traffic from manual TranStats CSV downloads.

Import one CSV (or the ZIP TranStats produces) per download with ``wm import``;
every raw input is read with the stdlib CSV reader (ZIP members ``*.csv``).
Each source row is a carrier x aircraft x service-class segment for one month
and airport pair. Observations use the origin airport (``iata:XXX``) as subject
and carry destination, carrier, aircraft type/configuration, service class and
data source as dimensions.
"""
from worldmodel.util import digest

METRICS = (('PASSENGERS', 'air_passengers', 'passengers'), ('FREIGHT', 'air_freight', 'lb'), ('MAIL', 'air_mail', 'lb'),
           ('SEATS', 'seats_offered', 'seats'), ('PAYLOAD', 'available_payload', 'lb'),
           ('DEPARTURES_PERFORMED', 'departures_performed', 'departures'),
           ('DEPARTURES_SCHEDULED', 'departures_scheduled', 'departures'), ('AIR_TIME', 'air_time', 'minutes'),
           ('RAMP_TO_RAMP', 'ramp_to_ramp_time', 'minutes'))
REQUIRED = ('YEAR', 'MONTH', 'ORIGIN', 'DEST')


def _clean(row):
    return {(key or '').strip().upper(): (value or '').strip() for key, value in row.items() if key}


def _number(text):
    if text in ('', None):
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def run(context):
    if not context.raw_inputs:
        raise ValueError('bts_airline_t100: import a TranStats T-100 Segment CSV with wm import first')
    for index, ref in enumerate(context.raw_inputs):
        receipt = context.raw_receipt(index)
        observed = receipt['retrieved_at']
        for locator, source in context.raw_rows(index, format='csv', members=['*.csv'], strict=False):
            row = _clean(source)
            missing = [field for field in REQUIRED if field not in row]
            if missing:
                raise ValueError(f'{locator}: T-100 export lacks required columns {missing}; include YEAR, MONTH, ORIGIN, DEST')
            if not row['ORIGIN'] or not row['DEST']:
                continue
            year, month = int(float(row['YEAR'])), int(float(row['MONTH']))
            valid_from = f'{year:04d}-{month:02d}-01'
            valid_to = f'{year + (month == 12):04d}-{month % 12 + 1:02d}-01'
            carrier = row.get('UNIQUE_CARRIER') or row.get('CARRIER') or 'unknown'
            dimensions = {'destination': 'iata:' + row['DEST'], 'carrier': 'dot:carrier:' + carrier}
            for field, name in (('AIRCRAFT_TYPE', 'aircraft_type'), ('AIRCRAFT_CONFIG', 'aircraft_config'), ('CLASS', 'service_class'),
                                ('DATA_SOURCE', 'data_source'), ('AIRCRAFT_GROUP', 'aircraft_group')):
                if row.get(field):
                    dimensions[name] = row[field]
            attributes = {'distance_miles': _number(row.get('DISTANCE')), 'carrier_name': row.get('UNIQUE_CARRIER_NAME') or row.get('CARRIER_NAME') or None,
                          'origin_airport_id': row.get('ORIGIN_AIRPORT_ID') or None, 'dest_airport_id': row.get('DEST_AIRPORT_ID') or None,
                          'origin_country': row.get('ORIGIN_COUNTRY') or None, 'dest_country': row.get('DEST_COUNTRY') or None,
                          'frequency': 'monthly', 'source_table': 'T-100 Segment (All Carriers)'}
            attributes = {k: v for k, v in attributes.items() if v is not None}
            base = 'bts:t100:' + digest([ref, locator])[:32]
            evidence = context.raw_evidence(locator, index)
            for field, metric, unit in METRICS:
                if field not in row or row[field] == '':
                    continue
                yield {'kind': 'observation', 'id': f'{base}:{metric}', 'observed_at': observed, 'evidence': evidence,
                       'subject': 'iata:' + row['ORIGIN'], 'metric': metric, 'value': _number(row[field]), 'unit': unit,
                       'valid_from': valid_from, 'valid_to': valid_to, 'dimensions': dimensions, 'attributes': attributes}
