# S1 真实模型试点 preflight 清单

**日期：** 2026-11-15。**性质：** 准备清单（preflight）。本文档撰写过程未发起任何真实模型 API 调用、未读取 `.env` 或任何密钥；所有 key 位置均以 `<占位符>` 表示。
**上游依据：** [model-retest-plan-v0.3.0.md](model-retest-plan-v0.3.0.md)（下称"重测方案"），重点 §2 候选模型、§3 冻结配置、§4 执行步骤与 §4.1 manifest、§6 成本预算、§7 风险。冲突处以重测方案为准。
**当前状态：** 重测整体暂缓（用户决策：暂不支出）；D1 已于 2026-11-15 决策为**路线 A：DeepSeek V4.1**。本文档的作用是**解冻时一步到位**：届时确认 §2 决策清单剩余项（D2–D5）、按 §3–§4 完成零成本准备，即可直接进入 §5 的 S1 当天流程。

---

## 1. S1 的目的与入口条件

**S1 是什么：** 每个候选模型在真实端点上的小样本试点——开发集 `evals/datasets/tool_selection.jsonl` 前 4 例（含 must_not_propose 负例），1 跑，不设通过门槛（重测方案 §4 流程第 2 步）。它回答的是"这个候选能不能进入 S2 开发集两跑"，而不是"这个模型达不达标"。

**入口条件（全部满足才允许发起 S1）：**

1. **S0 干跑已通过**——`MODEL_PROVIDER=fake` 管线验证已完成：harness、数据集装载、结果文件落盘均正常。fake 的指标无意义，只证明管道通（重测方案 §4.1、§7.1 红线）。S0 的结论已记录在案，S1 不再重跑 S0。
2. **§2 决策清单逐项确认完毕**（路线、预算、结果文件去向）。
3. **§3 环境准备就绪**（`.env` 新增行、数据集 sha256 核对、命令 dry-run）。
4. **解冻决策已做出**：本文档落笔时用户明确"重测继续暂缓、不花钱"；S1 只能在用户明确解冻后发起。

---

## 2. 启用前的决策清单（需用户逐项确认）

| # | 决策项 | 选项与默认 | 确认结果（执行日填写） |
|---|---|---|---|
| D1 | **走哪条路线** | **已决策（2026-11-15）：路线 A — DeepSeek V4.1**（`deepseek-chat`，端点 `https://api.deepseek.com`；与已测 V4.1-Flash 同端点同族，接入风险最低，直接回答重测方案 Q1 的"同族更强"版本）。B/C 保留为备选：若路线 A 的 S1 失败，按重测方案 §2.5 顺序换档 | 已决策：A |
| D2 | **预算上限** | 继承重测方案 §6.3：单候选上限 **$25**，全程序合计上限 **$50**；若选 Opus/Fable 档，先重议上限再跑。S1 试点本身预计 <$2（§6.4 低成本试探） | 待定 |
| D3 | **结果文件是否入 git** | 默认**不入**：`evals/results/` 已被 [.gitignore](../.gitignore) 忽略（仅保留 `.gitkeep` 与 `README.md`），仓库约定即"结果文件不入库，指标摘要（非原始文件）复制进 `docs/evidence/` 供复核"（重测方案 §3 末段）。若用户要求连原始结果文件入库，需显式解除 ignore 并说明理由 | 待定（默认：不入） |
| D4 | **候选模型 id 与端点** | **随 D1 确定：`deepseek-chat` @ `https://api.deepseek.com`**（重测方案 §2.1）；执行日跑前仍须以官方模型页复核当前精确 id | 已定（执行日复核 id） |
| D5 | **单跑墙钟与窗口** | 单跑（15 例）2 小时；整个重测窗口 5 个有效工作日（§6.3）。S1 只占其中很小一段，但需确认执行当天有完整时段 | 待定 |

> 红线提醒（重测方案 §7.1）：不把 fake 演示当证据；无 key 时只做 S0/干跑；不赊账、不借用他人 key、不把 key 写进任何入库文件。

---

## 3. 环境准备（解冻后、花钱前完成）

### 3.1 `.env` 需要新增的行

`.env` 已被 gitignore；以下模板只含占位符，**绝不写真实 key**。D1 已决策为路线 A：**只追加下方路线 A 的块**（路线 B/C 块仅作备选参考保留）；不要混填多条路线。

