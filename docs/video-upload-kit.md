# 演示视频上传材料包（docs/video-upload-kit.md）

为 [release-checklist.md](release-checklist.md) 第 5 节「演示视频上传」准备的全套文案与元数据。
本文件只提供上传所需的标题、简介、标签、分类、字幕导入步骤与回填编辑点；
**不执行上传**，也不改 README / showcase.md（回填由用户在上传完成后按第 6 节的编辑点执行）。

## 0. 素材清单（全部已在仓库内）

| 项 | 路径 | 时长 |
|---|---|---|
| 主视频（路径 A：审批→退款） | `web/public/showcase/demo-path-a.mp4` | 93.4 s |
| 路径 B（拒绝路径） | `web/public/showcase/demo-path-b.mp4` | 43.4 s |
| 路径 C（模型故障路径） | `web/public/showcase/demo-path-c.mp4` | 73.9 s |
| 英文字幕 | `web/public/showcase/demo-path-a.srt` / `-b.srt` / `-c.srt` | — |
| 封面图（1000×750） | `web/public/showcase/cover-1000x750.png` | — |
| 素材说明 | [showcase.md](showcase.md) | — |
| 分镜脚本 | [showcase-video-script.md](assets/showcase-raw/showcase-video-script.md) | — |

三段视频均为 **1440×900、H.264、25 fps**，录制自真实双进程栈
（Next.js dashboard + FastAPI api + worker + PostgreSQL/pgvector，
由 `scripts/demo_paths.py` 拉起，compose 项目 `opspilot-demo-test`）。
封面图与每段视频的标题卡/结尾卡都印有披露语。

## 1. 诚实红线（写文案前必读）

1. 视频里的 UI、API、worker、数据库、审批门、幂等退款、审计轨迹都是**真实运行**；
   但 **模型是假的**：`MODEL_PROVIDER=fake` 回放 `evals/datasets/fixtures/` 中的
   `duplicate_charge` / `already_refunded` fixtures，**没有任何外部 AI 服务调用**。
   统一披露语（英文原句，简介中必须原样出现）：
   > **scripted fake model provider — no external AI calls**
2. 简介不得写成 "AI 真实运行 / real-model demo / live AI agent"。正确表述是
   "live UI over a scripted scenario"、"a scripted fake model provider — no external AI calls"。
3. **真实模型**的证据是两份评测报告（开发集 3/15、2/15；holdout 10/32；safety unsafe=0），
   简介中如实指向：
   - [m3-02-dev-two-runs-2026-11-15.md](m3-02-dev-two-runs-2026-11-15.md)
   - [m3-03-holdout-safety-2026-11-15.md](m3-03-holdout-safety-2026-11-15.md)
4. B 站中文文案同样保留 fake 披露，禁止"真实 AI 运行""大模型实战"等夸大表述。

仓库链接占位符：`{{REPO_URL}}`，替换为
`https://github.com/gg320324492-lgtm/OpsPilot`。

## 2. 路径 A（主视频，93.4 s）— 审批 → 幂等退款

内容：标题卡披露（8 s）→ 全新栈 → 提交重复扣费工单 → agent 分类/检索/提议 $129 退款后
**停在审批门**（WAITING_APPROVAL）→ 审批卡（参数、风险、确定性解释 + 标注 untrusted 的模型解释）
→ 人工批准 → worker 以幂等键**恰好一次**执行退款 → 21 步审计轨迹、5 个引用来源 → 结尾卡。

### 2.1 YouTube 标题（98 字符，≤100 ✓）

```
OpsPilot path A — duplicate-charge refund through a human approval gate (scripted fake model)
```

### 2.2 YouTube 简介（直接粘贴）

```
OpsPilot is an AI operations agent for B2B billing support, built on FastAPI + Next.js + Electron. This is the main walkthrough: a duplicate-charge ticket is classified, the refund policy is retrieved, and the agent proposes a $129 refund — then the run PARKS at a human approval gate. Nothing executes until a human clicks Approve.

MODEL PROVIDER DISCLOSURE: the model in this video is a scripted fake model provider — no external AI calls. The dashboard, API, worker, PostgreSQL/pgvector database, approval gate, idempotent refund execution and audit trail are all real: every frame is a real click or real server state on the two-process demo stack. The refund is executed exactly once (idempotency-keyed) and leaves a 21-step audit trail with 5 cited sources.

Why the approval gate matters: the agent's proposal is shown with its arguments, risk level, a deterministic policy explanation, and the model's own justification — labelled UNTRUSTED. The human decision, not the model, releases the money.

What you see (timestamps approximate):
00:00 Title card — disclosure
00:08 Freshly brought-up demo stack
00:16 Duplicate-charge ticket submitted
00:41 Agent proposes refund, run parks at WAITING_APPROVAL
00:54 Approvals inbox — arguments, risk, explanations
01:00 Human approves
01:08 Refund executed exactly once; audit trail
01:22 Runs list — one completed, fully traceable run

This is a pipeline demo, not a real-model demo. The real-model evidence is in the eval reports (dev 3/15 and 2/15; holdout 10/32; safety unsafe=0), linked from the repository docs.

Run it yourself: python scripts/demo_paths.py up
Repo: {{REPO_URL}}
```

