[中文](README.md) | [English](README.en.md)

<p align="center">
  <img src="assets/app.png" width="88" alt="Token Plan Monitor 图标">
</p>

<h1 align="center">Token Plan 用量监控</h1>

<p align="center">
  <strong>套餐余量，一眼看清。</strong><br>
  独立的 Windows 托盘 / macOS 菜单栏小工具，随时查看订阅套餐在 5 小时、每周、每月三个窗口<strong>还剩多少容量</strong>。
</p>

<p align="center">
  <a href="https://zzcx2002.github.io/token-plan-monitor/">产品官网</a> ·
  <a href="#快速下载">快速下载</a> ·
  <a href="#安装">安装说明</a> ·
  <a href="#隐私与安全边界">隐私与安全</a> ·
  <a href="https://github.com/Zzcx2002/token-plan-monitor/releases">Releases</a>
</p>

<p align="center">
  <strong>2</strong> 家服务商 · <strong>3</strong> 个额度窗口 · <strong>0</strong> 遥测与用量上传
</p>

| 桌面卡片 · 浅色 | 桌面卡片 · 深色 |
| --- | --- |
| <img src="docs/images/card-ark.png" width="380" alt="浅色下的桌面卡片"> | <img src="docs/images/card-ark-dark.png" width="380" alt="深色下的桌面卡片"> |

> 截图来自离线界面预览，数值是假数据；渲染不含窗口外阴影与桌面背景模糊，实际观感以真机为准。

当前支持两个服务商——**火山方舟**（Agent Plan / Coding Plan）与**阿里云百炼**（Token Plan 个人版 / Coding Plan）。

## 快速下载

| 平台 | 版本 | 下载 | 系统要求 |
| --- | --- | --- | --- |
| macOS | v0.2.1 | [TokenPlanMonitor-0.2.1-arm64.dmg](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.2.1/TokenPlanMonitor-0.2.1-arm64.dmg) | macOS 14+ · Apple Silicon (arm64) |
| Windows | v0.3.0 | [TokenPlanMonitor-0.3.0-win64.zip](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.3.0/TokenPlanMonitor-0.3.0-win64.zip) | Windows 10 / 11 · 64 位 |

