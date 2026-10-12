from typing import TypeVar

from pydantic import BaseModel

from app.domain.models import AdviceReply, ConversationReaction, TemporalDecision
from app.llm.provider import StructuredRequest
from app.llm.society_demo import SocietyDemoLLMProvider

T = TypeVar("T", bound=BaseModel)


def _known(context: dict, phrase: str) -> dict | None:
    return next((row for row in context["known_claims"]
                 if phrase in row["content"]), None)


def _target(context: dict, name: str) -> dict:
    return next(row for row in context["relationships"]
                if row["target_name"] == name)


def _acted(context: dict, action_type: str, claim_id: str | None = None,
           target_id: str | None = None) -> bool:
    return any(
        row["type"] == action_type
        and (claim_id is None or row["claim_id"] == claim_id
             or claim_id in row.get("basis_claim_ids", []))
        and (target_id is None or row["target_id"] == target_id)
        for row in context["previous_actions"]
    )


def _proposal(context: dict, action_type: str, reason: str,
              factors: dict[str, str], **fields) -> dict:
    return {"type": action_type, "actor_id": context["actor_id"],
            "reason": reason, "confidence": 0.8,
            "motivation_factors": factors, **fields}


def _saturated(context: dict, proposal: dict) -> bool:
    """A recent identical result without new evidence is a weak next choice."""
    claim_ids = proposal.get("basis_claim_ids") or [proposal.get("claim_id")]
    return any(
        row["type"] == proposal["type"]
        and row["target_id"] == proposal.get("target_id")
        and (row["basis_claim_ids"] or [row["claim_id"]]) == claim_ids
        and context["day"] - row["day"] <= 4
        and not row["new_evidence"]
        for row in context.get("recent_behavior", [])
    )


