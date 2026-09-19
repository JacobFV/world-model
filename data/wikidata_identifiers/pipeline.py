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

MIN_SHARE_OF_DECLARED_COUNT = 0.98

_QID = re.compile(r'Q[1-9][0-9]*')
_ITEM_IRI = re.compile(r'https?://www\.wikidata\.org/entity/(Q[1-9][0-9]*)')
_HEADER = ('item', 'label', 'value', 'types')

# Wikidata ``P31 instance of`` classes mapped to this catalog's entity types. The map is read from
# the classes the acquired extract actually uses, most specific first; an item whose classes are
# all unmapped is typed ``entity``, which is what it is - something Wikidata identifies and this
# ontology does not have a name for. Typing matters beyond documentation in one place: the
# resolution layer reads an ``imo`` claim on a subject that is not a vessel as an IMO *company*
# number, so the vessel classes have to be complete enough that a ship is typed as one.
ENTITY_TYPE_BY_CLASS = {
    # -- vessels (IMO ship numbers) ------------------------------------------------------------
    'Q11446': 'vessel', 'Q2055880': 'vessel', 'Q17210': 'vessel', 'Q328464': 'vessel',
    'Q14970': 'vessel', 'Q106678': 'vessel', 'Q25653': 'vessel', 'Q1229765': 'vessel',
    'Q2072352': 'vessel', 'Q178193': 'vessel', 'Q1201871': 'vessel', 'Q190334': 'vessel',
    'Q11997': 'vessel', 'Q205198': 'vessel', 'Q193468': 'vessel', 'Q1207505': 'vessel',
    'Q1445518': 'vessel', 'Q2093545': 'vessel', 'Q3050756': 'vessel', 'Q216916': 'vessel',
    'Q1407378': 'vessel', 'Q170013': 'vessel', 'Q220869': 'vessel', 'Q18704604': 'vessel',
    # -- transport facilities -------------------------------------------------------------------
    'Q1248784': 'airport', 'Q644371': 'airport', 'Q62447': 'airport', 'Q46124': 'airport',
    'Q44782': 'port', 'Q283202': 'port', 'Q1069940': 'port',
    # -- people ---------------------------------------------------------------------------------
    'Q5': 'person',
    # -- companies and other organisations ------------------------------------------------------
    'Q4830453': 'business', 'Q783794': 'business', 'Q891723': 'business', 'Q6881511': 'business',
    'Q270791': 'business', 'Q161726': 'business', 'Q210167': 'business', 'Q18388277': 'business',
    'Q1589009': 'business', 'Q219577': 'business', 'Q2624520': 'business',
    'Q327333': 'government_agency', 'Q2659904': 'government_agency', 'Q1530022': 'government_agency',
    'Q7188': 'government_agency', 'Q12047392': 'government_agency',
    'Q3918': 'institution', 'Q875538': 'institution', 'Q31855': 'institution', 'Q902104': 'institution',
    'Q2385804': 'institution', 'Q38723': 'institution', 'Q4671277': 'institution', 'Q189004': 'institution',
    'Q16917': 'institution', 'Q1244442': 'institution', 'Q9826': 'institution', 'Q3914': 'institution',
    'Q163740': 'organization', 'Q157031': 'organization', 'Q43229': 'organization',
    'Q15911314': 'organization', 'Q48204': 'organization', 'Q17127659': 'organization',
    'Q79913': 'organization', 'Q1156831': 'organization', 'Q1664720': 'organization',
    'Q7075': 'organization',
    'Q22687': 'business', 'Q806718': 'business', 'Q2085381': 'business',
    'Q11032': 'organization', 'Q1002697': 'organization', 'Q15265344': 'organization',
    'Q1058914': 'business', 'Q167037': 'business', 'Q4830454': 'business',
    'Q18811583': 'investment_fund', 'Q2114521': 'investment_fund', 'Q1752459': 'investment_fund',
    'Q1149652': 'investment_fund',
    # -- jurisdictions and places ---------------------------------------------------------------
    'Q6256': 'country', 'Q3624078': 'country', 'Q7275': 'jurisdiction', 'Q1520223': 'country',
    'Q35657': 'state', 'Q107390': 'state', 'Q10864048': 'jurisdiction', 'Q13220204': 'jurisdiction',
    'Q56061': 'jurisdiction', 'Q15916867': 'jurisdiction', 'Q1799794': 'jurisdiction',
    'Q47168': 'county', 'Q28575': 'county', 'Q13410428': 'county', 'Q13360155': 'county',
    'Q515': 'location', 'Q486972': 'location', 'Q3957': 'location', 'Q532': 'location',
    'Q1549591': 'location', 'Q62049': 'location', 'Q755707': 'location', 'Q15284': 'location',
    'Q2074737': 'location', 'Q82794': 'location', 'Q618123': 'location',
    # -- facilities ------------------------------------------------------------------------------
    'Q159719': 'facility', 'Q11891': 'facility', 'Q134447': 'facility', 'Q12772819': 'facility',
    'Q1497649': 'facility', 'Q41176': 'facility', 'Q811979': 'facility', 'Q33506': 'facility',
    'Q43501': 'facility', 'Q207694': 'facility', 'Q2143825': 'facility',
    # -- securities and markets -------------------------------------------------------------------
    'Q11691': 'organization', 'Q1155472': 'organization',
    # -- refused: a Wikimedia page is not a thing in the world -------------------------------------
    'Q4167410': None, 'Q4167836': None, 'Q13406463': None, 'Q11266439': None, 'Q17362920': None,
    'Q15407973': None, 'Q14204246': None, 'Q11753321': None,
}


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


# Wikidata gives an item its P31 classes in no particular order, and one item is routinely an
# instance of several. This is the order the catalog type is chosen in: the most specific type an
# item's own classes support wins, so a "public company" that is also an "organization" is a
# business, and a container ship that is also a "ship" is a vessel.
TYPE_PRECEDENCE = ('vessel', 'airport', 'port', 'person', 'county', 'state', 'country',
                   'jurisdiction', 'government_agency', 'institution', 'investment_fund',
                   'business', 'organization', 'facility', 'location')


def entity_type(classes):
    """The catalog entity type of an item, from its own P31 classes, or ``None`` to refuse it."""
    mapped = [ENTITY_TYPE_BY_CLASS[c] for c in classes if c in ENTITY_TYPE_BY_CLASS]
    if mapped and all(value is None for value in mapped):
        return None  # every class Wikidata gives it is a Wikimedia page type
    named = {value for value in mapped if value is not None}
    for candidate in TYPE_PRECEDENCE:
        if candidate in named:
            return candidate
    return 'entity'


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
