# F4-E 发布前检查清单（Phase 4 — 发布收尾与版本冻结）

**性质：材料准备与核对清单，不是发布执行记录。** 本文件由 F4-E 材料准备
session 创建（2026-11-16），列出发版前必须逐项确认的内容。**任何一项未完成
前，不打 tag、不发布、不改版本号。** 执行时在每项前打 ✅ 并填上证据
（commit / run 链接 / 文件路径）。

上游依据：[`phase4-plan.md`](phase4-plan.md) §F4-E（目标与最终验收）；
版本现状参考 [`release-checklist.md`](release-checklist.md)（v0.2.0–v0.2.1
的历史清单，格式沿用）。

---

## 0. 前置门槛（全部满足才允许进入本清单后续步骤）

| # | 检查项 | 证据 |
|---|---|---|
| 0.1 | main 分支 CI 全绿（lint / unit / integration / web-e2e / desktop-package） | 待填：最近一次 green run 链接 |
| 0.2 | F4-D 视觉 QA / 可访问性 / 响应式 / bundle 审计完成，严重问题清零（暂留问题写入冻结报告） | 待填：审计记录链接 |
| 0.3 | web 全量 Playwright e2e 在干净环境通过（历史参考：122 项，99+6+17 的失败全部由端口占用引起，已修复；以最新全量结果为准） | 待填：e2e 结果记录 |
| 0.4 | 版本冻结 commit 已确定，所有下述文件指向同一 commit | 待填：commit hash |

## 1. 版本号统一（五处必须一致）

以下五处当前均为 `0.2.1`；若本次发版定名 v0.2.2，则五处**同一次 commit** 内
改为同一值，然后才允许打 tag。**改动本身属于源码改动，须由主会话在冻结时
执行，本清单只负责核对一致性。**

| # | 位置 | 当前值 | 冻结值 | 核对方式 |
|---|---|---|---|---|
| 1.1 | `desktop/package.json` → `"version"` | 0.2.1 | 待定 | `grep '"version"' desktop/package.json` |
| 1.2 | `web/package.json` → `"version"` | 0.2.1 | 待定 | `grep '"version"' web/package.json` |
| 1.3 | `pyproject.toml` → `version` | 0.2.1 | 待定 | `grep '^version' pyproject.toml` |
| 1.4 | `src/opspilot/__init__.py` → `__version__` | 0.2.1 | 待定 | `python -c "import opspilot; print(opspilot.__version__)"` |
| 1.5 | `src/opspilot/api/routers/health.py` → `_VERSION`（`/health` 返回值） | 0.2.1 | 待定 | `curl -s localhost:8000/health`（包内集成测试断言跟随包版本，见 763c128 提交说明） |

附带核对（不要求五处一致，但要求**有意识**）：

| # | 位置 | 现状 | 处理 |
|---|---|---|---|
| 1.6 | `src/opspilot/api/app.py:134` FastAPI `version="0.2.0"` | 落后于包版本 | 冻结时决定：跟随包版本或移除该字面量（OpenAPI `info.version`）；不要无声跳过 |
| 1.7 | [`docs/api-contract.md`](api-contract.md) §health 示例仍写 `"0.1.0"` | 文档示例滞后 | 冻结时随版本号一并更新示例 |
| 1.8 | `desktop/package-lock.json`、`web/package-lock.json` 内嵌版本 | 随各自 package.json | 重装/重锁后核对 lockfile 顶层 version 字段 |

## 2. 截图与演示材料更新（HeroUI 改版后）

README 目前在两处明示「截图待更新」（顶部 Phase 4 状态段、`Watch it run`
小节）。发布前必须消除该状态：**重拍并原地替换，不虚构、不保留旧图混发。**

| # | 检查项 | 说明 |
|---|---|---|
| 2.1 | `docs/assets/golden-path.gif` 在新 UI 上重录 | 同一 fake-provider 场景、同一流程（/ready → 提交工单 → WAITING_APPROVAL → 批准 → 幂等执行）；保持原尺寸/体积量级 |
| 2.2 | `docs/assets/screenshots/01–10` 十张截图重拍 | 同一 fixture、同一 viewport、同一点击顺序；覆盖 dashboard、knowledge 索引、工单提交/受理、run 停在审批、审批中/已决、退款入账审计、runs 列表 |
| 2.3 | README 中对应 alt 文本核对 | 新 UI 下时间线步数、引用文档数、状态文案若变化，alt 文本同步改（以实际截图内容为准，不沿用旧数字） |
| 2.4 | 演示视频 `web/public/showcase/demo-path-{a,b,c}.mp4` | 若 F4-D 后 UI 变化影响画面，重录；若仅色板微调且卡片披露仍在，可保留并在此记录决定 |
| 2.5 | 移除 README 两处「截图待更新」标注 | 仅在 2.1–2.4 完成后执行 |
| 2.6 | 新旧截图 before/after 留档 | F4-D 审计要求保留对比；存 `docs/assets/` 或审计记录引用的路径 |

