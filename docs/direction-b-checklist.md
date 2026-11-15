# 方向 B（产品化/演示强化）可执行清单 — 2026-11-16

**依据：** [phase4-draft.md](phase4-draft.md) §3（方向 B 定义与 B1–B7 工作项）、
[portfolio-acceptance-2026-11-15.md](portfolio-acceptance-2026-11-15.md) §7（第三轮复评 79/100）、
[installer-interactive-walk-2026-11-15.md](installer-interactive-walk-2026-11-15.md)（真人走查脚本）、
[release-checklist.md](release-checklist.md)（发布清单与 §7 发布后核验）、
[upwork-profile-entry-en.md](upwork-profile-entry-en.md)（Upwork 素材与合规红线）。

**前提状态（2026-11-16）：** 方向 A 已按用户预案关闭（五个模型家族均远离 10/15，
GLM-5.3-Flash S1 0/4 触发关闭条件）；v0.2.0 已发布（tag `656bbd3`，CI 绿）；
作品集验收 79.25 ≈ 79/100，未通过。方向 B 的目标是清掉维度 3/4/7 的收尾缺口，
把"已发布"变成"交付闭环"。

**总原则：** 每一项都要有可验证的完成标准；不造假、不冒充、不超范围声称
（合规红线见 [upwork-profile-entry-en.md](upwork-profile-entry-en.md) 开头声明与
[claims-audit-2026-11-15.md](claims-audit-2026-11-15.md)）。

---

## 排除项（本清单明确不做）

| 排除项 | 原因 |
|---|---|
| Python lockfile（`uv.lock`，原 B5） | 用户已定暂不引入 |
| 真实模型重测 / 达标线冲刺 | 方向 A 已关闭（2026-11-16，GLM S1 0/4 触发预案） |
| 任何需要花钱的项（v4-pro 实测、付费 API、代码签名证书等） | v4-pro 上游 402 配额拒绝；签名证书等均涉及支出，不在本清单范围 |
| 真实模型演示片段（原 B1） | 依赖方向 A 达标；A 已关闭，故**暂缓**（见 DBC-06，仅在未来重启 A 且达标时恢复） |

---

## 任务总览

| # | 任务 | 执行者 | 并行性 | 优先级 |
|---|---|---|---|---|
| DBC-01 | 真人交互式走查（新包，非作者） | 用户（安排走查人）+ Claude 备料/归档 | 可与 DBC-02/03 并行 | 高 |
| DBC-02 | 文档尾差清零 | Claude | 无依赖，立即可做 | 高（建议首项） |
| DBC-03 | 发布后点击检查 | Claude（GitHub 页面核对可由用户复核） | 依赖 DBC-02 完成 | 高 |
| DBC-04 | Upwork 发布准备（不实际发布） | Claude 定稿 + 用户执行发布 | 可与 DBC-02/03 并行 | 中 |
| DBC-05 | 多环境配置化（dev vs 演示） | Claude | 独立，可并行，低优先 | 低（可选） |
| DBC-06 | 真实模型演示片段 | — | **暂缓**（依赖已关闭的方向 A） | 暂缓 |

---

## DBC-01 真人交互式走查（新包，非作者）

**目标：** 对已发布的 v0.2.0 安装包完成一次"真人、双击、可交互"的干净机走查，
坐实维度 4（陌生人复现）9.0 锚点——这是第三轮复评中"严格口径下未坐实"的点。

**执行者：** **用户**负责安排走查人（要求：未参与本项目构建；Windows 10/11 x64；
已装 Docker Desktop；约 6 GB 空闲磁盘）；**Claude** 负责备料与结果归档。

**步骤：**

