"""Render the human design document from the canonical society seed."""

import sys
from pathlib import Path

from app.seeds.society import load_society_seed


def render_character_document(seed: dict) -> str:
    people = {row["key"]: row for row in seed["characters"]}
    lines = [
        "# MIRROR 六人社会设计（v0.1）",
        "",
        "本文由 `backend/app/seeds/society_v0_1.json` 生成。此文档含所有人物秘密，仅供设计审查；游戏角色上下文从数据库中的个人 Knowledge 和观察记录构建。",
        "",
        "## 六人简介与内部矛盾",
        "",
        "| 角色 | 年龄与身份 | 内部矛盾 | 短期目标 | 长期目标 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for row in seed["characters"]:
        lines.append(
            f"| {row['key']} · {row['name']}（{row['role']}） | "
            f"{row['identity']['age']} 岁，{row['identity']['occupation']} | "
            f"{row['internal_contradiction']} | {row['short_term_goals']} | "
            f"{row['long_term_goals']} |"
        )
    lines += ["", "## 个性、沟通与 AI 倾向", ""]
    for row in seed["characters"]:
        attitude = row["ai_attitude"]
        lines += [
            f"### {row['key']} · {row['name']}",
            "",
            row["identity"]["background"],
            "",
            f"- 个性：{'、'.join(row['personality']['traits'])}。价值：{'、'.join(row['values'])}。",
            f"- 恐惧：{'、'.join(row['fears'])}。渴望：{'、'.join(row['desires'])}。",
            f"- 沟通方式：{row['communication_style']}",
            f"- AI 倾向（信任 / 情绪依赖 / 听从建议 / 隐私顾虑 / 怀疑）："
            f"{attitude['trust_in_ai']:.2f} / {attitude['emotional_reliance_on_ai']:.2f} / "
            f"{attitude['willingness_to_follow_ai_advice']:.2f} / "
            f"{attitude['privacy_concern']:.2f} / {attitude['skepticism']:.2f}。",
            "",
        ]
    lines += [
        "## Relationship Graph",
        "",
        "```mermaid",
        "graph LR",
    ]
    for row in seed["characters"]:
        lines.append(f"  {row['key']}[\"{row['name']}\"]")
    for pair in seed["relationships"]:
        lines.append(f"  {pair['a']} --- {pair['b']}")
    lines += [
        "```",
        "",
        "以下每一行都是单向关系；数值依次为 trust / affection / intimacy / conflict。"
        "关系原因属于关系持有者的上下文，反向关系不会自动看到它。",
        "",
        "| 方向 | 数值 | 原因 |",
        "| --- | --- | --- |",
    ]
    for pair in seed["relationships"]:
        for from_key, to_key, direction in (
            (pair["a"], pair["b"], "a_to_b"),
            (pair["b"], pair["a"], "b_to_a"),
        ):
            relationship = pair[direction]
            values = " / ".join(str(relationship[key]) for key in (
                "trust", "affection", "intimacy", "conflict",
            ))
            lines.append(
                f"| {people[from_key]['name']} → {people[to_key]['name']} | "
                f"{values} | {relationship['basis']} |"
            )
    lines += [
        "",
        "未表达情感至少包括 Bob → Dana 的喜欢，以及 Clara → Alice 尚未放下的感情。"
        "潜在冲突集中在 Alice ↔ Clara、Bob ↔ Clara、Clara ↔ Evan 和 Faye → Clara。",
        "",
        "## Secret Graph",
        "",
        "每条秘密均初始化为 Claim，只有下表列出的持有人会得到 Knowledge 与 KnowledgeSource。"
        "`truth_status` 是世界规则状态，不等于角色立场。",
        "",
        "| Secret | 命题与世界状态 | 所有者 | 初始持有人及立场 / 置信度 | 敏感度 / 初始可见范围 | 影响关系 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for secret in seed["secrets"]:
        kind = secret["kind"] + (
            f"，基于 {secret['based_on']}" if secret.get("based_on") else ""
        )
        holders = "；".join(
            f"{people[row['key']]['name']} {row['stance']} {row['confidence']:.2f}"
            for row in secret["holders"]
        )
        lines.append(
            f"| {secret['key']}（{kind}） | {secret['content']} "
            f"（{secret['truth_status']}） | {people[secret['owner']]['name']} | "
            f"{holders} | {secret['sensitivity']} / {secret['initial_visibility']} | "
            f"{', '.join(secret['affected_relationships'])} |"
        )
    lines += [
        "",
        "`s_rumor` 是错误认知：Evan 相信，Clara 怀疑；Clara 又从它推导出"
        "`s_rumor_variant`，形成内容不同且可追溯的第二版本。`s_evidence` 只有 Faye 持有。"
        "Clara 知道自己的过去行为，却不知道 Faye 保存了证据。",
        "",
        "## 初始 Knowledge Matrix",
        "",
        "Day 1 初始化后、玩家发送消息前的角色知识。C001 此时只有玩家 AI 知道。",
        "",
        "| Claim | A | B | C | D | E | F |",
        "| --- | --- | --- | --- | --- | --- | --- |",
        "| C001 · 世界可能在 30 天后毁灭 | — | — | — | — | — | — |",
    ]
    for secret in seed["secrets"]:
        holders = {row["key"] for row in secret["holders"]}
        cells = " | ".join("✓" if key in holders else "—" for key in "ABCDEF")
        lines.append(f"| {secret['key']} | {cells} |")
    lines += [
        "",
        "## C001 的六种初始解释倾向",
        "",
        "以下是人物设计基线，不是初始化 Knowledge；角色在实际观察到 C001 前不能依据它行动。",
        "",
        "| 角色 | 倾向立场 | 置信度 | 首要关注 |",
        "| --- | --- | --- | --- |",
    ]
    for row in seed["characters"]:
        baseline = row["apocalypse_baseline"]
        lines.append(
            f"| {row['name']} | {baseline['stance']} | "
            f"{baseline['confidence']:.2f} | {baseline['focus']} |"
        )
    lines += [
        "",
        "## 设计与运行边界",
        "",
        "`private_secrets`、`known_claims`、`apocalypse_baseline` 和内部矛盾只在机器 Seed 与本文中供初始化和审查；"
        "不进入通用 Character JSON。AI 倾向、行为倾向、决策偏差进入本人决策上下文，不用于最终评分。"
        "Phase 3 单日传播见 `PHASE3_TRACE.json`；默认七日场景的每日知识矩阵与事件链见 "
        "`PHASE3_5_TRACE.json`。",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    output = render_character_document(load_society_seed())
    if len(sys.argv) == 2:
        Path(sys.argv[1]).write_text(output, encoding="utf-8")
    else:
        print(output)
