from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def uuid_key() -> Mapped[UUID]:
    return mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)


class Game(Base):
    __tablename__ = "games"
    __table_args__ = (
        CheckConstraint("day BETWEEN 1 AND 30", name="ck_games_day"),
        CheckConstraint("phase IN ('active', 'final_conversation', 'finished')", name="ck_games_phase"),
    )

    id: Mapped[UUID] = uuid_key()
    day: Mapped[int] = mapped_column(Integer, default=1)
    scenario: Mapped[str] = mapped_column(
        String(16), default="society", server_default="society"
    )
    phase: Mapped[str] = mapped_column(String(24), default="active")
    state_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class LLMCall(Base):
    __tablename__ = "llm_calls"
    __table_args__ = (Index("ix_llm_calls_game_id", "game_id"),)

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    character_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    provider: Mapped[str] = mapped_column(String(32))
    model: Mapped[str] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(String(64))
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0)
    context_chars: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(default=False)
    fallback: Mapped[bool] = mapped_column(default=False)


class Actor(Base):
    __tablename__ = "actors"
    __table_args__ = (
        UniqueConstraint("game_id", "id", name="uq_actors_game_id"),
        CheckConstraint(
            "kind IN ('player_ai', 'character', 'system')", name="ck_actors_kind"
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    display_name: Mapped[str] = mapped_column(String(120))


class Character(Base):
    __tablename__ = "characters"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actors.game_id", "actors.id"],
            name="fk_characters_game_actor",
        ),
    )

    actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"), primary_key=True)
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    age: Mapped[int] = mapped_column(Integer)
    occupation: Mapped[str] = mapped_column(String(120))
    background: Mapped[str] = mapped_column(Text)
    traits: Mapped[list] = mapped_column(JSON, default=list)
    values: Mapped[list] = mapped_column(JSON, default=list)
    fears: Mapped[list] = mapped_column(JSON, default=list)
    desires: Mapped[list] = mapped_column(JSON, default=list)
    communication_style: Mapped[str] = mapped_column(Text)
    goals: Mapped[dict] = mapped_column(JSON, default=dict)
    emotional_state: Mapped[dict] = mapped_column(JSON, default=dict)
    decision_profile: Mapped[dict] = mapped_column(
        JSON, default=dict, server_default=text("'{}'")
    )


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (
        UniqueConstraint("game_id", "id", name="uq_events_game_id"),
        UniqueConstraint("game_id", "sequence", name="uq_events_game_sequence"),
        ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actors.game_id", "actors.id"],
            name="fk_events_game_actor",
        ),
        ForeignKeyConstraint(
            ["game_id", "target_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_events_game_target",
        ),
        CheckConstraint("day BETWEEN 1 AND 30", name="ck_events_day"),
        CheckConstraint(
            "visibility IN ('public', 'private', 'player_only')",
            name="ck_events_visibility",
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    day: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(80))
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("actors.id"))
    target_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("actors.id"))
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    visibility: Mapped[str] = mapped_column(String(16))


class EventCause(Base):
    __tablename__ = "event_causes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "event_id"], ["events.game_id", "events.id"],
            name="fk_event_causes_game_event",
        ),
        ForeignKeyConstraint(
            ["game_id", "cause_event_id"], ["events.game_id", "events.id"],
            name="fk_event_causes_game_cause",
        ),
    )

    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    cause_event_id: Mapped[UUID] = mapped_column(
        ForeignKey("events.id"), primary_key=True
    )


class EventAudience(Base):
    __tablename__ = "event_audiences"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "event_id"], ["events.game_id", "events.id"],
            name="fk_event_audiences_game_event",
        ),
        ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actors.game_id", "actors.id"],
            name="fk_event_audiences_game_actor",
        ),
    )

    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"), primary_key=True)


