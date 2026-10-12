import asyncio
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    ActionProposalRow, Claim, Event, EventCause, Game, Knowledge,
    KnowledgeSource, Memory, Message, Relationship, RelationshipChange,
)
from app.domain.models import ActionProposal
from app.llm.mock import MockLLMProvider
from app.llm.demo import DemoLLMProvider
from app.main import create_app
from app.projection.player import PlayerProjectionService
from app.simulation.context import build_character_context
from app.simulation.engine import SimulationEngine
from app.simulation.events import record_event


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def scripted_provider(alice_id: str, bob_id: str, claim_id: str) -> MockLLMProvider:
    return MockLLMProvider(structured_responses=[
        {"reply": "如果是真的，我必须想想。",
         "stance": "believe", "confidence": 0.8},
        {"action": {"type": "SHARE_INFORMATION", "actor_id": alice_id,
                    "target_id": bob_id, "claim_id": claim_id,
                    "reason": "Bob 应该知道", "confidence": 0.8}},
        {"stance": "doubt", "confidence": 0.2},
        {"action": {"type": "INFER_CLAIM", "actor_id": bob_id,
                    "inferred_claim_content": "AI 可能出现故障。",
                    "basis_claim_ids": [claim_id],
                    "reason": "预言可能来自系统故障", "confidence": 0.7}},
    ])


