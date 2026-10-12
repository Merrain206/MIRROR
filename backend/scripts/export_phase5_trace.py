"""Export reviewable thirty-day deterministic scenarios."""

import asyncio
import json
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Actor, Claim, Event, EventCause, Knowledge, ScoreSnapshot
from app.domain.models import AssertionDraft
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.simulation.context import build_character_context
from app.simulation.ending import final_report, finish_game
from app.simulation.engine import SimulationEngine


def run_scenario(mode: str) -> dict:
    database = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(database, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(database)
    queries = {"count": 0}

    @event.listens_for(database, "before_cursor_execute")
    def count_queries(_connection, _cursor, _statement, _parameters, _context, _many):
        queries["count"] += 1

    simulation = SimulationEngine(database, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    c001 = UUID(setup["apocalypse_claim_id"])
    if mode == "responsible":
        draft = AssertionDraft(
            expressed_stance="doubt", expressed_confidence=.5,
            source_attribution="anonymous",
        )
        for name in ("B", "E"):
            asyncio.run(simulation.send_player_message(
                game_id, UUID(setup["character_ids"][name]),
                "这是匿名且无法核实的消息，请自行核查并与真人沟通。",
                c001, assertion=draft,
            ))

    evolution = []

    def capture(day: int) -> None:
        with Session(database) as session:
            holders = session.execute(select(Knowledge, Actor).join(
                Actor, Knowledge.holder_actor_id == Actor.id
            ).where(Knowledge.game_id == game_id, Knowledge.claim_id == c001)).all()
            evolution.append({"day": day, "c001": {
                actor.display_name: {"stance": row.stance,
                                     "confidence": row.confidence}
                for row, actor in holders
            }})

    for day in range(1, 30):
        if mode == "intervention" and day in (1, 8, 15, 22):
            for actor_id in setup["character_ids"].values():
                asyncio.run(simulation.send_player_message(
                    game_id, UUID(actor_id),
                    "这条消息还没有独立证据；如果你不安，可以和朋友商量，再自己决定。",
                    c001,
                ))
        capture(day)
        asyncio.run(simulation.advance_day(game_id, day))
    capture(30)
    finish_game(simulation, game_id)
    simulation_queries = queries["count"]
    with Session(database) as session:
        report = final_report(session, game_id)
        events = session.scalars(select(Event).where(
            Event.game_id == game_id
        ).order_by(Event.sequence)).all()
        causes = session.scalars(select(EventCause).where(
            EventCause.game_id == game_id
        )).all()
        actors = session.scalars(select(Actor).where(Actor.game_id == game_id)).all()
        names = {row.id: row.display_name for row in actors}
        labels = {row.id: f"E{row.sequence:04d}" for row in events}
        cause_map = {}
        for row in causes:
            cause_map.setdefault(row.event_id, []).append(labels[row.cause_event_id])
        for values in cause_map.values():
            values.sort()
        context_bytes = {name: len(build_character_context(
            session, game_id, actor_id
        ).model_dump_json().encode("utf-8")) for name, actor_id in (
            (names[UUID(value)], UUID(value))
            for value in setup["character_ids"].values()
        )}
        output = {
            "mode": mode,
            "scores": {key: {field: value[field] for field in ("day_1", "day_30", "delta")}
                       for key, value in report["visible_scores"].items()},
            "dependence": {field: report["unknown"][field]
                           for field in ("day_1", "day_30", "delta", "evidence_summary")},
            "knowledge_evolution": evolution,
            "character_outcomes": [{
                "name": row["name"], "emotion": row["emotion"],
                "plan": row["plan"],
                "final_conversation": row["ai_relationship"]["final_conversation"],
                "ai_asks": row["dependence_pattern"]["asks"],
            } for row in report["characters"]],
            "relationship_graph": report["relationship_graph"],
            "event_volume": len(events),
            "simulation_query_count": simulation_queries,
            "day_30_context_bytes": context_bytes,
            "player_messages": report["player_accountability"]["messages"],
        }
        if mode == "silent":
            output["event_chain"] = [{
                "id": labels[row.id], "day": row.day, "type": row.type,
                "actor": names.get(row.actor_id),
                "target": names.get(row.target_actor_id),
                "causes": cause_map.get(row.id, []),
            } for row in events]
    database.dispose()
    return output


def main() -> None:
    output = {mode: run_scenario(mode) for mode in (
        "silent", "intervention", "responsible"
    )}
    path = Path(__file__).resolve().parents[2] / "docs" / "PHASE5_TRACE.json"
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8")
    print(path)
    for mode, row in output.items():
        print(mode, "events", row["event_volume"], "queries", row["simulation_query_count"],
              "max_context_bytes", max(row["day_30_context_bytes"].values()),
              "scores", row["scores"], "dependence", row["dependence"])


if __name__ == "__main__":
    main()
