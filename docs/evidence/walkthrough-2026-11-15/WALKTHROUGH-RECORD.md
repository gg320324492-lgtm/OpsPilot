# OpsPilot v0.2.0 候选安装包·独立走查记录（2026-11-15）

> 走查人：独立验收走查员（未参与 OpsPilot 的构建与开发对话）。
> 工作区：`E:\OpsPilot-walkthrough-alice`（独立目录，含全部证据 JSON/日志）。
> 本记录为**新表单**，不覆盖 2026-11-14 旧记录。按
> `docs/installer-verification-new-package-template.md` 格式回填，
> 冲突处以英文权威原文 `installer-verification.md` 为准。

---

## 一、走查对象（先核对，再开始）

| 项 | 值 | 是否一致？ |
|---|---|---|
| 文件名 | `OpsPilot-Setup-0.2.0-x64.exe` | ✅ 一致（任务指定的 v0.2.0 候选包；模板仍印 0.1.0，以任务给定哈希为准） |
| 字节数 | **130,075,040** | ✅ 一致（实测 `stat` = 130,075,040） |
| SHA-256 | `3feb12939db05de50982331945b04c7e90c9bfcb844bdc34db0052facffb6315` | ✅ 一致（`sha256sum` 实测完全相同） |

复制件先于安装完成核对，`E:\OpsPilot-walkthrough-alice\OpsPilot-Setup-0.2.0-x64.exe`。

## 二、逐项结果（16 项）

