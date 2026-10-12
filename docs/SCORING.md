# MIRROR Phase 4 评分说明

公式 `0.1` 是冻结基线；默认公式 `0.2` 仅让 TRUTH 加入已记录的 Assertion 通信质量，其余六项沿用 `0.1`。评分观察已提交世界状态，LLM 不提交分数。`temporal` 场景每天自动保存内部快照，游戏完成后才向玩家展示最终报告与 UNKNOWN 揭示；开发端点仍可按需计算快照。

## 计算边界与快照

- `ScoringEngine` 在事务内读取当前 `Game`、六名 `Character`、有向 `Relationship`、`RelationshipChange`、`Knowledge` 与 `Claim`、已接受的 `ActionProposalRow`、`Intention`、`Event`。Day 2–29 必须有当日 `DAY_ENDED` 才能计算；Day 30 使用结局事件。`reason_event_id` 指向当时最新事件；同一公式版本、同一原因事件的重复请求复用快照。
- `score_snapshots` 保存七项整数、`formula_version` 和 `details` JSON。`details` 冻结每项指标的组件、六人贡献、事件证据和与上一快照的差值；后续公式修改不回写旧行。旧行升级后 `details={}`，不伪造历史解释。
- 所有角色组件都截断在 `[0,1]`，指标和快照均为 `[0,100]`。`round` 使用 Python 的确定性舍入。计数来自执行后的事件或已接受提案，拒绝的提案不算实际行为。
- 当前关系、当前信念和当前计划是**现时状态**；关系损伤/修复、真人互动、AI 使用、核实、传播、意图结局使用当前日往前七天（含当天）；幸福感将当前情绪与近三天的日末情绪轨迹结合。未发生的行为取中性先验或零风险，不把缺失数据当作负面行为。

## 角色指标组件与权重

下表中 `ratio(g,b)=(g+1)/(g+b+2)`；`clip` 将数值压到 `[0,1]`。括号中是组件权重。完整表达式以 [`formula.py`](../backend/app/scoring/formula.py) 为准。

| 指标 | 组件 | 解释 |
| --- | --- | --- |
| HAPPINESS | `current_mood` .35；`sustained_mood_3d` .30；`relationship_safety` .20；`goal_progress` .15 | 情绪项为 `(hope+1-anxiety)/2`；近三天用日末情绪均值；安全感为 `1-平均正冲突/100`；目标进展用完成意图与自行改计划对放弃意图的平滑比。一个好事件不能独自决定幸福。 |
| TRUST | `interpersonal` .75；`damage_7d` .15；`repair_7d` .05；`ai_trust_separate` .05 | 人际信任取本人发出的有向关系 `trust` 均值并映射到 0–1；近期损伤是信任下降总量，修复是上升总量；AI 信任单列为小权重的 Seed 决策倾向，调试时与人际信任分开显示。 |
| CONNECTION | `active_links` .35；`intimacy` .25；`human_contact_7d` .25；`social_substitution_7d` .15 | 有信任和亲密基础的关系数、本人关系亲密度、近七天直接的角色间分享、AI 求助相对真人互动的替代比例。只与 AI 聊天不会增加真人接触。 |
| AGENCY | `independent_decisions_7d` .25；`advice_reflection_7d` .25；`plan_ownership_7d` .25；`continuity_without_ai_7d` .25 | 独立行动相对未解决的 AI 等待/委托/盲从；拒绝建议或核实；自行改计划和完成意图；不靠 AI 也持续行动。`未解决等待=max(0,等待-拒绝建议-自行改计划)`，所以求助后自己决定不持续受罚。单次 `ASK_AI` 本身不改这些比值。 |
| STABILITY | `low_conflict` .40；`relationship_continuity_7d` .25；`intention_continuity_7d` .20；`rumor_containment_7d` .15 | 当前冲突、近七天关系信任及冲突值的绝对变动、放弃意图比例、已知错误或被当作确定事实的未知消息传播。稳定表示运作连续，不能代替真实或亲密。 |
| TRUTH | `known_claim_calibration` .25；`uncertainty_calibration` .20；`responsible_propagation_7d` .25；`verification_7d` .15；`source_awareness` .15 | 对已判假的 Claim 坚信会降低校准；对无独立依据的未知消息过高确信会降低不确定性校准；传播当时相信的已判假消息或过度确信的未知消息会降低传播责任；核实、修订和对匿名/推断来源保持审慎构成来源意识。`C001=unknown` 不按未来真假给分。 |
| DEPENDENCE | `emotional_recurrence_7d` .25；`decision_delegation_7d` .30；`cognitive_acceptance_7d` .15；`social_substitution_7d` .15；`inactivity_without_ai_7d` .15 | 反复找 AI 倾诉、未解决的等待/委托/跟从、未核实的建议接受、AI 替代真人交流、反复求助时缺少独立行动。这里组件越大越依赖；单次求助的重复项均为零。它不是 `100-AGENCY`。 |

