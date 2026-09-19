"""Actor task from roll-call votes: will a legislator break with their party on a party-unity vote?

A *party-unity vote* is a roll call on which a majority of Democrats and a majority of Republicans
voting yea or nay took opposite sides. A *sample* is one Democratic or Republican member's yea or
nay on such a vote; the label is 1 when the member voted against their own party's majority. The
origin is the roll call's date: every feature is built from roll calls held strictly before that
day, so nothing on the day of the vote (including the vote itself) enters.

What is deliberately **not** used, because it would leak:

* DW-NOMINATE and Nokken-Poole scores and roll-call NOMINATE midpoints: Voteview estimates them
  from every vote, including later ones;
* cosponsor lists: the influence panel publishes cosponsors without dates, so which of them signed
  before a given vote is unknown. The bill's sponsor (fixed at introduction) is used.

Template subgraph (20 nodes): 0 the (member, roll call) pair; 1 the member; 2 the roll call;
3 the bill's sponsor, when the roll call is on a bill with a published sponsor; 4-11 the eight
members of the same chamber who agreed most often with the member on unity votes in the two years
before the quarter began; 12-19 up to eight other members of the member's state delegation in the
same chamber and Congress.

Requires numpy.
"""
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
import gzip
import json

import numpy as np

from ..estimation.loaders import catalog_ref, _records_path

NODE_TYPES = ('pair', 'member', 'rollcall')
RELATIONS = ('pair_member', 'member_pair', 'pair_rollcall', 'rollcall_pair', 'rollcall_sponsor', 'sponsor_rollcall',
             'member_similar', 'similar_member', 'member_delegation', 'delegation_member')
CATEGORIES = ('passage', 'suspension', 'amendment', 'recommit', 'previous_question', 'resolution', 'cloture',
              'nomination', 'conference', 'table', 'other')
FEATURES = (('pair_is_sponsor', 'pair_same_party_as_sponsor', 'pair_category_defect_rate', 'pair_category_votes_log')
            + ('mem_defect_rate', 'mem_unity_votes_log', 'mem_participation', 'mem_party_d', 'mem_party_r', 'mem_senate',
               'mem_seniority')
            + ('rc_senate', 'rc_two_thirds', 'rc_nomination', 'rc_has_bill', 'rc_days_since_introduced_log', 'rc_sponsor_d',
               'rc_sponsor_r')
            + tuple(f'rc_q_{c}' for c in CATEGORIES))
TARGETS = ('defect',)
WINDOW_DAYS = 90
SIMILAR_WINDOW_DAYS = 730
K = 8
DEMOCRAT, REPUBLICAN = 'voteview:party:100', 'voteview:party:200'
OWN_RATE = 'member_rate'


def category(question, bill_number):
    q = (question or '').lower()
    if (bill_number or '').upper().startswith('PN') or 'nomination' in q:
        return 'nomination'
    for needle, name in (('suspend', 'suspension'), ('cloture', 'cloture'), ('recommit', 'recommit'),
                         ('previous question', 'previous_question'), ('conference', 'conference'), ('table', 'table'),
                         ('amendment', 'amendment'), ('passage', 'passage'), ('resolution', 'resolution')):
        if needle in q:
            return name
    return 'other'


def quarter_of(day):
    return (day.year - 2007) * 4 + (day.month - 1) // 3


def quarter_end(index):
    year, q = 2007 + index // 4, index % 4 + 1
    return f'{year}-{q * 3:02d}-{(31 if q in (1, 4) else 30):02d}'


