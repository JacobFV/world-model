"""A society of grounded agents, and the observatory that measures what it does.

Four layers, deliberately separated:

* ``PureObservatoryTests`` needs nothing optional - the statistics helpers in
  ``worldmodel.agents.observatory`` are plain Python and that module imports without the substrate,
  exactly like ``agents.affect`` - so they run everywhere.
* ``SocietyFixtureTests``, ``ObservatoryFixtureTests`` and ``SocietyCliTests`` skip unless the
  optional ``agents`` extra (tensorcode) is installed, and run over a fixture index small enough to
  reason about: two legislators who share a committee, that committee, and a firm with declared
  financials.
* ``RealSocietySmokeTests`` additionally skips unless this checkout has the unified-graph index on
  disk. It binds real legislators who share a real committee and a real issuer.

Every import that needs the substrate is inside a test body or a ``setUpClass``, so a checkout
without the extra **skips** rather than erroring at collection time. Nothing at module level
touches anything but ``worldmodel.agents`` (which is always importable) and ``worldmodel.graph``.

Nothing here is a validation. The society is not fitted to anything and the observatory measures
the simulation's own behaviour.
"""
import copy
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from worldmodel.agents import tensorcode_available
from worldmodel.graph import Graph

HAVE_TC = tensorcode_available()
NEEDS_TC = 'a society of grounded agents requires the optional agents extra (tensorcode)'
DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
REAL_INDEX = DATA_ROOT / 'world_evidence' / 'index.sqlite'
EXAMPLE = Path(__file__).resolve().parents[1] / 'examples' / 'society-congress.json'
UTC = dt.timezone.utc
REF = {'dataset': 'fixture_society', 'stage': 'normalized', 'version': 'e' * 64}
EVIDENCE = [{'input': {'dataset': 'fixture_society', 'artifact': 'a' * 64}, 'locator': 'line:1'}]

ADA = 'bioguide:X000001'
BEN = 'bioguide:X000002'
COMMITTEE = 'congress:committee:zz01'
OTHER_COMMITTEE = 'congress:committee:zz02'
FIRM = 'sec:cik:0000000042'
SUB = 'sec:cik:0000000043'
SHARED_BILL = 'congress:bill:119-s-2'
OUTSIDE_BILL = 'congress:bill:119-s-3'


# --------------------------------------------------------------------------- pure statistics


class PureObservatoryTests(unittest.TestCase):
    """The statistics helpers need no substrate at all."""

    def setUp(self):
        from worldmodel.agents import observatory as obs
        self.obs = obs

    def test_quantiles_interpolate_and_bound(self):
        values = [0.0, 0.25, 0.5, 0.75, 1.0]
        points = self.obs.quantiles(values)
        self.assertEqual(points['p0'], 0.0)
        self.assertEqual(points['p100'], 1.0)
        self.assertEqual(points['p50'], 0.5)
        self.assertEqual(self.obs.quantiles([]), {})

    def test_a_distribution_is_reported_not_a_point_estimate(self):
        summary = self.obs.distribution([0.1, 0.2, 0.3, 0.4], label='x')
        for key in ('count', 'mean', 'sd', 'min', 'max', 'quantiles', 'histogram'):
            self.assertIn(key, summary)
        self.assertEqual(sum(summary['histogram']['bins']), 4)
        self.assertEqual(summary['label'], 'x')
        self.assertEqual(self.obs.distribution([])['count'], 0)

    def test_runs_measure_dwell(self):
        self.assertEqual(self.obs.runs(['calm', 'calm', 'fear', 'calm']),
                         (('calm', 2), ('fear', 1), ('calm', 1)))
        self.assertEqual(self.obs.runs([]), ())

    def test_the_not_accuracy_label_says_what_it_is_not(self):
        self.assertIn('not agreement with the world', self.obs.NOT_ACCURACY)
        self.assertIn('not an error rate', self.obs.DIVERGENCE_LABEL)

    def test_a_motif_pair_reads_the_declared_cost_next_to_the_traffic(self):
        pair = self.obs.MotifPair('fear', 'anger', forward=3, backward=1,
                                  cost_forward=0.10, cost_backward=0.60)
        self.assertGreater(pair.asymmetry, 0)
        self.assertEqual(pair.cheaper, 1)
        self.assertTrue(pair.agrees)
        against = self.obs.MotifPair('fear', 'anger', forward=1, backward=3,
                                     cost_forward=0.10, cost_backward=0.60)
        self.assertFalse(against.agrees)


# --------------------------------------------------------------------------- fixture index


def entity(entity_id, label, entity_type, **attributes):
    return {'kind': 'entity', 'id': 'r:' + entity_id, 'entity_id': entity_id, 'entity_type': entity_type,
            'label': label, 'observed_at': '2025-01-01', 'evidence': EVIDENCE, 'attributes': attributes}


def assertion(key, subject, predicate, obj, **extra):
    return {'kind': 'assertion', 'id': 'r:' + key, 'subject': subject, 'predicate': predicate,
            'object': obj, 'observed_at': extra.pop('observed_at', '2025-01-01'),
            'evidence': EVIDENCE, **extra}


def observation(key, subject, metric, value, **extra):
    return {'kind': 'observation', 'id': 'r:' + key, 'subject': subject, 'metric': metric,
            'value': value, 'unit': 'score', 'observed_at': '2025-01-01', 'evidence': EVIDENCE, **extra}


