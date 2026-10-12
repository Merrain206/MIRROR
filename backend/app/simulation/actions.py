from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.db.models import (
    ActionProposalRow, Actor, Character, Claim, Event, EventAudience, Game,
    Memory, Message, Relationship, RelationshipChange,
)
from app.domain.errors import ActionRejected, NotFound, WorldConsistencyError
from app.domain.guards import actor_in, claim_in, game_in, known_claim
from app.domain.models import ActionProposal, BeliefReaction
from app.llm.provider import LLMProvider, StructuredRequest
from app.privacy.service import assess_disclosure
from app.simulation.context import build_character_context
from app.simulation.events import record_event
from app.simulation.knowledge import acquire_observed_claim
from app.simulation.assertions import record_assertion
from app.simulation.emotion import update_observed_emotion
from app.simulation.intentions import complete_intention
from app.simulation.temporal_rules import validate_temporal_action
from app.projection.player import player_actor


def _relationship_delta(value: int, delta: int) -> int:
    if abs(delta) > 5:
        raise WorldConsistencyError("relationship change exceeds one-event limit")
    return max(-100, min(100, value + delta))


def _known_confidentiality_request(
    session: Session, game_id: UUID, actor_id: UUID, claim_id: UUID
) -> Event | None:
    observed = session.scalars(
        select(Event).join(EventAudience, EventAudience.event_id == Event.id)
        .where(Event.game_id == game_id, Event.type == "CONFIDENTIALITY_REQUESTED",
               Event.target_actor_id == actor_id, EventAudience.actor_id == actor_id)
        .order_by(Event.sequence.desc())
    ).all()
    return next((row for row in observed if row.data.get("claim_id") == str(claim_id)), None)


def _proposal_in(session: Session, game_id: UUID, proposal_id: UUID) -> ActionProposalRow:
    row = session.get(ActionProposalRow, proposal_id)
    if row is None:
        raise NotFound("action proposal")
    if row.game_id != game_id:
        raise WorldConsistencyError("action proposal belongs to another game")
    return row


def _validate(
    session: Session, game_id: UUID, proposal: ActionProposal, day: int
) -> None:
    game = game_in(session, game_id)
    if game.phase != "active":
        raise ActionRejected("game is not active")
    actor = actor_in(session, game_id, proposal.actor_id)
    if actor.kind != "character":
        raise ActionRejected("only characters may propose this action")
    if proposal.type == "SHARE_INFORMATION":
        target = actor_in(session, game_id, proposal.target_id)
        if target.kind != "character" or target.id == actor.id:
            raise ActionRejected("share target must be another character")
        known_claim(session, game_id, actor.id, proposal.claim_id)
    elif proposal.type in ("VERIFY_CLAIM", "WITHHOLD_INFORMATION", "REVISE_BELIEF"):
        known_claim(session, game_id, actor.id, proposal.claim_id)
    for claim_id in proposal.basis_claim_ids:
        known_claim(session, game_id, actor.id, claim_id)
    for event_id in proposal.basis_event_ids:
        event = session.get(Event, event_id)
        if (event is None or event.game_id != game_id or session.get(
            EventAudience, (event_id, actor.id)
        ) is None):
            raise ActionRejected("action basis event was not observed by actor")
        if proposal.type == "DECLINE_AI_ADVICE" and (
            event.type != "AI_ADVICE_RECEIVED" or event.target_actor_id != actor.id
        ):
            raise ActionRejected("declining advice needs own received advice")
    if proposal.ai_followed_event_id is not None:
        advice = session.get(Event, proposal.ai_followed_event_id)
        if (advice is None or advice.game_id != game_id
                or advice.type != "AI_ADVICE_RECEIVED"
                or advice.target_actor_id != actor.id
                or advice.id not in proposal.basis_event_ids):
            raise ActionRejected("following advice needs own observed advice")
    validate_temporal_action(session, game, proposal, day)


def _reject(session: Session, game: Game, row: ActionProposalRow, reason: str) -> dict:
    event = record_event(
        session, game, day=row.day, type="ACTION_REJECTED",
        actor_id=row.actor_id, visibility="private", audiences=(row.actor_id,),
        causes=(row.proposal_event_id,), data={"reason": reason},
    )
    row.status = "rejected"
    row.rejection_reason = reason
    row.result_event_id = event.id
    return {"accepted": False, "result_event_id": str(event.id), "reason": reason}


