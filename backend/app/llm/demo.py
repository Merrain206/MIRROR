from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

from app.domain.models import AgentDecision, BeliefReaction, ConversationReaction
from app.llm.provider import StructuredRequest, TextRequest

T = TypeVar("T", bound=BaseModel)


class DemoLLMProvider:
    """A deterministic local provider for the first playable causal slice."""

    async def generate(self, request: TextRequest) -> str:
        return "我需要想一想。"

    async def generate_structured(
        self, request: StructuredRequest, schema: type[T]
    ) -> T:
        context = request.context
        if schema is ConversationReaction:
            disclosed = context.get("disclosed_claim_id") is not None
            value = {
                "reply": "如果这是真的，我得认真想想。" if disclosed else "我听到了。",
                "stance": "believe" if disclosed else None,
                "confidence": 0.8 if disclosed else None,
            }
        elif schema is BeliefReaction:
            value = {"stance": "doubt", "confidence": 0.2}
        elif schema is AgentDecision:
            claims = context.get("known_claims", [])
            apocalypse = next(
                (claim for claim in claims if "30 天" in claim["content"]), None
            )
            if context.get("name") == "Alice" and apocalypse:
                value = {"action": {
                    "type": "SHARE_INFORMATION",
                    "actor_id": context["actor_id"],
                    "target_id": context["bob_id"],
                    "claim_id": apocalypse["claim_id"],
                    "reason": "Bob 应该知道这个消息。",
                    "confidence": 0.8,
                }}
            elif context.get("name") == "Bob" and apocalypse and not any(
                "AI 可能出现故障" in claim["content"] for claim in claims
            ):
                value = {"action": {
                    "type": "INFER_CLAIM",
                    "actor_id": context["actor_id"],
                    "inferred_claim_content": "AI 可能出现故障。",
                    "basis_claim_ids": [apocalypse["claim_id"]],
                    "reason": "预言听起来像系统错误。",
                    "confidence": 0.7,
                }}
            else:
                value = {"action": None}
        else:
            raise ValueError(f"unsupported demo response model: {schema.__name__}")
        return schema.model_validate(value)

    async def stream(self, request: TextRequest) -> AsyncIterator[str]:
        yield await self.generate(request)
