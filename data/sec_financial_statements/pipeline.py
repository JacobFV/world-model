"""SEC Financial Statement Data Sets (quarterly sub/num/tag/pre ZIPs) -> as-filed statement values.

Each shard is one quarterly ZIP. ``sub.txt`` selects periodic reports (10-K/10-Q/20-F/40-F and
transition/amended forms); ``pre.txt`` supplies as-filed presentation (statement BS/IS/CF/EQ/CI,
report and line number, the filer's label, negation); ``num.txt`` values are normalized only
for the standard-taxonomy tags in ``TAGS`` (the same economic concepts as sec_company_assets'
companyfacts, so metric names match). Custom (company-extension) tags are skipped.

Unlike companyfacts, every value is kept as filed, including prior-period comparatives,
coregistrant values and dimensional (``segments``) values when the data set publishes them.
Knowledge time ``observed_at`` = the filing date. FSDS publishes the period end ``ddate`` and
its length in whole quarters ``qtrs``; the start date is derived as (end + 1 day) - qtrs*3 months
and flagged ``period_start_derived``. Values are in the reported ``uom``.
"""
import csv
import io
import re
import zipfile
from datetime import date, timedelta

TAGS = {
    'Assets': 'total_assets', 'AssetsCurrent': 'current_assets', 'Liabilities': 'total_liabilities',
    'LiabilitiesCurrent': 'current_liabilities', 'LiabilitiesAndStockholdersEquity': 'liabilities_and_equity',
    'StockholdersEquity': 'stockholders_equity',
    'StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest': 'total_equity_including_nci',
    'CashAndCashEquivalentsAtCarryingValue': 'cash_and_equivalents',
    'CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents': 'cash_restricted_cash_and_equivalents',
    'AccountsReceivableNetCurrent': 'accounts_receivable', 'InventoryNet': 'inventory', 'PropertyPlantAndEquipmentNet': 'ppe_net',
    'Goodwill': 'goodwill', 'IntangibleAssetsNetExcludingGoodwill': 'intangible_assets_net', 'AccountsPayableCurrent': 'accounts_payable',
    'LongTermDebt': 'long_term_debt', 'LongTermDebtNoncurrent': 'long_term_debt_noncurrent', 'LongTermDebtCurrent': 'long_term_debt_current',
    'ShortTermBorrowings': 'short_term_borrowings', 'RetainedEarningsAccumulatedDeficit': 'retained_earnings', 'Deposits': 'deposits',
    'LoansAndLeasesReceivableNetReportedAmount': 'net_loans',
    'Revenues': 'revenue', 'RevenueFromContractWithCustomerExcludingAssessedTax': 'revenue', 'SalesRevenueNet': 'revenue',
    'CostOfRevenue': 'cost_of_revenue', 'CostOfGoodsAndServicesSold': 'cost_of_revenue', 'GrossProfit': 'gross_profit',
    'OperatingExpenses': 'operating_expenses', 'CostsAndExpenses': 'costs_and_expenses',
    'ResearchAndDevelopmentExpense': 'research_and_development_expense', 'SellingGeneralAndAdministrativeExpense': 'sga_expense',
    'OperatingIncomeLoss': 'operating_income', 'InterestExpense': 'interest_expense', 'IncomeTaxExpenseBenefit': 'income_tax_expense',
    'IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest': 'pretax_income',
    'NetIncomeLoss': 'net_income', 'ProfitLoss': 'net_income_including_nci',
    'EarningsPerShareBasic': 'eps_basic', 'EarningsPerShareDiluted': 'eps_diluted',
    'WeightedAverageNumberOfSharesOutstandingBasic': 'weighted_average_shares_basic',
    'WeightedAverageNumberOfDilutedSharesOutstanding': 'weighted_average_shares_diluted',
    'DepreciationDepletionAndAmortization': 'depreciation_amortization', 'ShareBasedCompensation': 'share_based_compensation',
    'NetCashProvidedByUsedInOperatingActivities': 'operating_cash_flow', 'NetCashProvidedByUsedInInvestingActivities': 'investing_cash_flow',
    'NetCashProvidedByUsedInFinancingActivities': 'financing_cash_flow',
    'PaymentsToAcquirePropertyPlantAndEquipment': 'capital_expenditures', 'PaymentsOfDividends': 'dividends_paid',
    'PaymentsOfDividendsCommonStock': 'dividends_paid_common', 'PaymentsForRepurchaseOfCommonStock': 'share_repurchases',
    'ProceedsFromIssuanceOfLongTermDebt': 'debt_issuance_proceeds', 'RepaymentsOfLongTermDebt': 'debt_repayments',
    'CommonStockSharesOutstanding': 'shares_outstanding', 'CommonStockDividendsPerShareDeclared': 'dividends_declared_per_share',
    'EntityCommonStockSharesOutstanding': 'shares_outstanding_cover', 'EntityPublicFloat': 'public_float',
    'Revenue': 'revenue', 'ProfitLossAttributableToOwnersOfParent': 'net_income', 'Equity': 'total_equity_including_nci',
    'CashAndCashEquivalents': 'cash_and_equivalents', 'BasicEarningsLossPerShare': 'eps_basic', 'DilutedEarningsLossPerShare': 'eps_diluted',
    'CashFlowsFromUsedInOperatingActivities': 'operating_cash_flow',
}
STANDARD = re.compile(r'(us-gaap|ifrs|dei)/\d{4}')
FORMS = {'10-K', '10-K/A', '10-Q', '10-Q/A', '10-KT', '10-KT/A', '10-QT', '10-QT/A', '20-F', '20-F/A', '40-F', '40-F/A'}
UNITS = {'USD/shares': 'USD/share', 'pure': 'ratio'}


