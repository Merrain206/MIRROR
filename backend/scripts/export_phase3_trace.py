"""Export the deterministic six-character Day 1 to Day 2 audit trace."""

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
    ActionProposalRow, Actor, Event, Knowledge, KnowledgeSource,
    RelationshipChange,
)
from app.llm.society_demo import SocietyDemoLLMProvider
from app.projection.player import DebugWorldView, PlayerProjectionService
from app.seeds.society import load_society_seed
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
    simulation = SimulationEngine(database, SocietyDemoLLMProvider())
    setup = simulation.initialize_game("society")
    game_id = UUID(setup["game_id"])
    actor_ids = {key: UUID(value) for key, value in setup["character_ids"].items()}

    def matrix(session: Session) -> dict[str, list[str]]:
        return {
            key: [row["claim_id"] for row in build_character_context(
                session, game_id, actor_id
            ).known_claims]
            for key, actor_id in actor_ids.items()
        }

    with Session(database) as session:
        before = matrix(session)
    asyncio.run(simulation.send_player_message(
        game_id, actor_ids["A"], "我收到一条关于世界末日的消息。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    asyncio.run(simulation.advance_day(game_id, expected_day=1))

    with Session(database) as session:
        world = DebugWorldView(session).snapshot(game_id)
        actor_names = {str(row.id): row.display_name for row in session.scalars(
            select(Actor).where(Actor.game_id == game_id)
        )}
        event_names = {
            row["id"]: f"E{row['sequence']:03d}" for row in world["events"]
        }
        claim_names = {
            row["id"]: f"C{index:03d}"
            for index, row in enumerate(world["claims"], start=1)
        }
        names = {**actor_names, **event_names, **claim_names}

        def readable(value):
            if isinstance(value, str):
                return names.get(value, value)
            if isinstance(value, list):
                return [readable(item) for item in value]
            if isinstance(value, dict):
                return {key: readable(item) for key, item in value.items()}
            return value

        knowledge_rows = session.scalars(select(Knowledge).where(
            Knowledge.game_id == game_id,
        )).all()
        sources = session.scalars(select(KnowledgeSource).where(
            KnowledgeSource.game_id == game_id,
        )).all()
        source_by_knowledge = {row.knowledge_id: row for row in sources}
        knowledge = sorted(({
            "holder": actor_names[str(row.holder_actor_id)],
            "claim": claim_names[str(row.claim_id)],
            "stance": row.stance, "confidence": row.confidence,
            "acquired_day": row.first_acquired_day,
            "source_actor": actor_names.get(str(source_by_knowledge[row.id].source_actor_id))
            if source_by_knowledge[row.id].source_actor_id else None,
            "source_event": event_names[str(source_by_knowledge[row.id].source_event_id)],
        } for row in knowledge_rows), key=lambda row: (row["holder"], row["claim"]))
        proposals = session.scalars(select(ActionProposalRow).where(
            ActionProposalRow.game_id == game_id,
        )).all()
        changes = session.scalars(select(RelationshipChange).where(
            RelationshipChange.game_id == game_id,
        )).all()
        seed = load_society_seed()
        result = {
            "scenario": seed["version"],
            "initial_knowledge_matrix": readable(before),
            "final_knowledge_matrix": readable(matrix(session)),
            "baseline_interpretations": [
                {"actor": row["name"], **row["apocalypse_baseline"]}
                for row in seed["characters"]
            ],
            "actions": sorted([{
                "actor": actor_names[str(row.actor_id)], "type": row.type,
                "status": row.status,
                "proposal_event": event_names[str(row.proposal_event_id)],
                "result_event": event_names[str(row.result_event_id)],
            } for row in proposals], key=lambda row: row["proposal_event"]),
            "events": [
                {"id": event_names[row["id"]], "day": row["day"],
                 "type": row["type"], "causes": readable(row["causes"]),
                 "data": readable(row["data"])}
                for row in world["events"]
            ],
            "knowledge": knowledge,
            "relationship_changes": [
                {"event": event_names[str(row.relationship_event_id)],
                 "from": actor_names[str(row.from_actor_id)],
                 "to": actor_names[str(row.to_actor_id)],
                 "before": row.before_values, "after": row.after_values,
                 "reason": row.reason}
                for row in sorted(changes,
                                  key=lambda item: event_names[str(item.relationship_event_id)])
            ],
            "player_timeline": [
                {"event": event_names[row["id"]], "type": row["type"]}
                for row in PlayerProjectionService(session).timeline(game_id)
            ],
            "debug_observations": sorted([
                {"event": event_names[row["event_id"]],
                 "observer": actor_names[row["actor_id"]]}
                for row in world["observations"]
            ], key=lambda row: (row["event"], row["observer"])),
        }
    database.dispose()
    return result


if __name__ == "__main__":
    output = json.dumps(build_trace(), ensure_ascii=False, indent=2) + "\n"
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(output, encoding="utf-8")
    else:
        print(output)
