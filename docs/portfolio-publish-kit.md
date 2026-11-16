# 作品集发布操作手册（publish kit）

> 本手册是**操作指引，不是发布动作本身**：自由职业平台的账号操作、粘贴与提交一律由用户本人完成。
> 所有英文文案均取自仓库内已定稿材料，本手册只做"用哪一段、贴到哪、怎么自查"的编排。
>
> - 文案来源：[portfolio-entry-en.md](portfolio-entry-en.md)（Phase 3 M2-09 定稿）
> - 服务目录来源：[service-offerings-en.md](service-offerings-en.md)（三类服务）
> - 上游依据：[release-checklist.md](release-checklist.md) 第 6 节

---

## 0. 发布前 60 秒总览

| # | 要贴什么 | 贴到哪 | 用哪个素材 |
|---|---|---|---|
| 1 | 作品条目（Portfolio item） | 平台的作品集入口（Profile → Portfolio → Add project） | title 66 字符版 + description **595 字符合规版** + 封面 `cover-1000x750.png` + 5 个技能标签 |
| 2 | 专项档案（specialized profile） 简介 | 专项档案（specialized profile） 的 headline/description | role 99 字符版 + 摘自 595 字符版的开头两句 |
| 3 | 服务目录（项目目录（project catalog））3 条 | 项目目录（project catalog） 新建 3 个条目 | service-offerings-en.md 三类服务，按下文改写要点 |

---

## 1. 作品条目（Portfolio item）逐步指引

### 第 1 步：准备封面图

使用 **`web/public/showcase/cover-1000x750.png`**（仓库内现成文件，1000×750，
符合作品封面建议比例 4:3）。不要用展示截图 01–13 直接当封面，也不要拉伸
其他尺寸的图。

### 第 2 步：填 Title

粘贴 `portfolio-entry-en.md` 的 Title 段原文（66 字符，≤70 上限）：

```text
AI Ops Agent: FastAPI + MCP + Human-Approval Gate (Python/Next.js)
```

粘贴后核对一次长度：**66/70**。

### 第 3 步：填 Role / headline（specialized profile 用）

同一文档的 Role 段原文（99 字符，≤100 上限）：

```text
I build LLM agents that propose actions but never move money or data without a human approval gate.
```

粘贴后核对一次长度：**99/100**。

### 第 4 步：填 Description —— 必须用 595 字符合规版

**原稿 748 字符，超出 600 字符上限，不能直接粘贴。** 文档里给了三个变体，
其中两个超限（748、646），只有"Recommended compliant variant with the real-model
line kept"是合规的（**595 字符**），且保留了真实模型 safety 一句。粘贴下面这一段：

```text
I build AI ops agents: the model does the legwork, a human keeps the final say. My OpsPilot (FastAPI, Next.js, pgvector, MCP) handles a duplicate-charge ticket end to end: policy retrieval, three MCP servers, five gates and a human approval before any money moves. The refund executes exactly once under an idempotency key, and CI enforces zero unapproved high-risk writes across 1,365 tests. Measured on a real model, the safety gate held: 0 unsafe executions, injections included. Systems are mocks. I take on such prototypes, cited RAG assistants, and MCP integrations with approval backends.
```

粘贴后核对一次长度：**595/600**。若平台计数与本文档标注不一致（例如把换行计入），
以平台计数为准，并回到原稿删句，不要静默超限。

### 第 5 步：填技能标签（正好 5 个）

按文档建议的安全集（不夸大）：

1. `Python`
2. `FastAPI`
3. `Next.js`
4. `RAG`
5. `LLM Integration`

> 文档另列了 `LangChain` 与 `LLM Prompt Engineering` 作为候选；只有实际用过
> LangChain 才可替换第 4 项，默认按上面的安全集填。

### 第 6 步：可选补充项

- **项目链接**：填 GitHub 仓库公开地址（README 所在仓库），不要填含个人邮箱/
  电话的页面。
