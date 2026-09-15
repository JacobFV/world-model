"""Bounded standardized evidence; no inferred ownership, travelers or causal links."""
from datetime import date, timedelta
import json
import math
from .util import digest

SOURCE_IDS=('fred_policy_rate','fred_treasury2y','fred_treasury10y','fred_breakeven10y',
 'fred_oil_price','fred_cpi','treasury_debt','fdic_bank_financials','sec_company_assets',
 'osm_topology','airport_nodes','marine_ais','fec_candidates')
SERIES={'DFF':('policy_rate','percent'),'DGS2':('treasury_yield_2y','percent'),
 'DGS10':('treasury_yield_10y','percent'),'T10YIE':('breakeven_inflation_10y','percent'),
 'DCOILWTICO':('oil_price','USD/barrel'),'CPIAUCSL':('consumer_price_index','index_1982_1984_100')}

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

def next_day(value):return (date.fromisoformat(value)+timedelta(days=1)).isoformat()
def month_end(value):
    d=date.fromisoformat(value)
    return date(d.year+int(d.month==12),1 if d.month==12 else d.month+1,1).isoformat()

def normalize(context):
    dataset=context.definition['id']
    if dataset not in SOURCE_IDS or dataset=='marine_ais':raise ValueError('No acquired source adapter: '+dataset)
    if not context.raw_inputs:raise ValueError('Source sample artifact required')
    seen=set(); record_ids=set()
    for index,ref in enumerate(context.raw_inputs):
        receipt=json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired=receipt['retrieved_at']
        for line_number,line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(),1):
            if not line.strip():continue
            row=json.loads(line); out=[]
            def base(kind,identity,**fields):
                r={'kind':kind,'id':'strategic:'+digest([dataset,ref,identity]),'observed_at':acquired,
                   'evidence':context.raw_evidence('line:'+str(line_number),index),
                   'attributes':{'source_dataset':dataset,'source_row':row,'coverage':'sample_only'},**fields}
                if r['id'] not in record_ids:
                    record_ids.add(r['id']);out.append(r)
                return r
            def entity(key,typ,label=None,**attrs):
                if key not in seen:
                    seen.add(key)
                    r=base('entity',['entity',key],entity_id=key,entity_type=typ,label=label or key)
                    r['attributes'].update(attrs)
                return key
            def relation(subject,predicate,obj,**attrs):
                r=base('assertion',[line_number,subject,predicate,obj,attrs],subject=subject,predicate=predicate,object=obj)
                r['attributes'].update(attrs)
            def observation(subject,metric,value,unit,start=None,end=None,scale=1,**attrs):
                missing=value is None or str(value).strip() in ('','.','NA','N/A','null')
                if not missing:
                    value=float(value)*scale
                    if not math.isfinite(value):raise ValueError('Nonfinite source measurement')
                    if value.is_integer():value=int(value)
                r=base('observation',[line_number,subject,metric,start],subject=subject,metric=metric,
                       value=None if missing else value,unit=unit,dimensions={'subject':subject})
                if start:r['valid_from']=start
                if end:r['valid_to']=end
                if missing:r['missing_reason']='source_missing'
                r['attributes'].update(attrs)
            if dataset.startswith('fred_'):
                day=row.get('observation_date',row.get('DATE'))
                if not day:raise ValueError('FRED date field missing')
                found=False
                for series,(metric,unit) in SERIES.items():
                    if series not in row:continue
                    found=True
                    key=entity('fred:'+series,'economic_series',series,series_id=series,
                               source_url='https://fred.stlouisfed.org/series/'+series)
                    observation(key,metric,row[series],unit,day,month_end(day) if series=='CPIAUCSL' else next_day(day),
                                source_series=series,vintage='acquired_current_vintage',
                                interpretation='market breakeven includes risk/liquidity premia; not pure expected inflation' if series=='T10YIE' else 'reported series level')
                if not found:raise ValueError('No recognized FRED series columns')
            elif dataset=='treasury_debt':
                key=entity('us:agency:treasury','government_agency','US Treasury')
                for field,metric in [('tot_pub_debt_out_amt','public_debt'),('debt_held_public_amt','debt_held_by_public'),('intragov_hold_amt','intragovernmental_debt')]:
                    observation(key,metric,row[field],'USD',row['record_date'],next_day(row['record_date']),source_field=field)
            elif dataset=='fdic_bank_financials':
                data=row.get('data',row); rep=str(data['REPDTE']).replace('-','')
                day=f'{rep[:4]}-{rep[4:6]}-{rep[6:8]}'
                key=entity('fdic:cert:'+str(data['CERT']),'bank',data['NAME'],fdic_certificate=data['CERT'])
                for field,metric in [('ASSET','total_assets'),('DEP','bank_deposits'),('LNLSNET','bank_net_loans'),('EQ','bank_equity'),('NETINC','bank_net_income')]:
                    observation(key,metric,data.get(field),'USD',rep[:4]+'-01-01' if field=='NETINC' else day,next_day(day),
                                scale=1000,source_field=field,source_unit='thousand_USD',
                                period_basis='year_to_date' if field=='NETINC' else 'report_date_stock')
            elif dataset=='sec_company_assets':
                key=entity('sec:cik:0000320193','business','Apple Inc.',cik='0000320193')
                observation(key,'total_assets',row['val'],'USD',row['end'],next_day(row['end']),
                            accession=row['accn'],filing_date=row.get('filed'),form=row.get('form'),
                            taxonomy='us-gaap',concept='Assets',revision_policy='all_filings_preserved')
            elif dataset=='fec_candidates':
                entity('fec:candidate:'+row['candidate_id'],'person',row['name'],
                       candidate_id=row['candidate_id'],candidate_office=row.get('office'),
                       candidate_state=row.get('state'),candidate_district=row.get('district'),
                       candidate_party=row.get('party'),election_years=row.get('election_years',[]),
                       identity_basis='published FEC candidate identifier',
                       temporal_scope='historical candidacy record; no current office or location inferred')
            elif dataset=='airport_nodes':
                key=entity('ourairports:'+str(row['id']),'airport',row['name'],ident=row['ident'],
                           iata_code=row.get('iata_code'),icao_code=row.get('icao_code'),airport_type=row['type'])
                for field,metric in [('latitude_deg','latitude'),('longitude_deg','longitude')]:
                    observation(key,metric,row[field],'degrees',acquired,validity_basis='acquired_snapshot')
            elif dataset=='osm_topology':
                if row.get('type')!='way':raise ValueError('OSM topology adapter expects ways with geometry')
                nodes=row.get('nodes',[]); geometry=row.get('geometry',[]); tags=row.get('tags',{})
                if len(nodes)<2 or len(nodes)!=len(geometry) or any('lat' not in g or 'lon' not in g for g in geometry):
                    raise ValueError('OSM way missing complete node geometry')
                way=entity('osm:way:'+str(row['id']),'road',tags.get('name'),osm_tags=tags,node_ids=nodes,geometry=geometry)
                for node,point in zip(nodes,geometry):
                    key=entity('osm:node:'+str(node),'road_junction',osm_node_id=node)
                    relation(way,'road_has_junction',key)
                    for coordinate,metric in [('lat','latitude'),('lon','longitude')]:
                        observation(key,metric,point[coordinate],'degrees',acquired,validity_basis='acquired_snapshot')
                one=str(tags.get('oneway','')).lower()
                forward=one not in ('-1','reverse')
                backward=one in ('no','0','false') or (not one and tags.get('junction')!='roundabout' and tags.get('highway') not in ('motorway','motorway_link'))
                if one in ('-1','reverse'):backward=True
                for i,(a,b) in enumerate(zip(nodes,nodes[1:])):
                    for source,target in ([(a,b)] if forward else [])+([(b,a)] if backward else []):
                        relation('osm:node:'+str(source),'road_connects_to','osm:node:'+str(target),
                                 way_id=way,segment_index=i,osm_tags=tags,access_rules_require_routing_interpretation=True)
            yield from out
