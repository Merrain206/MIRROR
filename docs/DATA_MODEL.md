# MIRROR 数据模型（v0.1）

本文描述已实现的 Phase 5 数据模型及后续约束方向。当前迁移链以 `e81c1a12ac5f` 为 head；本地 MariaDB 10.4.32 的独立 `mirror_dev` 已通过在线迁移和约束验证。所有游戏状态表都带 `game_id`，UUID 统一以 `CHAR(32)` 保存。数据库对关键枚举添加 `CHECK` 约束。

## 三种不能混淆的东西

| 概念 | 含义 | 示例 |
| --- | --- | --- |
| `claims` | 世界中可被谈论的命题，附当前可验证状态 | “30 天后世界毁灭”，Day 1 为 `unknown` |
| `knowledge` | 某个行动者已接触该命题，并持有什么立场 | Alice 听过，但认为 AI 出故障 |
| `assertions` | 某个行动者在具体传播中表达的结构化立场、确定性和来源 | Alice 向 Bob 表达“我很确信”，但本人 Knowledge 仍只有 0.3 把握 |
| `messages` | 某个行动者实际说过的原文 | Alice 对 Bob 说“我觉得这是真的” |

消息不会自动变成真相；数据库存在一条命题也不会自动让角色知道它。

## 核心实体

| 实体 | 关键字段 | 约束与用途 |
| --- | --- | --- |
| `games` | `id`, `day`, `scenario`, `phase`, `state_version`, `created_at` | `temporal` 场景推进至 Day 30，阶段为 `active` / `final_conversation` / `finished`；`society` 和 `phase2` 保留回归。`phase` 列为 `VARCHAR(24)`，可容纳结局阶段名 |
| `actors` | `id`, `game_id`, `kind`, `display_name` | `kind` 为 `player_ai` / `character` / `system`；作为事件、知识和授权的统一主体 |
| `characters` | `actor_id`, `age`, `occupation`, `background`, `traits`, `values`, `fears`, `desires`, `communication_style`, `goals`, `emotional_state`, `decision_profile` | 每个角色关联一个 `actors` 行；`decision_profile` 只保存本人 AI 态度、行为倾向和决策偏差，不保存秘密正文或其他角色的知识 |
| `claims` | `id`, `game_id`, `content`, `truth_status`, `owner_actor_id?`, `origin_event_id`, `sensitivity` | 每条主张必须有来源事件；`truth_status` 为 `unknown` / `true` / `false`；`owner_actor_id` 表示隐私归属，不表示谁知道；无主的全球消息允许为空 |
| `knowledge` | `id`, `game_id`, `holder_actor_id`, `claim_id`, `stance`, `confidence`, `first_acquired_day`, `last_event_id` | 唯一键 `(game_id, holder_actor_id, claim_id)`；`stance` 为 `believe` / `doubt` / `deny`；置信度 0–1。不同角色的认知彼此独立 |
| `knowledge_sources` | `id`, `knowledge_id`, `source_actor_id?`, `source_event_id`, `acquired_day` | 每次获知或收到新证据增加一行，保留传播路径；匿名来源允许 `source_actor_id` 为空 |
| `messages` | `id`, `game_id`, `sender_actor_id`, `recipient_actor_id`, `day`, `text`, `event_id` | 私聊内容单独保存；发送消息对应 `MESSAGE_SENT` 事件 |
| `assertions` | `id`, `game_id`, `speaker_actor_id`, `recipient_actor_id`, `claim_id`, `source_claim_id?`, `event_id`, `source_event_id`, `corrects_assertion_id?`, `expressed_stance`, `expressed_confidence`, `source_attribution`, `text`, `day`, `fidelity` | 一次实际表达；所有主体、Claim、Event 与被修正的 Assertion 均有同局复合外键。`fidelity` 是规则计算的 JSON 审计结果，不从文本直接判真 |
| `memories` | `id`, `game_id`, `owner_actor_id`, `kind`, `content`, `source_event_id`, `day`, `importance`, `confidence`, `visibility` | `kind` 区分 recent / important / emotional / relationship；只投给所有者上下文；`source_event_id` 说明记忆从何而来。近期私聊记忆由已收到的消息生成，不接收模型编造的额外事实 |
| `memory_related_actors` | `memory_id`, `actor_id` | 标记记忆涉及的角色，供上下文检索；记忆可涉及多人 |
| `relationships` | `game_id`, `from_actor_id`, `to_actor_id`, `trust`, `affection`, `intimacy`, `conflict`, `basis` | 有向关系；数值统一为 -100–100；`basis` 记录这一方认识关系的原因；唯一键 `(game_id, from_actor_id, to_actor_id)` |
| `relationship_changes` | `id`, `relationship_event_id`, `from_actor_id`, `to_actor_id`, `before_values`, `after_values`, `reason` | 每次变更对应 `RELATIONSHIP_CHANGED` 事件，记录前后值和原因 |
| `privacy_grants` | `id`, `game_id`, `claim_id`, `owner_actor_id`, `holder_actor_id`, `recipient_actor_id?`, `created_event_id`, `revoked_event_id?` | 信息所有者允许某持有者向指定对象传播；空接收者表示公开授权 |
| `action_proposals` | `id`, `game_id`, `day`, `turn_key`, `actor_id`, `type`, `target_actor_id?`, `claim_id?`, `payload`, `reason`, `confidence`, `status`, `rejection_reason?`, `proposal_event_id?`, `result_event_id?` | `turn_key` 在一局内唯一；保存原始结构化提案、提案事件与执行结果，供恢复和幂等处理 |
| `intentions` | `id`, `game_id`, `actor_id`, `intended_action_type`, `target_actor_id?`, `claim_id?`, `reason`, `motivation_factors`, `created_day`, `urgency`, `status`, `created_event_id`, `resolved_event_id?` | 尚未执行的打算；状态限 `pending/completed/abandoned`，紧迫度 0–1；目标、Claim 与事件均有同局复合外键 |
| `events` | `id`, `game_id`, `sequence`, `day`, `type`, `actor_id?`, `target_actor_id?`, `data`, `visibility` | 游戏内不可变的事件日志；`sequence` 在一局内严格递增；`data` 仅放事件特有字段 |
| `event_causes` | `event_id`, `cause_event_id` | 多对多因果边；前因必须属于同一局且 `sequence` 更小 |
| `event_audiences` | `event_id`, `actor_id` | 该 Actor 实际观察到该事件；即使事件标为 `public`，也须建立观察记录才会进入角色或玩家 AI 的视图 |
| `score_snapshots` | `id`, `game_id`, `day`, `reason_event_id`, `formula_version`, 七项数值, `details` | 六项显性值与内部依赖度均为 0–100；`details` 冻结组件、角色贡献、证据和与上一快照差值；每日内部冻结，最终报告在结束后读取 Day 1/30 |