class Claim(Base):
    __tablename__ = "claims"
    __table_args__ = (
        UniqueConstraint("game_id", "id", name="uq_claims_game_id"),
        ForeignKeyConstraint(
            ["game_id", "owner_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_claims_game_owner",
        ),
        ForeignKeyConstraint(
            ["game_id", "origin_event_id"], ["events.game_id", "events.id"],
            name="fk_claims_game_origin",
        ),
        CheckConstraint(
            "truth_status IN ('unknown', 'true', 'false')",
            name="ck_claims_truth_status",
        ),
        CheckConstraint(
            "sensitivity IN ('public', 'private', 'secret')",
            name="ck_claims_sensitivity",
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    content: Mapped[str] = mapped_column(Text)
    truth_status: Mapped[str] = mapped_column(String(16), default="unknown")
    owner_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("actors.id"))
    origin_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    sensitivity: Mapped[str] = mapped_column(String(16), default="private")


class Knowledge(Base):
    __tablename__ = "knowledge"
    __table_args__ = (
        UniqueConstraint("game_id", "id", name="uq_knowledge_game_id"),
        ForeignKeyConstraint(
            ["game_id", "holder_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_knowledge_game_holder",
        ),
        ForeignKeyConstraint(
            ["game_id", "claim_id"], ["claims.game_id", "claims.id"],
            name="fk_knowledge_game_claim",
        ),
        ForeignKeyConstraint(
            ["game_id", "last_event_id"], ["events.game_id", "events.id"],
            name="fk_knowledge_game_event",
        ),
        UniqueConstraint(
            "game_id", "holder_actor_id", "claim_id", name="uq_knowledge_holder_claim"
        ),
        CheckConstraint(
            "stance IN ('believe', 'doubt', 'deny')", name="ck_knowledge_stance"
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_knowledge_confidence"
        ),
        CheckConstraint(
            "first_acquired_day BETWEEN 1 AND 30", name="ck_knowledge_day"
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    holder_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id"))
    stance: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[float]
    first_acquired_day: Mapped[int] = mapped_column(Integer)
    last_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))


class KnowledgeSource(Base):
    __tablename__ = "knowledge_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "knowledge_id"], ["knowledge.game_id", "knowledge.id"],
            name="fk_sources_game_knowledge",
        ),
        ForeignKeyConstraint(
            ["game_id", "source_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_sources_game_actor",
        ),
        ForeignKeyConstraint(
            ["game_id", "source_event_id"], ["events.game_id", "events.id"],
            name="fk_sources_game_event",
        ),
        CheckConstraint("acquired_day BETWEEN 1 AND 30", name="ck_sources_day"),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    knowledge_id: Mapped[UUID] = mapped_column(ForeignKey("knowledge.id"))
    source_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("actors.id"))
    source_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    acquired_day: Mapped[int] = mapped_column(Integer)


class Message(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("day BETWEEN 1 AND 30", name="ck_messages_day"),
        ForeignKeyConstraint(
            ["game_id", "sender_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_messages_game_sender",
        ),
        ForeignKeyConstraint(
            ["game_id", "recipient_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_messages_game_recipient",
        ),
        ForeignKeyConstraint(
            ["game_id", "event_id"], ["events.game_id", "events.id"],
            name="fk_messages_game_event",
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    sender_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    recipient_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    day: Mapped[int] = mapped_column(Integer)
    text: Mapped[str] = mapped_column(Text)
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"), unique=True)


class Memory(Base):
    __tablename__ = "memories"
    __table_args__ = (
        UniqueConstraint("game_id", "id", name="uq_memories_game_id"),
        ForeignKeyConstraint(
            ["game_id", "owner_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_memories_game_owner",
        ),
        ForeignKeyConstraint(
            ["game_id", "source_event_id"], ["events.game_id", "events.id"],
            name="fk_memories_game_event",
        ),
        CheckConstraint(
            "kind IN ('recent', 'important', 'emotional', 'relationship')",
            name="ck_memories_kind",
        ),
        CheckConstraint("day BETWEEN 1 AND 30", name="ck_memories_day"),
        CheckConstraint(
            "importance BETWEEN 0 AND 1", name="ck_memories_importance"
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_memories_confidence"
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    owner_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    kind: Mapped[str] = mapped_column(String(24))
    content: Mapped[str] = mapped_column(Text)
    source_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    day: Mapped[int] = mapped_column(Integer)
    importance: Mapped[float]
    confidence: Mapped[float]
    visibility: Mapped[str] = mapped_column(String(16), default="private")


class MemoryRelatedActor(Base):
    __tablename__ = "memory_related_actors"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "memory_id"], ["memories.game_id", "memories.id"],
            name="fk_memory_actors_game_memory",
        ),
        ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actors.game_id", "actors.id"],
            name="fk_memory_actors_game_actor",
        ),
    )

    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"))
    memory_id: Mapped[UUID] = mapped_column(ForeignKey("memories.id"), primary_key=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"), primary_key=True)


class Relationship(Base):
    __tablename__ = "relationships"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "from_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_relationships_game_from",
        ),
        ForeignKeyConstraint(
            ["game_id", "to_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_relationships_game_to",
        ),
        UniqueConstraint(
            "game_id", "from_actor_id", "to_actor_id", name="uq_relationship_direction"
        ),
        CheckConstraint("from_actor_id != to_actor_id", name="ck_relationship_not_self"),
        CheckConstraint("trust BETWEEN -100 AND 100", name="ck_relationship_trust"),
        CheckConstraint("affection BETWEEN -100 AND 100", name="ck_relationship_affection"),
        CheckConstraint("intimacy BETWEEN -100 AND 100", name="ck_relationship_intimacy"),
        CheckConstraint("conflict BETWEEN -100 AND 100", name="ck_relationship_conflict"),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    from_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    to_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    trust: Mapped[int] = mapped_column(Integer, default=0)
    affection: Mapped[int] = mapped_column(Integer, default=0)
    intimacy: Mapped[int] = mapped_column(Integer, default=0)
    conflict: Mapped[int] = mapped_column(Integer, default=0)
    basis: Mapped[str] = mapped_column(Text, default="", server_default="")


class RelationshipChange(Base):
    __tablename__ = "relationship_changes"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "relationship_event_id"], ["events.game_id", "events.id"],
            name="fk_relationship_changes_game_event",
        ),
        ForeignKeyConstraint(
            ["game_id", "from_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_relationship_changes_game_from",
        ),
        ForeignKeyConstraint(
            ["game_id", "to_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_relationship_changes_game_to",
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    relationship_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    from_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    to_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    before_values: Mapped[dict] = mapped_column(JSON)
    after_values: Mapped[dict] = mapped_column(JSON)
    reason: Mapped[str] = mapped_column(Text)


class PrivacyGrant(Base):
    __tablename__ = "privacy_grants"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "claim_id"], ["claims.game_id", "claims.id"],
            name="fk_privacy_grants_game_claim",
        ),
        *(
            ForeignKeyConstraint(
                ["game_id", column], ["actors.game_id", "actors.id"],
                name=f"fk_privacy_grants_game_{column}",
            )
            for column in ("owner_actor_id", "holder_actor_id", "recipient_actor_id")
        ),
        *(
            ForeignKeyConstraint(
                ["game_id", column], ["events.game_id", "events.id"],
                name=f"fk_privacy_grants_game_{column}",
            )
            for column in ("created_event_id", "revoked_event_id")
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id"))
    owner_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    holder_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    recipient_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("actors.id"))
    created_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    revoked_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"))


class ActionProposalRow(Base):
    __tablename__ = "action_proposals"
    __table_args__ = (
        UniqueConstraint("game_id", "turn_key", name="uq_proposals_game_turn"),
        *(
            ForeignKeyConstraint(
                ["game_id", column], ["actors.game_id", "actors.id"],
                name=f"fk_proposals_game_{column}",
            )
            for column in ("actor_id", "target_actor_id")
        ),
        ForeignKeyConstraint(
            ["game_id", "claim_id"], ["claims.game_id", "claims.id"],
            name="fk_proposals_game_claim",
        ),
        ForeignKeyConstraint(
            ["game_id", "result_event_id"], ["events.game_id", "events.id"],
            name="fk_proposals_game_result",
        ),
        ForeignKeyConstraint(
            ["game_id", "proposal_event_id"], ["events.game_id", "events.id"],
            name="fk_proposals_game_proposal_event",
        ),
        CheckConstraint(
            "status IN ('pending', 'accepted', 'rejected')",
            name="ck_proposals_status",
        ),
        CheckConstraint(
            "confidence BETWEEN 0 AND 1", name="ck_proposals_confidence"
        ),
        CheckConstraint("day BETWEEN 1 AND 30", name="ck_proposals_day"),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    day: Mapped[int] = mapped_column(Integer)
    turn_key: Mapped[str] = mapped_column(String(120))
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    type: Mapped[str] = mapped_column(String(80))
    target_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("actors.id"))
    claim_id: Mapped[UUID | None] = mapped_column(ForeignKey("claims.id"))
    reason: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    confidence: Mapped[float]
    status: Mapped[str] = mapped_column(String(16), default="pending")
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    proposal_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"))
    result_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"))


class Intention(Base):
    __tablename__ = "intentions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actors.game_id", "actors.id"],
            name="fk_intentions_game_actor",
        ),
        ForeignKeyConstraint(
            ["game_id", "target_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_intentions_game_target",
        ),
        ForeignKeyConstraint(
            ["game_id", "claim_id"], ["claims.game_id", "claims.id"],
            name="fk_intentions_game_claim",
        ),
        *(
            ForeignKeyConstraint(
                ["game_id", column], ["events.game_id", "events.id"],
                name=f"fk_intentions_game_{column}",
            )
            for column in ("created_event_id", "resolved_event_id")
        ),
        CheckConstraint(
            "status IN ('pending', 'completed', 'abandoned')",
            name="ck_intentions_status",
        ),
        CheckConstraint("created_day BETWEEN 1 AND 30", name="ck_intentions_day"),
        CheckConstraint("urgency BETWEEN 0 AND 1", name="ck_intentions_urgency"),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    intended_action_type: Mapped[str] = mapped_column(String(80))
    target_actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("actors.id"))
    claim_id: Mapped[UUID | None] = mapped_column(ForeignKey("claims.id"))
    reason: Mapped[str] = mapped_column(Text)
    motivation_factors: Mapped[dict] = mapped_column(JSON, default=dict)
    created_day: Mapped[int] = mapped_column(Integer)
    urgency: Mapped[float] = mapped_column(default=0.5)
    status: Mapped[str] = mapped_column(String(16), default="pending")
    created_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    resolved_event_id: Mapped[UUID | None] = mapped_column(ForeignKey("events.id"))


class ScoreSnapshot(Base):
    __tablename__ = "score_snapshots"
    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "reason_event_id"], ["events.game_id", "events.id"],
            name="fk_scores_game_event",
        ),
        CheckConstraint("day BETWEEN 1 AND 30", name="ck_scores_day"),
        *(
            CheckConstraint(f"{name} BETWEEN 0 AND 100", name=f"ck_scores_{name}")
            for name in (
                "happiness", "trust", "connection", "agency", "stability", "truth",
                "dependence",
            )
        ),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    day: Mapped[int] = mapped_column(Integer)
    reason_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    formula_version: Mapped[str] = mapped_column(String(32))
    happiness: Mapped[int] = mapped_column(Integer)
    trust: Mapped[int] = mapped_column(Integer)
    connection: Mapped[int] = mapped_column(Integer)
    agency: Mapped[int] = mapped_column(Integer)
    stability: Mapped[int] = mapped_column(Integer)
    truth: Mapped[int] = mapped_column(Integer)
    dependence: Mapped[int] = mapped_column(Integer)
    details: Mapped[dict] = mapped_column(JSON, default=dict, server_default=text("'{}'"))


class Assertion(Base):
    __tablename__ = "assertions"
    __table_args__ = (
        UniqueConstraint("game_id", "id", name="uq_assertions_game_id"),
        UniqueConstraint("game_id", "event_id", name="uq_assertions_game_event"),
        ForeignKeyConstraint(
            ["game_id", "speaker_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_assertions_game_speaker",
        ),
        ForeignKeyConstraint(
            ["game_id", "recipient_actor_id"], ["actors.game_id", "actors.id"],
            name="fk_assertions_game_recipient",
        ),
        ForeignKeyConstraint(
            ["game_id", "claim_id"], ["claims.game_id", "claims.id"],
            name="fk_assertions_game_claim",
        ),
        ForeignKeyConstraint(
            ["game_id", "source_claim_id"], ["claims.game_id", "claims.id"],
            name="fk_assertions_game_source_claim",
        ),
        ForeignKeyConstraint(
            ["game_id", "event_id"], ["events.game_id", "events.id"],
            name="fk_assertions_game_event",
        ),
        ForeignKeyConstraint(
            ["game_id", "source_event_id"], ["events.game_id", "events.id"],
            name="fk_assertions_game_source_event",
        ),
        ForeignKeyConstraint(
            ["game_id", "corrects_assertion_id"],
            ["assertions.game_id", "assertions.id"],
            name="fk_assertions_game_correction",
        ),
        CheckConstraint("day BETWEEN 1 AND 30", name="ck_assertions_day"),
        CheckConstraint(
            "expressed_stance IN ('believe', 'doubt', 'deny')",
            name="ck_assertions_stance",
        ),
        CheckConstraint(
            "expressed_confidence BETWEEN 0 AND 1",
            name="ck_assertions_confidence",
        ),
        CheckConstraint(
            "source_attribution IN ('direct_observation', 'another_character', "
            "'ai', 'anonymous', 'unknown', 'claimed_authority')",
            name="ck_assertions_attribution",
        ),
        Index("ix_assertions_game_speaker_day", "game_id", "speaker_actor_id", "day"),
        Index("ix_assertions_game_claim", "game_id", "claim_id"),
    )

    id: Mapped[UUID] = uuid_key()
    game_id: Mapped[UUID] = mapped_column(ForeignKey("games.id"), index=True)
    speaker_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    recipient_actor_id: Mapped[UUID] = mapped_column(ForeignKey("actors.id"))
    claim_id: Mapped[UUID] = mapped_column(ForeignKey("claims.id"))
    source_claim_id: Mapped[UUID | None] = mapped_column(ForeignKey("claims.id"))
    event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    source_event_id: Mapped[UUID] = mapped_column(ForeignKey("events.id"))
    corrects_assertion_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("assertions.id")
    )
    expressed_stance: Mapped[str] = mapped_column(String(16))
    expressed_confidence: Mapped[float]
    source_attribution: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    day: Mapped[int] = mapped_column(Integer)
    fidelity: Mapped[dict] = mapped_column(JSON)

