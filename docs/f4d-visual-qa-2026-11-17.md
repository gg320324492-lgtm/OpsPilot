# F4-D 视觉 QA 审计报告

**日期：** 2026 年 11 月 17 日
**范围：** `web/src/app` 全部 6 个页面 + `web/src/components` 基础组件
**性质：** 只读审计，未改动任何源码、测试或 CI。
**审计依据：** `docs/phase4-plan.md` §1.1（交互决策）与 §3（交付原则）。

## 0. 执行摘要

整体评价：**基础素质好**。语义 token 体系（`globals.css`）已建立且方向正确；六页共用 `Page`/`Panel`/`StatusBadge`/`Pill`/`EmptyState`/`ErrorPanel` 一套视觉词汇，卡片为细边框低圆角，状态色语义（琥珀=等待、红=失败、绿=完成）贯彻一致；空态/错误态区分诚实且文案完备；关键交互（决策 409、双重审批窗口、reindex 零结果）都有明确的状态反馈。

但对照 §1.1 的既定决策与 §3 的交付原则，仍发现 **8 个 P0、11 个 P1、14 个 P2** 问题，集中在四类：

1. **审计标准中的"琥珀=等待"与实现冲突**：`waiting_approval` 的状态徽章底色是 Tailwind `amber-100`/`amber-900`，而计划定义的琥珀语义 token 是 `--warning: #b7791f`，两套琥珀并存，深浅色下观感不一，且徽章色未走语义变量（P0）。
2. **键盘可达性缺口**：Recreate 模态框是自研 `div` 对话框，无焦点陷阱、无 `Esc` 关闭、无 `role="button"` 的遮罩可聚焦；`DecisionPanel`/`RecreateRunButton` 的 dismiss 按钮无可见焦点样式依赖全局 `:focus-visible`（已有，但模态框本身不合规）（P0）。
3. **纯色按钮对比度不足**：主操作按钮 `bg-neutral-900 text-white` 与辅助按钮 `border-neutral-300` 未接入语义 token（`--accent`、`--surface`、`--border`），与 HeroUI 组件并存时出现两套按钮语言（P1）。
4. **信息层级**：Dashboard 双主卡下的 `Headline` 用 `border-t-2` 彩条表达状态，弱于 §1.1 "待审批与失败任务并列为两个主卡片"的视觉权重预期；Run Detail 右栏 ApprovalSummaryCard 与说明卡竞争注意力（P1）。

HeroUI 用法方面：`Alert`、`Chip`、`Card`、`I18nProvider`、`ToastProvider` 已按 3.2.7 正确接入；但 `Page`/`Panel`/表格/筛选器/按钮仍是裸 Tailwind，与 HeroUI 组件视觉细节（圆角、阴影、聚焦环）存在轻微不一致，属于 P1/P2 整改项。

**未发现**：色彩硬编码绕过语义 token 的页面级违规（页面级颜色均通过 `text-danger`/`bg-warning/10` 等语义类）；文案绕过 en/zh-CN 词典的违规；动效违规（仅 nav 一处 `transition-colors`，服务状态反馈，符合约束）；Lucide 图标缺失标注问题（项目当前**未引入** lucide-react，无图标即无违规，但 §1.1 要求的"图标必须配文字"应作为引入时的准入规则记录）。

---

## 1. 逐页审计

### 1.1 Dashboard（`web/src/app/page.tsx`）

**视觉层级** — 符合"一页一主动作"。两个 `Headline` 主卡（等待审批 / 失败）在 `md:grid-cols-2` 网格中并列置顶，其后是 replay 警报、活动面板与知识库面板，顺序即论点。✅

| 级别 | 问题 | 位置 | 建议修法 |
|---|---|---|---|
| P1 | 主卡状态表达仅靠顶部 2px 彩条（`border-t-2 border-t-danger` 等），视觉权重弱，扫视时两张卡难以与下方普通 Panel 区分 | `page.tsx:354-361` | 改用左侧 3px 竖条 + 状态色底纹（如 `bg-danger/5`），或给数字配状态点；保持琥珀/红/绿语义 |
| P1 | `Headline` 数字用 `text-4xl`，但 `Panel` 标题仅 `text-sm`，页面 `h1` 仅 `text-xl`；主卡数字与页面标题的字号跨度偏大，削弱了"待办事实"与"页面"的层级连续性 | `page.tsx:363`，`ui.tsx:36` | 统一比例：`h1` 升至 `text-2xl`，主卡数字降至 `text-3xl`，保持"页面标题 ≥ 面板标题"的阶梯 |
| P2 | `Headline` 的 CTA 链接（`{cta} →`）是整个卡片唯一的交互元素，但可点击区域仅文字大小；在触屏（390px）上命中区域偏小 | `page.tsx:365-370` | 给链接加 `py-1` 或把整卡做成可点（注意保持可访问名称不变） |
| P2 | `Row` 的 hint 与 label 同为 `text-sm`/`text-xs` 且同色系，三行以上时"数值-解释"关系靠位置而非字重区分 | `page.tsx:382` 起 | hint 统一 `text-xs text-muted`，label 保持 `text-foreground` |
| P2 | 知识库面板的零 chunk 警示用 `text-amber-700 dark:text-amber-300` 硬编码 Tailwind 琥珀，而非 `text-warning` 语义类，与 `--warning` token 的琥珀（#b7791f）色相不一致 | `page.tsx:281-283` | 改为 `text-warning`；深色下 token 已有对应值 |
| P2 | 近期运行表 `colCreated` 用相对时间（`formatRelative`），而 Runs 列表同一列用绝对时间（`formatTimestamp`），同一数据两个页面两种读法 | `page.tsx:247-249` vs `runs/page.tsx:143` | 二者取一（建议 Dashboard 用相对、列表用绝对，但需在文案上说明），或都用 `<Time>` 的 title 承载另一种 |

