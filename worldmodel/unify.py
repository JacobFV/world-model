"""Build one queryable graph from the catalog's published normalized outputs.

The sample-era ``unify`` normalized eleven bounded samples and accumulated every record
in a Python list. This module instead streams the *published* output stage of every
catalog dataset straight into the disk-backed SQLite graph index:

* **Streaming.** Records are read line by line from the gzip artifacts, filtered on the
  raw bytes before they are parsed, and handed to :meth:`worldmodel.graph.Graph.build_from_records`
  in bounded batches. Nothing accumulates except per-dataset counters.
* **Selectable scope.** ``--datasets``, ``--domain`` (the ``docs/data`` groupings),
  ``--exclude``, ``--profile`` and ``--all``. The default profile indexes the graph
  *structure* (entities, relationships, identifier assertions, events) of every dataset
  plus observations only where the observations are the point, because the exhaustive
  build is roughly ten times larger. :data:`PROFILES` documents each one.
* **Provenance.** Every selected dataset is verified (output checksums) before it is
  read, and the published summary artifact plus the index metadata pin every input as
  ``dataset@stage@version``.

Raw acquisition payloads are *not* re-hashed here: ``Store.verify(ref, recursive=True)``
would read the multi-hundred-gigabyte raw tree on every build. Use
``python3 -m worldmodel verify <dataset>`` for the full recursive lineage check.
"""
from collections import Counter
import json
import os
from pathlib import Path
import resource
import shutil
import subprocess
import sys
import time
import uuid

from .graph import PUBLICATION_RULES, PUBLICATION_SCHEMA, READABLE_SCHEMAS, Graph
from .resolution.bridges import (BRIDGE_TAGS, BRIDGES, IMO_SHIP_ENTITY_TYPES, LINK_PREDICATES, NAMESPACE_ALIASES,
                                 REGISTER_VALIDATORS,
                                 cardinality as bridge_cardinality, flagged_fraudulent, normalize_value, record_claims)
from .resolution.deterministic import MAPPING_SPECS
from .util import atomic_json, canonical, digest, file_hash, now, read_json, slug

KINDS = ('entity', 'assertion', 'observation', 'event')
STRUCTURE_KINDS = ('entity', 'assertion', 'event')

# Dataset groups follow docs/data/*.md. A dataset may belong to exactly one group.
DOMAINS = {
    'companies': ('sec_company_assets', 'sec_financial_statements', 'sec_issuer_reference',
                  'sec_ownership_datasets', 'sec_13f_history', 'sec_gleif', 'gleif_parent_relationships',
                  'companies_house_uk', 'fdic_bank_financials', 'nasdaq_listings', 'iso_mic_venues',
                  'nasdaq_index_reference', 'ssga_dia_holdings', 'ssga_dia_nav', 'ssga_dia_premium',
                  'market_prices', 'market_obligations', 'market_corporate_actions', 'alpaca_daily_bars',
                  'crossref_research', 'openalex_people', 'nasa_publications'),
    'demographics': ('census_business', 'census_population', 'census_geography', 'census_relationship_files',
                     'cbsa_delineations', 'acs_5yr_tables', 'acs_pums', 'acs_pums_2019', 'irs_soi_migration',
                     'un_wpp', 'classifications', 'noaa_storm_events', 'fema_nri', 'openfema',
                     'usgs_earthquakes', 'usgs_resources', 'ibtracs', 'ghcn_daily', 'ghcn_monthly',
                     'noaa_climdiv', 'wri_aqueduct', 'epa_aqs_daily'),
    'energy_trade': ('eia_energy', 'eia_grid_operations', 'ember_owid_energy', 'usda_agriculture',
                     'usda_fas_psd', 'faostat', 'cepii_baci', 'cepii_baci_hs92', 'cepii_gravity',
                     'un_comtrade', 'census_intl_trade', 'wits_trains_tariffs', 'usitc_hts_tariffs',
                     'wto_timeseries', 'trade_concordances', 'ofac_sanctions', 'other_sanctions_lists',
                     'opensanctions', 'opensanctions_graph', 'ucdp_conflicts', 'gdelt_events', 'vdem',
                     'conflict_reference', 'acled'),
    'macro': ('fred_macro_panel', 'fred_cpi', 'fred_breakeven10y', 'fred_oil_price', 'fred_policy_rate',
              'fred_treasury10y', 'fred_treasury2y', 'bea_input_output', 'bea_national_regional',
              'bls_labor', 'bls_prices', 'lehd_lodes', 'treasury_debt', 'worldbank_wdi', 'imf_sdmx',
              'oecd_sdmx', 'bis_bulk', 'ecb_eurostat', 'exiobase3'),
    'politics': ('fec', 'fec_candidates', 'fec_individual_contributions', 'fec_individual_contributions_2024',
                 'fec_individual_contributions_2022', 'congress_people', 'voteview_rollcalls',
                 'govinfo_billstatus', 'congress_gov_api', 'lda_lobbying', 'federal_register_documents',
                 'mit_election_returns', 'parlgov', 'usaspending', 'usaspending_assistance'),
    'transport': ('osm_topology', 'osm_us_south', 'osm_us_midwest', 'osm_us_northeast', 'transport',
                  'airport_nodes', 'freight', 'marine_ais', 'bts_airline_t100', 'global_fishing_watch'),
}
DOMAIN_OF = {dataset: domain for domain, members in DOMAINS.items() for dataset in members}

# Fixtures, demos and derived report datasets are never evidence inputs to the unified graph.
NOT_EVIDENCE = frozenset({
    'world_evidence', 'world_graph', 'demo_countries', 'demo_graph', 'rando_joes_happiness_index',
    'calibration_reports', 'reference_evidence', 'strategic_scenarios', 'domain_reference_queries',
    'contract_candidates', 'reviewed_obligations', 'dia_nav_benchmark'})

# Datasets whose observations *are* the point: the macro, price, population, hazard, climate
# and commodity-balance series a cross-dataset question needs, chosen so the default index
# stays on one disk. Everything else contributes structure only under the default profile;
# its observations remain one ``--datasets <id>`` scope (or one ``Store.records`` call) away.
OBSERVATION_DATASETS = frozenset({
    # rate, price and fiscal anchors (the five FRED anchor series and the Treasury debt panel)
    'fred_cpi', 'fred_breakeven10y', 'fred_oil_price', 'fred_policy_rate', 'fred_treasury10y',
    'fred_treasury2y', 'treasury_debt',
    # population, local economy and geography
    'census_population', 'census_business', 'census_geography', 'census_relationship_files',
    'acs_5yr_tables', 'lehd_lodes', 'cbsa_delineations', 'classifications', 'usgs_resources',
    # hazard, climate, water and disaster assistance
    'fema_nri', 'noaa_storm_events', 'noaa_climdiv', 'openfema', 'epa_aqs_daily',
    # commodities, energy, trade and tariffs
    'usda_agriculture', 'usda_fas_psd', 'ember_owid_energy', 'un_comtrade', 'wto_timeseries',
    'usitc_hts_tariffs', 'trade_concordances',
    # funds, venues, politics and governance
    'ssga_dia_holdings', 'ssga_dia_nav', 'ssga_dia_premium', 'nasdaq_index_reference', 'nasdaq_listings',
    'iso_mic_venues', 'mit_election_returns', 'vdem', 'parlgov', 'ucdp_conflicts', 'conflict_reference',
    'voteview_rollcalls', 'congress_people', 'congress_gov_api', 'fec_candidates',
    'federal_register_documents', 'sec_issuer_reference', 'gleif_parent_relationships',
    'ofac_sanctions', 'other_sanctions_lists', 'opensanctions', 'opensanctions_graph',
    'airport_nodes', 'transport', 'openalex_people', 'crossref_research', 'nasa_publications'})

