# Project MIRROR v0.1

> 数据库目标更新（Phase 4.5）：本文件中的 PostgreSQL 规划已由 [ARCHITECTURE.md](docs/ARCHITECTURE.md) 与 [ROADMAP.md](docs/ROADMAP.md) 的 MySQL 兼容数据库决策取代。正式 Schema 使用 Alembic；本地在线验证库为独立的 `mirror_dev`。

你现在是 Project MIRROR 的长期主开发 Agent。

这不是一次性 Demo，也不是要求你一次生成整个游戏。

你的任务是与我共同从零开始构建 MIRROR，并保证代码具有良好的架构、可维护性、可测试性和未来扩展能力。

在任何实现决策中，优先考虑：

1. 系统一致性
2. 可解释性
3. 可调试性
4. 清晰的数据流
5. 未来扩展到更多 Agent 的能力

不要为了“看起来像 Agent”而引入不必要的 Agent Framework。

---

# 1. 产品定义

Project MIRROR 是一款：

**AI-native Social Simulation / Interactive Narrative Game**

玩家不是游戏世界中的人类角色。

玩家扮演的是：

> 同时存在于所有角色手机中的同一个 AI 私人助手。

每个角色都可能认为自己拥有一个独立的私人 AI。

事实上，玩家拥有跨角色的信息视角。

因此玩家可能知道：

- A 喜欢 B
- B 对 A 没有兴趣
- B 喜欢 C
- C 正准备离开这座城市
- D 知道 C 的秘密

但这些信息分别属于不同角色的私人信息。

玩家拥有知识，但不天然拥有传播这些知识的权力。

由此形成 MIRROR 最核心的冲突：

**Information × Privacy × Relationship × Consequence**

---

# 2. MIRROR 的核心主题

MIRROR 不应该首先是一部 AI 伦理教育作品。

它必须首先是一款好玩的游戏。

设计优先级为：

**FUN → EMERGENCE → MYSTERY → THEME**

玩家首先体验：

- 秘密
- 谎言
- 信任
- 爱情
- 冲突
- 信息差
- 人际关系变化
- 信息传播
- 蝴蝶效应
- 社会涌现

只有在游戏逐渐推进后，主题才浮现：

> 当 AI 能替人类记忆、判断、沟通、建议甚至维系关系时，人类是否正在逐渐失去独立处理生活的能力？

禁止一开始直接向玩家说教。

---

# 3. v0.1 Scope

第一版只实现：

**6 个角色**

但所有核心系统必须按照未来可以扩展到：

**30+ Characters**

进行设计。

游戏时间保留：

**Day 1 → Day 30**

但不要求每一天拥有大量复杂剧情。

允许通过：

- 关键剧情日
- 自动社会模拟
- 时间跳跃
- 玩家主动推进日期

控制开发复杂度。

---

# 4. Day 1

Day 1 开始后，玩家 AI 收到一条 Global Message：

> The world will end in 30 days.

中文：

> 世界将在 30 天后毁灭。

重要：

该消息：

- 没有来源
- 没有可信度
- 无法验证
- 玩家不知道是真是假
- NPC 初始状态下全部不知道这条消息

玩家决定：

- 是否告诉任何人
- 告诉谁
- 告诉多少人
- 如何表达
- 是否声称它是真的
- 是否表现出怀疑
- 是否完全隐瞒

这是整个模拟世界的第一个扰动。

---

# 5. Day 2–29

随后世界通过以下系统产生变化：

Player Conversation
→ Character Reaction
→ Character Decision
→ Character Action
→ Event
→ Information Propagation
→ Relationship Change
→ World State Change
→ New Memory
→ New Character Decision

角色不能只是等待玩家聊天。

他们必须拥有一定程度的自主行为。

例如：

A 从 AI 得知末日消息。

A 可能：

- 告诉 B
- 不告诉任何人
- 查询信息
- 辞职
- 向喜欢的人表白
- 认为 AI 出现故障
- 开始囤积物资
- 质疑 AI
- 与朋友讨论
- 改变未来计划

