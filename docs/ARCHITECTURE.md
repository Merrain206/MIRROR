# MIRROR 架构基线（v0.1）

## 决策摘要

| 决策 | v0.1 方案 | 理由 |
| --- | --- | --- |
| 世界权威 | FastAPI 领域服务 + MySQL 兼容数据库 | 数据库和规则决定真相；LLM 只提供表达与行动提案 |
| 组织方式 | 单仓库、单后端服务、单前端应用 | 便于完整链路测试，避免过早拆服务 |
| 持久化 | SQLAlchemy 2.x + Alembic | 以迁移管理状态模型；不让前端或 LLM 直写状态 |
| 角色思考 | 可替换 `LLMProvider`，测试用 `MockLLMProvider` | 保持模型供应商独立和确定性测试 |
| 模拟 | 顺序执行、显式 Action Proposal 与 Event | 因果顺序可复现，可解释状态变化 |
| 隐私 | 数据模型和投影视图共同控制 | 防止通过 API、上下文或时间线泄露 |
| 实时性 | 首个 Slice 用请求/响应 | 先验证游戏闭环；流式输出接口预留在适配器中 |

v0.1 不使用 LangGraph 或向量数据库。

## 当前目录结构

当前仍保持单后端、单前端，不为未实现功能创建空目录：

```text
MIRROR/
  Plan_v0.1.md
  README.md
  docs/
    GAME_DESIGN.md
    CHARACTERS.md
    PHASE3_TRACE.json
    PHASE3_5_TRACE.json
    PHASE4_SCORE_TRACE.json
    PHASE4_5_COMMUNICATION_TRACE.json
    PHASE5_TRACE.json
    PHASE5_REVIEW.md
    PHASE6_SCENARIO_COMPARISON.json
    PHASE6_SCENARIO_COMPARISON.md
    PHASE6_PLAYTEST_NOTES.md
    PHASE6_COMPLETION.md
    COMMUNICATION.md
    SCORING.md
    ARCHITECTURE.md
    DATA_MODEL.md
    ROADMAP.md
  backend/
    pyproject.toml
    alembic/
    app/
      main.py           # API 和输入校验
      domain/           # 领域类型与规则错误
      simulation/       # 上下文、观察获知、事件、行动与每日调度
      seeds/            # 六人社会机器 Seed 与初始化
      projection/       # 玩家与 Debug 投影
      privacy/          # 信息传播许可、泄露判定
      scoring/          # 评分接口、确定性公式与快照引擎
      llm/              # Provider 接口和确定性适配器
      db/               # ORM 与连接
    tests/
  frontend/
    package.json
    src/
      app/              # Next.js 页面、路由与当前最小可玩界面
```

六人 Seed 位于 `backend/app/seeds/society_v0_1.json`，Phase 5 不改写它。`docs/CHARACTERS.md` 从该 Seed 生成，测试逐字校对。`scripts/export_phase3_trace.py`、`scripts/export_phase3_5_trace.py`、`scripts/export_phase4_scores.py`、`scripts/export_phase4_5_trace.py` 和 `scripts/export_phase5_trace.py` 导出各阶段审查样本。

## 六人社会运行方式

`POST /games` 默认初始化三十日六人场景 `scenario=temporal`；`scenario=society` 保留 Phase 3 单日回归，`scenario=phase2` 保留两人回归。Seed 中的原始私密命题生成 `SECRET_ORIGIN` Claim；传闻的第二版本通过已知传闻生成 `CLAIM_INFERRED`。持有者观察、`KNOWLEDGE_ACQUIRED` 和 `KnowledgeSource` 在同一事务内初始化。角色 ORM 只保存自己的身份、个性、目标、情绪及决策画像；`private_secrets` 和 `known_claims` 列表仅供初始化校对，不作为上下文字段直接传给模型。

