# The agent layer

Entry point for `worldmodel/agents/`. The design contract is
[agents-design.md](agents-design.md); each area has its own record, listed below.

The catalog describes a world. This layer adds entities that *act* in it: they perceive
published records, hold beliefs with provenance, decide under constraints, remember, and
speak to one another. It runs on the [tensorcode](https://github.com/TensaCo/tensacode)
cognitive substrate, which is an **optional** dependency — the core imports without it and
every agent test skips.

```sh
pip install "worldmodel-substrate[agents]"       # pulls tensorcode from PyPI
python3 -m worldmodel agent-inspect bioguide:K000367
python3 -m worldmodel society-run --config examples/society-congress.json --ticks 6 --seed 7
```

## What is here

| Module | Holds | Needs substrate |
| --- | --- | --- |
| `affect.py` | the seven structural readings, twelve motif prototypes, asymmetric transition costs | no |
| `grounding.py` | `EvidenceIndex`, bounded perception, seeding from published records | yes |
| `person.py` | the person tick loop, appraisal, episodes, consolidation, theory of mind | yes |
| `roles.py` | roles, authority, delegation, procedures, `authorize`, the act log | yes |
| `corporate_affect.py` | viability, liquidity pressure, exposure, coupling, ascription | yes |
| `firm.py` | firms as role structures with procedure-based decisions | yes |
| `institution.py` | authority and legitimacy, instruments, ultra vires | yes |
| `lexicon.py` | sayable concepts, registers, wordings, the slip model | no |
| `speech.py` | utterance, hearing, hearsay provenance, what is sayable | yes |
| `transmission.py` | channels from published edges, trust, cascades, adjudication | yes |
| `affinity.py` | inferred informal ties from published co-membership | yes |
| `disclosure.py` | disclosure as a speech act, obligation, the belief/statement gap | yes |
| `instruments.py` | institutional instruments issued against declared power | yes |
| `society.py` | the shared clock, mixed population, tiering, delivery | yes |
| `observatory.py` | divergence, propagation, trajectories, decision structure | no |

`affect.py` and `observatory.py` deliberately import without the substrate, so the readings
and the statistics can be used anywhere.

## Three kinds of mind

The design mistake this layer avoids is one cognitive template with three sets of labels. The
ascription axes make the split principled: **agency and phenomenality dissociate**, so a firm
can be highly agentive and not experiential at all.

| | Person | Firm | Institution |
| --- | --- | --- | --- |
| Self | one | none — roles hold claims that contradict | none — roles and procedure |
| Viability | needs and survival | solvency | authority and legitimacy |
| Memory | episodic, decays, consolidates | persists; only the person scope clears | instruments lapse |
| Affect | seven readings, motif, curved space | step functions dated by filing, no motif | legitimacy erosion |
| Decides by | `choose` under constraints | a procedure across two or more roles | `authorize` against declared power |
| Speaks by | gossip, lossy, attributable | disclosure: exact, dated, audience-scoped | instruments, refused if ultra vires |
| αA / αP | high / high | 0.85 / 0.00 | high / 0.00 |

The differences are structural rather than cosmetic. Corporate arousal counts *commitment*
revisions only, so unlimited new facts move it by zero. Person integration partitions by
cognitive module; firm integration partitions by the org chart. A rumour lapses; a filing
does not.

## Grounding

An agent is bound to an entity id from the unified index. Seed claims come only from
published records, each carrying dataset, stage, version and record id as evidence. Absent
history stays `Unknown` — a promotion that finds no history invents none. What the world
published and what the agent believes are separately queryable, and their divergence is the
object of study rather than an error to remove.

Perception is bounded by position, role and relationship. A legislator tick reads about 100
index rows, forms a few hundred percepts and keeps 24.

## Reading the records

| Document | Covers |
| --- | --- |
| [agents-design.md](agents-design.md) | the contract, the three kinds, grounding, scale |
| [corporate-cognition.md](corporate-cognition.md) | roles, procedures, corporate readings, principal-agent divergence |
| [agent-communication.md](agent-communication.md) | person speech, channels, trust, cascades, drift |
| [corporate-communication.md](corporate-communication.md) | disclosure, obligation, instruments, reception |
| [agent-affinity.md](agent-affinity.md) | inferred informal ties and their evidence |
| [agent-society.md](agent-society.md) | the loop, simultaneity, tiering, the observatory |

## What this layer does not claim

**Nothing here is validated, and nothing is fitted to outcomes.** These agents are not scored
against baselines and do not go through the estimation layer. Fidelities, trust weights, slip
direction, corporate thresholds and authority defaults are **authored assumptions**, declared
as such in each module. A cascade in which a bill believed to be in committee is reported out
three hops later demonstrates that the mechanism works as designed; it is not evidence that
beliefs drift that way in the world.

**No claim that anything is experienced.** The affect readings are structural measures over an
agent's own processing. The corporate analogues are named for solvency and exposure precisely
so the slide to feeling is not available.

**Inferred ties are not published relationships.** Affinity is inference over published
co-membership, marked as inference and carrying its evidence. The measured result for inferred
name matching on this catalog — 0.4% recall at 2.3% precision, 103,211 entities wrongly merged
— is why the default resolution attaches none.

**The organizational facts are thin.** No source publishes bylaws, so authority defaults to
`UNDECLARED` and most institutional acts return `Unknown`. Roles are inferred from filing
titles, and tenure stays `Unknown` because filings publish a reporting period rather than an
appointment. The cognition is richer than the record it stands on.
