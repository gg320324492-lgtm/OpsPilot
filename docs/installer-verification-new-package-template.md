# OpsPilot 新安装包走查结果回填表单（模板）

> 用法：把本文件整个复制一份（改名或直接粘贴到消息里均可），逐项填写后
> 回传给发起走查的人。本模板对应 2026-11-15 重建的新候选包。
> 英文原版步骤说明见 [`installer-verification.md`](installer-verification.md)，
> 中文对照见 [`installer-verification.zh-CN.md`](installer-verification.zh-CN.md)。
> 遇到失败时：截图（弹窗 / 窗口日志整屏）+ 保留日志文件，按 §7 的五项取证，
> 在对应行的"备注"里写清楚，不要只写"失败"。

---

## 一、走查对象（先核对，再开始）

| 项 | 值 | 你拿到的文件是否一致？ |
|---|---|---|
| 文件名 | `OpsPilot-Setup-0.1.0-x64.exe` | ☐ 一致 ☐ 不一致：____ |
| 字节数 | 126,045,189（约 120.2 MB） | ☐ 一致 ☐ 不一致：____ |
| SHA-256 | `71ac5ac0adcb543fc96810c96085fbb66bbb73260c92d85325a6927997ee9a7b` | ☐ 一致 ☐ 不一致：____ |

**核对方法（普通命令提示符即可，无需管理员）：**

```
certutil -hashfile "你的路径\OpsPilot-Setup-0.1.0-x64.exe" SHA256
```

输出的第二行应与上表 SHA-256 完全一致。若你拿到的是 `119,227,780` 字节、
SHA-256 以 `565206f7` 开头的文件，那是 **2026-11-14 已走过查的旧包**——
停下来联系发起人换文件，不要继续。

## 二、逐项结果（16 项）

| # | 步骤 | 结果 | 备注（看到什么写什么） |
|---|---|---|---|
| 0 | 环境：Docker Desktop 已安装**且已启动** | ☐ 通过 ☐ 失败 | `docker version` 输出版本号：____ |
| 0b | 取得安装包并核对哈希/大小 | ☐ 通过 ☐ 失败 | 来源：____（谁发的 / 哪条链接 / CI run id） |
| 1 | 安装：双击安装包，出现桌面快捷方式与开始菜单项，图标为**蓝方块**（SmartScreen 弹窗属正常，点"更多信息→仍要运行"） | ☐ 通过 ☐ 失败 | 图标：☐ 蓝方块（新） ☐ 黑圆白三角（先重建图标缓存，见清单 §0 提醒 4） |
| 2 | 启动：双击 OpsPilot 快捷方式，窗口日志滚动后出现仪表盘（顶部导航 Dashboard/Tickets/Runs/Approvals/Knowledge），首次启动 5–15 分钟属正常 | ☐ 通过 ☐ 失败 | 首次启动耗时：____ 分钟 |
| 3.1 | 知识库索引：Knowledge → Reindex knowledge/，面板报 **17 seen / 17 indexed / 98 chunks** | ☐ 通过 ☐ 失败 | 实际数字：____ |
| 3.2 | 提交工单：Tickets → Fill with the README's ticket → Submit ticket，返回 run 链接 `/runs/<uuid>` | ☐ 通过 ☐ 失败 | run id：____ |
| 3.3 | **停在审批门**：约 10 秒内 run 变 **Waiting approval**，页面出现高风险退款审批卡（"This moves 129.00 …"）。**若直接 completed = 安全门退化，立即报告** | ☐ 通过 ☐ 失败 | |
| 3.4 | 批准：点 Approve，几秒内 run 变 **Completed**，时间线显示退款**只执行一次** | ☐ 通过 ☐ 失败 | |
| 3.5 | 幂等：再次批准被拒——返回 **HTTP 409**，body 含 `"code":"approval_already_decided"` | ☐ 通过 ☐ 失败 | 实际返回：____ |
| 4 | 健康检查：`curl -s http://127.0.0.1:8000/ready` 返回 `200` + `"status":"ready"` + `"migrations":"ok"` | ☐ 通过 ☐ 失败 | |
| 5.1 | 端口只绑 loopback：`netstat -ano \| findstr ":3210"`（及 `:8000`）每行都是 `127.0.0.1:…`，无 `0.0.0.0`/`[::]` | ☐ 通过 ☐ 失败 | |
| 5.2 | 跨源写被拒：`curl -X POST http://127.0.0.1:3210/api/knowledge/reindex` 返回 `"code":"origin_missing"` | ☐ 通过 ☐ 失败 | |
| 5.3 | 无 token 被拒：`curl -s http://127.0.0.1:8000/api/runs` 返回 `"code":"unauthorized"` | ☐ 通过 ☐ 失败 | |
| 5.4 | 包内无凭据：`dir /s /b "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\.env*"` 返回 *File Not Found* | ☐ 通过 ☐ 失败 | |
| 6 | 退出后容器停、卷留下：`docker ps --filter name=opspilot-` 无行；`docker volume ls \| findstr opspilot` 恰好两个卷（`opspilot_postgres-data`、`opspilot_mcp-data`） | ☐ 通过 ☐ 失败 | |
| 6b | 重启后数据还在：再次启动应用，§3.2 的 run 仍可见 | ☐ 通过 ☐ 失败 | |

## 三、走查环境与签名

**走查人（姓名或代号）：** ______________
**走查日期：** ____________（YYYY-MM-DD）
**机器：** 台式机 / 笔记本（勾一个）；是否为 OpsPilot 首次安装：☐ 是 ☐ 否
**Windows 版本：** ______________（如 Windows 11 Pro 10.0.26300）
**Docker Desktop 版本：** ______________（`docker version` 的 Engine 版本）
**安装包来源：** ______________（谁发的 / 哪条链接；若是 CI artifact，写 run id 与其构建的 commit）
**录屏：** ☐ 已录（文件名：____________，字节数：____） ☐ 未录，原因：____

**总判定（走查人填）：** ☐ 16 项全部通过 ☐ 有失败（共 ____ 项，见上表备注）

## 四、失败取证（任一项失败时）

1. **截图**：失败瞬间的整屏（含弹窗原文、窗口日志原文）。命名建议
   `fail-<步骤号>-<序号>.png`，例如 `fail-3.3-1.png`。
2. **窗口日志**：把 OpsPilot 窗口里的日志**原文全文**复制下来（不要转述）。
3. **容器状态与日志**：

```
docker ps -a --filter name=opspilot-
docker compose -f "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\docker-compose.yml" logs --tail 100
```

4. **环境**：Windows 版本、Docker Desktop 版本（§三已填）。
5. **安装包**：SHA-256（§一已填）+ 来源（§三已填）。

**不要**把 operator token 或任何 `.env` 文件内容贴进报告；若复制出的日志里
带了 token，把那一行替换成 `[redacted]` 并注明。

---

*模板版本：2026-11-15，对应新候选包（commit `81a0278` 构建）。旧记录
（2026-11-14，SHA-256 `565206f7…`）仍保留在
[`installer-verification.md`](installer-verification.md) 文末，本表不覆盖它。*
