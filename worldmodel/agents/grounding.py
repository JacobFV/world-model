"""Binding a cognitive agent to a real entity in the unified graph.

Three things live here, and they are the whole of what "grounded" means in
``docs/agents-design.md``:

**A read-only view of the index.** :class:`EvidenceIndex` opens
``data/world_evidence/index.sqlite`` (graph schema 3) read-only, with its own
``PRAGMA cache_size``, and offers exactly the bounded lookups an agent needs: the entity
record, the resolved identity cluster, windowed edges in one direction, and observations
by metric. Every row it hands back carries the ``dataset``/``stage``/``version`` and the
published record id it came from. It never scans; every query is served by one of the
index's eight indexes and is bounded by the agent's own degree.

**Seeding from published records only.** :func:`seed_store` fills a tensorcode ``Store``
with claims whose evidence is a published record. A facet with no published record stays
**Unknown**: it is reported in :class:`SeedReport.unknown` and no claim is invented for
it. On this catalog a legislator's individual roll-call votes are exactly such a facet -
``voteview_rollcalls`` publishes the *scaling* of their votes, not the votes - so the
agent's store says ``Unknown`` about how they voted, and says why.

Two Unknowns on this catalog are worth naming, because they are the contract working
rather than a gap: a legislator's **individual roll-call positions** are published as
``kind='event'`` records with no subject, object or entity id, so no edge reaches them and
they must be addressed by primary key (:class:`RollCalls` does exactly that, bounded); and
**contribution amounts** do not exist here at all - ``fec`` publishes ``supports_candidate``
as a per-cycle boolean, and all three ``fec_individual_contributions*`` datasets contributed
zero records to this index. The agent therefore believes *who* supported it and says
``Unknown`` about *how much*, with a reason.

**Bounded perception.** An agent does not see the catalog. It sees what its
:class:`Horizon` - a function of its role - lets through:

1. only entities within ``hops`` of its own resolved identity cluster in the edge index;
2. only the predicates and observation metrics its role declares (a legislator cannot see
   13F holdings or road topology, though they sit in the same file);
3. only records already **public** (``published_at <= known_at``) when the caller pins a
   knowledge horizon. A record whose publication date is unknown is withheld rather than
   dated by the unify run's ingest wall clock; :attr:`EdgeIndex.publication_policy` says
   which rule was in force, and ``include_unknown_publication=True`` restores the old
   ingest-time reading and labels it. On an index built before graph schema 4 there is no
   publication date at all, and a pinned ``known_at`` is refused rather than answered from
   ingest time. ``known_at`` still defaults to ``None`` (no filter); real time is carried
   by ``valid_from``/``valid_to``. See ``docs/point-in-time-graph.md``;
4. dated assertions only inside the tick's window; undated ones as standing condition;
5. at most ``edge_scan`` index rows fetched, ranked by salience, then truncated to
   ``per_tick`` (default 24, the civ sim's 12-30 band). :class:`Perception` reports how
   many were considered and how many were dropped, so the bound is auditable rather than
   implicit.

Nothing here is fitted to an outcome and nothing here is ``validated``.
"""
import datetime as _dt
import json
import re
import sqlite3
import zlib
from dataclasses import dataclass, field
from pathlib import Path

from . import load_tensorcode

tc = load_tensorcode()

UTC = _dt.timezone.utc
#: Mirrors ``worldmodel.graph.READABLE_SCHEMAS``: the resolved/edge shape this module reads.
REQUIRED_SCHEMA = '3'
#: Schema that carries ``published_at``. Below it, a pinned ``known_at`` cannot be honoured.
PUBLICATION_SCHEMA = '4'


def default_index_path(data_root=None):
    """The unified-graph index this checkout builds by default."""
    if data_root is None:
        from ..resources import resource_roots
        data_root = resource_roots()['data']
    return Path(data_root) / 'world_evidence' / 'index.sqlite'


#: A namespaced entity id becomes a ``Ref``; anything else stays a plain value. Deliberately
#: strict, because ``Ref`` accepts any ``kind:name`` and a published string like
#: ``"Public Law No: 119-1"`` would otherwise be mistaken for an entity.
_ENTITY_ID = re.compile(r'^[a-z][a-z0-9_]*(:[A-Za-z0-9_.\-]+)+$')


def _is_entity_id(value):
    return isinstance(value, str) and bool(_ENTITY_ID.match(value))


def _decode_body(row):
    """The published record, plus where it came from. Mirrors ``Graph._decode``."""
    body = row['body']
    record = json.loads(zlib.decompress(body) if isinstance(body, bytes) else body)
    record['_provenance'] = {'dataset': row['dataset'], 'stage': row['stage'], 'version': row['version'],
                             'record_id': row['id'], 'input': json.loads(row['input_ref'])}
    return record


def _time_key(value):
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.astimezone(UTC).isoformat() if value.tzinfo else value.replace(tzinfo=UTC).isoformat()
    return str(value)


def _parse_time(value):
    if not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


