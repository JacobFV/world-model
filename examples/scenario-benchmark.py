"""Run a 30-transition synthetic scenario comparison and five-call ambiguity probe."""
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldmodel.scenario_benchmark import benchmark_scenarios, parameter_ensemble


def rate_model(parameters, scenario):
    return {'rate': scenario.get('policy_rate', parameters['policy_rate']) + parameters['spread']}


def main():
    request = json.loads(Path(__file__).with_suffix('.json').read_text())
    report = benchmark_scenarios(**request)
    ensemble = parameter_ensemble(rate_model,
        [{'id': 'low_policy', 'parameters': {'policy_rate': .04, 'spread': .06}},
         {'id': 'high_policy', 'parameters': {'policy_rate': .08, 'spread': .02}},
         {'id': 'zero_baseline', 'parameters': {'policy_rate': 0, 'spread': 0}}],
        [{'input': {}, 'path': ['rate'], 'value': .1, 'unit': 'annual_fraction',
          'epistemic_status': 'synthetic_scenario'}],
        probes=[{'id': 'withheld_rate_override', 'input': {'policy_rate': .2}}],
        outputs={'rate': {'path': ['rate'], 'unit': 'annual_fraction'}},
        baseline_id='zero_baseline', tolerance=1e-12, max_evaluations=6)
    print(json.dumps({'scenario_comparison': report, 'parameter_ambiguity': ensemble}, indent=2, allow_nan=False))


if __name__ == '__main__': main()
