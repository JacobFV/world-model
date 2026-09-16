import unittest
from worldmodel.rl import train_tabular, VectorEnvironment, numerical_space_spec, gymnasium_adapter


class ChoiceEnvironment:
    """One-step contextual reward toy; no world-model validity implication."""
    spec = {'actions': {'choice': {'type': 'number', 'minimum': 0, 'maximum': 1}},
            'observations': {'context': {}}}

    def reset(self, seed=0):
        self.context = seed % 2
        return {'context': self.context}, {}

    def step(self, action):
        return {'context': self.context}, float(action['choice'] == self.context), True, False, {}


class RLTests(unittest.TestCase):
    def test_learns_toy_better_than_baseline_on_disjoint_seeds(self):
        kwargs = dict(env_factory=ChoiceEnvironment, actions=[{'choice': 0}, {'choice': 1}],
                      observation_encoder=lambda o: o['context'], training_seeds=list(range(200)),
                      evaluation_seeds=list(range(1000, 1040)), seed=3, max_steps=1)
        result = train_tabular(**kwargs)
        self.assertEqual(result, train_tabular(**kwargs))
        self.assertEqual(result['evaluation']['mean_return'], 1)
        self.assertEqual(result['baseline']['mean_return'], .5)
        self.assertEqual(result['evaluation']['episodes'], 40)
        self.assertFalse(set(result['training_seeds']) & set(result['evaluation_seeds']))

    def test_rejects_seed_overlap_and_work_budget(self):
        kwargs = dict(env_factory=ChoiceEnvironment, actions=[{'choice': 0}], observation_encoder=lambda o: 0,
                      training_seeds=[1], evaluation_seeds=[1])
        with self.assertRaises(ValueError): train_tabular(**kwargs)
        kwargs.update(evaluation_seeds=[2], max_steps=1001)
        from worldmodel.limits import LimitExceeded
        with self.assertRaisesRegex(LimitExceeded, 'environment_max_steps'): train_tabular(**kwargs, limits={'environment_max_steps': 1000})
        with self.assertRaisesRegex(LimitExceeded, 'rl_max_transitions'): train_tabular(**kwargs, limits={'rl_max_transitions': 3002})

    def test_vector_seeds_and_batch_validation(self):
        batch = VectorEnvironment([ChoiceEnvironment, ChoiceEnvironment])
        observations, infos = batch.reset([2, 3])
        self.assertEqual(observations, [{'context': 0}, {'context': 1}])
        with self.assertRaises(ValueError): batch.step([{'choice': 0}])
        obs, rewards, terminated, truncated, infos = batch.step([{'choice': 0}, {'choice': 1}])
        self.assertEqual(rewards, [1, 1])
        self.assertEqual(terminated, [True, True])
        with self.assertRaises(ValueError): batch.step([{'choice': 0}, {'choice': 1}])

    def test_explicit_numerical_space_declaration(self):
        spaces = numerical_space_spec(ChoiceEnvironment(), {'context': [0, 1]})
        self.assertEqual(spaces['actions']['choice'], {'low': 0, 'high': 1, 'shape': []})
        with self.assertRaises(ValueError): numerical_space_spec(ChoiceEnvironment(), {})

    def test_optional_gymnasium_dependency_boundary(self):
        try:
            import gymnasium
        except ImportError:
            with self.assertRaisesRegex(ImportError, 'pip install.*gymnasium'):
                gymnasium_adapter(ChoiceEnvironment(), {'context': [0, 1]})
        else:
            env = gymnasium_adapter(ChoiceEnvironment(), {'context': [0, 1]})
            obs, info = env.reset(seed=0)
            self.assertTrue(env.observation_space.contains(obs))
            self.assertEqual(env.step({'choice': 0})[1], 1)

    def test_public_toy_benchmark_is_bounded_and_labeled(self):
        from worldmodel.rl import toy_learning_benchmark
        result = toy_learning_benchmark()
        self.assertEqual(result['evaluation']['mean_return'], 1)
        self.assertGreater(result['mean_return_improvement'], 0)
        self.assertEqual(result['transitions'], 280)
        self.assertFalse(result['causally_validated'])

    def test_evaluation_does_not_add_unseen_q_states(self):
        class NewContext(ChoiceEnvironment):
            def reset(self, seed=0):
                self.context = seed
                return {'context': seed}, {}
        result = train_tabular(NewContext, [{'choice': 0}], lambda o: o['context'],
                               training_seeds=[1], evaluation_seeds=[2], max_steps=1)
        self.assertEqual(set(result['q_table']), {'1'})

    def test_trainer_bounds_nonterminating_environment(self):
        class Endless(ChoiceEnvironment):
            def step(self, action): return {'context': 0}, 1, False, False, {}
        result = train_tabular(Endless, [{'choice': 0}], lambda o: 0,
                               training_seeds=[1], evaluation_seeds=[2], max_steps=3)
        self.assertEqual(result['transitions'], 9)
        self.assertEqual(result['evaluation']['trainer_capped_episodes'], 1)

    def test_large_finite_rewards_produce_finite_summaries(self):
        import json
        import math
        class LargeReward(ChoiceEnvironment):
            def step(self, action): return {'context': 0}, 1e308, True, False, {}
        result = train_tabular(LargeReward, [{'choice': 0}], lambda o: 0,
                               training_seeds=[0], evaluation_seeds=[1, 2], max_steps=1)
        self.assertEqual(result['evaluation']['mean_return'], 1e308)
        self.assertTrue(math.isfinite(result['baseline']['mean_return']))
        self.assertEqual(result['mean_return_improvement'], 0)
        json.dumps(result, allow_nan=False)

    def test_numerical_spaces_accept_filtered_wrapper_and_reject_masks(self):
        from worldmodel.perception import ObservationWrapper
        class ExtraObservation(ChoiceEnvironment):
            spec = {'actions': ChoiceEnvironment.spec['actions'], 'observations': {'context': {}, 'hidden': {}}}
        env = ObservationWrapper(ExtraObservation(), ['context'])
        declaration = numerical_space_spec(env, {'context': [0, 1]})
        self.assertEqual(set(declaration['observations']), {'context'})
        env.spec['observations']['injected'] = {}
        self.assertEqual(set(env.spec['observations']), {'context'})
        masked = ObservationWrapper(ExtraObservation(), ['context'], masked=['context'])
        with self.assertRaisesRegex(ValueError, 'masked'):
            numerical_space_spec(masked, {'context': [0, 1]})

    def test_unrepresentable_improvement_is_rejected(self):
        class ExtremeChoices(ChoiceEnvironment):
            def step(self, action):
                return {'context': 0}, 1e308 if action['choice'] else -1e308, True, False, {}
        with self.assertRaisesRegex(ValueError, 'mean return improvement'):
            train_tabular(ExtremeChoices, [{'choice': 0}, {'choice': 1}], lambda o: 0,
                          training_seeds=list(range(20)), evaluation_seeds=[21, 22],
                          max_steps=1, epsilon=1, alpha=1, seed=3)