# Excluded from the default profile. Each is a *bulk* dataset: tens of millions of
# transaction, holding, segment or article rows. They are not less trustworthy, only large,
# and each is one ``--datasets <id>`` or ``--domain <group>`` scope away.
#
#   dataset                  default-profile rows   record text   reason
#   osm_topology / _us_*              98.6M           62 GB       road routing graph, not a join surface
#   usaspending                       66.7M           49 GB       one record per contract transaction
#   sec_13f_history                   78.6M           43 GB       one record per quarterly 13F holding line
#   usaspending_assistance            49.0M           38 GB       one record per assistance transaction
#   companies_house_uk                52.3M           27 GB       full UK register plus every PSC entry
#   gdelt_events                      17.0M           15 GB       one event record per news article
BULK_DATASETS = frozenset({'osm_topology', 'osm_us_south', 'osm_us_midwest', 'osm_us_northeast',
                           'usaspending', 'usaspending_assistance', 'sec_13f_history',
                           'companies_house_uk', 'gdelt_events'})

PROFILES = {
    'structure': {
        'description': ('Default. Graph structure (entities, relationships, identifier assertions and events) '
                        'for every dataset with a published output, plus observations for the datasets where '
                        'the observations are the point. Excludes the nine bulk datasets in BULK_DATASETS.'),
        'kinds': STRUCTURE_KINDS, 'observation_datasets': OBSERVATION_DATASETS, 'exclude': BULK_DATASETS},
    'structure_full': {
        'description': ('Same kind rules as "structure" but no dataset exclusions: adds the OSM road graphs, '
                        'USAspending transactions, 13F holdings, the UK register and GDELT events.'),
        'kinds': STRUCTURE_KINDS, 'observation_datasets': OBSERVATION_DATASETS, 'exclude': frozenset()},
    'identity': {
        'description': ('Identity and ownership backbone only: the company, sanctions, banking, legislator and '
                        'procurement registries whose entities carry published identifiers.'),
        'kinds': STRUCTURE_KINDS, 'observation_datasets': frozenset(),
        'datasets': ('sec_gleif', 'gleif_parent_relationships', 'sec_issuer_reference', 'sec_company_assets',
                     'sec_ownership_datasets', 'sec_13f_history', 'companies_house_uk', 'fdic_bank_financials',
                     'nasdaq_listings', 'iso_mic_venues', 'market_corporate_actions', 'ofac_sanctions',
                     'other_sanctions_lists', 'opensanctions', 'opensanctions_graph', 'congress_people',
                     'voteview_rollcalls', 'fec', 'fec_candidates', 'lda_lobbying', 'usaspending',
                     'usaspending_assistance')},
    'all': {
        'description': 'Every record of every dataset with a published output stage, including the OSM extracts.',
        'kinds': KINDS, 'observation_datasets': None, 'exclude': frozenset()},
}
DEFAULT_PROFILE = 'structure'


# -- inventory ---------------------------------------------------------------------------------

def _published(store, definition):
    """Pinned reference, row count and byte size of a dataset's published output stage."""
    dataset = definition['id']
    stage = definition.get('output_stage') if definition.get('schema_version') == 2 else None
    path = store.latest_path(dataset, stage)
    if not path.is_file():
        return None, 'no published output stage'
    ref = read_json(path)
    if ref.get('dataset') != dataset:
        return None, 'version pointer dataset mismatch'
    index = store.dataset_dir(dataset) / 'manifests' / (ref.get('stage') or 'final') / (ref['version'] + '.json')
    rows = size = None
    if index.is_file():
        outputs = read_json(index).get('outputs') or {}
        counted = [o['rows'] for o in outputs.values() if isinstance(o.get('rows'), int)]
        rows = sum(counted) if counted else None  # older manifests do not record a row count
        size = sum(o.get('bytes') or 0 for o in outputs.values())
    if not records_path(store, ref):
        return None, 'published version has no records output on disk'
    if rows == 0:
        return None, 'published output is empty'
    return {'dataset': dataset, 'stage': ref.get('stage') or 'final', 'ref': ref, 'rows': rows,
            'bytes': size, 'domain': DOMAIN_OF.get(dataset)}, None


def inventory(catalog, store):
    """Every catalog dataset with its published output stage, or the reason it has none."""
    available, missing = [], []
    for definition in catalog.list():
        dataset = definition['id']
        if dataset in NOT_EVIDENCE:
            missing.append({'dataset': dataset, 'reason': 'derived view or fixture, not a source of evidence'})
            continue
        item, reason = _published(store, definition)
        if item is None:
            missing.append({'dataset': dataset, 'reason': reason})
        else:
            available.append(item)
    available.sort(key=lambda item: item['dataset'])
    return available, missing


def scope(catalog, store, *, profile=DEFAULT_PROFILE, datasets=None, domains=None, exclude=None):
    """Resolve a selection into per-dataset plans; unknown names raise before any work starts."""
    if profile not in PROFILES:
        raise ValueError('Unknown profile %r; choose from %s' % (profile, ', '.join(sorted(PROFILES))))
    rules = PROFILES[profile]
    available, missing = inventory(catalog, store)
    by_name = {item['dataset']: item for item in available}
    requested = None
    if datasets:
        requested = set()
        for name in datasets:
            slug(name)
            if name not in by_name:
                known = next((m for m in missing if m['dataset'] == name), None)
                raise ValueError('%s has no published output to unify (%s)'
                                 % (name, known['reason'] if known else 'not in the catalog'))
            requested.add(name)
    if domains:
        requested = set() if requested is None else requested
        for domain in domains:
            if domain not in DOMAINS:
                raise ValueError('Unknown domain %r; choose from %s' % (domain, ', '.join(sorted(DOMAINS))))
            requested.update(name for name in DOMAINS[domain] if name in by_name)
    if requested is None:
        requested = set(by_name) - set(rules.get('exclude') or ())
        if rules.get('datasets') is not None:
            requested &= set(rules['datasets'])
    excluded = set(exclude or ())
    for name in excluded:
        slug(name)
    plan, skipped = [], list(missing)
    for item in available:
        name = item['dataset']
        if name in excluded:
            skipped.append({'dataset': name, 'reason': 'excluded by --exclude', 'rows': item['rows']})
            continue
        if name not in requested:
            skipped.append({'dataset': name, 'reason': 'not in scope (profile %s)' % profile, 'rows': item['rows']})
            continue
        observations = rules['observation_datasets']
        kinds = KINDS if observations is None or name in observations else tuple(rules['kinds'])
        plan.append({**item, 'kinds': tuple(kinds)})
    return {'profile': profile, 'profile_description': PROFILES[profile]['description'],
            'requested_datasets': sorted(datasets or ()), 'requested_domains': sorted(domains or ()),
            'excluded': sorted(excluded), 'selected': plan, 'skipped': skipped,
            'catalog_rows': sum(item['rows'] or 0 for item in available),
            'selected_rows': sum(item['rows'] or 0 for item in plan)}