### 2.3 标签（tags）与分类

- YouTube tags（逗号分隔，一次性粘贴）：
  ```
  ai agent, ai operations, human in the loop, approval workflow, llm agent, agent demo, ai safety, fastapi, nextjs, postgres, billing automation, ops automation, workflow automation, developer portfolio, system design
  ```
  （总长约 190 字符，远低于 500 字符上限；不要为流量添加"real AI""GPT"等误导性标签。）
- 分类：**Science & Technology**。
- 受众：不开"made for kids"。
- 封面：上传 `web/public/showcase/cover-1000x750.png`（已含披露语）。

### 2.4 B 站变体

标题（中文）：

```
OpsPilot 演示 A｜工单 → 人工审批门 → 幂等退款，全程可审计（脚本化 fake 模型，无外部 AI 调用）
```

简介（中文，保留披露）：

```
OpsPilot 是一个面向 B2B 计费客服的 AI 运维 agent（FastAPI + Next.js + Electron）。本视频是主路径演示：重复扣费工单被分类、检索退款政策后，agent 提议一笔 $129 退款——随后运行**停在人工审批门**，人不批准，钱就不动。

【模型披露】视频中的模型是脚本化 fake model provider——无任何外部 AI 调用（scripted fake model provider — no external AI calls）。仪表盘、API、worker、PostgreSQL/pgvector、审批门、幂等退款与审计轨迹均为真实运行：每一帧都是真实点击或真实服务端状态。

你会看到：提交工单 → agent 提议退款并停在 WAITING_APPROVAL → 审批卡展示参数、风险等级、确定性策略解释与标注为 untrusted 的模型解释 → 人工批准 → worker 以幂等键恰好一次执行退款 → 21 步审计轨迹与 5 个引用来源。

这是管线演示（fake model），不是真实模型演示；真实模型证据见仓库内的两份评测报告（开发集 3/15、2/15；holdout 10/32；safety unsafe=0）。

本地复现：python scripts/demo_paths.py up
仓库：{{REPO_URL}}
```

B 站标签：`AI`、`智能体`、`人机协同`、`运维自动化`、`审批流`、`FastAPI`、`Next.js`、`工作流引擎`、`软件工程`、`作品集`
B 站分区：科技 → 计算机技术。封面同上。

## 3. 路径 B（43.4 s）— 拒绝路径：钱不动，流程闭环

内容：披露卡（6 s）→ 同一重复扣费工单提交 → 调查相同、agent 提议退款并停靠 →
人工**拒绝** → 运行以升级回复（escalation reply）**正常完成**、账本仍只有原有一笔退款
→ 拒绝被记录在 Rejected 标签页。演示"拒绝也是工作流在正确工作"。

### 3.1 YouTube 标题（90 字符，≤100 ✓）

```
OpsPilot path B — the human says no: rejected refund, no money moves (scripted fake model)
```

### 3.2 YouTube 简介

```
OpsPilot is an AI operations agent for B2B billing support (FastAPI + Next.js + Electron). This short walkthrough shows the REJECT path: the same duplicate-charge investigation proposes the refund and parks at the approval gate — but this time the human says no. The run still COMPLETES, with an escalation reply for the customer, and no money moves: the ledger keeps exactly one refund. A rejection is recorded as a human decision on the Rejected tab — the workflow working as designed.

MODEL PROVIDER DISCLOSURE: the model here is a scripted fake model provider — no external AI calls. The dashboard, API, worker, PostgreSQL/pgvector database, approval gate and ledger are real: every frame is a real click or real server state on the two-process demo stack.

This is a pipeline demo, not a real-model demo. The real-model evidence is in the eval reports (dev 3/15 and 2/15; holdout 10/32; safety unsafe=0), linked from the repository docs.

Run it yourself: python scripts/demo_paths.py up
Repo: {{REPO_URL}}
```

### 3.3 标签与分类

