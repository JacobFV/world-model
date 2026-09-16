"""Scale paths for exposure clearing, compiled routing and coupling ledgers."""
from datetime import date
import math
import random
import unittest

from worldmodel.backends import numpy_available
from worldmodel.exposure import stress_exposures, stress_exposure_arrays
from worldmodel.limits import LimitExceeded
from worldmodel.process_contracts import CouplingLedger
from worldmodel.transport import compile_network, route
from worldmodel.util import canonical, digest


def reference_clear(config):
    """Verbatim pre-array clearing algorithm (dict per entity), used as an oracle."""
    def number(value, nonnegative=True):
        if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or (nonnegative and value < 0):
            raise ValueError('Expected finite number')
        return float(value)
    start, end = date.fromisoformat(config['as_of']), date.fromisoformat(config['end'])
    ids = [r['id'] for r in config['entities']]
    cash = {r['id']: number(r['cash']) for r in config['entities']}
    shock = number(config['shock_bps'], False) / 10000
    prepared = []
    for row in config['obligations']:
        maturity = date.fromisoformat(row['maturity'])
        principal, rate = number(row['principal']), number(row['annual_rate'])
        stressed = number(rate + (shock if row['rate_type'] == 'floating' else 0))
        fraction = (min(end, maturity) - start).days / 365
        principal_due = principal if maturity <= end else 0
        prepared.append({**row, 'baseline_due': principal_due + principal * rate * fraction,
                         'stressed_due': principal_due + principal * stressed * fraction, 'principal_due': principal_due})

    def clear(mode):
        due = {key: 0.0 for key in ids}
        for row in prepared: due[row['borrower']] += row[mode + '_due']
        paid = due.copy()
        for iteration in range(10000):
            incoming = {key: 0.0 for key in ids}
            for row in prepared:
                borrower = row['borrower']
                incoming[row['lender']] += paid[borrower] * (row[mode + '_due'] / due[borrower] if due[borrower] else 0)
            updated = {key: min(due[key], cash[key] + incoming[key]) for key in ids}
            error = max(abs(updated[key] - paid[key]) for key in ids)
            paid = updated
            if error <= 1e-9: break
        incoming = {key: 0.0 for key in ids}; loans = []
        for row in prepared:
            borrower = row['borrower']; liability = row[mode + '_due']
            payment = paid[borrower] * (liability / due[borrower] if due[borrower] else 0)
            incoming[row['lender']] += payment
            loans.append({'id': row['id'], 'borrower': borrower, 'lender': row['lender'], 'due': liability,
                          'principal_due': row['principal_due'], 'paid': payment, 'shortfall': liability - payment})
        state = [{'id': key, 'due': due[key], 'paid': paid[key], 'received': incoming[key],
                  'ending_cash': cash[key] + incoming[key] - paid[key], 'shortfall': due[key] - paid[key]} for key in ids]
        residual = sum(r['ending_cash'] for r in state) - sum(cash.values())
        return {'entities': state, 'obligations': loans, 'iterations': iteration + 1,
                'total_shortfall': sum(r['shortfall'] for r in loans), 'cash_conservation_residual': residual}
    return {'baseline': clear('baseline'), 'stressed': clear('stressed')}


def network(seed, entities=25, obligations=90):
    rng = random.Random(seed)
    n = rng.randint(2, entities)
    config = {'as_of': '2026-01-01', 'end': '2027-01-01', 'currency': 'USD', 'shock_bps': rng.choice([0, 150, 400]),
              'entities': [{'id': f'e{i}', 'cash': rng.choice([0, rng.randint(0, 40), rng.uniform(0, 120)])} for i in range(n)],
              'obligations': []}
    for k in range(rng.randint(0, obligations)):
        b, l = rng.sample(range(n), 2)
        config['obligations'].append({'id': f'o{k}', 'borrower': f'e{b}', 'lender': f'e{l}', 'principal': rng.uniform(0, 250),
                                      'annual_rate': rng.uniform(0, .15), 'rate_type': rng.choice(['fixed', 'floating']),
                                      'maturity': rng.choice(['2026-02-01', '2026-12-31', '2027-01-01', '2028-01-01'])})
    # A guaranteed cycle and a guaranteed shortfall.
    config['obligations'] += [{'id': 'cycle1', 'borrower': 'e0', 'lender': 'e1', 'principal': 500, 'annual_rate': 0, 'rate_type': 'fixed', 'maturity': '2026-06-01'},
                              {'id': 'cycle2', 'borrower': 'e1', 'lender': 'e0', 'principal': 400, 'annual_rate': .1, 'rate_type': 'floating', 'maturity': '2026-06-01'}]
    return config


