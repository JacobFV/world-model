"""Small evidence ontology. Valid periods are half-open: [from, to)."""
from datetime import datetime, timezone
from .util import canonical, hash_id, slug

from .ontology import ENTITY_TYPES


def instant(value):
    if not isinstance(value, str):
        raise ValueError('time must be an ISO date or timezone-aware timestamp')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if len(value) == 10:
            result = result.replace(tzinfo=timezone.utc)
        if result.tzinfo is None:
            raise ValueError('timestamp needs timezone')
        return result.astimezone(timezone.utc)
    except ValueError as error:
        raise ValueError(f'Invalid time: {value}') from error


def identifier(value):
    if not isinstance(value, str) or ':' not in value or not all(value.split(':', 1)):
        raise ValueError(f'Expected namespaced identifier: {value!r}')


MATCH_STATUSES = ('unreviewed', 'needs_review', 'accepted', 'rejected', 'source_asserted')
PRICE_BASES = ('nominal', 'real', 'index', 'unspecified')


def _validate_optional(record):
    """Optional fields added for units, reconciliation and entity resolution (all backward compatible)."""
    if 'conversion' in record:
        from .units import validate_conversion
        if not isinstance(record.get('unit'), str):
            raise ValueError('conversion requires a record unit')
        validate_conversion(record['conversion'], record['unit'])
    if 'price_basis' in record:
        if record['price_basis'] not in PRICE_BASES:
            raise ValueError('price_basis must be one of ' + ', '.join(PRICE_BASES))
        if record['price_basis'] == 'real' and not (isinstance(record.get('base_period'), str) and record['base_period']):
            raise ValueError('Real (constant-price) values require base_period')
    if 'base_period' in record and not (isinstance(record['base_period'], str) and record['base_period']):
        raise ValueError('base_period must be a nonempty string')
    for field in ('retracts', 'supersedes'):
        if field in record:
            if not isinstance(record[field], list) or not record[field]:
                raise ValueError(field + ' must be a nonempty list of record IDs')
            for value in record[field]:
                identifier(value)
    if record.get('vintage') is not None:
        instant(record['vintage'])
    if 'match' in record:
        match = record['match']
        if record.get('kind') != 'assertion' or not isinstance(match, dict):
            raise ValueError('match metadata belongs to assertions and must be an object')
        if not isinstance(match.get('method'), str) or not match['method']:
            raise ValueError('match requires a method')
        if match.get('reviewer_status', 'unreviewed') not in MATCH_STATUSES:
            raise ValueError('Unknown match reviewer_status')
        score = match.get('score')
        if score is not None and (isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 1):
            raise ValueError('match score must be in [0,1]')
        if 'features' in match and not isinstance(match['features'], dict):
            raise ValueError('match features must be an object')


def validate_record(record):
    if not isinstance(record, dict):
        raise ValueError('Record must be an object')
    canonical(record)  # Reject NaN/Infinity and non-JSON values.
    identifier(record.get('id'))
    kind = record.get('kind')
    if kind not in ('entity', 'observation', 'assertion', 'event'):
        raise ValueError(f'Unknown record kind: {kind}')
    evidence = record.get('evidence')
    if not isinstance(evidence, list) or not evidence:
        raise ValueError('Every record requires evidence')
    for item in evidence:
        ref = item.get('input', {})
        slug(ref.get('dataset'))
        if 'stage' in ref:slug(ref['stage'])
        if ('version' in ref) == ('artifact' in ref):
            raise ValueError('evidence input needs exactly one version or artifact')
        hash_id(ref.get('version', ref.get('artifact')))
        if 'version' in ref:
            identifier(item.get('record_id'))
        elif not isinstance(item.get('locator'), str) or not item['locator']:
            raise ValueError('Raw evidence requires a locator')
    if 'observed_at' not in record:
        raise ValueError('observed_at is required')
    instant(record['observed_at'])
    for field in ('valid_from', 'valid_to', 'occurred_at'):
        if record.get(field) is not None:
            instant(record[field])
    if record.get('valid_from') and record.get('valid_to'):
        if instant(record['valid_from']) >= instant(record['valid_to']):
            raise ValueError('Invalid valid time interval')
    confidence = record.get('confidence')
    if confidence is not None and (isinstance(confidence, bool) or not isinstance(confidence, (int, float))
                                   or not 0 <= confidence <= 1):
        raise ValueError('confidence must be in [0,1]')
    _validate_optional(record)
    if kind == 'entity':
        identifier(record.get('entity_id', record['id']))
        if record.get('entity_type') not in ENTITY_TYPES:
            raise ValueError('Unknown entity_type; extend model.ENTITY_TYPES explicitly')
        if not isinstance(record.get('label'), str) or not record['label']:
            raise ValueError('Entity label is required')
    elif kind == 'observation':
        for key in ('metric', 'unit'):
            if not isinstance(record.get(key), str) or not record[key]:
                raise ValueError(f'Observation requires {key}')
        if 'value' not in record or not isinstance(record.get('dimensions'), dict):
            raise ValueError('Observation requires value and dimensions')
        if record['value'] is None and not record.get('missing_reason'):
            raise ValueError('Null observation requires missing_reason (e.g. suppressed)')
    elif kind == 'assertion':
        identifier(record.get('subject'))
        if not isinstance(record.get('predicate'), str) or not record['predicate']:
            raise ValueError('Assertion requires predicate')
        if ('object' in record) == ('value' in record):
            raise ValueError('Assertion requires exactly one entity object or literal value')
        if 'object' in record:
            identifier(record['object'])
    elif kind == 'event':
        if not record.get('event_type') or not record.get('occurred_at'):
            raise ValueError('Event requires event_type and occurred_at')
        if not isinstance(record.get('participants'), list):
            raise ValueError('Event requires participants')
        for participant in record['participants']:
            identifier(participant)
    return record
