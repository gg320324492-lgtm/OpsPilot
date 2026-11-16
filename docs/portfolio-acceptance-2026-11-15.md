# OpsPilot 作品集验收表 — Phase 3 M3-06（2026-11-15）

**核对基准 commit：`main` / `3fdc0278cf0000dca88adeb649a2b087dabacbf8`（2026-11-15）。**
本表按 [phase3-plan.md](phase3-plan.md) §2 的七维量表，对该 commit 上 main 分支的实际交付逐维复评。
评分方法沿用 [portfolio-scorecard.md](portfolio-scorecard.md)：每维 0–10 锚点分 × 权重占比。
分数只按证据给：证据是文件、测试、评测记录或人工走查记录，不是愿望。

**硬门槛状态（phase3-plan §2：任一未满足则不判定验收完成）：**

| 硬门槛 | 状态 | 依据 |
|---|---|---|
| 真实模型证据（开发集两跑 ≥10/15） | **未达成** | M3-02：DeepSeek V4.1-Flash 两跑 3/15、2/15（[m3-02-dev-two-runs-2026-11-15.md](m3-02-dev-two-runs-2026-11-15.md)） |
| 可演示流程（A/B/C 浏览器路径 + 视频素材） | **达成** | M2-04/M2-05 已合并；[docs/demo/](demo/)、[web/public/showcase/](../web/public/showcase/) |
| 陌生人复现（安装/启动可按文档完成） | **部分达成** | 机器走查完成（M3-04）；真人干净机点选走查对新包未执行 |

**结论：作品集总体验收未通过**——真实模型门槛未达成，且真人安装走查未对新包执行。
**当前状态（2026-11-15 晚，v0.2.0 发布后）：** 见 §7 第三轮复评，79.25 ≈ 79/100，仍未通过。
可以宣称的是：工程与安全证据完备、演示素材齐全、safety gate 在真实模型下 unsafe=0；
**不可以宣称**：项目已通过真实模型验收、模型具备达标的工具选择能力。

---

## 1. 七维评分总表

| # | 维度 | 权重 | 锚点分 | 加权分 | 目标分 | 状态摘要 |
|---|---|---:|---:|---:|---:|---|
| 1 | 工程与安全证据 | 25 | 9.0 | 22.5 | 24 | 测试/架构/守卫齐备，声明逐项核对 |
| 2 | 真实模型可信度 | 20 | 4.0 | 8.0 | 17 | 重复测量已做，但开发集门槛未达成 |
| 3 | 展示体验 | 15 | 8.5 | 12.75 | 14 | 截图/视频/字幕齐，真实模型片段缺位 |
| 4 | 陌生人复现 | 10 | 8.0 | 8.0 | 9 | 机器走查完成，真人走查未做 |
| 5 | 案例与个人贡献 | 10 | 8.0 | 8.0 | 9 | 结构达标，模型表现数字未回填 |
| 6 | 接单说明 | 10 | 8.5 | 8.5 | 9 | 两份材料齐且合规 |
| 7 | 材料一致性与版本交付 | 10 | 7.0 | 7.0 | 10 | 新包已验，Release asset 未上传 |
| | **合计** | **100** | | **74.75 ≈ 75** | **92** | |

当前约 **75/100**（区间 73–77），较骨架自估（≈63，基线 51a99ab）上升约 12 分，
来源：M2-04/05 素材合入（维度 3 +3.5）、M3-02/03 真实模型批次执行（维度 2 +2，
因门槛未达成升幅受限）、M3-04 新包机器校验（维度 4 +2、维度 7 +1）。
与目标 92 的差额集中在：维度 2（真实模型达标，+9）、维度 3（真实模型演示片段，+1.5）、
维度 5/6 收尾（各 +1）、维度 7（Release 交付，+3）。

---

## 2. 逐维评分与证据

### 2.1 工程与安全证据（权重 25）— 锚点 9.0，加权 22.5

**证据（均在基准 commit 上）：**

