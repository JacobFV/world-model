"""One explicit, documented mechanism for simulation work and retention limits.

Every kernel size/work cap is a named field of :class:`Limits`. Defaults are
sized for national-scale synthetic runs (for example a coupled economy with
100k firms and 1M households over 3,650 daily steps, field grids of ~10M cells
with ~40M edges, 1M-actor/10M-obligation exposure networks and ~10M-edge
routing graphs) while still rejecting accidental unbounded work.

Resolution order, later wins::

    Limits() defaults
    < WORLD_MODEL_LIMITS environment variable (JSON object, or @path/to/file.json)
    < use_limits({...}) context (the CLI --limits option uses this)
    < limits=... argument passed to an individual kernel call

Lowering a limit is always allowed and still rejects oversized work. A rejected
request raises :class:`LimitExceeded` (a ``ValueError``) naming the limit and how
to raise it. Limits bound work and retained output; they are not guarantees that
a machine has enough memory or time for any run below them.
"""
from contextlib import contextmanager
import contextvars
from dataclasses import dataclass, field, fields, replace
import json
import os
from pathlib import Path

ENV_VAR = 'WORLD_MODEL_LIMITS'

KiB, MiB, GiB, TiB = 1024, 1024 ** 2, 1024 ** 3, 1024 ** 4


def _limit(default, doc):
    return field(default=default, metadata={'doc': doc})


class LimitExceeded(ValueError):
    """Requested work or retained output is above a named, raisable limit."""

    def __init__(self, name, value, maximum, label=None):
        self.limit = name
        self.value = value
        self.maximum = maximum
        prefix = label or name
        super().__init__(
            f'{prefix}: {value} exceeds limit {name}={maximum}. Raise it explicitly with '
            f'limits={{"{name}": N}} on the call, {ENV_VAR}=\'{{"{name}": N}}\' or the CLI '
            f'--limits \'{{"{name}": N}}\' option.')