# -- streaming ---------------------------------------------------------------------------------

def records_path(store, ref):
    directory = store.version_dir(ref)
    for name in ('records.jsonl.gz', 'records.jsonl'):
        if (directory / name).is_file():
            return directory / name
    return None


def _lines(path):
    """Yield raw record lines as bytes. gzip is decompressed in a helper process when one exists,
    which overlaps decompression with indexing; the stdlib reader is the portable fallback."""
    if path.suffix == '.gz':
        program = shutil.which('zcat') or shutil.which('gzip')
        if program:
            arguments = [program] + ([] if program.endswith('zcat') else ['-cd']) + [str(path)]
            process = subprocess.Popen(arguments, stdout=subprocess.PIPE, bufsize=1 << 22)
            try:
                for line in process.stdout:
                    yield line
            finally:
                process.stdout.close()
                code = process.wait()
            if code:
                raise OSError('Decompressing %s failed with exit code %d' % (path, code))
            return
        import gzip
        stream = gzip.open(path, 'rb')
    else:
        stream = path.open('rb')
    with stream:
        yield from stream


class Progress:
    """Whole-build progress on stderr: a line every ``every`` records read, and one per dataset."""

    def __init__(self, every=2_000_000, total=None, stream=sys.stderr):
        self.every, self.total, self.stream = every, total, stream
        self.started = time.time()
        self.read = self.indexed = 0
        self._next = every or float('inf')

    def advance(self, read_delta, indexed_delta, label='...'):
        self.read += read_delta
        self.indexed += indexed_delta
        if self.read >= self._next:
            self._next = self.read + self.every
            self.line(label)

    def line(self, label):
        if self.stream is None:
            return
        elapsed = max(time.time() - self.started, 1e-9)
        share = ('%5.1f%%' % (100 * self.read / self.total)) if self.total else '    - '
        remaining = ''
        if self.total and self.read:
            remaining = '  eta %5.0fs' % (elapsed * (self.total - self.read) / self.read)
        print('  %-36s read %12d %s  indexed %12d  %7.0f rec/s  rss %5.2f GiB  %6.0fs%s'
              % (label, self.read, share, self.indexed, self.read / elapsed, peak_rss() / 2 ** 30,
                 elapsed, remaining), file=self.stream, flush=True)


def stream_dataset(store, item, *, stats, progress=None, limit=None, verify=True):
    """Stream one dataset's published records, filtered to ``item['kinds']``.

    The byte filter is a cheap superset test on the canonical JSON; every surviving line is
    parsed and its ``kind`` re-checked, so a ``"kind":"entity"`` substring inside an attribute
    can never smuggle a record into a filtered build.
    """
    ref, kinds = item['ref'], set(item['kinds'])
    if verify:
        store.verify(ref, recursive=False)
    path = records_path(store, ref)
    if path is None:
        raise ValueError('No records output for %s@%s' % (ref['dataset'], ref['version']))
    tags = None if kinds == set(KINDS) else tuple(('"kind":"%s"' % kind).encode() for kind in sorted(kinds))
    counts, read, kept = Counter(), 0, 0
    pending_read = pending_kept = 0
    started = time.time()
    for line in _lines(path):
        read += 1
        pending_read += 1
        if tags is None or any(tag in line for tag in tags):
            record = json.loads(line)
            if record.get('kind') in kinds:
                kept += 1
                pending_kept += 1
                counts[record['kind']] += 1
                yield record
        if pending_read >= 100000:
            if progress is not None:
                progress.advance(pending_read, pending_kept, item['dataset'])
            pending_read = pending_kept = 0
        if limit is not None and kept >= limit:
            break
    if progress is not None:
        progress.advance(pending_read, pending_kept, item['dataset'])
        progress.line(item['dataset'] + ' (done)')
    stats[item['dataset']] = {
        'dataset': item['dataset'], 'stage': item['stage'], 'version': ref['version'],
        'kinds_selected': sorted(kinds), 'records_read': read, 'records_indexed': kept,
        'entities': counts['entity'], 'assertions': counts['assertion'],
        'observations': counts['observation'], 'events': counts['event'],
        'bytes_read': item['bytes'], 'seconds': round(time.time() - started, 2)}


def peak_rss():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024


# -- publication -------------------------------------------------------------------------------

def publish_scope(store, dataset, report, parameters, inputs, *, entrypoint='worldmodel.unify:unify'):
    """Publish the unify summary as an immutable artifact pinning every input dataset@stage@version.

    This mirrors :func:`worldmodel.artifacts.publish_report` but verifies input *outputs* only.
    Recursively re-hashing every raw acquisition payload (hundreds of gigabytes) on every build
    is not affordable, and the raw lineage is unchanged by unification; ``wm verify <dataset>``
    still performs the full recursive check.
    """
    from .provenance import capture_code
    from .rights import inherited_rights
    inputs = [dict(ref) for ref in inputs]
    code = capture_code(Path(__file__).resolve().parents[1], entrypoint)
    for ref in inputs:
        store.verify(ref, recursive=False)
    canonical(report)
    with store.lock(dataset):
        run_id = uuid.uuid4().hex
        staging = store.scratch_dir(dataset) / run_id
        staging.mkdir(parents=True)
        try:
            atomic_json(staging / 'report.json', report)
            (staging / 'records.jsonl').write_bytes(b'')
            identity = {'rights': inherited_rights(store, inputs, ()), 'schema_version': 1, 'dataset': dataset,
                        'definition': {'id': dataset, 'kind': 'derived', 'schema_version': 1, 'entrypoint': entrypoint},
                        'parameters': parameters, 'inputs': inputs, 'raw_inputs': [], 'code': code,
                        'outputs': {name: {'sha256': file_hash(staging / name),
                                           'bytes': (staging / name).stat().st_size}
                                    for name in ('report.json', 'records.jsonl')}}
            ref = {'dataset': dataset, 'version': digest(identity)}
            atomic_json(staging / 'manifest.json', {**identity, 'version': ref['version']})
            destination = store.version_dir(ref)
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists():
                store.verify(ref, recursive=False)
            else:
                os.rename(staging, destination)
            store.publish_index(ref)
            atomic_json(store.runs_dir(dataset) / (run_id + '.json'),
                        {'run_id': run_id, 'status': 'succeeded', 'output': ref, 'completed_at': now()})
            return ref
        finally:
            if staging.exists():
                shutil.rmtree(staging)


# -- build -------------------------------------------------------------------------------------

