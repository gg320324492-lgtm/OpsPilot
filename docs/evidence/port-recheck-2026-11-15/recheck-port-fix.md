# 桌面壳「API 端口跟随」修复独立复核报告

- 复核人视角：陌生人（独立工作区，未读仓库开发历史）
- 日期：2026-11-15
- 工作区：`E:\OpsPilot-portrecheck`（新建，未使用其他走查工作区）
- 安装包来源：`E:\OpsPilot\desktop\release\OpsPilot-Setup-0.2.0-x64.exe`（复制到本工作区）

## 1. 安装包核对

| 项目 | 期望 | 实测 | 结果 |
|---|---|---|---|
| 文件大小 | 130,075,633 字节 | 130,075,633 字节 | ✅ 一致 |
| SHA-256 | `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332` | `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332` | ✅ 一致 |

## 2. 安装

- 方式：NSIS 静默安装（`OpsPilot-Setup-0.2.0-x64.exe /S`），退出码 0。
- 安装目录：`D:\Users\pc\AppData\Local\Programs\OpsPilot\`（本机 Program Files 位于 D 盘）。
- `OpsPilot.exe` FileVersion：**0.2.0**（ProductVersion 0.2.0.0）。
- 打包栈位置：`D:\Users\pc\AppData\Local\Programs\OpsPilot\resources\stack\docker-compose.yml`。
- 首次启动后 app-data 生成 `C:\Users\pc\AppData\Roaming\OpsPilot\opspilot.env`（含 `OPSPILOT_OPERATOR_TOKEN`，值见下文「环境变量」一节，不在此复述）。

## 3. 复核前本机基线

- `127.0.0.1:8000` 被无关服务占用（PID 62688，microbubble-agent 项目），**未释放**，正是复现场景。
- 本机已有 compose 项目 `opspilot` 在运行：api 映射 `127.0.0.1:18001->8000`（healthy）、postgres 映射 `127.0.0.1:15434->5432`（healthy）、worker 运行中。项目配置文件路径为安装目录内的 stack compose。
- `E:\OpsPilot\.env` 中无 `OPSPILOT_API_HOST_PORT` / `OPSPILOT_COMPOSE_PROJECT_NAME` / `OPSPILOT_DESKTOP_PORT`（仅有 operator token）。

## 4. 使用的环境变量

| 变量 | 测试 A（反向） | 测试 B（正向） |
|---|---|---|
| `OPSPILOT_API_HOST_PORT` | 未设置 | `18001` |
| `OPSPILOT_DESKTOP_PORT` | 未设置 | `33210` |
| `OPSPILOT_COMPOSE_PROJECT_NAME` | 未设置 | `opspilot` |
| `OPSPILOT_OPERATOR_TOKEN` | 未设置 | 已设置（与运行中 opspilot 栈容器内一致的临时运行时值；按复核要求不在本报告中记录其明文） |

## 5. 测试 A：不设变量启动（反向验证旧缺陷仍在）

- 时间：19:29:09 启动 `OpsPilot.exe`（环境变量已逐项 `env -u` 清除）；另有一个 19:26:09 由安装器自动拉起的实例同样未设变量，为 A 提供了更长的自然观察样本。
- 预期：壳探测默认 `http://127.0.0.1:8000/ready`；该端口被无关服务占用且不响应 `/ready`，壳应停在等待/重试，不打开仪表盘。
- 观察（持续 4 分钟窗口）：
  - 仪表盘默认端口 `127.0.0.1:3210` 全程未监听（每 10 秒 `netstat` 轮询）。
  - 壳进程组保持存活（13 个 OpsPilot.exe 进程），未崩溃、未退出、未弹错误对话框。
  - **关键现象**：19:29:12（启动后约 3 秒）壳确曾尝试按默认配置重建 api/worker（容器 `opspilot-api-1`/`opspilot-worker-1` 被重建为 `Created` 状态），但其 compose 模板把 API 固定发布到 `127.0.0.1:8000`——与无关服务冲突，Docker 报 `Bind for 127.0.0.1:8000 failed: port is already allocated`，容器永远到不了运行态。此后壳继续等待就绪，直至观察窗口结束（246 秒）仪表盘仍未出现。
  - 此时 8000 端口上的无关服务对 `/ready` 返回 404、对 `/` 返回 200，因此壳的「栈已在跑」判定同样不成立，壳停在等待分支。
  - stderr 仅见 Electron 常规 GPU/disk-cache 噪音（与 0.2.0 包已知噪音一致），无致命错误栈；壳不写任何日志文件，进度只显示在窗口内。
- 结论：**默认行为未变**——不设 `OPSPILOT_API_HOST_PORT` 时，壳仍面向 8000；8000 被无关进程占用时，壳既无法采用「已在运行」的后端（`/ready` 404），也无法拉起自己的栈（8000 绑定冲突），最终卡死在等待。旧缺陷在「不设变量」场景下完整复现。

