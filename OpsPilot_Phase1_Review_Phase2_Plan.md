# OpsPilot｜Phase 1 独立审阅与 Phase 2 执行计划

审阅日期：2026-10-12  
对象：https://github.com/gg320324492-lgtm/OpsPilot  
冻结审阅基线：`main` — `1db075828f07ccb6d77ad3549918d7640bc3265c`  
目的：第二个 GitHub 作品集；用于 Upwork 技术能力证明。  
审阅性质：远程仓库代码抽查 + GitHub Actions 执行日志 + 外部同类项目文档对比；**并非在独立主机上的完整功能/渗透测试**。此文件只提出审阅意见和计划，不改变 GitHub 仓库。

## 一、阶段一审阅结论

**主线完成，允许进入 Phase 2；但不能宣传为 production-ready。**

### 已核验
- `src/opspilot/`：Domain / Ports / Adapters / Agent Runtime / API / Worker 分层。
- 三套模拟 MCP 服务：CRM、Billing、Issues；业务主线是重复收费调查、政策检索、退款建议、人工审核、执行及审计。
- PostgreSQL + pgvector；Next.js 16 + TypeScript；FastAPI + SQLAlchemy + Alembic；静态工具权限和幂等处理。
- GitHub Actions 最新运行：https://github.com/gg320324492-lgtm/OpsPilot/actions/runs/38258012442 ，12/12 jobs success。
- Python 3.12：861 passed，15 skipped；Playwright：4 passed。CI 日志显示真实 Docker/Postgres + Web 浏览器黄金路径的执行。
- 现有人工写入的测试 fixtures 与真实模型测试分开陈述；项目在 `docs/limitations.md` 明确暴露诸多未实现能力。

### 真实模型基线（README 所引用 2026-10-11 评测）
- 分类：16/20 = 0.800
- 检索 Recall@5：14/20 = 0.700；Precision@5 = 0.170（该数据集最高理论值仅 0.250，不能直接误读为 17% 业务有效性）。
- 工具选择的 exact-set 命中：3/15 = 0.200
- 工具参数有效：80/170 = 0.471（包含对抗/应被拦截的调用；必须进一步拆分正常场景和恶意场景）。
- 任务完成：9/9，属于该指标的样本，不应代替工具选择的结果。
- 观察到四项安全门禁为零违规，但仅意味着评测样本内未观察到违规。
- 延迟 p95 276.7 秒，为一次模型端点运行的结果，不可用作稳态基准。

### 首要问题（P0）
1. **Web 写操作的访问边界**：Compose 暴露 `3000:3000`、`8000:8000`、`5432:5432`；Web Next Route Handler（尤其审批接口）拥有服务端 Operator Token，却未设置独立客户端认证。若主机端口能被不可信机器访问，对方不需要知道 API Token 就可能通过 Web 代理提交审批。数据库固定演示凭据与暴露的 5432 形成额外风险。必须先限制本地网络访问、加请求来源及会话/写入保护，禁止发布公开实例。源：`docker-compose.yml`，`web/src/app/api/approvals/[approvalId]/[decision]/route.ts`，`web/src/app/api/tickets/route.ts`。
2. **承诺边界**：模拟退款服务的幂等性主要由单进程 JSON store 的 check-then-write 实现，无跨进程原子保证。真实财务执行不可依赖演示级保证；对外统一明确说明是模拟支付场景。
3. **并发及恢复**：没有双 Worker 争抢 PostgreSQL 的测试；运行中 Worker 中断会使 Run FAILED，而 WAITING_APPROVAL 可恢复。应审验实际进程/数据库边界，不能只凭 SQLite 单 Worker 用例。
4. **文档漂移**：`web/README.md` 仍称页面均为 placeholder、浏览器直接调用 API，与现有 Next Route Handler 和已完成页面相冲突；某些 Docker 注释也保留了“NOT VERIFIED”的历史语句。作品集文档应以当前实测能力为准。

## 二、同类项目定位

| 对象 | 它的成熟能力 | OpsPilot 的差异化价值 | 不应宣传的优点 |
|---|---|---|---|
| n8n | 可视化编排、AI Tool HITL、丰富连接器 | 特定 B2B billing 工作流中，可审查的代码级权限门禁、批准后资金动作、Run/Tool/Trace 联动 | 不应声称“第一个能人工审批的 AI Agent” |
| Flowise | AgentFlow、Human Input、工具级人工审批、流程图式开发 | 更强调固定业务约束、审计记录和端到端安全不变量 | 不应声称功能广度优于 Flowise |
| LangGraph | Checkpointer、暂停恢复、故障恢复、HITL、时间旅行 | OpsPilot 是围绕具体业务交付的垂直应用，不要求甲方先设计 Agent 图 | 不应声称恢复能力、框架成熟度胜过 LangGraph |

