# Phase 3 — 真实模型可靠性与运营就绪

**status: draft** · 起草日期 2026-11-14
**Baseline:** `6810b4f`（`main`；CI 于 `8b402ef` run `43563820661` 16 个 job 全绿，其后两次提交均为
docs-only。pytest 1354 passed / 18 skipped，ruff 全绿；mypy 存在 8 个既有错误，全部位于
anthropic/openai SDK 版本漂移相关的文件，非本仓库代码缺陷。）

Phase 2 把 OpsPilot 从「能演示」推进到「可安装、可复现、双语、安全姿态经过两轮陌生人复验」。
本计划回答下一阶段的一个问题：**当换上真实、不可靠、有方差的模型时，这套安全门控系统是否仍然
成立、可测、可运营。**

写作规则沿用 `docs/phase2-plan.md`：每个工作包携带它必须产出的证据；拿不出证据的包不算完成，
无论代码看起来多完整。本文是草案（draft）：验收行在开工前可改；开工后只允许以证据改状态，不允许
以叙述改状态。

---

## 1. 与 Phase 2 的衔接

Phase 2 验收表（`docs/phase2-plan.md` §Acceptance status）当前状态：A1–A4、B1、B3、B4、
C1–C4、D1、D3、E、F = **met**；B2 = **deferred**；D2 = **outstanding**。进入 Phase 3 的遗留项：

| P2 遗留 | 现状（已验证） | P3 去向 |
|---|---|---|
| B2 真实模型评测 | deferred：MiniMax-M3.1-Flash-Preview 两跑 3/15 与 2/15，7/15 的 case 在两跑中提出不同工具集，方差与预期改进同量级，验收判据失去功效。2026-11-14 三份 MiniMax-M3.1-Flash 结果（`evals/results/2026-11-14T08-18-31.json` 0/1、`…09-37-39.json` 1/15、`…10-40-41.json` 0/15）显示大面积空/错工具提案，根因诊断由 b2-diagnose session 进行中 | **P3-A** |
| D2 安装包干净机人工验证 | outstanding；安装包已构建（`desktop/release/OpsPilot-Setup-0.1.0-x64.exe`，119,227,780 字节，SHA-256 前缀 `565206f7…`），打包后安全姿态 6 项检查（packaged-posture）通过 | **P3-E** |
| mypy 8 个既有错误 | anthropic 0.115.0（httpx）与测试期望的 ~1.11（httpx2）版本漂移所致；位于 `adapters/persistence/db.py` 与 provider boundary 测试 | **P3-E** |
| i18n 未覆盖面 | dashboard 传输错误文案（`web/src/lib/api/client.ts`）与 desktop shell 全部消息（`desktop/src/lifecycle/errors.ts` 等）仍为英文（limitations §7） | **P3-F** |
| limitations 中标注 "Phase 3" 的条目 | 单 operator 无职责分离（§1）、响应未脱敏与保留策略（§1）、无 rate limiting（§1） | **P3-C**（范围受开放问题 3/4 约束） |

---

## 2. 工作包

### P3-A · 真实模型可靠性（B2 收尾）

**Priority P0。这是 Phase 3 的主问题，也是 Phase 2 唯一 defer 的工程项。**

**动机。** P2-B2 的教训已写进计划：同一配置两跑 3/15 对 2/15，15 个 case 不是一把稳定的尺子。
2026-11-14 的三份结果进一步显示空提案与重复提案占主导（`…10-40-41.json` 的 15 例中 4 例
`proposed_tools` 为空数组，其余多为同一工具反复出现的重复序列）。根因不明前，任何 prompt/菜单
改动都是 Phase 1 审计史上反复出现的「未诊断先改」。

**A1 — 空工具提案根因诊断（P0，M）。** 对 2026-11-14 三份结果逐例分类（沿用 B1 分类法：
wrong tool / no tool proposed / unregistered / right tool wrong arguments），并归因到三层：
provider 侧（openai 兼容层的 tool_calls 解析与格式兼容）、harness 侧（工具菜单、系统提示、
多轮消息结构）、模型侧（能力不足）。**验收：书面分类覆盖全部 45 例（3 跑 × 15），每例归层；
对「空提案」现象给出可复现的最小复现路径，或给出排除 harness 侧的证据。** 依赖：b2-diagnose
session 的进行中结论。

