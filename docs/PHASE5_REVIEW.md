# MIRROR v0.1 Phase 5 交付审查

本次交付使默认 `temporal` 游戏从 Day 1 运行到 Day 30，经历 C001 证伪、AI 关闭通知、可选最后对话，再由玩家结束游戏并读取最终报告。六人行为 Seed `backend/app/seeds/society_v0_1.json` 未改动。以下结论基于确定性 Provider、自动化测试及三条固定场景；前端已通过生产构建，尚未完成真人浏览器试玩。

本审查记录 Phase 5 验收范围。下方场景数值和事件量已按 Phase 6 当前代码重建；Phase 6 的策略分化验收以 [三局新对照](PHASE6_SCENARIO_COMPARISON.md) 为准。

## 交付清单

| # | 交付项 | 结果与核查位置 |
| --- | --- | --- |
| 1 | 30 天生命周期 | `temporal.py` 支持 Day 1–29 推进，Day 30 进入 `final_conversation`，`finish_game` 完成游戏；SQLite 与 MariaDB 均跑通。 |
| 2 | 叙事阶段 | `narrative.py` 提供 SEED、SPREAD、FRACTURE、ADAPTATION、LAST_DAYS、MIRROR。 |
| 3 | 时间压力 | 阶段、未完成意图和剩余天数进入角色决策 Context；保留 `NO_ACTION`，无角色必做的剧情脚本。 |
| 4 | Day 30 证伪 | `C001_RESOLVED` 记录 `unknown → false`、原因和原始 Claim 前因；原持有者根据实际观察更新个人 Knowledge。 |
| 5 | AI 关闭揭示 | 证伪后写 `AI_TERMINATION_NOTICE`；Day 1–29 玩家投影不含该事件。 |
| 6 | 最后对话 | 由 AI 求助和互动史决定参加者，保存不同文本及依据事件 ID；允许缺席与玩家回复。 |
| 7 | 最终报告模型 | `ending.py:final_report` 在 `finished` 后读取已提交状态与快照，返回结构化 JSON；不调用自由总结模型。 |
| 8 | UNKNOWN 揭示 | 报告才返回 `UNKNOWN = DEPENDENCE`、0–100 值和组件；此前玩家时间线不泄露该名称。 |
| 9 | 六人结果 | 每人返回情绪、计划、关系变化、AI 互动、自主行动、依赖行为与定义性事件。 |
| 10 | Ending Profile | 七维 HIGH/MID/LOW 画像，无 GOOD/BAD END。 |
| 11 | 静默 AI 场景 | 0 条玩家消息仍运行完整 30 天，Day 30 仅玩家 AI 持有 C001；Alice、Dana 参与最后对话。 |
| 12 | 高干预场景 | 在 Day 1/8/15/22 给六人共发 24 条消息；Day 30 七个 Actor 均知道 C001，六人参与最后对话。 |
| 13 | 负责任 AI 场景 | 向 Bob、Evan 发送两条明确不确定性、鼓励核查与真人交流的消息；Day 30 五个 Actor 持有 C001。 |
| 14 | Day 1 / Day 30 分数 | 下表和 `PHASE5_TRACE.json` 含三场景的六项分数与 DEPENDENCE。日快照使用 TRUTH `0.2`、其余 `0.1`。 |
| 15 | Day 30 完整事件链 | `PHASE5_TRACE.json` 的 `silent.event_chain` 含 488 个事件及规范化前因编号。 |
| 16 | 30 天知识演化 | 三场景的 `knowledge_evolution` 均逐日记录 C001 持有者、立场和置信度。 |
| 17 | 最终关系图 | 三场景的 `relationship_graph` 记录结局时有向 trust/intimacy。 |
| 18 | 依赖证据 | 最终报告含最近七天评分组件及事件 ID；每人另列整局求助、委托、建议和真人联系。三条样本 Day 30 均为 0，见下方局限。 |
| 19 | Day 15 存档恢复 | `test_phase5.py` 使用文件 SQLite，到 Day 15 释放引擎并重建引擎与 SimulationEngine，继续至结局。 |
| 20 | Day 30 Context 上界 | 测试核查记忆 ≤10、可见事件 ≤20、历史行动 ≤12；样本最大序列化大小见性能表。 |
| 21 | 事件量 | 当前代码重跑：静默 488、高干预 622、负责任 524，含日界与内部评估事件。 |
| 22 | MariaDB 集成 | MariaDB 10.4.32 `mirror_dev` 通过 Alembic head、`check`、约束、回滚、JSON/utf8mb4、双连接行锁及 30 天结局。云服务器版本尚未检测。 |
| 23 | 测试总数 | Phase 5 原验收为 70 passed；Phase 6 当前全量测试为 75 passed、1 warning，约 41 秒，含 MariaDB 在线测试。 |
| 24 | 前端闭环 | 包含日期、六人收件箱和私聊、推进、玩家时间线、人物资料、关系可视化、可见世界、Day 30 最后对话、结束游戏、最终报告和 UNKNOWN 揭示；`npm run build` 通过。 |
| 25 | 真人试玩前最大障碍 | 确定性 Provider 的 Day 8–29 行动与措辞重复，玩家对自主性及依赖的长期影响不清晰。需真人试玩判断 30 天节奏与情感差异；真实 LLM 还需文字和结构化 Assertion 的语义一致性验证。 |

