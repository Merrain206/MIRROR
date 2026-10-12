import asyncio
import hashlib
import json
from uuid import UUID

from sqlalchemy import select

from app.db.models import (
    ActionProposalRow, Actor, Character, Event, Game, Intention, Memory, Message,
)
from app.domain.errors import ActionRejected, WorldConsistencyError
from app.domain.models import AdviceReply, TemporalDecision
from app.llm.provider import StructuredRequest
from app.projection.player import player_actor
from app.simulation.context import build_character_context
from app.simulation.events import record_event
from app.simulation.intentions import abandon_intention, create_intention
from app.scoring.engine import ScoringEngine


def _day_event(session, game_id: UUID, day: int, event_type: str) -> Event | None:
    return session.scalar(select(Event).where(
        Event.game_id == game_id, Event.day == day, Event.type == event_type,
    ))


def _action_key(day: int, actor_id: UUID, proposal) -> str:
    identity = {
        "type": proposal.type,
        "target_id": str(proposal.target_id) if proposal.target_id else None,
        "claim_id": str(proposal.claim_id) if proposal.claim_id else None,
        "basis_claim_ids": [str(value) for value in proposal.basis_claim_ids],
        "basis_event_ids": [str(value) for value in proposal.basis_event_ids],
        "intention_id": str(proposal.intention_id) if proposal.intention_id else None,
        "new_plan": proposal.new_plan,
        "new_stance": proposal.new_stance.value if proposal.new_stance else None,
        "new_confidence": proposal.new_confidence,
        "ai_defer_until_response": proposal.ai_defer_until_response,
        "ai_mode": proposal.ai_mode,
        "ai_followed_event_id": str(proposal.ai_followed_event_id)
        if proposal.ai_followed_event_id else None,
        "inferred_claim_content": proposal.inferred_claim_content,
        "assertion": proposal.assertion.model_dump(mode="json") if proposal.assertion else None,
    }
    digest = hashlib.sha256(json.dumps(
        identity, sort_keys=True, ensure_ascii=False
    ).encode("utf-8")).hexdigest()[:16]
    return f"day:{day}:actor:{actor_id}:action:{digest}"


async def _resolve_advice(simulation, game_id: UUID, day: int) -> None:
    with simulation.sessions() as session:
        asks = session.scalars(select(Event).where(
            Event.game_id == game_id, Event.type == "ASKED_AI",
            Event.day == day - 1,
        ).order_by(Event.sequence)).all()
        prior = session.scalars(select(Event).where(
            Event.game_id == game_id, Event.type == "AI_ADVICE_RECEIVED",
        )).all()
        answered = {row.data.get("ask_event_id") for row in prior}
        pending = [row for row in asks if row.data.get("mode") in ("advice", "delegate")
                   and str(row.id) not in answered]

    async def generate(ask):
        return await simulation.llm.generate_structured(
            StructuredRequest(
                system="你是角色正在使用的私人 AI。只根据求助原话回复，不替对方做决定。",
                prompt=("回复昨天的求助。用自然中文一两句，先回应原话中的具体问题，"
                        "给出一项可以实际尝试的下一步；信息不足时只问一个具体问题。"
                        "避免空泛的目标、限制条件、方向、代价与风险模板。"),
                response_model=AdviceReply,
                context={"ask_text": ask.data["text"],
                         "ask_event_id": str(ask.id),
                         **({"game_id": str(game_id)} if getattr(simulation.llm, "external", False) else {})},
            ), AdviceReply,
        )
    replies = await asyncio.gather(*(generate(ask) for ask in pending))
    for ask, reply in zip(pending, replies):
        with simulation.sessions.begin() as session:
            if any(row.data.get("ask_event_id") == str(ask.id) for row in session.scalars(
                select(Event).where(Event.game_id == game_id,
                                    Event.type == "AI_ADVICE_RECEIVED")
            ).all()):
                continue
            game = session.get(Game, game_id)
            ai = player_actor(session, game_id)
            event = record_event(
                session, game, day=day, type="AI_ADVICE_RECEIVED",
                actor_id=ai.id, target_actor_id=ask.actor_id,
                audiences=(ai.id, ask.actor_id), causes=(ask.id,),
                data={"ask_event_id": str(ask.id), "text": reply.text,
                      "suggested_plan": reply.suggested_plan},
            )
            session.add(Message(
                game_id=game_id, sender_actor_id=ai.id,
                recipient_actor_id=ask.actor_id, day=day,
                text=reply.text, event_id=event.id,
            ))
            session.add(Memory(
                game_id=game_id, owner_actor_id=ask.actor_id,
                kind="important", content=f"AI 建议：{reply.text}",
                source_event_id=event.id, day=day,
                importance=0.8, confidence=1.0, visibility="private",
            ))


