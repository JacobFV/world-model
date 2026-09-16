"""Streaming, DTD-free XML item reader (stdlib only).

``iter_items(path)`` yields ``(section, ordinal, element)`` for every element two
levels below the document root (e.g. ``DistinctParties/DistinctParty``), removing
each from the tree after use so memory stays bounded by the largest single item.
Top-level sections named in ``whole`` (small lookup tables) are yielded intact
with ordinal 0.
"""
import re
import xml.etree.ElementTree as ET


def local(tag):
    return tag.rsplit('}', 1)[-1]


def check_safe(path, head_bytes=1 << 20):
    with open(path, 'rb') as stream:
        head = stream.read(head_bytes)
    if re.search(br'<!\s*(DOCTYPE|ENTITY)', head.replace(b'\x00', b''), re.I):
        raise ValueError('XML DTD/entity declarations are forbidden')


def sniff(path, size=512):
    with open(path, 'rb') as stream:
        return stream.read(size)


def iter_items(path, whole=()):
    check_safe(path)
    stack, counts = [], {}
    for event, element in ET.iterparse(str(path), events=('start', 'end')):
        if event == 'start':
            stack.append(element)
            continue
        stack.pop()
        depth = len(stack) + 1
        name = local(element.tag)
        if depth == 2 and name in whole:
            yield name, 0, element
            stack[-1].remove(element)
        elif depth == 3 and local(stack[-1].tag) not in whole:
            section = local(stack[-1].tag)
            counts[section] = counts.get(section, 0) + 1
            yield section, counts[section], element
            stack[-1].remove(element)


def text(element, path=None):
    if element is None:
        return None
    if path is not None:
        element = element.find(path)
        if element is None:
            return None
    value = (element.text or '').strip()
    return value or None