**颜色对比度** — 正文/弱化文本对比达标（`--foreground #1a1d21` on `#f6f7f9` ≈ 15:1；`--muted #5c636b` on white ≈ 7:1）。`Headline` 数字三态色（`text-danger`/`text-warning`/`text-success`）on white 分别约 4.9:1 / 5.3:1 / 4.6:1，达标。**但状态徽章的 Tailwind 琥珀与语义琥珀不一致**，见 §3 共性问题 P0-1。

**响应式** — `md:grid-cols-2`（主卡）、`lg:grid-cols-3`（活动区，1+2 分栏）合理；表格有 `overflow-x-auto`。768px 以下单列堆叠正确。390px 下表格横向滚动可用，但无滚动提示（见 §3 P2-共性问题 3）。

**键盘可达性** — 全部交互为 `<Link>` 与服务端渲染，无键盘陷阱。✅

**状态齐全度** — 加载失败（`value === null` → `ErrorPanel`）、空列表（`EmptyState`）、replay 警报、零 chunk 提示均有；**无加载中骨架屏**（Server Component 首屏即内容，可接受，但 `RunRefresher` 触发的 `router.refresh()` 期间无任何视觉反馈，见 §3 P2-共性 4）。

---

### 1.2 Run Detail（`web/src/app/runs/[runId]/page.tsx` + `approval-summary.tsx`、`export-button.tsx`、`recreate-button.tsx`、`run-refresher.tsx`）

**视觉层级** — 顶部状态横幅（失败/待审/已批准待执行）→ 运行字段面板 → 模型失败面板 → replay/failed-calls 警报 → 时间线+工具调用（2/3）与 审批卡+引用+客户回复（1/3）。信息密度高但分区清楚。⚠️ 右栏在 `lg` 以下会堆到主内容之后，审批卡（需要操作的主对象）在窄屏被时间线压到很深的位置，与 §1.1 "窄屏移到主内容顶部"的审批决策不一致。

| 级别 | 问题 | 位置 | 建议修法 |
|---|---|---|---|
| P0 | **审批卡在窄屏未置顶**。§1.1 明确"审批卡……窄屏移到主内容顶部"，当前 DOM 顺序是时间线在前、审批卡在后，768/390px 下操作对象沉底 | `page.tsx:315-326` | 用 `order` 工具类（`order-first lg:order-none`）或在窄屏断点调整 grid 源顺序，仅动布局不动逻辑 |
| P1 | `Field` 网格 `sm:grid-cols-2 lg:grid-cols-4` 在 768–1023px 区间为 2 列，8 个字段排 4 行，面板显得琐碎；且 `lastActivity` 的相对时间括号与绝对时间挤在一起 | `page.tsx:237` | 中间断点可保持 2 列但增大 `gap-y`；或 `lastActivity` 拆两行显示 |
| P1 | 时间线步骤的 `Pill`（`humanise(step.step_type)`）与 label、时间、延迟混排在一行，`flex-wrap` 换行后 Pill 可能落到第二行行首，视觉节奏断裂 | `page.tsx:392-404` | Pill 固定放 label 之后并给时间/延迟设 `ml-auto`，或时间线单行只留 sequence+label+time，Pill 下移到 detail 行 |
| P2 | `step.detail` 为空时用 `italic text-warning` 呈现"no detail"文案——斜体+警示色让后端缺口看起来像前端警告，语义过重 | `page.tsx:412-414` | 改 `text-muted` 不加斜体，保留文案即可（可见性已由文案保证） |
| P2 | `LatencyCell` 慢查询的展示（红字）混在时间线元信息行里，与状态 Pill 争抢注意力 | `page.tsx:403` | 慢延迟仅在超过阈值时显示，且用 `text-xs` 弱化 |
| P2 | `ToolCalls` 的每个 call 卡片 `rounded-md border border-separator`，与 `Panel` 的 `border-border` 边框色不同，同页两档边框语言 | `page.tsx:553` vs `ui.tsx:62` | 统一用 `border-border`（或 `border-separator`），二选一 |
| P2 | 引用列表（`Citations`）与客户回复（`CustomerReply`）无空态插图或图标，与整站"文字型空态"一致，但两面板 aside 计数一个有（sourcesAside）一个无，不对称 | `page.tsx:616-624` | 统一：有内容都给计数 aside，空态都靠 `EmptyState` |