| 证据 | 位置 | 验证方式 |
|---|---|---|
| 离线测试套件（unit + integration，SQLite 内存库无需服务） | `tests/unit/`（70+ 文件）、`tests/integration/`（40+ 文件） | 本地 pytest 全绿；CI lint+test job |
| 五道门与状态机文档-代码一致 | [agent-state-machine.md](agent-state-machine.md)、`tests/unit/test_state_machine_document_matches_the_code.py` | 守卫测试 |
| 退款恰好一次（进程内三层防护：幂等键派生、gate 4 短路、部分唯一索引） | `src/opspilot/domain/policies.py`、`tests/integration/test_refund_idempotency.py` | 集成测试 |
| 注入防御（结构门 vs 提示词免疫） | `tests/security/`、safety 数据集 15 例 | 评测 + 单测 |
| 显式单次模型请求（SDK max_retries=0） | `tests/unit/test_provider_single_shot_no_sdk_retries.py`（18 例） | 离线固定测试 |
| 能力声明逐项核对（replayable/exactly once/95%/unsafe=0/多签 五项降级或限定） | [claims-audit-2026-11-15.md](claims-audit-2026-11-15.md) | 文档审计 |
| 文档引用防漂移守卫（行号/测试名/交叉引用） | `tests/unit/test_doc_line_citations_point_at_the_code.py` 等 4 个守卫 | 守卫测试 |
| 架构文档（分层、五道门、worker 模型、范围控制） | [architecture.md](architecture.md)、`docs/adr/0001`–`0005` | 文档走查 |
| 失败结构化分类（76 条历史记录） | [eval-failure-classification-2026-11-15.md](eval-failure-classification-2026-11-15.md) | 审计记录 |

**评分理由：** 达到 9–10 锚点描述的全部要素（安全口径区分预期/授权、声明逐项核对、
失败结构化归因、引用自动守卫）。不给 10 的原因：集成测试中 PostgreSQL 标记用例
在本机默认跳过（依赖 CI 的 postgres 服务），全量矩阵以 CI 为准；基准 commit 的
CI 终态已确认为 success（见 §6 核对说明），扣分仅因覆盖结构而非任何失败。

### 2.2 真实模型可信度（权重 20）— 锚点 4.0，加权 8.0

**证据：**

| 证据 | 结果 | 位置 |
|---|---|---|
| Phase 1 基线（MiniMax-M3.1-Flash，2026-10-11） | tool selection 3/15；参数有效率 0.471 | [evals/baselines/phase1-baseline.json](../evals/baselines/phase1-baseline.json) |
| B2 修复后复测（MiniMax，两跑） | 2/15、2/15；参数有效率 0.378→1.000 | progress.md §"B2 executed against the real endpoint" |
| **M3-02 开发集两跑（DeepSeek V4.1-Flash，冻结配置）** | **3/15、2/15 — 未达 ≥10/15 门槛** | [m3-02-dev-two-runs-2026-11-15.md](m3-02-dev-two-runs-2026-11-15.md) |
| M3-03 holdout 32 例（同冻结配置，未调参） | tool selection 10/32（0.312）；参数有效率 1.000 | [m3-03-holdout-safety-2026-11-15.md](m3-03-holdout-safety-2026-11-15.md) |
| M3-03 safety 15 例（13 live + 2 注入） | **unsafe execution count = 0**；审批合规 1.000；expected-write 0.250 | 同上 |
| 重复测量之间的稳定性 | 两跑相同工具集合仅 8/15；run 间波动已如实报告 | M3-02 报告 §4 |

**评分理由：** 该维度考察"重复测量、成功与失败证据"。重复测量已做（开发集两跑 +
holdout + safety 独立批），失败证据完整（检索弃答 3 例的确定性归因、miss 模式集中性、
成本与延迟均如实记录），这部分值 6–7 分。但 **10/15 硬门槛未达成**（3/15、2/15，
与可竞争上限 12/15 相比也差距明显），holdout 10/32 同样远低于开发集同水平，
模型工具集合判断力这一核心能力主张不能成立。综合锚点 4.0。
**safety unsafe=0 是达标项**（13 live + 2 synthetic 注入全部拦截，审批门零放行），
但这按 M1-05 口径是"safety gate 的证据"，不能折算成模型能力分。

**影响声明：** 在门槛达成前，README、案例、作品集材料均不得宣称
"passed real-model acceptance""10/15 tool-selection accuracy" 或任何推断的自主处理率。
当前材料已符合此约束（见 §4 不一致表）。

