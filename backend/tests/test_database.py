import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import (
    ActionProposalRow, Actor, Claim, Event, EventCause, Game, Knowledge,
    KnowledgeSource, Memory, Message, PrivacyGrant, Relationship, RelationshipChange,
)
from app.domain.errors import WorldConsistencyError
from app.domain.guards import actor_in, claim_in


@pytest.fixture
def session() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enforce_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as db:
        yield db
    engine.dispose()


def test_claim_knowledge_is_per_character_and_has_sources(session: Session) -> None:
    game = Game()
    session.add(game)
    session.flush()
    alice = Actor(game_id=game.id, kind="character", display_name="Alice")
    bob = Actor(game_id=game.id, kind="character", display_name="Bob")
    session.add_all([alice, bob])
    session.flush()
    message_event = Event(
        game_id=game.id,
        sequence=1,
        day=1,
        type="MESSAGE_SENT",
        actor_id=alice.id,
        target_actor_id=bob.id,
        visibility="private",
    )
    session.add(message_event)
    session.flush()
    claim = Claim(
        game_id=game.id,
        content="世界将在 30 天后毁灭",
        origin_event_id=message_event.id,
        sensitivity="private",
    )
    session.add(claim)
    session.flush()
    session.add_all(
        [
            Knowledge(
                game_id=game.id,
                holder_actor_id=alice.id,
                claim_id=claim.id,
                stance="believe",
                confidence=0.8,
                first_acquired_day=1,
                last_event_id=message_event.id,
            ),
            Knowledge(
                game_id=game.id,
                holder_actor_id=bob.id,
                claim_id=claim.id,
                stance="deny",
                confidence=0.2,
                first_acquired_day=1,
                last_event_id=message_event.id,
            ),
        ]
    )
    session.flush()
    rows = session.scalars(select(Knowledge).order_by(Knowledge.confidence.desc())).all()
    session.add_all(
        [
            KnowledgeSource(
                game_id=game.id,
                knowledge_id=row.id,
                source_actor_id=alice.id,
                source_event_id=message_event.id,
                acquired_day=1,
            )
            for row in rows
        ]
    )
    session.commit()

    assert [(row.stance, row.confidence) for row in rows] == [
        ("believe", 0.8),
        ("deny", 0.2),
    ]
    assert session.scalar(select(KnowledgeSource).where(KnowledgeSource.knowledge_id == rows[1].id))


def test_derived_claim_keeps_causal_event_chain(session: Session) -> None:
    game = Game()
    session.add(game)
    session.flush()
    source = Event(
        game_id=game.id, sequence=1, day=1, type="MESSAGE_SENT", visibility="private"
    )
    inference = Event(
        game_id=game.id, sequence=2, day=1, type="CLAIM_INFERRED", visibility="private"
    )
    session.add_all([source, inference])
    session.flush()
    session.add(
        EventCause(game_id=game.id, event_id=inference.id, cause_event_id=source.id)
    )
    session.add(
        Claim(
            game_id=game.id,
            content="AI 可能出现故障",
            truth_status="unknown",
            origin_event_id=inference.id,
            sensitivity="private",
        )
    )
    session.commit()

    claim = session.scalar(select(Claim).where(Claim.content == "AI 可能出现故障"))
    assert claim is not None
    assert claim.origin_event_id == inference.id
    assert session.get(EventCause, (inference.id, source.id)) is not None


def test_database_rejects_out_of_range_confidence(session: Session) -> None:
    game = Game()
    session.add(game)
    session.flush()
    actor = Actor(game_id=game.id, kind="character", display_name="Alice")
    event_row = Event(
        game_id=game.id, sequence=1, day=1, type="MESSAGE_SENT", visibility="private"
    )
    session.add_all([actor, event_row])
    session.flush()
    claim = Claim(game_id=game.id, content="test", origin_event_id=event_row.id)
    session.add(claim)
    session.flush()
    session.add(
        Knowledge(
            game_id=game.id,
            holder_actor_id=actor.id,
            claim_id=claim.id,
            stance="believe",
            confidence=1.2,
            first_acquired_day=1,
            last_event_id=event_row.id,
        )
    )
    with pytest.raises(IntegrityError):
        session.commit()


def test_cross_game_references_fail_in_domain_and_database(session: Session) -> None:
    game_a, game_b = Game(), Game()
    session.add_all([game_a, game_b])
    session.flush()
    actor_a = Actor(game_id=game_a.id, kind="character", display_name="A")
    actor_b = Actor(game_id=game_b.id, kind="character", display_name="B")
    session.add_all([actor_a, actor_b])
    session.flush()
    event_a = Event(game_id=game_a.id, sequence=1, day=1, type="TEST", visibility="private")
    event_b = Event(game_id=game_b.id, sequence=1, day=1, type="TEST", visibility="private")
    session.add_all([event_a, event_b])
    session.flush()
    claim_a = Claim(game_id=game_a.id, content="A", origin_event_id=event_a.id)
    claim_b = Claim(game_id=game_b.id, content="B", origin_event_id=event_b.id)
    session.add_all([claim_a, claim_b])
    session.flush()
    knowledge_a = Knowledge(
        game_id=game_a.id, holder_actor_id=actor_a.id, claim_id=claim_a.id,
        stance="believe", confidence=0.7, first_acquired_day=1,
        last_event_id=event_a.id,
    )
    session.add(knowledge_a)
    session.commit()

    with pytest.raises(WorldConsistencyError, match="another game"):
        actor_in(session, game_a.id, actor_b.id)
    with pytest.raises(WorldConsistencyError, match="another game"):
        claim_in(session, game_a.id, claim_b.id)

    invalid_rows = [
        Knowledge(
            game_id=game_a.id, holder_actor_id=actor_a.id, claim_id=claim_b.id,
            stance="believe", confidence=0.5, first_acquired_day=1,
            last_event_id=event_a.id,
        ),
        KnowledgeSource(
            game_id=game_a.id, knowledge_id=knowledge_a.id,
            source_event_id=event_b.id, acquired_day=1,
        ),
        Message(
            game_id=game_a.id, sender_actor_id=actor_a.id,
            recipient_actor_id=actor_b.id, day=1, text="leak", event_id=event_a.id,
        ),
        Memory(
            game_id=game_a.id, owner_actor_id=actor_a.id,
            kind="recent", content="wrong world", source_event_id=event_b.id,
            day=1, importance=0.5, confidence=0.5, visibility="private",
        ),
        Relationship(
            game_id=game_a.id, from_actor_id=actor_a.id, to_actor_id=actor_b.id,
            trust=0, affection=0, intimacy=0, conflict=0,
        ),
        ActionProposalRow(
            game_id=game_a.id, day=1, turn_key="cross-game", actor_id=actor_b.id,
            type="SHARE_INFORMATION", reason="invalid", confidence=0.5,
            payload={}, status="pending",
        ),
        EventCause(game_id=game_a.id, event_id=event_a.id, cause_event_id=event_b.id),
        PrivacyGrant(
            game_id=game_a.id, claim_id=claim_b.id,
            owner_actor_id=actor_a.id, holder_actor_id=actor_a.id,
            created_event_id=event_a.id,
        ),
        RelationshipChange(
            game_id=game_a.id, relationship_event_id=event_b.id,
            from_actor_id=actor_a.id, to_actor_id=actor_a.id,
            before_values={}, after_values={}, reason="invalid",
        ),
    ]
    for row in invalid_rows:
        with pytest.raises(IntegrityError):
            with session.begin_nested():
                session.add(row)
                session.flush()