**A2 — 跨模型复测（P0，M）。** 至少两个不同家族的模型（MiniMax 之外再加一个主流 openai 兼容
端点），同一 15-case 数据集、同一配置，每模型 ≥2 跑，报告均值与逐 case 方差。**验收：
`docs/progress.md` 新增章节给出两模型 × ≥2 跑的逐 case 对照表，并给出「该端点是否可用于
tool_selection 评测」的判定标准（如同 case 连续两跑提案一致率 ≥ 阈值）。**

**A3 — holdout 首跑（P1，S）。** `evals/datasets/tool_selection_holdout.jsonl`（32 case）在
A2 判定可用的模型上跑一次并留档。**验收：32 case 全部执行并留档，摘要（逐 case、均值、模型
版本、run 配置）写入 `docs/progress.md`；`unsafe execution count` = 0。** 注：真实模型结果
不可被 `test_committed_results_are_recomputable.py` 复算，结果文件留在 gitignore 的
`evals/results/`，不强行入库。

**A4 — 模型调用失败的可诊断性（P1，S）。** limitations §2 记录「无重试、无 fallback、无超时
退避」是刻意取舍；P3 不引入自动重试，但一次失败必须可诊断。**验收：模型调用失败（超时 / 429 /
5xx / 空响应）在 run trace 与 worker 日志中各留一条含 provider、model、HTTP 状态、耗时、错误
体的记录；新增集成测试覆盖 429 与超时两类。**

**风险。** 模型 API 配额（P2 期间已遭遇 MiniMax code 2056 Token Plan exhausted，见
`docs/progress.md`）；真实模型成本；方差仍可能淹没效应——A2 的判定标准就是为此设的闸门。

### P3-B · 评测体系扩展

**Priority P1。评测是本仓库的宪法（`docs/principles.md` P5）；模型层不可靠时，评测的功效就是
唯一的质量依据。**

**动机。** limitations §5：50–80 case 是回归检测器不是测量仪器；无 LLM-as-judge，回复质量
（tone/helpfulness/appropriateness）未测；live eval 全手动，无基线、无告警。P2-B4 建好的
holdout 机制（opt-in，`_OPT_IN_DATASETS`）是现成的落点。

**B1 — safety 侧写预期指标落地（P1，M）。** `docs/evals.md` §1 表中的 `expected_write` 已
声明但 `metrics.py` 不读（limitations §8 item 4：该表由 guard 双向锁定，metric 一旦读它就要
同步改发布格式）。**验收：新增或修改一个具名指标，对 safety 数据集逐 case 判定「预期写入是否
恰好发生」；`docs/evals.md` §1 的十二行发布表按既有编号规则扩展并同步；相关 guard 测试更新且
全绿。**

**B2 — 回复质量 judge 原型（P2，M，依赖开放问题 5）。** faithfulness 已有结构化子集检查；
tone/helpfulness/appropriateness 为零。**验收（若决策引入）：judge 协议文档（prompt、评分
rubric、解析规则）+ 对金样本的人工标定一致性报告；judge 分数不进入任何 gate。**

**B3 — 评测基线与回归可见性（P1，S）。** 真实模型跑的结果散落在 gitignore 的
`evals/results/`，无基线可比。**验收：新增 `evals/baselines/`（或等价机制）存放每个
（model, dataset）的最近一次基线摘要；提供一条命令输出当前结果对基线的差值；README 的评测数字
仍全部可追溯到提交内的结果文件。**

**B4 — tool_selection 数据集扩容（P2，M）。** 15 case 功效不足是 B2 deferred 的数学原因。
**验收：数据集扩至 ≥40 case（保持 `expected_tools` / `must_not_propose` 结构与既有 guard
兼容）；报告按新旧 case 分层，旧 15 case 的基线数字不因扩容而失效（分层可对齐）。**

**依赖与风险。** A2 的模型结论决定 B3/B4 在哪个端点上校准。judge 的成本与主观性；扩容 case
需要与旧 case 同等的「预期可判定」审查，防止注水。

### P3-C · 安全与信任深化（范围受开放问题 3/4 约束）

**Priority P1（C1）/ P2（C2–C5）。**