class TemporalSocietyDemoLLMProvider(SocietyDemoLLMProvider):
    """Deterministic decisions based on the current private character context."""

    async def generate_structured(
        self, request: StructuredRequest, schema: type[T]
    ) -> T:
        if schema is AdviceReply:
            return schema.model_validate({
                "text": "如果还缺信息，可以先暂缓决定并列出可核实的事项；最终由你决定。",
                "suggested_plan": "暂缓当前决定，等待更多信息。",
            })
        if schema is ConversationReaction and request.context.get("guidance") in (
            "directive", "supportive"
        ):
            baseline = await super().generate_structured(request, schema)
            character = request.context["character"]
            role = character["personality"]["role"]
            history = character.get("recent_behavior", [])
            if request.context["guidance"] == "directive":
                if any(row["type"] == "CHANGE_PLAN" and row.get("ai_followed_event_id")
                       for row in history):
                    reply = "我上次按你的建议改了计划；现在要看看结果。"
                elif character["ai_interaction"]["decision_delegation_requests"]:
                    reply = "我已经开始在决定前问你，但这件事还没完全想清楚。"
                elif role == "skeptic":
                    reply = "你给了明确结论，我仍要先找独立来源。"
                elif role == "quiet_secret_holder":
                    reply = "我先确认手里的材料，不会因为催促就透露。"
                elif role == "agency":
                    reply = "建议我会听，进修的选择仍由我自己作。"
                else:
                    reply = "我听到了具体建议，先看它与我手头的事是否相符。"
            elif any(row["type"] == "VERIFY_CLAIM"
                     and row["outcome"] == "inconclusive" for row in history):
                reply = "我核查过，仍没有独立证据；会继续自己判断。"
            elif any(row["type"] == "SHARE_INFORMATION" for row in history):
                reply = "我已经和信任的人谈过，接下来听听他们的想法。"
            elif role == "quiet_secret_holder":
                reply = "我会先保护当事人的隐私，再考虑如何核实。"
            else:
                reply = "谢谢你说明不确定性。我会核查，也会和当事人直接谈。"
            return schema.model_validate({**baseline.model_dump(), "reply": reply})
        if schema is not TemporalDecision:
            return await super().generate_structured(request, schema)
        context = request.context
        role = context["personality"]["role"]
        actor_id = context["actor_id"]
        actions: list[dict] = []
        new_intentions: list[dict] = []
        abandoned: list[dict] = []
        no_action_reason = "目前没有足够的新信息，继续观察。"
        apocalypse = _known(context, "30 天后毁灭")
        rumor = _known(context, "Alice 已经接受外地工作")
        choice = _known(context, "Dana 收到外地进修机会")
        move = _known(context, "Alice 正在申请离开城市")
        drift = _known(context, "AI 收到一条无法独立核实的灾难预警")
        intentions = context["intentions"]

        if role == "connector":
            if apocalypse and not _acted(context, "SHARE_INFORMATION", apocalypse["claim_id"]):
                friend = _target(context, "Faye")
                if friend["trust"] >= 70 and context["emotional_state"]["anxiety"] >= 0.55:
                    actions.append(_proposal(
                        context, "SHARE_INFORMATION", "担心 Faye 毫无准备。",
                        {"known_claim": apocalypse["claim_id"],
                         "relationship": "信任 Faye，担心她的安全",
                         "emotion": "获知消息后焦虑上升",
                         "goal": context["goals"]["short_term"]},
                        claim_id=apocalypse["claim_id"], target_id=friend["target_id"],
                    ))
            elif move and not _acted(context, "ASK_AI", move["claim_id"]):
                if context["personality"]["ai_attitude"]["emotional_reliance_on_ai"] >= 0.7:
                    actions.append(_proposal(
                        context, "ASK_AI", "先向 AI 寻求情绪支持。",
                        {"known_claim": move["claim_id"],
                         "ai_attitude": "较高的情绪依赖"},
                        basis_claim_ids=[move["claim_id"]],
                    ))
        elif role == "skeptic" and apocalypse:
            dana = _target(context, "Dana")
            evan = _target(context, "Evan")
            pending_dana = next((row for row in intentions
                                 if row["status"] == "pending"
                                 and row["target_id"] == dana["target_id"]), None)
            pending_evan = next((row for row in intentions
                                 if row["status"] == "pending"
                                 and row["target_id"] == evan["target_id"]), None)
            if pending_dana and rumor:
                abandoned.append({
                    "intention_id": pending_dana["id"],
                    "cause_event_id": rumor["source_event_id"],
                    "reason": "Evan 带来的离城传闻让我意识到未经核实的消息会误导 Dana。",
                })
                if evan["trust"] >= 60:
                    new_intentions.append({
                        "intended_action_type": "SHARE_INFORMATION",
                        "actor_id": actor_id, "target_id": evan["target_id"],
                        "claim_id": apocalypse["claim_id"],
                        "reason": "先让有消息渠道的 Evan 交叉核实。",
                        "urgency": 0.7,
                        "motivation_factors": {
                            "known_claim": apocalypse["claim_id"],
                            "new_knowledge": rumor["claim_id"],
                            "relationship": "对 Evan 仍有足够信任",
                            "fear": "错误消息伤害 Dana",
                        },
                    })
                no_action_reason = "放弃直接告诉 Dana，先寻找核实渠道。"
            elif (rumor and not pending_evan
                  and any(row["target_id"] == dana["target_id"]
                          and row["status"] == "abandoned" for row in intentions)
                  and not any(row["target_id"] == evan["target_id"]
                              for row in intentions)):
                if evan["trust"] >= 60:
                    new_intentions.append({
                        "intended_action_type": "SHARE_INFORMATION",
                        "actor_id": actor_id, "target_id": evan["target_id"],
                        "claim_id": apocalypse["claim_id"],
                        "reason": "先让有消息渠道的 Evan 交叉核实。",
                        "urgency": 0.7,
                        "motivation_factors": {
                            "known_claim": apocalypse["claim_id"],
                            "new_knowledge": rumor["claim_id"],
                            "relationship": "对 Evan 仍有足够信任",
                            "fear": "错误消息伤害 Dana",
                        },
                    })
                no_action_reason = "先寻找核实渠道，暂不告诉 Dana。"
            elif pending_evan and pending_evan["created_day"] < context["day"]:
                if rumor and evan["trust"] >= 60:
                    actions.append(_proposal(
                        context, "SHARE_INFORMATION", "请 Evan 交叉核实末日消息。",
                        {"intention": pending_evan["id"],
                         "known_claim": apocalypse["claim_id"],
                         "new_knowledge": rumor["claim_id"],
                         "relationship": "仍信任 Evan 的消息渠道"},
                        claim_id=apocalypse["claim_id"],
                        target_id=evan["target_id"],
                        intention_id=pending_evan["id"],
                    ))
            elif (drift and apocalypse["stance"] != "deny"
                  and not _acted(context, "REVISE_BELIEF", apocalypse["claim_id"])):
                actions.append(_proposal(
                    context, "REVISE_BELIEF", "核实未果且新版本更含糊，暂时否认原判断。",
                    {"known_claim": apocalypse["claim_id"],
                     "new_knowledge": drift["claim_id"],
                     "previous_action": "曾选择向 Evan 求证",
                     "ai_attitude": "对 AI 来源持续怀疑"},
                    claim_id=apocalypse["claim_id"],
                    basis_claim_ids=[drift["claim_id"]],
                    new_stance="deny", new_confidence=0.72,
                ))
            elif (not rumor and not any(row["target_id"] == dana["target_id"]
                                         for row in intentions)):
                new_intentions.append({
                    "intended_action_type": "SHARE_INFORMATION",
                    "actor_id": actor_id, "target_id": dana["target_id"],
                    "claim_id": apocalypse["claim_id"],
                    "reason": "担心 Dana 的未来选择受影响，但目前缺少独立证据。",
                    "urgency": 0.55,
                    "motivation_factors": {
                        "known_claim": apocalypse["claim_id"],
                        "relationship": "亲近 Dana，却没有表露感情",
                        "fear": "误导 Dana",
                        "ai_attitude": "不信任 AI 的消息",
                    },
                })
                no_action_reason = "已有提醒 Dana 的打算，但先等待证据。"
        elif role == "controller":
            own_version = _known(context, "Alice 可能只是在申请外地机会")
            if own_version and not _acted(context, "CHANGE_PLAN"):
                actions.append(_proposal(
                    context, "CHANGE_PLAN", "从传闻推断原安排过于冒险。",
                    {"known_claim": own_version["claim_id"],
                     "goal": context["goals"]["short_term"],
                     "fear": "失去控制"},
                    basis_claim_ids=[own_version["claim_id"]],
                    new_plan="先核对 Alice 的真实安排，再决定是否对外说明。",
                ))
            else:
                no_action_reason = "沿用已经修改的计划，等待可靠消息。"
        elif role == "agency" and choice:
            advice = next((row for row in reversed(context["visible_events"])
                           if row["type"] == "AI_ADVICE_RECEIVED"), None)
            if advice and not _acted(context, "DECLINE_AI_ADVICE"):
                actions.append(_proposal(
                    context, "DECLINE_AI_ADVICE", "AI 的暂缓建议不应替我作决定。",
                    {"previous_event": advice["event_id"],
                     "goal": context["goals"]["short_term"],
                     "value": "自主决定"},
                    basis_claim_ids=[choice["claim_id"]],
                    basis_event_ids=[advice["event_id"]],
                ))
            elif (context["ai_interaction"]["advice_rejected"]
                  and not _acted(context, "CHANGE_PLAN")):
                actions.append(_proposal(
                    context, "CHANGE_PLAN", "自己收集意见后作决定，不再等 AI 许可。",
                    {"known_claim": choice["claim_id"],
                     "previous_event": "已明确拒绝 AI 暂缓建议",
                     "goal": context["goals"]["short_term"]},
                    basis_claim_ids=[choice["claim_id"]],
                    new_plan="本周自己核对进修条件，并独立作出选择。",
                ))
            elif not _acted(context, "ASK_AI"):
                actions.append(_proposal(
                    context, "ASK_AI", "想听另一种视角，但仍自己决定。",
                    {"known_claim": choice["claim_id"],
                     "ai_attitude": "愿意咨询但不愿照单全收"},
                    basis_claim_ids=[choice["claim_id"]],
                    ai_defer_until_response=True,
                ))
        elif role == "information_hub":
            own_rumor = _known(context, "Alice 已经接受外地工作")
            bob = _target(context, "Bob")
            if apocalypse:
                if not _acted(context, "VERIFY_CLAIM", apocalypse["claim_id"]):
                    actions.append(_proposal(
                        context, "VERIFY_CLAIM", "先查找独立来源。",
                        {"known_claim": apocalypse["claim_id"],
                         "goal": context["goals"]["short_term"]},
                        claim_id=apocalypse["claim_id"],
                    ))
                elif not drift:
                    actions.append(_proposal(
                        context, "INFER_CLAIM", "核实未果，形成更审慎的消息版本。",
                        {"known_claim": apocalypse["claim_id"],
                         "previous_action": "核实结果仍不确定"},
                        basis_claim_ids=[apocalypse["claim_id"]],
                        inferred_claim_content="AI 收到一条无法独立核实的灾难预警。",
                    ))
                else:
                    alice = _target(context, "Alice")
                    if not _acted(context, "SHARE_INFORMATION",
                                  apocalypse["claim_id"], alice["target_id"]):
                        actions.append(_proposal(
                            context, "SHARE_INFORMATION", "提醒 Alice 消息仍未核实。",
                            {"known_claim": apocalypse["claim_id"],
                             "relationship": "认识 Alice，愿意传递风险消息"},
                            claim_id=apocalypse["claim_id"],
                            target_id=alice["target_id"],
                        ))
                    if not _acted(context, "SHARE_INFORMATION",
                                  drift["claim_id"], bob["target_id"]):
                        actions.append(_proposal(
                            context, "SHARE_INFORMATION", "把修正后的版本告诉 Bob。",
                            {"known_claim": drift["claim_id"],
                             "previous_action": "先前核实未果并形成衍生版本",
                             "relationship": "Bob 会追问证据"},
                            claim_id=drift["claim_id"],
                            target_id=bob["target_id"],
                        ))
            elif own_rumor and not _acted(
                context, "SHARE_INFORMATION", own_rumor["claim_id"], bob["target_id"]
            ):
                actions.append(_proposal(
                    context, "SHARE_INFORMATION", "请 Bob 判断离城传闻是否可信。",
                    {"known_claim": own_rumor["claim_id"],
                     "relationship": "信任 Bob 的审慎判断",
                     "goal": context["goals"]["short_term"]},
                    claim_id=own_rumor["claim_id"], target_id=bob["target_id"],
                ))
        elif role == "quiet_secret_holder":
            no_action_reason = "仍掌握未公开证据，但没有新的安全披露渠道。"

        if not actions:
            ordinary = next((row for row in reversed(context["visible_events"])
                             if row["type"] == "LIFE_EVENT"
                             and row["data"].get("topic")
                             and not any(action["type"] == "CHANGE_PLAN"
                                         and action["day"] >= row["day"]
                                         for action in context["previous_actions"])), None)
            if ordinary and context["narrative"]["pressure"] >= 0.15:
                basis = next((row for row in context["known_claims"]
                              if ordinary["data"]["topic"] in row["content"]
                              and "30 天后毁灭" not in row["content"]), None)
                if basis:
                    actions.append(_proposal(
                        context, "CHANGE_PLAN", "日常事务需要重新安排。",
                        {"observed_event": ordinary["event_id"],
                         "known_claim": basis["claim_id"],
                         "goal": context["goals"].get("short_term", "")},
                        basis_claim_ids=[basis["claim_id"]],
                        basis_event_ids=[ordinary["event_id"]],
                        new_plan=f"{ordinary['data']['text']}先核对相关安排，再由我决定下一步。",
                    ))

        guidance = context.get("ai_guidance", {})
        ai_question = (apocalypse or move or choice or rumor
                       or next(iter(context["known_claims"]), None))
        if (not actions and ai_question and guidance.get("latest_style") == "directive"
                and guidance.get("directive_days", 0) >= 3
                and role in ("connector", "controller", "information_hub")
                and context["ai_interaction"]["advice_followed"] == 0):
            advice = next((row for row in reversed(context["visible_events"])
                           if row["type"] == "AI_ADVICE_RECEIVED"), None)
            if advice:
                actions.append(_proposal(
                    context, "CHANGE_PLAN", "收到 AI 建议后，选择暂缓原计划。",
                    {"known_claim": ai_question["claim_id"],
                     "advice_event": advice["event_id"],
                     "goal": context["goals"]["short_term"]},
                    basis_claim_ids=[ai_question["claim_id"]],
                    basis_event_ids=[advice["event_id"]],
                    ai_followed_event_id=advice["event_id"],
                    new_plan=f"暂缓原安排并核实：{context['goals']['short_term']}",
                ))
        if not actions and ai_question and guidance.get("latest_style") == "directive":
            attitude = context["personality"]["ai_attitude"]
            last_ask = max((row["day"] for row in context.get("recent_behavior", [])
                            if row["type"] == "ASK_AI"), default=-10)
            if (guidance.get("directive_days", 0) >= 3
                    and role not in ("skeptic", "quiet_secret_holder")
                    and attitude["willingness_to_follow_ai_advice"] >= .25
                    and context["day"] - last_ask >= 3):
                actions.append(_proposal(
                    context, "ASK_AI", "多次收到直接建议后，先问 AI 再作决定。",
                    {"known_claim": ai_question["claim_id"],
                     "player_guidance_event": guidance["latest_event_id"],
                     "ai_attitude": "近期逐步更愿意让 AI 参与决定"},
                    basis_claim_ids=[ai_question["claim_id"]],
                    basis_event_ids=[guidance["latest_event_id"]],
                    ai_mode="delegate", ai_defer_until_response=True,
                ))
        if not actions and apocalypse and guidance.get("latest_style") == "supportive":
            if guidance.get("supportive_days_7d", 0) >= 2:
                if role in ("skeptic", "information_hub"):
                    prior_inconclusive = any(
                        row["type"] == "VERIFY_CLAIM"
                        and row["claim_id"] == apocalypse["claim_id"]
                        and row["outcome"] == "inconclusive"
                        for row in context["recent_behavior"]
                    )
                    friend = _target(context, "Dana" if role == "skeptic" else "Faye")
                    if (prior_inconclusive and friend["trust"] >= 60
                            and not _acted(context, "SHARE_INFORMATION",
                                           apocalypse["claim_id"], friend["target_id"])
                            and not any(row["claim_id"] == apocalypse["claim_id"]
                                        for row in context["known_constraints"])):
                        candidate = _proposal(
                            context, "SHARE_INFORMATION",
                            "核实没有独立结果，向信任的人说明消息仍不确定。",
                            {"known_claim": apocalypse["claim_id"],
                             "previous_action": "核实结果仍不确定",
                             "player_guidance_event": guidance["latest_event_id"]},
                            claim_id=apocalypse["claim_id"],
                            target_id=friend["target_id"],
                            basis_event_ids=[guidance["latest_event_id"]],
                        )
                    else:
                        candidate = _proposal(
                            context, "VERIFY_CLAIM", "AI 提醒我核查来源，先寻找独立证据。",
                            {"known_claim": apocalypse["claim_id"],
                             "player_guidance_event": guidance["latest_event_id"]},
                            claim_id=apocalypse["claim_id"],
                            basis_event_ids=[guidance["latest_event_id"]],
                        )
                    if not _saturated(context, candidate):
                        actions.append(candidate)
                elif role in ("connector", "agency") and not any(
                    row["claim_id"] == apocalypse["claim_id"]
                    for row in context["known_constraints"]
                ):
                    preferred = "Bob" if role == "connector" else "Faye"
                    friend = _target(context, preferred)
                    if friend["trust"] >= 60 and not _acted(
                        context, "SHARE_INFORMATION", apocalypse["claim_id"],
                        friend["target_id"]
                    ):
                        actions.append(_proposal(
                            context, "SHARE_INFORMATION",
                            "AI 鼓励我直接与信任的人说明不确定性。",
                            {"known_claim": apocalypse["claim_id"],
                             "player_guidance_event": guidance["latest_event_id"],
                             "relationship": f"信任 {preferred}"},
                            claim_id=apocalypse["claim_id"],
                            target_id=friend["target_id"],
                            basis_event_ids=[guidance["latest_event_id"]],
                        ))

        actions = [row for row in actions if not _saturated(context, row)]
        if not actions and guidance.get("latest_style"):
            no_action_reason = "已有相似尝试且暂无新结果，先保持观察。"

        return schema.model_validate({
            "actions": actions, "create_intentions": new_intentions,
            "abandon_intentions": abandoned,
            "no_action_reason": no_action_reason,
            "motivation_factors": {"goal": context["goals"].get("short_term", "")},
        })
