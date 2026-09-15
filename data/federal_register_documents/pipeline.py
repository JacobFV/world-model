"""OFR document metadata; proposed, rule and notice are distinct source statuses."""
from worldmodel.source_records import Emitter,sampled_rows


def run(context):
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        emit.at(index,line,row,receipt,row['publication_date'])
        typ=row['type'];legal=typ in ('Rule','Proposed Rule','RULE','PRORULE')
        document=emit.entity('federalregister:'+row['document_number'],'regulation' if legal else 'publication',row['title'])
        emit.claim(document,'document_status',{'published_type':typ,'publication_date':row['publication_date'],
                   'effective_date':row.get('effective_on'),'effective_status':'unknown' if not row.get('effective_on') else 'source_effective_date',
                   'url':row['html_url'],'official_pdf':row.get('pdf_url'),'edition':'informational API rendition'})
        for agency in row.get('agencies',[]):
            if agency.get('id') is None:continue
            key=emit.entity('federalregister:agency:'+str(agency['id']),'government_agency',agency['name'])
            emit.relation(document,'issued_document_by',key)
        if legal and row.get('effective_on'):emit.claim(document,'legal_effective_date',row['effective_on'])
        yield from emit.rows