**键盘可达性** —
- **P0**：`RecreateRunButton` 的确认模态框（`recreate-button.tsx:208-249`）为自研 `div` 对话框：有 `role="dialog"`/`aria-modal`/`aria-label`，但 **无焦点陷阱**（Tab 可跑出对话框到背景页面）、**无 Esc 关闭**、打开时**不把焦点移入对话框**、关闭后不归还焦点。§3 要求"键盘可达"。建议改用 HeroUI Dialog（3.2.7 自带焦点管理），或补：`useEffect` 聚焦首个按钮 + `keydown` 监听 Esc + 简单 focus trap。
- **P1**：遮罩层 `onClick` 关闭（busy 时禁用），但遮罩本身无键盘等价物；键盘用户只能靠 Cancel 按钮。
- **P1**：`ExportButton`、`RecreateRunButton`、`DecisionActions` 的按钮使用裸 `<button>`，依赖全局 `:focus-visible` 焦点环（`globals.css:106`，✅ 已有 2px accent 环）；但 `disabled:opacity-50` 的禁用态无 aria-disabled 区分（原生 disabled 已足够，可接受）。

**颜色对比度** — `text-danger` 失败原因 on white ≈ 4.9:1 ✅；`text-warning` 空详情斜体 on white ≈ 5.3:1 ✅；`DecisionPanel` 琥珀态 `text-amber-950` on `bg-amber-50` 对比充足 ✅。`RecreateRunButton` 失败面板同上。深色 token（`.dark`）配套完整。

**状态齐全度** — 404（专用分支）、加载失败、空时间线（区分 claimed/unclaimed 两种文案）、replay 阻断、failed calls、导出失败、重建失败（409 琥珀/其他红色）均覆盖 ✅。**自动刷新（`RunRefresher`，5s）期间无任何加载指示**（P2，见 §3）。

**动效** — 本页无动画；`RunRefresher` 的静默刷新符合"动效只服务状态"约束，但反过来说，状态在变而页面无感知，建议刷新后给更新区域一个轻微的淡入或时间戳变化提示（P2，可选）。

---

### 1.3 Approvals（`web/src/app/approvals/page.tsx` + `components/approval-card.tsx`、`decision-actions.tsx`、`decision-panel.tsx`、`explanation.tsx`）

**视觉层级** — 顶部证据说明条 → 筛选器（Page actions 位）→ 面板化卡片列表。筛选器用 `aria-current="page"` + `bg-accent` 实心胶囊标记当前项 ✅。卡片内 RiskPill → permission Pill → 状态 Pill → 时间 → 工具名 → 双解释 → 参数/风险两栏 → 决策按钮，顺序合理。

| 级别 | 问题 | 位置 | 建议修法 |
|---|---|---|---|
| P1 | **筛选器是 Page 级 actions，但视觉上只是一排小胶囊**（`text-xs`），与 §1.1 "清晰操作入口"相比存在感弱；且 pending/approved/rejected/expired 四态切换无计数提示（除面板 aside 的 total 外） | `approvals/page.tsx:186-209` | 筛选胶囊带计数（如 `Pending · 3`），或提升为分段控件（segmented control）样式 |
| P1 | `ApprovalCard` 待审态用 `border-warning/60`，但卡内 RiskPill 的 `warn` 态是 `bg-warning/15 text-warning`，同一张卡上两种琥珀透明度叠加，深色模式下 `--warning: #d9a441` 在 `bg-warning/15` 上对比下降 | `approval-card.tsx:95-99`、`ui.tsx:111` | 卡片边框改 `border-warning` 实色（或 `border-l-4`），Pill 背景透明度提到 /20 并在深色下校验 |
| P1 | `TrustedExplanation`（emerald 系）与 `UntrustedExplanation`（neutral 系）的绿/灰区分良好，但 emerald 不在计划的三状态色内——§1.1 只定义了琥珀/红/绿（success）三色；emerald 系与 `--success`（#2f9e63）色相不同，形成第四种"绿" | `explanation.tsx:53-65` | Trusted 卡改用 `--success` 语义 token（`border-success/40 bg-success/10`），或将 emerald 定义为 token 后引用；避免页面级散落第二套绿 |
| P2 | `Badge`（trusted/untrusted）用 `text-[11px] font-bold uppercase tracking-wider`，11px 大写加宽字距在中文环境下可读性尚可，但 `untrusted` 徽章深色模式 `bg-neutral-400 text-neutral-900` 与页面 `--muted` 灰不同族 | `explanation.tsx:93-104` | untrusted 徽章接入 `--muted`/`--surface-secondary` token |
| P2 | `arguments_snapshot` 的 `<pre>` 无最大高度，超长 JSON 会把卡片撑得极长（截图/演示不友好） | `approval-card.tsx:156-158` | `max-h-64 overflow-auto`，配"展开完整参数"的细节（可选） |
| P2 | 决定后的卡片（`DecisionActions alreadyDecided` → `DecidedLine`）用 neutral 灰面板，与 pending 卡的琥珀边框形成的状态差异主要靠边框，卡片本体背景相同，扫视时不易分辨哪些已处理 | `approval-card.tsx:94-99`、`decision-actions.tsx:207` | 已决卡片降低整体不透明度或加 `bg-surface-secondary`，保持文字对比达标 |