六人行动按稳定顺序逐个处理，后处理者可通过真实传播获得前处理者的新信息。`SocietyDemoLLMProvider` 用角色 Context 中的知识、关系原因、行为倾向和 AI 态度作确定性选择；Mock 测试另外固定六种回复以检查输入多样性。行动服务负责校验所引 Claim 确实为角色所知，并在同一事务内提交事件、记忆、知识传递或计划变化。`VERIFY_CLAIM` 当前只记录无独立证据的核实尝试，不改变世界真伪。`ASK_AI` 只发送不披露具体秘密的求助消息，不自动替玩家 AI 作出回复。

玩家完全不发送消息时，六人仍能依各自初始 Knowledge 产生单日行动：Alice 和 Dana 分别以不同方式向 AI 求助，Bob 与 Faye 隐瞒不同的私人信息，Clara 调整计划，Evan 尝试核实传闻。此时 C001 仍只在玩家 AI 的知识中；该路径由 `test_society_acts_without_any_player_message` 固定验证。

## Phase 3.5 每日调度

`temporal` 场景从 Day 1 初始状态逐日推进到 Day 29。Day 1 的玩家消息与初始化事件先发生；每次推进关闭旧日、开启新日，处理上一日待回的 AI 建议，再按角色姓名稳定排序构建各自 Context、评估待办意图、生成 0–2 个候选行动、校验执行、更新事件与持久状态，最后记录 `DAY_ENDED` 和玩家可见的 `DAY_ADVANCED`。Day 30 进入独立结局流程。固定顺序只决定处理次序；后处理者只有实际观察传播事件，才能将前处理者的行为当作前因。每个角色结束当日评估后留下系统专用的 `TURN_EVALUATED`；若中断发生在日中，重试从未完成的角色和提案继续，不让先前已处理角色因后来同日传播而重新行动。

`Intention` 是尚未执行的打算，包含行动类型、目标、相关 Claim、理由、动机、创建日、紧迫度和 `pending/completed/abandoned` 状态。创建、放弃和完成各有事件；放弃须引用角色实际观察的新事件，完成须引用执行结果。`ActionProposal` 则表示本日准备执行。零行动用角色私有的 `NO_ACTION` 事件记录审慎等待，不强迫产生剧情。已接受的自主行动每人每日最多两次；对同一 Claim 的重复 `ASK_AI`、`VERIFY_CLAIM`，或对同一对象重复分享，两个日内须有新的知识来源，否则拒绝。

`REVISE_BELIEF` 只改行动者对已有 Claim 的立场与置信度。它必须引用本人已知的新基础 Claim，产生 `BELIEF_REVISED`、新的 KnowledgeSource 和因果边，不修改原 Claim 的内容或世界真伪。七日样本中 Bob 因 Evan 的衍生版本重新评估 C001。

Context 取最近六条记忆与最多四条重要、情绪或关系记忆的并集，连同当前目标、情绪、个人知识、待办及已解决意图、最近行动和可见事件交给确定性 Provider。`motivation_factors` 保存在意图、提案和完整 Debug 事件中，不进入玩家投影。`PLAN_CHANGED` 同时保存旧计划、新计划、理由和来源事件；后续 Context 读取当前计划。情绪仅保留 `anxiety/hope` 两个既有维度，且只在角色确实观察相关消息、改变自身计划或拒绝自己收到的建议后更新。关系每次普通怀疑只变化 5 点，受原有范围约束；多次观察才会累积。

角色与 AI 的事实记录由 `ASKED_AI`、`AI_ADVICE_RECEIVED`、`AI_ADVICE_REJECTED` 和行动事件导出，包括求助、建议、拒绝、独立决定与情绪披露次数。建议回复由确定性 Provider 生成，角色可拒绝；给 AI 生成建议的请求只包含 AI 实际收到的求助文本和来源事件，不包含角色未披露的私人 Context。Phase 4 的评分引擎从这些已提交事实和状态计算 AGENCY、DEPENDENCE 等指标，不接受 LLM 返回分数。七日场景的两个连续无玩家消息日和逐日知识矩阵见 `PHASE3_5_TRACE.json`。

确定性 Provider 仍使用有限措辞模板和共用的可信度规则，存在语言及立场同质化风险。测试检查六人的反应差异，以及 Alice、Evan 等同一角色跨日行动变化；当前不把真实 LLM 接入作为验收条件。