class EvidenceIndex:
    """A read-only, bounded reader over the unified graph index.

    ``worldmodel.graph.Graph`` owns the general traversal contracts (``neighborhood``,
    ``resolved_entity``, ``paths``) and is used for them through :meth:`graph`. What this
    class adds is the two shapes an agent tick needs and ``Graph`` does not express: an
    edge query restricted to a *half-open time window*, and a single long-lived read-only
    connection with a page cache, so a tick does not pay connection setup per query.
    """

    def __init__(self, path=None, *, cache_mb=64, data_root=None, include_unknown_publication=False):
        self.path = Path(path) if path is not None else default_index_path(data_root)
        if not self.path.is_file():
            raise ValueError('Graph index missing at %s; run `wm unify` (or `wm graph-build`) first' % self.path)
        uri = self.path.resolve().as_uri() + '?mode=ro'
        self._connection = sqlite3.connect(uri, uri=True)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute('PRAGMA cache_size=%d' % -(int(cache_mb) * 1024))
        self._connection.execute('PRAGMA query_only=ON')
        row = self._connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if row is None or row['value'] < REQUIRED_SCHEMA:
            self._connection.close()
            raise ValueError('Grounded agents need graph schema %s; run `wm unify` to rebuild' % REQUIRED_SCHEMA)
        self.schema_version = row['value']
        self.include_unknown_publication = bool(include_unknown_publication)
        self.publication_dates = row['value'] >= PUBLICATION_SCHEMA
        self._graph = None
        self._entity_cache = {}

    # -- lifecycle ----------------------------------------------------------------------
    def close(self):
        self._connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def graph(self):
        """The shared ``Graph`` façade, for the general traversal contracts."""
        if self._graph is None:
            from ..graph import Graph
            self._graph = Graph(self.path)
        return self._graph

    # -- the knowledge horizon ----------------------------------------------------------
    def _known_clause(self, known_at):
        """The ``known_at`` filter: publication date where the index has one, never ingest time."""
        if known_at is None:
            return '', []
        key = _time_key(known_at)
        if not self.publication_dates:
            if not self.include_unknown_publication:
                raise ValueError(
                    'This index is graph schema %s and carries no publication dates, so a knowledge horizon would be '
                    'the unify run\'s ingest clock, not what was public. Rebuild at schema %s (`wm unify`), or '
                    'construct EdgeIndex(include_unknown_publication=True) to accept ingest time knowingly.'
                    % (self.schema_version, PUBLICATION_SCHEMA))
            return ' AND observed_at <= ?', [key]
        if self.include_unknown_publication:
            return ' AND (published_at <= ? OR (published_at IS NULL AND observed_at <= ?))', [key, key]
        return ' AND published_at IS NOT NULL AND published_at <= ?', [key]

    @property
    def publication_policy(self):
        """What a pinned ``known_at`` means on this index, for the agent's own report."""
        return {'graph_schema': self.schema_version, 'publication_dates_available': self.publication_dates,
                'policy': 'include_unknown_publication_as_ingested' if self.include_unknown_publication
                          else 'exclude_unknown_publication',
                'filtered_on': 'published_at' if self.publication_dates else 'observed_at'}

    # -- identity -----------------------------------------------------------------------
    def canonical(self, entity_id):
        row = self._connection.execute('SELECT canonical_id FROM resolved WHERE entity_id=?', (entity_id,)).fetchone()
        return row['canonical_id'] if row else entity_id

    def cluster(self, entity_id):
        """Every entity id asserted to be the same thing, the agent's own id first."""
        canonical_id = self.canonical(entity_id)
        members = [r['entity_id'] for r in self._connection.execute(
            'SELECT entity_id FROM resolved WHERE canonical_id=? ORDER BY entity_id', (canonical_id,))]
        members = members or [canonical_id]
        return tuple([entity_id] + [m for m in members if m != entity_id])

    # -- records ------------------------------------------------------------------------
    def entity(self, entity_id, *, known_at=None):
        """The published entity record for ``entity_id``, or ``None`` when nothing published one."""
        key = (entity_id, _time_key(known_at))
        if key in self._entity_cache:
            return self._entity_cache[key]
        clause, args = self._known_clause(known_at)
        row = self._connection.execute(
            "SELECT * FROM records WHERE kind='entity' AND entity_id=?" + clause
            + ' ORDER BY observed_at DESC LIMIT 1', [entity_id, *args]).fetchone()
        record = _decode_body(row) if row is not None else None
        if len(self._entity_cache) < 4096:
            self._entity_cache[key] = record
        return record

    def record_at(self, rowid):
        row = self._connection.execute('SELECT * FROM records WHERE rowid=?', (rowid,)).fetchone()
        return _decode_body(row) if row is not None else None

    def edges(self, entity_ids, predicate, *, direction='out', since=None, until=None,
              known_at=None, limit=200):
        """Edges on ``predicate`` touching any of ``entity_ids``, newest first.

        ``since``/``until`` form a half-open window on ``valid_from``; rows with no
        ``valid_from`` are *standing* and are returned only when no window is given.
        ``known_at`` caps the **publication** date (see :meth:`_known_clause`). Served by ``edge_subject_idx``/``edge_object_idx``,
        so the cost is the agent's own degree on that predicate, never the table.
        """
        column = 'subject' if direction == 'out' else 'object'
        clauses, args = [], []
        if since is not None or until is not None:
            clauses.append('valid_from IS NOT NULL')
            if since is not None:
                clauses.append('valid_from >= ?')
                args.append(_time_key(since))
            if until is not None:
                clauses.append('valid_from < ?')
                args.append(_time_key(until))
        suffix = ''.join(' AND ' + clause for clause in clauses)
        known_clause, known_args = self._known_clause(known_at)
        suffix += known_clause
        args.extend(known_args)
        out, budget = [], int(limit)
        for entity_id in entity_ids:
            if budget <= 0:
                break
            rows = self._connection.execute(
                'SELECT subject, predicate, object, weight, valid_from, valid_to, observed_at, record_rowid'
                ' FROM edges WHERE %s=? AND predicate=?%s ORDER BY valid_from DESC, rowid DESC LIMIT ?' % (column, suffix),
                [entity_id, predicate, *args, budget]).fetchall()
            out.extend(dict(row) for row in rows)
            budget -= len(rows)
        return out

    def pinned_version(self, dataset, stage='normalized'):
        """The ``version`` this index pinned for ``dataset``, from ``metadata.inputs``."""
        if not hasattr(self, '_inputs'):
            row = self._connection.execute("SELECT value FROM metadata WHERE key='inputs'").fetchone()
            self._inputs = json.loads(row['value']) if row else []
        for ref in self._inputs:
            if ref.get('dataset') == dataset and ref.get('stage', 'normalized') == stage:
                return ref['version']
        return None

    def record_by_id(self, dataset, record_id, *, stage='normalized', version=None):
        """A record addressed by its primary key. The only path to ``kind='event'`` rows,
        which carry no subject, object or entity id and so appear in no index."""
        version = version or self.pinned_version(dataset, stage)
        if version is None:
            return None
        row = self._connection.execute(
            'SELECT * FROM records WHERE dataset=? AND stage=? AND version=? AND id=?',
            (dataset, stage, version, record_id)).fetchone()
        return _decode_body(row) if row is not None else None

    def observations(self, entity_ids, metric, *, known_at=None, limit=8):
        """The most recent published observations of ``metric`` about any of ``entity_ids``."""
        clause, args = self._known_clause(known_at)
        out = []
        for entity_id in entity_ids:
            if len(out) >= limit:
                break
            rows = self._connection.execute(
                "SELECT * FROM records WHERE subject=? AND kind='observation' AND metric=?" + clause
                + ' ORDER BY valid_from DESC LIMIT ?', [entity_id, metric, *args, limit - len(out)]).fetchall()
            out.extend(_decode_body(row) for row in rows)
        return out


