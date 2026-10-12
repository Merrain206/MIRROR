import asyncio
from types import SimpleNamespace
from uuid import uuid4
from uuid import UUID

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import Actor, Event, Game
from app.domain.models import AdviceReply, BeliefReaction, TemporalDecision
from app.llm.deepseek import (
    DeepSeekConfig, DeepSeekLLMProvider, _character_layers, _validate_proposals,
    semantic_consistency,
)
from app.llm.provider import StructuredRequest
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.simulation.engine import SimulationEngine
from app.simulation.events import record_event
from app.simulation.temporal import _resolve_advice


class FakeCompletions:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        content = next(self.replies)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
        )


def test_structured_invalid_json_retries_without_external_api():
    completions = FakeCompletions(["{", '{"stance":"doubt","confidence":0.4}'])
    client = SimpleNamespace(chat=SimpleNamespace(completions=completions))
    provider = DeepSeekLLMProvider(DeepSeekConfig(api_key="test-only"), client=client)
    request = StructuredRequest(system="", prompt="判断消息", response_model=BeliefReaction,
                                context={"incoming_claim": {"content": "未经核实的消息"}})
    result = asyncio.run(provider.generate_structured(request, BeliefReaction))
    assert result.stance.value == "doubt"
    assert provider.calls[0]["retries"] == 1
    assert provider.calls[0]["fallback"] is False
    assert provider.calls[0]["prompt_tokens"] == 24
    assert provider.calls[0]["completion_tokens"] == 16
    assert all(call["response_format"] == {"type": "json_object"} for call in completions.calls)
    assert all(call["extra_body"] == {"thinking": {"type": "disabled"}} for call in completions.calls)


def test_independent_advice_is_generated_together_and_committed_in_ask_order():
    database = create_engine("sqlite+pysqlite:///:memory:",
                             connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(database)
    simulation = SimulationEngine(database, TemporalSocietyDemoLLMProvider())
    game_id = UUID(simulation.initialize_game("temporal")["game_id"])
    with Session(database) as session, session.begin():
        actors = session.scalars(select(Actor).where(
            Actor.game_id == game_id, Actor.kind == "character"
        ).order_by(Actor.display_name)).all()[:2]
        game = session.get(Game, game_id)
        asks = [record_event(session, game, day=1, type="ASKED_AI", actor_id=actor.id,
                             audiences=(actor.id,), data={"mode": "advice", "text": actor.display_name})
                for actor in actors]
        ask_ids = [str(row.id) for row in asks]

    class ConcurrentAdvice:
        started = 0
        ready = asyncio.Event()

        async def generate_structured(self, request, schema):
            self.started += 1
            if self.started == 2:
                self.ready.set()
            await self.ready.wait()
            return AdviceReply(text=request.context["ask_text"], suggested_plan="自己核查")

    simulation.llm = ConcurrentAdvice()
    asyncio.run(asyncio.wait_for(_resolve_advice(simulation, game_id, 2), timeout=1))
    asyncio.run(_resolve_advice(simulation, game_id, 2))
    with Session(database) as session:
        received = session.scalars(select(Event).where(
            Event.game_id == game_id, Event.type == "AI_ADVICE_RECEIVED"
        ).order_by(Event.sequence)).all()
    assert [row.data["ask_event_id"] for row in received] == ask_ids
    assert len(received) == 2


def test_prompt_layers_do_not_include_unpassed_world_state():
    character = {
        "actor_id": "bob-id", "name": "Bob", "identity": {"name": "Bob"},
        "personality": {"communication_style": "简短"},
        "goals": {}, "emotional_state": {}, "intentions": [], "known_claims": [],
        "known_constraints": [], "heard_assertions": [], "relationships": [], "day": 3,
        "narrative": {"phase": "SEED"}, "memories": [], "visible_events": [],
        "recent_behavior": [], "ai_guidance": {}, "ai_interaction": {},
    }
    identity, payload = _character_layers({"character": character,
                                           "game_id": "internal", "based_on_state_version": 12})
    assert "Bob" in identity
    assert "internal" not in payload
    assert "based_on_state_version" not in payload
    assert "Knowledge Layer" in payload


def test_assertion_obvious_conflict_is_rejected():
    assert not semantic_consistency("已经百分之百确认", "doubt", .2)[0]
    assert not semantic_consistency("已经证伪", "believe", .9)[0]
    assert semantic_consistency("我听说这件事，但还没核实", "doubt", .3)[0]


def test_real_provider_retries_saturated_action_until_new_evidence():
    actor_id, claim_id = str(uuid4()), str(uuid4())
    context = {"actor_id": actor_id, "day": 12,
               "known_claims": [{"claim_id": claim_id}], "relationships": [],
               "visible_events": [], "recent_behavior": [{
                   "type": "VERIFY_CLAIM", "target_id": None, "claim_id": claim_id,
                   "basis_claim_ids": [], "day": 10, "new_evidence": False,
               }]}
    decision = TemporalDecision.model_validate({"actions": [{
        "type": "VERIFY_CLAIM", "actor_id": actor_id, "claim_id": claim_id,
        "reason": "再次核查", "confidence": .5,
    }]})
    with pytest.raises(ValueError, match="recent identical action"):
        _validate_proposals(decision, context)
    context["recent_behavior"][0]["new_evidence"] = True
    _validate_proposals(decision, context)


def test_sharing_text_cannot_invent_a_named_source():
    actor_id, claim_id, bob_id, ai_id = (str(uuid4()) for _ in range(4))
    context = {"actor_id": actor_id, "day": 2,
               "known_claims": [{"claim_id": claim_id, "source_actor_id": ai_id}],
               "relationships": [{"target_id": bob_id, "target_name": "Bob"}],
               "visible_events": [], "recent_behavior": []}
    decision = TemporalDecision.model_validate({"actions": [{
        "type": "SHARE_INFORMATION", "actor_id": actor_id, "target_id": bob_id,
        "claim_id": claim_id, "reason": "提醒朋友", "confidence": .5,
        "assertion": {"expressed_stance": "doubt", "expressed_confidence": .3,
                      "source_attribution": "anonymous",
                      "text": "Evan 说世界要毁灭，我不确定。"},
    }]})
    with pytest.raises(ValueError, match="unobserved source"):
        _validate_proposals(decision, context)
    decision.actions[0].assertion.text = "我从 AI 那里听到一条消息，但仍不确定。"
    _validate_proposals(decision, context)


def test_external_decision_rebuilds_context_after_state_version_change():
    database = create_engine("sqlite+pysqlite:///:memory:",
                             connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(database)

    class ChangingProvider:
        external = True
        contexts = []
        simulation = None

        async def generate_structured(self, request, schema):
            assert schema is TemporalDecision
            self.contexts.append(request.context)
            if len(self.contexts) == 1:
                with self.simulation.sessions.begin() as session:
                    game = session.get(Game, game_id)
                    record_event(session, game, day=game.day,
                                 type="TEST_CONCURRENT_CHANGE")
            return TemporalDecision()

    provider = ChangingProvider()
    simulation = SimulationEngine(database, provider)
    provider.simulation = simulation
    game_id = UUID(simulation.initialize_game("temporal")["game_id"])
    result = asyncio.run(simulation.advance_day(game_id, 1))
    assert result["day"] == 2
    assert len(provider.contexts) == 7
    assert provider.contexts[0]["actor_id"] == provider.contexts[1]["actor_id"]
    assert provider.contexts[0]["based_on_state_version"] < provider.contexts[1]["based_on_state_version"]