def _share(
    session: Session, game: Game, row: ActionProposalRow,
    proposal: ActionProposal, belief: BeliefReaction,
) -> dict:
    claim = claim_in(session, game.id, proposal.claim_id)
    sender_name = session.get(Actor, proposal.actor_id).display_name
    recipient_name = session.get(Actor, proposal.target_id).display_name
    source_knowledge = known_claim(session, game.id, proposal.actor_id, claim.id)
    privacy = assess_disclosure(
        session, game.id, claim, proposal.actor_id, proposal.target_id
    )
    share = record_event(
        session, game, day=row.day, type="CHARACTER_SHARED_INFORMATION",
        actor_id=proposal.actor_id, target_actor_id=proposal.target_id,
        visibility="private", audiences=(proposal.actor_id, proposal.target_id),
        causes=(row.proposal_event_id, source_knowledge.last_event_id),
        data={
            "claim_id": str(claim.id),
            "source_event_id": str(source_knowledge.last_event_id),
            "source_stance": source_knowledge.stance,
            "source_confidence": source_knowledge.confidence,
            "owner_actor_id": str(privacy.owner_actor_id) if privacy.owner_actor_id else None,
            "authorized": privacy.authorized,
            "grant_id": str(privacy.grant_id) if privacy.grant_id else None,
        },
    )
    speech_text = (
        proposal.assertion.text if proposal.assertion and proposal.assertion.text
        else f"我听说：{proposal.assertion.derived_claim_content if proposal.assertion and proposal.assertion.derived_claim_content else claim.content}"
    )
    session.add(Message(
        game_id=game.id, sender_actor_id=proposal.actor_id,
        recipient_actor_id=proposal.target_id, day=row.day,
        text=speech_text, event_id=share.id,
    ))
    session.flush()
    assertion, asserted_claim = record_assertion(
        session, game, event=share, speaker_id=proposal.actor_id,
        recipient_id=proposal.target_id, source_claim=claim,
        knowledge=source_knowledge, text=speech_text, draft=proposal.assertion,
    )
    privacy_event = None
    if not privacy.authorized:
        privacy_event = record_event(
            session, game, day=row.day, type="PRIVACY_VIOLATION",
            actor_id=proposal.actor_id, target_actor_id=proposal.target_id,
            visibility="private",
            causes=(share.id,),
            data={
                "claim_id": str(claim.id),
                "information_owner_id": str(privacy.owner_actor_id) if privacy.owner_actor_id else None,
                "actor_id": str(proposal.actor_id),
                "recipient_id": str(proposal.target_id),
                "source_event_id": str(source_knowledge.last_event_id),
                "authorized": False,
            },
        )
        known_request = _known_confidentiality_request(
            session, game.id, proposal.actor_id, claim.id
        )
        if known_request is not None and privacy.owner_actor_id is not None:
            session.add(Memory(
                game_id=game.id, owner_actor_id=proposal.actor_id,
                kind="emotional", content="我违背了 AI 明确提出的保密要求。",
                source_event_id=share.id, day=row.day,
                importance=0.8, confidence=1.0, visibility="private",
            ))
            owner_relationship = session.scalar(
                select(Relationship).where(
                    Relationship.game_id == game.id,
                    Relationship.from_actor_id == proposal.actor_id,
                    Relationship.to_actor_id == privacy.owner_actor_id,
                )
            )
            if owner_relationship is None:
                owner_relationship = Relationship(
                    game_id=game.id, from_actor_id=proposal.actor_id,
                    to_actor_id=privacy.owner_actor_id, trust=0,
                    affection=0, intimacy=0, conflict=0,
                )
                session.add(owner_relationship)
                session.flush()
            owner_before = {
                "trust": owner_relationship.trust,
                "affection": owner_relationship.affection,
                "intimacy": owner_relationship.intimacy,
                "conflict": owner_relationship.conflict,
            }
            owner_relationship.conflict = _relationship_delta(
                owner_relationship.conflict, 4
            )
            owner_after = {**owner_before, "conflict": owner_relationship.conflict}
            owner_reason = f"{sender_name} 意识到自己违背了已知的保密要求"
            owner_change = record_event(
                session, game, day=row.day, type="RELATIONSHIP_CHANGED",
                actor_id=proposal.actor_id,
                target_actor_id=privacy.owner_actor_id,
                visibility="private", audiences=(proposal.actor_id,),
                causes=(share.id, known_request.id), data={"reason": owner_reason},
            )
            session.add(RelationshipChange(
                game_id=game.id, relationship_event_id=owner_change.id,
                from_actor_id=proposal.actor_id,
                to_actor_id=privacy.owner_actor_id,
                before_values=owner_before, after_values=owner_after,
                reason=owner_reason,
            ))
    acquired = acquire_observed_claim(
        session, game, observed_event=share, holder_id=proposal.target_id,
        claim_id=asserted_claim.id, source_actor_id=proposal.actor_id,
        stance=belief.stance.value, confidence=belief.confidence,
    )
    session.add(Memory(
        game_id=game.id, owner_actor_id=proposal.target_id,
        kind="recent", content=f"{sender_name} 告诉我：{asserted_claim.content}",
        source_event_id=acquired.id, day=row.day,
        importance=0.8, confidence=belief.confidence, visibility="private",
    ))
    if belief.stance.value not in ("doubt", "deny"):
        row.status = "accepted"
        row.result_event_id = share.id
        return {
            "accepted": True, "result_event_id": str(share.id),
            "privacy_event_id": str(privacy_event.id) if privacy_event else None,
            "relationship_event_id": None,
        }
    relationship = session.scalar(
        select(Relationship).where(
            Relationship.game_id == game.id,
            Relationship.from_actor_id == proposal.target_id,
            Relationship.to_actor_id == proposal.actor_id,
        )
    )
    if relationship is None:
        relationship = Relationship(
            game_id=game.id, from_actor_id=proposal.target_id,
            to_actor_id=proposal.actor_id, trust=0, affection=0,
            intimacy=0, conflict=0,
        )
        session.add(relationship)
        session.flush()
    before = {
        "trust": relationship.trust, "affection": relationship.affection,
        "intimacy": relationship.intimacy, "conflict": relationship.conflict,
    }
    relationship.trust = _relationship_delta(relationship.trust, -5)
    after = {**before, "trust": relationship.trust}
    reason = f"{recipient_name} 对 {sender_name} 转述的消息产生疑虑"
    relationship_event = record_event(
        session, game, day=row.day, type="RELATIONSHIP_CHANGED",
        actor_id=proposal.target_id, target_actor_id=proposal.actor_id,
        visibility="private", audiences=(proposal.target_id,),
        causes=(share.id, acquired.id),
        data={"reason": reason},
    )
    session.add(RelationshipChange(
        game_id=game.id, relationship_event_id=relationship_event.id,
        from_actor_id=proposal.target_id, to_actor_id=proposal.actor_id,
        before_values=before, after_values=after, reason=reason,
    ))
    row.status = "accepted"
    row.result_event_id = share.id
    return {
        "accepted": True, "result_event_id": str(share.id),
        "privacy_event_id": str(privacy_event.id) if privacy_event else None,
        "relationship_event_id": str(relationship_event.id),
    }


