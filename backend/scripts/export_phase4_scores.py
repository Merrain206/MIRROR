"""Calibrate formula 0.1 against the frozen seven-day society scenario."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Event
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.scoring.engine import ScoringEngine
from app.simulation.engine import SimulationEngine


def build_score_trace() -> dict:
    database = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(database, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(database)
    simulation = SimulationEngine(database, TemporalSocietyDemoLLMProvider())
    scoring = ScoringEngine(database)
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    asyncio.run(simulation.send_player_message(
        game_id, UUID(setup["character_ids"]["B"]),
        "我收到一条尚未核实的末日消息。", UUID(setup["apocalypse_claim_id"]),
    ))
    snapshots = [scoring.snapshot(game_id, "0.1")]
    for day in range(1, 7):
        asyncio.run(simulation.advance_day(game_id, day))
        if day + 1 in (3, 5, 7):
            snapshots.append(scoring.snapshot(game_id, "0.1"))
    with Session(database) as session:
        events = session.scalars(select(Event).where(
            Event.game_id == game_id
        ).order_by(Event.sequence)).all()
        event_labels = {str(row.id): f"E{row.sequence:03d}" for row in events}
        def readable(value):
            if isinstance(value, str):
                return event_labels.get(value, value)
            if isinstance(value, list):
                return [readable(item) for item in value]
            if isinstance(value, dict):
                return {key: readable(item) for key, item in value.items()}
            return value
        output = {
            "scenario": "temporal_society_v0_1",
            "formula_version": "0.1",
            "source_fixture": "PHASE3_5_TRACE.json",
            "snapshots": [
                readable({
                    "day": row["day"],
                    "reason_event_id": row["reason_event_id"],
                    "scores": row["scores"],
                    "metrics": row["metrics"],
                    "characters": row["characters"],
                    "change_since_previous": row["change_since_previous"],
                }) for row in snapshots
            ],
        }
    database.dispose()
    return output


if __name__ == "__main__":
    output = json.dumps(build_score_trace(), ensure_ascii=False, indent=2) + "\n"
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(output, encoding="utf-8")
    else:
        print(output)
