# OpsPilot 桌面安装包验证清单（中文）

这是 [`docs/installer-verification.md`](installer-verification.md) 的中文对照版，给**执行验证的人**照做。
英文版是权威原文；本文件是它的转述，任何冲突以英文版为准。

**这一份要实现的是** `docs/phase2-plan.md` §P2-D2 的验收：

> 在一台干净的 Windows 机器上装好打包件，端到端走完 golden path。
> 这就是全部测试，而且**必须由没有构建它的人**执行。

---

## ⚠️ 四条会让你误报"失败"的提醒，先看这个

**1. 日志和应用错误信息是英文的，保持英文。**
你将看到的界面文字、窗口日志、Docker 报错，**全部是英文**——应用就是这么写的。
清单里凡是`代码格式`的英文句子，都是**你屏幕上会原样出现的字符串**，拿它逐字比对，不要翻译成中文去找。

**2. SmartScreen 弹窗的按钮名取决于你的 Windows 语言。**

| 英文原文 | 中文 Windows 上显示 |
|---|---|
| *"Windows protected your PC"* | *"Windows 已保护你的电脑"* |
| *"Unknown publisher"* | *"未知发布者"* |
| **More info** | **更多信息** |
| **Run anyway** | **仍要运行** |

看到这个弹窗是**正常的**（安装包没有代码签名），点「更多信息」→「仍要运行」。
**这不是失败**——它只是"这个文件来自下载"的提示，不是检测出文件有问题。

**3. 数字和 ID 必须逐字比对。** 见下方「必须原样保留的字面值」一节。
数字错了会让你报一个**假失败**。

**4. 这台机器上装过旧版的话，图标可能还是旧的。**

新安装包会**替换**程序文件，但 **Windows 的图标缓存**可能让开始菜单和桌面快捷方式
**继续显示旧图标**（一个黑圆里一个白三角）。
**程序文件里装的是新图标**，shell 给你看的是缓存里的旧图——
**这不是装错了版本，报成失败是假失败。**

**怎么做：** 在**普通**命令提示符里（开始菜单搜 `cmd` 直接回车，
**不要**用"以管理员身份运行"——提权的提示符起不回桌面 shell）逐行运行：

```
taskkill /f /im explorer.exe
del /a /f "%localappdata%\Microsoft\Windows\Explorer\iconcache_*.db"
start explorer.exe
```

第一行停掉资源管理器，第二行删掉它存图标的缓存库，第三行把资源管理器起回来。
任务栏和开着的文件夹窗口会重新出现，**图标会从文件里重新提取**。
如果 `del` 报某个文件"正在使用"，说明资源管理器抢在你删之前就回来了——把三行再跑一遍。

**为什么是删缓存，而不是"先卸载旧版再装新的"：**
缓存是**按路径**记的，而旧版和新版装在同一个路径
（`%LOCALAPPDATA%\Programs\OpsPilot\OpsPilot.exe`）。
那条缓存记录是**按这个路径命名**的，路径没变，
所以"先卸载再装"**并不能保证**它被清掉。
删掉缓存库则一定清得掉：shell 手里没有旧图可用，
只能从已安装的可执行文件里重新提取——那里面是新的美术稿。

---

## 0. 开始之前

### 你需要什么

| | |
|---|---|
| Windows | 10 或 11，**x64** |
| Docker Desktop | **已安装，且已启动** |
| 可用磁盘 | 约 **6 GB**（首次启动会从源码构建三个容器镜像） |
| 时间 | 首次启动 **5–15 分钟**；之后的启动不到一分钟 |

### 如果 Docker Desktop 没装

应用**不会**替你装 Docker，也**不会**装到一半能用。它会启动、开窗、然后明确告诉你问题：

```
Docker is not installed — this app could not run `docker`. Install
Docker Desktop from https://www.docker.com/products/docker-desktop/, then start
this app again.
```

**怎么做：** 装好 Docker Desktop，启动它，等系统托盘里的鲸鱼图标**停止转动**，再开 OpsPilot。
新机器上 Docker Desktop 自己第一次启动可能就要一分钟。

### ⚠️ 三种"Docker 有问题"是**三种不同状态**，不要混为一谈

| 状态 | 应用显示 |
|---|---|
| 没装 | `Docker is not installed…`（上面那段） |
| 装了但没启动 | `Docker is installed but not running` |
| "Docker 不好用" | **上面两者都不是** |

原文特意把三者分开，因为**处理方法不同**：没装要去装，没启动去启动。

---

## 1. 安装

1. 双击 `OpsPilot-Setup-<version>-x64.exe`（`<version>` 是实际版本号；
   当前构建的是 `OpsPilot-Setup-0.1.0-x64.exe`）。
2. 选安装目录，或直接用默认的 `%LOCALAPPDATA%\Programs\OpsPilot`。
   **不需要管理员权限。**
