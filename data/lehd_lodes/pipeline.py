"""LEHD LODES 8: workplace area characteristics and origin-destination commute flows.

Block-level LODES rows are aggregated while streaming:

* WAC (one shard per state-year): job counts summed to census tract (11-digit GEOID) and
  county (5-digit) for total jobs, worker age, monthly earnings and NAICS sector segments.
  Zero-valued segments are omitted (total jobs are always emitted).
* OD main (in-state home and work): files are sorted by workplace block, so flows are
  accumulated one workplace tract at a time (memory bounded by home tracts of one workplace
  tract) and emitted as tract-to-tract primary-job flows; county-to-county flows carry all
  earnings and industry-group segments.
"""
import re
from worldmodel.util import digest
from worldmodel.raw_readers import iter_rows

DATASET = 'lehd_lodes'
NAICS_SECTORS = ['11', '21', '22', '23', '31-33', '42', '44-45', '48-49', '51', '52', '53', '54', '55', '56', '61', '62',
                 '71', '72', '81', '92']
WAC_SEGMENTS = ([('C000', 'total', 'all_jobs')]
                + [(f'CA0{i}', 'worker_age', v) for i, v in enumerate(['29_or_younger', '30_to_54', '55_or_older'], 1)]
                + [(f'CE0{i}', 'monthly_earnings', v) for i, v in enumerate(['1250_or_less', '1251_to_3333', 'more_than_3333'], 1)]
                + [(f'CNS{i:02d}', 'naics_sector', code) for i, code in enumerate(NAICS_SECTORS, 1)])
OD_SEGMENTS = ([('S000', 'total', 'all_jobs')]
               + [(f'SA0{i}', 'worker_age', v) for i, v in enumerate(['29_or_younger', '30_to_54', '55_or_older'], 1)]
               + [(f'SE0{i}', 'monthly_earnings', v) for i, v in enumerate(['1250_or_less', '1251_to_3333', 'more_than_3333'], 1)]
               + [(f'SI0{i}', 'industry_group', v) for i, v in enumerate(['goods_producing', 'trade_transportation_utilities',
                                                                            'all_other_services'], 1)])
FILE = re.compile(r'^([a-z]{2})_(wac|od)_(S000_JT00|main_JT00)_(\d{4})\.csv\.gz$')


def shard_name(shard):
    request = shard.get('request') or {}
    return shard.get('name') or (request.get('url') or '').split('?')[0].rsplit('/', 1)[-1]


def run(context):
    for index, _ in enumerate(context.raw_inputs):
        coverage = context.raw_coverage(index)
        if coverage['sampled'] or coverage['layout'] != 'shards':
            raise ValueError(f'{DATASET}: requires the full sharded acquisition (wm acquire lehd_lodes)')
        builder = Lodes(context, index)
        for shard in context.raw_shards(index):
            match = FILE.match(shard_name(shard))
            if not match:
                raise ValueError(f'{DATASET}: unrecognized shard {shard_name(shard)}')
            state, kind, _, year = match.groups()
            if kind == 'wac':
                yield from builder.wac(shard, state, int(year))
            else:
                yield from builder.od(shard, state, int(year))