def unify(catalog, store, project=None, *, profile=DEFAULT_PROFILE, datasets=None, domains=None, exclude=None,
          index=None, output_dataset='world_evidence', batch_size=50000, cache_mb=1024, progress=2_000_000,
          publish=True, validate=False, limit=None, verify=True, dry_run=False, compress=True):
    """Stream the selected published outputs into ``index`` and publish a pinned summary."""
    plan = scope(catalog, store, profile=profile, datasets=datasets, domains=domains, exclude=exclude)
    if not plan['selected']:
        raise ValueError('Selected scope contains no dataset with a published output stage')
    if dry_run:
        return {'scope': _scope_report(plan), 'dry_run': True,
                'datasets': [{'dataset': i['dataset'], 'stage': i['stage'], 'version': i['ref']['version'],
                              'kinds': list(i['kinds']), 'published_rows': i['rows'], 'bytes': i['bytes']}
                             for i in plan['selected']],
                'skipped': plan['skipped']}
    index_path = Path(index) if index else store.root / output_dataset / 'index.sqlite'
    started = time.time()
    stats = {}
    monitor = Progress(every=progress, total=plan['selected_rows']) if progress else None
    groups = [(item['ref'], stream_dataset(store, item, stats=stats, progress=monitor, limit=limit,
                                           verify=verify)) for item in plan['selected']]
    if monitor:
        print('unify: profile %s, %d datasets, %s published records in scope -> %s'
              % (profile, len(groups), f"{plan['selected_rows']:,}", index_path), file=sys.stderr, flush=True)
    built = Graph(index_path).build_from_records(groups, batch_size=batch_size, validate=validate,
                                                 cache_mb=cache_mb, compress_bodies=compress,
                                                 publication_rules=PUBLICATION_RULES)
    elapsed = time.time() - started
    summary = {
        'scope': _scope_report(plan),
        'index': {'path': str(index_path), 'records': built['records'], 'edges': built['edges'],
                  'bytes': index_path.stat().st_size},
        'publication_coverage': built.get('publication_coverage'),
        'publication_rules': {name: PUBLICATION_RULES[name] for item in plan['selected']
                              for name in [item['dataset']] if name in PUBLICATION_RULES},
        'datasets': [stats[item['dataset']] for item in plan['selected'] if item['dataset'] in stats],
        'skipped': plan['skipped'],
        'inputs': [dict(item['ref']) for item in plan['selected']],
        'totals': _totals(stats),
        'build': {'seconds': round(elapsed, 1), 'peak_rss_bytes': peak_rss(),
                  'records_per_second': round(sum(s['records_read'] for s in stats.values()) / max(elapsed, 1e-9)),
                  'validated_records': bool(validate), 'verified_output_checksums': bool(verify),
                  'compressed_bodies': bool(compress), 'cache_mb': cache_mb, 'batch_size': batch_size},
        'limitations': [
            'Records are copied verbatim from each dataset\'s published output stage; nothing is merged, '
            'deduplicated or reconciled by this build.',
            'A scope narrower than --all indexes a documented subset: an absent edge may mean out of scope, '
            'not absent from the evidence.',
            'Raw acquisition payloads are not re-hashed by unify; run "wm verify <dataset>" for the full '
            'recursive lineage check.',
            'A record carries a publication date only where the publisher emits one (dimensions.available_at), '
            'where the row is ALFRED-vintaged (attributes.realtime_start), or where a declared dataset rule in '
            'worldmodel.graph.PUBLICATION_RULES can date it. Everything else is NULL, which means unknown, and is '
            'excluded from --known-at queries by default. "publication_coverage" reports the share per dataset; '
            'docs/point-in-time-graph.md says what a declared lag does and does not establish.'],
    }
    if publish:
        parameters = {'profile': profile, 'datasets': sorted(datasets or ()), 'domains': sorted(domains or ()),
                      'exclude': sorted(exclude or ()), 'limit': limit, 'index': str(index_path)}
        summary['artifact'] = publish_scope(store, output_dataset, summary, parameters, summary['inputs'])
    return summary


def _scope_report(plan):
    return {key: plan[key] for key in ('profile', 'profile_description', 'requested_datasets', 'requested_domains',
                                       'excluded', 'catalog_rows', 'selected_rows')} | {
        'selected_datasets': len(plan['selected']), 'skipped_datasets': len(plan['skipped'])}


def _totals(stats):
    total = Counter()
    for row in stats.values():
        for key in ('records_read', 'records_indexed', 'entities', 'assertions', 'observations', 'events'):
            total[key] += row[key]
    return dict(total)


# -- entity resolution over the real catalog ------------------------------------------------------

# Three published predicates carry identifiers, with three value shapes:
#   identifier_assignment  {'namespace': 'sec_cik', 'value': '1750'}       registries
#   identifier             {'id': 'lei:5493...', 'value': 'raw string'}    sanctions datasets
#   identified_by          'iata:UTK' (a namespaced literal)               transport datasets
IDENTITY_PREDICATES = ('identifier_assignment', 'identifier', 'identified_by')
# ``resolution.bridges`` adds the tags for published identifiers that live in other fields
# (GLEIF registration-authority attributes, issuer_security edges to an ISIN).
IDENTITY_TAGS = (tuple(('"predicate":"%s"' % p).encode() for p in IDENTITY_PREDICATES)
                 + (b'"predicate":"same_as"', b'"kind":"entity"') + BRIDGE_TAGS)

# A namespaced entity ID is itself a published identifier claim: the source chose to call this
# thing ``lei:5493...`` or ``sec:cik:0000320193``. Mapping the prefixes lets an OpenSanctions
# entity that publishes an LEI meet the GLEIF entity whose ID *is* that LEI.
ENTITY_ID_NAMESPACES = {
    'lei:': 'lei', 'sec:cik:': 'sec_cik', 'bioguide:': 'bioguide', 'icpsr:': 'icpsr',
    'fec:candidate:': 'fec_candidate', 'fec:committee:': 'fec_committee', 'uei:': 'uei',
    'duns:': 'duns', 'fdic:cert:': 'fdic_cert', 'rssd:': 'rssd', 'isin:': 'isin', 'figi:': 'figi',
    'cusip:': 'cusip', 'mmsi:': 'mmsi', 'imo:': 'imo', 'mic:': 'mic', 'wikidata:': 'wikidata',
    'opensanctions:': 'opensanctions', 'gb:companies_house:': 'gb_company_number',
    # transport keys an airport reference on its IATA code; crossref keys researchers on their ORCID
    # iD and institutions on their ROR ID. Each is the published identifier, used as the entity ID.
    'iata:': 'iata', 'orcid:': 'orcid', 'ror:': 'ror',
}
# Namespaces added to resolution.UNIQUE_NAMESPACES because each value names one thing at a time.
EXTRA_UNIQUE_NAMESPACES = frozenset({'gb_company_number', 'cusip', 'permid', 'ru_inn', 'ru_ogrn',
                                     'unlocode', 'iata', 'icao', 'swift'})
# Namespaces whose values a single publisher legitimately prints for more than one *different*
# thing: OurAirports keeps a closed airport's IATA/ICAO code on the closed record while the code
# serves a new airport, a Russian branch office prints its parent's INN, and a BIC is reassigned
# between institutions over time (GLEIF's BIC-to-LEI map must be 1:1 at a point in time). One dataset printing
# such a value for two subjects says the value does not identify one thing there, so that
# dataset's rows for the value are refused for clustering, and counted. In the other namespaces a
# within-dataset repeat is two records of one thing (a hull re-flagged under a second MMSI keeps
# its IMO number), which is exactly what the identifier is for, and is reported but kept.
#
# Measured on this catalog before the rule was written: of 519 INN values opensanctions_graph
# prints for two or more of its records, and 338 OGRN values, most pair *different* organisations
# ("Gazprom Dobycha Krasnodar" and "Gazprom Dobycha Vuktyl"; a company and a person), because a
# successor keeps its predecessor's numbers. 28 UN/LOCODEs are printed by two World Port Index
# ports (two harbours in one locode), and an MMSI is a radio identity reassigned between hulls.
REFUSE_DUPLICATES_WITHIN_A_DATASET = frozenset({'iata', 'icao', 'ru_inn', 'ru_ogrn', 'swift', 'unlocode', 'mmsi'})
# Sentinels identity_records yields alongside claims.
ENTITY_ROW, FRAUDULENT_CLAIM = '__entity__', '__fraudulent__'