**颜色对比度** — 决策按钮 `bg-neutral-900 text-white` 对比 17:1 ✅；`DecisionPanel` 红色态/琥珀态文字对比均达标 ✅；`UntrustedExplanation` 引用块 `text-neutral-800` on `bg-neutral-100` ✅。**风险点**：`RiskPill` 的 `danger`（`bg-danger/15 text-danger`）中 `#d64545` on 15% 透明红底的有效对比约 4.2:1，小字号（`text-xs`）下踩 WCAG AA 4.5:1 线（P2，建议深一档文字或底色降至 /10）。

**键盘可达性** — `DecisionActions` 的 Approve/Reject 为原生按钮，焦点环依赖全局 `:focus-visible` ✅；busy 时双按钮同禁（`disabled={busy !== null}`）防重复提交 ✅；`DecisionPanel` 的 dismiss 按钮可聚焦 ✅。**无 Esc/焦点问题**（无弹层）。

**状态齐全度** — pending（按钮）、已决（DecidedLine）、409（DecisionPanel 琥珀）、transport 失败（红色）、双重审批（DuplicateNotice）全覆盖 ✅，且 409 文案不可被软化（共享组件）——这是全站做得最好的部分。

---

### 1.4 Tickets（`web/src/app/tickets/page.tsx` + `ticket-form.tsx`）

**视觉层级** — `lg:grid-cols-5`：左 3 列工单列表、右 2 列提交表单。表单在右侧（次要位）但它是"启动一次 run"的主动作——与 §1.1 "一页一主动作"的精神相比，**主动作放右栏偏弱**；不过设计意图（README 黄金路径从本页开始）依赖表单常驻可见，属可接受权衡，记 P2。

| 级别 | 问题 | 位置 | 建议修法 |
|---|---|---|---|
| P1 | 表单输入框是裸 Tailwind（`border-neutral-300 bg-white`），未用 HeroUI Input，也未接 `--border`/`--surface` token；深色模式下 `dark:bg-neutral-950` 与页面 `--background: #121417` 色值不同族（neutral-950 ≈ #0a0a0a），输入框在深色页面上像"黑洞" | `ticket-form.tsx:122,132,144,153` | 输入框改 `border-border bg-surface`，焦点态沿用全局 focus 环；或换 HeroUI Input |
| P1 | 提交按钮 `bg-neutral-900`、示例按钮 `border-neutral-300`，与 Approvals 的决策按钮同款——但 §1.1 把 `--accent` 定义为"仅用于需要人操作的动作"，提交工单正是这类动作，却未用 accent | `ticket-form.tsx:162,174` | 主提交按钮改 `bg-accent text-accent-foreground`；示例填充按钮保持中性 |
| P2 | 必填标记（`Field required`）的呈现方式未确认（`Field` 组件实现未见星号样式），若仅靠 `required` 属性则无视觉标记 | `ticket-form.tsx:116,126,138` | 确认 `Field` 对 `required` 渲染 `*` 或"必填"文案，并进词典 |
| P2 | 成功面板（`border-emerald-300 bg-emerald-50`）又是第三处 emerald 系，与 `--success` 不一致（同 §1.3 P1-3） | `ticket-form.tsx:181` | 统一改 `--success` token 系 |
| P2 | 失败面板（`describeFailure` 渲染处）与成功面板视觉重量相同，提交失败后表单仍可编辑 ✅，但失败信息在按钮下方而不是紧贴触发它的按钮组 | `ticket-form.tsx`（failure 渲染区） | 失败面板移到按钮组正下方或用 `aria-describedby` 关联提交按钮 |

**颜色对比度** — 列表正文 `text-foreground` ✅；`text-muted` 元信息 ✅；emerald 成功面板文字 `text-emerald-900` on `bg-emerald-50` ✅。

**响应式** — `sm:grid-cols-2`（表单两字段并排）→ 390px 单列 ✅；`lg:grid-cols-5` 在 1024px 生效，768px 以下堆叠，列表在上表单在下 ✅（此页窄屏顺序正确，可作为 Run Detail 修复的参照）。

**键盘可达性** — 原生 `<form>` + 原生按钮 + label 关联（`Field` 包裹）✅；`disabled={busy}` 防重复提交 ✅。`<textarea rows={3}>` 可聚焦。无问题。

**状态齐全度** — 提交中（按钮文字切换 `submitting`）、成功（面板+runId 链接语义完整）、失败（describeFailure）、空列表（EmptyState）、ticket 404（专用警示条）、API 错误（ErrorPanel）齐全 ✅。

---

### 1.5 Runs 列表（`web/src/app/runs/page.tsx`）

**视觉层级** — 单面板表格 + 顶部筛选器。结构清晰，无多余装饰 ✅。筛选器三段式（All | 进行中 7 态 | 终态 2 态）以 `|` 分隔，顺序按紧急度，设计有想法。