路线 A（已决策）/ B（备选），OpenAI 兼容：

```bash
MODEL_PROVIDER=openai
MODEL_NAME=<候选精确 id>
OPENAI_BASE_URL=<候选端点>            # A: https://api.deepseek.com ；B: https://openrouter.ai/api/v1 或厂商官方兼容端点
OPENAI_API_KEY=<your-key>
# 仅当 S1 实测端点不接受 json_schema 信封时启用（先注释，实测后决定）：
# OPENAI_JSON_OBJECT_FALLBACK=true
```

路线 C（Anthropic，仓库原生支持）：

```bash
MODEL_PROVIDER=anthropic
MODEL_NAME=claude-sonnet-5-5           # 探路阶段可用 claude-haiku-5-5
ANTHROPIC_API_KEY=<your-key>
# 结构化输出走原生 output_config 信封（ANTHROPIC_STRUCTURED_OUTPUT=output_config 为仓库默认），无需 fallback 标志
```

注意（来自本仓库经验，[opspilot-phase2-progress] 一类教训）：`.env` 中暂不生效的行**整行注释掉**，不要留空值行——空行值可能被当成空字符串配置读走。

### 3.2 三条路线的 provider / base_url / model 组合表

| 路线 | `--provider` | `MODEL_PROVIDER` | base URL | json_schema 信封 | fallback |
|---|---|---|---|---|---|
| A DeepSeek V4.1 | `openai` | `openai` | `https://api.deepseek.com` | S1 实测 | `OPENAI_JSON_OBJECT_FALLBACK=true`（仓库原生路径） |
| B OpenAI 兼容强模型 | `openai` | `openai` | 官方兼容端点 / `https://openrouter.ai/api/v1` | 逐模型实测 | 同上 |
| C Claude | `anthropic` | `anthropic` | `https://api.anthropic.com`（`ANTHROPIC_BASE_URL` 可覆盖） | 原生 `output_config`，仓库已实现 | 不需要 |

三条路线都**不改任何生产代码**；差异全部落在 gitignored 的 `.env` 与 CLI 参数上（重测方案 §2.4）。

### 3.3 json_schema 与 fallback 的 S1 判定步骤

1. S1 的第一条实测项就是结构化输出：先以**不带** `OPENAI_JSON_OBJECT_FALLBACK` 的默认配置发起 S1。
2. **判定标准（来自仓库实测经验，[openai_provider.py](../src/opspilot/adapters/models/openai_provider.py) 注释与 model-validation-plan §2.4）：**
   - **兼容**：S1 各 case 的 structured-output 调用正常解析，`failure_reason` 无 schema/解析类错误，`terminal_status` 非 `failed`；
   - **不兼容形态一（显式拒绝）**：端点对 `json_schema` 信封返回 400（如 M3-01 实测的 `unknown variant 'object'`），或报 `response_format`/schema 相关错误；
   - **不兼容形态二（静默忽略，更隐蔽）**：端点接受信封但忽略它——回复时而"发明键名的 JSON"、时而 Markdown 散文，`StructuredOutputError.decoded=False`，重试也不收敛（MiniMax 实测形态）。
   - 两条不兼容形态任一出现，且同一模型普通对话正常，即判定该端点不接受 json_schema 信封。
3. 判定不兼容 → 在 `.env` 启用 `OPENAI_JSON_OBJECT_FALLBACK=true`（仓库原生降级路径：改问 `json_object`、schema 进 system prompt；MiniMax/M3-02 已验证），**重跑 S1 确认降级路径可用**，然后把该值作为冻结配置，之后全程不动（重测方案 §3）。
4. 把实际采用的路径（strict json_schema 或 fallback）记入 §4.1 manifest 的配置快照。
5. anthropic 路线跳过以上判定：`output_config` 信封由 `AnthropicModelProvider` 原生实现，无需 fallback 标志。

---

## 4. 干跑自检清单（零成本，解冻前即可做）

以下每项都不发起真实模型调用、不花钱：

- [ ] **S0 结论确认**：重测方案 §4 流程第 1 步的 S0 干跑已通过（fake 管线、结果落盘正常）。若距解冻时间较久、代码基线已变，重跑一次 S0：
  ```bash
  MODEL_PROVIDER=fake .venv/Scripts/python.exe -m opspilot.evals run --provider fake --dataset tool_selection --limit 4
  ```
  fake 的指标无意义，只看：命令退出码为 0、`evals/results/` 下生成了新的时间戳结果文件。
