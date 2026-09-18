"""Belief transmission between grounded agents: speech, channels, drift and conflict.

Three layers, deliberately separated the same way ``test_agents.py`` separates them:

* ``SpeechTableTests`` pins the declared tables in ``worldmodel.agents.speech`` and the arithmetic
  in ``worldmodel.agents.transmission`` that needs no substrate and no index;
* the fixture classes skip unless the optional ``agents`` extra (tensorcode) is installed, and run
  against a small index built in a temporary directory;
* ``RealNetworkSmokeTests`` additionally skips unless this checkout has the unified-graph index on
  disk. It propagates a claim about a real measure between real senators joined by published
  committee edges.

Everything is seeded. Nothing here is fitted to an outcome.
"""
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from worldmodel.agents import tensorcode_available
from worldmodel.graph import Graph

UTC = dt.timezone.utc
HAVE_TC = tensorcode_available()
REF = {'dataset': 'fixture_speech', 'stage': 'normalized', 'version': 'e' * 64}
EVIDENCE = [{'input': {'dataset': 'fixture_speech', 'artifact': 'b' * 64}, 'locator': 'line:1'}]
DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
REAL_INDEX = DATA_ROOT / 'world_evidence' / 'index.sqlite'
#: Senate Judiciary, and a measure only one of its members put a name to. Both are matters of
#: record in this catalog; the smoke test asserts the mechanism, not these particular people.
REAL_COMMITTEE = 'congress:committee:ssju00'
REAL_MEASURE = 'congress:bill:119-s-3419'
REAL_CHAIN = ('bioguide:H001042', 'bioguide:B001243', 'bioguide:B001288', 'bioguide:C001098')


# --------------------------------------------------------------------------- declared tables


