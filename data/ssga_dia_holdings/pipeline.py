"""Dated fund positions, preserving generic published identifiers without issuer inference."""
from datetime import datetime
from worldmodel.source_records import Emitter,sampled_rows,number
from worldmodel.util import digest


def run(context):
    emit=Emitter(context)
    for index,line,row,receipt in sampled_rows(context):
        metadata=row['_context']
        if metadata['ticker']!='DIA':raise ValueError('Unexpected fund identity')
        date=datetime.strptime(metadata['date'].removeprefix('As of '),'%d-%b-%Y').date().isoformat()
        emit.at(index,line,row,receipt,date)
        fund=emit.entity('ssga:fund:DIA','investment_fund',metadata['fund'])
        identifier=row.get('Identifier')
        if not identifier:raise ValueError('Holding lacks published instrument identifier')
        cash=row['Name']=='US DOLLAR' and row['Ticker']=='-' and row['Local Currency']=='USD'
        security=emit.entity('ssga:instrument:'+digest([identifier,row['Local Currency']]),'cash_balance' if cash else 'security',row['Name'])
        emit.claim(security,'published_instrument_identifier',{'publisher_field':'Identifier','value':identifier,'namespace':'ssga_published_identifier','ticker':row['Ticker'],'currency':row['Local Currency']})
        position=emit.entity('ssga:DIA:position:'+digest([date,security]),'investment_position')
        emit.relation(position,'position_holder',fund);emit.relation(position,'position_instrument',security)
        shares=number(row['Shares Held']);weight=number(row['Weight'])
        if shares<0 or not 0<=weight<=100:raise ValueError('Invalid shares/portfolio percent')
        emit.observation(position,'position_cash' if cash else 'position_shares',shares,'USD' if cash else 'shares')
        emit.observation(position,'portfolio_weight',weight,'percent')
        yield from emit.rows