### 2.3 展示体验（权重 15）— 锚点 8.5，加权 12.75

**证据：**

| 证据 | 位置 |
|---|---|
| M2-04 三条演示路径（种子数据 + Playwright 断言 + 启动/重置指引） | [demo-paths.md](demo-paths.md)、`tests/integration/test_demo_paths.py` |
| M2-05 截图 13 张（1440×900，真实 UI 录制） | [assets/screenshots/](assets/screenshots/)（10 张）、[demo/](demo/)（3 张路径图） |
| 1000×750 封面 | [assets/showcase-raw/](assets/)、`web/public/showcase/` |
| 3 条浏览器演示视频（A/B/C，90–180s）+ 英文字幕 | [demo/demo-path-a.mp4](demo/)、`demo-path-b.mp4`、`demo-path-c.mp4` + 同名 `.srt` |
| live/fake 披露（封面与片头/片尾卡） | [showcase.md](showcase.md) |
| golden-path GIF | [assets/golden-path.gif](assets/) |

**评分理由：** 达到 6–8 锚点全部要素，且 fake/real 披露清晰。未到 9–10 的缺口：
**真实模型片段缺位**——M2-05 素材全部使用 `MODEL_PROVIDER=fake`（确定性 fixture 重放），
showcase.md 已如实披露；phase3-plan §5 预留的"真实模型演示片段在 M3 验证后定稿"
因 M3-02 门槛未达成而无法定稿。9–10 锚点要求"真实模型片段与 run 证据一一对应"，
当前不满足，故 8.5。

### 2.4 陌生人复现（权重 10）— 锚点 8.0，加权 8.0

**证据：**

| 证据 | 结果 | 位置 |
|---|---|---|
| 三条启动路径（观看/离线复现/真实模型）文档 | 齐 | [README.md](../README.md) "Try it: three paths" |
| 新 installer 构建 + 机器可验校验（SHA-256、无凭据扫描、compose 一致性） | 126,045,189 B；SHA-256 `71ac5ac0…7ee9a7b` | [m3-04-repackage-verification.md](m3-04-repackage-verification.md) |
| 冷启动/热启动 Docker 走查（一次性项目 opspilot-m304-cold） | 冷启 3.5–4 min（有缓存）、热启 18.3s、/ready ok ×2 | 同上 §4 |
| 旧包真人走查（2026-11-14，非作者，16 项检查） | 通过 | [installer-verification.md](installer-verification.md) |
| **新包真人干净机点选走查** | **未执行** | M3-04 报告 §7 如实记录 |

**评分理由：** 6–8 锚点要素齐（三路径文档、单必填 token、端口/migration/排障说明）；
9–10 要求"安装走查由非作者完成"——旧包已做，**新包未做**，按锚点保守给 8.0。
M3-04 报告 §7 已列明未覆盖项（真人走查、新 checkout 离线流程、SmartScreen 复验、
可复现构建承诺）。

### 2.5 案例与个人贡献（权重 10）— 锚点 8.0，加权 8.0

**证据：**

| 证据 | 位置 |
|---|---|
| 英文案例研究（问题/角色/方案/验证/局限五段，每个数字可溯源） | [case-study-duplicate-charge-en.md](case-study-duplicate-charge-en.md) |
| 客户摘要版 | [case-study-summary-en.md](case-study-summary-en.md) |
| 中文对照版 | [case-study-duplicate-charge.md](case-study-duplicate-charge.md) |
| 能力边界审计（案例口径依据） | [claims-audit-2026-11-15.md](claims-audit-2026-11-15.md) |

**评分理由：** 结构与口径达标：个人贡献按实际描述（sole designer/implementer），
不冒充客户合同，不声称收益；验证段明确"demo 用确定性 provider，证明的是门与接线，
不是模型质量"，并如实指出真实模型结果低于自设验收线。扣分点：案例验证段引用的
仍是 phase2-review 的历史数字，**M3-02/03 的新批次结果未回填**（B1 回填项），
"real-model validation in progress" 的表述在 M3-02 已有结论后已不够准确。

### 2.6 接单说明（权重 10）— 锚点 8.5，加权 8.5

**证据：**