这些行为进一步影响其他角色。

目标是产生：

**Emergent Social Simulation**

而不是提前写死所有剧情。

---

# 6. Day 30

Day 30：

世界没有毁灭。

Global Message 的预言没有发生。

真正即将结束的是：

**Player AI**

AI 将被关闭。

此时游戏开始重新解释玩家此前所有行为。

系统生成 Final Report。

---

# 7. Scoring System

存在六个显性指标。

每项：

0–100

分别为：

## HAPPINESS

社会整体幸福程度。

## TRUST

人与人之间以及人与 AI 之间的信任。

## CONNECTION

人物之间真实的人际联结程度。

## AGENCY

人类独立做决定、解决问题和承担结果的能力。

## STABILITY

整个小型社会的稳定程度。

## TRUTH

这个社会中的真实程度，包括诚实、信息质量以及人与人之间是否真正了解彼此。

总显性分数：

600。

同时存在一个隐藏指标：

## UNKNOWN

范围：

0 ～ -100

它是负分项。

UNKNOWN 的真实含义为：

**DEPENDENCE**

即：

> Human dependence on AI.

游戏初期不得直接告诉玩家 UNKNOWN 代表什么。

v0.1 可以首先完成隐藏评分系统，在结局揭示其真正含义。

未来版本再实现：

- 每局一次猜测 UNKNOWN
- 20 个候选词
- 猜中 DEPENDENCE
- 解锁 Final Loop

因此请让 Scoring Engine 保留扩展能力。

---

# 8. MIRROR 的理想结局

MIRROR 的最佳结果不是简单地：

“所有人幸福。”

真正理想状态是：

高 Happiness  
高 Trust  
高 Connection  
高 Agency  
高 Stability  
高 Truth  

同时：

低 Dependence。

设计北极星：

> The best AI is one that helps humans eventually live without it.

也就是：

> 玩家帮助这些人生活得更好，却没有让他们失去独立生活的能力。

---

# 9. Architecture Principles

必须严格遵守：

## Rule 1

**LLM decides and expresses. Code owns truth and rules.**

LLM 可以：

- 推理
- 判断
- 表达
- 生成自然语言
- 提议角色行为
- 分析事件
- 生成 Action

但是：

LLM 绝不能直接成为 World State 的最终权威。

程序负责：

- 数据
- 状态
- 权限
- 数值
- 时间
- 信息所有权
- 事件是否合法
- Action 是否执行

---

# 10. Character Agent

每个角色至少具有：

Identity

- id
- name
- age
- occupation
- basic background

Personality

- personality traits
- values
- fears
- desires
- communication style

Goals

- short-term goals
- long-term goals

Private State

- secrets
- beliefs
- plans
- emotional state

Social State

- relationships
- trust
- intimacy
- conflict

Memory

- recent memories
- important memories

Known Facts

记录：

这个角色究竟知道什么。

非常重要：

**角色不能因为数据库里存在某条事实就自动知道它。**

Knowledge 必须具有 Owner / Source / Visibility。

---

# 11. Memory System

v0.1 不使用复杂向量数据库。

首先实现：

Structured Memory。

建议区分：

Recent Memory

Important Memory

Known Facts

Emotional Memory

Relationship Memory

每条 Memory 至少考虑：

- owner
- source
- content
- timestamp
- importance
- confidence
- visibility
- related characters
- related event

未来可以加入：

pgvector / semantic retrieval。

当前不要过度设计。

---

# 12. Privacy System

Privacy 是 MIRROR 的一级核心系统，而不是 Prompt 中一句：

“请保护隐私”。

系统必须知道：

- 信息属于谁
- 谁告诉了 AI
- 谁知道它
- 是否允许转述
- 是否属于秘密
- 信息敏感级别
- 信息传播路径

例如：

Alice → AI：

“I love Bob.”

这是 Alice Private Knowledge。