def fixture_records():
    """Two legislators on one committee, a committee with a real referral set, and a small issuer."""
    records = [
        entity(ADA, 'Ada Fixture', 'person', birthday='1970-02-03', gender='F'),
        entity(BEN, 'Ben Fixture', 'person', birthday='1968-05-06', gender='M'),
        entity('congress:role:x1', 'Senator for ZZ', 'role', role_type='sen', jurisdiction_code='ZZ',
               source_term={'party': 'Independent', 'start': '2025-01-03', 'end': '2031-01-03', 'type': 'sen'}),
        entity('congress:role:x2', 'Senator for YY', 'role', role_type='sen', jurisdiction_code='YY',
               source_term={'party': 'Independent', 'start': '2025-01-03', 'end': '2031-01-03', 'type': 'sen'}),
        entity(COMMITTEE, 'Fixture Committee', 'institution', chamber='Senate', committee_type='Standing'),
        entity(OTHER_COMMITTEE, 'Other Committee', 'institution', chamber='Senate'),
        entity(SHARED_BILL, 'S 2: Shared Act', 'law', origin_chamber='Senate',
               measure_status='not enacted as of source update',
               latest_action={'date': '2025-02-01', 'text': 'Read twice and referred to the Fixture Committee.'}),
        entity(OUTSIDE_BILL, 'S 3: Other Act', 'law', origin_chamber='Senate',
               measure_status='not enacted as of source update',
               latest_action={'date': '2025-02-10', 'text': 'Read twice and referred to the Other Committee.'}),
        entity('fec:candidate:X1', 'FIXTURE, ADA', 'person'),
        entity('fec:candidate:X2', 'FIXTURE, BEN', 'person'),
        entity('fec:committee:D1', 'Fixture Backers PAC', 'organization', committee_type='Q'),
        entity('fec:committee:O1', 'Opposing PAC', 'organization', committee_type='Q'),
        entity('icpsr:99001', 'Ada Fixture', 'person'),
        entity('icpsr:99002', 'Ben Fixture', 'person'),
        entity(FIRM, 'Fixture Industries N.V.', 'organization'),
        entity(SUB, 'Fixture Subholdings B.V.', 'organization'),
        entity('sec:cik:0000000101', 'Chief Executive', 'person'),
        entity('sec:cik:0000000102', 'Chief Financial', 'person'),
        entity('sec:cik:0000000106', 'General Counsel', 'person'),
        entity('sec:cik:0000000103', 'Director One', 'person'),
        entity('sec:cik:0000000104', 'Director Two', 'person'),
        entity('sec:cik:0000000105', 'Director Three', 'person'),
        assertion('same1', ADA, 'same_as', 'fec:candidate:X1'),
        assertion('same2', ADA, 'same_as', 'icpsr:99001'),
        assertion('same3', BEN, 'same_as', 'fec:candidate:X2'),
        assertion('same4', BEN, 'same_as', 'icpsr:99002'),
        assertion('role1', ADA, 'holds_role', 'congress:role:x1',
                  valid_from='2025-01-03', valid_to='2031-01-03'),
        assertion('role2', BEN, 'holds_role', 'congress:role:x2',
                  valid_from='2025-01-03', valid_to='2031-01-03'),
        assertion('cm1', ADA, 'committee_member', COMMITTEE, attributes={'title': 'chair'}),
        assertion('cm2', BEN, 'committee_member', COMMITTEE, attributes={'title': 'member'}),
        assertion('cm3', BEN, 'committee_member', OTHER_COMMITTEE, attributes={'title': 'member'}),
        assertion('sp1', ADA, 'sponsored_measure', SHARED_BILL, valid_from='2025-01-20'),
        assertion('sp2', BEN, 'sponsored_measure', SHARED_BILL, valid_from='2025-01-21'),
        assertion('sp3', ADA, 'sponsored_measure', OUTSIDE_BILL, valid_from='2025-02-05'),
        assertion('sp4', BEN, 'sponsored_measure', OUTSIDE_BILL, valid_from='2025-02-06'),
        assertion('ref1', SHARED_BILL, 'referred_to_committee', COMMITTEE, valid_from='2025-02-01'),
        assertion('ref2', OUTSIDE_BILL, 'referred_to_committee', OTHER_COMMITTEE, valid_from='2025-02-10'),
        assertion('sup1', 'fec:committee:D1', 'supports_candidate', 'fec:candidate:X1',
                  valid_from='2025-01-01', valid_to='2027-01-01'),
        assertion('sup2', 'fec:committee:D1', 'supports_candidate', 'fec:candidate:X2',
                  valid_from='2025-01-02', valid_to='2027-01-01'),
        assertion('opp1', 'fec:committee:O1', 'opposes_candidate', 'fec:candidate:X1',
                  valid_from='2025-01-06', valid_to='2027-01-01'),
        observation('dw1', 'icpsr:99001', 'dw_nominate_dim1', -0.5,
                    dimensions={'chamber': 'Senate', 'congress': 119},
                    valid_from='2025-01-03', valid_to='2027-01-03'),
        observation('dw2', 'icpsr:99002', 'dw_nominate_dim1', 0.4,
                    dimensions={'chamber': 'Senate', 'congress': 119},
                    valid_from='2025-01-03', valid_to='2027-01-03'),
        assertion('sub1', SUB, 'directly_consolidated_by', FIRM, valid_from='2024-01-01'),
        assertion('sub2', SUB, 'ultimately_consolidated_by', FIRM, valid_from='2024-01-01'),
    ]
    for cik, title, relationship in (('0000000101', 'Chief Executive Officer', ['Officer']),
                                     ('0000000102', 'EVP & Chief Financial Officer', ['Officer']),
                                     ('0000000106', 'General Counsel', ['Officer']),
                                     ('0000000103', 'Director', ['Director']),
                                     ('0000000104', 'Director', ['Director']),
                                     ('0000000105', 'Director', ['Director'])):
        records.append(assertion('ins' + cik, 'sec:cik:' + cik, 'insider_of', FIRM,
                                 valid_from='2024-01-01', valid_to='2026-12-31',
                                 attributes={'officer_title': title, 'relationship': relationship}))
    return records