# --------------------------------------------------------------------------- the horizon


@dataclass(frozen=True)
class PerceptSpec:
    """One published edge predicate a role is allowed to perceive, and what it means to it."""

    index_predicate: str
    direction: str            # 'out': the agent is the subject; 'in': the agent is the object
    predicate: str            # the predicate the agent's own claim uses
    salience: float           # base weight before recency and novelty
    dated: bool = True        # valid_from carries the event date, so the tick window applies
    episode: str = None       # episode tag when this arrives inside the window; None: not memorable
    caps: int = 120           # index rows fetched for this predicate per tick


@dataclass(frozen=True)
class MetricSpec:
    """One published observation metric a role is allowed to read."""

    metric: str
    predicate: str
    salience: float = 0.35
    caps: int = 2


@dataclass(frozen=True)
class Horizon:
    """What an agent in a role can see, and how much of it reaches working memory per tick.

    ``per_tick`` is the working-memory cap. The civ sim runs 12-30; 24 is the default here.
    """

    role: str
    specs: tuple = ()
    metrics: tuple = ()
    hops: int = 1
    per_tick: int = 24
    edge_scan: int = 400
    resolved: bool = True
    decay_days: float = 540.0
    per_predicate_share: float = 0.25   # no channel may own more than this share of working memory
    event_boost: float = 1.6            # something that happened inside the window, over standing context
    roll_calls: bool = False      # read this role's own roll-call positions (see ``RollCalls``)
    roll_call_scan: int = 60      # primary-key lookups allowed per tick
    roll_call_limit: int = 8      # votes that may reach working memory per tick

    def predicates(self):
        return tuple(spec.index_predicate for spec in self.specs)

    def spec_for(self, index_predicate, direction):
        for spec in self.specs:
            if spec.index_predicate == index_predicate and spec.direction == direction:
                return spec
        return None


#: A member of Congress. Every predicate here is one this catalog actually publishes for
#: ``bioguide:*`` entities (or for the ``icpsr:``/``fec:candidate:`` ids resolved into the
#: same cluster); nothing is aspirational.
LEGISLATOR = Horizon(
    role='legislator',
    specs=(
        PerceptSpec('committee_member', 'out', 'serves_on', 0.55, dated=False, episode='committee', caps=40),
        PerceptSpec('holds_role', 'out', 'holds_office', 0.50, dated=True, episode='office', caps=40),
        PerceptSpec('congressional_service', 'out', 'in_office', 0.40, dated=True, episode=None, caps=40),
        PerceptSpec('party_affiliation', 'out', 'party', 0.40, dated=True, episode=None, caps=20),
        PerceptSpec('sponsored_measure', 'out', 'sponsored', 0.85, dated=True, episode='sponsor', caps=120),
        PerceptSpec('cosponsored_measure', 'out', 'cosponsored', 0.45, dated=True, episode='cosponsor', caps=200),
        PerceptSpec('supports_candidate', 'in', 'received_support', 0.70, dated=True, episode='donation', caps=120),
        PerceptSpec('opposes_candidate', 'in', 'opposed_by', 0.80, dated=True, episode='opposition', caps=60),
        PerceptSpec('authorized_committee_of', 'in', 'campaign_committee', 0.25, dated=True, episode=None, caps=40),
        PerceptSpec('referred_to_committee', 'out', 'referred', 0.30, dated=True, episode=None, caps=40),
    ),
    metrics=(
        MetricSpec('dw_nominate_dim1', 'ideal_point_economic', 0.40),
        MetricSpec('dw_nominate_dim2', 'ideal_point_social', 0.30),
        MetricSpec('scaled_roll_call_votes', 'votes_scaled', 0.45),
    ),
    roll_calls=True,
)