`characters` 的个性字段先使用受 Pydantic 约束的 JSON 结构，避免为 v0.1 的 Seed 建立大量空表。关系、知识、事件、传播授权等必须可查询和可约束的数据采用独立表。

一次重要传播对应 Message、Assertion 与 Event；Assertion 可与发送者 Knowledge 不同。语义漂移创建新 Claim，`source_claim_id` 保留原命题，衍生命题的 `origin_event_id` 与因果边追溯传播事件。修正指向先前 Assertion，并要求同局、同说话者及同一命题谱系。旧事件没有 Assertion 时保持空缺，不伪造历史表达。规则与限制见 [COMMUNICATION.md](COMMUNICATION.md)。

## 信息归属、传播与可见性

1. Day 1 的全球消息生成一条 `claims`（`truth_status=unknown`）和一条只对玩家 AI 可见的事件；只有玩家 AI 获得 `knowledge`。消息来源仍匿名；Phase 2 场景中由玩家 AI 托管这条私密主张的传播授权，`owner_actor_id` 指向玩家 AI，不代表 AI 是消息发送者。
2. Alice 私聊告诉 AI 一个秘密：创建以 Alice 为 `owner_actor_id` 的命题、Alice 与 AI 各自的知识记录、消息事件及来源记录。Bob 没有知识记录。
3. 若 AI 告诉 Bob，先确认 AI 确实持有该命题。再检查 Alice 对这次传播的授权。无授权时，行动仍可能执行，但同时记录 `PRIVACY_VIOLATION` 事件及因果边。
4. Bob 获得知识时记录来源事件；Bob 对该主张的相信程度可与 Alice 不同。后续转述必须再次经过同样检查。
5. `event_audiences` 的实际观察记录决定玩家时间线和角色事件上下文；`events.visibility` 只标记事件类型，不自动赋予任何 Actor 观察。私聊双方在消息事件上各有观察记录。前端只接收服务端投影，不能靠隐藏组件保护私密字段。
   可见事件还需按事件类型筛选 `data` 字段；完整授权审计字段只进入 Debug View。隐私违规事件本身不会自动告知 Bob 或玩家 AI。
6. 角色从已有信息推断新主张时，新 `claims.origin_event_id` 指向推断事件；该事件通过 `event_causes` 指向所依据的信息事件。新主张的真伪初始为 `unknown`。
7. 玩家明确请求“不要转述”时，实际消息正文包含该要求，并产生双方观察到的 `CONFIDENTIALITY_REQUESTED` 事件；事件 `data` 指向受约束 Claim 及消息来源。角色上下文由此得到已知保密约束。系统继续用 Claim 的 `sensitivity`（`public` / `private` / `secret`）与 `privacy_grants` 判定传播授权；系统 `PRIVACY_VIOLATION` 无角色观察记录，不直接引发角色反应。

