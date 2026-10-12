# Project MIRROR

MIRROR 是以信息边界、角色认知和事件因果链驱动的 AI 原生社会模拟游戏。当前处于 **v0.1 Phase 8：试玩打磨**。Claim、个人 Knowledge 和实际 Assertion 分开保存；MySQL 写入遵守同局行锁协议。默认游戏可从 Day 1 运行至 Day 30，经历证伪、AI 关闭、最后对话和最终报告。

设计基线：[游戏设计](docs/GAME_DESIGN.md) · [架构](docs/ARCHITECTURE.md) · [数据模型](docs/DATA_MODEL.md) · [路线图](docs/ROADMAP.md)。Phase 6 交付与限制见 [PHASE6_COMPLETION.md](docs/PHASE6_COMPLETION.md)，三局行为对照见 [PHASE6_SCENARIO_COMPARISON.md](docs/PHASE6_SCENARIO_COMPARISON.md)，真人试玩记录表见 [PHASE6_PLAYTEST_NOTES.md](docs/PHASE6_PLAYTEST_NOTES.md)。通信语义见 [COMMUNICATION.md](docs/COMMUNICATION.md)，评分见 [SCORING.md](docs/SCORING.md)，六人设计见 [CHARACTERS.md](docs/CHARACTERS.md)，机器 Seed 为 `backend/app/seeds/society_v0_1.json`。

## 目录

Phase 7 工程审查见 [PHASE7_COMPLETION.md](docs/PHASE7_COMPLETION.md)；第二次真人试玩请记录在 [PHASE8_PLAYTEST.md](docs/PHASE8_PLAYTEST.md)。

```text
backend/   FastAPI、模拟引擎、投影服务、SQLAlchemy、Alembic、测试
frontend/  Next.js App Router、TypeScript、Tailwind CSS 的最小游戏界面
docs/      已批准的 v0.1 设计基线
```

## 本地运行

