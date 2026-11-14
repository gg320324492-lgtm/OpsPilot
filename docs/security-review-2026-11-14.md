# OpsPilot 安全复审报告 — 2026年11月14日

- 范围：仓库 `main`（基线 `6810b4f`，本次复审分支 `security-review-2026-11-14`）。
- 方法：人工代码走查（静态审读，无攻击性网络测试），覆盖六个领域：密钥与凭据管理、API 攻击面、审批工作流、MCP 工具层、依赖与容器、web/desktop 前端。
- 结论摘要：**未发现 High 或 Medium 级别的新问题**。认证、审批、幂等、注入面等此前多轮加固（A1–A4、D3–D6 等）在本次走查中全部得到确认。发现并修复 2 项 Low 问题；另有 4 项 Info 级观察，仅记录不改代码。
- 已修复（本次提交）：
  1. [L1] operator token 无最小长度校验 → `api/auth.py` 新增 `WeakOperatorToken`（< 16 字符拒绝启动）。
  2. [L2] API 响应缺安全响应头 → 新增 `api/headers.py::SecurityHeadersMiddleware`（nosniff / DENY / no-referrer / `/api/*` no-store）。
- 本报告不包含任何真实凭据值；所有 token 均以变量名引用。

---

## 严重度汇总

| 级别 | 数量 | 编号 |
| --- | --- | --- |
| High | 0 | — |
| Medium | 0 | — |
| Low（已修复） | 2 | L1, L2 |
| Info（仅记录） | 4 | I1–I4 |
| 待验证 | 0 | — |

---

## Low（已修复）

### L1 — operator token 缺少最小长度校验

- **位置**：[src/opspilot/api/auth.py](../src/opspilot/api/auth.py)（`ensure_operator_token_configured`，修复前仅做空值检查）。
- **影响**：`OPSPILOT_OPERATOR_TOKEN` 设置为 1–15 个字符（如本仓库测试里出现过的占位值 `"t"`）即可通过启动校验。该 token 是全部写操作（含 `billing.issue_refund` 审批）的唯一凭据；在未加网络层防护的部署里，短 token 可被手工枚举。
- **修复**：`ensure_operator_token_configured` 现对长度 < `MIN_TOKEN_LENGTH`（16）的 token 抛出新的 `WeakOperatorToken`，`create_app` 因此拒绝构建、uvicorn 非零退出。阈值与生成命令已同步写入 [.env.example](../.env.example) 与 [docs/api-contract.md §8](api-contract.md)。
- **测试**：`tests/unit/test_operator_token_minimum_length.py`（6 个用例：空值仍为 `MissingOperatorToken`、恰好 16 字符通过、短值拒绝、错误信息含变量名与修复指引、真实 `token_urlsafe(32)` 通过、`create_app` 拒绝启动）。

### L2 — API 响应缺少默认安全响应头

