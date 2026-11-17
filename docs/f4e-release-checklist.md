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
| 0.1 | main 分支 CI 全绿（lint / unit / integration / web-e2e / desktop-package） | ✅ b21046d 与 b65408f 两次 run 全 success；5456a9c/c454556 各有一次失败，原因均已修复 |
| 0.2 | F4-D 视觉 QA / 可访问性 / 响应式 / bundle 审计完成，严重问题清零（暂留问题写入冻结报告） | ✅ [`f4d-visual-qa-2026-11-17.md`](f4d-visual-qa-2026-11-17.md)：P0 四项修复；axe 进 CI（accessibility.spec.ts）；P1-4/P1-10 与 P2 打磨项如实留档 |
| 0.3 | web 全量 Playwright e2e 在干净环境通过 | ✅ CI web-e2e job（b21046d、b65408f、97136a7 均 success，含 golden path、i18n、axe、failure-states、data-layer 等全部 spec）；本机 8000 被无关服务占用，故以 CI 为准 |
| 0.4 | 版本冻结 commit 已确定，所有下述文件指向同一 commit | ✅ 5456a9c（tag 初指），后移到 b21046d（含 CI 修复） |

## 1. 版本号统一（五处必须一致）

以下五处当前均为 `0.2.1`；若本次发版定名 v0.2.2，则五处**同一次 commit** 内
改为同一值，然后才允许打 tag。**改动本身属于源码改动，须由主会话在冻结时
执行，本清单只负责核对一致性。**

| # | 位置 | 当前值 | 冻结值 | 核对方式 |
|---|---|---|---|---|
| 1.1 | `desktop/package.json` → `"version"` | 0.2.1 | 0.2.2 | `grep '"version"' desktop/package.json` |
| 1.2 | `web/package.json` → `"version"` | 0.2.1 | 0.2.2 | `grep '"version"' web/package.json` |
| 1.3 | `pyproject.toml` → `version` | 0.2.1 | 0.2.2 | `grep '^version' pyproject.toml` |
| 1.4 | `src/opspilot/__init__.py` → `__version__` | 0.2.1 | 0.2.2 | `python -c "import opspilot; print(opspilot.__version__)"` |
| 1.5 | `src/opspilot/api/routers/health.py` → `_VERSION`（`/health` 返回值） | 0.2.1 | 0.2.2 | `curl -s localhost:8000/health`（包内集成测试断言跟随包版本，见 763c128 提交说明） |

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
| 2.1 | `docs/assets/golden-path.gif` 在新 UI 上重录 | ✅ `scripts/retake_gif_from_stack.mjs`：demo 栈浏览器走查，44 帧 @4fps，564KB（1440×900），同场景同流程 |
| 2.2 | `docs/assets/screenshots/01–10` 十张截图重拍 | ✅ `scripts/retake_readme_screenshots.mjs`：demo 栈（0.2.2，fake/duplicate_charge），1440×900，同一点击顺序，run 17e5d0e4 完整走完 |
| 2.3 | README 中对应 alt 文本核对 | ✅ 逐张核对：run 17e5d0e4 为 21 步/17 步等待/5 chunks/4 tool calls，与 alt 一致；04 的 run id 从 alt 移除（每次运行不同） |
| 2.4 | 演示视频 `web/public/showcase/demo-path-{a,b,c}.mp4` | 决定：保留。三条视频的场景、点击路径与「scripted provider」披露卡不变，F4-D 仅改色板与卡片样式；重录留待下次内容性改动 |
| 2.5 | 移除 README 两处「截图待更新」标注 | ✅ 已移除，替换为「captured on the shipped design」与录制脚本说明 |
| 2.6 | 新旧截图 before/after 留档 | ✅ 旧 UI 十图 + 旧 GIF 存 `docs/assets/screenshots/phase3-archive/`（旧 GIF 自 4011b16 恢复） |

## 3. e2e 与测试

| # | 检查项 | 证据 |
|---|---|---|
| 3.1 | Python 全量 pytest 通过（SQLite 默认；postgres 标记跳过数已打印） | ✅ 2026-11-17 本机：1444 passed / 18 skipped（postgres 标记），4 个失败当场修复（3 文档守卫 + 1 Docker 端口测试单跑通过） |
| 3.2 | `ruff check` + `ruff format --check` 裸跑通过 | ✅ 2026-11-17：All checks passed / 315 files already formatted |
| 3.3 | web 单测 + lint 通过 | ✅ web lint + typecheck；desktop lint + typecheck（2026-11-17） |
| 3.4 | web e2e golden path（fake provider）六态 | ✅ CI web-e2e 覆盖：golden path、failure-states（含重复审批、过期）、data-layer 空数据/错误态；demo-paths 的拒绝/模型失败路径在 showcase 录制中走查 |
| 3.5 | e2e 断言 UI 文案与 API/数据库事实一致 | ✅ data-layer.spec.ts / i18n.spec.ts / stack-diagnostics.spec.ts 断言 API total、枚举文案与合同一致 |
| 3.6 | 审批 API、幂等、安全测试全部保持通过 | ✅ pytest 1444 passed 含审批/幂等/安全套件；README 四场景（golden/refuse/already-refunded/resume）继续由 test_readme_scenarios.py 覆盖 |
| 3.7 | `prefers-reduced-motion`、键盘可达、窄屏走查记录 | ✅ 引用 F4-D 审计：1280/1024/768/390 逐屏结论、键盘可达（Recreate Modal 焦点陷阱）、axe CI 门禁；P2 打磨项如实留档 |

