"""Streaming SDMX-ML 2.1 GenericData reader (stdlib iterparse; one series in memory at a time)."""
from xml.etree import ElementTree as ET

GENERIC = '{http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic}'


def _values(element):
    return {value.get('id'): value.get('value') for value in element.findall(GENERIC + 'Value')}


def iter_series(path):
    """Yield ``(ordinal, series_key, [observation, ...])`` for each ``generic:Series``.

    Each observation is ``{'time': str, 'value': str|None, 'attributes': {...}}``. A response whose root
    is not GenericData (e.g. WITS ``<string>`` error bodies) raises ``ValueError``.
    """
    ordinal = 0
    root_checked = False
    for event, element in ET.iterparse(path, events=('start', 'end')):
        if event == 'start':
            if not root_checked:
                root_checked = True
                if not element.tag.endswith('}GenericData'):
                    raise ValueError(f'Not an SDMX GenericData message (root {element.tag!r}); source error body?')
            continue
        if element.tag != GENERIC + 'Series':
            continue
        key_element = element.find(GENERIC + 'SeriesKey')
        key = _values(key_element) if key_element is not None else {}
        observations = []
        for obs in element.findall(GENERIC + 'Obs'):
            dimension = obs.find(GENERIC + 'ObsDimension')
            value = obs.find(GENERIC + 'ObsValue')
            attributes = obs.find(GENERIC + 'Attributes')
            observations.append({'time': dimension.get('value') if dimension is not None else None,
                                 'value': value.get('value') if value is not None else None,
                                 'attributes': _values(attributes) if attributes is not None else {}})
        yield ordinal, key, observations
        ordinal += 1
        element.clear()