| 证据 | 要点 | 位置 |
|---|---|---|
| 作品集条目 | title 66/70、role 99/100、description 593/600 字符，5 技能标签，无联系方式 | [portfolio-entry-en.md](portfolio-entry-en.md) |
| 三类服务说明 | 每类含客户输入/交付物/验收方式/排除项（985 词） | [service-offerings-en.md](service-offerings-en.md) |
| 边界一致性 | 不声称付费客户/收入/未测量节省；退款为模拟 | 两份材料开头声明 + claims-audit 呼应 |

**评分理由：** 达到 6–8 锚点全部要素。9–10 要求"服务边界与案例、能力审计完全一致"——
材料间口径已对齐，但案例的模型表现数字未回填（同 §2.5），一致性闭环差最后一步，
故 8.5。

### 2.7 材料一致性与版本交付（权重 10）— 锚点 7.0，加权 7.0

**证据：**

| 证据 | 状态 | 位置 |
|---|---|---|
| 新 installer 构建校验（SHA-256、无凭据、compose 一致、16 单测 + 6 门禁） | 已完成 | [m3-04-repackage-verification.md](m3-04-repackage-verification.md) |
| 版本号单一来源（0.1.0：pyproject = desktop = web） | 一致 | `pyproject.toml:7`、`desktop/package.json:3`、`web/package.json:3`；守卫 `test_the_version_is_one_number.py` |
| installer-verification 记录页已加"新候选存在"注记 | 已完成 | [installer-verification.md](installer-verification.md) |
| 文档一致性审计（路径/行号/测试名/交叉引用四维脚本审计） | 已完成（2026-11-14） | progress.md §"Documentation consistency audit" |
| **v0.1.0 Release asset 替换/上传（新包）** | **未执行** | GitHub Releases（用户操作项） |
| 数字回填（案例/README 的 M3 批次结果） | **未执行** | 见 §4 不一致表（M3-05 范围） |

**评分理由：** 仓库内一致性证据充分（守卫测试 + 审计 + 版本单一来源 + 新包校验），
但"版本交付"未闭环：新包未作为 Release asset 发布，案例与 README 仍载旧数字。
锚点 7.0。

---

## 3. 三张清单

### 3.1 已验证项（Verified）

| # | 验收项 | 证据位置 | 验证方式 | 结论 |
|---|---|---|---|---|
| V1 | 核心金路径离线可复现 | `tests/integration/`（Playwright e2e + API 集成） | pytest 全绿（本地 + CI） | ✅ |
| V2 | 退款恰好一次（进程内三层防护） | `tests/integration/test_refund_idempotency.py`、`docs/case-study-duplicate-charge-en.md` | 集成测试 + 走查 | ✅ |
| V3 | 五道门行为与文档一致 | `docs/agent-state-machine.md`、`tests/unit/test_state_machine_document_matches_the_code.py` | 守卫测试 | ✅ |
| V4 | 单次模型请求（无 SDK 隐式重试） | `tests/unit/test_provider_single_shot_no_sdk_retries.py`（18 例） | 离线固定测试 | ✅ |
| V5 | 注入防御（结构门 containment） | `tests/security/`、M3-03 safety 15 例（2 注入全拦截） | 测试 + 真实模型评测 | ✅ |
| V6 | 能力声明逐项有限定 | `docs/claims-audit-2026-11-15.md`（5 项：2 限定、1 不成立已删、2 限定） | 文档审计 | ✅ |
| V7 | README 数字与树一致 | `tests/unit/test_the_readme_figures_match_the_tree.py` | 守卫测试 | ✅ |
| V8 | A/B/C 演示路径浏览器可走通 | `tests/integration/test_demo_paths.py`、`docs/demo/demo-path-{a,b,c}.mp4` | Playwright 断言 + 录屏 | ✅ |
| V9 | **真实模型两跑 ≥10/15 工具命中** | M3-02：3/15、2/15 | 评测记录 | ❌ **未达成** |
| V10 | holdout 32 例 + safety 15 例独立报告 | `docs/m3-03-holdout-safety-2026-11-15.md`；safety **unsafe=0** | 评测记录 | ✅（holdout 分数如实记录，未达开发集水平） |
| V11 | 陌生人按文档完成安装启动 | 旧包：`docs/installer-verification.md`（2026-11-14，16 项）；**新包未做** | 人工走查 | ⚠️ 旧包 ✅ / 新包 ❌ |
| V12 | 30 秒/3 分钟测试通过 | 本报告 §5（AI 走查替代真人） | 走查记录 | ⚠️ 结构性通过，待真人复核 |
| V13 | 新包构建校验（SHA-256/无凭据/compose 一致） | `docs/m3-04-repackage-verification.md` | 机器校验 | ✅ |
| V14 | M3-03 safety unsafe=0（硬门槛） | `docs/m3-03-holdout-safety-2026-11-15.md` | 真实模型评测 | ✅ **达成** |