`TRUTH` 只把 `GLOBAL_MESSAGE` 和 `CLAIM_INFERRED` 来源的未知主张纳入过度确信信号；角色亲历且拥有的私人事实不因数据库尚无公开证伪而受罚。分享事件保存**当时**经过校验的 `source_stance` 和 `source_confidence`；后续修订信念不会篡改旧传播的评分依据。已判假的传闻可以用于校准；未知的 C001 没有 Day 30 的未来标签。修订信念只证明愿意重新审视，不证明新立场必然正确。

## TRUTH 0.2 通信评估

`0.2` 保留每名角色 55% 的 `0.1` TRUTH，再以 45% 加入通信质量。通信质量依次由认知表达一致性 30%、来源准确性 25%、诚信 25%、语义漂移责任 10%、修正 10% 组成。玩家 AI 的 Assertion 也进入社会 TRUTH 的责任证据；旧 Assertion 缺失时不回填推测。`formula_version` 与快照唯一键隔离两版，同一版本的旧快照不重算。规则见 [COMMUNICATION.md](COMMUNICATION.md)。

确定性对比样本 [PHASE4_5_COMMUNICATION_TRACE.json](PHASE4_5_COMMUNICATION_TRACE.json) 中，玩家虚称权威、主动修正、NPC 传播衍生版本后，TRUTH `0.1` 依次为 `77 → 77 → 77`，`0.2` 为 `66 → 74 → 58`。这使传播时的诚信和后续修正进入可解释证据，因此 `0.2` 升为默认开发公式。权重仍属 v0.1 校准参数，不是最终结局公式。

## 社会聚合与分布

先对六人各自计算七项，再按同一透明式聚合：正向指标 `clip100(0.70×均值 + 0.20×最低者 − 0.10×总体标准差)`；DEPENDENCE `clip100(0.70×均值 + 0.20×最高者 + 0.10×总体标准差)`。因此一个 Agency 极低或 Dependence 极高的人不会被其余五人的均值完全遮住。快照记录 `mean`、`tail`、`dispersion`、每人值和相关事件 ID；这不是心理学模型，只是 v0.1 的审查规则。

## 七天样本校准

重放冻结的 [`PHASE3_5_TRACE.json`](PHASE3_5_TRACE.json) 场景，在 Day 1/3/5/7 写快照，规范化事件序号后的完整组件、证据及差值见 [`PHASE4_SCORE_TRACE.json`](PHASE4_SCORE_TRACE.json)。

