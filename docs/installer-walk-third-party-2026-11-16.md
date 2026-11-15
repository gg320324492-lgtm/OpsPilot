# OpsPilot 0.2.0 安装包第三方走查报告（2026-11-16）

**测试人**：独立 QA 走查（context-free agent，与开发无关，仅凭书面任务书执行）
**日期**：2026-11-16　**机器**：Windows 11 x64 / Docker Desktop 29.9.2 / Git Bash

> 归档说明：这是 DBC-01 要求的「未参与构建的走查人」记录。走查人是运行在
> 本机上的无上下文 agent——它没有本项目的开发历史，只有任务书；严格说是
> 「隔离视角走查」而非「另一台机器的外部人员」。任务书原文见本文件末尾。
> 归档入 [`docs/installer-verification.md`](installer-verification.md)。

## 逐项结果

| 步骤 | 命令/动作 | 预期 | 实际 observed | 判定 |
|---|---|---|---|---|
| 0-1 | `docker info` | Docker 运行中 | Server 29.9.2，13 容器正常 | PASS |
| 0-2 | `netstat -ano` 查 3000/5432/8000 | 3000、5432 空闲；8000 已占 | 仅 `127.0.0.1:8000 LISTENING (PID 62688)`；3000/5432 空闲 | PASS |
| 0-3 | 检查已有安装 | 应无残留 | 发现**已有 OpsPilot 0.2.0**（目录+注册表均在）。执行 `Uninstall OpsPilot.exe /currentuser /S`，约 25s 后目录消失，注册表项消失 | PASS（先清理） |
| 1-4 | `./OpsPilot-Setup-0.2.0-x64.exe /S` | 静默安装完成 | 文件 5s 内落盘；安装进程运行约 **8.5 分钟**后自行退出（退出码 0）。期间无 UI、无报错 | PASS |
| 1-5 | 验证安装产物 | 目录/快捷方式/注册表 | `D:\Users\pc\AppData\Local\Programs\OpsPilot\OpsPilot.exe` 存在；桌面与开始菜单均有 `OpsPilot.lnk`（目标路径已验证指向安装 exe）；注册表 `DisplayVersion: 0.2.0` | PASS |
| 2-6 | 设置 `OPSPILOT_API_HOST_PORT=18002` 后 `Start-Process` | 应用启动 | 4 个 OpsPilot 进程启动（03:12:24） | PASS |
| 2-7 | 轮询 `/ready` + `docker ps` | 200，出现 opspilot-* 容器 | 03:12:48 返回 200；容器：`opspilot-postgres-1`(healthy)、`opspilot-migrate-1`(Exited 0)、`opspilot-api-1`(healthy)、`opspilot-worker-1` | PASS |
| 2-8 | 冷启动计时 | 2–15 分钟 | **约 21–24 秒**（见下方“注意”） | PASS（偏快，有前提） |
| 2-9 | `/ready` 返回体；仪表盘 13001 | ready；仪表盘可访问 | `/ready` = `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`；**13001 与 3000 均无监听**，netstat 仅见 18002。仪表盘未能从宿主机端口访问到 | FAIL（按字面预期）* |
| 3-10 | POST /api/tickets（带 token） | 返回 ticket id + run | ticket `55737037-257f-424e-b898-73e9f0a220a0`，run `aae4f22f-30c0-43de-aaba-06e105d3e40a`，status=received | PASS |
| 3-11 | 轮询 run 状态 | completed/failed/parked | **completed**（约 1 秒内；provider=`fake`，分类 duplicate_charge 0.93，检索 5 chunks，生成回复 183 字符，escalated=true） | PASS |
| 3-12 | 审批环节检查 | 记录 pending 审批或工具调用记录 | `GET /api/approvals` 返回空（无 pending）；`/audit` 有 3 条事件（model_called×2、retrieval_abstained）；`/trace` 有完整 8 步记录 | PASS（无审批可停，链路记录完整） |
| 3-13 | 重复决策保护 | 无 pending 则不构造 | 无 pending，跳过 | PASS（N/A） |
| 4-14 | `Stop-Process -Name OpsPilot`，确认容器移除、卷保留 | 容器移除 | **容器未随进程退出**（10 分钟后仍在运行，restart policy=unless-stopped）；且应用进程曾自行重启一次。最终手动 `docker compose -p opspilot down` 后容器移除，卷保留 | FAIL（自动清理缺失） |
| 4-15 | 再次启动，热启动计时 | 可用 | /ready=200，**约 15 秒** | PASS |
| 4-16 | GET /api/tickets 找原工单 | 工单还在 | 步骤 10 的工单（55737037…）在列表首位，数据完整 | PASS |
| 5-17 | 无 token POST /api/tickets | 401/403 | **401** `{"error":{"code":"unauthorized","message":"missing or invalid bearer token"}}` | PASS |
| 5-18 | 清理 | 停进程、留卷、不动 8000 与 microbubble | OpsPilot 进程已停；opspilot 容器已移除；`opspilot_postgres-data`、`opspilot_mcp-data` 卷保留；8000 端口进程与 microbubble-agent-* 容器未受影响 | PASS |

