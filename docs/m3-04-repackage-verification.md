# M3-04：从固定提交重建可发布包与机器可验证走查（2026-11-15）

任务编号：Phase 3 M3-04（`docs/phase3-plan.md` §6）。本文记录其中**机器可执行的
部分**：从固定提交重新构建 installer、打包产物校验（compose 一致性 / 无凭据 /
SHA-256）、以及一次性的冷启动与热启动 Docker 走查。真人干净机安装走查
（`docs/installer-verification.md` §1–§7 的点选步骤）仍按 D2 口径由人执行，本文
不代写、不冒充其结果。

## 1. 构建来源与版本对应

| 项 | 值 |
|---|---|
| 源码提交 | `81a0278`（`81a02783a3034838cc79fc366e6602611a3ab4f3`，"fix: the README's Compose-backed spec count follows the M2-04 additions"） |
| 构建分支 / worktree | `m3-04-repackage`，`E:\OpsPilot-worktrees\m3-04-repackage`，构建时工作区干净（无未提交改动） |
| 产物 | `desktop/release/OpsPilot-Setup-0.1.0-x64.exe` |
| 产物字节数 | 126,045,189（约 120.2 MB） |
| 产物 SHA-256 | `71ac5ac0adcb543fc96810c96085fbb66bbb73260c92d85325a6927997ee9a7b` |
| blockmap SHA-256（前 16） | `23c76a0eb4361597` |
| 版本号 | 0.1.0（取自 `desktop/package.json`，与旧包同版本号——见第 5 节） |
| 构建命令 | `cd web && npm ci && npm run build`；`cd desktop && npm ci && npm run package`（= `build:web` + `tsc` + `stage-stack` + `electron-builder --win --x64`） |
| 构建环境 | Windows 11 Pro 10.0.26300，Node v24.16.0，npm 11.13.0，electron-builder 26.18.0，Electron 44.11.3 |

与 2026-11-14 D2 走查所用旧包（119,227,780 字节，SHA-256 `565206f7…`）相比，本包
更大（约 +6.8 MB），来源是 `81a0278` 相对旧包基线新增的 M2-01…M2-04 功能与随之上
涨的 dashboard 打包体积。**旧包保留、不替换、不下架**：它对应 D2 走查记录，是当时
的验收物；本包是面向 Phase 3 收尾（M3-05 发布交接）的**新候选**，在完成真人走查并
由用户安排发布（`docs/phase3-plan.md` 对外操作边界）之前不产生 Release/下载链接。

## 2. 敏感文件排除的机制与核验

打包上下文分两层，两层都核对过：

1. **`desktop/scripts/stage-stack.mjs` 的白名单**（复制进 `desktop/stage/stack/`
   的东西）：仅 `docker-compose.yml`、`Dockerfile`、`pyproject.toml`、
   `alembic.ini`、`.dockerignore` 五个文件，加 `src/`、`mcp_servers/`、
   `migrations/`、`knowledge/`、`evals/datasets/` 五个目录，加
   `web/.next/standalone/`（打包后再补拷 `public/` 与 `.next/static/`）。清单里
   没有任何 `.env`；脚本末尾还有一道兜底：对整个 stage 树扫描
   `.env` / `.env.*` 文件名，发现即拒绝打包。
2. **`electron-builder.yml` 的 `files` 白名单**：asar 内仅 `dist/**/*` 与
   `package.json`（排除 `**/*.map`）；`stage/stack` 经 `extraResources` 以真实
   目录随包。`e2e/`、`scripts/`、node_modules 均不在包内。

本机构建时的实际环境：worktree 内无 `.env`（`E:\OpsPilot\.env` 只存在于主检出，
不在本 worktree，也不在 stage 白名单与 Docker context 内；`.dockerignore` 明确排
除 `.env`、`.env.local`、`.env.*.local`、`*.pem`、`*.key`）。构建过程未设置、也未
需要任何模型 API key；`OPSPILOT_OPERATOR_TOKEN` 仅在测试中以一次性假值出现，不进
入本次构建环境。

**结论：构建过程与产物都不含凭据。** 核验证据见第 3 节 (b)。

## 3. 产物校验结果

### (a) 打包内 docker-compose 与源码逐字节一致

`desktop/stage/stack/docker-compose.yml`（即安装后
`resources/stack/docker-compose.yml` 的来源）与仓库根 `docker-compose.yml` 做
`diff`：**完全一致，零差异**。差异要解释——没有差异，因为 stage 脚本就是从本
worktree 的同一文件复制的，且构建从清空 stage 开始，不存在陈旧副本。