项目应定位为：**Safety-gated B2B Support & Billing Agent — a portfolio-grade implementation of AI tool governance, auditability, and human approval.** 而非“通用 Agent 平台”。

## 三、Phase 2 任务总览

建议按 **5 个验收包** 执行。个人开发的粗估为 4–7 周，受现有开发环境及 Electron Windows 签名/打包问题影响较大。排序按风险和依赖，不按功能视觉吸引力。

### P2-A：部署安全边界与基线冻结（P0；2–4 个工作日）

**实施：**
- 将桌面/本地 Compose 模式的 API、Web、Postgres 监听默认缩小到 `127.0.0.1`；Postgres 不再默认对主机发布端口，调试时使用 opt-in profile。
- 避免固定生产口令；本地 demo 可生成独立随机凭据。
- 为 Web Route Handler 提供访问限制：本地 desktop 专用会话/随机访问令牌、请求来源（Origin）及 CSRF 防护；绝不将 API Operator Token 注入前端 JS。
- 复核审批 `approve/reject` 和 Ticket 创建路由；测试来自未授权会话和不可信 Origin 的请求均失败。
- 核查日志与 Trace 的敏感字段；演示模式默认采用最少必要留存。
- 对模拟支付/真实财务能力作明确免责声明。

**验收：** 未认证用户不能通过 Web 创建 Ticket 或批准退款；LAN 侧无法访问 desktop 模式 API/数据库；负向安全用例加入 CI；原黄金流程仍全部通过。

### P2-B：Agent 能力及 Evaluation 修正（P1；4–7 个工作日）

**实施：**
- 对 tool_selection 12/15 未命中用例按漏选、多选、工具不调用、无效参数分类；保留失败回归样本。
- 在现有 workflow 内优先改进工具选择约束、必要的前置读取规则、参数抽取/校验；不要新建多 Agent 平台。
- 针对 retrieval：优先调查同一 document 在 top-5 重复出现的问题；按文档维度做可配置去重/排序实验，保持对比评测；阈值按 embedding provider 分开校准。
- 将“合法普通业务样本的参数有效性”和“恶意请求被阻断数量”单独报告。
- 新建 ≥30 条未参与调参的 holdout 样本；保留现有 70-case benchmark，明确 fake / real / synthetic 边界。
- 两次相同模型和配置的 live eval，对比上一轮结果，不用单次随机改善宣称结论。

**验收建议：** 相同 15 用例至少 10/15 exact tool-set 命中（两次评测均达标）；30 条 holdout 达到至少 70% 的预定义工具集匹配；检索核心业务 Recall@5 不低于当前基线；四项安全零违规；新增真实结果文件及误差分析。阈值为 Phase 2 项目目标，尚非观察到的事实。

### P2-C：可恢复性、并发与可操作 UI（P1；4–6 个工作日）

**实施：**
- 用真实 PostgreSQL 启动两个 Worker 并发 claim，同一任务仅一方取到；模拟重复审批与重复的 refund intent。
- 明确外部工具在“成功执行、未写回数据库”窗口的重试责任；若无法端到端保证安全恢复，宁可 FAILED 并人工处理，而非自动重放高风险写操作。
- 优先建设“失败原因 / 重试建议 / 审批等待状态 / API 离线诊断”可见性；改善审批二次确认与审计详情。
- 增加 1–2 条负向真实浏览器 E2E，保持黄金路径不回退。
- 不开发多租户、批量退款、通用可视化工作流设计器。

**验收：** 双 Worker/Postgres 用例绿；中断窗口及失败反馈可观察；高风险副作用在定义好的幂等作用域内不会重复；Approvals 页面在超时/冲突/拒绝路径上准确反馈。

### P2-D：Electron 软件化（P1，阶段必做；7–12 个工作日）

**主路线：Windows Desktop Demo — Electron 外壳 + 现有 Next.js SSR + 本地 Docker 后端。**

为什么不能纯静态打包：Next.js 里存在 Server Components 与 Route Handlers，API Token 只存在 server-only 环境变量。把它直接 `next export` 成静态网页将破坏该设计。保留 `.next/standalone` 的本地 Next 服务是成本较低的桌面迁移方式。

**实施：**
1. 创建 `desktop/` 包（Electron Main、受限 Preload、打包配置与资源）。
2. Electron 启动受控的 `Next standalone` 本地子进程，使用动态或隔离的 loopback 地址；主窗体加载受信任的本地 UI。
3. Python API、Worker、PostgreSQL 仍使用原有 Docker Compose；做 Docker 就绪检查、启动指导和服务状态反馈。**明确安装包暂不包含 PostgreSQL 和 Python 后端**，不能称作无需 Docker 的独立离线应用。
4. Electron 使用 `contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`；IPC 白名单、限制导航、新窗口、外部链接及权限请求；避免把 Operator Token 通过 preload 暴露。
5. 本地配置放到用户配置目录；密钥管理要明确作用域；关闭时回收 Next 子进程，不误删已有用户 PostgreSQL volume。
6. Windows NSIS 安装包输出 `.exe`；支持安装、首次运行、检测后端、打开黄金路径、关闭、卸载。
7. GitHub Actions 增加 Windows 构建检查与 Electron smoke test，Release 中发布安装包、SHA256、安装前提和已知限制。

