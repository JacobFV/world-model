import unittest
from worldmodel.economy_seeding import seed_economy

class SeedingTests(unittest.TestCase):
    def test_observed_rates_and_prices_seed_explicit_units_and_preserve_evidence(self):
        rows=[{'kind':'observation','id':'r:rate','metric':'policy_rate','value':5,'unit':'percent',
               'valid_from':'2025-03-28','observed_at':'2026-09-15'},
              {'kind':'observation','id':'r:oil','metric':'oil_price','value':70,'unit':'USD/barrel',
               'valid_from':'2025-03-28','observed_at':'2026-09-15'}]
        result=seed_economy({'bank':{'annual_rate':.01},'energy_price':1},rows,'2025-03-31','2026-09-16')
        self.assertEqual(result['config']['bank']['annual_rate'],.05)
        self.assertEqual(result['config']['energy_price'],70)
        self.assertEqual(result['seeds'][0]['record_id'],'r:rate')
        with self.assertRaisesRegex(ValueError,'available'):
            seed_economy({},rows,'2025-04-30','2026-09-16')
        with self.assertRaisesRegex(ValueError,'available'):
            seed_economy({},rows,'2025-03-31','2025-03-31')

    def test_energy_quantities_cannot_silently_change_units(self):
        with self.assertRaisesRegex(ValueError,'energy_unit'):
            seed_economy({'energy_unit':'kWh'},[],'2025-03-31','2026-09-16')
