"""Actor task from FDIC call reports: does a declared distress marker appear next quarter?

A *sample* is one (bank, quarter) pair: an FDIC-insured charter (``fdic:cert:<CERT>``) that filed a
call report for quarter ``q`` and for ``q+1``, with positive loans and deposits in both. The label
is 1 when the **declared distress marker** appears in the ``q+1`` report.

**The distress marker, declared before any result.** It fires when either component does:

* *noncurrent onset* -- the noncurrent-loan ratio (``bank_noncurrent_loans`` over ``bank_net_loans``,
  both as the FDIC publishes them) is above 3% at ``q+1`` and at or below 3% at ``q``. It is an
  onset, not a level, so a bank that is already impaired does not carry a label of 1 forever;
* *deposit outflow* -- total deposits at ``q+1`` are below 90% of deposits at ``q``.

``bank_net_loans`` is net of the allowance for credit losses, so the ratio runs slightly above a
gross-loan ratio; the threshold is declared against this published quantity, not a textbook one.

**Dating rule, declared before any result.** A call report for quarter end ``R`` is due 30 calendar
days after ``R``; the FDIC's published financials follow. Every quarter is therefore treated as
public on **R + 60 days**, which is the forecast origin for samples of quarter ``R``: features come
from report dates on or before ``R``, and the label (quarter ``R+1``) is dated at that quarter's own
``R + 60 days``.

**Amendments.** The FDIC financials API serves one value per (bank, report date, field) and carries
no filing date and no amendment flag, so an amended call report **cannot** be distinguished from the
original one in this release. The 13F domain excludes amendments because the source marks them; here
there is nothing to exclude on. The rule is therefore stated rather than applied: values are the
publisher's current ones, a bank that restated a quarter after the fact is represented by the
restatement, and the attempt declares ``revision_leakage_possible`` for this series. Amendments are
common in the quarter or two after filing and rare later, which bounds the risk without removing it.

Template subgraph (17 nodes): 0 the bank at quarter ``q``; 1-8 up to eight other banks chartered in
the same state, the largest by total assets at ``q``; 9-16 up to eight banks nationally whose log
total assets at ``q`` are nearest to the bank's own. Edge weights are the size ratio of the two
banks, so a peer of very different size pulls less.

Requires numpy.
"""
from dataclasses import dataclass
from datetime import date, timedelta
import gzip
import json
from pathlib import Path

import numpy as np

from ...estimation.loaders import catalog_ref, _records_path

DATASET = 'fdic_bank_financials'
FIRST_YEAR = 2010
PUBLIC_LAG_DAYS = 60
NONCURRENT_THRESHOLD = 0.03
DEPOSIT_OUTFLOW = 0.10
OWN_RATE_QUARTERS = 8
CLIP = 1e4
K = 8

METRICS = ('total_assets', 'bank_deposits', 'bank_uninsured_deposits', 'bank_brokered_deposits', 'bank_net_loans',
           'bank_loans_nonfarm_nonresidential_re', 'bank_loans_multifamily_re',
           'bank_loans_construction_land_development', 'bank_loans_residential_1_4_family',
           'bank_loans_commercial_industrial', 'bank_loans_consumer', 'bank_securities', 'bank_cash_and_due',
           'bank_equity', 'bank_noncurrent_loans', 'bank_net_income', 'bank_net_charge_offs',
           'bank_tier1_leverage_ratio', 'bank_total_risk_based_capital_ratio', 'bank_employees')
METRIC_INDEX = {name: i for i, name in enumerate(METRICS)}

NODE_TYPES = ('bank', 'state_peer', 'size_peer')
RELATIONS = ('bank_state_peer', 'state_peer_bank', 'bank_size_peer', 'size_peer_bank')
FEATURES = ('log_assets', 'deposits_to_assets', 'uninsured_to_deposits', 'brokered_to_deposits', 'loans_to_assets',
            'noncurrent_ratio', 'securities_to_assets', 'cash_to_assets', 'equity_to_assets', 'tier1_leverage',
            'total_risk_based_capital', 'cre_to_equity', 'ci_to_loans', 'consumer_to_loans', 'residential_to_loans',
            'ytd_net_income_to_assets', 'ytd_charge_offs_to_loans', 'deposit_growth', 'asset_growth', 'loan_growth',
            'noncurrent_change', 'log_employees', 'quarter_of_year')