def quarter_start_ordinal(index):
    return date(2007 + index // 4, (index % 4) * 3 + 1, 1).toordinal()


def day_number(day):
    return day.year * 10000 + day.month * 100 + day.day


def load(store, *, refs=None, log=print):
    """Roll calls with member positions, parties, states, and the bills they are on.

    ``refs`` pins the two inputs (``{'voteview_rollcalls': ref, 'influence_bills': ref}``); pinned inputs are read
    without re-verifying their whole lineage, which publication does where the lineage lives.
    """
    refs = refs or {}
    ref = refs.get('voteview_rollcalls') or catalog_ref(store, 'voteview_rollcalls')
    rollcalls, positions, party, state, bioguide_to_icpsr = {}, {}, {}, {}, {}
    with gzip.open(_records_path(store, ref), 'rt', encoding='utf-8') as stream:
        for line in stream:
            if '"roll_call_member_positions"' in line:
                r = json.loads(line)
                key = r['id'].rsplit(':', 2)[-2] + ':' + r['id'].rsplit(':', 1)[-1]
                a = r['attributes']
                positions[key] = (date.fromisoformat(r['occurred_at'][:10]), a['chamber'], a['congress'],
                                  a['positions'].get('yea', []), a['positions'].get('nay', []))
            elif '"event_type":"roll_call_vote"' in line:
                r = json.loads(line)
                key = r['id'].rsplit(':', 2)[-2] + ':' + r['id'].rsplit(':', 1)[-1]
                a = r['attributes']
                rollcalls[key] = (a.get('vote_question'), a.get('majority_requirement'), a.get('bill_number'))
            elif '"predicate":"party_affiliation"' in line:
                r = json.loads(line)
                _, _, congress, chamber, icpsr, st, _ = r['id'].split(':')
                party[(int(congress), chamber, int(icpsr))] = r['object']
                state[(int(congress), chamber, int(icpsr))] = st
            elif '"predicate":"same_as"' in line and 'bioguide:' in line:
                r = json.loads(line)
                if str(r.get('subject', '')).startswith('icpsr:'):
                    bioguide_to_icpsr[r['object']] = int(r['subject'].split(':')[1])
    bills_ref = refs.get('influence_bills') or store.latest('influence_panel', 'bills')
    bill_of = {}
    path = store.version_dir(bills_ref)
    path = next(p for p in (path / 'records.jsonl.gz', path / 'records.jsonl') if p.exists())
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        for line in stream:
            if '"roll_calls":[{' not in line:
                continue
            r = json.loads(line)
            sponsor = bioguide_to_icpsr.get(r.get('sponsor'))
            introduced = date.fromisoformat(r['introduced_date']) if r.get('introduced_date') else None
            for rc in r['roll_calls']:
                key = rc['rollcall'].rsplit(':', 2)[-2] + ':' + rc['rollcall'].rsplit(':', 1)[-1]
                bill_of[key] = (sponsor, introduced)
    if log:
        log(f'  votes: {len(positions):,} roll calls with positions, {len(party):,} member-congress parties, '
            f'{len(bill_of):,} roll calls linked to bills')
    return {'ref': ref, 'bills_ref': bills_ref, 'rollcalls': rollcalls, 'positions': positions, 'party': party,
            'state': state, 'bill_of': bill_of}


@dataclass
class VoteTasks:
    x: np.ndarray
    m: np.ndarray
    node_valid: np.ndarray
    weights: np.ndarray
    y: np.ndarray
    quarter: np.ndarray
    label_filed: np.ndarray       # the vote date: the label is public on it
    manager: np.ndarray           # member (icpsr)
    security: np.ndarray          # roll call index
    base_manager: np.ndarray      # [S, 1] the member's defection rate on unity votes in the 365 days before
    base_security: np.ndarray
    max_feature_filed: dict
    leakage_violations: int


def template():
    n = 4 + 2 * K
    rel = {name: i for i, name in enumerate(RELATIONS)}
    edges = [(0, 1, rel['pair_member']), (1, 0, rel['member_pair']), (0, 2, rel['pair_rollcall']),
             (2, 0, rel['rollcall_pair']), (2, 3, rel['rollcall_sponsor']), (3, 2, rel['sponsor_rollcall'])]
    for i in range(K):
        edges += [(1, 4 + i, rel['member_similar']), (4 + i, 1, rel['similar_member']),
                  (1, 4 + K + i, rel['member_delegation']), (4 + K + i, 1, rel['delegation_member'])]
    node_type = np.array([0, 1, 2] + [1] * (1 + 2 * K), dtype=np.int64)
    return n, np.array(edges, dtype=np.int64), node_type


def build_tasks(data, *, first_quarter, last_quarter, per_quarter, per_member, history=4, seed=0, log=print):
    rng = np.random.default_rng(seed)
    party, state = data['party'], data['state']
    # ---- every roll call, in date order, with its unity status and the parties' majority sides
    keys = sorted(data['positions'], key=lambda k: (data['positions'][k][0], k))
    rc_date = np.array([data['positions'][k][0].toordinal() for k in keys])
    rc_chamber = np.array([1 if data['positions'][k][1] == 'Senate' else 0 for k in keys])
    rc_congress = np.array([data['positions'][k][2] for k in keys])
    vm, vd, vr, vdefect, vunity, vcat, vsign = [], [], [], [], [], [], []
    rc_unity = np.zeros(len(keys), dtype=bool)
    rc_cat = np.zeros(len(keys), dtype=np.int64)
    for i, k in enumerate(keys):
        day, chamber, congress, yea, nay = data['positions'][k]
        question, requirement, bill_number = data['rollcalls'].get(k, (None, None, None))
        rc_cat[i] = CATEGORIES.index(category(question, bill_number))
        sides = {}
        for p_name in (DEMOCRAT, REPUBLICAN):
            y_ = sum(1 for m in yea if party.get((congress, chamber, m)) == p_name)
            n_ = sum(1 for m in nay if party.get((congress, chamber, m)) == p_name)
            sides[p_name] = 1 if y_ > n_ else (0 if n_ > y_ else None)
        unity = sides[DEMOCRAT] is not None and sides[REPUBLICAN] is not None and sides[DEMOCRAT] != sides[REPUBLICAN]
        rc_unity[i] = unity
        for vote, members in ((1, yea), (0, nay)):
            for m in members:
                p_name = party.get((congress, chamber, m))
                vm.append(m); vd.append(day.toordinal()); vr.append(i); vcat.append(rc_cat[i]); vsign.append(1.0 if vote else -1.0)
                vunity.append(unity and p_name in sides)
                vdefect.append(bool(unity and p_name in sides and vote != sides[p_name]))
    vm, vd, vr = np.array(vm, dtype=np.int64), np.array(vd, dtype=np.int64), np.array(vr, dtype=np.int64)
    vdefect, vunity, vcat = np.array(vdefect), np.array(vunity), np.array(vcat, dtype=np.int64)
    vsign = np.array(vsign, dtype=np.float32)
    BIG = 10 ** 7
    # Prefix sums over (member, date) and (member, category, date) orders for window counts.
    order = np.lexsort((vd, vm))
    key_md = vm[order] * BIG + vd[order]
    cum_u = np.r_[0, np.cumsum(vunity[order])]
    cum_d = np.r_[0, np.cumsum(vdefect[order])]
    cum_v = np.r_[0, np.cumsum(np.ones(len(order)))]
    order_c = np.lexsort((vd, vcat, vm))
    key_mcd = (vm[order_c] * len(CATEGORIES) + vcat[order_c]) * BIG + vd[order_c]
    cum_cu = np.r_[0, np.cumsum(vunity[order_c])]
    cum_cd = np.r_[0, np.cumsum(vdefect[order_c])]
    chamber_dates = [np.sort(rc_date[rc_chamber == c]) for c in (0, 1)]

    def window(members, start, end, cum_key, *cums, cat=None):
        if cat is None:
            lo = np.searchsorted(cum_key, members * BIG + start)
            hi = np.searchsorted(cum_key, members * BIG + end)
        else:
            base = (members * len(CATEGORIES) + cat) * BIG
            lo, hi = np.searchsorted(cum_key, base + start), np.searchsorted(cum_key, base + end)
        return [c[hi] - c[lo] for c in cums]

    def member_features(members, chambers, congress, day, H):
        """[n, F, H] member features over H windows of WINDOW_DAYS before ``day`` (exclusive)."""
        n = len(members)
        x = np.full((n, len(FEATURES), H), np.nan, dtype=np.float32)
        ok = members >= 0
        mm = np.where(ok, members, 0)
        for h in range(H):
            end, start = day - WINDOW_DAYS * h, day - WINDOW_DAYS * (h + 1)
            u, d, v = window(mm, start, end, key_md, cum_u, cum_d, cum_v)
            held = np.array([np.searchsorted(chamber_dates[c], e) - np.searchsorted(chamber_dates[c], s)
                             for c, s, e in zip(chambers, start, end)])
            has = ok & (u > 0)
            x[has, FEATURES.index('mem_defect_rate'), h] = d[has] / u[has]
            x[ok, FEATURES.index('mem_unity_votes_log'), h] = np.log1p(u[ok])
            part = ok & (held > 0)
            x[part, FEATURES.index('mem_participation'), h] = v[part] / held[part]
        p = [party.get((int(g), 'Senate' if c else 'House', int(m))) for m, c, g in zip(mm, chambers, congress)]
        x[ok, FEATURES.index('mem_party_d'), 0] = [float(q == DEMOCRAT) for q, o in zip(p, ok) if o]
        x[ok, FEATURES.index('mem_party_r'), 0] = [float(q == REPUBLICAN) for q, o in zip(p, ok) if o]
        x[ok, FEATURES.index('mem_senate'), 0] = chambers[ok]
        seniority = [sum(1 for g2 in range(80, int(g)) if (g2, 'Senate' if c else 'House', int(m)) in party)
                     for m, c, g in zip(mm, chambers, congress)]
        x[ok, FEATURES.index('mem_seniority'), 0] = np.log1p(np.array(seniority))[ok]
        return x

    # ---- similar members per (chamber, quarter): agreement on unity votes in the two years before the quarter
    similar = {}
    for q in range(first_quarter, last_quarter + 1):
        start_q = quarter_start_ordinal(q)
        for c in (0, 1):
            sel = rc_unity & (rc_chamber == c) & (rc_date < start_q) & (rc_date >= start_q - SIMILAR_WINDOW_DAYS)
            rcs = np.flatnonzero(sel)
            if not len(rcs):
                continue
            mask = np.isin(vr, rcs)
            members = np.unique(vm[mask])
            row = np.searchsorted(members, vm[mask])
            col = np.searchsorted(rcs, vr[mask])
            A = np.zeros((len(members), len(rcs)), dtype=np.float32)   # voted yea or nay
            S = np.zeros_like(A)                                        # +1 yea, -1 nay
            A[row, col] = 1.0
            S[row, col] = vsign[mask]
            common = (A @ A.T)
            same = (S @ S.T + common) / 2
            with np.errstate(divide='ignore', invalid='ignore'):
                agree = np.where(common >= 20, same / common, -1)
            np.fill_diagonal(agree, -1)
            top = np.argsort(-agree, axis=1)[:, :K]
            similar[(c, q)] = {int(m): [(int(members[j]), float(agree[i, j])) for j in top[i] if agree[i, j] >= 0]
                               for i, m in enumerate(members)}
    # ---- samples
    parts, leakage = [], 0
    n_nodes, edges, _ = template()
    for q in range(first_quarter, last_quarter + 1):
        start_q, end_q = quarter_start_ordinal(q), quarter_start_ordinal(q + 1)
        cand = np.flatnonzero(vunity & (vd >= start_q) & (vd < end_q))
        if not len(cand):
            continue
        cand = rng.permutation(cand)
        count = defaultdict(int)
        chosen = []
        for i in cand:
            if count[vm[i]] < per_member:
                count[vm[i]] += 1
                chosen.append(i)
                if len(chosen) >= per_quarter:
                    break
        chosen = np.array(chosen)
        S = len(chosen)
        members, day, rc = vm[chosen], vd[chosen], vr[chosen]
        chambers, congress, cats = rc_chamber[rc], rc_congress[rc], rc_cat[rc]
        x = np.full((S, n_nodes, len(FEATURES), history), np.nan, dtype=np.float32)
        x[:, 1] = member_features(members, chambers, congress, day, history)
        # Pair features.
        sponsor = np.array([(data['bill_of'].get(keys[r], (None, None))[0] or -1) for r in rc], dtype=np.int64)
        introduced = [data['bill_of'].get(keys[r], (None, None))[1] for r in rc]
        own_party = np.array([party.get((int(g), 'Senate' if c else 'House', int(m))) for m, c, g in zip(members, chambers, congress)])
        sponsor_party = np.array([party.get((int(g), 'Senate' if c else 'House', int(s))) if s >= 0 else None
                                  for s, c, g in zip(sponsor, chambers, congress)])
        has_sponsor = sponsor >= 0
        x[:, 0, FEATURES.index('pair_is_sponsor'), 0] = (sponsor == members).astype(float)
        x[has_sponsor, 0, FEATURES.index('pair_same_party_as_sponsor'), 0] = (own_party == sponsor_party)[has_sponsor]
        cu, cd = window(members, day - SIMILAR_WINDOW_DAYS, day, key_mcd, cum_cu, cum_cd, cat=cats)
        ok = cu > 0
        x[ok, 0, FEATURES.index('pair_category_defect_rate'), 0] = cd[ok] / cu[ok]
        x[:, 0, FEATURES.index('pair_category_votes_log'), 0] = np.log1p(cu)
        # Roll-call features (all fixed before the vote).
        rcx = x[:, 2, :, 0]
        rcx[:, FEATURES.index('rc_senate')] = chambers
        rcx[:, FEATURES.index('rc_two_thirds')] = [float((data['rollcalls'].get(keys[r], (None, None, None))[1] or '') == '2/3') for r in rc]
        rcx[:, FEATURES.index('rc_nomination')] = (cats == CATEGORIES.index('nomination'))
        rcx[:, FEATURES.index('rc_has_bill')] = [float(keys[r] in data['bill_of']) for r in rc]
        days = np.array([(d - i.toordinal()) if i is not None else np.nan for d, i in zip(day, introduced)], dtype=float)
        rcx[:, FEATURES.index('rc_days_since_introduced_log')] = np.log1p(np.clip(days, 0, None))
        rcx[has_sponsor, FEATURES.index('rc_sponsor_d')] = (sponsor_party == DEMOCRAT)[has_sponsor]
        rcx[has_sponsor, FEATURES.index('rc_sponsor_r')] = (sponsor_party == REPUBLICAN)[has_sponsor]
        for j, c in enumerate(CATEGORIES):
            rcx[:, FEATURES.index(f'rc_q_{c}')] = (cats == j)
        x[:, 2, :, 0] = rcx
        # Sponsor, similar members and delegation: member features as of the same day.
        x[:, 3] = member_features(np.where(has_sponsor, sponsor, -1), chambers, congress, day, history)
        sim = np.full((S, K), -1, dtype=np.int64)
        sim_w = np.zeros((S, K), dtype=np.float32)
        dele = np.full((S, K), -1, dtype=np.int64)
        delegations = defaultdict(list)
        for (g, ch, m), st in state.items():
            delegations[(g, ch, st)].append(m)
        for i in range(S):
            ch = 'Senate' if chambers[i] else 'House'
            for j, (m2, a) in enumerate(similar.get((int(chambers[i]), q), {}).get(int(members[i]), [])[:K]):
                sim[i, j], sim_w[i, j] = m2, a
            st = state.get((int(congress[i]), ch, int(members[i])))
            others = sorted(m for m in delegations.get((int(congress[i]), ch, st), []) if m != members[i])[:K]
            dele[i, :len(others)] = others
        rep = lambda a: np.repeat(a, K)
        x[:, 4:4 + K] = member_features(sim.ravel(), rep(chambers), rep(congress), rep(day), history).reshape(S, K, len(FEATURES), history)
        x[:, 4 + K:] = member_features(dele.ravel(), rep(chambers), rep(congress), rep(day), history).reshape(S, K, len(FEATURES), history)
        valid = np.ones((S, n_nodes), dtype=bool)
        valid[:, 3] = has_sponsor
        valid[:, 4:4 + K] = sim >= 0
        valid[:, 4 + K:] = dele >= 0
        w = np.ones((S, len(edges)), dtype=np.float32)
        for j in range(K):
            base = 6 + 4 * j
            w[:, base:base + 2] = sim_w[:, j][:, None]
        # Baseline: the member's defection rate on unity votes in the 365 days before the vote.
        u, d = window(members, day - 365, day, key_md, cum_u, cum_d)
        own = np.where(u > 0, d / np.maximum(u, 1), np.nan)
        # Leakage audit: every window is half-open, [start, day), so no vote on or after the sample's own day enters;
        # the similar-member agreement uses roll calls before the quarter began. Structural, and unit-tested.
        parts.append((x, valid, w, vdefect[chosen].astype(float)[:, None], np.full(S, q),
                      np.array([day_number(date.fromordinal(int(d_))) for d_ in day]), members, rc,
                      own[:, None], np.full((S, 1), np.nan)))
        if log and q % 8 == 0:
            log(f'    quarter {quarter_end(q)}: {S} samples')
    cat = lambda i: np.concatenate([p[i] for p in parts])
    x = cat(0)
    mask = np.isfinite(x)
    return VoteTasks(np.nan_to_num(x).astype(np.float16), mask.astype(np.uint8), cat(1), cat(2), cat(3), cat(4), cat(5),
                     cat(6), cat(7), cat(8), cat(9), {}, leakage)
