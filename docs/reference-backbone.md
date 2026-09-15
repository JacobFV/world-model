# Reference backbone

The reference graph joins public people, dated roles, organizations, market venues,
listing snapshots and explicit parent relationships to the existing strategic evidence.
It preserves source records and exact input/code lineage. Names are searchable labels;
only published identifiers and explicit equivalence claims connect identities.

## Acquired on September 15, 2026

| Source | Retained rows | Retained bytes | Temporary download bytes |
| --- | ---: | ---: | ---: |
| ISO MIC selected US venues | 11 | 5,106 | 589,498 |
| Nasdaq listed directory | 100 | 19,405 | 348,098 |
| GLEIF direct parent relationship | 1 | 1,157 | 1,238 |
| Community Congress directory | 100 | 328,271 | 1,468,926 |
| OpenAlex authors | 5 | 107,070 | 114,457 |

Each source has its own `data/<source>/dataset.json` with selection criteria and
limits. Samples retain at most 100 rows and 1 MiB. Downloads serialize through the
shared 64 MiB temporary buffer; complete source files are discarded after sampling.
Normalized output can contain many graph records per sampled row, especially term
histories and publication affiliation years. These limits do not cap total graph size.

The joined `reference_evidence` artifact contains **17,528 records, 4,255 distinct
source/canonical entity IDs, 7,353 relation assertions and 3,361 observations**.
There are 233 person IDs and 1,909 role IDs. Person IDs include crosswalk aliases;
these counts are not distinct humans or estimates of world coverage.

ISO records include XCHI (NYSE Texas), XBOS (Nasdaq Texas), and TXSE/TXSD (Texas
Stock Exchange), with published status and dates preserved. MIC registration status
alone does not establish current operation. The exact `mic:XNAS` entity connects
records from both ISO and Nasdaq. Directory listings point to source-scoped security
references; absent permanent instrument and issuer IDs remain unresolved.

Congress data comes from the community-maintained congress-legislators directory.
Terms retain source start/end dates, chamber and affiliation. OpenAlex publication
affiliation years do not imply employment, continuous presence or travel. No new
person movement evidence was acquired.

## Commands

```sh
# Inspect verified samples; rerun acquisition only when wanted.
python3 -m worldmodel reference-sources
python3 -m worldmodel reference-sources --sample --allow-network

# Rebuild offline from immutable samples and the pinned strategic graph.
python3 -m worldmodel reference-build
python3 -m worldmodel coverage
python3 -m worldmodel verify reference_evidence
python3 -m worldmodel verify reference_coverage

# Explicit world-time and knowledge-time cutoffs.
python3 -m worldmodel resolve mic TXSE --at 2026-09-15 --known-at 2026-09-16
python3 -m worldmodel resolve ticker AAPL --scope XNAS --at 2026-09-15 --known-at 2026-09-16
python3 -m worldmodel search-entities Cantwell --at 2026-09-15 --known-at 2026-09-16
python3 -m worldmodel neighbors mic:XNAS --hops 1 --limit 20

# Invented financial state and obligations, with optional real identity links.
python3 -m worldmodel exposure --request examples/exposures.json
python3 -m worldmodel exposure --request examples/exposures-reference.json --graph reference_evidence --dataset reference_exposure_scenario
```

An undated ticker snapshot returns `temporal_unknown`, with candidates and evidence,
instead of claiming that the symbol existed in a historical or future year. Fully
dated assignments support historical reuse, scoped lookup and ambiguity. Durable
identifiers may resolve while still disclosing unknown temporal validity. Lookup
returns explicit equivalence components without rewriting source records. The build
rejects type-conflicting equivalence and contradictory unique identifiers before publication.
`examples/identity.json` demonstrates synthetic ticker reuse and ambiguity; its
placeholder evidence is for the direct Python example and must be rebound to a raw
artifact before publishing those records.

## Financial exposure adapter

The stress model evaluates supplied USD obligations under a fixed/floating rate shock.
It propagates liquidity shortfalls through a bounded, simultaneous proportional net
settlement and checks cash conservation. The example has a baseline shortfall of
$4,500 and a stressed shortfall of $4,950 after a 300 basis-point shock. These are
invented scenarios, including the obligations in the example that uses real entity IDs.

For evidence-backed initial state, use `exposure --evidence --graph <artifact>
--request <file>`. The request selects records, not inferred balances:

```json
{
  "as_of": "2026-09-15", "known_at": "2026-09-16",
  "end": "2027-09-15", "currency": "USD", "shock_bps": 300,
  "entities": [
    {"id": "org:borrower", "cash_record": "observation:borrower_cash"},
    {"id": "org:lender", "cash_record": "observation:lender_cash"}
  ],
  "obligations": [{"id": "contract:loan", "terms_record": "assertion:loan_terms"}]
}
```

Cash records must be observed `cash` values in USD. A `financial_obligation` entity's
`obligation_terms` assertion has unit `null` and an object containing exactly
`borrower`, `lender`, `principal`, `annual_rate`, `rate_type`, `maturity`, and `currency`.
Both state records must have explicit valid intervals covering `as_of` and observation
times no later than `known_at`. Rates use fractions per year; currencies must be USD.
Every borrower and lender needs explicit cash state. Missing, stale, mismatched or
synthetic evidence fails. The Store verifies the graph and its lineage before selection.
**No such real obligation network has been acquired yet.**

The model uses ACT/365 simple interest and one terminal net settlement. It has no
collateral priority, recovery process, intermediate payments, FX conversion or new
funding. Cyclic obligations can offset through netting; this is not a gross settlement
simulation. Shortfalls are not calibrated default probabilities.

## Coverage and gaps

`reference_coverage` publishes three independent maps:

1. Identity coverage: source IDs, identifier assertions and unresolved references.
2. Evidence coverage: actual observations, relations and source scopes; forecasts excluded.
3. Validation: executable process counts and existing descriptive holdout results.

All samples are nonrepresentative and completeness is unknown. Existing illustrative
kernels are not declared causally validated. The prior WTI holdout remains in the
coverage artifact; its AR(1) model did not outperform persistence.

Explicit source blockers remain for SEC issuer references (configure a real
`SEC_USER_AGENT` contact identity), index constituents, corporate actions, security
prices and bilateral obligations. The catalog describes access/entitlement or extraction
requirements. Dow is an index, not an exchange; no Dow membership was acquired.
Global roads, ocean transits, all public figures, complete ownership, court corpora,
posts, war observations and calibrated cross-domain causal dynamics remain incomplete
or absent. Existing declarations and simulation kernels do not fill those evidence gaps.

See [strategic systems](strategic-systems.md) for fields/topologies, routing, actor
kernels, lifecycle, banking, materialization and other implemented strategic capabilities.
See [verification results](reference-verification-2026-09-15.json) for exact artifacts.
