"""Actor task from campaign contributions: will a giving committee give to this recipient again?

A *sample* is one (giving committee, recipient candidate committee, election cycle) triple. The
pair is a sample at cycle ``C`` when the giver made at least one direct contribution to that
recipient committee during cycle ``C``; the label is 1 when it makes at least one again during
cycle ``C+2`` (the next two-year cycle). Read from the label's side, that is exactly "the giver
gave in the previous cycle, does it give again": the origin sits between the two cycles.

**Dating rule, declared before any result.** Every amount is dated by its published transaction
month (``dimensions.month`` of the ``committee_to_candidate_amount`` observation, which the FEC
pipeline takes from the `pas2` transaction date). A cycle's giving is treated as public on
**31 January of the year after the cycle ends** -- the due date of the year-end report that closes
the cycle, after which no further original report for it is due. That date is the sample's origin:
features come from cycle ``C`` and earlier, and the label of cycle ``C`` (giving in ``C+2``) is
dated 31 January of the year after ``C+2`` ends.

Late and amended filings are handled explicitly, and not silently:

* a transaction whose published month falls **outside the window of the cycle file that carries
  it** (cycle ``C`` covers 1 January ``C-1`` to 31 December ``C``) is a transaction reported in a
  later cycle's filings. It is dropped and counted (``counts['month_outside_cycle']``): keeping it
  would put money in a cycle whose totals were already public when the money was reported;
* a row whose transaction date the publisher could not parse (``period: cycle_date_unknown``) has
  no date at all, so it is dropped and counted;
* an **amendment filed inside the cycle's own file after the cycle's public date cannot be
  distinguished in this release**: the FEC bulk `pas2` file is a snapshot taken at acquisition and
  carries no filing or amendment date per transaction. This is declared as a revision risk in the
  attempt (``revision_leakage_possible``), not assumed away. The month/cycle rule above removes the
  largest part of it -- money reported a cycle or more late -- but not a same-cycle amendment.

What is deliberately **not** used: any cycle total from ``weball``/``webk`` (they are cycle-to-date
through a coverage end date the release does not pin to a filing), and individual contributions
(a different dataset, and out of this scope). Independent expenditures and communication costs
(`24E`, `24A`, `24F`, `24N`) are features but never the label or the eligibility condition: by law
they are not coordinated with the candidate, so they are not "giving to" the committee.

Template subgraph (19 nodes): 0 the (giver, recipient) pair; 1 the giver; 2 the recipient
committee; 3-10 up to eight other committees the giver gave most to in cycle ``C``; 11-18 up to
eight other committees that gave most to the recipient in cycle ``C``.

Requires numpy.
"""
from dataclasses import dataclass
import gzip
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from ...estimation.loaders import catalog_ref, _records_path

DATASET = 'fec'
METRIC = '"metric":"committee_to_candidate_amount"'
REGISTRATION = '"predicate":"fec_committee_registration"'
FIRST_CYCLE = 2000

NODE_TYPES = ('pair', 'giver', 'recipient')
RELATIONS = ('pair_giver', 'giver_pair', 'pair_recipient', 'recipient_pair',
             'giver_other_recipient', 'other_recipient_giver', 'recipient_other_giver', 'other_giver_recipient')
PAIR_FEATURES = ('pair_direct_log', 'pair_transactions_log', 'pair_gave', 'pair_share_of_giver',
                 'pair_share_of_recipient', 'pair_ie_support_log', 'pair_ie_oppose_log',
                 'pair_same_state', 'pair_same_party')
GIVER_FEATURES = ('giv_direct_log', 'giv_recipients_log', 'giv_transactions_log', 'giv_mean_gift_log',
                  'giv_repeat_rate', 'giv_ie_log', 'giv_party_d', 'giv_party_r',
                  'giv_is_pac', 'giv_is_party_committee', 'giv_is_candidate_committee')