# Namespaces the published data shows are *not* one-to-one, measured on this catalog. Linking on
# them would merge distinct entities, so they are excluded from identity clustering; the
# identifier assertions themselves stay in the graph as evidence.
#
#   ein  SEC filers share a taxpayer ID across a parent and its subsidiaries: EIN 850019030 is
#        published for both sec:cik:0000081023 (Public Service Co of New Mexico) and
#        sec:cik:0001108426 (TXNM Energy Inc), which are a subsidiary and its holding company.
#   rssd MAPPING_SPECS declares fdic_cert<->rssd as 1:1, but 943 FDIC certificate pairs in the
#        published institution directory share one FED_RSSD (e.g. fdic:cert:10005 "Valley Bank,
#        Green Bay" and fdic:cert:21710 "M&I Bank Northeast" both carry rssd 736943).
NON_UNIQUE_IN_PRACTICE = frozenset({'ein', 'rssd'})


def _entity_identifier(entity_id):
    for prefix, namespace in ENTITY_ID_NAMESPACES.items():
        if entity_id.startswith(prefix) and len(entity_id) > len(prefix):
            return namespace, entity_id[len(prefix):], None
    return None


def _identifier_value(record):
    """Normalize the three published shapes into (namespace, value, scope) or None."""
    predicate, value = record.get('predicate'), record.get('value')
    if predicate == 'identifier_assignment' and isinstance(value, dict):
        namespace, raw = value.get('namespace'), value.get('value')
        scope = value.get('scope')
    elif predicate == 'identifier' and isinstance(value, dict):
        identifier, raw = str(value.get('id') or ''), value.get('value')
        namespace, _, tail = identifier.partition(':')
        raw = tail or raw
        scope = None
    elif predicate == 'identified_by' and isinstance(value, str):
        namespace, _, raw = value.partition(':')
        scope = None
    else:
        return None
    if not namespace or raw in (None, ''):
        return None
    return str(namespace).strip().lower(), str(raw).strip(), scope


def identity_records(store, items, *, progress=None):
    """Stream the identifier and ``same_as`` assertions of the selected datasets.

    Yields ``(record, namespace, value, scope, bridge)`` for identifier claims and
    ``(record, None, None, None, None)`` for published ``same_as`` assertions. ``bridge`` is
    ``None`` for a claim the source published as an identifier, and the
    :data:`worldmodel.resolution.bridges.BRIDGES` key for one read out of another published
    field (a GLEIF registration-authority entity ID, the CUSIP inside a US ISIN).
    """
    for item in items:
        ref = item['ref']
        store.verify(ref, recursive=False)
        path = records_path(store, ref)
        read = kept = 0
        for line in _lines(path):
            read += 1
            if not any(tag in line for tag in IDENTITY_TAGS):
                continue
            record = json.loads(line)
            record['_dataset'] = item['dataset']  # the dataset being read, not an evidence input name
            kind = record.get('kind')
            for subject, namespace, value, scope, bridge in record_claims(record):
                kept += 1
                yield ({'id': record['id'] + ':' + bridge, 'subject': subject,
                        'predicate': 'identifier_assignment', 'evidence': record['evidence'], '_dataset': item['dataset']},
                       namespace, value, scope, bridge)
            if kind == 'entity':
                # Every entity record, so the resolution can say which datasets describe a cluster.
                yield ({'id': record['id'], 'subject': record.get('entity_id', record['id']),
                        'entity_type': record.get('entity_type'), 'evidence': record['evidence'], '_dataset': item['dataset']},
                       None, None, None, ENTITY_ROW)
                parsed = _entity_identifier(record.get('entity_id', record['id']))
                if parsed is not None:
                    kept += 1
                    yield ({'id': record['id'], 'subject': record.get('entity_id', record['id']),
                            'predicate': 'identifier_assignment', 'evidence': record['evidence'], '_dataset': item['dataset']}, *parsed, None)
                continue
            if kind != 'assertion':
                continue
            predicate = record.get('predicate')
            if (predicate == 'same_as' or predicate in LINK_PREDICATES) and record.get('object'):
                kept += 1
                yield record, None, None, None, None
                continue
            parsed = _identifier_value(record)
            if parsed is None:
                continue
            kept += 1
            # A value the publisher itself marks as fraudulently used is kept out of identity, and
            # remembered so the same value on a republished copy of the listing is refused too.
            yield record, *parsed, (FRAUDULENT_CLAIM if flagged_fraudulent(record.get('value')) else None)
        if progress is not None:
            progress.advance(read, kept, item['dataset'])
            progress.line(item['dataset'] + ' (identifiers)')


class _Links:
    """Disk-backed collector: identifier rows spill to SQLite so memory stays bounded."""

    def __init__(self, path):
        import sqlite3
        self.connection = sqlite3.connect(path)
        self.connection.executescript(
            'PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; PRAGMA cache_size=-262144;'
            'CREATE TABLE ids (namespace TEXT, value TEXT, scope TEXT, subject TEXT, record TEXT, dataset TEXT,'
            ' bridge TEXT);'
            'CREATE TABLE edges (subject TEXT, object TEXT, method TEXT, record TEXT, dataset TEXT);'
            'CREATE TABLE entities (entity_id TEXT, dataset TEXT, entity_type TEXT);'
            'CREATE TABLE flagged (namespace TEXT, value TEXT, scope TEXT, subject TEXT, dataset TEXT);')
        self.rows, self.edges, self.entities, self.flagged = [], [], [], []

    @staticmethod
    def _dataset(record):
        return record.get('_dataset') or (record['evidence'][0]['input'] or {}).get('dataset')

    def add_identifier(self, namespace, value, scope, record, bridge=None):
        self.rows.append((namespace, value, scope or '', record['subject'], record['id'],
                          self._dataset(record), bridge))
        if len(self.rows) >= 50000:
            self.flush()

    def add_same_as(self, record):
        self.edges.append((record['subject'], record['object'], 'source_asserted:' + record.get('predicate', 'same_as'),
                           record['id'], self._dataset(record)))
        if len(self.edges) >= 50000:
            self.flush()

    def add_entity(self, record):
        self.entities.append((record['subject'], self._dataset(record), record.get('entity_type')))
        if len(self.entities) >= 50000:
            self.flush()

    def add_flagged(self, namespace, value, scope, record):
        self.flagged.append((namespace, value, scope or '', record['subject'], self._dataset(record)))

    def flush(self):
        if self.rows:
            self.connection.executemany('INSERT INTO ids VALUES (?,?,?,?,?,?,?)', self.rows)
            self.rows = []
        if self.edges:
            self.connection.executemany('INSERT INTO edges VALUES (?,?,?,?,?)', self.edges)
            self.edges = []
        if self.entities:
            self.connection.executemany('INSERT INTO entities VALUES (?,?,?)', self.entities)
            self.entities = []
        if self.flagged:
            self.connection.executemany('INSERT INTO flagged VALUES (?,?,?,?,?)', self.flagged)
            self.flagged = []

    def close(self):
        self.connection.close()


