"""Public agency feed metadata; no article-content or factual-truth inference.

Full acquisitions are paged RSS documents (one shard per page) parsed with a
DTD/entity-rejecting stdlib reader (inline, so the module also loads outside
the dataset package); legacy samples are pre-extracted JSONL items.
"""
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from xml.parsers import expat
from worldmodel.source_records import sampled_rows
from worldmodel.util import digest


MAX_BYTES = 16 * 1024 * 1024
DC = '{http://purl.org/dc/elements/1.1/}creator'


def rss_items(path):
    """Yield (1-based item number, item dict) from one RSS document."""
    with open(path, 'rb') as stream:
        content = stream.read(MAX_BYTES + 1)
    if len(content) > MAX_BYTES:
        raise ValueError('RSS document exceeds 16 MiB cap')
    parser = expat.ParserCreate()

    def reject(*args):
        raise ValueError('RSS DTD/entities are forbidden')
    parser.StartDoctypeDeclHandler = reject
    parser.EntityDeclHandler = reject
    try:
        parser.Parse(content, True)
    except expat.ExpatError as exc:
        raise ValueError('Invalid RSS XML') from exc
    root = ET.fromstring(content)
    for number, item in enumerate(root.findall('./channel/item'), 1):
        yield number, {'title': (item.findtext('title') or '').strip(), 'link': (item.findtext('link') or '').strip(),
                       'guid': (item.findtext('guid') or '').strip() or None, 'published': item.findtext('pubDate'),
                       'author': item.findtext(DC), 'categories': [c.text for c in item.findall('category') if c.text][:20]}


def _rows(context):
    for index in range(len(context.raw_inputs)):
        receipt = context.raw_receipt(index)
        if 'acquisition' in (receipt.get('source') or {}):
            for shard in context.raw_shards(index):
                for number, item in rss_items(shard['path']):
                    yield index, f'shard:{shard["index"]}/item:{number}', item, receipt, True
        else:
            for i, line, row, rc in sampled_rows(context):
                if i == index:
                    yield index, 'line:' + str(line), row, rc, False


def run(context):
    seen = set()
    for index, locator, row, receipt, full in _rows(context):
        if not row.get('link') or not row.get('published'):
            raise ValueError('Publication needs link and timestamp')
        published = parsedate_to_datetime(row['published']).isoformat()
        key = digest(row.get('guid') or row['link'])
        if key in seen:
            continue  # the same post can appear on two pages when the feed advances during paging
        seen.add(key)
        base = {'observed_at': receipt['retrieved_at'], 'evidence': context.raw_evidence(locator, index)}
        attrs = {'source_dataset': 'nasa_publications', 'coverage': 'paged_feed_window' if full else 'bounded_sample'}
        if 'publisher' not in seen:
            seen.add('publisher')
            yield {**base, 'kind': 'entity', 'id': 'nasa_pub:publisher', 'entity_id': 'us:agency:nasa',
                   'entity_type': 'government_agency', 'label': 'NASA', 'attributes': attrs}
        post = 'nasa:publication:' + key
        yield {**base, 'kind': 'entity', 'id': post + ':entity', 'entity_id': post, 'entity_type': 'post',
               'label': row.get('title') or row['link'], 'attributes': {**attrs, 'categories': row.get('categories', [])}}
        yield {**base, 'kind': 'assertion', 'id': post + ':published_by', 'subject': post, 'predicate': 'published_by',
               'object': 'us:agency:nasa', 'attributes': attrs}
        yield {**base, 'kind': 'assertion', 'id': post + ':metadata', 'subject': post, 'predicate': 'publication_metadata',
               'value': {'publication_time': published, 'url': row['link'], 'reported_author': row.get('author'),
                         'factual_status': 'publisher claim; not verified world state'}, 'attributes': attrs}
