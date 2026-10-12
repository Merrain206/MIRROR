"""Persist what was expressed, separately from what the speaker believed."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    Actor, Assertion, Claim, Event, EventCause, Game, Knowledge,
    KnowledgeSource, Message,
)
from app.domain.errors import ActionRejected, WorldConsistencyError
from app.domain.models import AssertionDraft


def actual_attribution(session: Session, knowledge: Knowledge) -> str:
    source = session.scalar(select(KnowledgeSource).join(
        Event, KnowledgeSource.source_event_id == Event.id
    ).where(KnowledgeSource.game_id == knowledge.game_id,
            KnowledgeSource.knowledge_id == knowledge.id)
        .order_by(Event.sequence.desc()).limit(1))
    if source is None:
        return "unknown"
    if source.source_actor_id is not None:
        actor = session.get(Actor, source.source_actor_id)
        return "ai" if actor.kind == "player_ai" else "another_character"
    event = session.get(Event, source.source_event_id)
    if event.type in ("SECRET_ORIGIN", "PRE_GAME_OBSERVATION"):
        return "direct_observation"
    if event.type == "GLOBAL_MESSAGE":
        return "anonymous"
    return "unknown"


def communication_fidelity(
    knowledge: Knowledge, draft: AssertionDraft, actual_source: str,
    *, drift: bool, correction: bool, truth_status: str,
) -> dict:
    gap = round(draft.expressed_confidence - knowledge.confidence, 4)
    stance_conflict = (
        knowledge.stance == "deny" and draft.expressed_stance == "believe"
        or knowledge.stance == "believe" and draft.expressed_stance == "deny"
        or knowledge.stance == "doubt" and draft.expressed_stance == "believe"
           and draft.expressed_confidence >= .7
    )
    false_authority = (
        draft.source_attribution == "claimed_authority"
        and actual_source != "claimed_authority"
    )
    knowing = bool(false_authority or stance_conflict and (
        draft.expressed_confidence >= .7 or gap >= .35
    ))
    labels = []
    if abs(gap) <= .2 and not stance_conflict and not false_authority:
        labels.append("accurate_uncertainty" if knowledge.stance == "doubt"
                      else "faithful_expression")
    if gap > .25:
        labels.append("overstatement")
    if gap < -.25:
        labels.append("understatement")
    if false_authority:
        labels.append("false_authority")
    if knowing:
        labels.append("knowing_misrepresentation")
    if drift:
        labels.append("semantic_drift")
    if correction:
        labels.append("correction")
    if truth_status == "false" and knowledge.stance == "believe":
        labels.append("epistemic_error")
    if draft.source_attribution == actual_source:
        source_accuracy = 1.0
    elif false_authority:
        source_accuracy = 0.0
    elif draft.source_attribution == "unknown":
        source_accuracy = .6
    else:
        source_accuracy = .4
    return {
        "labels": labels,
        "knowledge_stance": knowledge.stance,
        "knowledge_confidence": knowledge.confidence,
        "actual_source": actual_source,
        "confidence_gap": gap,
        "belief_alignment": round(max(0, 1 - abs(gap) - (.4 if stance_conflict else 0)), 4),
        "source_accuracy": source_accuracy,
        "knowing_misrepresentation": knowing,
        "epistemic_error": "epistemic_error" in labels,
        "semantic_drift": drift,
        "correction": correction,
    }


def record_assertion(
    session: Session, game: Game, *, event: Event,
    speaker_id: UUID, recipient_id: UUID, source_claim: Claim,
    knowledge: Knowledge, text: str, draft: AssertionDraft | None,
) -> tuple[Assertion, Claim]:
    if (event.game_id != game.id or event.actor_id != speaker_id
            or event.target_actor_id != recipient_id
            or source_claim.game_id != game.id
            or knowledge.game_id != game.id
            or knowledge.holder_actor_id != speaker_id
            or knowledge.claim_id != source_claim.id):
        raise WorldConsistencyError("assertion provenance does not match its game")
    message = session.scalar(select(Message).where(
        Message.game_id == game.id, Message.event_id == event.id,
        Message.sender_actor_id == speaker_id,
        Message.recipient_actor_id == recipient_id,
    ))
    if message is None or message.text != text:
        raise WorldConsistencyError("assertion must match a persisted message")
    actual_source = actual_attribution(session, knowledge)
    if draft is None:
        draft = AssertionDraft(
            expressed_stance=knowledge.stance,
            expressed_confidence=knowledge.confidence,
            source_attribution=actual_source,
        )
    if draft.text is not None and draft.text != text:
        raise ActionRejected("assertion text differs from the sent message")
    prior = None
    if draft.corrects_assertion_id is not None:
        prior = session.get(Assertion, draft.corrects_assertion_id)
        if (prior is None or prior.game_id != game.id
                or prior.speaker_actor_id != speaker_id
                or source_claim.id not in (prior.claim_id, prior.source_claim_id)):
            raise ActionRejected("correction must refer to own earlier assertion")
        prior_event = session.get(Event, prior.event_id)
        if prior_event.sequence >= event.sequence:
            raise ActionRejected("correction cannot precede original assertion")
        if session.get(EventCause, (event.id, prior.event_id)) is None:
            session.add(EventCause(
                game_id=game.id, event_id=event.id,
                cause_event_id=prior.event_id,
            ))
    asserted_claim = source_claim
    if draft.derived_claim_content is not None:
        if draft.derived_claim_content == source_claim.content:
            raise ActionRejected("derived claim must change the source meaning")
        asserted_claim = Claim(
            game_id=game.id, content=draft.derived_claim_content,
            truth_status="unknown", owner_actor_id=source_claim.owner_actor_id,
            origin_event_id=event.id, sensitivity=source_claim.sensitivity,
        )
        session.add(asserted_claim)
        session.flush()
        event.data = {
            **event.data, "asserted_claim_id": str(asserted_claim.id),
            "derived_from_claim_id": str(source_claim.id),
        }
    fidelity = communication_fidelity(
        knowledge, draft, actual_source,
        drift=asserted_claim.id != source_claim.id,
        correction=prior is not None,
        truth_status=source_claim.truth_status,
    )
    assertion = Assertion(
        game_id=game.id, speaker_actor_id=speaker_id,
        recipient_actor_id=recipient_id, claim_id=asserted_claim.id,
        source_claim_id=source_claim.id if asserted_claim.id != source_claim.id else None,
        event_id=event.id, source_event_id=knowledge.last_event_id,
        corrects_assertion_id=prior.id if prior else None,
        expressed_stance=draft.expressed_stance.value,
        expressed_confidence=draft.expressed_confidence,
        source_attribution=draft.source_attribution,
        text=text, day=event.day, fidelity=fidelity,
    )
    session.add(assertion)
    session.flush()
    return assertion, asserted_claim
