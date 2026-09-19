"""Run the three worked decision-layer examples and write their reports to ``outputs/``.

    WORLD_MODEL_DATA=/path/to/data python3 examples/decision/run_examples.py

It reads the published calibration reports, so it needs a data root that holds
``calibration_reports`` (and, for the monetary examples, the ``fred_macro_panel`` and
``fred_cpi`` versions the validated attempt pinned). Outputs are what
docs/decision-layer.md quotes.
"""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from worldmodel.decision.compile import compile_from_store, policies_from, recommend            # noqa: E402
from worldmodel.decision.evidence import EvidenceIndex                                          # noqa: E402
from worldmodel.decision.fragility import stress_test                                           # noqa: E402
from worldmodel.decision.gated_rl import RefusedToOptimize, optimize                            # noqa: E402
from worldmodel.resources import resource_roots                                                 # noqa: E402
from worldmodel.store import Store                                                              # noqa: E402

HERE = Path(__file__).resolve().parent
OUT = HERE / 'outputs'


def write(name, body):
    OUT.mkdir(exist_ok=True)
    (OUT / name).write_text(json.dumps(body, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    print(f'  wrote outputs/{name} ({(OUT / name).stat().st_size // 1024} KiB)')


def trim(report, worst_cases=2):
    """Keep the reports small enough to read: fewer worst cases, no per-bin tables."""
    for policy in report['policies'].values():
        policy['worst_cases'] = policy['worst_cases'][:worst_cases]
        policy['regions']['per_dimension'] = [dict(row, bins=None) for row in policy['regions']['per_dimension'][:6]]
    return report


def main():
    store = Store(resource_roots()['data'])
    print(f'data root: {store.root}')
    index = EvidenceIndex.from_store(store)
    print('evidence:', json.dumps(index.summary()))

    print('\n1. monetary-treasury-stress (validated mechanism, declared scenario bounds)')
    compiled = compile_from_store(json.loads((HERE / 'monetary-treasury-stress.json').read_text()), store, index=index)
    policies = policies_from(json.loads((HERE / 'monetary-treasury-policies.json').read_text()), compiled.contract)
    print('  parameter uncertainty:', json.dumps(compiled.bound.uncertainty))
    started = time.time()
    report = stress_test(compiled, policies, evaluations=2000, seed=7)
    print(f'  {len(policies)} policies x 2000 rollouts in {time.time() - started:.1f}s')
    for claim in (report['policies'][name]['claim'] for name in sorted(report['policies'])):
        print('  -', claim)
    write('monetary-treasury-stress.json', trim(report))
    write('monetary-treasury-rollouts.json',
          {'evidence': compiled.evidence(), 'nominal_scenario': compiled.scenario()['point'],
           'rollouts': {name: compiled.rollout(policy, compiled.scenario(), name=name) for name, policy in policies.items()},
           'recommendation': recommend(compiled, policies, [compiled.scenario()])})

    print('\n2. economy-firm-stress (one validated, one estimated, two assumed mechanisms)')
    economy = compile_from_store(json.loads((HERE / 'economy-firm-stress.json').read_text()), store, index=index)
    for name, resolution in sorted(economy.resolutions.items()):
        print(f'  {name}: declared {resolution["declared_status"]} -> {resolution["status"]}')
    economy_policies = policies_from(json.loads((HERE / 'economy-firm-policies.json').read_text()), economy.contract)
    started = time.time()
    economy_report = stress_test(economy, economy_policies, evaluations=600, seed=3)
    print(f'  {len(economy_policies)} policies x 600 rollouts in {time.time() - started:.1f}s')
    for claim in (economy_report['policies'][name]['claim'] for name in sorted(economy_report['policies'])):
        print('  -', claim)
    write('economy-firm-stress.json', trim(economy_report))

    print('\n3. refusal: the same economy environment, offered to the optimizer')
    request = json.loads((HERE / 'monetary-treasury-optimize-request.json').read_text())
    try:
        optimize(economy, actions=[{'production': 4, 'credit_limit': 60}], encoder_bins={'net_cash': [0.0]},
                 training_seeds=[1], evaluation_seeds=[2])
        raise SystemExit('expected a refusal')
    except RefusedToOptimize as refusal:
        for reason in refusal.report['reasons']:
            print('  refused:', reason)
        write('economy-firm-refusal.json', refusal.report)

    print('\n4. monetary-treasury-optimize (gated policy optimization on validated dynamics)')
    learner = compile_from_store(json.loads((HERE / 'monetary-treasury-optimize.json').read_text()), store, index=index)
    started = time.time()
    seeds = lambda spec: list(range(spec['start'], spec['start'] + spec['count']))
    result = optimize(learner, actions=request['actions'], encoder_bins=request['encoder_bins'],
                      training_seeds=seeds(request['training_seeds']), evaluation_seeds=seeds(request['evaluation_seeds']),
                      baselines=request['baselines'], alpha=request['alpha'], gamma=request['gamma'],
                      epsilon=request['epsilon'], seed=request['seed'], fallback_action=request['fallback_action'])
    print(f'  trained and evaluated in {time.time() - started:.1f}s; gate allowed: {result["gate"]["allowed"]}')
    for split, evaluation in sorted(result['evaluations'].items()):
        print(f'  {split} ({evaluation["description"]}):')
        for name, row in sorted(evaluation['comparison'].items()):
            print(f'    {name:24s} score {row["mean_weighted_score"]:+.4f}  success {row["success_rate"]:.3f}  '
                  f'feasible {row["feasible_rate"]:.3f}')
    write('monetary-treasury-optimize.json', result)
    print('\n' + result['label']['text'])


if __name__ == '__main__':
    main()