3. 完成。

**✅ 成功的样子：** 安装程序无报错地返回资源管理器。桌面出现一个名为 **OpsPilot** 的快捷方式，开始菜单也多一项。

**这两个入口上的图标：** 一个**蓝色圆角方块**——左上角亮蓝，右下角接近深藏青——
正中是**一个青色的箭头**，外面一圈**断成四段的圆弧**，四道缺口里各有一个小图形。
美术稿之前用的是**一个黑圆里一个白三角**（Next.js 模板自带的占位图）。
**一个是蓝方块，一个是黑圆**，不会认错。

**❌ 失败的样子：** 任何错误弹窗都是**安装程序的**，不是应用的——截图它。
（注：如果之前已装过，安装程序会覆盖，这不叫失败。）

**❌ 失败：重建缓存后图标还是"黑圆里一个白三角"** ——
那就不是缓存，也不是安装失败：你装的是**美术稿之前**的构建。
报告它，并说明你看到的是哪一个图标。

---

## 2. 启动

双击桌面上的 **OpsPilot** 快捷方式。

**按顺序你会看到这些。** 先是一个标题为 **OpsPilot** 的深色窗口在滚动日志，随后仪表盘取代它：

| 时机 | 屏幕上的内容 |
|---|---|
| 立即 | `Checking that Docker is available…` |
| 约 1 秒后 | `Starting postgres, migrate, api, worker with Docker Compose…` |
| 1–15 分钟（**仅首次**） | Compose 和 pip 的输出在滚动 |
| | `Backend ready on http://127.0.0.1:8000.` |
| 再几秒后 | `Dashboard ready on http://127.0.0.1:3210.` |
| | 仪表盘渲染出来：左侧导航（Tickets、Runs、Knowledge、Approvals）+ 一个空的工单列表 |

> **注意端口：8000 是后端 API，3210 是仪表盘。** 不要弄混。
> 地址里的 `127.0.0.1` 不是 `localhost`。

**首次启动很慢，这是正常的。** Docker Desktop 要从源码构建 API 和 worker 镜像，
没有预构建镜像可下载。**首次启动期间不要退出**——半成品镜像下次会重新构建，
不会损坏，但你得再等一遍。

### ❌ 失败：Docker 缺失

窗口显示前面两条 Docker 报错之一，然后**停住**。回到第 0 节。

### ❌ 失败：后端没起来

窗口显示 Compose 打印的最后几行。读它——Compose 会指出是哪个服务、通常也说了为什么。
然后在**任意目录**开一个终端，逐字运行下面两条，**看着它执行**：

```
cd "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack"
docker compose --env-file "%APPDATA%\OpsPilot\opspilot.env" up -d --wait postgres migrate api worker
```

> **`--env-file` 是必需的，不是可选的。**
> `docker-compose.yml` 给 `OPSPILOT_OPERATOR_TOKEN` 用了 `:?` 形式，
> 所以**缺少它时 Compose 根本拒绝渲染整个项目**——**每一条命令都如此，`up` 和 `down` 都一样**。
> 这里指定的文件就是应用首次启动时写给自己的那个。
> **删掉它，应用下次启动会生成一个新 token，你就得自己去创建一个新的。**

### ❌ 失败：token 不匹配

窗口显示：

```
The backend already running does not accept this app's operator token
```

**机制：** 应用发现已有一个后端在应答，**接管了它**，随后发现那个后端是用**另一个 token** 启动的。

**放着不管会怎样：** 得到一个"渲染得完美无缺、但拒绝每一次批准"的仪表盘——
应用选择停下来，并打印一条命令。

**怎么做：** 逐字运行它打印的那条命令（那是它验证过能用的那一条），然后重新启动应用。

### ❌ 失败：仪表盘没构建

窗口显示 `The dashboard has not been built`。

**原因：** 安装不完整——仪表盘是**打包在应用里面**的。**重装。**

### ❌ 失败：空白窗口

**一个空白窗口，里面什么都没有** = 应用的 **preload** 加载失败。
这是**外壳的 bug，不是你机器的问题**。重装，并报告它。

---

## 3. 走一遍 golden path

这是项目的核心主张，端到端，通过**已安装的应用**走完。

### 3.1 建索引

点左侧 **Knowledge** → 点 **Reindex knowledge**。

**一分钟内**结果面板报告 **17 seen** 和 **17 indexed**（写入 **98 chunks**）。没有失败提示。

> 三个数字都要对上：**17 / 17 / 98**。

### 3.2 提交工单

点 **Tickets** → 点 **Fill with the README**（它会自动填好字段）→ 点 **Submit ticket**。

出现一个成功面板，给出新 run 的链接，形如 `/runs/<uuid>`。**记下这个 run id。**

