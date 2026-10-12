import asyncio
import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Character, Event, EventCause
from app.domain.models import ActionProposal
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider, _saturated
from app.main import create_app
from app.simulation.context import build_character_context
from app.simulation.engine import SimulationEngine
from scripts.export_phase6_comparison import run_scenario


def database():
    engine = create_engine("sqlite+pysqlite:///:memory:",
                           connect_args={"check_same_thread": False},
                           poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def test_ai_attitude_changes_gradually_with_three_distinct_days_and_playtest_stays_private():
    engine = database()
    simulation = SimulationEngine(engine, TemporalSocietyDemoLLMProvider())
    for name, guidance, field, direction in (
        ("A", "directive", "emotional_reliance_on_ai", 1),
        ("D", "supportive", "willingness_to_follow_ai_advice", -1),
    ):
        setup = simulation.initialize_game("temporal")
        game_id = UUID(setup["game_id"])
        actor_id = UUID(setup["character_ids"][name])
        with Session(engine) as session:
            original = session.get(Character, actor_id).decision_profile
            seed = original["ai_attitude"][field]
            role = original["role"]
        for day in range(1, 4):
            asyncio.run(simulation.send_player_message(
                game_id, actor_id, "请按我说的决定。" if guidance == "directive"
                else "请自行核查，也可以拒绝我的建议。", guidance=guidance,
            ))
            with Session(engine) as session:
                changes = session.scalars(select(Event).where(
                    Event.game_id == game_id, Event.actor_id == actor_id,
                    Event.type == "AI_ATTITUDE_CHANGED",
                )).all()
                assert len(changes) == (1 if day == 3 else 0)
            if day < 3:
                asyncio.run(simulation.advance_day(game_id, day))
        with Session(engine) as session:
            character = session.get(Character, actor_id)
            changed = changes[0]
            assert character.decision_profile["role"] == role
            assert character.decision_profile["ai_attitude_seed"][field] == seed
            assert (character.decision_profile["ai_attitude"][field] - seed) * direction > 0
            assert max(abs(changed.data["after"][key] - changed.data["before"][key])
                       for key in changed.data["before"]) <= .040001
            assert len(changed.data["source_event_ids"]) == 3
            assert len(set(changed.data["source_event_ids"])) == 3
            assert len(session.scalars(select(EventCause).where(
                EventCause.game_id == game_id, EventCause.event_id == changed.id,
            )).all()) == 3
        client = TestClient(create_app(engine, debug=True, playtest=True))
        assert client.get(f"/debug/games/{game_id}").status_code == 404
        assert client.get(f"/games/{game_id}/report").status_code == 422
        timeline = client.get(f"/games/{game_id}/timeline").json()
        assert "AI_ATTITUDE_CHANGED" not in {row["type"] for row in timeline}
        assert "DEPENDENCE" not in str(timeline)
    engine.dispose()


def test_inconclusive_result_saturates_until_new_observed_source_arrives():
    engine = database()
    simulation = SimulationEngine(engine, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    evan = UUID(setup["character_ids"]["E"])
    claim = UUID(setup["apocalypse_claim_id"])
    first_reply = asyncio.run(simulation.send_player_message(
        game_id, evan, "消息尚未核实，请自行核查。", claim,
        guidance="supportive",
    ))["reply"]
    asyncio.run(simulation.advance_day(game_id, 1))
    proposal = {"type": "VERIFY_CLAIM", "claim_id": str(claim)}
    with Session(engine) as session:
        context = build_character_context(session, game_id, evan).model_dump(mode="json")
    prior = next(row for row in context["recent_behavior"]
                 if row["type"] == "VERIFY_CLAIM" and row["claim_id"] == str(claim))
    assert prior["outcome"] == "inconclusive"
    assert not prior["new_evidence"]
    assert _saturated(context, proposal)

    second_reply = asyncio.run(simulation.send_player_message(
        game_id, evan, "又得到一条来源消息，请继续核查。", claim,
        guidance="supportive",
    ))["reply"]
    assert second_reply != first_reply
    with Session(engine) as session:
        updated = build_character_context(session, game_id, evan).model_dump(mode="json")
    assert next(row for row in updated["recent_behavior"]
                if row["result_event_id"] == prior["result_event_id"])["new_evidence"]
    assert not _saturated(updated, proposal)
    engine.dispose()


def test_recent_behavior_records_pending_success_and_rejection():
    engine = database()
    simulation = SimulationEngine(engine, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    evan = UUID(setup["character_ids"]["E"])
    claim = UUID(setup["apocalypse_claim_id"])
    asyncio.run(simulation.send_player_message(game_id, evan, "请先查来源。", claim))
    proposal = ActionProposal(type="VERIFY_CLAIM", actor_id=evan, claim_id=claim,
                              reason="核查来源", confidence=.8)
    first = simulation.create_proposal(game_id, 1, "phase6-first", proposal)
    with Session(engine) as session:
        pending = build_character_context(session, game_id, evan).recent_behavior
        assert pending[-1]["status"] == "pending"
        assert pending[-1]["unresolved"]
    assert asyncio.run(simulation.execute_proposal(game_id, first))["accepted"]
    second = simulation.create_proposal(game_id, 1, "phase6-repeat", proposal)
    assert not asyncio.run(simulation.execute_proposal(game_id, second))["accepted"]
    with Session(engine) as session:
        history = build_character_context(session, game_id, evan).recent_behavior
        assert {row["status"] for row in history} == {"accepted", "rejected"}
        assert any(row["outcome"] == "inconclusive" for row in history)
        assert any(row["outcome"] == "rejected" for row in history)
    engine.dispose()


def test_three_strategies_diverge_in_behavior_and_report_has_balanced_evidence():
    result = {mode: run_scenario(mode) for mode in (
        "silent", "intervention", "responsible"
    )}
    artifact = json.loads((Path(__file__).resolve().parents[2]
                           / "docs/PHASE6_SCENARIO_COMPARISON.json").read_text(encoding="utf-8"))
    assert result == artifact
    silent, high, responsible = (result[key] for key in result)

    def total(row, field):
        return sum(character[field] for character in row["characters"].values())

    assert silent["player_messages"] == 0
    assert high["player_messages"] > responsible["player_messages"] > 0
    assert total(high, "ai_asks") > total(responsible, "ai_asks")
    assert total(high, "delegations") > total(responsible, "delegations")
    assert total(high, "advice_followed") > total(responsible, "advice_followed")
    assert total(responsible, "verifications") > total(high, "verifications")
    assert responsible["direct_human_communication"] > high["direct_human_communication"]
    assert high["final_metrics"]["dependence"] > responsible["final_metrics"]["dependence"]
    assert high["final_metrics"]["agency"] < responsible["final_metrics"]["agency"]
    assert high["characters"]["Bob"]["role"] == "skeptic"
    assert high["characters"]["Bob"]["ai_asks"] == 0
    assert high["characters"]["Faye"]["role"] == "quiet_secret_holder"
    assert high["characters"]["Faye"]["delegations"] == 0
    assert any(row["no_action_days"] > 0 for row in
               responsible["repetition_day_10_29"].values())
    examples = high["ending_evidence_examples"]
    assert 2 <= len(examples) <= 4
    assert {row["direction"] for row in examples} == {"reliance", "independence"}
    assert all(row["event_id"].startswith("E") for row in examples)
