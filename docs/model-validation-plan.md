# M3-01 候选模型兼容与预算方案

**日期：** 2026-11-15。**基线：** `main` @ `51a99ab`（本分支 `m3-01-model-plan`）。
**性质：** 纯规划文档。本文档的撰写过程未发起任何真实模型 API 调用；后续正式调用在 M3-02 及之后按本方案与预算执行。
**上游依据：** [phase3-plan.md](phase3-plan.md) §6（M3-01–M3-06 验收标准与失败重测规则）、[progress.md](progress.md) B2 章节、[eval-failure-classification-2026-11-15.md](eval-failure-classification-2026-11-15.md)。
**价格数据：** 全部取自官方公开定价页（URL 见正文），查询日期 2026-11-15；执行时以官方页当时价格为准。

---

## 1. 背景与失败基线：为什么必须换模型

已测模型 **MiniMax-M3.1-Flash / M3.1-Flash-Preview**（OpenAI 兼容端点 `https://api.minimaxi.com/v1`）的历史 live 评测结果：

| 日期 | run | tool selection | 备注 |
|---|---|---|---|
| 2026-10-11 | `2026-10-11T15-39-42.json` | 3/15（0.200） | 70 例全量跑；argument validity 0.471 |
| 2026-10-13 | 两次同配置重跑 | 3/15 与 2/15 | 两跑仅 8/15 例提出相同工具集合——方差与任何可能的改进同量级 |
| 2026-11-14 | `09-37-39` / `10-40-41` | **0/15、0/15** | B2 修复前：提示词未携带参数 schema，98 次提案中 61 次 gate-1 拒绝 |
| 2026-11-14 | B2 修复后两跑 | **2/15、2/15** | 命中仅 tool-011、tool-015；argument validity 0.378→1.000 |

结论（详见 [progress.md](progress.md) "B2 executed against the real endpoint" 一节）：

1. **提示词缺陷已修复但分数未动。** B2 把参数 schema 带进规划提示词后，argument validity 从 0.378 升到 1.000，但 tool selection 仍是 2/15——剩余差距是模型对工具集合的判断力与循环行为，不是提示词工程能解决的。
2. **病态循环。** 被拒绝后，重规划提示词只回显 `tool_name(status)`，MiniMax 会重复提出完全相同的畸形调用（tool-001 上 24 轮规划、tool-008 上 19 次模型调用产出 16 行工具记录）。这类行为既烧预算又不产生信息。
3. **配额不可靠。** 2026-10-13 的完整 live run 曾被 MiniMax 免费档 `HTTP 429 / code 2056 (Token Plan exhausted)` 中断，正式验收批次不能建立在随时断供的配额上。
4. **10/15 门槛从未接近。** 六个 live run 的最好单跑成绩是 3/15。继续在该端点上调参，边际收益已被两次完整重测证明趋近于零。

因此 M3-01 的任务是选定**新候选模型（含新端点）**，在进入 M3-02 正式批次前把标识、端点、结构化输出适配、预算与停止规则写成可执行的方案。

## 2. 候选模型方案

### 2.1 主选：DeepSeek V4.1-Flash（`deepseek-flash`）

| 项 | 值 |
|---|---|
| 精确模型标识 | `deepseek-flash`（版本 DeepSeek-V4.1-Flash-1017） |
| 端点 | `https://api.deepseek.com`（OpenAI 兼容；SDK base_url 亦可写 `https://api.deepseek.com/v1`） |
| 上下文窗口 | 1M tokens |
| 最大输出 | 384K tokens |
| 输入价（peak） | $0.30 / MTok（cache miss）；$0.006 / MTok（cache hit） |
| 输入价（off-peak） | peak 的一半：$0.15 / MTok（cache miss） |
| 输出价（peak） | $1.20 / MTok；off-peak $0.60 / MTok |
| structured output | 官方支持 JSON output；`response_format` 走 `json_object`；json_schema 严格模式以小样本实测为准（见 §2.4 降级路径） |
| tool calling | 官方支持 function calling；本仓库的规划走"提示词菜单 + 结构化提案"而非 API 级 tools 声明，故关键在指令跟随与 schema 遵从 |
| 定价来源 | <https://api-docs.deepseek.com/quick_start/pricing/> |
| 计费方式 | 按 token 计量充值，无订阅配额断供问题；peak 时段为 UTC 周一至周五 01:00–04:00 与 06:00–10:00（其余时段含周末均为 off-peak 半价） |

接入配置（写入 gitignored 的 `.env`，**key 绝不提交**）：

```bash
MODEL_PROVIDER=openai
MODEL_NAME=deepseek-flash
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=<在 .env 中填写，不入库>
# 仅当小样本阶段确认端点拒绝 json_schema 信封时启用：
# OPENAI_JSON_OBJECT_FALLBACK=true
```