- tags：`ai agent, human in the loop, approval workflow, ai safety, rejection handling, llm agent, fastapi, nextjs, ops automation, developer portfolio`
- 分类：Science & Technology。封面同上（或截取拒绝决定画面）。

### 3.4 B 站变体

标题：

```
OpsPilot 演示 B｜人工拒绝退款：钱不动，流程闭环（脚本化 fake 模型，无外部 AI 调用）
```

简介：

```
OpsPilot 是面向 B2B 计费客服的 AI 运维 agent（FastAPI + Next.js + Electron）。本视频展示**拒绝路径**：同样的重复扣费调查提议退款并停靠在审批门，这次人工选择拒绝——运行仍以升级回复正常完成，账本不多一分钱，拒绝作为人工决策记录在 Rejected 标签页。拒绝不是失败，而是流程在正确工作。

【模型披露】视频中的模型是脚本化 fake model provider——无任何外部 AI 调用（scripted fake model provider — no external AI calls）；仪表盘、API、worker、数据库、审批门与账本均为真实运行。

这是管线演示（fake model），不是真实模型演示；真实模型证据见仓库内的两份评测报告。

仓库：{{REPO_URL}}
```

B 站标签：`AI`、`智能体`、`人机协同`、`审批流`、`风控`、`FastAPI`、`工作流引擎`、`软件工程`
分区：科技 → 计算机技术。

## 4. 路径 C（73.9 s）— 模型故障路径：大声失败，队列自愈

内容：披露卡（6 s）→ 一个一次性 HTTP 容器对所有模型调用返回 **HTTP 429**，SDK 配置为零重试
→ 提交工单后首个模型调用抛出限流错误，运行**大声失败**（`model_unavailable`，
结构化诊断上时间线，无静默重试、无吞错）→ 移除 429 容器、恢复真实 worker
→ 下一个工单照常完成（该路径无需审批），队列在故障中存活，账本不变。

### 4.1 YouTube 标题（86 字符，≤100 ✓）

```
OpsPilot path C — model outage: run fails loudly, queue recovers (scripted fake model)
```

### 4.2 YouTube 简介

```
OpsPilot is an AI operations agent for B2B billing support (FastAPI + Next.js + Electron). This walkthrough shows failure handling: a throwaway HTTP container answers every model call with HTTP 429 and the SDK is configured with ZERO retries. The first model call fails — and the run fails LOUDLY: model_unavailable, with the structured diagnosis on the timeline. No silent retry, no swallowed error. Then the 429 server is removed, a real worker is restored, and the next ticket completes normally. The queue survived the outage and the ledger is unchanged.

MODEL PROVIDER DISCLOSURE: outside the injected 429 fault, the model is a scripted fake model provider — no external AI calls. The dashboard, API, worker, PostgreSQL/pgvector database and audit trail are real: every frame is a real click or real server state on the two-process demo stack.

This is a pipeline demo, not a real-model demo. The real-model evidence is in the eval reports (dev 3/15 and 2/15; holdout 10/32; safety unsafe=0), linked from the repository docs.

Run it yourself: python scripts/demo_paths.py up
Repo: {{REPO_URL}}
```

### 4.3 标签与分类

- tags：`ai agent, failure handling, error handling, llm agent, resilience, ai safety, fastapi, nextjs, queue, ops automation, developer portfolio`
- 分类：Science & Technology。封面同上（或截取 `model_unavailable` 诊断画面）。

### 4.4 B 站变体

标题：

```
OpsPilot 演示 C｜模型服务故障：运行大声失败，队列自愈（脚本化 fake 模型，无外部 AI 调用）
```

简介：

```
OpsPilot 是面向 B2B 计费客服的 AI 运维 agent（FastAPI + Next.js + Electron）。本视频演示故障处理：用一个一次性 HTTP 容器对所有模型调用返回 HTTP 429，且 SDK 配置为零重试。第一个模型调用失败后，运行**大声失败**——model_unavailable，结构化诊断直接落在时间线上，没有静默重试、没有吞错。随后移除故障容器、恢复真实 worker，下一个工单照常完成：队列在故障中存活，账本分文未动。

【模型披露】除注入的 429 故障外，视频中的模型是脚本化 fake model provider——无任何外部 AI 调用（scripted fake model provider — no external AI calls）；仪表盘、API、worker、数据库与审计轨迹均为真实运行。

这是管线演示（fake model），不是真实模型演示；真实模型证据见仓库内的两份评测报告。

仓库：{{REPO_URL}}
```

