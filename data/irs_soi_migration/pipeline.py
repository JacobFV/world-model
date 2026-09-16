"""IRS SOI migration data -> migration flow entities, endpoint assertions and count/AGI observations.

File layouts (one CSV per filing-year pair and direction):
  countyinflow  : y2_statefips,y2_countyfips (destination), y1_statefips,y1_countyfips (origin), y1_state, y1_countyname, n1, n2, agi
  countyoutflow : y1_statefips,y1_countyfips (origin), y2_statefips,y2_countyfips (destination), y2_state, y2_countyname, n1, n2, agi
  stateinflow   : y2_statefips, y1_statefips, y1_state, y1_state_name, n1, n2, AGI
  stateoutflow  : y1_statefips, y2_statefips, y2_state, y2_state_name, n1, n2, AGI
y1 = first filing year (origin address), y2 = second filing year (destination). n1 = returns (households),
n2 = exemptions/individuals, agi = adjusted gross income in thousands of USD (of y2 returns).
Pseudo-FIPS codes: state 96 total US+foreign, 97 total US (county 000) / same state (county 001) / different state (county 003),
98 foreign (county 000), 57/59 "other flows" aggregates (same state / different state / region / foreign), county 000 on the
non-aggregate side means the state total. Values of -1 mark suppressed cells (< 20 returns).
Inflow and outflow files describe the same county-to-county movements from each side; both are kept with a
`perspective` dimension because the published aggregates and suppression differ.
"""
import re

DATASET = 'irs_soi_migration'
READER = {'format': 'csv', 'encoding': 'latin-1'}
FILE = re.compile(r'(county|state)(inflow|outflow)(\d{2})(\d{2})\.csv$')
SPECIAL_STATES = {'96', '97', '98', '57', '58', '59'}


def _fips(value, width):
    value = (value or '').strip()
    return value.zfill(width) if value.isdigit() else value


def _endpoint(state, county, name, level):
    """Return (entity id, entity type, label, aggregate?)."""
    if state in SPECIAL_STATES:
        if level == 'state':  # state files reuse 97 for both "Total Migration-US" and "Total Migration-Same State"
            county = '001' if 'same state' in (name or '').lower() else '000'
        code = state + county
        return f'irs:migration_aggregate:{code}', 'aggregate_cohort', (name or '').strip() or code, True
    if level == 'state' or county == '000':
        return 'geo:US:state:' + state, 'state', None, False
    return 'geo:US:county:' + state + county, 'county', None, False


def _count(text):
    text = (text or '').strip()
    if text in ('', '-1', 'd', 'D'):
        return None
    return int(float(text))


def _shard_files(context, index):
    """Map shard index -> (level, direction, y1, y2) from request URLs or single-payload names."""
    result = {}
    for shard in context.raw_shards(index):
        name = str((shard.get('request') or {}).get('url') or shard.get('url') or '')
        if not FILE.search(name):
            receipt = context.raw_receipt(index)
            name = receipt.get('original_name') or ''
        match = FILE.search(name)
        if match:
            level, direction, a, b = match.groups()
            result[shard['index']] = (level, direction, 2000 + int(a), 2000 + int(b))
    return result


