import asyncio
import json
from collections import Counter
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import ActionProposalRow, Character, Event, Game, Intention
from app.domain.errors import ActionRejected, WorldConsistencyError
from app.domain.models import ActionProposal, AdviceReply
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.main import create_app
from app.projection.player import PlayerProjectionService
from app.simulation.context import build_character_context
from app.simulation.emotion import update_observed_emotion
from app.simulation.engine import SimulationEngine
from app.simulation.events import record_event
from app.simulation.intentions import abandon_intention
from app.simulation.knowledge import acquire_observed_claim
from scripts.export_phase3_5_trace import build_trace


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def start_society(database):
    simulation = SimulationEngine(database, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    return simulation, setup, UUID(setup["game_id"])


def test_player_advance_progress_exposes_counts_without_private_actions(database):
    client = TestClient(create_app(engine=database, llm=TemporalSocietyDemoLLMProvider(),
                                   debug=False, playtest=True))
    game_id = client.post("/games").json()["game_id"]
    url = f"/games/{game_id}/advance-progress"
    initial = client.get(url).json()
    assert initial == {"day": 1, "phase": "SEED", "stage": "preparing",
                       "completed_characters": 0, "total_characters": 6}
    assert client.post(f"/games/{game_id}/advance", json={"expected_day": 1}).status_code == 200
    completed = client.get(url).json()
    assert completed == {"day": 2, "phase": "SEED", "stage": "characters",
                         "completed_characters": 6, "total_characters": 6}


def test_late_disclosure_uses_day_30_deadline(database):
    simulation, setup, game_id = start_society(database)
    for day in range(1, 4):
        asyncio.run(simulation.advance_day(game_id, day))
    asyncio.run(simulation.send_player_message(
        game_id, UUID(setup["bob_id"]), "我有一条旧传闻想请你核实。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    with Session(database) as session:
        conversation = PlayerProjectionService(session).conversation(game_id, UUID(setup["bob_id"]))
        known = build_character_context(session, game_id, UUID(setup["bob_id"])).known_claims
    apocalypse = next(row for row in known if row["claim_id"] == setup["apocalypse_claim_id"])
    assert "Day 30 毁灭" in conversation[0]["text"]
    assert "还有 26 天" in conversation[0]["text"]
    assert "还有 30 天" not in conversation[0]["text"]
    assert apocalypse["deadline_day"] == 30
    assert apocalypse["remaining_days"] == 26


def test_seven_day_trace_has_delays_drift_and_isolation():
    trace = build_trace()
    days = trace["days"]
    assert [row["day"] for row in days] == list(range(1, 8))
    assert trace["player_messages_by_day"] == {
        "1": 1, **{str(day): 0 for day in range(2, 8)}
    }
    assert all(set(row["knowledge_matrix"]["C001"]) == set("ABCDEF")
               for row in days)
    assert {key for key, value in days[0]["knowledge_matrix"]["C001"].items()
            if value} == {"B"}
    assert {key for key, value in days[6]["knowledge_matrix"]["C001"].items()
            if value} == {"A", "B", "E", "F"}
    assert all(days[6]["knowledge_matrix"]["C001"][key] is None
               for key in ("C", "D"))
    assert all(max(Counter(action["actor_id"] for action in day["actions"]).values(),
                   default=0) <= 2 for day in days)
    assert any(event["type"] == "NO_ACTION" for day in days
               for event in day["events"])
    assert any(action["type"] == "CHANGE_PLAN" for action in days[1]["actions"])
    assert {action["type"] for day in days for action in day["actions"]
            if action["actor_id"] == "Evan"} >= {
        "SHARE_INFORMATION", "VERIFY_CLAIM", "INFER_CLAIM"
    }
    assert {action["type"] for day in days for action in day["actions"]
            if action["actor_id"] == "Alice"} == {
        "ASK_AI", "SHARE_INFORMATION"
    }

    first, second = days[1]["intentions"][0], days[2]["intentions"][1]
    assert (first["actor_id"], first["target_id"], first["created_day"]) == (
        "Bob", "Dana", 2
    )
    assert first["status"] == "pending"
    assert days[2]["intentions"][0]["status"] == "abandoned"
    assert (second["actor_id"], second["target_id"], second["created_day"]) == (
        "Bob", "Evan", 3
    )
    assert days[3]["intentions"][1]["status"] == "completed"
    bob_action = next(action for action in days[3]["actions"]
                      if action["actor_id"] == "Bob")
    assert bob_action["type"] == "SHARE_INFORMATION"
    assert bob_action["intention_id"] == second["id"]
    assert "intention" in bob_action["motivation_factors"]
    assert any(event["type"] == "INTENTION_ABANDONED"
               and len(event["causes"]) == 2 for event in days[2]["events"])
    assert any(event["type"] == "INTENTION_COMPLETED"
               and bob_action["result_event_id"] in event["causes"]
               for event in days[3]["events"])

    drift = next(claim for claim in trace["claims"]
                 if "无法独立核实的灾难预警" in claim["content"])
    assert drift["id"] != "C001"
    inferred = next(event for event in days[4]["events"]
                    if event["id"] == drift["origin_event_id"])
    assert inferred["type"] == "CLAIM_INFERRED"
    assert inferred["data"]["basis_claim_ids"] == ["C001"]
    assert len(inferred["causes"]) >= 2
    assert days[5]["knowledge_matrix"]["C001"]["B"]["stance"] == "doubt"
    assert days[6]["knowledge_matrix"]["C001"]["B"] == {
        "stance": "deny", "confidence": 0.72,
        "source_event_id": next(event["id"] for event in days[6]["events"]
                                if event["type"] == "BELIEF_REVISED"),
    }
    revision = next(event for event in days[6]["events"]
                    if event["type"] == "BELIEF_REVISED")
    assert revision["data"]["basis_claim_ids"] == [drift["id"]]
    assert revision["data"]["previous_stance"] == "doubt"
    assert next(row for row in trace["claims"] if row["id"] == "C001")[
        "truth_status"
    ] == "unknown"
    assert days[0]["character_states"]["B"]["relationships"]["Evan"]["trust"] == 70
    assert days[1]["character_states"]["B"]["relationships"]["Evan"]["trust"] == 65
    assert days[5]["character_states"]["B"]["relationships"]["Evan"]["trust"] == 60
    assert all(abs(change["after"]["trust"] - change["before"]["trust"]) <= 5
               for day in days for change in day["relationship_changes"])
    assert days[1]["character_states"]["C"]["current_goals"]["short_term"] == (
        days[6]["character_states"]["C"]["current_goals"]["short_term"]
    )
    assert days[6]["character_states"]["C"]["current_goals"]["plan_revision"][
        "source_event_id"
    ].startswith("E")
    assert days[2]["character_states"]["D"]["ai_interaction"]["advice_rejected"] == 1
    assert days[3]["character_states"]["D"]["ai_interaction"]["self_decisions"] == 1
    assert days[1]["character_states"]["A"]["ai_interaction"][
        "emotional_disclosures"
    ] == 1
    assert days[6]["character_states"]["A"]["ai_interaction"][
        "decision_deferred_until_response"
    ] == 0
    assert days[1]["character_states"]["D"]["ai_interaction"][
        "decision_deferred_until_response"
    ] == 1
    assert "ASK_AI" in {action["type"] for action in days[1]["actions"]}
    assert "AI_ADVICE_REJECTED" in {event["type"] for event in days[2]["events"]}
    assert all(event["type"] not in {
        "CHARACTER_SHARED_INFORMATION", "INTENTION_CREATED", "NO_ACTION",
        "EMOTION_CHANGED", "AI_ADVICE_REJECTED",
    } for event in trace["player_timeline"])
    assert all("Dana 收到外地进修机会" not in str(event["data"])
               for event in trace["player_timeline"])


def test_trace_document_is_reproducible():
    path = Path(__file__).resolve().parents[2] / "docs" / "PHASE3_5_TRACE.json"
    assert json.loads(path.read_text(encoding="utf-8")) == build_trace()


def test_day_four_context_keeps_prior_memory_and_private_knowledge(database):
    simulation, setup, game_id = start_society(database)
    advice_contexts = []
    original_generate = simulation.llm.generate_structured

    async def capture_advice(request, schema):
        if schema is AdviceReply:
            advice_contexts.append(request.context)
        return await original_generate(request, schema)

    simulation.llm.generate_structured = capture_advice
    ids = {key: UUID(value) for key, value in setup["character_ids"].items()}
    asyncio.run(simulation.send_player_message(
        game_id, ids["B"], "我收到一条尚未核实的末日消息。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    for day in range(1, 4):
        asyncio.run(simulation.advance_day(game_id, day))
    assert len(advice_contexts) == 1
    assert set(advice_contexts[0]) == {"ask_text", "ask_event_id"}
    assert "Dana 收到外地进修机会" not in str(advice_contexts[0])
    with Session(database) as session:
        bob = build_character_context(session, game_id, ids["B"])
        clara = build_character_context(session, game_id, ids["C"])
        dana = build_character_context(session, game_id, ids["D"])
        faye = build_character_context(session, game_id, ids["F"])
        assert any(row["day"] == 2 and "先等待" in row["content"]
                   for row in bob.memories)
        assert any(row["status"] == "completed" for row in bob.intentions)
        assert bob.emotional_state["anxiety"] > 0.5
        assert clara.goals["plan_revision"]["current"] == clara.goals["short_term"]
        assert clara.goals["plan_revision"]["previous"] != clara.goals["short_term"]
        assert dana.ai_interaction["advice_requested"] == 1
        assert dana.ai_interaction["advice_rejected"] == 1
        assert setup["apocalypse_claim_id"] not in {
            row["claim_id"] for row in clara.known_claims + dana.known_claims
            + faye.known_claims
        }
        assert len(bob.memories) <= 10
        timeline = PlayerProjectionService(session).timeline(game_id)
        assert not any(row["type"] in ("INTENTION_CREATED", "NO_ACTION")
                       for row in timeline)


def test_budget_cooldown_and_closed_day_are_validated(database):
    simulation, setup, game_id = start_society(database)
    faye = UUID(setup["character_ids"]["F"])
    evidence = UUID(setup["secret_claim_ids"]["s_evidence"])
    ask = ActionProposal(
        type="ASK_AI", actor_id=faye, basis_claim_ids=[evidence],
        reason="问一种想法", confidence=0.7,
    )
    first = simulation.create_proposal(game_id, 1, "manual-ask-1", ask)
    assert asyncio.run(simulation.execute_proposal(game_id, first))["accepted"]
    second = simulation.create_proposal(game_id, 1, "manual-ask-2", ask)
    assert not asyncio.run(simulation.execute_proposal(game_id, second))["accepted"]
    withhold = ActionProposal(
        type="WITHHOLD_INFORMATION", actor_id=faye, claim_id=evidence,
        reason="暂缓披露", confidence=0.8,
    )
    third = simulation.create_proposal(game_id, 1, "manual-withhold-1", withhold)
    assert asyncio.run(simulation.execute_proposal(game_id, third))["accepted"]
    fourth = simulation.create_proposal(game_id, 1, "manual-withhold-2", withhold)
    assert not asyncio.run(simulation.execute_proposal(game_id, fourth))["accepted"]
    with Session(database) as session:
        assert len(session.scalars(select(Event).where(
            Event.game_id == game_id, Event.type == "ACTION_REJECTED",
        )).all()) == 2

    another, other_setup, other_game = start_society(database)
    asyncio.run(another.advance_day(other_game, 1))
    other_faye = UUID(other_setup["character_ids"]["F"])
    other_evidence = UUID(other_setup["secret_claim_ids"]["s_evidence"])
    with pytest.raises(ActionRejected):
        another.create_proposal(other_game, 2, "late-action", ActionProposal(
            type="WITHHOLD_INFORMATION", actor_id=other_faye,
            claim_id=other_evidence, reason="太晚了", confidence=0.8,
        ))


def test_cross_day_repeat_needs_new_observed_information(database):
    simulation, setup, game_id = start_society(database)
    faye = UUID(setup["character_ids"]["F"])
    evidence = UUID(setup["secret_claim_ids"]["s_evidence"])
    ask = ActionProposal(
        type="ASK_AI", actor_id=faye, basis_claim_ids=[evidence],
        reason="问一种想法", confidence=0.7,
    )
    first = simulation.create_proposal(game_id, 1, "ask-day-one", ask)
    assert asyncio.run(simulation.execute_proposal(game_id, first))["accepted"]
    with simulation.sessions.begin() as session:
        game = session.get(Game, game_id)
        record_event(session, game, day=1, type="DAY_ENDED", data={"day": 1})
        game.day = 2
        record_event(session, game, day=2, type="DAY_BEGAN", data={"day": 2})
    repeated = simulation.create_proposal(game_id, 2, "ask-day-two-no-news", ask)
    assert not asyncio.run(simulation.execute_proposal(game_id, repeated))["accepted"]
    with simulation.sessions.begin() as session:
        game = session.get(Game, game_id)
        observed = record_event(
            session, game, day=2, type="WORK_RECORD_REVIEWED",
            actor_id=faye, audiences=(faye,),
            data={"claim_id": str(evidence)},
        )
        acquire_observed_claim(
            session, game, observed_event=observed,
            holder_id=faye, claim_id=evidence,
            source_actor_id=None, stance="believe", confidence=1.0,
        )
    retried = simulation.create_proposal(game_id, 2, "ask-day-two-new-source", ask)
    assert asyncio.run(simulation.execute_proposal(game_id, retried))["accepted"]


def test_unobserved_events_cannot_change_emotion_or_abandon_intention(database):
    simulation, setup, game_id = start_society(database)
    ids = {key: UUID(value) for key, value in setup["character_ids"].items()}
    asyncio.run(simulation.send_player_message(
        game_id, ids["B"], "我收到末日消息。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    asyncio.run(simulation.advance_day(game_id, 1))
    with Session(database) as session:
        bob = session.get(Character, ids["B"])
        faye = session.get(Character, ids["F"])
        assert bob.emotional_state["anxiety"] > 0.5
        faye_before = dict(faye.emotional_state)
        private_message = session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "MESSAGE_SENT",
            Event.target_actor_id == ids["B"],
        ))
        with pytest.raises(WorldConsistencyError):
            update_observed_emotion(
                session, session.get(Game, game_id), ids["F"],
                private_message, anxiety=0.2,
            )
        assert faye.emotional_state == faye_before
        intention = session.scalar(select(Intention).where(
            Intention.game_id == game_id, Intention.actor_id == ids["B"],
        ))
        clara_secret = session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "SECRET_ORIGIN",
            Event.actor_id == ids["C"],
        ))
        with pytest.raises(ActionRejected):
            abandon_intention(
                session, session.get(Game, game_id), intention.id, ids["B"],
                "没看到这个事件", clara_secret.id,
            )
        assert intention.status == "pending"


def test_intention_composite_foreign_key_rejects_other_game_target(database):
    simulation, setup, game_id = start_society(database)
    other_setup = simulation.initialize_game("temporal")
    ids = {key: UUID(value) for key, value in setup["character_ids"].items()}
    asyncio.run(simulation.send_player_message(
        game_id, ids["B"], "我收到末日消息。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    asyncio.run(simulation.advance_day(game_id, 1))
    with pytest.raises(IntegrityError), Session(database) as session:
        with session.begin():
            intention = session.scalar(select(Intention).where(
                Intention.game_id == game_id, Intention.actor_id == ids["B"],
            ))
            intention.target_actor_id = UUID(other_setup["character_ids"]["D"])
            session.flush()


def test_interrupted_day_resumes_without_reordering_earlier_turns(database):
    simulation, setup, game_id = start_society(database)
    ids = {key: UUID(value) for key, value in setup["character_ids"].items()}
    asyncio.run(simulation.send_player_message(
        game_id, ids["B"], "我收到一条尚未核实的末日消息。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    for day in range(1, 5):
        asyncio.run(simulation.advance_day(game_id, day))
    original_execute = simulation.execute_proposal
    evan_calls = 0

    async def interrupted(game_id_arg, proposal_id):
        nonlocal evan_calls
        with Session(database) as session:
            row = session.get(ActionProposalRow, proposal_id)
            if row.day == 6 and row.actor_id == ids["E"]:
                evan_calls += 1
                if evan_calls == 2:
                    raise RuntimeError("simulated interruption")
        return await original_execute(game_id_arg, proposal_id)

    simulation.execute_proposal = interrupted
    with pytest.raises(RuntimeError, match="simulated interruption"):
        asyncio.run(simulation.advance_day(game_id, 5))
    simulation.execute_proposal = original_execute
    with Session(database) as session:
        assert session.get(Game, game_id).day == 6
        assert session.scalar(select(Event).where(
            Event.game_id == game_id, Event.day == 6,
            Event.type == "DAY_ENDED",
        )) is None
    assert asyncio.run(simulation.advance_day(game_id, 5)) == {
        "day": 6, "replayed": False
    }
    with Session(database) as session:
        alice = build_character_context(session, game_id, ids["A"])
        faye = build_character_context(session, game_id, ids["F"])
        assert setup["apocalypse_claim_id"] in {
            row["claim_id"] for row in alice.known_claims
        }
        assert setup["apocalypse_claim_id"] not in {
            row["claim_id"] for row in faye.known_claims
        }
        assert len(session.scalars(select(Event).where(
            Event.game_id == game_id, Event.day == 6,
            Event.type == "TURN_EVALUATED",
            Event.actor_id == ids["A"],
        )).all()) == 1
        assert len(session.scalars(select(ActionProposalRow).where(
            ActionProposalRow.game_id == game_id,
            ActionProposalRow.day == 6,
            ActionProposalRow.actor_id == ids["E"],
            ActionProposalRow.status == "accepted",
        )).all()) == 2
    assert asyncio.run(simulation.advance_day(game_id, 6))["day"] == 7


def test_default_api_continues_after_day_seven(database):
    client = TestClient(create_app(database, debug=False))
    setup = client.post("/games").json()
    game_id = setup["game_id"]
    assert client.post(f"/games/{game_id}/messages", json={
        "character_id": setup["character_ids"]["B"],
        "text": "我收到末日消息。",
        "disclosed_claim_id": setup["apocalypse_claim_id"],
    }).status_code == 200
    for day in range(1, 7):
        response = client.post(f"/games/{game_id}/advance",
                               json={"expected_day": day})
        assert response.status_code == 200
        assert response.json()["day"] == day + 1
    assert client.post(f"/games/{game_id}/advance",
                       json={"expected_day": 7}).json()["day"] == 8
    assert client.get(f"/games/{game_id}").json()["day"] == 8
