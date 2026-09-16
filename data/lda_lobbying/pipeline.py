"""LDA.gov API JSONL (filings and LD-203 contribution reports) -> lobbying relationships, amounts and contributions.

Filings (LD-1 registrations, LD-2 quarterly reports and their amendments/terminations):
* ``lda:filing:{uuid}`` publication entity with type, period, posting time and reported income/expenses
* ``lda:registrant:{id}`` and ``lda:client:{id}`` organizations; ``lobbies_for`` registrant -> client for the period
* ``lobbying_income`` / ``lobbying_expenses`` observations (USD, reporting period); amendments are flagged so
  consumers can keep the latest report per registrant x client x period
* issue areas (general issue code + description), government entities contacted, lobbyists per client
LD-203 reports: ``lda:contribution_report:{uuid}`` plus ``lobbyist_contribution_amount`` observations (FECA,
honorary, meeting and presidential library/inaugural items). Street addresses and phone numbers are not emitted.
"""
from datetime import date, timedelta

PERIODS = {'first_quarter': ('01-01', '04-01', 0), 'second_quarter': ('04-01', '07-01', 0), 'third_quarter': ('07-01', '10-01', 0),
           'fourth_quarter': ('10-01', '01-01', 1), 'mid_year': ('01-01', '07-01', 0), 'year_end': ('07-01', '01-01', 1)}


def clip(value, limit=600):
    if not isinstance(value, str):
        return value
    value = ' '.join(value.split())
    return value if len(value) <= limit else value[:limit - 1] + '…'


def amount(value):
    if value in (None, ''):
        return None
    result = round(float(value), 2)
    return int(result) if result.is_integer() else result


def period_window(year, period):
    if period not in PERIODS or not year:
        return {}
    start, end, carry = PERIODS[period]
    return {'valid_from': f'{year}-{start}', 'valid_to': f'{int(year) + carry}-{end}'}


def person_name(lobbyist):
    parts = [lobbyist.get(k) for k in ('first_name', 'middle_name', 'last_name', 'suffix_display')]
    return ' '.join(p.strip() for p in parts if isinstance(p, str) and p.strip()) or f"LDA lobbyist {lobbyist.get('id')}"


def run(context):
    dataset = context.definition['id']
    entities, filings = set(), set()
    for index, ref in enumerate(context.raw_inputs):
        observed_default = context.raw_receipt(index)['retrieved_at']
        observed_by_shard = {s['index']: s.get('retrieved_at') or observed_default for s in context.raw_shards(index)}
        for locator, row in context.raw_rows(index, format='jsonl'):
            uuid = row.get('filing_uuid')
            if not uuid:
                raise ValueError(f'{locator}: LDA record without filing_uuid')
            report_kind = 'contribution' if '/contributions/' in (row.get('url') or '') or 'contribution_items' in row else 'filing'
            if (report_kind, uuid) in filings:
                continue  # page drift while filings were posted during the crawl
            filings.add((report_kind, uuid))
            shard_index = int(locator.split('/', 1)[0].split(':')[1])
            observed = observed_by_shard.get(shard_index, observed_default)
            evidence = [{'input': ref, 'locator': locator}]

            def rec(record, **attributes):
                record.update(observed_at=observed, evidence=evidence, attributes={'source_dataset': dataset, **attributes})
                return record

            def entity(key, entity_type, label, **attributes):
                if key in entities:
                    return None
                entities.add(key)
                return rec({'kind': 'entity', 'id': f'{dataset}:entity:{key}', 'entity_id': key, 'entity_type': entity_type,
                            'label': label or key}, **attributes)

            converter = contribution_records if report_kind == 'contribution' else filing_records
            for record in converter(dataset, row, rec, entity):
                if record is not None:
                    yield record


def registrant_records(row, entity):
    registrant = row.get('registrant') or {}
    if registrant.get('id') is None:
        return None, []
    key = f"lda:registrant:{registrant['id']}"
    return key, [entity(key, 'organization', registrant.get('name'), house_registrant_id=registrant.get('house_registrant_id'),
                        description=clip(registrant.get('description')), state=registrant.get('state'), country=registrant.get('country'),
                        principal_place_of_business_country=registrant.get('ppb_country'), identity_basis='LDA registrant id')]