def build_fixture_index(path):
    graph = Graph(path)
    graph.build_from_records([(REF, fixture_records())])
    graph.attach_resolution(
        [{'canonical_id': ADA, 'members': [ADA, 'fec:candidate:X1', 'icpsr:99001']},
         {'canonical_id': BEN, 'members': [BEN, 'fec:candidate:X2', 'icpsr:99002']}],
        view={'view_digest': 'd' * 64, 'policy': {'links': 'fixture'}})
    return graph


QUARTERS = [{'period_end': '2025-03-31', 'filed': '2025-04-20', 'cash': 4.0e8, 'revenue': 9.0e8,
             'costs': 8.9e8, 'capex': 1.2e8},
            {'period_end': '2025-06-30', 'filed': '2025-07-20', 'cash': 3.1e8, 'revenue': 8.6e8,
             'costs': 8.8e8, 'capex': 1.2e8},
            {'period_end': '2025-09-30', 'filed': '2025-10-20', 'cash': 2.2e8, 'revenue': 8.2e8,
             'costs': 8.7e8, 'capex': 1.1e8}]

CONFIG = {
    'id': 'fixture-society',
    'clock': {'start': '2025-01-01', 'window_days': 90},
    'seed': 5,
    'persons': [{'entity_id': ADA, 'tier': 'focal'}, {'entity_id': BEN, 'tier': 'focal'}],
    'institutions': [{'entity_id': COMMITTEE, 'tier': 'focal'}],
    'firms': [{'entity_id': FIRM, 'tier': 'focal', 'financials': 'declared', 'quarters': QUARTERS,
               'authority': 'standard_delegation',
               'objective': [{'measure': 'runway_quarters', 'direction': 'at_least', 'target': 4.0,
                              'weight': 1.0, 'scale': 1.0},
                             {'measure': 'cash_cover', 'direction': 'at_least', 'target': 0.25,
                              'weight': 0.5, 'scale': 1.0}],
               'role_objectives': {'cfo': [{'measure': 'operating_margin', 'direction': 'maximize',
                                            'weight': 1.0, 'scale': 0.01}]},
               'options': [{'id': 'buyback', 'label': 'return capital',
                            'effects': [['cash_usd', -1.0e8], ['runway_quarters', -2.5],
                                        ['cash_cover', -0.35], ['operating_margin', 0.004]],
                            'amount': 1.0e8},
                           {'id': 'retain', 'label': 'hold the cash',
                            'effects': [['runway_quarters', 0.8], ['cash_cover', 0.12],
                                        ['operating_margin', -0.002]], 'amount': 0.0}]},
              {'entity_id': SUB, 'tier': 'compact'}],
    'links': [[ADA, FIRM]],
}


def fixture_config(**overrides):
    config = copy.deepcopy(CONFIG)
    config.update(overrides)
    return config


