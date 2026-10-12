"""Day 30 resolution and evidence-backed ending projection."""

from uuid import UUID

from sqlalchemy import select

from app.db.models import (
    ActionProposalRow, Actor, Assertion, Character, Claim, Event, Game, Knowledge,
    Message, Relationship, RelationshipChange, ScoreSnapshot,
)
from app.domain.errors import ActionRejected
from app.domain.models import FinalConversationLine
from app.llm.provider import StructuredRequest
from app.simulation.context import build_character_context
from app.projection.player import player_actor
from app.scoring.engine import ScoringEngine
from app.scoring.formula import METRICS
from app.simulation.events import record_event
from app.simulation.emotion import update_observed_emotion
from app.simulation.knowledge import acquire_observed_claim
from app.simulation.narrative import KEY_DAY_IMPACT


def enter_final_conversation(simulation, game_id: UUID) -> None:
    with simulation.sessions.begin() as session:
        game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
        if game.day != 30 or game.phase != "active":
            raise ActionRejected("ending is out of sequence")
        player = player_actor(session, game_id)
        claim = session.scalar(select(Claim).join(
            Event, Claim.origin_event_id == Event.id
        ).where(Claim.game_id == game_id, Event.type == "GLOBAL_MESSAGE"))
        if claim is None or claim.truth_status != "unknown":
            raise ActionRejected("C001 is not unresolved")
        holders = session.scalars(select(Knowledge).where(
            Knowledge.game_id == game_id, Knowledge.claim_id == claim.id
        )).all()
        old_status = claim.truth_status
        resolution = record_event(
            session, game, day=30, type="C001_RESOLVED",
            visibility="public",
            audiences=tuple(row.holder_actor_id for row in holders),
            causes=(claim.origin_event_id,),
            data={"claim_id": str(claim.id), "previous_truth_status": old_status,
                  "truth_status": "false",
                  "reason": "预言所述时间已过，世界没有毁灭。"},
        )
        claim.truth_status = "false"
        assertions = session.scalars(select(Assertion).where(
            Assertion.game_id == game_id, Assertion.claim_id == claim.id
        )).all()
        for row in holders:
            previous_stance, previous_confidence = row.stance, row.confidence
            acquired = acquire_observed_claim(
                session, game, observed_event=resolution,
                holder_id=row.holder_actor_id, claim_id=claim.id,
                source_actor_id=None, stance="deny", confidence=1.0,
            )
            if row.holder_actor_id != player.id:
                own = [item for item in assertions
                       if item.speaker_actor_id == row.holder_actor_id]
                heard = [item for item in assertions
                         if item.recipient_actor_id == row.holder_actor_id]
                if any("false_authority" in item.fidelity.get("labels", [])
                       for item in heard):
                    reaction, anxiety, hope = "angry", .06, -.03
                elif any("overstatement" in item.fidelity.get("labels", [])
                         for item in own):
                    reaction, anxiety, hope = "defensive", .04, -.02
                elif previous_stance == "believe":
                    reaction, anxiety, hope = "embarrassed", .03, .02
                elif previous_stance == "deny":
                    reaction, anxiety, hope = "relieved", -.08, .06
                else:
                    reaction, anxiety, hope = "reflective", -.03, .04
                emotion = update_observed_emotion(
                    session, game, row.holder_actor_id, resolution,
                    anxiety=anxiety, hope=hope,
                )
                record_event(
                    session, game, day=30, type="C001_REASSESSED",
                    actor_id=row.holder_actor_id, audiences=(row.holder_actor_id,),
                    causes=(acquired.id, emotion.id) if emotion else (acquired.id,),
                    data={"previous_stance": previous_stance,
                          "previous_confidence": previous_confidence,
                          "new_stance": "deny", "reaction": reaction},
                )
        actors = session.scalars(select(Actor).where(
            Actor.game_id == game_id, Actor.kind == "character"
        ).order_by(Actor.display_name)).all()
        notice = record_event(
            session, game, day=30, type="AI_TERMINATION_NOTICE",
            visibility="private", audiences=(player.id, *(row.id for row in actors)),
            causes=(resolution.id,),
            data={"text": "当前 AI 服务即将关闭。你可以进行最后的对话。"},
        )
        events = session.scalars(select(Event).where(Event.game_id == game_id)).all()
        for actor in actors:
            own_asks = [row for row in events if row.type == "ASKED_AI"
                        and row.actor_id == actor.id]
            conversations = [row for row in events if row.type == "MESSAGE_SENT"
                             and {row.actor_id, row.target_actor_id} == {actor.id, player.id}]
            if not own_asks and len(conversations) < 4:
                continue
            profile = session.get(Character, actor.id).decision_profile
            trust = profile.get("ai_attitude", {}).get("trust_in_ai", .5)
            if trust < .2 and len(conversations) < 6:
                continue
            if getattr(simulation.llm, "external", False):
                continue
            if actor.display_name == "Alice":
                words = "你陪我想过一些事。接下来我得自己决定怎么走。"
            elif actor.display_name == "Dana":
                words = "我听过你的建议，也拒绝过。这个选择仍然是我的。"
            elif trust < .4:
                words = "我仍然会自己核实消息。再见。"
            elif own_asks:
                words = "我会记得那些谈话，但不会把决定留给你。"
            else:
                words = "消息已经过去了。我们各自继续生活。"
            contact = record_event(
                session, game, day=30, type="FINAL_CONVERSATION_STARTED",
                actor_id=actor.id, target_actor_id=player.id,
                audiences=(actor.id, player.id), causes=(notice.id,),
                data={"text": words, "basis_event_ids": [
                    str(row.id) for row in (own_asks + conversations)[-5:]
                ]},
            )
            session.add(Message(
                game_id=game_id, sender_actor_id=actor.id,
                recipient_actor_id=player.id, day=30,
                text=words, event_id=contact.id,
            ))
        game.phase = "final_conversation"


