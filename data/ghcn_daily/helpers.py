"""Stdlib reader for GHCN-Daily station metadata (ghcnd-stations.txt, fixed width).

Columns (1-based, from readme.txt): ID 1-11, LATITUDE 13-20, LONGITUDE 22-30, ELEVATION 32-37, STATE 39-40,
NAME 42-71, GSN FLAG 73-75, HCN/CRN FLAG 77-79, WMO ID 81-85.
"""


def station_records(stream):
    """Yield (line number, dict) for each station line of a text stream."""
    for number, line in enumerate(stream, 1):
        line = line.rstrip('\r\n')
        if len(line) < 38 or not line[:11].strip():
            continue
        elevation = float(line[31:37])
        yield number, {'id': line[0:11].strip(), 'latitude': float(line[12:20]), 'longitude': float(line[21:30]),
                       'elevation_m': None if elevation <= -999 else elevation, 'state': line[38:40].strip() or None,
                       'name': line[41:71].strip(), 'gsn': line[72:75].strip() == 'GSN',
                       'network': line[76:79].strip() or None, 'wmo_id': line[80:85].strip() or None}