RECIPIENT_FEATURES = ('rec_direct_log', 'rec_givers_log', 'rec_transactions_log', 'rec_mean_gift_log',
                      'rec_retention_rate', 'rec_ie_support_log', 'rec_ie_oppose_log',
                      'rec_party_d', 'rec_party_r', 'rec_office_house', 'rec_office_senate', 'rec_office_president')
FEATURES = PAIR_FEATURES + GIVER_FEATURES + RECIPIENT_FEATURES
TARGETS = ('gives_again',)
OWN_RATE = 'giver_repeat_rate'
K = 8

# Transaction classes. ``direct`` is money to the campaign: a contribution (24K), an in-kind
# contribution (24Z) and a coordinated party expenditure (24C). Only ``direct`` defines giving.
DIRECT, SUPPORT_IE, OPPOSE_IE, OTHER = 0, 1, 2, 3
TRANSACTION_CLASS = {'24K': DIRECT, '24Z': DIRECT, '24C': DIRECT,
                     '24E': SUPPORT_IE, '24F': SUPPORT_IE, '24A': OPPOSE_IE, '24N': OPPOSE_IE}
PARTY_CODES = {'DEM': 1, 'REP': 2}
CANDIDATE_TYPES, PARTY_TYPES, PAC_TYPES = set('HSP'), set('XYZ'), set('QNOVWU')
OFFICES = ('H', 'S', 'P')


def cycle_index(cycle):
    return (int(cycle) - FIRST_CYCLE) // 2


def cycle_end(index):
    """The period-end string that names cycle ``index`` in a plan block."""
    return f'{FIRST_CYCLE + 2 * int(index)}-12-31'


def cycle_public_day(index):
    """The declared public date of a cycle: 31 January of the year after it ends, as YYYYMMDD."""
    return (FIRST_CYCLE + 2 * int(index) + 1) * 10000 + 131


def cycle_of_month(year, month):
    """The FEC two-year cycle that a calendar month belongs to (an odd year opens the cycle)."""
    del month
    return year + (year % 2)


def cache_path(store, ref):
    return Path(store.root) / 'embedding_reports' / 'scratch' / 'cache' / f'fec-pas2-{ref["version"]}.npz'