class RollCalls:
    """A legislator's own roll-call positions, addressed by primary key and bounded.

    ``voteview_rollcalls`` publishes one ``roll_call_member_positions`` event per roll call,
    holding the ICPSR numbers that voted yea, nay or not-voting. Those events carry no
    subject, object or entity id, so no index reaches them: the only way in is the
    ``(dataset, stage, version, id)`` primary key, with the published id template
    ``voteview_rollcalls:positions:{S|H}{congress}:{rollnumber}``.

    This walks roll numbers **downward** from a probed maximum, at most ``scan`` primary-key
    lookups per tick, and stops as soon as a roll call falls before the window. Finding the
    maximum costs about twenty more lookups (an exponential probe and a bisection) and is
    cached. No scan of any table is involved.
    """

    DATASET = 'voteview_rollcalls'

    def __init__(self, index, *, scan=60):
        self.index = index
        self.scan = int(scan)
        self._max = {}

    def _positions(self, chamber, congress, rollnumber):
        return self.index.record_by_id(
            self.DATASET, '%s:positions:%s%d:%d' % (self.DATASET, chamber, congress, rollnumber))

    def _rollcall(self, chamber, congress, rollnumber):
        return self.index.record_by_id(
            self.DATASET, '%s:rollcall:%s%d:%d' % (self.DATASET, chamber, congress, rollnumber))

    def last_rollnumber(self, chamber, congress):
        """The highest published roll number for this chamber and congress, by bisection."""
        key = (chamber, congress)
        if key in self._max:
            return self._max[key]
        if self._positions(chamber, congress, 1) is None:
            self._max[key] = 0
            return 0
        low, high = 1, 2
        while high <= 4096 and self._positions(chamber, congress, high) is not None:
            low, high = high, high * 2
        while low + 1 < high:
            middle = (low + high) // 2
            if self._positions(chamber, congress, middle) is not None:
                low = middle
            else:
                high = middle
        self._max[key] = low
        return low

    def occurred(self, chamber, congress, rollnumber):
        record = self._positions(chamber, congress, rollnumber)
        if record is None:
            return None
        return _parse_time((record.get('attributes') or {}).get('occurred_at') or record.get('occurred_at'))

    def last_before(self, chamber, congress, when):
        """The highest roll number that occurred strictly before ``when``, by bisection.

        Roll numbers rise with time within a congress, so this is ~10 primary-key lookups
        instead of walking a whole session backwards.
        """
        last = self.last_rollnumber(chamber, congress)
        if when is None or last == 0:
            return last
        low, high = 0, last  # low: known before `when` (0 is the empty sentinel); high: known not-before or the end
        occurred = self.occurred(chamber, congress, last)
        if occurred is not None and occurred < when:
            return last
        while low + 1 < high:
            middle = (low + high) // 2
            occurred = self.occurred(chamber, congress, middle)
            if occurred is None or occurred < when:
                low = middle
            else:
                high = middle
        return low

    def votes(self, chamber, congress, icpsr_number, *, window=None, limit=12):
        """``(rollnumber, position, positions_record, rollcall_record)`` newest first."""
        start, end = (window or (None, None))
        last = self.last_before(chamber, congress, end) if end is not None else self.last_rollnumber(chamber, congress)
        out, looked = [], 0
        for rollnumber in range(last, 0, -1):
            if looked >= self.scan or len(out) >= limit:
                break
            looked += 1
            record = self._positions(chamber, congress, rollnumber)
            if record is None:
                continue
            occurred = _parse_time((record.get('attributes') or {}).get('occurred_at') or record.get('occurred_at'))
            if end is not None and occurred is not None and occurred >= end:
                continue
            if start is not None and occurred is not None and occurred < start:
                break  # roll numbers rise with time: everything earlier is out of the window
            positions = ((record.get('attributes') or {}).get('positions') or {})
            position = next((name for name, members in positions.items() if icpsr_number in members), None)
            if position is None:
                continue
            out.append((rollnumber, position, record, self._rollcall(chamber, congress, rollnumber)))
        return out


def icpsr_number(cluster):
    """The bare ICPSR integer for a resolved cluster, or ``None``. Voteview keys on it."""
    for member in cluster:
        if member.startswith('icpsr:'):
            try:
                return int(member.split(':', 1)[1])
            except ValueError:
                return None
    return None


def congress_in_window(index, cluster, window, *, known_at=None):
    """``(chamber_letter, congress)`` published for this agent over ``window``, or ``None``.

    Read from voteview's own per-congress observation rows, which carry ``dimensions.chamber``
    and ``dimensions.congress`` with the congress's validity interval. Nothing is inferred
    from the calendar.
    """
    start, end = (window or (None, None))
    best = None
    for record in index.observations(cluster, 'dw_nominate_dim1', known_at=known_at, limit=40):
        dimensions = record.get('dimensions') or {}
        chamber = str(dimensions.get('chamber') or '')[:1].upper()
        congress = dimensions.get('congress')
        if not chamber or not congress:
            continue
        valid_from, valid_to = _parse_time(record.get('valid_from')), _parse_time(record.get('valid_to'))
        if end is not None and valid_from is not None and valid_from >= end:
            continue
        if start is not None and valid_to is not None and valid_to <= start:
            continue
        if best is None or (valid_from is not None and best[2] is not None and valid_from > best[2]):
            best = (chamber, int(congress), valid_from)
    return (best[0], best[1]) if best else None


#: Attributes projected out of a perceived entity's published record, per ``entity_type``.
#: This is the declared, hand-written list; an attribute not named here is not perceived.
ATTRIBUTES = {
    'law': (('measure_status', 'measure_status'), ('policy_area', 'policy_area'),
            ('origin_chamber', 'origin_chamber')),
    'institution': (('chamber', 'chamber'), ('committee_type', 'committee_type')),
    'role': (('role_type', 'role_type'), ('jurisdiction_code', 'jurisdiction')),
    'person': (),
    'organization': (('committee_type', 'committee_type'),),
}

#: How a published latest action maps onto a stage, most advanced first. Declared, not fitted.
ADVANCE = (
    ('enacted', ('became public law', 'signed by president', 'public law no')),
    ('failed', ('failed of passage', 'motion to table agreed', 'rejected by', 'vetoed', 'cloture not invoked')),
    ('passed_chamber', ('passed senate', 'passed house', 'passed/agreed to', 'agreed to in senate',
                        'agreed to in house', 'resolution agreed to')),
    ('reported', ('reported by', 'ordered to be reported', 'placed on ', 'committee consideration')),
    ('referred', ('referred to the', 'read twice and referred', 'referred to committee')),
)
#: Stages worth remembering as an episode: the measure's fate changed.
TERMINAL_STAGES = ('enacted', 'passed_chamber', 'failed')


def advance_state(record):
    """The stage a published measure has reached, or ``None`` when the record does not say.

    ``govinfo_billstatus`` writes ``measure_status`` as either a public-law citation or the
    literal ``"not enacted as of source update"``, so the status is read for *enactment only*
    and everything finer comes from ``latest_action.text``.
    """
    attributes = (record or {}).get('attributes') or {}
    status = str(attributes.get('measure_status') or '').strip().lower()
    action = attributes.get('latest_action')
    text = str((action or {}).get('text') if isinstance(action, dict) else action or '').strip().lower()
    if attributes.get('public_laws') or (status and not status.startswith('not enacted') and 'public law' in status):
        return 'enacted'
    if not text:
        return None if not status else 'pending'
    for state, needles in ADVANCE:
        if any(needle in text for needle in needles):
            return state
    return 'pending'


