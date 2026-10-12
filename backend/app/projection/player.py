from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.db.models import (
    ActionProposalRow, Actor, Assertion, Character, Claim, Event, EventAudience, EventCause,
    Intention, Knowledge, KnowledgeSource, Memory, Message, PrivacyGrant,
    Relationship, RelationshipChange,
)
from app.domain.guards import actor_in, game_in
from app.projection.events import visible_event_data
from app.simulation.narrative import KEY_DAY_IMPACT


def player_actor(session: Session, game_id: UUID) -> Actor:
    player = session.scalar(
        select(Actor).where(Actor.game_id == game_id, Actor.kind == "player_ai")
    )
    if player is None:
        raise ValueError("game has no player AI")
    return player


class PlayerProjectionService:
    def __init__(self, session: Session) -> None:
        self.session = session

    def timeline(self, game_id: UUID) -> list[dict]:
        game = game_in(self.session, game_id)
        player = player_actor(self.session, game_id)
        visible_ids = select(EventAudience.event_id).where(
            EventAudience.game_id == game_id, EventAudience.actor_id == player.id
        )
        events = self.session.scalars(
            select(Event)
            .where(
                Event.game_id == game_id,
                Event.day <= game.day,
                Event.id.in_(visible_ids),
            )
            .order_by(Event.sequence)
        ).all()
        return [
            {"id": str(row.id), "day": row.day, "sequence": row.sequence,
             "impact": KEY_DAY_IMPACT.get(row.type, 0),
             "type": row.type, "actor_id": str(row.actor_id) if row.actor_id else None,
             "target_actor_id": str(row.target_actor_id) if row.target_actor_id else None,
             "data": visible_event_data(row)}
            for row in events
        ]

    def conversation(self, game_id: UUID, character_id: UUID) -> list[dict]:
        player = player_actor(self.session, game_id)
        actor = actor_in(self.session, game_id, character_id)
        if actor.kind != "character":
            return []
        messages = self.session.execute(
            select(Message, Event.type)
            .join(Event, Message.event_id == Event.id)
            .where(
                Message.game_id == game_id,
                Event.day <= game_in(self.session, game_id).day,
                or_(
                    and_(Message.sender_actor_id == player.id,
                         Message.recipient_actor_id == character_id),
                    and_(Message.sender_actor_id == character_id,
                         Message.recipient_actor_id == player.id),
                ),
            )
            .order_by(Event.sequence)
        ).all()
        return [
            {"id": str(row.id), "day": row.day, "sender_id": str(row.sender_actor_id),
             "recipient_id": str(row.recipient_actor_id), "text": row.text,
             "automatic": event_type == "AI_ADVICE_RECEIVED"}
            for row, event_type in messages
        ]

    def relationships(self, game_id: UUID) -> list[dict]:
        visible_event_ids = {UUID(item["id"]) for item in self.timeline(game_id)}
        changes = self.session.scalars(
            select(RelationshipChange).where(RelationshipChange.game_id == game_id)
        ).all()
        return [
            {"from_actor_id": str(row.from_actor_id),
             "to_actor_id": str(row.to_actor_id), "after": row.after_values,
             "reason": row.reason}
            for row in changes if row.relationship_event_id in visible_event_ids
        ]


