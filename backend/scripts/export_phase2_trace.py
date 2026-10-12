"""Print a normalized, reproducible trace of the first causal slice."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Actor, KnowledgeSource, RelationshipChange
from app.llm.demo import DemoLLMProvider
from app.projection.player import DebugWorldView, PlayerProjectionService
from app.simulation.engine import SimulationEngine


def main() -> None:
    database = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(database, "connect")
    def foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(database)
    simulation = SimulationEngine(database, DemoLLMProvider())
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    asyncio.run(simulation.send_player_message(
        game_id, UUID(setup["alice_id"]),
        "我收到一条消息：世界可能在 30 天后毁灭。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    asyncio.run(simulation.advance_day(game_id, expected_day=1))

    with Session(database) as session:
        world = DebugWorldView(session).snapshot(game_id)
        actor_names = {
            str(row.id): row.display_name for row in session.scalars(
                select(Actor).where(Actor.game_id == game_id)
            )
        }
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

        source_rows = session.scalars(
            select(KnowledgeSource).where(KnowledgeSource.game_id == game_id)
        ).all()
        sources_by_knowledge = {}
        for row in source_rows:
            sources_by_knowledge.setdefault(str(row.knowledge_id), []).append(
                {"actor": readable(str(row.source_actor_id)) if row.source_actor_id else None,
                 "event": readable(str(row.source_event_id))}
            )
        knowledge = [
            {"holder": readable(row["holder_id"]),
             "claim": readable(row["claim_id"]),
             "stance": row["stance"], "confidence": row["confidence"],
             "sources": sources_by_knowledge.get(row["id"], []),
             "last_event": readable(row["last_event_id"])}
            for row in world["knowledge"]
        ]
        changes = session.scalars(
            select(RelationshipChange).where(RelationshipChange.game_id == game_id)
        ).all()
        result = {
            "events": [
                {"id": event_names[row["id"]], "day": row["day"],
                 "type": row["type"], "causes": readable(row["causes"]),
                 "data": readable(row["data"])}
                for row in world["events"]
            ],
            "claims": [
                {"id": claim_names[row["id"]], "content": row["content"],
                 "owner": readable(row["owner_actor_id"]),
                 "origin_event": readable(row["origin_event_id"])}
                for row in world["claims"]
            ],
            "alice_knowledge": [row for row in knowledge if row["holder"] == "Alice"],
            "bob_knowledge": [row for row in knowledge if row["holder"] == "Bob"],
            "player_ai_knowledge": [
                row for row in knowledge if row["holder"] == "Player AI"
            ],
            "privacy_violation": next(
                readable(row["data"]) for row in world["events"]
                if row["type"] == "PRIVACY_VIOLATION"
            ),
            "relationship_changes": [
                {"event": readable(str(row.relationship_event_id)),
                 "before": row.before_values, "after": row.after_values,
                 "reason": row.reason}
                for row in changes
            ],
            "player_timeline": [
                {"event": readable(row["id"]), "type": row["type"],
                 "day": row["day"]}
                for row in PlayerProjectionService(session).timeline(game_id)
            ],
        }
        output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if len(sys.argv) == 2:
            Path(sys.argv[1]).write_text(output, encoding="utf-8")
        else:
            print(output)
    database.dispose()


if __name__ == "__main__":
    main()