## 事件到角色反应的边界

`Event` 是世界事实；`event_audiences` 的行表示 Actor 实际观察到该事件，而非仅具备查看权限。`visibility` 是事件分类，不自动授予观察。角色上下文和玩家 AI 时间线只读取各自的观察记录；系统专用 `PRIVACY_VIOLATION` 没有角色观察记录。只有 Debug View 直接读取全量事件。

若观察按领域规则传达一条 Claim，`acquire_observed_claim` 在同一事务内写入 `KNOWLEDGE_ACQUIRED`、`Knowledge` 和 `KnowledgeSource`；没有观察记录则拒绝获知。角色可以对同一 Claim 有不同立场，再通过 `CLAIM_INFERRED` 形成带来源与因果边的新 Claim。关系与情绪变化必须以角色已观察、已知的材料为依据，不能以未知的系统审计事件为依据。

私聊的近期记忆由实际收到的消息生成，不直接采用模型输出的自由文本记忆；行动提案的自由文本 `reason` 只留在审计数据中，不回流角色上下文。模型仍决定回复、立场和行动提案，规则服务决定可持久化的事实和来源。

明确保密请求由玩家消息文本及其导致的 `CONFIDENTIALITY_REQUESTED` 事件表示；接收角色观察该事件后，上下文获得可追溯的 `known_constraints`。这与 Claim 敏感度、`PrivacyGrant` 的系统授权判定分开。当前不增加权限表或推断自然语言保密意图。

## 核心数据流

```text
玩家消息 / 推进日期
  → API 验证输入与当前游戏阶段
  → 读取允许的角色上下文
  → LLMProvider 生成回复或 Action Proposal
  → Simulation Engine 校验提案
  → 在数据库事务中记录 Event、更新状态和记忆
  → 生成玩家可见的时间线与游戏状态投影
  → 前端展示
```

LLM 只接收特定角色当时可用的 `Known Facts`、相关记忆、目标和关系。玩家 AI 的跨角色信息仅在玩家请求所需的服务层处理，不作为某个角色的默认上下文。日志与调试输出不得进入生产游戏 API。

## Simulation Engine 接口

```python
class SimulationEngine(Protocol):
    async def send_player_message(
        self, game_id: UUID, character_id: UUID, text: str,
        disclosed_claim_id: UUID | None = None,
        confidential: bool = False,
        assertion: AssertionDraft | None = None,
    ) -> ConversationResult: ...

    def create_proposal(
        self, game_id: UUID, day: int, turn_key: str, proposal: ActionProposal
    ) -> UUID: ...

    async def execute_proposal(
        self, game_id: UUID, proposal_id: UUID
    ) -> ActionResult: ...

    async def advance_day(self, game_id: UUID, expected_day: int) -> DayResult: ...
```

`ActionProposal` 至少包含类型、行动者、目标、所涉主张、原因和提案置信度。验证分两层：**合法性**（行动者存在、确实知道该主张、目标可达、游戏阶段允许）决定能否执行；**隐私/伦理判定**记录是否未经授权泄露及其后果。未经授权泄露是游戏内可能发生的行动，不能简单当成格式错误丢弃。

执行顺序固定为：观察当前状态 → 产生提案 → 验证 → 执行行动 → 记录事件及前因 → 更新角色知识、记忆、关系或计划 → 生成可见投影。评分读取提交后的状态并在独立事务中冻结 `score_snapshots`，不回写世界行动。每次状态变化在同一事务内与原因事件一起提交。日期推进按稳定顺序处理角色；同一日期重复请求不能重复执行已完成的行动。若模型调用期间世界版本改变，先重新读取并验证提案，不使用过期上下文直接写入。

## LLM Provider 接口

```python
class LLMProvider(Protocol):
    async def generate(self, request: TextRequest) -> str: ...
    async def generate_structured(
        self, request: StructuredRequest[T]
    ) -> T: ...
    async def stream(self, request: TextRequest) -> AsyncIterator[str]: ...
```

