"""UCDP reported events with estimate bounds and source reporting context."""
from worldmodel.source_records import Emitter,sampled_rows,number


def run(context):
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        emit.at(index,line,row,receipt)
        conflict=emit.entity('ucdp:conflict:'+str(row['conflict_new_id']),'conflict',row.get('conflict_name'))
        actors=[]
        for side in ('a','b'):
            key=emit.entity('ucdp:actor:'+str(row['side_'+side+'_new_id']),'organization',row['side_'+side]);actors.append(key);emit.relation(key,'participates_in_conflict',conflict)
        place=emit.entity('ucdp:country:'+str(row['country_id']),'country',row['country']);emit.relation(conflict,'conflict_in',place)
        low,best,high=[number(row[k]) for k in ('low','best','high')]
        if not 0<=low<=best<=high:raise ValueError('Invalid reported death bounds')
        event=emit.emit('event',['event',row['id']],event_type='reported_organized_violence',participants=[conflict,place,*actors],occurred_at=row['date_start'][:10])
        event['attributes'].update(reported_start=row['date_start'],reported_end=row['date_end'],date_precision=row.get('date_prec'),
             deaths={'low':low,'best':best,'high':high},source_article=row.get('source_article'),reporting_status='source estimate; not independently verified')
        yield from emit.rows