def filing_records(dataset, row, rec, entity):
    uuid, year, period, kind = row['filing_uuid'], row.get('filing_year'), row.get('filing_period'), row.get('filing_type') or ''
    window = period_window(year, period)
    registrant, out = registrant_records(row, entity)
    yield from out
    client_row = row.get('client') or {}
    client = f"lda:client:{client_row['id']}" if client_row.get('id') is not None else None
    if client:
        yield entity(client, 'organization', client_row.get('name'), general_description=clip(client_row.get('general_description')),
                     state=client_row.get('state'), country=client_row.get('country'), principal_place_of_business_state=client_row.get('ppb_state'),
                     principal_place_of_business_country=client_row.get('ppb_country'),
                     client_government_entity=client_row.get('client_government_entity'), identity_basis='LDA client id')
    filing = 'lda:filing:' + uuid
    amendment = 'A' in kind or '@' in kind
    income, expenses = amount(row.get('income')), amount(row.get('expenses'))
    yield rec({'kind': 'entity', 'id': f'{dataset}:filing:{uuid}', 'entity_id': filing, 'entity_type': 'publication',
               'label': f"{row.get('filing_type_display') or kind} {year} {period or ''}".strip()},
              filing_type=kind, filing_type_display=row.get('filing_type_display'), filing_year=year, filing_period=period,
              posted_at=row.get('dt_posted'), termination_date=row.get('termination_date'), income=income, expenses=expenses,
              expenses_method=row.get('expenses_method'), is_amendment=amendment, document_url=row.get('filing_document_url'),
              registrant=registrant, client=client)
    for predicate, target in (('filed_by', registrant), ('filed_on_behalf_of', client)):
        if target:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:{predicate}:{uuid}', 'subject': filing, 'predicate': predicate, 'object': target})
    if registrant and client:
        yield rec({'kind': 'assertion', 'id': f'{dataset}:lobbies_for:{uuid}', 'subject': registrant, 'predicate': 'lobbies_for',
                   'object': client, **window}, filing=filing, filing_type=kind, is_amendment=amendment,
                  validity_basis='LDA reporting period of the filing (registration periods start at the effective quarter)')
    dims = {'client': client, 'filing': filing, 'filing_type': kind, 'filing_year': year, 'filing_period': period, 'is_amendment': amendment}
    if registrant and income is not None:
        yield rec({'kind': 'observation', 'id': f'{dataset}:income:{uuid}', 'subject': registrant, 'metric': 'lobbying_income',
                   'value': income, 'unit': 'USD', 'dimensions': dims, **window},
                  basis='income from the client for lobbying in the period (LD-2 line 12); null values mean under $5,000 or not reported')
    if registrant and expenses is not None:
        yield rec({'kind': 'observation', 'id': f'{dataset}:expenses:{uuid}', 'subject': registrant, 'metric': 'lobbying_expenses',
                   'value': expenses, 'unit': 'USD', 'dimensions': {**dims, 'expenses_method': row.get('expenses_method')}, **window},
                  basis='self-filer lobbying expenses in the period (LD-2 line 13)')
    agencies, lobbyists = {}, {}
    for position, activity in enumerate(row.get('lobbying_activities') or []):
        code = activity.get('general_issue_code')
        yield rec({'kind': 'assertion', 'id': f'{dataset}:issue:{uuid}:{position}', 'subject': filing, 'predicate': 'lobbying_issue',
                   'value': {'general_issue_code': code, 'general_issue': activity.get('general_issue_code_display'),
                             'description': clip(activity.get('description')),
                             'foreign_entity_issues': clip(activity.get('foreign_entity_issues'))}, **window})
        for government in activity.get('government_entities') or []:
            if government.get('id') is not None:
                agencies.setdefault(government['id'], {'name': government.get('name'), 'codes': set()})['codes'].add(code)
        for item in activity.get('lobbyists') or []:
            lobbyist = item.get('lobbyist') or {}
            if lobbyist.get('id') is None:
                continue
            entry = lobbyists.setdefault(lobbyist['id'], {'row': lobbyist, 'codes': set(), 'covered_positions': set(), 'new': False})
            entry['codes'].add(code)
            if item.get('covered_position'):
                entry['covered_positions'].add(clip(item['covered_position'], 300))
            entry['new'] = entry['new'] or bool(item.get('new'))
    for ident, info in sorted(agencies.items()):
        key = f'lda:government_entity:{ident}'
        yield entity(key, 'government_agency', info['name'], identity_basis='LDA government entity id')
        yield rec({'kind': 'assertion', 'id': f'{dataset}:contacted:{uuid}:{ident}', 'subject': filing, 'predicate': 'contacted_government_entity',
                   'object': key, **window}, issue_codes=sorted(c for c in info['codes'] if c))
    for ident, info in sorted(lobbyists.items()):
        key = f'lda:lobbyist:{ident}'
        yield entity(key, 'person', person_name(info['row']), identity_basis='LDA lobbyist id (registrant-scoped listing)')
        if client:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:lobbyist_client:{uuid}:{ident}', 'subject': key, 'predicate': 'lobbied_for_client',
                       'object': client, **window}, filing=filing, registrant=registrant, issue_codes=sorted(c for c in info['codes'] if c),
                      covered_positions=sorted(info['covered_positions']) or None, newly_listed=info['new'])
    for position, foreign in enumerate(row.get('foreign_entities') or []):
        yield rec({'kind': 'assertion', 'id': f'{dataset}:foreign_entity:{uuid}:{position}', 'subject': filing, 'predicate': 'foreign_entity_interest',
                   'value': {'name': foreign.get('name'), 'country': foreign.get('country'), 'ppb_country': foreign.get('ppb_country'),
                             'contribution': amount(foreign.get('contribution')), 'ownership_percentage': foreign.get('ownership_percentage')}})
    for position, affiliate in enumerate(row.get('affiliated_organizations') or []):
        yield rec({'kind': 'assertion', 'id': f'{dataset}:affiliate:{uuid}:{position}', 'subject': filing, 'predicate': 'affiliated_organization',
                   'value': {'name': affiliate.get('name'), 'country': affiliate.get('country'), 'ppb_country': affiliate.get('ppb_country')}})