### 3.3 看它在审批门停住

**约 10 秒内** run 到达 **Awaiting approval**。点那个 run 链接。

页面显示一条 trace——**分类、检索、规划**——以及一张针对**高危写操作**的**审批卡片**：

```
The duplicate is confirmed and policy supports a refund. This is
HIGH_RISK_WRITE and requires human approval.
This moves $129.00 USD out of the account, refunding transaction TX-88219,
and cannot be undone automatically.
```

> **这就是整个产品的重点。** 模型提出；确定性代码判定它危险；run 停下，等人。
>
> **🚨 如果 run 直接变成 "completed" 而没有审批，说明安全门退化了——停下来，报告它。**

### 3.4 批准

点卡片上的 **Approve**。**几秒内** run 变为 **Completed**，时间线显示退款**执行了一次**。

### 3.5 确认它不能被做第二次

（这一步测的是**再次批准**必须被拒——第一次批准是成功的。）

```
curl -X POST http://127.0.0.1:3210/api/approvals/<approval-id>/approve ^
  -H "Origin: http://127.0.0.1:3210" -H "Content-Type: application/json" ^
  -d '{}'
```

> `<approval-id>` 是占位符，从 run 页面上的审批卡片读取。
> 注意这是 **3210 端口（仪表盘）**，不是 8000。
> 每一行末尾的 `^` 是 **cmd.exe 的续行符**，必须是该行**最后一个字符**。

**期望：** **HTTP 409**，且响应体里的 `code` 字段是 `approval_already_decided`。

> 响应长这样（FastAPI 默认格式，**冒号和逗号后面有一个空格**）：
>
> ```
> {"code": "approval_already_decided", "message": "..."}
> ```
>
> **只比对 `code` 的值就够了**，不要纠结空格的多少。

---

## 4. 用一条命令确认"整体通了"

应用**正在运行时**，在任意终端：

```
curl -s -o NUL -w "dashboard: %{http_code}\n" http://127.0.0.1:3210/
curl -s http://127.0.0.1:8000/ready
```

**期望输出：**

```
dashboard: 200
{"status":"ready","checks":{"database":"ok","migrations":"ok"}}
```

> `NUL` 是 Windows 的空设备，保持写成 `NUL`（**不要**写成 `/dev/null`）。
>
> ⚠️ **注意这里是紧凑格式（冒号后无空格）**，和第 5 节的错误响应不同。
> 两者不一样是有原因的：`/ready` 返回的是 Pydantic 模型，FastAPI 紧凑序列化；
> 而错误走的是 `JSONResponse`，默认带空格。
> **两处都按各自文档里写的形式比对即可**——重点是键名和值，空格不用纠结。

**判据：** `200` **加上** `"migrations":"ok"` 意味着整条链路通了——
应用起了 Docker、Compose 跑了迁移、API 在服务、应用拉起的仪表盘在应答。

---

## 5. 检查安全姿态在打包后是否仍然成立

四件事，每件一条命令。**这些测的是*已安装*的应用。**

### 5.1 仪表盘只绑定 loopback，没有暴露到你的网络

```
netstat -ano | findstr ":3210"
```

**每一行都必须读作 `127.0.0.1:3210`。**
如果任何一行是 `0.0.0.0:3210` 或 `[::]:3210`，那么这个仪表盘——**它持有操作员 token、能批准退款**——从你的局域网可达。
**🚨 这是严重发现，必须报告。**

**API 的 8000 端口同理：**

```
netstat -ano | findstr ":8000"
```

每条也必须是 `127.0.0.1:8000`。

### 5.2 仪表盘拒绝跨源写请求

```
curl -X POST http://127.0.0.1:3210/api/knowledge/reindex
```

**期望：** 一个 JSON 错误，其 `code` 字段的值是 `origin_missing`。

> ⚠️ **这条命令故意不带任何请求头。** 不要"好心"给它加上 `Origin` 头——
> **那个缺失正是它要测的东西**。带上 Origin 头会让这条测试失效。

### 5.3 API 拒绝没有 token 的请求

```
curl -s http://127.0.0.1:8000/api/runs
```

**期望：** 响应体里的 `code` 字段是 `unauthorized`。

> token 只存在于仪表盘的**服务端进程**里；浏览器从不持有它。
> 这条命令同样**故意不带任何请求头**。

### 5.4 安装包里不含任何凭据

```
dir /s /b "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\.env*"
```

**期望：** 命令输出 ***File Not Found***（这是 cmd.exe 自己的英文输出，保持英文比对）。

> 操作员 token 是在**你的机器上**首次启动时生成的，写在 `%APPDATA%\OpsPilot\opspilot.env`。
> **同一份安装文件装到两台机器上，会得到两个不同的 token。**
> 删掉那个文件再启动应用，会生成一个新的。