TARGETS = ('distress',)
OWN_RATE = 'bank_rate'


def quarter_index(year, month):
    return (int(year) - FIRST_YEAR) * 4 + (int(month) - 1) // 3


def quarter_end(index):
    year, q = FIRST_YEAR + int(index) // 4, int(index) % 4 + 1
    return f'{year}-{q * 3:02d}-{(31 if q in (1, 4) else 30):02d}'


def day_number(day):
    return day.year * 10000 + day.month * 100 + day.day


def origin_day(index):
    """The declared public date of a quarter's call reports, as YYYYMMDD."""
    return day_number(date.fromisoformat(quarter_end(index)) + timedelta(days=PUBLIC_LAG_DAYS))


def cache_path(store, ref):
    return Path(store.root) / 'embedding_reports' / 'scratch' / 'cache' / f'fdic-calls-{ref["version"]}.npz'


def _build(store, ref, path, log):
    certs, states = {}, {}
    rows = []
    counts = {'observations': 0, 'kept': 0, 'before_first_quarter': 0, 'null_value': 0, 'banks_with_state': 0}
    with gzip.open(_records_path(store, ref), 'rt', encoding='utf-8') as stream:
        for line in stream:
            if '"entity_type":"bank"' in line:
                record = json.loads(line)
                state = (record.get('attributes') or {}).get('state')
                if state:
                    states[record['entity_id'].rsplit(':', 1)[-1]] = state
                continue
            if '"report_date"' not in line or '"kind":"observation"' not in line:
                continue
            record = json.loads(line)
            metric = record.get('metric')
            if metric not in METRIC_INDEX:
                continue
            counts['observations'] += 1
            report = record['dimensions']['report_date']
            quarter = quarter_index(report[:4], report[5:7])
            if quarter < 0:
                counts['before_first_quarter'] += 1
                continue
            if record.get('value') is None:          # a field the bank did not report that quarter
                counts['null_value'] += 1
                continue
            cert = record['subject'].rsplit(':', 1)[-1]
            rows.append((certs.setdefault(cert, len(certs)), quarter, METRIC_INDEX[metric], float(record['value'])))
            counts['kept'] += 1
            if log and counts['kept'] % 2_000_000 == 0:
                log(f'  fdic: {counts["kept"]:,} observations')
    n_banks, n_quarters = len(certs), max(r[1] for r in rows) + 1
    values = np.full((n_banks, n_quarters, len(METRICS)), np.nan, dtype=np.float32)
    for bank, quarter, metric, value in rows:
        values[bank, quarter, metric] = value
    cert_ids = sorted(certs, key=certs.get)
    state_names = sorted(set(states.values()))
    state_index = {name: i for i, name in enumerate(state_names)}
    state = np.array([state_index.get(states.get(cert, ''), -1) for cert in cert_ids], dtype=np.int16)
    counts['banks_with_state'] = int((state >= 0).sum())
    meta = {'input': dict(ref), 'counts': counts, 'banks': n_banks, 'quarters': n_quarters,
            'first_quarter_end': quarter_end(0), 'last_quarter_end': quarter_end(n_quarters - 1)}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, values=values, state=state, cert_ids=np.array(cert_ids), states=np.array(state_names),
             meta=np.array(json.dumps(meta)))
    return meta


def load(store, *, ref=None, log=print):
    """Call-report values as ``[bank, quarter, metric]``, with a deterministic npz cache."""
    ref = ref or catalog_ref(store, DATASET)
    path = cache_path(store, ref)
    if not path.exists():
        _build(store, ref, path, log)
    stored = np.load(path, allow_pickle=False)
    meta = json.loads(str(stored['meta']))
    data = {'ref': dict(ref), 'meta': meta, 'values': stored['values'], 'state': stored['state'],
            'cert_ids': [str(v) for v in stored['cert_ids']], 'states': [str(v) for v in stored['states']]}
    if log:
        log(f'  fdic: {meta["banks"]:,} charters over {meta["quarters"]} quarters '
            f'({meta["first_quarter_end"]}..{meta["last_quarter_end"]}), '
            f'{meta["counts"]["banks_with_state"]:,} with a published state')
    return data