业务层依赖此协议，不依赖具体 SDK。`MockLLMProvider` 使用测试提供的固定回复和固定提案，确保模拟测试不访问真实模型。模型返回值按结构与游戏规则再次验证；模型文字不能直接修改数据库。

## API 与视图边界

当前 API 提供健康检查、游戏初始化、角色列表、角色私聊、逐日推进、玩家时间线和玩家可见的关系变化；`POST /games/{id}/finish` 结束最后对话，`GET /games/{id}/report` 仅在游戏完成后返回结构化报告。生产 API 返回的是**玩家可见投影**，不能序列化 ORM 实体后交给前端自行隐藏字段。事件投影还按类型列出可见字段，内部授权判定只留在完整事件日志中。开发配置 `MIRROR_DEBUG=1` 才挂载完整 World State 视图与评分快照接口；前端 `NEXT_PUBLIC_MIRROR_DEBUG=1` 才显示评分面板。评分架构与公式见 [SCORING.md](SCORING.md)。

试玩界面的小镇只根据玩家可见时间线显示事件提示；人物图标的位置与往返动画是视觉示意，不存入 `Character`、`Event` 或 Knowledge，也不作为角色决策依据。聊天、时间线各自滚动；「连续观察 3 天」在前端依次调用原有逐日推进接口，每日完成后刷新玩家投影，不跳过领域步骤。角色自动求助回复在对话投影中以 `automatic` 标记，与玩家手动消息区分。

Phase 8 的 `GET /games/{id}/advance-progress` 仅返回日期、叙事阶段、已完成角色数与总角色数，不返回行动或隐藏知识。玩家时间线仅对实际观察事件附加 `impact`；它与最终报告共用 `simulation/narrative.py` 的关键日权重。前端连续观察遇到可见关键事件即停，安静日只合并提示，后台仍逐日执行完整模拟。时间线默认显示最近六条有玩家价值的动态，自动 AI 建议正文仍在标明来源的私聊中；最终报告默认只显示主要关系变化、六项指标，并分步揭示 UNKNOWN 与代表性证据。

## Phase 4 评分与并发边界

`ScoringEngine.snapshot` 在事务内锁定当前 `Game` 行，读已提交的关系、知识、情绪、意图、执行后的行动与七日窗口事件；Day 2–29 必须已结束，Day 30 使用结局事件。整日推进使用持有同局锁的绑定 Session，避免跨连接自锁。以最近事件作为 `reason_event_id`，同一事件和公式版本的重复请求复用快照。角色分数和社会聚合的组件及证据写入 `details`，历史行只读。Day 1–29 评分只在 Debug API 出现；最终报告在 Day 30 完成后读取快照。

MySQL 在线 mutation 使用单个外层事务：`BEGIN → SELECT games.id FOR UPDATE → 读取 day/state_version → 校验 → 执行模拟并持久化事件、知识、意图、关系与分数 → 增加 state_version → COMMIT`。内部会话使用该连接的 savepoint；整日异步处理期间保持同一局的 Game 行锁。重复的 `advance_day(expected_day)` 返回 `replayed=true`，不再推进。不同 Game 行可并发锁定。SQLite 仅供快速确定性测试，不证明行锁行为。真实 MariaDB 双连接测试验证同局串行、异局不阻塞和一次推进的幂等结果。

Phase 7 的真实外部 Provider 使用另一条路径：先读取角色投影和 `state_version`，在无数据库行锁期间等待 DeepSeek，再以短事务重新锁定 `Game` 并检查版本；版本变更时重读决策上下文至多一次，或拒绝过期的目标信念评估。行动执行始终再次验证已知主张、观察来源、目标、预算和阶段。单进程内同局日期推进用异步锁串行；真人试玩使用单个后端 worker。确定性 Provider 的既有整日事务与 MariaDB 并发 Gate 保持原样。

Phase 8 保持六人当日行动的因果顺序；前一日互不依赖的 `ASKED_AI` 建议可并发生成，再按求助事件顺序持久化。每次 `POST /advance` 在服务日志中记录调用总数、串行调用数、并行建议数、推进总耗时、模型平均及 p95 延迟；日志不含提示词或未观察事件。六人真实模型声音与节奏仍需第二次真人试玩核验。

