"""Grounded cognitive agents: the affect geometry, grounding, and the person agent.

Three layers, deliberately separated:

* ``AffectGeometryTests`` needs nothing optional at all - ``worldmodel.agents.affect`` is pure
  Python - so it runs in every environment and pins the readings and the motif asymmetry.
* the rest skip unless the optional ``agents`` extra (tensorcode) is installed, the same way the
  numpy tests skip on the ``fast`` extra.
* ``RealLegislatorSmokeTests`` additionally skips unless this checkout has the unified-graph
  index on disk. It is a smoke test against real published records, not a fixture.
"""
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path

from worldmodel.agents import INSTALL_HINT, tensorcode_available
from worldmodel.agents import affect as af
from worldmodel.graph import Graph

UTC = dt.timezone.utc
HAVE_TC = tensorcode_available()
REF = {'dataset': 'fixture_congress', 'stage': 'normalized', 'version': 'c' * 64}
EVIDENCE = [{'input': {'dataset': 'fixture_congress', 'artifact': 'a' * 64}, 'locator': 'line:1'}]
DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
REAL_INDEX = DATA_ROOT / 'world_evidence' / 'index.sqlite'
REAL_LEGISLATOR = 'bioguide:K000367'


# --------------------------------------------------------------------------- pure affect


class AffectGeometryTests(unittest.TestCase):
    """The seven readings and the motif geometry, with no optional dependency in sight."""

    def test_readings_are_the_seven_declared_measures(self):
        self.assertEqual(af.READINGS, ('valence', 'arousal', 'integration', 'effective_rank',
                                       'counterfactual', 'self_attention', 'self_causal'))
        for name, prototype in af.PROTOTYPES.items():
            self.assertEqual(len(prototype), len(af.READINGS), name)

    def test_motif_is_the_nearest_prototype(self):
        for name, prototype in af.PROTOTYPES.items():
            reading = dict(zip(af.READINGS, prototype))
            self.assertEqual(af.nearest_motif(reading, name)[0], name)

    def test_transition_costs_are_asymmetric(self):
        self.assertLess(af.transition_cost('fear', 'anger'), af.transition_cost('anger', 'fear'))
        self.assertLess(af.transition_cost('attachment', 'grief'), af.transition_cost('grief', 'attachment'))
        self.assertEqual(af.transition_cost('anger', 'anger'), 0.0)

    def test_the_cheap_direction_makes_a_motif_an_attractor(self):
        """Anger is one-way: a reading 60% of the way across lands there from either side.

        Starting in fear, a reading 60% toward anger becomes anger, because fear slides into
        anger cheaply. Starting in *anger*, a reading 60% toward fear stays anger, because the
        return trip is dear. Without the asymmetric cost the second case would flip to fear.
        """
        def blend(a, b, t):
            return {key: af.PROTOTYPES[a][i] * (1 - t) + af.PROTOTYPES[b][i] * t
                    for i, key in enumerate(af.READINGS)}

        self.assertEqual(af.nearest_motif(blend('fear', 'anger', 0.6), 'fear')[0], 'anger')
        self.assertEqual(af.nearest_motif(blend('anger', 'fear', 0.6), 'anger')[0], 'anger')
        # The same reading with the costs switched off would have gone back to fear.
        unbiased = min(af.PROTOTYPES, key=lambda name: sum(
            (a - b) ** 2 for a, b in zip(blend('anger', 'fear', 0.6).values(), af.PROTOTYPES[name])))
        self.assertEqual(unbiased, 'fear')

    def test_soft_viability_tracks_the_tightest_margin(self):
        comfortable = af.soft_viability({'a': 0.8, 'b': 0.9})
        pinched = af.soft_viability({'a': 0.8, 'b': -0.5})
        self.assertGreater(comfortable, pinched)
        self.assertLess(pinched, 0.0)
        self.assertEqual(af.soft_viability({}), 0.0)

    def test_effective_rank_is_one_concern_versus_many(self):
        self.assertLess(af.entropy_rank([1.0], 7), af.entropy_rank([1.0] * 7, 7))
        self.assertAlmostEqual(af.entropy_rank([1.0] * 7, 7), 1.0, places=6)
        self.assertEqual(af.entropy_rank([], 7), 0.0)

    def test_modules_are_declared_for_every_prototype_predicate(self):
        for predicate in ('appraises', 'wants', 'means', 'projects', 'motif'):
            self.assertNotEqual(af.module_of(predicate), 'other', predicate)
        self.assertEqual(af.module_of('no_such_predicate'), 'other')


class SubstrateAbsenceTests(unittest.TestCase):
    def test_the_install_hint_names_the_extra(self):
        self.assertIn('worldmodel-substrate[agents]', INSTALL_HINT)

    def test_importing_the_package_never_requires_the_substrate(self):
        import importlib
        self.assertTrue(importlib.import_module('worldmodel.agents'))
        self.assertTrue(importlib.import_module('worldmodel.agents.affect'))

    def test_unknown_attribute_is_an_attribute_error(self):
        import worldmodel.agents as agents
        with self.assertRaises(AttributeError):
            agents.NoSuchAgentKind