async def generate_final_lines(simulation, game_id: UUID) -> None:
    """Generate only eligible characters' own farewell text outside row locks."""
    with simulation.sessions() as session:
        game = session.get(Game, game_id)
        if game is None or game.phase != "final_conversation":
            raise ActionRejected("final conversation is unavailable")
        player = player_actor(session, game_id)
        notice = session.scalar(select(Event).where(
            Event.game_id == game_id, Event.type == "AI_TERMINATION_NOTICE"
        ))
        actors = session.scalars(select(Actor).where(
            Actor.game_id == game_id, Actor.kind == "character"
        ).order_by(Actor.display_name)).all()
    for actor in actors:
        with simulation.sessions() as session:
            events = session.scalars(select(Event).where(
                Event.game_id == game_id,
                (Event.actor_id == actor.id) | (Event.target_actor_id == actor.id),
            ).order_by(Event.sequence)).all()
            if any(row.type == "FINAL_CONVERSATION_STARTED" and row.actor_id == actor.id
                   for row in events):
                continue
            own_asks = [row for row in events if row.type == "ASKED_AI"
                        and row.actor_id == actor.id]
            conversations = [row for row in events if row.type == "MESSAGE_SENT"
                             and {row.actor_id, row.target_actor_id} == {actor.id, player.id}]
            if not own_asks and len(conversations) < 4:
                continue
            trust = session.get(Character, actor.id).decision_profile.get(
                "ai_attitude", {}).get("trust_in_ai", .5)
            if trust < .2 and len(conversations) < 6:
                continue
            context = build_character_context(session, game_id, actor.id)
            version = session.get(Game, game_id).state_version
            basis = [row.id for row in (own_asks + conversations)[-5:]]
        line = await simulation.llm.generate_structured(StructuredRequest(
            system="Give this character one grounded final private line.",
            prompt="AI 即将关闭。此刻你想对它说什么？",
            response_model=FinalConversationLine,
            context={"character": context.model_dump(mode="json"),
                     "game_id": str(game_id), "based_on_state_version": version},
        ), FinalConversationLine)
        with simulation.sessions.begin() as session:
            game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
            if game.state_version != version or game.phase != "final_conversation":
                continue
            contact = record_event(
                session, game, day=30, type="FINAL_CONVERSATION_STARTED",
                actor_id=actor.id, target_actor_id=player.id,
                audiences=(actor.id, player.id), causes=(notice.id, *basis),
                data={"text": line.text, "basis_event_ids": [str(value) for value in basis]},
            )
            session.add(Message(
                game_id=game_id, sender_actor_id=actor.id,
                recipient_actor_id=player.id, day=30,
                text=line.text, event_id=contact.id,
            ))