def refuse_bridge_cardinality_violations(connection):
    """Delete bridge claim rows that break the cardinality their mapping specification declares.

    A bridge reads a published crosswalk field, so its rows carry a declared cardinality. Where
    the published data breaks it - two LEIs printing one CIK as their SEC registration-authority
    entity ID - clustering would merge two distinct legal entities. Those rows are removed and
    reported, exactly as ``link_mapping`` refuses a 1:1 row that maps to several values, and the
    same rule that keeps ``ein`` and ``rssd`` out of clustering.
    """
    refused = []
    for bridge in sorted(BRIDGES):
        declared = bridge_cardinality(bridge)
        left, right = declared.split(':')
        # ``left`` constrains how many entities may publish one value; ``right`` how many values
        # one entity may publish. A bridge stores the right-hand namespace against the left-hand
        # entity, so the two checks are the two groupings of the same table. Both are held per
        # publishing dataset: OFAC and the CSL's copy of an OFAC entry printing one INN is two
        # publishers agreeing, while one publisher printing it for two parties is the break.
        for column, other, limit, shape in (('value', 'subject', left, 'one %s value is published by %d entities'),
                                            ('subject', 'value', right, 'one entity publishes %d %s values')):
            if limit != '1':
                continue
            for row in connection.execute(
                    'SELECT namespace, scope, %s AS key, COUNT(DISTINCT %s) AS n, dataset FROM ids WHERE bridge=? '
                    'GROUP BY namespace, scope, dataset, %s HAVING n > 1' % (column, other, column),
                    (bridge,)).fetchall():
                detail = shape % ((row[0], row[3]) if column == 'value' else (row[3], row[0]))
                refused.append({'bridge': bridge, 'cardinality': declared, 'dataset': row[4],
                                'reason': '%s, which the mapping specification forbids' % detail,
                                'namespace': row[0], column: row[2],
                                'collided_with': sorted(v[0] for v in connection.execute(
                                    'SELECT DISTINCT %s FROM ids WHERE bridge=? AND namespace=? AND scope=? '
                                    'AND dataset IS ? AND %s=?' % (other, column),
                                    (bridge, row[0], row[1], row[4], row[2])))[:8]})
                connection.execute('DELETE FROM ids WHERE bridge=? AND namespace=? AND scope=? AND dataset IS ? '
                                   'AND %s=?' % column, (bridge, row[0], row[1], row[4], row[2]))
    return refused


def refuse_flagged_and_ambiguous_values(connection):
    """Apply the three value-level refusals of :mod:`worldmodel.resolution.bridges`, and count them.

    * **Fraudulent**: a claim whose publisher marks the value as fraudulently used never reaches
      ``ids``; the same value on a record that a published link predicate makes the same
      designation (the CSL copy of an OFAC entry, which drops the flag) is deleted here.
    * **IMO series**: an ``imo`` claim on a subject whose own entity record is not a vessel is an
      IMO company number, and is retyped ``imo_company`` so the two series never meet.
    * **Codes one dataset prints for two things** (:data:`REFUSE_DUPLICATES_WITHIN_A_DATASET`):
      that dataset's rows for the value are deleted. Repeats in other namespaces are counted only.
    """
    counts = Counter()
    ship_types = ','.join("'%s'" % t for t in sorted(IMO_SHIP_ENTITY_TYPES))
    methods = ','.join("'source_asserted:%s'" % p for p in sorted(LINK_PREDICATES))
    connection.executescript(
        'CREATE INDEX IF NOT EXISTS entities_id ON entities(entity_id);'
        'DROP TABLE IF EXISTS twins;'
        'CREATE TEMP TABLE twins AS SELECT subject AS a, object AS b FROM edges WHERE method IN (%s) '
        'UNION SELECT object, subject FROM edges WHERE method IN (%s);' % (methods, methods))
    counts['fraudulent_claims'] = connection.execute('SELECT COUNT(*) FROM flagged').fetchone()[0]
    counts['fraudulent_copies_refused'] = connection.execute(
        'DELETE FROM ids WHERE rowid IN (SELECT i.rowid FROM flagged f JOIN twins t ON t.a = f.subject '
        'JOIN ids i ON i.subject = t.b AND i.namespace = f.namespace AND i.value = f.value)').rowcount
    counts['imo_claims_retyped_imo_company'] = connection.execute(
        "UPDATE ids SET namespace = 'imo_company' WHERE namespace = 'imo' AND subject IN (SELECT entity_id FROM "
        'entities WHERE entity_type IS NOT NULL AND entity_type NOT IN (%s))' % ship_types).rowcount
    duplicates = connection.execute(
        'SELECT namespace, dataset, COUNT(*) FROM (SELECT namespace, dataset, value, scope FROM ids '
        'GROUP BY namespace, dataset, value, scope HAVING COUNT(DISTINCT subject) > 1) GROUP BY namespace, dataset '
        'ORDER BY 3 DESC').fetchall()
    within = [{'namespace': ns, 'dataset': ds, 'values': n, 'refused': ns in REFUSE_DUPLICATES_WITHIN_A_DATASET}
              for ns, ds, n in duplicates]
    for namespace in sorted(REFUSE_DUPLICATES_WITHIN_A_DATASET):
        counts['duplicate_code_rows_refused_' + namespace] = connection.execute(
            'DELETE FROM ids WHERE rowid IN (SELECT i.rowid FROM ids i JOIN (SELECT dataset, value, scope FROM ids '
            'WHERE namespace = ? GROUP BY dataset, value, scope HAVING COUNT(DISTINCT subject) > 1) d '
            'ON d.dataset IS i.dataset AND d.value = i.value AND d.scope = i.scope WHERE i.namespace = ?)',
            (namespace, namespace)).rowcount
    return dict(counts), within


