from contextlib import closing
import multiprocessing
from pathlib import Path
import tempfile
import time
import unittest
from worldmodel.parallel_rollouts import run_episodes


class ToyEnvironment:
    def __init__(self, config): self.config = config
    def reset(self, seed=0):
        self.seed = seed
        if self.config.get('sqlite'):
            import sqlite3
            with closing(sqlite3.connect(self.config['sqlite'])) as connection, connection:
                connection.execute('INSERT INTO runs(seed) VALUES (?)', (seed,))
        if self.config.get('fail'): raise ValueError('intentional worker failure')
        time.sleep(self.config.get('delay', 0))
        return {'seed': seed}, {}
    def step(self, action):
        return {'seed': self.seed, 'payload': 'x' * self.config.get('payload', 0)}, action['reward'], True, False, {}
    def close(self):
        if self.config.get('closed'): Path(self.config['closed']).write_text('closed')


def toy_factory(config): return ToyEnvironment(config)


class ParallelRolloutTests(unittest.TestCase):
    def episodes(self):
        return [{'id': 'b', 'seed': 12, 'config': {}, 'actions': [{'reward': 2}]},
                {'id': 'a', 'seed': 11, 'config': {'delay': .15}, 'actions': [{'reward': 1}]}]

    def test_spawn_and_sequential_oracle_match_in_id_order(self):
        args = {'factory': 'tests.test_parallel_rollouts:toy_factory', 'timeout_seconds': 3, 'max_transitions': 2}
        serial = run_episodes(self.episodes(), mode='sequential', **args)
        parallel = run_episodes(self.episodes(), workers=2, **args)
        self.assertEqual(serial, parallel)
        self.assertEqual([r['id'] for r in parallel['episodes']], ['a', 'b'])
        self.assertEqual([r['return'] for r in parallel['episodes']], [1, 2])

    def test_failure_timeout_oversize_cleanup_and_no_retry(self):
        before = {p.pid for p in multiprocessing.active_children()}
        with tempfile.TemporaryDirectory() as root:
            episodes = [{'id': 'error', 'seed': 1, 'config': {'fail': True, 'closed': root + '/closed'}, 'actions': [{'reward': 1}]},
                        {'id': 'timeout', 'seed': 2, 'config': {'delay': 10}, 'actions': [{'reward': 1}]},
                        {'id': 'oversize', 'seed': 3, 'config': {'payload': 10000}, 'actions': [{'reward': 1}]}]
            result = run_episodes(episodes, factory='tests.test_parallel_rollouts:toy_factory', workers=3,
                                  timeout_seconds=.7, max_result_bytes=2000, max_transitions=3)
            statuses = {r['id']: r['status'] for r in result['episodes']}
            self.assertEqual(statuses, {'error': 'error', 'oversize': 'oversize', 'timeout': 'timeout'})
            self.assertTrue(Path(root, 'closed').exists())
        self.assertEqual({p.pid for p in multiprocessing.active_children()}, before)

    def test_journal_reserves_before_dispatch_and_blocks_reexecution(self):
        from worldmodel.execution_journal import ExecutionJournal, QuotaExceeded
        with tempfile.TemporaryDirectory() as root, ExecutionJournal(Path(root) / 'journal.sqlite') as journal:
            journal.configure_quota('rollouts', 1)
            with self.assertRaises(QuotaExceeded):
                run_episodes(self.episodes(), factory='tests.test_parallel_rollouts:toy_factory', journal=journal,
                             reservation_key='batch1', max_transitions=2)
            one = self.episodes()[:1]
            result = run_episodes(one, factory='tests.test_parallel_rollouts:toy_factory', journal=journal,
                                  reservation_key='batch2', max_transitions=1)
            self.assertEqual(journal.quota('rollouts')['used'], 1)
            with self.assertRaisesRegex(ValueError, 'already'): run_episodes(one, factory='tests.test_parallel_rollouts:toy_factory',
                journal=journal, reservation_key='batch2', max_transitions=1)

    def test_validation_before_workers(self):
        for kwargs in ({'max_transitions': 1}, {'workers': 0}, {'timeout_seconds': float('nan')}):
            with self.assertRaises(ValueError): run_episodes(self.episodes(), factory='tests.test_parallel_rollouts:toy_factory', **kwargs)
        episodes = self.episodes(); episodes[0]['live_backend'] = True
        with self.assertRaisesRegex(ValueError, 'live'): run_episodes(episodes, factory='tests.test_parallel_rollouts:toy_factory')

    def test_factory_reconstructs_sqlite_in_each_child(self):
        import sqlite3
        with tempfile.TemporaryDirectory() as root:
            path = str(Path(root) / 'runs.sqlite')
            with closing(sqlite3.connect(path)) as connection, connection: connection.execute('CREATE TABLE runs(seed INTEGER)')
            episodes = self.episodes()
            for episode in episodes: episode['config'] = {'sqlite': path}
            result = run_episodes(episodes, factory='tests.test_parallel_rollouts:toy_factory', workers=2,
                                  timeout_seconds=3, max_transitions=2)
            self.assertEqual(result['completed_episodes'], 2)
            with closing(sqlite3.connect(path)) as connection, connection:
                self.assertEqual(connection.execute('SELECT seed FROM runs ORDER BY seed').fetchall(), [(11,), (12,)])