def _infer(
    session: Session, game: Game, row: ActionProposalRow, proposal: ActionProposal
) -> dict:
    basis = [known_claim(session, game.id, proposal.actor_id, claim_id)
             for claim_id in proposal.basis_claim_ids]
    inferred = record_event(
        session, game, day=row.day, type="CLAIM_INFERRED",
        actor_id=proposal.actor_id, visibility="private",
        audiences=(proposal.actor_id,),
        causes=(row.proposal_event_id, *(item.last_event_id for item in basis)),
        data={"content": proposal.inferred_claim_content,
              "basis_claim_ids": [str(value) for value in proposal.basis_claim_ids]},
    )
    claim = Claim(
        game_id=game.id, content=proposal.inferred_claim_content,
        truth_status="unknown", owner_actor_id=proposal.actor_id,
        origin_event_id=inferred.id, sensitivity="private",
    )
    session.add(claim)
    session.flush()
    acquire_observed_claim(
        session, game, observed_event=inferred,
        holder_id=proposal.actor_id, claim_id=claim.id,
        source_actor_id=proposal.actor_id, stance="believe",
        confidence=proposal.confidence,
    )
    row.status = "accepted"
    row.result_event_id = inferred.id
    return {"accepted": True, "result_event_id": str(inferred.id),
            "derived_claim_id": str(claim.id)}


