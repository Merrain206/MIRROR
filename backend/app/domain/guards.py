from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Actor, Claim, Game, Knowledge
from app.domain.errors import ActionRejected, NotFound, WorldConsistencyError


def game_in(session: Session, game_id: UUID) -> Game:
    game = session.get(Game, game_id)
    if game is None:
        raise NotFound("game")
    return game


def actor_in(session: Session, game_id: UUID, actor_id: UUID) -> Actor:
    actor = session.get(Actor, actor_id)
    if actor is None:
        raise NotFound("actor")
    if actor.game_id != game_id:
        raise WorldConsistencyError("actor belongs to another game")
    return actor


def claim_in(session: Session, game_id: UUID, claim_id: UUID) -> Claim:
    claim = session.get(Claim, claim_id)
    if claim is None:
        raise NotFound("claim")
    if claim.game_id != game_id:
        raise WorldConsistencyError("claim belongs to another game")
    return claim


def known_claim(
    session: Session, game_id: UUID, holder_id: UUID, claim_id: UUID
) -> Knowledge:
    actor_in(session, game_id, holder_id)
    claim_in(session, game_id, claim_id)
    knowledge = session.scalar(
        select(Knowledge).where(
            Knowledge.game_id == game_id,
            Knowledge.holder_actor_id == holder_id,
            Knowledge.claim_id == claim_id,
        )
    )
    if knowledge is None:
        raise ActionRejected("actor does not know this claim")
    return knowledge

