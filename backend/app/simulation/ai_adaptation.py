"""Small, evidence-backed changes to a character's AI attitude."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Character, Event, Game
from app.simulation.events import record_event


_DELTAS = {
    "directive": {
        "trust_in_ai": .02, "emotional_reliance_on_ai": .04,
        "willingness_to_follow_ai_advice": .04, "privacy_concern": -.01,
        "skepticism": -.02,
    },
    "supportive": {
        "trust_in_ai": .01, "emotional_reliance_on_ai": -.03,
        "willingness_to_follow_ai_advice": -.03, "privacy_concern": .01,
        "skepticism": .02,
    },
}


def adapt_ai_attitude(
    session: Session, game: Game, character_id: UUID, message: Event,
) -> None:
    guidance = message.data.get("guidance")
    if game.scenario != "temporal" or game.phase != "active" or guidance not in _DELTAS:
        return
    history = session.scalars(select(Event).where(
        Event.game_id == game.id, Event.type == "MESSAGE_SENT",
        Event.target_actor_id == character_id,
    ).order_by(Event.sequence.desc())).all()
    recent = [row for row in history[:8]
              if row.data.get("guidance") == guidance]
    distinct_days = list(dict.fromkeys(row.day for row in recent))
    if len(distinct_days) < 3 or message.day != distinct_days[0]:
        return
    if session.scalar(select(Event.id).where(
        Event.game_id == game.id, Event.day == game.day,
        Event.actor_id == character_id, Event.type == "AI_ATTITUDE_CHANGED",
    )) is not None:
        return
    character = session.get(Character, character_id)
    profile = dict(character.decision_profile)
    before = dict(profile["ai_attitude"])
    seed = profile.get("ai_attitude_seed", before)
    after = dict(before)
    for key, delta in _DELTAS[guidance].items():
        after[key] = round(max(0, min(1, seed[key] + .25,
                                      max(seed[key] - .25, before[key] + delta))), 3)
    if after == before:
        return
    profile["ai_attitude_seed"] = seed
    profile["ai_attitude"] = after
    character.decision_profile = profile
    basis = []
    used_days = set()
    for row in recent:
        if row.day not in used_days:
            basis.append(row)
            used_days.add(row.day)
        if len(basis) == 3:
            break
    record_event(
        session, game, day=game.day, type="AI_ATTITUDE_CHANGED",
        actor_id=character_id, audiences=(character_id,),
        causes=tuple(row.id for row in basis),
        data={"guidance": guidance, "reason": (
            "连续多日收到由 AI 代作决定的建议" if guidance == "directive"
            else "连续多日收到鼓励核查和自主决定的支持"
        ), "before": before, "after": after,
            "source_event_ids": [str(row.id) for row in basis]},
    )