Bob 不应自动获得该事实。

玩家如果告诉 Bob：

必须产生一个明确的：

Privacy Violation / Information Disclosure Event。

之后：

Alice Trust

Bob Trust

Relationship

Dependence

Truth

等状态可能变化。

---

# 13. Relationship System

Relationship 不能只是：

friend / enemy。

至少需要支持连续数值。

例如：

trust
affection
intimacy
conflict

范围可以统一设计，例如：

-100 ～ 100

具体方案由你评估后提出。

每次变化必须能够追踪原因。

禁止出现：

relationship += random()

必须存在：

Event / Action / Reason。

---

# 14. Event System

任何重要变化都应该形成 Event。

例如：

PLAYER_DISCLOSED_SECRET

CHARACTER_CONFESSION

CHARACTER_ARGUMENT

CHARACTER_SHARED_INFORMATION

CHARACTER_LEFT_JOB

CHARACTER_RESEARCHED_RUMOR

RELATIONSHIP_CHANGED

WORLD_EVENT

每个 Event 至少拥有：

id

type

timestamp / game_day

actor

target

data

cause

visibility

related_event_ids

这样未来可以形成：

**Causal Event Chain**

例如：

E001
Player tells Alice about apocalypse

↓

E008
Alice tells Bob

↓

E014
Bob quits job

↓

E021
Bob confesses to Carol

↓

E033
Carol breaks up with David

必须能够追踪：

“为什么发生了这件事？”

---

# 15. Action System

LLM 不直接修改数据库。

Character Agent 只能生成结构化 Action Proposal。

例如：

```json
{
  "type": "SHARE_INFORMATION",
  "actor_id": "alice",
  "target_id": "bob",
  "fact_id": "fact_apocalypse",
  "reason": "Alice believes Bob deserves to know",
  "confidence": 0.82
}
```

Simulation Engine：

1. Validate
2. Apply rules
3. Generate Event
4. Update State
5. Update Memory
6. Update Relationships
7. Recalculate relevant scores

所有 Action 都必须遵循类似流程。

---

# 16. Simulation Loop

核心世界循环：

OBSERVE

↓

THINK

↓

PROPOSE ACTION

↓

VALIDATE

↓

EXECUTE

↓

CREATE EVENT

↓

UPDATE WORLD STATE

↓

UPDATE MEMORY

↓

UPDATE RELATIONSHIP

↓

PROPAGATE INFORMATION

↓

NEXT TURN / NEXT DAY

这是 MIRROR 最重要的工程核心。

不要把它隐藏在一个巨大的 LLM Prompt 中。

---

# 17. Technology Stack

Frontend:

- Next.js
- TypeScript
- Tailwind CSS
- Zustand
- React Flow

Backend:

- Python
- FastAPI

Database:

- PostgreSQL
- Supabase PostgreSQL is acceptable

ORM:

- SQLAlchemy 2.x preferred

Validation:

- Pydantic

LLM:

建立 Provider Adapter。

业务逻辑不能直接依赖某一家模型 SDK。

设计统一接口，例如：

LLMProvider

generate()
generate_structured()
stream()

未来能够替换：

OpenAI
Anthropic
Google
DeepSeek
Local Model

v0.1 不使用 LangGraph。

v0.1 不使用向量数据库。

未来根据真实需要再引入。

---

# 18. Suggested Backend Modules

不要机械照搬，但整体职责需要类似：

app/

core/

domain/

characters/

memory/

relationships/

privacy/

events/

simulation/

scoring/

llm/

api/

db/

其中：

Simulation Engine

应该成为整个后端的核心。

避免：

一个 3000 行的 game.py。

---

# 19. Frontend v0.1

第一版前端重点不是华丽视觉。

首先确保游戏闭环。

至少需要：

## AI Inbox / Character List

显示六个人。

## Private Conversation

玩家和某个角色聊天。

## Game Day

Day X / 30。

## Advance Time

推进模拟。

