from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Event, EventAudience, Game, Intention, Memory
from app.domain.errors import ActionRejected
from app.domain.guards import actor_in, known_claim
from app.domain.models import IntentionDraft
from app.simulation.events import record_event


def create_intention(session: Session, game: Game, draft: IntentionDraft) -> Intention:
    actor = actor_in(session, game.id, draft.actor_id)
    target = actor_in(session, game.id, draft.target_id)
    if actor.kind != "character" or target.kind != "character" or actor.id == target.id:
        raise ActionRejected("intention must target another character")
    known = known_claim(session, game.id, actor.id, draft.claim_id)
    existing = session.scalar(select(Intention).where(
        Intention.game_id == game.id, Intention.actor_id == actor.id,
        Intention.intended_action_type == draft.intended_action_type,
        Intention.target_actor_id == target.id, Intention.claim_id == draft.claim_id,
        Intention.status == "pending",
    ))
    if existing:
        return existing
    event = record_event(
        session, game, day=game.day, type="INTENTION_CREATED",
        actor_id=actor.id, target_actor_id=target.id,
        audiences=(actor.id,), causes=(known.last_event_id,),
        data={"action_type": draft.intended_action_type,
              "claim_id": str(draft.claim_id), "reason": draft.reason,
              "motivation_factors": draft.motivation_factors},
    )
    row = Intention(
        game_id=game.id, actor_id=actor.id,
        intended_action_type=draft.intended_action_type,
        target_actor_id=target.id, claim_id=draft.claim_id,
        reason=draft.reason, motivation_factors=draft.motivation_factors,
        created_day=game.day, urgency=draft.urgency,
        status="pending", created_event_id=event.id,
    )
    session.add(row)
    session.add(Memory(
        game_id=game.id, owner_actor_id=actor.id, kind="important",
        content=f"我考虑向 {target.display_name} 说明一条消息，但决定先等待。",
        source_event_id=event.id, day=game.day,
        importance=0.82, confidence=1.0, visibility="private",
    ))
    session.flush()
    return row


def abandon_intention(
    session: Session, game: Game, intention_id: UUID, actor_id: UUID,
    reason: str, cause_event_id: UUID,
) -> Event:
    row = session.get(Intention, intention_id)
    if row is None or row.game_id != game.id or row.actor_id != actor_id:
        raise ActionRejected("intention is not owned by actor")
    if row.status != "pending":
        raise ActionRejected("intention is already resolved")
    observed = session.get(Event, cause_event_id)
    if (observed is None or observed.game_id != game.id
            or session.get(EventAudience, (cause_event_id, actor_id)) is None):
        raise ActionRejected("abandonment cause was not observed by actor")
    event = record_event(
        session, game, day=game.day, type="INTENTION_ABANDONED",
        actor_id=actor_id, target_actor_id=row.target_actor_id,
        audiences=(actor_id,), causes=(row.created_event_id, cause_event_id),
        data={"intention_id": str(row.id), "reason": reason},
    )
    row.status = "abandoned"
    row.resolved_event_id = event.id
    session.add(Memory(
        game_id=game.id, owner_actor_id=actor_id, kind="important",
        content=f"我放弃了原先的告知打算：{reason}",
        source_event_id=event.id, day=game.day,
        importance=0.85, confidence=1.0, visibility="private",
    ))
    return event


def complete_intention(
    session: Session, game: Game, intention_id: UUID, actor_id: UUID,
    action_event_id: UUID,
) -> Event:
    row = session.get(Intention, intention_id)
    if row is None or row.game_id != game.id or row.actor_id != actor_id:
        raise ActionRejected("intention is not owned by actor")
    if row.status != "pending":
        raise ActionRejected("intention is already resolved")
    event = record_event(
        session, game, day=game.day, type="INTENTION_COMPLETED",
        actor_id=actor_id, target_actor_id=row.target_actor_id,
        audiences=(actor_id,), causes=(row.created_event_id, action_event_id),
        data={"intention_id": str(row.id)},
    )
    row.status = "completed"
    row.resolved_event_id = event.id
    return event