# --------------------------------------------------------------------------- fixture index


def entity(entity_id, label, entity_type, **attributes):
    return {'kind': 'entity', 'id': 'r:' + entity_id, 'entity_id': entity_id, 'entity_type': entity_type,
            'label': label, 'observed_at': '2025-01-01', 'evidence': EVIDENCE, 'attributes': attributes}


def assertion(key, subject, predicate, obj, **extra):
    return {'kind': 'assertion', 'id': 'r:' + key, 'subject': subject, 'predicate': predicate, 'object': obj,
            'observed_at': extra.pop('observed_at', '2025-01-01'), 'evidence': EVIDENCE, **extra}


def observation(key, subject, metric, value, **extra):
    return {'kind': 'observation', 'id': 'r:' + key, 'subject': subject, 'metric': metric, 'value': value,
            'unit': 'score', 'observed_at': '2025-01-01', 'evidence': EVIDENCE, **extra}


def fixture_records():
    """A small but complete legislator: a seat, two committees, four measures, money and a vote."""
    records = [
        entity('bioguide:X000001', 'Ada Fixture', 'person', birthday='1970-02-03', gender='F'),
        entity('congress:role:x1', 'Senator for ZZ', 'role', role_type='sen', jurisdiction_code='ZZ',
               source_term={'party': 'Independent', 'start': '2025-01-03', 'end': '2031-01-03', 'type': 'sen'}),
        entity('congress:committee:zz01', 'Fixture Committee', 'institution', chamber='Senate',
               committee_type='Standing'),
        entity('congress:committee:zz02', 'Other Committee', 'institution', chamber='Senate'),
        entity('congress:bill:119-s-1', 'S 1: Carried Act', 'law', origin_chamber='Senate',
               measure_status='Public Law No: 119-1', public_laws=[{'number': '119-1'}],
               latest_action={'date': '2025-03-01', 'text': 'Became Public Law No: 119-1.'}),
        entity('congress:bill:119-s-2', 'S 2: Stuck Act', 'law', origin_chamber='Senate',
               measure_status='not enacted as of source update',
               latest_action={'date': '2025-02-01', 'text': 'Read twice and referred to the Fixture Committee.'}),
        entity('congress:bill:119-s-3', 'S 3: Also Stuck', 'law', origin_chamber='Senate',
               measure_status='not enacted as of source update',
               latest_action={'date': '2025-02-10', 'text': 'Read twice and referred to the Other Committee.'}),
        entity('congress:bill:119-hr-9', 'HR 9: House Bill', 'law', origin_chamber='House',
               measure_status='not enacted as of source update',
               latest_action={'date': '2025-02-11', 'text': 'Read twice and referred to the Fixture Committee.'}),
        entity('fec:candidate:X1', 'FIXTURE, ADA', 'person'),
        entity('fec:committee:D1', 'Fixture Backers PAC', 'organization', committee_type='Q'),
        entity('fec:committee:D2', 'Second Backers PAC', 'organization', committee_type='Q'),
        entity('fec:committee:O1', 'Opposing PAC', 'organization', committee_type='Q'),
        entity('icpsr:99001', 'Ada Fixture', 'person'),
        assertion('same1', 'bioguide:X000001', 'same_as', 'fec:candidate:X1'),
        assertion('same2', 'bioguide:X000001', 'same_as', 'icpsr:99001'),
        assertion('role1', 'bioguide:X000001', 'holds_role', 'congress:role:x1',
                  valid_from='2025-01-03', valid_to='2031-01-03'),
        assertion('cm1', 'bioguide:X000001', 'committee_member', 'congress:committee:zz01'),
        assertion('cm2', 'bioguide:X000001', 'committee_member', 'congress:committee:zz02'),
        assertion('sp1', 'bioguide:X000001', 'sponsored_measure', 'congress:bill:119-s-1', valid_from='2025-01-10'),
        assertion('sp2', 'bioguide:X000001', 'sponsored_measure', 'congress:bill:119-s-2', valid_from='2025-01-20'),
        assertion('sp3', 'bioguide:X000001', 'sponsored_measure', 'congress:bill:119-s-3', valid_from='2025-02-05'),
        assertion('cs1', 'bioguide:X000001', 'cosponsored_measure', 'congress:bill:119-hr-9', valid_from='2025-02-06'),
        assertion('ref1', 'congress:bill:119-s-2', 'referred_to_committee', 'congress:committee:zz01',
                  valid_from='2025-02-01'),
        assertion('ref2', 'congress:bill:119-s-3', 'referred_to_committee', 'congress:committee:zz02',
                  valid_from='2025-02-10'),
        assertion('sup1', 'fec:committee:D1', 'supports_candidate', 'fec:candidate:X1',
                  valid_from='2025-01-01', valid_to='2027-01-01'),
        assertion('sup2', 'fec:committee:D2', 'supports_candidate', 'fec:candidate:X1',
                  valid_from='2025-01-05', valid_to='2027-01-01'),
        assertion('opp1', 'fec:committee:O1', 'opposes_candidate', 'fec:candidate:X1',
                  valid_from='2025-01-06', valid_to='2027-01-01'),
        assertion('acc1', 'fec:committee:D1', 'authorized_committee_of', 'fec:candidate:X1',
                  valid_from='2025-01-01', valid_to='2027-01-01'),
        assertion('pa1', 'icpsr:99001', 'party_affiliation', 'voteview:party:328',
                  valid_from='2025-01-03', valid_to='2027-01-03'),
        observation('dw1', 'icpsr:99001', 'dw_nominate_dim1', -0.5,
                    dimensions={'chamber': 'Senate', 'congress': 119},
                    valid_from='2025-01-03', valid_to='2027-01-03'),
        observation('src1', 'icpsr:99001', 'scaled_roll_call_votes', 3,
                    dimensions={'chamber': 'Senate', 'congress': 119},
                    valid_from='2025-01-03', valid_to='2027-01-03'),
    ]
    # Roll calls: events with no subject or object, reachable only by primary key. Ada is on the
    # losing side twice (nay on a motion that carried, yea on an amendment that fell) and the
    # winning side once, so her published coalition margin is negative.
    for number, position, result, question in ((1, 'nay', 'Cloture Motion Agreed to', 'On the Cloture Motion'),
                                               (2, 'yea', 'Amendment Rejected', 'On the Amendment'),
                                               (3, 'yea', 'Bill Passed', 'On Passage of the Bill')):
        positions = {'yea': [99001] if position == 'yea' else [10001],
                     'nay': [99001] if position == 'nay' else [10001], 'not_voting': []}
        day = '2025-02-%02d' % (10 + number)
        records.append({'kind': 'event', 'id': 'fixture_congress:positions:S119:%d' % number,
                        'event_type': 'roll_call_member_positions', 'observed_at': '2025-03-01',
                        'occurred_at': day, 'evidence': EVIDENCE, 'participants': ['us:congress:senate'],
                        'attributes': {'chamber': 'Senate', 'congress': 119, 'member_namespace': 'icpsr',
                                       'occurred_at': day, 'positions': positions}})
        records.append({'kind': 'event', 'id': 'fixture_congress:rollcall:S119:%d' % number,
                        'event_type': 'roll_call_vote', 'observed_at': '2025-03-01', 'occurred_at': day,
                        'evidence': EVIDENCE, 'participants': ['us:congress:senate'],
                        'attributes': {'chamber': 'Senate', 'congress': 119, 'rollnumber': number,
                                       'vote_question': question, 'vote_result': result, 'bill_number': 'S%d' % number}})
    return records


