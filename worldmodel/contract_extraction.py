"""Bounded labelled-clause extraction and explicit human-reviewed obligation mapping.

This adapter handles UTF-8 text and simple two-column text tables. It does not
claim general PDF/OCR/legal interpretation, counterparty inference or completeness.
"""
from copy import deepcopy
from datetime import date
import hashlib
import math
import re
from .util import digest

FIELDS={'borrower','lender','principal','currency','annual_rate','rate_type','maturity','collateral','seniority','schedule','effective_date','supersedes'}
METHOD={'name':'labelled-text-and-two-column-table','version':'1','model':None}


def extract_candidates(document,*,max_characters=1000000,max_candidates=10000):
    from .model import instant
    if set(document)!={'text','sha256','accession','url','observed_at'}:raise ValueError('Document requires text/hash/accession/url/observed_at')
    if type(max_characters) is not int or not 1<=max_characters<=1000000 or type(max_candidates) is not int or not 1<=max_candidates<=10000:raise ValueError('Invalid extraction bounds')
    text=document['text']
    if not isinstance(text,str) or len(text)>max_characters or len(text.encode())>4000000:raise ValueError('Document character/byte budget exceeded')
    if hashlib.sha256(text.encode()).hexdigest()!=document['sha256']:raise ValueError('Document hash mismatch')
    if not any(isinstance(document[k],str) and document[k].strip() for k in ('accession','url')):raise ValueError('Document accession or URL required')
    instant(document['observed_at']);section=None;offset=0;out=[]
    for number,line in enumerate(text.splitlines(keepends=True),1):
        if line.lstrip().startswith('#'):section=line.strip().lstrip('#').strip()
        match=re.match(r'\s*([A-Za-z _]+?)\s*([:|])\s*(.*?)\s*$',line)
        if match:
            field=match[1].strip().lower().replace(' ','_')
            if field in FIELDS and match[3]:
                if len(out)>=max_candidates:raise ValueError('Candidate budget exceeded')
                start,end=offset+match.start(3),offset+match.end(3)
                claim={'document':{k:v for k,v in document.items() if k!='text'},'field':field,'value':match[3],
                    'quote':text[start:end],'locator':{'section':section,'line':number,'start':start,'end':end,
                        'table_row':number if match[2]=='|' else None,'table_column':2 if match[2]=='|' else None},
                    'method':deepcopy(METHOD),'confidence':0.7,'effective_date':None,'status':'candidate'}
                claim['id']='contract_candidate:'+digest(claim);out.append(claim)
        offset+=len(line)
    return out


def _value(field,value):
    if field in ('principal','annual_rate'):
        try:number=float(value.replace(',','').rstrip('%'))
        except (ValueError,AttributeError) as exc:raise ValueError('Noncanonical numeric candidate') from exc
        if field=='annual_rate' and value.endswith('%'):number/=100
        if not math.isfinite(number) or number<0:raise ValueError('Nonnegative finite term required')
        return number
    if field=='maturity':return date.fromisoformat(value).isoformat()
    return value


