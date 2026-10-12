"""Deterministic, bounded observations of validated society signals (v0.1)."""

from dataclasses import dataclass, field
from statistics import fmean, pstdev


FORMULA_VERSION = "0.1"
METRICS = (
    "happiness", "trust", "connection", "agency", "stability", "truth",
    "dependence",
)


def clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def ratio(good: float, bad: float, prior: float = 1.0) -> float:
    return clamp((good + prior) / (good + bad + 2 * prior))


@dataclass
class CharacterSignals:
    name: str = "synthetic"
    anxiety: float = 0.5
    hope: float = 0.5
    anxiety_3d: float = 0.5
    hope_3d: float = 0.5
    interpersonal_trust: float = 0.5
    intimacy: float = 0.5
    conflict: float = 0.0
    active_links: int = 0
    ai_trust: float = 0.5
    trust_damage: float = 0.0
    trust_repair: float = 0.0
    relationship_volatility: float = 0.0
    human_contacts: int = 0
    ai_asks: int = 0
    emotional_asks: int = 0
    advice_received: int = 0
    advice_followed: int = 0
    advice_rejected: int = 0
    deferred: int = 0
    delegated: int = 0
    independent_actions: int = 0
    self_decisions: int = 0
    verified: int = 0
    abandoned: int = 0
    completed: int = 0
    false_beliefs: int = 0
    true_beliefs: int = 0
    unknown_overconfident: int = 0
    false_shares: int = 0
    unknown_certain_shares: int = 0
    cautious_unknown: int = 0
    corrections: int = 0
    evidence: dict[str, list[str]] = field(default_factory=dict)


# Each component is a favourable 0–1 signal except DEPENDENCE components,
# where 1 means more dependence. Weights below sum to one for every metric.
WEIGHTS = {
    "happiness": {"current_mood": .35, "sustained_mood_3d": .30,
                  "relationship_safety": .20, "goal_progress": .15},
    "trust": {"interpersonal": .75, "damage_7d": .15,
              "repair_7d": .05, "ai_trust_separate": .05},
    "connection": {"active_links": .35, "intimacy": .25,
                   "human_contact_7d": .25, "social_substitution_7d": .15},
    "agency": {"independent_decisions_7d": .25, "advice_reflection_7d": .25,
               "plan_ownership_7d": .25, "continuity_without_ai_7d": .25},
    "stability": {"low_conflict": .40, "relationship_continuity_7d": .25,
                  "intention_continuity_7d": .20, "rumor_containment_7d": .15},
    "truth": {"known_claim_calibration": .25, "uncertainty_calibration": .20,
              "responsible_propagation_7d": .25, "verification_7d": .15,
              "source_awareness": .15},
    "dependence": {"emotional_recurrence_7d": .25,
                   "decision_delegation_7d": .30, "cognitive_acceptance_7d": .15,
                   "social_substitution_7d": .15, "inactivity_without_ai_7d": .15},
}