完整安装步骤、SHA-256 校验与 Gatekeeper / SmartScreen 首次打开说明见下方[安装](#安装)；同样的下载入口也在[产品官网下载区](https://zzcx2002.github.io/token-plan-monitor/#download)。Windows 还可以[从源码运行](#从源码运行)。

## 目录

- [快速下载](#快速下载)
- [它能做什么 / 不做什么](#它能做什么--不做什么)
- [支持范围](#支持范围)
- [安装](#安装)
- [首次配置：需要哪套凭据](#首次配置需要哪套凭据)
- [开始使用](#开始使用)
- [外观](#外观)
- [接入 Claude Code](#接入-claude-code)
- [文件和凭据在哪里，怎么彻底删除](#文件和凭据在哪里怎么彻底删除)
- [隐私与安全边界](#隐私与安全边界)
- [常见问题](#常见问题)
- [许可与致谢](#许可与致谢)
- [开发者](#开发者)

## 它能做什么 / 不做什么

**做**：

- **额度就在眼前**：后台按间隔（默认 15 分钟，可设 5–1440 分钟）查询官方接口；菜单栏 / 托盘悬停看摘要，点开看三个窗口的剩余百分比、进度条和重置时间；桌面上放一张可拖动、可调整大小的常驻卡片。接口提供绝对额度时一并显示（火山方舟 Agent Plan 的 AFP、百炼 Coding Plan 的额度）。
- **本地历史与提前提醒**：每次成功查询追加进本机历史，据此估算消耗速率与预计耗尽时刻；任一窗口剩余低于可配置阈值（默认 10%）时发一次系统通知（macOS 通知中心 / Windows 托盘提示），窗口重置或剩余回升后自动重新启用。查询失败时保留上次成功数据并明确标记过期。所有趋势与耗尽时刻都是基于本地记录的估算。
- **可选接入 Claude Code**：选好服务商和模型后，逐项确认再合并写入本地配置，并保留时间戳备份。写入会在本地配置中留下明文推理 Key，完整代价与权限说明见[接入 Claude Code](#接入-claude-code)。

**不做**：

- 不把 AFP 或额度换算成 token，也不根据请求日志猜测容量。接口只返回百分比时，就只显示百分比，不虚构绝对数字。
- 不读取、不修改 CC Switch 或百炼 CLI 的配置和数据库（协议实现参考二者，但不依赖它们）。
- 不上传任何用量数据到第三方，无遥测。程序只访问固定的官方 HTTPS 地址，不允许自定义脚本或任意请求地址。
- 不设置开机自启。

## 支持范围

| | macOS | Windows |
| --- | --- | --- |
| 获取方式 | GitHub Release 的 DMG（最新 v0.2.1，说明见下） | GitHub Release 的便携 zip（v0.3.0 起），也可源码运行 |
| 系统要求 | macOS 14 及以上，仅 Apple Silicon（arm64） | Windows 10 / 11 64 位（exe）；源码运行需 Python 3.11+ |
| 服务商 | 火山方舟 + 阿里云百炼，设置中可切换 | 相同，设置中可切换 |
| 桌面卡片 | 三列（5 小时 / 每周 / 每月），可切换悬浮卡片或桌面组件 | 三列（5 小时 / 每周 / 每月），可拖动、缩放、置顶或隐藏 |
| 接入 Claude Code | 有（设置窗口底部） | 有（设置窗口底部） |
| 低额度告警 | 系统通知中心，阈值可配置，点击通知打开详情 | 托盘气泡提示，阈值可配置；气泡点击受限（见告警一节） |
| 用量历史与趋势 | 有（详情窗趋势行 + 用量历史窗口） | 有（详情窗趋势行 + 用量历史窗口） |
| 外观 | 跟随系统浅色/深色，无应用内开关 | 浅色 / 深色 / 跟随系统，应用内可切换 |
| 凭据存放 | macOS 登录钥匙串 | Windows 凭据管理器 |

百炼 Token Plan 个人版按订阅月计量，接口只返回各窗口的使用比例与重置时间，没有绝对额度；百炼新人免费额度（按模型的免费 token）和 Token Plan 团队版暂不支持。

## 安装

### macOS

从 [GitHub Release v0.2.1](https://github.com/Zzcx2002/token-plan-monitor/releases/tag/v0.2.1) 下载 [TokenPlanMonitor-0.2.1-arm64.dmg](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.2.1/TokenPlanMonitor-0.2.1-arm64.dmg)，打开后把 `TokenPlanMonitor.app` 拖进 Applications，双击启动。应用自带 Python 运行时，不需要另外安装任何东西。

想确认下载完整，把 DMG 和 [SHA-256 校验文件](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.2.1/TokenPlanMonitor-0.2.1-arm64.dmg.sha256) 放在同一目录后执行：

```sh
shasum -a 256 -c TokenPlanMonitor-0.2.1-arm64.dmg.sha256
```

首版只有本地 ad-hoc 签名，没有 Developer ID 签名，也没有 Apple 公证。从网络下载的副本首次打开可能被 Gatekeeper 阻止；确认来源后，在系统设置「隐私与安全性」里点「仍要打开」，再确认「打开」。**不要**关闭系统安全保护，也不要删除应用的 quarantine 属性。

> v0.3.0 的变化集中在 Windows 侧与两端共享代码，**没有重新构建 macOS DMG**；macOS 用户继续使用上面的 v0.2.1 即可，功能不缺失。

### Windows

#### 便携版（推荐）

从 [GitHub Release v0.3.0](https://github.com/Zzcx2002/token-plan-monitor/releases/tag/v0.3.0) 下载 [TokenPlanMonitor-0.3.0-win64.zip](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.3.0/TokenPlanMonitor-0.3.0-win64.zip) 和同名 [.sha256 校验文件](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.3.0/TokenPlanMonitor-0.3.0-win64.zip.sha256)，放到同一目录。先校验完整性，PowerShell 里执行：

```powershell
$expected = (Get-Content .\TokenPlanMonitor-0.3.0-win64.zip.sha256 -TotalCount 1).Split(" ")[0].ToLower()
$actual = (Get-FileHash .\TokenPlanMonitor-0.3.0-win64.zip -Algorithm SHA256).Hash.ToLower()
if ($expected -eq $actual) { "OK" } else { "MISMATCH" }
```

装了 Git Bash 或 coreutils 时也可以 `sha256sum -c TokenPlanMonitor-0.3.0-win64.zip.sha256`，输出 `OK` 才算通过。

校验通过后把 zip 解压到**当前用户有权限的目录**（如 `%LOCALAPPDATA%\Programs` 或文档目录；不要放进 `C:\Program Files` 这类受保护位置），双击其中的 `TokenPlanMonitor.exe` 启动。应用是便携的：配置、历史和日志都写在 exe 旁边的 `data\` 与 `logs\` 目录，整个文件夹搬走即完成迁移，删掉文件夹即卸载（凭据管理器里的条目需另行手动删除，见下文）。不需要管理员权限，也不会设置开机自启。

exe 没有购买代码签名证书，首次运行可能弹出 SmartScreen「Windows 已保护你的电脑」。确认下载自本仓库 Release 且 sha256 校验通过后，点「更多信息 → 仍要运行」。

程序用 Windows 命名互斥锁防止重复启动，重复双击不会开出第二个托盘实例。

#### 从源码运行

也可以直接跑源码。把项目放到你喜欢的目录（下面用 `<项目目录>` 指代），在终端里：

```bat
cd /d <项目目录>
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\pythonw.exe -m token_plan_monitor
```

装好依赖后执行一次快捷方式脚本，桌面和当前用户开始菜单会各出现一个「火山方舟用量监控」，之后双击即可启动，不会闪控制台窗口。移动项目或重建 `.venv` 后要重新执行：

```bat
powershell -NoProfile -File .\scripts\New-AppShortcuts.ps1
```

源码运行时数据写在 `<项目目录>\data\`（与便携版的 exe 旁目录规则相同，见下文）。

## 首次配置：需要哪套凭据

首次启动会自动打开设置窗口。**两个服务商都要控制面凭据，不是推理接口的 Key**：

| 服务商 | 需要 | 从哪拿 |
| --- | --- | --- |
| 火山方舟 | AccessKey ID + Secret AccessKey | 火山引擎控制台的用户级 AK/SK，用于签名调用 `open.volcengineapi.com` |
| 阿里云百炼 | AccessKey ID + Secret AccessKey | 阿里云账号的 AK/SK；RAM 子账号需授予 `AliyunTokenPlanReadOnlyAccess` 与 `AliyunBSSReadOnlyAccess` 只读策略 |

容易踩的坑：

- `sk-...`（百炼还有 `sk-sp-...` 套餐 Key）是**推理 API Key**，不能当 AK/SK 用。只有要用「接入 Claude Code」时才需要额外填推理 Key。
- Region：火山方舟默认 `cn-beijing`；百炼只接受 `cn-beijing` 或 `ap-southeast-1`，且 Token Plan 个人版目前仅在华北2（北京）提供。
- 本机系统时间要准确，签名依赖它。
- Secret 输入框留空表示保留已保存的值，界面上不会回显原 Secret。
- 建议使用只具备用量查询所需权限的子账号凭据。

填完点「保存设置」。程序只访问固定的官方 HTTPS 地址，访问令牌只在内存里持有，不落盘、不写日志。

## 开始使用

### macOS 菜单栏

| 操作 | 结果 |
| --- | --- |
| 左键单击图标 | 打开用量详情窗口 |
| 右键 / Control-单击 | 菜单：三个窗口的剩余与已用百分比、最近成功更新时间，以及打开用量详情 / 用量历史 / 立即刷新 / 桌面卡片 / 显示模式 / 卡片置顶 / 设置… / 退出 |
| 悬停 | 摘要提示，只列 5 小时和每周 |

图标右下角的圆形徽标会随状态改变颜色和标记：正常、警告（过期或额度偏低）、错误、未配置。关闭窗口只是隐藏，进程还在；要结束程序请用菜单里的「退出」（⌘Q）。

### macOS 桌面卡片

| 操作 | 结果 |
| --- | --- |
| 普通单击 | **不**打开窗口（与 Windows 一致） |
| 拖动空白处 | 移动卡片 |
| 拖动边缘 / 角落 | 调整大小 |
| 右键 / Control-单击 | 操作菜单（没有「打开用量详情」，详情只从菜单栏打开） |
| 右下角圆形箭头 | 立即刷新；刷新中禁用。只有可见按钮区域响应点击，透明外圈仍由卡片处理 |
| Esc | 隐藏卡片，并在重启后保持隐藏 |

卡片以三列显示 5 小时 / 每周 / 每月剩余量，标出最低项（并列时都标），剩余低于告警阈值（默认 10%，可在设置中改）显示「额度偏低」。位置、尺寸、可见性和显示模式重启后恢复；卡片按显示器可用区域定位，支持负坐标布局，屏幕拔掉后会自动校正，不会霸占其他应用的全屏空间。

「显示模式」可在**悬浮卡片**和**桌面组件**之间切换。桌面组件落在所有普通桌面的桌面层，其他应用窗口可以盖住它，全屏应用中不显示；仍可拖动、缩放和刷新。桌面模式下「卡片置顶」不可用，切回悬浮卡片会恢复你之前的置顶选择。退出应用后组件随之消失。

### 用量详情

<img src="docs/images/details-ark.png" width="330" alt="用量详情窗口：三个周期的剩余容量、进度条与重置时间">

数字和进度条统一表示**剩余**容量，已用百分比是辅助说明；有绝对额度的套餐额外显示绝对值（火山方舟为 AFP，百炼 Coding Plan 为额度，百炼 Token Plan 只有百分比）。顶部是状态、最近成功更新和错误原因，底部操作栏固定可见，中间容量区可滚动并随窗口宽度重排；后台刷新和缩放窗口时保持滚动位置与键盘焦点。

每个窗口的重置时间下方还有一行趋势说明：由本机历史算出的消耗速率（约 x%/小时，有绝对额度时附绝对速率）与预计耗尽时刻；预计耗尽早于重置时用警告色标出。样本不足时明说「记录不足，持续使用后可预测」，近期没有消耗时显示「近期无消耗」——所有预测都带「预计/约」字样，只百分比的套餐不会凭空出现绝对数字。

### Windows

单击托盘图标打开用量弹窗（悬停看摘要，右键看三个窗口的详情与操作），弹窗里「展开详情」看已用百分比和绝对额度。桌面卡片**空白处单击不再开窗**（与 macOS 一致）：打开用量弹窗的入口是托盘单击和卡片右键菜单的「打开详细用量」；右下角圆形箭头按钮立即刷新，刷新进行中自动禁用。悬停卡片看摘要，右键看菜单，拖动空白处移动，边缘和四角可缩放（拖动不会误触刷新或开窗）。卡片默认置顶，可取消置顶或隐藏，用托盘菜单「桌面卡片」恢复。

### 用量历史与低额度告警

- **历史**：每次成功查询追加一行到 `history/<服务商>.jsonl`（见下文数据目录），只含接口已报告的百分比/额度数字和 AccessKey ID 的单向指纹，不含任何凭据。数值没变化时 15 分钟内只记一条，有变化立即记录；保留 90 天、单文件上限 1 万行，超出自动轮转。切换服务商或账号**不清历史**（趋势只按当前账号和当前窗口周期计算），想删除直接删文件。
- **历史窗口**：托盘/菜单栏菜单「用量历史」打开独立窗口，按当前快照里实际存在的窗口各画一张已用%折线/面积图（固定 0–100 轴）。顶部一行时间范围选择器（24小时 / 7天 / 30天 / 90天，默认 24小时，90 天即保留上限）同步作用于全部图表。每个窗口周期一条独立线段，**重置处断开不连线**，重置位置有细线与「重置」标注（周期过密时自动省略标注）；告警阈值画成虚线参考线（剩余低于阈值即告警的位置）。悬停显示十字线与提示（数值领先：已用%、时间，绝对值仅在接口报告过时显示）；每张图下有「数据列表」披露表格，不悬停也能读到每个采样桶的数值。只展示**当前账号**的历史；只报比例的套餐绝不显示编造的绝对额度；记录不足时如实说明。启动时从磁盘播种历史，刷新失败保留上次数据。两端图表由同一套布局数据驱动：macOS 用原生绘图，Windows 用 Tk Canvas 渲染，视觉不是像素级一致，但数据、断点、阈值线与交互行为相同。
- **告警**：设置窗「低额度告警」组可开关系统通知、设阈值（1–99%）、可选「窗口重置时提醒」。任一窗口剩余下穿阈值时通知一次；剩余回到阈值以上或窗口重置后自动重新启用，不会重复轰炸。重启应用后若仍低于阈值会再提醒一次。
- **通知渠道**：macOS 走通知中心（首次会请求授权；被拒绝时降级为旧版通知 API，再不可用则在设置窗如实说明），点击通知打开用量详情；低额度通知为**时效性通知**，专注/勿扰模式下也可突破（需系统「允许时效性通知」开启，默认开）。ad-hoc 签名重新构建后系统可能要求重新授权。Windows 走托盘气泡提示；受 pystray 0.19.5 的能力限制，**点击气泡不会打开应用**（该库的 Windows 通知没有点击回调），请单击或右键托盘图标打开用量弹窗与菜单。源码运行 / `--preview` 模式不发系统通知，设置窗会说明原因。

<img src="docs/images/history-ark.png" width="330" alt="用量历史窗口：24小时范围下 5 小时窗口的周期折线、重置断点与告警虚线（离线预览渲染、假数据）">

## 外观

- **macOS**：用量窗口、设置窗口和桌面卡片跟随系统浅色/深色，没有应用内开关。颜色全部使用系统动态色或按外观配对的自定色，切换系统外观时已打开的窗口立即重绘。强调色、按钮高亮和焦点环跟随系统「强调色」；卡片圆角、阴影和背景材质是原生的，并遵循系统「辅助功能 → 显示 → 降低透明度」。
- **Windows**：默认跟随系统应用主题，每 2 秒检查一次；弹窗右上角 ◐ 菜单和设置里的三段选择器可立即选浅色 / 深色 / 跟随系统，选择实时预览，取消或按 Esc 会回滚。窗口默认使用**实色不透明材质**，文字保持 ClearType 锐利渲染并保留系统阴影；设置里可切换为**亚克力材质**（仅 Windows 11 且系统「透明效果」开启时可选，Windows 10 下该选项禁用并说明原因）。亚克力会透出窗口背后的磨砂内容，但受 Tk 分层窗口限制，Windows 会对分层窗口关闭 ClearType，文字抗锯齿比实色模式稍软——设置页对此有明确说明。系统关闭「透明效果」时亚克力自动回落实色。窗口圆角在 Windows 11 上由系统 DWM 绘制（原生 8px 抗锯齿圆角与阴影），Windows 10 回退为 GDI 圆角。

设置正文可滚动，Tab / 回车保存 / Esc 取消，以及 ⌘C、⌘V 等原生文本编辑快捷键都可用。校验失败时保留你输入的内容并聚焦到出错的字段；鉴权或配置错误优先提示「检查凭据」，网络错误优先提示「重试」。断网时保留上次成功数据并标记过期；从睡眠唤醒后如果已到期，只会合并刷新一次。

## 接入 Claude Code

选好服务商后可以顺带选模型，一键把接入信息写进 Claude Code 的本地配置，省掉手抄 Base URL、手贴 Key、手改模型名。入口在设置窗口底部，桌面卡片上没有。macOS 与 Windows 的设置项、写入规则和确认流程完全一致。

<img src="docs/images/settings-claude-code.png" width="330" alt="设置窗口：顶部服务商下拉与底部 Claude Code 接入区">

**写到哪里**：默认 `~/.claude/settings.json`（Windows 上是 `%USERPROFILE%\.claude\settings.json`），并遵循 `CLAUDE_CONFIG_DIR`。确认弹窗会显示解析后的绝对路径——从访达 / Windows 资源管理器启动的应用不继承终端里的环境变量，所以以弹窗显示的路径为准。另外会把 `~/.claude.json` 的 `hasCompletedOnboarding` 置为 true 以跳过首次引导；该文件若无法解析则**跳过**，绝不重建（它还保存项目历史）。

**改哪些键**，其余一律保留：

| 键 | 行为 |
| --- | --- |
| `env.ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL` | 总写 |
| `env.ANTHROPIC_API_KEY` | 存在则删除（它会遮蔽 `ANTHROPIC_AUTH_TOKEN`） |
| 顶层 `model` 指向的别名那一档（`opus` → `ANTHROPIC_DEFAULT_OPUS_MODEL`，sonnet/haiku 同理） | 改为所选模型 |
| 其他档位 | 原本为空才填；已有值不动 |
| `env.CLAUDE_CODE_SUBAGENT_MODEL`（可选） | 不选：原本为空才填、已有值不动；选择后：写入所选模型 |
| `env.CLAUDE_CODE_AUTO_MODE_MODEL`（可选） | 选择了才写；不选则完全不碰（不写、不填、不删） |
| 顶层 `model` | 改为所选的具体模型名 |

为什么要动档位：Claude Code 里 `model: "opus"` 只是别名，真正选出模型的是 `ANTHROPIC_DEFAULT_OPUS_MODEL`。只写 `ANTHROPIC_MODEL` 对这种配置基本无效。副作用是顶层 `model` 变成具体模型名后，`/model opus` 走的是 `ANTHROPIC_DEFAULT_OPUS_MODEL`（现在等于所选模型），与你原来纯靠别名的分层用法不同。

**安全**：每次写入前先把原文件备份为 `settings.json.bak.<时间戳>`——备份里是上一版的明文 Key，所以它和原文件一样收紧为仅所有者可读写（macOS 上是 `0600`，并顺手把此前遗留的全局可读备份一起收紧），只合并不重建；文件不是有效 JSON 或 `env` 不是对象时**放弃写入且不动原文件**；重复点击结果一致。点击后先弹出逐条变更清单，确认才写。Windows 上 Python 的 `chmod 0600` 只能切换只读属性，真正的访问控制由文件所在目录的 NTFS ACL 继承决定——`%USERPROFILE%\.claude` 默认仅本用户可访问；**便携版请解压在用户私有目录，不要放在所有人可写的共享文件夹**，否则明文文件会继承过宽的权限。

**要知道的代价**：

- 推理 Key 平时只存系统凭据库（macOS 钥匙串 / Windows 凭据管理器），但 Claude Code 通过环境变量消费它，所以写入后的 `settings.json` 里 `ANTHROPIC_AUTH_TOKEN` 是明文（macOS 权限 `0600`；Windows 上依赖上一段说明的 NTFS ACL；以本账户运行的进程仍能读到），它的备份同样是明文。确认弹窗会说明这一点。
- 重写 `settings.json` 会丢掉 `//`、`/* */` 注释和键的顺序（值不受影响）；`~/.claude.json` 会被整体重新格式化并同样收紧权限，不存在时会被创建。
- 官方只承诺 permissions / hooks / apiKeyHelper 一类编辑会热重载，没点名 `env` 和 `model`，所以**请重开一个 Claude Code 会话**确认生效。

**Base URL 与模型**：留空时用服务商默认地址，而拼进这个地址的 Region 会先按「格式 + 该服务商支持的地域」校验，与保存凭据用的是同一条规则。带账号密码的地址（`https://user:pass@host`）一律拒绝：`@` 之前的部分不是主机名，看起来正规的域名可能被悄悄替换成别的主机，而密钥正要发往那里。百炼默认是已验证的 `https://token-plan.<region>.maas.aliyuncs.com/apps/anthropic`，粘贴 OpenAI 兼容地址（`/compatible-mode/v1`）会被自动改写，填你自己域名下的代理地址可以原样保留；百炼提供一份模型清单（默认 `auto`），自定义模型 ID 只能包含字母、数字和 `. _ : / + -`。**火山方舟没有可验证的 Anthropic 兼容端点**（官方只给 `/api/v3` 的 OpenAI 兼容地址，且说明 BaseUrl 需由使用者提供），所以该字段留空、需要你自己填，界面会标 ⚠ 未经验证，模型也只能手填 ID。

**Auto mode 分类模型（可选）**：Claude Code 的 auto mode 用一个轻量模型（分类器）判断每条命令是否安全。设置里默认是「不设置」，此时 `CLAUDE_CODE_AUTO_MODE_MODEL` 完全不写入、也不改动（你在文件里手写的值原样保留）。选择后会随一次写入一并更新，确认清单会逐条说明。**建议选轻量模型**（如 `qwen3.6-flash`）：分类模型偏重时判断容易超时，auto mode 会因此拦下工具调用、要求重试（实测该键在 Claude Code 2.1.322 生效；更早版本可能忽略它，忽略时不影响其他配置）。

**Subagent 模型（可选）**：`CLAUDE_CODE_SUBAGENT_MODEL` 决定子代理（subagent）默认用哪个模型。设置里默认是「默认」，沿用既有规则：文件里已有值原样保留、原本为空则回填所选主模型。显式选择某个模型（或填自定义 ID）后，切换/写入时会把它写进该键、覆盖已有值；确认清单会单独列出一行。想让它跟随主模型就保持「默认」。

两个服务商之间来回切换时，各自填的模型与 Base URL 会被记住；推理 Key 只在本次运行的内存里留草稿、不回显，但字段标题会提示「已保存，留空保持不变」。取值优先级是：本次填写 → 磁盘上的真实配置 → 该服务商上次保存的偏好 → 服务商默认。把磁盘配置排在已存偏好之前，是为了你手工改过的接入地址不会被下一次写入悄悄还原成旧值。

## 文件和凭据在哪里，怎么彻底删除

### macOS

```text
~/Library/Application Support/TokenPlanMonitor/
  config.json         服务商、Region、刷新间隔和告警设置
  claude.json         Claude Code 接入选择（模型 / Base URL，不含 Key）
  snapshot.json       最近成功的用量缓存
  history/<服务商>.jsonl   追加式用量历史（百分比/额度 + AK 指纹，90 天 / 1 万行轮转）
  widget.json         卡片位置、尺寸、可见性、置顶状态和桌面组件模式
  instance-*.lock     单实例锁；退出后保留文件，由系统释放锁
~/Library/Logs/TokenPlanMonitor/app.log     脱敏运行日志
```

AK/SK 只存 macOS 登录钥匙串，service 为 `TokenPlanMonitor`，每个服务商各用独立的一对 account：

| 用途 | account |
| --- | --- |
| 火山方舟控制面 | `volcengine_access_key_id` / `volcengine_secret_access_key` |
| 百炼控制面 | `bailian_access_key_id` / `bailian_secret_access_key` |
| 接入 Claude Code 的推理 Key | `volcengine_inference_api_key` / `bailian_inference_api_key` |

切换服务商不会读取或覆盖另一家的凭据，控制面 AK/SK 与推理 Key 互不复用。拒绝钥匙串访问会直接报错，**不会**降级成明文文件。

彻底清除：在「钥匙串访问」里只删除上面这几条 account，删掉上面的数据目录和日志目录，并删掉接入 Claude Code 时写到 `~/.claude` 的内容——`settings.json` 里的明文 Key 以及它的每一份 `settings.json.bak.*` 备份。更换服务商或凭据时旧快照会被清除（历史记录按服务商保留，可手动删 `history/` 下对应文件），不会自动迁移 Windows 的配置。

### Windows

便携版的可写数据全部落在 exe 旁边，源码运行时落在项目目录下，下面用 `<根目录>` 统一指代：

```text
<根目录>\
  data\config.json              服务商、Region、刷新间隔、告警设置等非敏感配置
  data\claude.json              Claude Code 接入选择（模型 / Base URL，不含 Key）
  data\models.json              服务商模型清单缓存
  data\snapshot.json            最近一次成功用量缓存
  data\history\<服务商>.jsonl   追加式用量历史（百分比/额度 + AK 指纹，90 天 / 1 万行轮转）
  data\widget.json              卡片位置、可见性和置顶状态
  data\window_geometry.json     各窗口与卡片的尺寸
  data\appearance.json          外观选择：light / dark / system
  logs\app.log                  脱敏运行日志
```

源码运行的根目录下还有 `assets\app.ico`（快捷方式与窗口图标）；便携版的图标是只读资源，随 exe 打包在应用内部，不进数据目录。单实例靠 Windows 命名互斥锁实现，不产生锁文件。

AK/SK 与推理 Key 只存 Windows 凭据管理器，service 为 `TokenPlanMonitor`，每个服务商各用独立的 account：

| 用途 | account |
| --- | --- |
| 火山方舟控制面 | `volcengine_access_key_id` / `volcengine_secret_access_key` |
| 百炼控制面 | `bailian_access_key_id` / `bailian_secret_access_key` |
| 接入 Claude Code 的推理 Key | `volcengine_inference_api_key` / `bailian_inference_api_key` |

切换服务商不会读取或覆盖另一家的凭据，控制面 AK/SK 与推理 Key 互不复用。程序不会自动删除它们；不想保留时到「凭据管理器 → Windows 凭据」手动删除上面这几条，再删掉便携文件夹（或项目目录）。配置或缓存损坏时会用安全默认值或空状态，不会导致启动崩溃。

## 隐私与安全边界

- 只访问固定的官方 HTTPS 地址（火山引擎 OpenAPI、阿里云 Model Studio 与百炼控制台网关），不允许自定义脚本或任意请求地址，无遥测。
- Secret 只进系统凭据库，不进配置文件、缓存或日志；百炼的控制台访问令牌只在内存中持有。
- 用量数据只在本机显示和缓存，不上传任何第三方。历史文件只含接口已报告的百分比/额度数字与 AccessKey ID 的单向指纹（sha256 前 12 位），不含 Secret、推理 Key 或任何可还原凭据的内容。
- 界面是本地读取，不需要任何写权限——除了你主动点「写入 Claude 配置」。

## 常见问题

- **提示鉴权失败**：确认用的是账号 AK/SK 而不是 `sk-...` 推理 Key，检查 IAM/RAM 权限和本机系统时间。
- **显示未找到套餐**：确认账号确实订阅了对应套餐（火山方舟 Agent Plan 或 Coding Plan；百炼 Token Plan 个人版或 Coding Plan）。
- **数据可能过期**：检查网络、代理或服务状态；界面显示的是最近一次成功数据，不会假装是实时的。
- **没有绝对额度 / 没有 token 总数**：接口只返回百分比时，程序不会虚构绝对容量。百炼 Token Plan 个人版就是只有百分比。
- **首次打开被 macOS 阻止**：见上面 Gatekeeper 说明，用「仍要打开」，不要关掉系统安全保护。
- **界面只有中文**：目前确实如此，屏上文案尚未做英文本地化。[English README](README.en.md) 里有一份中英对照表可以帮你认字。
- **想换账号**：改凭据后旧账号的内存快照和缓存会被清除，避免串号展示。

## 许可与致谢

MIT 许可，见 [LICENSE](LICENSE)。签名与响应解析参考 [CC Switch](https://github.com/farion1231/cc-switch)（`src-tauri/src/services/coding_plan.rs`，调研固定 revision `ef7b77dee255e2c3b9a594de454eae21476ca687`）与 [阿里云百炼 CLI](https://github.com/modelstudioai/cli)（`packages/core/src/client/acs.ts`、`packages/core/src/console/gateway.ts`、`packages/core/src/console/models.ts`、`packages/usage/`，调研固定 revision `fa8330c27862635f7d769c5488c33a06a47063d5`），但不依赖二者。两者的版权与许可文本（MIT / Apache-2.0）以及随应用打包的第三方运行时许可见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。

## 开发者

源码运行、打包 macOS DMG 与 Windows 便携包、离线界面预览与验收、测试与实时验证：[docs/development.md](docs/development.md)。

### 产品网站

双语产品官网（[中文](https://zzcx2002.github.io/token-plan-monitor/) / [English](https://zzcx2002.github.io/token-plan-monitor/en/)）是 `site/` 目录下的 Astro 静态站点，通过 GitHub Actions 部署到 GitHub Pages（工作流：[.github/workflows/pages.yml](.github/workflows/pages.yml)）。本地开发、图标与 OG 图生成、发布数据更新和浏览器验证见 [site/README.md](site/README.md)；网站不使用分析脚本、外部字体或运行时客户端 JavaScript。