### (b) 包内无凭据

对解包树 `desktop/release/win-unpacked/`（electron-builder 压缩 installer 的同一
棵树）做了四层扫描：

- **文件名**：`find . -name ".env*"` → 0 个命中（stage 兜底扫描同结果）。
- **字面 key 形态**：对 `resources/stack/` 全树（排除 `node_modules/` 与 Next 的
  `.nft.json` 追溯清单）做正则扫描 `sk-[A-Za-z0-9_-]{20,}`、
  `sk-ant-…`、`-----BEGIN … PRIVATE KEY-----` → 0 个命中。
- **变量引用核验**：`OPENAI_API_KEY` / `ANTHROPIC_API_KEY` 只以
  `${OPENAI_API_KEY:-}`（compose 空默认）与 `settings.py` 的
  `Field(default="", alias=…)` 形式出现——是**读取环境变量的空默认**，不是凭据值。
  `OPSPILOT_OPERATOR_TOKEN` 在 compose 中只有 `:?`（必须由运行环境提供）形态；
  对 `resources/app.asar` 二进制同样扫描 `OPSPILOT_OPERATOR_TOKEN=<值>`、
  `sk-…`、私钥头 → 0 个命中。
- **数据库口令**：仅见本地开发默认值形态 `${POSTGRES_PASSWORD:-opspilot}`（模板，
  非硬编码真实口令；该默认值本身在 `docker-compose.yml` 注释与
  `tests/unit/test_compose_postgres_password_is_configurable.py` 中有明确解释）。
  `postgresql://…:…@` 形态的 4 处命中全部是上述模板或 `settings.py` 的
  `localhost` 开发默认，非真实凭据。

MiniMax 相关 key 只存在于 gitignored 的 `E:\OpsPilot\.env`，未进入本 worktree、
未进入打包上下文、未出现在任何产物文件中。

### (c) SHA-256

见第 1 节表格。校验方式：对 `desktop/release/` 下最终 installer 文件直接计算。

## 4. 冷启动 / 热启动 Docker 走查（一次性 compose 项目名）

按 `docs/installer-verification.md` §2 的口径，对**打包内容物**
（`desktop/stage/stack/`，即安装后的 `resources/stack/`）执行 compose 启动走查。
使用一次性项目名与端口偏移，避开本机正在运行的 `microbubble-agent` 栈（宿主
8000）与 M2-05 会话在用的 `opspilot-demo-test` 项目：

| 项 | 值 |
|---|---|
| compose 项目名 | `opspilot-m304-cold`（经 `COMPOSE_PROJECT_NAME` / `OPSPILOT_COMPOSE_PROJECT_NAME` 覆盖文件默认 `opspilot`） |
| 端口偏移 | `OPSPILOT_API_HOST_PORT=18000`、`OPSPILOT_PG_HOST_PORT=15432`、`OPSPILOT_WEB_HOST_PORT=13000`（均为 `e8e7f6a` 引入的可配置宿主端口） |
| token | 一次性假值 `OPSPILOT_OPERATOR_TOKEN=m3-04-cold-start-throwaway-token`（仅 compose `:?` 渲染所需，用后即弃，非真实凭据） |
| Docker | 29.9.2，Docker Desktop 已启动 |

### 冷启动（首次：构建三个镜像 + 启动）

命令：`docker compose -p opspilot-m304-cold up -d --build --wait --wait-timeout 300 postgres migrate api worker`。

- 结果：**成功**。`postgres` healthy、`api` healthy、`worker` healthy、
  `migrate` exited 0；`curl http://127.0.0.1:18000/ready` →
  `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`。
- 耗时（本机，已有 python:3.12-slim、pgvector/pgvector:pg16 基础镜像与 uv 缓存，
  但三个 OpsPilot 镜像按源码重建）：**compose up 全程约 3.5–4 分钟**（日志时间戳
  12:25:06 起，healthy 全部就绪止）。这落在文档预告的"first launch 5–15 minutes"
  区间内且更快，因为 pip 依赖层有本机缓存；**陌生人机器上无任何缓存时应按文档的
  5–15 分钟预告**。
