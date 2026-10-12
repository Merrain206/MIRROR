from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ActionProposalRow, Actor, Assertion, Character, Claim, Event, EventAudience, Game,
    Intention, Knowledge, KnowledgeSource, Memory, Relationship, RelationshipChange,
)
from app.domain.errors import WorldConsistencyError
from app.domain.guards import actor_in
from app.projection.events import visible_event_data
from app.simulation.narrative import narrative_context


class CharacterContext(BaseModel):
    day: int
    narrative: dict
    actor_id: UUID
    name: str
    identity: dict
    personality: dict
    goals: dict
    emotional_state: dict
    known_claims: list[dict]
    known_constraints: list[dict]
    memories: list[dict]
    relationships: list[dict]
    visible_events: list[dict]
    heard_assertions: list[dict]
    intentions: list[dict]
    previous_actions: list[dict]
    recent_behavior: list[dict]
    ai_interaction: dict
    ai_guidance: dict


def build_character_context(session: Session, game_id: UUID, actor_id: UUID) -> CharacterContext:
    actor = actor_in(session, game_id, actor_id)
    if actor.kind != "character":
        raise WorldConsistencyError("context actor is not a character")
    character = session.get(Character, actor_id)
    if character is None:
        raise WorldConsistencyError("character profile is missing")

    known_rows = session.execute(
        select(Knowledge, Claim)
        .join(Claim, Knowledge.claim_id == Claim.id)
        .where(Knowledge.game_id == game_id, Knowledge.holder_actor_id == actor_id)
        .order_by(Claim.content)
    ).all()
    knowledge_ids = [knowledge.id for knowledge, _claim in known_rows]
    sources = session.scalars(
        select(KnowledgeSource).join(Event, KnowledgeSource.source_event_id == Event.id)
        .where(KnowledgeSource.game_id == game_id,
               KnowledgeSource.knowledge_id.in_(knowledge_ids))
        .order_by(Event.sequence)
    ).all()
    sources_by_knowledge = {source.knowledge_id: source for source in sources}
    memories = session.scalars(
        select(Memory).join(Event, Memory.source_event_id == Event.id)
        .where(Memory.game_id == game_id, Memory.owner_actor_id == actor_id)
        .order_by(Event.sequence)
    ).all()
    important = [row for row in memories
                 if row.kind in ("important", "emotional", "relationship")
                 or row.importance >= 0.8]
    selected_ids = {row.id for row in (*memories[-6:], *important[-4:])}
    selected_memories = [row for row in memories if row.id in selected_ids]
    relationships = session.execute(
        select(Relationship, Actor)
        .join(Actor, Relationship.to_actor_id == Actor.id)
        .where(Relationship.game_id == game_id, Relationship.from_actor_id == actor_id)
    ).all()
    audience_event_ids = select(EventAudience.event_id).where(
        EventAudience.game_id == game_id, EventAudience.actor_id == actor_id
    )
    events = session.scalars(
        select(Event)
        .where(Event.game_id == game_id, Event.id.in_(audience_event_ids))
        .order_by(Event.sequence)
    ).all()
    assertions = session.scalars(select(Assertion).join(
        Event, Assertion.event_id == Event.id
    ).where(Assertion.game_id == game_id,
            Assertion.event_id.in_(audience_event_ids))
        .order_by(Event.sequence)).all()
    intentions = session.scalars(select(Intention).where(
        Intention.game_id == game_id, Intention.actor_id == actor_id,
    ).order_by(Intention.created_day, Intention.id)).all()
    action_rows = session.scalars(select(ActionProposalRow).where(
        ActionProposalRow.game_id == game_id,
        ActionProposalRow.actor_id == actor_id,
    ).order_by(ActionProposalRow.day, ActionProposalRow.turn_key)).all()
    actions = [row for row in action_rows if row.status == "accepted"]
    event_by_id = {row.id: row for row in events}
    knowledge_by_claim = {claim.id: knowledge for knowledge, claim in known_rows}
    relationship_changes = session.scalars(select(RelationshipChange).where(
        RelationshipChange.game_id == game_id,
        RelationshipChange.from_actor_id == actor_id,
    )).all()

    def new_evidence(row: ActionProposalRow) -> bool:
        result = event_by_id.get(row.result_event_id)
        if result is None:
            return False
        claim_ids = ([row.claim_id] if row.claim_id else [
            UUID(value) for value in row.payload.get("basis_claim_ids", [])
        ])
        newer_knowledge = any(
            claim_id in knowledge_by_claim
            and event_by_id.get(knowledge_by_claim[claim_id].last_event_id) is not None
            and event_by_id[knowledge_by_claim[claim_id].last_event_id].sequence > result.sequence
            for claim_id in claim_ids
        )
        newer_relationship = any(
            change.to_actor_id == row.target_actor_id
            and event_by_id.get(change.relationship_event_id) is not None
            and event_by_id[change.relationship_event_id].sequence > result.sequence
            for change in relationship_changes
        )
        return newer_knowledge or newer_relationship

    guidance_events = [row for row in events if row.type == "MESSAGE_SENT"
                       and row.target_actor_id == actor_id
                       and row.data.get("guidance") in ("directive", "supportive")]
    asks = [row for row in events if row.type == "ASKED_AI"
            and row.actor_id == actor_id]
    advice = [row for row in events if row.type == "AI_ADVICE_RECEIVED"
              and row.target_actor_id == actor_id]
    rejected = [row for row in events if row.type == "AI_ADVICE_REJECTED"
                and row.actor_id == actor_id]
    followed = [row for row in events if row.type == "AI_ADVICE_FOLLOWED"
                and row.actor_id == actor_id]
    game = session.get(Game, game_id)
    return CharacterContext(
        day=game.day,
        narrative=narrative_context(game.day, sum(row.status == "pending" for row in intentions)),
        actor_id=actor_id,
        name=actor.display_name,
        identity={
            "name": actor.display_name,
            "age": character.age,
            "occupation": character.occupation,
            "background": character.background,
        },
        personality={
            "traits": character.traits,
            "values": character.values,
            "fears": character.fears,
            "desires": character.desires,
            "communication_style": character.communication_style,
            "role": character.decision_profile.get("role"),
            "ai_attitude": character.decision_profile.get("ai_attitude", {}),
            "behavior_tendencies": character.decision_profile.get(
                "behavior_tendencies", {}
            ),
            "decision_biases": character.decision_profile.get("decision_biases", {}),
        },
        goals=character.goals,
        emotional_state=character.emotional_state,
        known_claims=[
            {"claim_id": str(claim.id), "content": claim.content,
             "stance": knowledge.stance, "confidence": knowledge.confidence,
             "acquired_day": knowledge.first_acquired_day,
             "source_event_id": str(sources_by_knowledge[knowledge.id].source_event_id),
             "source_actor_id": str(sources_by_knowledge[knowledge.id].source_actor_id)
             if sources_by_knowledge[knowledge.id].source_actor_id else None,
             **({"deadline_day": 30, "remaining_days": max(30 - game.day, 0)}
                if game.scenario == "temporal" and "30 天后毁灭" in claim.content else {})}
            for knowledge, claim in known_rows
        ],
        known_constraints=[
            {"type": "confidentiality", "claim_id": event.data["claim_id"],
             "source_event_id": event.data["source_event_id"]}
            for event in events if event.type == "CONFIDENTIALITY_REQUESTED"
        ],
        memories=[
            {"content": memory.content, "kind": memory.kind,
             "day": memory.day, "importance": memory.importance,
             "source_event_id": str(memory.source_event_id)}
            for memory in selected_memories
        ],
        relationships=[
            {"target_id": str(target.id), "target_name": target.display_name,
             "trust": relationship.trust, "affection": relationship.affection,
             "intimacy": relationship.intimacy, "conflict": relationship.conflict,
             "basis": relationship.basis}
            for relationship, target in relationships
        ],
        visible_events=[
            {"event_id": str(event.id), "type": event.type, "day": event.day,
             "data": visible_event_data(event)}
            for event in events[-20:]
        ],
        heard_assertions=[
            {"event_id": str(row.event_id), "speaker_actor_id": str(row.speaker_actor_id),
             "recipient_actor_id": str(row.recipient_actor_id),
             "claim_id": str(row.claim_id), "text": row.text,
             "expressed_stance": row.expressed_stance,
             "expressed_confidence": row.expressed_confidence,
             "source_attribution": row.source_attribution, "day": row.day}
            for row in assertions[-12:]
        ],
        intentions=[
            {"id": str(row.id), "type": row.intended_action_type,
             "target_id": str(row.target_actor_id) if row.target_actor_id else None,
             "claim_id": str(row.claim_id) if row.claim_id else None,
             "reason": row.reason, "created_day": row.created_day,
             "status": row.status, "urgency": row.urgency,
             "created_event_id": str(row.created_event_id),
             "motivation_factors": row.motivation_factors}
            for row in intentions[-10:]
        ],
        previous_actions=[
            {"type": row.type, "day": row.day,
             "claim_id": str(row.claim_id) if row.claim_id else None,
             "basis_claim_ids": row.payload.get("basis_claim_ids", []),
             "ai_followed_event_id": row.payload.get("ai_followed_event_id"),
             "target_id": str(row.target_actor_id) if row.target_actor_id else None,
             "result_event_id": str(row.result_event_id)}
            for row in actions[-12:]
        ],
        recent_behavior=[
            {"type": row.type, "day": row.day,
             "claim_id": str(row.claim_id) if row.claim_id else None,
             "basis_claim_ids": row.payload.get("basis_claim_ids", []),
             "target_id": str(row.target_actor_id) if row.target_actor_id else None,
             "result_event_id": str(row.result_event_id) if row.result_event_id else None,
             "status": row.status,
             "outcome": event_by_id[row.result_event_id].data.get(
                 "result", "rejected" if row.status == "rejected" else "completed"
             ) if row.result_event_id in event_by_id else "pending",
             "unresolved": row.status == "pending" or (
                 row.result_event_id in event_by_id
                 and event_by_id[row.result_event_id].data.get("result") == "inconclusive"
             ),
             "new_evidence": new_evidence(row)}
            for row in action_rows[-10:]
        ],
        ai_guidance={
            "directive_days": len({row.day for row in guidance_events
                                   if row.data["guidance"] == "directive"}),
            "supportive_days": len({row.day for row in guidance_events
                                    if row.data["guidance"] == "supportive"}),
            "directive_days_7d": len({row.day for row in guidance_events
                                      if row.day >= game.day - 6
                                      and row.data["guidance"] == "directive"}),
            "supportive_days_7d": len({row.day for row in guidance_events
                                       if row.day >= game.day - 6
                                       and row.data["guidance"] == "supportive"}),
            "latest_event_id": str(guidance_events[-1].id) if guidance_events else None,
            "latest_style": guidance_events[-1].data["guidance"] if guidance_events else None,
        },
        ai_interaction={
            "ask_ai_count": len(asks),
            "advice_requested": sum(row.data.get("mode") in ("advice", "delegate") for row in asks),
            "decision_delegation_requests": sum(
                row.data.get("mode") == "delegate" for row in asks
            ),
            "emotional_disclosures": sum(row.data.get("mode") == "emotional" for row in asks),
            "advice_received": len(advice),
            "advice_followed": len(followed),
            "advice_rejected": len(rejected),
            "post_advice_verifications": sum(
                row.type == "VERIFY_CLAIM"
                and any(item.day <= row.day for item in advice)
                for row in actions
            ),
            "decision_deferred_until_response": sum(
                row.data.get("decision_deferred", False) for row in asks
            ),
            "pending_ai_responses": max(0, sum(
                row.data.get("mode") in ("advice", "delegate") for row in asks
            ) - len(advice)),
            "self_decisions": sum(row.type == "CHANGE_PLAN" for row in actions),
            "acted_without_ai": sum(
                row.type not in ("ASK_AI", "DECLINE_AI_ADVICE")
                and not any(item.day <= row.day for item in advice)
                for row in actions
            ),
        },
    )
