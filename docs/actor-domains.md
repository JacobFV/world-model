# Additional actor domains: campaign contributions and bank distress

Two more actor domains for the world-state encoder, built to the pattern of
[`worldmodel/embedding/votes.py`](../worldmodel/embedding/votes.py): a domain module builds a
fixed-template dated subgraph per sample with a binary target, an adapter in
[`actors_assay.py`](../worldmodel/embedding/actors_assay.py) hands it to the existing probability
runner, and an attempt in [`plan.json`](../worldmodel/embedding/plan.json) fixes everything that
will be scored before anything is scored. See
[world-state-embeddings.md](world-state-embeddings.md) for the encoder itself and the protocol.

| Domain | Module | Sample | Label | Periods |
| --- | --- | --- | --- | --- |
| Campaign contributions | `worldmodel/embedding/domains/fec.py` | (giving committee, recipient candidate committee, cycle) | gives to the same recipient again next cycle | two-year cycles |
| Bank distress | `worldmodel/embedding/domains/fdic.py` | (bank, quarter) | a declared distress marker in next quarter's call report | quarters |

Both need the optional `embed` extra (numpy). Neither attempt has been run; both are registered and
waiting for the GPU queue.

## Campaign contributions

From the `fec` dataset's `committee_to_candidate_amount` observations — the FEC's itemized `pas2`
contributions, aggregated by the pipeline to committee × candidate × transaction type × calendar
month. A pair is a sample at cycle **C** when the giver made at least one *direct* contribution to
that recipient committee during C; the label is 1 when it makes at least one again during **C+2**.
Read from the label's side that is exactly "the giver gave in the previous cycle — does it give
again": the forecast origin sits between the two cycles.

*Direct* means money to the campaign: a contribution (`24K`), an in-kind contribution (`24Z`) or a
coordinated party expenditure (`24C`). Independent expenditures and communication costs (`24E`,
`24A`, `24F`, `24N`) are features but never the label and never the eligibility condition, because
by law they are not coordinated with the candidate.

**Dating rule.** Every amount is dated by its published transaction month. A cycle's giving is
declared public on **31 January of the year after the cycle ends** — the due date of the year-end
report that closes the cycle. That date is the origin: features come from cycle C and earlier, the
label from C+2, dated at C+2's own public date.

**Late and amended filings**, handled explicitly rather than assumed away:

* a transaction whose month falls **outside the window of the cycle file carrying it** (cycle C runs
  1 January C−1 to 31 December C) is money reported a cycle or more late. It is dropped and counted:
  650 of 3.23M groups;
* a group the publisher could not date (`period: cycle_date_unknown`) is dropped and counted: 2,390;
* a group with no recipient committee id is dropped and counted (131,450, 3.9%): nothing is joined
  except on published identifiers;
* an **amendment filed into the cycle's own file after the cycle's public date cannot be
  distinguished** — the bulk release carries no per-transaction filing or amendment date. The
  attempt declares `revision_leakage_possible` for the series and carries a named allowance on
  `no_revision_leakage`, so a pass on that criterion is *not* evidence that no revision entered.

