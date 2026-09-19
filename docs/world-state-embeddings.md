# World-state embeddings

`worldmodel.embedding` embeds any dated subgraph of the evidence catalog into a state vector,
and scores what that vector is worth under the same pre-registered, leakage-audited protocol as
every estimator in this repository. The first domain is US counties, because it is the one domain
where identity is already clean: every source keys counties by FIPS code, so nothing has to be
resolved before a graph exists.

> Status: see [Results](#results). An encoder that forecasts well is not a validated model of
> anything until its attempt passes every declared criterion, and the first attempt was declared,
> before it ran, to fail `no_revision_leakage` whatever its skill.

## What it is

```text
published normalized datasets ──> county_panel (dated values + dated edges)
                                        │   as-of cut at origin T-12-31
                                        v
                 subgraph around a seed county (≤ 64 nodes: seed, state, nation,
                 geographic neighbours, migration partners, CBSA peers, 2-hop geography)
                                        │
   feature-history tokens ─> node pooling ─> 3 relation-gated message-passing layers
                                        │
   root readout: 16 slot queries x 12 passes of top-k (16) sparse attention from the seed
                                        │
                    1,024-d state vector + 16 slot vectors
                          │                         │
             Student-t forecast head      masked-token reconstruction
```

| Piece | Code | Notes |
| --- | --- | --- |
| Dated panel | `worldmodel/embedding/county_panel.py` | stdlib; publishes the `county_panel` derived dataset |
| As-of tensors | `worldmodel/embedding/tensors.py` | numpy; snapshots, subgraphs, anchors |
| Encoder | `worldmodel/embedding/model.py` | torch |
| Fitting | `worldmodel/embedding/train.py` | refit per origin, split-conformal scales |
| Assay | `worldmodel/embedding/assay.py`, `plan.json` | pre-registered attempts, published reports |
| Places query | `worldmodel/embedding/query.py` | nearest embedded states across time |

Install the optional extra (`pip install "worldmodel-substrate[embed]"`: numpy, torch, lightgbm).
`wm embed-panel` needs none of it.

```sh
python3 -m worldmodel embed-panel                                  # build + publish county_panel
python3 -m worldmodel embed-assay                                  # list the pre-registered plan
python3 -m worldmodel embed-assay places.county_root_readout_v1    # run and publish one attempt
python3 -m worldmodel embed-query geo:US:county:48453 --as-of 2023 \
    --checkpoint data/embedding_reports/scratch/checkpoints/places.county_root_readout_v1.root_readout.2023.pt
```

## The panel, and the one rule that matters

`county_panel` holds one observation per county × feature × year, and every one carries
`dimensions.available_at`: the date the value is treated as public. An as-of reader fills a
history slot only if `available_at` is on or before its origin. The dates come from a declared
publication rule per source (in the dataset's `report.json`):

| Source | Features | Lag after the reference year | Revisions |
| --- | --- | --- | --- |
| QCEW (`bls_labor`, agglvl 70/74) | employment, establishments, wages, pay; private employment by NAICS sector | 9 months (end of September y+1) | minor |
| LAUS (`bls_labor`) | employment, labor force, unemployed, unemployment rate | 4 months | major (annual benchmark) |
| BEA county (`bea_national_regional`) | personal income and its components, population, GDP, real GDP, commuting earnings flows | 12 months | major |
| nClimDiv | temperature, precipitation, PDSI | 1 month | minor |
| NOAA storm events | property and crop damage, deaths, injuries, event count | 4 months | minor |
| OpenFEMA | declarations, major disasters | 0 (declaration date) | none |
| Census geography | internal point, land and water area | static (declared exception) | static |
| IRS SOI migration (edges) | county-to-county flows | 18 months after the second filing year | none |
| OMB CBSA delineations (edges) | CBSA membership | bulletin date (2018-09-14, 2023-07-21) | none |

Published version `county_panel@9991624a`: 3,490 counties (3,240 with QCEW employment, which
defines the node set), 58 features, 310,328 migration flows, 3,830 CBSA memberships, no duplicate
source values after the BEA industry totals rule. FIPS `SS000` (state totals) and `SS999` (QCEW's
unknown county) are excluded; states come from the FIPS code's own first two digits.

Two things the panel cannot remove, both recorded on every report:

* **Revision leakage.** Values are the current vintage at retrieval. QCEW, LAUS and BEA revise, so
  an origin in 2012 sees 2011 values as later revised. Real-time county vintages (ALFRED county
  series) are not in the catalog. The repository's `no_revision_leakage` criterion therefore fails
  for every attempt on this panel, by declaration, before it runs.
* **Declared, not measured, release dates.** A lag is a statement about a publisher's calendar.

## Design decisions and why

* **Inductive tokens, not node ids.** A token is one (node, feature) history window: values,
  availability masks and first differences, plus a learned feature embedding. No parameter is keyed
  to a county, so the encoder embeds subgraphs it has never seen, and scales to other node types
  by adding features, not rows.
* **Several latent slots, not one vector.** A single vector cannot reconstruct an arbitrary node of
  a 4,096-node subgraph. The readout keeps 16 slots, refined over 12 passes; the state vector is a
  projection of their mean, the first slot and the root.
* **Top-k starvation.** A node never selected early never learns. Training attends densely for the
  first 30% of steps, then sparsely, with Gumbel noise on the *selection* (not the weights) that
  decays to zero.
* **Fat tails by construction.** The forecast head is Student-t with learned degrees of freedom per
  target, and the scale is recalibrated by split conformal on the two most recent public label
  years, which are never trained on. Interval coverage is the repository's most common failing
  criterion; this is the response to it, fixed before scoring.
* **Loss balance.** Masked reconstruction weighs every (node, feature) window equally, so no single
  source dominates. One forward pass serves both losses, and masking touches only the seed's
  neighbours, so the forecast head always sees the seed's own history.
* **The honest baseline.** A gradient-boosted model (LightGBM) receives the same subgraph: the
  seed's history and differences, neighbour means, state and nation slots, the same training and
  calibration rows. Graph models on tabular-heavy data often lose to exactly this.

## Protocol

Frozen in [`worldmodel/embedding/plan.json`](../worldmodel/embedding/plan.json) and committed before
any validation or test origin was scored.

* **Samples.** (origin year t, seed county). Labels: each target's log change from its *anchor* (the
  latest value public at t-12-31, which is year t-1 for QCEW and BEA) to year t+1: two annual steps.
* **Refit at every origin.** At origin T every forecaster is fit on labels public by T-12-31; the two
  most recent label years calibrate scales and are not trained on.
* **Selection on validation only.** Candidates: the encoder on the full subgraph (`root_readout`) and
  on the seed alone (`seed_only`, the same network without the graph). Lower validation MSE wins;
  the selection is hashed; the test window is scored once.
* **Criteria.** Minimum forecasts; Diebold-Mariano (squared loss, p < 0.10, lower loss) against
  persistence, drift and the gradient-boosted model; interval coverage 0.80 ± 0.15; no timing
  leakage (counted from the arrays); no revision leakage. `parameter_bounds` is replaced by the two
  extra skill tests, since the encoder has no structural parameters to bound.
* **Robustness beside the criterion.** Pooled DM tests treat county-years as independent; a common
  shock such as 2020 makes them overstate significance. Each report carries
  `test.year_clustered_dm`, a t-test on per-year mean loss differences.

## Actors: 13F positions

The same encoder, on a different graph. `worldmodel/embedding/holdings.py` extracts every holding
of every *original* 13F-HR filing in `sec_13f_history` into a version-pinned array cache:
71,968,683 rows, 11,977 managers, 130,215 securities, 49 quarters (2013Q2-2025Q2). The 4,867,350
rows of amendments (13F-HR/A) are excluded, since a restatement can arrive long after the quarter.
7,714,116 original rows were filed after their 45-day deadline; they label outcomes but never enter
features.

`worldmodel/embedding/actors.py` turns it into two quarterly decisions. A sample is one reported
position (manager m, security s) at its quarter's filing deadline:

* **exit**: s is absent from m's next original filing;
* **increase**: s is kept and m's log change in shares beats, by 0.1, the median log change of every
  holder that kept s (so splits, which move every holder at once, are not "increases").

Every sample has the same template subgraph, so batching is pure GPU indexing: the query position
(node 0, the root), the manager, the security, the security's top 12 other holders' positions, and
the manager's top 12 other positions with their securities. Node features are four-quarter
histories: position shares, value, portfolio weight, presence and share change; manager value,
position count, exit and entry rates; security 13F value, holder count, holder exit and entry rates.

Baselines: the training base rate (the Brier-skill reference), the manager's own exit rate at the
origin, and LightGBM on the same template features. Criteria: Diebold-Mariano on Brier loss against
the base rate and LightGBM (and the manager's rate, for exit), positive Brier skill, expected
calibration error ≤ 0.02, and no timing or revision leakage (original filings only, so no
revision enters). The worst-bin calibration deviation is reported but not judged: a bin holding a
handful of forecasts decides it.

## Actors: roll-call votes

The second actor domain, on the same runner and the same candidates, so the question "does the
embedding add signal?" is asked of a different kind of decision. A sample is one member's yea or
nay on a *party-unity* roll call (a majority of each party on opposite sides); the label is voting
against their own party's majority, which happens on about 4% of them. The origin is the roll call's
date, and every feature comes from roll calls held strictly before it.

Excluded on purpose: DW-NOMINATE and Nokken-Poole scores and roll-call midpoints, which Voteview
re-estimates from later votes; and cosponsor lists, which the published data carries without dates,
so which cosponsors had signed before a given vote is unknown. That removes the strongest available
signal of a bill's bipartisanship, and the registration says so.

Template (20 nodes): the (member, roll call) pair, the member, the roll call, the bill's sponsor,
the eight members who agreed most with the member on unity votes in the two years before the
quarter began, and up to eight of their state delegation. Baselines: the training base rate, the
member's own defection rate over the previous year, and LightGBM on the same features.

## Running it

The home GB10 is shared; heavy runs go to the dedicated second GB10 (`ssh gb10-direct`), with the
repository and `.venv` mirrored at the same path and every job inside a cgroup memory cap:

```sh
systemd-run --user --scope -p MemoryMax=40G -p MemorySwapMax=0 \
    .venv/bin/python -m worldmodel embed-assay places.county_root_readout_v2 --save run.json
python3 -m worldmodel embed-publish run.json      # at home, from a checkout at the same commit
```

`embed-publish` re-derives each report id before writing, and every report records the commit and
whether `worldmodel/` was modified when it ran. A GPU memory fraction (`WM_EMBED_GPU_GB`) is not a
host-memory cap on a GB10: CPU and GPU share one pool, which is why the cgroup is required.

## The places query

`wm embed-query` embeds every county at every origin with a saved encoder and ranks the states
nearest to one county's state now. Each candidate is embedded from what was public at *its own*
origin, so a match across time compares like with like.

```sh
python3 -m worldmodel embed-query geo:US:county:48453 --as-of 2023 --across time --limit 10 \
    --checkpoint data/embedding_reports/scratch/checkpoints/places.county_root_readout_v3.root_readout.2023.pt
```

Travis County, Texas (Austin) as of 2023-12-31, nearest other counties at earlier origins
([full output](../examples/embedding/places-nearest-travis-county.json)):

| County | As of | Distance |
| --- | --- | ---: |
| 12021 Collier, FL | 2022 | 0.130 |
| 12071 Lee, FL | 2022 | 0.133 |
| 37183 Wake, NC (Raleigh) | 2022 | 0.148 |
| 48157 Fort Bend, TX | 2021 | 0.168 |
| 49035 Salt Lake, UT | 2022 | 0.172 |

The county's own earlier states are excluded by default (`--include-self` keeps them); they are
nearest by construction. The encoder is the one refit at the attempt's last origin
(`worldmodel/embedding/assay.py` saves it; it is not part of the scoring). Distance is Euclidean in
the 1,024-d state space, divided by the square root of the dimension.

What it does not establish: nearness is in a space trained to forecast employment, establishments
and population and to reconstruct masked features, not a similarity of everything a reader cares
about; and a past match does not mean the query county will follow that county's later path.

## Record of attempts

| Attempt | Status | Note |
| --- | --- | --- |
| `places.county_root_readout_v1` | not run (compute budget) | stopped at 29 min on the shared machine, projecting ~64 min against 60, before any validation score existed |
| `places.county_root_readout_v2` | not run (compute budget) | shared the second GB10's GPU with the actors attempt; stopped after one validation origin, before any score |
| `places.county_root_readout_v3` | **fail** (all three targets) | employment beats every baseline; all three fail the declared revision-leakage criterion; see below |
| `actors.13f_exit_increase_v1` | **fail** (both tasks) | beats the base rate, loses to LightGBM; see below |
| `actors.13f_exit_increase_v2` | **fail** (both tasks) | the embedding adds nothing to LightGBM; see below |
| `actors.votes_party_defection_v1` | **fail** | same pattern as 13F: beats the base rate and the member's own rate, does not beat LightGBM |
| `actors.fec_repeat_contribution_v1` | queued | will a committee give to the same recipient again next cycle |
| `actors.fdic_bank_distress_v1` | queued | will a bank's noncurrent ratio cross 3%, or deposits fall over 10% |

## Results

### places.county_root_readout_v3

Published `embedding_reports@8221e167` (employment), `@e0a5232d` (establishments), `@8afcd1ba`
(population). Test origins 2018-2023, refit at every origin, 19,320 county-year forecasts per
QCEW target. The full-subgraph encoder was selected over the seed-only one for all three targets on
validation. Wall clock 64 minutes on the dedicated GB10, peak GPU 6.1 GiB.

| Target | Encoder MSE | Persistence | Drift | LightGBM | 80% interval coverage |
| --- | ---: | ---: | ---: | ---: | ---: |
| QCEW employment | **0.00467** | 0.00496 | 0.00612 | 0.00564 | 0.748 |
| QCEW establishments | **0.00410** | 0.00488 | 0.00433 | 0.00411 | 0.694 |
| BEA population | 0.000437 | 0.000641 | **0.000388** | **0.000336** | 0.766 |

* **Employment** is the one real win: lower squared error than persistence, drift *and* LightGBM on
  the same subgraph, with interval coverage inside the declared tolerance. **But** the pooled
  Diebold-Mariano test that the criterion uses (p < 0.001 against all three) treats 19,320
  county-years as independent; the conservative per-year test over the six test years gives p = 0.29
  against persistence and p = 0.25 against LightGBM. On six years, the honest statement is that the
  encoder's *average* loss is lowest and the year-to-year evidence is not significant.
* **Establishments**: beats persistence and drift, ties LightGBM (p = 0.24).
* **Population**: beats persistence, loses to drift and LightGBM. County population is close to a
  smooth trend, which is exactly what drift extrapolates.
* **None is validated**, and all three fail for the reason declared before the run:
  `no_revision_leakage`. The panel holds current-vintage values, and QCEW, LAUS and BEA all revise.
  A real-time county panel (ALFRED county vintages) is being acquired; until then no attempt on this
  panel can pass, whatever its skill.
* The graph helps: the seed-only encoder was worse on validation for every target
  (0.00386 against 0.00422 for employment).

### actors.votes_party_defection_v1

Published `embedding_reports@26ca05a8`. 128,000 member-votes on party-unity roll calls in the
117th-118th Congresses, refit each year, base rate 4.65%.

| | Brier |
| --- | ---: |
| LightGBM + label-free embedding (selected) | 0.03715 |
| LightGBM | **0.03687** |
| member's own defection rate (previous year) | 0.04038 |
| training base rate | 0.04439 |

Brier skill 0.163 over the base rate, expected calibration error 0.0062, and it beats the member's
own rate decisively (p < 0.001, pooled and quarter-clustered). Against LightGBM on the same features
it is again a hair worse (p = 1.00). Every criterion passes except `beats_gbdt_dm`, so the attempt
is not validated.

The same three candidates were compared as on 13F and the same one won validation: the label-free
embedding stacked into LightGBM, ahead of the encoder alone and of the cross-fitted encoder.

### actors.13f_exit_increase_v2 — does the embedding add signal?

Published `embedding_reports@ff29521e` (exit) and `@9bf9e546` (increase). The question this attempt
exists to answer: the encoder alone lost to LightGBM in v1, so is the *embedding* useful to a model
that already sees the same inputs?

On validation, both stacked candidates beat the encoder alone, and LightGBM plus the **label-free**
embedding (32 principal components of a reconstruction-only encoder's state vectors) was selected
for both tasks. On the untouched test window it does not beat plain LightGBM:

| Task | LightGBM + embedding | LightGBM | Base rate | DM p (pooled / quarter-clustered) |
| --- | ---: | ---: | ---: | --- |
| exit | 0.088924 | **0.088763** | 0.105691 | 0.99 / 0.96 |
| increase | 0.141645 | **0.141247** | 0.158212 | 1.00 / 0.96 |

The differences are tiny (+0.0002 and +0.0004 Brier, the wrong way) and not significant either way.
The honest reading: **on these two 13F tasks the world-state embedding carries no signal that the
template's own tabular features do not already carry.** Both stacked candidates beat the encoder
alone, so the encoder was the weaker consumer of the same graph, not a producer of new information.
Calibration is good (expected calibration error 0.0044 and 0.0079), and every other criterion
passes, including the manager's-own-rate baseline that v1's scoring defect had made unevaluable.

### actors.13f_exit_increase_v1

Published `embedding_reports@e950075a` (exit) and `@00088be4` (increase). Test window 2021Q1-2024Q4,
refit each year, 128,000 test positions (8,000 per quarter; 112,635 with an increase label).

| Task | Test base rate | Encoder Brier | Base-rate Brier | LightGBM Brier | Brier skill | Expected calibration error |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| exit | 0.120 | 0.0905 | 0.1057 | **0.0888** | 0.144 | 0.0074 |
| increase | 0.197 | 0.1447 | 0.1582 | **0.1413** | 0.086 | 0.0053 |

* Against the base rate: better on both tasks, pooled and quarter-clustered DM p < 0.001.
* Against LightGBM on the same template features: worse on both (Brier higher by 0.0017 and 0.0034);
  `beats_gbdt_dm` fails. Neither task is validated.
* On validation (2019-2020) the full subgraph beat the encoder on the query position alone
  (exit 0.0961 against 0.1022, increase 0.1514 against 0.1566): the relational context carries
  signal. LightGBM, given hand-aggregated neighbour features, used it better.
* `beats_manager_rate_dm` failed as *unavailable*, not on skill: a scoring defect dropped that
  baseline because a few rows lack a prior-quarter rate. It is fixed for later attempts; the verdict
  does not depend on it, since the encoder already fails against LightGBM.
* The report records commit `397b384`; the run started at `70bb84c`. The compute host's checkout was
  synced during the run (the Python is identical between the two; only the plan changed). Commits
  are now captured at start, and runs use an exported, immutable code directory.