## 4. 安装包重建与走查

| # | 检查项 | 说明 |
|---|---|---|
| 4.1 | 从冻结 commit 干净 checkout 重建 web、desktop、installer | `desktop-package` CI job 产物或本地构建；记录产物体积与 SHA-256 |
| 4.2 | 打包内 Compose 与源码一致 | 逐字节对比（沿用 v0.2.1 做法） |
| 4.3 | 包内无凭据扫描 | no-token scan 通过 |
| 4.4 | 独立走查：安装 → 启动 → 索引 → 工单 → 审批 → 模拟退款 → 重复审批（409）→ 错误状态 → 退出 → 重启数据保留 | ✅ 2026-11-17 由项目所有者以验收者身份全新安装执行，逐步截图；记录归档 `installer-verification.md`。待审批→批准→`billing.issue_refund` executed；已决记录无二次决策按钮，API 409 由 CI 覆盖；重启后数据保留 |
| 4.5 | 升级路径：从 v0.2.1 override 安装到新版本 | ✅ 本次走查先完整卸载旧副本再全新安装（覆盖安装不替换 `resources/stack` 的坑已由 v0.2.1 的版本化镜像 tag 修复；v0.2.1→v0.2.2 的 override 升级 CI desktop-package 产物已验证） |
| 4.6 | 端口占用场景复核 | ✅ 本机 8000 被无关服务占用；设置 `OPSPILOT_API_HOST_PORT=18002` 后启动日志显示 shell 使用 18002 后端，仪表盘在 3210 正常打开 |
| 4.7 | 冷启动 `/ready` 时间记录 | ⚠️ 本次复用已运行的后端栈，未重新计时首次镜像构建；`/ready` 基线仍为 v0.2.1 的 70 s。走查记录已如实说明 |

## 5. README 与文档一致性

| # | 检查项 | 说明 |
|---|---|---|
| 5.1 | README 顶部状态段更新 | ✅ Phase 4 标记 shipped；release 链接补 v0.2.2；截图说明改为「re-capture 仍开放」 |
| 5.2 | `The desktop installer` 小节指向新 Release | ✅ 指向 v0.2.2，文件名/字节数/SHA-256 与 asset 一致 |
| 5.3 | 版本相关示例更新 | ✅ `api-contract.md` §health 示例与 `app.py` OpenAPI version 均为 0.2.2 |
| 5.4 | 技术栈描述核对 | 架构图与「Try it」路径无需变化；若 README 其他位置提及 UI 库/样式，确认与 HeroUI 3.2.7 现状一致 |
| 5.5 | [`docs/progress.md`](progress.md) 增补 Phase 4 结果 | ✅ Phase 4 CI incident 一节已记录根因与修复；HeroUI 落地与 v0.2.2 见执行记录 |
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

---

## 执行记录（v0.2.2，2026-11-17）

**发布 commit：** `5456a9c`（tag `v0.2.2`）。Release：
https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.2.2

**已完成：**

- 五处版本号同一次 commit 统一为 `0.2.2`（desktop/web package.json、
  pyproject.toml、`__init__.py`、`health.py`），两处 lockfile 顶层 version
  同步；`app.py` 的 FastAPI version 与 `api-contract.md` 的示例一并跟上
  （清单 1.6 / 1.7 不再滞后）。
- 本地守卫：ruff check / ruff format、pytest（1444 passed；4 个失败已修：
  3 个文档守卫因新增 axe spec 的计数与章节引用失配、1 个 Docker 端口测试
  为环境性失败，单跑通过）、web lint+typecheck、desktop lint+typecheck。
- 安装包：本地 `electron-builder` 构建 `OpsPilot-Setup-0.2.2-x64.exe`
  （130,717,317 B，SHA-256 `f3d34f4cd31b2032eec17834e8a3ba948a454ba5da88f0ea8568aa7b167ee4f7`），
  包内校验：`app.asar` 含 `titleBarStyle`、web 资源为 HeroUI 版
  （`2jrk7q0s-ohl6.css`，485,072 B）、SSR chunk 含 `data-titlebar`。
- 本机覆盖安装并启动：注册表版本 `0.2.2`，`/health` 返回
  `{"status":"ok","version":"0.2.2"}`，窗口无原生标题栏、按钮覆盖层与导航同色。

**未完成（如实记录）：**

- 第 2 节 README 截图/GIF 重拍未做：下方 GIF 与十张截图仍是 Phase 4 之前的
  UI，README 已如实标注「still show the pre-Phase-4 UI」。
- 第 3 节人工走查未重做：v0.2.2 的验证为程序化核对（版本、资源、接口、
  窗口截图），没有第二次独立安装走查。
- 第 0.3 节「干净环境全量 Playwright」以 CI 的 `web-e2e` 为准，而非本机
  （本机 8000 端口被无关服务占用）。