# --------------------------------------------------------------------------- the loop


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class SocietyFixtureTests(unittest.TestCase):
    """The loop, the closure between agents, the tiers, and determinism."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'index.sqlite'
        build_fixture_index(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def society(self, config=None, **kwargs):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.society import Society
        index = EvidenceIndex(self.path, cache_mb=8)
        self.addCleanup(index.close)
        return Society.from_config(config or fixture_config(), index=index, **kwargs)

    # -- construction -------------------------------------------------------------------
    def test_the_population_is_mixed_and_grounded(self):
        society = self.society()
        kinds = {m.entity_id: m.kind for m in society.members.values()}
        self.assertEqual(kinds[ADA], 'person')
        self.assertEqual(kinds[COMMITTEE], 'institution')
        self.assertEqual(kinds[FIRM], 'firm')
        self.assertEqual(society.members[SUB].tier, 'compact')
        self.assertEqual(society.members[ADA].label, 'Ada Fixture')

    def test_the_audience_comes_from_published_co_membership(self):
        society = self.society()
        self.assertIn(BEN, society.audience(ADA))          # both sit on the Fixture Committee
        self.assertIn(COMMITTEE, society.audience(ADA))
        self.assertIn(FIRM, society.audience(ADA))         # a declared link, on top of the record
        self.assertIn(SUB, society.audience(FIRM))         # published consolidation

    def test_the_order_is_focal_first_then_by_entity_id(self):
        society = self.society()
        order = [m.entity_id for m in society.order()]
        self.assertEqual(order, sorted([ADA, BEN, COMMITTEE, FIRM]) + [SUB])

    def test_a_bad_config_says_what_is_wrong(self):
        from worldmodel.agents.society import validate_config
        with self.assertRaises(ValueError):
            validate_config({'persons': []})
        with self.assertRaises(ValueError):
            validate_config({'clock': {'start': '2025-01-01'}, 'persons': [{'tier': 'focal'}]})
        with self.assertRaises(ValueError):
            validate_config({'clock': {'start': '2025-01-01'},
                             'persons': [{'entity_id': ADA, 'tier': 'nowhere'}]})
        with self.assertRaises(ValueError):
            validate_config({'clock': {'start': '2025-01-01'},
                             'persons': [{'entity_id': ADA}, {'entity_id': ADA}]})

    # -- the tick -----------------------------------------------------------------------
    def test_a_tick_perceives_thinks_and_decides_for_every_focal_agent(self):
        society = self.society()
        record = society.tick()
        self.assertEqual(record.tick, 0)
        self.assertIn(ADA, record.perception)
        self.assertIn(ADA, record.motifs)
        self.assertIn(FIRM, record.regimes)
        self.assertIn(COMMITTEE, record.legitimacy)
        self.assertTrue(record.decisions)
        self.assertGreater(len(society.channel), 0)

    def test_acts_are_visible_in_the_next_tick_and_not_in_their_own(self):
        """The simultaneity decision, asserted rather than assumed."""
        society = self.society()
        first = society.tick()
        self.assertEqual(first.delivered, 0)               # nothing was said before tick 0
        self.assertGreater(first.published, 0)
        second = society.tick()
        self.assertGreater(second.delivered, 0)
        for delivery in society.channel.deliveries:
            utterance = next(u for u in society.channel.utterances if u.id == delivery.utterance)
            self.assertEqual(delivery.tick, utterance.tick + 1)
        self.assertEqual(society.simultaneity, 'next_tick')

    def test_one_agents_act_becomes_another_agents_percept_and_moves_it(self):
        """The whole point of the module: a claim crosses from one store into another and derives.

        The utterance is constructed explicitly so the test does not depend on which intention the
        chooser happened to pick, but it is the same value ``_act_person`` publishes.
        """
        from worldmodel.agents.society import Utterance, claim_key
        society = self.society()
        society.tick()
        listener = society.members[BEN]
        content = (SHARED_BILL, 'counter_opponent', 'contesting')
        utterance = Utterance(id='u1', tick=society.ticks - 1, actor=ADA, kind='stance', content=content,
                              salience=0.9, claim_key=claim_key(content), origin_tick=society.ticks - 1,
                              origin_actor=ADA, memo='counter_opponent')
        before = {r.id for r in listener.agent.store.claims(listener.agent.me, 'appraises')}
        listener.inbox = (utterance,)
        derived = society._deliver_focal(listener, (utterance,), society.clock.window(society.ticks))
        heard = listener.agent.store.claims(scope=listener.agent.heard_scope)
        self.assertTrue(heard, 'the utterance reached the listener store')
        relation = [r for r in heard if r.claim.predicate == 'contested_by']
        self.assertTrue(relation)
        evidence = relation[0].evidence[0]
        self.assertEqual(str(evidence.source), 'agent:%s' % ADA)
        # 'hearsay' when the transmission seam is installed (it weighs a teller's word against
        # direct observation), 'relayed' when it is not and the bare fidelity is recorded.
        self.assertIn(evidence.confidence.kind, ('hearsay', 'relayed'))
        self.assertLess(evidence.confidence.value, 1.0)
        # and it moved the listener: a rebuff appraisal about the measure it sponsors
        self.assertGreater(derived, 0)
        after = {r.id for r in listener.agent.store.claims(listener.agent.me, 'appraises')}
        self.assertTrue(after - before)
        tags = {r.claim.object[0] for r in listener.agent.store.claims(listener.agent.me, 'appraises')}
        self.assertIn('rebuff', tags)

    def test_hearsay_lapses_and_is_forgotten(self):
        from worldmodel.agents.society import Utterance, claim_key
        society = self.society(fixture_config(hearsay_ticks=1))
        society.tick()
        listener = society.members[BEN]
        content = (SHARED_BILL, 'counter_opponent', 'contesting')
        utterance = Utterance(id='u1', tick=0, actor=ADA, kind='stance', content=content, salience=0.9,
                              claim_key=claim_key(content), origin_tick=0, origin_actor=ADA)
        society._deliver_focal(listener, (utterance,), society.clock.window(society.ticks))
        self.assertTrue(listener.agent.store.claims(scope=listener.agent.heard_scope))
        society.ticks += 2
        dropped = society._expire(society.ticks)
        self.assertGreater(dropped, 0)
        self.assertFalse(listener.agent.store.claims(scope=listener.agent.heard_scope))

    def test_an_institution_acts_on_what_the_population_asked_for(self):
        """A person's decision reaches the committee, which reports it or refuses it as ultra vires."""
        from worldmodel.agents.society import Utterance, claim_key
        society = self.society()
        society.tick()
        committee = society.members[COMMITTEE]
        for measure in (SHARED_BILL, OUTSIDE_BILL):
            content = (measure, 'advance_measure', 'advancing')
            society._deliver_focal(committee, (Utterance(
                id='u:' + measure, tick=0, actor=ADA, kind='stance', content=content, salience=0.9,
                claim_key=claim_key(content), origin_tick=0, origin_actor=ADA),),
                society.clock.window(1))
        record = society.tick()
        acts = [d for d in record.decisions if d.kind == 'institution']
        self.assertEqual(len(acts), 2)
        by_subject = {d.subject: d for d in acts}
        self.assertEqual(by_subject[SHARED_BILL].authority, 'holds')      # referred to this committee
        self.assertEqual(by_subject[OUTSIDE_BILL].authority, 'fails')     # referred elsewhere
        self.assertIn('outside the declared jurisdiction', by_subject[OUTSIDE_BILL].reasons[0])
        self.assertTrue(committee.agent.institution.refusals())

    def test_a_firm_speaks_through_the_disclosure_desk(self):
        """A firm does not gossip. With the desk installed its utterance is a ``Disclosure``."""
        from worldmodel.agents.society import seams
        if seams().desk is None:
            self.skipTest('agents.disclosure is not in this checkout')
        society = self.society()
        society.tick()
        spoken = [u for u in society.channel.utterances if u.actor == FIRM]
        self.assertTrue(spoken)
        self.assertTrue(all(u.formal for u in spoken), 'a firm speaks formally or not at all')
        regime = next(u for u in spoken if u.content[1] == 'regime')
        self.assertEqual(regime.speech.form, 'press_release')
        self.assertIn('public', regime.speech.audience)
        self.assertEqual(regime.speech.role_id, society.members[FIRM].agent.speaking_role())
        # and it reaches a listener as a *disclosure*, not as an observation
        society.tick()
        from worldmodel.agents.disclosure import disclosed_not_observed, provenance_of
        listener = society.members[ADA].agent          # linked to the firm in the config
        held = [r for r in listener.store.claims(scope=listener.heard_scope)
                if r.claim.predicate == 'regime']
        self.assertTrue(held, 'the firm disclosure reached the linked legislator')
        self.assertTrue(disclosed_not_observed(listener.store, held[0].id))
        self.assertEqual(provenance_of(listener.store, held[0].id)[0][:2], ('disclosed', FIRM))

    def test_a_firm_with_no_declared_authority_to_speak_says_nothing(self):
        from worldmodel.agents.society import seams
        if seams().desk is None:
            self.skipTest('agents.disclosure is not in this checkout')
        config = fixture_config()
        config['firms'] = [{**config['firms'][0], 'authority': None}, config['firms'][1]]
        society = self.society(config)
        record = society.tick()
        self.assertEqual([u for u in society.channel.utterances if u.actor == FIRM], [])
        silent = [d for d in record.decisions if d.actor == FIRM and d.outcome == 'said_nothing']
        self.assertEqual(len(silent), 1)
        self.assertEqual(silent[0].authority, 'unknown')
        self.assertIn('approve_disclosure', silent[0].reasons[0])

    def test_an_institution_answers_with_an_instrument(self):
        from worldmodel.agents.society import INSTITUTION_INSTRUMENT, Utterance, claim_key, seams
        if seams().docket is None:
            self.skipTest('agents.instruments is not in this checkout')
        society = self.society()
        society.tick()
        committee = society.members[COMMITTEE]
        for measure in (SHARED_BILL, OUTSIDE_BILL):
            content = (measure, 'advance_measure', 'advancing')
            society._deliver_focal(committee, (Utterance(
                id='u:' + measure, tick=0, actor=ADA, kind='stance', content=content, salience=0.9,
                claim_key=claim_key(content), origin_tick=0, origin_actor=ADA),),
                society.clock.window(1))
        record = society.tick()
        acts = {d.subject: d for d in record.decisions if d.kind == 'institution'}
        self.assertEqual(acts[SHARED_BILL].outcome, INSTITUTION_INSTRUMENT)
        self.assertEqual(acts[SHARED_BILL].authority, 'holds')
        self.assertEqual(acts[OUTSIDE_BILL].authority, 'fails')
        issued = [u for u in society.channel.utterances if u.actor == COMMITTEE and u.formal]
        self.assertEqual(len(issued), 1, 'only the authorized act is said out loud')
        self.assertEqual(issued[0].content, (COMMITTEE, INSTITUTION_INSTRUMENT, SHARED_BILL))
        self.assertTrue(issued[0].speech.issued)
        # the refusal is silence to the population and evidence to a reader
        self.assertTrue(committee.agent.institution.refusals())
        self.assertTrue(any(r.status != 'granted' for r in committee.agent.institution.log.records))

    def test_a_private_disclosure_reaches_nobody_outside_its_audience(self):
        """The audience lattice belongs to the disclosure module and the society obeys it."""
        from worldmodel.agents.society import Utterance, claim_key, seams
        if seams().desk is None:
            self.skipTest('agents.disclosure is not in this checkout')
        society = self.society()
        society.tick()
        social = society.members[FIRM].agent
        private = social.desk.disclose(statement=(FIRM, 'guidance', 'withdrawn'),
                                       form='private_briefing', audience='counterparty',
                                       role_id=social.speaking_role(), recipients=(SUB,),
                                       effective_date=society.clock.window(0)[1])
        self.assertEqual(private.status, 'disclosed')
        content = (FIRM, 'guidance', 'withdrawn')
        utterance = Utterance(id='u-private', tick=0, actor=FIRM, kind='disclosure', content=content,
                              salience=0.9, claim_key=claim_key(content), origin_tick=0,
                              origin_actor=FIRM, speech=private)
        listener = society.members[ADA]
        before = len(listener.agent.store._claims)
        added = society._absorb(listener, utterance, at=society.clock.window(1)[0])
        self.assertEqual(added, ())
        self.assertEqual(len(listener.agent.store._claims), before)
        delivery = society.channel.deliveries[-1]
        self.assertFalse(delivery.accepted)
        self.assertEqual(delivery.refused, 'not_in_the_audience')

    def test_a_compact_agent_relays_without_a_store(self):
        from worldmodel.agents.society import Utterance, claim_key
        society = self.society()
        sub = society.members[SUB]
        self.assertIsNone(sub.agent)
        content = (FIRM, 'regime', 'distressed')
        sub.inbox = (Utterance(id='u1', tick=0, actor=FIRM, kind='regime', content=content,
                               salience=0.8, claim_key=claim_key(content), origin_tick=0,
                               origin_actor=FIRM),)
        record = society.history and society.history[-1]
        from worldmodel.agents.society import TickRecord
        record = TickRecord(tick=1, window=society.clock.window(1))
        relayed = society._relay(sub, 1, record)
        self.assertEqual(len(relayed), 1)
        self.assertEqual(relayed[0].chain, (FIRM,))
        self.assertLess(relayed[0].fidelity, 1.0)
        self.assertEqual(relayed[0].origin_actor, FIRM)
        self.assertEqual(sub.compact.relayed, 1)

    # -- determinism --------------------------------------------------------------------
    def test_two_runs_of_the_same_seed_are_the_same_society(self):
        def run(seed):
            society = self.society(fixture_config(), seed=seed)
            society.run(3)
            return ([record.to_json() for record in society.history],
                    [u.to_json() for u in society.channel.utterances],
                    [d.to_json() for d in society.channel.deliveries])

        first, second = run(5), run(5)
        self.assertEqual(first[1], second[1])
        self.assertEqual(first[2], second[2])
        for a, b in zip(first[0], second[0]):
            for key in ('motifs', 'regimes', 'decisions', 'divergence', 'claims_live',
                        'delivered', 'published', 'relayed'):
                self.assertEqual(a[key], b[key], key)

    def test_the_seed_is_what_makes_an_agent_that_agent(self):
        society_a = self.society(fixture_config(), seed=1)
        society_b = self.society(fixture_config(), seed=2)
        self.assertNotEqual(society_a.members[ADA].agent.coupling,
                            society_b.members[ADA].agent.coupling)

    # -- tiering ------------------------------------------------------------------------
    def test_promotion_invents_nothing(self):
        society = self.society()
        society.run(2)
        report = society.promote(SUB)
        self.assertIsNotNone(report)
        self.assertEqual(report.invented, 0)
        self.assertEqual(society.members[SUB].tier, 'focal')
        store = society.members[SUB].agent.store
        self.assertGreater(len(store._claims), 0)
        allowed = ('dataset:',      # a published record
                   'agent:',        # an informal utterance replayed out of the shared event log
                   'disclosure:',   # a formal organizational act replayed out of the same log
                   'charter:')      # the charter that declared the seat
        for record in store._claims.values():
            for evidence in record.evidence:
                source = str(evidence.source)
                self.assertTrue(source.startswith(allowed),
                                'a promoted claim may only cite a published record, a logged act or '
                                'the charter that declared the seat, got %s' % source)
        # every facet the catalog does not publish stays Unknown and is named
        self.assertTrue(report.unknown)
        self.assertTrue(any('quarterly financials' in item for item in report.unknown))

    def test_promotion_replays_the_event_log_as_hearsay_not_as_record(self):
        """A replayed claim is marked as told, never as observed, whichever speech act carried it."""
        society = self.society()
        society.run(3)
        report = society.promote(SUB)
        store = society.members[SUB].agent.store
        replayed = [r for r in store._claims.values()
                    if r.evidence and (r.evidence[0].method or '').startswith(('event-log:',
                                                                              'disclosed_by:'))]
        self.assertEqual(len(replayed), report.from_event_log)
        self.assertGreater(len(replayed), 0)
        for record in replayed:
            method = record.evidence[0].method
            if method.startswith('event-log:'):
                self.assertIn('replayed at tick', record.evidence[0].locator)
                self.assertIn(record.evidence[0].confidence.kind, ('hearsay', 'relayed'))
            else:
                # Formal speech keeps the disclosure module's own provenance, so "was this seen or
                # was I told" stays a query rather than becoming a convention of this module.
                from worldmodel.agents.disclosure import disclosed_not_observed
                self.assertTrue(disclosed_not_observed(store, record.id))

    def test_demotion_records_what_was_dropped(self):
        society = self.society()
        society.run(2)
        before = len(society.members[BEN].agent.store._claims)
        report = society.demote(BEN)
        self.assertIsNotNone(report)
        self.assertEqual(report.claims_dropped, before)
        self.assertEqual(sum(count for _predicate, count in report.by_predicate), before)
        self.assertGreater(len(report.by_predicate), 1)
        self.assertGreater(report.derivations_dropped, 0)
        self.assertIn('world', report.scopes_dropped)
        self.assertEqual(society.members[BEN].tier, 'compact')
        self.assertIsNone(society.members[BEN].agent)
        self.assertEqual(society.members[BEN].compact.claims_at_demotion, before)
        self.assertEqual(society.members[BEN].compact.dropped, report)
        self.assertTrue(dict(report.retained)['state'])

    def test_the_schedule_promotes_and_demotes_mid_run(self):
        config = fixture_config(demote=[{'at_tick': 1, 'entity_id': BEN}],
                                promote=[{'at_tick': 2, 'entity_id': SUB}])
        society = self.society(config)
        society.run(3)
        self.assertEqual(society.members[BEN].tier, 'compact')
        self.assertEqual(society.members[SUB].tier, 'focal')
        self.assertEqual(len(society._demotions), 1)
        self.assertEqual(len(society._promotions), 1)

    def test_a_cohort_absorbs_reach_and_can_be_sampled_out_of(self):
        config = fixture_config()
        config['cohorts'] = [{'id': 'cohort:fixture-subs', 'kind': 'firm',
                              'members': ['lei:FIXTURECOHORT00000001']}]
        config['links'] = config['links'] + [[FIRM, 'cohort:fixture-subs']]
        society = self.society(config)
        society.run(2)
        cohort = society.cohorts['cohort:fixture-subs']
        self.assertGreater(cohort.heard, 0, 'the cohort absorbs reach as a count')
        report = society.promote('lei:FIXTURECOHORT00000001')
        # Disaggregation is marked as sampled, and an entity the catalog publishes nothing about
        # is promoted to a store with no claims at all rather than to an invented one.
        self.assertTrue(report.sampled)
        self.assertEqual(report.claims, 0)
        self.assertEqual(report.invented, 0)
        self.assertEqual(len(report.unknown), 5)
        self.assertEqual(cohort.disaggregated, ('lei:FIXTURECOHORT00000001',))
        self.assertEqual(len(society.members['lei:FIXTURECOHORT00000001'].agent.store._claims), 0)

    # -- budget -------------------------------------------------------------------------
    def test_the_focal_budget_is_measured_not_assumed(self):
        society = self.society()
        society.run(1)
        budget = society.budget(memory_budget_bytes=1024 ** 3)
        self.assertEqual(budget.focal_agents, 4)
        self.assertGreater(budget.claims_per_focal, 0)
        self.assertGreater(budget.focal_ceiling, 0)
        self.assertIsNotNone(budget.serialized_bytes_per_claim)
        self.assertEqual(budget.declared_bytes_per_claim, 1200)
        self.assertIn('declared 1.2 KB/claim', budget.to_json()['basis'])

    # -- seams --------------------------------------------------------------------------
    def test_the_optional_seams_are_reported_present_or_absent(self):
        from worldmodel.agents.society import seams
        resolved = seams()
        self.assertEqual(set(resolved._fields),
                         {'hearsay_weight', 'desk', 'docket', 'instrument_powers', 'receive'})
        society = self.society()
        flags = society.to_json()['seams']
        self.assertEqual(set(flags), set(resolved._fields))
        for name in resolved._fields:
            self.assertEqual(flags[name], getattr(resolved, name) is not None)

    def test_the_transmission_seam_weighs_hearsay_below_direct_observation(self):
        from worldmodel.agents.society import Seams, Utterance, claim_key
        society = self.society()
        content = (SHARED_BILL, 'counter_opponent', 'contesting')
        utterance = Utterance(id='u1', tick=0, actor=ADA, kind='stance', content=content,
                              salience=0.9, claim_key=claim_key(content), origin_tick=0,
                              origin_actor=ADA, fidelity=1.0)
        member = society.members[BEN]
        weighed = society._hearsay_confidence(member, utterance)
        self.assertLess(weighed.value, 1.0)
        # and with the seam absent the bare fidelity is recorded, labelled so it cannot be read as
        # a calibrated probability
        society.seams = Seams(None, society.seams.desk, society.seams.docket,
                              society.seams.instrument_powers, society.seams.receive)
        bare = society._hearsay_confidence(member, utterance)
        self.assertEqual((bare.value, bare.kind), (1.0, 'relayed'))

    def test_the_relay_decay_is_the_declared_constant(self):
        from worldmodel.agents.society import RELAY_FIDELITY, TickRecord, Utterance, claim_key
        society = self.society()
        content = (FIRM, 'regime', 'pressured')
        society.members[SUB].inbox = (Utterance(
            id='u1', tick=0, actor=FIRM, kind='regime', content=content, salience=0.9,
            claim_key=claim_key(content), origin_tick=0, origin_actor=FIRM),)
        relayed = society._relay(society.members[SUB], 1,
                                 TickRecord(tick=1, window=society.clock.window(1)))
        self.assertAlmostEqual(relayed[0].fidelity, RELAY_FIDELITY, places=6)