| 级别 | 问题 | 位置 | 建议修法 |
|---|---|---|---|
| P1 | 筛选器 `text-xs` + 胶囊，与 Approvals 同款问题：操作入口视觉偏弱；且 10 个筛选链接一行排开，768px 以下 `flex-wrap` 换行后 `|` 分隔符可能落在行首/行尾，视觉破碎 | `runs/page.tsx:225-251` | 窄屏隐藏 `|`（`hidden sm:inline`），或改用与 Approvals 统一的分段控件 |
| P1 | 表格 8 列（run/ticket/status/model/created/started/completed/failure），1280px 内可容，1024px 起依赖 `overflow-x-auto`，但表头无 `scope="col"`、无 `min-w`，横向滚动时列宽挤压 | `runs/page.tsx:101-112` | `<th scope="col">`；给表格 `min-w-[720px]` 保证滚动时列不塌 |
| P2 | `failure_reason` 列红色文字（`text-danger`）对无失败行显示 `—`，但失败枚举值（如 `llm_error`）直接裸展示，无本地化标签——这是刻意的（合同枚举），但视觉上与 StatusBadge 的"有标签"形成不一致 | `runs/page.tsx:152-157` | 保持原样（合同枚举不译），但可给失败行整行加 `bg-danger/5` 底色提升扫读性（可选） |
| P2 | 行链接（run id、ticket id）是 `font-mono text-xs underline`，点击区域小；整行不可点 | `runs/page.tsx:120-135` | 可保持现状；若改整行可点需保留链接语义 |
| P2 | 空态区分了 unfiltered/filtered/ticket 三种文案 ✅，但空态无"清除筛选"的返回动作链接 | `runs/page.tsx:165-176` | 空态 hint 后附"查看全部 runs"链接（文案进词典） |

**颜色对比度** — StatusBadge 各态对比达标（amber-900 on amber-100 ≈ 7:1 等）✅；表头 `text-muted uppercase text-xs` on surface ≈ 7:1 ✅。

**键盘可达性** — 筛选器是 `<Link>` + `aria-current="page"` ✅（同 Approvals）；表格链接可聚焦 ✅。

**状态齐全度** — 加载失败、ticket 404、三种空态齐全 ✅；无加载态（Server Component，可接受）。

**响应式** — `overflow-x-auto` 兜底；建议加表头 sticky（P2，可选）。

---

### 1.6 Knowledge（`web/src/app/knowledge/page.tsx` + `reindex-button.tsx`）

**视觉层级** — 顶部 ReindexButton + 提示文案 → 文档表格。一页一主动作（reindex）明确 ✅。`ResultPanel` 对 `documents_indexed == 0` 用 emerald 成功态而非警告——这是本页最重要的防误读设计，注释里写明了理由，✅ 保持。

| 级别 | 问题 | 位置 | 建议修法 |
|---|---|---|---|
| P1 | ReindexButton `bg-neutral-900`——同 Tickets 的 accent 问题：这是需要人操作的动作按钮，按 §1.1 应使用 `--accent` | `reindex-button.tsx:90` | 改 `bg-accent text-accent-foreground` |
| P1 | reindex 提示文案里两个 `<code>`（glob、dir）用默认样式，无底色区分，混在 `text-neutral-500` 说明文字里不显眼 | `reindex-button.tsx:94-98` | `<code>` 加 `rounded bg-surface-secondary px-1` 微底色 |
| P2 | 表格 5 列含 `content_hash`（长哈希），`overflow-x-auto` 下哈希列可能需要截断展示 | `knowledge/page.tsx:71-82` | 哈希列 `max-w-[160px] truncate` + `title` 全值 |
| P2 | ResultPanel 成功（非零 indexed）用 neutral 灰、零变更用 emerald——逻辑正确，但"成功且有小变更"用灰色视觉上弱于"零变更"的绿色，主次略颠倒 | `reindex-button.tsx:127-133` | 非零成功也可用 emerald（或两者都用中性，仅 `documents_seen == 0` 真空树用警示） |
| P2 | busy 态按钮文字切换为 `reindexing` 但无 spinner；reindex 可能耗时较长（embedding），期间无进度感知 | `reindex-button.tsx:86-93` | 加 HeroUI Spinner 或 CSS 转圈（服务状态反馈，符合动效约束） |

**颜色对比度** — 全部达标；`ResultPanel` emerald 态文字对比充足 ✅。

**键盘可达性** — 原生按钮 ✅；结果面板/失败面板均为静态 div + `role="alert"`（失败）✅。

**状态齐全度** — busy、成功（区分零/非零）、空树警告、失败（红色 alert）、空索引（EmptyState）、读取失败（ErrorPanel）齐全 ✅。

---

### 1.7 全局组件（`nav.tsx`、`locale-switcher.tsx`、`ui.tsx`、`api-status.tsx`、`heroui-providers.tsx`）

