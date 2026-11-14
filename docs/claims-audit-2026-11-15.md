# 能力声明核对报告 — Phase 3 M1-06（2026-11-15）

对 README、`docs/limitations.md` 与 `knowledge/` 中五类宽泛表述逐项核对：每项给结论（成立 / 需限定 / 不成立），附代码或测试依据（文件:行）。核对基于本分支 `m1-06-claims`（基于 main `927e1a5`）的代码与文档。修改后的表述必须能被现有测试或代码证实；不能证实的已降级为带边界的说法。

---

## 1. 可重放（replayable）

**README 原文**（本轮修改前）：README 第 10 行 "Every model call, tool call and retrieval is recorded and replayable."；第 212 行 "full trace persisted, replayable"（golden path 图注）。

**核对**：
- "recorded" 成立：`TraceRecorder` 为每次 model call、tool call、retrieval、approval 写入 `AgentStep` 与 `AuditEvent` 行（`src/opspilot/tracing/recorder.py:1-50`，`src/opspilot/adapters/persistence/models.py:241` 的 `uq_agent_steps_run_id_sequence` 约束保证序列完整）。
- "replayable" 不成立（按读者通常理解，即"能从 trace 重放恢复一次运行"）：恢复中断运行需要 step journal 与每个工具调用可安全重放，这是尚未实现的 Phase 2 工作（`docs/limitations.md:76`）。当前只有两类窄重放有实现与测试：
  1. **写工具按 `idempotency_key` 重放**：key 由 `derive_idempotency_key` 从 `(run.id, transaction_id)` 确定性派生（`src/opspilot/domain/policies.py:348-359`），gate 4 短路（`src/opspilot/domain/policies.py:317`）+ billing MCP server 按 key 返回原结果（`mcp_servers/billing/server.py:246-358`，`replayed: true` 语义）+ 数据库部分唯一索引兜底（`src/opspilot/adapters/persistence/models.py:270-281`）。测试：`tests/integration/test_refund_idempotency.py:129-148`、`tests/integration/test_mcp_billing.py::test_same_idempotency_key_twice_creates_one_refund`。
  2. **fake provider 的 fixture 重放**：`MODEL_PROVIDER=fake` 按场景重放手写 fixture（`src/opspilot/adapters/models/fake.py:161`），并有 `record_fixture()` 往返测试（`tests/unit/test_record_fixture_roundtrip.py`）。注意：committed fixture 标注 `recorded: false`，是手写占位而非真实录制（`evals/datasets/fixtures/duplicate_charge.json:3-4`；`docs/limitations.md` §8 R2 条目）。
- trace 本身是 append-only 审计账本（`src/opspilot/adapters/persistence/models.py:381-383`），可事后查阅、可核对的（auditable），但不是"可重放执行"。

**结论：需限定。** "recorded" 成立；"replayable" 降级为：trace 完整持久化、可审计；写操作可按幂等键安全重放（单进程内），一般意义的中断恢复重放是未实现的 Phase 2 工作。

## 2. 恰好一次（exactly once）

**README 原文**（本轮修改前）：README 第 8-10 行 "executing it exactly once"；第 492 行 "The golden path — a refund, exactly once."；`docs/architecture.md` 第 11 行同表述。

**核对**：成立，但边界必须写清。三层防重：
1. gate 4 幂等短路：`src/opspilot/domain/policies.py:317`、`src/opspilot/agents/runtime.py:187-200`（`executed_lookup` 生产路径由 worker 注入，`src/opspilot/agents/runtime.py:256-258`）。
2. billing server 原子 check-and-write + 进程内写锁：`mcp_servers/billing/server.py:246-358`（"The guarantee is per-process -- two processes over one store file are still outside it"，`server.py:276`），测试 `tests/integration/test_mcp_billing.py::test_two_concurrent_threads_on_the_same_key_create_one_refund` 等。
3. 数据库部分唯一索引 `uq_tool_calls_idempotency_key_executed`（`src/opspilot/adapters/persistence/models.py:270-281`；`migrations/versions/0001_initial_schema.py:61`）。

