"""Publisher-deposited DOI records; no affiliation tenure or name-based identity inference."""
from worldmodel.source_records import Emitter,sampled_rows
from worldmodel.util import digest


def run(context):
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        emit.at(index,line,row,receipt)
        doi=row['DOI'].lower();paper=emit.entity('doi:'+doi,'research_paper','; '.join(row.get('title',[])))
        emit.claim(paper,'publication_metadata',{'doi':doi,'issued_date_parts':row.get('issued',{}).get('date-parts'),
            'published_print':row.get('published-print'),'published_online':row.get('published-online'),'publisher':row.get('publisher'),
            'url':row.get('URL'),'licenses':row.get('license',[]),
            'omitted_authors':max(0,len(row.get('author',[]))-100),
            'omitted_references':max(0,len(row.get('reference',[]))-100),
            'omitted_affiliations':sum(max(0,len(a.get('affiliation',[]))-20) for a in row.get('author',[])[:100])})
        for i,author in enumerate(row.get('author',[])[:100]):
            key='orcid:'+author['ORCID'].rsplit('/',1)[-1] if author.get('ORCID') else 'crossref:author:'+digest([doi,i,author])
            person=emit.entity(key,'researcher',' '.join(str(author.get(k,'')) for k in ('given','family')))
            emit.relation(person,'authored',paper)
            for affiliation in author.get('affiliation',[])[:20]:
                if not affiliation.get('name'):continue
                org=emit.entity('crossref:affiliation:'+digest([doi,i,affiliation]),'academic_institution',affiliation['name'])
                emit.relation(person,'research_affiliation',org)['attributes']['tenure']='unknown; publication metadata only'
        for reference in row.get('reference',[])[:100]:
            if reference.get('DOI'):
                target=emit.entity('doi:'+reference['DOI'].lower(),'publication');emit.relation(paper,'cites',target)
        yield from emit.rows