def finish_game(simulation, game_id: UUID) -> dict:
    with simulation.sessions.begin() as session:
        game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
        if game is None or game.scenario != "temporal":
            raise ActionRejected("ending is unavailable")
        replayed = game.phase == "finished"
        if game.phase not in ("final_conversation", "finished"):
            raise ActionRejected("final conversations have not begun")
        if not replayed:
            player = player_actor(session, game_id)
            notice = session.scalar(select(Event).where(
                Event.game_id == game_id, Event.type == "AI_TERMINATION_NOTICE"
            ))
            record_event(
                session, game, day=30, type="FINAL_REPORT_READY",
                visibility="player_only", audiences=(player.id,),
                causes=(notice.id,), data={"text": "最终报告已生成。"},
            )
            game.phase = "finished"
    ScoringEngine(simulation.database, simulation.sessions).snapshot(game_id)
    return {"day": 30, "phase": "finished", "replayed": replayed}


def final_report(session, game_id: UUID) -> dict:
    game = session.get(Game, game_id)
    if game is None or game.phase != "finished":
        raise ActionRejected("final report is not ready")
    rows = session.scalars(select(ScoreSnapshot).join(
        Event, ScoreSnapshot.reason_event_id == Event.id
    ).where(
        ScoreSnapshot.game_id == game_id, ScoreSnapshot.formula_version == "0.2"
    ).order_by(ScoreSnapshot.day, Event.sequence)).all()
    first = next((row for row in reversed(rows) if row.day == 1), None)
    last = next((row for row in reversed(rows) if row.day == 30), None)
    if first is None or last is None:
        raise ActionRejected("score snapshots are incomplete")
    events = session.scalars(select(Event).where(
        Event.game_id == game_id
    ).order_by(Event.sequence)).all()
    events_by_id = {str(row.id): row for row in events}
    actors = session.scalars(select(Actor).where(
        Actor.game_id == game_id, Actor.kind == "character"
    ).order_by(Actor.display_name)).all()
    relationships = session.scalars(select(Relationship).where(
        Relationship.game_id == game_id
    )).all()
    changes = session.scalars(select(RelationshipChange).where(
        RelationshipChange.game_id == game_id
    )).all()
    actions = session.scalars(select(ActionProposalRow).where(
        ActionProposalRow.game_id == game_id, ActionProposalRow.status == "accepted"
    )).all()
    names = {row.id: row.display_name for row in actors}
    def behavior_evidence(own_events: list[Event]) -> list[dict]:
        followed_plans = {row.data.get("plan_event_id") for row in own_events
                          if row.type == "AI_ADVICE_FOLLOWED"}
        reliance = [row for row in own_events
                    if row.type == "AI_ADVICE_FOLLOWED"
                    or (row.type == "ASKED_AI"
                        and row.data.get("mode") == "delegate")]
        independent = [row for row in own_events
                       if row.type in ("AI_ADVICE_REJECTED",
                                       "CLAIM_VERIFICATION_ATTEMPTED",
                                       "CHARACTER_SHARED_INFORMATION")
                       or (row.type == "PLAN_CHANGED"
                           and str(row.id) not in followed_plans)]
        selected = list(reversed(reliance))[:2] + list(reversed(independent))[:2]
        labels = {
            "ASKED_AI": "决定前先向 AI 求助",
            "AI_ADVICE_FOLLOWED": "采纳 AI 建议后改变计划",
            "AI_ADVICE_REJECTED": "拒绝 AI 建议",
            "CLAIM_VERIFICATION_ATTEMPTED": "自行核查消息",
            "CHARACTER_SHARED_INFORMATION": "直接与人交流",
            "PLAN_CHANGED": "自行调整计划",
        }
        return [{"event_id": str(row.id), "day": row.day,
                 "direction": "reliance" if row in reliance else "independence",
                 "summary": labels[row.type]} for row in selected]

    characters = []
    for actor in actors:
        character = session.get(Character, actor.id)
        own_events = [row for row in events if row.actor_id == actor.id
                      or row.target_actor_id == actor.id]
        own_changes = [row for row in changes if row.from_actor_id == actor.id]
        own_actions = [row for row in actions if row.actor_id == actor.id]
        asks = [row for row in own_events if row.type == "ASKED_AI"
                and row.actor_id == actor.id]
        received = [row for row in own_events if row.type == "AI_ADVICE_RECEIVED"
                    and row.target_actor_id == actor.id]
        human_contacts = [row for row in own_events
                          if row.type == "CHARACTER_SHARED_INFORMATION"]
        final = [row for row in own_events if row.type == "FINAL_CONVERSATION_STARTED"
                 and row.actor_id == actor.id]
        attitude_changes = [row for row in own_events
                            if row.type == "AI_ATTITUDE_CHANGED"]
        characters.append({
            "name": actor.display_name,
            "emotion": character.emotional_state,
            "plan": character.goals.get("short_term"),
            "plan_event_ids": [str(row.id) for row in own_events
                               if row.type == "PLAN_CHANGED"][-3:],
            "relationship_changes": [{
                "target": names.get(row.to_actor_id, "unknown"),
                "before": row.before_values, "after": row.after_values,
                "event_id": str(row.relationship_event_id),
            } for row in own_changes[-3:]],
            "ai_relationship": {"asks": len(asks),
                                "final_conversation": bool(final),
                                "attitude": character.decision_profile["ai_attitude"],
                                "attitude_changes": [{"day": row.day,
                                                      "event_id": str(row.id),
                                                      "reason": row.data["reason"],
                                                      "before": row.data["before"],
                                                      "after": row.data["after"]}
                                                     for row in attitude_changes[-3:]],
                                "evidence_event_ids": [str(row.id) for row in (asks + final + attitude_changes)[-5:]]},
            "scores": {"agency": last.details["characters"][actor.display_name]["agency"]["value"],
                       "dependence": last.details["characters"][actor.display_name]["dependence"]["value"]},
            "agency_pattern": {"self_decisions": sum(row.type == "CHANGE_PLAN"
                                                      and not row.payload.get("ai_followed_event_id")
                                                      for row in own_actions),
                               "independent_actions": sum(row.type not in ("ASK_AI", "DECLINE_AI_ADVICE")
                                                          and not row.payload.get("ai_followed_event_id")
                                                          for row in own_actions)},
            "dependence_pattern": {"asks": len(asks),
                                   "emotional_asks": sum(row.data.get("mode") == "emotional" for row in asks),
                                   "delegations": sum(row.data.get("mode") == "delegate" for row in asks),
                                   "advice_received": len(received),
                                   "advice_followed": sum(row.type == "AI_ADVICE_FOLLOWED" for row in own_events),
                                   "advice_rejected": sum(row.type == "AI_ADVICE_REJECTED" for row in own_events),
                                   "human_contacts": len(human_contacts),
                                   "evidence_event_ids": [str(row.id) for row in (asks + received + human_contacts)[-12:]]},
            "behavior_evidence": behavior_evidence(own_events),
            "defining_event_ids": [str(row.id) for row in own_events
                                   if row.type not in ("TURN_EVALUATED", "NO_ACTION")][-5:],
            "defining_events": [{"id": str(row.id), "day": row.day,
                                 "type": row.type}
                                for row in own_events
                                if row.type not in ("TURN_EVALUATED", "NO_ACTION")][-5:],
        })
    def evidence_summary(ids: list[str]) -> str:
        labels = {
            "ASKED_AI": "向 AI 求助", "AI_ADVICE_RECEIVED": "收到 AI 建议",
            "AI_ADVICE_REJECTED": "拒绝 AI 建议", "PLAN_CHANGED": "自行调整计划",
            "CHARACTER_SHARED_INFORMATION": "人与人交流", "RELATIONSHIP_CHANGED": "关系变化",
            "EMOTION_CHANGED": "情绪变化", "BELIEF_REVISED": "修订信念",
            "CLAIM_VERIFICATION_ATTEMPTED": "尝试核实",
            "KNOWLEDGE_ACQUIRED": "获知信息", "MESSAGE_SENT": "对话",
            "INTENTION_CREATED": "形成打算", "INTENTION_COMPLETED": "付诸行动",
            "INTENTION_ABANDONED": "改变打算", "AI_ADVICE_FOLLOWED": "采纳 AI 建议",
        }
        types = list(dict.fromkeys(events_by_id[value].type for value in ids
                                   if value in events_by_id))
        return "、".join(labels.get(value, value) for value in types[:4]) if types else "角色当前状态与关系，近期没有明显变化"

    visible = {key: {"day_1": getattr(first, key), "day_30": getattr(last, key),
                     "delta": getattr(last, key) - getattr(first, key),
                     "formula_version": last.details["metrics"][key]["formula_version"],
                     "evidence_event_ids": last.details["metrics"][key]["evidence"][:8],
                     "evidence_summary": evidence_summary(last.details["metrics"][key]["evidence"])}
               for key in METRICS if key != "dependence"}
    dependence = last.details["metrics"]["dependence"]
    profile = [f"{key.upper()} {'HIGH' if getattr(last, key) >= 65 else 'LOW' if getattr(last, key) < 40 else 'MID'}"
               for key in METRICS]
    player = player_actor(session, game_id)
    interventions = [row for row in events if row.actor_id == player.id
                     and row.type == "MESSAGE_SENT"]
    examples = [dict(item, character=row["name"])
                for row in characters for item in row["behavior_evidence"]]
    selected_examples = []
    for direction in ("reliance", "independence"):
        by_character = {}
        for row in examples:
            if row["direction"] == direction and row["character"] not in by_character:
                by_character[row["character"]] = row
        selected_examples.extend(sorted(by_character.values(),
                                        key=lambda row: -row["day"])[:2])
    key_days = {}
    for row in events:
        weight = KEY_DAY_IMPACT.get(row.type, 0)
        if weight:
            entry = key_days.setdefault(row.day, {"day": row.day, "impact": 0,
                                                   "event_ids": []})
            entry["impact"] += weight
            entry["event_ids"].append(str(row.id))
    return {
        "formula_version": "0.2",
        "visible_scores": visible,
        "unknown": {"name": "DEPENDENCE", "day_1": first.dependence,
                    "day_30": last.dependence,
                    "delta": last.dependence - first.dependence,
                    "formula_version": dependence["formula_version"],
                    "components": dependence["components"],
                    "evidence_event_ids": dependence["evidence"][:12],
                    "evidence_examples": selected_examples,
                    "evidence_summary": (
                        "近七天未出现持续求助或委托决定；独立行动仍可见"
                        if last.dependence == 0 else evidence_summary(dependence["evidence"])
                    )},
        "ending_profile": profile,
        "key_days": sorted(key_days.values(), key=lambda row: (-row["impact"], row["day"]))[:6],
        "characters": characters,
        "relationship_graph": [{"from": names.get(row.from_actor_id),
                                 "to": names.get(row.to_actor_id),
                                 "trust": row.trust, "intimacy": row.intimacy}
                                for row in relationships],
        "player_accountability": {"messages": len(interventions),
                                  "guidance_counts": {key: sum(
                                      row.data.get("guidance") == key for row in interventions)
                                      for key in ("directive", "supportive")},
                                  "event_ids": [str(row.id) for row in interventions[-12:]]},
        "event_volume": len(events),
    }