- 所需外网依赖（冷启动一次性）：Docker Hub 拉取 `python:3.12-slim`、
  `pgvector/pgvector:pg16` 基础镜像；Dockerfile 内 `uv` 同步 Python 依赖
  （PyPI）；electron-builder 构建期另行下载 NSIS 工具链与 Electron 运行时
  （GitHub releases）。**不调用任何模型 API**，构建与启动全程零 LLM 请求。

### 热启动（退出后重启）

命令：先 `docker compose -p opspilot-m304-cold down --remove-orphans`（**不带
`-v`**，对应桌面应用"退出不删数据"的行为），再同参数 `up -d --wait`（不带
`--build`，镜像已存在）。

- `down`：6.6 秒；`up --wait` 到四服务全部就绪：**18.3 秒**；
  `/ready` 立即返回 `{"status":"ready",…}`。落在文档预告的"later launches well
  under a minute"内。
- 容器与卷核对：`down` 后无 `opspilot-m304-cold-*` 容器残留；卷
  `opspilot-m304-cold_postgres-data` / `opspilot-m304-cold_mcp-data` 保留
  （重启留数据的机制层面成立）。

### 本走查与真人走查的边界

本节只覆盖文档口径中**可用命令验证**的部分：镜像可从源码构建、四服务可就绪、
`/ready` 链路通、退出/重启行为、冷热耗时量级。**未覆盖**（留给真人按
`docs/installer-verification.md` §1–§7 执行，不在此代答）：双击 installer 的真实
安装、Electron 窗口启动 dashboard、页面点选（索引 17/17/98、提单、审批门、退款、
409 重放拒绝）、§5 的 netstat/curl 安全姿态（打包产物的该部分已由
`desktop/e2e/packaged-posture.spec.ts` 六项检查覆盖，见第 6 节）。

## 5. 与 v0.1.0 旧包的关系

- 旧包 `OpsPilot-Setup-0.1.0-x64.exe`（SHA-256 前缀 `565206f73b3ef924`，119,227,780
  字节）对应 2026-11-14 的 D2 走查，其记录在 `docs/installer-verification.md` 与
  `docs/progress.md` 的 D2 段落中，**继续有效，不撤销**。
- 本包（SHA-256 `71ac5ac0…`，126,045,189 字节）从 `81a0278` 重建，包含 M2 合并列
  车之后的所有修复与功能（含 host-port / compose 项目名可配置化，即 D2 走查记录的
  两项偏差的修复）。版本号仍为 0.1.0，因为项目尚未发布 0.2.0；**两者的区分以
  SHA-256 为准，不以版本号区分**。
- 发布边界不变：在新包完成真人走查且用户明确安排发布之前，不产生 Release、tag 或
  下载链接。到那时，Release asset 应替换/新增为本包，并同步更新
  `docs/installer-verification.md` 记录页的 installer SHA。

## 6. 打包姿态的既有门禁（本次构建后复跑，全部通过）

CI 的 `desktop-package` job 对每个 push 都做同样的事：构建 → 以
`OPSPILOT_PACKAGED_REQUIRED=1` 跑 `packaged-posture` 六项检查。本任务在构建完成后
于本机复跑了同一门禁：`cd desktop && OPSPILOT_PACKAGED_REQUIRED=1 npx playwright
test --project=lifecycle packaged-posture`（并按 CI 同样方式在环境中放了一次性假
token `OPSPILOT_OPERATOR_TOKEN`，使第 5 项"构建环境中的 token 不出现在任何打包文
件"检查真正执行而非跳过）——**6 passed，0 skipped**：loopback 绑定、打包 compose
与仓库一致、无 `.env`、假 token 不在任何打包文件、asar 内安全策略与源码一致、图
标资源完整。第 3 节的独立扫描与该门禁互为印证。

## 7. 未覆盖项（如实列出）

1. 真人干净机安装与页面点选走查（§1–§3.5）：未由本任务执行；D2 已有一次 2026-11-14
   记录，但那是对旧包。新包如需对等记录，须再走一次。
2. "另用新 checkout 验离线流程"（M3-04 验收的后半句）：本任务未覆盖，属后续工作
   （需要在无模型 API 的前提下从新 checkout 走通安装/启动，或明确其不可行性）。
3. installer 的 SmartScreen / 代码签名行为：未签名（与旧包一致），文档已按"可忽略
   的警告"口径书写，本次未重新验证。
4. 产物的可复现构建（byte-identical rebuild）：未验证；electron-builder 的压缩与
   时间戳不保证逐字节可复现，本文以 SHA-256 记录本包而非承诺复现性。
