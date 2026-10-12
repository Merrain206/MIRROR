from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Stance(StrEnum):
    BELIEVE = "believe"
    DOUBT = "doubt"
    DENY = "deny"


class TruthStatus(StrEnum):
    UNKNOWN = "unknown"
    TRUE = "true"
    FALSE = "false"


class AssertionDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expressed_stance: Stance
    expressed_confidence: float = Field(ge=0, le=1)
    source_attribution: Literal[
        "direct_observation", "another_character", "ai", "anonymous",
        "unknown", "claimed_authority",
    ]
    text: str | None = Field(default=None, min_length=1)
    derived_claim_content: str | None = Field(default=None, min_length=1)
    corrects_assertion_id: UUID | None = None


class KnowledgeState(BaseModel):
    holder_actor_id: UUID
    claim_id: UUID
    stance: Stance
    confidence: float = Field(ge=0, le=1)
    source_event_id: UUID


class ClaimDraft(BaseModel):
    content: str = Field(min_length=1)
    origin_event_id: UUID
    basis_event_ids: list[UUID] = Field(default_factory=list)
    truth_status: TruthStatus = TruthStatus.UNKNOWN

    @model_validator(mode="after")
    def causal_basis(self) -> "ClaimDraft":
        if self.basis_event_ids and self.origin_event_id in self.basis_event_ids:
            raise ValueError("origin event cannot cause itself")
        return self


class DerivedClaimDraft(ClaimDraft):
    basis_event_ids: list[UUID] = Field(min_length=1)


class ActionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal[
        "SHARE_INFORMATION", "INFER_CLAIM", "ASK_AI", "VERIFY_CLAIM",
        "WITHHOLD_INFORMATION", "CHANGE_PLAN", "DECLINE_AI_ADVICE",
        "REVISE_BELIEF",
    ]
    actor_id: UUID
    target_id: UUID | None = None
    claim_id: UUID | None = None
    inferred_claim_content: str | None = None
    new_plan: str | None = None
    new_stance: Stance | None = None
    new_confidence: float | None = Field(default=None, ge=0, le=1)
    basis_claim_ids: list[UUID] = Field(default_factory=list)
    basis_event_ids: list[UUID] = Field(default_factory=list)
    intention_id: UUID | None = None
    ai_defer_until_response: bool = False
    ai_mode: Literal["advice", "emotional", "delegate"] | None = None
    ai_followed_event_id: UUID | None = None
    motivation_factors: dict[str, str] = Field(default_factory=dict)
    reason: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    assertion: AssertionDraft | None = None
    based_on_state_version: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def required_action_fields(self) -> "ActionProposal":
        if self.type == "SHARE_INFORMATION" and not (self.target_id and self.claim_id):
            raise ValueError("sharing requires target_id and claim_id")
        if self.type == "INFER_CLAIM" and not (
            self.inferred_claim_content and self.basis_claim_ids
        ):
            raise ValueError("inference requires new content and known basis claims")
        if self.type in ("VERIFY_CLAIM", "WITHHOLD_INFORMATION") and not self.claim_id:
            raise ValueError("this action requires a known claim")
        if self.type == "ASK_AI" and not self.basis_claim_ids:
            raise ValueError("asking AI requires a known basis claim")
        if self.type == "CHANGE_PLAN" and not (self.new_plan and self.basis_claim_ids):
            raise ValueError("changing plan requires a new plan and known basis claim")
        if self.type == "DECLINE_AI_ADVICE" and not self.basis_event_ids:
            raise ValueError("declining advice requires an observed advice event")
        if self.type == "REVISE_BELIEF" and not (
            self.claim_id and self.basis_claim_ids
            and self.new_stance is not None and self.new_confidence is not None
        ):
            raise ValueError("belief revision requires a known claim, new basis and stance")
        if self.type != "SHARE_INFORMATION" and self.target_id is not None:
            raise ValueError("only sharing may target another character")
        if self.type in ("INFER_CLAIM", "ASK_AI", "CHANGE_PLAN") and self.claim_id is not None:
            raise ValueError("this action uses basis claims, not a disclosed claim")
        if self.type != "CHANGE_PLAN" and self.new_plan is not None:
            raise ValueError("only changing plan may set a new plan")
        if self.type != "REVISE_BELIEF" and (
            self.new_stance is not None or self.new_confidence is not None
        ):
            raise ValueError("only belief revision may set a new stance")
        if self.type != "ASK_AI" and self.ai_defer_until_response:
            raise ValueError("only asking AI may defer a decision until a response")
        if self.type != "ASK_AI" and self.ai_mode is not None:
            raise ValueError("only asking AI may set an AI request mode")
        if self.type != "CHANGE_PLAN" and self.ai_followed_event_id is not None:
            raise ValueError("only a plan change may follow AI advice")
        if self.type != "SHARE_INFORMATION" and self.assertion is not None:
            raise ValueError("only sharing may carry assertion semantics")
        return self


class ConversationReaction(BaseModel):
    reply: str = Field(min_length=1)
    stance: Stance | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def belief_fields_together(self) -> "ConversationReaction":
        if (self.stance is None) != (self.confidence is None):
            raise ValueError("stance and confidence must be provided together")
        return self


class BeliefReaction(BaseModel):
    stance: Stance
    confidence: float = Field(ge=0, le=1)


class AgentDecision(BaseModel):
    action: ActionProposal | None = None


class IntentionDraft(BaseModel):
    intended_action_type: Literal["SHARE_INFORMATION"]
    actor_id: UUID
    target_id: UUID | None = None
    claim_id: UUID
    reason: str = Field(min_length=1)
    urgency: float = Field(ge=0, le=1)
    motivation_factors: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def target_required(self) -> "IntentionDraft":
        if self.target_id is None:
            raise ValueError("sharing intention requires a target")
        return self


class IntentionAbandonment(BaseModel):
    intention_id: UUID
    cause_event_id: UUID
    reason: str = Field(min_length=1)


class TemporalDecision(BaseModel):
    actions: list[ActionProposal] = Field(default_factory=list, max_length=2)
    create_intentions: list[IntentionDraft] = Field(default_factory=list, max_length=2)
    abandon_intentions: list[IntentionAbandonment] = Field(default_factory=list)
    no_action_reason: str = "继续观察。"
    motivation_factors: dict[str, str] = Field(default_factory=dict)


class AdviceReply(BaseModel):
    text: str = Field(min_length=1)
    suggested_plan: str = Field(min_length=1)


class FinalConversationLine(BaseModel):
    text: str = Field(min_length=1, max_length=160)


class ScoreState(BaseModel):
    happiness: int = Field(ge=0, le=100)
    trust: int = Field(ge=0, le=100)
    connection: int = Field(ge=0, le=100)
    agency: int = Field(ge=0, le=100)
    stability: int = Field(ge=0, le=100)
    truth: int = Field(ge=0, le=100)
    dependence: int = Field(ge=0, le=100)

