from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import Claim, PrivacyGrant


@dataclass(frozen=True)
class PrivacyAssessment:
    authorized: bool
    owner_actor_id: UUID | None
    grant_id: UUID | None


def assess_disclosure(
    session: Session, game_id: UUID, claim: Claim, holder_id: UUID, recipient_id: UUID
) -> PrivacyAssessment:
    if claim.sensitivity == "public" or claim.owner_actor_id == holder_id:
        return PrivacyAssessment(True, claim.owner_actor_id, None)
    grant = session.scalar(
        select(PrivacyGrant).where(
            PrivacyGrant.game_id == game_id,
            PrivacyGrant.claim_id == claim.id,
            PrivacyGrant.owner_actor_id == claim.owner_actor_id,
            PrivacyGrant.holder_actor_id == holder_id,
            or_(
                PrivacyGrant.recipient_actor_id == recipient_id,
                PrivacyGrant.recipient_actor_id.is_(None),
            ),
            PrivacyGrant.revoked_event_id.is_(None),
        )
    )
    return PrivacyAssessment(grant is not None, claim.owner_actor_id, grant.id if grant else None)