# --------------------------------------------------------------------------- percepts


@dataclass(frozen=True)
class Percept:
    """One thing the agent can see this tick, with the published record that says so."""

    subject: str
    predicate: str
    object: object
    salience: float
    dataset: str
    stage: str
    version: str
    record_id: str
    observed_at: object = None
    valid_from: object = None
    valid_to: object = None
    episode: str = None
    text: str = ''
    standing: bool = True

    @property
    def source(self):
        """The evidence ``Ref``: the exact published dataset version this came from."""
        return tc.Ref('dataset:%s/%s@%s' % (self.dataset, self.stage, str(self.version)[:12]))

    def claim(self, scope=None):
        obj = tc.Ref(self.object) if _is_entity_id(self.object) else self.object
        return tc.Claim(tc.Ref(self.subject), self.predicate, obj, scope=scope)

    def evidence(self, method='published'):
        return tc.Evidence(self.source, self.observed_at or _dt.datetime.now(UTC),
                           locator=self.record_id, method='%s:%s' % (method, self.dataset))


@dataclass
class Perception:
    """What reached working memory this tick, and what the bound kept out."""

    percepts: tuple = ()
    events: tuple = ()
    considered: int = 0
    generated: int = 0
    dropped: int = 0
    window: tuple = None
    horizon_role: str = ''
    cap: int = 0
    predicates_seen: tuple = ()

    @property
    def bound(self):
        """The perception bound, as numbers: rows read, percepts formed, kept, dropped."""
        return {'index_rows': self.considered, 'generated': self.generated, 'kept': len(self.percepts),
                'dropped': self.dropped, 'cap': self.cap, 'role': self.horizon_role,
                'predicates': list(self.predicates_seen)}


def _label(record, fallback):
    if record and record.get('label'):
        return str(record['label'])
    return fallback


def _acted_within(record, start, end):
    """Did the published ``latest_action`` on this measure fall inside the window?

    With no window at all, everything counts; with no published action date, nothing does, so a
    quiet window never manufactures an event out of a state that has held for years.
    """
    if start is None and end is None:
        return True
    action = (record or {}).get('attributes', {}).get('latest_action')
    when = _parse_time(action.get('date') if isinstance(action, dict) else None)
    if when is None:
        return False
    return (start is None or when >= start) and (end is None or when < end)


def _recency(valid_from, now, decay_days):
    moment = _parse_time(valid_from)
    if moment is None or now is None:
        return 0.6
    days = max(0.0, (now - moment).total_seconds() / 86400.0)
    import math
    return math.exp(-days / max(1.0, decay_days))


def perceive(index, grounding, *, window=None, known_at=None, now=None, seen=()):
    """Collect, rank and cap what the agent can see. This is the perception bound in code.

    ``window`` is a half-open ``(start, end)`` on published ``valid_from``: dated assertions
    inside it are this tick's *events*, everything undated is standing condition. ``seen`` is
    the set of object ids already in the agent's store, used for the novelty term.
    """
    horizon = grounding.horizon
    ids = grounding.cluster if horizon.resolved else (grounding.entity_id,)
    now = now or (window[1] if window else None) or _dt.datetime.now(UTC)
    start, end = (window or (None, None))
    candidates, considered, predicates_seen = [], 0, set()
    budget = horizon.edge_scan

    for spec in horizon.specs:
        if budget <= 0:
            break
        rows = index.edges(ids, spec.index_predicate, direction=spec.direction,
                           since=start if spec.dated else None,
                           until=end if spec.dated else None,
                           known_at=known_at, limit=min(spec.caps, budget))
        if not rows and spec.dated:
            # nothing inside the window: the standing form of the same predicate is still
            # the agent's situation (which committee it sits on, which office it holds).
            rows = index.edges(ids, spec.index_predicate, direction=spec.direction,
                               known_at=known_at, limit=min(4, budget))
            in_window = False
        else:
            in_window = spec.dated
        budget -= len(rows)
        considered += len(rows)
        if rows:
            predicates_seen.add(spec.index_predicate)
        for row in rows:
            other = row['object'] if spec.direction == 'out' else row['subject']
            record = index.entity(other, known_at=known_at)
            provenance = _edge_provenance(index, row)
            novelty = 1.0 if other not in seen else 0.45
            salience = spec.salience * novelty * (_recency(row['valid_from'], now, horizon.decay_days)
                                                 if spec.dated else 0.8)
            stage = advance_state(record)
            episode = spec.episode if in_window else None
            if in_window:
                salience *= horizon.event_boost  # what just happened outranks what has long been true
            # A measure the agent put its name to changing state is its own event, dated by the
            # published latest action rather than by when the agent signed on: a bill cosponsored
            # last year and enacted this quarter is *this* quarter's news.
            if stage in TERMINAL_STAGES and _acted_within(record, start, end):
                salience = max(salience, spec.salience * novelty) * horizon.event_boost
                episode = 'measure_' + stage
            candidates.append(Percept(
                subject=grounding.entity_id, predicate=spec.predicate, object=other,
                salience=round(salience, 5), observed_at=_parse_time(row['observed_at']),
                valid_from=_parse_time(row['valid_from']), valid_to=_parse_time(row['valid_to']),
                episode=episode,
                text='%s %s%s' % (spec.predicate.replace('_', ' '), _label(record, other),
                                  ' (%s)' % stage if stage else ''),
                standing=not spec.dated, **provenance))
            for predicate, value, weight in _attribute_percepts(record):
                candidates.append(Percept(
                    subject=other, predicate=predicate, object=value,
                    salience=round(salience * weight, 5), observed_at=_parse_time(record['observed_at'])
                    if record.get('observed_at') else None,
                    dataset=record['_provenance']['dataset'], stage=record['_provenance']['stage'],
                    version=record['_provenance']['version'], record_id=record['_provenance']['record_id'],
                    episode=None, text='%s %s %s' % (_label(record, other), predicate.replace('_', ' '), value),
                    standing=True))

    for metric in horizon.metrics:
        for record in index.observations(ids, metric.metric, known_at=known_at, limit=metric.caps):
            considered += 1
            predicates_seen.add(metric.metric)
            candidates.append(Percept(
                subject=grounding.entity_id, predicate=metric.predicate, object=record.get('value'),
                salience=metric.salience, observed_at=_parse_time(record.get('observed_at')),
                valid_from=_parse_time(record.get('valid_from')), valid_to=_parse_time(record.get('valid_to')),
                dataset=record['_provenance']['dataset'], stage=record['_provenance']['stage'],
                version=record['_provenance']['version'], record_id=record['_provenance']['record_id'],
                episode=None, text='%s is %s' % (metric.predicate.replace('_', ' '), record.get('value')),
                standing=True))

    if horizon.roll_calls:
        considered_votes, vote_percepts = _roll_call_percepts(index, grounding, window, known_at, horizon)
        considered += considered_votes
        if vote_percepts:
            predicates_seen.add('roll_call_member_positions')
        candidates.extend(vote_percepts)

    # Deterministic ranking: salience, then predicate and record id, so two runs over the same
    # index give the same working memory.
    ranked = _dedupe(sorted(candidates, key=lambda p: (-p.salience, p.predicate, str(p.object), p.record_id)))
    kept = _select(ranked, horizon)
    return Perception(percepts=tuple(kept), events=tuple(p for p in kept if p.episode),
                      considered=considered, generated=len(ranked), dropped=max(0, len(ranked) - len(kept)),
                      window=window, horizon_role=horizon.role, cap=horizon.per_tick,
                      predicates_seen=tuple(sorted(predicates_seen)))