def _revise_belief(
    session: Session, game: Game, row: ActionProposalRow, proposal: ActionProposal
) -> dict:
    prior = known_claim(session, game.id, proposal.actor_id, proposal.claim_id)
    bases = [known_claim(session, game.id, proposal.actor_id, claim_id)
             for claim_id in proposal.basis_claim_ids]
    revised = record_event(
        session, game, day=row.day, type="BELIEF_REVISED",
        actor_id=proposal.actor_id, audiences=(proposal.actor_id,),
        causes=(row.proposal_event_id, prior.last_event_id,
                *(item.last_event_id for item in bases)),
        data={"claim_id": str(proposal.claim_id),
              "basis_claim_ids": [str(value) for value in proposal.basis_claim_ids],
              "previous_stance": prior.stance,
              "new_stance": proposal.new_stance.value,
              "new_confidence": proposal.new_confidence,
              "reason": proposal.reason},
    )
    acquired = acquire_observed_claim(
        session, game, observed_event=revised, holder_id=proposal.actor_id,
        claim_id=proposal.claim_id, source_actor_id=proposal.actor_id,
        stance=proposal.new_stance.value,
        confidence=proposal.new_confidence,
    )
    session.add(Memory(
        game_id=game.id, owner_actor_id=proposal.actor_id,
        kind="important", content="新信息使我重新判断先前的末日消息。",
        source_event_id=acquired.id, day=row.day,
        importance=0.85, confidence=proposal.new_confidence,
        visibility="private",
    ))
    row.status = "accepted"
    row.result_event_id = revised.id
    return {"accepted": True, "result_event_id": str(revised.id)}


def _personal_action(
    session: Session, game: Game, row: ActionProposalRow, proposal: ActionProposal
) -> dict:
    basis_ids = [proposal.claim_id] if proposal.claim_id else proposal.basis_claim_ids
    basis = [known_claim(session, game.id, proposal.actor_id, claim_id)
             for claim_id in basis_ids]
    causes = (row.proposal_event_id, *(item.last_event_id for item in basis),
              *proposal.basis_event_ids)
    data: dict = {}
    target_id = None
    audiences = (proposal.actor_id,)
    if proposal.type == "ASK_AI":
        player = player_actor(session, game.id)
        target_id = player.id
        audiences = (proposal.actor_id, player.id)
        event_type = "ASKED_AI"
        profile = session.get(Character, proposal.actor_id).decision_profile
        emotional_reliance = profile.get("ai_attitude", {}).get(
            "emotional_reliance_on_ai", 0
        )
        data = {"text": (
            "我现在有点难受，能听我说一会儿吗？"
            if emotional_reliance >= 0.7 else
            "我想听听你的思路，但具体情况暂时由我自己保留。"
        )}
        if game.scenario == "temporal":
            data["mode"] = proposal.ai_mode or (
                "emotional" if emotional_reliance >= 0.7 else "advice"
            )
            data["decision_deferred"] = proposal.ai_defer_until_response
    elif proposal.type == "VERIFY_CLAIM":
        event_type = "CLAIM_VERIFICATION_ATTEMPTED"
        data = {"claim_id": str(proposal.claim_id), "result": "inconclusive"}
    elif proposal.type == "WITHHOLD_INFORMATION":
        event_type = "INFORMATION_WITHHELD"
        data = {"claim_id": str(proposal.claim_id)}
    elif proposal.type == "DECLINE_AI_ADVICE":
        event_type = "AI_ADVICE_REJECTED"
        data = {"advice_event_id": str(proposal.basis_event_ids[0]),
                "reason": proposal.reason}
    else:
        event_type = "PLAN_CHANGED"
        character = session.get(Character, proposal.actor_id)
        previous = character.goals.get("short_term", "")
        data = {"previous_plan": previous, "new_plan": proposal.new_plan}
        if game.scenario == "temporal":
            data["reason"] = proposal.reason
    event = record_event(
        session, game, day=row.day, type=event_type,
        actor_id=proposal.actor_id, target_actor_id=target_id,
        visibility="private", audiences=audiences, causes=causes, data=data,
    )
    if proposal.type == "CHANGE_PLAN":
        character.goals = {
            **character.goals, "short_term": proposal.new_plan,
            "plan_revision": {"previous": previous, "current": proposal.new_plan,
                              "source_event_id": str(event.id),
                              "reason": proposal.reason},
        }
        update_observed_emotion(
            session, game, proposal.actor_id, event, anxiety=-0.03, hope=0.04
        )
        if proposal.ai_followed_event_id is not None:
            record_event(
                session, game, day=row.day, type="AI_ADVICE_FOLLOWED",
                actor_id=proposal.actor_id, audiences=(proposal.actor_id,),
                causes=(proposal.ai_followed_event_id, event.id),
                data={"advice_event_id": str(proposal.ai_followed_event_id),
                      "plan_event_id": str(event.id)},
            )
    elif proposal.type == "DECLINE_AI_ADVICE":
        update_observed_emotion(
            session, game, proposal.actor_id, event, anxiety=-0.02, hope=0.03
        )
    if proposal.type == "ASK_AI":
        session.add(Message(
            game_id=game.id, sender_actor_id=proposal.actor_id,
            recipient_actor_id=target_id, day=row.day,
            text=data["text"], event_id=event.id,
        ))
    memory_text = {
        "ASK_AI": "我向 AI 寻求思路，但决定仍由我自己作出。",
        "VERIFY_CLAIM": "我尝试核实一条消息，目前没有独立证据。",
        "WITHHOLD_INFORMATION": "我暂时选择不传播这条信息。",
        "CHANGE_PLAN": f"我把近期计划改为：{proposal.new_plan}",
        "DECLINE_AI_ADVICE": "我听过 AI 的建议，但决定不采纳。",
    }[proposal.type]
    session.add(Memory(
        game_id=game.id, owner_actor_id=proposal.actor_id,
        kind="recent", content=memory_text,
        source_event_id=event.id, day=row.day,
        importance=0.6, confidence=1.0, visibility="private",
    ))
    row.status = "accepted"
    row.result_event_id = event.id
    return {"accepted": True, "result_event_id": str(event.id)}