### 2.2 备选：Claude Haiku 5.5（`claude-haiku-5-5`）

| 项 | 值 |
|---|---|
| 精确模型标识 | `claude-haiku-5-5` |
| 端点 | Anthropic Messages API `https://api.anthropic.com`（仓库原生 `MODEL_PROVIDER=anthropic`，经 `ANTHROPIC_BASE_URL` 可覆盖） |
| 上下文窗口 | 1M tokens |
| 最大输出 | 128K tokens |
| 输入价 | $0.10 / MTok（prompt ≤100K）；$0.50 / MTok（prompt >100K） |
| 输出价 | $0.50 / MTok（≤100K）；$2.50 / MTok（>100K） |
| cache | 5m 写 $0.125 / MTok，读 $0.01 / MTok |
| structured output | Anthropic 原生支持 json_schema 约束输出（`output_config`）；本仓库 `AnthropicModelProvider` 已按该信封实现 |
| tool calling | 原生 tool use；与 DeepSeek 同样，本仓库实际依赖提示词菜单 + 结构化提案 |
| 定价来源 | <https://platform.claude.com/docs/en/about-claude/pricing>；模型页 <https://platform.claude.com/docs/en/models/haiku-5-5/overview> |

接入配置（同样只进 `.env`）：

```bash
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-haiku-5-5
ANTHROPIC_API_KEY=<在 .env 中填写，不入库>
```

**使用规则：** 备选模型仅在主选的小样本试点（§4 第 1 步）失败、或开发集两跑未达标且故障归因于主选端点/模型时启用；它同时充当"第二模型可选对照"的角色（phase3-plan §6 M3-01）。若两个候选都达不到 10/15，是否升级到更强模型（如 Claude Sonnet 5.5，$2/$10 每 MTok）属于人工决策，不在本方案默认预算内。

### 2.3 为何候选优于 MiniMax-M3.1-Flash

1. **代际与能力。** 两个候选都是 2026 年在售的旗舰/次旗舰代际模型，官方支持 structured output 与 function calling；MiniMax-M3.1-Flash 在 B2 修复提示词后仍只有 2/15，差距在模型的工具集合判断力本身。
2. **schema 遵从。** MiniMax 在仅见工具名时会编造参数名（`email` 对 `customer_email`、`body` 对 `description`），被拒后重复同一畸形提案。候选模型以 schema 遵从与指令跟随见长，这正是 gate-1 通过率的决定因素。
3. **计费可靠性。** MiniMax 免费档 429 断供曾中断正式跑；DeepSeek 与 Anthropic 均为按量充值计费，在 §3 的预算上限内不存在中途断供风险。
4. **成本反而更低。** 按 §3 估算，整个验证程序在主选模型上约 $1–4，备选约 $1–2，均低于或接近 MiniMax 已消耗的量级，且换来的是"有希望通过 10/15 门槛"的模型代际。
5. **接入零改动。** 两个候选分别对应仓库已有的 `openai`（自定义 base_url）与 `anthropic` 两条 provider 路径，不需要改任何代码；M1-02 已把两条路径的 SDK 隐式重试关闭（`max_retries=0`，单次尝试语义）。

### 2.4 结构化输出与工具调用适配（含降级路径）

本仓库对模型的实际要求不是 API 级 tool calling，而是：

- **规划调用**（`choose_tool`）：在用户消息里看到工具菜单与参数 schema，通过 `response_format` 结构化信封返回 `{"tool_name", "arguments", "reason", "done"}` 提案；提案是否合法由 gate 1（schema）与 gate 2（注册表）裁决，provider 层不信任模型输出。
- **分类调用**（`generate_structured`）：`response_format` 携带 Pydantic 派生的 json_schema 信封。
- **自由文本**（`generate_text`）：客户回复，无结构化要求。

适配要点与风险：

| 项 | DeepSeek V4.1-Flash | Claude Haiku 5.5 |
|---|---|---|
| json_schema 严格信封 | 待小样本实测；DeepSeek 官方文档确认 JSON output，若 `beta.chat.completions.parse` 的 json_schema 信封被端点拒绝，启用 `OPENAI_JSON_OBJECT_FALLBACK=true`（schema 写进 system prompt + `json_object`）——这是 MiniMax 时代已验证可行的降级路径，仓库原生支持 | Anthropic 原生支持结构化输出信封；注意历史记录中"本地网关会静默忽略 `output_config`"的问题只存在于 `127.0.0.1:15742` 网关，直连 `api.anthropic.com` 不受影响 |
| 提示词菜单（非 API tools） | 两者的评测口径相同：菜单与 schema 在提示词里，模型按约输出提案 JSON。候选是否比 MiniMax 更会"看菜单点菜"，由 §4 的小样本直接测量，不做纸面推断 | 同左 |
| 单次尝试语义 | 已由 M1-02 落实：两 provider 均显式 `max_retries=0`，失败即失败并给出原因，无 SDK 隐式重试（不放大账单） | 同左 |
| token 用量回报 | 需在小样本阶段确认 usage 字段可用；MiniMax 曾大量调用不回报 usage（156 次调用仅 28 次可测），影响费用核账——这是本方案把预算做成"上限制"而非"精确制"的原因之一 | Anthropic usage 回报完整可靠 |