- [ ] Claude 备料三件套发给走查人：
  1. [installer-verification.request.md](installer-verification.request.md)（说明信）；
  2. 走查脚本 [installer-interactive-walk-2026-11-15.md](installer-interactive-walk-2026-11-15.md)
     （交互式：双击安装、SmartScreen、桌面图标、菜单语言切换、退出/重启数据保留）；
     如走查人愿意，可加做 16 项检查脚本 [installer-verification.md](installer-verification.md)；
  3. 安装包信息：`OpsPilot-Setup-0.2.0-x64.exe`，130,075,633 字节，
     SHA-256 `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`，
     下载地址见脚本文档头部（下载后先核对大小与 SHA-256）。
- [ ] 走查人按脚本第 0–5 部分执行（全程约 20–30 分钟，冷启动等 Docker 5–15 分钟），
      在表格"实际记录"列如实填写，第 5 部分主观评价自由填写。
- [ ] 走查人把填好的文档交回项目所有者。
- [ ] Claude 将结果**追加**到 [installer-verification.md](installer-verification.md)
      （不覆盖 2026-11-14 旧记录），并在 progress.md 记一条。

**涉及文件：** docs/installer-interactive-walk-2026-11-15.md（脚本，不改）、
docs/installer-verification.md（追加记录）、docs/installer-verification.request.md、
docs/progress.md。

**完成标准（可验证）：**
- [ ] 走查人非项目构建参与者，记录中可看出是真人点选（含实际观察与主观评价，非全"通过"套话）；
- [ ] installer-verification.md 出现新追加的 0.2.0 包走查记录（含日期、走查人环境、每步实际记录）；
- [ ] 若发现阻塞问题，登记为独立 issue/条目并评估是否需要修复后重走查。

**工作量：** 走查人 20–30 分钟 + 冷启动等待 5–15 分钟；Claude 备料/归档约 0.5 小时。
**依赖：** 无（材料已就绪，随时可发）。**并行性：** 可与 DBC-02/03/04 并行。
**注意：** release-checklist §3 写明"未走查就发布属可接受但需自担风险的决定"——
当前 v0.2.0 已发布，本次走查是**发布后补验**，若发现问题按问题严重度决定是否
发 v0.2.1 修复。

---

## DBC-02 文档尾差清零

**目标：** 清掉第三轮复评（79/100 复评记录）与文档一致性审计点名的、当前仍存在的
尾差，使材料一致性与版本交付（维度 7）从 9.0 冲 10。

**执行者：** Claude。**依赖：** 无。**并行性：** 可立即开始（建议首项）。

**已知尾差清单（逐项勾选；前两项"142/142"与 README evals 目录树、"Nothing here is
published yet"过时句已在近期 commit 修复，列出仅为核对不复做）：**

- [x] （已修，核对即可）release-notes "142/142" 已改逐批数字；
- [x] （已修，核对即可）README evals 目录树已改为 "the harness itself is src/opspilot/evals/"（README.md:587）；
- [x] （已修，核对即可）"Nothing here is published yet" 过时句已不存在；
- [ ] **tag 指向统一**：release-checklist.md 决策记录（L27 "指向 8133a89"）、
      §0 表（L19 "从 v0.2.0 tag（2b96e70…）构建"）、§4.4（L85 "指向 2b96e70"）与
      release-notes-v0.2.0-draft.md L105（"built from commit 2b96e70"）——统一口径为：
      **构建 checkout = `2b96e70`，最终 tag/Release = `656bbd3`，两者差异为纯文档 commit
      （bbf0c79、656bbd3）**，各处按此注明，消除"同一 tag 两个指向"的矛盾。
- [ ] **release-checklist.md §4.4**：Release 创建步骤仍标【待用户执行】，实际 v0.2.0
      已发布——改为 ✅ 已完成并回填 Release 链接；§2/§3/§6 中已完成的步骤同步勾选，
      仅保留真正待用户执行的条目。
- [ ] **walkthrough-kit.md 旧包信息**：仍是 0.1.0 旧包（`OpsPilot-Setup-0.1.0-x64.exe`、
      126,045,189 B、SHA-256 `71ac5ac0…`、CI run 43635841749）——更新为 0.2.0 包
      （130,075,633 B、`2461f146…`）或明确标注"历史记录（0.1.0）"并另附 0.2.0 版。