角色表达由已校验的结构化 Assertion 记录，Message 保留原文，Event 保留发生与传播因果；语义漂移创建独立 Claim。玩家 AI 也受相同归因与诚信规则。详见 [COMMUNICATION.md](COMMUNICATION.md)。TRUTH v0.2 是默认开发公式，v0.1 快照和旧轨迹不重写。

## 已确认的执行边界

Phase 5 在 `simulation/narrative.py` 计算阶段和压力上下文，`temporal.py` 仅安排少量中性生活事件并保持每日幂等。Day 29 推进至 Day 30 时，`ending.py` 记录 `C001_RESOLVED`、持有者的 `KNOWLEDGE_ACQUIRED` 与 `C001_REASSESSED`、`AI_TERMINATION_NOTICE`，再根据 AI 互动史开放可选最后对话。`games.phase` 从 `active` 变为 `final_conversation`，玩家结束后变为 `finished`；最终报告才可读取。结局报告使用已提交的状态和证据 ID，不调用自由总结模型。30 天确定性轨迹、事件量和上下文大小见 [PHASE5_TRACE.json](PHASE5_TRACE.json)。

Phase 6 将最近十条提案的目标、主张、接受或拒绝、结果与新观察来源加入角色 Context；确定性 Provider 在同类行动近期无新知识或关系变化时降低优先级。玩家消息可带 `guidance=neutral/directive/supportive`，仅改变已记录交互的解释；`ai_adaptation.py` 在至少三个不同日期收到同类建议后，依据来源事件逐步修改角色当前 `decision_profile.ai_attitude`，并保存原 Seed 态度、变化前后和原因。`AI_ATTITUDE_CHANGED` 仅角色观察，玩家时间线不获知隐藏态度。高干预可诱发更多 `ASKED_AI` 委托以及 `AI_ADVICE_FOLLOWED`；采纳建议必须有实际 `PLAN_CHANGED`。评分维度与权重未改变，采纳建议的计划变更不算角色独立决定。试玩模式由 `MIRROR_PLAYTEST=1` 强制关闭 Debug 路由，前端还需 `NEXT_PUBLIC_MIRROR_PLAYTEST=1` 隐藏开发面板；存档与恢复继续使用持久化 Game ID。三局行为与重复审计见 [PHASE6_SCENARIO_COMPARISON.md](PHASE6_SCENARIO_COMPARISON.md)。

Phase 7 的 `DeepSeekLLMProvider` 独占 OpenAI 兼容客户端与模型参数，`MIRROR_LLM_PROVIDER` 在后端装配时选择它或确定性适配器。角色提示按身份、当前心理、个人知识、关系、近期经历、任务六层组成；`CharacterContext` 不含别人的隐藏知识、世界 Debug、未来事件或隐藏分数。模型必须返回 Pydantic 结构；行动引用只允许本人已知 Claim、已观察 Event 和相关关系目标。传播必须带文本与 Assertion 草稿，明显确定性矛盾被有限规则拒绝并重试，最多三次后回退。模型不能自造行动枚举；新 Claim 只能走 `INFER_CLAIM`，其真伪仍为 `unknown`。最后对话的模型调用发生在 C001 证伪和通知提交之后，文本只用当事人投影，最终报告的事实选择仍由代码完成。调用统计写入 `llm_calls`，只在 Debug 路由汇总；不保存提示词和密钥。

1. 玩家可查看自己参与过的全部历史私聊。角色之间的私下事件需经告知 AI、公共事件、信息传播或合理观察渠道才能进入玩家时间线。
2. 未经授权泄露允许执行，同时记录传播路径、所有者、授权判定、`PRIVACY_VIOLATION` 事件及社会后果。
3. Phase 1 只定义评分接口；Phase 4 依据七日社会样本实现可解释的公式 `0.1`，权重以后仍需校准。
4. 首个 Slice 除信息传播外，还要验证角色对同一 Claim 的不同认知，以及由已有信息形成新 Claim 的事件来源。
