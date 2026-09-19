import unittest

from worldmodel.estimation.acceptance import evaluate_criterion


class CalibrationMeasureTests(unittest.TestCase):
    report = {'test': {'metrics': {'model': {'calibration': {'max_abs_deviation': 0.4, 'expected_calibration_error': 0.01}}}}}

    def test_default_measure_is_the_worst_bin(self):
        result = evaluate_criterion(self.report, {'id': 'c', 'type': 'calibration_error', 'maximum': 0.05})
        self.assertFalse(result['passed'])
        self.assertEqual(result['observed'], 0.4)

    def test_expected_calibration_error_can_be_declared(self):
        result = evaluate_criterion(self.report, {'id': 'c', 'type': 'calibration_error', 'maximum': 0.02,
                                                  'measure': 'expected_calibration_error'})
        self.assertTrue(result['passed'])
        self.assertEqual(result['threshold']['measure'], 'expected_calibration_error')

    def test_unknown_measure_fails_closed(self):
        result = evaluate_criterion(self.report, {'id': 'c', 'type': 'calibration_error', 'maximum': 1.0, 'measure': 'nope'})
        self.assertFalse(result['passed'])


if __name__ == '__main__':
    unittest.main()