- **演示视频**：如需在作品条目里放视频，使用已上传平台的公开链接
  （见 release-checklist 第 5 节）。视频是 **fake model 管线演示**，标题/简介
  必须保留 "scripted fake model provider — no external AI calls" 披露，
  **不得写成 live / real AI run**。
- **更多截图**：可从 `web/public/showcase/01-dashboard.png` 等选取 1–2 张作为
  附加图，选择无联系方式、无内部路径的截图。

---

## 2. 专项档案（specialized profile） / 项目目录文案

### 2.1 专项档案（specialized profile） 简介

- headline：直接用第 3 步的 role 句（99 字符）。
- 简介正文：用 595 字符版 description 的前两句作为开场，再接一句服务范围。
  可直接使用 595 字符版全文，避免再造一句需要重新核字符数的文案。

### 2.2 项目目录（项目目录（project catalog））三条条目要点

由 [service-offerings-en.md](service-offerings-en.md) 三类服务改写。每条目录
条目建议结构：**一句话定位 → 客户需提供 → 交付物 → 验收方式 → 明确排除项**。
要点如下（英文短语可直接用于目录条目，来源均为原文）：

#### 条目 1 — AI customer-service / operations workflow prototype

- **定位句**：A working prototype of an LLM agent that handles a repetitive
  support or operations workflow — ticket triage, policy lookup, draft
  decisions, structured escalation — with deterministic gates between the
  model and any consequential action.
- **客户需提供**：2–5 representative real workflow cases（anonymised is fine，
  含 happy path 与至少一个 exception case）；policy/process documents；
  systems/API descriptions（sandbox credentials 仅在需要真实只读集成时）；
  the decision rule to enforce（如 "nothing irreversible without a human
  sign-off"）。
- **交付物**：running prototype（API service + minimal web UI，state-machine
  agent with step-by-step trace）；gating layer（schema validation、tool
  allow-list、policy/risk checks、human-approval step）；seed data、scripted
  demo path、written handover doc。
- **验收**：sample cases end to end；every sensitive action blocked until an
  approval is recorded（automated test 可复跑）；walkthrough session。
- **排除项**：production deployment、SSO/enterprise auth、load testing、
  migration of live data、guarantees about model-vendor pricing or accuracy。

#### 条目 2 — Knowledge-base / RAG assistant with source citations

- **定位句**：A retrieval-augmented assistant over your documents that always
  answers with citations to the source chunks it used, so your team can check
  every claim.（技术栈：PostgreSQL + pgvector，可适配其他存储。）
- **客户需提供**：document corpus 与处理授权；10–20 real questions with
  expected answers or source documents 作为验收集；指定 LLM provider/model
  （或 provider-agnostic setup）。
- **交付物**：ingestion pipeline（chunking + metadata）；retrieval + answer
  service（API + small chat UI，per-claim source references，含
  "insufficient evidence" 路径而非硬答）；evaluation report（hit rate、
  citation accuracy、failure examples）；handover doc。
- **验收**：验收集上 answers cite the expected sources（约定阈值，如 ≥ 80%
  correct citations）；corpus 外问题 refusal/escalation 而非编造；客户可自行
  add a document and re-run evaluation。
- **排除项**：content authoring、multilingual OCR of poorly-scanned archives、
  fine-tuning of foundation models，以及任何对未测试数据的 accuracy claim。

#### 条目 3 — MCP tool integration with a human-approval backend

- **定位句**：Expose your internal tools/APIs to LLM agents safely using the
  Model Context Protocol — MCP server wrappers plus the approval/audit backend
  that keeps a human in command of every consequential call（OpsPilot 同款
  架构：three MCP servers、`ToolGateway` contract、persisted approval gate
  with full audit trail）。
- **客户需提供**：API documentation or OpenAPI specs；sandbox/test accounts
  与 non-production data；risk policy（哪些 read-only、哪些需审批、amount/
  status limits）；a named approver（or approver group）。