# --------------------------------------------------------------------------- the observatory


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class ObservatoryFixtureTests(unittest.TestCase):
    """What the observatory measures, and what it refuses to claim."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'index.sqlite'
        build_fixture_index(cls.path)
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.observatory import Observatory
        from worldmodel.agents.society import Society
        cls.index = EvidenceIndex(cls.path, cache_mb=8)
        cls.society = Society.from_config(fixture_config(), index=cls.index)
        cls.society.run(4)
        cls.observatory = Observatory(cls.society)
        cls.report = cls.observatory.report(memory_budget_bytes=1024 ** 3)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()
        cls.tmp.cleanup()

    def test_divergence_is_reported_as_a_distribution_and_labelled_expected(self):
        divergence = self.report['belief_divergence']
        self.assertGreater(divergence['samples'], 0)
        self.assertIn('quantiles', divergence['aggregate'])
        self.assertIn('not an error rate', divergence['label'])
        self.assertIn(ADA, divergence['per_agent'])
        self.assertIn('serves_on', divergence['per_predicate'])
        for value in divergence['aggregate']['quantiles'].values():
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)

    def test_divergence_separates_belief_beyond_the_record_from_the_perception_bound(self):
        divergence = self.report['belief_divergence']
        self.assertIn('unsupported_claims', divergence)
        self.assertIn('missing_edges', divergence)
        self.assertIn('hearsay', divergence['unsupported_claims']['label'])
        self.assertIn('perception bound', divergence['missing_edges']['label'])

    def test_propagation_reports_reach_latency_and_degradation(self):
        propagation = self.report['propagation']
        self.assertGreater(propagation['deliveries'], 0)
        self.assertGreater(propagation['distinct_claims'], 0)
        self.assertIn('reach_fraction', propagation)
        self.assertGreaterEqual(propagation['latency_ticks']['min'], 1)
        hops = {row['hops']: row for row in propagation['degradation']}
        self.assertIn(0, hops)
        self.assertAlmostEqual(hops[0]['mean_fidelity'], 1.0, places=6)
        for count, row in sorted(hops.items()):
            self.assertAlmostEqual(row['mean_fidelity'],
                                   propagation['declared_relay_fidelity'] ** count, places=5)

    def test_belief_provenance_counts_where_each_store_came_from(self):
        provenance = self.report['belief_divergence']['provenance']
        self.assertIn(ADA, provenance['final'])
        counts = provenance['final'][ADA]
        self.assertEqual(counts['total'],
                         counts['published'] + counts['hearsay'] + counts['derived'] + counts['internal'])
        self.assertGreater(counts['published'], 0)
        self.assertGreater(counts['derived'], 0)
        self.assertGreater(provenance['from_the_record_share']['mean'], 0.0)
        self.assertLessEqual(provenance['from_the_record_share']['max'], 1.0)

    def test_propagation_separates_formal_speech_from_gossip(self):
        propagation = self.report['propagation']
        self.assertEqual(propagation['formal_utterances'] + propagation['informal_utterances'],
                         propagation['utterances'])
        self.assertEqual(propagation['accepted'] + sum(propagation['not_held'].values()),
                         propagation['deliveries'])
        self.assertIn('audience lattice', propagation['not_held_note'])

    def test_trajectories_carry_motifs_regimes_and_the_declared_transition_costs(self):
        trajectories = self.report['trajectories']
        self.assertEqual(len(trajectories['motifs'][ADA]), 4)
        self.assertEqual(len(trajectories['regimes'][FIRM]), 4)
        self.assertIn('hysteresis', trajectories)
        for pair in trajectories['hysteresis']['pairs']:
            self.assertIn('cost_forward', pair)
            self.assertIn('cost_backward', pair)
        self.assertGreaterEqual(trajectories['stickiness']['motif_mean_dwell'], 1.0)
        # a firm has no transition-cost matrix, so regime hysteresis is refused rather than faked
        self.assertFalse(trajectories['regime_hysteresis']['measured'])
        self.assertIn('no prototypes and no transition-cost matrix',
                      trajectories['regime_hysteresis']['why'])

    def test_decision_structure_counts_non_decisions_divergence_and_refusals(self):
        decisions = self.report['decision_structure']
        self.assertIn('unknown_by_reason', decisions['persons'])
        self.assertGreaterEqual(decisions['persons']['decisions'], 4)
        self.assertIn('outcome_changed', decisions['firms'])
        self.assertIn('ultra_vires_share', decisions['institutions'])
        self.assertIn('honest non-decision', decisions['persons']['label'])

    def test_every_section_is_labelled_as_internal_behaviour(self):
        self.assertEqual(self.report['epistemic_status'], 'simulation_internal_behaviour')
        self.assertFalse(self.report['validated'])
        for section in ('belief_divergence', 'propagation', 'trajectories', 'decision_structure'):
            self.assertIn('not_accuracy', self.report[section], section)
        self.assertIn('No statistic in it is a forecast', self.report['disclaimer'])

    def test_the_report_says_how_to_reproduce_itself(self):
        reproduce = self.report['reproduce']
        self.assertEqual(reproduce['seed'], 5)
        self.assertEqual(reproduce['ticks'], 4)
        self.assertIn('society-run', reproduce['command'])
        self.assertEqual(reproduce['config']['id'], 'fixture-society')

    def test_the_report_is_json_serializable_and_renders(self):
        import json
        from worldmodel.agents.observatory import render
        text = json.dumps(self.report, allow_nan=False)
        self.assertGreater(len(text), 1000)
        lines = render(json.loads(text))
        self.assertTrue(any('belief divergence' in line for line in lines))
        self.assertTrue(any('propagation' in line for line in lines))
        self.assertTrue(any('focal ceiling' in line for line in lines))

    def test_publishing_goes_through_the_artifact_path(self):
        import json
        from worldmodel.artifacts import load_report
        from worldmodel.store import Store
        with tempfile.TemporaryDirectory() as root:
            store = Store(Path(root))
            ref = self.observatory.publish(store, dataset='agent_society_report',
                                           report=json.loads(json.dumps(self.report)))
            self.assertEqual(ref['dataset'], 'agent_society_report')
            stored = load_report(store, ref)
            self.assertEqual(stored['epistemic_status'], 'simulation_internal_behaviour')


# --------------------------------------------------------------------------- the CLI


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class SocietyCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'index.sqlite'
        build_fixture_index(cls.path)
        cls.config = Path(cls.tmp.name) / 'society.json'
        import json
        cls.config.write_text(json.dumps(fixture_config()), encoding='utf-8')

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_cli(self, *argv):
        from worldmodel.cli import execute, parser
        return execute(parser().parse_args(['--data-root', self.tmp.name, *argv]))

    def test_society_run_prints_a_report(self):
        result = self.run_cli('society-run', '--config', str(self.config), '--ticks', '2',
                              '--index', str(self.path), '--no-series')
        self.assertEqual(result['epistemic_status'], 'simulation_internal_behaviour')
        self.assertNotIn('series', result['belief_divergence'])
        self.assertEqual(result['reproduce']['ticks'], 2)

    def test_society_run_and_report_round_trip_through_an_artifact(self):
        published = self.run_cli('society-run', '--config', str(self.config), '--ticks', '2',
                                 '--index', str(self.path), '--no-series', '--publish')
        reference = '%s@%s' % (published['artifact']['dataset'], published['artifact']['version'])
        lines = self.run_cli('society-report', reference)
        self.assertTrue(any('belief divergence' in line for line in lines))
        section = self.run_cli('society-report', reference, '--section', 'propagation')
        self.assertIn('degradation', section)

    def test_the_commands_are_registered_in_the_top_level_cli(self):
        from worldmodel.cli import parser
        args = parser().parse_args(['society-run', '--config', 'x.json', '--ticks', '1'])
        self.assertEqual(args.command, 'society-run')


# --------------------------------------------------------------------------- real data


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
@unittest.skipUnless(REAL_INDEX.exists(), 'no local unified-graph index; skipping the real-data smoke test')
class RealSocietySmokeTests(unittest.TestCase):
    """Real legislators who share a real committee, a real issuer and its real subsidiaries."""

    def test_the_example_config_binds_entities_this_index_publishes(self):
        from worldmodel.agents.grounding import EvidenceIndex
        from worldmodel.agents.society import load_config
        config = load_config(EXAMPLE)
        index = EvidenceIndex(REAL_INDEX, cache_mb=32)
        self.addCleanup(index.close)
        named = [entry['entity_id'] for key in ('persons', 'firms', 'institutions')
                 for entry in config.get(key, ())]
        for entity_id in named:
            self.assertIsNotNone(index.entity(entity_id), '%s is not published in this index' % entity_id)

    def test_a_short_real_run_produces_a_report(self):
        from worldmodel.agents.observatory import Observatory
        from worldmodel.agents.society import Society, load_config, open_index
        config = load_config(EXAMPLE)
        index = open_index(REAL_INDEX, cache_mb=32)
        self.addCleanup(index.close)
        society = Society.from_config(config, index=index)
        society.run(2)
        report = Observatory(society).report(include_series=False)
        self.assertEqual(report['epistemic_status'], 'simulation_internal_behaviour')
        self.assertGreater(report['belief_divergence']['samples'], 0)
        self.assertGreater(len(report['society']['population']), 3)


if __name__ == '__main__':
    unittest.main()