def vote_outcome(position, result):
    """Did this member's position carry, or was it rebuffed? ``None`` if the result does not say.

    Read from the published ``vote_result`` text and the member's own position. The mapping is
    declared here, not fitted: a yea on something that carried prevailed, a nay on something
    that carried was rebuffed, and either way a member who did not vote was absent.
    """
    text = str(result or '').lower()
    if not text:
        return None
    carried = any(word in text for word in ('agreed to', 'passed', 'confirmed', 'adopted', 'sustained'))
    failed = any(word in text for word in ('rejected', 'failed', 'not agreed', 'not sustained', 'not invoked'))
    if not carried and not failed:
        return None
    if position == 'not_voting':
        return 'absent'
    if (position == 'yea' and carried) or (position == 'nay' and failed):
        return 'prevailed'
    return 'rebuffed'


def _roll_call_percepts(index, grounding, window, known_at, horizon):
    number = icpsr_number(grounding.cluster)
    if number is None:
        return 0, []
    seat = congress_in_window(index, grounding.cluster, window, known_at=known_at)
    if seat is None:
        return 0, []
    chamber, congress = seat
    source = grounding.roll_calls or RollCalls(index, scan=horizon.roll_call_scan)
    grounding.roll_calls = source
    percepts, considered = [], 0
    for rollnumber, position, positions, rollcall in source.votes(
            chamber, congress, number, window=window, limit=horizon.roll_call_limit):
        considered += 1
        provenance = positions['_provenance']
        base = {'dataset': provenance['dataset'], 'stage': provenance['stage'],
                'version': provenance['version'], 'record_id': provenance['record_id']}
        attributes = (rollcall or {}).get('attributes') or {}
        occurred = _parse_time((positions.get('attributes') or {}).get('occurred_at') or positions.get('occurred_at'))
        reference = 'congress:rollcall:%s%d-%d' % (chamber, congress, rollnumber)
        outcome = vote_outcome(position, attributes.get('vote_result'))
        percepts.append(Percept(
            subject=grounding.entity_id, predicate='voted', object=(reference, position),
            salience=0.9 if position != 'not_voting' else 0.35,
            observed_at=_parse_time(positions.get('observed_at')), valid_from=occurred,
            episode='vote', standing=False, text='voted %s on %s' % (position, attributes.get('vote_question') or reference),
            **base))
        if outcome:
            percepts.append(Percept(
                subject=grounding.entity_id, predicate='vote_outcome', object=(reference, outcome),
                salience=0.95 if outcome != 'absent' else 0.3, observed_at=_parse_time(positions.get('observed_at')),
                valid_from=occurred, episode=None, standing=False,
                text='%s on %s' % (outcome, attributes.get('vote_question') or reference), **base))
        if rollcall is not None:
            rollcall_provenance = rollcall['_provenance']
            for key, predicate in (('vote_question', 'question'), ('vote_result', 'result'), ('bill_number', 'measure')):
                value = attributes.get(key)
                if value:
                    percepts.append(Percept(
                        subject=reference, predicate=predicate, object=str(value), salience=0.3,
                        observed_at=_parse_time(rollcall.get('observed_at')), valid_from=occurred,
                        dataset=rollcall_provenance['dataset'], stage=rollcall_provenance['stage'],
                        version=rollcall_provenance['version'], record_id=rollcall_provenance['record_id'],
                        text='%s %s %s' % (reference, predicate, value), standing=True))
    return considered, percepts


def _select(ranked, horizon):
    """Fill working memory by salience, but let no single channel own more than its quota.

    Without this a session with 900 roll calls fills every slot with roll calls and the agent
    stops seeing its committees or its money. The quota is a share of ``per_tick``, so working
    memory stays mixed by construction rather than by luck.
    """
    quota = max(2, int(horizon.per_tick * horizon.per_predicate_share))
    kept, counts, overflow = [], {}, []
    for percept in ranked:
        if len(kept) >= horizon.per_tick:
            break
        if counts.get(percept.predicate, 0) >= quota:
            overflow.append(percept)
            continue
        counts[percept.predicate] = counts.get(percept.predicate, 0) + 1
        kept.append(percept)
    for percept in overflow:  # spare capacity goes back to the loudest channel
        if len(kept) >= horizon.per_tick:
            break
        kept.append(percept)
    return sorted(kept, key=lambda p: (-p.salience, p.predicate, str(p.object), p.record_id))


