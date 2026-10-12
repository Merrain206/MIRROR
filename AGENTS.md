# MIRROR 项目开发约定

- 开始改动前阅读 `docs/GAME_DESIGN.md`、`docs/ARCHITECTURE.md`、`docs/DATA_MODEL.md` 和 `docs/ROADMAP.md`；六人设计见 `docs/CHARACTERS.md`。
- `backend/app/seeds/society_v0_1.json` 是已批准的六人行为基线。未经用户明确调整，不改动六人的性格、目标、初始知识、关系、秘密或 AI 态度。
- 代码和数据库拥有世界真相与规则；LLM Provider 只生成表达、立场和行动提案。角色只依据本人实际观察事件、Knowledge、记忆、关系与目标行动。玩家时间线只读玩家 AI 的观察投影。
- 隐私违规允许作为游戏行动执行，但必须记录传播路径、所有者、授权判断和可追溯后果。系统审计事件不会自动成为角色知识。
- 当前默认 `temporal` 场景运行 Day 1–30，并在 Day 30 进入结局；`society` 和 `phase2` 是回归场景。可通过 `MIRROR_LLM_PROVIDER=deepseek` 接入真实模型；领域规则、六人 Seed、评分维度及玩家可见投影仍由代码与数据库控制。Phase 8 只打磨玩家呈现与等待体验，不新增核心系统；普通测试不调用付费 API，第二次真实模型真人试玩完成前不得宣布 Phase 8 通过。
- 人物文档由 `backend/scripts/render_characters.py` 生成；各阶段轨迹由 `backend/scripts/export_phase3_5_trace.py`、`backend/scripts/export_phase4_scores.py`、`backend/scripts/export_phase4_5_trace.py`、`backend/scripts/export_phase5_trace.py`、`backend/scripts/export_phase6_comparison.py` 生成。改动相关规则后重建产物并运行测试。
- 正式数据库目标为 MySQL 兼容数据库，SQLite 仅用于快速确定性测试。本地 `mirror_dev` 已在 MariaDB 10.4.32 通过在线 Gate；部署前须记录云服务器 vendor/大版本并复验。所有正式 Schema 变更通过 Alembic；同局 mutation 持有 `Game` 行的 `SELECT ... FOR UPDATE` 锁，直至事务提交。
- 后端测试：在 `backend/` 执行 `uv run pytest`；前端构建：在 `frontend/` 执行 `npm run build`。本地运行与环境变量见根目录 `README.md`。
