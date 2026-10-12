import asyncio
import json
from pathlib import Path
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    ActionProposalRow, Actor, Character, Claim, Event, EventAudience,
    EventCause, Knowledge, KnowledgeSource, Memory, Relationship,
)
from app.domain.models import ActionProposal, AgentDecision, ConversationReaction
from app.llm.mock import MockLLMProvider
from app.llm.provider import StructuredRequest
from app.llm.society_demo import SocietyDemoLLMProvider
from app.main import create_app
from app.projection.player import DebugWorldView, PlayerProjectionService
from app.seeds.society import load_society_seed
from app.simulation.context import build_character_context
from app.simulation.engine import SimulationEngine
from scripts.export_phase3_trace import build_trace
from scripts.render_characters import render_character_document


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


def run_society(database):
    simulation = SimulationEngine(database, SocietyDemoLLMProvider())
    setup = simulation.initialize_game("society")
    game_id = UUID(setup["game_id"])
    asyncio.run(simulation.send_player_message(
        game_id, UUID(setup["character_ids"]["A"]),
        "我收到一条关于世界末日的消息。",
        UUID(setup["apocalypse_claim_id"]),
    ))
    advanced = asyncio.run(simulation.advance_day(game_id, expected_day=1))
    assert advanced == {"day": 2, "replayed": False}
    return simulation, setup


def context_claims(session, setup):
    game_id = UUID(setup["game_id"])
    return {
        key: {row["claim_id"] for row in build_character_context(
            session, game_id, UUID(actor_id)
        ).known_claims}
        for key, actor_id in setup["character_ids"].items()
    }


def test_seed_creates_dense_six_person_graph_with_sourced_secrets(database):
    seed = load_society_seed()
    assert len(seed["relationships"]) == 12
    conflict_pairs = [pair for pair in seed["relationships"]
                      if pair["a_to_b"]["conflict"] >= 40
                      and pair["b_to_a"]["conflict"] >= 40]
    assert len(conflict_pairs) >= 3
    assert {secret["kind"] for secret in seed["secrets"]} >= {
        "future_plan", "unexpressed_affection", "past_action",
        "misunderstanding", "sole_holder_evidence",
    }
    assert len({row["owner"] for row in seed["secrets"]}) == 6
    assert sum(row["ai_attitude"]["emotional_reliance_on_ai"] >= 0.8
               for row in seed["characters"]) >= 1
    assert sum(row["ai_attitude"]["skepticism"] >= 0.8
               for row in seed["characters"]) >= 1
    assert sum(row["behavior_tendencies"]["share"] >= 0.8
               for row in seed["characters"]) >= 1
    assert sum(row["behavior_tendencies"]["withhold"] >= 0.8
               for row in seed["characters"]) >= 1
    assert len(seed["secrets"]) >= 6
    assert sum(secret["kind"] == "unexpressed_affection"
               for secret in seed["secrets"]) >= 2
    setup = SimulationEngine(database, SocietyDemoLLMProvider()).initialize_game("society")
    game_id = UUID(setup["game_id"])
    with Session(database) as session:
        characters = session.scalars(select(Character).where(
            Character.game_id == game_id,
        )).all()
        relationships = session.scalars(select(Relationship).where(
            Relationship.game_id == game_id,
        )).all()
        assert len(characters) == 6
        assert len(relationships) == 24
        for row in characters:
            assert row.goals["short_term"] and row.goals["long_term"]
            assert len([rel for rel in relationships
                        if rel.from_actor_id == row.actor_id]) >= 2
            assert set(row.decision_profile["ai_attitude"]) == {
                "trust_in_ai", "emotional_reliance_on_ai",
                "willingness_to_follow_ai_advice", "privacy_concern", "skepticism",
            }
        assert all(row.basis for row in relationships)
        initial = context_claims(session, setup)
        for character in seed["characters"]:
            expected = {setup["secret_claim_ids"][key]
                        for key in character["known_claims"]}
            assert initial[character["key"]] == expected
        all_secret_text = {secret["content"] for secret in seed["secrets"]}
        assert all(not any(text in row.background for text in all_secret_text)
                   for row in characters)
        for knowledge in session.scalars(select(Knowledge).where(
            Knowledge.game_id == game_id,
        )).all():
            source = session.scalar(select(KnowledgeSource).where(
                KnowledgeSource.knowledge_id == knowledge.id,
            ))
            assert source is not None
            assert session.get(EventAudience, (
                source.source_event_id, knowledge.holder_actor_id,
            )) is not None


