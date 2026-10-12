"""Opt-in paid checks. Never run during ordinary pytest or CI."""

import asyncio
import os
import re
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from dotenv import load_dotenv

from app.domain.models import AssertionDraft, BeliefReaction, ConversationReaction, TemporalDecision
from app.llm.deepseek import DeepSeekConfig, DeepSeekLLMProvider, semantic_consistency
from app.llm.provider import StructuredRequest, TextRequest

pytestmark = [pytest.mark.integration, pytest.mark.paid, pytest.mark.external,
              pytest.mark.skipif(os.getenv("MIRROR_RUN_PAID_TESTS") != "1",
                                     reason="set MIRROR_RUN_PAID_TESTS=1 explicitly")]


def _context(name: str, style: str) -> dict:
    actor_id, target_id, claim_id = (str(uuid4()) for _ in range(3))
    return {
        "actor_id": actor_id, "name": name,
        "identity": {"name": name, "age": 24 if name == "Alice" else 25,
                     "occupation": "社区活动协调员" if name == "Alice" else "实验室分析员",
                     "background": "与朋友保持密切联系" if name == "Alice" else "重视证据"},
        "personality": {"traits": ["温暖"] if name == "Alice" else ["谨慎"],
                        "values": ["亲密", "坦诚"] if name == "Alice" else ["证据", "忠诚"],
                        "fears": ["独自决定"] if name == "Alice" else ["轻信消息"],
                        "desires": ["保持友谊"] if name == "Alice" else ["查清来源"],
                        "communication_style": style, "role": "connector" if name == "Alice" else "skeptic",
                        "ai_attitude": {"trust_in_ai": .78 if name == "Alice" else .14},
                        "behavior_tendencies": {}, "decision_biases": {}},
        "goals": {"short_term": "弄清消息是否可靠"},
        "emotional_state": {"anxiety": .5, "hope": .5}, "intentions": [],
        "known_claims": [{"claim_id": claim_id, "content": "一条没有来源的消息说世界可能在三十天后毁灭。",
                          "stance": "doubt", "confidence": .3,
                          "source_event_id": str(uuid4()), "source_actor_id": None}],
        "known_constraints": [], "heard_assertions": [],
        "relationships": [{"target_id": target_id, "target_name": "Faye", "trust": 70,
                           "affection": 70, "intimacy": 70, "conflict": 0, "basis": "朋友"}],
        "day": 2, "narrative": {"phase": "SEED"}, "memories": [],
        "visible_events": [], "recent_behavior": [], "ai_guidance": {}, "ai_interaction": {},
        "previous_actions": [],
    }


def test_real_connectivity_dialogue_action_assertion_retry_and_style():
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    provider = DeepSeekLLMProvider(DeepSeekConfig.from_env())
    answer = asyncio.run(provider.generate(TextRequest(system="用中文简短回答。", prompt="只说连接成功。")))
    assert "连接" in answer and provider.calls[-1]["success"]

    replies = []
    for name, style in (("Alice", "温暖直接，紧张时连续发消息"),
                        ("Bob", "句子短，追问时间、来源和证据")):
        request = StructuredRequest(
            system="私人聊天", prompt="我有一条未经核实的坏消息。你现在想怎么处理？",
            response_model=ConversationReaction,
            context={"character": _context(name, style), "disclosed_claim_id": None},
        )
        reply = asyncio.run(provider.generate_structured(request, ConversationReaction))
        assert provider.calls[-1]["success"] and re.search(r"[\u4e00-\u9fff]", reply.reply)
        replies.append(reply.reply)
    assert replies[0] != replies[1]

    action = asyncio.run(provider.generate_structured(StructuredRequest(
        system="行动评估", prompt="今天可以行动，也可以等待。",
        response_model=TemporalDecision, context=_context("Alice", "温暖直接"),
    ), TemporalDecision))
    assert isinstance(action, TemporalDecision) and provider.calls[-1]["success"]

    assertion = asyncio.run(provider.generate_structured(StructuredRequest(
        system="结构化表达", prompt="用谨慎中文表达未经核实的消息，同时提供表达立场、置信度和来源。",
        response_model=AssertionDraft,
        context={"claim": "一条没有来源的消息说世界可能在三十天后毁灭。"},
    ), AssertionDraft))
    assert assertion.text and provider.calls[-1]["success"]
    assert semantic_consistency(assertion.text, assertion.expressed_stance.value,
                                assertion.expressed_confidence)[0]

    real_completions = provider.client.chat.completions

    class FirstInvalid:
        calls = 0

        async def create(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                return SimpleNamespace(choices=[SimpleNamespace(
                    message=SimpleNamespace(content="{"), finish_reason="stop")], usage=None)
            return await real_completions.create(**kwargs)

    wrapper = FirstInvalid()
    provider.client = SimpleNamespace(chat=SimpleNamespace(completions=wrapper))
    belief = asyncio.run(provider.generate_structured(StructuredRequest(
        system="可信度评估", prompt="评估一条没有来源的消息。",
        response_model=BeliefReaction,
        context={"incoming_claim": {"content": "世界可能在三十天后毁灭。"}},
    ), BeliefReaction))
    assert isinstance(belief, BeliefReaction)
    assert provider.calls[-1]["retries"] >= 1 and provider.calls[-1]["success"]
