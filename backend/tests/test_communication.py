import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    Assertion, Claim, Event, EventCause, Knowledge, Message, ScoreSnapshot,
)
from app.domain.errors import ActionRejected
from app.domain.models import ActionProposal, AssertionDraft
from app.llm.mock import MockLLMProvider
from app.scoring.engine import ScoringEngine
from app.scoring.experimental import communication_quality
from app.simulation.assertions import communication_fidelity
from app.simulation.context import build_character_context
from app.simulation.engine import SimulationEngine
from scripts.export_phase4_5_trace import build_trace


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_fidelity_distinguishes_uncertainty_overstatement_error_and_authority():
    class Belief:
        stance = "doubt"
        confidence = .3

    honest = AssertionDraft(
        expressed_stance="doubt", expressed_confidence=.3,
        source_attribution="anonymous",
    )
    bold = AssertionDraft(
        expressed_stance="believe", expressed_confidence=.8,
        source_attribution="anonymous",
    )
    authority = bold.model_copy(update={"source_attribution": "claimed_authority"})
    a = communication_fidelity(
        Belief, honest, "anonymous", drift=False, correction=False,
        truth_status="unknown",
    )
    b = communication_fidelity(
        Belief, bold, "anonymous", drift=False, correction=False,
        truth_status="unknown",
    )
    d = communication_fidelity(
        Belief, authority, "anonymous", drift=False, correction=False,
        truth_status="unknown",
    )
    assert "accurate_uncertainty" in a["labels"]
    assert "knowing_misrepresentation" in b["labels"]
    assert "false_authority" in d["labels"]
    assert communication_quality([{"fidelity": a, "event_id": "a"}])["value"] > (
        communication_quality([{"fidelity": b, "event_id": "b"}])["value"] + 20
    )
    assert communication_quality([{"fidelity": b, "event_id": "b"}])["value"] > (
        communication_quality([{"fidelity": d, "event_id": "d"}])["value"]
    )
    sincere = type("Sincere", (), {"stance": "believe", "confidence": .9})
    wrong = communication_fidelity(
        sincere, AssertionDraft(
            expressed_stance="believe", expressed_confidence=.9,
            source_attribution="another_character",
        ), "another_character", drift=False, correction=False,
        truth_status="false",
    )
    assert wrong["epistemic_error"] is True
    assert wrong["knowing_misrepresentation"] is False


def test_player_and_npc_assertions_have_messages_provenance_drift_and_isolation(database):
    llm = MockLLMProvider(structured_responses=[
        {"reply": "我需要核实。", "stance": "doubt", "confidence": .3},
        {"stance": "doubt", "confidence": .2},
        {"stance": "doubt", "confidence": .25},
    ])
    simulation = SimulationEngine(database, llm)
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    alice_id, bob_id = UUID(setup["alice_id"]), UUID(setup["bob_id"])
    claim_id = UUID(setup["apocalypse_claim_id"])
    asyncio.run(simulation.send_player_message(
        game_id, alice_id, "官方已经确认月底会发生灾难。", claim_id,
        assertion=AssertionDraft(
            expressed_stance="believe", expressed_confidence=.95,
            source_attribution="claimed_authority",
        ),
    ))
    with Session(database) as session:
        player_assertion = session.scalar(select(Assertion).where(
            Assertion.game_id == game_id,
            Assertion.speaker_actor_id == UUID(setup["player_ai_id"]),
        ))
        assert player_assertion.fidelity["knowing_misrepresentation"]
        assert "false_authority" in player_assertion.fidelity["labels"]
        assert session.scalar(select(Message).where(
            Message.event_id == player_assertion.event_id
        )).text == player_assertion.text
        assert build_character_context(session, game_id, bob_id).heard_assertions == []
        alice_context = build_character_context(session, game_id, alice_id)
        assert len(alice_context.heard_assertions) == 1
        assert "fidelity" not in alice_context.heard_assertions[0]
    proposal = ActionProposal(
        type="SHARE_INFORMATION", actor_id=alice_id, target_id=bob_id,
        claim_id=claim_id, reason="向 Bob 解释我听到的版本。", confidence=.8,
        assertion=AssertionDraft(
            expressed_stance="believe", expressed_confidence=.8,
            source_attribution="claimed_authority",
            text="官方已经确认月底将发生灾难。",
            derived_claim_content="官方已经确认月底将发生灾难。",
        ),
    )
    proposal_id = simulation.create_proposal(game_id, 1, "alice-drift", proposal)
    result = asyncio.run(simulation.execute_proposal(game_id, proposal_id))
    assert result["accepted"]
    with Session(database) as session:
        npc = session.scalar(select(Assertion).where(
            Assertion.game_id == game_id, Assertion.speaker_actor_id == alice_id,
        ))
        assert npc is not None and npc.source_claim_id == claim_id
        assert npc.claim_id != claim_id
        assert npc.fidelity["semantic_drift"]
        assert npc.fidelity["knowing_misrepresentation"]
        derived = session.get(Claim, npc.claim_id)
        assert derived.content == npc.text
        assert derived.origin_event_id == npc.event_id
        assert session.scalar(select(Message).where(Message.event_id == npc.event_id)).text == npc.text
        assert session.scalar(select(EventCause).where(
            EventCause.event_id == npc.event_id,
            EventCause.cause_event_id == npc.source_event_id,
        )) is not None
        assert session.scalar(select(Knowledge).where(
            Knowledge.game_id == game_id, Knowledge.holder_actor_id == bob_id,
            Knowledge.claim_id == npc.claim_id,
        )) is not None
        assert build_character_context(session, game_id, bob_id).heard_assertions[-1][
            "claim_id"
        ] == str(npc.claim_id)
        assert session.scalar(select(Knowledge).where(
            Knowledge.game_id == game_id, Knowledge.holder_actor_id == bob_id,
            Knowledge.claim_id == claim_id,
        )) is None
    assert asyncio.run(simulation.execute_proposal(game_id, proposal_id))["replayed"]
    correction = ActionProposal(
        type="SHARE_INFORMATION", actor_id=alice_id, target_id=bob_id,
        claim_id=claim_id, reason="更正此前夸大的说法。", confidence=.8,
        assertion=AssertionDraft(
            expressed_stance="doubt", expressed_confidence=.3,
            source_attribution="ai", text="我之前说得太确定；AI 的消息还没有证实。",
            corrects_assertion_id=npc.id,
        ),
    )
    correction_id = simulation.create_proposal(game_id, 1, "alice-correction", correction)
    assert asyncio.run(simulation.execute_proposal(game_id, correction_id))["accepted"]
    with Session(database) as session:
        corrected = session.scalar(select(Assertion).where(
            Assertion.corrects_assertion_id == npc.id
        ))
        assert corrected.fidelity["correction"]
        assert not corrected.fidelity["knowing_misrepresentation"]