| # | 步骤 | 结果 | 备注（实际观察到什么） |
|---|---|---|---|
| 0 | 环境：Docker Desktop 已安装且已启动 | ✅ 通过 | `docker version` Engine = **29.9.2**，运行中 |
| 0b | 取得安装包并核对哈希/大小 | ✅ 通过 | 来源：协调者会话指定 `E:\OpsPilot\desktop\release\OpsPilot-Setup-0.2.0-x64.exe`（构建记录见 `docs/progress.md`：commit `8133a89`，clean checkout `E:/OpsPilot-release-v020`）；复制到独立工作区后实测哈希/大小与给定值一致 |
| 1 | 安装（NSIS） | ✅ 通过（方式：/S 静默安装） | 无法进行真人点选 UI，改用 `installer /S` 静默安装（记录中注明）。安装路径 `D:\Users\pc\AppData\Local\Programs\OpsPilot\`（本机 per-user 目录在 D 盘）；`OpsPilot.exe` FileVersion **0.2.0**；桌面与开始菜单均出现 `OpsPilot.lnk`，目标指向安装路径。**图标颜色未检查**（无头环境无法人工看图标，不做臆测）。卸载器存在（`Uninstall OpsPilot.exe`） |
| 2 | 启动到仪表盘 | ⚠️ 部分（有条件通过，见偏差 #1） | 首次启动：compose 冷启动成功（postgres/migrate/api/worker 全部按序 healthy，migrate Exited 0）；但**壳内仪表盘从未监听 3210**。诊断：打包壳把 API 地址硬编码为 `http://127.0.0.1:8000`（`desktop/src/runtime/dependencies.ts:309` `DEFAULT_API_URL`，`main.ts` 未提供覆盖），而本机 8000 被无关服务（microbubble-agent）占用且任务禁止动它——壳探测 8000 的 `/ready` 得到 404，永远判"后端未就绪"。用打包内同一 standalone server + 同参数直接拉起验证：仪表盘本身**能正常启动并应答**（3210 → 200，页面含 Dashboard/Tickets/Runs/Approvals/Knowledge 导航），`ELECTRON_RUN_AS_NODE=1` 下 spawn 亦正常。首次冷启动 compose 栈耗时 **约 5 分钟**（17:53:49 开机 → 17:58:xx /ready 200，以镜像构建完成为主） |
| 3.1 | 知识库索引 | ✅ 通过 | 首次索引后 DB 实测 **17 documents / 98 chunks**（psql 计数）；二次 reindex 返回 `{"documents_seen":17,"documents_indexed":0,"chunks_written":0}`（HTTP 200），与契约的幂等承诺一致。经仪表盘代理（3210，带 Origin）调用同样成功 |
| 3.2 | 提交工单 | ✅ 通过（经 API 完成） | `POST /api/tickets`（README 金路径工单原文）→ HTTP 201，run id **`cbda2aa7-6db4-4f7d-ad27-581e508e715c`**（ticket `1c48e9de`） |
| 3.3 | 停在审批门 | ✅ 通过 | run 约 1–2 秒内进入 **waiting_approval**；`GET /api/approvals?status=pending` 返回审批卡 `a5279924`：`risk_explanation = "This moves 129.00 out of the account, refunding transaction TX-88219, and cannot be undone automatically…"`，`permission=high_risk_write`。**安全门未退化**（没有直接 completed） |
| 3.4 | 批准 → 退款恰好一次 | ⚠️ 见备注 | `POST /api/approvals/a5279924/approve` → HTTP 200，run `waiting_approval → executing → responding → completed`（16 秒内）。**但退款工具调用结果是 `failed / invalid_state`**：账本卷（`opspilot_mcp-data:/data/mcp/billing.json`）在走查开始前就已含有 2026-11-14 遗留的退款记录 REF-10091（TX-88219 已是 refunded，幂等键 `refund:e2b07c50…`）——本机不是干净数据机。run 以 `escalated: true` 的客户回复 COMPLETED（对已退款交易拒绝二次退款是**正确的守卫行为**，见 `docs/agent-state-machine.md` §"Write of any level → refused → COMPLETED escalated"）。**幂等性本身用三个独立证据验证为通过**：① 同幂等键重放 → `replayed: true`，账本 refunds 仍 1 条；② 不同幂等键对已退款交易 → `invalid_state`，账本仍 1 条；③ 第二张金路径工单（run `11de6404`）走完审批门后同样被 `invalid_state` 拒绝，全账本**始终恰好 1 笔退款** |
| 3.5 | 再次批准被拒（409） | ✅ 通过 | 重放 `POST /api/approvals/a5279924/approve` → **HTTP 409**，body `{"error":{"code":"approval_already_decided",…}}`（紧凑格式，与文档一致） |
| 4 | `/ready` 健康检查 | ✅ 通过（API 18001 上） | `curl http://127.0.0.1:18001/ready` → 200 `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`（紧凑格式）。**8000 端口无法用于本应用的 /ready**（被无关服务占用，见偏差 #1） |
| 5.1 | 端口只绑 loopback | ✅ 通过 | `netstat -ano`：postgres `127.0.0.1:15434`、api `127.0.0.1:18001`、仪表盘 `127.0.0.1:3210`，全部仅 loopback；无任何 `0.0.0.0`/`[::]` 绑定 |
| 5.2 | 跨源写被拒 | ✅ 通过 | `curl -X POST http://127.0.0.1:3210/api/knowledge/reindex`（无 Origin）→ **HTTP 403** `{"error":{"code":"origin_missing",…}}`；带 `Origin: http://127.0.0.1:3210` 的同请求 → 200 正常代理 |
| 5.3 | 无 token 被拒 | ✅ 通过 | `curl http://127.0.0.1:18001/api/runs`（无 Authorization）→ `{"error":{"code":"unauthorized","message":"missing or invalid bearer token",…}}` |
| 5.4 | 包内无凭据 | ✅ 通过 | 安装目录 `find -name ".env*"` → 无结果；对 `resources/stack/src`、`web/src`、compose、asar 主进程做硬编码长凭据字面量扫描 → 无命中；compose 中 token 为 `${OPSPILOT_OPERATOR_TOKEN:?…}` 必填注入形式。操作员 token 在首启时于 `%APPDATA%\OpsPilot\opspilot.env` 本机生成（本机新生成 token 与 11-14 遗留值不同，已验证） |
| 6 | 退出后容器停、卷留下 | ✅ 通过（见偏差 #3） | 强杀进程树会绕过壳的优雅 teardown（预期内）；改用**与壳完全相同语义**的 `docker compose down --remove-orphans`（同一打包 compose 文件、同一项目名、带 --env-file）后：`docker ps -a --filter name=opspilot-` → **无容器**；`docker volume ls` → **恰好两个卷** `opspilot_postgres-data`、`opspilot_mcp-data`。8000 端口上的无关服务全程未受影响 |
| 6b | 重启后数据还在 | ✅ 通过 | 热启动（同一受控端口配置）**16 秒**到 `/ready` 200；重启后实测：tickets **4**（= 退出前）、approved 审批 **3**（= 退出前）、run1 审计事件 **11**（= 退出前）、billing refunds **1**、TX-88219 仍为 refunded；仪表盘 run 详情页仍渲染 REF-10091 / TX-88219 / approved / refunded 证据 |

**总判定：16 项中 14 项通过，1 项部分通过（#2），1 项通过但带数据前提说明（#3.4）；无"失败"项；无安全姿态退化。**

## 三、走查环境与签名