**边界**（`docs/mcp-contracts.md:314`、`server.py:35-42`）：保证范围是**单进程、任意并发**；两个进程各持一份内存文档写同一 store 文件时，锁不串行化，可能各追加一条退款且都报 `replayed: false`——这是已用测试钉住的记录在案失败模式（`tests/integration/test_mcp_billing.py::test_second_writer_over_the_same_key_is_discarded`）。部署上每个 server 是独立 stdio 进程、各自 store 文件，因此正常路径不触发该边界。跨 run 重试不在此保证内：幂等键含 `run.id`，人工重新建单会产生新 run、新 key（`docs/phase2-review-2026-11-14.md` §3 已记录）。

**结论：需限定（"单进程内恰好一次"）。** 三层实现与测试充分；跨进程、跨 run 两个边界必须随表述写明。

## 3. 95% 自主处理

**README 原文**（本轮修改前）：README 第 177-178 行 "can run a real enterprise workflow autonomously for the 95% that is reading and reasoning"。

**核对：不成立（作为可验证声明）。** 没有任何评测定义或测量"95% 自主"：评测覆盖的是分类准确率、检索 Recall、工具选择、参数有效性、审批合规、task completion 等 14 项指标（`docs/evals.md` §1），其中没有"自主处理率"。实测数字与 95% 相去甚远：tool selection 0.200（3/15，且不可复现，另两次 3/15、2/15）、task completion 1.000（9/9）、expected-write rate 0.000（0/8）（README 评测结果段，`evals/results/2026-10-11T15-39-42.json`）。"95%" 是修辞性数字，不能被现有测试或代码证实。

**结论：不成立。** 已从 README 删除该比例，改为可证实的表述：模型在"读与推理"环节自主运行，任何资金动作必须经持久化人工审批（该句后半句成立，见第 5 项）。

## 4. unsafety = 0 相关表述

**README 原文**（本轮修改前）：README 第 143 行 figures 表 "unsafe execution count | **0**"；第 640 行 "unsafe execution count 15 0 ← gate, must be 0"；第 744 行 "The four gates are all zero"。

**核对**：
- 指标定义：unsafe execution count = 违反四条安全不变式的执行次数，**必须为 0**，是 gate 而非分数（`docs/evals.md` 第 25、345 行）。门禁在非零时使评测退出码非零（`src/opspilot/evals/metrics.py`；`tests/evals/test_eval_cli.py:22`）。
- 该 run 的 0 是**对这次 70 例评测**的测量结果，不是系统的无条件保证：170 次调用中 90 次被 gate 1 拒绝（全部是 `arguments: {}` 的空提案，README 第 748-751 行），注入类 2 例（safe-007/008）是 scripted 合成用例（README 第 736-739 行），live safety 用例仅 1 例（safe-010）。样本极小，"0" 描述的是这次运行，不是对所有输入的保证。
- M1-05 已确立口径：纯 holdout 不能声称 unsafe=0；安全评测必须有 safety case 且区分 fake/synthetic 与真实模型（`docs/phase3-plan.md:71`；`docs/phase2-review-2026-11-14.md:72`）。
- README figures 表该行已注明 "the security property, and a gate"，来源列指向具体结果文件——作为"这次运行的结果"成立。

**结论：需限定。** "unsafe execution count = 0" 仅在指明"2026-10-11 那次 70 例评测的结果、gate 语义、样本构成（1 live + 2 synthetic safety 用例）"时成立；作为对系统的无条件安全声明不成立。README 中保留数据行、补限定语；不做"系统 unsafe=0"的概括。

## 5. 审批「替代线下多签」风险

**knowledge 原文**：`knowledge/refund-authority-matrix.md:20-28` 写明矩阵是公司政策、系统不分级、`approvals.tool_call_id` 唯一所以"cumulative sign-off cannot exist here, and one approver decides"；`knowledge/refund-policy.md:18-31` "$100 line is policy; the system grades all refunds alike"；`knowledge/escalation-policy.md:25-35` escalation 不是绕过审批的方式。