def _build(store, ref, path, log):
    committees, candidates = {}, {}
    giver, recipient, cycle, klass, amount, count, office = [], [], [], [], [], [], []
    registrations = {}
    counts = {'rows': 0, 'kept': 0, 'no_month': 0, 'month_outside_cycle': 0, 'missing_identifier': 0,
              'before_first_cycle': 0, 'registrations': 0}
    with gzip.open(_records_path(store, ref), 'rt', encoding='utf-8') as stream:
        for line in stream:
            if REGISTRATION in line:
                record = json.loads(line)
                value = record['value']
                registrations[(cycle_index(value['cycle']), record['subject'].rsplit(':', 1)[-1])] = (
                    PARTY_CODES.get(value.get('party') or '', 0), (value.get('committee_type') or '')[:1],
                    value.get('mailing_state') or '')
                counts['registrations'] += 1
                continue
            if METRIC not in line:
                continue
            record = json.loads(line)
            counts['rows'] += 1
            dimensions = record['dimensions']
            month = dimensions.get('month')
            if not month:
                counts['no_month'] += 1
                continue
            year, month_number = int(month[:4]), int(month[5:7])
            if cycle_of_month(year, month_number) != int(dimensions['cycle']):
                counts['month_outside_cycle'] += 1
                continue
            index = cycle_index(dimensions['cycle'])
            if index < 0:
                counts['before_first_cycle'] += 1
                continue
            # Nothing is joined except on published identifiers: a row without both committee ids
            # names no pair and is dropped.
            if not dimensions.get('recipient_committee') or not record.get('subject'):
                counts['missing_identifier'] += 1
                continue
            source = record['subject'].rsplit(':', 1)[-1]
            target = dimensions['recipient_committee'].rsplit(':', 1)[-1]
            candidate = (dimensions.get('candidate') or '').rsplit(':', 1)[-1]
            giver.append(committees.setdefault(source, len(committees)))
            recipient.append(committees.setdefault(target, len(committees)))
            candidates.setdefault(candidate, len(candidates))
            cycle.append(index)
            klass.append(TRANSACTION_CLASS.get(dimensions['transaction_type'], OTHER))
            amount.append(float(record['value']))
            count.append(int(record['attributes'].get('transaction_count', 1)))
            office.append(OFFICES.index(candidate[0]) if candidate[:1] in OFFICES else -1)
            counts['kept'] += 1
            if log and counts['kept'] % 1_000_000 == 0:
                log(f'  fec: {counts["kept"]:,} transaction groups')
    n_committees = len(committees)
    states = sorted({state for _, _, state in registrations.values()})
    state_index = {name: i for i, name in enumerate(states)}
    keys, party, ctype, state = [], [], [], []
    for (index, identifier), (party_code, type_code, state_name) in registrations.items():
        if identifier not in committees or index < 0:
            continue
        keys.append(index * n_committees + committees[identifier])
        party.append(party_code)
        ctype.append((1 if type_code in CANDIDATE_TYPES else 2 if type_code in PARTY_TYPES
                      else 3 if type_code in PAC_TYPES else 0))
        state.append(state_index.get(state_name, -1))
    order = np.argsort(np.array(keys, dtype=np.int64)) if keys else np.zeros(0, dtype=np.int64)
    arrays = {
        'giver': np.array(giver, dtype=np.int32), 'recipient': np.array(recipient, dtype=np.int32),
        'cycle': np.array(cycle, dtype=np.int16), 'klass': np.array(klass, dtype=np.int8),
        'amount': np.array(amount, dtype=np.float64), 'count': np.array(count, dtype=np.int32),
        'office': np.array(office, dtype=np.int8),
        'reg_key': np.array(keys, dtype=np.int64)[order] if keys else np.zeros(0, dtype=np.int64),
        'reg_party': np.array(party, dtype=np.int8)[order] if keys else np.zeros(0, dtype=np.int8),
        'reg_type': np.array(ctype, dtype=np.int8)[order] if keys else np.zeros(0, dtype=np.int8),
        'reg_state': np.array(state, dtype=np.int16)[order] if keys else np.zeros(0, dtype=np.int16),
    }
    meta = {'input': dict(ref), 'counts': counts, 'committees': n_committees, 'candidates': len(candidates),
            'cycles': int(arrays['cycle'].max()) + 1 if len(arrays['cycle']) else 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    np.savez(path, **arrays, committee_ids=np.array(sorted(committees, key=committees.get)),
             meta=np.array(json.dumps(meta)))
    return meta


def load(store, *, ref=None, log=print):
    """Committee-to-candidate transaction groups as dated arrays, with a deterministic npz cache.

    The cache is keyed by the pinned ``fec`` version the attempt records, so it carries no
    provenance of its own and can always be rebuilt.
    """
    ref = ref or catalog_ref(store, DATASET)
    path = cache_path(store, ref)
    if not path.exists():
        _build(store, ref, path, log)
    stored = np.load(path, allow_pickle=False)
    data = {name: stored[name] for name in stored.files if name not in ('meta', 'committee_ids')}
    meta = json.loads(str(stored['meta']))
    data['ref'] = dict(ref)
    data['meta'] = meta
    data['n_committees'] = meta['committees']
    data['committee_ids'] = [str(v) for v in stored['committee_ids']]
    if log:
        counts = meta['counts']
        log(f'  fec: {counts["kept"]:,} transaction groups over {meta["cycles"]} cycles, '
            f'{meta["committees"]:,} committees; dropped {counts["no_month"]:,} undated and '
            f'{counts["month_outside_cycle"]:,} reported outside their cycle window')
    return data


def _runs(keys):
    """Start offsets of each run of equal values in a non-decreasing array, and the run values."""
    if not len(keys):
        return np.zeros(0, dtype=np.int64), np.zeros(0, dtype=keys.dtype)
    starts = np.flatnonzero(np.r_[True, keys[1:] != keys[:-1]])
    return starts, keys[starts]


def _lookup(sorted_keys, values, keys, missing):
    """``values`` at ``keys``, or ``missing`` where the key is absent."""
    out = np.full(len(keys), missing, dtype=np.float64)
    if not len(sorted_keys):
        return out, np.zeros(len(keys), dtype=bool)
    index = np.searchsorted(sorted_keys, keys)
    safe = np.clip(index, 0, len(sorted_keys) - 1)
    hit = (index < len(sorted_keys)) & (sorted_keys[safe] == keys)
    out[hit] = values[safe[hit]]
    return out, hit


def aggregate(data):
    """Pair-, giver- and recipient-level cycle aggregates, and the repeat rates built from them."""
    nc = int(data['n_committees'])
    giver = data['giver'].astype(np.int64)
    recipient = data['recipient'].astype(np.int64)
    cycle = data['cycle'].astype(np.int64)
    klass, amount, count = data['klass'], data['amount'], data['count'].astype(np.int64)
    pair_key = (cycle * nc + giver) * nc + recipient
    order = np.argsort(pair_key, kind='stable')
    sorted_key = pair_key[order]
    starts, uniq = _runs(sorted_key)

    def segment(values):
        return np.add.reduceat(values[order], starts) if len(starts) else values[:0].astype(np.float64)

    direct = segment(np.where(klass == DIRECT, amount, 0.0))
    transactions = segment(np.where(klass == DIRECT, count, 0).astype(np.float64))
    ie_support = segment(np.where(klass == SUPPORT_IE, amount, 0.0))
    ie_oppose = segment(np.where(klass == OPPOSE_IE, amount, 0.0))
    office = data['office'][order][starts] if len(starts) else data['office'][:0]
    p_cycle, rest = uniq // (nc * nc), uniq % (nc * nc)
    p_giver, p_recipient = rest // nc, rest % nc
    agg = {'nc': nc, 'pair_key': uniq, 'cycle': p_cycle, 'giver': p_giver, 'recipient': p_recipient,
           'direct': direct, 'transactions': transactions, 'ie_support': ie_support, 'ie_oppose': ie_oppose,
           'office': office}
    positive = direct > 0
    agg['positive_pair_key'] = uniq[positive]

    # Giver level: the pair rows are already sorted by (cycle, giver, recipient).
    g_key = p_cycle * nc + p_giver
    g_start, g_uniq = _runs(g_key)
    agg['giver_key'] = g_uniq
    agg['giver_start'] = g_start
    agg['giver_direct'] = np.add.reduceat(direct, g_start) if len(g_start) else direct[:0]
    agg['giver_recipients'] = np.add.reduceat(positive.astype(np.float64), g_start) if len(g_start) else direct[:0]
    agg['giver_transactions'] = np.add.reduceat(transactions, g_start) if len(g_start) else direct[:0]
    agg['giver_ie'] = np.add.reduceat(ie_support + ie_oppose, g_start) if len(g_start) else direct[:0]

    # Recipient level.
    r_key = p_cycle * nc + p_recipient
    r_order = np.argsort(r_key, kind='stable')
    r_sorted = r_key[r_order]
    r_start, r_uniq = _runs(r_sorted)
    agg['recipient_order'] = r_order
    agg['recipient_key'] = r_uniq
    agg['recipient_start'] = r_start
    take = lambda v: np.add.reduceat(v[r_order], r_start) if len(r_start) else v[:0]
    agg['recipient_direct'] = take(direct)
    agg['recipient_givers'] = take(positive.astype(np.float64))
    agg['recipient_transactions'] = take(transactions)
    agg['recipient_ie_support'] = take(ie_support)
    agg['recipient_ie_oppose'] = take(ie_oppose)

    # Repeat and retention rates, keyed by the *later* cycle: of the pairs a giver gave to in cycle
    # c-1, the share it gave to again in cycle c (and the mirror image for a recipient's givers).
    pos_cycle, pos_giver, pos_recipient = p_cycle[positive], p_giver[positive], p_recipient[positive]
    next_key = ((pos_cycle + 1) * nc + pos_giver) * nc + pos_recipient
    again = np.isin(next_key, agg['positive_pair_key'])
    for name, entity in (('giver_repeat', pos_giver), ('recipient_retention', pos_recipient)):
        key = (pos_cycle + 1) * nc + entity
        rank = np.argsort(key, kind='stable')
        start, uniq_key = _runs(key[rank])
        if len(start):
            numerator = np.add.reduceat(again[rank].astype(np.float64), start)
            denominator = np.add.reduceat(np.ones(len(rank)), start)
        else:
            numerator = denominator = np.zeros(0)
        agg[f'{name}_key'] = uniq_key
        agg[f'{name}_rate'] = np.divide(numerator, denominator, out=np.full(len(uniq_key), np.nan),
                                        where=denominator > 0)
    return agg


def template():
    n = 3 + 2 * K
    rel = {name: i for i, name in enumerate(RELATIONS)}
    edges = [(0, 1, rel['pair_giver']), (1, 0, rel['giver_pair']),
             (0, 2, rel['pair_recipient']), (2, 0, rel['recipient_pair'])]
    for i in range(K):
        edges += [(1, 3 + i, rel['giver_other_recipient']), (3 + i, 1, rel['other_recipient_giver']),
                  (2, 3 + K + i, rel['recipient_other_giver']), (3 + K + i, 2, rel['other_giver_recipient'])]
    node_type = np.array([0, 1, 2] + [2] * K + [1] * K, dtype=np.int64)
    return n, np.array(edges, dtype=np.int64), node_type


@dataclass
class FecTasks:
    x: np.ndarray
    m: np.ndarray
    node_valid: np.ndarray
    weights: np.ndarray
    y: np.ndarray
    quarter: np.ndarray           # cycle index
    label_filed: np.ndarray       # the declared public date of the label cycle, YYYYMMDD
    manager: np.ndarray           # giving committee index
    security: np.ndarray          # recipient committee index
    base_manager: np.ndarray      # [S, 1] the giver's own repeat rate into cycle C
    base_security: np.ndarray
    max_feature_filed: dict
    leakage_violations: int


def _registration(data, entities, cycle):
    """Party, committee type and mailing state of ``entities`` as registered for ``cycle``."""
    nc = int(data['n_committees'])
    key = cycle * nc + np.where(entities >= 0, entities, 0)
    party, hit = _lookup(data['reg_key'], data['reg_party'].astype(np.float64), key, np.nan)
    ctype, _ = _lookup(data['reg_key'], data['reg_type'].astype(np.float64), key, np.nan)
    state, _ = _lookup(data['reg_key'], data['reg_state'].astype(np.float64), key, np.nan)
    ok = hit & (entities >= 0)
    return party, ctype, state, ok


def _entity_block(agg, data, side, entities, cycle, history):
    """[n, F, H] features for ``entities`` of ``side`` ('giver' or 'recipient'), over H cycles back.

    A cycle in which a committee appears in no transaction is a zero, not a missing value: the
    source itemizes every committee-to-candidate transaction, so absence is absence of giving.
    Cycles before the first published one stay missing.
    """
    nc = agg['nc']
    n = len(entities)
    x = np.full((n, len(FEATURES), history), np.nan, dtype=np.float32)
    ok = entities >= 0
    safe = np.where(ok, entities, 0)
    f = FEATURES.index
    for h in range(history):
        c = cycle - h
        inside = ok & (c >= 0)
        key = c * nc + safe
        if side == 'giver':
            direct, _ = _lookup(agg['giver_key'], agg['giver_direct'], key, 0.0)
            others, _ = _lookup(agg['giver_key'], agg['giver_recipients'], key, 0.0)
            transactions, _ = _lookup(agg['giver_key'], agg['giver_transactions'], key, 0.0)
            ie, _ = _lookup(agg['giver_key'], agg['giver_ie'], key, 0.0)
            rate, _ = _lookup(agg['giver_repeat_key'], agg['giver_repeat_rate'], key, np.nan)
            x[inside, f('giv_direct_log'), h] = np.log1p(np.clip(direct, 0, None))[inside]
            x[inside, f('giv_recipients_log'), h] = np.log1p(others)[inside]
            x[inside, f('giv_transactions_log'), h] = np.log1p(transactions)[inside]
            mean = np.divide(direct, others, out=np.full(n, np.nan), where=others > 0)
            good = inside & np.isfinite(mean)
            x[good, f('giv_mean_gift_log'), h] = np.log1p(np.clip(mean, 0, None))[good]
            good = inside & np.isfinite(rate)
            x[good, f('giv_repeat_rate'), h] = rate[good]
            x[inside, f('giv_ie_log'), h] = np.log1p(np.clip(ie, 0, None))[inside]
        else:
            direct, _ = _lookup(agg['recipient_key'], agg['recipient_direct'], key, 0.0)
            others, _ = _lookup(agg['recipient_key'], agg['recipient_givers'], key, 0.0)
            transactions, _ = _lookup(agg['recipient_key'], agg['recipient_transactions'], key, 0.0)
            support, _ = _lookup(agg['recipient_key'], agg['recipient_ie_support'], key, 0.0)
            oppose, _ = _lookup(agg['recipient_key'], agg['recipient_ie_oppose'], key, 0.0)
            rate, _ = _lookup(agg['recipient_retention_key'], agg['recipient_retention_rate'], key, np.nan)
            x[inside, f('rec_direct_log'), h] = np.log1p(np.clip(direct, 0, None))[inside]
            x[inside, f('rec_givers_log'), h] = np.log1p(others)[inside]
            x[inside, f('rec_transactions_log'), h] = np.log1p(transactions)[inside]
            mean = np.divide(direct, others, out=np.full(n, np.nan), where=others > 0)
            good = inside & np.isfinite(mean)
            x[good, f('rec_mean_gift_log'), h] = np.log1p(np.clip(mean, 0, None))[good]
            good = inside & np.isfinite(rate)
            x[good, f('rec_retention_rate'), h] = rate[good]
            x[inside, f('rec_ie_support_log'), h] = np.log1p(np.clip(support, 0, None))[inside]
            x[inside, f('rec_ie_oppose_log'), h] = np.log1p(np.clip(oppose, 0, None))[inside]
    party, ctype, _, known = _registration(data, entities, cycle)
    if side == 'giver':
        x[known, f('giv_party_d'), 0] = (party == PARTY_CODES['DEM'])[known]
        x[known, f('giv_party_r'), 0] = (party == PARTY_CODES['REP'])[known]
        x[known, f('giv_is_candidate_committee'), 0] = (ctype == 1)[known]
        x[known, f('giv_is_party_committee'), 0] = (ctype == 2)[known]
        x[known, f('giv_is_pac'), 0] = (ctype == 3)[known]
    else:
        x[known, f('rec_party_d'), 0] = (party == PARTY_CODES['DEM'])[known]
        x[known, f('rec_party_r'), 0] = (party == PARTY_CODES['REP'])[known]
    return x


def build_tasks(data, *, first_cycle, last_cycle, per_cycle, per_giver, history=4, seed=0, log=print):
    """Samples for cycles ``first_cycle..last_cycle``; the label of cycle C is giving in C+2."""
    rng = np.random.default_rng(seed)
    agg = aggregate(data)
    nc = agg['nc']
    n_nodes, edges, _ = template()
    f = FEATURES.index
    parts, leakage = [], 0
    for c in range(first_cycle, last_cycle + 1):
        rows = np.flatnonzero((agg['cycle'] == c) & (agg['direct'] > 0))
        if not len(rows):
            continue
        rows = rng.permutation(rows)
        chosen, per = [], defaultdict(int)
        for i in rows:
            g = int(agg['giver'][i])
            if per[g] < per_giver:
                per[g] += 1
                chosen.append(i)
                if len(chosen) >= per_cycle:
                    break
        chosen = np.array(sorted(chosen))
        S = len(chosen)
        givers, recipients = agg['giver'][chosen], agg['recipient'][chosen]
        cycles = np.full(S, c, dtype=np.int64)
        # Label: does the same pair receive a direct contribution in the next cycle?
        next_key = ((cycles + 1) * nc + givers) * nc + recipients
        y = np.isin(next_key, agg['positive_pair_key']).astype(float)[:, None]
        x = np.full((S, n_nodes, len(FEATURES), history), np.nan, dtype=np.float32)
        x[:, 1] = _entity_block(agg, data, 'giver', givers, cycles, history)
        x[:, 2] = _entity_block(agg, data, 'recipient', recipients, cycles, history)
        # ---- pair features over the same H cycles
        for h in range(history):
            cyc = c - h
            if cyc < 0:
                continue
            key = ((cyc * nc) + givers) * nc + recipients
            direct, hit = _lookup(agg['pair_key'], agg['direct'], key, 0.0)
            transactions, _ = _lookup(agg['pair_key'], agg['transactions'], key, 0.0)
            support, _ = _lookup(agg['pair_key'], agg['ie_support'], key, 0.0)
            oppose, _ = _lookup(agg['pair_key'], agg['ie_oppose'], key, 0.0)
            g_total, _ = _lookup(agg['giver_key'], agg['giver_direct'], cyc * nc + givers, 0.0)
            r_total, _ = _lookup(agg['recipient_key'], agg['recipient_direct'], cyc * nc + recipients, 0.0)
            x[:, 0, f('pair_direct_log'), h] = np.log1p(np.clip(direct, 0, None))
            x[:, 0, f('pair_transactions_log'), h] = np.log1p(transactions)
            x[:, 0, f('pair_gave'), h] = (direct > 0).astype(float)
            x[:, 0, f('pair_ie_support_log'), h] = np.log1p(np.clip(support, 0, None))
            x[:, 0, f('pair_ie_oppose_log'), h] = np.log1p(np.clip(oppose, 0, None))
            share_g = np.divide(direct, g_total, out=np.full(S, np.nan), where=g_total > 0)
            share_r = np.divide(direct, r_total, out=np.full(S, np.nan), where=r_total > 0)
            x[np.isfinite(share_g), 0, f('pair_share_of_giver'), h] = share_g[np.isfinite(share_g)]
            x[np.isfinite(share_r), 0, f('pair_share_of_recipient'), h] = share_r[np.isfinite(share_r)]
            del hit
        g_party, _, g_state, g_known = _registration(data, givers, cycles)
        r_party, _, r_state, r_known = _registration(data, recipients, cycles)
        both = g_known & r_known
        states = both & (g_state >= 0) & (r_state >= 0)
        x[states, 0, f('pair_same_state'), 0] = (g_state == r_state)[states]
        parties = both & (g_party > 0) & (r_party > 0)
        x[parties, 0, f('pair_same_party'), 0] = (g_party == r_party)[parties]
        office, office_hit = _lookup(agg['pair_key'], agg['office'].astype(np.float64),
                                     ((cycles * nc) + givers) * nc + recipients, np.nan)
        office_hit = office_hit & (office >= 0)
        for j, name in enumerate(('rec_office_house', 'rec_office_senate', 'rec_office_president')):
            x[office_hit, 2, f(name), 0] = (office == j)[office_hit]
        # ---- neighbours: the giver's other recipients and the recipient's other givers in cycle C
        other_recipients = np.full((S, K), -1, dtype=np.int64)
        other_recipient_w = np.zeros((S, K), dtype=np.float32)
        other_givers = np.full((S, K), -1, dtype=np.int64)
        other_giver_w = np.zeros((S, K), dtype=np.float32)
        g_index = np.searchsorted(agg['giver_key'], c * nc + givers)
        r_index = np.searchsorted(agg['recipient_key'], c * nc + recipients)
        g_bounds = np.r_[agg['giver_start'], len(agg['pair_key'])]
        r_bounds = np.r_[agg['recipient_start'], len(agg['pair_key'])]
        for i in range(S):
            lo, hi = g_bounds[g_index[i]], g_bounds[g_index[i] + 1]
            block = np.arange(lo, hi)
            block = block[(agg['recipient'][block] != recipients[i]) & (agg['direct'][block] > 0)]
            block = block[np.argsort(-agg['direct'][block])][:K]
            total = agg['giver_direct'][g_index[i]]
            other_recipients[i, :len(block)] = agg['recipient'][block]
            if total > 0:
                other_recipient_w[i, :len(block)] = agg['direct'][block] / total
            lo, hi = r_bounds[r_index[i]], r_bounds[r_index[i] + 1]
            block = agg['recipient_order'][lo:hi]
            block = block[(agg['giver'][block] != givers[i]) & (agg['direct'][block] > 0)]
            block = block[np.argsort(-agg['direct'][block])][:K]
            total = agg['recipient_direct'][r_index[i]]
            other_givers[i, :len(block)] = agg['giver'][block]
            if total > 0:
                other_giver_w[i, :len(block)] = agg['direct'][block] / total
        repeat = lambda a: np.repeat(a, K)
        x[:, 3:3 + K] = _entity_block(agg, data, 'recipient', other_recipients.ravel(), repeat(cycles),
                                      history).reshape(S, K, len(FEATURES), history)
        x[:, 3 + K:] = _entity_block(agg, data, 'giver', other_givers.ravel(), repeat(cycles),
                                     history).reshape(S, K, len(FEATURES), history)
        valid = np.ones((S, n_nodes), dtype=bool)
        valid[:, 3:3 + K] = other_recipients >= 0
        valid[:, 3 + K:] = other_givers >= 0
        weights = np.ones((S, len(edges)), dtype=np.float32)
        for j in range(K):
            base = 4 + 4 * j
            weights[:, base:base + 2] = other_recipient_w[:, j][:, None]
            weights[:, base + 2:base + 4] = other_giver_w[:, j][:, None]
        own, _ = _lookup(agg['giver_repeat_key'], agg['giver_repeat_rate'], c * nc + givers, np.nan)
        # Leakage: every feature window is cycle C or earlier, and cycle C is public on the origin
        # date (31 January after C); the label is cycle C+2, public two years later. Structural.
        parts.append((x, valid, weights, y, np.full(S, c), np.full(S, cycle_public_day(c + 1)),
                      givers, recipients, own[:, None], np.full((S, 1), np.nan)))
        if log:
            log(f'    cycle {cycle_end(c)}: {S} samples, base rate {float(y.mean()):.3f}')
    if not parts:
        raise ValueError('No samples in the requested cycles')
    join = lambda i: np.concatenate([p[i] for p in parts])
    x = join(0)
    mask = np.isfinite(x)
    return FecTasks(np.nan_to_num(x).astype(np.float16), mask.astype(np.uint8), join(1), join(2), join(3),
                    join(4), join(5), join(6), join(7), join(8), join(9), {}, leakage)
