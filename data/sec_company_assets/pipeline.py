"""SEC XBRL companyfacts -> point-in-time issuer financial facts for a documented tag set.

Full acquisitions are the nightly companyfacts.zip (one JSON document per CIK). Only the
concepts in ``CONCEPTS`` are normalized (balance sheet, income statement, cash flow,
shares, EPS, dividends, cover-page float; IFRS equivalents for foreign private issuers)
and only from periodic/current report forms in ``FORMS``. companyfacts carries no
dimensional (segment) facts, so segments are not available from this source.

Point in time: ``observed_at`` is the filing date (knowledge time). For each
(concept, unit, period) the first reported value is emitted, and a later filing is emitted
again only when it reports a different value (``revision: restated``); unchanged comparative
repeats are dropped (they add no new knowledge). Instants are valid [end, end+1d); durations
[start, end+1d). Legacy JSONL samples (companyconcept rows) keep the original mapping.
"""
from datetime import date, timedelta
import json
import math
from worldmodel.util import digest
from worldmodel.source_helpers import next_day

CONCEPTS = {
    'us-gaap': {
        'Assets': 'total_assets', 'AssetsCurrent': 'current_assets', 'Liabilities': 'total_liabilities',
        'LiabilitiesCurrent': 'current_liabilities', 'LiabilitiesAndStockholdersEquity': 'liabilities_and_equity',
        'StockholdersEquity': 'stockholders_equity',
        'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest': 'total_equity_including_nci',
        'CashAndCashEquivalentsAtCarryingValue': 'cash_and_equivalents',
        'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents': 'cash_restricted_cash_and_equivalents',
        'AccountsReceivableNetCurrent': 'accounts_receivable', 'InventoryNet': 'inventory',
        'PropertyPlantAndEquipmentNet': 'ppe_net', 'Goodwill': 'goodwill',
        'IntangibleAssetsNetExcludingGoodwill': 'intangible_assets_net', 'AccountsPayableCurrent': 'accounts_payable',
        'LongTermDebt': 'long_term_debt', 'LongTermDebtNoncurrent': 'long_term_debt_noncurrent',
        'LongTermDebtCurrent': 'long_term_debt_current', 'ShortTermBorrowings': 'short_term_borrowings',
        'RetainedEarningsAccumulatedDeficit': 'retained_earnings', 'Deposits': 'deposits',
        'LoansAndLeasesReceivableNetReportedAmount': 'net_loans',
        'Revenues': 'revenue', 'RevenueFromContractWithCustomerExcludingAssessedTax': 'revenue', 'SalesRevenueNet': 'revenue',
        'CostOfRevenue': 'cost_of_revenue', 'CostOfGoodsAndServicesSold': 'cost_of_revenue', 'GrossProfit': 'gross_profit',
        'OperatingExpenses': 'operating_expenses', 'CostsAndExpenses': 'costs_and_expenses', 'ResearchAndDevelopmentExpense': 'research_and_development_expense',
        'SellingGeneralAndAdministrativeExpense': 'sga_expense', 'OperatingIncomeLoss': 'operating_income',
        'InterestExpense': 'interest_expense', 'IncomeTaxExpenseBenefit': 'income_tax_expense',
        'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest': 'pretax_income',
        'NetIncomeLoss': 'net_income', 'ProfitLoss': 'net_income_including_nci',
        'EarningsPerShareBasic': 'eps_basic', 'EarningsPerShareDiluted': 'eps_diluted',
        'WeightedAverageNumberOfSharesOutstandingBasic': 'weighted_average_shares_basic',
        'WeightedAverageNumberOfDilutedSharesOutstanding': 'weighted_average_shares_diluted',
        'DepreciationDepletionAndAmortization': 'depreciation_amortization', 'ShareBasedCompensation': 'share_based_compensation',
        'NetCashProvidedByUsedInOperatingActivities': 'operating_cash_flow',
        'NetCashProvidedByUsedInInvestingActivities': 'investing_cash_flow',
        'NetCashProvidedByUsedInFinancingActivities': 'financing_cash_flow',
        'PaymentsToAcquirePropertyPlantAndEquipment': 'capital_expenditures', 'PaymentsOfDividends': 'dividends_paid',
        'PaymentsOfDividendsCommonStock': 'dividends_paid_common', 'PaymentsForRepurchaseOfCommonStock': 'share_repurchases',
        'ProceedsFromIssuanceOfLongTermDebt': 'debt_issuance_proceeds', 'RepaymentsOfLongTermDebt': 'debt_repayments',
        'CommonStockSharesOutstanding': 'shares_outstanding',
        'CommonStockDividendsPerShareDeclared': 'dividends_declared_per_share'},
    'dei': {'EntityCommonStockSharesOutstanding': 'shares_outstanding_cover', 'EntityPublicFloat': 'public_float'},
    'ifrs-full': {
        'Revenue': 'revenue', 'ProfitLoss': 'net_income_including_nci', 'ProfitLossAttributableToOwnersOfParent': 'net_income',
        'Assets': 'total_assets', 'Liabilities': 'total_liabilities', 'Equity': 'total_equity_including_nci',
        'CashAndCashEquivalents': 'cash_and_equivalents', 'BasicEarningsLossPerShare': 'eps_basic',
        'DilutedEarningsLossPerShare': 'eps_diluted', 'CashFlowsFromUsedInOperatingActivities': 'operating_cash_flow'},
}
FORMS = {'10-K', '10-K/A', '10-Q', '10-Q/A', '10-KT', '10-KT/A', '10-QT', '10-QT/A', '20-F', '20-F/A', '40-F', '40-F/A',
         '6-K', '6-K/A', '8-K', '8-K/A'}