@dataclass(frozen=True)
class Limits:
    # --- energy/bank economy (economy.py, economy_processes.py) -------------
    economy_max_days: int = _limit(36_500, 'Daily horizon of the cash-funded energy economy and its replay adapter.')
    economy_max_businesses: int = _limit(1_000_000, 'Businesses in the cash-funded energy economy.')
    economy_max_business_days: int = _limit(1_000_000_000, 'Days x businesses simulated by one energy-economy run.')
    economy_max_retained_business_days: int = _limit(5_000_000, 'Business-day snapshot rows retained with history="full" (use every_n/summary above this).')
    economy_max_replay_firm_days: int = _limit(1_000_000_000, 'Cumulative firm-days recomputed by the replaying economy process adapter.')
    bond_max_periods: int = _limit(1_000_000, 'Coupon periods valued by value_bond.')
    # --- coupled commercial-bank economy (coupled_economy.py) --------------
    coupled_max_firms: int = _limit(250_000, 'Firms in a coupled economy state.')
    coupled_max_households: int = _limit(2_500_000, 'Households in a coupled economy state.')
    coupled_max_steps: int = _limit(36_500, 'Daily steps (horizon) of a coupled economy, including policy/shock sequences.')
    coupled_max_step_transactions: int = _limit(50_000_000, 'Reserved ledger transactions (slots) in one coupled-economy step.')
    coupled_max_ledger_cells: int = _limit(20_000_000, 'Ledger balance cells (reserves, equity, deposits, loans) in a coupled economy.')
    coupled_max_retained_postings: int = _limit(20_000_000, 'Journal postings retained across the history with history="full".')
    coupled_max_history_actor_steps: int = _limit(50_000_000, 'Retained (step x actors) per-firm history rows with history="full".')
    economy_max_quantity: int = _limit(1_000_000_000, 'Integer goods/labor quantities (production, inventory, purchases, capacities).')
    # --- exact-cent banking ledger (banking.py) -----------------------------
    banking_max_banks: int = _limit(10_000, 'Banks in one ledger.')
    banking_max_transactions: int = _limit(50_000_000, 'Transactions applied by one simulate_banking call.')
    banking_max_audit_cells: int = _limit(50_000_000, 'Balance cells copied into before/after audit snapshots with audit="full".')
    # --- obligation exposure clearing (exposure.py) -------------------------
    exposure_max_entities: int = _limit(2_000_000, 'Entities in an exposure stress network.')
    exposure_max_obligations: int = _limit(20_000_000, 'Obligations in an exposure stress network.')
    exposure_max_clearing_iterations: int = _limit(100_000, 'Fixed-point clearing iterations per scenario.')
    exposure_max_clearing_work: int = _limit(20_000_000_000, 'Clearing work: iterations x (obligations + entities), summed over baseline and stress.')
    # --- conservative fields and spatial storage ----------------------------
    field_max_cells: int = _limit(20_000_000, 'Support cells in a field world, spatial import, selection or timeline domain.')
    field_max_fields: int = _limit(1_000, 'Named fields in one field world or spatial store.')
    field_max_values: int = _limit(200_000_000, 'Stored field components: cells x fields x vector width.')
    field_max_edges: int = _limit(100_000_000, 'Topology edges in a field world, spatial import or selection.')
    field_max_claims: int = _limit(1_000_000, 'Territory claims.')
    field_max_claim_memberships: int = _limit(50_000_000, 'Claim-cell membership rows.')
    field_max_substeps: int = _limit(10_000_000, 'Ceiling for requested numerical substeps in one evolution or process call.')
    field_max_work: int = _limit(1_000_000_000_000, 'Ceiling for requested substeps x (cells + edges) x field components.')
    field_max_projection_records: int = _limit(100_000_000, 'Ceiling for a field projection record limit.')
    field_max_input_bytes: int = _limit(64 * GiB, 'Canonical JSON size of a dict-based field world input.')
    timeline_max_frames: int = _limit(1_000_000, 'Frames (samples and event instants) in one spatial timeline.')
    timeline_max_snapshots: int = _limit(500_000_000, 'Snapshot rows retained by one spatial timeline.')
    timeline_max_output_bytes: int = _limit(16 * GiB, 'Estimated serialized timeline output.')
    timeline_max_request_bytes: int = _limit(256 * MiB, 'Canonical timeline request size.')
    timeline_max_lifecycle_operations: int = _limit(10_000_000, 'Dated lifecycle operations in one timeline.')
    spatial_max_query_rows: int = _limit(20_000_000, 'Ceiling for bbox/select/neighborhood/audit query limits.')
    spatial_max_candidates: int = _limit(20_000_000, 'Geometry query candidate rows and refinement candidate cells.')
    spatial_max_edge_rows: int = _limit(100_000_000, 'Edge rows read by neighborhood, incident-topology and split queries.')
    spatial_max_lifecycle_batch: int = _limit(1_000_000, 'Events in one lifecycle batch or dated timeline batch.')
    spatial_max_lifecycle_cells: int = _limit(1_000_000, 'Cells merged or children created by one lifecycle event.')
    spatial_max_row_bytes: int = _limit(1 * GiB, 'Canonical JSON payload of one stored spatial row (including audit rows).')
    spatial_max_refinement_parts: int = _limit(1_000_000, 'Children/sources of one explicit rectangle refinement or coarsening.')
    geometry_max_vertices: int = _limit(1_000_000, 'Vertices of one explicit polygon ring.')
    # --- composition and coupling contracts ---------------------------------
    composition_max_steps: int = _limit(1_000_000, 'Composition steps.')
    composition_max_supports: int = _limit(20_000_000, 'Spatial supports in a composition world.')
    composition_max_accounts: int = _limit(10_000_000, 'Accounts in a composition.')
    composition_max_transfers: int = _limit(1_000_000_000, 'Estimated coupled transfers across a composition.')
    composition_max_demand: int = _limit(1_000_000_000, 'Integer kg demand of one composition step.')
    composition_max_lifecycle_work: int = _limit(100_000_000_000, 'Actor lifecycle reconstruction work in a composition.')
    composition_max_output_bytes: int = _limit(16 * GiB, 'Serialized composition frames.')
    composition_max_config_bytes: int = _limit(1 * GiB, 'Canonical composition config size.')
    contracts_max_rows: int = _limit(200_000_000, 'Rows aggregated by aggregate_quantity.')
    contracts_max_observations: int = _limit(100_000_000, 'Timed observations scanned by select_timed_input.')
    contracts_max_transfers: int = _limit(1_000_000_000, 'Transfers retained by one CouplingLedger.')
    contracts_max_batch_transfers: int = _limit(100_000_000, 'Transfers in one CouplingLedger batch.')
    contracts_max_accounts: int = _limit(50_000_000, 'Accounts in one CouplingLedger.')
    contracts_max_interfaces: int = _limit(1_000_000, 'Interfaces (and quantities) in one CouplingLedger.')
    contracts_max_payload_bytes: int = _limit(1 * GiB, 'Canonical coupling schema or transfer batch size.')
    agent_max_payload_bytes: int = _limit(64 * MiB, 'Agent contract, request and output payload size.')
    lifecycle_max_entities: int = _limit(10_000_000, 'Actors in a lifecycle reconstruction.')
    lifecycle_max_events: int = _limit(100_000_000, 'Events in a lifecycle reconstruction.')
    # --- routing (transport.py) ---------------------------------------------
    transport_max_nodes: int = _limit(20_000_000, 'Nodes in a routed network.')
    transport_max_edges: int = _limit(50_000_000, 'Edges in a routed network.')
    transport_max_search_labels: int = _limit(1_000_000_000, 'Ceiling for requested max_expansions/max_labels.')
    transport_max_transfers: int = _limit(10_000_000, 'Dated transfer rules.')
    transport_max_windows: int = _limit(1_000_000, 'Closure/capacity windows per edge.')
    transport_max_departures: int = _limit(10_000_000, 'Scheduled departures per edge.')
    # --- materialization, environments, checkpoints and journals ------------
    materialize_max_points: int = _limit(100_000_000, 'Ceiling for request max_points.')
    materialize_max_calls: int = _limit(100_000_000, 'Ceiling for request max_calls and evaluator total call budgets.')
    materialize_max_interventions: int = _limit(10_000_000, 'Scheduled literal interventions.')
    materialize_max_lifecycle_work: int = _limit(100_000_000_000, 'Lifecycle reconstruction work before execution.')
    environment_max_ports: int = _limit(100_000, 'Declared environment actions/observations/reward terms.')
    environment_max_steps: int = _limit(10_000_000, 'Environment max_steps, rollout actions per episode and RL horizon.')
    environment_max_output_bytes: int = _limit(16 * GiB, 'Serialized materialization result per environment evaluation.')
    environment_max_action_bytes: int = _limit(64 * MiB, 'Serialized action payload per step.')
    checkpoint_max_json_bytes: int = _limit(4 * GiB, 'Single JSON checkpoint document size (use chunked checkpoints above this).')
    checkpoint_max_items: int = _limit(1_000_000_000, 'JSON-native values scanned when validating checkpoint payloads.')
    checkpoint_chunk_bytes: int = _limit(64 * MiB, 'Default chunk size of chunked checkpoints.')
    journal_max_payload_bytes: int = _limit(1 * GiB, 'Ceiling for an execution journal max_payload_bytes setting.')
    journal_max_checkpoint_bytes: int = _limit(64 * GiB, 'Ceiling for an execution journal max_checkpoint_bytes setting.')
    journal_max_storage_bytes: int = _limit(4 * TiB, 'Ceiling for an execution journal max_storage_bytes setting.')
    journal_max_records: int = _limit(10_000_000_000, 'Ceiling for an execution journal max_records setting.')
    journal_max_page_items: int = _limit(1_000_000, 'Items in one journal history/effects/reservations page or prune batch.')
    journal_max_page_bytes: int = _limit(16 * GiB, 'Bytes in one journal page.')
    rollout_max_episodes: int = _limit(1_000_000, 'Episodes in one parallel rollout batch.')
    rollout_max_workers: int = _limit(256, 'Concurrent rollout worker processes.')
    rollout_max_transitions: int = _limit(10_000_000_000, 'Ceiling for rollout max_transitions.')
    rollout_max_result_bytes: int = _limit(4 * GiB, 'Ceiling for per-episode max_result_bytes.')
    rollout_max_total_result_bytes: int = _limit(64 * GiB, 'Episodes x max_result_bytes.')
    rollout_max_request_bytes: int = _limit(4 * GiB, 'Serialized rollout request.')
    rollout_max_timeout_seconds: int = _limit(7 * 86_400, 'Ceiling for per-episode timeout_seconds.')
    rl_max_seeds: int = _limit(100_000_000, 'Training or evaluation seeds.')
    rl_max_transitions: int = _limit(100_000_000_000, 'Training plus evaluation transitions.')
    rl_max_actions: int = _limit(1_000_000, 'Discrete actions and their serialized size budget in KiB.')
    rl_max_observation_bytes: int = _limit(16 * MiB, 'Encoded observation key size.')
    rl_max_states: int = _limit(1_000_000_000, 'Tabular Q states.')
    rl_max_vector_environments: int = _limit(1_000_000, 'Environments in a VectorEnvironment.')
    spaces_max_choices: int = _limit(10_000_000, 'Categorical choices.')
    spaces_max_length: int = _limit(100_000_000, 'Array/vector length.')
    spaces_max_channels: int = _limit(1_000_000_000, 'Flattened structured-space channels.')
    spaces_max_properties: int = _limit(1_000_000, 'Object properties.')
    spaces_max_schema_bytes: int = _limit(1 * GiB, 'Canonical schema size.')
    scenario_max_transitions: int = _limit(100_000_000_000, 'Ceiling for scenario benchmark max_transitions.')
    scenario_max_scenarios: int = _limit(1_000_000, 'Scenarios in one policy benchmark.')
    scenario_max_policies: int = _limit(1_000_000, 'Policies in one policy benchmark.')
    scenario_max_evaluations: int = _limit(10_000_000_000, 'Ceiling for parameter ensemble max_evaluations.')
    scenario_max_parameter_sets: int = _limit(10_000_000, 'Parameter candidates in an ensemble.')
    scenario_max_observations: int = _limit(10_000_000, 'Observations or probes in an ensemble.')
    surface_max_rows: int = _limit(50_000_000, 'Input snapshot/record rows accepted by a rendered surface.')
    surface_max_panel_rows: int = _limit(1_000_000, 'Ceiling for table/plot/map panel row limits.')
    surface_max_graph_nodes: int = _limit(100_000, 'Ceiling for graph panel node limits.')
    surface_max_graph_edges: int = _limit(1_000_000, 'Edge references listed by a graph panel.')
    surface_max_html_bytes: int = _limit(1 * GiB, 'Rendered standalone HTML size.')
    surface_max_field_chars: int = _limit(10_000_000, 'Characters in one displayed field.')
    surface_max_input_bytes: int = _limit(16 * GiB, 'Serialized spatial surface input.')
    surface_max_frames: int = _limit(1_000_000, 'Spatial surface frames and lifecycle rows.')
    surface_max_cells: int = _limit(1_000_000, 'Ceiling for spatial surface max_cells/max_edges.')
    cli_max_input_bytes: int = _limit(4 * GiB, 'Local scenario/request file imported by a CLI command.')

    def __post_init__(self):
        for item in fields(self):
            value = getattr(self, item.name)
            if type(value) is not int or value < 1:
                raise ValueError(f'Limit {item.name} must be a positive integer')

    def override(self, overrides=None, **kwargs):
        """Return a copy with validated named overrides; unknown names fail."""
        values = {}
        if overrides is not None:
            if isinstance(overrides, Limits):
                return overrides if not kwargs else overrides.override(**kwargs)
            if not isinstance(overrides, dict):
                raise ValueError('Limit overrides must be a mapping of limit names to positive integers')
            values.update(overrides)
        values.update(kwargs)
        known = {item.name for item in fields(self)}
        unknown = sorted(set(values) - known)
        if unknown:
            raise ValueError(f'Unknown limit(s): {unknown}; see worldmodel.limits.describe_limits()')
        return replace(self, **values) if values else self

    def check(self, name, value, label=None):
        """Raise LimitExceeded when value is above the named limit; return value."""
        maximum = getattr(self, name)
        if value > maximum:
            raise LimitExceeded(name, value, maximum, label)
        return value

    def integer(self, name, value, label=None, minimum=1):
        """Validate an integer in minimum..limit (type/minimum errors stay ValueError)."""
        if type(value) is not int or value < minimum:
            raise ValueError(f'{label or name} must be an integer >= {minimum} (at most limit {name}={getattr(self, name)})')
        return self.check(name, value, label)

    def as_dict(self):
        return {item.name: getattr(self, item.name) for item in fields(self)}