def _ymd(text):
    text = (text or '').strip()
    if not re.fullmatch(r'\d{8}', text):
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:])).isoformat()
    except ValueError:
        return None


def _start(end, quarters):
    """Start of a duration ending on ``end`` spanning ``quarters`` quarters: (end + 1 day) - 3*quarters months."""
    d = date.fromisoformat(end) + timedelta(days=1)
    month = d.month - 3 * quarters
    year = d.year + (month - 1) // 12
    month = (month - 1) % 12 + 1
    leap = year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)
    day = min(d.day, [31, 29 if leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day).isoformat()


def _rows(archive, member, prefix):
    csv.field_size_limit(64 * 1024 * 1024)
    with archive.open(member) as stream:
        text = io.TextIOWrapper(stream, encoding='utf-8', errors='replace', newline='')
        reader = csv.reader(text, delimiter='\t', quoting=csv.QUOTE_NONE)
        header = next(reader)
        last = 1
        for values in reader:
            start, last = last + 1, reader.line_num
            if not values or values == ['']:
                continue
            values = (values + [''] * len(header))[:len(header)]
            yield f'{prefix}/member:{member}/line:{start}', dict(zip(header, values))


def _member(names, wanted):
    return next((n for n in names if n.rsplit('/', 1)[-1].lower() == wanted), None)


def run(context):
    if not context.raw_inputs:
        raise ValueError('Source artifact required')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            raise ValueError('sec_financial_statements requires a full acquisition artifact (FSDS quarterly ZIPs)')
        for shard in context.raw_shards(index):
            with zipfile.ZipFile(shard['path']) as archive:
                names = archive.namelist()
                if not (_member(names, 'sub.txt') and _member(names, 'num.txt')):
                    raise ValueError(f'shard:{shard["index"]}: not a Financial Statement Data Set ZIP')
                yield from _quarter(context, index, archive, names, f'shard:{shard["index"]}')


def _quarter(context, index, archive, names, prefix):
    subs = {}
    issuers = set()
    for locator, row in _rows(archive, _member(names, 'sub.txt'), prefix):
        form = (row.get('form') or '').strip()
        filed = _ymd(row.get('filed'))
        cik = (row.get('cik') or '').strip()
        if form not in FORMS or not filed or not cik.isdigit() or int(cik) == 0:
            continue
        subject = 'sec:cik:' + cik.zfill(10)
        subs[row['adsh']] = (subject, form, filed, row.get('fy') or None, row.get('fp') or None, row.get('prevrpt') == '1')
        if subject not in issuers:
            issuers.add(subject)
            attributes = {k: v for k, v in {'sic': row.get('sic') or None, 'country_business': row.get('countryba') or None,
                          'state_business': row.get('stprba') or None, 'country_incorporation': row.get('countryinc') or None,
                          'state_incorporation': row.get('stprinc') or None, 'fiscal_year_end': row.get('fye') or None,
                          'filer_status': row.get('afs') or None, 'identity_basis': 'SEC CIK in Financial Statement Data Sets'}.items() if v}
            yield {'kind': 'entity', 'id': f'fsds:{prefix}:issuer:{subject}', 'entity_id': subject, 'entity_type': 'business',
                   'label': (row.get('name') or subject).strip(), 'observed_at': filed, 'evidence': context.raw_evidence(locator, index),
                   'attributes': attributes}
    issuers.clear()
    presentation = {}
    pre = _member(names, 'pre.txt')
    if pre:
        for _, row in _rows(archive, pre, prefix):
            if row.get('adsh') not in subs or row.get('tag') not in TAGS:
                continue
            key = (row['adsh'], row['tag'], row.get('version'))
            entry = presentation.get(key)
            placement = f"{row.get('stmt') or '?'}:{row.get('report') or ''}:{row.get('line') or ''}"
            if entry is None:
                presentation[key] = [row.get('stmt') or None, (row.get('plabel') or '')[:200], [placement], row.get('negating') == '1']
            elif len(entry[2]) < 5:
                entry[2].append(placement)
    for locator, row in _rows(archive, _member(names, 'num.txt'), prefix):
        adsh, tag, version = row.get('adsh'), row.get('tag'), row.get('version') or ''
        if tag not in TAGS or adsh not in subs or not STANDARD.match(version):
            continue
        end = _ymd(row.get('ddate'))
        text = (row.get('value') or '').strip()
        qtrs = (row.get('qtrs') or '').strip()
        if not end or not text or not qtrs.isdigit():
            continue
        try:
            value = float(text)
        except ValueError:
            continue
        value = int(value) if value.is_integer() else value
        quarters = int(qtrs)
        subject, form, filed, fy, fp, prevrpt = subs[adsh]
        uom = (row.get('uom') or '').strip()
        segments = (row.get('segments') or '').strip()
        coreg = (row.get('coreg') or '').strip()
        dims = {'concept': f"{version.split('/')[0]}:{tag}", 'form': form, 'period_type': 'duration' if quarters else 'instant', 'qtrs': quarters}
        if fp:
            dims['fiscal_period'] = fp
        if fy:
            dims['fiscal_year'] = int(fy) if fy.isdigit() else fy
        if segments:
            dims['segments'] = segments[:500]
        if coreg:
            dims['coregistrant'] = coreg[:200]
        attributes = {'accession': adsh, 'taxonomy_version': version, 'period_end': end}
        valid_from = end
        if quarters:
            valid_from = _start(end, quarters)
            attributes.update(period_start=valid_from, period_start_derived=True)
        placed = presentation.get((adsh, tag, version))
        if placed:
            dims['statement'] = placed[0]
            attributes.update(as_filed_label=placed[1], placements=placed[2])
            if placed[3]:
                attributes['negating_label'] = True
        if prevrpt:
            attributes['amended_by_later_filing'] = True
        # num.txt occasionally repeats a (adsh, tag, version, ddate, qtrs, uom, segments, coreg) key,
        # so the physical line keeps record IDs unique; the semantic key stays in dimensions.
        line = locator.rsplit(':', 1)[1]
        yield {'kind': 'observation', 'id': f'fsds:{adsh}:{tag}:{end}:{quarters}:{uom}:L{line}', 'subject': subject,
               'metric': TAGS[tag], 'value': value, 'unit': UNITS.get(uom, uom or 'unknown'),
               'valid_from': valid_from, 'valid_to': (date.fromisoformat(end) + timedelta(days=1)).isoformat(),
               'observed_at': filed, 'dimensions': dims, 'attributes': attributes, 'evidence': context.raw_evidence(locator, index)}
    presentation.clear()