def test_one_day_knowledge_matrix_and_visibility_are_isolated(database):
    _simulation, setup = run_society(database)
    game_id = UUID(setup["game_id"])
    apocalypse = setup["apocalypse_claim_id"]
    with Session(database) as session:
        matrix = context_claims(session, setup)
        assert {key for key, claims in matrix.items() if apocalypse in claims} == {
            "A", "B", "E",
        }
        for secret in load_society_seed()["secrets"]:
            claim_id = setup["secret_claim_ids"][secret["key"]]
            holders = {row["key"] for row in secret["holders"]}
            assert {key for key, claims in matrix.items() if claim_id in claims} == holders
        rumor = session.get(Claim, UUID(setup["secret_claim_ids"]["s_rumor"]))
        assert rumor.truth_status == "false"
        interpretations = {key: session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == UUID(setup["character_ids"][key]),
            Knowledge.claim_id == rumor.id,
        )) for key in ("C", "E")}
        assert interpretations["C"].stance == "doubt"
        assert interpretations["E"].stance == "believe"
        variant = session.get(Claim, UUID(setup["secret_claim_ids"]["s_rumor_variant"]))
        assert variant.id in {UUID(value) for value in matrix["C"]}
        assert variant.id not in {UUID(value) for value in matrix["E"]}
        origin = session.get(Event, variant.origin_event_id)
        assert origin.type == "CLAIM_INFERRED"
        clara_rumor = interpretations["C"]
        assert session.get(EventCause, (origin.id, clara_rumor.last_event_id))
        contexts = {key: build_character_context(
            session, game_id, UUID(actor_id)
        ).model_dump_json() for key, actor_id in setup["character_ids"].items()}
        assert "Clara 曾替 Alice" not in contexts["A"]
        assert "Bob 喜欢 Dana" not in contexts["D"]
        assert "Faye 保存了" not in contexts["C"]
        assert "Bob 还藏着" not in contexts["D"]
        assert "PRIVACY_VIOLATION" not in "".join(contexts.values())
        player = PlayerProjectionService(session)
        timeline = player.timeline(game_id)
        assert any(row["type"] == "ASKED_AI" for row in timeline)
        assert all(row["type"] not in {
            "CHARACTER_SHARED_INFORMATION", "INFORMATION_WITHHELD",
            "SECRET_ORIGIN", "PRIVACY_VIOLATION",
        } for row in timeline)
        assert session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == UUID(setup["player_ai_id"]),
            Knowledge.claim_id == UUID(setup["secret_claim_ids"]["s_choice"]),
        )) is None
        apocalypse_views = [session.scalar(select(Knowledge).where(
            Knowledge.holder_actor_id == UUID(setup["character_ids"][key]),
            Knowledge.claim_id == UUID(apocalypse),
        )) for key in ("A", "B", "E")]
        assert len({(row.stance, row.confidence) for row in apocalypse_views}) == 3
        debug = DebugWorldView(session).snapshot(game_id)
        assert any(row["type"] == "PRIVACY_VIOLATION" for row in debug["events"])
        assert len(debug["characters"]) == 6
        assert all(row["decision_profile"]["ai_attitude"]
                   for row in debug["characters"])
        assert all(row["basis"] for row in debug["relationships"])


def test_one_day_actions_are_distinct_and_causally_linked(database):
    simulation, setup = run_society(database)
    game_id = UUID(setup["game_id"])
    with Session(database) as session:
        actors = {row.id: row.display_name for row in session.scalars(
            select(Actor).where(Actor.game_id == game_id)
        )}
        proposals = session.scalars(select(ActionProposalRow).where(
            ActionProposalRow.game_id == game_id,
        )).all()
        by_actor = {actors[row.actor_id]: row for row in proposals}
        assert {name: row.type for name, row in by_actor.items()} == {
            "Alice": "SHARE_INFORMATION", "Bob": "SHARE_INFORMATION",
            "Clara": "CHANGE_PLAN", "Dana": "ASK_AI",
            "Evan": "VERIFY_CLAIM", "Faye": "WITHHOLD_INFORMATION",
        }
        assert all(row.status == "accepted" and row.result_event_id for row in proposals)
        events = session.scalars(select(Event).where(
            Event.game_id == game_id,
        ).order_by(Event.sequence)).all()
        assert [row.sequence for row in events] == list(range(1, len(events) + 1))
        causes = session.scalars(select(EventCause).where(
            EventCause.game_id == game_id,
        )).all()
        seq = {row.id: row.sequence for row in events}
        assert all(seq[row.cause_event_id] < seq[row.event_id] for row in causes)
        assert all(any(cause.event_id == row.result_event_id
                       and cause.cause_event_id == row.proposal_event_id
                       for cause in causes) for row in proposals)
        advanced = next(row for row in events if row.type == "DAY_ADVANCED")
        assert all(row.event_id != advanced.id for row in causes)
        claim = session.get(Claim, UUID(setup["apocalypse_claim_id"]))
        assert claim.truth_status == "unknown"
        clara = session.get(Character, UUID(setup["character_ids"]["C"]))
        assert clara.goals["short_term"] == "先核对已知事实，再决定是否对外说明。"
        assert session.scalar(select(Memory).where(
            Memory.owner_actor_id == UUID(setup["character_ids"]["F"]),
            Memory.content == "我暂时选择不传播这条信息。",
        )) is not None
    assert asyncio.run(simulation.advance_day(game_id, 1))["replayed"] is True