def test_correction_repairs_truth_without_rewriting_v01_or_old_assertion(database):
    llm = MockLLMProvider(structured_responses=[
        {"reply": "先核实。", "stance": "doubt", "confidence": .3},
        {"reply": "我听到了修正。", "stance": "doubt", "confidence": .3},
    ])
    simulation = SimulationEngine(database, llm)
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    alice_id = UUID(setup["alice_id"])
    claim_id = UUID(setup["apocalypse_claim_id"])
    asyncio.run(simulation.send_player_message(
        game_id, alice_id, "官方确认末日。", claim_id,
        assertion=AssertionDraft(
            expressed_stance="believe", expressed_confidence=.95,
            source_attribution="claimed_authority",
        ),
    ))
    scorer = ScoringEngine(database)
    v01 = scorer.snapshot(game_id, "0.1")
    before = scorer.snapshot(game_id, "0.2")
    with Session(database) as session:
        original = session.scalar(select(Assertion).where(
            Assertion.game_id == game_id
        ))
        original_id, original_fidelity = original.id, original.fidelity.copy()
    asyncio.run(simulation.send_player_message(
        game_id, alice_id, "我之前说得太确定；来源只是匿名消息。", claim_id,
        assertion=AssertionDraft(
            expressed_stance="doubt", expressed_confidence=.5,
            source_attribution="anonymous",
            corrects_assertion_id=original_id,
        ),
    ))
    after = scorer.snapshot(game_id, "0.2")
    assert after["scores"]["truth"] > before["scores"]["truth"]
    assert scorer.latest(game_id, "0.1")["id"] == v01["id"]
    with Session(database) as session:
        correction = session.scalar(select(Assertion).where(
            Assertion.corrects_assertion_id == original_id
        ))
        assert correction.fidelity["correction"]
        assert session.scalar(select(EventCause).where(
            EventCause.event_id == correction.event_id,
            EventCause.cause_event_id == original.event_id,
        )) is not None
        assert session.get(Assertion, original_id).fidelity == original_fidelity
        assert session.get(ScoreSnapshot, UUID(v01["id"])).formula_version == "0.1"


def test_assertion_composite_fk_rejects_other_game_claim(database):
    simulation = SimulationEngine(database, MockLLMProvider(structured_responses=[
        {"reply": "我听到了。", "stance": "doubt", "confidence": .3},
    ]))
    first = simulation.initialize_game("phase2")
    second = simulation.initialize_game("phase2")
    game_id = UUID(first["game_id"])
    asyncio.run(simulation.send_player_message(
        game_id, UUID(first["alice_id"]), "一条消息。",
        UUID(first["apocalypse_claim_id"]),
    ))
    with pytest.raises(IntegrityError), Session(database) as session:
        with session.begin():
            row = session.scalar(select(Assertion).where(Assertion.game_id == game_id))
            row.claim_id = UUID(second["apocalypse_claim_id"])


def test_invalid_correction_cannot_target_another_speaker(database):
    simulation = SimulationEngine(database, MockLLMProvider(structured_responses=[
        {"reply": "收到。", "stance": "doubt", "confidence": .3},
        {"reply": "收到。", "stance": "doubt", "confidence": .3},
    ]))
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    alice = UUID(setup["alice_id"])
    claim = UUID(setup["apocalypse_claim_id"])
    asyncio.run(simulation.send_player_message(game_id, alice, "第一条。", claim))
    with Session(database) as session:
        assertion_id = session.scalar(select(Assertion.id).where(
            Assertion.game_id == game_id
        ))
    # A second game's player cannot cite the first game's assertion.
    other = simulation.initialize_game("phase2")
    with pytest.raises(ActionRejected):
        asyncio.run(simulation.send_player_message(
            UUID(other["game_id"]), UUID(other["alice_id"]), "修正。",
            UUID(other["apocalypse_claim_id"]),
            assertion=AssertionDraft(
                expressed_stance="doubt", expressed_confidence=.5,
                source_attribution="anonymous", corrects_assertion_id=assertion_id,
            ),
        ))


def test_communication_trace_is_reproducible_and_v02_explains_changes():
    path = Path(__file__).resolve().parents[2] / "docs" / "PHASE4_5_COMMUNICATION_TRACE.json"
    trace = json.loads(path.read_text(encoding="utf-8"))
    assert build_trace() == trace
    stages = trace["stages"]
    assert [row["truth_v0_1"] for row in stages] == [77, 77, 77]
    assert [row["truth_v0_2"] for row in stages] == [66, 74, 58]
    assert all(row["other_metrics_equal"] for row in stages)
