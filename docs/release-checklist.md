# 发布操作清单（Phase 3 M3-05，2026-11-15）

**本清单只列步骤与材料，不执行任何对外发布。** 每一步都标明执行者：标 ✅ 的
已由本仓库的自动化或既有记录完成，仅供核对；标 **【待用户执行】** 的才是对外
动作，由仓库所有者决定时机并亲手操作。所有密钥只存在于 gitignored 的 `.env`，
本清单不含任何凭据。

**定稿基线：** v0.2.0 tag @ `656bbd3`（检索弃答修复、重跑报告、2026-11-15
走查与端口复核记录；发布后 main 已前移到 `43fde58` 的闭环记录）。

---

## 0. 当前状态一览（2026-11-15 更新）

| 项 | 状态 | 位置/证据 |
|---|---|---|
| 代码与材料定稿 | ✅ tag `v0.2.0` @ `656bbd3`（本地+远端；发布后 main 前移到 `43fde58`） | `git log --oneline -1 v0.2.0` |
| 真实模型结果 | ✅ 已如实回填（开发集首测 3/15、2/15；检索修复后重跑 2/15、3/15——四跑均未达 10/15；holdout 10/32；safety unsafe=0） | [m3-02-dev-two-runs-2026-11-15.md](m3-02-dev-two-runs-2026-11-15.md)、[m3-02-rerun-after-retrieval-fix-2026-11-15.md](m3-02-rerun-after-retrieval-fix-2026-11-15.md)、[m3-03-holdout-safety-2026-11-15.md](m3-03-holdout-safety-2026-11-15.md) |
| 新 installer 候选 | ✅ 已从 v0.2.0 tag（`2b96e70`，含 2026-11-15 走查记录与壳跟随 API 端口的修复）干净 checkout 构建并机器校验；独立走查已完成，端口占用场景复核通过 | `desktop/release/OpsPilot-Setup-0.2.0-x64.exe`（130,075,633 B，SHA-256 `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`）；compose 逐字节一致、包内无凭据、packaged-posture 5 passed / 1 skipped（no-token scan） |
| 线上 Release | ✅ `v0.1.0` 与 `v0.2.0` 均已发布；v0.2.0 asset 为新包（130,075,633 B，SHA-256 `2461f146…`），2026-11-15 走查+复核通过 | <https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.1.0> |
| 演示视频 | ✅ 已入库（fake provider，卡片已披露）；**按用户决定仅 GitHub 展示，不上传外部平台** | `web/public/showcase/demo-path-{a,b,c}.mp4` + 同名 `.srt`、[showcase.md](showcase.md) |
| Upwork 素材 | ✅ 草稿定稿（含合规 description 变体）；**平台发布未做** | [upwork-profile-entry-en.md](upwork-profile-entry-en.md)、[service-offerings-en.md](service-offerings-en.md) |
| CI | ✅ tag 提交 `656bbd3` 远端 CI success（run 43710760626）；发布记录 `43fde58` CI success（run 43711752333） | `gh run list --repo gg320324492-lgtm/OpsPilot` |

### 决策记录（2026-11-15，用户确认）

1. **Release 方案 A**：新 tag `v0.2.0`，新旧 Release 并存；tag 指向含检索修复的 `8133a89`。
2. **真实模型门槛**：接受现状，按 open metric 如实展示；不换模型重跑（后续可随时重测）。
3. **演示视频**：仅 GitHub 仓库内展示，不上传 YouTube/B 站/Upwork 媒体库。
4. **新包真人走查**：发布前安排；**v0.2.0 Release 在走查完成后创建上传**（清单第 3、4 节顺序不变）。

---

## 1. 本地检查（推送前，任何人可复跑）

与本清单对应的树上依次执行；全绿才进入第 2 节。

```bash
py -3.12 -m pytest tests/unit/test_doc_line_citations_point_at_the_code.py tests/unit/test_the_documented_counts_agree_with_the_tree.py -q
py -3.12 -m pytest -q            # 全量离线套件（SQLite 内存库，无需服务/Docker）
py -3.12 -m mypy                  # 全仓 strict；当前基线 275 文件零错误
ruff check && ruff format --check
```

- 通过标准：两个守卫测试 + 全量 pytest 绿；ruff 零报告；mypy 零错误。
- 以上是**本地检查**；它不能替代下面的**远端 CI**。

## 2. 远端 CI（推送后）【待用户执行】