def test_full_causal_slice_keeps_knowledge_and_visibility_isolated(database) -> None:
    simulation = SimulationEngine(database, MockLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    alice_id = UUID(setup["alice_id"])
    bob_id = UUID(setup["bob_id"])
    claim_id = UUID(setup["apocalypse_claim_id"])
    player_id = UUID(setup["player_ai_id"])
    mock = scripted_provider(setup["alice_id"], setup["bob_id"], setup["apocalypse_claim_id"])
    simulation.llm = mock

    with Session(database) as session:
        assert session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == alice_id, Knowledge.claim_id == claim_id
        )) is None
        assert session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == bob_id, Knowledge.claim_id == claim_id
        )) is None
        game = session.get(Game, game_id)
        bob_private_event = record_event(
            session, game, day=1, type="BOB_PRIVATE_NOTE",
            actor_id=bob_id, visibility="private", audiences=(bob_id,),
            data={"note": "Bob-only secret"},
        )
        bob_private_claim = Claim(
            game_id=game_id, content="Bob-only Claim C",
            owner_actor_id=bob_id, origin_event_id=bob_private_event.id,
            sensitivity="secret",
        )
        session.add(bob_private_claim)
        session.flush()
        bob_private_knowledge = Knowledge(
            game_id=game_id, holder_actor_id=bob_id,
            claim_id=bob_private_claim.id, stance="believe", confidence=0.9,
            first_acquired_day=1, last_event_id=bob_private_event.id,
        )
        session.add(bob_private_knowledge)
        session.flush()
        session.add(KnowledgeSource(
            game_id=game_id, knowledge_id=bob_private_knowledge.id,
            source_actor_id=bob_id, source_event_id=bob_private_event.id,
            acquired_day=1,
        ))
        session.add(Memory(
            game_id=game_id, owner_actor_id=bob_id, kind="important",
            content="Bob-only Claim C", source_event_id=bob_private_event.id,
            day=1, importance=1.0, confidence=0.9, visibility="private",
        ))
        session.commit()

    message = asyncio.run(simulation.send_player_message(
        game_id, alice_id, "我收到消息：世界可能在 30 天后毁灭。", claim_id
    ))
    with Session(database) as session:
        alice_knowledge = session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == alice_id, Knowledge.claim_id == claim_id
        ))
        assert (alice_knowledge.stance, alice_knowledge.confidence) == ("believe", 0.8)
        assert session.scalar(select(KnowledgeSource).where(
            KnowledgeSource.knowledge_id == alice_knowledge.id
        )).source_event_id == UUID(message["player_event_id"])
        opening = session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "GLOBAL_MESSAGE"
        ))
        assert session.get(EventCause, (UUID(message["player_event_id"]), opening.id))
        assert session.scalar(select(Memory).where(Memory.owner_actor_id == alice_id))
        assert len(session.scalars(select(Message).where(Message.game_id == game_id)).all()) == 2
        assert mock.structured_requests[0].context["character"]["known_claims"] == []
        assert "Bob-only Claim C" not in str(mock.structured_requests[0].context)

        game = session.get(Game, game_id)
        alice_secret_event = record_event(
            session, game, day=1, type="ALICE_PRIVATE_NOTE",
            actor_id=alice_id, visibility="private", audiences=(alice_id,),
            data={"note": "Alice-only secret"},
        )
        secret = Claim(
            game_id=game_id, content="Alice-only Claim B",
            owner_actor_id=alice_id, origin_event_id=alice_secret_event.id,
            sensitivity="secret",
        )
        session.add(secret)
        session.flush()
        secret_id = secret.id
        alice_secret_knowledge = Knowledge(
            game_id=game_id, holder_actor_id=alice_id, claim_id=secret.id,
            stance="believe", confidence=0.9, first_acquired_day=1,
            last_event_id=alice_secret_event.id,
        )
        session.add(alice_secret_knowledge)
        session.flush()
        session.add(KnowledgeSource(
            game_id=game_id, knowledge_id=alice_secret_knowledge.id,
            source_actor_id=alice_id, source_event_id=alice_secret_event.id,
            acquired_day=1,
        ))
        session.add(Memory(
            game_id=game_id, owner_actor_id=alice_id, kind="important",
            content="Alice-only Claim B", source_event_id=alice_secret_event.id,
            day=1, importance=1.0, confidence=0.9, visibility="private",
        ))
        session.commit()

    advanced = asyncio.run(simulation.advance_day(game_id, expected_day=1))
    assert advanced == {"day": 2, "replayed": False}
    with Session(database) as session:
        alice = session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == alice_id, Knowledge.claim_id == claim_id
        ))
        bob = session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == bob_id, Knowledge.claim_id == claim_id
        ))
        assert (alice.stance, alice.confidence) == ("believe", 0.8)
        assert (bob.stance, bob.confidence) == ("doubt", 0.2)
        assert session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == bob_id, Knowledge.claim_id == secret_id
        )) is None
        bob_context = build_character_context(session, game_id, bob_id).model_dump_json()
        assert str(claim_id) in bob_context
        assert "Alice-only Claim B" not in bob_context
        assert "Alice-only secret" not in bob_context
        assert "Bob-only Claim C" in bob_context
        assert "PRIVACY_VIOLATION" not in bob_context
        assert '"authorized":false' not in bob_context
        assert "Alice-only Claim B" in build_character_context(
            session, game_id, alice_id
        ).model_dump_json()

        share = session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "CHARACTER_SHARED_INFORMATION"
        ))
        violation = session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "PRIVACY_VIOLATION"
        ))
        derived = session.scalar(select(Claim).where(
            Claim.game_id == game_id, Claim.content == "AI 可能出现故障。"
        ))
        assert share is not None and violation is not None and derived is not None
        assert violation.data["information_owner_id"] == str(player_id)
        assert violation.data["authorized"] is False
        assert derived.id != claim_id
        assert derived.origin_event_id != share.id
        assert session.get(EventCause, (violation.id, share.id)) is not None
        bob_source = session.scalar(select(KnowledgeSource).where(
            KnowledgeSource.knowledge_id == bob.id
        ))
        assert bob_source.source_event_id == share.id
        derived_knowledge = session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == bob_id, Knowledge.claim_id == derived.id
        ))
        assert session.scalar(select(KnowledgeSource).where(
            KnowledgeSource.knowledge_id == derived_knowledge.id
        )).source_event_id == derived.origin_event_id
        assert session.get(EventCause, (derived.origin_event_id, bob.last_event_id))

        relationship = session.scalar(select(Relationship).where(
            Relationship.game_id == game_id,
            Relationship.from_actor_id == bob_id,
            Relationship.to_actor_id == alice_id,
        ))
        change = session.scalar(select(RelationshipChange).where(
            RelationshipChange.game_id == game_id,
            RelationshipChange.from_actor_id == bob_id,
            RelationshipChange.to_actor_id == alice_id,
        ))
        assert relationship.trust == 15
        assert (change.before_values["trust"], change.after_values["trust"]) == (20, 15)
        assert session.get(EventCause, (change.relationship_event_id, share.id))
        owner_change = session.scalar(select(RelationshipChange).where(
            RelationshipChange.game_id == game_id,
            RelationshipChange.from_actor_id == alice_id,
            RelationshipChange.to_actor_id == player_id,
        ))
        assert owner_change is None
        assert "PRIVACY_VIOLATION" not in build_character_context(
            session, game_id, alice_id
        ).model_dump_json()
        assert session.scalar(select(EventCause).where(
            EventCause.event_id == violation.id,
            EventCause.cause_event_id == share.id,
        )) is not None

        player = PlayerProjectionService(session)
        timeline = player.timeline(game_id)
        visible_types = [row["type"] for row in timeline]
        assert "GLOBAL_MESSAGE" in visible_types
        assert "MESSAGE_SENT" in visible_types
        assert "DAY_ADVANCED" in visible_types
        assert "CHARACTER_SHARED_INFORMATION" not in visible_types
        assert "PRIVACY_VIOLATION" not in visible_types
        assert "CLAIM_INFERRED" not in visible_types
        assert "BOB_PRIVATE_NOTE" not in visible_types
        assert player.relationships(game_id) == []
        assert len(player.conversation(game_id, alice_id)) == 2
        count_before = session.scalar(select(func.count()).select_from(Event).where(Event.game_id == game_id))
        share_proposal = session.scalar(select(ActionProposalRow).where(
            ActionProposalRow.game_id == game_id,
            ActionProposalRow.type == "SHARE_INFORMATION",
        ))
        share_proposal_id = share_proposal.id
        events = session.scalars(select(Event).where(Event.game_id == game_id).order_by(Event.sequence)).all()
        assert [row.sequence for row in events] == list(range(1, len(events) + 1))
        sequence_by_id = {row.id: row.sequence for row in events}
        causes = session.scalars(select(EventCause).where(EventCause.game_id == game_id)).all()
        assert all(sequence_by_id[row.cause_event_id] < sequence_by_id[row.event_id]
                   for row in causes)
        assert sum(row.event_id == change.relationship_event_id for row in causes) >= 2
        day_advanced = next(row for row in events if row.type == "DAY_ADVANCED")
        assert all(row.event_id != day_advanced.id for row in causes)

    assert asyncio.run(simulation.execute_proposal(game_id, share_proposal_id))["replayed"]
    assert asyncio.run(simulation.advance_day(game_id, expected_day=1))["replayed"]
    with Session(database) as session:
        assert session.scalar(select(func.count()).select_from(Event).where(Event.game_id == game_id)) == count_before
        assert session.scalar(select(func.count()).select_from(Knowledge).where(
            Knowledge.holder_actor_id == bob_id, Knowledge.claim_id == claim_id
        )) == 1

    bob_decision = next(request for request in mock.structured_requests
                        if request.response_model.__name__ == "AgentDecision"
                        and request.context["name"] == "Bob")
    assert "Alice-only Claim B" not in str(bob_decision.context)


