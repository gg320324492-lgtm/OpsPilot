# UI 选型记录 — 2026-11-16

## 结论

OpsPilot 第四阶段采用 **HeroUI 作为 Web UI 主库**，继续使用项目现有的 Tailwind CSS 4；图标使用 Lucide；现有 i18n、数据层、API 契约和安全门不变。

## 选型依据

本地参考集：`E:\ui-libraries\web\`，说明见 `E:\ui-libraries\README.md`。

| 维度 | HeroUI | shadcn/ui | Radix | Mantine | Ant Design |
|---|---|---|---|---|---|
| 当前栈匹配 | React 19 / Next / Tailwind 4 | Tailwind 友好 | React 友好 | React 友好 | React 友好 |
| 默认视觉 | 高 | 低，需要组装 | 低，需要设计 | 中高 | 中 |
| 交互行为 | React Aria 基础 | 依赖 Radix 组合 | 强 | 强 | 强 |
| 动效/状态 | 内置且适合展示 | 需自行组合 | 需自行组合 | 有 | 有 |
| 收尾成本 | 低到中 | 中到高 | 高 | 中 | 中到高 |
| OpsPilot 适配 | **最高** | 高度可定制但过于开放 | 行为底座合适 | 样式体系重复 | 企业后台味道过重 |

本地 HeroUI 3.2.7 的包说明明确写有：

- 基于 Tailwind CSS 4；
- 支持 React 19 和 Next.js；
- 基于 React Aria，包含可访问交互行为；
- Apache-2.0 许可证。

## 使用边界

HeroUI 只负责组件呈现和交互反馈，不负责：

- 审批授权；
- 状态机；
- API 数据真实性；
- 退款幂等；
- 审计记录；
- 模型安全；
- 权限判断。

这些仍由现有后端和领域代码决定。任何 UI 组件都不能绕过 API 或审批门。

## 回滚方案

如果 HeroUI 引入后出现构建、包体、样式覆盖或可访问性问题：

1. 保留语义 token、页面信息层级和交互验收；
2. 退回现有 Tailwind 组件；
3. 只保留经过验证的 Lucide 图标和 CSS 过渡；
4. 不改变 API、数据库和业务流程。

## 许可证与交付

HeroUI 为 Apache-2.0；实际安装版本必须写入 `web/package-lock.json` 并随最终 release commit 固定。实施时重新核对上游许可证和依赖清单，不将参考集源码复制进 OpsPilot。