---

## 6. 退出，并检查它留下了什么

关闭窗口。

**✅ 成功的样子：** 窗口关闭，容器停止，**而你的数据还在**。

```
docker ps --filter name=opspilot-
```

**期望：没有输出。**（注意 `opspilot-` 末尾那个**连字符**，它是名称前缀过滤器。）

然后：

```
docker volume ls | findstr opspilot
```

**期望：恰好两个卷**——`opspilot_postgres-data` 和 `opspilot_mcp-data`。

> 这两个卷**本来就该留着**：退出**不能**删掉你的 run、审批记录或退款账本。
> **再启动一次应用，第 3 步那个 run 应该还在。**

**❌ 失败：** 容器还在列表里 → 应用没有停掉它自己启动的东西。

### ⚠️ 一个**不是失败**的情况

如果应用启动时后端**已经在跑**（你手动起的），那么退出后**它会继续跑**。
这是**刻意的**——应用只停它自己启动的东西，所以它不会杀掉你正在用的栈。
它的日志会说明这一点：

```
Leaving the backend running: this app did not start it.
```

**在这里仅仅关窗就是预期行为，不是 bug。**

---

## 7. 如果要报告问题

有东西失败时，这四样让问题可诊断：

1. **你看到的原文**——**整段窗口日志，不要转述**。
2. 下面两条命令的输出：

   ```
   docker ps -a --filter name=opspilot-
   docker compose -f "%LOCALAPPDATA%\Programs\OpsPilot\resources\stack\docker-compose.yml" logs --tail 100
   ```

3. Windows 版本、Docker Desktop 版本（`docker version`）。
4. **首次启动确实是首次，还是重复启动。**

---

## 必须原样保留的字面值

翻译或转述时，下面这些**一个字符都不能变**——它们是你屏幕上要比对的东西：

`x64` · `~6 GB` · `5–15 minutes` · `1–15 min` · `~1 s` · 约 10 秒 · 一分钟内 · 几秒内
端口 **8000**（后端 API）· 端口 **3210**（仪表盘）· `127.0.0.1`（**绝不写 `localhost`**）
`17 seen` / `17 indexed` / `98 chunks` · `$129.00 USD` · `TX-88219` · `HIGH_RISK_WRITE`
`HTTP 409` · `HTTP 200` · `/runs/<uuid>` · `<approval-id>`
`opspilot_postgres-data` · `opspilot_mcp-data` · `name=opspilot-` · `--tail 100`
`%LOCALAPPDATA%\Programs\OpsPilot` · `%APPDATA%\OpsPilot\opspilot.env` · `resources\stack`
`OPSPILOT_OPERATOR_TOKEN` · `:?` · `NUL`

---

## 这份文档**没有**声称的事

安装包的首次启动**在你机器上从源码构建** API 和 worker 镜像。
这是"不把镜像发布到 registry"的**刻意代价**，也是首次启动要几分钟的原因。
之后都很快，而且**运行时不会从 OpsPilot 下载任何东西**。

---

## 给执行者的记录表（建议填写）

| 步骤 | 结果 | 备注 |
|---|---|---|
| 0 环境（Docker 已启动） | ☐ 通过 ☐ 失败 | |
| 1 安装 | ☐ 通过 ☐ 失败 | 图标：☐ 蓝方块（新）　☐ 黑圆白三角（**缓存** → 重建缓存） |
| 2 启动到仪表盘 | ☐ 通过 ☐ 失败 | 首次启动耗时：____ 分钟 |
| 3.1 索引 17/17/98 | ☐ 通过 ☐ 失败 | 实际数字：____ |
| 3.2 提交工单 | ☐ 通过 ☐ 失败 | run id：____ |
| 3.3 **停在审批门** | ☐ 通过 ☐ 失败 | **若直接 completed = 安全门退化** |
| 3.4 批准 → 退款一次 | ☐ 通过 ☐ 失败 | |
| 3.5 再次批准 → 409 | ☐ 通过 ☐ 失败 | |
| 4 `/ready` 返回 200 + ok | ☐ 通过 ☐ 失败 | |
| 5.1 端口只绑 loopback | ☐ 通过 ☐ 失败 | |
| 5.2 跨源写被拒 | ☐ 通过 ☐ 失败 | |
| 5.3 无 token 被拒 | ☐ 通过 ☐ 失败 | |
| 5.4 包内无凭据 | ☐ 通过 ☐ 失败 | |
| 6 退出后容器停、卷留下 | ☐ 通过 ☐ 失败 | |
| 6b 重启后 run 还在 | ☐ 通过 ☐ 失败 | |

**执行人：** ______________  **日期：** ____________
**Windows 版本：** ______________  **Docker 版本：** ______________
**安装包 SHA256（前 16 位）：** ______________