- [ ] **收尾核对**：全仓 grep `8133a89|2b96e70|71ac5ac0|126,045|0.1.0-x64` 确认无
      未标注的旧指向残留（历史记录类可保留但须有"历史"标注）。

**涉及文件：** docs/release-checklist.md、docs/release-notes-v0.2.0-draft.md、
docs/walkthrough-kit.md（如走查材料需要，联动 installer-verification-new-package-template.md）。

**完成标准（可验证）：**
- [ ] 上述 grep 模式在 docs/ 与 README 中无"未标注历史"的残留；
- [ ] tag 指向全文一致（构建 checkout 与最终 tag 两个概念分得清）；
- [ ] 两个守卫测试通过：
      `py -3.12 -m pytest tests/unit/test_doc_line_citations_point_at_the_code.py tests/unit/test_the_documented_counts_agree_with_the_tree.py -q`；
- [ ] 全量离线套件绿：`py -3.12 -m pytest -q`。

**工作量：** 约 1–2 小时。**依赖：** 无。

---

## DBC-03 发布后点击检查

**目标：** 对外可见的一切链接、数字、模型标识逐一核对，确保陌生人从 README 或
Release 页进入后看到的信息自洽且可达（phase4-draft B4；release-checklist §7 方向）。

**执行者：** Claude（本地核对 + 网络可达性）；Release 页面最终状态可由用户复核。
**依赖：** DBC-02（尾差修正后数字才稳定）。**并行性：** 与 DBC-01/04 可并行。

**步骤：**

