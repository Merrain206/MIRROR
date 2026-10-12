# Phase 4.5 通信语义

## 事实边界

`Claim` 是可讨论的命题；`Knowledge` 是某位 Actor 对该命题的立场、置信度与获知来源；`Assertion` 是这位 Actor 在某次具体交流中表达的结构化立场、确定性和来源；`Message` 保存实际文字；`Event` 记录传播发生、观察者与因果前因。五者不能互相代替。说话者的 Knowledge 不自动等于他表达的 Assertion，接收者也不会因收到消息而自动相信它。

重要传播在同一事务内建立 Message、Assertion、Event 和接收者的获知记录。Assertion 引用说话者、接收者、Claim、来源事件及消息事件。角色上下文只读取本人实际观察到的 Assertion；完整的 `fidelity` 审计只进 Debug World。玩家 AI 发出的消息同样建立 Assertion 并进入 TRUTH 证据。旧事件保持原样，未补造历史表达。

## 结构化表达与校验

`AssertionDraft` 可提出 `expressed_stance`、`expressed_confidence`、`source_attribution`、`text`、可选的 `derived_claim_content` 和 `corrects_assertion_id`。规则层检查参与者、同局引用、说话者确实知道来源 Claim、对应 Message/Event、修正关系的同局同说话者同命题谱系。来源类别为 `direct_observation`、`another_character`、`ai`、`anonymous`、`unknown`、`claimed_authority`。文本仅是表达原文，不能直接改写世界真伪。

规则按传播时 Knowledge 与实际来源计算 `fidelity`：认知与表达的置信度差、来源准确性、`accurate_uncertainty`、`overstatement`、`understatement`、`false_authority`、`knowing_misrepresentation`、`epistemic_error`、`semantic_drift` 和 `correction`。高确信地传播最终被证伪的命题，在当时仍可能是诚实的认知错误；明显背离自己当时所知的表达才归为知情误导。分类是结构判断，不是完整的人类道德判断。

若表达改变命题内容，系统创建新 Claim，并用 `source_claim_id`、新 Claim 的 `origin_event_id`、Assertion 与 EventCause 留住原命题和传播路径；接收者获得新 Claim，而原 Claim 不被覆盖。修正指向旧 Assertion，新增指向旧表达事件的因果边，并允许 TRUTH 后续获得有限修复。未经授权泄露仍可发生，原有所有者、授权与隐私违规机制继续运作。

Phase 6 的 `MESSAGE_SENT.data.guidance` 是玩家声明的建议方式，取中性、代作决定或支持自主三种值；它不代替消息文字、Claim 或 Assertion，也不改变传播真伪。确定性试玩样本保持文字与声明一致；真实 LLM 阶段仍需对文字、结构化建议方式及 Assertion 建立语义校验。

## 评分与确定性样本

TRUTH `0.1` 保持旧算法和快照。实验 `0.2` 在角色层以 55% 旧 TRUTH + 45% 通信质量组合；通信质量使用认知表达一致性 30%、来源准确性 25%、表达诚信 25%、语义漂移责任 10%、修正 10%。玩家 AI 的 Assertion 也进入社会 TRUTH 证据。其他六项指标沿用 v0.1。比较显示同一场景 v0.1 为 `77 → 77 → 77`，v0.2 为 `66 → 74 → 58`，可区分错误权威表达、主动修正和新漂移；因此开发接口默认 v0.2，仍可显式请求 v0.1。样本与事件证据见 [PHASE4_5_COMMUNICATION_TRACE.json](PHASE4_5_COMMUNICATION_TRACE.json)。

确定性测试覆盖诚实不确定、夸大、诚实但错误的信念、虚假权威、语义漂移、修正、玩家与 NPC 责任、观察隔离、跨局拒绝和旧轨迹兼容。当前最大限制：结构化字段和自然语言原文只校验为同一条 Message，尚无语义一致性验证器；未来真实 LLM 可能提交互相矛盾的文字与字段，接入前必须增加验证或拒绝策略。