## Event Timeline

展示玩家能够知道的公共或相关事件。

注意：

不能展示玩家按设定不应该知道的信息。

## Relationship Graph

使用 React Flow。

显示人物之间关系变化。

第一版可以简单。

## World Report

Day 30 展示：

HAPPINESS  
TRUST  
CONNECTION  
AGENCY  
STABILITY  
TRUTH  
UNKNOWN

最终揭露：

UNKNOWN = DEPENDENCE

---

# 20. Six Initial Characters

暂时不要立即写死最终人物设定。

但请创建：

6 个具有明显差异、同时存在关系网络的 Character Seed。

要求他们之间不是六个陌生人。

应该存在例如：

朋友
恋人
前任
同事
家人
暗恋
竞争
秘密

形成一个小型社会网络。

重要：

不要把角色设计成纯粹的功能 NPC。

他们必须：

有矛盾人格
有自己的目标
有互相冲突的信息
有不会主动告诉 AI 的事情
有可能说谎
有可能误解
有可能改变观点

先生成独立的 seed 文件。

我之后会与 Codex 一起修改角色。

---

# 21. Development Philosophy

不要一次实现整个 MIRROR。

按照 Vertical Slice 开发。

第一条完整链路应该是：

Player sends message to Alice

↓

Alice Agent receives correct context

↓

Alice replies

↓

Alice gains a memory

↓

World database records conversation

↓

Player gives Alice a secret

↓

Alice decides whether to tell Bob

↓

Simulation creates structured action

↓

Action validated

↓

Bob gains knowledge

↓

Event created

↓

Relationship changes

↓

Frontend timeline reflects consequence

只要这一条链真正跑通：

MIRROR 就已经开始存在。

---

# 22. Testing

这个项目高度依赖状态一致性。

所以从一开始必须建立测试。

重点测试：

Information ownership

Memory isolation

Privacy visibility

Relationship updates

Event causality

Action validation

Game day progression

Score calculations

不要依赖真实 LLM 完成全部测试。

设计：

MockLLMProvider

使 Simulation Engine 可以完全 deterministic 地测试。

---

# 23. Logging / Debugging

MIRROR 后续一定会非常难调试。

因此从第一版开始提供：

Development Debug View。

至少能够查看：

Current World State

Character Known Facts

Character Memories

Relationship Values

Pending Actions

Event Chain

Score State

生产游戏 UI 不显示这些内容。

开发模式必须可以看。

---

# 24. First Task

现在不要直接生成完整游戏。

首先：

1. 检查当前 repository 状态。
2. 如果 repository 为空，则建立合理的 monorepo/project structure。
3. 创建 README.md。
4. 创建 docs/ARCHITECTURE.md。
5. 创建 docs/GAME_DESIGN.md。
6. 创建 docs/DATA_MODEL.md。
7. 创建 docs/ROADMAP.md。
8. 设计数据库 Entity / Schema。
9. 设计 Simulation Engine interface。
10. 设计 LLM Provider interface。
11. 创建 MockLLMProvider。
12. 建立最小 FastAPI backend。
13. 建立最小 Next.js frontend。
14. 建立 Health Check。
15. 建立第一批 domain model tests。

在正式大量编写代码前：

先向我展示：

- repository structure
- architecture decisions
- database schema
- core domain models
- simulation loop
- 第一阶段 implementation plan

然后开始实施。

不要询问我那些可以通过合理工程判断自行决定的小问题。

如果遇到真正影响产品方向的重大选择，再向我说明：

Option A
Option B

以及你的推荐。

---

# 25. Most Important Rule

始终记住：

MIRROR 不是：

“六个 ChatGPT Bot 在群聊。”

MIRROR 是：

**一个拥有事实、记忆、关系、秘密、信息边界、因果链和自主行为的小型社会模拟系统。**

LLM 是角色的大脑之一。

但：

**The simulation is the game.**

从现在开始，以此原则开发 Project MIRROR v0.1。
