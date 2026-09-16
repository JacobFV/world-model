"""OpenFEMA API pages (JSONL records) -> disaster entities, county declaration events and assistance observations.

Record families are recognized by their selected fields (several families can share a JSONL shard):
* DisasterDeclarationsSummaries v2 -> one `disaster_declaration` event per designated area (county FIPS or statewide),
  plus one disaster entity `fema:disaster:<disasterNumber>`;
* FemaWebDisasterSummaries v1 -> cumulative per-disaster IA/IHP/PA/HMGP amounts (valid time = as of retrieval);
* PublicAssistanceFundedProjectsSummaries v1 -> federal obligations and project counts by applicant;
* HousingAssistanceOwners / HousingAssistanceRenters v2 -> IHP registrations, inspections and approved amounts by
  county name and ZIP code.
Amounts are nominal USD as currently reported (OpenFEMA values change as obligations are updated).
"""
from worldmodel.util import digest

READER = {'format': 'jsonl'}


def _date(value):
    return value if isinstance(value, str) and value else None


def _geo(state, county):
    state, county = (state or '').strip(), (county or '').strip()
    if not state:
        return None
    if not county or county == '000':
        return 'geo:US:state:' + state
    return 'geo:US:county:' + state + county


def _family(row):
    if 'femaDeclarationString' in row:
        return 'declaration'
    if 'totalObligatedAmountPa' in row or 'totalAmountIhpApproved' in row:
        return 'web_summary'
    if 'numberOfProjects' in row:
        return 'pa_summary'
    if 'validRegistrations' in row:
        return 'housing_owner' if 'totalDamage' in row else 'housing_renter'
    return None