UNITS = {'USD/shares': 'USD/share', 'pure': 'ratio'}


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            yield from _full(context, index, receipt)
        else:
            yield from _sample(context, index, receipt)


def _valid_day(text):
    try:
        return date.fromisoformat(text).isoformat()
    except (TypeError, ValueError):
        return None


def _full(context, index, receipt):
    for locator, doc in context.raw_rows(index, format='json', members=['*.json']):
        cik_value = doc.get('cik')
        if cik_value in (None, '') or not str(cik_value).isdigit():
            continue  # a handful of members are placeholders without facts
        cik = str(int(cik_value)).zfill(10)
        subject = 'sec:cik:' + cik
        facts = doc.get('facts') or {}
        emitted_entity = False
        ids = set()
        for taxonomy, concepts in CONCEPTS.items():
            for concept, metric in concepts.items():
                body = (facts.get(taxonomy) or {}).get(concept)
                if not body:
                    continue
                for unit, rows in (body.get('units') or {}).items():
                    order = sorted(range(len(rows)), key=lambda i: (rows[i].get('filed') or '', rows[i].get('accn') or '', i))
                    last = {}
                    for i in order:
                        fact = rows[i]
                        if fact.get('form') not in FORMS:
                            continue
                        end, filed, value = _valid_day(fact.get('end')), _valid_day(fact.get('filed')), fact.get('val')
                        start = _valid_day(fact.get('start')) if fact.get('start') else None
                        if not end or not filed or type(value) not in (int, float) or not math.isfinite(value):
                            continue
                        if start and start > end:
                            continue
                        period = (start, end)
                        previous = last.get(period)
                        if previous is not None and previous == value:
                            continue
                        last[period] = value
                        if not emitted_entity:
                            emitted_entity = True
                            yield {'kind': 'entity', 'id': f'secfacts:{cik}:entity', 'entity_id': subject, 'entity_type': 'business',
                                   'label': doc.get('entityName') or subject, 'observed_at': receipt['retrieved_at'],
                                   'evidence': context.raw_evidence(locator, index),
                                   'attributes': {'identity_basis': 'SEC CIK in companyfacts'}}
                        record_id = f'secfacts:{cik}:{taxonomy}:{concept}:{unit}:{start or ""}:{end}:{fact.get("accn")}'
                        if record_id in ids:
                            record_id += f':{i}'
                        ids.add(record_id)
                        dimensions = {'concept': f'{taxonomy}:{concept}', 'period_type': 'duration' if start else 'instant',
                                      'form': fact.get('form')}
                        if fact.get('fp'):
                            dimensions['fiscal_period'] = fact['fp']
                        if fact.get('fy'):
                            dimensions['fiscal_year'] = fact['fy']
                        attributes = {'accession': fact.get('accn'), 'revision': 'first_report' if previous is None else 'restated', 'period_end': end}
                        if start:
                            attributes['period_start'] = start
                        if fact.get('frame'):
                            attributes['frame'] = fact['frame']
                        yield {'kind': 'observation', 'id': record_id, 'subject': subject, 'metric': metric,
                               'value': value, 'unit': UNITS.get(unit, unit), 'valid_from': start or end, 'valid_to': next_day(end),
                               'observed_at': filed, 'dimensions': dimensions, 'attributes': attributes,
                               'evidence': context.raw_evidence(f'{locator}/facts/{taxonomy}/{concept}/units/{unit}/{i}', index)}
                    last.clear()
        ids.clear()


def _sample(context, index, receipt):
    dataset = 'sec_company_assets'
    ref = context.raw_inputs[index]
    acquired = receipt['retrieved_at']
    seen = set()
    record_ids = set()
    for line_number, line in enumerate(context.raw_path(index).read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        out = []

        def base(kind, identity, **fields):
            r = {'kind': kind, 'id': 'strategic:' + digest([dataset, ref, identity]), 'observed_at': acquired,
                 'evidence': context.raw_evidence('line:' + str(line_number), index),
                 'attributes': {'source_dataset': dataset, 'source_row': row, 'coverage': 'sample_only'}, **fields}
            if r['id'] not in record_ids:
                record_ids.add(r['id'])
                out.append(r)
            return r

        key = 'sec:cik:0000320193'
        if key not in seen:
            seen.add(key)
            r = base('entity', ['entity', key], entity_id=key, entity_type='business', label='Apple Inc.')
            r['attributes'].update(cik='0000320193')
        value = float(row['val'])
        if not math.isfinite(value):
            raise ValueError('Nonfinite source measurement')
        r = base('observation', [line_number, key, 'total_assets', row['end']], subject=key, metric='total_assets',
                 value=int(value) if value.is_integer() else value, unit='USD', dimensions={'subject': key})
        r['valid_from'] = row['end']
        r['valid_to'] = next_day(row['end'])
        r['attributes'].update(accession=row['accn'], filing_date=row.get('filed'), form=row.get('form'), taxonomy='us-gaap',
                               concept='Assets', revision_policy='all_filings_preserved')
        yield from out