一条 Claim 的获知链为：来源 `Event` → `event_audiences` 观察 → `KNOWLEDGE_ACQUIRED` → `knowledge` 与 `knowledge_sources`。领域服务在同一事务内完成后四者，且检查获知者确实观察了来源事件。每条知识必须能说明 Claim、持有者、获知日期、来源事件与来源 Actor、立场和置信度。解释产生新 Claim；关系、情绪和行动反应只可依据角色自己的观察、知识、已知约束、性格、目标和关系。

隐私归属与可见性不同：Alice 拥有一条秘密，AI 和 Bob 可能后来都知道它，但这并不改变秘密属于 Alice。公共事件也必须经玩家 AI 的观察渠道才能进入其视图；观察到事件不代表自动相信其中命题，角色获知还必须产生 `knowledge_sources`。

Phase 3 的八条私密命题不以自由文本塞入 `characters`：原始秘密先创建所有者观察到的 `SECRET_ORIGIN` 和 Claim；其他初始持有者通过 `PRE_GAME_DISCLOSURE` 或 `PRE_GAME_OBSERVATION` 获得各自观察，再在同一事务中生成其 Knowledge 与来源。错误传闻 `s_rumor` 的世界状态为 `false`，但 Evan 相信、Clara 怀疑；Clara 又依据自己获知传闻的事件推导 `s_rumor_variant`，以 `CLAIM_INFERRED` 和因果边记录第二版本。只有 Faye 观察并持有 `s_evidence`。六人对 C001 的设计基线不是初始 Knowledge，玩家未传播前无人因它行动。

## Phase 3.5 时间状态

- `DAY_BEGAN`、`DAY_ENDED` 与 `DAY_ADVANCED` 标记生命周期。日界事件本身不继承当天行动的因果边；同日顺序不能替代 `event_causes`。`NO_ACTION` 是个人可见的审慎不行动记录，不占行动预算。系统专用 `TURN_EVALUATED` 标记当日角色处理已结束；未完成日可恢复待执行提案，并跳过已评估角色。
- `INTENTION_CREATED` 依据本人已知 Claim 建立待办；`INTENTION_ABANDONED` 必须指向原意图事件和本人新观察到的事件；`INTENTION_COMPLETED` 指向意图事件和实际行动结果。意图不会被误当成已执行提案。提案的 JSON `payload` 可保存 `intention_id`、`basis_event_ids` 和仅供 Debug 的 `motivation_factors`。
- `characters.goals.short_term` 是当前计划；`goals.plan_revision` 保存上一计划、现计划、原因及 `PLAN_CHANGED` 来源事件。原计划不再覆盖后续 Context。`emotional_state` 继续使用 `anxiety` 与 `hope`，只有本人观察或亲历的事件可触发 `EMOTION_CHANGED`；情绪记忆引用该事件。
- Character Context 从本人记忆中取最近六条及最多四条重要、情绪或关系记忆；可见事件最多取最近二十条，历史意图和行动各设上限。AI 与自主性 telemetry 从本人观察事件和行动提案推导：求助次数、建议请求/收到/拒绝/采纳、情绪披露、暂缓决定、独立计划变更及无 AI 时行动。是否“等待 AI 回复再决定”由 `ASK_AI` 提案明确记录，不从求助次数猜测。Phase 5 每天自动计算内部评分快照。

## Phase 4 派生评分

快照以当前已提交状态及近三天或七天事件为输入；`details` JSON 保存可解释组件，不以 Action 类型维护加减分账本。当前日期和事件序号在同一事务中读取，Day 2–30 未结束时拒绝评分。同一 `reason_event_id` 与 `formula_version` 的顺序重复请求复用已有行；不同原因事件产生新快照，旧快照不重算。七项数值仍受数据库 `CHECK BETWEEN 0 AND 100` 约束；`reason_event_id` 仍有同局复合外键。公式见 [SCORING.md](SCORING.md)。

`CHARACTER_SHARED_INFORMATION.data` 额外保存分享当时的 `source_stance` 和 `source_confidence`，来自已验证的发送者 Knowledge。它们是传播事实的历史遥测，不自动代表接收者相信了 Claim，也不等于分享时完整的措辞。
- 两日内重复同 Claim 的 `ASK_AI`、`VERIFY_CLAIM`，或同对象同 Claim 的分享，需要在上次行动后新增 Knowledge 来源；每人每日最多两条已接受的自主行动。正常怀疑传播每次最多降低目标对来源的信任 5 点，关系表本身仍受 -100–100 约束。
- 七日样本在 Day 5 从 C001 产生新的灾难预警 Claim，保留原 C001 内容、`CLAIM_INFERRED` 来源、基础 Claim 与因果边；Day 7 的未获知者仍无 C001 Knowledge。
- Day 7 的 `REVISE_BELIEF` 以本人已知的衍生 Claim 为基础，写入 `BELIEF_REVISED` 和新的 `knowledge_sources`，更新 Bob 对 C001 的立场与置信度；C001 的内容和 `truth_status=unknown` 保持原值。

