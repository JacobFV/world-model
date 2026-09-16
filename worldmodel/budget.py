"""Global fair-share download budget backed by a cross-process SQLite ledger.

Allocation is weighted max-min fairness (water-filling) over datasets that
declare an ``acquisition`` block. Usage is measured, not assumed: ``reconcile``
rescans each dataset's ``artifacts/raw`` and in-progress ``scratch/acquire-*``
files (hardlinked inodes count once). Downloads add outstanding reservations
and staged increments between scans.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid

GIB = 1024 ** 3
DEFAULT_TOTAL = 100 * GIB
DEFAULT_MAX_SHARE = 0.05
_UNITS = {'': 1, 'b': 1, 'kb': 1000, 'mb': 1000 ** 2, 'gb': 1000 ** 3, 'tb': 1000 ** 4,
          'kib': 1024, 'mib': 1024 ** 2, 'gib': 1024 ** 3, 'tib': 1024 ** 4,
          'k': 1024, 'm': 1024 ** 2, 'g': 1024 ** 3, 't': 1024 ** 4}


class BudgetExhausted(Exception):
    """The dataset's fair-share allocation cannot cover the next bytes."""


class DailyRequestLimit(Exception):
    """A declared per-host requests_per_day quota is used up for today (UTC)."""


def parse_size(value):
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise ValueError('Size must be non-negative')
        return value
    match = re.fullmatch(r'\s*(\d+(?:\.\d+)?)\s*([A-Za-z]*)\s*', str(value))
    if not match or match[2].lower() not in _UNITS:
        raise ValueError(f'Invalid size: {value!r} (use bytes or e.g. 100GiB, 500MB)')
    return int(float(match[1]) * _UNITS[match[2].lower()])


def resolve_total(value=None, environ=None):
    environ = os.environ if environ is None else environ
    if value is not None:
        return parse_size(value)
    if environ.get('WORLD_MODEL_DOWNLOAD_BUDGET'):
        return parse_size(environ['WORLD_MODEL_DOWNLOAD_BUDGET'])
    return DEFAULT_TOTAL


