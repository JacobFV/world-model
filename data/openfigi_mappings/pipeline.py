"""OpenFIGI mapping answers, published as the statements OpenFIGI made.

One ``openfigi_mapping`` assertion per row OpenFIGI returned, keyed on the FIGI it returned and
carrying the query it answers, and one ``entity`` per FIGI at the levels this catalog can meet: the
US composite (``exchCode`` ``US``), an instrument OpenFIGI publishes no composite for (a bond, a
muni, a preferred), and a listing OpenFIGI itself scoped to an ISO 10383 MIC because the request
carried a ``micCode``. A venue-level FIGI on some other exchange - a US equity's Frankfurt,
Mexican or Swiss line - is published as a statement and is deliberately given no entity record:
nothing else in this catalog names it, and a quarter of a million unreachable securities would be
a worse graph, not a bigger one.

The statements are **not** published as ``identifier_assignment`` rows. OpenFIGI is the FIGI
registration authority and the FIGI in an answer is its own assignment, but the CUSIP or ticker in
the answer is the *query*: it is the identifier this catalog asked about, and turning "OpenFIGI
answered this CUSIP with this FIGI" into "this CUSIP is this security" is exactly the judgement
that belongs in the resolution layer, where a cardinality break is refused and counted
(:mod:`worldmodel.resolution.openfigi`).

Completeness is checked, not assumed. The mapping endpoint answers positionally - one result per
job, in request order, with no echo of what was asked - so a response with a different number of
results than its request had jobs is either a truncated body or a shifted answer, and either makes
every CUSIP in the shard wrong. Both fail the build.

The one answer that is *not* a misalignment is the service's own request-level failure: it replies
``HTTP 200`` with the single-element body ``[{"error": "There was an error while processing this
request."}]``, which says it did not process the request at all. Because the status is 200 the
runner cannot retry it, so it is read here as a **failed request**, counted, and nothing is
published for the identifiers it was asked about. Six of the 11,246 requests came back this way on
2026-09-19; re-sending all six by hand answered correctly, so it is transient and a re-acquisition
recovers them.
"""
_FIELDS = ('figi', 'name', 'ticker', 'exchCode', 'compositeFIGI', 'shareClassFIGI', 'securityType',
           'securityType2', 'marketSector', 'securityDescription')
_GROUPS = ('cusip', 'ticker_mic', 'ticker_us')


def _shard_requests(context, index):
    """``{shard index: the combination that produced it}``, checked against this declaration."""
    requests = {}
    for shard in context.raw_shards(index):
        params = ((shard.get('request') or {}).get('params')) or {}
        for key in ('group', 'batch', 'jobs'):
            if key not in params:
                raise ValueError(f'Shard {shard["index"]} has no {key} in its recorded request')
        if params['group'] not in _GROUPS:
            raise ValueError(f'Shard {shard["index"]} names an undeclared request group {params["group"]!r}')
        if not isinstance(params['jobs'], list) or not params['jobs']:
            raise ValueError(f'Shard {shard["index"]} recorded no mapping jobs')
        requests[shard['index']] = params
    if not requests:
        raise ValueError('No acquisition shards to read')
    return requests


def _locator_parts(locator):
    shard, _, record = locator.partition('/')
    return int(shard.split(':', 1)[1]), int(record.split(':', 1)[1])


def level(row):
    """Which level of the FIGI hierarchy a returned row sits at.

    ``us_composite`` is the row OpenFIGI returns at ``exchCode`` ``US``: the composite security for
    the United States, which is the thing a CUSIP names. ``unlisted`` is a row with no composite at
    all - a bond, a muni or a preferred, which OpenFIGI answers with ``NOT LISTED``, ``TRACE`` or no
    exchange code - and is likewise one instrument. ``composite`` and ``venue`` are another
    country's composite and a single exchange's line of the same security.
    """
    figi, composite = row.get('figi'), row.get('compositeFIGI')
    if not composite:
        return 'unlisted'
    if composite != figi:
        return 'venue'
    return 'us_composite' if (row.get('exchCode') or '').strip().upper() == 'US' else 'composite'


def query_of(job):
    """The declared mapping job, as the query the answer answers."""
    query = {'id_type': job['idType'], 'id_value': str(job['idValue'])}
    for source, target in (('micCode', 'mic_code'), ('exchCode', 'exch_code')):
        if job.get(source):
            query[target] = job[source]
    return query