\* 步骤 2-9：任务书允许「如 13001 未监听，改查 netstat 判断仪表盘端口并如实记录」。netstat 未发现任何仪表盘宿主端口；Electron 进程命令行含 `--standard-schemes=opspilot`（应用内自定义协议加载界面），故仪表盘可能仅存在于应用窗口内、不暴露宿主端口。仅记录观察，不下结论。

## 总体结论

**安装包可用。** 安装、卸载、静默安装、服务启动、API 核心流程、鉴权、数据持久化均通过。唯一功能性缺陷是**关闭应用后 Docker 栈不自动回收**（需手动 `docker compose -p opspilot down`）。

- **冷启动耗时**：约 21–24 秒（Start-Process → /ready=200）
- **热启动耗时**：约 15 秒

## 发现的问题

1. **应用退出不清理 Docker 容器**（被杀进程路径）。复现：启动应用至 /ready=200 → `powershell Stop-Process -Name OpsPilot` → 等待 10 分钟 → `docker ps -a | grep opspilot` 仍显示 4 个容器（api/worker/postgres Up，migrate Exited 0），容器 restart policy 为 `unless-stopped`。需手动 `docker compose -p opspilot down`（项目名 opspilot，workdir `D:\Users\pc\AppData\Local\Programs\OpsPilot\resources\stack`）。
2. **进程被杀后出现过一次自行重启**：第一次 Stop-Process 后约 40 秒，出现新的 OpsPilot 进程树（父进程为 explorer.exe，命令行为裸 `OpsPilot.exe`），需再次 Stop-Process 才停干净。原因未查明，仅记录现象。
3. **仪表盘无宿主监听端口**：13001/3000 均未监听，无法按任务书用 curl 验证仪表盘页面。
4. **打包版使用 fake 模型**：run 的 `model_provider=fake`、`model_name=fake`，audit 显示 `model: hand-written-placeholder`——工单流水线可完整跑通但非真实 LLM（与发布默认一致，仪表盘有披露）。

## 走查前提说明（影响冷启动数据解读）

- 本机在走查前已存在 `opspilot-api/worker/migrate` 镜像（此前测试遗留），且 `opspilot_postgres-data` 卷创建于 2026-11-14（库中含 11-15 的历史工单）。因此本次「首装」的冷启动未经历镜像构建/拉取，21 秒不代表全新机器的首跑耗时（任务书给的 2–15 分钟区间更适用于全新环境）。安装目录与注册表本身是干净重装的。

## 哈希核对

- 文件：`E:\labcairn-local\tmp\thirdparty-walk\OpsPilot-Setup-0.2.0-x64.exe`
- 大小：130,075,633 字节 = 预期 ✅
- SHA-256：`2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332` = 预期 ✅

## 清理确认

OpsPilot 进程已全部停止；opspilot 容器与网络已移除；数据卷保留；18002 端口已释放；8000 端口进程与 microbubble-agent-* 容器原样未动。遗留项：用户级环境变量 `OPSPILOT_API_HOST_PORT=18002` 仍保留（走查配置所需，如需可删除）。
