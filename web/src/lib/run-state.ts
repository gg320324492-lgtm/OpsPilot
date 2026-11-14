/**
 * Run Detail 的状态判定：把 API 的契约字段变成页面能渲染的事实。
 *
 * 全部是纯函数：无 React、无 fetch、无时钟（`lastActivityAt` 只在已有字段间
 * 挑选，不读取当前时间），所以 `e2e/run-state.spec.ts` 可以在不启动浏览器或
 * Docker 的情况下直接调用 —— 与 `e2e/i18n-catalogue.spec.ts` 测目录的方式相同。
 *
 * 每个判定都对应 Phase 3 M2-01 的一条验收语义：终态停止刷新、"已批准但未执行"
 * 与"等待审批"/"被拒绝"/"失败"的区分、幂等重放的识别，以及模型错误的提取。
 * 判定依据的是合同字段本身，而不是页面上碰巧还显示了什么。
 */

import type { components } from "@/lib/api/types";
import type { RunDetail, Trace, ToolCall } from "@/lib/data";

/** 一条 `model_failed` 审计事件的展示投影（字段可缺，缺即未知）。 */
export type ModelFailure = {
  at: string;
  stage: string;
  category: string;
  summary: string;
  httpStatus: number | null;
  attempt: number | null;
};

export type AuditEvent = components["schemas"]["AuditEventDetail"];

/**
 * `completed` 与 `failed` 是仅有的两个终态。
 *
 * `docs/agent-state-machine.md` §1：终态即状态机没有出边。`waiting_approval`
 * 不是终态 —— worker 已释放行，但一次批准就会让它继续；`received` 等 in-flight
 * 状态更不是。页面据此决定是否自动刷新（M2-01："能更新且终态停止刷新"）。
 */
export const TERMINAL_RUN_STATUSES: ReadonlySet<string> = new Set([
  "completed",
  "failed",
]);

export function isTerminalStatus(status: string): boolean {
  return TERMINAL_RUN_STATUSES.has(status);
}

/** 非终态自动刷新；终态停止。 */
export function shouldAutoRefresh(status: string): boolean {
  return !isTerminalStatus(status);
}

/**
 * 这个 run 最近一次有动静的时间，用于"最近更新时间"字段。
 *
 * 优先级即时间顺序：`completed_at` 晚于一切步骤；没有完成时间（非终态）时取
 * trace 的最后一步；trace 还没写步骤时退回 `started_at`；再退 `created_at`
 * （一行 run 至少有创建时间，所以返回值只在 run 本身缺字段时才是 `null`）。
 * `trace` 传 `undefined`（读取失败）时跳过该来源，而不是让整个字段消失。
 */
export function lastActivityAt(
  detail: RunDetail,
  trace: Trace | null | undefined,
): string | null {
  const steps = trace?.steps ?? [];
  const lastStepAt = steps.length > 0 ? steps[steps.length - 1].at : undefined;
  return detail.completed_at ?? lastStepAt ?? detail.started_at ?? detail.created_at;
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.length > 0 ? value : null;
}

function asNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * 从一条审计事件提取 `model_failed` 的展示字段；不是该事件类型则返回 `null`。
 *
 * payload 的形状由契约 §4.1 描述，但这里逐字段判型而不是断言：台账是
 * append-only 的记录，一个旧部署少写一个字段时，缺的字段按"未知"展示，
 * 而不是让整个面板抛错。`error_category` 是机器值，verbatim 展示
 * （`src/i18n/enums.ts` 的规则：quoted value 不翻译）。
 */
export function parseModelFailure(event: AuditEvent): ModelFailure | null {
  if (event.event_type !== "model_failed") {
    return null;
  }
  const payload = (event.payload ?? {}) as Record<string, unknown>;
  return {
    at: event.created_at,
    stage: asString(payload.stage) ?? "unknown",
    category: asString(payload.error_category) ?? "unknown",
    summary: asString(payload.error_summary) ?? "",
    httpStatus: asNumber(payload.http_status),
    attempt: asNumber(payload.attempt),
  };
}

/**
 * 台账最旧在前（契约 §4.1），展示最新在前：操作员先看最近一次失败。
 * 非 `model_failed` 事件被过滤；`events` 缺省（接口未返回）按空处理。
 */
export function latestModelFailures(
  events: readonly AuditEvent[] | null | undefined,
): ModelFailure[] {
  return (events ?? [])
    .map(parseModelFailure)
    .filter((failure): failure is ModelFailure => failure !== null)
    .reverse();
}

/**
 * 幂等重放被 gate 4 拒绝时写入 `tool_calls.error` 的机器值。
 * `domain/policies.py::REASON_IDEMPOTENT_REPLAY`。
 */
export const REPLAY_BLOCKED_REASON = "idempotency_key_already_executed";

/**
 * 这条调用是不是"重放被阻止"：资金已经因为同一个幂等键动过一次，这次提案在
 * gate 4 被拒，`status='rejected'` 且 `error` 是上面的机器值。只有两个条件都
 * 成立才算 —— 一个普通的 gate 拒绝（如 `refund_above_ceiling`）不是重放。
 */
export function isReplayBlockedCall(call: ToolCall): boolean {
  return call.status === "rejected" && call.error === REPLAY_BLOCKED_REASON;
}

/**
 * "已批准但未执行"：M2-01 要求与"等待审批"区分开的那个状态。
 *
 * 三个条件缺一不可：
 * - **非终态** —— 被拒绝的 run 会经 `RESPONDING` 到 `COMPLETED`，它上面的
 *   `awaiting_approval` 调用是"人类说了不"，不是"批了还没跑"；
 * - **非 `waiting_approval` 且无 `pending_approval`** —— 那是"等待审批"，
 *   页面已有专用的 parked 横幅和审批卡；批准之后 `pending_approval` 就不再
 *   返回（`api/routers/runs.py::_load_pending_approval` 只在 parked 时装配），
 *   而批准动作把 run 置回可领取的 `EXECUTING`；
 * - **存在 `awaiting_approval` 的工具调用** —— gate 5 写下的状态在 worker
 *   重新执行该调用前保持不变，所以"批了、worker 还没跑到"在这列上可见。
 *
 * 典型场景：操作员点了批准，但 worker 进程没在轮询 —— run 停在 `executing`，
 * 钱未动，也没有任何东西在推进。这正是最需要一个横幅说清楚的空白。
 */
export function isApprovedAwaitingExecution(detail: RunDetail): boolean {
  if (isTerminalStatus(detail.status)) {
    return false;
  }
  if (detail.status === "waiting_approval") {
    return false;
  }
  if (detail.pending_approval) {
    return false;
  }
  return (detail.tool_calls ?? []).some((call) => call.status === "awaiting_approval");
}