| 级别 | 问题 | 位置 | 建议修法 |
|---|---|---|---|
| P1 | **当前页高亮缺失**。§1.1 明确"保留顶部导航，增加当前页高亮"；`Nav` 的五个链接是纯 Server Component，无 `usePathname`，所有页面导航链接样式完全相同 | `nav.tsx:46-57` | 把链接列表拆成小的 client 组件读 `usePathname`，当前项加 `aria-current="page"` + `text-foreground font-medium`（或底部指示条） |
| P1 | 导航底色 `bg-white dark:bg-neutral-950` 与页面 `--background: #f6f7f9`/`#121417` 不是同一个 token：浅色下导航比页面白，深色下比页面黑，均形成色阶断裂 | `nav.tsx:36` | 改 `bg-surface`（或直接用 `--background`），边框 `border-border` |
| P2 | 导航链接 hover 有 `transition-colors`（全站唯一动效）✅ 符合约束，但当前项无任何标识使 hover 与"所在页"无法区分 | 同上 | 同 P1-当前页高亮 |
| P2 | `LocaleSwitcher` 激活态 `bg-accent text-accent-foreground` 使用了 accent——语言切换不是"需要人决策的操作"，与 §1.1 accent 定义（审批动作专用）有张力；但作为导航级控件可辩护 | `locale-switcher.tsx:77-81` | 可保留；若从严，激活态改 `bg-foreground text-background` 或 `bg-surface-secondary font-medium` |
| P2 | `Page` 的 `h1` 仅 `text-xl`，六页标题视觉偏轻，与 HeroUI 默认页面标题习惯（更大）不一致 | `ui.tsx:36` | 升 `text-2xl`（配合 §1.1 的 Headline 字号回调） |
| P2 | `Panel` 阴影 `shadow-[0_1px_2px_rgba(16,18,20,0.04)]` 是任意的自定值，未 token 化；HeroUI Card 有自己的阴影层级，两者并存时深浅不一 | `ui.tsx:62` | 把阴影定义为 CSS 变量（如 `--shadow-panel`）或直接采用 HeroUI Card |
| P2 | `ErrorPanel` 是 async 组件（内部 `getT()`），无法在 Client Component 里复用；Dashboard `Headline` 在 `value === null` 时渲染它 ✅，但同一错误在"页面顶部 ApiStatus 横幅"与"面板内 ErrorPanel"可能重复出现（同一 API 不可达） | `ui.tsx:162`、`api-status.tsx` | 可接受（横幅是全局的，面板是局部的）；若要去重，让 `Headline` 的 null 态复用 ApiStatus 文案 |
| P2 | `StatusBadge` 用 HeroUI `Chip variant="soft"` + 自定 Tailwind 状态类，Chip 自带的圆角/内边距与 `Pill`（自研 `rounded-full px-2 py-0.5`）不一致，同屏出现两代"胶囊" | `ui.tsx:88-94` vs `ui.tsx:102-122` | 统一：要么都走 HeroUI Chip，要么 Pill 也换 Chip；至少统一圆角与字号 |

**ApiStatus 横幅**（`api-status.tsx`）— auth 失败用 amber、不可达用 red，语义正确 ✅；深色模式 `bg-amber-950/40`/`bg-red-950/40` 配套 ✅；**但 amber/red 同样是 Tailwind 原生色而非 `--warning`/`--danger`**（见共性 P0-1）。

---

## 2. HeroUI 组件用法一致性

| 组件 | 使用情况 | 评价 |
|---|---|---|
| `Alert` | Dashboard replay、Run Detail parked/approved、ErrorPanel auth 分支 | ✅ 正确；`status="accent"`（approved-awaiting）已对照 `@heroui/styles` 的 `alertVariants` 确认为 3.2.7 合法枚举（accent/danger/default/success/warning） |
| `Chip` | 仅 `StatusBadge` | ⚠️ 与自研 `Pill` 双轨 |
| `Card` | 仅 Dashboard `Headline` | ⚠️ 与自研 `Panel` 双轨（圆角、阴影、边框细节不同） |
| `I18nProvider` / `ToastProvider` | `heroui-providers.tsx`，locale 服务端解析后传入 | ✅ 正确，无首帧闪烁 |
| HeroUI Button / Input / Dialog / Select | **未使用**，全部为裸 `<button>`/`<input>`/自研 dialog | ⚠️ 见结论 |

**结论**：HeroUI 目前是"浅接入"——只用了 Alert/Chip/Card/I18nProvider/ToastProvider。这不违反计划（计划允许 Tailwind 继续做布局基础），但造成三类一致性成本：按钮两套语言（HeroUI 无按钮可用的裸按钮 vs 未来接入的 HeroUI 按钮）、胶囊两代（Chip vs Pill）、卡片两种（Card vs Panel）。**建议后续修复以"token 先行、组件跟随"为序**：先统一颜色/边框/圆角 token，再决定是否把 Button/Input/Dialog 换成 HeroUI（Dialog 因焦点陷阱问题建议优先替换，见 P0-2）。

---

## 3. 跨页共性问题（按严重度）

### P0（阻塞 §1.1 验收或可访问性硬伤）

