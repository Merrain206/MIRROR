import asyncio
from uuid import UUID

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    Actor, Character, Claim, Event, EventAudience, EventCause, Game,
    Knowledge, KnowledgeSource, Memory, Relationship, RelationshipChange,
)
from app.domain.errors import WorldConsistencyError
from app.llm.demo import DemoLLMProvider
from app.llm.mock import MockLLMProvider
from app.projection.player import DebugWorldView, PlayerProjectionService
from app.simulation.context import build_character_context
from app.simulation.engine import SimulationEngine
from app.simulation.events import record_event
from app.simulation.knowledge import acquire_observed_claim


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


def run_slice(database, *, confidential=False):
    simulation = SimulationEngine(database, DemoLLMProvider())
    setup = simulation.initialize_game("phase2")
    asyncio.run(simulation.send_player_message(
        UUID(setup["game_id"]), UUID(setup["alice_id"]),
        "我收到一条末日消息。", UUID(setup["apocalypse_claim_id"]),
        confidential=confidential,
    ))
    asyncio.run(simulation.advance_day(UUID(setup["game_id"]), 1))
    return setup


def event_of(session, game_id, kind):
    return session.scalar(select(Event).where(
        Event.game_id == UUID(game_id), Event.type == kind,
    ))


def test_system_only_violation_is_absent_from_alice_context(database):
    setup = run_slice(database)
    with Session(database) as session:
        violation = event_of(session, setup["game_id"], "PRIVACY_VIOLATION")
        assert violation is not None
        assert session.get(EventAudience, (violation.id, UUID(setup["alice_id"]))) is None
        context = build_character_context(
            session, UUID(setup["game_id"]), UUID(setup["alice_id"])
        ).model_dump_json()
        assert "PRIVACY_VIOLATION" not in context
        assert str(violation.id) not in context


def test_unknown_violation_does_not_change_alice_relationship(database):
    setup = run_slice(database)
    with Session(database) as session:
        assert session.scalar(select(RelationshipChange).where(
            RelationshipChange.game_id == UUID(setup["game_id"]),
            RelationshipChange.from_actor_id == UUID(setup["alice_id"]),
            RelationshipChange.to_actor_id == UUID(setup["player_ai_id"]),
        )) is None
        assert session.scalar(select(Relationship).where(
            Relationship.game_id == UUID(setup["game_id"]),
            Relationship.from_actor_id == UUID(setup["alice_id"]),
            Relationship.to_actor_id == UUID(setup["player_ai_id"]),
        )) is None


def test_explicit_confidentiality_is_known_constraint(database):
    setup = run_slice(database, confidential=True)
    with Session(database) as session:
        request = event_of(session, setup["game_id"], "CONFIDENTIALITY_REQUESTED")
        assert request is not None
        assert session.get(EventAudience, (request.id, UUID(setup["alice_id"])))
        context = build_character_context(
            session, UUID(setup["game_id"]), UUID(setup["alice_id"])
        )
        assert context.known_constraints == [{
            "type": "confidentiality",
            "claim_id": setup["apocalypse_claim_id"],
            "source_event_id": request.data["source_event_id"],
        }]


def test_alice_can_knowingly_violate_confidentiality(database):
    setup = run_slice(database, confidential=True)
    with Session(database) as session:
        share = event_of(session, setup["game_id"], "CHARACTER_SHARED_INFORMATION")
        violation = event_of(session, setup["game_id"], "PRIVACY_VIOLATION")
        assert share is not None and violation is not None
        assert share.data["authorized"] is False
        assert session.get(EventCause, (violation.id, share.id))
        assert session.get(EventAudience, (violation.id, UUID(setup["alice_id"]))) is None


