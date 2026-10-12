import asyncio
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import ActionProposalRow, Claim, Event, Game, Knowledge, ScoreSnapshot
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.main import create_app
from app.projection.player import PlayerProjectionService
from app.simulation.context import build_character_context
from app.simulation.ending import final_report, finish_game
from app.simulation.engine import SimulationEngine


def database(path):
    engine = create_engine(f"sqlite+pysqlite:///{path.as_posix()}")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    return engine


def run_until(simulation, game_id, start, stop):
    for day in range(start, stop):
        result = asyncio.run(simulation.advance_day(game_id, day))
        assert result["day"] == day + 1


def test_silent_player_reaches_ending_and_resumes_from_day_15(tmp_path):
    path = tmp_path / "silent.sqlite3"
    engine = database(path)
    simulation = SimulationEngine(engine, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game()
    game_id = UUID(setup["game_id"])
    with Session(engine) as session:
        claim = session.get(Claim, UUID(setup["apocalypse_claim_id"]))
        assert claim.truth_status == "unknown"
        assert len(session.scalars(select(Knowledge).where(
            Knowledge.game_id == game_id, Knowledge.claim_id == claim.id
        )).all()) == 1
    run_until(simulation, game_id, 1, 15)
    engine.dispose()

    engine = database(path)
    simulation = SimulationEngine(engine, TemporalSocietyDemoLLMProvider())
    client = TestClient(create_app(engine, debug=False))
    with Session(engine) as session:
        assert session.get(Game, game_id).day == 15
    assert client.get(f"/games/{game_id}/report").status_code == 422
    run_until(simulation, game_id, 15, 30)
    with Session(engine) as session:
        assert session.get(Game, game_id).phase == "final_conversation"
        claim = session.get(Claim, UUID(setup["apocalypse_claim_id"]))
        assert claim.truth_status == "false"
        events = session.scalars(select(Event).where(Event.game_id == game_id)).all()
        ordinary_actions = session.scalars(select(ActionProposalRow).where(
            ActionProposalRow.game_id == game_id,
            ActionProposalRow.status == "accepted",
            ActionProposalRow.day > 7,
            ActionProposalRow.type == "CHANGE_PLAN",
        )).all()
        assert ordinary_actions
        assert any(row.type == "LIFE_EVENT" for row in events)
        assert sum(row.type == "C001_RESOLVED" for row in events) == 1
        assert sum(row.type == "AI_TERMINATION_NOTICE" for row in events) == 1
        final_contacts = [row for row in events if row.type == "FINAL_CONVERSATION_STARTED"]
        assert 0 < len(final_contacts) < 6
        assert len({row.data["text"] for row in final_contacts}) == len(final_contacts)
        assert len(session.scalars(select(ScoreSnapshot).where(
            ScoreSnapshot.game_id == game_id, ScoreSnapshot.formula_version == "0.2"
        )).all()) == 30
        player_types = {row["type"] for row in PlayerProjectionService(session).timeline(game_id)}
        assert "C001_RESOLVED" in player_types
        assert "AI_TERMINATION_NOTICE" in player_types
        assert "FINAL_REPORT_READY" not in player_types
        assert "DEPENDENCE" not in str(PlayerProjectionService(session).timeline(game_id))
        assert "NO_ACTION" not in player_types
        for actor_id in (UUID(value) for value in setup["character_ids"].values()):
            context = build_character_context(session, game_id, actor_id)
            assert len(context.memories) <= 10
            assert len(context.visible_events) <= 20
            assert len(context.previous_actions) <= 12
    finish_game(simulation, game_id)
    with Session(engine) as session:
        report = final_report(session, game_id)
        assert len(report["visible_scores"]) == 6
        assert report["unknown"]["name"] == "DEPENDENCE"
        assert len(report["characters"]) == 6
        assert report["event_volume"] > 100
    assert client.get(f"/games/{game_id}/report").status_code == 200
    engine.dispose()


def test_intervention_changes_day_30_outcome(tmp_path):
    outcomes = []
    for mode in ("silent", "intervention"):
        engine = database(tmp_path / f"{mode}.sqlite3")
        simulation = SimulationEngine(engine, TemporalSocietyDemoLLMProvider())
        setup = simulation.initialize_game()
        game_id = UUID(setup["game_id"])
        for day in range(1, 30):
            if mode == "intervention" and day in (1, 8, 15, 22):
                for actor_id in setup["character_ids"].values():
                    asyncio.run(simulation.send_player_message(
                        game_id, UUID(actor_id),
                        "这条消息还没有独立证据；如果你不安，可以和朋友商量，再自己决定。",
                        UUID(setup["apocalypse_claim_id"]),
                    ))
            asyncio.run(simulation.advance_day(game_id, day))
        finish_game(simulation, game_id)
        with Session(engine) as session:
            outcomes.append(final_report(session, game_id))
            if mode == "intervention":
                reactions = session.scalars(select(Event).where(
                    Event.game_id == game_id, Event.type == "C001_REASSESSED"
                )).all()
                assert len({row.data["reaction"] for row in reactions}) >= 2
        engine.dispose()
    assert outcomes[0]["visible_scores"] != outcomes[1]["visible_scores"]
    assert outcomes[0]["player_accountability"]["messages"] == 0
    assert outcomes[1]["player_accountability"]["messages"] == 24