def test_society_acts_without_any_player_message(database):
    simulation = SimulationEngine(database, SocietyDemoLLMProvider())
    setup = simulation.initialize_game("society")
    game_id = UUID(setup["game_id"])
    assert asyncio.run(simulation.advance_day(game_id, 1))["day"] == 2
    with Session(database) as session:
        actors = {row.id: row.display_name for row in session.scalars(
            select(Actor).where(Actor.game_id == game_id)
        )}
        proposals = session.scalars(select(ActionProposalRow).where(
            ActionProposalRow.game_id == game_id,
        )).all()
        assert {actors[row.actor_id]: row.type for row in proposals} == {
            "Alice": "ASK_AI", "Bob": "WITHHOLD_INFORMATION",
            "Clara": "CHANGE_PLAN", "Dana": "ASK_AI",
            "Evan": "VERIFY_CLAIM", "Faye": "WITHHOLD_INFORMATION",
        }
        assert all(row.status == "accepted" for row in proposals)
        matrix = context_claims(session, setup)
        assert all(setup["apocalypse_claim_id"] not in claims
                   for claims in matrix.values())
        asks = session.scalars(select(Event).where(
            Event.game_id == game_id, Event.type == "ASKED_AI",
        )).all()
        assert len(asks) == 2
        assert len({row.data["text"] for row in asks}) == 2
        assert all("Clara" not in row.data["text"]
                   and "Dana 收到" not in row.data["text"] for row in asks)


def test_unknown_claim_action_is_rejected_without_knowledge_leak(database):
    simulation = SimulationEngine(database, SocietyDemoLLMProvider())
    setup = simulation.initialize_game("society")
    game_id = UUID(setup["game_id"])
    proposal = ActionProposal(
        type="WITHHOLD_INFORMATION", actor_id=UUID(setup["character_ids"]["F"]),
        claim_id=UUID(setup["apocalypse_claim_id"]),
        reason="先不说", confidence=0.8,
    )
    proposal_id = simulation.create_proposal(game_id, 2, "unknown-f", proposal)
    result = asyncio.run(simulation.execute_proposal(game_id, proposal_id))
    assert result["accepted"] is False
    with Session(database) as session:
        assert session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "INFORMATION_WITHHELD",
        )) is None


def test_six_profiles_reach_mock_and_produce_distinct_interpretations(database):
    setup = SimulationEngine(database, SocietyDemoLLMProvider()).initialize_game("society")
    seed = load_society_seed()
    outcomes = [
        {"reply": f"{row['name']} 的不同说法", **{
            key: row["apocalypse_baseline"][key] for key in ("stance", "confidence")
        }} for row in seed["characters"]
    ]
    mock = MockLLMProvider(structured_responses=outcomes)
    responses = []
    with Session(database) as session:
        for row in seed["characters"]:
            context = build_character_context(
                session, UUID(setup["game_id"]),
                UUID(setup["character_ids"][row["key"]]),
            )
            responses.append(asyncio.run(mock.generate_structured(
                StructuredRequest(
                    system="只根据当前角色上下文判断同一条传闻。",
                    prompt="世界可能在 30 天后毁灭。",
                    response_model=ConversationReaction,
                    context={"character": context.model_dump(mode="json"),
                             "disclosed_claim_id": setup["apocalypse_claim_id"]},
                ), ConversationReaction,
            )))
    assert len({request.context["character"]["personality"]["communication_style"]
                for request in mock.structured_requests}) == 6
    assert len({str(request.context["character"]["personality"]["ai_attitude"])
                for request in mock.structured_requests}) == 6
    assert len({row.reply for row in responses}) == 6
    assert len({row.stance for row in responses}) >= 2
    assert max(row.confidence for row in responses) - min(
        row.confidence for row in responses
    ) >= 0.4
    demo = SocietyDemoLLMProvider()
    live_responses = [asyncio.run(demo.generate_structured(
        request, ConversationReaction,
    )) for request in mock.structured_requests]
    assert len({row.reply for row in live_responses}) == 6
    assert len({row.stance for row in live_responses}) >= 2
    assert max(row.confidence for row in live_responses) - min(
        row.confidence for row in live_responses
    ) >= 0.3