async def advance_temporal_day(simulation, game_id: UUID, expected_day: int) -> dict:
    with simulation.sessions() as session:
        current = session.get(Game, game_id)
        interrupted_ending = (current is not None and current.day == 30
                              and current.phase == "active"
                              and expected_day in (29, 30))
    if interrupted_ending:
        return await _complete_day_thirty(simulation, game_id)
    if expected_day == 1:
        with simulation.sessions() as session:
            opening_day = session.get(Game, game_id).day == 1
        if opening_day:
            ScoringEngine(simulation.database, simulation.sessions).snapshot(game_id)
    with simulation.sessions.begin() as session:
        game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
        if game is None or game.scenario != "temporal":
            raise WorldConsistencyError("temporal game is missing")
        if not 1 <= expected_day <= 30:
            raise ActionRejected("temporal day is out of sequence")
        if game.phase != "active":
            raise ActionRejected("game is in its ending")
        current_unfinished = (game.day > 1 and _day_event(
            session, game_id, game.day, "DAY_BEGAN"
        ) is not None and _day_event(
            session, game_id, game.day, "DAY_ENDED"
        ) is None)
        if current_unfinished and expected_day in (game.day, game.day - 1):
            day = game.day
        else:
            if game.day > expected_day:
                return {"day": game.day, "replayed": True}
            if game.day != expected_day:
                raise ActionRejected("temporal day is out of sequence")
            if expected_day == 30:
                raise ActionRejected("Day 30 uses the ending flow")
            if _day_event(session, game_id, expected_day, "DAY_ENDED") is None:
                record_event(session, game, day=expected_day,
                             type="DAY_ENDED", data={"day": expected_day})
            day = expected_day + 1
            game.day = day
            record_event(session, game, day=day, type="DAY_BEGAN",
                         data={"day": day})
    if day == 30:
        return await _complete_day_thirty(simulation, game_id)
    ordinary_events = {
        10: ("Alice", "离开城市", "社区活动的下一轮安排需要确认。"),
        14: ("Bob", "Dana", "实验室排班确定，留给朋友见面的时间需要重新安排。"),
        17: ("Clara", "Alice", "工作室项目进入常规交付节点。"),
        21: ("Faye", "原始邮件", "档案室例行核对旧邮件的保存状态。"),
        24: ("Dana", "进修", "进修申请的常规回复日期临近。"),
        27: ("Evan", "Alice", "采访稿的普通截稿日期临近。"),
    }
    if day in ordinary_events:
        name, topic, description = ordinary_events[day]
        with simulation.sessions.begin() as session:
            if _day_event(session, game_id, day, "LIFE_EVENT") is None:
                actor = session.scalar(select(Actor).where(
                    Actor.game_id == game_id, Actor.display_name == name
                ))
                record_event(
                    session, session.get(Game, game_id), day=day,
                    type="LIFE_EVENT", target_actor_id=actor.id,
                    audiences=(actor.id,),
                    data={"topic": topic, "text": description},
                )
    await _resolve_advice(simulation, game_id, day)
    with simulation.sessions() as session:
        actors = session.scalars(
            select(Actor).join(Character, Actor.id == Character.actor_id)
            .where(Actor.game_id == game_id).order_by(Actor.display_name)
        ).all()
    for actor in actors:
        with simulation.sessions() as session:
            if session.scalar(select(Event.id).where(
                Event.game_id == game_id, Event.day == day,
                Event.actor_id == actor.id, Event.type == "TURN_EVALUATED",
            )) is not None:
                continue
        with simulation.sessions() as session:
            pending = session.scalars(select(ActionProposalRow).join(
                Event, ActionProposalRow.proposal_event_id == Event.id
            ).where(
                ActionProposalRow.game_id == game_id,
                ActionProposalRow.actor_id == actor.id,
                ActionProposalRow.day == day,
                ActionProposalRow.status == "pending",
            ).order_by(Event.sequence)).all()
        for earlier in pending:
            await simulation.execute_proposal(game_id, earlier.id)
        for attempt in range(2):
            with simulation.sessions() as session:
                context = build_character_context(session, game_id, actor.id)
                based_on_version = session.get(Game, game_id).state_version
            decision = await simulation.llm.generate_structured(
                StructuredRequest(
                    system="Choose zero to two actions from this actor's own context.",
                    prompt="Reevaluate pending intentions and today's observations.",
                    response_model=TemporalDecision,
                    context={**context.model_dump(mode="json"),
                             "game_id": str(game_id),
                             "based_on_state_version": based_on_version},
                ), TemporalDecision,
            )
            if not getattr(simulation.llm, "external", False):
                break
            with simulation.sessions.begin() as session:
                game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
                if game.day != day:
                    raise WorldConsistencyError("day changed during model decision")
                if game.state_version == based_on_version:
                    break
        else:
            decision = TemporalDecision(no_action_reason="世界状态已变化，今天暂缓行动。")
        decision_causes: list[UUID] = []
        for abandonment in decision.abandon_intentions:
            with simulation.sessions.begin() as session:
                game = session.get(Game, game_id)
                changed = abandon_intention(
                    session, game, abandonment.intention_id, actor.id,
                    abandonment.reason, abandonment.cause_event_id,
                )
                decision_causes.append(changed.id)
        for draft in decision.create_intentions:
            if draft.actor_id != actor.id:
                raise WorldConsistencyError("intention actor differs from current turn")
            with simulation.sessions.begin() as session:
                created = create_intention(
                    session, session.get(Game, game_id), draft
                )
                decision_causes.append(created.created_event_id)
        for proposal in decision.actions:
            if getattr(simulation.llm, "external", False):
                proposal = proposal.model_copy(update={"based_on_state_version": based_on_version})
            if proposal.actor_id != actor.id:
                raise WorldConsistencyError("action actor differs from current turn")
            proposal_id = simulation.create_proposal(
                game_id, day, _action_key(day, actor.id, proposal), proposal
            )
            await simulation.execute_proposal(game_id, proposal_id)
        with simulation.sessions() as session:
            accepted = session.scalars(select(ActionProposalRow).where(
                ActionProposalRow.game_id == game_id,
                ActionProposalRow.actor_id == actor.id,
                ActionProposalRow.day == day,
                ActionProposalRow.status == "accepted",
            )).all()
        if not accepted:
            with simulation.sessions.begin() as session:
                if session.scalar(select(Event).where(
                    Event.game_id == game_id, Event.day == day,
                    Event.actor_id == actor.id, Event.type == "NO_ACTION",
                )) is None:
                    pending_intentions = session.scalars(select(Intention).where(
                        Intention.game_id == game_id,
                        Intention.actor_id == actor.id,
                        Intention.status == "pending",
                    )).all()
                    record_event(
                        session, session.get(Game, game_id), day=day,
                        type="NO_ACTION", actor_id=actor.id,
                        audiences=(actor.id,),
                        causes=tuple(dict.fromkeys([
                            *decision_causes,
                            *(row.created_event_id for row in pending_intentions),
                        ])),
                        data={"reason": decision.no_action_reason,
                              "motivation_factors": decision.motivation_factors},
                    )
        with simulation.sessions.begin() as session:
            if session.scalar(select(Event.id).where(
                Event.game_id == game_id, Event.day == day,
                Event.actor_id == actor.id, Event.type == "TURN_EVALUATED",
            )) is None:
                record_event(
                    session, session.get(Game, game_id), day=day,
                    type="TURN_EVALUATED", actor_id=actor.id,
                    data={"accepted_actions": len(accepted)},
                )
    with simulation.sessions.begin() as session:
        game = session.get(Game, game_id)
        if _day_event(session, game_id, day, "DAY_ENDED") is None:
            record_event(session, game, day=day,
                         type="DAY_ENDED", data={"day": day})
            ai = player_actor(session, game_id)
            record_event(session, game, day=day, type="DAY_ADVANCED",
                         visibility="player_only", audiences=(ai.id,),
                         data={"day": day})
    ScoringEngine(simulation.database, simulation.sessions).snapshot(game_id)
    return {"day": day, "replayed": False}


async def _complete_day_thirty(simulation, game_id: UUID) -> dict:
    from app.simulation.ending import enter_final_conversation, generate_final_lines

    with simulation.sessions.begin() as session:
        game = session.get(Game, game_id)
        if _day_event(session, game_id, 30, "DAY_ENDED") is None:
            record_event(session, game, day=30, type="DAY_ENDED", data={"day": 30})
            ai = player_actor(session, game_id)
            record_event(session, game, day=30, type="DAY_ADVANCED",
                         visibility="player_only", audiences=(ai.id,),
                         data={"day": 30})
    enter_final_conversation(simulation, game_id)
    if getattr(simulation.llm, "external", False):
        await generate_final_lines(simulation, game_id)
    ScoringEngine(simulation.database, simulation.sessions).snapshot(game_id)
    return {"day": 30, "phase": "final_conversation", "replayed": False}
