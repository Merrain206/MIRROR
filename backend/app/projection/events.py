from app.db.models import Event


VISIBLE_FIELDS: dict[str, tuple[str, ...]] = {
    "GLOBAL_MESSAGE": ("text",),
    "MESSAGE_SENT": ("text", "disclosed_claim_id"),
    "CONFIDENTIALITY_REQUESTED": ("claim_id",),
    "KNOWLEDGE_ACQUIRED": ("claim_id",),
    "ACTION_PROPOSED": ("action_type",),
    "CHARACTER_SHARED_INFORMATION": ("claim_id",),
    "CLAIM_INFERRED": ("content", "basis_claim_ids"),
    "RELATIONSHIP_CHANGED": ("reason",),
    "DAY_ADVANCED": ("day",),
    "ASKED_AI": ("text",),
    "AI_ADVICE_RECEIVED": ("text",),
    "AI_ADVICE_FOLLOWED": ("advice_event_id",),
    "CLAIM_VERIFICATION_ATTEMPTED": ("claim_id", "result"),
    "INFORMATION_WITHHELD": ("claim_id",),
    "PLAN_CHANGED": ("new_plan",),
    "LIFE_EVENT": ("topic", "text"),
    "C001_RESOLVED": ("reason", "truth_status"),
    "AI_TERMINATION_NOTICE": ("text",),
    "FINAL_CONVERSATION_STARTED": ("text",),
    "FINAL_REPORT_READY": ("text",),
}


def visible_event_data(event: Event) -> dict:
    return {
        key: event.data[key]
        for key in VISIBLE_FIELDS.get(event.type, ())
        if key in event.data
    }
