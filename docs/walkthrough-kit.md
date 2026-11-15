# OpsPilot 新安装包·真人走查材料包（2026-11-15）

> **这份材料包发给谁：** 一位**没有参与构建 OpsPilot**、且愿意花约 30–60 分钟
> 的人。他需要一台**干净的 Windows 11（x64）机器**——干净的含义见下文
> 「预置条件」。**收下材料包的人不用读仓库、不用装开发工具、不用 GitHub
> 账号**：照着本页做即可。
>
> **材料包里有什么：** ① 本说明（walkthrough-kit.md，先读这页）；
> ② 走查步骤权威原文 `installer-verification.md`（英文，16 项检查）；
> ③ 中文对照 `installer-verification.zh-CN.md`（照着做看这份）；
> ④ 结果回填表单模板 `installer-verification-new-package-template.md`
> （复制一份逐项填写，回传）。本说明的「执行顺序」一节把 16 项串成了一条
> 时间线；两个文档里的分步说明更细，冲突时以英文原文为准。

---

## 1. 走查目标

OpsPilot 是一个 AI 运维 agent：接一张客户工单 → 检索适用政策 → 提议一个
动作；当动作**会动钱**时，它必须停下来等人工批准。本次走查要回答两个问题：

1. **装得上、跑得通**：安装包在一台陌生 Windows 机器上，只靠 Docker Desktop，
   能否装好、启动、端到端走完"工单 → 提议退款 → 停在审批门 → 批准 → 退款
   恰好一次 → 拒绝重复批准"。
2. **写法是否够好**：步骤文档是写给没碰过这个项目的人的。哪一步看不懂、
   卡住了，本身就是有效结论——**被测的是说明书写得好不好，不是走查人**。

> 提前说明，免得吓到：项目里的"计费/退款"是**模拟的**，没有真实支付通道，
> 批准退款只是往测试文件里写一行记录，不会动任何真钱。

## 2. 预置条件

| 项 | 要求 |
|---|---|
| 机器 | Windows 10 或 **11**，**x64**，普通笔记本即可 |
| 「干净」 | 这台机器**没装过 OpsPilot**；你（走查人）**不是项目作者**、没参与构建 |
| Docker Desktop | **已安装且已启动**（启动后给它一分钟稳定）。下载：<https://www.docker.com/products/docker-desktop/> |
| 磁盘 | 空闲约 **6 GB**（首次启动要在本机构建三个容器镜像） |
| 时间 | 首次启动 5–15 分钟；整场走查连同录屏约 30–60 分钟 |
| 网络 | 需要可用外网：装 Docker Desktop、npm/pip 拉依赖、知识库索引联网检索 |
| 录屏 | 任何能输出 **MP4**、可限码率的录屏工具（要求见 §6） |

**不需要**：项目源码、Node.js、任何构建工具、GitHub 账号、管理员权限。

## 3. 是哪个文件（核对后再开始）

| 项 | 值 |
|---|---|
| 文件名 | `OpsPilot-Setup-0.1.0-x64.exe` |
| 字节数 | **126,045,189**（约 120.2 MB） |
| SHA-256 | `71ac5ac0adcb543fc96810c96085fbb66bbb73260c92d85325a6927997ee9a7b` |
| 构建来源 | 本仓库 commit `81a0278`（`81a02783a3034838cc79fc366e6602611a3ab4f3`），2026-11-15 从干净 worktree 构建；机器校验记录见 [m3-04-repackage-verification.md](m3-04-repackage-verification.md) |

**先核对再安装**（普通命令提示符即可）：

```
certutil -hashfile "你的路径\OpsPilot-Setup-0.1.0-x64.exe" SHA256
```

第二行输出应与上表完全一致。

