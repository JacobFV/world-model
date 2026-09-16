"""Dataset-local, bounded-memory evidence emission (stdlib only).

Record IDs are ``<prefix>:<natural key>`` when the identity is a string (compact and
compressible), otherwise a digest of (dataset, raw artifact, identity). Entities are
emitted once per entity_id across the whole stage via a spill-to-SQLite key set, so
memory stays bounded even for millions of distinct keys.
"""
import math
import os
import sqlite3
import tempfile
from datetime import date

from worldmodel.util import digest

MISSING = {'', '.', 'NA', 'N/A', 'na', 'n/a', 'null', 'NULL', 'None', 'nan', 'NaN', '-', '--'}


def is_full(context):
    """True for sharded full acquisitions (or non-sample single payload imports)."""
    coverage = getattr(context, 'raw_coverage', None)
    if coverage is None:
        return False
    info = coverage()
    return info['layout'] == 'shards' or not info['sampled'] and 'acquisition' in (context.raw_receipt().get('source') or {})


def num(value):
    """Parse a numeric source cell; None for blanks/missing markers. Integral floats become ints."""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        result = float(value)
    else:
        text = str(value).strip().replace(',', '')
        if text in MISSING:
            return None
        result = float(text)
    if not math.isfinite(result):
        return None
    return int(result) if result.is_integer() and abs(result) < 2 ** 53 else result


def year_bounds(year):
    year = int(year)
    return f'{year:04d}-01-01', f'{year + 1:04d}-01-01'


def month_bounds(year, month):
    year, month = int(year), int(month)
    end = date(year + (month == 12), 1 if month == 12 else month + 1, 1)
    return f'{year:04d}-{month:02d}-01', end.isoformat()


class KeySet:
    """Set of strings that spills to a temporary SQLite file past ``limit`` keys."""

    def __init__(self, limit=500_000):
        self.limit, self.memory, self.db, self.dir = limit, set(), None, None

    def add(self, key):
        """Add key; return True if it was new."""
        if self.db is None:
            if key in self.memory:
                return False
            self.memory.add(key)
            if len(self.memory) > self.limit:
                self.dir = tempfile.mkdtemp(prefix='wm-keys-')
                self.db = sqlite3.connect(os.path.join(self.dir, 'keys.sqlite'))
                self.db.executescript('PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; CREATE TABLE k (key TEXT PRIMARY KEY)')
                self.db.executemany('INSERT INTO k VALUES (?)', ((k,) for k in self.memory))
                self.memory = set()
            return True
        return self.db.execute('INSERT OR IGNORE INTO k VALUES (?)', (key,)).rowcount == 1

    def close(self):
        if self.db is not None:
            self.db.close()
            for name in os.listdir(self.dir):
                os.unlink(os.path.join(self.dir, name))
            os.rmdir(self.dir)
            self.db = None


class Evidence:
    """Builds evidence records bound to one raw input."""

    def __init__(self, context, prefix, index=0):
        self.context, self.prefix, self.index = context, prefix, index
        self.dataset = context.definition['id']
        self.ref = context.raw_inputs[index]
        receipt = context.raw_receipt(index) if hasattr(context, 'raw_receipt') else {}
        self.observed_at = receipt.get('retrieved_at') or '1970-01-01T00:00:00+00:00'
        self.complete = receipt.get('complete', True)
        self.keys = KeySet()

    def record_id(self, identity):
        if isinstance(identity, str):
            return self.prefix + ':' + identity
        return self.prefix + ':' + digest([self.dataset, self.ref, identity])

    def _base(self, kind, identity, locator, attrs=None, **fields):
        record = {'kind': kind, 'id': self.record_id(identity), 'observed_at': self.observed_at,
                  'evidence': self.context.raw_evidence(locator, self.index),
                  'attributes': {'source_dataset': self.dataset, **(attrs or {})}}
        record.update(fields)
        return record

    def entity(self, key, entity_type, label, locator, **attrs):
        """Return the entity record the first time ``key`` is seen, else None."""
        if not self.keys.add('e|' + key):
            return None
        return self._base('entity', 'entity:' + key, locator, attrs, entity_id=key, entity_type=entity_type,
                          label=str(label or key)[:500])

    def relation(self, subject, predicate, obj, locator, identity=None, once=True, **attrs):
        """Assertion subject-predicate-object. ``once`` dedupes on (s,p,o)."""
        ident = identity if isinstance(identity, str) else ['rel', subject, predicate, obj, identity]
        if once and not self.keys.add('r|' + (ident if isinstance(ident, str) else digest(ident))):
            return None
        return self._base('assertion', ident, locator, attrs, subject=subject, predicate=predicate, object=obj)

    def claim(self, subject, predicate, value, locator, identity=None, **attrs):
        ident = identity if isinstance(identity, str) else ['claim', subject, predicate, value]
        return self._base('assertion', ident, locator, attrs, subject=subject, predicate=predicate, value=value)

    def observation(self, subject, metric, value, unit, locator, *, valid_from=None, valid_to=None,
                    dimensions=None, missing_reason=None, identity=None, aggregate=None, **attrs):
        dims = dict(dimensions or {})
        ident = identity if isinstance(identity, str) else ['obs', subject, metric, valid_from, valid_to, dims, identity]
        record = self._base('observation', ident, locator, attrs, subject=subject, metric=metric, value=value,
                            unit=unit, dimensions=dims)
        if valid_from:
            record['valid_from'] = valid_from
        if valid_to:
            record['valid_to'] = valid_to
        if value is None:
            record['missing_reason'] = missing_reason or 'source_missing'
        if aggregate is not None:
            record['attributes']['aggregate'] = aggregate
        return record

    def event(self, event_type, occurred_at, participants, locator, identity, **fields):
        attrs = fields.pop('attributes', {})
        return self._base('event', identity if isinstance(identity, str) else ['event', identity], locator, attrs,
                          event_type=event_type, occurred_at=occurred_at, participants=list(participants), **fields)

    def close(self):
        self.keys.close()