def run(context):
    if not context.raw_inputs:
        raise ValueError('openfema: raw acquisition required')
    for index, _ in enumerate(context.raw_inputs):
        observed = context.raw_receipt(index)['retrieved_at']
        disasters, places = set(), set()  # ~5k disasters, ~3.5k counties/states
        for locator, row in context.raw_rows(index, **READER):
            family = _family(row)
            if family is None:
                raise ValueError(f'{locator}: unrecognized OpenFEMA record fields')
            evidence = context.raw_evidence(locator, index)
            number = row.get('disasterNumber')
            disaster = f'fema:disaster:{number}'
            if family == 'declaration':
                if number not in disasters:
                    disasters.add(number)
                    yield {'kind': 'entity', 'id': f'openfema:entity:disaster:{number}', 'entity_id': disaster,
                           'entity_type': 'entity', 'label': f'{row.get("femaDeclarationString")} {row.get("declarationTitle") or ""}'.strip(),
                           'observed_at': observed, 'evidence': evidence,
                           'attributes': {'hazard_type': 'declared_disaster', 'declaration_type': row.get('declarationType'),
                                          'incident_type': row.get('incidentType'), 'state': row.get('state'),
                                          'fema_region': row.get('region'), 'incident_begin': _date(row.get('incidentBeginDate')),
                                          'incident_end': _date(row.get('incidentEndDate'))}}
                place = _geo(row.get('fipsStateCode'), row.get('fipsCountyCode'))
                participants = [disaster] + ([place] if place else [])
                if place and place not in places:
                    places.add(place)
                    yield {'kind': 'entity', 'id': 'openfema:entity:' + place, 'entity_id': place,
                           'entity_type': 'county' if ':county:' in place else 'state',
                           'label': row.get('designatedArea') if ':county:' in place else row.get('state') or place,
                           'observed_at': observed, 'evidence': evidence, 'attributes': {}}
                area = f'{row.get("fipsStateCode") or ""}{row.get("fipsCountyCode") or ""}:{row.get("placeCode") or ""}'
                attrs = {k: row.get(k) for k in ('femaDeclarationString', 'declarationType', 'incidentType', 'declarationTitle',
                                                 'ihProgramDeclared', 'iaProgramDeclared', 'paProgramDeclared', 'hmProgramDeclared',
                                                 'incidentBeginDate', 'incidentEndDate', 'disasterCloseoutDate', 'tribalRequest',
                                                 'designatedArea', 'placeCode', 'designatedIncidentTypes', 'declarationRequestDate')}
                yield {'kind': 'event', 'id': f'openfema:declaration:{row.get("femaDeclarationString")}:{area}',
                       'event_type': 'disaster_declaration', 'occurred_at': row['declarationDate'], 'participants': participants,
                       'observed_at': observed, 'evidence': evidence,
                       'attributes': {k: v for k, v in attrs.items() if v not in (None, '')}}
                continue
            if family == 'web_summary':
                values = (('totalNumberIaApproved', 'ia_registrations_approved', 'registrations', {}),
                          ('totalAmountIhpApproved', 'ihp_amount_approved', 'USD', {'program': 'ihp'}),
                          ('totalAmountHaApproved', 'ihp_amount_approved', 'USD', {'program': 'housing_assistance'}),
                          ('totalAmountOnaApproved', 'ihp_amount_approved', 'USD', {'program': 'other_needs_assistance'}),
                          ('totalObligatedAmountPa', 'pa_federal_obligated', 'USD', {'category': 'all'}),
                          ('totalObligatedAmountCatAb', 'pa_federal_obligated', 'USD', {'category': 'A-B_emergency_work'}),
                          ('totalObligatedAmountCatC2g', 'pa_federal_obligated', 'USD', {'category': 'C-G_permanent_work'}),
                          ('totalObligatedAmountHmgp', 'hmgp_federal_obligated', 'USD', {}))
                for field, metric, unit, dims in values:
                    if row.get(field) is None:
                        continue
                    yield {'kind': 'observation', 'id': f'openfema:web:{number}:{field}', 'subject': disaster, 'metric': metric,
                           'value': row[field], 'unit': unit, 'observed_at': observed, 'evidence': evidence,
                           'dimensions': {'disaster_number': number, **dims},
                           'attributes': {'cumulative_as_of_retrieval': True, 'source_field': field}}
                continue
            key = digest([row.get('state'), row.get('county'), row.get('applicantName'), row.get('zipCode'), locator])[:20]
            if family == 'pa_summary':
                dims = {'disaster_number': number, 'state': row.get('state'), 'county': row.get('county'),
                        'applicant': row.get('applicantName'), 'education_applicant': row.get('educationApplicant')}
                start = _date(row.get('declarationDate'))
                for field, metric, unit in (('federalObligatedAmount', 'pa_federal_obligated', 'USD'),
                                            ('numberOfProjects', 'pa_project_count', 'projects')):
                    if row.get(field) is None:
                        continue
                    record = {'kind': 'observation', 'id': f'openfema:pas:{number}:{key}:{field}', 'subject': disaster,
                              'metric': metric, 'value': row[field], 'unit': unit, 'observed_at': observed, 'evidence': evidence,
                              'dimensions': {k: v for k, v in dims.items() if v is not None},
                              'attributes': {'incident_type': row.get('incidentType'), 'cumulative_as_of_retrieval': True}}
                    if start:
                        record['valid_from'] = start
                    yield record
                continue
            tenure = 'owner' if family == 'housing_owner' else 'renter'
            dims = {'disaster_number': number, 'tenure': tenure, 'state': row.get('state'), 'county': row.get('county'),
                    'zip_code': row.get('zipCode')}
            for field, metric, unit in (('validRegistrations', 'ihp_valid_registrations', 'registrations'),
                                        ('totalInspected', 'ihp_inspections', 'inspections'),
                                        ('totalDamage', 'fema_inspected_damage', 'USD'),
                                        ('approvedForFemaAssistance', 'ihp_approved_registrations', 'registrations'),
                                        ('totalApprovedIhpAmount', 'ihp_amount_approved', 'USD'),
                                        ('repairReplaceAmount', 'ihp_repair_replace_amount', 'USD'),
                                        ('rentalAmount', 'ihp_rental_amount', 'USD'),
                                        ('otherNeedsAmount', 'ihp_other_needs_amount', 'USD')):
                if row.get(field) is None:
                    continue
                yield {'kind': 'observation', 'id': f'openfema:ha:{tenure}:{number}:{key}:{field}', 'subject': disaster,
                       'metric': metric, 'value': row[field], 'unit': unit, 'observed_at': observed, 'evidence': evidence,
                       'dimensions': {k: v for k, v in dims.items() if v is not None},
                       'attributes': {'cumulative_as_of_retrieval': True}}