- [ ] **README 链接可达性**：提取 README.md 全部外链与仓库内相对链接
      （docs/*、evals/results/*、web/public/showcase/* 等），逐一点击/请求确认
      200 或文件存在；重点：Release 链接（v0.1.0、v0.2.0）、demo 视频与字幕、
      installer 下载链接、CI badge。
- [ ] **Release 页信息核对**：v0.2.0 Release 页的 asset 文件名、字节数
      （130,075,633）、SHA-256/digest 与本地记录一致；Release notes 正文数字
      （测试数、评测数、逐批准确率）与仓库文档一致。
- [ ] **模型标识一致性**：README、limitations.md、验收表、release-notes、案例研究
      中出现的模型名/版本（DeepSeek V4.1-Flash、MiniMax-M3.1-Flash、
      deepseek-v4.1-flash-max、GLM-5.3-Flash、Claude Sonnet 5.5）与各自 run 报告
      中的标识一致，无串写。
- [ ] **数字一致性**：README Status 段、limitations.md、
      portfolio-acceptance-2026-11-15.md、release-notes 中的关键数字同源
      （开发集四跑 3/15、2/15、2/15、3/15；holdout 10/32；safety unsafe=0；
      测试 1422 passed / 19 skipped；arg validity 等），无"同一指标两个数"。
- [ ] **记录**：核对结果写入一份简短检查记录（可追加到 release-checklist.md §7
      或独立 docs/post-release-click-check-2026-11-16.md），含每项核对方式与结果。

**涉及文件：** README.md、docs/release-notes-v0.2.0-draft.md、
docs/release-checklist.md（或新增检查记录文件）、docs/limitations.md、
docs/portfolio-acceptance-2026-11-15.md（只读核对）。

**完成标准（可验证）：**
- [ ] 检查记录存在，覆盖上述四类核对，每项有"通过/发现问题"结论；
- [ ] 发现的失效链接或数字不一致已修复（或登记为待办并注明原因）；
- [ ] 两个守卫测试 + 全量 pytest 仍绿。

**工作量：** 约 0.5–1 小时。**依赖：** DBC-02。

---

## DBC-04 Upwork 发布准备（不实际发布）

**目标：** 把 Upwork 作品条目与平台操作步骤准备到"用户拿到即可粘贴发布"的状态；
**本任务不执行任何对外发布**（phase4-draft B7 的前置；发布动作本身属用户）。

**执行者：** Claude 定稿材料；**用户**执行实际平台发布。**依赖：** 无。
**并行性：** 可与 DBC-02/03 并行。

**步骤：**

- [ ] **description 定稿**：在 [upwork-profile-entry-en.md](upwork-profile-entry-en.md)
      的三个变体中确定一个（建议采用文内标注的合规变体：保留真实模型安全结论、
      595 字符 ≤ 600 上限），把"定稿版本"单独标出，避免粘贴时选错超限版本。
- [ ] **skill tags 定稿**：按文档建议的安全集合（Python · FastAPI · Next.js ·
      LLM Prompt Engineering + 第 5 个按实际经验选 RAG 或 LangChain），确认 ≤ 5 个
      且无过度声称。
- [ ] **title / role 复核**：title ≤ 70 字符（现 66）、role ≤ 100 字符（现 99），
      与 description 口径一致（不声称付费客户/收入/未测量节省；退款为模拟）。
- [ ] **配图/附件清单**：确认可附材料（如 showcase 截图或 demo 链接）与平台
      附件规则不冲突；demo 视频按用户决定仅 GitHub 展示、不上传外部平台。
- [ ] **平台操作步骤卡**：写一份给用户的简短步骤（在 Upwork 哪个入口、填哪些字段、
      粘贴哪段、发布前自查哪些红线），例如存为 docs/upwork-publish-steps.md 或
      追加到 upwork-profile-entry-en.md 末尾。
- [ ] 用户确认材料后，由**用户**在平台执行发布（本清单不含此步的执行）。

**涉及文件：** docs/upwork-profile-entry-en.md、docs/service-offerings-en.md（口径
参照）、新增/追加的发布步骤说明。

**完成标准（可验证）：**
- [ ] 存在明确的"定稿"标记（不再有三个变体并存的歧义）；
- [ ] 定稿 description 字符数 ≤ 600，title/role 在限内，技能标签 ≤ 5；
- [ ] 发布步骤卡存在，用户可照做；本仓库内无任何自动发布动作发生。

**工作量：** Claude 约 0.5–1 小时；用户发布另计。**依赖：** 无。

---

## DBC-05 多环境配置化（dev vs 演示）（**已完成**，2026-11-16）

**目标：** 提供 compose override / 环境分层的最小示例，让"开发"与"演示"两套
配置分明，不改默认行为（phase4-draft B6）。属锦上添花，排在核心项之后。

**执行者：** Claude。**依赖：** 无（但建议在 DBC-02/03/04 完成后再做）。
**并行性：** 独立可并行。

**步骤：**

- [x] 盘点现有 compose 与环境变量入口，确定 override 方案；
- [x] 提供演示态最小差异，不引入凭据、不改默认行为；
- [x] README 增补用法说明；
- [x] 启动验证：compose 渲染双路径核验（基线渲染逐字节不变、demo 渲染
      各项差异符合预期），离线测试套件绿。

**实际方案（2026-11-16）：** 新增 [`docker-compose.demo.yml`](../docker-compose.demo.yml)
——第二 Compose 文件，不叫 override：带 `-f` 合并才生效，不带时默认路径完全不变。
演示差异：独立项目名 `opspilot-demo`（容器/网络/镜像前缀与开发栈隔离，镜像
tag 前缀经 `OPSPILOT_IMAGE_PREFIX` 锚点跟随项目名，复用 DBC-07 的版本化
机制）；演示端口 API 18001 / 仪表盘 13001 / PostgreSQL 15433（**经 shell
环境传入**——Compose 在合并文件前用 shell 环境插值 `ports:`，文件内
`environment:` 块移动不了已发布端口，README 命令已带这三个变量）；fake
脚本切到 `already_refunded`（探测到已退款、全程不提议写操作——"闸门根本
不必触发"的演示结尾，比退款更有说服力，已在 fixture 层核实）；`LOG_LEVEL=WARNING`。
不设任何凭据、不切真实 provider，仪表盘的 scripted-provider 披露保持原样。

**涉及文件：** `docker-compose.yml`（仅注释：前缀跟随机制的调用方清单）、
新增 `docker-compose.demo.yml`、`.env.example`（scenario 段补演示指引）、
README.md（"A demo stack beside the development one" 小节）。

**完成标准（可验证）：**
- [x] override 文件存在且默认启动路径完全不变（不带 override 时行为与现在一致）；
- [x] 演示配置渲染验证通过（项目名、端口、scenario、日志级别、镜像前缀、
      浏览器 API 地址全部符合预期；未连真实模型）；
- [x] 全量离线测试套件绿（compose 相关 25 passed、文档守卫 14 passed）。

**工作量：** 约 1–2 小时。**依赖：** 无。

---

## DBC-06 真实模型演示片段（**暂缓**）

**目标（原 B1）：** 90–180s 真实模型浏览器录屏 + 字幕，与 run 证据一一对应。

**状态：** **暂缓。** 该项依赖方向 A 达标（真实模型 tool selection ≥10/15）；
方向 A 已于 2026-11-16 按用户预案关闭（五个模型家族均远离门槛），本项随之搁置。
维度 3 的 +1.25 预期增量在 A 重启并达标前放弃。若未来重启方向 A 且有模型达标，
再按 phase4-draft B1 原始定义恢复本项。**当前不做任何替代性摆拍**——展示体验
维度维持现有 fake-provider 素材的如实披露口径。

---

## DBC-07 升级场景的镜像版本一致性（**已修复（代码侧）**，2026-11-16）

**目标：** 安装版 compose 用固定镜像 tag（`opspilot-api`、`opspilot-worker`、
`opspilot-migrate`），`docker compose up` 在镜像已存在时不重建。在装过旧版本
（或用源码构建过同名镜像）的机器上升级，会静默复用旧镜像、跑旧 API——2026-11-15/16
真人走查实测：新装 v0.2.0 后「导出摘要」返回 404，因为本机残留 2026-11-14 构建的
`opspilot-api`（缺 export 路由）。干净机器首次安装不受影响。

**建议做法：**

1. compose 镜像 tag 带版本号（如 `opspilot-api:0.2.0`），或在首次启动时 `build`；
2. 启动/升级时校验镜像版本与安装包版本一致，不一致则重建；
3. 失败路径给出可读提示（镜像陈旧），不要静默降级。

**证据：** [installer-verification.md](installer-verification.md) 的
「Interactive walk on the published v0.2.0 installer」节。

**修复（2026-11-16，方案 1 的版本化 tag）：**

- `docker-compose.yml` 的四个构建服务（migrate/api/worker/web）显式命名镜像
  `${OPSPILOT_IMAGE_PREFIX:-opspilot}-<service>:${OPSPILOT_IMAGE_TAG:-local}`；
- 桌面壳（`desktop/src/main.ts`）注入 `OPSPILOT_IMAGE_TAG=<app 版本>` 与
  `OPSPILOT_IMAGE_PREFIX=<compose 项目名>`，升级后是新 tag → Compose 必构建，
  同版本才是可复用的同树镜像；
- `web/e2e/stack.ts` 为具名项目同步设置 `OPSPILOT_IMAGE_PREFIX`，e2e 栈与
  默认栈不共享镜像；
- 回归测试：`desktop/e2e/compose-commands.spec.ts` 新增两条断言（读
  compose 文件，防止有人删掉版本 tag）；`desktop` lifecycle 121 passed /
  `web` typecheck / compose 相关 python 测试全绿。

**待办：** 下一次打包后用升级路径实测（先装旧版再装新版，确认镜像重建）；
在那之前本项标「代码已修复、待构建验证」。

---

## DBC-08 被杀进程后的容器回收（**已修复（方案 1）**，2026-11-16）

**目标：** 第三方走查（[`docs/installer-walk-third-party-2026-11-16.md`](installer-walk-third-party-2026-11-16.md)）
发现的唯一功能性缺陷：应用进程被杀（`Stop-Process`，非正常退出路径）后，
opspilot 容器因 `restart: unless-stopped` 持续运行 10 分钟以上不回收，需手动
`docker compose -p opspilot down`。正常退出路径的 `compose down` 是好的
（owner 走查 4.1 已验证）；缺口只在异常终止路径。

**建议做法（任选其一或组合）：**

1. 容器 restart policy 从 `unless-stopped` 改为 `on-failure`（容器随宿主
   Docker 守护进程存活，但应用被杀后不会无限重启）；
2. 安装版附带一个轻量看护（计划任务/托盘退出钩子），检测 OpsPilot 进程
   消失后执行 `docker compose -p opspilot down`；
3. 至少在文档/失败页提示回收命令。

**证据：** [installer-verification.md](installer-verification.md) 的
「Third-party interactive walk of the published v0.2.0 installer」节。

**修复（2026-11-16，方案 1）：** `docker-compose.yml` 的 postgres / api /
worker / web 四个长期运行服务从 `restart: unless-stopped` 改为
`restart: on-failure`（migrate 保持 `"no"` 不变——它本就是一次性任务）。
效果：正常退出路径不变（壳的 `compose down` 照旧）；应用进程被杀后，健康
运行的容器不再是「失败」，Docker 引擎不会把它们无限拉起——这正是走查实测
的缺口。崩溃的容器仍会重启，保留原 policy 的本意。

**修复有效范围（升级路径实测后修正）：** on-failure 修复的是「容器自身
崩溃后的重启语义」，它**不能**回收「应用进程被杀后仍在健康运行」的容器
——restart policy 只在容器退出时生效，外部进程死亡不影响运行中的容器
（已用 `docker run --restart on-failure alpine sleep 120` 对照验证）。
真正闭环该缺口的是 **DBC-07 的版本化镜像 tag**：升级后新版本 compose 用
新镜像 tag，旧镜像不再被复用，配合「升级先卸旧版」的发布口径，残留风险
收敛到单版本内。对已装机器，手动 `docker compose -p opspilot down` 仍是
回收方式。剩余缺口（进程被杀后的自动回收）记录在案，属未来增强（方案 2
的看护进程），不再阻塞发布。

**验证（升级路径实测，0.2.0 → 0.2.1）：** 覆盖安装后注册表版本 0.2.1、
包内 compose 含 on-failure 与版本化 tag；冷启动 70 秒；容器全部使用
`opspilot-*:0.2.1` 新镜像（旧 `latest` 不再被复用——DBC-07 的原始缺口
消除，export 路由 200）；golden path 走通（批准 → completed、重复批准
409、无 token 401）；`docker stop` 后容器保持 Exited 不复活（on-failure
语义实证）。

---

## 建议执行顺序

```
第 1 步  DBC-02 文档尾差清零        （Claude，无依赖，立即开始；建议首项）
第 2 步  DBC-03 发布后点击检查      （Claude，依赖 DBC-02）
  并行  DBC-01 真人走查备料 → 用户安排走查人执行 → Claude 归档
  并行  DBC-04 Upwork 材料定稿 + 发布步骤卡（Claude）→ 用户择机发布
第 3 步  DBC-05 多环境配置化        （可选，核心项完成后）
       DBC-06 暂缓（随方向 A 关闭）
```

**理由：** DBC-02 是纯文档修正、零依赖、直接解锁 DBC-03 的数字核对，且两个守卫
测试可即时验证，适合作为首项；DBC-01 与 DBC-04 主要等待人的安排（走查人、用户
发布时机），备料工作可即刻并行前置；DBC-05 价值最低、放在最后，不阻塞任何验收
关键路径。全部完成后，维度 4 坐实 9.0、维度 7 冲 10，总分预期从 79.25 提升到
约 80–81（维度 2 的 +9 缺口在方向 A 重启前不存在，总分上限 ~83 的判断不变）。
