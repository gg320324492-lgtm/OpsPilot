"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

/**
 * Run Detail 的自动刷新，仅限非终态（M2-01："能更新且终态停止刷新"）。
 *
 * 为什么是 `router.refresh()` 而不是 client 轮询接口：这个页面是 Server
 * Component，数据在服务端读取（`lib/data.ts` 的模块注释解释了 operator token
 * 不能进浏览器的原因）。`refresh()` 只让 Next.js 重新执行服务端渲染，读到的
 * 仍是同一批端点，不把任何 token 或 fetch 逻辑搬进浏览器。这个模式页面里已在
 * 用（`decision-actions.tsx`、`reindex-button.tsx`），这里只是把它变成"随状态
 * 自动进行"。
 *
 * 为什么不放进 `page.tsx` 本身：`page.tsx` 是 async Server Component，不能带
 * `"use client"`；一个只有这个职责的小组件也让"终态不刷新"的规则有一个单独
 * 的家。
 *
 * 终态由父组件先判一遍（不挂载本组件），这里再判一次作为保险：一次刷新把 run
 * 推到终态后，本次挂载内计时器立即停止，而不是等下一轮。
 *
 * 间隔 5s：比 worker 的轮询间隔慢半拍也够用，且远低于会让人以为页面卡死的
 * 阈值。它是刷新节奏，不是任何业务语义。
 */
export function RunRefresher({ intervalMs = 5000 }: { intervalMs?: number }) {
  const router = useRouter();

  useEffect(() => {
    let cancelled = false;
    const timer = setInterval(() => {
      if (cancelled) {
        return;
      }
      router.refresh();
    }, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [router, intervalMs]);

  return null;
}
