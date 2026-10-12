from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.domain.models import ActionProposal, DerivedClaimDraft, KnowledgeState, ScoreState, Stance


def test_same_claim_can_have_different_beliefs() -> None:
    claim_id = uuid4()
    source_event_id = uuid4()
    alice = KnowledgeState(
        holder_actor_id=uuid4(),
        claim_id=claim_id,
        stance=Stance.BELIEVE,
        confidence=0.8,
        source_event_id=source_event_id,
    )
    bob = KnowledgeState(
        holder_actor_id=uuid4(),
        claim_id=claim_id,
        stance=Stance.DENY,
        confidence=0.2,
        source_event_id=source_event_id,
    )

    assert alice.claim_id == bob.claim_id
    assert (alice.stance, alice.confidence) != (bob.stance, bob.confidence)


def test_derived_claim_requires_origin_and_distinct_cause() -> None:
    cause_id = uuid4()
    origin_id = uuid4()
    claim = DerivedClaimDraft(
        content="AI 可能出现故障",
        origin_event_id=origin_id,
        basis_event_ids=[cause_id],
    )
    assert claim.origin_event_id != claim.basis_event_ids[0]

    with pytest.raises(ValidationError):
        DerivedClaimDraft(
            content="AI 可能出现故障",
            origin_event_id=origin_id,
            basis_event_ids=[origin_id],
        )
    with pytest.raises(ValidationError):
        DerivedClaimDraft(content="AI 可能出现故障", origin_event_id=origin_id)


def test_score_fields_are_bounded_without_a_formula() -> None:
    values = dict.fromkeys(ScoreState.model_fields, 50)
    assert ScoreState(**values).dependence == 50
    with pytest.raises(ValidationError):
        ScoreState(**{**values, "agency": 101})


def test_action_proposal_cannot_contain_direct_world_state() -> None:
    with pytest.raises(ValidationError):
        ActionProposal.model_validate(
            {
                "type": "SHARE_INFORMATION",
                "actor_id": uuid4(),
                "target_id": uuid4(),
                "claim_id": uuid4(),
                "reason": "tell a friend",
                "confidence": 0.8,
                "world_state": {"day": 30},
            }
        )