def _ratio(numerator, denominator):
    out = np.full(numerator.shape, np.nan, dtype=np.float32)
    ok = np.isfinite(numerator) & np.isfinite(denominator) & (denominator > 0)
    out[ok] = numerator[ok] / denominator[ok]
    return out


def derive(data):
    """[bank, quarter, feature] ratios and quarter-on-quarter changes, and the distress marker.

    ``marker[b, q]`` is the marker of the transition from ``q-1`` to ``q``; the label of a sample at
    quarter ``q`` is ``marker[b, q + 1]``.
    """
    values = data['values']
    v = lambda name: values[:, :, METRIC_INDEX[name]]
    assets, deposits, loans = v('total_assets'), v('bank_deposits'), v('bank_net_loans')
    noncurrent = v('bank_noncurrent_loans')
    n_banks, n_quarters = assets.shape
    x = np.full((n_banks, n_quarters, len(FEATURES)), np.nan, dtype=np.float32)
    f = FEATURES.index
    positive_assets = np.isfinite(assets) & (assets > 0)
    x[:, :, f('log_assets')] = np.where(positive_assets, np.log1p(np.clip(assets, 0, None)), np.nan)
    x[:, :, f('deposits_to_assets')] = _ratio(deposits, assets)
    x[:, :, f('uninsured_to_deposits')] = _ratio(v('bank_uninsured_deposits'), deposits)
    x[:, :, f('brokered_to_deposits')] = _ratio(v('bank_brokered_deposits'), deposits)
    x[:, :, f('loans_to_assets')] = _ratio(loans, assets)
    noncurrent_ratio = _ratio(noncurrent, loans)
    x[:, :, f('noncurrent_ratio')] = noncurrent_ratio
    x[:, :, f('securities_to_assets')] = _ratio(v('bank_securities'), assets)
    x[:, :, f('cash_to_assets')] = _ratio(v('bank_cash_and_due'), assets)
    x[:, :, f('equity_to_assets')] = _ratio(v('bank_equity'), assets)
    x[:, :, f('tier1_leverage')] = v('bank_tier1_leverage_ratio')
    x[:, :, f('total_risk_based_capital')] = v('bank_total_risk_based_capital_ratio')
    cre = (np.nan_to_num(v('bank_loans_nonfarm_nonresidential_re')) + np.nan_to_num(v('bank_loans_multifamily_re'))
           + np.nan_to_num(v('bank_loans_construction_land_development')))
    x[:, :, f('cre_to_equity')] = _ratio(cre.astype(np.float32), v('bank_equity'))
    x[:, :, f('ci_to_loans')] = _ratio(v('bank_loans_commercial_industrial'), loans)
    x[:, :, f('consumer_to_loans')] = _ratio(v('bank_loans_consumer'), loans)
    x[:, :, f('residential_to_loans')] = _ratio(v('bank_loans_residential_1_4_family'), loans)
    x[:, :, f('ytd_net_income_to_assets')] = _ratio(v('bank_net_income'), assets)
    x[:, :, f('ytd_charge_offs_to_loans')] = _ratio(v('bank_net_charge_offs'), loans)
    employees = v('bank_employees')
    x[:, :, f('log_employees')] = np.where(np.isfinite(employees), np.log1p(np.clip(employees, 0, None)), np.nan)
    # Only for a bank-quarter that was actually reported: an absent report must leave no token behind.
    x[:, :, f('quarter_of_year')] = np.where(np.isfinite(assets),
                                             np.arange(n_quarters, dtype=np.float32)[None, :] % 4, np.nan)

    def growth(series):
        out = np.full(series.shape, np.nan, dtype=np.float32)
        previous, current = series[:, :-1], series[:, 1:]
        ok = np.isfinite(previous) & np.isfinite(current) & (previous > 0) & (current > 0)
        out[:, 1:][ok] = np.log(current[ok] / previous[ok])
        return out

    x[:, :, f('deposit_growth')] = growth(deposits)
    x[:, :, f('asset_growth')] = growth(assets)
    x[:, :, f('loan_growth')] = growth(loans)
    change = np.full(noncurrent_ratio.shape, np.nan, dtype=np.float32)
    ok = np.isfinite(noncurrent_ratio[:, 1:]) & np.isfinite(noncurrent_ratio[:, :-1])
    change[:, 1:][ok] = (noncurrent_ratio[:, 1:] - noncurrent_ratio[:, :-1])[ok]
    x[:, :, f('noncurrent_change')] = change
    # Ratios whose denominator can be near zero (equity, above all) are clipped before the
    # half-precision cast the runner makes; without it they overflow to infinity.
    np.clip(x, -CLIP, CLIP, out=x)

    # ---- the declared distress marker of the transition q-1 -> q
    usable = (np.isfinite(loans) & (loans > 0) & np.isfinite(deposits) & (deposits > 0)
              & np.isfinite(noncurrent_ratio))
    both = usable[:, 1:] & usable[:, :-1]
    onset = (noncurrent_ratio[:, 1:] > NONCURRENT_THRESHOLD) & (noncurrent_ratio[:, :-1] <= NONCURRENT_THRESHOLD)
    outflow = deposits[:, 1:] < (1 - DEPOSIT_OUTFLOW) * deposits[:, :-1]
    marker = np.full(assets.shape, np.nan, dtype=np.float32)
    marker[:, 1:][both] = (onset | outflow)[both].astype(np.float32)
    components = np.full(assets.shape + (2,), np.nan, dtype=np.float32)
    components[:, 1:, 0][both] = onset[both]
    components[:, 1:, 1][both] = outflow[both]
    return {'x': x, 'marker': marker, 'components': components, 'usable': usable, 'assets': assets}


