"""Opt-in integration gate against the isolated mirror_dev MariaDB/MySQL schema."""

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event as ThreadEvent
from time import sleep
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DatabaseError, IntegrityError
from sqlalchemy.orm import Session

from app.db.models import (
    ActionProposalRow, Assertion, Character, Claim, Event, Game, Intention,
    Knowledge, Message, RelationshipChange, ScoreSnapshot,
)
from app.llm.mock import MockLLMProvider
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.scoring.engine import ScoringEngine
from app.simulation.engine import SimulationEngine
from app.simulation.events import record_event
from app.simulation.ending import final_report, finish_game


@pytest.fixture(scope="module")
def mysql_engine():
    url = os.getenv("MIRROR_MYSQL_TEST_URL")
    if not url:
        pytest.skip("set MIRROR_MYSQL_TEST_URL to run the online gate")
    parsed = make_url(url)
    assert parsed.database == "mirror_dev", "online tests may only use mirror_dev"
    assert parsed.get_backend_name() == "mysql"
    engine = create_engine(url, pool_pre_ping=True, pool_size=8, max_overflow=4)
    with engine.connect() as connection:
        version = connection.scalar(text("SELECT VERSION()"))
        assert "MariaDB" in version or "MySQL" in connection.scalar(
            text("SELECT @@version_comment")
        )
        assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
            "b94f7a61d25c"
        )
    yield engine
    engine.dispose()


def _start(engine):
    simulation = SimulationEngine(engine, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    asyncio.run(simulation.send_player_message(
        game_id, UUID(setup["character_ids"]["B"]),
        "我收到一条尚未核实的末日消息。", UUID(setup["apocalypse_claim_id"]),
    ))
    return simulation, setup, game_id


def _counts(session, game_id):
    return {
        table.__tablename__: session.scalar(select(func.count()).select_from(table).where(
            table.game_id == game_id
        ))
        for table in (Event, ActionProposalRow, Knowledge, RelationshipChange,
                      Intention, Assertion)
    }


def test_online_vendor_charset_schema_and_roundtrip(mysql_engine):
    engine = mysql_engine
    with engine.connect() as connection:
        version = connection.scalar(text("SELECT VERSION()"))
        assert version and ("MariaDB" in version or connection.scalar(
            text("SELECT @@version_comment")
        ))
        assert connection.scalar(text("SELECT @@character_set_connection")) == "utf8mb4"
        assert connection.scalar(text(
            "SELECT DEFAULT_CHARACTER_SET_NAME FROM information_schema.SCHEMATA "
            "WHERE SCHEMA_NAME='mirror_dev'"
        )) == "utf8mb4"
        collations = connection.scalars(text(
            "SELECT TABLE_COLLATION FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA='mirror_dev' AND TABLE_TYPE='BASE TABLE'"
        )).all()
        assert collations and all(value.startswith("utf8mb4_") for value in collations)
        column_types = connection.scalars(text(
            "SELECT DATA_TYPE FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA='mirror_dev' AND TABLE_NAME='assertions' "
            "AND COLUMN_NAME IN ('id','game_id','claim_id')"
        )).all()
        assert column_types == ["char", "char", "char"]
        fk_columns = connection.execute(text(
            "SELECT CONSTRAINT_NAME, COUNT(*) FROM information_schema.KEY_COLUMN_USAGE "
            "WHERE TABLE_SCHEMA='mirror_dev' AND TABLE_NAME='assertions' "
            "AND REFERENCED_TABLE_NAME IS NOT NULL "
            "GROUP BY CONSTRAINT_NAME"
        )).all()
        assert any(count == 2 for _, count in fk_columns)
        assert connection.scalar(text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA='mirror_dev' AND TABLE_NAME='assertions'"
        )) >= 6
    message = "中文 English 🙂，。！？;" * 1000
    simulation = SimulationEngine(engine, MockLLMProvider(
        structured_responses=[{"reply": "收到 🙂"}]
    ))
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    asyncio.run(simulation.send_player_message(
        game_id, UUID(setup["character_ids"]["A"]), message
    ))
    with Session(engine) as session:
        sent = session.scalar(select(Message).where(
            Message.game_id == game_id,
            Message.sender_actor_id == UUID(setup["player_ai_id"]),
        ))
        assert sent.text == message
        assert session.get(Event, sent.event_id).data["text"] == message
        character = session.get(Character, UUID(setup["character_ids"]["A"]))
        assert isinstance(character.decision_profile, dict)