- **走查人：** 独立验收走查员（非项目作者；本机未安装过 OpsPilot——安装前 `AppData/Local/Programs/OpsPilot` 不存在）
- **走查日期：** 2026-11-15
- **机器：** 台式机；是否首次安装 OpsPilot：**是**（但机器上有 2026-11-14 走查遗留的 Docker 卷与 `%APPDATA%\OpsPilot` 残留，见偏差 #2/#4）
- **Windows 版本：** Windows 11 Pro 10.0.26300
- **Docker Desktop 版本：** 29.9.2（Engine）
- **安装包来源：** 协调者会话提供的仓库内 `desktop/release/OpsPilot-Setup-0.2.0-x64.exe`（构建于 commit `8133a89` 的干净 checkout，记录见 `docs/progress.md` "v0.2.0 moves to the retrieval-fix commit"）
- **安装方式：** NSIS `/S` 静默安装（无头环境无法点选 UI；已在 #1 注明）
- **录屏：** ☐ 已录 ☑ 未录，原因：无头自动化环境无屏幕交互可录；所有步骤以命令输出与 JSON 证据留档于 `E:\OpsPilot-walkthrough-alice\`

## 四、偏差与未检查项（如实记录）

**偏差 #1（最重要，属产品/部署事实，非本包新缺陷）：** 打包桌面壳的 API 地址硬编码为 `127.0.0.1:8000`（源码 `desktop/src/runtime/dependencies.ts` 的 `DEFAULT_API_URL`；打包主进程仅识别 `OPSPILOT_DESKTOP_PORT` / `OPSPILOT_OPERATOR_TOKEN` / `OPSPILOT_USER_DATA_DIR` 三个环境变量）。compose 栈的宿主端口虽可经 `OPSPILOT_*_HOST_PORT` 配置（本包 compose 已含 `e8e7f6a` 模板化），但**壳不会跟随**。后果：在 8000 被占用的机器上，壳永远无法确认后端就绪，其内置仪表盘不会被拉起（2026-11-14 旧记录也因同一问题需要"暂停 8000 上的无关服务"才能走查）。本次按任务约束（8000 绝不能动）改用受控端口 18001 承载 API：compose 栈与打包仪表盘均验证正常，金路径全部走通；壳在"后端已在 18001 运行"时不会误停它（其 `isHttpReady(8000)` 为假 → 走 compose up；外部以同项目名预先 up 时壳的 compose up 会等待既有容器）。**建议（不在本次范围内执行）：给壳补一个 `OPSPILOT_API_URL`（或复用 `OPSPILOT_API_HOST_PORT`）的读取逻辑，消除"端口可配置但壳不跟随"的缝隙。**

**偏差 #2（数据遗产）：** 本机 Docker 中存有 2026-11-14 走查遗留的 `opspilot_postgres-data` / `opspilot_mcp-data` 卷：TX-88219 已被 REF-10091 退款、工单/审批历史非空、 `%APPDATA%\OpsPilot\opspilot.env` 遗留旧 token。走查未删除任何既有数据（任务约束）；因此 #3.4 的"退款恰好执行一次"表现为**对已退款交易的正确拒绝**（invalid_state + escalated 回复），而幂等性以三个独立证据验证（同键重放 replayed=true；异键拒绝；第二工单同一结论；账本始终 1 笔退款）。真正的"首次退款成功执行"需要一台无遗留卷的干净数据机。

**偏差 #3（退出语义）：** 任务约束禁止点选 UI，壳窗口由强杀结束，绕过了 `before-quit` 的优雅 teardown；随后按壳源码的同一命令语义执行 `docker compose down --remove-orphans`（保留卷），容器全部移除、两卷保留。第 6 项结论基于该等效验证。

**偏差 #4（多个壳实例）：** 排查期间产生了 3 个壳实例（首启失败实例、端口冲突再试实例、受控重启实例），已全部结束；对机器无遗留（无新卷、无新网络；`Roaming/OpsPilot` 为应用自身数据目录）。

**未检查项：**
- **#1 的图标颜色**（蓝方块 vs 旧图标）：无头环境无法目视确认，未检查。
- **真人点选路径**（SmartScreen 弹窗、"Fill with the README's ticket" 按钮、Approve 按钮点击、Knowledge 面板读数）：以 API/命令层等效完成并留证，但"人在 UI 上点"这一体验本身未覆盖。
- **录屏**：未录制（无屏幕交互），模板 §三的录屏项如实标注未录。
- **#2 首次启动的"窗口日志滚动"呈现**：壳窗口因端口冲突停在错误/等待状态，其启动日志序列（Checking Docker → Starting … → Backend ready on :8000 → Dashboard ready on :3210）未能在 8000 被占用的本机完整复现；compose 各阶段就绪以 Docker 状态与 /ready 实测代替。

## 五、证据清单（均在 E:\OpsPilot-walkthrough-alice\）

- `ticket-submit.json`（201 + run/ticket id）、`approvals-pending.json`（审批卡全文）、`approve-1.json`（200 批准）、`approve-2-replay.json`（409 幂等拒绝）
- `run-final.json` / `run3-final.json`（run 全步骤、tool_calls、escalated 回复）、`audit.json`（11 条审计事件）、`trace.json`（21 步时间线）
- `billing-snapshot.json`（走查前账本：REF-10091 已在）、容器内复核输出（refunds 恒 1）
- `reindex.json`（17/0/0 幂等）、`preexisting-opspilot.env.bak`（遗留 token 备份，未在报告中展示内容）
- `final-containers.txt` / `final-volumes.txt`（走查终态）

## 六、一句话结论

**该 v0.2.0 安装包在"装得上、跑得通"上成立：安装、冷/热启动、金路径（工单 → 审批门 → 批准 → 退款幂等 → 审计可查）、安全姿态（loopback、CORS、token、无凭据）全部按文档预期工作；唯一实质缺口是桌面壳把 API 固定在 127.0.0.1:8000，在 8000 被占用的机器上无法开箱即用——这正是不动 8000 的约束下本次以受控端口绕过的点。是否对外发布，取决于发布方是否接受"要求目标机 8000 空闲"这一既有部署前提（与 2026-11-14 D2 走查口径一致），或先补上壳的 API 地址可配置化再发布。**