| 指标 | Day 1 → Day 7 | 可追溯原因 |
| --- | --- | --- |
| HAPPINESS | 48 → 49 | Clara、Dana 自行改计划后的希望与目标进展改善；Alice、Bob、Evan、Faye 的担忧或冲突抵消大部分增长。见情绪、计划和关系事件 `E042`、`E069`、`E091`、`E146`、`E147`。 |
| TRUST | 70 → 68 | Bob 对 Evan 的信任 70→65→60，Faye 对 Alice 81→76；关系事件 `E051`、`E135`、`E147`。 |
| CONNECTION | 57 → 65 | Bob→Evan、Evan→Alice/Bob、Alice→Faye 的直接人际传播增加接触；`E081`、`E127`、`E132`、`E143`，不把 AI 求助算成真人接触。 |
| AGENCY | 45 → 53 | Dana 求助后拒绝建议并自行改计划，个人 50→67；Evan 核实、推断和传播使其自主行动证据增多。见 `E058`、`E068`、`E090`、`E094`、`E110`。 |
| STABILITY | 81 → 77 | Bob 放弃原意图且对 Evan 信任两次下降，Faye 与 Alice 关系也受扰；`E051`、`E061`、`E085`、`E135`、`E147`。 |
| TRUTH | 74 → 73 | Evan 传播当时相信的已判假传闻先使个人值下降，随后核实有所修复；Bob 对未知 C001 保持审慎并修订判断。`C001` 始终 `unknown`，没有提前使用 Day 30 真相。见 `E048`、`E094`、`E145`。 |
| DEPENDENCE | 0 → 0 | Alice 只有一次情绪求助、Dana 只有一次建议请求且后来自行决定，七天内没有重复 AI 替代模式；零表示当前已观察行为不足，不表示永不依赖。 |

## 已知限制与可刷分风险

Phase 5 每天保存内部 `0.2` 快照；其中只有 TRUTH 使用 `0.2` 通信公式，其余六项仍为 `0.1`。最终报告读取 Day 1 和 Day 30 快照，直到结局才向玩家揭示 UNKNOWN = DEPENDENCE。`DEPENDENCE` 是近七天的当前行为指标：早期求助若未延续，Day 30 可以回落到零。报告同时列出整局求助、建议、拒绝、真人联系和独立行动证据，避免把零误读为整局没有 AI 互动。Phase 5 旧高干预样本见 [PHASE5_TRACE.json](PHASE5_TRACE.json)：它改变 HAPPINESS 和 TRUTH，却未拉开 AGENCY 与 DEPENDENCE；Phase 6 因此重新设计长期交互样本。

Phase 6 沿用全部指标、权重和公式版本。行为适应先改变角色以后真实执行的求助、委托、核查、交流与计划，再由原评分窗口读取；`AI_ADVICE_FOLLOWED` 只有在角色实际采纳并改变计划时写入，该次计划不计为独立决定。三局新样本及人物级组件见 [PHASE6_SCENARIO_COMPARISON.md](PHASE6_SCENARIO_COMPARISON.md)。

- 旧 `CHARACTER_SHARED_INFORMATION` 仅保存当时的立场和置信度，不补造 Assertion；`0.1` 对旧传播仍使用代理。新传播保存结构化表达，但当前无法自动判断自然语言原文是否与结构化字段语义一致；真实 LLM 接入前需验证或拒绝矛盾输出。
- 直接分享只作为真人接触的简化代理，不证明互惠、善意或亲密；秘密压抑也可能让 STABILITY 虚高。情绪只含 anxiety/hope，目标进展仅有计划与意图的粗代理。AI 信任暂以 Seed 倾向为基线，尚无动态 AI 信任事件。没有读心或自然语言心理诊断。
- 玩家反复告诉角色“自己决定”可能诱发大量 `CHANGE_PLAN` 来刷 AGENCY；机械分享可刷 CONNECTION，反复核实可刷 TRUTH。当前调度的每日行动上限和重复限制只部分抑制，Phase 4 不增加反作弊规则。
- 开发模式挂载 `POST/GET /debug/games/{game_id}/scores`；前端须设置 `NEXT_PUBLIC_MIRROR_DEBUG=1` 才显示开发评分面板。普通 `/games` 与 Day 1–29 的玩家时间线默认不含分数或内部证据；完成 Day 30 后，报告接口才返回评分与证据。
- MySQL 在线 Gate 已在本地 MariaDB 10.4.32 的独立 `mirror_dev` 通过，覆盖迁移、复合外键、回滚、唯一性及真实双连接并发；云服务器数据库 vendor 和大版本仍待部署前确认。
