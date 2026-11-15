# v0.3.0 真实模型重测方案（tool selection 门槛）

**日期：** 2026-11-15。**性质：** 纯规划文档。本文档撰写过程未发起任何真实模型 API 调用、未读取 `.env` 或任何密钥；后续正式调用按本方案执行。
**上游依据：** [model-validation-plan.md](model-validation-plan.md)（M3-01 冻结配置与预算框架）、[phase3-plan.md](phase3-plan.md) §6（门槛与重测规则）、[m3-02-dev-two-runs-2026-11-15.md](m3-02-dev-two-runs-2026-11-15.md)、[m3-02-rerun-after-retrieval-fix-2026-11-15.md](m3-02-rerun-after-retrieval-fix-2026-11-15.md)、[m3-03-holdout-safety-2026-11-15.md](m3-03-holdout-safety-2026-11-15.md)、[portfolio-acceptance-2026-11-15.md](portfolio-acceptance-2026-11-15.md) §N1/R3。
**版本含义：** "v0.3.0" 指下一轮以"真实模型门槛重测"为主题的迭代，版本号在发布时按当时内容定稿。

---

## 1. 为什么重测

### 1.1 现状与差距

真实模型（OpenAI 兼容端点）在 15 例开发集（`evals/datasets/tool_selection.jsonl`）上的工具集合命中率（exact set equality），门槛为**同配置两跑每跑 ≥10/15**：

| 批次 | 模型 | Run 1 | Run 2 | 达标 |
|---|---|---|---|---|
| M3-02 首测（2026-11-15） | DeepSeek V4.1-Flash | 3/15 | 2/15 | 否 |
| M3-02 检索修复后重跑（2026-11-15） | DeepSeek V4.1-Flash | 2/15 | 3/15 | 否 |
| MiniMax-M3.1-Flash（B2 修复后，2026-11-14） | MiniMax-M3.1-Flash | 2/15 | 2/15 | 否 |

辅助批次（同冻结配置）：holdout 32 例 **10/32**；safety 15 例 **unsafe = 0**（硬门槛已达成）；argument validity 修复后 **1.000**。

四个开发集 run 全部落在 2–3/15 区间，距 10/15 门槛差距悬殊，且两个不同厂商的模型（MiniMax、DeepSeek）落在同一区间——这不太像单模型的偶然短板。

### 1.2 归因：瓶颈在模型的工具集合判断力

两条排除路径已经走完，重测不应再在检索或提示词层找补：

1. **提示词/schema 层已修复且无效于 headline。** B2 把参数 schema 带进规划提示词后，argument validity 从 0.378 升到 1.000，但 tool selection 不动（[progress.md](progress.md) "B2 executed against the real endpoint"）。
2. **检索层已修复且无效于 headline。** 检索弃答修复（commit `229ead6`，`RETRIEVAL_MIN_SCORE` 0.22→0.205 + 查询扩展）解除了 tool-005/006/012 三例的确定性弃答，可竞争上限恢复 15/15，但修复后两跑仍为 2/15、3/15（[retrieval-abstain-analysis.md](retrieval-abstain-analysis.md)）。
3. **失败形态指向模型判断。** miss 模式为漏掉前置读取工具（`crm.get_customer`、`billing.get_invoice`）、多提无关工具（`issues.create`、`knowledge.search`）、被拒后重复同一提案不收敛（[eval-failure-classification-2026-11-15.md](eval-failure-classification-2026-11-15.md)）。run 间一致率低（两跑仅 8/15 例提出相同集合），方差来自模型决策而非确定性环节。

### 1.3 重测要回答的具体问题

| # | 问题 | 判定方式 |
|---|---|---|
| Q1 | 2–3/15 是这两个模型的能力上限，还是任务/接口的系统性上限？换代际更强的模型，在**完全冻结**的 harness 配置下能否过 10/15？ | S2 开发集两跑 |
| Q2 | 失败形态（漏前置读取、多提工具、重复提案）是否跨模型家族存在？若更强模型仍 miss 且形态相同，指向接口/提示词设计问题；若形态改变或消失，指向模型判断力。 | 逐例对比（本方案 §5） |
| Q3 | 更强模型在真实成本与延迟下是否可接受？ | S1 实测 per-call token 与延迟，回填 §6 |
| Q4 | 作品集叙事需要什么证据？达标 → 回填材料并重评验收表；未达标 → open metric 结论获得第二个模型家族佐证。 | §8 |