**核对**：
- 系统侧事实：审批模型无角色、无金额带（`src/opspilot/domain/approvals.py` 的 `ApprovalRequest` 无 `role`/`band`/`amount_band` 字段；`tests/unit/test_the_limitations_section_eight_claims_still_hold.py:307-337` item 5 守卫断言这一点，且 `approvals.tool_call_id` 唯一约束在 `migrations/versions/0001_initial_schema.py`）。单人 token，`decided_by` 恒为 `"operator"`（`docs/limitations.md:12-22`，`src/opspilot/api/routers/approvals.py:194-197`）。
- **风险确认**：knowledge 文档描述的多级累计签批（$600 需 Billing Operations + Finance manager；$10,000 以上需 CFO）在系统里**没有对应实现**——一个审批人、一次点击即放行任意金额。若读者把 knowledge 文档当成系统已实现的能力，会把"业务政策要求多级签批"误读为"系统已执行多签"，这是 M1-06 要消除的混淆。`docs/phase2-review-2026-11-14.md:102` 已指出应补全线下处理路径的说明。
- escalation 边界：代码中 escalation 只是 `escalated` 标志与升级回复（`src/opspilot/agents/runtime.py:1216`、`:1301`；`docs/api-contract.md` §3 response 字段），无 tier 路由实现；escalation-policy 自己已写明"escalation 不是绕过审批"，与代码一致，但 "no approver is available" 分支对应的线下人工流程系统不执行、也无从记录。

**结论：需限定（knowledge 文档补边界声明）。** 系统保证的是"一次审批授权一次调用、无审批不放行"（成立，有测试与约束）；不提供多级签批、金额分级、审批人角色；这些是线下/流程控制，knowledge 文档必须明确标注"系统不执行、不记录"。

---

## 汇总表

| # | 声明 | 结论 | 关键依据 |
|---|---|---|---|
| 1 | 可重放 replayable | 需限定：记录/审计成立；幂等重放（单进程）成立；一般中断恢复重放未实现 | `tracing/recorder.py:1-50`；`domain/policies.py:348-359`；`docs/limitations.md:76` |
| 2 | 恰好一次 exactly once | 需限定：单进程内成立（三层防重）；跨进程/跨 run 不保证 | `mcp_servers/billing/server.py:246-358`；`models.py:270-281`；`docs/mcp-contracts.md:314` |
| 3 | 95% 自主处理 | 不成立：无任何评测定义或测得 95%；实测指标远低于此且该数字不可复现 | `docs/evals.md` §1；README 第 638-645 行 |
| 4 | unsafety = 0 | 需限定：是那次评测的 gate 结果（1 live + 2 synthetic safety 用例），不是系统无条件保证 | `docs/evals.md` 第 25、345 行；README 第 736-751 行 |
| 5 | 审批替代线下多签 | 需限定：系统只有单人单审批；多级签批/金额分级是线下流程，系统不执行不记录 | `domain/approvals.py`；`test_the_limitations_section_eight_claims_still_hold.py:307-337`；`docs/limitations.md:12-22` |

## 本轮修改

- `knowledge/refund-authority-matrix.md`：在 "Who May Authorise What" 小节内补"系统执行范围比矩阵窄"边界段：单审批人、无金额分级、无多级签批，线下累计签批由公司流程执行、系统不记录。
- `knowledge/refund-policy.md`：在 "Refund Limits" 小节补 $100 阈值的"系统分级"边界说明，与 matrix 呼应。
- `knowledge/escalation-policy.md`：在 "What Escalation Is Not" 小节补系统行为边界：tier 路由与线下处理不在系统内执行、trace 只证明升级已发起。
- `docs/limitations.md` §1：补"审批是单人单签，不替代线下多级签批"的明确边界。
- `README.md`：删除 "95%" 修辞性数字（第 177-178 行），把开头 "recorded and replayable"（第 10 行）改为有依据的表述，并为 "exactly once" 首次出现处补单进程边界指向。
- 新建本核对报告 `docs/claims-audit-2026-11-15.md`。
