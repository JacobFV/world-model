"""Public agency feed metadata; no article-content or factual-truth inference."""
from email.utils import parsedate_to_datetime
from worldmodel.source_records import Emitter,sampled_rows
from worldmodel.util import digest


def run(context):
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        if not row.get('link') or not row.get('published'):raise ValueError('Publication needs link and timestamp')
        date=parsedate_to_datetime(row['published']).isoformat()
        emit.at(index,line,row,receipt)
        publisher=emit.entity('us:agency:nasa','government_agency','NASA')
        post=emit.entity('nasa:publication:'+digest(row.get('guid') or row['link']),'post',row.get('title'))
        emit.relation(post,'published_by',publisher)
        emit.claim(post,'publication_metadata',{'publication_time':date,'url':row['link'],'reported_author':row.get('author'),'factual_status':'publisher claim; not verified world state'})
        yield from emit.rows
