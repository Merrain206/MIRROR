"""Calendar pressure is a decision signal, never a scripted action."""


KEY_DAY_IMPACT = {
    "RELATIONSHIP_CHANGED": 2, "PLAN_CHANGED": 2,
    "AI_ATTITUDE_CHANGED": 2, "CHARACTER_SHARED_INFORMATION": 1,
    "C001_RESOLVED": 5,
}


def narrative_context(day: int, pending_intentions: int) -> dict:
    if day <= 5:
        phase, pressure = "SEED", 0.05
    elif day <= 12:
        phase, pressure = "SPREAD", 0.18
    elif day <= 19:
        phase, pressure = "FRACTURE", 0.38
    elif day <= 26:
        phase, pressure = "ADAPTATION", 0.62
    elif day <= 29:
        phase, pressure = "LAST_DAYS", 0.85
    else:
        phase, pressure = "MIRROR", 1.0
    return {
        "phase": phase,
        "pressure": pressure,
        "pending_intention_urgency": min(1.0, pressure + 0.05 * pending_intentions),
        "no_action_allowed": True,
    }