- **位置**：[src/opspilot/api/app.py](../src/opspilot/api/app.py)（`create_app`，修复前仅安装 CORS 中间件）。
- **影响**：所有响应（含携带运行轨迹、审批队列、工单正文的 `/api/*`）都不带 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy`、`Cache-Control`。浏览器/中间缓存层失去三道廉价防线：内容嗅探、点击劫持框架嵌入、已认证响应入缓存。这不绕过 bearer token，但属于纵深防御缺口。
- **修复**：新增 [src/opspilot/api/headers.py](../src/opspilot/api/headers.py) `SecurityHeadersMiddleware`（纯 ASGI，无缓冲）：所有响应带 `nosniff` / `DENY` / `no-referrer`；`/api/*` 额外带 `Cache-Control: private, no-store`；`/health`、`/ready` 探针不带缓存指令（它们无凭据，且是 healthcheck 热路径）；路由自行设置的同名头优先于默认值。
- **测试**：`tests/unit/test_api_security_headers.py`（5 个用例：静态头存在、`/api/*` no-store、探针无缓存头、路由自定义头不被覆盖、真实 `create_app` 装配后生效）。

---

## Info（仅记录，不建议当前行动）

### I1 — CI 中的明文测试 token

- **位置**：[.github/workflows/ci.yml](../.github/workflows/ci.yml)（3 处 `OPSPILOT_OPERATOR_TOKEN: ci-disposable-token-not-a-secret`）。
- **说明**：值仅用于一次性 runner 上拉起 compose 栈，名称即声明其非秘密，且 A3 守卫（secrets 扫描/输出脱敏）已覆盖。无需修改；列出仅为完整性。

### I2 — `/docs`、`/openapi.json` 随应用暴露

- **位置**：[src/opspilot/api/app.py:129](../src/opspilot/api/app.py)（`FastAPI(...)` 未关闭 docs/redoc/openapi）。
- **说明**：文档端点自身不携带数据，仅描述路由结构；所有真实数据端点都在 bearer token 之后。若未来部署到不可信网络，可用 `docs_url=None, redoc_url=None, openapi_url=None` 关闭，属产品决策，本次不动。

### I3 — API 无内建速率限制

- **位置**：[src/opspilot/api/app.py](../src/opspilot/api/app.py)（中间件仅 CORS + 安全头）。
- **说明**：认证失败与写操作目前不限速。Phase 1 定位为单操作员本地部署（见 `docs/api-contract.md` §8），速率限制属部署层职责（反向代理/网关）。L1 修复后短 token 枚举已不可行，长 token 暴力空间足够大；建议未来公网部署时在网关层解决。

### I4 — 审批 TTL=0 时 pending 审批永久存留

- **位置**：`APPROVAL_TTL_SECONDS`（[src/opspilot/settings.py:383](../src/opspilot/settings.py)，默认 3600s；文档已声明 0 = 关闭清扫）。
- **说明**：操作员显式设 0 是配置选择而非缺陷；`expire_pending` 的条件更新已保证与人工决策竞争安全。仅记录。

---

## 已确认健壮的方面（走查通过，未发现问题）

以下各项均有对应守卫测试，本次复核确认其声明与实现一致：

1. **认证一致性**：全部 4 个业务路由（`approvals`、`runs`、`tickets`、`knowledge`）在 `APIRouter` 级声明 `dependencies=[Depends(require_operator)]`；豁免仅 `/health`、`/ready` 两个探针。比较使用 `secrets.compare_digest`；空 token 拒绝启动（本次补齐短 token 同样拒绝）。守卫：`tests/integration/test_api_tickets.py`、`tests/unit/test_compose_settings_are_wired.py`。
2. **注入面**：持久层全程 SQLAlchemy 表达式（无 `text()` 字符串拼接）；stdio MCP 命令经 `shlex` 解析为 argv 直接 spawn，无 `shell=True`；knowledge 目录为编译期常量，无用户路径输入。守卫：`tests/unit/test_layering.py` 等。
3. **审批完整性**：决策为条件更新 `WHERE id=? AND status='pending'`，重复点击得 409；`has_approved` 绑定 `tool_call_id` 而非 `run_id`，跨交易授权不可行；`expire_pending` 与人工决策的竞争由同一谓词保证安全；`decided_by` 仅来自 token，请求体禁止携带。守卫：`tests/integration/test_api_approvals.py`、`tests/integration/test_approval_expiry.py`。
4. **幂等键**：key 由运行时确定性派生（`refund:{run_id}:{transaction_id}`），模型无法自选 key 绕过查重；billing server 的 check-then-write 在进程内写锁下原子执行，重放返回 `replayed: true`。守卫：`tests/unit/test_policies.py`、`mcp_servers/billing/server.py` 文档。
5. **CORS**：来源来自 `OPSPILOT_CORS_ORIGINS`，`*` 在 settings 校验器与使用点双重拒绝；方法为显式白名单。web 侧四个写路由另有同源 Origin/Host 校验（`web/src/lib/request-origin.ts`），locale 路由的 `returnTo` 做了协议相对与同源双重校验，无开放重定向。守卫：`tests/integration/test_api_cors.py`、`web/e2e/write-origin.spec.ts`。
6. **密钥管理**：`.env` 在 `.gitignore` 与 `.dockerignore` 双重排除；compose 中 token 用 `:?` 强制非空；web 端 token 仅存在于服务端 Route Handler，永不进浏览器 bundle；desktop 端 token 写入用户目录并 `chmod 0o600`；错误处理统一走 envelope，未捕获异常返回 `trace_id` 而非内部细节；日志脱敏守卫（`redactComposeSecrets`）在 desktop e2e 中断言。守卫：`desktop/e2e/security.spec.ts`、`packaged-posture.spec.ts`。
7. **容器与 CI**：三个 Dockerfile 均多阶段、非 root 用户（Python 侧 uid 1001，web 侧 nextjs）、镜像 tag 固定（`python:3.12-slim`、`node:22-slim`、`pgvector/pgvector:pg16`）；compose 三个发布端口全部显式绑定 `127.0.0.1`（有专门守卫测试）；CI 权限收敛为 `contents: read`，eval-live 的 provider key 仅经 GitHub secrets 注入且 workflow 仅手动触发。守卫：`tests/unit/test_compose_settings_are_wired.py::test_every_published_port_is_bound_to_loopback`、`tests/unit/test_dependency_declarations.py`。
8. **桌面壳**：`contextIsolation: true`、`nodeIntegration: false`、`sandbox: true`、`webviewTag: false`（`desktop/src/security-policy.ts`）；导航限制在本应用 origin，`setWindowOpenHandler` 一律 deny；IPC 面仅 2 个无凭据通道。守卫：`desktop/e2e/security.spec.ts`。
9. **XSS 面**：web/desktop 源码中无 `dangerouslySetInnerHTML`、`innerHTML`、`eval`、`new Function`；无 `localStorage`/`sessionStorage` 凭据存储。

---

## 复审方法与覆盖说明

- 走查文件：`src/opspilot/api/**`（全部路由、auth、errors、app 工厂）、`src/opspilot/settings.py`、`src/opspilot/adapters/tools/{mcp_gateway,mcp_stdio,composite_gateway}.py`、`src/opspilot/adapters/persistence/{repositories,db}.py`、`src/opspilot/domain/policies.py`、`src/opspilot/agents/runtime.py`（门控路径）、`mcp_servers/**`、`docker-compose.yml`、`Dockerfile`、`web/Dockerfile`、`.github/workflows/{ci,eval-live}.yml`、`desktop/{electron-builder.yml,src/security*.ts,src/main.ts,src/ipc.ts,src/runtime/operator-token.ts}`、`web/src/lib/{client.ts,request-origin.ts}`、`web/src/app/api/**`、`.env.example`、`.dockerignore`、`scripts/`。
- 交叉验证：`grep` 全仓库扫描 `shell=True`、`eval/exec/pickle/yaml.load`、`dangerouslySetInnerHTML`、`localStorage`、硬编码密钥模式（`sk-`/`AKIA`/`ghp_`/PEM 等）——均无命中（测试占位值除外，且带 `noqa: S105` 声明）。
- 本报告基于静态走查；未执行渗透测试、模糊测试或依赖漏洞扫描（依赖审计由 CI 的 `desktop-check`/npm audit 路径与 `.tmp-audit` 记录另行覆盖）。