## 3. 预算方案

### 3.1 调用量估算（依据本机实测）

MiniMax 在 15 例 tool_selection 开发集上的实测每跑模型调用数（`evals/results/2026-11-14T09-37-39.json` 与 `10-40-41.json`，gitignored，仅本机）：

- 每跑 136–156 次调用；每例均值 9.1–10.4 次，最小 2，最大 25（harness 步数上限）。
- 候选模型若循环行为健康，每例调用应不高于此（被拒重试更少）；本方案按任务口径取 **每 run 5–10 次模型调用** 做中位估算，并以 MiniMax 实测值作为上界参照。

验证程序各批次的调用量（按每例调用数区间）：

| 批次 | 规模 | 每例调用 | 估算调用数 |
|---|---|---|---:|
| S1 小样本试点（dev 前 4 例 ×1 跑） | 4 例 | 5–10 | 20–40 |
| S2 开发集两跑（15 × 2） | 30 例 | 5–10 | 150–300 |
| S3 holdout（32 × 1，冻结配置） | 32 例 | 5–10 | 160–320 |
| S4 safety 独立跑（15 × 1） | 15 例 | 5–10 | 75–150 |
| S5 展示场景（3 类 × 2 live 跑） | 6 跑 | 5–10 | 30–60 |
| 重测余量（1 次开发集两跑重测 + 零星复跑） | ~30 例 | 5–10 | 150–300 |
| **合计（中位估算）** | | | **585–1,170** |

病态上界：若候选模型像 MiniMax 一样在部分案例上撞到 25 步上限，单批 15 例可达 ~375 次；全程序最坏情况约 **2,500–3,000 次**。这正是必须设预算上限的原因。

### 3.2 费用估算（按每调用 2K 输入 + 0.6K 输出中位假设）

> 该 per-call token 假设偏保守（MiniMax 可测样本的均值约 0.6K 入 / 0.6K 出，但仅覆盖 18% 调用；规划调用携带完整工具菜单与 schema，实际更高）。候选模型的真实 per-call token 在 S1 小样本后回填修正。

| 模型 | 单次调用估算（peak 价） | 全程序 585–1,170 次 | 病态上界 ~3,000 次 |
|---|---|---|---|
| DeepSeek V4.1-Flash（主选） | ~$0.0013 | **$0.8–1.6** | ~$3.9 |
| DeepSeek off-peak（半价） | ~$0.00066 | **$0.4–0.8** | ~$2.0 |
| Claude Haiku 5.5（备选） | ~$0.0005 | **$0.3–0.6** | ~$1.5 |
| Claude Sonnet 5.5（仅作升级参照，不在本预算内） | ~$0.010 | $5.9–11.7 | ~$30 |

**结论：主选模型的全程序费用区间约 $1–4（含病态余量 < $5），备选约 $1–2。** 相对 MiniMax 免费档，这是用少量真实费用换取计费可靠性与通过门槛的现实可能。

### 3.3 预算上限与停止规则

- **预算上限：USD $25（按主选模型计，覆盖中位估算的 ~15 倍、病态上界的 ~6 倍）。** 备选模型单独核算，上限同为 $25；两模型合计承诺不超过 $50。
- **执行方式：** 每个批次开始前记录当日官方价与余额；每个 run 结束后从结果文件的 usage 聚合记账（无法回报 usage 的调用按 per-call 估算上限记账，宁可高估）。
- **到达上限即停止：** 任何时刻累计估算费用触及上限，立即中止后续 live 批次，**保留未完成状态**——已完成的批次结果与失败记录全部保留，作品集材料如实标注"预算内完成 X/Y 批次"，绝不宣称通过未执行的验收，也绝不把未知费用显示成免费（phase3-plan §6）。
- **时间上限：** 单次模型调用 timeout 沿用 `MODEL_TIMEOUT_SECONDS`（默认 60s）；单例步数上限 25（harness 既有）；单跑（15 例）墙钟上限 2 小时；整个 M3 真实验证窗口计划 5 个有效工作日内完成，超期未完成按未达成记录。