### 3.2 未验证 / 未达成项（Not verified / Known gaps）

| # | 项 | 缺失的证据 | 现状 | 影响 | 计划 |
|---|---|---|---|---|---|
| N1 | **真实模型门槛（10/15 两跑）** | 达标 run 记录 | M3-02 实测 3/15、2/15；DeepSeek 与 MiniMax 同水平（2–3/15） | **作品集不能宣传已通过真实模型验收**；维度 2 上限被压住 | 需用户决策：换更强模型（如 Claude Sonnet 5.5，方案 §2.2 预留）或接受现状如实展示 |
| N2 | 新包真人干净机点选走查 | 非作者走查记录 | M3-04 仅机器走查；旧包走查记录仍有效但不覆盖新包 | 维度 4 不到 9 分；发布前风险 | 发布前由用户安排一位非作者按 `docs/installer-verification.request.md` 走查新包 |
| N3 | 真实模型演示片段 | 真实 run 的浏览器录屏 | M2-05 素材全部为 fake provider（已披露） | 维度 3 不到 9 分 | M3-02 达标后补录，或维持 fake 披露现状 |
| N4 | Release asset 更新（新包） | GitHub Release v0.1.0 的新 asset | 本地 `desktop/release/OpsPilot-Setup-0.1.0-x64.exe` 已验，未上传 | 维度 7 交付不闭环 | 用户执行（见 §3.3 R1） |
| N5 | 真实客户/生产使用反馈 | 无 | 项目定位为参考实现 | 不适用（边界声明，materials 已如实） | 不适用 |
| N6 | 真实支付集成 | 无 | 模拟计费是既定边界 | 不适用（边界声明） | 不适用 |
| N7 | 本基准 commit 的 CI 终态 | ~~绿色 run 记录~~ **已取得** | `gh run list`：**3fdc027 CI completed success**（2026-11-15 查得） | 无 | 已闭环；此前一跑（933d53e）失败已由后续修复覆盖 |

### 3.3 发布操作清单（待用户执行）

来源：[release-checklist 类既有约定]（仓库未设独立 release-checklist 文档，
以下按 M3-04 报告 §7、installer-verification 与 phase3-plan §7 的交接要求汇总）。

| # | 操作 | 依据/输入 | 前置条件 |
|---|---|---|---|
| R1 | 上传新 installer 到 GitHub Release v0.1.0（附 SHA-256 `71ac5ac0…7ee9a7b`、字节数 126,045,189、blockmap） | `desktop/release/OpsPilot-Setup-0.1.0-x64.exe` | 建议先做 R2 |
| R2 | 安排非作者按 `docs/installer-verification.request.md` 对新包做干净机点选走查，回填 `docs/installer-verification.md` | 走查请求信 | 干净 Windows 机 + Docker Desktop |
| R3 | （决策）真实模型门槛是否继续追求：换模型重跑 M3-02 口径两跑，或接受"未达标"并保持材料现状 | M3-02 报告 §5 | 预算与 API key |
| R4 | （若 R3 换模型且达标）回填 §4 不一致表所列数字，并补录真实模型演示片段 | 新 run 记录 | R3 完成 |
| R5 | （若 R3 维持现状）在 README/案例的相应位置把 "in progress" 表述改为 "attempted, below our own bar"（数字已如实） | 本报告 §4 | — |
| R6 | 作品集条目发布（素材已就绪） | `docs/portfolio-entry-en.md`、`docs/service-offerings-en.md` | R1/R2 完成后 |

---

## 4. 材料间数字不一致表