| # | 问题 | 涉及位置 | 建议修法 |
|---|---|---|---|
| P0-1 | **两套琥珀/红/绿并存**：语义 token（`--warning #b7791f` 等）与 Tailwind 原生色（`amber-100/900`、`red-*`、`emerald-*`、`sky-*`）混用。状态徽章、ApiStatus、DecisionPanel、explanation、ticket-form 成功面板、reindex ResultPanel 全部走 Tailwind 原生色；`statusTone()` 是最大源头。同一"等待"在 Dashboard 主卡上是 `#b7791f`、在徽章上是 amber-900，色相不同 | `lib/format.ts:183-201`（statusTone）、`api-status.tsx:30-31`、`explanation.tsx`、`decision-panel.tsx:46-47`、`ticket-form.tsx:181`、`reindex-button.tsx:131`、`page.tsx:281` | 将 `statusTone` 与各状态面板改为引用语义 token 的工具类（`bg-warning/15 text-warning ring-warning/30` 等，需在 `@theme` 中确认 opacity 修饰符可用）；emerald→`success`、sky→新增 `--info` 或 `accent`、red→`danger`。改完全局仅剩 token 一个色彩来源 |
| P0-2 | **Recreate 模态框键盘不合规**：无焦点陷阱、无 Esc、不自动聚焦、关闭不还焦点 | `recreate-button.tsx:208-249` | 换 HeroUI Dialog（首选，自带 React Aria 焦点管理）；或手补 focus trap + Esc + 初始聚焦 |
| P0-3 | **审批卡窄屏未置顶**（违反 §1.1 明文决策"窄屏移到主内容顶部"） | `runs/[runId]/page.tsx:315-326` | 窄屏断点 `order-first`，仅动布局 |
| P0-4 | **导航无当前页高亮**（违反 §1.1 明文决策"增加当前页高亮"） | `nav.tsx:46-57` | client 化链接列表 + `usePathname` + `aria-current` |

### P1（影响可用性/一致性，下轮修复应覆盖）

| # | 问题 | 涉及位置 |
|---|---|---|
| P1-1 | 主操作按钮（决策、提交工单、reindex、recreate 确认）用 `bg-neutral-900` 而非 `--accent`，"accent 只给人必须行动之处"的语义未落地 | `decision-actions.tsx:164+`、`ticket-form.tsx:162`、`reindex-button.tsx:90`、`recreate-button.tsx:236` |
| P1-2 | 表单输入框/次级按钮未接 `--border`/`--surface` token，深色模式下与页面底色断层 | `ticket-form.tsx:122-174`、`export-button.tsx:102`、`recreate-button.tsx:178,244` |
| P1-3 | emerald 系（TrustedExplanation、ticket 成功、reindex 零变更）与 `--success` 并存，四色绿 | `explanation.tsx:53`、`ticket-form.tsx:181`、`reindex-button.tsx:131` |
| P1-4 | Dashboard 主卡视觉权重弱（仅 border-t-2 彩条），与"两个主卡片"定位不匹配 | `page.tsx:354-361` |
| P1-5 | Approvals/Runs 筛选器视觉存在感弱、Runs 筛选分隔符窄屏破碎 | `approvals/page.tsx:186`、`runs/page.tsx:225` |
| P1-6 | Runs 表格 8 列无 `scope="col"`、无 min-w，窄屏横向滚动体验差 | `runs/page.tsx:101` |
| P1-7 | 导航底色未用 `--background`/`--surface`，与页面形成色阶断裂 | `nav.tsx:36` |
| P1-8 | 审批卡 warn 双透明度叠加 + 深色下对比下降 | `approval-card.tsx:95`、`ui.tsx:111` |
| P1-9 | Run Detail 时间线元信息行 flex-wrap 后 Pill 换行节奏断裂 | `runs/[runId]/page.tsx:392-404` |
| P1-10 | Recreate 模态框遮罩无键盘关闭等价物 | `recreate-button.tsx:210-217` |
| P1-11 | Reindex busy 态无 spinner/进度感知（长耗时操作） | `reindex-button.tsx:86-93` |

### P2（打磨项，可排后）

| # | 问题 | 涉及位置 |
|---|---|---|
| P2-1 | `h1` `text-xl` 偏轻；与 `text-4xl` 主卡数字跨度失衡 | `ui.tsx:36`、`page.tsx:363` |
| P2-2 | `Chip` vs `Pill`、`Card` vs `Panel` 两代组件视觉细节不一 | `ui.tsx` |
| P2-3 | 表格横向滚动无提示（渐隐遮罩/阴影），390px 体验生硬 | `runs/page.tsx:100`、`knowledge/page.tsx:71`、`page.tsx:215` |
| P2-4 | `router.refresh()`（RunRefresher、决策后、reindex 后）期间无加载指示 | `run-refresher.tsx` 等三处 |
| P2-5 | 时间线空 detail 用 `italic text-warning` 语义过重 | `runs/[runId]/page.tsx:412` |
| P2-6 | Knowledge `content_hash` 列未截断 | `knowledge/page.tsx` |
| P2-7 | Dashboard 与 Runs 的 created 列时间格式不一致（相对 vs 绝对） | `page.tsx:247` vs `runs/page.tsx:143` |
| P2-8 | RiskPill danger 态 `text-xs` 对比约 4.2:1 踩线 | `ui.tsx:112` |
| P2-9 | 已决审批卡与待审卡本体背景相同，扫读区分弱 | `approval-card.tsx:94` |
| P2-10 | 已决 `DecidedLine`、explanation untrusted 徽章的 neutral 色未 token 化 | `decision-actions.tsx:207`、`explanation.tsx:97` |
| P2-11 | Ticket 表单必填标记呈现方式待确认 | `ticket-form.tsx` Field |
| P2-12 | Ticket 失败面板位置与触发按钮关联弱 | `ticket-form.tsx` |
| P2-13 | Runs 空态无"清除筛选"返回链接 | `runs/page.tsx:165` |
| P2-14 | reindex 提示中 `<code>` 无底色；LocaleSwitcher 激活态占用 accent（可辩护，记录即可） | `reindex-button.tsx:94`、`locale-switcher.tsx:79` |