1. `git push origin main`（本 commit 所在分支）。
2. `gh run watch` 或打开
   <https://github.com/gg320324492-lgtm/OpsPilot/actions> 等待本次 push 的
   workflow 结束。
3. 记录 run id 与结论（绿/红）。**红则先修复再发布**；绿则把 run 链接回填到
   本清单第 0 节，并继续。

## 3. 新 installer 的真人走查（建议在发布前完成）【待用户执行】

材料三件套（发给走查人）：

1. [installer-verification.request.md](installer-verification.request.md) — 说明信；
2. 新包 `desktop/release/OpsPilot-Setup-0.2.0-x64.exe`
   （130,075,633 B，SHA-256
   `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`；即
   [v0.2.0 Release](https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.2.0)
   的 asset）；
3. [installer-verification.md](installer-verification.md)（中文对照
   [installer-verification.zh-CN.md](installer-verification.zh-CN.md)）— 十六项
   检查脚本。

要求：走查人**未参与构建**、机器有 Docker Desktop；走查后在
[installer-verification.md](installer-verification.md) 追加新表单（不要覆盖
2026-11-14 旧记录，该文件末尾已有此要求）。未走查就发布属可接受但需自担风险
的决定——如实记录"新包未经真人走查"。

## 4. Release 与 tag【待用户执行，二选一】

> 旧 `v0.1.0` Release 与其 asset 对应 2026-11-14 的 D2 走查记录，**不建议静默
> 替换**。两个方案都要求第 2 节 CI 绿。

**方案 A（已确认采用）：新 tag `v0.2.0`，新旧 Release 并存**

1. ✅ 版本号已统一提升为 `0.2.0`（`pyproject.toml`、`desktop/package.json`、
   `web/package.json`；守卫测试通过），独立 commit 在 tag 祖先里。
2. ✅ `v0.2.0` 已创建并推送，指向 `2b96e70`（含检索弃答修复、重跑报告、
   2026-11-15 独立走查记录，以及走查唯一缺口的修复：壳跟随
   `OPSPILOT_API_HOST_PORT`）。旧 tag 位置 `e6d9f69`→`8133a89`→`2b96e70`
   的每次移动都发生在创建 Release 之前——没有发布物受影响。
3. ✅ 在该 tag 的干净 checkout（`E:/OpsPilot-release-v020`）上构建完成：
   `cd web && npm ci && npm run build && cd ../desktop && npm ci && npm run package`。
   产物 `desktop/release/OpsPilot-Setup-0.2.0-x64.exe`，
   **130,075,633 B，SHA-256
   `2461f146320f221f1a7570565c786b49e8c9a799a826d8ac4eea587726c19332`**。
   机器校验：打包 compose 与源码逐字节一致；包内无 `.env`/凭据字面量；
   packaged-posture 门禁在 tag checkout 上重跑 =
   `npx playwright test e2e/packaged-posture.spec.ts e2e/packaged-posture-icon.spec.ts --project lifecycle`
   → **5 passed, 1 skipped**（跳过项为 operator-token 字面量扫描，构建环境未配置
   token 时无可搜索内容，与 M3-04 行为一致）。冷/热启动时间沿用
   [m3-04-repackage-verification.md](m3-04-repackage-verification.md) 的记录，
   本次未重新计时。
4. ✅ **已完成（2026-11-15）**：创建 Release 并上传：
   `gh release create v0.2.0 desktop/release/OpsPilot-Setup-0.2.0-x64.exe --title "v0.2.0" --notes-file docs/release-notes-v0.2.0-draft.md`
   （`.blockmap` 已同目录上传）。线上地址
   <https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.2.0>；
   asset 的 SHA-256 已在上传后于 release 对象上核对，与本地构建一致
   （`2461f146…`，130,075,633 B）。

**方案 B：把新包作为 asset 挂到现有 `v0.1.0`**

1. 先完成第 3 节真人走查（替换已发布 asset 前，新包必须有对等走查记录）。
2. `gh release upload v0.1.0 desktop/release/OpsPilot-Setup-0.1.0-x64.exe --clobber`
   —— 这会**覆盖** D2 走查对应的旧 asset；在 Release notes 追加一段说明新旧
   SHA-256 与走查记录对应关系，避免"记录页指向的文件不再是发布文件"。

两案共同收尾：确认 Release 页下载链接可访问、文件大小与 SHA-256 与本地一致；
旧记录（`565206f7…`）在方案 A 下继续有效，在方案 B 下需在
[installer-verification.md](installer-verification.md) 注记"asset 已替换"。

## 5. 演示视频上传（按用户决定：仅 GitHub 展示，不上传外部平台）