DEFAULTS = Limits()
_context = contextvars.ContextVar('worldmodel_limits', default=None)
_env_cache = {}


def parse_limits(value):
    """Parse a JSON object string, a JSON file path, or '@path' into overrides."""
    if value is None or value == '':
        return {}
    if isinstance(value, (dict, Limits)):
        return value
    if not isinstance(value, (str, os.PathLike)):
        raise ValueError('Limits must be a JSON object, a path to one, or a mapping')
    text = os.fspath(value).strip()
    if text.startswith('@'):
        text = Path(text[1:]).read_text(encoding='utf-8')
    elif not text.startswith('{') and Path(text).is_file():
        text = Path(text).read_text(encoding='utf-8')
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as error:
        raise ValueError(f'Limits must be a JSON object: {error}') from error
    if not isinstance(parsed, dict):
        raise ValueError('Limits must be a JSON object of limit names to positive integers')
    return parsed


def _environment_limits():
    raw = os.environ.get(ENV_VAR, '')
    if raw not in _env_cache:
        try:
            _env_cache[raw] = DEFAULTS.override(parse_limits(raw))
        except (OSError, ValueError) as error:
            raise ValueError(f'Invalid {ENV_VAR}: {error}') from error
    return _env_cache[raw]


def current_limits():
    """Defaults, then environment, then the innermost use_limits context."""
    scoped = _context.get()
    return scoped if scoped is not None else _environment_limits()


def resolve_limits(limits=None):
    """Effective limits for one call: current limits plus per-call overrides."""
    if isinstance(limits, Limits):
        return limits
    base = current_limits()
    if limits is None:
        return base
    return base.override(parse_limits(limits))


@contextmanager
def use_limits(overrides=None, **kwargs):
    """Scope limit overrides (dict, Limits, JSON text or path) to a block."""
    effective = current_limits().override(parse_limits(overrides) if overrides is not None else None, **kwargs)
    token = _context.set(effective)
    try:
        yield effective
    finally:
        _context.reset(token)


def describe_limits():
    """Name, default, effective value and documentation for every limit."""
    effective = current_limits()
    return [{'name': item.name, 'default': item.default, 'effective': getattr(effective, item.name),
             'doc': item.metadata.get('doc', '')} for item in fields(Limits)]