- [ ] **数据集 sha256 核对**（三个数据集都必须与重测方案 §3 冻结值一致，**不换数据集**）：
  ```bash
  python -c "import hashlib;[print(f,hashlib.sha256(open(f'evals/datasets/{f}','rb').read()).hexdigest()) for f in ['tool_selection.jsonl','tool_selection_holdout.jsonl','safety.jsonl']]"
  ```
  期望值：
  - `tool_selection.jsonl` → `05195a7d8f2a725a542148e967f0cf0a6275fc18a8f2eacddf8f647ef516bfdd`
  - `tool_selection_holdout.jsonl` → `6892b64fce3902433bfd27cad90b4c6ce09e992ba2638082e588d1eb3b8bc35c`
  - `safety.jsonl` → `9623db351b82875387c31fe2961f43a60b8a31565b53a8ed2a7096be041b4a77`
- [ ] **命令 dry-run 写法确认**：S1 命令模板见 §5 第 4 步；`--limit 4` 表示开发集前 4 例，`--dataset tool_selection` 是默认四数据集之一，无需 opt-in；holdout 是 opt-in 数据集，必须显式 `--dataset tool_selection_holdout`，且仅在 S2 达标并冻结后跑（§3 冻结纪律）。
- [ ] **结果文件命名与落盘位置确认**：harness 自动落盘 `evals/results/<UTC 时间戳>.json`（如 `evals/results/2026-11-15T13-02-31.json`）；每跑的 run id 即该时间戳文件名。该目录 gitignored，`.gitkeep` 与 `README.md` 除外。结果文件顶层结构（实测）：
  - `provider` / `model`：provider 名与**响应回报名**（网关改写检测点）；
  - `config`：`datasets`、`limit`、`retrieval_top_k`、`retrieval_min_score` 等本次配置快照；
  - `metrics`：各指标 `name`/`value`/`gate` 列表；
  - `breakdown`：分数据集指标；
  - `results`：逐 case 明细——`case_id`、`terminal_status`、`failure_reason`、`proposed_tools`、`expected_tools`、`must_not_propose`、`executed_tool_calls`、`model_calls`（内含每次调用的 `provider`/`model`/`latency_ms`/`tokens_available`/`input_tokens`/`output_tokens`/`estimated_cost_usd`）等。
- [ ] **manifest 字段清单备好**（引用重测方案 §4.1，每跑必填；执行时复制下表填写）：

  | 字段 | 来源 / 填法 |
  |---|---|
  | run id | `evals/results/` 下本次时间戳文件名 |
  | provider / 请求模型名 / 响应回报名 | 请求名来自命令与 `.env`；回报名读结果文件顶层 `model`（及 `model_calls[].model`）；**两者不一致即在报告中标红** |
  | 端点 base URL（不含 key） | `.env` 的 `OPENAI_BASE_URL` / `ANTHROPIC_BASE_URL` |
  | 代码基线 commit + 工作区状态 | `git rev-parse HEAD`；`git status --porcelain` 应为空 |
  | 数据集名 + sha256 | §4 自检清单的三个冻结值 |
  | 配置快照 | `MAX_STEPS=24`、`MODEL_TIMEOUT_SECONDS=300`、`OPENAI_JSON_OBJECT_FALLBACK`（或 `ANTHROPIC_STRUCTURED_OUTPUT=output_config`）、`RETRIEVAL_MIN_SCORE=0.205`、`RETRIEVAL_TOP_K=5` |
  | token 用量与估算费用 | 聚合结果文件 `results[].model_calls[]` 的 `input_tokens`/`output_tokens`/`estimated_cost_usd`；规划调用按端口设计不回报 token，按 2K 入 / 0.6K 出上界估算并入 |
  | 报告落点 | `docs/model-retest-v0.3.0-<候选代号>.md` + 指标摘要 JSON 入 `docs/evidence/` |
- [ ] **冻结配置快照确认**（S1 起就按冻结值跑，之后全程不动）：`MAX_STEPS=24`、`MODEL_TIMEOUT_SECONDS=300`、`RETRIEVAL_TOP_K=5`、`RETRIEVAL_MIN_SCORE=0.205`、SDK `max_retries=0`、采样参数不设置（重测方案 §3）。
- [ ] **`git status` 干净**：评测代码路径与检索修复 commit `229ead6` 一致；若 HEAD 仅比 `229ead6` 多 docs 提交，报告中说明即可（重测方案 §3 代码基线行）。

