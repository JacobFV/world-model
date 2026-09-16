"""MIT Election Lab returns -> candidate vote counts and total votes by state, county or House district.

Accepts any number of raw inputs (``wm run mit_election_returns --raw A --raw B``): the scripted
sharded acquisition (state-level president CSV, Senate TAB) and manual ``wm import`` payloads of the
guestbook-gated files (countypres_2000-2024.tab, 1976-2024-house.tab). The file kind is detected from
its header, and the delimiter from the first line.
"""
from datetime import date, timedelta
from worldmodel.raw_readers import iter_rows
from worldmodel.util import digest

MISSING = ('', 'NA', 'N/A', 'None', 'null', 'nan')


def first_line(path):
    with open(path, 'rb') as stream:
        head = stream.read(65536)
    if head[:2] == b'\x1f\x8b' or head[:4] == b'PK\x03\x04':
        raise ValueError('Compressed MIT election files are not supported; import the extracted .tab/.csv')
    return head.split(b'\n', 1)[0].decode('utf-8-sig', 'replace')


def election_day(year):
    """US general election: the Tuesday after the first Monday of November."""
    first = date(year, 11, 1)
    monday = first + timedelta(days=(0 - first.weekday()) % 7)
    return monday + timedelta(days=1)


def number(value):
    value = str(value if value is not None else '').strip().strip('"')
    if value in MISSING:
        return None
    result = float(value)
    return int(result) if result.is_integer() else result


def text(value):
    value = str(value if value is not None else '').strip().strip('"')
    return None if value in MISSING else value


def fips2(value):
    value = number(value)
    return None if value is None else f'{int(value):02d}'


def run(context):
    dataset = context.definition['id']
    for index, ref in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        totals = set()
        for shard in context.raw_shards(index):
            header = first_line(shard['path'])
            tabbed = '\t' in header
            # Dataverse TAB exports quote strings but do not escape inner quotes ("JOHN "JACK" DOE"), so TSV is
            # read with quoting disabled and quotes are stripped per field; the CSV export uses standard quoting.
            reader = {'format': 'tsv', 'encoding': 'utf-8-sig', 'strict': False, 'quoting': 'none'} if tabbed else \
                {'format': 'csv', 'encoding': 'utf-8-sig', 'strict': False}
            columns = set(header.replace('"', '').replace('\t', ',').split(','))
            kind = 'county' if 'county_fips' in columns else 'district' if 'district' in columns and 'runoff' in columns else 'state'
            for locator, row in iter_rows([shard], reader):
                row = {key.strip('"'): value for key, value in row.items()}
                year = int(number(row['year']))
                office = text(row.get('office')) or 'unknown'
                state = fips2(row.get('state_fips'))
                district = None
                if kind == 'county':
                    fips = number(row.get('county_fips'))
                    if fips is None:
                        geo = 'reference:mit_election_returns:county:' + digest([row.get('state_po'), row.get('county_name')])[:24]
                    else:
                        geo = f'geo:US:county:{int(fips):05d}'
                elif kind == 'district':
                    district = text(row.get('district'))
                    number_ = number(district)
                    geo = f'geo:US:state:{state}:cd:{int(number_):02d}' if number_ is not None else f'geo:US:state:{state}'
                else:
                    geo = f'geo:US:state:{state}' if state else 'geo:US'
                    district = text(row.get('district'))
                stage = (text(row.get('stage')) or 'gen').lower()
                special = str(row.get('special', 'False')).strip('"').upper() == 'TRUE'
                mode = (text(row.get('mode')) or 'total').lower()
                if stage == 'gen' and not special and year % 2 == 0:
                    day = election_day(year)
                    window = {'valid_from': day.isoformat(), 'valid_to': (day + timedelta(days=1)).isoformat()}
                    timing = 'regular general election day'
                else:
                    window = {'valid_from': f'{year}-01-01', 'valid_to': f'{year + 1}-01-01'}
                    timing = 'election year only (special, runoff, primary or odd-year contest date not in source)'
                dims = {'office': office, 'election_year': year, 'stage': stage, 'special': special, 'mode': mode,
                        **({'district': district} if district is not None else {})}
                evidence = [{'input': ref, 'locator': locator}]
                attributes = {'source_dataset': dataset, 'geography_level': kind, 'state_po': text(row.get('state_po')),
                              'version': text(row.get('version')), 'timing_basis': timing}
                if text(row.get('unofficial')):
                    attributes['unofficial'] = str(row['unofficial']).strip('"').upper() == 'TRUE'
                candidate = text(row.get('candidate'))
                party = text(row.get('party_detailed')) or text(row.get('party'))
                votes = number(row.get('candidatevotes'))
                record = {'kind': 'observation', 'id': f'{dataset}:votes:' + digest([ref, locator])[:40], 'subject': geo,
                          'metric': 'votes_received', 'value': votes, 'unit': 'votes', 'observed_at': observed,
                          'dimensions': {**dims, 'candidate': candidate, 'party': party,
                                         'party_simplified': text(row.get('party_simplified')),
                                         'writein': str(row.get('writein', 'False')).strip('"').upper() == 'TRUE'},
                          'evidence': evidence, 'attributes': attributes, **window}
                if votes is None:
                    record['missing_reason'] = 'source_missing'
                yield record
                key = (geo, office, year, stage, special, mode, district)
                total = number(row.get('totalvotes'))
                if key not in totals and total is not None:
                    totals.add(key)
                    yield {'kind': 'observation', 'id': f'{dataset}:total:' + digest([ref, key])[:40], 'subject': geo,
                           'metric': 'total_votes_cast', 'value': total, 'unit': 'votes', 'observed_at': observed,
                           'dimensions': dims, 'evidence': evidence, 'attributes': attributes, **window}
