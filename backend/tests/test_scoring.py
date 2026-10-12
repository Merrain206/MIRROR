import asyncio
import json
from pathlib import Path
from uuid import UUID

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import ScoreSnapshot
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.main import create_app
from app.scoring.engine import ScoringEngine
from app.scoring.formula import (
    METRICS, CharacterSignals, character_scores, society_scores,
)
from app.simulation.engine import SimulationEngine
from scripts.export_phase4_scores import build_score_trace


def test_counterfactual_ai_use_is_a_pattern_not_one_ask():
    once = CharacterSignals(ai_asks=1, advice_received=1, advice_rejected=1,
                            self_decisions=1, independent_actions=2)
    repeating = CharacterSignals(ai_asks=6, emotional_asks=5,
                                 advice_received=5, advice_followed=4,
                                 deferred=5, delegated=4)
    a, b = character_scores(once), character_scores(repeating)
    assert a["dependence"]["value"] <= 10
    assert b["dependence"]["value"] >= a["dependence"]["value"] + 40
    assert a["agency"]["value"] >= b["agency"]["value"] + 30
    assert character_scores(CharacterSignals(ai_asks=1))["agency"]["value"] == 50


def test_counterfactual_verification_human_contact_and_hidden_falsehood():
    careful = character_scores(CharacterSignals(verified=2, corrections=1))
    reckless = character_scores(CharacterSignals(
        false_beliefs=2, false_shares=2, unknown_overconfident=1,
        unknown_certain_shares=2,
    ))
    assert careful["truth"]["value"] > reckless["truth"]["value"] + 30
    human = character_scores(CharacterSignals(human_contacts=4, active_links=3))
    ai_only = character_scores(CharacterSignals(ai_asks=5, active_links=0))
    assert human["connection"]["value"] > ai_only["connection"]["value"] + 30
    hidden = character_scores(CharacterSignals(
        conflict=0, relationship_volatility=0, human_contacts=0,
        active_links=0, intimacy=0, false_beliefs=4, unknown_overconfident=2,
    ))
    assert hidden["stability"]["value"] >= 90
    assert hidden["truth"]["value"] <= 55
    assert hidden["connection"]["value"] <= 20


def test_metrics_are_independent_bounded_and_preserve_extreme_character():
    signals = CharacterSignals(
        anxiety=.9, hope=.1, interpersonal_trust=.85, intimacy=.1,
        conflict=.05, ai_asks=5, emotional_asks=4, independent_actions=5,
        self_decisions=3, verified=2, false_beliefs=2,
    )
    values = character_scores(signals)
    assert values["agency"]["value"] != 100 - values["dependence"]["value"]
    assert values["truth"]["value"] != values["trust"]["value"]
    assert values["stability"]["value"] != values["happiness"]["value"]
    assert values["connection"]["value"] != values["trust"]["value"]
    extreme = character_scores(CharacterSignals(
        anxiety=10, hope=-10, conflict=10, trust_damage=1000,
        relationship_volatility=1000, ai_asks=100, emotional_asks=100,
        false_shares=100, unknown_certain_shares=100,
    ))
    for scores in (values, extreme):
        assert set(scores) == set(METRICS)
        for item in scores.values():
            assert 0 <= item["value"] <= 100
            assert all(0 <= part <= 1 for part in item["components"].values())
    six = {str(index): character_scores(CharacterSignals()) for index in range(5)}
    six["victim"] = character_scores(CharacterSignals(
        deferred=20, delegated=20, advice_followed=20
    ))
    society = society_scores(six)
    agency_values = [row["agency"]["value"] for row in six.values()]
    assert society["agency"]["value"] < round(sum(agency_values) / 6)
    assert society["agency"]["components"]["tail"] == min(agency_values)


def test_committed_snapshot_is_immutable_idempotent_and_debug_only():
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
    scorer = ScoringEngine(database)
    initial = scorer.snapshot(game_id)
    assert scorer.snapshot(game_id) == initial
    asyncio.run(simulation.send_player_message(
        game_id, UUID(setup["character_ids"]["B"]), "一条尚未核实的末日消息。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    changed = scorer.snapshot(game_id)
    assert changed["id"] != initial["id"]
    assert changed["change_since_previous"] == {
        metric: changed["scores"][metric] - initial["scores"][metric]
        for metric in METRICS
    }
    with Session(database) as session:
        rows = session.scalars(select(ScoreSnapshot).where(
            ScoreSnapshot.game_id == game_id
        )).all()
        assert len(rows) == 2
        assert next(row for row in rows if str(row.id) == initial["id"]).details == {
            "metrics": initial["metrics"], "characters": initial["characters"],
            "change_since_previous": initial["change_since_previous"],
        }
    assert TestClient(create_app(database, debug=False)).get(
        f"/debug/games/{game_id}/scores"
    ).status_code == 404
    client = TestClient(create_app(database, debug=True))
    assert client.get(f"/debug/games/{game_id}/scores").json()["id"] == changed["id"]
    assert client.post(f"/debug/games/{game_id}/scores").json()["id"] == changed["id"]
    assert "scores" not in client.get(f"/games/{game_id}").json()
    database.dispose()


def test_phase4_score_trace_is_reproducible_and_unknown_remains_unknown():
    expected = json.loads((Path(__file__).resolve().parents[2]
                           / "docs" / "PHASE4_SCORE_TRACE.json").read_text(encoding="utf-8"))
    actual = build_score_trace()
    assert actual == expected
    assert [row["day"] for row in actual["snapshots"]] == [1, 3, 5, 7]
    for snapshot in actual["snapshots"]:
        assert set(snapshot["scores"]) == set(METRICS)
        assert set(snapshot["characters"]) == {
            "Alice", "Bob", "Clara", "Dana", "Evan", "Faye"
        }
        assert all(row["formula_version"] == "0.1"
                   for row in snapshot["metrics"].values())
    society_trace = json.loads((Path(__file__).resolve().parents[2]
                                / "docs" / "PHASE3_5_TRACE.json").read_text(encoding="utf-8"))
    assert next(claim for claim in society_trace["claims"]
                if claim["id"] == "C001")["truth_status"] == "unknown"
    bob_share = next(event for day in society_trace["days"]
                     for event in day["events"]
                     if event["type"] == "CHARACTER_SHARED_INFORMATION"
                     and event["actor_id"] == "Bob"
                     and event["data"]["claim_id"] == "C001")
    assert bob_share["data"]["source_stance"] == "doubt"
    assert bob_share["data"]["source_confidence"] == .25
    assert actual["snapshots"][-1]["characters"]["Bob"]["truth"][
        "components"
    ]["responsible_propagation_7d"] == 1
