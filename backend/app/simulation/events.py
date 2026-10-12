from uuid import UUID

from sqlalchemy.orm import Session

from app.db.models import Event, EventAudience, EventCause, Game
from app.domain.errors import WorldConsistencyError


def record_event(
    session: Session,
    game: Game,
    *,
    day: int,
    type: str,
    actor_id: UUID | None = None,
    target_actor_id: UUID | None = None,
    data: dict | None = None,
    visibility: str = "private",
    audiences: tuple[UUID, ...] = (),
    causes: tuple[UUID, ...] = (),
) -> Event:
    game.state_version += 1
    row = Event(
        game_id=game.id,
        sequence=game.state_version,
        day=day,
        type=type,
        actor_id=actor_id,
        target_actor_id=target_actor_id,
        data=data or {},
        visibility=visibility,
    )
    session.add(row)
    session.flush()
    for cause_id in dict.fromkeys(causes):
        cause = session.get(Event, cause_id)
        if cause is None or cause.game_id != game.id or cause.sequence >= row.sequence:
            raise WorldConsistencyError("event cause must precede the event in this game")
        session.add(EventCause(game_id=game.id, event_id=row.id, cause_event_id=cause_id))
    for audience_id in dict.fromkeys(audiences):
        session.add(EventAudience(game_id=game.id, event_id=row.id, actor_id=audience_id))
    session.flush()
    return row