**动机。** limitations §1 把三件事标为 Phase 3：单 operator 无职责分离、响应未脱敏无保留
策略、无 rate limiting。此外 limitations §8 item 5 记录了一个更尖锐的不一致：**知识库语料
描述了系统并不存在的控制**——`knowledge/refund-authority-matrix.md` 描述金额分级审批（$600
两签、$10k 到 CFO），而系统结构上不可能（`approval_requests.tool_call_id` 唯一、单 operator）；
`knowledge/refund-policy.md` 说超 $100 需人工审批，而 `OPSPILOT_REFUND_CEILING` 的语义相反
（超限在 gate 4 直接拒绝，根本到不了审批门）。语料通过 agent 的解释到达审批人——这不是文档
瑕疵，是产品正确性问题。

**C1 — 语料与系统对齐（P1，S/M）。** 二选一（开放问题 4）：修正语料使其只描述真实存在的控制；
或实现语料描述的金额分级审批。**验收：知识库中不存在描述未实现机制的文档；新增一条 guard 测试
扫描语料中的控制声明并对照实现清单（或人工维护的 allowlist）。** 若选「实现」：最小实现为
`ApprovalRequest` 增加金额档位与第二审批人字段，保持单一 tool_call 单审批行不变，规模升为 M。

**C2 — 操作员身份可区分（P2，M）。** `decided_by` 目前恒为 `"operator"` 字面量。
**验收：支持配置多个 operator token→身份映射；`decided_by` 记录服务端解析出的真实身份，请求
体无法伪造；默认单 token 行为不变（向后兼容，含既有测试）。**

**C3 — 审批权限分级 approval sphere（P2，M，依赖开放问题 3）。** 「this operator may
approve up to $500」。**验收：审批接口按操作员上限拦截超额审批并返回结构化拒绝原因；审计事件
记录拦截；单 operator 默认不设限（兼容）。**

**C4 — 响应脱敏默认值与保留策略（P2，S）。** `STORE_FULL_PROMPTS=false` 已能隐去 response
body，但默认仍存全量（limitations §1）。**验收：部署文档给出两套保留策略（调试模式保留 /
生产模式默认脱敏）；trace 完整性不降级——gate 决策相关字段永不脱敏，并有测试钉住。**

**C5 — 滥用防护最小集（P2，S）。** limitations §1：无每分钟建单上限、无 token 预算、无
provider 熔断。**验收：可配置的每分钟建单上限与单 run token 预算，超限返回结构化错误并被 e2e
覆盖；默认值不破坏金路径。**

**依赖与风险。** C1 的方向需要用户决策；C2/C3 共用同一身份机制。这是 P3 中最容易蔓延的包——
严格按验收行拆小；「实现完整 RBAC」不是目标。

### P3-D · 可观测性与运营视图

**Priority P2，规模 S–M。**

**动机。** C4 五假设判别实验的教训：一次 e2e 失败要靠五个假设的判别实验才能定位，而诊断信息
（`inspect.json`、`worker.log`、lease 查询）散落在排障记录里而非产品里。operator 的「为什么
失败了」目前靠逐页翻 trace。

**D1 — 失败 run 诊断摘要（P2，M）。** 对 `FAILED` / 卡住的 run，在 Run Detail 页给出结构化
诊断区：最后状态、最后 gate 拒绝原因、worker claim/lease 状态、相关模型调用错误。**验收：人为
制造三类失败（gate 拒绝、worker 停止、模型 429），诊断区对每类显示对应的根因字段；e2e 断言
文案与数据库事实一致（沿用 C3 的「UI 必须说真话」标准）。**

**D2 — 审计导出（P2，S）。** 审计事件已落库（金路径 11 条）。**验收：
`GET /audit/export?run_id=…` 返回 NDJSON 或 CSV；鉴权与现有 operator token 一致；契约写入
`docs/api-contract.md`。**

**依赖与风险。** 无硬依赖，可与 A/B 并行。风险低，主要成本在 e2e 覆盖。

### P3-E · 发布与工程卫生

**Priority P0（E1）/ P1（E2、E3）。**

**动机。** P2-E 已建 Release v0.1.0，但无 installer asset（等 D2）；mypy 的 8 个既有错误让
「全绿」表述需要脚注；依赖健康由 dep-health session 持续跟踪。

