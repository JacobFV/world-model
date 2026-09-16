import unittest

from worldmodel.models.games import Game, MultiActorEnvironment, compare_strategies, solve_game
from worldmodel.perception import split_observations
from worldmodel.strategy import rank_outcomes


def dilemma(profile, scenario):
    table = {('C', 'C'): (3, 3), ('C', 'D'): (0, 5), ('D', 'C'): (5, 0), ('D', 'D'): (1, 1)}
    left, right = table[(profile['a'], profile['b'])]
    return {'a_payoff': left, 'b_payoff': right}


def actor(name, actions, **extra):
    return {'id': name, 'goals': [{'metric': f'{name}_payoff'}], 'actions': [{'id': x} for x in actions], **extra}


class GameSolverTests(unittest.TestCase):
    def test_prisoners_dilemma_pure_nash_best_response_and_comparison(self):
        spec = {'actors': [actor('a', 'CD'), actor('b', 'CD')], 'solver': {'method': 'pure_nash'}}
        result = solve_game(spec, dilemma)
        self.assertEqual(result['equilibria'][0]['strategy'], {'a': {'D': 1.0}, 'b': {'D': 1.0}})
        self.assertFalse(result['causally_calibrated'])
        dynamics = solve_game({**spec, 'solver': {'method': 'best_response_dynamics'}}, dilemma)
        self.assertTrue(dynamics['converged'])
        compared = compare_strategies(spec, 'a', evaluate=dilemma)
        self.assertEqual(compared['recommended_policy'], 'D')
        self.assertEqual(compared['responses']['C'], {'a': {'C': 1.0}, 'b': {'D': 1.0}})

    def test_matching_pennies_mixed_equilibrium(self):
        pennies = lambda p, s: {'a_payoff': 1 if p['a'] == p['b'] else -1, 'b_payoff': -1 if p['a'] == p['b'] else 1}
        spec = {'actors': [actor('a', 'HT'), actor('b', 'HT')], 'solver': {'method': 'support_enumeration'}}
        equilibria = solve_game(spec, pennies)['equilibria']
        self.assertEqual(len(equilibria), 1)
        self.assertAlmostEqual(equilibria[0]['strategy']['a']['H'], 0.5)
        self.assertEqual(solve_game({**spec, 'solver': {'method': 'pure_nash'}}, pennies)['equilibria'], [])
        play = solve_game({**spec, 'solver': {'method': 'fictitious_play', 'iterations': 3000, 'tolerance': 0}}, pennies)
        self.assertLess(play['nash_conv'], 0.05)

    def test_private_signals_budgets_and_evaluation_bounds(self):
        def entry(profile, scenario):
            demand = 4 if scenario['id'] == 'high' else -2
            enter = profile['entrant'] == 'enter'
            fight = profile['incumbent'] == 'fight'
            return {'entrant_payoff': (demand - (3 if fight else 0)) if enter else 0,
                    'incumbent_payoff': (2 if enter else 5) - (1 if fight else 0)}
        spec = {'actors': [actor('entrant', ['stay', 'enter'], observes=['demand'], budget={'capital': 1}),
                           {**actor('incumbent', ['accommodate', 'fight', 'war']),
                            'actions': [{'id': 'accommodate'}, {'id': 'fight'}, {'id': 'war', 'cost': {'capital': 9}}]}],
                'scenarios': [{'id': 'high', 'probability': 0.5, 'signals': {'demand': 'high'}},
                              {'id': 'low', 'probability': 0.5, 'signals': {'demand': 'low'}}]}
        spec['actors'][0]['actions'][1]['cost'] = {'capital': 1}
        result = solve_game(spec, entry)
        self.assertEqual(result['infeasible_actions']['incumbent'][0]['action'], 'war')
        strategy = result['equilibria'][0]['strategy']
        self.assertEqual(strategy['entrant|demand="high"'], {'enter': 1.0})
        self.assertEqual(strategy['entrant|demand="low"'], {'stay': 1.0})
        with self.assertRaisesRegex(ValueError, 'max_evaluations'):
            Game({**spec, 'max_evaluations': 3}, entry)

    def test_model_environment_rejects_conflicting_actor_levers(self):
        from worldmodel.models.trade import example_config
        base = example_config()
        spec = {'environment': {'family': 'trade', 'base': base},
                'actors': [{'id': 'x', 'goals': [{'metric': 'usa'}], 'actions': [{'id': 'set', 'overrides': {'fidelity': 'partial'}}]},
                           {'id': 'y', 'goals': [{'metric': 'usa'}], 'actions': [{'id': 'set', 'overrides': {'fidelity': 'general_equilibrium'}}]}],
                'metrics': {'usa': {'path': ['bilateral_trade_change', 'CHN->USA']}}}
        with self.assertRaisesRegex(ValueError, 'both set fidelity'):
            solve_game(spec)

    def test_trade_war_example_runs_through_general_equilibrium(self):
        import json
        from pathlib import Path
        spec = json.loads((Path(__file__).resolve().parents[1] / 'examples/models-trade-war-game.json').read_text())
        result = solve_game(spec)
        self.assertEqual(result['evaluations'], 18)
        self.assertEqual(len(result['players']), 3)
        self.assertTrue(result['equilibria'])


