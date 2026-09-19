import unittest

try:
    import numpy as np
    import torch
except ImportError:  # the optional ``embed`` extra is not installed
    np = torch = None


def synthetic_panel(counties=12, years=range(1990, 2006), seed=0):
    """Counties in two states on a small grid; employment grows at a county-specific rate."""
    from worldmodel.embedding.tensors import Panel
    rng = np.random.default_rng(seed)
    values, available, units = {}, {}, {}
    for c in range(counties):
        county = f'geo:US:county:{"01" if c < counties // 2 else "02"}{c + 1:03d}'
        rate = 0.01 + 0.02 * (c % 3)
        for feature, lag in (('geography:latitude', None), ('geography:longitude', None)):
            values[(county, feature, 1980)] = 30.0 + c * 0.3 if feature.endswith('latitude') else -90.0 + (c % 4)
            available[(county, feature, 1980)] = '1980-01-01'
        for year in years:
            level = 1000 * (1 + rate) ** (year - 1990) * (1 + 0.01 * rng.standard_normal())
            values[(county, 'qcew:employment', year)] = level
            available[(county, 'qcew:employment', year)] = f'{year + 1}-09-30'
            values[(county, 'bea:population', year)] = 3 * level
            available[(county, 'bea:population', year)] = f'{year + 1}-12-31'
    units.update({'qcew:employment': 'persons', 'bea:population': 'persons', 'geography:latitude': 'degrees',
                  'geography:longitude': 'degrees'})
    edges = [('geo:US:county:01001', 'migration_flow', 'geo:US:county:01002', 10.0, '1995-01-01', '1998-06-30')]
    return Panel(values, available, units, edges, history=5)


@unittest.skipIf(torch is None, 'optional embed extra (numpy, torch) not installed')
class SnapshotTests(unittest.TestCase):
    def test_values_not_public_by_the_origin_are_masked(self):
        panel = synthetic_panel()
        panel.fit_standardization(1998)
        snap = panel.snapshot(2000)
        j = panel.feature_index['qcew:employment']
        # 2000 is public 2001-09-30: slot 0 is empty; 1999 is public 2000-09-30: slot 1 is filled.
        self.assertEqual(snap.m[0, j, 0], 0.0)
        self.assertEqual(snap.m[0, j, 1], 1.0)
        self.assertLessEqual(snap.max_available, snap.origin)

    def test_state_and_nation_aggregate_public_county_values(self):
        panel = synthetic_panel()
        snap = panel.snapshot(2000)
        j = panel.feature_index['qcew:employment']
        nation = panel.index['geo:US']
        state = panel.index['geo:US:state:01']
        self.assertEqual(snap.m[nation, j, 1], 1.0)
        self.assertEqual(snap.m[state, j, 0], 0.0)

    def test_migration_edges_appear_only_once_public(self):
        panel = synthetic_panel()
        from worldmodel.embedding.tensors import RELATIONS
        before, after = panel.snapshot(1997), panel.snapshot(1998)
        relation = RELATIONS.index('migration_out')
        self.assertFalse((before.edges[:, 2] == relation).any())
        self.assertTrue((after.edges[:, 2] == relation).any())

    def test_subgraph_starts_with_seed_then_state_and_nation(self):
        panel = synthetic_panel()
        snap = panel.snapshot(2000)
        nodes = panel.subgraph(snap, 0, max_nodes=10)
        self.assertEqual(nodes[:3], [0, panel.index['geo:US:state:01'], panel.index['geo:US']])
        self.assertLessEqual(len(nodes), 10)
        self.assertEqual(len(set(nodes)), len(nodes))

    def test_anchor_is_the_latest_public_value(self):
        panel = synthetic_panel()
        value, year = panel.anchor(0, 'qcew:employment', 2000)
        self.assertEqual(year, 1999)
        self.assertAlmostEqual(value, panel.target_values(0, 'qcew:employment', 1999)[0])


