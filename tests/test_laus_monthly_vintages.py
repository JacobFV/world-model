"""fred_county_laus_monthly_vintages under the project test run (fixtures only, no network).

The tests live beside the declaration, as for every dataset; this module exposes them to
``python3 -m unittest discover -s tests``.
"""
import importlib.util
from pathlib import Path
import unittest

_PATH = (Path(__file__).resolve().parents[1] / 'data' / 'fred_county_laus_monthly_vintages'
         / 'tests' / 'test_pipeline.py')
_SPEC = importlib.util.spec_from_file_location('fred_county_laus_monthly_vintages_tests', _PATH)
_MODULE = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MODULE)


class LausMonthlyVintagesEmissionTests(_MODULE.EmissionTests):
    pass


class LausMonthlyVintagesConfigTests(_MODULE.ConfigTests):
    pass


if __name__ == '__main__':
    unittest.main()
