"""Source-specific adapters for immutable sampled JSONL, without microdata fabrication."""
import json
from .util import digest

AVAILABLE = ('census_geography','census_population','census_business','bls_labor',
             'classifications','eia_energy','fec','sec_gleif','transport','usaspending','usgs_resources')
UNAVAILABLE = ('bea_input_output','freight','usda_agriculture')
# Stable US postal/FIPS crosswalk (territories included); geographic joins never use labels.
STATE_FIPS = dict(zip('AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY AS GU MP PR VI'.split(),
 '01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56 60 66 69 72 78'.split()))

def normalize_sample(context):
    dataset = context.definition['id']
    if dataset not in AVAILABLE:
        raise ValueError(f'{dataset}: no sample normalizer available; BEA, freight and USDA samples are unavailable')
    if not context.raw_inputs: raise ValueError(f'{dataset}: no sample artifact supplied')
    seen = set()
    for index, ref in enumerate(context.raw_inputs):
        receipt = json.loads(context.raw_path(index).with_name('receipt.json').read_text())
        acquired = receipt['retrieved_at']
        for number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(),1):
            if not line.strip(): continue
            row = json.loads(line)
            evidence = context.raw_evidence(f'line:{number}',index)
            out = []
            def base(kind, identity, **fields):
                record = {'kind':kind,'id':'normalized:'+digest([dataset,ref,identity]),
                          'observed_at':acquired,'evidence':evidence,
                          'attributes':{'source_row':row,'source_dataset':dataset},**fields}
                out.append(record)
                return record
            def entity(key, typ, label=None, synthetic=False, aggregate=False, **attrs):
                if key not in seen:
                    seen.add(key)
                    r = base('entity',['entity',key],entity_id=key,entity_type=typ,label=label or key)
                    r['attributes'].update(synthetic_reference=synthetic,aggregate=aggregate,**attrs)
                return key
            def geo(state=None, label=None):
                us = entity('geo:US','country','United States',synthetic=not (label and (not state or state in ('US','00'))))
                if not state or state in ('US','00'): return us
                code = STATE_FIPS.get(state,state)
                key = entity('geo:US:state:'+code,'state',label or 'US state FIPS '+code,synthetic=not bool(label))
                rel(key,'within',us)
                return key
            def rel(subject,predicate,obj):
                identity = ('relation',subject,predicate,obj)
                if identity not in seen:
                    seen.add(identity)
                    base('assertion',identity,subject=subject,predicate=predicate,object=obj)
            def obs(subject,metric,value,unit,start=None,end=None,**attrs):
                missing = value is None or (isinstance(value,str) and value.strip() in ('','-','(D)','D','S','N','NA','null'))
                if not missing and metric not in ('development_status','legal_status'):
                    value = float(value)
                    if value.is_integer(): value = int(value)
                r = base('observation',['obs',number,subject,metric,start],subject=subject,metric=metric,
                         value=None if missing else value,unit=unit,dimensions={'subject':subject})
                r['attributes'].update(source_unit=unit,**attrs)
                if missing: r['missing_reason']='source_missing_or_suppressed'
                if start: r['valid_from']=start
                if end: r['valid_to']=end
                return r
            def year(y): return f'{int(y):04d}-01-01',f'{int(y)+1:04d}-01-01'
            if dataset == 'census_geography':
                state = geo(row['STATE'])
                key = entity('geo:US:county:'+row['GEOID'],'county',row['NAME'])
                rel(key,'within',state)
            elif dataset == 'census_population':
                level = row['SUMLEV']
                if level == '010': key = geo(label=row['NAME'])
                elif level == '040': key = geo(row['STATE'],label=row['NAME'])
                else:
                    scope = 'region' if level == '020' else 'division'
                    key = entity('geo:US:'+scope+':'+row[scope.upper()],'location',row['NAME'])
                    rel(key,'within',geo())
                for field,value in row.items():
                    if field.startswith('POPESTIMATE'):
                        start = field[-4:]+'-07-01'
                        obs(key,'population',value,'people',start,field[-4:]+'-07-02',estimate_vintage=2024,source_field=field)
            elif dataset == 'classifications':
                code = str(row['2022 NAICS Code']).strip()
                entity('naics:2022:'+code,'industry',row['2022 NAICS Title'].strip(),classification_revision='2022')
            elif dataset == 'census_business':
                state = geo(row['fipstate'])
                code = row['naics'].strip().rstrip('/')
                industry = entity('naics:2022:'+code,'industry','NAICS 2022 '+code,synthetic=True,classification_revision='2022')
                key = entity('cohort:cbp:2023:'+digest([row['fipstate'],code,row['lfo']]),'business_cohort',
                             '2023 CBP aggregate '+row['fipstate']+'/'+code+'/'+row['lfo'],aggregate=True,
                             classification_revision='2022',legal_form=row['lfo'])
                rel(key,'within',state); rel(key,'classified_as',industry)
                for field,metric,unit in [('emp','employment','people'),('est','establishment_count','establishments'),
                                          ('ap','annual_payroll','thousand_USD'),('qp1','first_quarter_payroll','thousand_USD')]:
                    # D is suppressed; noise flags G/H are retained and do not erase reported numbers.
                    value = None if row.get(field+'_nf') == 'D' else row.get(field)
                    period = ('2023-03-12','2023-03-13') if field in ('emp','est') else (('2023-01-01','2023-04-01') if field == 'qp1' else year(2023))
                    obs(key,metric,value,unit,*period,noise_flag=row.get(field+'_nf'),source_field=field,
                        reference_basis='CBP March reference period for employment/establishments; reported payroll period otherwise')
            elif dataset == 'bls_labor':
                config = receipt.get('source',{}).get('sampling',{}).get('config',{})
                series = config.get('body',{}).get('seriesid',[])
                if series != ['LNS14000000']: raise ValueError('BLS adapter requires receipt identifying LNS14000000')
                key = entity('cohort:US:civilian-labor-force:16plus','population_cohort','US civilian labor force age 16+',aggregate=True)
                rel(key,'within',geo())
                month = int(row['period'][1:]); y = int(row['year'])
                if not 1 <= month <= 12: raise ValueError('BLS annual average requires separate adapter')
                start = f'{y:04d}-{month:02d}-01'; end = f'{y+1:04d}-01-01' if month == 12 else f'{y:04d}-{month+1:02d}-01'
                obs(key,'unemployment_rate',row['value'],'percent',start,end,series_id=series[0],seasonally_adjusted=True)
            elif dataset == 'eia_energy':
                state=geo(row['stateid'])
                resource=entity('commodity:electricity','electricity','Electricity',synthetic=True)
                key=entity('cohort:eia:electricity:'+row['stateid']+':'+row['sectorid'],'aggregate_cohort',
                           row['stateDescription']+' electricity '+row['sectorName'],aggregate=True)
                rel(key,'within',state); rel(key,'measures_resource',resource)
                obs(key,'electricity_sales',row['sales'],row['sales-units'],*year(row['period']))
            elif dataset == 'transport':
                key=entity('osm:'+row['type']+':'+str(row['id']),'road',row.get('tags',{}).get('name','OSM road '+str(row['id'])))
                # The captured bounding-box query is California; county only when explicitly tagged.
                rel(key,'within',geo('CA'))
                if row.get('tags',{}).get('tiger:county') == 'San Francisco, CA':
                    county=entity('geo:US:county:06075','county','San Francisco County',synthetic=True)
                    rel(county,'within',geo('CA')); rel(key,'within',county)
                for field,metric in [('lat','latitude'),('lon','longitude')]:
                    if field in row.get('center',{}): obs(key,metric,row['center'][field],'degrees',acquired,None,spatial_role='way bounding-box center, not full geometry')
            elif dataset == 'usgs_resources':
                key=entity('mrds:'+row['dep_id'],'resource_deposit',row['site_name'],historical_inventory=True)
                obs(key,'development_status',row.get('dev_stat'),'category',None,None,valid_time_unknown=True,historical_inventory=True)
                for code in row['code_list'].split():
                    commodity=entity('commodity:mrds:'+code,'mineral','MRDS commodity '+code,synthetic=True)
                    rel(key,'contains_resource',commodity)
            elif dataset == 'sec_gleif':
                data=row['attributes']['entity']
                typ='investment_fund' if data.get('category') == 'FUND' else 'organization'
                key=entity('lei:'+row['id'],typ,data['legalName']['name'])
                country=data.get('jurisdiction')
                if country:
                    target=geo() if country=='US' else entity('geo:'+country,'country',country,synthetic=True)
                    rel(key,'registered_in',target)
                obs(key,'legal_status',data.get('status'),'category',acquired,None,validity_basis='status in acquired snapshot')
                # Relationship URLs are discovery links, not claims that their targets exist.
            elif dataset == 'fec':
                key=entity('fec:committee:'+row['committee_id'],'political_committee',row['name'])
                if row.get('state') in STATE_FIPS: rel(key,'within',geo(row['state']))
                if row.get('affiliated_committee_name'):
                    name=row['affiliated_committee_name']
                    target=entity('reference:fec:affiliate:'+digest(name),'organization',name,synthetic=True,identity_basis='unresolved source name')
                    rel(key,'affiliated_with',target)
                for candidate in row.get('candidate_ids') or []:
                    target=entity('fec:candidate:'+candidate,'person','FEC candidate '+candidate,synthetic=True)
                    rel(key,'supports_candidate',target)
            elif dataset == 'usaspending':
                key=entity('usaspending:award:'+row['generated_internal_id'],'award',row['Award ID'])
                agency=entity('usaspending:agency:'+str(row['awarding_agency_id']),'government_agency',row['Awarding Agency'])
                name=row['Recipient Name']
                recipient=entity('reference:usaspending:recipient:'+digest(name),'organization',name,synthetic=True,identity_basis='unresolved source recipient name; not matched to LEI')
                rel(key,'awarded_by',agency); rel(key,'awarded_to',recipient); rel(agency,'within',geo())
                obs(key,'award_amount',row['Award Amount'],'USD',acquired,None,validity_basis='award summary at acquisition; query window is not award validity')
            yield from out