@unittest.skipIf(torch is None, 'optional embed extra (numpy, torch) not installed')
class EncoderTests(unittest.TestCase):
    def test_forward_shapes_and_sparse_readout(self):
        from worldmodel.embedding.model import WorldStateEncoder
        from worldmodel.embedding.tensors import NODE_TYPES, RELATIONS
        torch.manual_seed(0)
        model = WorldStateEncoder(4, len(NODE_TYPES), len(RELATIONS), 5, d=16, layers=2, slots=4, passes=3, top_k=2,
                                  out_dim=32, n_targets=2)
        B, N = 3, 6
        batch = {'x': torch.randn(B, N, 4, 5), 'm': torch.ones(B, N, 4, 5), 'node_type': torch.zeros(B, N, dtype=torch.long),
                 'valid': torch.ones(B, N, dtype=torch.bool), 'src': torch.tensor([0, 1, 6]),
                 'dst': torch.tensor([1, 0, 7]), 'rel': torch.tensor([4, 4, 4]), 'weight': torch.ones(3)}
        encoded = model.encode(batch)
        self.assertEqual(tuple(encoded['state'].shape), (B, 32))
        self.assertEqual(tuple(encoded['slots'].shape), (B, 4, 16))
        mean, scale, df = model.predict(encoded)
        self.assertEqual(tuple(mean.shape), (B, 2))
        self.assertTrue(bool((scale > 0).all()) and bool((df > 2).all()))
        # Each pass reads at most top_k nodes per slot: attention mass sits on <= passes*slots*top_k nodes.
        self.assertTrue(torch.allclose(encoded['attended'].sum(-1), torch.ones(B), atol=1e-4))
        mask = torch.zeros(B, N, 4, dtype=torch.bool)
        mask[0, 1, 2] = True
        index, predicted = model.reconstruct(model.encode(batch, token_mask=mask), mask)
        self.assertEqual(tuple(predicted.shape), (1, 5))

    def test_masked_tokens_hide_their_values(self):
        from worldmodel.embedding.model import TokenEncoder
        encoder = TokenEncoder(2, 3, 8)
        x = torch.randn(1, 1, 2, 3)
        m = torch.ones(1, 1, 2, 3)
        mask = torch.tensor([[[True, False]]])
        a = encoder(x, m, mask)[0, 0, 0]
        b = encoder(x * 5, m, mask)[0, 0, 0]
        self.assertTrue(torch.allclose(a, b))

    def test_student_t_nll_matches_torch_distribution(self):
        from worldmodel.embedding.model import student_t_nll
        y, mean, scale, df = torch.tensor([0.3]), torch.tensor([0.1]), torch.tensor([0.5]), torch.tensor([4.0])
        expected = -torch.distributions.StudentT(df, mean, scale).log_prob(y)
        self.assertTrue(torch.allclose(student_t_nll(y, mean, scale, df), expected, atol=1e-5))


@unittest.skipIf(torch is None, 'optional embed extra (numpy, torch) not installed')
class FitTests(unittest.TestCase):
    def test_fit_uses_only_public_labels_and_calibrates_scale(self):
        from worldmodel.embedding.train import SubgraphCache, build_samples, fit, label_public
        panel = synthetic_panel()
        panel.fit_standardization(1998)
        origins = list(range(1992, 2004))
        snapshots = [panel.snapshot(t) for t in origins]
        rows = {t: i for i, t in enumerate(origins)}
        targets = ['qcew:employment', 'bea:population']
        samples = build_samples(panel, rows, origins, targets)
        public = label_public(panel, samples, targets, 2002)
        self.assertTrue((samples.label_year[public.any(axis=1)] <= 2001).all())
        x_all = torch.as_tensor(np.stack([s.x for s in snapshots]))
        m_all = torch.as_tensor(np.stack([s.m for s in snapshots]))
        cache = SubgraphCache(panel, snapshots, 8)
        config = {'d': 16, 'layers': 1, 'slots': 2, 'passes': 2, 'top_k': 2, 'out_dim': 16, 'epochs': 2, 'batch': 16,
                  'max_nodes': 8}
        forecaster, batcher = fit(panel, snapshots, samples, public, targets, config=config, x_all=x_all, m_all=m_all,
                                  cache=cache)
        self.assertEqual(forecaster.diagnostics['calibration_label_years'], [2000, 2001])
        evaluate = np.flatnonzero(samples.origin == 2002)
        pred = forecaster.predict(batcher, samples.snap[evaluate], samples.seed[evaluate])
        self.assertEqual(pred['mean'].shape, (len(evaluate), 2))
        self.assertTrue(np.isfinite(pred['scale']).all())


if __name__ == '__main__':
    unittest.main()
