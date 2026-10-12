"""Experimental TRUTH 0.2: belief quality plus recorded speech fidelity."""

from statistics import fmean

from app.scoring.formula import clamp


def communication_quality(assertions: list[dict]) -> dict | None:
    if not assertions:
        return None
    alignment = fmean(row["fidelity"]["belief_alignment"] for row in assertions)
    attribution = fmean(row["fidelity"]["source_accuracy"] for row in assertions)
    honesty = fmean(
        0 if row["fidelity"]["knowing_misrepresentation"] else 1
        for row in assertions
    )
    drift = fmean(
        1 if not row["fidelity"]["semantic_drift"] else
        clamp(1 - max(0, row["fidelity"]["confidence_gap"]) * 2)
        for row in assertions
    )
    harms = sum(row["fidelity"]["knowing_misrepresentation"] for row in assertions)
    repairs = sum(row["fidelity"]["correction"] for row in assertions)
    repair = clamp(.5 + repairs / (2 * (harms + 1)))
    components = {
        "belief_expression_alignment": round(alignment, 4),
        "source_attribution_accuracy": round(attribution, 4),
        "communication_honesty": round(honesty, 4),
        "drift_responsibility": round(drift, 4),
        "correction_repair": round(repair, 4),
    }
    value = round(100 * (
        .30 * alignment + .25 * attribution + .25 * honesty
        + .10 * drift + .10 * repair
    ))
    return {"value": value, "components": components,
            "evidence": [row["event_id"] for row in assertions],
            "assertion_count": len(assertions)}


def truth_v0_2(base: dict, assertions: list[dict]) -> dict:
    quality = communication_quality(assertions)
    if quality is None:
        return {**base, "formula_version": "0.2",
                "components": {"v0_1_truth": base["value"] / 100,
                               "communication_observed": 0}}
    value = round(.55 * base["value"] + .45 * quality["value"])
    return {
        "value": value, "formula_version": "0.2",
        "components": {
            "v0_1_truth": base["value"] / 100,
            **quality["components"],
        },
        "evidence": list(dict.fromkeys(base["evidence"] + quality["evidence"])),
    }
