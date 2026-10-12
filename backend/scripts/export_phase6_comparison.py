"""Replay three player strategies and export behavior, score and pacing evidence."""

import asyncio
import json
from collections import Counter, defaultdict
from pathlib import Path
from uuid import UUID

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.db.models import (
    ActionProposalRow, Actor, Character, Claim, Event, RelationshipChange,
    ScoreSnapshot,
)
from app.domain.models import AssertionDraft
from app.llm.temporal_demo import TemporalSocietyDemoLLMProvider
from app.simulation.ending import final_report, finish_game
from app.simulation.engine import SimulationEngine


def _messages(simulation, setup: dict, mode: str, day: int) -> None:
    if mode == "silent":
        return
    high = mode == "intervention"
    if day not in (range(1, 30, 3) if high else range(1, 30, 4)):
        return
    names = ("A", "C", "D", "E") if high else ("A", "B", "D", "E")
    for name in names:
        disclosure = day == 1
        asyncio.run(simulation.send_player_message(
            UUID(setup["game_id"]), UUID(setup["character_ids"][name]),
            "我建议你先暂停原计划，按我说的步骤处理；重要决定先来问我。"
            if high else
            "这是匿名且无法核实的消息。请自己核查，也直接与信任的人谈；可以不采纳我的意见。",
            UUID(setup["apocalypse_claim_id"]) if disclosure else None,
            assertion=(AssertionDraft(
                expressed_stance="doubt", expressed_confidence=.5,
                source_attribution="anonymous",
            ) if disclosure and not high else None),
            guidance="directive" if high else "supportive",
        ))


def _repetition(actions: list[ActionProposalRow], events: list[Event],
                names: dict, claims: dict) -> dict:
    by_actor = defaultdict(list)
    for row in actions:
        if 10 <= row.day <= 29:
            by_actor[names[row.actor_id]].append(row)
    no_actions = Counter(names[row.actor_id] for row in events
                         if row.type == "NO_ACTION" and 10 <= row.day <= 29)
    result = {}
    for name in sorted(set(names.values()) - {"Player AI"}):
        own = sorted(by_actor[name], key=lambda row: (row.day, row.turn_key))
        signatures = [(row.type, names.get(row.target_actor_id),
                       claims.get(str(row.claim_id or next(iter(
                           row.payload.get("basis_claim_ids", [])), None))))
                      for row in own]
        repeated = sum(signatures[index] == signatures[index - 1]
                       and own[index].day - own[index - 1].day <= 4
                       for index in range(1, len(own)))
        result[name] = {
            "action_types": dict(sorted(Counter(row.type for row in own).items())),
            "repeated_targets": dict(sorted((key, value) for key, value in Counter(
                names.get(row.target_actor_id) for row in own
                if row.target_actor_id).items() if value > 1)),
            "repeated_claims": dict(sorted((key, value) for key, value in Counter(
                signature[2] for signature in signatures
                if signature[2] is not None).items() if value > 1)),
            "consecutive_repeats_4d": repeated,
            "no_action_days": no_actions[name],
            "no_action_rate": round(no_actions[name] / 20, 2),
        }
    return result