def template():
    n = 1 + 2 * K
    rel = {name: i for i, name in enumerate(RELATIONS)}
    edges = []
    for i in range(K):
        edges += [(0, 1 + i, rel['bank_state_peer']), (1 + i, 0, rel['state_peer_bank']),
                  (0, 1 + K + i, rel['bank_size_peer']), (1 + K + i, 0, rel['size_peer_bank'])]
    node_type = np.array([0] + [1] * K + [2] * K, dtype=np.int64)
    return n, np.array(edges, dtype=np.int64), node_type


@dataclass
class BankTasks:
    x: np.ndarray
    m: np.ndarray
    node_valid: np.ndarray
    weights: np.ndarray
    y: np.ndarray
    quarter: np.ndarray
    label_filed: np.ndarray       # the declared public date of the label quarter, YYYYMMDD
    manager: np.ndarray           # bank (index into cert_ids)
    security: np.ndarray          # the quarter again: a bank-quarter has no second actor
    base_manager: np.ndarray      # [S, 1] the bank's own marker rate over the previous eight quarters
    base_security: np.ndarray
    max_feature_filed: dict
    leakage_violations: int
    rates: dict                   # per-quarter base rate and the two component rates, for the log


def _peers(derived, state, quarter, banks):
    """State peers (largest in the same state) and size peers (nearest in log assets), at ``quarter``."""
    assets = derived['assets'][:, quarter]
    live = np.isfinite(assets) & (assets > 0)
    order = np.argsort(-np.where(live, assets, -np.inf))
    ranked = order[live[order]]
    by_state = {}
    for bank in ranked:
        if int(state[bank]) < 0:          # no published state: no state-peer nodes, rather than a bucket of unknowns
            continue
        by_state.setdefault(int(state[bank]), []).append(int(bank))
    log_assets = np.log1p(np.clip(assets, 0, None))
    size_order = ranked[np.argsort(log_assets[ranked], kind='stable')]
    position = {int(b): i for i, b in enumerate(size_order)}
    state_peers = np.full((len(banks), K), -1, dtype=np.int64)
    size_peers = np.full((len(banks), K), -1, dtype=np.int64)
    for i, bank in enumerate(banks):
        others = [b for b in by_state.get(int(state[bank]), ()) if b != bank][:K]
        state_peers[i, :len(others)] = others
        centre = position.get(int(bank))
        if centre is None:
            continue
        lo = max(0, centre - K // 2)
        window = [int(b) for b in size_order[lo:lo + K + 1] if int(b) != int(bank)][:K]
        size_peers[i, :len(window)] = window
    return state_peers, size_peers


def build_tasks(data, *, first_quarter, last_quarter, per_quarter, history=4, seed=0, log=print):
    """Samples for quarters ``first_quarter..last_quarter``; the label of quarter q is the marker at q+1."""
    rng = np.random.default_rng(seed)
    derived = derive(data)
    features, marker, usable = derived['x'], derived['marker'], derived['usable']
    assets = derived['assets']
    state = data['state']
    n_nodes, edges, _ = template()
    parts, rates = [], {}
    for q in range(first_quarter, last_quarter + 1):
        if q + 1 >= marker.shape[1]:
            continue
        eligible = np.flatnonzero(usable[:, q] & np.isfinite(marker[:, q + 1]))
        if not len(eligible):
            continue
        if len(eligible) > per_quarter:
            eligible = np.sort(rng.permutation(eligible)[:per_quarter])
        S = len(eligible)
        y = marker[eligible, q + 1].astype(float)[:, None]
        state_peers, size_peers = _peers(derived, state, q, eligible)
        nodes = np.concatenate([eligible[:, None], state_peers, size_peers], axis=1)
        x = np.full((S, n_nodes, len(FEATURES), history), np.nan, dtype=np.float32)
        for h in range(history):
            if q - h < 0:
                continue
            block = features[np.where(nodes >= 0, nodes, 0), q - h]
            x[:, :, :, h] = np.where((nodes >= 0)[:, :, None], block, np.nan)
        valid = nodes >= 0
        own_assets = np.clip(assets[eligible, q], 1.0, None)
        peer_assets = np.clip(assets[np.where(nodes >= 0, nodes, 0), q], 1.0, None)
        ratio = np.minimum(peer_assets, own_assets[:, None]) / np.maximum(peer_assets, own_assets[:, None])
        ratio = np.where(valid & np.isfinite(ratio), ratio, 0.0).astype(np.float32)
        weights = np.ones((S, len(edges)), dtype=np.float32)
        for j in range(K):
            weights[:, 4 * j:4 * j + 2] = ratio[:, 1 + j][:, None]
            weights[:, 4 * j + 2:4 * j + 4] = ratio[:, 1 + K + j][:, None]
        # The bank's own marker rate over the previous eight transitions, all public at the origin.
        lo = max(0, q - OWN_RATE_QUARTERS + 1)
        window = marker[eligible, lo:q + 1]
        seen = np.isfinite(window)
        own = np.divide(np.nansum(np.where(seen, window, 0), axis=1), seen.sum(axis=1),
                        out=np.full(S, np.nan), where=seen.sum(axis=1) > 0)
        components = derived['components'][eligible, q + 1]
        rates[quarter_end(q)] = {'samples': S, 'base_rate': float(y.mean()),
                                 'noncurrent_onset': float(np.nanmean(components[:, 0])),
                                 'deposit_outflow': float(np.nanmean(components[:, 1]))}
        parts.append((x, valid, weights, y, np.full(S, q), np.full(S, origin_day(q + 1)),
                      eligible, np.full(S, q), own[:, None], np.full((S, 1), np.nan)))
        if log:
            log(f'    quarter {quarter_end(q)}: {S} samples, base rate {float(y.mean()):.4f}')
    if not parts:
        raise ValueError('No samples in the requested quarters')
    join = lambda i: np.concatenate([p[i] for p in parts])
    x = join(0)
    mask = np.isfinite(x)
    return BankTasks(np.nan_to_num(x).astype(np.float16), mask.astype(np.uint8), join(1), join(2), join(3),
                     join(4), join(5), join(6), join(7), join(8), join(9), {}, 0, rates)