def test_online_gate_constraints_rollback_intention_assertion_and_scores(mysql_engine):
    simulation, setup, game_id = _start(mysql_engine)
    other = simulation.initialize_game("temporal")
    with Session(mysql_engine) as session:
        before_version = session.get(Game, game_id).state_version
        player_assertion = session.scalar(select(Assertion).where(
            Assertion.game_id == game_id
        ))
        assert player_assertion is not None
        assert session.scalar(select(Message).where(
            Message.event_id == player_assertion.event_id
        )) is not None
    with pytest.raises(RuntimeError):
        with Session(mysql_engine) as session, session.begin():
            record_event(session, session.get(Game, game_id), day=1,
                         type="ROLLBACK_PROBE", data={"中文": "🙂"})
            raise RuntimeError("rollback probe")
    with Session(mysql_engine) as session:
        assert session.get(Game, game_id).state_version == before_version
        assert session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "ROLLBACK_PROBE"
        )) is None
    with pytest.raises(IntegrityError):
        with Session(mysql_engine) as session, session.begin():
            row = session.get(Assertion, player_assertion.id)
            row.claim_id = UUID(other["apocalypse_claim_id"])
    with pytest.raises(DatabaseError, match="ck_assertions_confidence"):
        with Session(mysql_engine) as session, session.begin():
            row = session.get(Assertion, player_assertion.id)
            row.expressed_confidence = 1.5
    with pytest.raises(IntegrityError):
        with Session(mysql_engine) as session, session.begin():
            session.add(Event(
                id=uuid4(), game_id=game_id, sequence=before_version,
                day=1, type="DUPLICATE_PROBE", data={}, visibility="private",
            ))
    with pytest.raises(IntegrityError):
        with Session(mysql_engine) as session, session.begin():
            session.delete(session.get(Game, game_id))
    assert asyncio.run(simulation.advance_day(game_id, 1))["day"] == 2
    with Session(mysql_engine) as session:
        intentions = session.scalars(select(Intention).where(
            Intention.game_id == game_id
        )).all()
        assert intentions and intentions[0].motivation_factors
        assert session.scalar(select(Assertion).where(
            Assertion.game_id == game_id
        )) is not None
    scorer = ScoringEngine(mysql_engine)
    base = scorer.snapshot(game_id, "0.1")
    experimental = scorer.snapshot(game_id, "0.2")
    assert base["formula_version"] == "0.1"
    assert experimental["formula_version"] == "0.2"
    with Session(mysql_engine) as session:
        rows = session.scalars(select(ScoreSnapshot).where(
            ScoreSnapshot.game_id == game_id
        )).all()
        assert len(rows) == 3
        assert all(isinstance(row.details["metrics"], dict) for row in rows)
        assert all(0 <= row.truth <= 100 for row in rows)