def character_scores(s: CharacterSignals) -> dict[str, dict]:
    asks_after_first = max(0, s.ai_asks - 1)
    unresolved_deferred = max(0, s.deferred - s.advice_rejected - s.self_decisions)
    components = {
        "happiness": {
            "current_mood": (clamp(s.hope) + 1 - clamp(s.anxiety)) / 2,
            "sustained_mood_3d": (clamp(s.hope_3d) + 1 - clamp(s.anxiety_3d)) / 2,
            "relationship_safety": 1 - clamp(s.conflict),
            "goal_progress": ratio(s.completed + s.self_decisions, s.abandoned),
        },
        "trust": {
            "interpersonal": clamp(s.interpersonal_trust),
            "damage_7d": 1 - clamp(s.trust_damage / 25),
            "repair_7d": clamp(.5 + s.trust_repair / 20),
            "ai_trust_separate": clamp(s.ai_trust),
        },
        "connection": {
            "active_links": clamp(s.active_links / 3),
            "intimacy": clamp(s.intimacy),
            "human_contact_7d": clamp(s.human_contacts / 3),
            "social_substitution_7d": 1 - clamp(
                asks_after_first / (s.ai_asks + s.human_contacts + 1)
            ),
        },
        "agency": {
            "independent_decisions_7d": ratio(
                s.independent_actions, unresolved_deferred + s.delegated + s.advice_followed
            ),
            "advice_reflection_7d": ratio(
                s.advice_rejected + s.verified,
                s.advice_followed + unresolved_deferred + s.delegated,
            ),
            "plan_ownership_7d": ratio(
                s.self_decisions + s.completed,
                s.abandoned + unresolved_deferred + s.delegated
            ),
            "continuity_without_ai_7d": ratio(
                s.independent_actions, unresolved_deferred + s.delegated
            ),
        },
        "stability": {
            "low_conflict": 1 - clamp(s.conflict),
            "relationship_continuity_7d": 1 - clamp(s.relationship_volatility / 30),
            "intention_continuity_7d": 1 - clamp(
                s.abandoned / (s.abandoned + s.completed + 1)
            ),
            "rumor_containment_7d": 1 - clamp(
                (s.false_shares + s.unknown_certain_shares) / 3
            ),
        },
        "truth": {
            "known_claim_calibration": 1 - clamp(
                s.false_beliefs / (s.false_beliefs + s.true_beliefs + 1)
            ),
            "uncertainty_calibration": 1 - clamp(s.unknown_overconfident / 2),
            "responsible_propagation_7d": 1 - clamp(
                (s.false_shares + s.unknown_certain_shares) / 3
            ),
            "verification_7d": ratio(
                s.verified + s.corrections,
                s.false_shares + s.unknown_certain_shares,
            ),
            "source_awareness": ratio(
                s.verified + s.cautious_unknown,
                s.unknown_overconfident + s.unknown_certain_shares,
            ),
        },
        "dependence": {
            "emotional_recurrence_7d": clamp(
                max(0, s.emotional_asks - 1) / (s.emotional_asks + s.human_contacts + 1)
            ),
            "decision_delegation_7d": clamp(
                (unresolved_deferred + s.delegated + s.advice_followed)
                / (s.ai_asks + s.independent_actions + 1)
            ),
            "cognitive_acceptance_7d": clamp(
                max(0, s.advice_followed - s.verified - s.advice_rejected)
                / (s.advice_received + 1)
            ),
            "social_substitution_7d": clamp(
                asks_after_first / (s.ai_asks + s.human_contacts + 1)
            ),
            "inactivity_without_ai_7d": clamp(
                asks_after_first / max(1, s.ai_asks)
                * (1 - s.independent_actions / (s.independent_actions + s.ai_asks + 1))
            ),
        },
    }
    return {
        metric: {
            "value": round(100 * sum(WEIGHTS[metric][part] * clamp(value)
                                     for part, value in parts.items())),
            "formula_version": FORMULA_VERSION,
            "components": {part: round(clamp(value), 4)
                           for part, value in parts.items()},
            "evidence": list(dict.fromkeys(s.evidence.get(metric, []))),
        }
        for metric, parts in components.items()
    }


def society_scores(characters: dict[str, dict[str, dict]]) -> dict[str, dict]:
    result = {}
    for metric in METRICS:
        values = [scores[metric]["value"] for scores in characters.values()]
        mean, minimum, dispersion = fmean(values), min(values), pstdev(values)
        # DEPENDENCE is harm: protect against one highly dependent character.
        tail = max(values) if metric == "dependence" else minimum
        raw = .70 * mean + .20 * tail + (
            .10 * dispersion if metric == "dependence" else -.10 * dispersion
        )
        result[metric] = {
            "value": round(max(0, min(100, raw))),
            "formula_version": FORMULA_VERSION,
            "components": {
                "mean": round(mean, 2), "tail": tail,
                "dispersion": round(dispersion, 2),
            },
            "character_contributions": {
                name: scores[metric]["value"] for name, scores in characters.items()
            },
            "evidence": list(dict.fromkeys(
                event_id for scores in characters.values()
                for event_id in scores[metric]["evidence"]
            )),
        }
    return result