---

## 5. S1 当天的执行顺序

前提：§2 决策清单已确认、§3 环境已备好、§4 自检全绿。全程冻结配置，任何配置改动都构成新版本、须按 phase3-plan §6 重测（重测方案 §3）。

| 步 | 动作 | 判定标准 | 中止/回退条件 |
|---|---|---|---|
| 1 | 确认 `.env` 只含路线 A 的行，key 为真实值但**不落任何入库文件**；`git status --porcelain` 为空 | 两项均满足 | `.env` 有残留他路线行 → 清理后再跑 |
| 2 | 记录当日官方单价与预算上限（D2），写进执行笔记 | 单价有官方出处 | 拿不到官方价 → 换有公开定价的候选，不估着跑 |
| 3 | 记录 `git rev-parse HEAD` 与数据集 sha256（§4 命令），入 manifest | 三 sha256 与冻结值一致 | 不一致 → 停，先查数据集是否被改动 |
| 4 | **S1 试点**：开发集前 4 例，1 跑（命令模板见下） | 见第 5 步判定 | — |
| 5 | **S1 判定**（重测方案 §4 流程 2） | 全部满足才进 S2：① 端点连通；② 结构化输出路径可用（或已按 §3.3 启用 fallback 并复跑确认）；③ usage 可回报（规划调用不回报 token 属预期）；④ 无病态循环（无 case 撞 24 步上限）；⑤ 实测 per-call token 回填 §6 预算 | 任一不满足 → 修配置或换下一候选（§2.5 顺序），**不进 S2**；连续两候选 S1 失败 → 停下复盘再决策 |
| 6 | **S2 开发集两跑**（仅 S1 通过后）：同一条命令原样执行两次，产生两个 run id | 门槛：**每跑 ≥10/15**（不取均值、不取最好跑） | 第一跑已 <10/15 → 第二跑仍要跑完留档，但本轮判未达标；是否换候选按 §6.4 读数决策 |
| 7 | **S3+S4**（仅 S2 达标并冻结一切后）：holdout 32 例跑一次（opt-in，须显式指定）+ safety 15 例独立跑 | holdout 如实报告不设线、不据此调参；safety 硬门槛 **unsafe = 0** | safety unsafe > 0 → 硬门槛未过，如实记录，不进入发布叙事 |
| 8 | **记账与披露**：每跑填 §4.1 manifest；费用按 model-validation-plan §3.3 口径记账；指标摘要入 `docs/evidence/` | 所有回填数字能从 `evals/results/<run id>.json` 复算 | 对不上账 → 以结果文件为准修正报告，不留"据说达标" |

S1 / S2 命令模板（重测方案 §4，openai 路线示例；anthropic 路线把 provider 与 env 换成 §3.2 的 C 行）：

```bash
# S1 小样本试点：开发集前 4 例，1 跑，不设通过门槛
MODEL_PROVIDER=openai \
MODEL_NAME=<候选 id> \
OPENAI_BASE_URL=<候选端点> \
OPENAI_JSON_OBJECT_FALLBACK=<S1 实测后定> \
MODEL_TIMEOUT_SECONDS=300 \
MAX_STEPS=24 \
.venv/Scripts/python.exe -m opspilot.evals run --provider openai --model <候选 id> --dataset tool_selection --limit 4
```

```bash
# S2 开发集两跑：同一命令原样执行两次，产生两个 run id（去掉 --limit）
MODEL_PROVIDER=openai \
MODEL_NAME=<候选 id> \
OPENAI_BASE_URL=<候选端点> \
OPENAI_JSON_OBJECT_FALLBACK=<冻结值> \
MODEL_TIMEOUT_SECONDS=300 \
MAX_STEPS=24 \
.venv/Scripts/python.exe -m opspilot.evals run --provider openai --model <候选 id> --dataset tool_selection
```

```bash
# S3 holdout（仅 S2 达标并冻结后）与 S4 safety
.venv/Scripts/python.exe -m opspilot.evals run --provider <provider> --model <候选 id> --dataset tool_selection_holdout
.venv/Scripts/python.exe -m opspilot.evals run --provider <provider> --model <候选 id> --dataset safety
```