class LexiconTableTests(unittest.TestCase):
    """The declared tables in ``worldmodel.agents.lexicon``: pure Python, like ``affect``.

    Nothing in this class touches the substrate, the index or the parser, so it runs in every
    environment and pins the wordings, the stage ladder and the token forms.
    """

    def test_every_concept_has_a_canonical_wording_and_a_frame_rule(self):
        from worldmodel.agents import lexicon as lx
        for concept in lx.CONCEPTS:
            self.assertIn(concept, lx.BASE_FORMS, concept)
            self.assertIn(concept, lx.SUBJECT_KIND, concept)
            self.assertIn(concept, lx.CONCEPT_WEIGHT, concept)
        rules = {concept for _stems, _word, concept in lx.FRAME_RULES}
        self.assertEqual(rules, set(lx.CONCEPTS))
        self.assertEqual(set(lx.STAGE_CONCEPT.values()) | set(lx.PREDICATE_CONCEPT.values()),
                         set(lx.CONCEPTS))

    def test_frame_rules_only_map_canonical_verbs(self):
        """The register mechanism *is* this: a non-canonical verb has no rule, so it is opaque."""
        from worldmodel.agents import lexicon as lx
        canonical_verbs = set()
        for phrase in lx.BASE_FORMS.values():
            canonical_verbs.update(word.lower() for word in phrase.split())
        for stems, _word, concept in lx.FRAME_RULES:
            self.assertTrue(any(any(word.startswith(stem) for word in canonical_verbs) for stem in stems),
                            '%s maps a verb no canonical wording uses: %s' % (concept, stems))

    def test_the_stage_ladder_only_slips_upward(self):
        from worldmodel.agents import lexicon as lx
        for stage, slipped in lx.SLIP.items():
            if stage == 'failed':
                self.assertEqual(slipped, 'failed')
                continue
            self.assertGreaterEqual(lx.LADDER.index(slipped), lx.LADDER.index(stage), stage)

    def test_the_slip_is_seeded_and_bounded_by_fidelity(self):
        from worldmodel.agents import lexicon as lx
        self.assertEqual(lx.slip('stage_referred', 1.0, seed=1, utterance='u'), ('stage_referred', False))
        self.assertEqual(lx.slip('cosponsored', 0.0, seed=1, utterance='u'), ('cosponsored', False))
        self.assertEqual(lx.slip('stage_failed', 0.0, seed=1, utterance='u'), ('stage_failed', False))
        outcomes = {lx.slip('stage_referred', 0.4, seed=seed, utterance='u')[0] for seed in range(40)}
        self.assertEqual(outcomes, {'stage_referred', 'stage_reported'})
        for seed in range(8):
            first = lx.slip('stage_referred', 0.4, seed=seed, utterance='u')
            self.assertEqual(lx.slip('stage_referred', 0.4, seed=seed, utterance='u'), first)

    def test_registers_split_procedural_and_evaluative_vocabulary(self):
        """Chamber governs procedure, party governs evaluation. Both splits are published facts."""
        from worldmodel.agents import lexicon as lx
        senate_d = lx.register_from('senate', 'Democrat')
        senate_r = lx.register_from('senate', 'Republican')
        house_d = lx.register_from('house', 'Democrat')
        self.assertEqual(senate_d.phrase('stage_referred'), senate_r.phrase('stage_referred'))
        self.assertNotEqual(senate_d.phrase('stage_enacted'), senate_r.phrase('stage_enacted'))
        self.assertNotEqual(senate_d.phrase('stage_referred'), house_d.phrase('stage_referred'))
        self.assertEqual(senate_d.phrase('stage_enacted'), house_d.phrase('stage_enacted'))
        self.assertEqual(lx.intelligibility(senate_d, senate_d), 1.0)
        for other in (senate_r, house_d, lx.LOBBY_REGISTER):
            self.assertLess(lx.intelligibility(senate_d, other), 1.0)

    def test_a_measure_token_is_one_word_and_derivable_from_the_id(self):
        from worldmodel.agents import lexicon as lx
        self.assertEqual(lx.spoken_token('congress:bill:119-s-1241'), 'S1241')
        self.assertEqual(lx.spoken_token('congress:bill:118-hres-9'), 'HRES9')
        self.assertEqual(lx.spoken_token('bioguide:H001042', 'Mazie K. Hirono'), 'Hirono')
        self.assertEqual(lx.spoken_token('bioguide:K000367', 'Klobuchar, Amy J.'), 'Klobuchar')
        self.assertEqual(lx.kind_of_token('S1241'), 'measure')
        self.assertEqual(lx.kind_of_token('Hirono'), 'person')
        self.assertEqual(lx.say('stage_enacted', 'S1241', None, lx.BASE_REGISTER), 'S1241 became law')
        self.assertEqual(lx.say('stage_enacted', 'S1241', None, lx.BASE_REGISTER, attributed='Hirono'),
                         'Hirono told me that S1241 became law')


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
class EvidenceWeightTests(unittest.TestCase):
    """How lines of support are weighed. Arithmetic only, but it lives beside the substrate."""

    def test_one_teller_can_never_reach_the_weight_of_a_published_record(self):
        from worldmodel.agents import transmission as tr
        self.assertLess(tr.HEARSAY_CEILING, tr.DIRECT_FLOOR)
        for trust in (0.0, 0.5, 1.0, 5.0):
            for fidelity in (0.0, 0.5, 1.0):
                weight = tr.hearsay_weight(trust, fidelity, 1.0, 1.0)
                self.assertLess(weight, tr.DIRECT_FLOOR, (trust, fidelity))

    def test_chain_length_enters_through_the_upstream_confidence(self):
        from worldmodel.agents import transmission as tr
        one = tr.hearsay_weight(0.7, 0.9, 1.0, 1.0)
        two = tr.hearsay_weight(0.7, 0.9, 1.0, one)
        three = tr.hearsay_weight(0.7, 0.9, 1.0, two)
        self.assertGreater(one, two)
        self.assertGreater(two, three)
        self.assertLess(three * 3, one)

    def test_independent_tellers_combine_and_a_shared_chain_does_not(self):
        from worldmodel.agents import speech as sp
        from worldmodel.agents import transmission as tr
        alone = sp.Provenance(kind='hearsay', chain=('a',), hops=1, confidence=0.5)
        other = sp.Provenance(kind='hearsay', chain=('b',), hops=1, confidence=0.5)
        downstream = sp.Provenance(kind='hearsay', chain=('c', 'a'), hops=2, confidence=0.5)
        self.assertAlmostEqual(tr.combined_hearsay((alone,)), 0.5, places=5)
        self.assertAlmostEqual(tr.combined_hearsay((alone, other)), 0.75, places=5)
        self.assertGreater(tr.combined_hearsay((alone, other)), tr.DIRECT_FLOOR)
        # c heard it from a: one report that passed through two mouths, not two reports.
        self.assertAlmostEqual(tr.combined_hearsay((alone, downstream)), 0.5, places=5)
        self.assertLess(tr.combined_hearsay((alone, downstream)), tr.DIRECT_FLOOR)

    def test_trust_is_a_function_of_published_counts_only(self):
        from worldmodel.agents import transmission as tr
        empty = tr.Tie(a='x', b='y')
        self.assertAlmostEqual(tr.trust_from(empty), tr.TRUST_WEIGHTS['base'])
        rich = tr.Tie(a='x', b='y', shared_committees=('c1', 'c2'),
                      shared_measures=tuple('m%d' % i for i in range(40)), same_party=True)
        self.assertAlmostEqual(tr.trust_from(rich), 1.0)
        # An unpublished party contributes nothing rather than being guessed at.
        unknown = tr.Tie(a='x', b='y', shared_committees=('c1', 'c2'),
                         shared_measures=tuple('m%d' % i for i in range(40)))
        self.assertLess(tr.trust_from(unknown), tr.trust_from(rich))


# --------------------------------------------------------------------------- the fixture index


def entity(entity_id, label, entity_type, **attributes):
    return {'kind': 'entity', 'id': 'r:' + entity_id, 'entity_id': entity_id, 'entity_type': entity_type,
            'label': label, 'observed_at': '2025-01-01', 'evidence': EVIDENCE, 'attributes': attributes}


def assertion(key, subject, predicate, obj, **extra):
    return {'kind': 'assertion', 'id': 'r:' + key, 'subject': subject, 'predicate': predicate,
            'object': obj, 'observed_at': extra.pop('observed_at', '2025-01-01'),
            'evidence': EVIDENCE, **extra}


