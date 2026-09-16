"""The single explicit limits mechanism and optional backend selection."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worldmodel import limits as limits_module
from worldmodel.backends import numpy_available, resolve_backend
from worldmodel.limits import (DEFAULTS, ENV_VAR, LimitExceeded, Limits, current_limits, describe_limits,
                               parse_limits, resolve_limits, use_limits)


class LimitsTests(unittest.TestCase):
    def setUp(self):
        limits_module._env_cache.clear()

    def test_defaults_are_sized_for_national_targets(self):
        self.assertGreaterEqual(DEFAULTS.coupled_max_firms, 100_000)
        self.assertGreaterEqual(DEFAULTS.coupled_max_households, 1_000_000)
        self.assertGreaterEqual(DEFAULTS.coupled_max_steps, 3_650)
        self.assertGreaterEqual(DEFAULTS.field_max_cells, 10_000_000)
        self.assertGreaterEqual(DEFAULTS.field_max_edges, 40_000_000)
        self.assertGreaterEqual(DEFAULTS.exposure_max_entities, 1_000_000)
        self.assertGreaterEqual(DEFAULTS.exposure_max_obligations, 10_000_000)
        self.assertGreaterEqual(DEFAULTS.transport_max_edges, 10_000_000)
        rows = describe_limits()
        self.assertEqual(len(rows), len({row['name'] for row in rows}))
        self.assertTrue(all(row['doc'] and row['default'] == row['effective'] for row in rows))

    def test_resolution_order_env_context_and_call(self):
        with patch.dict(os.environ, {ENV_VAR: json.dumps({'coupled_max_firms': 10, 'coupled_max_steps': 5})}):
            self.assertEqual((current_limits().coupled_max_firms, current_limits().coupled_max_steps), (10, 5))
            with use_limits({'coupled_max_firms': 20}):
                self.assertEqual((current_limits().coupled_max_firms, current_limits().coupled_max_steps), (20, 5))
                with use_limits(coupled_max_steps=7) as inner:
                    self.assertEqual((inner.coupled_max_firms, inner.coupled_max_steps), (20, 7))
                effective = resolve_limits({'coupled_max_firms': 30})
                self.assertEqual((effective.coupled_max_firms, effective.coupled_max_steps), (30, 5))
            self.assertEqual(current_limits().coupled_max_firms, 10)
        self.assertEqual(current_limits(), DEFAULTS)
        explicit = Limits(coupled_max_firms=3)
        self.assertIs(resolve_limits(explicit), explicit)

    def test_environment_file_and_parse_forms(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'limits.json'
            path.write_text('{"exposure_max_entities": 12}')
            for value in ('@' + str(path), str(path)):
                with patch.dict(os.environ, {ENV_VAR: value}):
                    limits_module._env_cache.clear()
                    self.assertEqual(current_limits().exposure_max_entities, 12)
            self.assertEqual(parse_limits('{"field_max_cells": 4}'), {'field_max_cells': 4})
            self.assertEqual(parse_limits(None), {})
        for bad in ('[1]', '{"nope": 1}', '{"field_max_cells": 0}', '{"field_max_cells": 1.5}', '{"field_max_cells": true}'):
            with self.assertRaises(ValueError):
                DEFAULTS.override(parse_limits(bad))
        with patch.dict(os.environ, {ENV_VAR: '{"nope": 1}'}):
            limits_module._env_cache.clear()
            with self.assertRaisesRegex(ValueError, ENV_VAR):
                current_limits()

    def test_limit_exceeded_names_limit_and_how_to_raise(self):
        limits = DEFAULTS.override(banking_max_banks=2)
        with self.assertRaises(LimitExceeded) as caught:
            limits.check('banking_max_banks', 3, 'Bank count')
        error = caught.exception
        self.assertIsInstance(error, ValueError)
        self.assertEqual((error.limit, error.value, error.maximum), ('banking_max_banks', 3, 2))
        for fragment in ('Bank count', 'banking_max_banks=2', 'limits=', ENV_VAR, '--limits'):
            self.assertIn(fragment, str(error))
        self.assertEqual(limits.check('banking_max_banks', 2), 2)
        with self.assertRaisesRegex(ValueError, 'integer'):
            limits.integer('banking_max_banks', 0)
        with self.assertRaises(LimitExceeded):
            limits.integer('banking_max_banks', 3)

    def test_backend_resolution(self):
        self.assertEqual(resolve_backend('python', size=10 ** 9), 'python')
        self.assertEqual(resolve_backend('auto', size=1), 'python')
        with self.assertRaises(ValueError):
            resolve_backend('gpu')
        if numpy_available():
            self.assertEqual(resolve_backend('auto', size=10 ** 7), 'numpy')
            self.assertEqual(resolve_backend('numpy'), 'numpy')
        with patch.dict(os.environ, {'WORLD_MODEL_BACKEND': 'python'}):
            self.assertEqual(resolve_backend(None, size=10 ** 9), 'python')

    def test_cli_limits_option_lowers_and_raises_kernel_limits(self):
        from worldmodel import cli
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            request = root / 'banking.json'
            request.write_text(json.dumps({'banks': [{'id': 'a', 'reserves': 10, 'equity': 10, 'accounts': {}, 'loans': {}}],
                                           'transactions': [{'kind': 'originate', 'bank': 'a', 'borrower': 'x', 'amount': 1}] * 3}))
            base = ['--data-root', str(root / 'data'), '--catalog-root', str(root / 'catalog'), 'banking', '--request', str(request)]
            args = cli.parser().parse_args(base + ['--limits', '{"banking_max_transactions": 2}'])
            with self.assertRaisesRegex(LimitExceeded, 'banking_max_transactions=2'):
                cli.execute(args)
            result = cli.execute(cli.parser().parse_args(base + ['--limits', '{"banking_max_transactions": 3}']))
            self.assertTrue(result['accounting']['balanced'])
            self.assertEqual(current_limits(), DEFAULTS)


if __name__ == '__main__':
    unittest.main()