def contribution_records(dataset, row, rec, entity):
    uuid, year, period = row['filing_uuid'], row.get('filing_year'), row.get('filing_period')
    window = period_window(year, period)
    registrant, out = registrant_records(row, entity)
    yield from out
    lobbyist_row = row.get('lobbyist') or {}
    lobbyist = f"lda:lobbyist:{lobbyist_row['id']}" if lobbyist_row.get('id') is not None else None
    if lobbyist:
        yield entity(lobbyist, 'person', person_name(lobbyist_row), identity_basis='LDA lobbyist id (registrant-scoped listing)')
    report = 'lda:contribution_report:' + uuid
    yield rec({'kind': 'entity', 'id': f'{dataset}:contribution_report:{uuid}', 'entity_id': report, 'entity_type': 'publication',
               'label': f"LD-203 {row.get('filing_type_display') or row.get('filing_type')} {year}"},
              filing_type=row.get('filing_type'), filing_year=year, filing_period=period, filer_type=row.get('filer_type'),
              posted_at=row.get('dt_posted'), no_contributions=row.get('no_contributions'), pacs=row.get('pacs') or None,
              document_url=row.get('filing_document_url'), registrant=registrant, lobbyist=lobbyist)
    for predicate, target in (('filed_by', registrant), ('filed_by_lobbyist', lobbyist)):
        if target:
            yield rec({'kind': 'assertion', 'id': f'{dataset}:{predicate}:{uuid}', 'subject': report, 'predicate': predicate, 'object': target})
    filer = lobbyist if row.get('filer_type') == 'lobbyist' and lobbyist else registrant
    if not filer:
        return
    for position, item in enumerate(row.get('contribution_items') or []):
        value = amount(item.get('amount'))
        day = item.get('date')
        try:
            valid = {'valid_from': day, 'valid_to': (date.fromisoformat(day) + timedelta(days=1)).isoformat()} if day else window
        except ValueError:
            valid = window
        record = {'kind': 'observation', 'id': f'{dataset}:contribution:{uuid}:{position}', 'subject': filer,
                  'metric': 'lobbyist_contribution_amount', 'value': value, 'unit': 'USD', **valid,
                  'dimensions': {'contribution_type': item.get('contribution_type'), 'contributor_name': clip(item.get('contributor_name'), 200),
                                 'payee_name': clip(item.get('payee_name'), 200), 'honoree_name': clip(item.get('honoree_name'), 200),
                                 'report': report, 'registrant': registrant}}
        if value is None:
            record['missing_reason'] = 'source_missing'
        yield rec(record, contribution_type_display=item.get('contribution_type_display'),
                  basis='LD-203 itemized contribution; payee and honoree are published names, not resolved to FEC IDs')