> 本表只记录，不修改对应文档；回填属 M3-05/后续工作。

| # | 位置 | 当前表述 | 与之不一致的证据 | 建议 |
|---|---|---|---|---|
| D1 | `docs/case-study-duplicate-charge-en.md` 验证段 | "published results so far (phase2-review-2026-11-14) fall short…" | M3-02/03 已有更新结果（开发集 3/15、2/15；holdout 10/32；safety unsafe=0），案例未引用 | M3-05 回填：替换为 M3 批次数字，"in progress" 改为已尝试结论 |
| D2 | `docs/case-study-summary-en.md` | 同上（摘要版数字口径沿旧批次） | 同上 | 同上 |
| D3 | `README.md` Evaluation 段 | 基线表为 2026-10-11 MiniMax run；DeepSeek 两跑 3/15、2/15 已有注记，但 holdout 10/32 与 safety 15 例 unsafe=0 未入正文 | M3-03 报告 | M3-05 决定是否把 holdout/safety 提升为正文表格（当前在 M3-03 报告与 progress.md 中可查） |
| D4 | `docs/progress.md` 骨架期记录 | portfolio-scorecard.md 的 V/N/B 清单仍标 "M3-02 未开始/M3-03 未开始/M2-05 未开始" | M3-02/03/04 已完成、M2-05 已合入 | M3-05 更新 scorecard 骨架清单状态（本报告 §3 已给出终态） |
| D5 | `docs/phase3-plan.md` §2 | "当前估计 65"（骨架期值） | 本次复评 75（基准 3fdc027） | 属预测值与实测值之差，可在 M3-05 加一行"验收实测"注记，不必改历史 |
| D6 | `docs/portfolio-scorecard.md` | 自估 ≈63（基线 51a99ab） | 本次复评 75（基线 3fdc027） | 同上；scorecard 保留为骨架历史，验收以本表为准 |

无互相矛盾的"同一指标两个数"情形；上表均为"旧数字未回填/状态未刷新"，不是数据冲突。

---

## 5. 30 秒理解测试（陌生人视角走查）

> 说明：本次由 AI 会话以"第一次打开仓库的陌生人"视角执行（记录如下）；
> portfolio-scorecard.md §3 要求的"另一位读者"真人测试仍建议在发布前补做（V12 标 ⚠️ 的原因）。

**测试对象：** README.md 首屏（标题 → 一句话定位 → 摘要段 → Status 段，约前 22 行）。
**限时：** 30 秒通读；随后合上页面回答三问。

| 问题 | 能否回答 | 依据（首屏原文） |
|---|---|---|
| 解决什么问题？ | ✅ 秒答 | "A safety-gated AI operations agent for B2B support and billing" + "takes a customer support ticket, investigates it through MCP tools… stops and waits for a human" |
| 怎么看演示？ | ✅ 秒答 | 首屏下方 "Try it: three paths" 表第 1 行："Watch it — The GIF and ten screenshots below…"（无门槛入口清楚） |
| 怎么复现？ | ✅ 秒答 | 同表第 2/3 行："Reproduce it offline — docker compose up… no API key" / "Point it at a real model — …MODEL_PROVIDER, OPENAI_BASE_URL…" |

**记录到的理解障碍（已尽量按陌生人初读记录）：**

1. **首屏信息密度偏高**：定位句之后的摘要段一句话约 60 词，含 idempotency key、
   append-only trace 两个术语；技术上准确（claims-audit 已限定），但 30 秒读者
   可能跳过。缓解：加粗的 four-line 口诀（"The model proposes. Deterministic code
   decides. …"）起到了第二入口作用，实测可靠它完成理解。**不构成阻断**。
2. **"Phase 3 is in progress" 的 Status 段**对陌生人略含糊（不知道 Phase 3 剩什么）。
   缓解：紧随其后的 progress.md 链接可回答；但陌生人未必点。**建议**（M3-05 可选）：
   Status 段补半句"current focus: real-model validation"。
3. **演示视频入口在首屏不可见**：A/B/C 三条 mp4 在 `docs/demo/`，README 首屏只
   指 GIF 与截图；想看视频的访客需进入 "Showcase" 或 docs。**建议**（M3-05 可选）：
   在 "Watch it" 行加 demo 视频直链。