**验收：** 清洁 Windows 机器有 Docker Desktop 环境时，依据简短指南可完成安装并演示 Submit → Retrieve → Park → Approve → Audit；无 Docker 时提供友好错误且不会假装成功；桌面端越权访问测试绿；安装包能执行和卸载；有对应截图与录屏。

**可选但不承诺：** 跨平台 macOS/Linux、完全无 Docker、内嵌 Postgres、真正 offline bundled Python、自动更新和代码签名；这些需要单独工期与真实用户需求。

### P2-E：Upwork 作品集打磨及发布（P1；2–4 个工作日）

**实施：**
- README 首屏改为问题 → 20–40 秒真实产品 GIF/视频 → 关键安全边界 → 快速开始 → 技术栈 → 测试证据 → 限制；将历史过程移至 `docs/`。
- 替换过时 `web/README.md`、Docker 注释，删除 Next 默认 SVG/模板痕迹；补充 3–5 张真实 UI 图片（Dashboard、Run Trace、Approval、Failure）。
- 提供英文一段式客户价值陈述；展示一条模拟退款从请求到审核、再到审计的完整案例。
- 新增 `docs/portfolio-case-study.md`：Problem / Constraints / Architecture / Security Decisions / What Was Tested / Known Limits / What I Would Do for a Client。
- 发布 `v0.2.0-beta` GitHub Release，附 Electron 安装包；标注 demo-only 和 single-operator 限制。

**验收：** 招聘者 90 秒内能理解业务场景与差异点；10 分钟内可以按文档复现 demo（含 Docker 安装前提）；有来自 GitHub Actions 的可点开证据；无未经证实的“生产级”“完全自主”“100% 安全”字眼。

## 四、执行顺序与停止条件

**推荐 PR 顺序：**
- PR #1：desktop/local exposure hardening + auth tests
- PR #2：eval diagnosis and tool-selection improvements
- PR #3：PostgreSQL concurrency and interruption tests
- PR #4：dashboard error UX + browser regressions
- PR #5：Electron shell, packaging, Windows smoke
- PR #6：portfolio README, screenshots, release, limitations consistency

编号为建议顺序，不表示已创建 PR。每个 PR 写：Before / Change / Evidence / Remaining Gap；CI 全绿后才合并。

**Phase 2 退出门槛（全部满足）：**
- P0 部署暴露与 Web 代理鉴权问题已修复并有负面测试。
- 真实模型能力对比表有新老固定基线、至少一份 holdout 结果及错误分类。
- PostgreSQL 真实并发、Worker 恢复边界与审批幂等得到可信测试。
- Windows Electron 安装包已在至少一个干净环境成功演示并发布。
- 作品集 README、案例研究、截图、使用说明一致；没有遗留的重大虚假能力声明。
- Github Actions 主分支绿；版本冻结到 `v0.2.0-beta`。

## 五、Phase 3：收尾成熟，而非继续做功能

- 修复 Phase 2 新暴露的 blocking / high 缺陷；统一日志、错误、版本、异常状态和稳定性。
- 处理文档漂移、依赖扫描结果、安装 / 卸载 / 数据持久化 / 备份说明、许可和第三方组件清单。
- Electron 发布链路若需要对真实客户分发，再评估代码签名；未签名不可伪装成已签名的可信安装程序。
- 发布 `v1.0.0-demo` 或 `v0.3.0-stable-demo`（名称要与真实能力相符），封存已知限制。
- **停止增量功能开发，等待真实用户、客户面试或 Upwork 项目反馈。** 不提前建设 RBAC/SSO、多租户、大量集成、通用 Agent Builder、Kubernetes、跨端自动升级。

## 六、下次审阅需要提供的证据

只要把修改推送 GitHub，无须重述项目背景。告知审核分支/commit 即可。最好同时提供：
- 对比基线的 commit diff / PR 列表；
- GitHub Actions 最新绿色 run URL；
- real-model evaluation JSON 和 metric comparison；
- Electron 安装包的 Release 链接及干净 Windows 环境运行证据；
- 修改后的 README、截图及已知限制。

我将按此计划逐条标记：PASS / PARTIAL / FAIL / NOT TESTED，重点复核之前的 P0 问题，不以“新增文件存在”代替真实验收。