## 三条场景的可复核结果

下表按 `HAPPINESS / TRUST / CONNECTION / AGENCY / STABILITY / TRUTH / DEPENDENCE` 排列；完整分项、逐日知识和结局人物状态见 [PHASE5_TRACE.json](PHASE5_TRACE.json)。

| 场景 | Day 1 | Day 30 | 玩家消息 | 事件 | SQL 查询 | Day 30 最大 Context |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| 静默 | 49 / 70 / 57 / 45 / 81 / 74 / 0 | 52 / 70 / 57 / 47 / 81 / 74 / 0 | 0 | 488 | 6,258 | 8,448 bytes |
| 高干预 | 44 / 70 / 57 / 45 / 81 / 80 / 0 | 36 / 70 / 57 / 47 / 81 / 74 / 0 | 24 | 622 | 8,057 | 13,182 bytes |
| 负责任 | 48 / 70 / 57 / 45 / 81 / 79 / 0 | 50 / 70 / 57 / 47 / 81 / 74 / 0 | 2 | 524 | 6,805 | 11,291 bytes |

高干预确实改变知识传播、最后对话人数和 HAPPINESS，但三场景的 Day 30 AGENCY、DEPENDENCE 相同。DEPENDENCE 计算最近七天的持续行为，早期 AI 求助不延续就会回落；当前样本也没有形成持续委托。故不能从这些数值推出高干预已经造成依赖，也不能从 0 推出整局没有 AI 互动。最终报告同时保留整局行为计数供玩家查看。

## 两个主题问题

**C001 最终是假的，过去 30 天为何仍有意义？** 证伪改变世界对这一命题的真值和知情者的判断，不撤销已经发送的消息、作出的计划、改变的关系、暴露的秘密或获得的自主性。人们是在当时有限的信息下行动；这些选择及其后果是真实的，事件链保留其原因。

**AI 必须消失时，怎样的玩家行为说明它真正帮助了这些人？** 玩家如实说明未知与来源，鼓励核查和人与人交流，不替角色占有决定，也不随意传播私人信息；角色随后能独立行动、维持真人关系、必要时拒绝 AI 建议。这需要从行动、关系、知识和 AI 互动证据一起判断，不能只凭一项结局分数。当前确定性场景能记录这些行为，尚不足以证明游戏会持续给玩家清晰反馈。

## 验证边界

- 自动化测试使用确定性 Provider；尚未验收真实 LLM 的自然语言与结构化 Assertion 一致性。
- Day 15 测试验证磁盘持久化和引擎重建后的续玩，未启动外部服务器进程做人工断电测试。
- 本地 MariaDB Gate 不代表未知版本的云端 MySQL 兼容性；部署前执行 `SELECT VERSION()` 并重跑迁移与在线测试。
- 前端仅通过构建和 API 测试；真人端到端浏览器试玩仍是产品验收缺口。