## 6. 测试 B：设置变量启动（正向验证修复生效）

- 启动方式：在 shell 中导出第 4 节所列 B 组变量后启动 `OpsPilot.exe`。
- 观察：
  - 壳未重建/未改动既有 `opspilot` 栈（`docker compose ls` 前后一致，容器无变化）——`/ready` 在 18001 已有应答，壳走了「采用已在运行的后端」分支。
  - 就绪探测命中：`GET http://127.0.0.1:18001/ready → 200`。
  - 仪表盘在 `http://127.0.0.1:33210/` 打开：`GET → 200`（约 0.05–0.15s 响应），返回正常 Next.js 仪表盘 HTML。
  - 启动到仪表盘可用耗时：**约 5 秒**（壳主进程 19:28:20 创建，持有 33210 的 dashboard 子进程 19:28:35 创建，数秒内 curl 已得 200；因栈已就绪、无需 compose up）。
  - 持有 33210 监听的进程即新拉起的壳进程组（PID 57712，父进程 14292）。
- 结论：**修复生效**。壳读取 `OPSPILOT_API_HOST_PORT=18001`，就绪探测与仪表盘的 API 基址均指向 18001，仪表盘按 `OPSPILOT_DESKTOP_PORT=33210` 打开；8000 被占用不再影响启动。

## 7. 异常与说明

- 测试 B 首次尝试时误用 `./D:/...` 相对路径导致一次启动失败（工作区自身的操作失误，与产品无关），改用 Windows 绝对路径后成功。
- Electron stderr 中的 `Unable to move the cache: 拒绝访问。(0x5)` / `Gpu Cache Creation failed: -2` 为已知的无害噪音（多实例共用用户数据目录时的缓存竞争），不影响功能。
- **测试 A 的意外副作用与处置**：测试 A 的壳在等待期间已用默认配置重建了 `opspilot` 项目的 api/worker 容器（未设端口变量 → 绑定 8000 失败而卡在 `Created`），导致既有 opspilot 栈一度不完整。测试 A 结束后，已按原配置（`OPSPILOT_API_HOST_PORT=18001`、`OPSPILOT_PG_HOST_PORT=15434`、项目名 `opspilot`、原 `OPSPILOT_OPERATOR_TOKEN`）执行 `docker compose up -d --wait` 将 api、postgres、worker 全部恢复为 healthy，`GET http://127.0.0.1:18001/ready → 200`，带原 token 的 `GET /api/runs → 200`。数据卷（`opspilot_postgres-data`、`opspilot_mcp-data`）与库内容经核对完好（agent_runs 4、tickets 4、knowledge_documents 17、knowledge_chunks 98，alembic 迁移版本在位）。opspilot 栈保持运行，未被删除或改动数据。
- 两个壳实例（A 与 B）同时运行期间共享同一 userData 目录，仅产生上述缓存噪音；观察结束后各实例均按计划结束进程，最终 OpsPilot.exe 进程数为 0。
- 全程未执行任何 `git` 命令，未修改 `E:\OpsPilot` 主仓文件。

## 8. 总结论

| 项 | 结果 |
|---|---|
| 安装包哈希 | ✅ 与发布记录一致 |
| FileVersion | ✅ 0.2.0 |
| A：不设变量 → 仍卡 8000 | ✅ 复现（默认行为未回归） |
| B：设 18001/33210 → 跟随生效 | ✅ 修复生效 |
| 对既有 opspilot 栈的影响 | ✅ 栈已恢复运行（见 §7 意外事件与处置），数据完好 |

**「API 端口跟随」修复在独立复核中判定为有效。**

## 9. 关键日志摘录

```text
# 测试 A —— Docker 拒绝把重建的 api 绑定到被占用的 8000（壳等待期间拉起）：
opspilot-api-1  Created
  err=failed to set up container networking: driver failed programming
  external connectivity on endpoint opspilot-api-1 (...):
  Bind for 127.0.0.1:8000 failed: port is already allocated

# 测试 A —— 8000 上的无关服务不是 OpsPilot API（壳的「已在运行」判定不成立）：
8000/ready -> 404
8000/      -> 200
（壳在 4 分钟窗口内：3210 从未监听，13 个进程存活等待，无崩溃、无错误对话框）

# 测试 B —— 端口跟随生效：
GET http://127.0.0.1:18001/ready -> 200      # 壳探测命中被指引的 API 端口
GET http://127.0.0.1:33210/      -> 200      # 仪表盘按 OPSPILOT_DESKTOP_PORT 打开
dashboard 200 in 0.045s
（docker compose ls 前后一致：仅既有 opspilot 与无关 microbubble-agent，壳未重建栈）
```