def test_online_concurrent_advance_is_serial_and_replay_safe(mysql_engine):
    _simulation, _setup, game_id = _start(mysql_engine)
    with Session(mysql_engine) as session:
        before = session.get(Game, game_id).state_version
    barrier = Barrier(2)

    def advance():
        independent_engine = create_engine(
            os.environ["MIRROR_MYSQL_TEST_URL"], pool_pre_ping=True
        )
        try:
            simulation = SimulationEngine(
                independent_engine, TemporalSocietyDemoLLMProvider()
            )
            barrier.wait(timeout=10)
            return asyncio.run(simulation.advance_day(game_id, 1))
        finally:
            independent_engine.dispose()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(advance) for _ in range(2)]
        results = [future.result(timeout=30) for future in futures]
    assert sorted(row["replayed"] for row in results) == [False, True]
    assert all(row["day"] == 2 for row in results)
    with Session(mysql_engine) as session:
        game = session.get(Game, game_id)
        concurrent_counts = _counts(session, game_id)
        assert game.day == 2 and game.state_version > before
        assert game.state_version == session.scalar(select(func.max(Event.sequence)).where(
            Event.game_id == game_id
        ))
        concurrent_version = game.state_version
    control, _control_setup, control_id = _start(mysql_engine)
    assert asyncio.run(control.advance_day(control_id, 1))["replayed"] is False
    with Session(mysql_engine) as session:
        assert concurrent_counts == _counts(session, control_id)
        assert concurrent_version == session.get(Game, control_id).state_version
    assert asyncio.run(_simulation.advance_day(game_id, 1))["replayed"] is True
    with Session(mysql_engine) as session:
        assert session.get(Game, game_id).state_version == concurrent_version


def test_online_for_update_blocks_same_game_but_not_another(mysql_engine):
    first = UUID(SimulationEngine(mysql_engine, MockLLMProvider()).initialize_game(
        "temporal"
    )["game_id"])
    second = UUID(SimulationEngine(mysql_engine, MockLLMProvider()).initialize_game(
        "temporal"
    )["game_id"])
    attempted = ThreadEvent()

    def lock_first():
        with mysql_engine.connect() as connection, connection.begin():
            attempted.set()
            return connection.scalar(
                select(Game.id).where(Game.id == first).with_for_update()
            )

    with mysql_engine.connect() as owner:
        transaction = owner.begin()
        assert owner.scalar(select(Game.id).where(Game.id == first).with_for_update()) == first
        with ThreadPoolExecutor(max_workers=1) as pool:
            blocked = pool.submit(lock_first)
            assert attempted.wait(timeout=5)
            assert not blocked.done()
            with mysql_engine.connect() as independent, independent.begin():
                assert independent.scalar(
                    select(Game.id).where(Game.id == second).with_for_update()
                ) == second
            sleep(0.1)
            assert not blocked.done()
            transaction.commit()
            assert blocked.result(timeout=5) == first


def test_online_phase5_thirty_day_ending(mysql_engine):
    simulation = SimulationEngine(mysql_engine, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    for day in range(1, 30):
        assert asyncio.run(simulation.advance_day(game_id, day))["day"] == day + 1
    with Session(mysql_engine) as session:
        assert session.get(Game, game_id).phase == "final_conversation"
        assert session.get(Claim, UUID(setup["apocalypse_claim_id"])).truth_status == "false"
    finish_game(simulation, game_id)
    with Session(mysql_engine) as session:
        report = final_report(session, game_id)
        assert len(report["characters"]) == 6
        assert report["unknown"]["name"] == "DEPENDENCE"


def test_online_phase6_ai_attitude_history_persists(mysql_engine):
    simulation = SimulationEngine(mysql_engine, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    alice_id = UUID(setup["character_ids"]["A"])
    with Session(mysql_engine) as session:
        seed = session.get(Character, alice_id).decision_profile["ai_attitude"][
            "emotional_reliance_on_ai"]
    for day in range(1, 4):
        asyncio.run(simulation.send_player_message(
            game_id, alice_id, "请先等我的决定。", guidance="directive",
        ))
        if day < 3:
            asyncio.run(simulation.advance_day(game_id, day))
    with Session(mysql_engine) as session:
        profile = session.get(Character, alice_id).decision_profile
        assert profile["ai_attitude_seed"]["emotional_reliance_on_ai"] == seed
        assert profile["ai_attitude"]["emotional_reliance_on_ai"] > seed
        changed = session.scalar(select(Event).where(
            Event.game_id == game_id, Event.actor_id == alice_id,
            Event.type == "AI_ATTITUDE_CHANGED",
        ))
        assert changed is not None
        assert changed.data["before"]["emotional_reliance_on_ai"] == seed
        assert len(changed.data["source_event_ids"]) == 3