---

## 6. 风险与回退（摘要，全文见重测方案 §7）

| 风险 | 处置（要点） |
|---|---|
| 候选模型 json_schema 信封不兼容（尤其聚合器路由） | S1 第一项实测；不兼容 → `OPENAI_JSON_OBJECT_FALLBACK=true`（仓库原生路径），复跑 S1 确认，manifest 记录实际路径 |
| 网关/聚合器改写模型名或静默忽略参数 | 核对结果文件"请求模型名 vs 响应回报名"，不一致在报告中标红（`127.0.0.1:15742` 与 magpie 网关前车之鉴，model-validation-plan §2.4、R5） |
| 超时/限流（MiniMax 429、sensenova 429 教训） | 只用按量计费的可靠端点跑正式批次；免费档仅限 S0/S1 手测；`MODEL_TIMEOUT_SECONDS=300`、`max_retries=0` 不变，失败进结果文件如实记录 |
| 费用失控 | 单候选 $25 / 全程序 $50 触顶即停（D2）；每跑按 usage 记账，规划调用按上界计入，绝不把未知费用显示成免费 |
| Claude adaptive thinking 推高输出成本 | 思考 token 按输出价计费，§6 高档情景已预留 ~2 倍输出余量；S1 的实测 usage 是校准依据 |
| 结果文件误入库 | `evals/results/` 默认 gitignored；提交前 `git status` 检查，勿 `git add -f` 该目录（D3 决策） |

---

## 7. 当天操作卡（从设置 env 到拿到结果，10 步）

```bash
# 0) 前置：解冻决策 + D2–D5 已确认；D1 已决策为路线 A（DeepSeek V4.1）。

# 1) 工作区与基线
git status --porcelain && git rev-parse HEAD

# 2) 数据集 sha256 核对（三个值须与 §4 清单一致）
python -c "import hashlib;[print(f,hashlib.sha256(open(f'evals/datasets/{f}','rb').read()).hexdigest()) for f in ['tool_selection.jsonl','tool_selection_holdout.jsonl','safety.jsonl']]"

# 3) 编辑 .env：追加路线 A 的 env 行（key 用真实值，绝不入库）
#    MODEL_PROVIDER=openai
#    MODEL_NAME=deepseek-chat
#    OPENAI_BASE_URL=https://api.deepseek.com
#    OPENAI_API_KEY=<your-key>

# 4) S1 试点（开发集前 4 例，1 跑）
MODEL_PROVIDER=openai MODEL_NAME=deepseek-chat OPENAI_BASE_URL=https://api.deepseek.com \
MODEL_TIMEOUT_SECONDS=300 MAX_STEPS=24 \
.venv/Scripts/python.exe -m opspilot.evals run --provider openai --model deepseek-chat --dataset tool_selection --limit 4

# 5) 读结果：run id = evals/results/ 下最新时间戳文件名
ls -t evals/results | head -3

# 6) S1 判定：端点连通、结构化输出可用、usage 可回报、无 case 撞 24 步、token 回填预算
#    不兼容 → .env 启用 OPENAI_JSON_OBJECT_FALLBACK=true 后重跑第 4 步再判定

# 7) S1 通过 → S2 两跑（同命令去掉 --limit，原样跑两次）
MODEL_PROVIDER=openai MODEL_NAME=deepseek-chat OPENAI_BASE_URL=https://api.deepseek.com \
MODEL_TIMEOUT_SECONDS=300 MAX_STEPS=24 \
.venv/Scripts/python.exe -m opspilot.evals run --provider openai --model deepseek-chat --dataset tool_selection

# 8) 判定门槛：两跑每跑 >=10/15 才达标（不取均值、不取最好跑）

# 9) 达标并冻结后：S3 holdout 一次 + S4 safety（opt-in 数据集必须显式指定）
.venv/Scripts/python.exe -m opspilot.evals run --provider openai --model deepseek-chat --dataset tool_selection_holdout
.venv/Scripts/python.exe -m opspilot.evals run --provider openai --model deepseek-chat --dataset safety

# 10) 记账与披露：每跑填 manifest（§4.1），指标摘要入 docs/evidence/，报告写 docs/model-retest-v0.3.0-<候选代号>.md
```

> 每一步结束都核对结果文件里的"请求模型名 vs 响应回报名"；触预算上限即停（D2）。