## 3. e2e 与测试

| # | 检查项 | 证据 |
|---|---|---|
| 3.1 | Python 全量 pytest 通过（SQLite 默认；postgres 标记跳过数已打印） | 待填 |
| 3.2 | `ruff check` + `ruff format --check` 裸跑通过（四棵树：src/tests/evals/mcp_servers） | 待填 |
| 3.3 | web 单测 + lint 通过 | 待填 |
| 3.4 | web e2e golden path（fake provider）：待审批、批准完成、拒绝、过期、失败、空数据六态 | 待填 |
| 3.5 | e2e 断言 UI 文案与 API/数据库事实一致（F4-B 验收项延续） | 待填 |
| 3.6 | 审批 API、幂等、安全（registry literal、409、401）测试全部保持通过 | 待填 |
| 3.7 | `prefers-reduced-motion`、键盘可达、窄屏（1280/1024/768/390）走查记录 | 待填（可引用 F4-D 产出） |

## 4. 安装包重建与走查

| # | 检查项 | 说明 |
|---|---|---|
| 4.1 | 从冻结 commit 干净 checkout 重建 web、desktop、installer | `desktop-package` CI job 产物或本地构建；记录产物体积与 SHA-256 |
| 4.2 | 打包内 Compose 与源码一致 | 逐字节对比（沿用 v0.2.1 做法） |
| 4.3 | 包内无凭据扫描 | no-token scan 通过 |
| 4.4 | 独立走查：安装 → 启动 → 索引 → 工单 → 审批 → 模拟退款 → 重复审批（409）→ 错误状态 → 退出 → 重启数据保留 | 由非构建者执行；记录归档 `docs/installer-verification.md` |
| 4.5 | 升级路径：从 v0.2.1 override 安装到新版本 | 版本化 image tag 生效，无旧安装产物混入（v0.2.1 修复的回归点） |
| 4.6 | 端口占用场景复核 | shell 跟随 `OPSPILOT_API_HOST_PORT` |
| 4.7 | 冷启动 `/ready` 时间记录 | 参考基线：v0.2.1 走查为 70 s |

## 5. README 与文档一致性

| # | 检查项 | 说明 |
|---|---|---|
| 5.1 | README 顶部状态段更新 | Phase 4 完成后，将「in progress / screenshots pending」改为定稿表述；release 链接补本版本 |
| 5.2 | `The desktop installer` 小节指向新 Release | 文件名、字节数、SHA-256 与实际 asset 一致 |
| 5.3 | 版本相关示例更新 | `api-contract.md` §health 示例、`app.py` OpenAPI version（与 §1 决定一致） |
| 5.4 | 技术栈描述核对 | 架构图与「Try it」路径无需变化；若 README 其他位置提及 UI 库/样式，确认与 HeroUI 3.2.7 现状一致 |
| 5.5 | [`docs/progress.md`](progress.md) 增补 Phase 4 结果 | 如实记录：完成项、F4-D 审计结论、暂留问题 |
| 5.6 | `docs/phase4-closeout.md` 生成（phase4-plan §F4-E 要求的冻结后唯一状态说明，文件尚未创建） | 内容：已验证 / 未验证 / 已知限制 / 重开条件（真实用户采用、明确缺陷、真实客户需求、安全问题） |
| 5.7 | release notes 定稿 | [`release-notes-v0.2.2-draft.md`](release-notes-v0.2.2-draft.md) 去掉 DRAFT 标记与「待更新」项，版本号按 §1 定稿 |

## 6. 发布动作（全部核对完毕后，由仓库所有者执行）

| # | 动作 | 说明 |
|---|---|---|
| 6.1 | 冻结 commit + 打 annotated tag `v0.2.x` | tag 与五处版本号同 commit |
| 6.2 | push tag，等待 tag CI 绿 | 参考历史：v0.2.0 tag run success |
| 6.3 | 创建 GitHub Release，正文用定稿 release notes | 語气沿用 v0.2.1：只写已验证内容 |
| 6.4 | 上传 installer asset，并在 Release 正文回填字节数与 SHA-256 | `gh release upload v0.2.x <file>` |
| 6.5 | （可选）若演示视频重录，更新 `docs/video-upload-kit.md` 清单 | 上传平台仍按用户既定决定 |

## 7. 明确不在本清单范围

- 真实模型重测（tool selection 门槛）—— 属于独立的 v0.3.0 迭代，
  见 [`model-retest-plan-v0.3.0.md`](model-retest-plan-v0.3.0.md)；不阻塞本发布。
- 外部平台作品集发布 —— 用户既定：另行安排。
- 任何源码、测试、权限或 API 行为改动 —— 本阶段只允许版本号文件、文档与
  截图资产变更。