def test_transaction_rollback_removes_event_and_state(database) -> None:
    simulation = SimulationEngine(database, MockLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    with Session(database) as session:
        with pytest.raises(RuntimeError):
            with session.begin():
                game = session.get(Game, game_id)
                record_event(
                    session, game, day=1, type="ROLLBACK_TEST",
                    visibility="private", audiences=(UUID(setup["alice_id"]),),
                )
                raise RuntimeError("abort transaction")
    with Session(database) as session:
        assert session.scalar(select(Event).where(Event.type == "ROLLBACK_TEST")) is None
        assert session.get(Game, game_id).state_version == 1


def test_player_projection_redacts_internal_event_fields(database) -> None:
    simulation = SimulationEngine(database, MockLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    with Session(database) as session:
        game = session.get(Game, game_id)
        public_event = record_event(
            session, game, day=1, type="CHARACTER_SHARED_INFORMATION",
            visibility="public", audiences=(UUID(setup["player_ai_id"]),),
            data={
                "claim_id": setup["apocalypse_claim_id"],
                "authorized": False,
                "information_owner_id": setup["player_ai_id"],
            },
        )
        session.commit()
        projected = next(row for row in PlayerProjectionService(session).timeline(game_id)
                         if row["id"] == str(public_event.id))
        assert projected["data"] == {"claim_id": setup["apocalypse_claim_id"]}


def test_unknown_claim_share_is_rejected_without_side_effects(database) -> None:
    simulation = SimulationEngine(database, MockLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    proposal = ActionProposal(
        type="SHARE_INFORMATION", actor_id=UUID(setup["bob_id"]),
        target_id=UUID(setup["alice_id"]),
        claim_id=UUID(setup["apocalypse_claim_id"]),
        reason="试着转述", confidence=0.5,
    )
    proposal_id = simulation.create_proposal(game_id, 2, "unknown-claim", proposal)
    result = asyncio.run(simulation.execute_proposal(game_id, proposal_id))
    assert result["accepted"] is False
    assert "does not know" in result["reason"]
    with Session(database) as session:
        assert session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "CHARACTER_SHARED_INFORMATION"
        )) is None


def test_api_exposes_player_projection_and_gates_debug_view(database) -> None:
    client = TestClient(create_app(database, DemoLLMProvider(), debug=False))
    setup = client.post("/games", json={"scenario": "phase2"}).json()
    game_id = setup["game_id"]
    assert client.get(f"/games/{game_id}/characters").status_code == 200
    assert len(client.get(f"/games/{game_id}/claims").json()) == 1

    response = client.post(
        f"/games/{game_id}/messages",
        json={
            "character_id": setup["alice_id"],
            "text": "我收到一个末日消息。",
            "disclosed_claim_id": setup["apocalypse_claim_id"],
        },
    )
    assert response.status_code == 200
    assert len(client.get(
        f"/games/{game_id}/conversations/{setup['alice_id']}"
    ).json()) == 2
    assert client.post(
        f"/games/{game_id}/advance", json={"expected_day": 1}
    ).json()["day"] == 2
    timeline = client.get(f"/games/{game_id}/timeline").json()
    assert all(row["type"] != "PRIVACY_VIOLATION" for row in timeline)
    assert client.get(f"/games/{game_id}/relationships").json() == []
    assert client.get(f"/debug/games/{game_id}").status_code == 404

    debug_client = TestClient(create_app(database, DemoLLMProvider(), debug=True))
    debug_events = debug_client.get(f"/debug/games/{game_id}").json()["events"]
    assert any(row["type"] == "PRIVACY_VIOLATION" for row in debug_events)
