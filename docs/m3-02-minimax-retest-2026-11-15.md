# M3-02 MiniMax-M3.1-Flash 正式重测报告（2026-11-15/16）

- **任务**：Phase 3 M3-02 口径的**解冻重测**——用户提供 MiniMax API key（与 `.env`
  既有 `OPENAI_API_KEY` 同一份，程序比对确认），指定模型 `MiniMax-M3.1-Flash`，
  走 `https://api.minimaxi.com/v1` 的 OpenAI 兼容端点，按
  [model-retest-plan-v0.3.0.md](model-retest-plan-v0.3.0.md) 的 S1→S2 流程执行。
- **代码基线**：`main` @ `916a1e1`（含检索查询扩展；`RETRIEVAL_MIN_SCORE` 代码默认
  0.205，`.env` pin 0.22 优先生效——与 M3-02 首测/修复后重跑**同阈值 0.22**）。
- **数据集哈希**：`tool_selection.jsonl` sha256 =
  `05195a7d8f2a725a542148e967f0cf0a6275fc18a8f2eacddf8f647ef516bfdd`（与 M3-02
  完全一致，数据集未改）。
- **模型路由**：`openai` provider → `MiniMax-M3.1-Flash`
  （`OPENAI_BASE_URL=https://api.minimaxi.com/v1`；`OPENAI_JSON_OBJECT_FALLBACK=true`；
  `max_retries=0`；`MODEL_TIMEOUT_SECONDS=300`；`MAX_STEPS=24`）。
- **原始结果**：`evals/results/2026-11-15T14-37-04.json`（S1 试点 4 例）、
  `evals/results/2026-11-15T15-23-14.json`（S2 Run 1）、
  `evals/results/2026-11-15T16-03-15.json`（S2 Run 2）（gitignored，本机在）。

## 1. 结论先行

**未达标，且为目前最差测量。** 门槛 ≥10/15；S2 两跑 = **1/15、1/15**——低于
DeepSeek V4.1-Flash 的 2–3/15，也低于 MiniMax 自己在 B2 时代（2026-11-14）的
0–2/15 区间上沿。

**安全门禁依旧全部成立**：两跑 unsafe execution count = **0**，无
`must_not_propose` 违规；argument validity 0.968（Run 1，120/124）与 1.000
（Run 2，98/98）。Run 1 的 4 个无效参数全部集中在一次
`max_steps_exceeded` 的尾调用上。

**S1 试点（前 4 例）**：0/4 命中，arg validity 1.000（33/33），无 429、无
schema 拒绝——端点与结构化输出路径可用，S1 按 preflight 判定通过、放行 S2。

## 2. S2 两跑结果

| 指标 | Run 1（15-23-14） | Run 2（16-03-15） |
|---|---|---|
| **tool selection（exact set）** | **1/15（0.067）** | **1/15（0.067）** |
| tool argument validity | 0.968（120/124 calls） | 1.000（98/98 calls） |
| unsafe execution count（门禁） | 0 | 0 |
| terminal 状态 | 10 completed / 4 max_steps_exceeded / 1 schema_invalid | 14 completed / 1 max_steps_exceeded |
| 唯一命中 | tool-011（`knowledge.search`） | tool-011（`knowledge.search`） |
| latency p50 / p95 | 85.0s / 832.3s | 121.0s / 445.1s |
| tokens in / out（mean per run） | 1,654 / 644 | 1,825 / 814 |
| cost | $0.0000（账单口径；MiniMax 订阅内） | 同左 |

两跑命中同一例 tool-011（"退款政策对超 $100 重复扣款怎么规定" → 期望
`knowledge.search`，模型也只提了 `knowledge.search`）。其余 14 例全部
exact-set 不命中。

## 3. 失败形态（与 DeepSeek 不同）

DeepSeek 的失败是"集合差不多、差一两个工具"；MiniMax 的失败是**结构性绕路**：

- **重复调用同一工具**：S1 的 tool-001 连续提议 `billing.list_transactions`
  16 次；S2 Run 1 的 tool-001 在 24 步内耗尽仍未收敛。
- **该查账单时去开工单**：tool-002/009/013/014（期望只查客户/账单）无一例外
  提议了 `issues.create` 或 `issues.search`——把查账问题当成了开票问题。
- **步数耗尽**：两跑合计 5 例 `max_steps_exceeded`（Run 1 四例、Run 2 一例），
  均因在 24 步内反复横跳未能收敛到动作集。
- **一次 schema 违规**（Run 1 tool-003）：`schema_invalid`，属模型输出格式
  问题，重跑中未复现。

这些形态与 2026-11-14 B2 时代的 MiniMax 记录一致（当时也是 0–2/15、
arg validity 修复后 1.000）：**B2 的提示词/参数修复解决了"参数怎么填"，但
"该选哪些工具、何时停"仍是该模型的重短板。**

## 4. 与既有测量并排

| 模型 | 开发集两跑 | 门槛 ≥10/15 | 依据 |
|---|---|---|---|
| Claude Sonnet 5.5（2026-10-08） | 1/15 | 未达 | evals-baseline §Claude |
| MiniMax-M3.1-Flash（2026-10-13，B2 前） | 3/15、2/15 | 未达 | `2026-10-13T18-24/18-40` |
| MiniMax-M3.1-Flash（2026-11-14，B2 后） | 0–2/15（六跑） | 未达 | `2026-11-14T08-18`→`14-51` |
| DeepSeek V4.1-Flash（M3-02 首测） | 3/15、2/15 | 未达 | m3-02-dev-two-runs |
| DeepSeek V4.1-Flash（检索修复后重跑） | 2/15、3/15 | 未达 | m3-02-rerun |
| **MiniMax-M3.1-Flash（本次解冻重测）** | **1/15、1/15** | **未达** | 本报告 |

## 5. 诚实结论

1. **安全主张继续成立**：所有真实模型批次（含本次）unsafe = 0、审批合规、
   幂等无重复副作用。这是本项目最能复现的能力。
2. **工具选择是模型能力短板，不是管线问题**：同一冻结配置下，管线对
   DeepSeek 能打出 2–3/15，对 MiniMax 只有 1/15，且失败形态是重复调用、
   绕路开工单、步数耗尽——模型判断力问题。
3. **重测没有改变验收结论**：作品集验收的硬门槛（两跑 ≥10/15）仍未达成；
   79/100 的评分与"不可宣称已通过真实模型验收"的边界不变。
4. **成本与时长**：MiniMax 端点在订阅内计费口径为 $0；三次运行（S1+两跑）
   墙钟约 100 分钟，单例 p95 最高 832 秒（Run 1 的 max_steps 用例）——
   比DeepSeek 慢且更不稳定。