def build_fixture_index(path):
    graph = Graph(path)
    graph.build_from_records([(REF, fixture_records())])
    graph.attach_resolution([{'canonical_id': 'bioguide:X000001',
                              'members': ['bioguide:X000001', 'fec:candidate:X1', 'icpsr:99001']}],
                            view={'view_digest': 'd' * 64, 'policy': {'links': 'fixture'}})
    return graph


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
class GroundedAgentFixtureTests(unittest.TestCase):
    """Everything about grounding and the person loop, on a fixture small enough to reason about."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'index.sqlite'
        build_fixture_index(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        from worldmodel.agents.grounding import EvidenceIndex, LEGISLATOR
        # The fixture dataset is named for itself, so point the roll-call reader at it.
        self.horizon = LEGISLATOR
        self.index = EvidenceIndex(self.path, cache_mb=8)
        self.addCleanup(self.index.close)

    def use_fixture_rollcalls(self):
        """The roll-call reader addresses records by primary key, so point it at the fixture dataset."""
        import worldmodel.agents.grounding as grounding
        original = grounding.RollCalls.DATASET
        grounding.RollCalls.DATASET = 'fixture_congress'
        self.addCleanup(setattr, grounding.RollCalls, 'DATASET', original)

    def agent(self, **kwargs):
        from worldmodel.agents.person import Person
        self.use_fixture_rollcalls()
        kwargs.setdefault('seed', 5)
        return Person.ground(self.index, 'bioguide:X000001', horizon=self.horizon, **kwargs)

    # -- the index reader ------------------------------------------------------------------
    def test_index_is_read_only_and_schema_checked(self):
        import sqlite3
        self.assertEqual(self.index.schema_version, '3')
        with self.assertRaises(sqlite3.OperationalError):
            self.index._connection.execute('DELETE FROM records')

    def test_missing_index_says_what_to_run(self):
        from worldmodel.agents.grounding import EvidenceIndex
        with self.assertRaises(ValueError) as caught:
            EvidenceIndex(Path(self.tmp.name) / 'absent.sqlite')
        self.assertIn('unify', str(caught.exception))

    def test_cluster_puts_the_agents_own_id_first(self):
        cluster = self.index.cluster('fec:candidate:X1')
        self.assertEqual(cluster[0], 'fec:candidate:X1')
        self.assertEqual(set(cluster), {'bioguide:X000001', 'fec:candidate:X1', 'icpsr:99001'})

    def test_events_are_reachable_only_by_primary_key(self):
        self.assertIsNone(self.index.entity('fixture_congress:positions:S119:1'))
        record = self.index.record_by_id('fixture_congress', 'fixture_congress:positions:S119:1', stage='normalized')
        self.assertEqual(record['event_type'], 'roll_call_member_positions')
        self.assertEqual(record['_provenance']['version'], REF['version'])

    def test_advance_state_does_not_read_not_enacted_as_enacted(self):
        from worldmodel.agents.grounding import advance_state
        self.assertEqual(advance_state(self.index.entity('congress:bill:119-s-1')), 'enacted')
        self.assertEqual(advance_state(self.index.entity('congress:bill:119-s-2')), 'referred')
        self.assertIsNone(advance_state(None))

    # -- seeding --------------------------------------------------------------------------
    def test_seed_uses_published_records_only_and_reports_unknowns(self):
        import tensorcode as tc
        from worldmodel.agents.grounding import seed_store
        self.use_fixture_rollcalls()
        store = tc.Store()
        ground, report = seed_store(store, self.index, 'bioguide:X000001', horizon=self.horizon)
        self.assertEqual(report.label, 'Ada Fixture')
        self.assertEqual(report.canonical_id, 'bioguide:X000001')
        self.assertGreater(report.claims, 20)
        # Every claim in the store names a published record and its dataset version.
        for record in store.claims():
            evidence = record.evidence[0]
            self.assertTrue(str(evidence.source).startswith('dataset:'), record.claim)
            self.assertTrue(evidence.locator, record.claim)
        self.assertIn('contribution_amounts', report.unknown)
        self.assertIn('lobbying_contacts', report.unknown)
        self.assertNotIn('committees', report.unknown)
        self.assertEqual(report.datasets, ('fixture_congress',))
        # Absent history is Unknown with a reason, never a claim.
        unknown = ground.unknown('contribution_amounts')
        self.assertEqual(unknown.reason, 'no_published_record')
        self.assertIsNone(ground.unknown('committees'))
        self.assertEqual(ground.unknown('nonsense').reason, 'not_a_declared_facet')

    def test_the_graph_and_the_belief_are_separately_queryable(self):
        agent = self.agent()
        published = agent.grounding.graph_facts('committee_member')
        self.assertEqual({row['object'] for row in published},
                         {'congress:committee:zz01', 'congress:committee:zz02'})
        # A belief the record does not support shows up as divergence, and is not corrected.
        import tensorcode as tc
        agent.store.tell(tc.Claim(agent.me, 'serves_on', tc.Ref('congress:committee:zz99')),
                         tc.Evidence(tc.Ref('rumour:corridor'), dt.datetime(2025, 3, 1, tzinfo=UTC), method='heard'))
        diverging = agent.grounding.divergence(agent.store, 'serves_on')
        self.assertEqual([r.claim.object.id for r in diverging], ['congress:committee:zz99'])

    # -- the perception bound -------------------------------------------------------------
    def test_perception_is_capped_ranked_and_reports_its_own_bound(self):
        from worldmodel.agents.grounding import perceive, ground
        import dataclasses
        tight = dataclasses.replace(self.horizon, per_tick=6, roll_calls=False)
        grounding = ground(self.index, 'bioguide:X000001', horizon=tight)
        window = (dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC))
        seen = perceive(self.index, grounding, window=window)
        self.assertEqual(len(seen.percepts), 6)
        self.assertEqual(seen.bound['cap'], 6)
        self.assertGreater(seen.bound['dropped'], 0)
        self.assertEqual(seen.bound['generated'], len(seen.percepts) + seen.dropped)
        saliences = [p.salience for p in seen.percepts]
        self.assertEqual(saliences, sorted(saliences, reverse=True))

    def test_perception_never_leaves_the_role_horizon(self):
        from worldmodel.agents.grounding import perceive, ground
        grounding = ground(self.index, 'bioguide:X000001', horizon=self.horizon)
        window = (dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC))
        allowed = set(self.horizon.predicates()) | {m.metric for m in self.horizon.metrics} \
            | {'roll_call_member_positions'}
        self.assertLessEqual(set(perceive(self.index, grounding, window=window).predicates_seen), allowed)

    def test_perception_is_deterministic(self):
        from worldmodel.agents.grounding import perceive, ground
        grounding = ground(self.index, 'bioguide:X000001', horizon=self.horizon)
        window = (dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC))
        first = perceive(self.index, grounding, window=window)
        second = perceive(self.index, grounding, window=window)
        self.assertEqual([(p.predicate, str(p.object), p.record_id) for p in first.percepts],
                         [(p.predicate, str(p.object), p.record_id) for p in second.percepts])

    def test_a_window_with_nothing_in_it_still_gives_standing_condition(self):
        from worldmodel.agents.grounding import perceive, ground
        grounding = ground(self.index, 'bioguide:X000001', horizon=self.horizon)
        quiet = perceive(self.index, grounding,
                        window=(dt.datetime(2030, 1, 1, tzinfo=UTC), dt.datetime(2030, 2, 1, tzinfo=UTC)))
        self.assertEqual(quiet.events, ())
        self.assertTrue(any(p.predicate == 'serves_on' for p in quiet.percepts))

    def test_roll_calls_are_bounded_and_windowed(self):
        from worldmodel.agents.grounding import RollCalls
        self.use_fixture_rollcalls()
        source = RollCalls(self.index, scan=10)
        self.assertEqual(source.last_rollnumber('S', 119), 3)
        self.assertEqual(source.last_before('S', 119, dt.datetime(2025, 2, 13, tzinfo=UTC)), 2)
        votes = source.votes('S', 119, 99001)
        self.assertEqual([(number, position) for number, position, _p, _r in votes],
                         [(3, 'yea'), (2, 'yea'), (1, 'nay')])
        self.assertEqual(source.votes('S', 119, 10001)[0][1], 'nay')  # somebody else's positions

    def test_vote_outcome_reads_the_published_result(self):
        from worldmodel.agents.grounding import vote_outcome
        self.assertEqual(vote_outcome('yea', 'Bill Passed'), 'prevailed')
        self.assertEqual(vote_outcome('nay', 'Bill Passed'), 'rebuffed')
        self.assertEqual(vote_outcome('nay', 'Amendment Rejected'), 'prevailed')
        self.assertEqual(vote_outcome('yea', 'Amendment Rejected'), 'rebuffed')
        self.assertEqual(vote_outcome('not_voting', 'Bill Passed'), 'absent')
        self.assertIsNone(vote_outcome('yea', 'Motion entered'))

    # -- the person loop ------------------------------------------------------------------
    def test_a_tick_appraises_reads_affect_and_decides(self):
        agent = self.agent()
        report = agent.tick((dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC)))
        self.assertEqual(set(report.affect), set(af.READINGS))
        self.assertIn(report.motif, af.MOTIFS)
        self.assertEqual(agent.store.claims(agent.me, 'motif')[0].claim.object, report.motif)
        appraisals = {tuple(r.claim.object) for r in agent.store.claims(agent.me, 'appraises')}
        tags = {tag for tag, _about in appraisals}
        # A carried measure is pride; a referred one is pressure; a seat is standing; money is support.
        self.assertLessEqual({'pride', 'pressure', 'standing', 'support'}, tags)
        self.assertTrue(report.decision)
        self.assertIsNotNone(report.decision_id)
        self.assertGreater(report.episodes, 0)

    def test_every_appraisal_explains_down_to_a_published_record(self):
        agent = self.agent()
        agent.tick((dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC)))
        lines = agent.explain('appraises pride', depth=8)
        self.assertTrue(any('r:sp1' in line for line in lines), lines)
        self.assertTrue(any('rule:measure_carried' in line for line in lines), lines)
        # and the decision cites premises that are themselves explained
        why = agent.explain(agent.store.claim(agent.decision_id), depth=8)
        self.assertTrue(any('choose:expected_viability' in line for line in why))
        self.assertTrue(any('observed in' in line for line in why))

    def test_a_percept_that_falls_out_withdraws_what_rested_on_it(self):
        """Working memory is a snapshot: what is no longer perceived goes, and so does what it held up.

        Standing knowledge seeded from the record survives, because it was never a percept. That
        split is the point of putting perception in its own scope.
        """
        import tensorcode as tc
        from worldmodel.agents.person import PERCEPT
        agent = self.agent()
        agent.tick((dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC)))
        # A roll call is a dated percept: it belongs to the window it happened in and to no other.
        vote = next(r for r in agent.store.claims(scope=PERCEPT) if r.claim.predicate == 'vote_outcome')
        resting = tc.Claim(agent.me, 'projects', ('rests_only_on_a_percept',))
        agent.store.tell(resting, tc.Evidence(tc.Ref('rule:test'), dt.datetime(2025, 4, 1, tzinfo=UTC),
                                              method='derive@test', derived_from=(vote.id,)))
        standing = agent.store.claims(agent.me, 'serves_on')[0].id
        self.assertIn(resting.id, agent.store._claims)
        # A window in which nothing happened: the dated percepts drop out of the snapshot, and
        # what rested only on them is withdrawn and forgotten with them.
        agent.tick((dt.datetime(2030, 1, 1, tzinfo=UTC), dt.datetime(2030, 2, 1, tzinfo=UTC)))
        self.assertNotIn(vote.id, {r.id for r in agent.store.claims(scope=PERCEPT)})
        self.assertNotIn(resting.id, agent.store._claims)
        # Standing condition perceived every tick, and seeded knowledge, both survive.
        self.assertIn(standing, agent.store._claims)
        self.assertTrue(agent.store.claims(scope=PERCEPT))

    def test_episodes_decay_and_consolidate_into_a_standing_belief(self):
        from worldmodel.agents.person import Episode
        agent = self.agent()
        old = Episode(at=dt.datetime(2020, 1, 1, tzinfo=UTC), tag='vote', text='ancient', salience=1.0)
        fresh = Episode(at=dt.datetime(2025, 3, 1, tzinfo=UTC), tag='vote', text='recent', salience=1.0)
        now = dt.datetime(2025, 4, 1, tzinfo=UTC)
        self.assertLess(old.decay(now), fresh.decay(now))
        # Two donation episodes about one backer become a standing obligation with premises.
        agent.now = now
        for _ in range(2):
            agent.episodes.append(Episode(at=now, tag='donation', text='backed me', salience=0.8,
                                          who=('fec:committee:D1',)))
            agent.episodes[-1].claim_id = agent._tell_self(
                __import__('tensorcode').Claim(agent.me, 'recalls', ('donation', str(len(agent.episodes)))),
                [], 'recall')
        formed = agent._consolidate()
        self.assertIn('obliged_to fec:committee:D1', formed)
        held = agent.store.claims(agent.me, 'obliged_to')
        self.assertEqual([r.claim.object.id for r in held], ['fec:committee:D1'])
        self.assertTrue(held[0].evidence[0].derived_from)
        self.assertEqual(agent.relation('fec:committee:D1')['label'], 'backer')

    def test_theory_of_mind_only_for_targets_with_ascribed_agency(self):
        from worldmodel.agents.person import ascription_of
        agent = self.agent()
        agent.tick((dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC)))
        self.assertEqual(ascription_of('congress:bill:119-s-1'), (0.05, 0.0))
        self.assertEqual(ascription_of('fec:committee:D1')[1], 0.02)   # agentive, not experiential
        self.assertEqual(ascription_of('bioguide:X000002'), (0.9, 0.9))
        self.assertTrue(agent.tom)
        for target, model in agent.tom.items():
            self.assertGreaterEqual(model['agency'], 0.6)
            self.assertNotIn('congress:bill:', target)
            scope = __import__('tensorcode').Ref('model:%s' % target)
            self.assertTrue(agent.store.claims(scope=scope), target)
            # The stance follows the relation that brought the target into view, not whichever
            # incidental attribute percept happened to mention it first.
            from worldmodel.agents.person import STANCE
            if model['because'] in STANCE:
                self.assertEqual((model['wants'], model['believes']), STANCE[model['because']], target)
        # A backer is modelled as wanting access; the model cites the FEC record that says so.
        backer = agent.tom.get('fec:committee:D1') or agent.tom.get('fec:committee:D2')
        if backer:
            self.assertEqual(backer['wants'], 'access')
            self.assertEqual(backer['phenomenality'], 0.02)

    def test_unknown_is_recorded_as_an_honest_non_decision(self):
        import tensorcode as tc
        from worldmodel.agents.person import Intention
        agent = self.agent()
        agent.tick((dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC)))
        # No option survives the constraints: the agent must not invent one.
        agent.options = lambda: []
        decision, unknown = agent._choose()
        self.assertIsInstance(unknown, tc.Unknown)
        self.assertEqual(unknown.reason, 'no_feasible_option')
        self.assertEqual(agent.store.claims(agent.me, 'decided')[0].claim.object, ('unknown', 'no_feasible_option'))
        # Two options the agent cannot tell apart: also Unknown, not a coin flip.
        premise = agent.store.claims(agent.me, 'serves_on')[0].id
        agent.options = lambda: [Intention('constituency_service', '', 0.5, (premise,), 'a'),
                                 Intention('fundraise', '', 0.5, (premise,), 'b')]
        _decision, tie = agent._choose()
        self.assertEqual(tie.reason, 'tie_within_margin')

    def test_constraints_refuse_rather_than_discount(self):
        from worldmodel.agents.person import Intention
        agent = self.agent()
        agent.tick((dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC)))
        premise = agent.store.claims(agent.me, 'serves_on')[0].id
        checks = {c.name: c for c in agent.constraints()}
        # Opposing an agentive-but-not-experiential committee is allowed; a person is not.
        self.assertTrue(checks['harm_forbidden_by_ascription'].test(
            Intention('counter_opponent', 'fec:committee:O1', 1.0, (premise,), ''), None))
        self.assertFalse(checks['harm_forbidden_by_ascription'].test(
            Intention('counter_opponent', 'bioguide:X000002', 1.0, (premise,), ''), None))
        # A House measure is outside a senator's chamber; its own committee is inside its authority.
        self.assertFalse(checks['within_role_authority'].test(
            Intention('advance_measure', 'congress:bill:119-hr-9', 1.0, (premise,), ''), None))
        self.assertTrue(checks['within_role_authority'].test(
            Intention('advance_measure', 'congress:bill:119-s-2', 1.0, (premise,), ''), None))
        self.assertFalse(checks['within_role_authority'].test(
            Intention('committee_work', 'congress:committee:zz99', 1.0, (premise,), ''), None))
        # An intention resting on no live premise is not choosable at all.
        self.assertFalse(checks['grounded_in_a_live_premise'].test(
            Intention('fundraise', '', 1.0, (), ''), None))

    def test_coupling_gates_the_rules_that_cross_modules(self):
        low = self.agent(coupling=0.1)
        high = self.agent(coupling=0.9)
        window = (dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC))
        low_report, high_report = low.tick(window), high.tick(window)
        self.assertEqual(len(low.store.claims(low.me, 'wants')), 0)
        self.assertGreater(len(high.store.claims(high.me, 'wants')), 0)
        self.assertLess(low_report.affect['integration'], high_report.affect['integration'])

    def test_the_same_seed_gives_the_same_agent(self):
        window = (dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC))
        first, second = self.agent(seed=11), self.agent(seed=11)
        other = self.agent(seed=12)
        self.assertEqual((first.coupling, first.gain), (second.coupling, second.gain))
        self.assertNotEqual((first.coupling, first.gain), (other.coupling, other.gain))
        a, b = first.tick(window), second.tick(window)
        self.assertEqual((a.motif, a.decision, a.affect), (b.motif, b.decision, b.affect))

    def test_viability_is_read_from_the_agents_own_beliefs(self):
        agent = self.agent()
        agent.tick((dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC)))
        margins = agent.viability_margins()
        self.assertEqual(set(margins), {'office', 'standing', 'efficacy', 'coalition', 'support'})
        self.assertGreater(margins['office'], 0)       # the record publishes a live term
        self.assertLess(margins['coalition'], 0)       # two votes lost against one won
        for value in margins.values():
            self.assertTrue(-1.0 <= value <= 1.0, margins)

    def test_inspect_reports_claims_affect_motif_episodes_and_intentions(self):
        agent = self.agent()
        agent.tick((dt.datetime(2025, 1, 1, tzinfo=UTC), dt.datetime(2025, 4, 1, tzinfo=UTC)))
        view = agent.inspect(claims=400)
        for key in ('entity_id', 'label', 'cluster', 'affect', 'motif', 'viability', 'appraisals',
                    'intentions', 'decision', 'why', 'episodes', 'theory_of_mind', 'unknown_facets', 'beliefs'):
            self.assertIn(key, view)
        self.assertEqual(view['label'], 'Ada Fixture')
        self.assertIn('contribution_amounts', view['unknown_facets'])
        # Everything the agent did not derive for itself names the published record behind it.
        observed = [b for b in view['beliefs'] if not b['derived']
                    and str(b['source']).startswith(('dataset:', 'obs:'))]
        self.assertTrue(observed)
        self.assertTrue(all(b['record_id'] for b in observed), observed)
        self.assertTrue(any(str(b['source']).startswith('dataset:') for b in view['beliefs']))
        json.dumps(view)  # the CLI prints this


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
class AgentCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'index.sqlite'
        build_fixture_index(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cli(self, argv):
        from worldmodel.cli import execute, parser
        import worldmodel.agents.grounding as grounding
        original = grounding.RollCalls.DATASET
        grounding.RollCalls.DATASET = 'fixture_congress'
        self.addCleanup(setattr, grounding.RollCalls, 'DATASET', original)
        args = parser().parse_args(argv + ['--index', str(self.path)])
        return execute(args)

    def test_agent_inspect_and_explain_are_registered_and_scriptable(self):
        view = self.run_cli(['agent-inspect', 'bioguide:X000001', '--ticks', '1', '--until', '2025-04-01'])
        self.assertEqual(view['label'], 'Ada Fixture')
        self.assertEqual(len(view['ticks']), 1)
        self.assertEqual(view['ticks'][0]['perception']['cap'], 24)
        json.dumps(view)

    def test_agent_explain_reaches_the_published_record_ids(self):
        result = self.run_cli(['agent-explain', 'bioguide:X000001', 'appraises pride',
                               '--ticks', '1', '--until', '2025-04-01'])
        self.assertTrue(result['matches'])
        match = result['matches'][0]
        self.assertTrue(any('rule:measure_carried' in line for line in match['why']))
        self.assertIn('r:sp1', {entry['record_id'] for entry in match['records']})
        for entry in match['records']:
            self.assertTrue(entry['dataset_version'].startswith('dataset:fixture_congress/'))

    def test_agent_explain_on_nothing_says_what_to_run_instead(self):
        with self.assertRaises(ValueError) as caught:
            self.run_cli(['agent-explain', 'bioguide:X000001', 'no_such_predicate'])
        self.assertIn('agent-inspect', str(caught.exception))

    def test_divergence_is_reported(self):
        view = self.run_cli(['agent-inspect', 'bioguide:X000001', '--diverge', 'serves_on'])
        self.assertEqual(view['divergence'], [])


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
@unittest.skipUnless(REAL_INDEX.exists(), 'no local unified-graph index; skipping the real-data smoke test')
class RealLegislatorSmokeTests(unittest.TestCase):
    """A real legislator from the real index: it must ground, tick and explain itself."""

    @classmethod
    def setUpClass(cls):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.person import Person
        cls.index = EvidenceIndex(REAL_INDEX, cache_mb=64)
        cls.agent = Person.ground(cls.index, REAL_LEGISLATOR, seed=7)
        cls.report = cls.agent.tick((dt.datetime(2026, 1, 1, tzinfo=UTC), dt.datetime(2026, 4, 1, tzinfo=UTC)))

    @classmethod
    def tearDownClass(cls):
        cls.index.close()

    def test_the_cluster_joins_the_published_identifiers(self):
        cluster = set(self.agent.grounding.cluster)
        self.assertIn(REAL_LEGISLATOR, cluster)
        self.assertTrue(any(member.startswith('icpsr:') for member in cluster), cluster)
        self.assertTrue(any(member.startswith('fec:candidate:') for member in cluster), cluster)

    def test_seeded_only_from_published_records(self):
        self.assertGreater(self.agent.report.claims, 100)
        self.assertTrue(self.agent.grounding.label)
        self.assertLessEqual({'congress_people', 'govinfo_billstatus'}, set(self.agent.report.datasets))
        # Every claim's first evidence is either a published dataset version, the percept frame
        # that read one (whose locator names the record and its version), or one of the agent's
        # own declared machines. Nothing comes from anywhere else.
        allowed = ('dataset:', 'obs:', 'rule:', 'reading:', 'memory:', 'choose:', 'consolidation:',
                   'ascription:', 'recall:')
        for record in self.agent.store.claims():
            evidence = record.evidence[0]
            self.assertTrue(str(evidence.source).startswith(allowed), (record.claim, evidence.source))
            if str(evidence.source).startswith('obs:'):
                self.assertIn(' in dataset:', evidence.locator or '', record.claim)

    def test_contribution_amounts_stay_unknown_on_this_catalog(self):
        # fec publishes supports_candidate as a per-cycle boolean and the individual-contribution
        # datasets contributed no records, so the amount is Unknown and says why.
        self.assertIn('contribution_amounts', self.agent.report.unknown)
        unknown = self.agent.grounding.unknown('contribution_amounts')
        self.assertEqual(unknown.reason, 'no_published_record')
        self.assertEqual(self.agent.store.claims(self.agent.me, 'contribution_amount'), [])

    def test_a_real_tick_stays_inside_its_perception_bound(self):
        bound = self.report.perception.bound
        self.assertEqual(bound['kept'], bound['cap'])
        self.assertGreater(bound['dropped'], 0)
        self.assertIn('cosponsored_measure', bound['predicates'])
        self.assertIn('roll_call_member_positions', bound['predicates'])

    def test_a_real_tick_reads_affect_and_names_a_motif(self):
        self.assertIn(self.report.motif, af.MOTIFS)
        self.assertEqual(set(self.report.affect), set(af.READINGS))
        self.assertTrue(-1.0 <= self.report.affect['valence'] <= 1.0)
        self.assertTrue(0.0 <= self.report.affect['arousal'] <= 1.0)

    def test_a_real_decision_explains_down_to_published_record_ids(self):
        from worldmodel.agents_cli import _records_behind
        self.assertTrue(self.report.decision)
        record = self.agent.store.claim(self.agent.decision_id)
        lines = self.agent.explain(record, depth=8)
        self.assertTrue(any('observed in' in line for line in lines))
        behind = _records_behind(record, self.agent)
        self.assertTrue(behind)
        for entry in behind:
            self.assertTrue(entry['dataset_version'].startswith('dataset:'), entry)
            self.assertTrue(entry['record_id'])

    def test_real_ticks_are_reproducible(self):
        from worldmodel.agents.person import Person
        window = (dt.datetime(2026, 1, 1, tzinfo=UTC), dt.datetime(2026, 4, 1, tzinfo=UTC))
        twin = Person.ground(self.index, REAL_LEGISLATOR, seed=7)
        again = twin.tick(window)
        self.assertEqual((again.motif, again.decision), (self.report.motif, self.report.decision))
        self.assertEqual(again.affect, self.report.affect)


if __name__ == '__main__':
    unittest.main()