def review_obligation(candidates,review):
    from .model import identifier,instant
    if len(candidates)>10000:raise ValueError('Review candidate budget exceeded')
    keys={'id','candidate_ids','terms','parties','schedule','collateral','seniority','completeness','disposition','reviewer','reviewed_at','valid_from','valid_to','supersedes'}
    if set(review)!=keys:raise ValueError('Review requires exact declared fields')
    identifier(review['id']);known=instant(review['reviewed_at'])
    if not isinstance(review['reviewer'],str) or not review['reviewer'].strip():raise ValueError('Explicit reviewer required')
    if not isinstance(review['supersedes'],list) or len(review['supersedes'])>100:raise ValueError('Invalid superseded obligations')
    for key in review['supersedes']:identifier(key)
    if review['completeness'] not in ('complete','partial','unknown') or review['disposition'] not in ('approved','incomplete','superseded','rejected'):raise ValueError('Invalid completeness/disposition')
    if instant(review['valid_from'])>=instant(review['valid_to']):raise ValueError('Review requires positive explicit validity')
    by_id={}
    for c in candidates:
        body={k:v for k,v in c.items() if k not in ('id','evidence')}
        if c['id']!='contract_candidate:'+digest(body) or c['value']!=c['quote'] or c['method']!=METHOD:raise ValueError('Invalid candidate identity/content')
        if c['id'] in by_id:raise ValueError('Duplicate candidate')
        by_id[c['id']]=c
    if not isinstance(review['candidate_ids'],dict) or set(review['candidate_ids'])-FIELDS:raise ValueError('Unknown reviewed field')
    selected={}
    for field,ids in review['candidate_ids'].items():
        if not isinstance(ids,list) or not ids or len(ids)>100 or len(set(ids))!=len(ids):raise ValueError('Explicit bounded candidate references required')
        selected[field]=[]
        for key in ids:
            c=by_id.get(key)
            if c is None or c['field']!=field or instant(c['document']['observed_at'])>known:raise ValueError('Candidate field or point-in-time mismatch')
            selected[field].append(c)
    result=deepcopy(review);result['simulation_eligible']=False
    if review['disposition']!='approved':return result
    if review['completeness']!='complete':raise ValueError('Approved obligations require complete review')
    terms=review['terms'];required={'borrower','lender','principal','annual_rate','rate_type','maturity','currency'}
    if not isinstance(terms,dict) or set(terms)!=required:raise ValueError('Complete exact obligation terms required')
    if set(review['parties'])!={'borrower','lender'}:raise ValueError('Explicit resolved counterparties required')
    for field in ('borrower','lender'):
        party=review['parties'][field]
        if set(party)!={'entity_id','label','resolution_evidence'} or not isinstance(party['resolution_evidence'],list) or not party['resolution_evidence']:raise ValueError('Counterparty resolution evidence required')
        identifier(party['entity_id'])
        if party['entity_id']!=terms[field] or not selected.get(field) or any(c['value']!=party['label'] for c in selected[field]):raise ValueError('Counterparty review does not match candidates')
    if terms['borrower']==terms['lender']:raise ValueError('Counterparties must differ')
    for field in ('principal','annual_rate'):
        if type(terms[field]) not in (int,float) or not math.isfinite(terms[field]) or terms[field]<0:raise ValueError('Nonnegative finite term required')
    if not re.fullmatch('[A-Z]{3}',terms['currency']) or terms['rate_type'] not in ('fixed','floating'):raise ValueError('Explicit currency and rate type required')
    date.fromisoformat(terms['maturity'])
    for field in ('principal','annual_rate','maturity','currency','rate_type','schedule','collateral','seniority'):
        target=terms[field] if field in terms else review[field]
        if not selected.get(field) or any(_value(field,c['value'])!=target for c in selected[field]):raise ValueError('Reviewed term contradicts or lacks candidate evidence: '+field)
    result['simulation_eligible']=(review['schedule']=='bullet_act365' and review['collateral']=='unsecured' and review['seniority']=='pari_passu' and terms['currency']=='USD')
    return result


def bounded_rows(context):
    """Canonical JSONL import with pre-read aggregate bytes and row bounds."""
    import json
    if not 1<=len(context.raw_inputs)<=100:raise ValueError('Require 1..100 explicit imported files')
    paths=[context.raw_path(i) for i in range(len(context.raw_inputs))]
    if sum(p.stat().st_size for p in paths)>16000000:raise ValueError('Import byte budget exceeded')
    count=0
    for index,path in enumerate(paths):
        with path.open(encoding='utf-8') as stream:
            for number,line in enumerate(stream,1):
                if not line.strip():continue
                count+=1
                if count>10000:raise ValueError('Import row budget exceeded')
                row=json.loads(line)
                if not isinstance(row,dict):raise ValueError('JSONL objects required')
                yield index,'line:'+str(number),row