def run(context):
    if not context.raw_inputs:
        raise ValueError('openfigi_mappings requires a full acquisition artifact')
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' not in (receipt.get('source') or {}):
            raise ValueError('openfigi_mappings reads full acquisition shards only')
        observed = receipt['retrieved_at']
        requests = _shard_requests(context, index)
        results, entities, failed_requests = {}, set(), set()
        for locator, result in context.raw_rows(index, format='json'):
            shard, number = _locator_parts(locator)
            request = requests.get(shard)
            if request is None:
                raise ValueError(f'{locator}: no recorded request for this shard')
            results[shard] = results.get(shard, 0) + 1
            if request_level_failure(request, number, result):
                failed_requests.add(shard)
                continue
            if number >= len(request['jobs']):
                raise ValueError(f'{locator}: the answer carries more results than the request had jobs, '
                                 'so the results no longer line up with the identifiers asked about')
            job = request['jobs'][number]
            query = query_of(job)
            rows = result.get('data')
            if rows is None:
                if not (result.get('warning') or result.get('error')):
                    raise ValueError(f'{locator}: neither data nor a warning; the answer is not an '
                                     'OpenFIGI mapping result')
                continue  # no published mapping for this identifier; counted by the reader, not published
            if not isinstance(rows, list):
                raise ValueError(f'{locator}: data is not an array')
            base = {'observed_at': observed, 'evidence': context.raw_evidence(locator, index)}
            prefix = f'openfigi:{request["group"]}:{request["batch"]}:{number}'
            for rank, row in enumerate(rows):
                figi = (row.get('figi') or '').strip().upper()
                if not figi:
                    raise ValueError(f'{locator}: a returned row carries no FIGI')
                published = {key: row.get(key) for key in _FIELDS}
                where = level(row)
                subject = 'figi:' + figi
                if (where in ('us_composite', 'unlisted') or query.get('mic_code')) and subject not in entities:
                    entities.add(subject)
                    yield {**base, 'kind': 'entity', 'id': f'openfigi:{figi}:entity', 'entity_id': subject,
                           'entity_type': 'security',
                           'label': (row.get('name') or row.get('securityDescription') or figi).strip() or figi,
                           'attributes': {'ticker': row.get('ticker'), 'exch_code': row.get('exchCode'),
                                          'mic_code': query.get('mic_code'),
                                          'composite_figi': row.get('compositeFIGI'),
                                          'share_class_figi': row.get('shareClassFIGI'),
                                          'security_type': row.get('securityType'),
                                          'security_type2': row.get('securityType2'),
                                          'market_sector': row.get('marketSector'),
                                          'security_description': row.get('securityDescription'),
                                          'figi_level': where,
                                          'identity_basis': 'FIGI published by OpenFIGI, the FIGI registration '
                                                            'authority; the queried identifier is the question, '
                                                            'not a claim about this security'}}
                yield {**base, 'kind': 'assertion', 'id': f'{prefix}:{rank}', 'subject': subject,
                       'predicate': 'openfigi_mapping',
                       'value': {'query': query, 'figi_level': where, 'match_rank': rank,
                                 'matches_for_query': len(rows), 'published': published},
                       'attributes': {'validity_basis': 'OpenFIGI does not date a mapping; the retrieval date '
                                                        'in the evidence is the only date there is'}}
        _check_complete(requests, results, failed_requests)


def request_level_failure(request, number, result):
    """Whether this answer is the service saying it did not process the request at all.

    OpenFIGI replies to a request-level failure with ``HTTP 200`` and a one-element body whose only
    member is an ``error`` - no ``data`` and no per-job results. It is distinguishable from a
    truncated answer only because the request carried more than one job and exactly one result came
    back, so a single-job request is never read this way.
    """
    return (number == 0 and len(request['jobs']) > 1 and isinstance(result, dict)
            and result.get('error') and 'data' not in result)


def _check_complete(requests, results, failed_requests=()):
    """Every request must be answered with exactly one result per job, in request order.

    The endpoint does not echo the identifier it answered, so a short or long answer silently
    re-aligns every following result with the wrong CUSIP. There is no way to detect that after the
    fact and no safe way to publish it, so it fails the build. A request the service reports it did
    not process is the one exception: it is counted here and nothing is published for it.
    """
    for shard, request in sorted(requests.items()):
        expected, got = len(request['jobs']), results.get(shard, 0)
        if shard in failed_requests:
            if got != 1:
                raise ValueError(f'Shard {shard} carries {got} results alongside a request-level error')
            continue
        if got != expected:
            raise ValueError(f'Shard {shard} ({request["group"]} batch {request["batch"]}) asked {expected} '
                             f'mapping jobs and its answer carries {got} results; a positional answer that is '
                             'not one result per job cannot be matched back to the identifiers asked about')
    return {'failed_requests': sorted(failed_requests),
            'identifiers_not_mapped': sum(len(requests[shard]['jobs']) for shard in failed_requests)}