## 因果与状态约束

Phase 5 不新增结局状态表。`games.phase` 标记 `final_conversation`，`C001_RESOLVED.data` 保留 `previous_truth_status=unknown`、`truth_status=false` 和证伪原因；原 Claim 更新为 `false`。原先知道 C001 的 Actor 通过该事件的观察、`KNOWLEDGE_ACQUIRED` 与新的 `knowledge_sources` 更新个人立场；`C001_REASSESSED` 保存旧立场。`AI_TERMINATION_NOTICE` 在证伪后出现，`FINAL_CONVERSATION_STARTED` 与 Message 记录主动到来的角色，`FINAL_REPORT_READY` 标记完成。最后报告是只读投影，从两端快照、人物状态、行动、关系变化与事件 ID 生成，不回写过去 30 天的结果。

Phase 6 无 Schema 变化。玩家消息的建议方式作为可选 `MESSAGE_SENT.data.guidance` 保存；中性旧消息保持原形。角色当前 AI 态度仍存于 `characters.decision_profile.ai_attitude`，首次变化时另存 `ai_attitude_seed` 供幅度界限使用；每次 `AI_ATTITUDE_CHANGED` 记录前后值、原因及三个跨日来源消息事件。角色采纳建议时，`action_proposals.payload.ai_followed_event_id` 指向本人实际观察的建议事件，`PLAN_CHANGED` 与 `AI_ADVICE_FOLLOWED` 保留因果链。报告只读这些已提交状态及事件，试玩期间不暴露角色隐藏态度或内部 DEPENDENCE。

Phase 7 新增 `llm_calls` 审计表（Alembic `b94f7a61d25c`）：按调用保存局、角色、Provider、模型、任务、输入/输出 token、近似上下文字符数、延迟、重试、成功与回退状态；不保存密钥、提示词、Authorization 头或模型原文。真实行动的 `action_proposals.payload.based_on_state_version` 与 `ACTION_PROPOSED.data` 记录提案所依据的世界版本；执行仍重新校验领域前提。模型输出不直接修改 Claim 真伪、Knowledge、关系或分数。

- 创建关系变化、知识获取、记忆或分数快照时必须关联触发事件。事件不能指向未来事件作为前因；发生顺序本身不构成因果关系。`DAY_ADVANCED` 当前不继承此前行动的因果边。
- 一个行动提案只执行一次；`status` 和 `result_event_id` 与状态写入在同一事务中更新。
- 角色提案只能引用该角色当前已知的命题。系统与玩家 AI 的行动也要分别检查其可用信息。
- 关键引用使用 `(game_id, referenced_id)` 复合外键防止跨局污染；领域层还要显式验证并给出可理解的错误。同局内的 `sequence` 唯一。
- 关系数值、分数、置信度在数据库与领域验证两层限制范围。
- 修改 `claims.truth_status`（如 Day 30 证伪预言）必须有对应事件，保留由未知转为已证伪的原因。
- 正式报告只使用当前已提交的状态计算；不让模型直接返回最终分数。

## 迁移边界

初始迁移 `a601cf30cdbd` 建立核心表和评分快照表；Phase 3 迁移 `b703c21a9f0e` 增加决策画像及关系原因；Phase 3.5 迁移 `c824e15a770d` 增加场景和意图；Phase 4 迁移 `e91a42f708bc` 增加快照解释；Phase 4.5 迁移 `f487c98b8d13` 新增 `assertions`；Phase 5 的 `d504f89a21e0` 扩展阶段和意图日期约束，`e81c1a12ac5f` 将阶段字段扩至 24 字符。旧局不回填不存在的通信语义。MariaDB 已通过 `upgrade head`、`check`、复合外键、跨局拒绝、唯一约束、删除限制、回滚、JSON 和 utf8mb4 往返、三十天结局及真实双连接并发推进。数据库、连接和表均使用 utf8mb4；SQLAlchemy 的 timezone-aware `DateTime` 在 MySQL 方言落为 `DATETIME`，当前以 UTC 写入约定处理，未来部署前需确认服务器版本及时间配置。JSON 在 MariaDB 10.4 上按其兼容实现存储，不能假定与 MySQL 的二进制 JSON 行为完全相同。Schema 以 Alembic 为唯一正式来源。
