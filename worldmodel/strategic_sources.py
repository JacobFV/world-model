"""Shared ontology declarations and compatibility access to dataset-local pipelines."""
from .source_helpers import SERIES, next_day, month_end, run_local_pipeline

SOURCE_IDS=('fred_policy_rate','fred_treasury2y','fred_treasury10y','fred_breakeven10y',
 'fred_oil_price','fred_cpi','treasury_debt','fdic_bank_financials','sec_company_assets',
 'osm_topology','airport_nodes','marine_ais','fec_candidates')

def schema():
    variables={metric:{'type':'number','unit':unit,'domain':'economic_series'} for metric,unit in SERIES.values()}
    for metric,domain in [('total_assets','organization'),('bank_deposits','bank'),('bank_net_loans','bank'),
                          ('bank_equity','bank'),('bank_net_income','bank'),('public_debt','government_agency'),
                          ('debt_held_by_public','government_agency'),('intragovernmental_debt','government_agency')]:
        variables[metric]={'type':'number','unit':'USD','domain':domain}
    return {'entity_types':{'economic_series':{'parent':'entity'},'bank':{'parent':'business'},
                            'road_junction':{'parent':'location'}},
            'relations':{'road_has_junction':{'domain':'road','range':'road_junction'},
                         'road_connects_to':{'domain':'road_junction','range':'road_junction'}},
            'variables':variables}


def normalize(context):
    dataset = context.definition['id']
    if dataset not in SOURCE_IDS:
        raise ValueError('No acquired source adapter: ' + dataset)
    return run_local_pipeline(context)