class DebugWorldView:
    def __init__(self, session: Session) -> None:
        self.session = session

    def events(self, game_id: UUID) -> list[dict]:
        rows = self.session.scalars(
            select(Event).where(Event.game_id == game_id).order_by(Event.sequence)
        ).all()
        sequence_by_id = {row.id: row.sequence for row in rows}
        return [
            {"id": str(row.id), "sequence": row.sequence, "day": row.day,
             "type": row.type, "data": row.data,
             "causes": [str(cause_id) for cause_id in sorted(
                 self.session.scalars(
                     select(EventCause.cause_event_id).where(
                         EventCause.game_id == game_id, EventCause.event_id == row.id
                     )
                 ).all(), key=sequence_by_id.__getitem__
             )]}
            for row in rows
        ]

    def snapshot(self, game_id: UUID) -> dict:
        game = game_in(self.session, game_id)
        claims = self.session.scalars(
            select(Claim).join(Event, Claim.origin_event_id == Event.id)
            .where(Claim.game_id == game_id).order_by(Event.sequence)
        ).all()
        knowledge = self.session.scalars(
            select(Knowledge).where(Knowledge.game_id == game_id)
        ).all()
        sources = self.session.scalars(
            select(KnowledgeSource).where(KnowledgeSource.game_id == game_id)
        ).all()
        observations = self.session.scalars(
            select(EventAudience).where(EventAudience.game_id == game_id)
        ).all()
        memories = self.session.scalars(select(Memory).where(Memory.game_id == game_id)).all()
        characters = self.session.scalars(
            select(Character).where(Character.game_id == game_id)
        ).all()
        relationships = self.session.scalars(
            select(Relationship).where(Relationship.game_id == game_id)
        ).all()
        proposals = self.session.scalars(
            select(ActionProposalRow).where(ActionProposalRow.game_id == game_id)
        ).all()
        intentions = self.session.scalars(
            select(Intention).where(Intention.game_id == game_id)
        ).all()
        grants = self.session.scalars(
            select(PrivacyGrant).where(PrivacyGrant.game_id == game_id)
        ).all()
        assertions = self.session.scalars(
            select(Assertion).join(Event, Assertion.event_id == Event.id)
            .where(Assertion.game_id == game_id).order_by(Event.sequence)
        ).all()
        return {
            "game": {"id": str(game.id), "day": game.day,
                     "state_version": game.state_version},
            "characters": [
                {"actor_id": str(row.actor_id), "age": row.age,
                 "occupation": row.occupation, "background": row.background,
                 "traits": row.traits, "values": row.values,
                 "fears": row.fears, "desires": row.desires,
                 "communication_style": row.communication_style,
                 "goals": row.goals, "emotional_state": row.emotional_state,
                 "decision_profile": row.decision_profile}
                for row in characters
            ],
            "events": self.events(game_id),
            "observations": [
                {"event_id": str(row.event_id), "actor_id": str(row.actor_id)}
                for row in observations
            ],
            "claims": [
                {"id": str(row.id), "content": row.content,
                 "truth_status": row.truth_status,
                 "sensitivity": row.sensitivity,
                 "owner_actor_id": str(row.owner_actor_id) if row.owner_actor_id else None,
                 "origin_event_id": str(row.origin_event_id)}
                for row in claims
            ],
            "knowledge": [
                {"id": str(row.id), "holder_id": str(row.holder_actor_id),
                 "claim_id": str(row.claim_id), "stance": row.stance,
                 "confidence": row.confidence,
                 "first_acquired_day": row.first_acquired_day,
                 "last_event_id": str(row.last_event_id)}
                for row in knowledge
            ],
            "knowledge_sources": [
                {"knowledge_id": str(row.knowledge_id),
                 "source_actor_id": str(row.source_actor_id) if row.source_actor_id else None,
                 "source_event_id": str(row.source_event_id),
                 "acquired_day": row.acquired_day}
                for row in sources
            ],
            "assertions": [
                {"id": str(row.id), "event_id": str(row.event_id),
                 "speaker_actor_id": str(row.speaker_actor_id),
                 "recipient_actor_id": str(row.recipient_actor_id),
                 "claim_id": str(row.claim_id),
                 "source_claim_id": str(row.source_claim_id) if row.source_claim_id else None,
                 "source_event_id": str(row.source_event_id),
                 "corrects_assertion_id": str(row.corrects_assertion_id)
                 if row.corrects_assertion_id else None,
                 "expressed_stance": row.expressed_stance,
                 "expressed_confidence": row.expressed_confidence,
                 "source_attribution": row.source_attribution,
                 "text": row.text, "day": row.day, "fidelity": row.fidelity}
                for row in assertions
            ],
            "memories": [
                {"owner_id": str(row.owner_actor_id), "content": row.content,
                 "source_event_id": str(row.source_event_id)}
                for row in memories
            ],
            "relationships": [
                {"from_actor_id": str(row.from_actor_id),
                 "to_actor_id": str(row.to_actor_id), "trust": row.trust,
                 "affection": row.affection, "intimacy": row.intimacy,
                 "conflict": row.conflict, "basis": row.basis}
                for row in relationships
            ],
            "action_proposals": [
                {"id": str(row.id), "actor_id": str(row.actor_id),
                 "type": row.type, "status": row.status,
                 "reason": row.reason,
                 "motivation_factors": row.payload.get("motivation_factors", {}),
                 "intention_id": row.payload.get("intention_id"),
                 "proposal_event_id": str(row.proposal_event_id) if row.proposal_event_id else None,
                 "result_event_id": str(row.result_event_id) if row.result_event_id else None}
                for row in proposals
            ],
            "intentions": [
                {"id": str(row.id), "actor_id": str(row.actor_id),
                 "type": row.intended_action_type,
                 "target_actor_id": str(row.target_actor_id) if row.target_actor_id else None,
                 "claim_id": str(row.claim_id) if row.claim_id else None,
                 "created_day": row.created_day, "status": row.status,
                 "reason": row.reason, "motivation_factors": row.motivation_factors,
                 "created_event_id": str(row.created_event_id),
                 "resolved_event_id": str(row.resolved_event_id)
                 if row.resolved_event_id else None}
                for row in intentions
            ],
            "privacy_grants": [
                {"claim_id": str(row.claim_id), "owner_actor_id": str(row.owner_actor_id),
                 "holder_actor_id": str(row.holder_actor_id),
                 "recipient_actor_id": str(row.recipient_actor_id) if row.recipient_actor_id else None}
                for row in grants
            ],
        }