**防呆提示：** 还存在一个**旧包**——`119,227,780` 字节、SHA-256 以
`565206f7` 开头，2026-11-14 已由内部走过查，GitHub Release
[v0.1.0](https://github.com/gg320324492-lgtm/OpsPilot/releases/tag/v0.1.0)
上挂的就是它。**不要用它走查**；两个文件大小差约 7 MB，肉眼即可区分。
拿到哪个以哈希为准，不确定就问发起人。

## 4. 新包怎么拿到

发起人会把文件直接发给走查人（网盘 / 即时通讯 / U 盘均可）。收到后按 §3
核对哈希与字节数。以下模板供**发起人**发送时使用（走查人可跳过本节）：

> 你好，附件是 OpsPilot 的安装包，请按材料包说明做一次安装走查。
> 文件：`OpsPilot-Setup-0.1.0-x64.exe`
> 大小：126,045,189 字节
> SHA-256：`71ac5ac0adcb543fc96810c96085fbb66bbb73260c92d85325a6927997ee9a7b`
> 请先核对上面两项再安装；核对方法见材料包 §3。
> 走查步骤见 `installer-verification.zh-CN.md`（中文），结果请填
> `installer-verification-new-package-template.md` 回传给我。
> 过程请录屏，要求见材料包 §6。

> **交接现状（如实记录）：** 2026-11-15 构建的新包文件目前**不在构建机本地**
> （构建 worktree 清理时已删除，本机全盘检索无此文件）。要走查需先从
> 以下来源之一重新取得：
> 1. **CI 构建产物（可自助下载，需 GitHub 账号登录）**：仓库
>    <https://github.com/gg320324492-lgtm/OpsPilot> → **Actions** →
>    commit `81a0278` 对应的 **CI** run（run id `43635841749`，2026-11-14，
>    `desktop-package (the Windows artefact)` job 成功）→ 页面底部
>    **Artifacts** → 下载 **`OpsPilot-Setup-windows-x64`**（zip，解压即得
>    安装包）。**注意：这是从同一 commit `81a0278` 由 CI 在干净 runner 上
>    构建的包，与上表 SHA-256 不保证逐字节一致**（electron-builder 的
>    NSIS 打包嵌入了时间戳等非确定性内容），所以下载后以**你实际测得的
>    SHA-256 与字节数**为准，把实测值填进回填表单 §一，并注明"取自 CI
>    run 43635841749"。功能性 16 项不受影响。
> 2. **重新本地构建**：按 [m3-04-repackage-verification.md](m3-04-repackage-verification.md)
>    §1 的构建命令在仓库根目录执行，产物回到
>    `desktop/release/OpsPilot-Setup-0.1.0-x64.exe`。
> 3. **向原构建人索要**：若有人保留了 2026-11-15 构建的原始文件，其哈希
>    应与 §3 完全一致；核对后再用。
>
> artifact 保留期为 90 天（自 2026-11-14 起），过期请改用来源 2。

## 5. 16 项检查的执行顺序与预期

完整步骤见 `installer-verification.md`（权威原文）与
`installer-verification.zh-CN.md`（中文对照，照做看这份）。下面是顺序与
每项的**预期结果**速查；❗ 标出两处"看到即失败、立即停"的情形。

| 顺序 | 检查 | 预期结果 |
|---|---|---|
| 0 | 环境 | Docker Desktop 已启动；`docker version` 能出版本号 |
| 0b | 取包核对 | 文件名、字节数（126,045,189）、SHA-256（`71ac5ac0…`）三项一致 |
| 1 | 安装 | 双击安装包（SmartScreen 弹窗 → **更多信息 → 仍要运行**，属正常），装到 `%LOCALAPPDATA%\Programs\OpsPilot`；出现桌面快捷方式与开始菜单项，图标是**蓝方块**（不是黑圆白三角） |
| 2 | 启动 | 窗口日志依次滚动（Checking Docker → Starting postgres, migrate, api, worker → Backend ready on :8000 → Dashboard ready on :3210），随后仪表盘渲染：顶部导航 Dashboard/Tickets/Runs/Approvals/Knowledge，Recent runs 显示 "No runs yet"。首次启动 5–15 分钟，**别中途退出** |
| 3.1 | 知识库索引 | Knowledge → **Reindex knowledge/**：约 1 分钟内面板报 **17 seen / 17 indexed（98 chunks）**，无失败提示 |
| 3.2 | 提交工单 | Tickets → **Fill with the README's ticket** → **Submit ticket**：出现成功面板与 `/runs/<uuid>` 链接；**记下 run id** |
| 3.3 | 停在审批门 | 约 10 秒内 run 状态变 **Waiting approval**；点进 run，看到 trace（分类/检索/规划）与一张高风险退款审批卡（原文含 *"This moves 129.00 out of the account, refunding transaction TX-88219"*）。❗ **若 run 直接到 "completed" 而不经审批 = 安全门退化，停止并报告** |
| 3.4 | 批准 | 点 **Approve**：几秒内 run 变 **Completed**，时间线显示退款**执行一次** |
| 3.5 | 幂等 | 再次批准（UI 或 curl）被拒：**HTTP 409**，body 含 `"code":"approval_already_decided"` |
| 4 | 健康检查 | `curl -s http://127.0.0.1:8000/ready` → `{"status":"ready","checks":{"database":"ok","migrations":"ok"}}`；`curl -s -o NUL -w "%{http_code}" http://127.0.0.1:3210/` → `200` |
| 5.1 | 端口只绑 loopback | `netstat -ano \| findstr ":3210"`（及 `:8000`）每行都是 `127.0.0.1:…`；出现 `0.0.0.0` 或 `[::]` = 严重问题，报告 |
| 5.2 | 跨源写被拒 | `curl -X POST http://127.0.0.1:3210/api/knowledge/reindex` → `"code":"origin_missing"` |
| 5.3 | 无 token 被拒 | `curl -s http://127.0.0.1:8000/api/runs` → `"code":"unauthorized"` |
| 5.4 | 包内无凭据 | `dir /s /b "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\.env*"` → *File Not Found* |
| 6 | 退出善后 | 关窗后 `docker ps --filter name=opspilot-` 无行；`docker volume ls \| findstr opspilot` 恰好两个卷：`opspilot_postgres-data`、`opspilot_mcp-data`（数据应保留） |
| 6b | 重启留存 | 再次启动应用，§3.2 的 run 仍在列表里 |

**中文 Windows 的两个易错点**（详见中文对照版开头四条提醒）：

- SmartScreen 按钮显示为「**更多信息**」/「**仍要运行**」；窗口内所有日志与
  错误串**保持英文**，逐字比对清单里的代码格式原文，不要翻译。
- 若机器上曾装过旧版，开始菜单/桌面图标可能因**图标缓存**仍显示旧的黑圆
  白三角——这不是装错版本。按中文对照版 §0 的三行命令重建图标缓存后再看。

## 6. 录屏要求

- **时机**：双击安装包**之前**开始录，一直录到 §3.5 结束。首次启动的等待
  **保留**，不要剪掉——录的就是"陌生机器上从安装到跑通"的全过程。
- **规格**：MP4（H.264）、**1280x720** 固定分辨率、**无音频**、限码率使文件
  ≤ **10 MiB**（低个位数 MB 最理想）。
- **文件名**：`d2-walkthrough.mp4`（提交到仓库时放 `docs/assets/` 下；仓库名
  不可改，有测试把关）。工具只能出 WebM 时保留原文件并命名为
  `d2-walkthrough.webm`，**不要**把 WebM 改名成 .mp4。
- 回填表单里填写实际文件名与字节数。

## 7. 结果怎么记录与回传

1. **复制回填模板**：打开
   [`installer-verification-new-package-template.md`](installer-verification-new-package-template.md)，
   整份复制（存成新文件或直接贴进回复消息都行）。
2. **逐项填写**：16 项每项勾 通过/失败；失败项在备注里写清**你看到的原文**
   （弹窗文字、日志末尾几行），并按 §8 取证。
3. **填签名区**：走查人、日期、Windows 版本、Docker 版本、安装包来源、
   录屏文件名与字节数、总判定。
4. **回传**：把填好的表单 + 截图 + （如有）录屏文件，连同本材料包发回给
   发起人即可；不需要操作 git 仓库。

> 结果最终会**追加**到 [`installer-verification.md`](installer-verification.md)
> 文末的记录区（与 2026-11-14 的旧记录并列、不覆盖）。这一步由发起人完成，
> 走查人只需交回表单。

## 8. 遇到失败怎么取证

任一项失败时，做满五件事（对应清单 §7）：

1. **截图**：失败瞬间的**整屏**（弹窗、窗口日志原样），命名如
   `fail-3.3-1.png`。
2. **窗口日志原文**：把 OpsPilot 窗口里的日志**全文**复制下来，不要转述、
   不要只截最后一句。
3. **容器状态与日志**：

```
docker ps -a --filter name=opspilot-
docker compose -f "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\docker-compose.yml" logs --tail 100
```

4. **环境信息**：Windows 版本、Docker Desktop 版本（`docker version`）、
   本次是首次启动还是重复启动。
5. **安装包溯源**：SHA-256（前 16 位即可）+ 文件来源（谁给的 / 哪条 CI run）。

**安全红线：** 不要把 operator token 或任何 `.env` 文件内容贴进报告或聊天；
复制日志时若带上 token，把那一行替换成 `[redacted]` 并注明。

## 9. 发起人核对清单（发送前）

- [ ] 已确认走查人未参与构建、其机器未装过 OpsPilot、装有并启动了 Docker Desktop。
- [ ] 已按 §4 的三种来源之一把**新包**交到走查人手里，并附上哈希与字节数。
- [ ] 已连同本材料包发送：`installer-verification.md`、
      `installer-verification.zh-CN.md`、
      `installer-verification-new-package-template.md`。
- [ ] 已约定回传方式（表单 + 截图 + 录屏），并说明 token 脱敏要求。
- [ ] （走查完成后）把回填表单追加到 `installer-verification.md` 文末记录区，
      不覆盖 2026-11-14 旧记录。

---

*材料包整理：2026-11-15，Phase 3 M3-05 发布交接。对应新候选包：commit
`81a0278` 构建，126,045,189 字节，SHA-256 `71ac5ac0adcb543f…9a7b`。*
