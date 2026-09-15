"""Four-transition isolated economy rollouts, checked against a sequential oracle."""
import json
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from worldmodel.coupled_economy import initialize_economy, step_economy
from worldmodel.execution_journal import ExecutionJournal
from worldmodel.parallel_rollouts import run_episodes
from worldmodel.scenario_benchmark import economy_scenario_example
from worldmodel.structured_spaces import StructuredSpace


class EconomyEpisode:
    def __init__(self, config): self.config = config
    def reset(self, seed=0):
        self.state = initialize_economy(self.config['initial_state']); self.index = 0
        return {'inventory': self.state['firms'][0]['inventory']}, {}
    def step(self, action):
        self.state = step_economy(self.state, action, self.config['shocks'][self.index]); self.index += 1
        inventory = self.state['firms'][0]['inventory']
        return {'inventory': inventory}, inventory, self.index == len(self.config['shocks']), False, {}
    def close(self): pass


def economy_factory(config): return EconomyEpisode(config)


def main():
    request = economy_scenario_example()
    episodes = [{'id': scenario['id'], 'seed': 101 + i, 'config': scenario['config'],
                 'actions': request['policies'][2]['actions']} for i, scenario in enumerate(request['scenarios'][:2])]
    kwargs = {'factory': '__main__:economy_factory', 'max_transitions': 4, 'timeout_seconds': 5, 'max_result_bytes': 8192}
    with tempfile.TemporaryDirectory() as temporary:
        with ExecutionJournal(Path(temporary) / 'quota.sqlite') as journal:
            journal.configure_quota('rollouts', 8)
            parallel = run_episodes(episodes, workers=2, journal=journal, reservation_key='parallel', **kwargs)
            oracle = run_episodes(episodes, mode='sequential', journal=journal, reservation_key='oracle', **kwargs)
    if parallel != oracle: raise AssertionError('Sequential oracle mismatch')
    declaration = StructuredSpace({'type': 'object', 'properties': {
        'inventory': {'type': 'number', 'minimum': 0, 'maximum': 100, 'unit': 'goods'},
        'forecast': {'type': 'vector', 'length': 2, 'minimum': 0, 'maximum': 100, 'unit': 'goods', 'nullable': True}}})
    print(json.dumps({'rollouts': parallel, 'sequential_oracle_equal': True,
        'total_transitions_including_oracle': 8, 'observation_space': declaration.declaration(),
        'masked_example': declaration.flatten({'inventory': 2, 'forecast': None}),
        'limitations': 'Synthetic numerical scenarios; seeds declare episode identity, this economy is deterministic.'}, indent=2))


if __name__ == '__main__': main()
