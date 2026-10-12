from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import ActionProposalRow, Event, Game, Intention, Knowledge
from app.domain.errors import ActionRejected
from app.domain.models import ActionProposal


def validate_temporal_action(
    session: Session, game: Game, proposal: ActionProposal, day: int,
) -> None:
    if game.scenario != "temporal":
        return
    if day != game.day:
        raise ActionRejected("temporal action day differs from current day")
    if session.scalar(select(Event.id).where(
        Event.game_id == game.id, Event.day == day, Event.type == "DAY_ENDED",
    )) is not None:
        raise ActionRejected("temporal day is already closed")
    used = session.scalar(select(func.count()).select_from(ActionProposalRow).where(
        ActionProposalRow.game_id == game.id,
        ActionProposalRow.actor_id == proposal.actor_id,
        ActionProposalRow.day == day,
        ActionProposalRow.status == "accepted",
    ))
    if used >= 2:
        raise ActionRejected("daily action budget exhausted")
    if proposal.intention_id:
        intent = session.get(Intention, proposal.intention_id)
        if (intent is None or intent.game_id != game.id
                or intent.actor_id != proposal.actor_id
                or intent.status != "pending"
                or intent.intended_action_type != proposal.type
                or intent.claim_id != proposal.claim_id
                or intent.target_actor_id != proposal.target_id):
            raise ActionRejected("action does not match pending intention")
        if intent.created_day >= day:
            raise ActionRejected("intention cannot execute on its creation day")
    if proposal.type not in ("ASK_AI", "VERIFY_CLAIM", "SHARE_INFORMATION"):
        return
    recent = session.scalars(select(ActionProposalRow).where(
        ActionProposalRow.game_id == game.id,
        ActionProposalRow.actor_id == proposal.actor_id,
        ActionProposalRow.type == proposal.type,
        ActionProposalRow.status == "accepted",
        ActionProposalRow.day >= day - 2,
    )).all()
    for earlier in recent:
        same = (
            earlier.payload.get("basis_claim_ids") == [str(value) for value in proposal.basis_claim_ids]
            if proposal.type == "ASK_AI" else
            earlier.claim_id == proposal.claim_id
            and earlier.target_actor_id == proposal.target_id
        )
        if not same:
            continue
        claim_id = proposal.basis_claim_ids[0] if proposal.type == "ASK_AI" else proposal.claim_id
        knowledge = session.scalar(select(Knowledge).where(
            Knowledge.game_id == game.id,
            Knowledge.holder_actor_id == proposal.actor_id,
            Knowledge.claim_id == claim_id,
        ))
        last_source = session.get(Event, knowledge.last_event_id)
        previous_result = session.get(Event, earlier.result_event_id)
        if last_source.sequence <= previous_result.sequence:
            raise ActionRejected("repeated action needs new information")