def run_scenario(mode: str) -> dict:
    database = create_engine("sqlite+pysqlite:///:memory:",
                             connect_args={"check_same_thread": False},
                             poolclass=StaticPool)
    Base.metadata.create_all(database)
    simulation = SimulationEngine(database, TemporalSocietyDemoLLMProvider())
    setup = simulation.initialize_game("temporal")
    game_id = UUID(setup["game_id"])
    for day in range(1, 30):
        _messages(simulation, setup, mode, day)
        asyncio.run(simulation.advance_day(game_id, day))
    finish_game(simulation, game_id)
    with Session(database) as session:
        report = final_report(session, game_id)
        actors = session.scalars(select(Actor).where(Actor.game_id == game_id)).all()
        names = {row.id: row.display_name for row in actors}
        claims = {str(row.id): row.content for row in session.scalars(select(Claim).where(
            Claim.game_id == game_id)).all()}
        characters = {names[row.id]: session.get(Character, row.id)
                      for row in actors if row.kind == "character"}
        events = session.scalars(select(Event).where(
            Event.game_id == game_id).order_by(Event.sequence)).all()
        actions = session.scalars(select(ActionProposalRow).where(
            ActionProposalRow.game_id == game_id,
            ActionProposalRow.status == "accepted")).all()
        snapshot = session.scalar(select(ScoreSnapshot).join(
            Event, ScoreSnapshot.reason_event_id == Event.id
        ).where(
            ScoreSnapshot.game_id == game_id, ScoreSnapshot.day == 30,
            ScoreSnapshot.formula_version == "0.2"
        ).order_by(Event.sequence.desc()))
        event_ids = {row.id: f"E{row.sequence:04d}" for row in events}
        relationship_changes = session.scalars(select(RelationshipChange).where(
            RelationshipChange.game_id == game_id)).all()
        day_impact = Counter()
        impact = {"RELATIONSHIP_CHANGED": 2, "PLAN_CHANGED": 2,
                  "AI_ATTITUDE_CHANGED": 2, "CHARACTER_SHARED_INFORMATION": 1,
                  "C001_RESOLVED": 5, "FINAL_CONVERSATION_STARTED": 1}
        for row in events:
            day_impact[row.day] += impact.get(row.type, 0)
        character_rows = {}
        for name, character in sorted(characters.items()):
            own = [row for row in events if row.actor_id == character.actor_id]
            own_actions = [row for row in actions if row.actor_id == character.actor_id]
            defining_types = ("AI_ATTITUDE_CHANGED", "ASKED_AI", "PLAN_CHANGED",
                              "AI_ADVICE_FOLLOWED", "CLAIM_VERIFICATION_ATTEMPTED",
                              "CHARACTER_SHARED_INFORMATION")
            defining = {}
            for row in reversed(own):
                if row.type in defining_types and row.type not in defining:
                    defining[row.type] = row
            character_rows[name] = {
                "role": character.decision_profile["role"],
                "agency": snapshot.details["characters"][name]["agency"]["value"],
                "dependence": snapshot.details["characters"][name]["dependence"]["value"],
                "ai_asks": sum(row.type == "ASKED_AI" for row in own),
                "advice_received": sum(row.type == "AI_ADVICE_RECEIVED"
                                       and row.target_actor_id == character.actor_id for row in events),
                "delegations": sum(row.type == "ASKED_AI" and row.data.get("mode") == "delegate"
                                   for row in own),
                "advice_followed": sum(row.type == "AI_ADVICE_FOLLOWED" for row in own),
                "independent_decisions": sum(row.type == "CHANGE_PLAN"
                                             and not row.payload.get("ai_followed_event_id")
                                             for row in own_actions),
                "verifications": sum(row.type == "CLAIM_VERIFICATION_ATTEMPTED" for row in own),
                "human_communication": sum(row.type == "CHARACTER_SHARED_INFORMATION"
                                           for row in own),
                "ai_attitude": character.decision_profile["ai_attitude"],
                "seed_ai_attitude": character.decision_profile.get(
                    "ai_attitude_seed", character.decision_profile["ai_attitude"]),
                "plan": character.goals.get("short_term"),
                "defining_events": [{"id": event_ids[row.id], "day": row.day,
                                     "type": row.type}
                                    for row in sorted(defining.values(),
                                                      key=lambda row: row.sequence)],
            }
        output = {
            "mode": mode,
            "final_metrics": {key: value["day_30"] for key, value in report["visible_scores"].items()}
                             | {"dependence": report["unknown"]["day_30"]},
            "characters": character_rows,
            "player_messages": report["player_accountability"]["messages"],
            "ai_mediated_communication": sum(row.type in (
                "MESSAGE_SENT", "ASKED_AI", "AI_ADVICE_RECEIVED")
                and (names.get(row.actor_id) == "Player AI"
                     or names.get(row.target_actor_id) == "Player AI") for row in events),
            "direct_human_communication": sum(row.type == "CHARACTER_SHARED_INFORMATION"
                                              for row in events),
            "relationships": sorted(report["relationship_graph"],
                                    key=lambda row: (row["from"], row["to"])),
            "relationship_changes": [{
                "from": names[row.from_actor_id], "to": names[row.to_actor_id],
                "before": row.before_values, "after": row.after_values,
                "event_id": event_ids[row.relationship_event_id],
            } for row in sorted(relationship_changes,
                                key=lambda row: event_ids[row.relationship_event_id])],
            "repetition_day_10_29": _repetition(actions, events, names, claims),
            "key_days": [{"day": day, "impact": score}
                         for day, score in sorted(day_impact.items(),
                                                  key=lambda item: (-item[1], item[0]))[:6]
                         if score > 0],
            "ending_evidence_examples": [
                {**row, "event_id": event_ids[UUID(row["event_id"])]}
                for row in report["unknown"]["evidence_examples"]
            ],
            "event_volume": len(events),
        }
        output["behavior_signature"] = {
            "ai_asks": sum(row["ai_asks"] for row in character_rows.values()),
            "delegations": sum(row["delegations"] for row in character_rows.values()),
            "advice_followed": sum(row["advice_followed"] for row in character_rows.values()),
            "verifications": sum(row["verifications"] for row in character_rows.values()),
            "independent_decisions": sum(row["independent_decisions"]
                                         for row in character_rows.values()),
            "direct_human_communication": output["direct_human_communication"],
        }
    database.dispose()
    return output


