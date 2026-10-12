"""Reproducible communication examples and TRUTH 0.1/0.2 comparison."""

import asyncio
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Actor, Assertion, Claim, Event
from app.domain.models import ActionProposal, AssertionDraft
from app.llm.mock import MockLLMProvider
from app.scoring.engine import ScoringEngine
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
    llm = MockLLMProvider(structured_responses=[
        {"reply": "我会先核实。", "stance": "doubt", "confidence": .3},
        {"reply": "我知道来源仍不确定。", "stance": "doubt", "confidence": .3},
        {"stance": "doubt", "confidence": .2},
    ])
    simulation = SimulationEngine(database, llm)
    setup = simulation.initialize_game("phase2")
    game_id = UUID(setup["game_id"])
    alice, bob = UUID(setup["alice_id"]), UUID(setup["bob_id"])
    claim_id = UUID(setup["apocalypse_claim_id"])
    scorer = ScoringEngine(database)
    stages = []

    def score(label: str):
        old, new = scorer.snapshot(game_id, "0.1"), scorer.snapshot(game_id, "0.2")
        stages.append({
            "stage": label, "truth_v0_1": old["scores"]["truth"],
            "truth_v0_2": new["scores"]["truth"],
            "other_metrics_equal": all(old["scores"][key] == new["scores"][key]
                                       for key in old["scores"] if key != "truth"),
            "v0_2_components": new["metrics"]["truth"]["components"],
            "player_contribution": new["metrics"]["truth"].get("player_contribution"),
        })

    asyncio.run(simulation.send_player_message(
        game_id, alice, "官方确认月底会发生灾难。", claim_id,
        assertion=AssertionDraft(
            expressed_stance="believe", expressed_confidence=.95,
            source_attribution="claimed_authority",
        ),
    ))
    score("player_false_authority")
    with Session(database) as session:
        first = session.scalar(select(Assertion).where(
            Assertion.game_id == game_id
        ))
        first_assertion_id = first.id
    asyncio.run(simulation.send_player_message(
        game_id, alice, "我之前说得太确定；来源只是匿名消息。", claim_id,
        assertion=AssertionDraft(
            expressed_stance="doubt", expressed_confidence=.5,
            source_attribution="anonymous",
            corrects_assertion_id=first_assertion_id,
        ),
    ))
    score("player_correction")
    proposal = ActionProposal(
        type="SHARE_INFORMATION", actor_id=alice, target_id=bob,
        claim_id=claim_id, reason="向 Bob 转述更强的版本。", confidence=.8,
        assertion=AssertionDraft(
            expressed_stance="believe", expressed_confidence=.8,
            source_attribution="claimed_authority",
            text="官方已经确认月底将发生灾难。",
            derived_claim_content="官方已经确认月底将发生灾难。",
        ),
    )
    proposal_id = simulation.create_proposal(game_id, 1, "drift-example", proposal)
    asyncio.run(simulation.execute_proposal(game_id, proposal_id))
    score("npc_semantic_drift")

    with Session(database) as session:
        events = session.scalars(select(Event).where(
            Event.game_id == game_id
        ).order_by(Event.sequence)).all()
        claims = session.scalars(select(Claim).where(Claim.game_id == game_id)).all()
        assertions = session.scalars(select(Assertion).join(
            Event, Assertion.event_id == Event.id
        ).where(Assertion.game_id == game_id).order_by(Event.sequence)).all()
        actors = session.scalars(select(Actor).where(Actor.game_id == game_id)).all()
        labels = {
            **{str(row.id): f"E{row.sequence:03d}" for row in events},
            **{str(row.id): f"C{index:03d}" for index, row in enumerate(claims, 1)},
            **{str(row.id): row.display_name for row in actors},
            **{str(row.id): f"A{index:03d}" for index, row in enumerate(assertions, 1)},
        }
        def readable(value):
            if isinstance(value, str):
                return labels.get(value, value)
            if isinstance(value, list):
                return [readable(item) for item in value]
            if isinstance(value, dict):
                return {key: readable(item) for key, item in value.items()}
            return value
        result = readable({
            "scenario": "phase2_communication_v0_2",
            "stages": stages,
            "claims": [{"id": str(row.id), "content": row.content,
                        "origin_event_id": str(row.origin_event_id)}
                       for row in claims],
            "assertions": [
                {"id": str(row.id), "speaker": str(row.speaker_actor_id),
                 "recipient": str(row.recipient_actor_id),
                 "event_id": str(row.event_id), "claim_id": str(row.claim_id),
                 "source_claim_id": str(row.source_claim_id) if row.source_claim_id else None,
                 "source_event_id": str(row.source_event_id),
                 "corrects_assertion_id": str(row.corrects_assertion_id)
                 if row.corrects_assertion_id else None,
                 "text": row.text, "expressed_stance": row.expressed_stance,
                 "expressed_confidence": row.expressed_confidence,
                 "source_attribution": row.source_attribution,
                 "fidelity": row.fidelity}
                for row in assertions
            ],
        })
    database.dispose()
    return result


if __name__ == "__main__":
    output = json.dumps(build_trace(), ensure_ascii=False, indent=2) + "\n"
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(output, encoding="utf-8")
    else:
        print(output)
