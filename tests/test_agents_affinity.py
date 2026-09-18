"""Inferred informal ties: the declared tables, the strength model, and the channel they open.

Three layers, separated the way ``test_agents_speech.py`` separates them:

* ``AffinityTableTests`` and ``StrengthModelTests`` pin the declared tables and the arithmetic in
  ``worldmodel.agents.affinity``. That half of the module is pure Python — the same relationship
  ``lexicon`` has to ``speech`` — so these run in **every** environment, with or without the
  optional ``agents`` extra;
* the fixture classes skip unless tensorcode is installed, and run against a small index built in a
  temporary directory;
* ``RealAffinitySmokeTests`` additionally skips unless this checkout has the unified-graph index on
  disk. It builds the inferred network around a real legislator and a real researcher.

Nothing here is fitted to an outcome.
"""
import dataclasses
import datetime as dt
import tempfile
import unittest
from pathlib import Path

from worldmodel.agents import affinity as af
from worldmodel.agents import tensorcode_available
from worldmodel.graph import Graph

UTC = dt.timezone.utc
HAVE_TC = tensorcode_available()
NEEDS_TC = 'inferred affinity ties require the optional agents extra (tensorcode)'
REF = {'dataset': 'fixture_affinity', 'stage': 'normalized', 'version': 'a' * 64}
EVIDENCE = [{'input': {'dataset': 'fixture_affinity', 'artifact': 'b' * 64}, 'locator': 'line:1'}]
DATA_ROOT = Path(__file__).resolve().parents[1] / 'data'
REAL_INDEX = DATA_ROOT / 'world_evidence' / 'index.sqlite'
AT = dt.datetime(2026, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------------- declared tables


class AffinityTableTests(unittest.TestCase):
    """The tables in ``affinity``: pure Python, so they are pinned in every environment."""

    def test_the_module_imports_without_the_substrate(self):
        """Half the module has to be inspectable with no extra installed, like ``lexicon``."""
        self.assertTrue(af.SIGNALS)
        self.assertTrue(af.ASSUMPTIONS)
        self.assertIsInstance(af.selectivity(4), float)

    def test_every_signal_is_well_formed_and_uniquely_named(self):
        names = [signal.name for signal in af.SIGNALS]
        self.assertEqual(len(names), len(set(names)))
        predicates = [signal.predicate for signal in af.SIGNALS]
        self.assertEqual(len(predicates), len(set(predicates)))
        for signal in af.SIGNALS:
            self.assertIn(signal.shape, ('context', 'pair'), signal.name)
            self.assertTrue(0.0 < signal.base <= 1.0, signal.name)
            self.assertGreater(signal.rows, 0, signal.name)
            self.assertTrue(signal.measured.strip(), signal.name)
            self.assertTrue(signal.note.strip(), signal.name)
            self.assertGreater(signal.per_signal, 0, signal.name)
        self.assertEqual(af.BY_NAME, {s.name: s for s in af.SIGNALS})
        self.assertEqual(set(af.CONTEXT_SIGNALS) | set(af.PAIR_SIGNALS), set(af.SIGNALS))

    def test_base_weights_are_ordered_strongest_first(self):
        """The ordering is the falsifiable claim; the table declares it in one place."""
        bases = [signal.base for signal in af.SIGNALS]
        self.assertEqual(bases, sorted(bases, reverse=True))
        self.assertGreater(af.BY_NAME['kinship'].base, af.BY_NAME['board'].base)
        self.assertGreater(af.BY_NAME['board'].base, af.BY_NAME['co_authorship'].base)
        # Same office is last, because this catalog puts 2,870 people in "United States senator".
        self.assertEqual(min(bases), af.BY_NAME['office'].base)

    def test_a_pair_signal_names_both_people_and_a_context_signal_does_not(self):
        for signal in af.PAIR_SIGNALS:
            self.assertTrue(signal.attested_pair, signal.name)
        for signal in af.CONTEXT_SIGNALS:
            self.assertFalse(signal.attested_pair, signal.name)

    def test_education_is_declared_absent_rather_than_approximated(self):
        """Requirement: do not fabricate alma maters. The absence is a table entry, not silence."""
        self.assertIn('education', af.NOT_AVAILABLE)
        note = af.NOT_AVAILABLE['education']
        self.assertIn('0.0044', note)          # the measured name-matching recall
        self.assertIn('103,211', note)         # the entities it wrongly merged
        for key, text in af.NOT_AVAILABLE.items():
            self.assertGreater(len(text), 80, key)
        # And no signal reaches for education, schooling or a name.
        words = ('educat', 'alma', 'school', 'alumn', 'name_match', 'similar')
        for signal in af.SIGNALS:
            for word in words:
                self.assertNotIn(word, signal.predicate, signal.name)

    def test_friend_of_is_never_used_as_a_value_anywhere_in_the_module(self):
        """An inferred tie is never dressed up as a published social relationship.

        Checked on the parse tree rather than the text, so the prose may *discuss* ``friend_of``
        (``NOT_AVAILABLE`` has to say that nothing publishes it) while no string constant in the
        module ever **is** it.
        """
        import ast
        tree = ast.parse(Path(af.__file__).read_text())
        literals = {node.value for node in ast.walk(tree)
                    if isinstance(node, ast.Constant) and isinstance(node.value, str)}
        for forbidden in ('friend_of', 'friends_with', 'knows'):
            self.assertNotIn(forbidden, literals)
        self.assertEqual(af.INFERRED_PREDICATE, 'affinity_with')
        for signal in af.SIGNALS:
            self.assertNotEqual(signal.predicate, 'friend_of')

    def test_every_authored_constant_is_declared_in_the_assumptions_table(self):
        """An assumption cannot be added to the code without being declared and justified."""
        declared = {assumption.name: assumption for assumption in af.ASSUMPTIONS}
        live = af.assumption_values()
        self.assertEqual(set(declared), set(live))
        for name, value in live.items():
            self.assertEqual(declared[name].value, value, name)
            self.assertTrue(declared[name].assumes.strip(), name)
            self.assertTrue(declared[name].why.strip(), name)
            # Every authored number says what observation would argue against it.
            self.assertTrue(declared[name].falsifier.strip(), name)
            self.assertIn('name', declared[name].to_json())

    def test_the_informal_channel_is_more_candid_and_less_faithful_than_the_formal_ones(self):
        """The whole point of a separate channel. The formal numbers are transmission's own."""
        formal_fidelity = {'committee': 0.90, 'cosponsorship': 0.75, 'lobby_contact': 0.50}
        formal_willingness = {'committee': 0.80, 'cosponsorship': 0.60, 'lobby_contact': 0.90}
        channel = af.AFFINITY_CHANNEL
        for name in ('committee', 'cosponsorship'):
            self.assertLess(channel.fidelity, formal_fidelity[name], name)
            self.assertGreater(channel.willingness, formal_willingness[name], name)
        self.assertFalse(channel.attested)
        self.assertEqual(channel.candour, 'unfiltered')
        for basis in (channel.fidelity_basis, channel.willingness_basis, channel.candour_basis):
            self.assertGreater(len(basis), 40)

    @unittest.skipUnless(HAVE_TC, NEEDS_TC)
    def test_the_declared_formal_numbers_match_transmission(self):
        """Pinned separately so the comparison above cannot drift from the real channels."""
        from worldmodel.agents import transmission as tr
        self.assertAlmostEqual(tr.BY_NAME['committee'].fidelity, 0.90)
        self.assertAlmostEqual(tr.BY_NAME['cosponsorship'].fidelity, 0.75)
        self.assertAlmostEqual(tr.BY_NAME['committee'].willingness, 0.80)
        self.assertAlmostEqual(tr.BY_NAME['cosponsorship'].willingness, 0.60)
        self.assertAlmostEqual(af.TRUST_FLOOR, tr.TRUST_WEIGHTS['base'])
        # and the channel really is a transmission.Channel once that module is importable
        self.assertIsInstance(af.AFFINITY_CHANNEL.as_channel(), tr.Channel)


# --------------------------------------------------------------------------- the strength model


def context(signal, members, *, context_id='ctx', records=('rec:1',), first=None, pair=False):
    return af.Context(signal=signal, predicate=af.BY_NAME[signal].predicate, kind=signal,
                      context_id=context_id, label=context_id, members=members,
                      members_seen=members, records=records, attested_pair=pair, first=first,
                      last=first)


def bare(contexts, *, duration=None):
    return af.AffinityTie(a='x:1', b='x:2', contexts=tuple(contexts), duration_days=duration)


class StrengthModelTests(unittest.TestCase):
    """Selectivity, frequency, duration and homophily. Arithmetic only; no substrate, no index."""

    def test_a_two_person_context_is_fully_selective_and_a_big_one_is_nearly_worthless(self):
        self.assertAlmostEqual(af.selectivity(2), 1.0)
        for smaller, bigger in ((2, 3), (3, 13), (13, 28), (28, 67), (67, 2870), (2870, 12671)):
            self.assertGreater(af.selectivity(smaller), af.selectivity(bigger), (smaller, bigger))
        # The published extremes in this index: "United States senator" and a two-person board.
        self.assertLess(af.selectivity(2870), 0.01)
        self.assertLess(af.selectivity(12671), af.selectivity(2870))
        self.assertGreater(af.selectivity(2) / af.selectivity(13), 6.0)

    def test_selectivity_never_leaves_the_unit_interval(self):
        for members in (0, 1, 2, 5, 500, 10 ** 6):
            value = af.selectivity(members)
            self.assertTrue(0.0 < value <= 1.0, members)

    def test_frequent_co_membership_beats_a_one_off(self):
        """Nine co-authored papers are much better evidence than one of the same size."""
        one = bare([context('co_authorship', 8, context_id='doi:1')])
        nine = bare([context('co_authorship', 8, context_id='doi:%d' % i) for i in range(9)])
        self.assertGreater(nine.strength, one.strength)
        self.assertGreater(nine.frequency, one.frequency)
        # strictly increasing, not just eventually bigger
        previous = 0.0
        for count in range(1, 10):
            value = bare([context('co_authorship', 8, context_id='doi:%d' % i)
                          for i in range(count)]).strength
            self.assertGreater(value, previous, count)
            previous = value

    def test_an_exclusive_context_beats_a_broad_one_at_the_same_frequency(self):
        tiny = bare([context('co_authorship', 2)])
        small = bare([context('co_authorship', 8)])
        broad = bare([context('co_authorship', 100)])
        self.assertGreater(tiny.strength, small.strength)
        self.assertGreater(small.strength, broad.strength)

    def test_one_exclusive_context_beats_many_very_broad_ones(self):
        """Sitting on the same 400-person body many times over is still not a friendship."""
        exclusive = bare([context('board', 2)])
        crowd = bare([context('office', 2870, context_id='pos:%d' % i) for i in range(12)])
        self.assertGreater(exclusive.strength, crowd.strength)
        self.assertLess(crowd.strength, af.CHANNEL_FLOOR)

    def test_repeats_of_one_signal_are_discounted_but_different_signals_are_not(self):
        """Twenty-five bills with the same colleague are mostly one fact; a board is another."""
        two_same = bare([context('co_authorship', 4, context_id='doi:1'),
                         context('co_authorship', 4, context_id='doi:2')])
        weights = [weight for _c, weight in two_same.weights]
        self.assertAlmostEqual(weights[1], weights[0] * af.REPEAT_DISCOUNT, places=5)
        mixed = bare([context('co_authorship', 4, context_id='doi:1'),
                      context('committee', 4, context_id='cm:1')])
        self.assertEqual({round(w, 6) for _c, w in mixed.weights},
                         {round(c.weight, 6) for c in mixed.contexts})
        self.assertGreater(mixed.strength, two_same.strength)

    def test_the_discount_is_what_keeps_two_strong_ties_apart(self):
        """Without it the noisy-OR saturates and strength stops ranking anything."""
        many = [context('cosponsorship', 6, context_id='b:%d' % i) for i in range(60)]
        undiscounted = af.combine([c.weight for c in many])
        discounted = af.co_membership_of(many)
        self.assertGreater(undiscounted, 0.999)         # saturated: no room left to rank anything
        self.assertLess(discounted, 0.95)               # still has room
        # and the saturation is what the discount undoes: a stronger pair is still distinguishable
        stronger = many + [context('board', 2, context_id='co:1')]
        self.assertGreater(af.co_membership_of(stronger), discounted)
        self.assertAlmostEqual(af.combine([c.weight for c in stronger]), undiscounted, places=3)

    def test_duration_is_a_small_bonus_and_unknown_is_exactly_neutral(self):
        self.assertAlmostEqual(af.duration_multiplier(None), 1.0)
        self.assertAlmostEqual(af.duration_multiplier(0), 1.0)
        self.assertAlmostEqual(af.duration_multiplier(af.DURATION_FULL_DAYS),
                               1.0 + af.DURATION_BONUS)
        self.assertAlmostEqual(af.duration_multiplier(10 * af.DURATION_FULL_DAYS),
                               1.0 + af.DURATION_BONUS)
        brief = bare([context('cosponsorship', 6)], duration=30)
        long = bare([context('cosponsorship', 6)], duration=4000)
        undated = bare([context('cosponsorship', 6)])
        self.assertGreater(long.strength, brief.strength)
        self.assertGreater(brief.strength, undated.strength)

    def test_homophily_is_a_bonus_only_and_is_capped(self):
        none = af.Homophily(same_party=False, same_delegation=False, same_cohort=False)
        self.assertAlmostEqual(none.multiplier, 1.0)
        party = af.Homophily(same_party=True, same_delegation=False, same_cohort=False)
        self.assertGreater(party.multiplier, 1.0)
        everything = af.Homophily(same_party=True, same_delegation=True, same_cohort=True,
                                  shared_donors=10 ** 6)
        self.assertAlmostEqual(everything.multiplier, af.HOMOPHILY_CAP)
        self.assertLessEqual(everything.multiplier, af.HOMOPHILY_CAP)

    def test_strength_is_capped_at_one_and_every_term_is_reported(self):
        value = af.AffinityTie(a='x:1', b='x:2', contexts=(context('kinship', 2, pair=True),
                                                           context('board', 2, context_id='co:1')),
                               homophily=af.Homophily(same_party=True, same_delegation=True,
                                                      same_cohort=True, shared_donors=99),
                               duration_days=9000)
        self.assertLessEqual(value.strength, 1.0)
        terms = value.terms
        for key in ('co_membership_noisy_or', 'repeat_discount', 'contexts', 'frequency',
                    'exclusivity_smallest_context', 'duration_days', 'duration_multiplier',
                    'homophily', 'strength'):
            self.assertIn(key, terms)
        self.assertEqual(terms['strength'], value.strength)

    def test_a_tie_always_says_it_is_inferred_and_never_claims_to_be_published(self):
        value = bare([context('board', 2)])
        self.assertTrue(value.inferred)
        self.assertEqual(value.method, af.METHOD)
        body = value.to_json()
        self.assertTrue(body['inferred'])
        self.assertFalse(body['is_a_published_relationship'])
        self.assertEqual(body['predicate_if_written'], 'affinity_with')

    def test_a_tie_always_carries_the_records_behind_every_context(self):
        value = bare([context('board', 2, context_id='co:1', records=('rec:a', 'rec:b')),
                      context('committee', 9, context_id='cm:1', records=('rec:c',))])
        self.assertEqual(set(value.records), {'rec:a', 'rec:b', 'rec:c'})
        for entry in value.terms['contexts']:
            self.assertTrue(entry['records'], entry)

    def test_the_basis_and_the_exclusivity_are_reported(self):
        value = bare([context('board', 2, context_id='co:1'),
                      context('committee', 40, context_id='cm:1')])
        self.assertEqual(value.basis, ('board', 'committee'))
        self.assertEqual(value.exclusivity, 2)
        self.assertEqual(value.predicates, ('committee_member', 'director_of'))

    def test_an_attested_pair_context_is_marked_and_a_shared_context_is_not(self):
        pair = bare([context('kinship', 2, pair=True)])
        shared = bare([context('committee', 9)])
        self.assertEqual(len(pair.attested_contexts), 1)
        self.assertEqual(len(shared.attested_contexts), 0)

    def test_a_bounded_member_count_is_labelled_as_a_lower_bound(self):
        value = af.Context(signal='office', predicate='holds_position', kind='office',
                           context_id='pos:1', members=500, members_seen=189,
                           members_bounded=True, records=('rec:1',))
        body = value.to_json()
        self.assertTrue(body['member_count_is_a_lower_bound'])
        self.assertEqual(body['members_read'], 189)
        self.assertTrue(body['broad'])
        self.assertIn('>=500', bare([value]).explain())


# --------------------------------------------------------------------------- the fixture index


def entity(entity_id, label, entity_type, **attributes):
    return {'kind': 'entity', 'id': 'r:' + entity_id, 'entity_id': entity_id,
            'entity_type': entity_type, 'label': label, 'observed_at': '2025-01-01',
            'evidence': EVIDENCE, 'attributes': attributes}


def assertion(key, subject, predicate, obj, **extra):
    return {'kind': 'assertion', 'id': 'r:' + key, 'subject': subject, 'predicate': predicate,
            'object': obj, 'observed_at': extra.pop('observed_at', '2025-01-01'),
            'evidence': EVIDENCE, **extra}


#: The fixture people. ``P1`` is the subject of every pairwise assertion below.
P1, P2, P3, P4, P5, P6 = ('opensanctions:P%d' % n for n in range(1, 7))
#: Two legislators for the transmission seam, and a third who shares nothing but homophily.
L1, L2, L3 = 'bioguide:Z000001', 'bioguide:Z000002', 'bioguide:Z000003'
TINY_BOARD = 'opensanctions:CO-TINY'
WIDE_OFFICE = 'opensanctions:POS-WIDE'
PAPERS = tuple('doi:10.0000/paper%d' % n for n in range(1, 4))
ONE_PAPER = 'doi:10.0000/single'
BIG_PAPER = 'doi:10.0000/big'
COMMITTEE = 'congress:committee:zz01'
MEASURE = 'congress:bill:119-s-1241'


def fixture_records():
    """Every shape the strength model has to tell apart, and nothing else.

    * ``P1``/``P2``: a two-person board **and** three four-author papers — frequent and exclusive;
    * ``P1``/``P4``: one four-author paper — the same selectivity, a twelfth of the frequency;
    * ``P1``/``P5``: one twelve-author paper — the same frequency, a third of the selectivity;
    * ``P1``/``P3``: only a 40-holder office — published support, almost no evidence;
    * ``P1``/``P6``: a published kinship edge, which names both people;
    * ``L1``/``L2``: a committee and a measure, so the formal channels are open too;
    * ``L3``: same party, same delegation, same cohort as ``L1``, and **no shared context at all**.
    """
    records = [
        entity(TINY_BOARD, 'Tiny Holdings Ltd', 'business'),
        entity(WIDE_OFFICE, 'Member of a large assembly', 'post'),
        entity(COMMITTEE, 'Zephyr Committee', 'institution', chamber='Senate'),
        entity(MEASURE, 'S 1241 (119th): Fixture Act', 'law', origin_chamber='Senate',
               policy_area='International Affairs',
               measure_status='not enacted as of source update',
               latest_action={'date': '2025-04-01',
                              'text': 'Read twice and referred to the Zephyr Committee.'}),
        assertion('ref1', MEASURE, 'referred_to_committee', COMMITTEE, valid_from='2025-04-01'),
    ]
    for entity_id, label in ((P1, 'Person One'), (P2, 'Person Two'), (P3, 'Person Three'),
                             (P4, 'Person Four'), (P5, 'Person Five'), (P6, 'Person Six')):
        records.append(entity(entity_id, label, 'person'))
    # a two-person board: maximal selectivity
    records += [assertion('d1', P1, 'director_of', TINY_BOARD),
                assertion('d2', P2, 'director_of', TINY_BOARD)]
    # three four-author papers shared by P1 and P2
    for number, doi in enumerate(PAPERS):
        records.append(entity(doi, 'Fixture Paper %d' % number, 'research_paper'))
        for slot, author in enumerate((P1, P2, 'crossref:author:filler1', 'crossref:author:filler2')):
            records.append(assertion('a%d%d' % (number, slot), author, 'authored', doi))
    # one four-author paper shared by P1 and P4: same selectivity, lower frequency
    records.append(entity(ONE_PAPER, 'Single Fixture Paper', 'research_paper'))
    for slot, author in enumerate((P1, P4, 'crossref:author:filler1', 'crossref:author:filler2')):
        records.append(assertion('s%d' % slot, author, 'authored', ONE_PAPER))
    # one twelve-author paper shared by P1 and P5: same frequency, lower selectivity
    records.append(entity(BIG_PAPER, 'Crowded Fixture Paper', 'research_paper'))
    for slot, author in enumerate([P1, P5] + ['crossref:author:big%d' % n for n in range(10)]):
        records.append(assertion('g%d' % slot, author, 'authored', BIG_PAPER))
    # a 40-holder office shared by P1 and P3, and nothing else between them
    holders = [P1, P3] + ['opensanctions:FILL%d' % n for n in range(38)]
    for slot, holder in enumerate(holders):
        records.append(assertion('h%d' % slot, holder, 'holds_position', WIDE_OFFICE))
    # published kinship: the edge names both people
    records.append(assertion('k1', P1, 'family_member_of', P6))
    # two legislators who share a committee and a measure, and a third who shares nothing
    for number, (bioguide, label) in enumerate(((L1, 'Alpha, Ann'), (L2, 'Beta, Ben'),
                                                (L3, 'Gamma, Cara'))):
        records += [
            entity(bioguide, label, 'person'),
            entity('congress:role:%s' % bioguide.split(':')[1], 'Senator', 'role', role_type='sen',
                   jurisdiction_code='ZZ',
                   source_term={'party': 'Democrat', 'start': '2025-01-03', 'end': '2031-01-03',
                                'type': 'sen'}),
            assertion('lr%d' % number, bioguide, 'holds_role',
                      'congress:role:%s' % bioguide.split(':')[1],
                      valid_from='2025-01-03', valid_to='2031-01-03'),
        ]
    for number, bioguide in enumerate((L1, L2)):
        records += [assertion('lc%d' % number, bioguide, 'committee_member', COMMITTEE),
                    assertion('lm%d' % number, bioguide, 'cosponsored_measure', MEASURE,
                              valid_from='2025-04-02')]
    return records


def build_fixture_index(path):
    graph = Graph(path)
    graph.build_from_records([(REF, fixture_records())])
    graph.attach_resolution([], view={'view_digest': 'c' * 64, 'policy': {'links': 'fixture'}})
    return graph


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
class FixtureCase(unittest.TestCase):
    """One index, shared across the class; nothing here mutates it."""

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
        self.index = EvidenceIndex(self.path)
        self.addCleanup(self.index.close)


class InferenceTests(FixtureCase):
    """What is inferred, what is refused, and what the inference cites."""

    def test_no_tie_is_emitted_without_a_published_edge_joining_the_pair(self):
        import tensorcode as tc
        answer = af.tie(self.index, P1, L3, at=AT)
        self.assertIsInstance(answer, tc.Unknown)
        self.assertEqual(answer.reason, 'no_shared_published_context')
        self.assertIn('name', answer.detail)

    def test_homophily_on_its_own_never_makes_a_tie(self):
        """L1 and L3 match on party, delegation and cohort and share no published context."""
        import tensorcode as tc
        resemblance = af.homophily(self.index, L1, L3, at=AT)
        self.assertIs(resemblance.same_party, True)
        self.assertIs(resemblance.same_delegation, True)
        self.assertIs(resemblance.same_cohort, True)
        self.assertGreater(resemblance.multiplier, 1.0)
        self.assertIsInstance(af.tie(self.index, L1, L3, at=AT), tc.Unknown)

    def test_an_entity_has_no_tie_to_itself(self):
        import tensorcode as tc
        answer = af.tie(self.index, P1, P1, at=AT)
        self.assertIsInstance(answer, tc.Unknown)
        self.assertEqual(answer.reason, 'same_entity')

    def test_every_context_of_an_emitted_tie_cites_a_published_record(self):
        for other in (P2, P3, P4, P5, P6):
            value = af.tie(self.index, P1, other, at=AT)
            self.assertFalse(af._is_unknown(value), other)
            self.assertTrue(value.records, other)
            for entry in value.contexts:
                self.assertTrue(entry.records, (other, entry.signal))
                for record in entry.records:
                    self.assertTrue(self.index.record_by_id(REF['dataset'], record) is not None
                                    or record.startswith('r:'), record)

    def test_the_strength_ordering_on_real_published_shapes(self):
        """Frequent and exclusive beats one-off, which beats broad, which beats a crowd."""
        frequent = af.tie(self.index, P1, P2, at=AT)      # board of 2 + three 4-author papers
        one_off = af.tie(self.index, P1, P4, at=AT)       # one 4-author paper
        crowded = af.tie(self.index, P1, P5, at=AT)       # one 12-author paper
        broad = af.tie(self.index, P1, P3, at=AT)         # a 40-holder office and nothing else
        self.assertGreater(frequent.strength, one_off.strength)
        self.assertGreater(one_off.strength, crowded.strength)
        self.assertGreater(crowded.strength, broad.strength)
        self.assertEqual(broad.basis, ('office',))
        self.assertLess(broad.strength, af.CHANNEL_FLOOR)

    def test_a_published_kinship_edge_is_an_attested_pair_context(self):
        value = af.tie(self.index, P1, P6, at=AT)
        self.assertEqual(value.basis, ('kinship',))
        self.assertEqual(len(value.attested_contexts), 1)
        self.assertEqual(value.contexts[0].members, 2)
        self.assertTrue(value.inferred)   # the *edge* is attested; the friendship is still inferred

    def test_the_member_count_of_a_shared_context_is_read_from_the_index(self):
        contexts, _scanned = af.shared_contexts(self.index, P1, P3)
        self.assertEqual([c.signal for c in contexts], ['office'])
        self.assertEqual(contexts[0].members, 40)
        self.assertFalse(contexts[0].members_bounded)
        small, _ = af.shared_contexts(self.index, P1, P2)
        by_signal = {c.signal: c for c in small}
        self.assertEqual(by_signal['board'].members, 2)
        self.assertEqual(by_signal['co_authorship'].members, 4)

    def test_duration_is_unknown_when_the_shared_predicate_carries_no_date(self):
        import tensorcode as tc
        undated = af.tie(self.index, P1, P2, at=AT)
        self.assertIsInstance(undated.duration_days, tc.Unknown)
        self.assertEqual(undated.duration_days.reason, 'undated_signals')
        self.assertAlmostEqual(af.duration_multiplier(af._days(undated.duration_days)), 1.0)
        dated = af.tie(self.index, L1, L2, at=AT)
        self.assertIsInstance(dated.duration_days, int)

    def test_an_unpublished_homophily_term_is_unknown_and_contributes_nothing(self):
        import tensorcode as tc
        resemblance = af.homophily(self.index, P1, P2, at=AT)
        self.assertIsInstance(resemblance.same_party, tc.Unknown)
        self.assertIsInstance(resemblance.same_delegation, tc.Unknown)
        self.assertAlmostEqual(resemblance.multiplier, 1.0)
        self.assertIsNone(resemblance.terms['same_party'])

    def test_the_seat_reader_returns_the_published_delegation_and_cohort(self):
        chamber, party, delegation, entered, record = af.seat(self.index, L1, at=AT)
        self.assertEqual((chamber, party, delegation), ('senate', 'Democrat', 'ZZ'))
        self.assertEqual(entered.year, 2025)
        self.assertTrue(record)

    def test_a_network_reports_its_bound_and_its_refusals(self):
        net = af.network(self.index, P1, top=20, also=(P3, P5, L3))
        self.assertEqual(net.entity_id, P1)
        self.assertTrue(net.ties)
        strengths = [value.strength for value in net.ties]
        self.assertEqual(strengths, sorted(strengths, reverse=True))
        order = [value.b for value in net.ties]
        # Published kinship leads, and among the co-membership ties the frequent, exclusive one does.
        self.assertEqual(order[0], P6)
        for weaker in (P4, P5, P3):
            self.assertLess(order.index(P2), order.index(weaker), weaker)
        body = net.to_json()
        self.assertIn('candidate_cap', body['bound'])
        self.assertEqual([entry['entity'] for entry in body['not_inferable']], [L3])
        self.assertEqual(body['not_inferable'][0]['reason'], 'no_shared_published_context')
        self.assertIn('NOT inferable', net.report())

    def test_a_duplicated_published_edge_counts_once(self):
        """``holds_position`` is published once per term; the same room is not evidence twice."""
        net = af.network(self.index, P1, top=8)
        for value in net.ties:
            keys = [(c.signal, c.context_id) for c in value.contexts]
            self.assertEqual(len(keys), len(set(keys)), value.b)

    def test_the_per_pair_path_and_the_network_walk_agree(self):
        net = af.network(self.index, P1, top=8)
        for value in net.ties:
            direct = af.tie(self.index, P1, value.b, at=None)
            self.assertEqual({(c.signal, c.context_id) for c in direct.contexts},
                             {(c.signal, c.context_id) for c in value.contexts}, value.b)
            self.assertAlmostEqual(direct.co_membership, value.co_membership, places=6)


class WritingTests(FixtureCase):
    """Putting an inferred tie into a store without it becoming a published claim."""

    def test_the_claim_is_affinity_with_and_its_evidence_says_inferred(self):
        import tensorcode as tc
        store = tc.Store()
        value = af.tie(self.index, P1, P2, at=AT)
        claim = af.claim(value)
        self.assertEqual(claim.predicate, 'affinity_with')
        body = af.evidence(value, now=AT)
        self.assertTrue(body.method.startswith('inferred:'))
        self.assertEqual(body.confidence.kind, 'inferred_affinity')
        claim_id, premises = af.assert_into(store, value, now=AT)
        self.assertTrue(claim_id)
        self.assertTrue(premises)
        from tensorcode import cognition
        lines = '\n'.join(cognition.explain(store, claim_id, depth=4))
        self.assertIn('affinity_with', lines)
        self.assertIn('inferred:%s' % af.METHOD, lines)
        self.assertIn('is_published_relationship', lines)
        self.assertIn(value.records[0], lines)

    def test_a_tie_that_claims_not_to_be_inferred_is_refused(self):
        import tensorcode as tc
        store = tc.Store()
        value = af.tie(self.index, P1, P2, at=AT)
        with self.assertRaises(ValueError):
            af.assert_into(store, dataclasses.replace(value, inferred=False), now=AT)


class ChannelTests(FixtureCase):
    """The seam onto ``transmission``: a channel it can use, with nothing in it edited."""

    def person(self, entity_id):
        from worldmodel.agents.person import Person
        agent = Person.ground(self.index, entity_id)
        agent.now = AT
        return agent

    def test_a_strong_tie_opens_a_link_transmission_can_use(self):
        from worldmodel.agents import transmission as tr
        link = af.link_for(self.index, P1, P2, at=AT)
        self.assertIsInstance(link, tr.Link)
        self.assertEqual(link.channel.name, 'affinity')
        self.assertFalse(link.attested)
        self.assertAlmostEqual(link.fidelity, af.AFFINITY_CHANNEL.fidelity)
        self.assertGreaterEqual(link.trust, af.TRUST_FLOOR)

    def test_the_channel_carries_no_relevance_filter_and_that_is_the_candour(self):
        link = af.link_for(self.index, P1, P2, at=AT)
        self.assertEqual(link.about, ())
        # speech.sayable reads an empty ``about`` as "no filter", which is the whole mechanism.
        from worldmodel.agents import speech as sp
        agent = self.person(L1)
        formal = [candidate for candidate in af.channels(self.index, self.person(L1),
                                                         self.person(L2), at=AT)
                  if candidate.channel.name == 'committee']
        self.assertTrue(formal)
        informal = af.link_for(self.index, L1, L2, at=AT)
        self.assertGreaterEqual(len(sp.sayable(agent, about=informal.about)),
                                len(sp.sayable(agent, about=formal[0].about)))

    def test_a_weak_tie_is_refused_a_channel_with_the_reason_recorded(self):
        import tensorcode as tc
        link = af.link_for(self.index, P1, P3, at=AT)
        self.assertIsInstance(link, tc.Unknown)
        self.assertEqual(link.reason, 'tie_too_weak_for_a_channel')
        self.assertIn('office', link.detail)
        self.assertIn('0.20', link.detail)

    def test_no_shared_context_means_no_channel_at_all(self):
        import tensorcode as tc
        link = af.link_for(self.index, L1, L3, at=AT)
        self.assertIsInstance(link, tc.Unknown)
        self.assertEqual(link.reason, 'no_shared_published_context')

    def test_channels_puts_the_formal_ones_first(self):
        links = af.channels(self.index, self.person(L1), self.person(L2), at=AT)
        names = [link.channel.name for link in links]
        self.assertIn('affinity', names)
        self.assertIn('committee', names)
        self.assertLess(names.index('committee'), names.index('affinity'))
        fidelities = [link.channel.fidelity for link in links]
        self.assertEqual(fidelities, sorted(fidelities, reverse=True))

    def test_gossip_goes_through_transmission_and_is_recorded_as_hearsay(self):
        from worldmodel.agents import transmission as tr
        speaker, hearer = self.person(L1), self.person(L2)
        result = af.gossip(speaker, hearer, index=self.index, now=AT, seed=1)
        self.assertIsInstance(result, tr.Transmission)
        self.assertIn(result.outcome, tr.OUTCOMES)
        self.assertEqual(result.channel, 'affinity')
        if result.claim_id:
            trace = tr.trace(hearer, result.claim_id)
            self.assertEqual(trace['kind'], 'hearsay')
            self.assertTrue(any('heard:affinity' in line for line in trace['lines']))

    def test_gossip_without_a_channel_returns_the_refusal_rather_than_speaking(self):
        import tensorcode as tc
        result = af.gossip(self.person(L1), self.person(L3), index=self.index, now=AT)
        self.assertIsInstance(result, tc.Unknown)


# --------------------------------------------------------------------------- real data


@unittest.skipUnless(HAVE_TC, NEEDS_TC)
@unittest.skipUnless(REAL_INDEX.is_file(), 'needs the unified-graph index on disk')
class RealAffinitySmokeTests(unittest.TestCase):
    """The inferred network around real people. Asserts the mechanism, not these particular ties."""

    @classmethod
    def setUpClass(cls):
        from worldmodel.agents.grounding import EvidenceIndex
        cls.index = EvidenceIndex()

    @classmethod
    def tearDownClass(cls):
        cls.index.close()

    def test_a_real_legislator_has_an_inferred_network_with_records_behind_every_tie(self):
        net = af.network(self.index, af.WORKED_EXAMPLE['legislator'], top=6, at=AT)
        self.assertTrue(net.ties)
        for value in net.ties:
            self.assertTrue(value.inferred)
            self.assertTrue(value.records, value.b)
            self.assertTrue(value.basis, value.b)
            for entry in value.contexts:
                self.assertTrue(entry.records, (value.b, entry.signal))
        strengths = [value.strength for value in net.ties]
        self.assertEqual(strengths, sorted(strengths, reverse=True))

    def test_a_shared_committee_beats_the_shared_office_on_the_real_index(self):
        """"United States senator" has 2,870 published holders; a subcommittee has ten."""
        panel = af.tie(self.index, af.WORKED_EXAMPLE['legislator'],
                       af.WORKED_EXAMPLE['judiciary_colleague'], at=AT)
        office = af.tie(self.index, af.WORKED_EXAMPLE['legislator'],
                        af.WORKED_EXAMPLE['broad_context_only'], at=AT)
        self.assertIn('committee', panel.basis)
        self.assertEqual(office.basis, ('office',))
        self.assertGreater(panel.strength, office.strength)
        self.assertGreater(office.homophily.multiplier, 1.0)     # homophily is there
        self.assertLess(office.strength, af.CHANNEL_FLOOR)       # and it is not enough
        self.assertTrue(office.contexts[0].members_bounded)
        self.assertGreaterEqual(office.contexts[0].members, af.MEMBER_CAP)

    def test_frequent_real_co_authorship_beats_the_office_and_carries_its_dois(self):
        value = af.tie(self.index, af.WORKED_EXAMPLE['researcher'],
                       af.WORKED_EXAMPLE['co_author'], at=AT)
        self.assertEqual(value.basis, ('co_authorship',))
        self.assertGreater(value.frequency, 5)
        self.assertGreater(value.strength, af.CHANNEL_FLOOR)
        for entry in value.contexts:
            self.assertTrue(entry.context_id.startswith('doi:'), entry.context_id)
            self.assertTrue(entry.records)

    def test_a_real_board_interlock_with_published_kinship_is_near_the_maximum(self):
        value = af.tie(self.index, af.WORKED_EXAMPLE['director'],
                       af.WORKED_EXAMPLE['co_director'], at=AT)
        self.assertIn('board', value.basis)
        self.assertIn('kinship', value.basis)
        self.assertGreater(value.strength, 0.9)
        self.assertEqual(value.exclusivity, 2)
        self.assertTrue(value.attested_contexts)

    def test_a_legislator_and_a_researcher_are_not_joinable_and_it_says_why(self):
        import tensorcode as tc
        answer = af.tie(self.index, af.WORKED_EXAMPLE['legislator'],
                        af.WORKED_EXAMPLE['researcher'], at=AT)
        self.assertIsInstance(answer, tc.Unknown)
        self.assertEqual(answer.reason, 'no_shared_published_context')

    def test_the_two_research_namespaces_are_not_bridged_and_are_not_name_matched(self):
        """The measured reason ``NOT_AVAILABLE`` gives, checked against the index."""
        import tensorcode as tc
        self.assertEqual(self.index.cluster(af.WORKED_EXAMPLE['unjoinable_researcher']),
                         (af.WORKED_EXAMPLE['unjoinable_researcher'],))
        self.assertEqual(self.index.cluster(af.WORKED_EXAMPLE['researcher']),
                         (af.WORKED_EXAMPLE['researcher'],))
        self.assertIsInstance(af.tie(self.index, af.WORKED_EXAMPLE['researcher'],
                                     af.WORKED_EXAMPLE['unjoinable_researcher'], at=AT),
                             tc.Unknown)

    def test_the_catalog_publishes_no_education_predicate_at_all(self):
        """The claim in ``NOT_AVAILABLE['education']``, asserted against the real index."""
        connection = self.index._connection       # read-only; the index exposes no predicate list
        rows = connection.execute(
            "SELECT DISTINCT predicate FROM edges WHERE predicate LIKE '%educat%' "
            "OR predicate LIKE '%alma%' OR predicate LIKE '%school%' OR predicate LIKE '%alumn%' "
            "OR predicate LIKE '%degree%' OR predicate LIKE '%attend%'").fetchall()
        self.assertEqual([row[0] for row in rows], [])

    def test_the_co_membership_predicates_are_undated_except_cosponsorship(self):
        """The measured basis for the duration term being small and usually Unknown."""
        connection = self.index._connection
        for signal in af.SIGNALS:
            row = connection.execute(
                'SELECT COUNT(*) FROM edges WHERE predicate=? AND valid_from IS NOT NULL',
                (signal.predicate,)).fetchone()
            if signal.name == 'cosponsorship':
                self.assertGreater(row[0], 0, signal.name)
            else:
                self.assertEqual(row[0], 0, signal.name)

    def test_an_informal_channel_opens_between_two_real_legislators(self):
        from worldmodel.agents import transmission as tr
        from worldmodel.agents.person import Person
        speaker = Person.ground(self.index, af.WORKED_EXAMPLE['legislator'])
        hearer = Person.ground(self.index, af.WORKED_EXAMPLE['judiciary_colleague'])
        speaker.now = hearer.now = AT
        links = af.channels(self.index, speaker, hearer, at=AT)
        names = [link.channel.name for link in links]
        self.assertIn('affinity', names)
        self.assertLess(names.index('committee'), names.index('affinity'))
        result = af.gossip(speaker, hearer, index=self.index, now=AT, seed=2)
        self.assertIsInstance(result, tr.Transmission)
        self.assertEqual(result.channel, 'affinity')
        self.assertIn(result.outcome, tr.OUTCOMES)

    def test_the_informal_channel_can_carry_more_than_the_formal_one(self):
        """Candour, measured: the same speaker, two channels, two sayable sets."""
        from worldmodel.agents import speech as sp
        from worldmodel.agents.person import Person
        speaker = Person.ground(self.index, af.WORKED_EXAMPLE['legislator'])
        hearer = Person.ground(self.index, af.WORKED_EXAMPLE['judiciary_colleague'])
        speaker.now = hearer.now = AT
        links = {link.channel.name: link for link in af.channels(self.index, speaker, hearer, at=AT)}
        self.assertIn('committee', links)
        self.assertIn('affinity', links)
        formal = len(sp.sayable(speaker, about=links['committee'].about))
        informal = len(sp.sayable(speaker, about=links['affinity'].about))
        self.assertGreater(informal, formal)


if __name__ == '__main__':
    unittest.main()
