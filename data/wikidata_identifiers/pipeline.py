"""Wikidata items and the external identifiers they carry.

One ``entity`` record per item, keyed on the QID, and one ``external_identifier`` assertion per
statement, carrying the Wikidata property that published it. Nothing is inferred from a label: the
label is a name and the entity type comes from the item's own ``P31`` classes.

The statements are deliberately **not** published as ``identifier_assignment`` rows. Wikidata is a
wiki; its identifier statements are community assertions, not a register's own assignment. The
resolution layer reads them through :mod:`worldmodel.resolution.bridges`, which holds every family
to the cardinality its mapping specification declares, refuses and counts the values that break it,
and can be switched off wholesale with ``unify-resolve --no-bridges``.

Completeness is enforced here, not assumed. The query service answers a query that exceeds its
server-side time limit with HTTP 200 and a **truncated** body, so a short page is indistinguishable
from a finished property unless the pages are checked as a sequence: within one property, pages
arrive in offset order, every page before the last carries exactly its declared row count, and
every page after a short one is empty. The acquired row total per property is additionally held to
the count the declaration measured against the service.
"""
import re

from .entity_types import entity_type

MIN_SHARE_OF_DECLARED_COUNT = 0.98

_QID = re.compile(r'Q[1-9][0-9]*')
_ITEM_IRI = re.compile(r'https?://www\.wikidata\.org/entity/(Q[1-9][0-9]*)')
_HEADER = ('item', 'label', 'value', 'types')


def _properties(context):
    declared = context.parameters.get('properties')
    if not declared:
        raise ValueError('wikidata_identifiers requires parameters.properties')
    return {row['property']: row for row in declared}


def _shard_requests(context, index):
    """{shard index: the (property, offset, limit) combination that produced it}."""
    requests = {}
    for shard in context.raw_shards(index):
        params = ((shard.get('request') or {}).get('params')) or {}
        for key in ('property', 'offset', 'limit'):
            if key not in params:
                raise ValueError(f'Shard {shard["index"]} has no {key} in its recorded request')
        requests[shard['index']] = {'property': str(params['property']), 'offset': int(params['offset']),
                                    'limit': int(params['limit'])}
    if not requests:
        raise ValueError('No acquisition shards to read')
    return requests


def _qid(iri):
    match = _ITEM_IRI.fullmatch((iri or '').strip())
    return match.group(1) if match else None


def check_pages(pages):
    """Refuse a page sequence that the service truncated.

    ``pages`` is ``[(offset, limit, rows), ...]`` for one property, in acquisition order. Within a
    property the pages partition a totally ordered result, so every page up to the last non-empty
    one carries exactly ``limit`` rows; a short page followed by a non-empty page means the short
    one was cut off mid-stream, which the service does with HTTP 200 and no error.
    """
    offsets = [page[0] for page in pages]
    if offsets != sorted(offsets):
        raise ValueError('Acquisition pages are out of offset order')
    ended = False
    for offset, limit, rows in pages:
        if ended and rows:
            raise ValueError(f'Page at offset {offset} has {rows} rows after a short page: the '
                             'query service truncated a response')
        if rows > limit:
            raise ValueError(f'Page at offset {offset} returned {rows} rows over its LIMIT {limit}')
        if rows < limit:
            ended = True
    return sum(page[2] for page in pages)


def run(context):
    if not context.raw_inputs:
        raise ValueError('Full acquisition artifact required')
    properties = _properties(context)
    seen_items = set()
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            raise ValueError('wikidata_identifiers reads full acquisition shards only')
        observed = receipt['retrieved_at']
        requests = _shard_requests(context, index)
        pages, statements = {}, {}
        counters = {'rows': 0}
        for locator, row in context.raw_rows(index, format='csv'):
            shard = int(locator.split('/', 1)[0].split(':', 1)[1])
            request = requests[shard]
            if set(row) != set(_HEADER):
                raise ValueError(f'{locator}: expected the columns {_HEADER}, got {sorted(row)}; the '
                                 'query service answered in another format')
            key = (request['property'], request['offset'], request['limit'])
            pages[key] = pages.get(key, 0) + 1
            counters['rows'] += 1
            declared = properties.get(request['property'])
            if declared is None:
                raise ValueError(f'{locator}: property {request["property"]} is not declared')
            item = _qid(row['item'])
            if item is None:
                raise ValueError(f'{locator}: {row["item"]!r} is not a Wikidata item IRI')
            classes = [c for c in (row['types'] or '').split('|') if _QID.fullmatch(c)]
            kind = entity_type(classes)
            if kind is None:
                continue  # a Wikimedia disambiguation or list page, not a thing in the world
            entity = 'wikidata:' + item
            base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
            if entity not in seen_items:
                seen_items.add(entity)
                yield {**base, 'kind': 'entity', 'id': f'wikidata_identifiers:{item}:entity',
                       'entity_id': entity, 'entity_type': kind,
                       'label': (row['label'] or '').strip() or item,
                       'attributes': {'wikidata_classes': classes,
                                      'identity_basis': 'Wikidata item; the QID is the published identifier',
                                      'label_language': 'en,mul'}}
            value = (row['value'] or '').strip()
            if not value:
                continue
            ordinal = statements[(item, request['property'])] = statements.get((item, request['property']), 0) + 1
            yield {**base, 'kind': 'assertion',
                   'id': f'wikidata_identifiers:{item}:{request["property"]}:{ordinal}',
                   'subject': entity, 'predicate': 'external_identifier',
                   'value': {'property': request['property'],
                             'property_label': declared['property_label'], 'value': value},
                   'attributes': {'rank': 'truthy',
                                  'basis': 'Wikidata statement at truthy rank (deprecated ranks excluded)',
                                  'validity_basis': 'Wikidata does not date an external-identifier statement'}}
        _check_completeness(properties, requests, pages)


def _check_completeness(properties, requests, pages):
    by_property = {}
    for request in requests.values():
        key = (request['property'], request['offset'], request['limit'])
        by_property.setdefault(request['property'], []).append(
            (request['offset'], request['limit'], pages.get(key, 0)))
    for prop, rows in sorted(by_property.items()):
        acquired = check_pages(sorted(rows))
        declared = properties[prop]['statements_counted_2026_09_19']
        if acquired < declared * MIN_SHARE_OF_DECLARED_COUNT:
            raise ValueError(
                f'{prop} acquired {acquired} statements against the {declared} counted at declaration '
                f'time; that is below {MIN_SHARE_OF_DECLARED_COUNT:.0%} and reads as an incomplete '
                'acquisition, not as Wikidata shrinking')
