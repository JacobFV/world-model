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

## Results

Filled in from the published reports once the attempt has run; see
[data/embedding_reports](../data/embedding_reports/README.md).