def arrays(config):
    index = {e['id']: i for i, e in enumerate(config['entities'])}
    start = date.fromisoformat(config['as_of'])
    rows = config['obligations']
    return dict(as_of=config['as_of'], end=config['end'], shock_bps=config['shock_bps'],
                cash=[e['cash'] for e in config['entities']], borrower=[index[r['borrower']] for r in rows],
                lender=[index[r['lender']] for r in rows], principal=[r['principal'] for r in rows],
                annual_rate=[r['annual_rate'] for r in rows], floating=[r['rate_type'] == 'floating' for r in rows],
                maturity_days=[(date.fromisoformat(r['maturity']) - start).days for r in rows],
                entity_ids=[e['id'] for e in config['entities']])


def strip(result):
    return {key: value for key, value in result.items() if key != 'execution'}


class ExposureScaleTests(unittest.TestCase):
    def test_python_backend_matches_dict_reference_bit_for_bit(self):
        for seed in range(25):
            config = network(seed)
            expected = reference_clear(config)
            result = stress_exposures(config, backend='python')
            for mode in ('baseline', 'stressed'):
                self.assertEqual(canonical(result[mode]), canonical(expected[mode]), (seed, mode))
            self.assertGreater(result['baseline']['total_shortfall'], 0)

    @unittest.skipUnless(numpy_available(), 'numpy backend not installed')
    def test_numpy_backend_is_bit_identical_for_full_and_summary(self):
        for seed in range(25):
            config = network(seed)
            for detail in ('full', 'summary'):
                python = stress_exposures(config, backend='python', detail=detail, top=5)
                vector = stress_exposures(config, backend='numpy', detail=detail, top=5)
                self.assertEqual(canonical(strip(python)), canonical(strip(vector)), (seed, detail))
                self.assertEqual(vector['execution']['backend'], 'numpy')

    def test_summary_totals_match_full_detail(self):
        config = network(3)
        full, summary = stress_exposures(config, backend='python'), stress_exposures(config, backend='python', detail='summary', top=3)
        for mode in ('baseline', 'stressed'):
            self.assertEqual(summary[mode]['total_shortfall'], full[mode]['total_shortfall'])
            self.assertEqual(summary[mode]['iterations'], full[mode]['iterations'])
            self.assertNotIn('entities', summary[mode])
            largest = sorted(full[mode]['entities'], key=lambda r: -r['shortfall'])[0]
            self.assertEqual(summary[mode]['largest_entity_shortfalls'][0]['id'], largest['id'])
            self.assertLessEqual(len(summary[mode]['largest_entity_shortfalls']), 3)

    def test_array_input_matches_dict_input(self):
        config = network(7)
        full = stress_exposures(config, backend='python')
        backends = ['python'] + (['numpy'] if numpy_available() else [])
        outputs = []
        for backend in backends:
            result = stress_exposure_arrays(**arrays(config), backend=backend, top=4, return_arrays=True)
            self.assertEqual(result['stressed']['total_shortfall'], full['stressed']['total_shortfall'])
            paid = result['baseline']['arrays']['paid']
            self.assertEqual(list(paid.tolist() if hasattr(paid, 'tolist') else paid), [r['paid'] for r in full['baseline']['entities']])
            result['baseline'].pop('arrays'); result['stressed'].pop('arrays')
            outputs.append(canonical(strip(result)))
            detailed = stress_exposure_arrays(**arrays(config), backend=backend, detail='full')
            self.assertEqual([r['shortfall'] for r in detailed['baseline']['obligations']], [r['shortfall'] for r in full['baseline']['obligations']])
        self.assertEqual(len(set(outputs)), 1)
        bad = arrays(config); bad['lender'] = list(bad['borrower'])
        with self.assertRaises(ValueError): stress_exposure_arrays(**bad, backend='python')

    def test_lowered_limits_reject_and_name_the_limit(self):
        config = network(1)
        with self.assertRaisesRegex(LimitExceeded, 'exposure_max_obligations=2'):
            stress_exposures(config, limits={'exposure_max_obligations': 2})
        with self.assertRaisesRegex(LimitExceeded, 'exposure_max_entities=1.*WORLD_MODEL_LIMITS'):
            stress_exposures(config, limits={'exposure_max_entities': 1})
        with self.assertRaisesRegex(LimitExceeded, 'Clearing work budget exceeded before convergence.*exposure_max_clearing_work'):
            stress_exposures(config, limits={'exposure_max_clearing_work': 5})
        chain = {'as_of': '2026-01-01', 'end': '2027-01-01', 'currency': 'USD', 'shock_bps': 0,
                 'entities': [{'id': f'e{i}', 'cash': 0} for i in range(30)],
                 'obligations': [{'id': f'o{i}', 'borrower': f'e{i}', 'lender': f'e{i + 1}', 'principal': 10, 'annual_rate': 0,
                                  'rate_type': 'fixed', 'maturity': '2026-06-01'} for i in range(29)]}
        with self.assertRaisesRegex(LimitExceeded, 'Clearing failed to converge.*exposure_max_clearing_iterations=3'):
            stress_exposures(chain, limits={'exposure_max_clearing_iterations': 3})
        cascade = stress_exposures(chain)['baseline']
        self.assertGreater(cascade['iterations'], 3)
        self.assertEqual(cascade['total_shortfall'], 290)