class RankingAndPerceptionTests(unittest.TestCase):
    def test_rank_outcomes_orders_infeasible_last_and_requires_complete_rows(self):
        scenarios = [{'id': 's1'}, {'id': 's2'}]
        rows = [{'policy': 'p', 'scenario': 's1', 'probability': .5, 'value': 10, 'violations': []},
                {'policy': 'p', 'scenario': 's2', 'probability': .5, 'value': 0, 'violations': [{'metric': 'x'}]},
                {'policy': 'q', 'scenario': 's1', 'probability': .5, 'value': 4, 'violations': []},
                {'policy': 'q', 'scenario': 's2', 'probability': .5, 'value': 4, 'violations': []}]
        ranking = rank_outcomes(['p', 'q'], scenarios, rows, 1, 'minimax_regret')
        self.assertEqual([r['policy'] for r in ranking], ['q', 'p'])
        self.assertEqual(ranking[0]['max_regret'], 6)
        with self.assertRaisesRegex(ValueError, 'one outcome per scenario'):
            rank_outcomes(['p', 'q'], scenarios, rows[:3], 1)

    def test_split_observations_copies_and_rejects_unknown_keys(self):
        observation = {'price': 1, 'secret': {'v': 2}}
        views = split_observations(observation, {'a': ['price'], 'b': ['price', 'secret']})
        views['b']['secret']['v'] = 9
        self.assertEqual(observation['secret']['v'], 2)
        self.assertNotIn('secret', views['a'])
        with self.assertRaisesRegex(ValueError, 'undeclared'):
            split_observations(observation, {'a': ['missing']})

    def test_multi_actor_environment_partitions_actions_budgets_and_private_rewards(self):
        class Env:
            spec = {'actions': {'tax': {}, 'spend': {}}, 'observations': {'revenue': {}, 'welfare': {}}}
            def reset(self, seed=0):
                self.state = {'revenue': 0.0, 'welfare': 0.0}
                return dict(self.state), {'step': 0}
            def step(self, actions):
                self.state = {'revenue': self.state['revenue'] + actions['tax'], 'welfare': self.state['welfare'] + actions['spend'] - actions['tax']}
                return dict(self.state), 0.0, False, False, {'step': 1}
        env = MultiActorEnvironment(Env(), [
            {'id': 'treasury', 'controls': ['tax'], 'observes': ['revenue'], 'goals': [{'observation': 'revenue'}]},
            {'id': 'ministry', 'controls': ['spend'], 'observes': ['welfare'], 'goals': [{'observation': 'welfare'}],
             'budget': {'amount': 5, 'costs': {'spend': 1}}}])
        views, info = env.reset()
        self.assertEqual(views, {'treasury': {'revenue': 0.0}, 'ministry': {'welfare': 0.0}})
        views, rewards, _, _, info = env.step({'treasury': {'tax': 2}, 'ministry': {'spend': 3}})
        self.assertEqual(rewards, {'treasury': 2, 'ministry': 1})
        self.assertEqual(info['ministry']['budget_remaining'], 2)
        with self.assertRaisesRegex(ValueError, 'budget'):
            env.step({'treasury': {'tax': 0}, 'ministry': {'spend': 3}})
        with self.assertRaisesRegex(ValueError, 'partition'):
            MultiActorEnvironment(Env(), [{'id': 'solo', 'controls': ['tax']}])


if __name__ == '__main__':
    unittest.main()