B 站标签：`AI`、`智能体`、`故障处理`、`高可用`、`消息队列`、`FastAPI`、`软件工程`、`可观测性`
分区：科技 → 计算机技术。

## 5. 字幕（.srt）导入步骤

三份英文字幕与视频同名同目录：
`web/public/showcase/demo-path-a.srt`、`demo-path-b.srt`、`demo-path-c.srt`
（英文；时间轴按录像自身节拍偏移，与 mp4 严格对齐，可直接使用，无需改时间）。

### 5.1 YouTube Studio

1. 打开 [studio.youtube.com](https://studio.youtube.com) → 左侧 **Content** → 选中刚上传的视频。
2. 进入 **Subtitles**（字幕）页签 → 点击 **Add language** → 选 **English**。
3. 点击 **ADD FILE** → 选择 **With timing**（即 .srt 文件）→ 选中对应 `demo-path-X.srt` 上传。
4. 核对几条时间轴（尤其路径 A 的 00:41–00:54 长句）→ **Publish**。
5. 三个视频分别操作；文件名与视频一一对应，勿交叉上传。

> 注：若 Studio 界面改版导致入口名称变化，功能关键词是 "Subtitles / Add language / With timing (.srt)"。

### 5.2 B 站

1. 在 [member.bilibili.com](https://member.bilibili.com) 投稿页上传 mp4 后，进入稿件编辑的 **字幕** 步骤（或投稿完成后：创作中心 → 内容管理 → 稿件管理 → 对应稿件 → 字幕管理）。
2. 选择语言 **英语（English）** → 上传对应 `demo-path-X.srt`。
3. 提交后等审核；B 站部分入口要求先完成实名认证才能添加字幕。
4. 若投稿流程未出现字幕入口，用创作中心"字幕管理"补传即可，srt 格式两边通用。

> B 站观众以中文为主：英文字幕保留原样即可（它同时证明披露语的英文原句），
> 不必翻译；如需中文字幕，可另建一条语言上传中文 .srt（本材料包未提供中文 srt，
> 如需可按分镜脚本 [showcase-video-script.md](assets/showcase-raw/showcase-video-script.md) 的台词自行翻译后制作）。

## 6. 上传后的回填位置（用户执行）

上传完成、拿到在线链接后，按以下编辑点回填（本材料包不预先修改这些文件）：

1. **README.md — "Watch it run" 小节**（`### Watch it run`，约 README.md:191 起）：
   在 "**Browser videos with the same disclosure.**" 那一段（约 README.md:200–205）
   之后追加一行在线观看链接，例如：
   ```markdown
   Watch online: [path A](https://…), [path B](https://…), [path C](https://…).
   ```
   若只上传了主视频 A，就只放 A 的链接，并保持"另外两段在 `web/public/showcase/`"的现有表述不变。
2. **docs/showcase.md — "Videos" 表格之后**（表格在 showcase.md:49–55）：
   在表格与 "Subtitles:" 段之间加一行：
   ```markdown
   Online: [path A](https://…) · [path B](https://…) · [path C](https://…)
   ```
   （按实际上传的平台/可见性填写；unlisted 链接也可放，README 面向的是仓库访问者。）
3. **docs/release-checklist.md — 第 5 节**：把"演示视频上传【待用户执行】"的状态改为已完成，
   并附三个链接与上传日期（YYYY年MM月DD日格式）。
4. 回填时同样遵守第 1 节红线：链接旁的描述保持 "scripted fake model provider" 表述，
   不写"真实模型演示"。

## 7. 上传参数速查

| 项 | 路径 A | 路径 B | 路径 C |
|---|---|---|---|
| 文件 | `web/public/showcase/demo-path-a.mp4` | `web/public/showcase/demo-path-b.mp4` | `web/public/showcase/demo-path-c.mp4` |
| 时长 | 93.4 s | 43.4 s | 73.9 s |
| 字幕 | `demo-path-a.srt` | `demo-path-b.srt` | `demo-path-c.srt` |
| YouTube 标题字符数 | 98 | 90 | 86 |
| 分类 | Science & Technology | Science & Technology | Science & Technology |
| 可见性建议 | Public（主视频） | Public 或 unlisted | Public 或 unlisted |
| 封面 | `web/public/showcase/cover-1000x750.png` | 同左 | 同左 |

平台选择建议（与 release-checklist 第 5 节一致）：YouTube unlisted 适合作品集外链
（简历/Upwork 可直接引用）；B 站面向中文观众。三段建议**逐个上传**，A 为主，
B/C 可在 A 的简介和置顶评论中互链。
