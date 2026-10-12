from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Character, Event, EventAudience, Game, Memory
from app.domain.errors import WorldConsistencyError
from app.simulation.events import record_event


def update_observed_emotion(
    session: Session, game: Game, actor_id: UUID, source: Event,
    *, anxiety: float = 0, hope: float = 0,
) -> Event | None:
    if game.scenario != "temporal" or not (anxiety or hope):
        return None
    if source.game_id != game.id or session.get(
        EventAudience, (source.id, actor_id)
    ) is None:
        raise WorldConsistencyError("emotion source was not observed by character")
    character = session.get(Character, actor_id)
    previous = character.emotional_state
    current = {
        **previous,
        "anxiety": round(max(0, min(1, previous.get("anxiety", 0.5) + anxiety)), 2),
        "hope": round(max(0, min(1, previous.get("hope", 0.5) + hope)), 2),
    }
    if current == previous:
        return None
    event = record_event(
        session, game, day=source.day, type="EMOTION_CHANGED",
        actor_id=actor_id, audiences=(actor_id,), causes=(source.id,),
        data={"previous": previous, "current": current},
    )
    character.emotional_state = current
    session.add(Memory(
        game_id=game.id, owner_actor_id=actor_id, kind="emotional",
        content="这次亲身获知的消息改变了我的心情。",
        source_event_id=event.id, day=source.day,
        importance=0.82, confidence=1.0, visibility="private",
    ))
    return event