#: Six senators on two committees. The four Democrats are the chain the cascade runs down; the two
#: Republicans are there so register mismatch can be exercised; the seventh is a *House* member on
#: a committee of his own, sharing no measure, so no published edge reaches him at all.
CHAIN_MEMBERS = (('S000001', 'Alpha, Ann', 'Democrat'), ('S000002', 'Beta, Ben', 'Democrat'),
                 ('S000003', 'Gamma, Cara', 'Democrat'), ('S000004', 'Delta, Dan', 'Democrat'))
OPPOSITION = (('S000005', 'Epsilon, Eve', 'Republican'), ('S000006', 'Zeta, Zach', 'Republican'))
FIXTURE_MEMBERS = CHAIN_MEMBERS + OPPOSITION
STRANGER = ('H000009', 'Omega, Otto', 'Democrat')
#: The chain is a trusting one - two shared committees, the same party - which is what lets a
#: rumour survive three hops at all. Weaken the tie and the third hop honestly falls below the
#: relay floor instead, which is the mechanism working rather than a gap.


def fixture_records():
    """Two committees, one measure before them that only Alpha cosponsored, and one enacted."""
    records = [
        entity('congress:committee:zz01', 'Zephyr Committee', 'institution', chamber='Senate',
               committee_type='Standing'),
        entity('congress:committee:zz02', 'Second Committee', 'institution', chamber='Senate'),
        entity('congress:committee:zz99', 'Lonely Committee', 'institution', chamber='House'),
        entity('congress:bill:119-s-1241', 'S 1241 (119th): Fixture Act', 'law', origin_chamber='Senate',
               policy_area='International Affairs', measure_status='not enacted as of source update',
               latest_action={'date': '2025-04-01', 'text': 'Read twice and referred to the Zephyr Committee.'}),
        entity('congress:bill:119-s-9', 'S 9 (119th): Carried Act', 'law', origin_chamber='Senate',
               measure_status='Public Law No: 119-9', public_laws=[{'number': '119-9'}],
               latest_action={'date': '2025-03-01', 'text': 'Became Public Law No: 119-9.'}),
        entity('lda:government_entity:1', 'SENATE', 'government_agency'),
        entity('lda:filing:f1', '1st Quarter - Report 2025', 'publication'),
        assertion('lf1', 'lda:filing:f1', 'contacted_government_entity', 'lda:government_entity:1',
                  valid_from='2025-01-01'),
        assertion('ref1', 'congress:bill:119-s-1241', 'referred_to_committee', 'congress:committee:zz01',
                  valid_from='2025-04-01'),
    ]
    for index, (bioguide, label, party) in enumerate(FIXTURE_MEMBERS):
        entity_id = 'bioguide:' + bioguide
        records += [
            entity(entity_id, label, 'person'),
            entity('congress:role:%s' % bioguide, 'Senator', 'role', role_type='sen',
                   jurisdiction_code='ZZ',
                   source_term={'party': party, 'start': '2025-01-03', 'end': '2031-01-03', 'type': 'sen'}),
            assertion('role%d' % index, entity_id, 'holds_role', 'congress:role:%s' % bioguide,
                      valid_from='2025-01-03', valid_to='2031-01-03'),
            assertion('cma%d' % index, entity_id, 'committee_member', 'congress:committee:zz01'),
            assertion('cmb%d' % index, entity_id, 'committee_member', 'congress:committee:zz02'),
            assertion('cs9%d' % index, entity_id, 'cosponsored_measure', 'congress:bill:119-s-9',
                      valid_from='2025-02-02'),
        ]
    # A House member on a committee of his own: no committee, no measure and no chamber in common.
    stranger_id = 'bioguide:' + STRANGER[0]
    records += [
        entity(stranger_id, STRANGER[1], 'person'),
        entity('congress:role:%s' % STRANGER[0], 'Representative', 'role', role_type='rep',
               jurisdiction_code='ZZ-1',
               source_term={'party': STRANGER[2], 'start': '2025-01-03', 'end': '2027-01-03', 'type': 'rep'}),
        assertion('rolez', stranger_id, 'holds_role', 'congress:role:%s' % STRANGER[0],
                  valid_from='2025-01-03', valid_to='2027-01-03'),
        assertion('cmz', stranger_id, 'committee_member', 'congress:committee:zz99'),
    ]
    # Only Alpha put her name to the measure the rumour is about, so it is news to the others.
    records.append(assertion('cs0', 'bioguide:S000001', 'cosponsored_measure', 'congress:bill:119-s-1241',
                             valid_from='2025-04-02'))
    return records


def build_fixture_index(path):
    graph = Graph(path)
    graph.build_from_records([(REF, fixture_records())])
    graph.attach_resolution([], view={'view_digest': 'f' * 64, 'policy': {'links': 'fixture'}})
    return graph


NOW = dt.datetime(2026, 1, 1, tzinfo=UTC)
MEASURE = 'congress:bill:119-s-1241'
ENACTED = 'congress:bill:119-s-9'


