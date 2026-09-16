"""USGS ComCat FDSN CSV (monthly windows) -> earthquake/seismic events and magnitude/depth/location observations.

Each shard is one calendar-month query (starttime inclusive, endtime exclusive per FDSN). An event whose origin
time equals a window end could be returned by two adjacent windows, so rows at or after the shard's endtime are
skipped. Event IDs are ComCat preferred IDs (`usgs:eq:<id>`); the catalog is revised continuously, so
`status` (automatic/reviewed) and `updated` are kept.
"""
from urllib.parse import parse_qs, urlsplit

READER = {'format': 'csv'}


def _num(text):
    text = (text or '').strip()
    if not text:
        return None
    value = float(text)
    return int(value) if value.is_integer() else value


def _stamp(text):
    """'2024-01-31T23:52:27.000Z' -> same instant, normalized to seconds precision with milliseconds if present."""
    text = text.strip()
    return text if text.endswith('Z') else text + 'Z'


def _window_end(shard):
    request = shard.get('request') or {}
    params = request.get('params') or {}
    end = params.get('end')
    if not end and request.get('url'):
        end = (parse_qs(urlsplit(request['url']).query).get('endtime') or [None])[0]
    return end.replace('Z', '') if end else None


def run(context):
    if not context.raw_inputs:
        raise ValueError('usgs_earthquakes: raw acquisition required')
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        ends = {shard['index']: _window_end(shard) for shard in context.raw_shards(index)}
        for locator, row in context.raw_rows(index, **READER):
            shard = int(locator.split('/', 1)[0].split(':')[1])
            time = row['time'].strip()
            end = ends.get(shard)
            if end and time.replace('Z', '')[:19] >= end[:19]:
                continue
            event_id = row['id'].strip()
            subject = 'usgs:eq:' + event_id
            occurred = _stamp(time)
            evidence = context.raw_evidence(locator, index)
            event_type = (row.get('type') or 'earthquake').strip().replace(' ', '_')
            attrs = {'place': row.get('place', '').strip() or None, 'status': row.get('status', '').strip() or None,
                     'network': row.get('net', '').strip() or None, 'updated': row.get('updated', '').strip() or None,
                     'mag': _num(row.get('mag')), 'mag_type': row.get('magType', '').strip() or None,
                     'depth_km': _num(row.get('depth')), 'latitude': _num(row.get('latitude')),
                     'longitude': _num(row.get('longitude')), 'nst': _num(row.get('nst')), 'gap': _num(row.get('gap')),
                     'rms': _num(row.get('rms')), 'location_source': row.get('locationSource', '').strip() or None,
                     'mag_source': row.get('magSource', '').strip() or None}
            yield {'kind': 'event', 'id': 'usgseq:event:' + event_id, 'event_type': event_type, 'occurred_at': occurred,
                   'participants': [subject], 'observed_at': observed, 'evidence': evidence,
                   'attributes': {k: v for k, v in attrs.items() if v is not None}}
            dims = {'event_type': event_type, 'status': attrs['status']}
            for field, metric, unit, extra in (('mag', 'earthquake_magnitude', 'magnitude', {'magnitude_type': attrs['mag_type']}),
                                               ('depth', 'hypocenter_depth', 'km', {}),
                                               ('latitude', 'latitude', 'degrees', {'point': 'epicenter'}),
                                               ('longitude', 'longitude', 'degrees', {'point': 'epicenter'})):
                value = _num(row.get(field))
                if value is None:
                    continue
                error = {'mag': 'magError', 'depth': 'depthError', 'latitude': 'horizontalError', 'longitude': 'horizontalError'}[field]
                record = {'kind': 'observation', 'id': f'usgseq:{event_id}:{metric}', 'subject': subject, 'metric': metric,
                          'value': value, 'unit': unit, 'valid_from': occurred, 'observed_at': observed, 'evidence': evidence,
                          'dimensions': {**dims, **{k: v for k, v in extra.items() if v is not None}}, 'attributes': {}}
                uncertainty = _num(row.get(error))
                if uncertainty is not None:
                    record['attributes']['uncertainty'] = uncertainty
                    record['attributes']['uncertainty_unit'] = 'km' if field in ('latitude', 'longitude') else unit
                yield record