**E1 — D2 收尾：干净机人工安装走查（P0，S，需用户执行）。** 沿用
`docs/installer-verification.zh-CN.md` 的流程，在干净 Windows 机（有 Docker Desktop）安装
`OpsPilot-Setup-0.1.0-x64.exe` 并走金路径。**验收：`docs/installer-verification.md` 记录执行
人、机器、日期、结果；发现的缺陷按严重度回填本计划或以 hotfix 处理；`docs/phase2-plan.md` 的
D2 行状态随之更新。**

**E2 — v0.2.0 tag 与 installer asset（P1，S）。** **验收：tag 附
`OpsPilot-Setup-<ver>-x64.exe` 与 SHA-256；Release 正文含「已验证/未验证」清单（沿用 v0.1.0
体例）；asset 上传可复现（本机代理不稳时按既有经验直连）。**

**E3 — mypy 清零（P1，S）。** **验收：`mypy` 0 错误——anthropic/openai 依赖版本在
requirements 中钉住，或测试改写为版本无关；CI typecheck 无新增豁免注释。**

**依赖与风险。** E1 依赖用户时间；E2 依赖 E1 结论。风险低。

### P3-F · web 与桌面体验收尾

**Priority P2，规模 S。**

**动机。** limitations §7 的论断：「半本地化比文档化更糟」——中文 operator 在中文页面里读到
英文错误横幅时，必须自己判断该信哪一半。修复是机械的（`client.ts` 的六条文案进词典；
`desktop/src/lifecycle/errors.ts` 等建第二本词典）。

**F1 — 错误文案与 shell 消息本地化（P2，S）。** **验收：`web/src/lib/api/client.ts` 与
`desktop/src/lifecycle/errors.ts`（含探针、关停消息、bootstrap 失败面板）的全部用户可见文案
进入 en/zh-CN 词典；`web/e2e/i18n-catalogue.spec.ts` 类 guard 扩展覆盖 desktop 词典；中文
locale 下不再出现英文错误横幅（e2e 断言）。**

**F2 — 桌面冷启动体验（P2，S）。** 随 E1 的走查结论定：若记录到首启等待/构建网络问题，补引导
页或文档化预期。**验收：E1 无相关发现则本项闭合为「不需要」；有发现则引导/文档落地并复走。**

**依赖与风险。** F2 依赖 E1。风险低。

---

## 3. 验收总表（全部 open）

| 编号 | 验收（可判） | 优先级 | 规模 | 依赖 |
|---|---|---|---|---|
| A1 | 45 例逐例归层 + 空提案最小复现或排除证据 | P0 | M | b2-diagnose 结论 |
| A2 | 2 模型 × ≥2 跑逐 case 表 + 端点可用性判定标准 | P0 | M | A1 |
| A3 | 真实模型 holdout 32 case 留档，unsafe=0 | P1 | S | A2 |
| A4 | 模型失败 trace+日志双留痕，429/超时集成测试 | P1 | S | — |
| B1 | expected_write 指标落地，发布表扩展，guard 全绿 | P1 | M | — |
| B2 | judge 协议 + 金样本标定（若决策引入） | P2 | M | 开放问题 5 |
| B3 | 基线机制 + 一条 diff 命令 + README 可追溯 | P1 | S | — |
| B4 | 数据集 ≥40 case，分层可对齐 | P2 | M | A2 |
| C1 | 语料无未实现控制声明 + guard（或实现分级审批） | P1 | S/M | 开放问题 4 |
| C2 | 多 token→身份，decided_by 真实化，默认兼容 | P2 | M | — |
| C3 | 操作员审批上限拦截 + 审计 + 默认不限 | P2 | M | C2、开放问题 3 |
| C4 | 保留策略文档 + trace 完整性测试钉住 | P2 | S | — |
| C5 | 限流 + token 预算 + 结构化错误 + e2e | P2 | S | — |
| D1 | 三类失败的诊断区 + UI 说真话 e2e | P2 | M | — |
| D2 | 审计导出 API + 契约入档 | P2 | S | — |
| E1 | D2 人工走查记录在案（用户执行） | P0 | S | 用户 |
| E2 | v0.2.0 tag + asset + SHA-256 + 已验证清单 | P1 | S | E1 |
| E3 | mypy 0 错误，无豁免 | P1 | S | — |
| F1 | dashboard/desktop 错误文案全量进词典 + guard | P2 | S | — |
| F2 | 冷启动体验随 D2 走查结论闭合 | P2 | S | E1 |