def _dedupe(percepts):
    out, seen = [], set()
    for percept in percepts:
        key = (percept.subject, percept.predicate, str(percept.object))
        if key in seen:
            continue
        seen.add(key)
        out.append(percept)
    return out


def _edge_provenance(index, row):
    record = index.record_at(row['record_rowid']) if row.get('record_rowid') else None
    if record is None:
        return {'dataset': 'unknown', 'stage': 'unknown', 'version': 'unknown',
                'record_id': 'edge:%s' % row.get('record_rowid')}
    provenance = record['_provenance']
    return {'dataset': provenance['dataset'], 'stage': provenance['stage'],
            'version': provenance['version'], 'record_id': provenance['record_id']}


def _attribute_percepts(record):
    if not record:
        return []
    attributes = record.get('attributes') or {}
    out = []
    for key, predicate in ATTRIBUTES.get(record.get('entity_type'), ()):
        value = attributes.get(key)
        if value not in (None, '', [], {}):
            out.append((predicate, str(value), 0.6))
    state = advance_state(record)
    if state is not None:
        # Always below the percept it hangs off: the salient thing is the agent's own act,
        # not the attribute. The terminal-stage boost is applied to that act instead.
        out.append(('measure_stage', state, 0.55 if state in TERMINAL_STAGES else 0.3))
    return out


# --------------------------------------------------------------------------- seeding


#: The facets a person agent is seeded with. Each names the published predicates or metrics
#: that could supply it; when none does, the facet is reported ``Unknown`` and no claim is made.
PERSON_FACETS = {
    'identity': ('entity record',),
    'office': ('holds_role', 'congressional_service'),
    'party': ('party_affiliation', 'holds_role'),
    'jurisdiction': ('holds_role',),
    'committees': ('committee_member',),
    'legislative_record': ('sponsored_measure', 'cosponsored_measure'),
    'ideal_point': ('dw_nominate_dim1', 'dw_nominate_dim2'),
    'campaign_support': ('supports_candidate', 'authorized_committee_of'),
    # fec publishes supports_candidate as a per-cycle boolean and all three
    # fec_individual_contributions* datasets contributed zero records to this index, so the
    # amount of any contribution is Unknown here no matter who the agent is.
    'contribution_amounts': ('individual_contributions_amount',),
    'roll_call_votes': ('roll_call_member_positions',),
    'lobbying_contacts': ('contacted_government_entity',),
}


@dataclass
class SeedReport:
    """What the published record supported, and what stayed Unknown."""

    entity_id: str
    canonical_id: str
    cluster: tuple = ()
    claims: int = 0
    facets: dict = field(default_factory=dict)
    datasets: tuple = ()
    record_ids: tuple = ()
    label: str = None
    #: What a pinned ``known_at`` meant here: which date the horizon filtered on, and under
    #: which policy. ``None`` when the caller pinned no knowledge horizon.
    publication: dict = None

    @property
    def unknown(self):
        return tuple(sorted(name for name, state in self.facets.items() if state == 'Unknown'))

    @property
    def seeded(self):
        return tuple(sorted(name for name, state in self.facets.items() if state != 'Unknown'))

    def to_json(self):
        return {'entity_id': self.entity_id, 'canonical_id': self.canonical_id, 'label': self.label,
                'cluster': list(self.cluster), 'claims': self.claims, 'facets': dict(sorted(self.facets.items())),
                'unknown': list(self.unknown), 'datasets': list(self.datasets),
                'records_cited': len(self.record_ids), 'publication': self.publication}


@dataclass
class Grounding:
    """An agent's binding to a real entity: its ids, its horizon, and what it was seeded with."""

    entity_id: str
    canonical_id: str
    cluster: tuple
    horizon: Horizon
    me: object = None
    report: SeedReport = None
    index: object = None
    roll_calls: object = None

    @property
    def label(self):
        return self.report.label if self.report else None

    def unknown(self, facet):
        """``tc.Unknown`` when no published record supports ``facet``; ``None`` when one does."""
        state = (self.report.facets if self.report else {}).get(facet)
        if state is None:
            return tc.Unknown('not_a_declared_facet', facet)
        if state == 'Unknown':
            return tc.Unknown('no_published_record',
                              '%s publishes nothing for %s in this index' % (self.entity_id, facet))
        return None

    def graph_facts(self, predicate, *, direction='out', limit=20, known_at=None):
        """What the *graph* says, as distinct from what the agent believes.

        The contract requires the two to be separately queryable; this is the graph side.
        """
        if self.index is None:
            raise ValueError('this grounding has no open index')
        return self.index.edges(self.cluster, predicate, direction=direction, known_at=known_at, limit=limit)

    def divergence(self, store, predicate, *, index_predicate=None, known_at=None, limit=200):
        """Claims the agent holds on ``predicate`` that no live published edge supports.

        Divergence between belief and record is where error, rumour and ideology live. It is
        reported, never silently corrected.
        """
        spec = None
        for candidate in self.horizon.specs:
            if candidate.predicate == predicate:
                spec = candidate
                break
        index_predicate = index_predicate or (spec.index_predicate if spec else predicate)
        direction = spec.direction if spec else 'out'
        published = {row['object'] if direction == 'out' else row['subject']
                     for row in self.graph_facts(index_predicate, direction=direction,
                                                 limit=limit, known_at=known_at)}
        held = []
        for record in store.claims(self.me, predicate):
            obj = getattr(record.claim.object, 'id', record.claim.object)
            if str(obj) not in published:
                held.append(record)
        return held


def ground(index, entity_id, *, horizon=LEGISLATOR, known_at=None):
    """Resolve ``entity_id`` in the index and build its :class:`Grounding` (no store yet)."""
    from ..model import identifier
    identifier(entity_id)
    cluster = index.cluster(entity_id) if horizon.resolved else (entity_id,)
    record = index.entity(entity_id, known_at=known_at)
    report = SeedReport(entity_id=entity_id, canonical_id=index.canonical(entity_id), cluster=cluster,
                        label=_label(record, None),
                        publication=dict(index.publication_policy, known_at=_time_key(known_at)) if known_at else None)
    return Grounding(entity_id=entity_id, canonical_id=report.canonical_id, cluster=cluster,
                     horizon=horizon, me=tc.Ref(entity_id), report=report, index=index)


