from collections.abc import AsyncIterator
from typing import TypeVar

from pydantic import BaseModel

from app.domain.models import AgentDecision, BeliefReaction, ConversationReaction
from app.llm.demo import DemoLLMProvider
from app.llm.provider import StructuredRequest, TextRequest

T = TypeVar("T", bound=BaseModel)


def _credibility(character: dict) -> float:
    personality = character["personality"]
    attitude = personality["ai_attitude"]
    anxiety = character["emotional_state"].get("anxiety", 0.5)
    value = (
        0.58 + 0.32 * attitude["trust_in_ai"]
        - 0.42 * attitude["skepticism"] + 0.08 * anxiety
        + 0.05 * attitude["emotional_reliance_on_ai"]
        - 0.04 * attitude["privacy_concern"]
    )
    return round(max(0.05, min(0.95, value)), 2)


def _reply(style: str) -> str:
    if "温暖" in style:
        return "我有点担心大家，先让我想想。"
    if "追问" in style:
        return "来源是谁？有独立证据吗？"
    if "措辞周全" in style:
        return "我先确认这条消息的来源。"
    if "开放问题" in style:
        return "如果是真的，我该怎样自己做选择？"
    if "反应快" in style:
        return "我会找第二个来源核实。"
    if "寡言" in style:
        return "我先听着。"
    return "我需要想一想。"


def _known_apocalypse(context: dict) -> dict | None:
    return next(
        (row for row in context["known_claims"] if "30 天后毁灭" in row["content"]),
        None,
    )


def _preferred_target(context: dict, key: str) -> str | None:
    preferred_name = context["personality"]["decision_biases"].get(key)
    if preferred_name is None:
        return None
    return next(
        (row["target_id"] for row in context["relationships"]
         if row["target_name"] == preferred_name and row["trust"] >= 50),
        None,
    )


class SocietyDemoLLMProvider:
    """Deterministic adapter driven by the six characters' supplied contexts."""

    async def generate(self, request: TextRequest) -> str:
        return "我需要想一想。"

    async def generate_structured(
        self, request: StructuredRequest, schema: type[T]
    ) -> T:
        context = request.context
        character = context.get("character", context)
        if not character["personality"].get("ai_attitude"):
            return await DemoLLMProvider().generate_structured(request, schema)
        if schema is ConversationReaction:
            character = context["character"]
            disclosed = context.get("disclosed_claim_id") is not None
            credibility = _credibility(character)
            value = {
                "reply": _reply(character["personality"]["communication_style"]),
                "stance": ("believe" if credibility >= 0.62 else "doubt")
                if disclosed else None,
                "confidence": credibility if disclosed else None,
            }
        elif schema is BeliefReaction:
            credibility = _credibility(context["character"])
            value = {
                "stance": "believe" if credibility >= 0.62 else "doubt",
                "confidence": credibility,
            }
        elif schema is AgentDecision:
            personality = context["personality"]
            tendency = personality["behavior_tendencies"]
            bias = personality["decision_biases"]
            attitude = personality["ai_attitude"]
            actor_id = context["actor_id"]
            apocalypse = _known_apocalypse(context)
            own_claim = next(
                (row for row in context["known_claims"]
                 if row["source_actor_id"] == actor_id and row is not apocalypse),
                None,
            )
            if (apocalypse and tendency["share"] >= 0.8
                    and bias.get("share_under_stress", 0) >= 0.8
                    and attitude["trust_in_ai"] >= 0.6):
                target_id = _preferred_target(context, "preferred_confidant")
                value = {"action": {
                    "type": "SHARE_INFORMATION", "actor_id": actor_id,
                    "target_id": target_id, "claim_id": apocalypse["claim_id"],
                    "reason": "担心亲近的人没有准备。", "confidence": 0.78,
                }} if target_id else {"action": None}
            elif (apocalypse and tendency["verify"] >= 0.9
                  and bias.get("verify_with_person", 0) >= 0.8
                  and attitude["skepticism"] >= 0.7):
                target_id = _preferred_target(context, "preferred_information_contact")
                value = {"action": {
                    "type": "SHARE_INFORMATION", "actor_id": actor_id,
                    "target_id": target_id, "claim_id": apocalypse["claim_id"],
                    "reason": "请信任的消息联系人交叉核实。", "confidence": 0.83,
                }} if target_id else {"action": None}
            elif apocalypse and tendency["verify"] >= 0.7 and bias.get("verify_before_forwarding", 0) >= 0.7:
                value = {"action": {
                    "type": "VERIFY_CLAIM", "actor_id": actor_id,
                    "claim_id": apocalypse["claim_id"],
                    "reason": "先找独立证据，再决定是否传播。", "confidence": 0.8,
                }}
            elif (own_claim and tendency["control"] >= 0.8
                  and attitude["privacy_concern"] >= 0.6):
                value = {"action": {
                    "type": "CHANGE_PLAN", "actor_id": actor_id,
                    "basis_claim_ids": [own_claim["claim_id"]],
                    "new_plan": "先核对已知事实，再决定是否对外说明。",
                    "reason": "不确定性让原计划显得冒险。", "confidence": 0.76,
                }}
            elif (own_claim and tendency["seek_ai"] >= 0.8
                  and bias.get("ask_without_delegating", 0) >= 0.8
                  and attitude["trust_in_ai"] >= 0.3
                  and attitude["willingness_to_follow_ai_advice"] < 0.5):
                value = {"action": {
                    "type": "ASK_AI", "actor_id": actor_id,
                    "basis_claim_ids": [own_claim["claim_id"]],
                    "reason": "想听另一种视角，但仍自己决定。", "confidence": 0.72,
                }}
            elif (own_claim and tendency["withhold"] >= 0.8
                  and attitude["privacy_concern"] >= 0.8):
                value = {"action": {
                    "type": "WITHHOLD_INFORMATION", "actor_id": actor_id,
                    "claim_id": own_claim["claim_id"],
                    "reason": "披露证据的时机尚未成熟。", "confidence": 0.81,
                }}
            elif (own_claim and tendency["verify"] >= 0.7
                  and bias.get("verify_before_forwarding", 0) >= 0.7):
                value = {"action": {
                    "type": "VERIFY_CLAIM", "actor_id": actor_id,
                    "claim_id": own_claim["claim_id"],
                    "reason": "先核实自己听到的版本。", "confidence": 0.74,
                }}
            elif (own_claim and tendency["seek_ai"] >= 0.7
                  and attitude["emotional_reliance_on_ai"] >= 0.7):
                value = {"action": {
                    "type": "ASK_AI", "actor_id": actor_id,
                    "basis_claim_ids": [own_claim["claim_id"]],
                    "reason": "需要有人接住此刻的情绪。", "confidence": 0.75,
                }}
            elif (own_claim and tendency["withhold"] >= 0.5
                  and bias.get("protect_unspoken_feelings", 0) >= 0.8):
                value = {"action": {
                    "type": "WITHHOLD_INFORMATION", "actor_id": actor_id,
                    "claim_id": own_claim["claim_id"],
                    "reason": "还没准备好向当事人表达私人感情。", "confidence": 0.7,
                }}
            else:
                value = {"action": None}
        else:
            raise ValueError(f"unsupported society response model: {schema.__name__}")
        return schema.model_validate(value)

    async def stream(self, request: TextRequest) -> AsyncIterator[str]:
        yield await self.generate(request)