def identity_links(store, items, *, workdir, namespaces=None, progress=None, bridges=True):
    """Deterministic, source-asserted identity links across the real catalog.

    Three sources, all asserted rather than inferred:

    * published ``same_as`` assertions (e.g. congress_people -> fec_candidates / Voteview);
    * entities from different sources carrying the same value in a namespace that identifies
      exactly one thing at a time (``resolution.UNIQUE_NAMESPACES``), via
      :func:`worldmodel.resolution.shared_identifier_links`, which also reports the case of
      one entity holding two concurrent values in such a namespace;
    * published identifiers that a source prints in a field other than an identifier assertion
      (:mod:`worldmodel.resolution.bridges`): a GLEIF registration-authority entity ID, the CUSIP
      inside a US ISIN. Bridge rows are held to the cardinality their mapping specification
      declares and are refused where the published data breaks it. ``bridges=False`` turns them
      off, which is what the regression test for "nothing inferred by default" compares against.

    Identifier rows spill to a SQLite work file; only the values that actually collide across
    distinct entity IDs are materialized, so peak memory does not scale with the catalog.
    """
    from .identity import _normalize, _scope
    from .resolution import UNIQUE_NAMESPACES, shared_identifier_links
    namespaces = set(namespaces or ((set(UNIQUE_NAMESPACES) | set(EXTRA_UNIQUE_NAMESPACES))
                                    - set(NON_UNIQUE_IN_PRACTICE)))
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    work = workdir / 'identity.sqlite'
    work.unlink(missing_ok=True)
    links = _Links(work)
    counts = Counter()
    try:
        for record, namespace, value, scope, bridge in identity_records(store, items, progress=progress):
            if bridge == ENTITY_ROW:
                links.add_entity(record)
                continue
            if namespace is None:
                predicate = record.get('predicate', 'same_as')
                if predicate != 'same_as' and not bridges:
                    continue  # a published link predicate is a crosswalk field like any other bridge
                counts['same_as' if predicate == 'same_as' else 'link_' + predicate] += 1
                links.add_same_as(record)
                continue
            flagged = bridge == FRAUDULENT_CLAIM
            bridge = None if flagged else bridge
            if bridge is not None and not bridges:
                continue
            if bridges and namespace in NAMESPACE_ALIASES:
                counts['aliased_' + namespace] += 1
                namespace = NAMESPACE_ALIASES[namespace]
            counts['identifiers'] += 1
            counts['identifiers_' + namespace] += 1
            if bridge is not None:
                counts['bridge_claims_' + bridge] += 1
            if namespace not in namespaces:
                continue
            try:  # Normalize before grouping: sec_cik 1750 and 0000001750 are the same filer.
                namespace, value = _normalize(namespace, value)
                value = normalize_value(namespace, value)
                scope = _scope(scope)
            except ValueError:
                counts['unnormalizable_identifiers'] += 1
                continue
            if bridges and namespace in REGISTER_VALIDATORS and REGISTER_VALIDATORS[namespace](value) is None:
                counts['check_digit_failures_' + namespace] += 1
                continue
            if flagged:
                links.add_flagged(namespace, value, scope, record)
                continue
            links.add_identifier(namespace, value, scope, record, bridge)
        links.flush()
        connection = links.connection
        connection.execute('CREATE INDEX ids_bridge ON ids(bridge, namespace, scope, value)')
        bridge_conflicts = refuse_bridge_cardinality_violations(connection)
        counts['bridge_cardinality_refusals'] = len(bridge_conflicts)
        for conflict in bridge_conflicts:
            counts['bridge_cardinality_refusals_' + conflict['bridge']] += 1
        connection.execute('CREATE INDEX ids_value ON ids(namespace, value, scope)')
        refusals, within_dataset_duplicates = refuse_flagged_and_ambiguous_values(connection)
        counts.update(refusals)
        colliding = connection.execute(
            'SELECT namespace, value, scope FROM ids GROUP BY namespace, value, scope '
            'HAVING COUNT(DISTINCT subject) > 1').fetchall()
        counts['colliding_identifier_values'] = len(colliding)
        # Attribution: how many surviving bridge claims actually met another publisher's entity.
        for bridge, joined in connection.execute(
                'SELECT i.bridge, COUNT(*) FROM (SELECT namespace, value, scope FROM ids GROUP BY namespace, value, '
                'scope HAVING COUNT(DISTINCT subject) > 1) c JOIN ids i ON i.namespace=c.namespace AND i.value=c.value '
                'AND i.scope=c.scope WHERE i.bridge IS NOT NULL GROUP BY i.bridge'):
            counts['bridge_joined_' + bridge] = joined
        shared, batch = [], []
        for namespace, value, scope in colliding:
            for row in connection.execute(
                    'SELECT subject, record, dataset FROM ids WHERE namespace=? AND value=? AND scope=?',
                    (namespace, value, scope)):
                batch.append({'kind': 'assertion', 'id': row[1], 'subject': row[0],
                              'predicate': 'identifier_assignment', 'observed_at': '1970-01-01',
                              'value': {'namespace': namespace, 'value': value,
                                        **({'scope': scope} if scope else {})}})
        result = shared_identifier_links(batch, namespaces=namespaces, observed_at=now(),
                                         evidence=[{'input': {'dataset': 'world_evidence'}, 'locator': 'identity_links'}])
        for assertion in result['assertions']:
            shared.append((assertion['subject'], assertion['object'],
                           'deterministic:shared_identifier', assertion['id'],
                           assertion['match']['features'].get('namespace')))
        connection.executemany('INSERT INTO edges VALUES (?,?,?,?,?)', shared)
        connection.commit()
        counts['shared_identifier_links'] = len(shared)
        counts['identifier_conflicts'] = len(result['conflicts'])
        edges = connection.execute('SELECT subject, object, method, record, dataset FROM edges').fetchall()
        return {'edges': edges, 'counts': dict(counts), 'conflicts': result['conflicts'][:200],
                'bridge_conflicts': bridge_conflicts[:200], 'bridges': sorted(BRIDGES) if bridges else [],
                'link_predicates': ['same_as'] + (sorted(LINK_PREDICATES) if bridges else []),
                'within_dataset_duplicates': within_dataset_duplicates[:200],
                'namespaces': sorted(namespaces), 'workdir': str(workdir)}
    finally:
        links.close()


def identity_clusters(edges, *, max_cluster_size=5000):
    """Connected components over asserted ``same_as`` edges, with the smallest ID canonical.

    Components larger than ``max_cluster_size`` are reported and dropped rather than merged:
    a runaway component is a data problem, not an identity.
    """
    parent = {}

    def find(node):
        parent.setdefault(node, node)
        root = node
        while parent[root] != root:
            root = parent[root]
        while parent[node] != root:
            parent[node], node = root, parent[node]
        return root

    for subject, obj, *_ in edges:
        a, b = find(subject), find(obj)
        if a != b:
            parent[max(a, b)] = min(a, b)
    members = {}
    for node in list(parent):
        members.setdefault(find(node), []).append(node)
    clusters, oversized = [], []
    for canonical_id, group in sorted(members.items()):
        if len(group) < 2:
            continue
        if len(group) > max_cluster_size:
            oversized.append({'canonical_id': canonical_id, 'size': len(group)})
            continue
        clusters.append({'canonical_id': canonical_id, 'members': sorted(group)})
    return clusters, oversized