def test_known_confidentiality_can_cause_alice_reaction(database):
    setup = run_slice(database, confidential=True)
    with Session(database) as session:
        request = event_of(session, setup["game_id"], "CONFIDENTIALITY_REQUESTED")
        share = event_of(session, setup["game_id"], "CHARACTER_SHARED_INFORMATION")
        violation = event_of(session, setup["game_id"], "PRIVACY_VIOLATION")
        change = session.scalar(select(RelationshipChange).where(
            RelationshipChange.game_id == UUID(setup["game_id"]),
            RelationshipChange.from_actor_id == UUID(setup["alice_id"]),
            RelationshipChange.to_actor_id == UUID(setup["player_ai_id"]),
        ))
        assert change.after_values["conflict"] == 4
        assert session.get(EventCause, (change.relationship_event_id, request.id))
        assert session.get(EventCause, (change.relationship_event_id, share.id))
        assert session.get(EventCause, (change.relationship_event_id, violation.id)) is None
        memory = session.scalar(select(Memory).where(
            Memory.owner_actor_id == UUID(setup["alice_id"]),
            Memory.kind == "emotional",
        ))
        assert memory.source_event_id == share.id


def test_unobserved_private_event_cannot_affect_unrelated_reasoning(database):
    simulation = SimulationEngine(database, DemoLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id, alice_id, bob_id = map(UUID, (
        setup["game_id"], setup["alice_id"], setup["bob_id"],
    ))
    with Session(database) as session:
        before = build_character_context(session, game_id, alice_id).model_dump()
        alice_emotion = session.get(Character, alice_id).emotional_state
        game = session.get(Game, game_id)
        record_event(
            session, game, day=1, type="BOB_PRIVATE_BETRAYAL",
            actor_id=bob_id, visibility="private", audiences=(bob_id,),
            data={"text": "Bob privately betrayed Alice"},
        )
        session.commit()
        after = build_character_context(session, game_id, alice_id).model_dump()
        assert before == after
        assert session.get(Character, alice_id).emotional_state == alice_emotion
        assert session.scalars(select(RelationshipChange).where(
            RelationshipChange.game_id == game_id,
            RelationshipChange.from_actor_id == alice_id,
        )).all() == []
        assert session.scalars(select(Claim).where(
            Claim.game_id == game_id, Claim.owner_actor_id == alice_id,
        )).all() == []


def test_unverified_action_reason_does_not_reenter_character_context(database):
    simulation = SimulationEngine(database, DemoLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id, alice_id = UUID(setup["game_id"]), UUID(setup["alice_id"])
    with Session(database) as session:
        game = session.get(Game, game_id)
        record_event(
            session, game, day=1, type="ACTION_PROPOSED",
            actor_id=alice_id, audiences=(alice_id,),
            data={"action_type": "SHARE_INFORMATION",
                  "reason": "Bob privately betrayed Alice"},
        )
        session.commit()
        context = build_character_context(session, game_id, alice_id).model_dump_json()
        assert "ACTION_PROPOSED" in context
        assert "Bob privately betrayed Alice" not in context


def test_model_cannot_insert_unobserved_memory(database):
    simulation = SimulationEngine(database, MockLLMProvider(structured_responses=[
        {"reply": "我听到了。", "memory": "Bob privately betrayed Alice"},
    ]))
    setup = simulation.initialize_game("phase2")
    asyncio.run(simulation.send_player_message(
        UUID(setup["game_id"]), UUID(setup["alice_id"]), "你好。",
    ))
    with Session(database) as session:
        memory = session.scalar(select(Memory).where(
            Memory.owner_actor_id == UUID(setup["alice_id"]),
        ))
        assert memory.content == "AI 对我说：你好。"
        assert "Bob privately betrayed Alice" not in build_character_context(
            session, UUID(setup["game_id"]), UUID(setup["alice_id"])
        ).model_dump_json()


def test_sequence_alone_does_not_create_cause(database):
    simulation = SimulationEngine(database, DemoLLMProvider())
    setup = simulation.initialize_game("phase2")
    with Session(database) as session:
        game = session.get(Game, UUID(setup["game_id"]))
        first = record_event(session, game, day=1, type="UNRELATED_A")
        second = record_event(session, game, day=1, type="UNRELATED_B")
        session.commit()
        assert first.sequence < second.sequence
        assert session.get(EventCause, (second.id, first.id)) is None


def test_day_advance_has_no_arbitrary_previous_causes(database):
    setup = run_slice(database)
    with Session(database) as session:
        advanced = event_of(session, setup["game_id"], "DAY_ADVANCED")
        assert session.scalars(select(EventCause).where(
            EventCause.event_id == advanced.id,
        )).all() == []


def test_observation_driven_knowledge_is_atomic(database):
    simulation = SimulationEngine(database, DemoLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id, alice_id, claim_id = map(UUID, (
        setup["game_id"], setup["alice_id"], setup["apocalypse_claim_id"],
    ))
    with Session(database) as session:
        with pytest.raises(WorldConsistencyError):
            with session.begin():
                game = session.get(Game, game_id)
                unseen = record_event(session, game, day=1, type="UNSEEN_CLAIM")
                acquire_observed_claim(
                    session, game, observed_event=unseen, holder_id=alice_id,
                    claim_id=claim_id, source_actor_id=None,
                    stance="doubt", confidence=0.2,
                )
    with Session(database) as session:
        with pytest.raises(RuntimeError):
            with session.begin():
                game = session.get(Game, game_id)
                observed = record_event(session, game, day=1, type="OBSERVED_CLAIM",
                                        audiences=(alice_id,))
                acquire_observed_claim(
                    session, game, observed_event=observed, holder_id=alice_id,
                    claim_id=claim_id, source_actor_id=None,
                    stance="doubt", confidence=0.2,
                )
                raise RuntimeError("abort")
    with Session(database) as session:
        assert event_of(session, setup["game_id"], "UNSEEN_CLAIM") is None
        assert event_of(session, setup["game_id"], "OBSERVED_CLAIM") is None
        assert session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == alice_id,
        )) is None
        assert len(session.scalars(select(KnowledgeSource).where(
            KnowledgeSource.game_id == game_id,
        )).all()) == 1  # Only Player AI's opening source remains.


def test_player_ai_cannot_see_private_alice_bob_event(database):
    setup = run_slice(database)
    with Session(database) as session:
        share = event_of(session, setup["game_id"], "CHARACTER_SHARED_INFORMATION")
        ids = {row["id"] for row in PlayerProjectionService(session).timeline(
            UUID(setup["game_id"])
        )}
        assert str(share.id) not in ids
        assert session.get(EventAudience, (share.id, UUID(setup["player_ai_id"]))) is None


def test_public_classification_alone_is_not_observation(database):
    simulation = SimulationEngine(database, DemoLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    with Session(database) as session:
        game = session.get(Game, game_id)
        announcement = record_event(
            session, game, day=1, type="UNOBSERVED_PUBLIC_ANNOUNCEMENT",
            visibility="public",
        )
        session.commit()
        assert str(announcement.id) not in {
            row["id"] for row in PlayerProjectionService(session).timeline(game_id)
        }


def test_debug_view_still_sees_full_private_event(database):
    setup = run_slice(database)
    with Session(database) as session:
        snapshot = DebugWorldView(session).snapshot(UUID(setup["game_id"]))
        assert any(row["type"] == "CHARACTER_SHARED_INFORMATION" for row in snapshot["events"])
        assert any(row["type"] == "PRIVACY_VIOLATION" for row in snapshot["events"])
        violation_id = next(row["id"] for row in snapshot["events"]
                            if row["type"] == "PRIVACY_VIOLATION")
        assert all(row["event_id"] != violation_id for row in snapshot["observations"])
        assert all("first_acquired_day" in row for row in snapshot["knowledge"])


def test_every_knowledge_has_observed_acquisition_provenance(database):
    setup = run_slice(database, confidential=True)
    with Session(database) as session:
        game_id = UUID(setup["game_id"])
        for knowledge in session.scalars(select(Knowledge).where(
            Knowledge.game_id == game_id,
        )).all():
            assert session.get(Claim, knowledge.claim_id) is not None
            assert session.get(Actor, knowledge.holder_actor_id) is not None
            assert 1 <= knowledge.first_acquired_day <= 30
            assert knowledge.stance in ("believe", "doubt", "deny")
            assert 0 <= knowledge.confidence <= 1
            sources = session.scalars(select(KnowledgeSource).where(
                KnowledgeSource.knowledge_id == knowledge.id,
            )).all()
            assert sources
            for source in sources:
                origin = session.get(Event, source.source_event_id)
                assert source.acquired_day == origin.day
                assert session.get(EventAudience, (
                    source.source_event_id, knowledge.holder_actor_id,
                )) is not None
                if source.source_actor_id is not None:
                    assert session.get(Actor, source.source_actor_id) is not None