def _fill(amount, caps, weights):
    """Weighted water-filling of ``amount`` into per-name ``caps``; integer bytes."""
    give = {name: 0 for name in caps}
    active = {name for name in caps if caps[name] > 0}
    remaining = amount
    while active and remaining > 0:
        total_weight = sum(weights[name] for name in active)
        saturated = [name for name in active
                     if caps[name] - give[name] <= remaining * weights[name] / total_weight]
        if saturated:
            for name in saturated:
                remaining -= caps[name] - give[name]
                give[name] = caps[name]
                active.discard(name)
            continue
        shares = {name: int(remaining * weights[name] // total_weight) for name in active}
        for name in active:
            give[name] += min(shares[name], caps[name] - give[name])
        remaining = amount - sum(give.values())
        for name in sorted(active, key=lambda n: (-weights[n], n)):
            if remaining <= 0:
                break
            if give[name] < caps[name]:
                give[name] += 1
                remaining -= 1
        break
    return give


def allocate(total, demands, max_share=DEFAULT_MAX_SHARE):
    """Allocate ``total`` bytes among ``{name: {desired, min, priority}}``.

    1. Guarantees: each dataset first receives ``min(min, desired)``; if the
       guarantees alone exceed the total they are water-filled by priority.
    2. Fair share: water-fill the rest with caps ``min(desired, max_share*total)``
       (never below the guarantee), weighted by ``priority``.
    3. Leftover that would otherwise go unused is water-filled up to ``desired``,
       ignoring ``max_share``.
    """
    total = max(0, int(total))
    if not 0 < max_share <= 1:
        raise ValueError('max_share must be in (0, 1]')
    names = sorted(demands)
    desired = {n: max(0, int(demands[n].get('desired', 0))) for n in names}
    weights = {n: float(demands[n].get('priority', 1.0)) for n in names}
    if any(w <= 0 for w in weights.values()):
        raise ValueError('priority weights must be positive')
    floor = {n: min(max(0, int(demands[n].get('min', 0))), desired[n]) for n in names}
    if sum(floor.values()) >= total:
        return _fill(total, floor, weights)
    allocation = dict(floor)
    cap = int(max_share * total)
    first = _fill(total - sum(floor.values()),
                  {n: max(floor[n], min(desired[n], cap)) - floor[n] for n in names}, weights)
    for n in names:
        allocation[n] += first[n]
    leftover = total - sum(allocation.values())
    if leftover > 0:
        second = _fill(leftover, {n: desired[n] - allocation[n] for n in names}, weights)
        for n in names:
            allocation[n] += second[n]
    return allocation


def scan_bytes(store, dataset):
    """Actual bytes of raw artifacts plus in-progress acquisition staging (unique inodes)."""
    base = store.dataset_dir(dataset)
    seen, total = set(), 0
    roots = [base / 'artifacts' / 'raw'] + sorted((base / 'scratch').glob('acquire-*'))
    for root in roots:
        if not root.is_dir():
            continue
        for directory, _, files in os.walk(root):
            for name in files:
                try:
                    stat = os.lstat(os.path.join(directory, name))
                except FileNotFoundError:
                    continue
                key = (stat.st_dev, stat.st_ino)
                if key not in seen:
                    seen.add(key)
                    total += stat.st_size
    return total


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


class Ledger:
    SCHEMA = '''
    CREATE TABLE IF NOT EXISTS usage (dataset TEXT PRIMARY KEY, disk_bytes INTEGER NOT NULL DEFAULT 0,
        staged_bytes INTEGER NOT NULL DEFAULT 0, scanned_at TEXT, complete_digest TEXT);
    CREATE TABLE IF NOT EXISTS reservations (id TEXT PRIMARY KEY, dataset TEXT NOT NULL,
        bytes INTEGER NOT NULL, pid INTEGER NOT NULL, created_at REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS hosts (host TEXT PRIMARY KEY, next_allowed REAL NOT NULL);
    CREATE TABLE IF NOT EXISTS daily (host TEXT NOT NULL, day TEXT NOT NULL, requests INTEGER NOT NULL,
        PRIMARY KEY (host, day));
    '''

    def __init__(self, data_root):
        self.directory = Path(data_root).resolve() / '.acquisition'
        self.directory.mkdir(parents=True, exist_ok=True)
        ignore = self.directory / '.gitignore'
        if not ignore.exists():
            ignore.write_text('*\n', encoding='utf-8')
        self.path = self.directory / 'ledger.sqlite'
        self.lock_path = self.directory / 'ledger.lock'
        with self.lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            db = sqlite3.connect(self.path, timeout=60, isolation_level=None)
            try:
                db.executescript(self.SCHEMA)  # executescript manages its own transaction.
            finally:
                db.close()
                fcntl.flock(lock, fcntl.LOCK_UN)

    @contextmanager
    def transaction(self):
        with self.lock_path.open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            db = sqlite3.connect(self.path, timeout=60, isolation_level=None)
            try:
                db.execute('BEGIN IMMEDIATE')
                try:
                    yield db
                except BaseException:
                    db.execute('ROLLBACK')
                    raise
                db.execute('COMMIT')
            finally:
                db.close()
                fcntl.flock(lock, fcntl.LOCK_UN)

    @staticmethod
    def _purge(db):
        for rid, pid in db.execute('SELECT id, pid FROM reservations').fetchall():
            if not _alive(pid):
                db.execute('DELETE FROM reservations WHERE id=?', (rid,))

    @staticmethod
    def _used(db, dataset):
        row = db.execute('SELECT disk_bytes + staged_bytes FROM usage WHERE dataset=?', (dataset,)).fetchone()
        reserved = db.execute('SELECT COALESCE(SUM(bytes), 0) FROM reservations WHERE dataset=?', (dataset,)).fetchone()[0]
        return (row[0] if row else 0), reserved

    def usage(self, dataset):
        with self.transaction() as db:
            self._purge(db)
            used, reserved = self._used(db, dataset)
            row = db.execute('SELECT scanned_at, complete_digest FROM usage WHERE dataset=?', (dataset,)).fetchone()
        return {'used': used, 'reserved': reserved, 'scanned_at': row[0] if row else None,
                'complete_digest': row[1] if row else None}

    def record_scan(self, dataset, disk_bytes):
        with self.transaction() as db:
            db.execute('INSERT INTO usage (dataset, disk_bytes, staged_bytes, scanned_at) VALUES (?,?,0,?) '
                       'ON CONFLICT(dataset) DO UPDATE SET disk_bytes=excluded.disk_bytes, staged_bytes=0, '
                       'scanned_at=excluded.scanned_at',
                       (dataset, disk_bytes, datetime.now(timezone.utc).isoformat()))

    def reconcile_dataset(self, store, dataset, *, holding_lock=False):
        """Rescan real on-disk usage. Skips datasets with an active acquisition in another process."""
        if not holding_lock:
            lock_path = store.dataset_dir(dataset) / 'scratch' / '.acquire.lock'
            if lock_path.exists():
                with lock_path.open('a') as lock:
                    try:
                        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        return None
                    try:
                        size = scan_bytes(store, dataset)
                        self.record_scan(dataset, size)
                        return size
                    finally:
                        fcntl.flock(lock, fcntl.LOCK_UN)
        size = scan_bytes(store, dataset)
        self.record_scan(dataset, size)
        return size

    def mark_complete(self, dataset, config_digest):
        with self.transaction() as db:
            db.execute('INSERT INTO usage (dataset, complete_digest) VALUES (?,?) '
                       'ON CONFLICT(dataset) DO UPDATE SET complete_digest=excluded.complete_digest',
                       (dataset, config_digest))

    def reserve(self, dataset, nbytes, allowed):
        """Reserve bytes against ``allowed``; returns reservation id or raises BudgetExhausted."""
        rid = uuid.uuid4().hex
        with self.transaction() as db:
            self._purge(db)
            used, reserved = self._used(db, dataset)
            if used + reserved + nbytes > allowed:
                raise BudgetExhausted(f'{dataset}: allocation {allowed:,} bytes cannot cover '
                                      f'{nbytes:,} more (used {used:,}, reserved {reserved:,})')
            db.execute('INSERT INTO reservations VALUES (?,?,?,?,?)', (rid, dataset, nbytes, os.getpid(), time.time()))
        return rid

    def extend(self, rid, dataset, nbytes, allowed):
        with self.transaction() as db:
            used, reserved = self._used(db, dataset)
            if used + reserved + nbytes > allowed:
                raise BudgetExhausted(f'{dataset}: allocation {allowed:,} bytes exhausted')
            db.execute('UPDATE reservations SET bytes = bytes + ? WHERE id=?', (nbytes, rid))

    def consume(self, rid, dataset, nbytes):
        """Move written bytes from an outstanding reservation into staged usage."""
        if nbytes == 0:
            return
        with self.transaction() as db:
            db.execute('UPDATE reservations SET bytes = MAX(bytes - ?, 0) WHERE id=?', (nbytes, rid))
            db.execute('INSERT INTO usage (dataset, staged_bytes) VALUES (?,?) '
                       'ON CONFLICT(dataset) DO UPDATE SET staged_bytes = staged_bytes + excluded.staged_bytes',
                       (dataset, nbytes))

    def add_staged(self, dataset, delta):
        """Adjust staged usage, e.g. negative after discarding a partial file."""
        with self.transaction() as db:
            db.execute('INSERT INTO usage (dataset, staged_bytes) VALUES (?,?) '
                       'ON CONFLICT(dataset) DO UPDATE SET staged_bytes = MAX(staged_bytes + excluded.staged_bytes, 0)',
                       (dataset, delta))

    def release(self, rid):
        with self.transaction() as db:
            db.execute('DELETE FROM reservations WHERE id=?', (rid,))

    def rate_slot(self, host, requests_per_second=None, requests_per_day=None):
        """Claim the next request slot for ``host``; returns seconds the caller must wait."""
        now = time.time()
        with self.transaction() as db:
            if requests_per_day:
                day = datetime.now(timezone.utc).date().isoformat()
                row = db.execute('SELECT requests FROM daily WHERE host=? AND day=?', (host, day)).fetchone()
                if row and row[0] >= requests_per_day:
                    raise DailyRequestLimit(f'{host}: {requests_per_day} requests/day used for {day} (UTC)')
                db.execute('INSERT INTO daily VALUES (?,?,1) ON CONFLICT(host, day) DO UPDATE SET requests = requests + 1',
                           (host, day))
            if not requests_per_second:
                return 0.0
            row = db.execute('SELECT next_allowed FROM hosts WHERE host=?', (host,)).fetchone()
            start = max(now, row[0] if row else now)
            db.execute('INSERT INTO hosts VALUES (?,?) ON CONFLICT(host) DO UPDATE SET next_allowed=excluded.next_allowed',
                       (host, start + 1.0 / requests_per_second))
        return max(0.0, start - now)

    def daily_requests(self, host):
        day = datetime.now(timezone.utc).date().isoformat()
        with self.transaction() as db:
            row = db.execute('SELECT requests FROM daily WHERE host=? AND day=?', (host, day)).fetchone()
        return row[0] if row else 0


NON_CONTENT_KEYS = frozenset({
    'desired_bytes', 'min_bytes', 'priority', 'description', 'documentation', 'reader', 'rate_limit',
    'timeout_seconds', 'retries', 'backoff_seconds', 'max_backoff_seconds', 'max_retry_after_seconds',
    'reserve_chunk_bytes', 'publish_partial', 'user_agent', 'user_agent_env'})


def acquisition_identity(block):
    """Digest of the keys that determine *what* is downloaded.

    Budget, politeness and reader settings may change without invalidating
    resumable staging or a settled (complete) acquisition.
    """
    from .util import digest
    return digest({k: v for k, v in block.items() if k not in NON_CONTENT_KEYS})


def demand(definition):
    """Return ``(desired, min, priority)`` or raise ValueError for an invalid block."""
    block = definition.get('acquisition')
    if not isinstance(block, dict):
        raise ValueError('No acquisition block')
    desired, minimum, priority = block.get('desired_bytes'), block.get('min_bytes', 0), block.get('priority', 1)
    for name, value in (('desired_bytes', desired), ('min_bytes', minimum)):
        if type(value) is not int or value < 0:
            raise ValueError(f'acquisition.{name} must be a non-negative integer')
    if type(priority) not in (int, float) or priority <= 0:
        raise ValueError('acquisition.priority must be a positive number')
    return desired, minimum, float(priority)


def budget_table(definitions, store, ledger, total=None, max_share=DEFAULT_MAX_SHARE, *, rescan=False):
    """Allocation table for every catalog dataset; unmanaged raw usage shrinks the pool."""
    total = resolve_total(total)
    managed, unmanaged, errors, config_digests = {}, 0, {}, {}
    for definition in definitions:
        name = definition['id']
        info = ledger.usage(name)
        if rescan or info['scanned_at'] is None:
            if store.dataset_dir(name).exists():
                ledger.reconcile_dataset(store, name)
                info = ledger.usage(name)
        if 'acquisition' not in definition:
            unmanaged += info['used'] + info['reserved']
            continue
        try:
            desired, minimum, priority = demand(definition)
        except ValueError as error:
            errors[name] = str(error)
            unmanaged += info['used'] + info['reserved']
            continue
        config_digests[name] = acquisition_identity(definition['acquisition'])
        settled = info['complete_digest'] is not None and info['complete_digest'] == config_digests[name]
        held = info['used'] + info['reserved']
        if settled:  # A finished acquisition only needs what it actually holds.
            desired = max(minimum, held)
        # Bytes already on disk are sunk: allocate them first so the rest of the
        # pool is shared fairly and the total can never exceed the budget, even
        # when shares shrink after other datasets declare demand.
        desired, minimum = max(desired, held), max(minimum, held)
        managed[name] = {'desired': desired, 'min': minimum, 'priority': priority, 'info': info,
                         'declared_desired': definition['acquisition']['desired_bytes'], 'settled': settled}
    pool = max(0, total - unmanaged)
    allocation = allocate(pool, {n: v for n, v in managed.items()}, max_share)
    rows = []
    for name in sorted(managed):
        item = managed[name]
        used, reserved = item['info']['used'], item['info']['reserved']
        rows.append({'dataset': name, 'desired': item['declared_desired'], 'demand': item['desired'],
                     'min': item['min'], 'priority': item['priority'], 'allocated': allocation[name],
                     'used': used, 'reserved': reserved,
                     'remaining': max(0, allocation[name] - used - reserved), 'settled': item['settled']})
    for name, error in sorted(errors.items()):
        rows.append({'dataset': name, 'error': error})
    valid = [r for r in rows if 'error' not in r]
    return {'total': total, 'max_share': max_share, 'unmanaged_bytes': unmanaged, 'pool': pool,
            'datasets': rows,
            'totals': {key: sum(r[key] for r in valid) for key in ('desired', 'allocated', 'used', 'reserved', 'remaining')}}