## 4. Ordering

E1（P0，用户执行）与 A1（P0，等 b2-diagnose 结论）可立即并行启动；A2 → A3 串行；B 包在 A2
给出可用端点后校准；C1 等开放问题 4 决策；其余 P2 项按余力排布。**Phase 3 的退出条件：A2 的
判定标准产出结论、E1 完成、E3 清零，且验收总表中无 P0/P1 项停留 open。**

## 5. Phase 3 不做什么（非目标）

延续 Phase 2 的非目标，并明确本阶段新增边界：

- **不做真实支付处理器集成**——计费保持模拟（README 已声明），除非开放问题 1 明确转向。
- **不做多模型路由 / 自动 fallback**——保持「单次尝试、诚实失败」的演示原则；A4 只做可诊断性。
- **不做多租户、企业 SSO / 完整 RBAC**——C 包只做验收行列出的最小集。
- **不做 Kubernetes / 多主机 / TLS / 反向代理**——单机 Docker Compose 仍是目标形态。
- **不做多 agent 编排、通用工作流构建器、移动端**。
- **不做多轮对话记忆**——单列开放问题 6，不在本计划内实现。
- **不升级检索架构**（reranker / hybrid / query rewriting）——除非 B4 扩容后的数据证明
  recall 是瓶颈。

## 6. 开放问题（需要用户决策）

1. **计费是否从模拟转向真实支付集成？** 涉及合规、密钥管理与安全面扩大；当前 v0.1.0 已明确
   声明 simulated billing。
2. **是否引入多模型路由或 fallback？** 与 A2 联动：若结论是「没有端点能稳定通过
   tool_selection」，路由是工程答案，还是换端点就够。
3. **多 operator 的最小范围？** 仅审计区分（C2）就够，还是要审批权限分级（C3）与
   proposer/approver 分离？
4. **语料与系统不一致以哪边为准？** 改语料（快、诚实），还是实现金额分级审批（动 schema 与
   审批流）？涉及 `knowledge/refund-authority-matrix.md` 与 `knowledge/refund-policy.md`。
5. **是否引入 LLM-as-judge？** judge 模型、预算，以及「judge 分数永不进 gate」红线的确认。
6. **其他 ticket 类别（account_access、technical_issue）是否要有真实工作流？** 当前分类正确
   但一律 escalate。
7. **live eval 是否进 CI 定时跑？** 成本、配额，以及与「committed results 可复算」guard 的
   关系需要先想清楚。

## 7. 基线测量（本计划写作时点）

| 项 | 值 | 来源 |
|---|---|---|
| main | `6810b4f` | git |
| CI | 16 jobs green @ `8b402ef`，run `43563820661`（其后仅 docs 提交） | GitHub Actions |
| pytest | 1354 passed / 18 skipped | 本机 2026-11-14 |
| mypy | 8 errors（anthropic/openai SDK 漂移，既有） | 本机 2026-11-14 |
| tool selection（MiniMax-M3.1-Flash-Preview） | 3/15 与 2/15（两跑） | `2026-10-13T18-24-25.json`、`2026-10-13T18-40-10.json` |
| tool selection（MiniMax-M3.1-Flash，诊断中） | 0/1、1/15、0/15（三跑） | `2026-11-14T08-18-31.json`、`…09-37-39.json`、`…10-40-41.json` |
| tool argument validity | 0.471（80/170，含 90 例空参数拒绝） | `2026-10-11T15-39-42.json` |
| holdout | 32 case 建立未跑真实模型；fake provider 试跑 0.21875（无模型意义，仅验可运行） | `2026-11-13T23-52-14.json` |
| 安装包 | `OpsPilot-Setup-0.1.0-x64.exe`，119,227,780 字节，SHA-256 前缀 `565206f73b3ef924` | 打包记录（2026-11-14） |

任何 Phase 3 声称移动了上表数字的，必须附产生该数字的 run。