需要 Python 3.12、[uv](https://docs.astral.sh/uv/)、Node.js 20.9+、npm 与 MySQL 兼容数据库。本机已在 XAMPP MariaDB 10.4.32 的独立 `mirror_dev` 库验证；空库应以 `utf8mb4` 创建。部署时先记录服务器的 `SELECT VERSION()`，再以 Alembic 建表。

```powershell
cd backend
uv sync --extra dev
$env:DATABASE_URL = 'mysql+pymysql://root@127.0.0.1:3306/mirror_dev?charset=utf8mb4'
uv run alembic upgrade head
uv run alembic check
uv run uvicorn app.main:app --reload
```

后端接口：`http://127.0.0.1:8000/health` 检查进程，`/health/database` 检查数据库连接。`POST /games` 默认创建 `temporal` 三十日六人社会；`{"scenario":"society"}` 重放 Phase 3 单日场景，`{"scenario":"phase2"}` 重放旧两人场景。前端可选六名角色私聊、明确透露 Day 1 主张、要求保密，并逐日推进至 Day 30；在最后对话后结束游戏并读取报告。消息 API 的 `confidential: true` 需与 `disclosed_claim_id` 一起提交。生产时间线只返回玩家 AI 实际观察到的事件。`GET /games/{id}/report` 仅在结束后开放。

`GET /games/{id}/advance-progress` 只返回日期、叙事阶段和已完成角色数，不暴露角色私下行动。生产时间线为玩家实际观察的事件标出关键日影响值；界面可据此停在重要事件。

另开终端启动前端：

```powershell
cd frontend
npm ci
npm run dev
```

打开 `http://localhost:3000`。

## 试玩入口

试玩时在后端启动前设置 `$env:MIRROR_PLAYTEST = '1'`，在前端启动前设置 `$env:NEXT_PUBLIC_MIRROR_PLAYTEST = '1'`。即使本机另外设置了 Debug 开关，试玩模式也不挂载后端 Debug 路由、不显示前端开发评分面板。玩家在消息框选择普通交流、直接替对方决定或支持对方自主核查；选择与实际文字一同保存，长期反复的行为才会改变人物 AI 态度。游戏 ID 保存在浏览器本地存储；关闭页面后用同一浏览器重新打开即可继续当前存档。

试玩界面提供小镇可见动态示意、限定高度的私聊与事件列表，以及「连续观察 3 天」按钮。连续观察仍逐日执行模拟，可以减少操作次数，但真实模型的等待时间仍取决于每一天的调用。Day 1 收到的末日传闻始终指向 Day 30；以后披露时会说明当前剩余天数。角色求助后系统生成的 AI 回复在私聊中标为「自动回复」。完成报告后可点「再开一局」。小镇人物位置目前是界面动画，不代表数据库中的实时地点。

体验从 Day 1 到 Day 30，结束后点击分步按钮依次查看六项分数、UNKNOWN、DEPENDENCE 和具体证据。第二次真人观察填写到 [PHASE8_PLAYTEST.md](docs/PHASE8_PLAYTEST.md)，不要用自动化结果代替试玩回答。真实模型是否启用由后端 Provider 配置决定。

## Phase 8 第二次试玩

连续观察仍逐日执行模拟，遇到玩家可见的关键事件就停下；连续安静日合并日期提示。推进时显示当前阶段和已完成角色数。报告按社会变化、六项分数、UNKNOWN、DEPENDENCE 与代表性证据逐步展开。第二次试玩使用真实 DeepSeek 并填写 [PHASE8_PLAYTEST.md](docs/PHASE8_PLAYTEST.md)；前述 Phase 6、7 表单保留历史记录。

后端 `MIRROR advance` 日志逐次记录模型调用总数、串行数、可并行的自动建议数、推进耗时及模型平均和 p95 延迟。角色当天的决策保持顺序，上一日互不依赖的 AI 建议并发生成。

## Phase 7 DeepSeek 配置

在项目根目录新建被 Git 忽略的 `.env`，参照 [.env.example](.env.example) 填写 `MIRROR_LLM_PROVIDER=deepseek`、`DEEPSEEK_API_KEY`、`DEEPSEEK_BASE_URL` 和 `DEEPSEEK_MODEL`。不要把真实密钥写入示例、源码、前端、文档或测试数据。后端启动时读取该环境文件。`MIRROR_LLM_PROVIDER=demo` 保留确定性体验；`mock` 供注入预置回复的测试使用。真人试玩建议使用单个后端 worker。

先在 `backend/` 执行 `uv run alembic upgrade head`，再运行 `uv run python -m scripts.smoke_deepseek` 做一次独立付费连通性测试。开发者可以按 A–E 顺序使用 `uv run python -m scripts.phase7_stage A`，之后将打印的 Game ID 传给 `--game-id` 继续；此脚本会发起真实付费调用，不能代替真人试玩。`MIRROR_PLAYTEST=1` 会强制关闭 Debug 路由，前端另需设置 `NEXT_PUBLIC_MIRROR_PLAYTEST=1`。

开发模式另提供 `POST /debug/llm/smoke` 与 `GET /debug/games/{game_id}/llm-usage`。后者只显示调用数、角色及任务分布、token、延迟、重试与回退；不保存提示词或密钥。付费集成测试单独以 `$env:MIRROR_RUN_PAID_TESTS = '1'` 和 `uv run pytest -q tests/test_deepseek_paid.py` 启用；普通 `pytest` 默认跳过。

SQLite 可用于快速确定性测试或本地演示：将 `DATABASE_URL` 设为 `sqlite+pysqlite:///./mirror_demo.sqlite3`。事务、约束与并发验收使用独立的 `mirror_dev` MySQL 库。

开发时可在启动后端前设置 `$env:MIRROR_DEBUG = '1'`，启用 `/debug/games/{game_id}` 查看完整事件、知识来源、记忆、关系和提案；`GET /debug/games/{game_id}/scores` 读取最新快照，`POST` 计算并保存当前快照。若还要显示前端开发评分面板，在启动前端前设置 `$env:NEXT_PUBLIC_MIRROR_DEBUG = '1'`。生产模式不挂载调试路由，也不显示面板。前端可通过 `NEXT_PUBLIC_MIRROR_API_URL` 指向其他后端地址。

## 验证

```powershell
cd backend
uv run pytest
uv run alembic upgrade head
cd ../frontend
npm run build
```

快速测试使用临时 SQLite 数据库验证领域约束、复合外键、迁移、六人知识隔离、七日因果链和评分。在线 Gate 只接受 `MIRROR_MYSQL_TEST_URL` 指向 `mirror_dev`：

```powershell
$env:MIRROR_MYSQL_TEST_URL = 'mysql+pymysql://root@127.0.0.1:3306/mirror_dev?charset=utf8mb4'
uv run pytest -q tests/test_mysql_online.py
```

数据库连接失败时 `/health/database` 返回 503。

修正后的第一条链样本见 [PHASE2_TRACE.json](docs/PHASE2_TRACE.json)。默认无明确保密要求，因此系统有隐私违规记录，Alice 不产生无依据的冲突；日期推进事件也不继承此前行动的因果边。可在 `backend/` 运行 `uv run python -m scripts.export_phase2_trace ../docs/PHASE2_TRACE.json` 重新生成；事件 ID 被规范化为 `E001` 等，Claim ID 被规范化为 `C001` 等。

六人场景见 [PHASE3_TRACE.json](docs/PHASE3_TRACE.json)。在 `backend/` 运行 `uv run python -m scripts.export_phase3_trace ../docs/PHASE3_TRACE.json` 可重建事件链；运行 `uv run python -m scripts.render_characters ../docs/CHARACTERS.md` 可从同一 Seed 重建人类设计文档。两份产物都由测试校对，避免与运行 Seed 漂移。

七日场景见 [PHASE3_5_TRACE.json](docs/PHASE3_5_TRACE.json)。在 `backend/` 运行 `uv run python -m scripts.export_phase3_5_trace ../docs/PHASE3_5_TRACE.json` 可重建每日角色状态、Knowledge Matrix、意图、行动、关系、情绪、计划和 AI 互动事实。测试逐项检查轨迹并校对文件内容。

评分校准见 [PHASE4_SCORE_TRACE.json](docs/PHASE4_SCORE_TRACE.json)。在 `backend/` 运行 `uv run python -m scripts.export_phase4_scores ../docs/PHASE4_SCORE_TRACE.json` 可重建 Day 1/3/5/7 的七项指标、组件、六人贡献、证据和变化量。测试逐项校对；普通玩家视图不展示这些内部信息。

Phase 4.5 的确定性通信样本见 [PHASE4_5_COMMUNICATION_TRACE.json](docs/PHASE4_5_COMMUNICATION_TRACE.json)；在 `backend/` 运行 `uv run python -m scripts.export_phase4_5_trace ../docs/PHASE4_5_COMMUNICATION_TRACE.json` 可重建。TRUTH v0.1 历史样本保持原值，v0.2 为新默认公式。

Phase 5 的静默、高干预与负责任玩家样本见 [PHASE5_TRACE.json](docs/PHASE5_TRACE.json)；在 `backend/` 运行 `uv run python -m scripts.export_phase5_trace` 重建。文件包含 Day 1–30 知识演化、完整静默事件链、两端分数、结局关系图、事件量、查询数和 Day 30 上下文大小。

Phase 6 的三局行为对照见 [PHASE6_SCENARIO_COMPARISON.md](docs/PHASE6_SCENARIO_COMPARISON.md) 与 [JSON](docs/PHASE6_SCENARIO_COMPARISON.json)；在 `backend/` 运行 `uv run python -m scripts.export_phase6_comparison` 重建。记录 Day 10–29 重复审计、七项结局分数、人物级自主/依赖、AI 使用、真人传播、态度、计划、关系和实际事件依据。

**MySQL Online Gate：本地通过。** `SELECT VERSION()` 为 MariaDB 10.4.32；`alembic upgrade head`、`alembic check`、复合外键、跨局拒绝、唯一性、回滚、JSON/utf8mb4 往返、持久化、行锁、两连接并发推进及完整三十天结局均已验证。云服务器数据库类型和大版本尚待部署前记录。当前 Alembic head 为 `b94f7a61d25c`；Schema 只由 Alembic 演化，不依赖开发库 dump。