**结论：** 30 秒理解测试**通过**（三问均可答）；两条可选改进列入 M3-05 建议，不阻断验收。

---

## 6. 核对说明与后续

- **核对基准：** 本表全部证据取自 `main@3fdc027` 工作区实读；评测数字取自
  M3-02/M3-03 报告与 `evals/results/`（2026-11-15 批次）；安装包数字取自 M3-04 报告。
- **M3-05 并行说明：** M3-05（定稿回填）可能并行进行。若其合入改变了本文所引数字，
  以 main 最新提交为准复核；本报告记录的基准为 3fdc027。
- **CI 状态（已确认）：** 基准 commit `3fdc027` 的 GitHub Actions run
  **completed success**（报告定稿前经 `gh run list` 查得终态）。验收表引用的本地验证为：
  718 单测 + 489 集成 + mypy + ruff 全部通过（Phase 3 收尾基线），与 CI 结论互证。
  维度 1 不给 10 分的原因相应修正为：PostgreSQL 标记用例在本机默认跳过、全量矩阵
  依赖 CI 环境单一来源，属覆盖结构而非失败。
- **给 M3-05 的输入：** §4 不一致表（D1–D6）即回填工作清单；§5 的两条 README 建议
  为可选项；§3.3 R1–R6 中 R1/R2/R6 需用户执行，R3 是用户决策点。

---

## 7. 第三轮复评（2026-11-15 晚，v0.2.0 发布后）

**性质：** 只读复评，不修改证据、不重跑评测。**基准：** tag `v0.2.0` = `656bbd3`；
`main` = `ac04c82`（含 `43fde58` 发布闭环与 `ac04c82` checklist 刷新）。§1 的
74.75 分保留为 M3-06 当时的快照；本节是发布后的当前状态。

| # | 维度 | 权重 | 上轮锚点 | 本轮锚点 | 本轮加权 | 变化 | 一句话依据 |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | 工程与安全证据 | 25 | 9.0 | 9.0 | 22.50 | 0 | 证据链无变化 |
| 2 | 真实模型可信度 | 20 | 4.0 | 4.0 | 8.00 | 0 | 四跑 3/15、2/15、2/15、3/15，holdout 10/32；重测方案只是计划，未实测不给分 |
| 3 | 展示体验 | 15 | 8.5 | 8.5 | 12.75 | 0 | 素材齐全且披露清晰；真实模型片段仍缺位 |
| 4 | 陌生人复现 | 10 | 8.5 | 9.0 | 9.00 | +0.5 | 端口修复在最终发布物上独立复核通过，证据已归档 `docs/evidence/` |
| 5 | 案例与个人贡献 | 10 | 9.0 | 9.0 | 9.00 | 0 | 案例与验收材料口径一致 |
| 6 | 接单说明 | 10 | 9.0 | 9.0 | 9.00 | 0 | 两份材料一致；平台发布未做（边界一致） |
| 7 | 材料一致性与版本交付 | 10 | 7.0 | 9.0 | 9.00 | +2.0 | v0.2.0 Release 已发布、asset 哈希核对一致、tag CI 绿 |
| | **合计** | **100** | | | **79.25 ≈ 79** | **+2.5** | |

**结论：验收仍未通过。** 硬门槛「真实模型开发集两跑 ≥10/15」依然未达成（四跑均
2–3/15），真实模型演示片段仍缺位；分数上升来自版本交付与陌生人复现两项闭环，
不代表模型能力变化。

**严格口径说明：** 维度 4 若按「真人交互式点选走查对新包仍未做」计，维持 8.5，
总分为 78.75 ≈ 79，四舍五入相同。headless 走查不覆盖双击安装、图标与交互项。

**剩余差距（按可完成度排序）：**

1. **真实模型重测**（维度 2，最大缺口）：方案已写好
   （[model-retest-plan-v0.3.0.md](model-retest-plan-v0.3.0.md)），需 provider key
   与预算；达标前维度 2 不给情面分。
2. **真实模型演示片段**（维度 3）：需一次达标的真实模型运行才能录制；依赖第 1 项。
3. **真人交互式走查**（维度 4 的严格口径）：可选，headless 记录已覆盖功能面。
4. **文档尾差**（维度 7）：四跑数字、tag 指向、asset/Release 状态已在本轮同步；
   剩余为持续性维护而非扣分项。

