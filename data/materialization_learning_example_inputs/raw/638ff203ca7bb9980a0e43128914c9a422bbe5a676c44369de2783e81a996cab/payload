#!/usr/bin/env python3
"""Train against a bounded, fictional checkpoint-backed economy, without network.

From the checkout:
    python3 examples/train-materialization.py
    python3 examples/train-materialization.py --data-root /path/to/data

Requires an existing strategic_scenarios graph. The objective rewards retained
inventory; it is an illustrative learning objective, not economic welfare.
"""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

# Permit direct execution from the checkout without installing the package.
PROJECT = Path(__file__).resolve().parents[1]
if (PROJECT / 'worldmodel').is_dir():
    sys.path.insert(0, str(PROJECT))

from worldmodel.artifacts import publish_report
from worldmodel.checkpoints import CheckpointEvaluator
from worldmodel.cli import reference
from worldmodel.environments import Environment
from worldmodel.perception import ObservationWrapper
from worldmodel.resources import resource_roots
from worldmodel.rl import train_tabular
from worldmodel.store import Store
from worldmodel.util import read_json


def main():
    roots = resource_roots()
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-root', type=Path, default=roots['data'])
    parser.add_argument('--graph', default='strategic_scenarios')
    parser.add_argument('--request', type=Path, default=roots['examples'] / 'environment-coupled-economy.json')
    parser.add_argument('--dataset', default='materialization_learning_example')
    args = parser.parse_args()
    store = Store(args.data_root)
    graph = reference(args.graph, store)
    store.verify(graph)
    script = Path(__file__).resolve()
    if args.request.stat().st_size > 1024 * 1024 or script.stat().st_size > 1024 * 1024:
        raise ValueError('Example inputs exceed the 1 MiB per-file bound')
    raw_script = store.import_file(args.dataset + '_inputs', script,
                                  {'publisher': 'worldmodel project', 'license': 'MIT',
                                   'role': 'exact executable example source'}, update_latest=False)
    raw_request = store.import_file(args.dataset + '_inputs', args.request,
                                   {'publisher': 'user-provided', 'role': 'exact environment request'},
                                   update_latest=False)
    request = read_json(store.artifact_dir(raw_request) / 'payload')
    # Every episode reuses identical endowments; only policies vary. Separate
    # seeds establish train/evaluation separation, not new economic scenarios.
    if request['environment']['max_steps'] != 3 or request['step_seconds'] != 86400:
        raise ValueError('This example requires the supplied three-step daily request')
    actions = deepcopy([request['actions'][2], request['actions'][1], request['actions'][0]])
    training_seeds = list(range(12))
    evaluation_seeds = list(range(100, 104))
    wrapper_parameters = {'allowlist': ['inventory'], 'masked': [], 'delay': 0,
                          'noise_std': 0, 'memory_size': 1}
    trainer_parameters = {'training_seeds': training_seeds, 'evaluation_seeds': evaluation_seeds,
                          'max_steps': 3, 'alpha': .5, 'gamma': .95, 'epsilon': .3,
                          'seed': 17, 'baseline_action': 0}

    def factory():
        evaluator = CheckpointEvaluator(store, graph, request['materialization'],
                                        request['step_seconds'], max_total_calls=3)
        environment = Environment(evaluator, request['environment'])
        return ObservationWrapper(environment, **wrapper_parameters)

    def observation_encoder(observation):
        return observation['inventory']

    result = train_tabular(factory, actions, observation_encoder, **trainer_parameters)
    if result['transitions'] > 100:
        raise ValueError('Example exceeded its 100-transition bound')
    result.update(epistemic_status='synthetic_scenario_learning_example', causally_validated=False,
                  empirical_calibration=False, reward_definition=request['environment']['reward'],
                  limitations=['Reward is inventory accumulation, not validated economic welfare.',
                               'The graph provides provenance; this economy uses explicit fictional endowments.',
                               'Different seeds do not create different deterministic economic scenarios.',
                               'Held-out scores demonstrate interface integration, not policy generalization.'])
    parameters = {'checkpoint_evaluator': {'graph': graph, 'request': request['materialization'],
                                          'step_seconds': request['step_seconds'], 'max_total_calls': 3,
                                          'registry': 'default_registry', 'agent_backend': None},
                  'environment': request['environment'], 'observation_wrapper': wrapper_parameters,
                  'observation_encoder': "observation['inventory']", 'actions': actions,
                  'trainer': trainer_parameters, 'transition_cap': 100}
    artifact = publish_report(store, args.dataset, result, parameters, inputs=[graph],
                              raw_inputs=[raw_script, raw_request], entrypoint='worldmodel.rl:train_tabular')
    print(json.dumps({'artifact': artifact, **result}, indent=2, allow_nan=False))


if __name__ == '__main__':
    main()
