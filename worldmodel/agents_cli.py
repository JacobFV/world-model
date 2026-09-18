"""``wm agent-inspect`` and ``wm agent-explain``: what a grounded agent believes, and why.

Both commands are read-only. They ground an agent on the unified-graph index, optionally tick
it over a window of real time, and print JSON. ``agent-explain`` prints the support for a
claim all the way down to the published record ids that carry it, which is the whole point of
putting a claim store under these agents rather than a state vector.

The cognitive substrate is an optional extra; without it these commands fail with the install
hint and nothing else in ``wm`` is affected.
"""
import datetime as _dt
from pathlib import Path

from .agents import INSTALL_HINT, tensorcode_available

COMMANDS = {'agent-inspect', 'agent-explain'}
UTC = _dt.timezone.utc
DEFAULT_ROLE = 'legislator'


def add_commands(sub):
    for name, help_text in (
            ('agent-inspect', 'Show a grounded agent: claims, affect readings, motif, episodes and intentions'),
            ('agent-explain', 'Show why a grounded agent believes something, down to the published record ids')):
        command = sub.add_parser(name, help=help_text)
        command.add_argument('entity', help='Entity ID from the unified graph, e.g. bioguide:K000367')
        if name == 'agent-explain':
            command.add_argument('claim', help='A claim id, a predicate ("decided"), or "predicate object-substring"')
            command.add_argument('--depth', type=int, default=8, help='How far down the support chain to print')
        command.add_argument('--index', type=Path, help='Graph index path (default <data>/world_evidence/index.sqlite)')
        command.add_argument('--role', default=DEFAULT_ROLE, choices=sorted(ROLES), help='Perception horizon to use')
        # One tick by default: a freshly seeded agent has beliefs but has not yet appraised
        # anything, so its affect readings, motif, episodes and intentions would all be empty.
        command.add_argument('--ticks', type=int, default=1, help='Tick the agent this many windows before reporting')
        command.add_argument('--window-days', type=int, default=90, help='Length of one tick window, in days')
        command.add_argument('--until', help='End of the last window (ISO date); default today')
        command.add_argument('--seed', type=int, default=0, help='Reproducible per-agent perceptual axes')
        command.add_argument('--cache-mb', type=int, default=64, help='SQLite page cache for the read-only connection')
        if name == 'agent-inspect':
            command.add_argument('--claims', type=int, default=40, help='Most recent claims to list')
            command.add_argument('--memories', type=int, default=10, help='Episodes to list')
            command.add_argument('--diverge', help='Also report claims on this predicate with no live published support')


#: The horizons a role name selects. ``firm`` and ``institution`` are the sibling agent kinds
#: from the design contract; they register their own horizons when their modules land.
ROLES = {'legislator': ('grounding', 'LEGISLATOR')}


def _horizon(role):
    import importlib
    module_name, attribute = ROLES[role]
    return getattr(importlib.import_module('.agents.' + module_name, __package__), attribute)


def _windows(until, count, days):
    end = _dt.datetime.fromisoformat(until).replace(tzinfo=UTC) if until else _dt.datetime.now(UTC)
    span = _dt.timedelta(days=days)
    return [(end - span * (count - i), end - span * (count - i - 1)) for i in range(count)]


def execute(args, catalog, store, project, reference):
    if not tensorcode_available():
        raise RuntimeError(INSTALL_HINT)
    from .agents.grounding import EvidenceIndex, default_index_path
    from .agents.person import Person

    index_path = args.index or default_index_path(store.root)
    index = EvidenceIndex(index_path, cache_mb=args.cache_mb)
    try:
        agent = Person.ground(index, args.entity, horizon=_horizon(args.role), seed=args.seed)
        ticks = [agent.tick(window).to_json()
                 for window in _windows(args.until, max(0, args.ticks), args.window_days)]
        if args.command == 'agent-explain':
            found = agent.find(args.claim)
            if not found:
                raise ValueError('No live claim matches %r; try `wm agent-inspect %s` to see what it believes'
                                 % (args.claim, args.entity))
            return {'entity_id': args.entity, 'label': agent.grounding.label, 'index': str(index_path),
                    'query': args.claim, 'ticks': ticks,
                    'matches': [{'claim': record.id,
                                 'statement': '%s %s %s' % (record.claim.subject, record.claim.predicate,
                                                            _plain(record.claim.object)),
                                 'scope': str(record.claim.scope) if record.claim.scope else None,
                                 'why': agent.explain(record, depth=args.depth),
                                 'records': _records_behind(record, agent)}
                                for record in found]}
        result = agent.inspect(claims=args.claims, memories=args.memories)
        result['index'] = str(index_path)
        result['ticks'] = ticks
        if getattr(args, 'diverge', None):
            result['divergence'] = [
                {'claim': record.id, 'statement': '%s %s %s' % (record.claim.subject, record.claim.predicate,
                                                                _plain(record.claim.object))}
                for record in agent.grounding.divergence(agent.store, args.diverge)]
        return result
    finally:
        index.close()


def _records_behind(record, agent, depth=8):
    """Every published record id the support chain of ``record`` bottoms out in."""
    out, stack, seen = [], [(record.id, 0)], set()
    while stack:
        claim_id, level = stack.pop()
        if claim_id in seen or level > depth:
            continue
        seen.add(claim_id)
        held = agent.store._claims.get(claim_id)
        if held is None:
            continue
        for evidence in held.evidence:
            if evidence.derived_from:
                stack.extend((premise, level + 1) for premise in evidence.derived_from)
            elif evidence.locator:
                # A percept snapshot has one source frame, so its locator names the record and
                # the dataset version together: "<record id> in <dataset:name/stage@version>".
                record_id, _, dataset_version = evidence.locator.partition(' in ')
                out.append({'dataset_version': dataset_version or str(evidence.source),
                            'record_id': record_id, 'method': evidence.method})
    unique = {}
    for entry in out:
        unique[(entry['dataset_version'], entry['record_id'])] = entry
    return sorted(unique.values(), key=lambda e: (e['dataset_version'], e['record_id']))


def _plain(value):
    if isinstance(value, (tuple, list)):
        return [_plain(v) for v in value]
    return value if isinstance(value, (str, int, float, bool)) or value is None else str(value)