---

## 8. 第四轮复评（2026-11-16，只读；方向 A 关闭后）

**基准：** `main` @ `a2340e9`（`656bbd3`/v0.2.0 之后 20 个提交，全部为 docs/）。
**新增事实：** MiniMax-M3.1-Flash 解冻重测 S2 两跑 **1/15、1/15**；DeepSeek 同族
更强档 **deepseek-v4.1-flash-max** 重测 **2/15、1/15**（v4-pro 被上游 402 拒绝
未能测）；GLM-5.3-Flash S1 **0/4**，**方向 A 按用户预案关闭**；MIN_SCORE 勘误
（检索修复实际生效的是查询扩展，阈值仍 0.22）。

| # | 维度 | 权重 | 三轮锚点 | 四轮锚点 | 四轮加权 | 变化 | 依据（要点） |
|---|---|---:|---:|---:|---:|---:|---|
| 1 | 工程与安全证据 | 25 | 9.0 | 9.0 | 22.50 | 0 | two-worker 认领已在第二 PostgreSQL 实测（`916a1e1`），属存量加固；既定扣分点不变 |
| 2 | 真实模型可信度 | 20 | 4.0 | **4.5** | **9.00** | **+0.5** | 证据完整度提升：五家族同冻结口径测量、失败形态跨模型一致、arg validity ≥0.968、unsafe=0；但 ≥10/15 预定义结果无一达成，加的是证据分，不是"关闭方向 A"本身 |
| 3 | 展示体验 | 15 | 8.5 | 8.5 | 12.75 | 0 | 真实模型片段随 A 关闭冻结；fake 披露现状维持且如实 |
| 4 | 陌生人复现 | 10 | 9.0 | 9.0 | 9.00 | 0 | 严格口径（真人点选走查）仍未执行 |
| 5 | 案例与个人贡献 | 10 | 9.0 | 9.0 | 9.00 | 0 | 案例数字已回填且口径如实 |
| 6 | 接单说明 | 10 | 9.0 | 9.0 | 9.00 | 0 | 作品集条目已同步新数字，边界一致 |
| 7 | 材料一致性与版本交付 | 10 | 9.0 | 9.0 | 9.00 | 0 | v0.2.0 交付闭环维持；MIN_SCORE 勘误已同步 |
| | **合计** | **100** | 79.25 | **80.25 ≈ 80** | | **+1.0** | |

**结论：验收仍未通过。** 硬门槛（开发集同配置两跑 ≥10/15）在五个模型家族中
均未接近（最好 3/15）；safety unsafe=0 继续跨家族成立。"不可宣称已通过真实
模型验收/模型具备达标工具选择能力"的边界不变。

**通往 92 的剩余路径（方向 A 关闭后）：**
- 维度 2 的 +9 与维度 3 的 +1.25 只能由真实模型达标产生；在册候选已耗尽
  （GLM 淘汰、MiniMax/flash-max 证伪、v4-pro 402），唯一未在当前冻结配置
  重测的家族是 Claude Sonnet 5.5（历史 1/15 为旧配置）——重启属用户决策
  （花费 + 重测）。
- 无 A 的上限：24(需C1+C2) + 9.0 + 12.75 + 9.0(B2 真人走查坐实) + 9.0 +
  9.0 + 10(B3/B4) = **82.75 ≈ 83**。
- 方向 B 可交付：B3/B4 → 维度 7 +1.0；B2 → 维度 4 严格口径坐实 9.0；
  B1 冻结；B5/B6/B7 不动分。
- 方向 C（C1+C2）→ 维度 1 +1.5。
- 另一条路是 phase4-draft A9/R5 的 harness/任务接口归因立项（另立项目）。

**过时条目清单（本轮已同步）：** phase4-draft §2.4 的「GLM S1 运行中」、
§5 的候选顺序与推荐组合、§7 的数字；README 评测表补 MiniMax 行；
one-pager/作品集条目补 MiniMax 1/15；MIN_SCORE 勘误同步 m3-02-rerun/两份案例/
release-notes/progress。
