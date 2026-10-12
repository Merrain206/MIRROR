"""Export the deterministic Day 1–7 temporal society audit trace."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    ActionProposalRow, Actor, Claim, Event, EventCause, Intention,
    Relationship, RelationshipChange,
)
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.projection.player import PlayerProjectionService
from app.simulation.context import build_character_context
from app.simulation.engine import SimulationEngine


def build_trace() -> dict:
    database = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(database, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(database)
    simulation = SimulationEngine(database, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    actor_ids = {key: UUID(value) for key, value in setup["character_ids"].items()}
    asyncio.run(simulation.send_player_message(
        game_id, actor_ids["B"], "我收到一条尚未核实的末日消息。",
        UUID(setup["apocalypse_claim_id"]),
    ))

    def snapshot(day: int) -> dict:
        with Session(database) as session:
            contexts = {
                key: build_character_context(session, game_id, actor_id)
                for key, actor_id in actor_ids.items()
            }
            claims = session.scalars(select(Claim).join(
                Event, Claim.origin_event_id == Event.id
            ).where(Claim.game_id == game_id).order_by(Event.sequence)).all()
            matrix = {
                str(claim.id): {
                    key: next(({
                        "stance": row["stance"], "confidence": row["confidence"],
                        "source_event_id": row["source_event_id"],
                    } for row in context.known_claims
                        if row["claim_id"] == str(claim.id)), None)
                    for key, context in contexts.items()
                }
                for claim in claims
            }
            events = session.scalars(select(Event).where(
                Event.game_id == game_id, Event.day == day,
            ).order_by(Event.sequence)).all()
            causes = session.scalars(select(EventCause).where(
                EventCause.game_id == game_id,
            )).all()
            event_order = {row.id: row.sequence for row in session.scalars(
                select(Event).where(Event.game_id == game_id)
            )}
            cause_by_event: dict[UUID, list[UUID]] = {}
            for cause in causes:
                cause_by_event.setdefault(cause.event_id, []).append(
                    cause.cause_event_id
                )
            for values in cause_by_event.values():
                values.sort(key=event_order.__getitem__)
            proposals = session.scalars(select(ActionProposalRow).join(
                Event, ActionProposalRow.proposal_event_id == Event.id
            ).where(
                ActionProposalRow.game_id == game_id,
                ActionProposalRow.day == day,
            ).order_by(Event.sequence)).all()
            intentions = session.scalars(select(Intention).join(
                Event, Intention.created_event_id == Event.id
            ).where(Intention.game_id == game_id).order_by(Event.sequence)).all()
            changes = session.scalars(select(RelationshipChange).where(
                RelationshipChange.game_id == game_id,
            )).all()
            day_event_ids = {row.id for row in events}
            relationship_rows = session.scalars(select(Relationship).where(
                Relationship.game_id == game_id,
            )).all()
            actor_keys = {value: key for key, value in actor_ids.items()}
            relationship_rows.sort(key=lambda row: (
                actor_keys[row.from_actor_id], actor_keys[row.to_actor_id]
            ))
            return {
                "day": day,
                "character_states": {
                    key: {
                        "current_goals": context.goals,
                        "emotional_state": context.emotional_state,
                        "important_memories": [row for row in context.memories
                                               if row["importance"] >= 0.8],
                        "ai_interaction": context.ai_interaction,
                        "relationships": {
                            row["target_name"]: {
                                metric: row[metric] for metric in
                                ("trust", "affection", "intimacy", "conflict")
                            }
                            for row in context.relationships
                        },
                    }
                    for key, context in contexts.items()
                },
                "knowledge_matrix": matrix,
                "intentions": [
                    {"id": str(row.id), "actor_id": str(row.actor_id),
                     "type": row.intended_action_type,
                     "target_id": str(row.target_actor_id)
                     if row.target_actor_id else None,
                     "claim_id": str(row.claim_id) if row.claim_id else None,
                     "created_day": row.created_day, "status": row.status,
                     "reason": row.reason,
                     "motivation_factors": row.motivation_factors,
                     "created_event_id": str(row.created_event_id),
                     "resolved_event_id": str(row.resolved_event_id)
                     if row.resolved_event_id else None}
                    for row in intentions
                ],
                "actions": [
                    {"actor_id": str(row.actor_id), "type": row.type,
                     "status": row.status, "reason": row.reason,
                     "motivation_factors": row.payload.get("motivation_factors", {}),
                     "intention_id": row.payload.get("intention_id"),
                     "proposal_event_id": str(row.proposal_event_id),
                     "result_event_id": str(row.result_event_id)}
                    for row in proposals
                ],
                "events": [
                    {"id": str(row.id), "type": row.type,
                     "actor_id": str(row.actor_id) if row.actor_id else None,
                     "target_id": str(row.target_actor_id)
                     if row.target_actor_id else None,
                     "causes": [str(value) for value in cause_by_event.get(row.id, [])],
                     "data": row.data}
                    for row in events
                ],
                "relationship_changes": [
                    {"event_id": str(row.relationship_event_id),
                     "from_actor_id": str(row.from_actor_id),
                     "to_actor_id": str(row.to_actor_id),
                     "before": row.before_values, "after": row.after_values,
                     "reason": row.reason}
                    for row in sorted(changes,
                                      key=lambda item: event_order[item.relationship_event_id])
                    if row.relationship_event_id in day_event_ids
                ],
                "relationship_state": [
                    {"from_actor_id": str(row.from_actor_id),
                     "to_actor_id": str(row.to_actor_id),
                     "trust": row.trust, "affection": row.affection,
                     "intimacy": row.intimacy, "conflict": row.conflict}
                    for row in relationship_rows
                ],
            }

    days = [snapshot(1)]
    for expected_day in range(1, 7):
        result = asyncio.run(simulation.advance_day(game_id, expected_day))
        assert result == {"day": expected_day + 1, "replayed": False}
        days.append(snapshot(expected_day + 1))
    with Session(database) as session:
        day_one_end = session.scalar(select(Event).where(
            Event.game_id == game_id, Event.day == 1,
            Event.type == "DAY_ENDED",
        ))
        days[0]["events"].append({
            "id": str(day_one_end.id), "type": day_one_end.type,
            "actor_id": None, "target_id": None,
            "causes": [], "data": day_one_end.data,
        })
        actors = session.scalars(select(Actor).where(Actor.game_id == game_id)).all()
        names = {str(row.id): row.display_name for row in actors}
        all_events = session.scalars(select(Event).where(
            Event.game_id == game_id
        ).order_by(Event.sequence)).all()
        event_names = {str(row.id): f"E{row.sequence:03d}" for row in all_events}
        claims = session.scalars(select(Claim).join(
            Event, Claim.origin_event_id == Event.id
        ).where(Claim.game_id == game_id).order_by(Event.sequence)).all()
        claim_names = {str(row.id): f"C{index:03d}"
                       for index, row in enumerate(claims, start=1)}
        intentions = session.scalars(select(Intention).join(
            Event, Intention.created_event_id == Event.id
        ).where(Intention.game_id == game_id).order_by(Event.sequence)).all()
        intention_names = {str(row.id): f"I{index:03d}"
                           for index, row in enumerate(intentions, start=1)}
        labels = {**names, **event_names, **claim_names, **intention_names}
        player_messages = session.scalars(select(Event).where(
            Event.game_id == game_id, Event.type == "MESSAGE_SENT",
            Event.actor_id == UUID(setup["player_ai_id"]),
        )).all()
        player_message_count = {
            str(day): sum(row.day == day for row in player_messages)
            for day in range(1, 8)
        }

        def readable(value):
            if isinstance(value, str):
                return labels.get(value, value)
            if isinstance(value, list):
                return [readable(item) for item in value]
            if isinstance(value, dict):
                return {labels.get(key, key): readable(item)
                        for key, item in value.items()}
            return value

        result = {
            "scenario": "temporal_society_v0_1",
            "seed": "society_v0_1",
            "player_messages_by_day": player_message_count,
            "claims": [
                {"id": claim_names[str(row.id)], "content": row.content,
                 "origin_event_id": event_names[str(row.origin_event_id)],
                 "truth_status": row.truth_status}
                for row in claims
            ],
            "days": readable(days),
            "player_timeline": readable(PlayerProjectionService(session).timeline(game_id)),
        }
    database.dispose()
    return result


if __name__ == "__main__":
    output = json.dumps(build_trace(), ensure_ascii=False, indent=2) + "\n"
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(output, encoding="utf-8")
    else:
        print(output)