def seed_store(store, index, entity_id, *, horizon=LEGISLATOR, known_at=None, per_predicate=40,
               facets=PERSON_FACETS):
    """Seed ``store`` from published records only. Returns ``(grounding, SeedReport)``.

    Every claim carries an ``Evidence`` naming the dataset, stage, version and published
    record id. A facet with no supporting record produces **no claim** and is marked
    ``Unknown`` in the report.
    """
    grounding = ground(index, entity_id, horizon=horizon, known_at=known_at)
    report = grounding.report
    me = grounding.me
    edits, datasets, record_ids = [], set(), []
    supplied = set()

    def tell(percept, method='published'):
        edits.append(tc.Tell(percept.claim(), (percept.evidence(method),)))
        datasets.add(percept.dataset)
        record_ids.append(percept.record_id)

    entity_record = index.entity(entity_id, known_at=known_at)
    if entity_record is not None:
        provenance = entity_record['_provenance']
        base = {'dataset': provenance['dataset'], 'stage': provenance['stage'],
                'version': provenance['version'], 'record_id': provenance['record_id'],
                'observed_at': _parse_time(entity_record.get('observed_at'))}
        if entity_record.get('label'):
            tell(Percept(entity_id, 'name', str(entity_record['label']), 1.0, **base))
            supplied.add('identity')
        if entity_record.get('entity_type'):
            tell(Percept(entity_id, 'role', str(entity_record['entity_type']), 0.9, **base))
        for key in ('birthday', 'gender'):
            value = (entity_record.get('attributes') or {}).get(key)
            if value:
                tell(Percept(entity_id, key, str(value), 0.6, **base))
    for other in grounding.cluster[1:]:
        if entity_record is not None:
            tell(Percept(entity_id, 'same_as', other, 0.5, **base))

    # Standing structure, newest first, bounded per predicate.
    for spec in horizon.specs:
        rows = index.edges(grounding.cluster, spec.index_predicate, direction=spec.direction,
                           known_at=known_at, limit=min(spec.caps, per_predicate))
        if not rows:
            continue
        for row in rows:
            other = row['object'] if spec.direction == 'out' else row['subject']
            provenance = _edge_provenance(index, row)
            tell(Percept(subject=entity_id, predicate=spec.predicate, object=other, salience=spec.salience,
                         observed_at=_parse_time(row['observed_at']), valid_from=_parse_time(row['valid_from']),
                         valid_to=_parse_time(row['valid_to']), **provenance))
            record = index.entity(other, known_at=known_at)
            for predicate, value, _weight in _attribute_percepts(record):
                tell(Percept(subject=other, predicate=predicate, object=value, salience=0.5,
                             observed_at=_parse_time(record.get('observed_at')),
                             dataset=record['_provenance']['dataset'], stage=record['_provenance']['stage'],
                             version=record['_provenance']['version'],
                             record_id=record['_provenance']['record_id']))
        for facet, sources in facets.items():
            if spec.index_predicate in sources:
                supplied.add(facet)
        if spec.index_predicate == 'holds_role':
            supplied.update(_role_facets(index, rows, entity_id, tell, known_at))

    for metric in horizon.metrics:
        for record in index.observations(grounding.cluster, metric.metric, known_at=known_at, limit=metric.caps):
            tell(Percept(subject=entity_id, predicate=metric.predicate, object=record.get('value'),
                         salience=metric.salience, observed_at=_parse_time(record.get('observed_at')),
                         valid_from=_parse_time(record.get('valid_from')), valid_to=_parse_time(record.get('valid_to')),
                         dataset=record['_provenance']['dataset'], stage=record['_provenance']['stage'],
                         version=record['_provenance']['version'],
                         record_id=record['_provenance']['record_id']))
            for facet, sources in facets.items():
                if metric.metric in sources:
                    supplied.add(facet)

    if horizon.roll_calls and 'roll_call_votes' in facets:
        _considered, votes = _roll_call_percepts(index, grounding, None, known_at, horizon)
        for percept in votes:
            tell(percept)
        if votes:
            supplied.add('roll_call_votes')

    if edits:
        commit = store.apply(tc.Patch(tuple(edits), store.revision))
        report.claims = len(commit.added)
    report.facets = {name: ('published' if name in supplied else 'Unknown') for name in facets}
    report.datasets = tuple(sorted(datasets))
    report.record_ids = tuple(dict.fromkeys(record_ids))
    return grounding, report


def _role_facets(index, rows, entity_id, tell, known_at):
    """Party and jurisdiction are published inside the role record, not as their own edge."""
    supplied = set()
    for row in rows[:4]:
        record = index.entity(row['object'], known_at=known_at)
        if not record:
            continue
        attributes = record.get('attributes') or {}
        term = attributes.get('source_term') or {}
        provenance = record['_provenance']
        base = {'dataset': provenance['dataset'], 'stage': provenance['stage'], 'version': provenance['version'],
                'record_id': provenance['record_id'], 'observed_at': _parse_time(record.get('observed_at')),
                'valid_from': _parse_time(row['valid_from']), 'valid_to': _parse_time(row['valid_to'])}
        if term.get('party'):
            tell(Percept(entity_id, 'party', str(term['party']), 0.7, **base))
            supplied.add('party')
        if attributes.get('jurisdiction_code'):
            tell(Percept(entity_id, 'represents', str(attributes['jurisdiction_code']), 0.7, **base))
            supplied.add('jurisdiction')
        if attributes.get('role_type'):
            tell(Percept(entity_id, 'chamber', 'senate' if attributes['role_type'] == 'sen' else 'house', 0.8, **base))
            supplied.add('office')
    return supplied