**Template (19 nodes).** The pair; the giver; the recipient committee; the giver's eight largest
other recipients in cycle C; the recipient's eight largest other givers in cycle C. Edge weights are
the neighbour's share of the giver's (or the recipient's) direct total, so a token relationship
pulls less than a principal one. Node features are 32: pair amounts, transaction counts, shares of
each side's total, independent-expenditure amounts, same state and same party; giver totals,
distinct recipients, mean gift, repeat rate, party and committee type; recipient totals, distinct
givers, retention rate, independent expenditures for and against, party and office. Each is measured
over four cycles of history.

**Baselines.** The training base rate (`historical_mean`), the giver's own repeat rate into cycle C
(`giver_repeat_rate` — of the recipients it gave to in C−2, the share it gave to again in C), and
LightGBM on the same template features (`gbdt`).

Measured on the pinned version, 400 sampled pairs per cycle:

| Cycle | 2004 | 2006 | 2008 | 2010 | 2012 | 2014 | 2016 | 2018 | 2020 | 2022 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| base rate | 0.440 | 0.403 | 0.395 | 0.405 | 0.400 | 0.448 | 0.438 | 0.470 | 0.430 | 0.398 |

The own-rate baseline exists for 93–99% of samples (a giver with no prior cycle has none). At the
registered sampling — 8,000 pairs per cycle, at most 50 per giving committee — the attempt covers
ten cycles, four of them test.

## Bank distress

From `fdic_bank_financials` call reports: 8,142 charters, 66 report dates from 2010Q1 to 2026Q2,
7.71M kept observations across 20 published fields. A sample is one (bank, quarter) that filed for
both `q` and `q+1` with positive loans and deposits in both.

**The distress marker**, declared before any result, fires when either component does:

* **noncurrent onset** — the noncurrent-loan ratio (`bank_noncurrent_loans` over `bank_net_loans`)
  is above 3% at `q+1` and at or below 3% at `q`. It is a *crossing*, not a level, so an
  already-impaired bank does not carry a label of 1 forever;
* **deposit outflow** — deposits at `q+1` are below 90% of deposits at `q`.

`bank_net_loans` is net of the allowance for credit losses, so the ratio runs slightly above a
gross-loan ratio; the threshold is declared against the published quantity, not a textbook one.

Over 2012Q1–2024Q4, 289,781 samples in 52 quarters: base rate **2.77%** — noncurrent onset 1.60%,
deposit outflow 1.21%. Quarterly base rates run from 1.5% to 4.7%, highest in first quarters.

**Dating rule.** A call report is due 30 calendar days after its quarter end and the FDIC's published
financials follow; a quarter is declared public at **report date + 60 days**, which is the origin.
Features come from report dates on or before `q`; the label, quarter `q+1`, is dated at its own
report date + 60 days.

**Amendments.** The FDIC financials API serves one value per bank, report date and field, with no
filing date and no amendment flag. An amended call report **cannot** be distinguished from the
original. The 13F domain excludes amendments because that source marks them; here there is nothing
to exclude on, so the rule is stated rather than applied: values are the publisher's current ones,
a bank that restated a quarter is represented by the restatement, and the attempt declares
`revision_leakage_possible` with a named allowance on `no_revision_leakage`. Amendments are common
in the quarter or two after filing and rare later, which bounds the risk without removing it.

**Template (17 nodes).** The bank at quarter `q`; up to eight other banks chartered in the same
state, the largest by total assets at `q`; up to eight banks nationally whose log total assets at
`q` are nearest to the bank's own. Edge weights are the size ratio of the two banks. In practice
samples get 7.97 state peers and 8.00 size peers on average. Node features are 23: log assets,
deposit, uninsured-deposit, brokered-deposit, loan, securities, cash and equity ratios; the
noncurrent ratio and its change; tier 1 leverage and total risk-based capital; commercial real
estate to equity; C&I, consumer and residential shares of loans; year-to-date income and charge-offs;
quarter-on-quarter deposit, asset and loan growth; employees; and the quarter of the year (the
published flows are year-to-date, so the model must be told where in the year it is). Ratios whose
denominator can be near zero are clipped at 10,000 before the runner's half-precision cast.

**Baselines.** The training base rate (`historical_mean`), the bank's own marker rate over the
previous eight quarter transitions (`bank_rate`, available for every sample), and LightGBM on the
same template features (`gbdt`).

## Pipeline check, not a result

Both domains were run end to end through the unmodified runner on **training-period periods only** —
every block below sits inside the training window of the registered attempt — with a fraction of the
registered encoder (d 32, 1 layer, 4 slots, 2 passes, 1 epoch) and a fraction of the registered
sampling. These numbers say the code path works. They say nothing about whether the embedding adds
signal, and they are not comparable to a scored attempt.

| | campaign contributions | bank distress |
| --- | --- | --- |
| validation block / test block | 2010 / 2012 | 2016Q1–Q2 / 2016Q3–Q4 |
| samples (fit + score) | 2,000 | 30,000 |
| test forecasts | 400 | 3,000 |
| test base rate | 0.400 | 0.0293 |
| selected candidate | `gbdt_plus_self_supervised_embedding` | `gbdt_plus_self_supervised_embedding` |
| Brier, model | 0.1892 | 0.02384 |
| Brier, base rate | 0.2402 | 0.02849 |
| Brier, own rate | 0.2411 | 0.02898 |
| Brier, LightGBM | 0.1939 | 0.02416 |
| Brier skill | 0.212 | 0.163 |
| expected calibration error | 0.072 | 0.0086 |
| timing-leakage violations | 0 | 0 |
| wall clock (CPU, `gb10-direct`) | 18 s | 174 s |

Two things in that table are worth reading as warnings rather than encouragement. The calibration
error on the contributions check is 0.072 against a registered criterion of 0.02 — a one-epoch
encoder on 800 training rows should not be calibrated, and the registered run may well fail that
criterion too. And the bank-distress check already fails `beats_gbdt_dm`: the model's Brier is lower
than LightGBM's but not significantly so, which is the same shape as the actors v1 result.

## Registered attempts

| Attempt | Domain | Test blocks | Own-rate baseline | Status |
| --- | --- | --- | --- | --- |
| `actors.fec_repeat_contribution_v1` | fec | 2016, 2018, 2020, 2022 cycles | `giver_repeat_rate` | ready to queue |
| `actors.fdic_bank_distress_v1` | fdic | 2021–2024, by year | `bank_rate` | ready to queue |

Both use the same three candidates as `actors.13f_exit_increase_v2` — `root_readout`,
`gbdt_plus_self_supervised_embedding`, `gbdt_plus_crossfit_encoder` — the same criteria plus the
own-rate Diebold-Mariano test above, and a declared compute budget of 120 minutes each. Run them
one at a time per GPU, as the plan's rules require:

```sh
systemd-run --user --scope -p MemoryMax=40G -p MemorySwapMax=0 \
    .venv/bin/python -m worldmodel embed-assay actors.fec_repeat_contribution_v1 --save run.json
```

## What these domains do not establish

* **Nothing causal.** Out-of-sample skill is predictive association. Neither target responds to any
  intervention, and no result here licenses a claim about what would happen if a committee, a
  candidate or a regulator acted differently.
* **Nothing about failure.** The bank-distress label is a declared marker, not a supervisory
  determination: not a failure, a closure, a consent order or a CAMELS downgrade. Samples are
  conditioned on the bank filing again next quarter, so a charter that disappears between quarters —
  through failure or merger — leaves no sample at all. The most severe outcome is outside the
  target by construction. Coverage begins at 2010Q1, so the 2008–2009 failures are outside the
  panel entirely.
* **Nothing about influence.** The contributions label is whether money moves again, not whether it
  bought anything. A recipient committee that does not run again is labelled 0, so the task mixes
  the giver's choice with the candidate's, and a model can score well by forecasting retirements.
* **Nothing about a population.** Contributions are sampled at most 50 per giving committee and
  8,000 per cycle; scores describe that sampling. Pooled Diebold-Mariano tests treat samples within
  a period as independent, which they are not; `test.quarter_clustered_dm` is the conservative check
  beside them, and on the contributions attempt it has three degrees of freedom — weak by
  construction, because a cycle is two years long.
* **Nothing about revisions.** Both attempts carry an explicit allowance on `no_revision_leakage`,
  because neither publisher marks an amendment in the release we read. A pass on that criterion
  means the allowance was declared, not that no revision entered.