---

## 2. 候选模型（三类路线）

### 2.0 选择标准

1. 官方支持结构化输出（json_schema 或可降级的 json_object）；
2. 上下文 ≥ 128K（规划调用携带完整工具菜单与 schema，实测单跑可测输入约 24K tokens，余量充足即可）；
3. 按量计费、配额可靠（MiniMax 免费档 429 断供、sensenova 路由上游 429 的教训：**不用免费档跑正式批次**）；
4. 工具选择/指令跟随能力有代际优势的公开证据；
5. 接入零代码改动：走仓库既有 `openai`（自定义 `OPENAI_BASE_URL`）或 `anthropic` 两条 provider 路径（M1-02 已落实两路径 `max_retries=0` 单次尝试语义）。

### 2.1 路线 A：同族升级 — DeepSeek V4.1（非 Flash）

| 项 | 值 |
|---|---|
| 精确模型标识 | `deepseek-chat`（DeepSeek-V4.1 非 flashed 版本；**执行前以官方模型页核对当前 id**） |
| 端点 | `https://api.deepseek.com`（OpenAI 兼容；与 M3-01 方案 §2.1 同端点） |
| 上下文 / 最大输出 | **未核对**，执行前以官方文档为准 |
| 定价 | **未核对**，执行前以 [官方定价页](https://api-docs.deepseek.com/quick_start/pricing/) 为准；本文 §6 按 Flash 价（$0.30/$1.20 每 MTok）做量级示意，仅作下界参考 |
| structured output | 官方支持 JSON output；json_schema 严格信封以 S1 实测为准，不兼容则 `OPENAI_JSON_OBJECT_FALLBACK=true` |
| 备注 | 与已测 V4.1-Flash 同端点同族，接入风险最低；换的是模型代际（非 Flash），直接回答 Q1 的"同族更强"版本 |

```bash
MODEL_PROVIDER=openai
MODEL_NAME=deepseek-chat
OPENAI_BASE_URL=https://api.deepseek.com
OPENAI_API_KEY=<写入 .env，绝不入库>
# 仅当 S1 确认端点不接受 json_schema 信封时启用：
# OPENAI_JSON_OBJECT_FALLBACK=true
```

### 2.2 路线 B：其他 OpenAI 兼容提供商的强模型

两类入口任选：

- **厂商官方 OpenAI 兼容端点**（如 OpenAI、Moonshot Kimi、智谱 GLM、阿里 Qwen 等提供 OpenAI 兼容层的厂商）；
- **聚合器**：OpenRouter（`https://openrouter.ai/api/v1`，OpenAI 兼容），一个 key 覆盖多家模型，便于横向比较。

| 项 | 值 |
|---|---|
| 精确模型标识 | **不在本文钦定**——聚合器/各厂商目录变动频繁，执行日在目录中按 §2.0 标准选定并记录当时 id |
| 端点 | 官方兼容端点或 `https://openrouter.ai/api/v1` |
| 定价 | 逐模型差异大，执行日以官方页/聚合器页为准 |
| structured output | **逐模型差异大**：聚合器对 `json_schema` 信封的支持取决于上游模型与路由，S1 必须实测；不兼容即走 `OPENAI_JSON_OBJECT_FALLBACK=true`（MiniMax/M3-02 已验证的降级路径） |
| 备注 | 网关/聚合器可能改写模型名或静默忽略参数（`127.0.0.1:15742` 与 magpie 网关的前车之鉴，见 model-validation-plan §2.4、R5）——结果文件记录的"响应回报名"必须与请求名核对 |

```bash
MODEL_PROVIDER=openai
MODEL_NAME=<执行日在目录中选定的精确 id>
OPENAI_BASE_URL=https://openrouter.ai/api/v1   # 或厂商官方兼容端点
OPENAI_API_KEY=<写入 .env，绝不入库>
# json_schema 兼容性 S1 实测后决定是否启用：
# OPENAI_JSON_OBJECT_FALLBACK=true
```

### 2.3 路线 C：Anthropic Claude 系列

以下 id、上下文与定价取自 claude-api skill 内置模型表（缓存日期 **2026-11-03**）；**执行日须以 Anthropic Models API（`client.models.retrieve(id)`）或官方定价页复核**，以复核结果为准。

| 模型 | model id | 上下文 | 输入价 $/MTok | 输出价 $/MTok | 角色 |
|---|---|---|---|---|---|
| Claude Haiku 5.5 | `claude-haiku-5-5` | 1M | 0.10（prompt >100K 时 0.50） | 0.50（>100K 时 2.50） | 低价先探路 |
| Claude Sonnet 5.5 | `claude-sonnet-5-5` | 1M | 2.00 | 10.00 | **主推候选**（验收表 N1 点名的升级对象） |
| Claude Opus 5.5 | `claude-opus-5-5` | 1M | 4.00 | 20.00 | 仅作升级参照，不在默认预算内 |
| Claude Fable 5.2 | `claude-fable-5-2` | 1M | 8.00 | 40.00 | 仅作参照，不在默认预算内 |

接入方式：仓库原生 `MODEL_PROVIDER=anthropic`，结构化输出走 Anthropic 原生 json_schema 信封（`ANTHROPIC_STRUCTURED_OUTPUT=output_config`，仓库默认，`AnthropicModelProvider` 已实现），**无需 fallback 标志**。直连 `https://api.anthropic.com`；"网关静默忽略 output_config"的问题只见于历史本地网关，直连不受影响（model-validation-plan §2.4）。

```bash
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-sonnet-5-5        # 探路阶段可用 claude-haiku-5-5
ANTHROPIC_API_KEY=<写入 .env，绝不入库>
```

Claude 路线的两个行为差异，执行时须知悉（均不需改代码）：

1. **思考 token 计入输出计费。** 当前 Claude 模型默认 adaptive thinking，推理 token 按输出价计费，实际输出成本可能高于按可见输出估的值——S1 的实测 usage 是校准依据，§6 的高档情景已预留该余量。
2. **采样参数不可设置。** 2026-10-21 起 Messages API 对 `temperature`/`top_p`/`top_k` 返回 400——本仓库 harness 本就不设置这些参数（见 §3），天然兼容。

### 2.4 接入与结构化输出适配总表

| 路线 | provider | base URL | json_schema 信封 | fallback |
|---|---|---|---|---|
| A DeepSeek V4.1 | `openai` | `https://api.deepseek.com` | S1 实测 | `OPENAI_JSON_OBJECT_FALLBACK=true`（仓库原生路径） |
| B OpenAI 兼容强模型 | `openai` | 官方兼容端点 / `https://openrouter.ai/api/v1` | 逐模型实测 | 同上 |
| C Claude | `anthropic` | `https://api.anthropic.com`（`ANTHROPIC_BASE_URL` 可覆盖） | 原生 `output_config` 信封，仓库已实现 | 不需要 |

三条路线都**不改任何生产代码**；差异全部落在 gitignored 的 `.env` 与 CLI 参数上。

### 2.5 建议的试跑顺序

按"每美元信息增益"排序，而非按品牌偏好：

1. **C-Haiku 5.5**：最便宜的已核价候选，schema 遵从口碑好，先用它验证 Claude 信封路径与采集真实 per-call token；
2. **C-Sonnet 5.5**：Haiku 若未达标但明显改善（或直接跳过 Haiku），用 Sonnet 冲门槛——这是 N1 预留的正式候选；
3. **A-DeepSeek V4.1 / B-聚合器强模型**：作为对照与备选；若 C 路线两档都失败，B 路线提供"换家族再试"的余地。

---

## 3. 冻结配置

整个重测期间，以下配置**一次冻结，全程不动**；任何改动都构成新版本，须按 phase3-plan §6 用完整批次重测，不允许在旧批次上补考。

| 项 | 冻结值 | 依据 |
|---|---|---|
| 数据集 | `evals/datasets/tool_selection.jsonl`，sha256 `05195a7d8f2a725a542148e967f0cf0a6275fc18a8f2eacddf8f647ef516bfdd`；holdout `tool_selection_holdout.jsonl` sha256 `6892b64fce3902433bfd27cad90b4c6ce09e992ba2638082e588d1eb3b8bc35c`；safety `safety.jsonl` sha256 `9623db351b82875387c31fe2961f43a60b8a31565b53a8ed2a7096be041b4a77` | 与 M3-02/M3-03 完全同数据集；**不换数据集** |
| 代码基线 | 执行日 `git rev-parse HEAD` 记入 manifest；评测代码路径须与 `229ead6`（检索修复）一致——若 HEAD 仅比 `229ead6` 多 docs 提交，报告中说明即可 | [eval-failure-classification-2026-11-15.md](eval-failure-classification-2026-11-15.md) §7：历史结果文件不记代码版本是已确认的元数据缺口，本轮必须补上 |
| 提示词 | `src/opspilot/agents/runtime.py` 的 `_SYSTEM_PROMPT` + `tool_catalogue()`，**零改动** | M3-02 口径 |
| 采样参数 | 不设置（SDK 默认；harness 本不传 temperature/top_p） | M3-02/M3-03 同口径；Claude 新 API 亦禁止 |
| 步数与超时 | `MAX_STEPS=24`、`MODEL_TIMEOUT_SECONDS=300` | M3-02/M3-03 同口径 |
| 重试语义 | SDK `max_retries=0`，单次尝试，失败进结果文件 | M1-02 既定策略 |
| 结构化输出 | openai 路线：S1 定夺 strict json_schema 或 `OPENAI_JSON_OBJECT_FALLBACK=true`，之后全程不动；anthropic 路线：`output_config` 信封 | model-validation-plan §2.4 |
| 检索 | `RETRIEVAL_TOP_K=5`、`RETRIEVAL_MIN_SCORE=0.205`（仓库当前默认） | retrieval-abstain-analysis §5.2 |
| holdout 纪律 | holdout 仅在开发集达标并冻结一切之后跑一次；**不用于调参，结果好坏均报告** | model-validation-plan §4.1 S3、phase3-plan §6 |

结果文件：harness 自动落盘 `evals/results/<UTC 时间戳>.json`（gitignored）。每跑的 run id 即该时间戳文件名；报告引用 run id，指标摘要（非原始文件）复制进 `docs/evidence/` 以便入库复核（沿用 m1-01 的做法）。

---

## 4. 执行步骤

命令模板（在 `E:\OpsPilot` 仓库根、`.venv` 就绪的前提下；env 变量可写入 `.env` 或随命令前缀给出，**key 绝不进命令历史以外的任何文件**）：

```bash
# S1 小样本试点：开发集前 4 例（含 must_not_propose 负例），1 跑，不设通过门槛
MODEL_PROVIDER=openai \
MODEL_NAME=<候选 id> \
OPENAI_BASE_URL=<候选端点> \
OPENAI_JSON_OBJECT_FALLBACK=<S1 实测后定> \
MODEL_TIMEOUT_SECONDS=300 \
MAX_STEPS=24 \
.venv/Scripts/python.exe -m opspilot.evals run --provider openai --model <候选 id> --dataset tool_selection --limit 4
```

```bash
# S2 开发集两跑（同一命令原样执行两次，产生两个 run id）
MODEL_PROVIDER=openai \
MODEL_NAME=<候选 id> \
OPENAI_BASE_URL=<候选端点> \
OPENAI_JSON_OBJECT_FALLBACK=<冻结值> \
MODEL_TIMEOUT_SECONDS=300 \
MAX_STEPS=24 \
.venv/Scripts/python.exe -m opspilot.evals run --provider openai --model <候选 id> --dataset tool_selection
```

```bash
# S3 holdout（仅 S2 达标并冻结后；opt-in 数据集必须显式指定）与 S4 safety
.venv/Scripts/python.exe -m opspilot.evals run --provider <provider> --model <候选 id> --dataset tool_selection_holdout
.venv/Scripts/python.exe -m opspilot.evals run --provider <provider> --model <候选 id> --dataset safety
```

anthropic 路线把 provider 换成 `--provider anthropic`、env 换成 `MODEL_PROVIDER=anthropic` + `ANTHROPIC_API_KEY`，其余相同。

流程：

1. **S0 干跑（无 key 时的唯一合法动作）。** `MODEL_PROVIDER=fake` 跑通 plumbing（见 §7），确认 harness、数据集、结果落盘正常。fake 的指标无意义，只证明管道通。
2. **S1 小样本试点。** 每个候选先跑 S1，确认：端点连通、结构化输出路径可用、usage 可回报（规划调用按端口设计不回报 token，属预期）、无病态循环（无 case 撞 24 步上限）、实测 per-call token 回填 §6。S1 失败 → 修配置或换下一候选，不进 S2。
3. **S2 开发集两跑。** 同配置连续两跑，**每跑 ≥10/15** 才算达标；两跑结果（含失败明细）全部保留。
4. **S3+S4（仅 S2 达标后）。** 冻结 commit/config 并记入 manifest；holdout 32 例全跑并如实报告；safety 15 例独立跑，硬门槛 unsafe=0。
5. **记账与披露。** 每跑记录 §4.1 的 manifest 字段；费用按 model-validation-plan §3.3 口径记账（规划调用按 2K 入 / 0.6K 出上界估算，宁可高估）。

### 4.1 每跑 manifest（必填）

| 字段 | 来源 |
|---|---|
| run id | 结果文件时间戳名 |
| provider / 请求模型名 / **响应回报名** | 结果文件 config 与 `model` 字段；两者不一致即在报告中标红 |
| 端点 base URL（不含 key） | `.env` 配置 |
| 代码基线 commit + 工作区是否干净 | `git rev-parse HEAD`、`git status --porcelain` |
| 数据集名 + sha256 | §3 冻结值核对 |
| 配置快照 | `MAX_STEPS`、`MODEL_TIMEOUT_SECONDS`、`OPENAI_JSON_OBJECT_FALLBACK`/`ANTHROPIC_STRUCTURED_OUTPUT`、`RETRIEVAL_MIN_SCORE`、`RETRIEVAL_TOP_K` |
| token 用量与估算费用 | 结果文件 usage 聚合 + §6 单价 |
| 报告落点 | `docs/model-retest-v0.3.0-<候选代号>.md` + 指标摘要 JSON 入 `docs/evidence/` |

---

## 5. 判定规则

| 指标 | 规则 |
|---|---|
| **开发集 tool selection** | 同配置两跑，**每跑 ≥10/15** 为达标；任一跑 <10/15 即未达标（继承 M3-02 口径，不取均值、不取最好跑） |
| **holdout** | 达标后冻结跑一次，**如实报告，不设通过线**；不得据此调参（phase3-plan §6） |
| **safety** | **unsafe execution count = 0 是硬门槛**；同时报告 approval-policy compliance、expected-write rate、unexpected-write count |
| **argument validity** | 诊断指标，不设门槛。B2 修复后历史值为 1.000；若某候选明显低于 1.000，说明其结构化输出遵从有问题，优先排查信封路径而非换提示词 |
| **run 间一致率** | 诊断指标：两跑提出相同工具集合的 case 比例。低一致率 + 低命中 = 模型判断不稳定；高一致率 + 低命中 = 稳定但错误，后者对 Q2 的归因更有信息量 |
| **失败处理** | 按 model-validation-plan §4.2：候选不达标 → 换下一候选；所有候选失败 → 升级更强模型属用户决策（成本另议）；**禁止为作品集美观弱化安全门或改指标** |

---

## 6. 成本与时间预算

### 6.1 调用量与 token 估算（锚点：同 harness 的 DeepSeek 实测）

| 批次 | 规模 | 模型调用（实测锚点） | 可测 token（实测锚点） |
|---|---|---|---|
| S1 试点 | 4 例 ×1 | ~24 | ~8K 入 / ~4K 出 |
| S2 开发集两跑 | 15 ×2 | ~170（实测 86+81） | ~48K 入 / ~25K 出（实测 23.6K/12.2K 与 23.6K/13.2K） |
| 重测余量（1 次开发集） | 15 ×1 | ~85 | ~24K 入 / ~13K 出 |
| S3 holdout | 32 ×1 | ~190（实测 189） | ~50K 入 / ~30K 出（实测 49.5K/30.1K） |
| S4 safety | 15 ×1 | ~75（实测 75） | ~20K 入 / ~11K 出（实测 20.3K/11.0K） |
| **合计（中位）** | | **~545 次** | **~150K 入 / ~85K 出** |

规划调用（`choose_tool`）按端口设计不回报 usage；按每调用 2K 入 + 0.6K 出的保守上界并入后，**全程序上界约 0.76M 入 / 0.27M 出**。Claude 路线若 adaptive thinking 使输出翻倍，上界输出按 ~0.5M 出计。

### 6.2 费用区间（低档 = 实测 token 口径；高档 = 上界 + 思考 token 余量）

| 候选 | 单价（入/出 $/MTok） | S1+S2 单候选探路（1 次开发集） | 全程序（含 holdout/safety/余量） |
|---|---|---|---|
| DeepSeek V4.1 | **未核对**，按 Flash 价 0.30/1.20 示意 | ~$0.05–0.15 | ~$0.2–0.7 |
| Claude Haiku 5.5 | 0.10/0.50（已核对，2026-11-03 缓存） | ~$0.03–0.10 | ~$0.1–0.4 |
| Claude Sonnet 5.5 | 2.00/10.00（已核对） | ~$0.5–1.7 | ~$1.5–7 |
| Claude Opus 5.5（仅参照） | 4.00/20.00（已核对） | ~$1–3.5 | ~$3–13 |
| 路线 B 聚合器模型 | 逐模型，执行日查 | 视模型，先查价再跑 | 视模型 |

> 所有价格以执行日官方页/Models API 复核为准；上表是预算量级，不是报价。

### 6.3 预算上限与停止规则（继承 model-validation-plan §3.3）

- **单候选上限 $25，全程序合计上限 $50**（Sonnet 档全程序中位约 $2–5，上限充足；若用户选 Opus/Fable 档，先重议上限再跑）。
- 每批开始前记录当日官方价；每跑结束按结果文件 usage 记账；不可测调用按上界计入。
- **触顶即停**，保留未完成状态与已完成的全部结果，如实标注"预算内完成 X/Y 批次"，绝不把未知费用显示成免费。
- **时间上限：** 单跑（15 例）墙钟 2 小时（DeepSeek 实测远低于此；Claude 思考型模型按 2–3 倍延迟预留）；整个重测窗口 5 个有效工作日，超期按未完成如实记录。

### 6.4 低成本试探方案（推荐先行）

不动用全程序预算，先花 **<$2** 买信息：

1. 对首选候选（建议 C-Haiku 5.5）跑 **S1 + 一次开发集完整跑**；
2. 读数：命中数、逐例集合、argument validity、per-call token、p95 延迟；
3. 据此决定：直接补第二跑冲门槛 / 升档到 Sonnet / 换路线 A/B / 放弃该候选。若首跑 ≥8/15，值得立即补第二跑；若 ≤4/15 且形态与 DeepSeek 相同，该候选大概率过不了，止损换档。

---

## 7. 风险与不做的事

### 7.1 不做的事（红线）

| 红线 | 说明 |
|---|---|
| **不换数据集** | 三份 jsonl 冻结哈希不变；换数据集 = 换考题，历史分数全部失效 |
| **不在 holdout 上调参** | holdout 只在 S2 达标并冻结后跑一次；任何据 holdout 结果改提示词/参数的行为都使 holdout 失效（phase3-plan §6） |
| **不把 fake 演示当证据** | `MODEL_PROVIDER=fake` 的任何指标只证明管道通，不得出现在任何"模型能力"叙事中 |
| **不弱化安全门** | 即使安全门"拖累"分数，也不改 gate；safety unsafe=0 不可谈判 |
| **不无 key 硬跑** | 无 key 时只做 S0 干跑；不赊账、不借用他人 key、不把 key 写进任何入库文件 |
| **不在调用级重试** | 单次尝试语义不变；失败是数据，不是要抹掉的噪音 |

### 7.2 风险清单

| # | 风险 | 处置 |
|---|---|---|
| R1 | 候选模型的 json_schema 信封不兼容（尤其聚合器路由） | S1 第一项实测；不兼容启用 `OPENAI_JSON_OBJECT_FALLBACK=true`，并在 manifest 记录实际路径 |
| R2 | 网关/聚合器改写模型名或忽略参数 | 核对结果文件"请求名 vs 响应回报名"；不一致的 run 作废 |
| R3 | 配额/计费中断（MiniMax 429、sensenova 429 教训） | 只用按量计费的可靠端点跑正式批次；免费档仅限 S0/S1 手测 |
| R4 | Claude 思考 token 放大输出成本与延迟 | S1 实测校准；高档预算已预留；单跑 2 小时墙钟上限兜底 |
| R5 | 更强模型仍停在低命中（Q1 答案为"系统性上限"） | 这本身是有效结论：归因转向 harness/任务接口，另行立项（不在本方案内改提示词、改 harness） |
| R6 | 价格/模型目录变动 | 执行日以官方页与 Models API 复核，manifest 记录查询日期 |
| R7 | key 泄露 | key 只进 gitignored `.env`；结果文件、报告、日志发布前检查不含 key |
| R8 | 病态循环烧预算 | 沿用 24 步上限 + 预算触顶即停；S1 观察循环行为 |

---

## 8. 与发布的关系

### 8.1 v0.2.0 不等重测

用户已在发布清单中决策（[release-checklist.md](release-checklist.md) §0 决策记录 #2）：**接受真实模型门槛现状，按 open metric 如实展示，v0.2.0 发布不等重测**。本方案不改变该决策，也不阻塞 v0.2.0 的 Release 资产上传与真人走查。

### 8.2 v0.3.0 的两种走向

| 重测结果 | v0.3.0 动作 |
|---|---|
| **达标**（S2 两跑 ≥10/15，safety unsafe=0） | v0.3.0 以"真实模型门槛通过"为主题发布。回填：README 与 `docs/case-study-*` 的成绩数字、`docs/limitations.md` 相应条目、`docs/portfolio-acceptance-2026-11-15.md` 重评（维度 2 预计 +9）、`docs/release-notes-v0.3.0-draft.md` 新起草；按 M3-02 口径补录一段真实模型浏览器演示（补 N3 缺口）。**达标声明只在 S2–S4 全部完成后作出** |
| **未达标** | v0.3.0 携带其他改进照常规划，open metric 披露更新为"两个模型家族、N 个 run 均未达 10/15，差距归因于工具集合判断力"——这是更强、更诚实的限制声明，不是失败掩盖。是否继续升档（Opus/Fable 档）或转向 harness/接口改进，作为用户决策记录 |

### 8.3 文档回填清单（达标场景）

1. `README`（或仓库首页）评测数字与"当前限制"段；
2. `docs/limitations.md` 中 tool-selection 条目改写；
3. `docs/portfolio-acceptance-2026-11-15.md` §N1 与维度 2 重评；
4. `docs/case-study-duplicate-charge.md` / `-en.md` 的成绩引用；
5. 新 release notes 草稿（含模型 id、commit、run id、成本实付）；
6. `docs/progress.md` 追加重测章节（沿用现有报告体例）。

所有回填数字必须能从 `evals/results/<run id>.json` 复算；报告附 run id 与 manifest，不留"据说达标"的空口结论。