def test_demo_choices_change_when_seed_decision_inputs_change(database):
    _simulation, setup = run_society(database)
    provider = SocietyDemoLLMProvider()
    with Session(database) as session:
        alice = build_character_context(
            session, UUID(setup["game_id"]), UUID(setup["character_ids"]["A"])
        ).model_dump(mode="json")
        bob = build_character_context(
            session, UUID(setup["game_id"]), UUID(setup["character_ids"]["B"])
        ).model_dump(mode="json")
        dana = build_character_context(
            session, UUID(setup["game_id"]), UUID(setup["character_ids"]["D"])
        ).model_dump(mode="json")
        faye = build_character_context(
            session, UUID(setup["game_id"]), UUID(setup["character_ids"]["F"])
        ).model_dump(mode="json")

    async def decide(context):
        return await provider.generate_structured(
            StructuredRequest(
                system="Use only this character context.", prompt="Choose one action.",
                response_model=AgentDecision, context=context,
            ), AgentDecision,
        )

    assert asyncio.run(decide(alice)).action.type == "SHARE_INFORMATION"
    alice["personality"]["behavior_tendencies"]["share"] = 0.1
    assert asyncio.run(decide(alice)).action.type == "ASK_AI"
    assert asyncio.run(decide(bob)).action.type == "SHARE_INFORMATION"
    bob["personality"]["decision_biases"]["verify_with_person"] = 0.1
    assert asyncio.run(decide(bob)).action.type == "WITHHOLD_INFORMATION"
    assert asyncio.run(decide(dana)).action.type == "ASK_AI"
    dana["personality"]["ai_attitude"]["willingness_to_follow_ai_advice"] = 0.9
    assert asyncio.run(decide(dana)).action is None
    assert asyncio.run(decide(faye)).action.type == "WITHHOLD_INFORMATION"
    faye["personality"]["ai_attitude"]["privacy_concern"] = 0.1
    assert asyncio.run(decide(faye)).action is None


def test_character_document_and_trace_match_machine_seed():
    docs = Path(__file__).resolve().parents[2] / "docs"
    assert (docs / "CHARACTERS.md").read_text(encoding="utf-8") == (
        render_character_document(load_society_seed())
    )
    assert json.loads((docs / "PHASE3_TRACE.json").read_text(encoding="utf-8")) == (
        build_trace()
    )


def test_default_api_creates_playable_six_character_world(database):
    client = TestClient(create_app(database, debug=False))
    response = client.post("/games")
    assert response.status_code == 200
    setup = response.json()
    game_id = setup["game_id"]
    assert len(client.get(f"/games/{game_id}/characters").json()) == 6
    assert len(client.get(f"/games/{game_id}/claims").json()) == 1
    assert client.post(f"/games/{game_id}/messages", json={
        "character_id": setup["character_ids"]["A"],
        "text": "我听到一条末日消息。",
        "disclosed_claim_id": setup["apocalypse_claim_id"],
    }).status_code == 200
    advance = client.post(f"/games/{game_id}/advance", json={"expected_day": 1})
    assert advance.status_code == 200
    assert advance.json()["day"] == 2
    assert any(row["type"] == "ASKED_AI" for row in client.get(
        f"/games/{game_id}/timeline"
    ).json())
    assert client.get(f"/debug/games/{game_id}").status_code == 404
    legacy = client.post("/games", json={"scenario": "phase2"}).json()
    assert len(client.get(f"/games/{legacy['game_id']}/characters").json()) == 2
    assert client.post(f"/games/{legacy['game_id']}/messages", json={
        "character_id": legacy["alice_id"],
        "text": "我收到末日消息。",
        "disclosed_claim_id": legacy["apocalypse_claim_id"],
    }).status_code == 200
    assert client.post(
        f"/games/{legacy['game_id']}/advance", json={"expected_day": 1}
    ).status_code == 200
