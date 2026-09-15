"""Published premium/discount percentage of NAV (publisher formula multiplies by100)."""
from datetime import datetime
from worldmodel.source_records import Emitter,sampled_rows,number


def run(context):
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        if row['_context']['ticker']!='DIA':raise ValueError('Unexpected fund identity')
        date=datetime.strptime(row['Date'],'%d-%b-%Y').date().isoformat();emit.at(index,line,row,receipt,date)
        fund=emit.entity('ssga:fund:DIA','investment_fund',row['_context']['fund'])
        emit.observation(fund,'fund_premium_discount',number(row['Premium/Discount']),'percent')
        yield from emit.rows
