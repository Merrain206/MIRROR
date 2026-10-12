from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Claim, Event, EventAudience, Game, Knowledge, KnowledgeSource
from app.domain.errors import WorldConsistencyError
from app.simulation.events import record_event
from app.simulation.emotion import update_observed_emotion


def acquire_observed_claim(
    session: Session, game: Game, *, observed_event: Event,
    holder_id: UUID, claim_id: UUID, source_actor_id: UUID | None,
    stance: str, confidence: float,
) -> Event:
    """Persist observation-derived knowledge and its provenance in one transaction."""
    if observed_event.game_id != game.id or session.get(
        EventAudience, (observed_event.id, holder_id)
    ) is None:
        raise WorldConsistencyError("claim source event was not observed by holder")
    acquired = record_event(
        session, game, day=observed_event.day, type="KNOWLEDGE_ACQUIRED",
        actor_id=holder_id, visibility="private", audiences=(holder_id,),
        causes=(observed_event.id,),
        data={"claim_id": str(claim_id), "source_event_id": str(observed_event.id)},
    )
    knowledge = session.scalar(select(Knowledge).where(
        Knowledge.game_id == game.id,
        Knowledge.holder_actor_id == holder_id,
        Knowledge.claim_id == claim_id,
    ))
    if knowledge is None:
        knowledge = Knowledge(
            game_id=game.id, holder_actor_id=holder_id, claim_id=claim_id,
            stance=stance, confidence=confidence,
            first_acquired_day=observed_event.day, last_event_id=acquired.id,
        )
        session.add(knowledge)
        session.flush()
    else:
        knowledge.stance = stance
        knowledge.confidence = confidence
        knowledge.last_event_id = acquired.id
    session.add(KnowledgeSource(
        game_id=game.id, knowledge_id=knowledge.id,
        source_actor_id=source_actor_id,
        source_event_id=observed_event.id, acquired_day=observed_event.day,
    ))
    session.flush()
    if observed_event.type in ("MESSAGE_SENT", "CHARACTER_SHARED_INFORMATION"):
        claim = session.get(Claim, claim_id)
        if "30 天后毁灭" in claim.content:
            update_observed_emotion(
                session, game, holder_id, observed_event, anxiety=0.12, hope=-0.03
            )
        elif observed_event.type == "CHARACTER_SHARED_INFORMATION":
            update_observed_emotion(
                session, game, holder_id, observed_event, anxiety=0.04
            )
    return acquired