**决策：** 2026-11-15 用户确认演示视频仅保留在仓库内（`web/public/showcase/`
与 `docs/showcase.md` 入口），不制作 YouTube/B 站/Upwork 媒体库外链。下述步骤
保留备查，不再作为发布待办。

材料（均在仓库内，无需再加工）：

| 项 | 路径 |
|---|---|
| 主视频（路径 A，93.4s） | `web/public/showcase/demo-path-a.mp4` |
| 路径 B（43.4s）/ 路径 C（73.9s） | `web/public/showcase/demo-path-b.mp4`、`demo-path-c.mp4` |
| 英文字幕 | 同目录 `demo-path-a.srt` / `-b.srt` / `-c.srt` |
| 封面（1000×750） | `web/public/showcase/cover-1000x750.png` |
| 文案与清单 | [showcase.md](showcase.md)、脚本 [showcase-video-script.md](assets/showcase-raw/showcase-video-script.md) |

步骤：

1. 选平台（YouTube unlisted / B 站 / Upwork 媒体库均可）；三个视频可合并为一个
   合集或分开上传。
2. 上传视频后，在平台的字幕功能里导入对应 `.srt`（YouTube 支持 SRT 直接上传）。
3. 标题/简介使用 [showcase.md](showcase.md) 的措辞；**保留 fake provider 披露**
   （"scripted fake model provider — no external AI calls"），视频首尾卡已印有
   同样字样，简介里也要写。
4. 把得到的公开链接回填到 README 的"Watch it run"段或 [showcase.md](showcase.md)
   （当前 README 指向仓库内文件；若上传平台，建议在 showcase.md 加"在线观看"
   链接并提交）。
5. 如实标注：这些视频是**管线演示（fake model）**；真实模型证据是两份评测报告，
   不是这些视频。不要把视频描述写成"AI 真实运行"。

## 6. Upwork / 服务页更新【待用户执行】

1. 作品条目：按 [upwork-profile-entry-en.md](upwork-profile-entry-en.md) 粘贴
   —— title 66/70 字符、role 99/100 字符；description 用该文档中标好字符数的
   **合规变体**（595 字符那版，含真实模型 safety 一句；两段超限变体仅作备选，
   不要超限粘贴）；技能标签用建议安全集 Python · FastAPI · Next.js · RAG ·
   LLM Integration。
2. 服务说明：[service-offerings-en.md](service-offerings-en.md) 三类服务可直接
   改写为 Upwork 项目目录/ specialized profile 文案；其中"不承诺工期与收益"
   的边界保留。
3. 诚实红线（来自该文档"Honesty notes"，发布前自查）：不添加客户名/收入/节省
   额；保留"refund is simulated / systems are mocks"；不把 fake 视频说成 live；
   不声称"已通过真实模型验收"（M3-02 门槛未达成，如实表述为 open metric）。
4. 素材中**不含联系方式**（合成的客户邮箱仅是示例）；如平台需要联系信息，用
   平台私信功能，不要把个人邮箱写进公开文案。

## 7. 发布后核验（10 分钟）【待用户执行】

- [ ] Release 页：tag、asset 名、字节数、SHA-256 三者与本地记录一致。
- [ ] README 中所有外链（Release、CI run、视频）逐一点击可访问。
- [ ] README / 案例研究 / Upwork 条目 / showcase.md 四处的模型标识
      （DeepSeek V4.1-Flash via magpie 网关 `workbuddy/deepseek-v4.1-flash`）、
      评测数字（3/15、2/15、10/32、unsafe=0）与"fake 演示 vs 真实证据"的区分
      表述一致。
- [ ] `.env`、`evals/results/2026-11-15T05-08-14.json`、
      `2026-11-15T05-19-05.json`（M3-02 原始结果，gitignored）等未入库文件
      仍不在 git 历史中：`git log --all --oneline -- .env evals/results/2026-11-15T05-0*.json`
      应为空。
- [ ] 把实际 Release 链接、视频链接、走查记录位置追加到 `docs/progress.md`
      当日条目（一行即可），闭环 M3-05。

---

## 附：本清单未包含、需要用户另行决策的事项

- **真实模型门槛未达标的后续**：换更强模型重测（[model-validation-plan.md](model-validation-plan.md)
  §2.2 备选）或维持现状如实展示——影响作品集话术，属商业决策，不在本清单内。
- **新包真人走查的排期**（第 3 节）与**发布方案 A/B 的选择**（第 4 节）。