def resolve_identities(catalog, store, *, workdir, index=None, profile=DEFAULT_PROFILE, datasets=None,
                       domains=None, exclude=None, output_dataset='world_evidence', attach=True,
                       progress=2_000_000, max_cluster_size=5000, bridges=True, measure=True):
    """Run the deterministic identity layer over the real catalog and attach it to the index.

    Everything here is *asserted*: published ``same_as`` rows, published unique identifiers and
    published crosswalk fields (:mod:`worldmodel.resolution.bridges`). Name-similarity matches are
    inferred, belong to :mod:`worldmodel.resolution.engine`, and are deliberately not attached by
    this command; see ``examples/graph-queries/resolution_evaluation.py``.
    """
    plan = scope(catalog, store, profile=profile, datasets=datasets, domains=domains, exclude=exclude)
    if not plan['selected']:
        raise ValueError('Selected scope contains no dataset with a published output stage')
    index_path = Path(index) if index else store.root / output_dataset / 'index.sqlite'
    monitor = Progress(every=progress, total=plan['selected_rows']) if progress else None
    started = time.time()
    result = identity_links(store, plan['selected'], workdir=workdir, progress=monitor, bridges=bridges)
    clusters, oversized = identity_clusters(result['edges'], max_cluster_size=max_cluster_size)
    inputs = [dict(item['ref']) for item in plan['selected']]
    policy = {'links': 'published same_as assertions, shared unique identifiers and published '
                       'crosswalk fields (resolution.bridges) only',
              'inferred_matches_attached': False, 'max_cluster_size': max_cluster_size,
              'bridges': result['bridges'], 'namespaces': result['namespaces'],
              'link_predicates': result['link_predicates'],
              'namespace_aliases': dict(NAMESPACE_ALIASES) if bridges else {},
              'refusals': {'fraudulent_values': 'refused on the flagged record and its published designation twins',
                           'imo_series': 'imo on a non-vessel subject is typed imo_company',
                           'duplicate_codes_within_a_dataset': sorted(REFUSE_DUPLICATES_WITHIN_A_DATASET)}}
    view = {'policy': policy, 'input_digest': digest(inputs), 'model_digest': digest(policy),
            'view_digest': digest([inputs, policy, [c['canonical_id'] for c in clusters], len(clusters)])}
    report = {'scope': _scope_report(plan), 'counts': result['counts'], 'clusters': len(clusters),
              'resolved_entities': sum(len(c['members']) for c in clusters),
              'largest_cluster': max((len(c['members']) for c in clusters), default=0),
              'oversized_components': oversized, 'identifier_conflicts': result['conflicts'],
              'bridges': {name: dict(BRIDGES[name], spec=MAPPING_SPECS[BRIDGES[name]['spec']])
                          for name in result['bridges']},
              'link_predicates': {name: LINK_PREDICATES[name] for name in result['link_predicates']
                                  if name in LINK_PREDICATES},
              'bridge_conflicts': result['bridge_conflicts'],
              'within_dataset_duplicates': result['within_dataset_duplicates'],
              'view': view, 'inputs': inputs, 'seconds': round(time.time() - started, 1),
              'peak_rss_bytes': peak_rss(),
              'interpretation': ('Clusters join entity IDs that published sources assert are the same thing. '
                                 'No name-similarity match is included; nothing is merged in the source records.')}
    with (Path(workdir) / 'clusters.jsonl').open('w', encoding='utf-8') as stream:
        for cluster in clusters:
            stream.write(json.dumps(cluster, sort_keys=True) + '\n')
    atomic_json(Path(workdir) / 'view.json', view)
    if measure:
        # How much of this scope's entity universe the clusters actually join across datasets.
        from .resolution.join_coverage import publisher_families, scope_join_coverage
        coverage = scope_join_coverage(Path(workdir) / 'identity.sqlite', workdir=Path(workdir) / 'coverage',
                                       clusters=Path(workdir) / 'clusters.jsonl',
                                       families=publisher_families(catalog.root), top=15)
        report['join_coverage'] = {key: coverage[key] for key in ('totals', 'by_domain', 'by_entity_type',
                                                                   'top_dataset_combinations')}
    if attach:
        if index_path.is_file():  # the resolution being replaced stays re-attachable
            from .resolution.history import export_resolution
            report['previous_resolution'] = export_resolution(index_path, Path(workdir) / 'previous_resolution')
        report['attached'] = Graph(index_path).attach_resolution(clusters, view=view)
        report['index'] = str(index_path)
    return report


# -- publishing a summary for an index that already exists ----------------------------------------

def index_summary(index_path):
    """Recompute a unify summary from an existing index.

    Everything except ``records_read`` and per-dataset timings is recoverable from the index
    itself: the pinned inputs live in ``metadata.inputs`` and the per-dataset kind counts are a
    grouped scan of ``records``. Use this after a build whose publish step failed (for example
    because the unify source changed mid-build and the code snapshot check refused to publish),
    or to publish a summary for an index built by hand.
    """
    import sqlite3
    index_path = Path(index_path)
    if not index_path.is_file():
        raise ValueError('No index at ' + str(index_path))
    connection = sqlite3.connect(index_path.resolve().as_uri() + '?mode=ro', uri=True)
    try:
        version = connection.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()
        if not version or version[0] not in READABLE_SCHEMAS:
            raise ValueError('Index schema is not readable; rebuild with unify')
        pinned = connection.execute("SELECT value FROM metadata WHERE key='inputs'").fetchone()
        inputs = json.loads(pinned[0]) if pinned else []
        census = connection.execute("SELECT value FROM metadata WHERE key='publication_coverage'").fetchone()
        coverage = json.loads(census[0]) if census else None
        counts = {}
        for dataset, stage, ref_version, kind, total in connection.execute(
                'SELECT dataset, stage, version, kind, COUNT(*) FROM records GROUP BY 1, 2, 3, 4'):
            row = counts.setdefault((dataset, stage, ref_version), Counter())
            row[kind] += total
        edges = connection.execute('SELECT COUNT(*) FROM edges').fetchone()[0]
        records = connection.execute('SELECT COUNT(*) FROM records').fetchone()[0]
        resolved = connection.execute('SELECT COUNT(*) FROM resolved').fetchone()[0]
    finally:
        connection.close()
    datasets = []
    for (dataset, stage, ref_version), row in sorted(counts.items()):
        datasets.append({'dataset': dataset, 'stage': stage, 'version': ref_version,
                         'records_indexed': sum(row.values()), 'entities': row['entity'],
                         'assertions': row['assertion'], 'observations': row['observation'],
                         'events': row['event']})
    return {'index': {'path': str(index_path), 'records': records, 'edges': edges,
                      'resolved_entities': resolved, 'bytes': index_path.stat().st_size},
            'publication_coverage': coverage,
            'datasets': datasets, 'inputs': inputs,
            'totals': {'records_indexed': sum(d['records_indexed'] for d in datasets),
                       **{key: sum(d[key] for d in datasets)
                          for key in ('entities', 'assertions', 'observations', 'events')}},
            'reconstructed_from_index': True,
            'limitations': ['Recomputed from the index: records read and per-dataset timings are not '
                            'recoverable, only records indexed.',
                            'Records are copied verbatim from each dataset\'s published output stage; nothing '
                            'is merged, deduplicated or reconciled by this build.']
            + ([] if coverage else ['This index predates graph schema %s and carries no publication dates, so it '
                                    'cannot answer a --known-at query; see docs/point-in-time-graph.md.'
                                    % PUBLICATION_SCHEMA])}


def publish_existing_index(store, index_path, *, output_dataset='world_evidence', parameters=None):
    """Publish the pinned summary artifact for an index that is already on disk."""
    summary = index_summary(index_path)
    if not summary['inputs']:
        raise ValueError('Index metadata pins no inputs; rebuild with unify')
    summary['artifact'] = publish_scope(store, output_dataset, summary,
                                        {'index': str(index_path), 'reconstructed_from_index': True,
                                         **(parameters or {})},
                                        summary['inputs'], entrypoint='worldmodel.unify:publish_existing_index')
    return summary