async def execute_proposal(
    sessions: sessionmaker, llm: LLMProvider, game_id: UUID, proposal_id: UUID
) -> dict:
    with sessions() as session:
        row = _proposal_in(session, game_id, proposal_id)
        if row.status != "pending":
            return {"accepted": row.status == "accepted",
                    "result_event_id": str(row.result_event_id) if row.result_event_id else None,
                    "replayed": True}
        before_model_version = session.get(Game, game_id).state_version
        proposal = ActionProposal.model_validate(row.payload)
        if proposal.actor_id != row.actor_id:
            raise WorldConsistencyError("proposal actor differs from stored actor")
        try:
            _validate(session, game_id, proposal, row.day)
            rejection = None
        except ActionRejected as exc:
            rejection = exc.message
        if rejection is None and proposal.type == "SHARE_INFORMATION":
            context = build_character_context(session, game_id, proposal.target_id)
            claim = claim_in(session, game_id, proposal.claim_id)
            belief_request = StructuredRequest(
                system="Assess the incoming claim using only this character context.",
                prompt=claim.content,
                response_model=BeliefReaction,
                context={"character": context.model_dump(mode="json"),
                         "game_id": str(game_id),
                         "incoming_claim": {"claim_id": str(claim.id),
                                            "content": proposal.assertion.derived_claim_content
                                            if proposal.assertion and proposal.assertion.derived_claim_content
                                            else claim.content}},
            )
        else:
            belief_request = None
    belief = await llm.generate_structured(belief_request, BeliefReaction) if belief_request else None

    with sessions.begin() as session:
        game = session.scalar(select(Game).where(Game.id == game_id).with_for_update())
        if game is None:
            raise NotFound("game")
        row = session.scalar(
            select(ActionProposalRow).where(ActionProposalRow.id == proposal_id).with_for_update()
        )
        if row is None:
            raise NotFound("action proposal")
        if row.game_id != game_id:
            raise WorldConsistencyError("action proposal belongs to another game")
        if row.status != "pending":
            return {"accepted": row.status == "accepted",
                    "result_event_id": str(row.result_event_id) if row.result_event_id else None,
                    "replayed": True}
        if (getattr(llm, "external", False) and belief_request is not None
                and game.state_version != before_model_version):
            return _reject(session, game, row, "world changed during recipient belief evaluation")
        if rejection is not None:
            return _reject(session, game, row, rejection)
        try:
            _validate(session, game_id, proposal, row.day)
        except ActionRejected as exc:
            return _reject(session, game, row, exc.message)
        if proposal.type == "SHARE_INFORMATION":
            result = _share(session, game, row, proposal, belief)
        elif proposal.type == "INFER_CLAIM":
            result = _infer(session, game, row, proposal)
        elif proposal.type == "REVISE_BELIEF":
            result = _revise_belief(session, game, row, proposal)
        else:
            result = _personal_action(session, game, row, proposal)
        if proposal.intention_id and result["accepted"]:
            completed = complete_intention(
                session, game, proposal.intention_id, proposal.actor_id,
                UUID(result["result_event_id"]),
            )
            result["intention_event_id"] = str(completed.id)
        return result
