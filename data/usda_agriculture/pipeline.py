"""NASS annual state corn yield; suppressions remain unknown, never zero."""
from worldmodel.source_records import Emitter,sampled_rows,number
from worldmodel.util import digest


def run(context):
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        if row['commodity_desc']!='CORN' or row['statisticcat_desc']!='YIELD' or row['unit_desc']!='BU / ACRE' or row['agg_level_desc']!='STATE' or row['freq_desc']!='ANNUAL':
            raise ValueError('Adapter requires explicit annual state corn yield in BU / ACRE')
        year=int(row['year']);state=str(row['state_fips_code']).zfill(2)
        if not 1900<=year<=2100 or len(state)!=2 or not state.isdigit():raise ValueError('Invalid source year/geography')
        emit.at(index,line,row,receipt)
        place=emit.entity('us:state:'+state,'state',row.get('state_name'))
        cohort=emit.entity('nass:cohort:'+digest([state,row['commodity_desc'],row.get('domain_desc'),row.get('domaincat_desc')]),'aggregate_cohort')
        emit.relation(cohort,'within',place)
        value=str(row['Value']).strip();suppressed=value.startswith('(')
        record=emit.observation(cohort,'corn_yield',None if suppressed else number(value),'BU / ACRE')
        if suppressed:record['missing_reason']='NASS suppression '+value
        record.update(valid_from=f'{year}-01-01',valid_to=f'{year+1}-01-01')
        record['attributes'].update(suppression_code=value if suppressed else None,source_series=row.get('short_desc'),
                                    methodology='NASS published aggregate; no individual farms inferred')
        yield from emit.rows