class FixtureCase(unittest.TestCase):
    """Shared setup: one index, fresh agents per test so stores never leak between them."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = Path(cls.tmp.name) / 'index.sqlite'
        build_fixture_index(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        from worldmodel.agents.grounding import EvidenceIndex
        self.index = EvidenceIndex(self.path, cache_mb=8)
        self.addCleanup(self.index.close)

    def agent(self, bioguide, **kwargs):
        from worldmodel.agents.grounding import LEGISLATOR
        from worldmodel.agents.person import Person
        kwargs.setdefault('seed', 3)
        agent = Person.ground(self.index, 'bioguide:' + bioguide, horizon=LEGISLATOR, **kwargs)
        agent.now = NOW
        return agent

    def chain(self):
        return [self.agent(bioguide) for bioguide, _label, _party in CHAIN_MEMBERS]


# --------------------------------------------------------------------------- speech surface


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
class SpeechSurfaceTests(FixtureCase):
    """Saying and hearing: the round trip, register opacity, relay loss and the slip."""

    def names(self):
        from worldmodel.agents import speech as sp
        book = sp.Names(self.index)
        for entity_id in (MEASURE, ENACTED, 'congress:committee:zz01', 'bioguide:S000001',
                          'bioguide:S000002'):
            book.learn(entity_id)
        return book

    def test_register_comes_from_the_published_role_not_the_store_order(self):
        from worldmodel.agents import speech as sp
        democrat = sp.register_for(self.agent('S000001'), at=NOW, index=self.index)
        republican = sp.register_for(self.agent('S000005'), at=NOW, index=self.index)
        self.assertEqual((democrat.chamber, democrat.party), ('senate', 'Democrat'))
        self.assertEqual((republican.chamber, republican.party), ('senate', 'Republican'))
        self.assertTrue(democrat.basis, 'the register must name the record that chose it')

    def test_every_concept_round_trips_inside_one_register(self):
        from worldmodel.agents import speech as sp
        names = self.names()
        register = sp.register_for(self.agent('S000001'), at=NOW, index=self.index)
        tokens = {'cosponsored': ('Alpha', 'S1241'), 'sponsored': ('Alpha', 'S1241'),
                  'serves_on': ('Alpha', 'Zephyr'), 'policy_area': ('S1241', 'InternationalAffairs')}
        for concept in sp.CONCEPTS:
            subject, obj = tokens.get(concept, ('S1241', None))
            sentence = sp.say(concept, subject, obj, register)
            heard = sp.hear(sentence, register, names)
            self.assertEqual(heard.concept, concept, '%r -> %s' % (sentence, heard.note))
            self.assertTrue(heard.understood)

    def test_a_foreign_register_is_opaque_and_says_so(self):
        from worldmodel.agents import speech as sp
        democrat = sp.register_for(self.agent('S000001'), at=NOW, index=self.index)
        republican = sp.register_for(self.agent('S000005'), at=NOW, index=self.index)
        names = self.names()
        # The evaluative vocabulary splits on party, so enactment does not cross the aisle...
        sentence = sp.say('stage_enacted', 'S1241', None, republican)
        self.assertEqual(sentence, 'S1241 was signed into law')
        lost = sp.hear(sentence, democrat, names)
        self.assertFalse(lost.understood)
        self.assertIn('unfamiliar wording', lost.note)
        # ...while the procedural vocabulary splits on chamber, so two senators share it.
        procedural = sp.say('stage_referred', 'S1241', None, republican)
        self.assertEqual(sp.hear(procedural, democrat, names).concept, 'stage_referred')
        self.assertLess(sp.intelligibility(republican, democrat), 1.0)
        self.assertEqual(sp.intelligibility(democrat, democrat), 1.0)

    def test_naming_your_source_costs_you_the_committee(self):
        """Measured, not assumed: the parser hangs the adjunct on the outer ``tell`` frame."""
        from worldmodel.agents import speech as sp
        register = sp.register_for(self.agent('S000001'), at=NOW, index=self.index)
        names = self.names()
        for concept, subject, obj in (('serves_on', 'Alpha', 'Zephyr'), ('stage_referred', 'S1241', None),
                                      ('cosponsored', 'Alpha', 'S1241')):
            sentence = sp.say(concept, subject, obj, register, attributed='Beta')
            heard = sp.hear(sentence, register, names)
            self.assertEqual(heard.attributed, 'Beta', sentence)
            if concept in sp.RELAY_LOSSY_CONCEPTS:
                self.assertFalse(heard.understood, sentence)
                self.assertIn('no longer say', heard.note)
            else:
                self.assertEqual(heard.concept, concept, '%r -> %s' % (sentence, heard.note))

    def test_an_unknown_measure_number_lands_on_a_different_ref(self):
        from worldmodel.agents import speech as sp
        register = sp.register_for(self.agent('S000001'), at=NOW, index=self.index)
        empty = sp.Names(self.index)
        heard = sp.hear('S1241 became law', register, empty)
        self.assertTrue(heard.understood)
        self.assertFalse(heard.resolved)
        self.assertEqual(str(heard.subject), 'measure:S1241')
        self.assertNotEqual(str(heard.subject), MEASURE)

    def test_the_slip_is_seeded_and_goes_only_upward(self):
        from worldmodel.agents import speech as sp
        register = sp.register_for(self.agent('S000001'), at=NOW, index=self.index)
        names = self.names()
        outcomes = set()
        for seed in range(24):
            heard = sp.hear('S1241 was referred to committee', register, names, fidelity=0.3,
                            seed=seed, utterance_id='utterance:test')
            outcomes.add(heard.concept)
            twice = sp.hear('S1241 was referred to committee', register, names, fidelity=0.3,
                            seed=seed, utterance_id='utterance:test')
            self.assertEqual(twice.concept, heard.concept, 'the same seed must mishear the same way')
        self.assertEqual(outcomes, {'stage_referred', 'stage_reported'})

    def test_nothing_is_sayable_that_is_not_believed_and_relevant(self):
        from worldmodel.agents import speech as sp
        alpha = self.agent('S000001')
        everything = sp.sayable(alpha)
        self.assertTrue(everything)
        self.assertNotIn(None, [s.concept for s in everything])
        # A relevance filter that matches nothing leaves the agent with nothing to say, which is
        # an outcome and not an error.
        self.assertEqual(sp.sayable(alpha, about=('congress:bill:119-s-99999',)), [])
        for candidate in sp.sayable(alpha, about=(MEASURE,)):
            self.assertIn(MEASURE, (candidate.subject, str(candidate.object)))


# --------------------------------------------------------------------------- channels


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
class ChannelTests(FixtureCase):
    """Who can talk to whom, and on what published evidence."""

    def test_channels_and_trust_derive_from_published_edges(self):
        from worldmodel.agents import transmission as tr
        alpha, beta = self.agent('S000001'), self.agent('S000005')
        found = tr.tie(self.index, alpha, beta, at=NOW)
        self.assertEqual(found.shared_committees,
                         ('congress:committee:zz01', 'congress:committee:zz02'))
        self.assertIn(ENACTED, found.shared_measures)
        self.assertIs(found.same_party, False)
        self.assertIs(found.same_chamber, True)
        self.assertTrue(found.records, 'a tie must cite the records that made it')
        self.assertTrue(found.bounded)
        self.assertGreater(tr.trust_from(found), tr.TRUST_WEIGHTS['base'])

        links = tr.links(self.index, alpha, beta, at=NOW)
        by_name = {link.channel.name: link for link in links}
        self.assertEqual(set(by_name), {'committee', 'cosponsorship', 'lobby_contact'})
        self.assertGreaterEqual(links[0].channel.fidelity, links[-1].channel.fidelity)
        for name, link in by_name.items():
            self.assertEqual(link.channel.predicate, tr.BY_NAME[name].predicate)
        # The committee channel is about the committee *and* what is before it.
        self.assertIn(MEASURE, by_name['committee'].about)
        # The lobbying edge names a chamber, not a senator, so the channel is not attested.
        self.assertFalse(by_name['lobby_contact'].attested)
        self.assertTrue(by_name['committee'].attested)

    def test_no_published_edge_means_no_speech_and_that_is_recorded(self):
        from worldmodel.agents import transmission as tr
        alpha, stranger = self.agent('S000001'), self.agent(STRANGER[0])
        self.assertEqual(tr.links(self.index, alpha, stranger, at=NOW), ())
        step = tr.tell(alpha, stranger, index=self.index, now=NOW)
        self.assertEqual(step.outcome, 'no_channel')
        silences = alpha.store.claims(alpha.grounding.me, 'said_nothing')
        self.assertTrue(silences)
        self.assertEqual(silences[0].claim.object[1], 'no_channel')

    def test_the_chamber_channel_closes_if_the_published_label_moves(self):
        from worldmodel.agents import transmission as tr
        alpha, beta = self.agent('S000001'), self.agent('S000005')
        original = dict(tr.CHAMBER_ENTITY)
        self.addCleanup(tr.CHAMBER_ENTITY.update, original)
        tr.CHAMBER_ENTITY['senate'] = ('lda:government_entity:1', 'NOT THE SENATE')
        names = {link.channel.name for link in tr.links(self.index, alpha, beta, at=NOW)}
        self.assertNotIn('lobby_contact', names)


# --------------------------------------------------------------------------- conflict


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
class ConflictTests(FixtureCase):
    """A hearer holding two incompatible claims adjudicates them, on the record."""

    def hear_from(self, hearer, teller, stage, *, confidence, chain=None, hops=1, subject=ENACTED):
        from worldmodel.agents import transmission as tr
        tr.prepare(hearer)
        claim = tr.tc.Claim(tr.tc.Ref(subject), 'measure_stage', stage)
        return tr.receive(hearer, claim, speaker=teller, sentence='%s is %s' % (subject, stage),
                          hops=hops, chain=chain or (teller,), origin=None, confidence=confidence,
                          now=NOW)[0]

    def test_hearsay_from_one_teller_never_outranks_direct_observation(self):
        from worldmodel.agents import transmission as tr
        hearer = self.agent('S000002')
        published = hearer.store.claims(tr.tc.Ref(ENACTED), 'measure_stage')
        self.assertEqual([str(r.claim.object) for r in published], ['enacted'])
        # The strongest possible single teller: full trust, perfect channel, hop one.
        best = tr.hearsay_weight(1.0, 1.0, 1.0, 1.0)
        rumour = self.hear_from(hearer, 'bioguide:S000001', 'passed_chamber', confidence=best)
        result = tr.adjudicate(hearer.store, tr.tc.Ref(ENACTED), 'measure_stage', now=NOW,
                              me=hearer.grounding.me)
        self.assertEqual(str(result.kept.object), 'enacted')
        self.assertEqual(result.kept.kind, 'published')
        self.assertEqual([str(c.object) for c in result.dropped], ['passed_chamber'])
        # Retracted, not forgotten: the hearer can still say what it used to hold and why it stopped.
        self.assertEqual(hearer.store.claims(tr.tc.Ref(ENACTED), 'measure_stage'), published)
        dropped = hearer.store.claim(rumour)
        self.assertIsNotNone(dropped.retracted)
        self.assertIn('outweighs', dropped.retracted.reason)
        self.assertTrue(hearer.store.claims(hearer.grounding.me, 'adjudicated'))

    def test_repeated_independent_hearsay_can_win(self):
        from worldmodel.agents import transmission as tr
        hearer = self.agent('S000002')
        weight = tr.HEARSAY_CEILING
        self.hear_from(hearer, 'bioguide:S000001', 'passed_chamber', confidence=weight)
        self.hear_from(hearer, 'bioguide:S000003', 'passed_chamber', confidence=weight)
        result = tr.adjudicate(hearer.store, tr.tc.Ref(ENACTED), 'measure_stage', now=NOW,
                              me=hearer.grounding.me)
        self.assertEqual(str(result.kept.object), 'passed_chamber')
        self.assertEqual(result.kept.kind, 'hearsay')
        self.assertEqual(len(result.kept.tellers), 2)
        live = [str(r.claim.object) for r in hearer.store.claims(tr.tc.Ref(ENACTED), 'measure_stage')]
        self.assertEqual(live, ['passed_chamber'])

    def test_two_tellers_on_one_chain_do_not_corroborate_each_other(self):
        from worldmodel.agents import transmission as tr
        hearer = self.agent('S000002')
        weight = tr.HEARSAY_CEILING
        self.hear_from(hearer, 'bioguide:S000001', 'passed_chamber', confidence=weight,
                       chain=('bioguide:S000001',))
        self.hear_from(hearer, 'bioguide:S000003', 'passed_chamber', confidence=weight, hops=2,
                       chain=('bioguide:S000003', 'bioguide:S000001'))
        result = tr.adjudicate(hearer.store, tr.tc.Ref(ENACTED), 'measure_stage', now=NOW,
                              me=hearer.grounding.me)
        self.assertEqual(str(result.kept.object), 'enacted')

    def test_a_tie_leaves_both_claims_standing(self):
        from worldmodel.agents import transmission as tr
        hearer = self.agent('S000002')
        # Two rumours of equal weight about a measure the hearer has no record for.
        subject = MEASURE
        self.assertEqual(hearer.store.claims(tr.tc.Ref(subject), 'measure_stage'), [])
        self.hear_from(hearer, 'bioguide:S000001', 'reported', confidence=0.4, subject=subject)
        self.hear_from(hearer, 'bioguide:S000003', 'passed_chamber', confidence=0.4, subject=subject)
        result = tr.adjudicate(hearer.store, tr.tc.Ref(subject), 'measure_stage', now=NOW,
                               me=hearer.grounding.me)
        self.assertIsNotNone(result.unknown)
        self.assertEqual(result.unknown.reason, 'tie_within_margin')
        live = sorted(str(r.claim.object)
                      for r in hearer.store.claims(tr.tc.Ref(subject), 'measure_stage'))
        self.assertEqual(live, ['passed_chamber', 'reported'])


# --------------------------------------------------------------------------- cascades


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
class CascadeTests(FixtureCase):
    """A claim moving down a published chain, and what it costs it."""

    def test_a_three_hop_chain_is_measurably_degraded(self):
        from worldmodel.agents import transmission as tr
        people = self.chain()
        cascade = tr.propagate(people, index=self.index, subject=MEASURE, seed=5, now=NOW)
        self.assertEqual(cascade.published, 'referred')
        self.assertEqual([step.outcome for step in cascade.steps], ['integrated'] * 3)
        self.assertEqual([step.hops for step in cascade.steps], [1, 2, 3])
        confidences = [step.confidence for step in cascade.steps]
        self.assertEqual(confidences, sorted(confidences, reverse=True))
        self.assertLess(confidences[-1] * 3, confidences[0], 'three hops must cost more than two thirds')
        # The first agent read the record; the last holds hearsay three tellers deep and can say so.
        first = tr.believed(people[0], MEASURE, 'measure_stage')
        last = tr.believed(people[-1], MEASURE, 'measure_stage')
        self.assertEqual(first['holds'][0]['kind'], 'published')
        self.assertEqual(last['holds'][0]['kind'], 'hearsay')
        self.assertEqual(last['holds'][0]['hops'], 3)
        self.assertEqual(tuple(last['holds'][0]['chain']),
                         tuple(p.grounding.entity_id for p in reversed(people[:-1])))
        self.assertLess(last['holds'][0]['confidence'], first['holds'][0]['confidence'])

    def test_the_last_hearer_can_trace_back_to_the_published_record(self):
        from worldmodel.agents import transmission as tr
        people = self.chain()
        tr.propagate(people, index=self.index, subject=MEASURE, seed=5, now=NOW)
        last = people[-1]
        held = last.store.claims(tr.tc.Ref(MEASURE), 'measure_stage')
        self.assertTrue(held)
        traced = tr.trace(last, held[0].id)
        self.assertEqual(traced['kind'], 'hearsay')
        self.assertEqual(traced['hops'], 3)
        self.assertEqual(traced['origin'], 'r:' + MEASURE)
        text = '\n'.join(traced['lines'])
        for teller in traced['chain']:
            self.assertIn(teller, text)
        self.assertIn('utterance_origin', text)
        self.assertIn('heard:', text)

    def test_divergence_from_the_record_is_reported_not_corrected(self):
        from worldmodel.agents import transmission as tr
        people = self.chain()
        # A low-fidelity channel loses more, so force one: the lobbying channel is chamber-level.
        cascade = tr.propagate(people, index=self.index, subject=MEASURE, seed=5, now=NOW)
        self.assertTrue(all(step.integrated for step in cascade.steps))
        seen = [tr.divergence(person, MEASURE, 'measure_stage', index=self.index) for person in people]
        self.assertFalse(seen[0]['diverges'])
        for report in seen[1:]:
            self.assertEqual(report['published'], 'referred')
            self.assertGreaterEqual(report['distance'], 0, 'the slip only ever goes upward')
            self.assertTrue(report['provenance'][0]['chain'])
        # Whatever happened, the record is untouched and still says what it said.
        self.assertEqual(seen[-1]['published'], 'referred')

    def test_an_unsayable_or_unheard_exchange_is_recorded(self):
        from worldmodel.agents import transmission as tr
        alpha, beta = self.agent('S000001'), self.agent('S000002')
        # Nothing about a measure that does not exist is sayable.
        step = tr.tell(alpha, beta, index=self.index, now=NOW, choose=lambda candidates: None)
        self.assertEqual(step.outcome, 'nothing_sayable')
        self.assertTrue(alpha.store.claims(alpha.grounding.me, 'said_nothing'))
        # Speech in a foreign register is heard as noise, and the hearer records that.
        import worldmodel.agents.speech as sp
        original = dict(sp.PARTY_FORMS['Republican'])
        self.addCleanup(sp.PARTY_FORMS.__setitem__, 'Republican', original)
        sp.PARTY_FORMS['Republican'] = dict(original, stage_referred='{subject} got kicked upstairs')
        gamma = self.agent('S000003')
        lost = tr.tell(gamma, beta, index=self.index, now=NOW,
                       choose=lambda candidates: next((c for c in candidates
                                                       if c.subject == MEASURE), None))
        self.assertIn(lost.outcome, ('not_understood', 'nothing_sayable'))
        if lost.outcome == 'not_understood':
            self.assertTrue(beta.store.claims(beta.grounding.me, 'did_not_understand'))

    def test_a_slip_leaves_the_chain_believing_something_the_record_does_not_say(self):
        """The whole point: three tellers on, the belief is the wrong stage and says where it came from."""
        from worldmodel.agents import transmission as tr
        people = self.chain()
        cascade = tr.propagate(people, index=self.index, subject=MEASURE, seed=23, now=NOW)
        self.assertEqual(cascade.published, 'referred')
        self.assertTrue(any(step.heard is not None and step.heard.slipped for step in cascade.steps))
        last = tr.divergence(people[-1], MEASURE, 'measure_stage', index=self.index)
        self.assertTrue(last['diverges'])
        self.assertEqual(last['believed'], ['reported'])
        self.assertEqual(last['distance'], 1, 'the rumour moved one rung up the ladder')
        self.assertEqual(last['provenance'][0]['hops'], 3)
        self.assertEqual(last['provenance'][0]['origin'], 'r:' + MEASURE)

    def test_a_third_party_claim_can_be_passed_on_as_gossip(self):
        """What the record only ever says about the agent itself, a teller can say about anyone."""
        from worldmodel.agents import speech as sp
        from worldmodel.agents import transmission as tr
        alpha, beta, gamma = self.agent('S000001'), self.agent('S000002'), self.agent('S000003')

        def about_alpha(candidates):
            return next((c for c in candidates if c.concept == 'cosponsored'
                         and str(c.subject).endswith('S000001')), None)

        first = tr.tell(alpha, beta, index=self.index, now=NOW, choose=about_alpha)
        self.assertTrue(first.integrated, first.note)
        second = tr.tell(beta, gamma, index=self.index, now=NOW, choose=about_alpha)
        self.assertTrue(second.integrated, second.note)
        self.assertEqual(second.chain, ('bioguide:S000002', 'bioguide:S000001'))
        held = gamma.store.claims(tr.tc.Ref('bioguide:S000001'), 'cosponsored')
        self.assertTrue(held, 'the colleague roster of a shared committee resolves the name')
        provenance = sp.provenance_of(gamma.store, held[0])
        self.assertEqual(provenance.kind, 'hearsay')
        self.assertEqual(provenance.hops, 2)

    def test_a_rumour_with_no_record_behind_it_traces_back_to_nobody(self):
        """`explain` has to be able to say there is nothing at the end of the chain."""
        from worldmodel.agents import transmission as tr
        alpha, beta = self.agent('S000001'), self.agent('S000002')
        # An unsourced rumour reaches Alpha: a teller, but no published record behind it.
        claim = tr.tc.Claim(tr.tc.Ref(MEASURE), 'measure_stage', 'passed_chamber')
        tr.prepare(alpha)
        heard = tr.receive(alpha, claim, speaker='bioguide:S000003', sentence='S1241 passed the floor',
                           hops=1, chain=('bioguide:S000003',), origin=None, confidence=0.5, now=NOW)[0]
        traced = tr.trace(alpha, heard)
        self.assertIsNone(traced['origin'])
        self.assertIn("utterance_origin 'nobody'", '\n'.join(traced['lines']))
        # And it stays nobody when Alpha passes it on.
        step = tr.tell(alpha, beta, index=self.index, now=NOW,
                       choose=lambda candidates: next((c for c in candidates
                                                       if c.provenance.kind == 'hearsay'), None))
        self.assertTrue(step.integrated, step.note)
        self.assertIsNone(step.origin)
        onward = tr.trace(beta, step.claim_id)
        self.assertIsNone(onward['origin'])
        self.assertEqual(onward['chain'], ['bioguide:S000001', 'bioguide:S000003'])

    def test_the_cascade_is_deterministic_under_a_seed(self):
        from worldmodel.agents import transmission as tr
        runs = []
        for _ in range(2):
            people = self.chain()
            cascade = tr.propagate(people, index=self.index, subject=MEASURE, seed=11, now=NOW)
            runs.append([(step.sentence, step.outcome, step.confidence) for step in cascade.steps])
        self.assertEqual(runs[0], runs[1])


# --------------------------------------------------------------------------- real data


@unittest.skipUnless(HAVE_TC, 'grounded agents require the optional agents extra (tensorcode)')
@unittest.skipUnless(REAL_INDEX.is_file(), 'needs the unified-graph index; run `wm unify` first')
class RealNetworkSmokeTests(unittest.TestCase):
    """Propagate a claim about a real measure between real senators on a real committee."""

    @classmethod
    def setUpClass(cls):
        from worldmodel.agents.grounding import EvidenceIndex, LEGISLATOR
        from worldmodel.agents.person import Person
        cls.index = EvidenceIndex(REAL_INDEX, cache_mb=64)
        cls.when = dt.datetime(2026, 6, 1, tzinfo=UTC)
        cls.people = []
        for entity_id in REAL_CHAIN:
            agent = Person.ground(cls.index, entity_id, horizon=LEGISLATOR, seed=3)
            agent.now = cls.when
            cls.people.append(agent)

    @classmethod
    def tearDownClass(cls):
        cls.index.close()

    def test_the_committee_channel_is_a_join_over_published_edges(self):
        from worldmodel.agents import transmission as tr
        for speaker, hearer in zip(self.people, self.people[1:]):
            links = {link.channel.name: link for link in
                     tr.links(self.index, speaker, hearer, at=self.when)}
            self.assertIn('committee', links, '%s and %s share no committee' %
                          (speaker.grounding.entity_id, hearer.grounding.entity_id))
            self.assertIn(REAL_COMMITTEE, links['committee'].about)
            self.assertTrue(links['committee'].records)
            self.assertGreater(links['committee'].trust, tr.TRUST_WEIGHTS['base'])

    def test_registers_come_from_the_published_seat(self):
        from worldmodel.agents import speech as sp
        for person in self.people:
            register = sp.register_for(person, at=self.when, index=self.index)
            self.assertEqual(register.chamber, 'senate', person.grounding.entity_id)
            self.assertIn(register.party, ('Democrat', 'Republican'), person.grounding.entity_id)
            self.assertTrue(register.basis)

    def test_a_real_rumour_drifts_and_still_names_its_chain(self):
        from worldmodel.agents import transmission as tr
        cascade = tr.propagate(self.people, index=self.index, subject=REAL_MEASURE, seed=9,
                               now=self.when)
        self.assertEqual(cascade.published, 'referred')
        self.assertTrue(all(step.integrated for step in cascade.steps),
                        [step.outcome for step in cascade.steps])
        confidences = [step.confidence for step in cascade.steps]
        self.assertEqual(confidences, sorted(confidences, reverse=True))
        self.assertLess(confidences[-1] * 3, confidences[0])
        last = tr.divergence(self.people[-1], REAL_MEASURE, 'measure_stage', index=self.index)
        self.assertEqual(last['published'], 'referred')
        self.assertGreaterEqual(last['distance'], 0)
        self.assertEqual(last['provenance'][0]['hops'], 3)
        self.assertEqual(last['provenance'][0]['origin'], 'govinfo_billstatus:measure:119-s-3419')
        self.assertEqual(tuple(last['provenance'][0]['chain']),
                         tuple(reversed(REAL_CHAIN[:-1])))


if __name__ == '__main__':
    unittest.main()