MODES = ['road', 'walk', 'air', 'sea', 'rail', 'transfer']


def stamp(seconds):
    return f'2026-01-01T{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}Z'


def graph(seed):
    rng = random.Random(seed)
    n = rng.randint(2, 12)
    edges = []
    for k in range(rng.randint(1, 45)):
        a, b, d = rng.randrange(n), rng.randrange(n), rng.randint(0, 600)
        edge = {'id': f'x{k}', 'source': f'n{a}', 'target': f'n{b}', 'mode': rng.choice(MODES), 'duration_seconds': d,
                'cost': rng.randint(0, 5), 'evidence': [{'source': 'synthetic', 'k': k}]}
        if rng.random() < .3: edge['duration_range_seconds'] = [d // 2, d + rng.randint(0, 300)]
        if rng.random() < .3: edge['departures'] = [stamp(rng.randint(0, 7200)) for _ in range(rng.randint(1, 4))]
        if rng.random() < .2: edge['closures'] = [{'valid_from': stamp(x), 'valid_to': stamp(x + rng.randint(1, 900))} for x in [rng.randint(0, 5000)]]
        if rng.random() < .2: edge['capacity_windows'] = [{'valid_from': stamp(x), 'valid_to': stamp(x + rng.randint(1, 900)), 'capacity': rng.randint(0, 3)} for x in [rng.randint(0, 5000)]]
        if rng.random() < .2: edge['capacity'] = rng.randint(0, 3)
        if rng.random() < .1: edge.update(valid_from=stamp(0), valid_to=stamp(rng.randint(100, 8000)))
        if rng.random() < .1: edge['vehicle_id'] = 'vehicle'
        edges.append(edge)
    result = {'nodes': [{'id': f'n{i}'} for i in range(n)], 'edges': edges}
    if rng.random() < .6:
        result['transfers'] = [{'node': f'n{rng.randrange(n)}', 'from_mode': rng.choice(MODES), 'to_mode': rng.choice(MODES),
                                'minimum_seconds': rng.randint(0, 300), 'valid_from': stamp(0), 'valid_to': stamp(rng.randint(1000, 9000))}
                               for _ in range(rng.randint(0, 20))]
    return result, rng


class CompiledRoutingTests(unittest.TestCase):
    def test_compiled_network_matches_dict_route_on_random_graphs(self):
        statuses = set()
        for seed in range(80):
            net, rng = graph(seed)
            compiled = compile_network(net)
            for _ in range(4):
                request = {'origin': f'n{rng.randrange(len(net["nodes"]))}', 'destination': f'n{rng.randrange(len(net["nodes"]))}',
                           'departure_time': stamp(rng.randint(0, 3000)), 'objective': rng.choice(['earliest_arrival', 'least_cost']),
                           'demand': rng.choice([1, 2]), 'duration_policy': rng.choice(['nominal', 'upper_bound']),
                           'max_labels': rng.choice([5, 100000]), 'max_expansions': rng.choice([3, 10000])}
                if rng.random() < .3: request['permitted_modes'] = rng.sample(MODES, 3)
                expected = route(net, request)
                self.assertEqual(canonical(expected), canonical(route(compiled, request)), seed)
                statuses.add(expected['status'])
                if expected['status'] == 'ok' and expected['legs']:
                    expected['legs'][0]['evidence'].append('mutated')
                    self.assertNotIn('mutated', canonical(route(compiled, request)).decode())
        self.assertEqual(statuses, {'ok', 'unreachable', 'budget_exhausted'})

    def test_route_limits_are_named_and_raisable(self):
        net, _ = graph(5)
        request = {'origin': 'n0', 'destination': 'n1', 'departure_time': stamp(0)}
        with self.assertRaisesRegex(LimitExceeded, 'transport_max_edges=0|transport_max_edges=1'):
            compile_network({**net, 'edges': net['edges'] * 1}, limits={'transport_max_edges': 1} if len(net['edges']) > 1 else {'transport_max_edges': 1, 'transport_max_nodes': 1})
        with self.assertRaisesRegex(LimitExceeded, 'transport_max_search_labels=10'):
            route(net, {**request, 'max_labels': 11}, limits={'transport_max_search_labels': 10})
        self.assertIn(route(net, {**request, 'max_labels': 2_000_000})['status'], ('ok', 'unreachable', 'budget_exhausted'))


class LedgerScaleTests(unittest.TestCase):
    def ledger(self, accounts=50):
        balances = {f'a{i}': {'cash': 1000, 'kg': 10.1 * (i + 1)} for i in range(accounts)}
        names = sorted(balances)
        return CouplingLedger({'cash': {'unit': 'cent', 'integer': True}, 'kg': {'unit': 'kg', 'integer': False}}, balances,
                              {'pay': {'quantity': 'cash', 'sources': names, 'targets': names},
                               'ship': {'quantity': 'kg', 'sources': names, 'targets': names}}, max_transfers=100000)

    def test_batches_touch_only_accounts_and_match_full_recomputation(self):
        ledger = self.ledger(); rng = random.Random(4); count = 0
        for batch in range(30):
            transfers = []
            for _ in range(rng.randint(1, 20)):
                a, b = rng.sample(range(50), 2)
                if rng.random() < .5:
                    transfers.append({'id': f't{count}', 'interface': 'pay', 'source': f'a{a}', 'target': f'a{b}', 'quantity': 'cash', 'unit': 'cent', 'amount': rng.randint(0, 3)})
                else:
                    transfers.append({'id': f't{count}', 'interface': 'ship', 'source': f'a{a}', 'target': f'a{b}', 'quantity': 'kg', 'unit': 'kg', 'amount': rng.uniform(0, .05)})
                count += 1
            report = ledger.apply(transfers)
            self.assertEqual(report['receipt_hash'], digest(ledger.receipts))
            self.assertEqual(report['totals']['kg'], math.fsum(v['kg'] for v in ledger.balances.values()))
            self.assertEqual(report['totals']['cash'], 50000)
        snapshot = ledger.snapshot()
        bad = [{'id': 'fresh', 'interface': 'pay', 'source': 'a1', 'target': 'a2', 'quantity': 'cash', 'unit': 'cent', 'amount': 1},
               {'id': 'overdraw', 'interface': 'pay', 'source': 'a3', 'target': 'a1', 'quantity': 'cash', 'unit': 'cent', 'amount': 10 ** 9}]
        with self.assertRaises(ValueError): ledger.apply(bad)
        self.assertEqual(ledger.snapshot(), snapshot)
        with self.assertRaisesRegex(ValueError, 'Duplicate'): ledger.apply([{**bad[0], 'id': 't0'}])
        self.assertEqual(ledger.snapshot(), snapshot)
        with self.assertRaisesRegex(LimitExceeded, 'contracts_max_batch_transfers=1'):
            CouplingLedger({'cash': {'unit': 'cent', 'integer': True}}, {'a': {'cash': 2}, 'b': {'cash': 0}},
                           {'pay': {'quantity': 'cash', 'sources': ['a'], 'targets': ['b']}},
                           limits={'contracts_max_batch_transfers': 1}).apply([{**bad[0], 'source': 'a', 'target': 'b', 'id': str(i)} for i in range(2)])


if __name__ == '__main__':
    unittest.main()