def run(context):
    if not context.raw_inputs:
        raise ValueError('irs_soi_migration: raw acquisition required')
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        files = _shard_files(context, index)
        emitted = set()  # geography/aggregate entities: a few thousand
        flows = set()  # distinct origin-destination pairs over all years: a few hundred thousand short strings
        for locator, row in context.raw_rows(index, **READER):
            shard = int(locator.split('/', 1)[0].split(':')[1])
            if shard not in files:
                raise ValueError(f'{locator}: cannot identify IRS migration file layout')
            level, direction, y1, y2 = files[shard]
            row = {k.strip().lower(): v for k, v in row.items() if k}
            evidence = context.raw_evidence(locator, index)
            if level == 'county':
                origin = (_fips(row['y1_statefips'], 2), _fips(row['y1_countyfips'], 3))
                destination = (_fips(row['y2_statefips'], 2), _fips(row['y2_countyfips'], 3))
                other_name = row['y1_countyname'] if direction == 'inflow' else row['y2_countyname']
            else:
                origin = (_fips(row['y1_statefips'], 2), '000')
                destination = (_fips(row['y2_statefips'], 2), '000')
                other_name = row['y1_state_name'] if direction == 'inflow' else row['y2_state_name']
            # the "own" side is the file's subject geography; the other side may be an aggregate pseudo-code
            own, other = (destination, origin) if direction == 'inflow' else (origin, destination)
            own_id, own_type, _, _ = _endpoint(own[0], own[1], None, level)
            other_id, other_type, other_label, aggregate = _endpoint(other[0], other[1], other_name, level)
            same = own == other
            for entity_id, typ, label in ((own_id, own_type, None), (other_id, other_type, other_label)):
                if entity_id not in emitted:
                    emitted.add(entity_id)
                    pseudo = typ == 'aggregate_cohort'
                    yield {'kind': 'entity', 'id': 'irsmig:entity:' + entity_id, 'entity_id': entity_id, 'entity_type': typ,
                           'label': label or entity_id, 'observed_at': observed, 'evidence': evidence,
                           'attributes': {'irs_pseudo_fips': True, 'aggregate': True} if pseudo else {}}
            source_id, target_id = (other_id, own_id) if direction == 'inflow' else (own_id, other_id)
            period = f'{y1}{y2 % 100:02d}'
            source_code, target_code = source_id.rsplit(':', 1)[1], target_id.rsplit(':', 1)[1]
            flow_type = 'non_migrant' if same else ('aggregate' if aggregate or own[1] == '000' and level == 'county' else 'migration')
            if direction == 'outflow' and flow_type != 'aggregate':
                continue  # outflow files repeat the inflow files' origin->destination and non-migrant cells
            pair = f'{level}:{source_code}-{target_code}'
            flow = 'irs:migration_flow:' + pair
            if pair not in flows:
                flows.add(pair)
                label = f'IRS SOI {level} migration {source_id} -> {target_id}'
                yield {'kind': 'entity', 'id': 'irsmig:flow:' + pair, 'entity_id': flow, 'entity_type': 'flow', 'label': label,
                       'observed_at': observed, 'evidence': evidence, 'attributes': {'flow_type': flow_type, 'level': level}}
                yield {'kind': 'assertion', 'id': f'irsmig:flow:{pair}:source', 'subject': flow, 'predicate': 'flow_source',
                       'object': source_id, 'observed_at': observed, 'evidence': evidence, 'attributes': {}}
                yield {'kind': 'assertion', 'id': f'irsmig:flow:{pair}:destination', 'subject': flow, 'predicate': 'flow_destination',
                       'object': target_id, 'observed_at': observed, 'evidence': evidence, 'attributes': {}}
            key = f'{level}:{period}:{source_code}-{target_code}'
            dims = {'origin': source_id, 'destination': target_id, 'perspective': direction, 'flow_type': flow_type}
            for field, metric, unit, scale in (('n1', 'migration_returns', 'returns', 1), ('n2', 'migration_individuals', 'people', 1),
                                               ('agi', 'migration_agi', 'USD', 1000)):
                value = _count(row.get(field))
                record = {'kind': 'observation', 'id': f'irsmig:{key}:{metric}', 'subject': flow, 'metric': metric,
                          'value': None if value is None else value * scale, 'unit': unit,
                          'valid_from': f'{y1}-01-01', 'valid_to': f'{y2 + 1}-01-01', 'observed_at': observed,
                          'evidence': evidence, 'dimensions': dims,
                          'attributes': {'source_field': field, 'aggregate': flow_type != 'migration'}}
                if value is None:
                    record['missing_reason'] = 'suppressed_fewer_than_20_returns'
                yield record
