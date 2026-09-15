"""NAV per fund share; no market-price or total-return inference."""
from datetime import datetime
from worldmodel.source_records import Emitter,sampled_rows,number


def run(context):
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        if row['_context']['ticker']!='DIA':raise ValueError('Unexpected fund identity')
        date=datetime.strptime(row['Date'],'%d-%b-%Y').date().isoformat()
        emit.at(index,line,row,receipt,date)
        fund=emit.entity('ssga:fund:DIA','investment_fund',row['_context']['fund'])
        for key,metric,unit in [('NAV','fund_nav','USD/share'),('Shares Outstanding','fund_shares_outstanding','shares'),('Total Net Assets','fund_net_assets','USD')]:
            value=number(row[key])
            if value<0:raise ValueError('Negative fund value')
            emit.observation(fund,metric,value,unit)
        yield from emit.rows