### 3.4 费用不可预估项（标未知）

| 项 | 状态 |
|---|---|
| 候选模型真实 per-call token 用量 | **未知**，S1 小样本后回填；当前按 2K/0.6K 保守假设 |
| 病态循环（撞 25 步上限）的发生率 | **未知**，取决于候选模型行为；以预算上限兜底 |
| DeepSeek / Anthropic 促销价、免费额度或价格调整 | **未知**，执行日以官方页为准 |
| 展示录屏的重拍与彩排调用（同一场景多次拍摄） | **未计入**验收预算；如需重跑按当时余额人工决定 |
| 人工时间、网络/代理成本 | **不计入**本方案（非 API 费用） |
| prompt caching 折扣 | 本仓库未启用缓存，估算未计入；如启用只会更便宜 |

## 4. 验证流程

按 [phase3-plan.md](phase3-plan.md) §6 的门槛与重测规则执行；以下步骤中任何一步未达标都按 §4.2 处理。

### 4.1 步骤

1. **S1 小样本试点（不设通过门槛，只做兼容性确认）。** 在开发集前 4 例（含正例与至少 1 例预期弃答/负例）上跑 1 遍，确认：端点连通、结构化输出路径可用（strict json_schema 或已启用 fallback）、usage 可回报、无病态循环。S1 期间同时回填 §3.4 的 per-call token。S1 失败（结构化解析持续失败、循环失控）→ 修配置或切换备选模型，**不进入 S2**。
2. **S2 开发集两跑（核心门槛）。** 同一 15 例开发集、同配置、至少两跑；**每跑 tool selection ≥ 10/15** 方可通过。两跑结果都保留（含失败案例明细），不得丢弃失败只报成功。
3. **S3 holdout + S4 safety（冻结后）。** S2 通过后**冻结 prompt/config/代码版本**（记录 commit），32 例 holdout 全跑并报告（不用于调参，结果好坏均报告）；15 例 safety 独立运行，硬门槛 **unsafe = 0**，同时报告 approval-policy compliance、expected-write rate、unexpected-write count。
4. **S5 展示场景。** 3 类场景（重复扣款、政策证据不足转人工、无需退款的已退款）冻结输入后每类 2 次 live 运行，**6/6 达到各自预定义业务结果**；全部尝试留痕。注意：10/15 是开发集回归门槛，不能解释成业务成功率 67%；6/6 是小样本核验，不是泛化保证。
5. **记账与披露。** 每步报告完成率、参数有效率、延迟与可测 token；费用按 §3.3 记账。

### 4.2 失败处理与重测规则（引用 phase3-plan §6）

- 正式验收批次开始前**固定场景、次数和成功定义**；失败则记未达成，后续修复使用**新的版本与完整批次重测**——不允许在同一批次的剩余样本上"补考"。
- 候选模型达不到门槛时，先换合适端点或修复已定位问题；**禁止为了作品集美观弱化安全门或改指标**。
- 单次调用失败不重试（M1-02 单次尝试语义）：失败进结果文件并给出原因与人工入口；是否换模型/端点属于批次级决策，不是调用级重试。
- 预算未配置或已耗尽时，可继续完成离线工作与材料草稿，但 M3 相关验收保持"待验证"，作品集不得宣称已通过真实模型验收。

## 5. 风险与开放问题

| # | 风险 | 处置 |
|---|---|---|
| R1 | DeepSeek 对 json_schema 严格信封的兼容性未实测 | S1 第一项即验证；不兼容则启用 `OPENAI_JSON_OBJECT_FALLBACK=true`（已有 MiniMax 先例），并在结果文件 config 中记录实际路径 |
| R2 | 候选模型同样可能"看菜单点菜"能力不足 | S1/S2 早期暴露；备选模型就位；若双双失败，升级决策交用户（成本另议） |
| R3 | usage 不回报导致费用不可核账 | 记账按 per-call 估算上限从高计；S1 验证 usage 可用性 |
| R4 | API key 泄露 | key 只写入 gitignored `.env`；结果文件、截图、日志发布前检查不含 key；本方案自身不含任何 key |
| R5 | 本地网关/代理干扰（历史 `127.0.0.1:15742` 教训） | S1 直连官方端点验证；不经过任何会改写模型名或忽略参数的中间层 |
| R6 | 价格页与实际计费不符 | 以控制台账单为准；S1 后用实测用量核对一次单价 |

**待用户确认后再进入 M3-02：** 注册并充值 DeepSeek（主选）/ Anthropic（备选）账号，把 key 写入 `.env`；本方案不代替该操作，也不在未获得 key 前发起任何调用。