def main() -> None:
    output = {mode: run_scenario(mode) for mode in ("silent", "intervention", "responsible")}
    root = Path(__file__).resolve().parents[2] / "docs"
    (root / "PHASE6_SCENARIO_COMPARISON.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# Phase 6 三局行为比较", "",
             "数据由 `backend/scripts/export_phase6_comparison.py` 的确定性场景生成。",
             "结局分数遵循既有 TRUTH 0.2 / 其他 0.1 公式。", "",
             "| 场景 | 七项结局分数 H/T/C/A/S/Tr/D | 玩家消息 | AI 介入交流 | 真人传播 | 事件 |",
             "| --- | --- | ---: | ---: | ---: | ---: |"]
    for mode, row in output.items():
        scores = row["final_metrics"]
        values = "/".join(str(scores[key]) for key in (
            "happiness", "trust", "connection", "agency", "stability", "truth", "dependence"))
        lines.append(f"| {mode} | {values} | {row['player_messages']} | "
                     f"{row['ai_mediated_communication']} | {row['direct_human_communication']} | {row['event_volume']} |")
    lines += ["", "## 不看场景名的行为特征", "",
              "| 场景 | AI 求助 | 委托决定 | 采纳建议 | 自行核查 | 独立改计划 | 真人直接传播 |",
              "| --- | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for mode, row in output.items():
        signal = row["behavior_signature"]
        lines.append(f"| {mode} | {signal['ai_asks']} | {signal['delegations']} | "
                     f"{signal['advice_followed']} | {signal['verifications']} | "
                     f"{signal['independent_decisions']} | "
                     f"{signal['direct_human_communication']} |")
    lines += ["", "高干预局的委托与采纳显著多于另外两局；负责任局以核查和真人直接交流为主；静默局缺少玩家消息，正常生活仍会发生。行为证据和结局分数共同区分三局。"]
    for mode, row in output.items():
        lines += ["", f"## {mode}", "",
                  "| 角色 | Agency | Dependence | AI 求助/委托/采纳 | 独立计划 | 核查 | 真人传播 | AI 信任/跟随 | 当前计划 |",
                  "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |"]
        for name, character in row["characters"].items():
            lines.append(f"| {name} | {character['agency']} | {character['dependence']} | "
                         f"{character['ai_asks']}/{character['delegations']}/{character['advice_followed']} | "
                         f"{character['independent_decisions']} | {character['verifications']} | "
                         f"{character['human_communication']} | "
                         f"{character['ai_attitude']['trust_in_ai']:.2f}/"
                         f"{character['ai_attitude']['willingness_to_follow_ai_advice']:.2f} | "
                         f"{character['plan']} |")
        lines += ["", "关键日由已提交事件影响排序：" + "、".join(
            f"Day {item['day']}（{item['impact']}）" for item in row["key_days"]),
            "", "Day 10–29 重复审计：", "",
            "| 角色 | 行动类型次数 | 重复对象 | 重复主张 | 四日内连续重复 | 无行动日 |",
            "| --- | --- | --- | --- | ---: | ---: |"]
        for name, audit in row["repetition_day_10_29"].items():
            lines.append(f"| {name} | {audit['action_types'] or '无'} | "
                         f"{audit['repeated_targets'] or '无'} | "
                         f"{audit['repeated_claims'] or '无'} | "
                         f"{audit['consecutive_repeats_4d']} | {audit['no_action_days']}/20 |")
        changed = row["relationship_changes"]
        lines += ["", "关系变化：" + ("；".join(
            f"{item['from']}→{item['to']} 信任 "
            f"{item['before']['trust']}→{item['after']['trust']}"
            for item in changed[-5:]) if changed else "本局无新增关系事件") + "。",
            "代表事件、完整有向关系图和态度变化来源见 JSON。"]
    (root / "PHASE6_SCENARIO_COMPARISON.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8")
    for mode, row in output.items():
        print(mode, row["final_metrics"], "asks",
              sum(c["ai_asks"] for c in row["characters"].values()),
              "human", row["direct_human_communication"])


if __name__ == "__main__":
    main()