class Lodes:
    def __init__(self, context, index):
        self.context, self.index = context, index
        self.min_pair = int(context.parameters.get('od_min_tract_pair_jobs', 1))

    def base(self, shard, kind, identity, locator, **fields):
        return {'kind': kind, 'id': f'{DATASET}:{kind}:' + digest(identity), 'observed_at': shard['retrieved_at'],
                'evidence': self.context.raw_evidence(locator, self.index), **fields}

    def geography(self, shard, state_fips, tracts, counties, locator):
        """Entities and containment for one shard (bounded by the state's tracts)."""
        us = 'geo:US'
        state = f'geo:US:state:{state_fips}'
        yield self.base(shard, 'entity', ['entity', shard['index'], state], locator, entity_id=state, entity_type='state',
                        label='US state FIPS ' + state_fips, attributes={'source_dataset': DATASET, 'state_fips': state_fips})
        yield self.base(shard, 'assertion', ['within', shard['index'], state], locator, subject=state, predicate='within',
                        object=us, attributes={'source_dataset': DATASET})
        for county in sorted(counties):
            key = f'geo:US:county:{county}'
            yield self.base(shard, 'entity', ['entity', shard['index'], key], locator, entity_id=key, entity_type='county',
                            label='US county FIPS ' + county, attributes={'source_dataset': DATASET, 'county_fips': county})
            yield self.base(shard, 'assertion', ['within', shard['index'], key], locator, subject=key, predicate='within',
                            object=state, attributes={'source_dataset': DATASET})
        for tract in sorted(tracts):
            key = f'geo:US:tract:{tract}'
            yield self.base(shard, 'entity', ['entity', shard['index'], key], locator, entity_id=key, entity_type='jurisdiction',
                            label='Census tract ' + tract, attributes={'source_dataset': DATASET, 'tract_geoid': tract,
                                                                       'geography_level': 'census_tract_2020'})
            yield self.base(shard, 'assertion', ['within', shard['index'], key], locator, subject=key, predicate='within',
                            object=f'geo:US:county:{tract[:5]}', attributes={'source_dataset': DATASET})

    # ----- workplace area characteristics ---------------------------------------------------------
    def wac(self, shard, state, year):
        columns = [c for c, _, _ in WAC_SEGMENTS]
        tracts, counties, blocks = {}, {}, {}
        state_fips = None
        for locator, row in iter_rows([shard], {'format': 'csv'}):
            block = row['w_geocode']
            if len(block) != 15 or not block.isdigit():
                raise ValueError(f'{locator}: invalid block GEOID {block!r}')
            state_fips = state_fips or block[:2]
            values = [int(row[c]) for c in columns]
            for table, key in ((tracts, block[:11]), (counties, block[:5])):
                current = table.get(key)
                if current is None:
                    table[key] = values[:]
                    blocks[key] = 1
                else:
                    for i, v in enumerate(values):
                        current[i] += v
                    blocks[key] += 1
        if state_fips is None:
            return
        locator = f'shard:{shard["index"]}'
        yield from self.geography(shard, state_fips, tracts, counties, locator)
        bounds = {'valid_from': f'{year:04d}-01-01', 'valid_to': f'{year + 1:04d}-01-01'}
        for level, table in (('census_tract', tracts), ('county', counties)):
            for key in sorted(table):
                subject = f'geo:US:{"tract" if level == "census_tract" else "county"}:{key}'
                for (column, segment_type, segment), value in zip(WAC_SEGMENTS, table[key]):
                    if value == 0 and column != 'C000':
                        continue
                    yield self.base(shard, 'observation', ['wac', year, key, column], locator, subject=subject,
                                    metric='jobs_count', value=value, unit='jobs', **bounds,
                                    dimensions={'segment_type': segment_type, 'segment': segment, 'place_basis': 'workplace',
                                                'job_type': 'all_jobs', 'frequency': 'annual', 'lodes_year': year,
                                                'aggregation': level},
                                    attributes={'source_dataset': DATASET, 'lodes_column': column, 'blocks_aggregated': blocks[key],
                                                'aggregation_method': 'sum_of_noise_infused_block_counts',
                                                'zero_segments_omitted': True, 'lodes_version': 'LODES8'})

    # ----- origin-destination flows ---------------------------------------------------------------
    def od(self, shard, state, year):
        columns = [c for c, _, _ in OD_SEGMENTS]
        bounds = {'valid_from': f'{year:04d}-01-01', 'valid_to': f'{year + 1:04d}-01-01'}
        county_pairs, tracts, counties = {}, set(), set()
        finished = set()
        current_tract, homes, first_line = None, {}, None
        state_fips = None

        def flush(work_tract, homes, first):
            locator = f'shard:{shard["index"]}/line:{first}'
            residual, below = 0, 0
            for home in sorted(homes):
                jobs = homes[home]
                if jobs < self.min_pair:
                    residual += jobs
                    below += 1
                    continue
                yield self.base(shard, 'observation', ['od', year, work_tract, home], locator,
                                subject=f'geo:US:tract:{work_tract}', metric='commuting_jobs', value=jobs, unit='jobs', **bounds,
                                dimensions={'home_geography': f'geo:US:tract:{home}', 'segment_type': 'total',
                                            'segment': 'all_jobs', 'job_type': 'primary_jobs', 'flow_scope': 'in_state_main',
                                            'aggregation': 'census_tract_pair', 'frequency': 'annual', 'lodes_year': year},
                                attributes={'source_dataset': DATASET, 'direction': 'home_tract_to_work_tract',
                                            'aggregation_method': 'sum_of_noise_infused_block_pairs', 'lodes_version': 'LODES8'})
            if below:
                yield self.base(shard, 'observation', ['od-residual', year, work_tract], locator,
                                subject=f'geo:US:tract:{work_tract}', metric='commuting_jobs', value=residual, unit='jobs',
                                **bounds, dimensions={'home_geography': 'other_tracts_below_threshold', 'segment_type': 'total',
                                                      'segment': 'all_jobs', 'job_type': 'primary_jobs',
                                                      'flow_scope': 'in_state_main', 'aggregation': 'census_tract_pair_residual',
                                                      'frequency': 'annual', 'lodes_year': year},
                                attributes={'source_dataset': DATASET, 'home_tracts_aggregated': below,
                                            'threshold_jobs': self.min_pair})

        for locator, row in iter_rows([shard], {'format': 'csv'}):
            work, home = row['w_geocode'], row['h_geocode']
            state_fips = state_fips or work[:2]
            work_tract, home_tract = work[:11], home[:11]
            if work_tract != current_tract:
                if current_tract is not None:
                    finished.add(current_tract)
                    yield from flush(current_tract, homes, first_line)
                if work_tract in finished:
                    raise ValueError(f'{locator}: OD file is not grouped by workplace tract; streaming aggregation unsafe')
                current_tract, homes = work_tract, {}
                first_line = locator.rsplit(':', 1)[-1]
            values = [int(row[c]) for c in columns]
            homes[home_tract] = homes.get(home_tract, 0) + values[0]
            tracts.add(work_tract)
            tracts.add(home_tract)
            counties.add(work[:5])
            counties.add(home[:5])
            pair = (work[:5], home[:5])
            totals = county_pairs.get(pair)
            if totals is None:
                county_pairs[pair] = values
            else:
                for i, v in enumerate(values):
                    totals[i] += v
        if current_tract is not None:
            yield from flush(current_tract, homes, first_line)
        if state_fips is None:
            return
        locator = f'shard:{shard["index"]}'
        yield from self.geography(shard, state_fips, tracts, counties, locator)
        for (work_county, home_county) in sorted(county_pairs):
            for (column, segment_type, segment), value in zip(OD_SEGMENTS, county_pairs[(work_county, home_county)]):
                if value == 0 and column != 'S000':
                    continue
                yield self.base(shard, 'observation', ['od-county', year, work_county, home_county, column], locator,
                                subject=f'geo:US:county:{work_county}', metric='commuting_jobs', value=value, unit='jobs',
                                **bounds, dimensions={'home_geography': f'geo:US:county:{home_county}',
                                                      'segment_type': segment_type, 'segment': segment,
                                                      'job_type': 'primary_jobs', 'flow_scope': 'in_state_main',
                                                      'aggregation': 'county_pair', 'frequency': 'annual', 'lodes_year': year},
                                attributes={'source_dataset': DATASET, 'lodes_column': column, 'zero_segments_omitted': True,
                                            'direction': 'home_county_to_work_county'})