- **交付物**：MCP servers following a written tool contract（names、schemas、
  error semantics、idempotency behaviour）；approval backend（requests
  persisted、expire、唯一执行高风险调用的路径，append-only audit log）；
  integration tests（无审批不执行高风险调用；同 idempotency key 重放不重复
  生效）；tool contracts 文档与 operator runbook。
- **验收**：自动化测试证明 "every high-risk write has a matching approval"；
  simulated end-to-end incident 走通 investigate → propose → approve →
  execute exactly once → audit trail；客户可按文档自行 add one new tool。
- **排除项**：production go-live、penetration testing / formal security
  certification、multi-role approval chains 与 spending-authority matrices
  （可另行设计报价）、integration into live customer-facing systems。

#### 三条目录通用的商业措辞

- **不承诺时间线与 ROI**：No timelines or ROI figures are promised — those are
  quoted per project after scoping.
- **生产集成另行评估**：Integration into your real production system is a
  separate scoping exercise, not part of the base offering.

---

## 3. 发布前自查清单（红线逐条 checkbox）

以下每条均来自 [portfolio-entry-en.md](portfolio-entry-en.md) 的
Honesty notes 与相关文档的红线要求。**粘贴完成后逐条勾选，任何一条不满足都不要提交。**

- [ ] description 未含任何客户名、收入数字或节省额（项目是 independent case
  study，无客户合作）。
- [ ] 保留了 "Systems are mocks" 与 refund 为 simulated 的措辞（若改写
  description，这两句必须原样保留；demo 的 CRM/Billing/Issues 均为 in-repo
  mocks）。
- [ ] 演示视频（若放）未被描述成 live / real-model run；标题与简介保留了
  "scripted fake model provider — no external AI calls" 披露。
- [ ] 未声称"已通过真实模型验收/评分"：tool-selection accuracy 写成
  **open metric**；真实模型证据只表述为 safety gate held（0 unsafe
  executions），不升级为整体 success claim。
- [ ] 未 reintroduce "fully replayable" 这类 bare claim；只按 claims-audit
  的有界表述：trace is *recorded and auditable*，writes replay by idempotency
  key。
- [ ] "1,365 tests / zero violations" 数字只在有出处的语境中使用（来源
  docs/phase2-review-2026-11-14.md），未扩展成其他未验证指标。
- [ ] 未含个人联系方式（邮箱、电话、微信/WhatsApp 等一律不出现），站外联系
  走平台站内。
- [ ] 技能标签只有实际使用过的技术（默认安全集：Python · FastAPI · Next.js ·
  RAG · LLM Integration；未用过 LangChain 就不放）。
- [ ] description 字符数 ≤ 600（用 595 字符版；如平台计数不同，删句而不是
  静默超限）。
- [ ] title ≤ 70（66/70）、role ≤ 100（99/100）。
- [ ] 封面图为 `cover-1000x750.png`（1000×750），无拉伸变形、无联系方式水印。
- [ ] 服务目录三条均含 "Excluded" 段，且未承诺时间线/ROI。

---

## 4. 发布后核验项

提交后立即逐项核对；任何一项不符就回到草稿修正后重新提交。

- [ ] 字符数复核：平台显示的 title/role/description 长度与本文档标注一致
  （66/70、99/100、595/600）。
- [ ] description 在前台展示完整，末句 "…approval backends." 未被截断。
- [ ] 技能标签恰好 5 个，与所填一致。
- [ ] 封面图与附加图可正常加载、比例正确、无联系方式或内部信息。
- [ ] 项目链接（若填）可公开访问，指向仓库公开页。
- [ ] 演示视频链接（若填）可访问，且页面文案含 fake provider 披露。
- [ ] 服务目录三条的标题/要点与第 2.2 节一致，价格档位与商业措辞（不承诺
  时间线/ROI）未被平台表单诱导改写。
- [ ] 以普通访客（未登录）视角查看 profile，确认无个人联系方式泄露。
- [ ] 24 小时后复查一次：条目通过平台审核、状态为 published，无被降级/
  隐藏提示。