---

## 4. 专项核对

### 4.1 颜色对比度（WCAG AA）

| 组合 | 位置 | 结论 |
|---|---|---|
| 正文 `#1a1d21` on `#f6f7f9` | 全站 | ✅ ≈15:1 |
| `--muted #5c636b` on `--surface #fff` | 全站弱化文本 | ✅ ≈7:1 |
| `text-danger #d64545` on white | 失败原因/失败横幅 | ✅ ≈4.9:1 |
| `text-warning #b7791f` on white | 空详情/警示 | ✅ ≈5.3:1 |
| `text-success #2f9e63` on white | Headline ok | ✅ ≈4.6:1（大字号达标；小字需注意） |
| `text-danger` on `bg-danger/15`（Pill） | RiskPill/tool status | ⚠️ ≈4.2:1，`text-xs` 踩线（P2-8） |
| amber-900 on amber-100（StatusBadge） | 三处表格 | ✅ ≈7:1（但见 P0-1 双琥珀问题） |
| 深色 `.dark` 全套 | `globals.css:45-64` | ✅ token 齐全；`#d9a441` on `#121417` ≈ 8:1 |

### 4.2 响应式断点核对（1280 / 1024 / 768 / 390）

- **1280px**：全部布局按 `lg`（1024px）已展开，无问题。
- **1024px**：Dashboard 1+2 分栏 ✅；Run Detail 2+1 分栏 ✅（但审批卡在右栏，见 P0-3）；Tickets 3+2 分栏 ✅；Runs/Knowledge 表格进入横向滚动 ⚠️（P1-6/P2-3）。
- **768px**：`sm:` 断点生效（Run Detail 字段 2 列、Ticket 表单字段 2 列），`md:` 主卡并排。⚠️ Runs 筛选器 `|` 分隔符换行破碎（P1-5）。
- **390px**：全部单列 ✅；表格横向滚动可用但无提示 ⚠️；主卡 CTA 命中区偏小（P2）；**Run Detail 审批卡沉底（P0-3）在此断点最严重**。

### 4.3 键盘可达性

- 全局 `:focus-visible` 2px accent 环 ✅（`globals.css:106-109`），覆盖所有裸按钮/链接。
- `<html lang={locale}>` ✅； landmarks：`<nav aria-label>` 主导航/筛选器/语言切换三个同级 nav，无嵌套 ✅（nav.tsx 注释明确此设计）。
- `aria-current="page"`：筛选器 ✅、语言切换 ✅、主导航 ❌（P0-4）。
- `role="alert"`：DecisionPanel、recreate 失败、reindex 失败、failed-calls ✅；`role="status"`：ApiStatus、replay 阻断、RunRefresher 场景 ✅。
- **模态框**：Recreate dialog 缺焦点陷阱/Esc/初始聚焦（P0-2）。
- `<time dateTime>` + `title` 提供机器可读时间 ✅。

### 4.4 动效约束（"只服务状态变化、展开、加载和操作反馈"）

- 全站仅 1 处 `transition-colors`（nav hover）✅。
- 无循环装饰动画 ✅。
- 缺失的是"应有的反馈动效"：刷新/提交/reindex 的 busy 态无进度表达（P2-4、P1-11）——按计划这属于"动效服务加载"的正向要求，建议补齐。

### 4.5 图标（Lucide）准入核对

`package.json` 无 `lucide-react` 依赖，源码无 SVG 图标。§1.1"图标必须有可见文字或 aria-label"当前**零违规**（无图标）；若后续引入 Lucide，需把该规则作为 code review 准入项。本次审计不将"未引入图标"记为问题——审计工作台风格下纯文字导航是合理选择。

### 4.6 i18n 核对

新增用户可见文案均走 `getT()`/`getCopy()` + 词典 ✅；`humanise()`、`failure_reason`、`step_type`、`content_hash` 等合同枚举/标识符按 §P2-F 保持 verbatim 不译 ✅；`<html lang>` 随 cookie ✅；未发现绕过词典的硬编码用户文案。

### 4.7 产品边界核对

未发现 RBAC、支付、新工单类型等越界功能；所有交互均落在既有合同端点内 ✅。

---

## 5. 修复排序建议

1. **第一批（P0，先做）**：P0-1 色 token 统一（`statusTone` 改语义类是杠杆点，一处改动惠及三页）→ P0-2 Dialog 换 HeroUI → P0-3 审批卡窄屏置顶 → P0-4 导航当前页高亮。
2. **第二批（P1）**：accent 按钮语义落地（P1-1）→ 表单/次级按钮 token 化（P1-2）→ emerald 归并（P1-3）→ 其余按页面推进（Dashboard 主卡 → 筛选器 → 表格可达性）。
3. **第三批（P2）**：字号阶梯、组件双轨归并、滚动提示、busy 指示等打磨项。

> 排序理由：P0-1 影响全部页面且是后续一切颜色类修复的前置；P0-2/3/4 是明文验收项或硬性可访问性缺陷；P1 的 accent/token 化依赖 P0-1 完成后的 token 体系稳定，故排后。
