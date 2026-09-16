"""Shared evidence emission mechanics; source-specific interpretation stays local."""
import json
import math
from datetime import date, timedelta
from .util import digest


def sampled_rows(context):
    for index,ref in enumerate(context.raw_inputs):
        path=context.raw_path(index)
        receipt=json.loads(path.with_name('receipt.json').read_text())
        with path.open(encoding='utf-8-sig') as stream:
            for line_number,line in enumerate(stream,1):
                if line.strip():yield index,line_number,json.loads(line),receipt


def full_rows(context, **reader):
    """Yield (raw index, locator, row, receipt) over every raw input, sharded or not.

    Streams CSV/TSV/pipe (plain, gzip, ZIP members), JSONL, JSON pages and XLSX
    via worldmodel.raw_readers. Locators look like ``shard:3/member:a.csv/line:12``
    and belong in ``context.raw_evidence(locator, index)``.
    """
    for index, _ in enumerate(context.raw_inputs):
        receipt = context.raw_receipt(index)
        for locator, row in context.raw_rows(index, **reader):
            yield index, locator, row, receipt


def number(value):
    result=float(str(value).replace(',',''))
    if not math.isfinite(result):raise ValueError('Nonfinite source quantity')
    return result


class Emitter:
    def __init__(self,context):self.context=context;self.seen=set();self.rows=[]
    def at(self,index,line,row,receipt,date=None):
        self.index,self.line,self.row,self.receipt,self.date=index,line,row,receipt,date
        self.rows=[]
        return self
    def emit(self,kind,identity,**fields):
        locator='line:'+str(self.line)
        attrs={'source_dataset':self.context.definition['id'],'source_row':self.row,'coverage':'bounded_sample','representative':False}
        if self.date:attrs['snapshot_date']=self.date
        record={'kind':kind,'id':'source:'+digest([self.context.definition['id'],self.context.raw_inputs[self.index],identity]),
                'observed_at':self.receipt['retrieved_at'],'evidence':self.context.raw_evidence(locator,self.index),'attributes':attrs,**fields}
        self.rows.append(record);return record
    def entity(self,key,typ,label=None):
        if key not in self.seen:self.seen.add(key);self.emit('entity',['entity',key],entity_id=key,entity_type=typ,label=label or key)
        return key
    def relation(self,subject,predicate,target):return self.emit('assertion',[self.line,subject,predicate,target],subject=subject,predicate=predicate,object=target)
    def claim(self,subject,predicate,value):return self.emit('assertion',[self.line,subject,predicate,value],subject=subject,predicate=predicate,value=value)
    def observation(self,subject,metric,value,unit):
        return self.emit('observation',[self.line,subject,metric],subject=subject,metric=metric,value=value,unit=unit,
                         dimensions={'snapshot_date':self.date} if self.date else {},
                         **({'valid_from':self.date,'valid_to':(date.fromisoformat(self.date)+timedelta(days=1)).isoformat()} if self.date else {}))
