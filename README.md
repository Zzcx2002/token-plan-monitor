# Token Plan 用量监控（火山方舟 · 阿里云百炼）

一个独立的 Windows 托盘 / macOS 菜单栏工具，用于查看订阅套餐的 5 小时、每周和每月容量。当前支持两个服务商：**火山方舟**（Agent Plan / Coding Plan）与**阿里云百炼**（Token Plan 个人版 / Coding Plan）。macOS 版可在设置中切换服务商；Windows 版目前只查询火山方舟。协议实现参考 CC Switch 与阿里云百炼 CLI，但不依赖二者，也不读取或修改其配置和数据库。

## macOS 版

目标系统为 **macOS 14 及以上、Apple Silicon（arm64）**。使用 AppKit 原生菜单栏、详情窗口、设置和桌面卡片，与 Windows 版共用查询及用量规则；默认浅色，不设置开机自启，不显示 Dock 图标。

### 安装和使用

从 [GitHub Release v0.1.0](https://github.com/Zzcx2002/token-plan-monitor/releases/tag/v0.1.0) 下载 [TokenPlanMonitor-0.1.0-arm64.dmg](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.1.0/TokenPlanMonitor-0.1.0-arm64.dmg)，打开后将 `TokenPlanMonitor.app` 拖到 Applications，再双击启动。应用包含 Python 运行时，不需要另外安装依赖。

可同时下载 [SHA-256 校验文件](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.1.0/TokenPlanMonitor-0.1.0-arm64.dmg.sha256)。将 DMG 和校验文件放在同一目录后运行 `shasum -a 256 -c TokenPlanMonitor-0.1.0-arm64.dmg.sha256`，应显示 `OK`。

首版只有本地 ad-hoc 签名，没有 Developer ID 签名和 Apple 公证。对于从网络下载的副本，首次打开可能被 Gatekeeper 阻止；确认来源后，可在系统设置“隐私与安全性”中使用“仍要打开”，再确认“打开”。不要关闭系统安全保护或删除应用的 quarantine 属性。

- 首次启动填写账号 AK/SK、Region 和刷新间隔；Secret 留空保留已保存值，输入框不会显示原 Secret。
- 菜单栏图标左键打开详情，右键或 Control-单击打开三周期用量及操作菜单，悬停显示摘要。
- 卡片普通单击不打开窗口；空白处拖动移动，边缘或角落拖动调整大小，右键或 Control-单击显示操作菜单。默认可见且置顶，可隐藏和恢复，重启恢复位置、尺寸和显示偏好。
- 菜单栏右键菜单和卡片右键菜单的“显示模式”可切换“悬浮卡片”与“桌面组件”。桌面组件显示在所有普通桌面的桌面层，其他应用窗口可盖住它，全屏应用中不显示；仍可拖动、调整大小和刷新。桌面模式下“卡片置顶”不可用，切回悬浮卡片会恢复之前的置顶选择。切换保持位置、尺寸及隐藏状态，重启恢复模式，退出应用后组件随之关闭。
- 卡片使用显示器可用区域定位，支持负坐标布局和屏幕断开后的自动校正；不覆盖其他应用的全屏空间。
- 详情的数字和进度条统一表示剩余容量；已用百分比为辅助说明，提供绝对额度的套餐额外显示已用/总额（火山方舟为 AFP，百炼 Coding Plan 为 Credits 额度；百炼 Token Plan 只返回百分比）。顶部展示状态、最近成功更新及错误，底部操作栏固定可见，中间容量信息可滚动并随窗口宽度重排；后台更新和窗口缩放保持滚动位置及键盘焦点。
- 桌面卡片以三列展示 5 小时、每周、每月剩余量，标记最低项并显示更新时间；剩余低于 10% 时显示“额度偏低”。右下角圆形箭头按钮可立即刷新，只有可见按钮区域响应点击和悬停提示，透明外圈仍由卡片处理；刷新中禁用，最外侧角落用于缩放。卡片右键菜单提供其他操作，主详情窗口仅从菜单栏打开。
- 首次配置与已保存 Secret 使用不同说明，设置提供可展开的凭据帮助；校验失败保留输入并聚焦对应字段。鉴权或配置错误优先“检查凭据”，网络错误优先“重试”。断网保留上次成功数据并标记过期；从睡眠唤醒后到期则合并刷新一次。
- 关闭窗口只隐藏窗口，通过菜单栏“退出”关闭进程。设置支持 Tab、回车保存、Escape 取消以及 Command-C/V 等原生文本编辑快捷键。
- 卡片使用原生圆角、阴影和背景材质，遵循系统“辅助功能 → 显示 → 降低透明度”设置。

### macOS 文件和凭据

```text
~/Library/Application Support/TokenPlanMonitor/
  config.json         服务商、Region 和刷新间隔
  snapshot.json       最近成功的用量缓存
  widget.json         卡片位置、尺寸、可见性、置顶状态和桌面组件模式（Cocoa 点坐标）
  instance-*.lock     单实例锁文件；退出后保留文件，由系统释放锁
~/Library/Logs/TokenPlanMonitor/app.log
```

AK/SK 只存入 macOS 登录钥匙串，service 为 `TokenPlanMonitor`。每个服务商使用各自独立的一对 account：火山方舟为 `volcengine_access_key_id` / `volcengine_secret_access_key`，阿里云百炼为 `bailian_access_key_id` / `bailian_secret_access_key`，切换服务商不会读取或覆盖另一家的凭据。拒绝钥匙串访问会显示错误，不降级到明文文件。更换服务商或凭据后清除旧快照，不自动迁移 Windows 配置。移除应用时，如需清除凭据，请在“钥匙串访问”中仅删除上述条目，再删除上述数据和日志目录。

### 源码运行和构建

在 Apple Silicon Mac 安装 **python.org 官方 Python 3.13 framework 版本**及 Xcode Command Line Tools。py2app 不支持此项目使用 uv 的便携解释器打包；源码运行可使用兼容的 Python 3.11 及以上版本。

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-macos-build.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m token_plan_monitor
```

一键构建：

```sh
.venv/bin/python scripts/build_macos.py
```

脚本替换生成的 `build/`、`dist/TokenPlanMonitor.app` 和 DMG 暂存目录，生成独立应用及 DMG。构建检查所有 Mach-O 文件为 arm64、最低系统版本不高于 14，且动态链接不依赖开发机路径；同时验证项目及第三方许可证资源已入包和本地 ad-hoc 签名。Bundle ID 固定为 `com.zzcx2002.tokenplanmonitor`，应用数据不写入 `.app` 内。

### macOS 离线验证

```sh
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src scripts
.venv/bin/python -m token_plan_monitor.macos.preview --smoke-test
.venv/bin/python -m token_plan_monitor.macos.preview --state stale
```

预览和界面检查使用临时数据目录、内存凭据和假监控，不读取真实钥匙串、不访问云端。状态包括 `agent`、`coding`、`refreshing`、`stale`、`unconfigured`、`error`、长文本 `long`，以及边界值 `limits`、周期缺失 `missing`、最低值并列 `tied`。检查覆盖默认/较小/加宽窗口的内容重排、进度条与无障碍标签、焦点和滚动保持、设置校验与帮助、卡片刷新按钮的单次命令路由、刷新中禁用、无障碍标签、父视图/翻转卡片坐标转换、可见按钮逐边与透明外圈命中，以及按钮/角落缩放分流，以及点击、移动及缩放的按下/拖动/释放事件路径、菜单入口、最小/加宽卡片布局和尺寸重启恢复。检查输出在 `artifacts/macos/`，包括原生视图截图和通过记录；视图截图不包含桌面合成后的完整背景模糊及外部阴影。打包应用检查请为 `--artifacts` 指定绝对路径，避免 py2app 改变工作目录后将截图写入应用资源目录。

打包后也可使用隔离假数据验证：

```sh
dist/TokenPlanMonitor.app/Contents/MacOS/TokenPlanMonitor --smoke-test --artifacts /tmp/token-monitor-bundle-smoke
open dist/TokenPlanMonitor.app --args --preview --state agent
```

实时查询继续要求显式启用：保存真实凭据后，运行 `TOKEN_PLAN_MONITOR_LIVE=1 .venv/bin/python -m pytest -m live -s`。

验收记录与尚需实机复核的场景见 [macOS 验收记录](docs/macos-validation.md)。macOS 14 最低版本声明和二进制检查不能替代 macOS 14 实机运行测试。

## Windows 版

以下启动、快捷方式、原生特效和文件位置说明适用于 Windows；已有 Windows 数据路径及凭据条目保持兼容。

## 功能

- 后台每 15 分钟查询一次，可在设置中调整为 5–1440 分钟。
- macOS 设置顶部可切换服务商（火山方舟 / 阿里云百炼）。各服务商的凭据、Region 和填写说明相互独立；切换会清空上一家的用量缓存并立即按新服务商重新查询。Windows 版暂只查询火山方舟，设置中不提供切换。
- 托盘悬停简要显示 5 小时和每周剩余量；右键菜单直接列出 5 小时、每周、每月的已用/剩余百分比及最近成功更新时间。
- 单击托盘图标打开简洁用量弹窗，查看剩余百分比、进度和重置时间；“展开详情”查看已用百分比与绝对额度；也支持立即刷新、设置和退出。
- 桌面常驻一张可拖动的 macOS 风格圆角迷你卡片，显示状态、5 小时和每周剩余量；悬停看摘要，右键看三窗口详情与操作。卡片尺寸随字体和 DPI 实际测量，避免文字裁切。
- 迷你卡片默认置顶，可从卡片或托盘菜单取消置顶、隐藏和恢复；位置、可见性和置顶状态会随应用重启恢复，不会设置开机自启。
- 桌面卡片、用量弹窗、设置和悬停提示采用统一的 macOS 风格：黑白双主题、少量蓝色强调、无常态描边的圆角输入框与按钮、柔和阴影。卡片表面、剩余进度条和主要按钮采用轻微渐变。Windows 支持时在底板和留白处显示 Acrylic 毛玻璃，正文与容量面板保持不透明，不降低文字透明度。
- 透明效果遵循 Windows“辅助功能 / 视觉效果 → 透明效果”设置。系统关闭透明效果或原生接口不可用时使用当前主题的实心背景；应用不添加窗口外层投影或独立阴影窗口。窗口保留正常的键盘操作和关闭行为。
- 用量窗口、设置窗口和桌面卡片支持拖动边缘调整宽高；字体大小不变，内容随宽高重新布局。尺寸独立保存到 `data/window_geometry.json`，重启后按当前 DPI 和工作区恢复；刷新、主题切换及详情展开不会重置手动尺寸。
- 用量和设置窗口采用统一的无描边圆角外框，顶部栏替代 Windows 系统标题栏。右上角提供最小化、最大化／还原和关闭按钮；顶部空白可拖动，双击可最大化／还原，关闭用量窗口仍隐藏到托盘。最大化和最小化不会覆盖已保存的正常窗口尺寸。
- 默认跟随 Windows 的应用主题，每 2 秒检查一次；弹窗右上角的 ◐ 菜单可立即保存浅色、深色或跟随系统的选择。设置中的三段选择器支持实时预览，取消、Esc 或关闭设置会恢复原选择，保存后记住选择。
- 外观偏好独立保存到 `data/appearance.json`，不触发用量查询、不重写账号凭据，也不清除缓存或当前展开状态。设置中只改外观并保存时，不重新配置后台查询。
- 大字体或工作区较小时，窗口正文可滚动，底部操作保持可见；Tab 切换到正文中的控件时会自动滚动到该控件。Windows 原生菜单使用系统支持的外观。
- 火山方舟 Agent Plan 额外展示接口返回的 AFP 已用、总额和剩余量；Coding Plan 与百炼套餐按各自接口能力展示百分比或 Credits 额度（百炼 Token Plan 只返回百分比）。本工具不把 AFP 或 Credits 换算成 token，也不根据请求日志猜测容量。
- 查询失败时保留上次成功结果，并明确标记“数据可能过期”。
- AK/SK 仅保存到 Windows 凭据管理器，配置、缓存和日志不保存 Secret。

## 支持的服务商

| 服务商 | 套餐 | 用量窗口 | 绝对额度 |
| --- | --- | --- | --- |
| 火山方舟 | Agent Plan（先查）、Coding Plan（回落） | 5 小时 / 每周 / 每月 | Agent Plan 有 AFP |
| 阿里云百炼 | Token Plan 个人版（先查）、Coding Plan（回落） | 按接口返回，通常为每月 | Coding Plan 有 Credits 额度 |

百炼 Token Plan 个人版按订阅月计量，接口只返回各窗口的使用比例与重置时间，没有绝对额度；百炼新人免费额度（按模型的免费 token）和 Token Plan 团队版暂不支持。

## 重要：需要哪套凭据

两个服务商都使用**控制面凭据**，不是推理接口的 Key：

- **火山方舟**：火山引擎控制面 OpenAPI（`POST https://open.volcengineapi.com/`，`Action=GetAFPUsage / GetCodingPlanUsage`，`Version=2024-01-01`），需要火山账号的 **AccessKey ID + Secret AccessKey**。
- **阿里云百炼**：先用 AK/SK 以 ACS3-HMAC-SHA256 签名调用 `modelstudio.<region>.aliyuncs.com` 的 `GenerateCLIAccessToken` 换取控制台访问令牌，再用该令牌调用百炼控制台网关查询额度（与百炼官方 CLI 相同的协议）。需要阿里云账号的 **AccessKey ID + Secret AccessKey**；RAM 子账号还需授予 `AliyunTokenPlanReadOnlyAccess` 与 `AliyunBSSReadOnlyAccess` 只读策略。百炼 Token Plan 目前仅在华北2（北京）提供，Region 只接受 `cn-beijing` 或 `ap-southeast-1`。

两家的凭据都**不能**用 `sk-...`（百炼还有 `sk-sp-...` 套餐 Key）推理 API Key 代替。建议使用只具有套餐用量查询所需权限的子账号凭据，并确保本机系统时间准确。访问令牌只在内存中持有，不落盘、不写日志。

程序只会访问固定的官方 HTTPS 地址，不启用遥测，不允许自定义脚本或任意请求地址。

## 安装与启动

所有项目文件和运行数据均位于 `E:\token-plan-monitor`。在终端进入该目录：

```bat
E:
cd \token-plan-monitor
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m token_plan_monitor
```

### 创建桌面与开始菜单快捷方式（推荐）

完成上述依赖安装后，在项目目录执行：

```bat
powershell -NoProfile -File .\scripts\New-AppShortcuts.ps1
```

脚本会在 Windows 桌面和当前用户开始菜单各创建一个“火山方舟用量监控”快捷方式。之后可直接双击或从开始菜单搜索启动，不会出现控制台窗口。快捷方式使用项目内的 `pythonw.exe`、多尺寸圆角应用图标和正确工作目录，不需要管理员权限，也不会设置开机自启。窗口、任务栏和托盘使用统一的容量图形与状态标记。

脚本可重复执行以更新这两个快捷方式；如果移动项目或重新创建 `.venv`，需要重新执行。手动删除桌面和开始菜单中的对应 `.lnk` 即可移除快捷方式。

### 首次配置

首次启动会打开设置窗口。填写 AK/SK、Region（默认 `cn-beijing`）和刷新间隔，然后点击“保存设置”。保存时只会创建或更新 Windows 凭据管理器的以下两项：

```text
TokenPlanMonitor/volcengine_access_key_id
TokenPlanMonitor/volcengine_secret_access_key
```

Secret 输入框留空表示保留已保存的 Secret。程序不会自动删除这两项；卸载时可在 Windows“凭据管理器 → Windows 凭据”中手动删除。

验证控制台启动正常后，可使用无控制台窗口的方式后台运行：

```bat
.venv\Scripts\pythonw.exe -m token_plan_monitor
```

关闭用量弹窗只会隐藏窗口；选择托盘菜单“退出”才会退出程序。程序使用 Windows 命名互斥锁防止重复启动，重复双击快捷方式不会创建第二个托盘实例或并发写入缓存。

### 桌面迷你卡片

- **单击**：打开完整用量弹窗。
- **悬停**：显示状态和 5 小时、每周剩余量摘要。
- **右键**：查看三个窗口的已用/剩余详情，并可打开详情、刷新、切换置顶、隐藏卡片、设置或退出。
- **拖动**：移动卡片位置；拖动不会误触打开弹窗，松开时保存位置。
- **拖动边缘或四角**：调整卡片大小；边缘显示缩放光标，缩放不会触发打开弹窗。
- **隐藏后恢复**：使用托盘菜单中的“桌面卡片”；“卡片置顶”也可从托盘切换。

卡片随应用启动和退出，不创建登录启动项。启动和拖动结束时会按最近显示器的可用工作区校正位置，支持负坐标的多显示器布局；修改用量查询设置不会覆盖卡片偏好。

## 文件位置

```text
E:\token-plan-monitor\
  .venv\                 本地虚拟环境
  src\                   程序源码
  tests\                 离线测试与显式启用的实时测试
  data\config.json       Region、刷新间隔等非敏感配置
  data\snapshot.json     最近一次成功用量缓存
  data\widget.json       卡片位置、可见性和置顶状态
  data\appearance.json   外观选择：light / dark / system
  logs\app.log           脱敏运行日志
  assets\app.ico         快捷方式与窗口图标
  scripts\               快捷方式创建脚本
```

配置或缓存损坏时会使用安全默认值或空状态，不会导致启动崩溃。更换 AK/SK 后，旧账号的内存快照和缓存会被清除，避免串号展示。

## 测试

普通测试完全离线，实时测试默认跳过：

```bat
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m compileall src
```

覆盖内容包括：

- 火山签名 V4 的 canonical query、固定签名头顺序、scope 和确定性签名。
- Agent/Coding Plan 响应解析、重置时间、零配额回退和错误分类。
- 鉴权/网络错误不错误回退，HTTP 200 内嵌 OpenAPI 错误不会被当作成功。
- 配置与缓存恢复、Secret 不进入 JSON。
- 托盘摘要与三窗口菜单格式、原生 tooltip 长度，以及桌面卡片偏好恢复和损坏配置回退。
- 后台刷新单 flight、手动刷新合并、旧设置结果丢弃和断网保留快照。

### 离线界面预览

以下命令使用假数据覆盖 Agent Plan、Coding Plan、低余额、刷新中、过期、未配置和错误七种场景，不读取真实凭据，也不访问云端。浅色与深色主题都会以 100%、150%、200% DPI 比例检查收起／展开弹窗、卡片文字边界与设置窗口的滚动布局：

```bat
.venv\Scripts\python.exe tests\gui_preview.py
```

使用 `.venv\Scripts\python.exe tests\gui_material_smoke.py` 可在独立假数据窗口验证点击、拖动、置顶、悬停、隐藏恢复、无额外阴影窗口、字段内联校验、设置保存、主题保存与取消回滚、系统主题变化及主题切换不触发查询，不操作正在运行的监控实例。

使用 `.venv\Scripts\python.exe tests\gui_material_preview.py` 可额外生成浅色、深色条纹背景下的材质对照图（`artifacts\material-*.png`），仅使用假数据和隔离凭据后端。背景模糊与内部柔和阴影以屏幕截图为准，PrintWindow 窗口截图不包含完整合成效果。

使用 `.venv\Scripts\python.exe tests\gui_resize_smoke.py` 可验证三档 DPI 下的最小尺寸、边缘缩放、刷新与主题切换保留尺寸，以及重启恢复。

使用 `.venv\Scripts\python.exe tests\gui_chrome_smoke.py` 可检查完整原生窗口边界、八方向缩放命中、最大化工作区、最小化恢复、关闭回滚与正常尺寸保留；完整窗口截图保存在 `artifacts\chrome-*-full.png`，包含外框而不仅是正文。

截图保存在 `artifacts\theme-*.png`，其中 `theme-light/dark-popup-collapsed/expanded.png`、`theme-light/dark-widget.png`、`theme-light/dark-settings.png` 是两套主题的代表截图。材质对照图还包含两套主题与浅深背景的四种组合。需要重新生成多尺寸应用图标时，运行 `.venv\Scripts\python.exe scripts\generate_app_icon.py`，再重新执行快捷方式脚本。

`tests\gui_smoke.py` 兼容入口同样运行上述隔离交互检查，不需要预先启动应用，也不会搜索或点击正在运行的设置窗口。

完整的托盘与桌面卡片原生交互检查使用独立假数据外壳，先关闭其他监控实例，然后运行：

```bat
.venv\Scripts\python.exe tests\gui_shell_smoke.py
```

该脚本会实际操作托盘悬停/右键、卡片悬停/单击/拖动/置顶/隐藏与恢复，并通过托盘退出和重启验证偏好持久化及屏外位置校正。所有数据位于临时目录，使用禁用凭据的后端，不访问云端；截图保存在 `artifacts\native-*.png`。运行期间请勿移动鼠标或操作键盘。

### 显式实时验证

先通过应用设置保存真实 AK/SK，再在本机执行：

```bat
set TOKEN_PLAN_MONITOR_LIVE=1
.venv\Scripts\python.exe -m pytest -m live -s
set TOKEN_PLAN_MONITOR_LIVE=
```

实时测试只打印套餐名称和脱敏百分比，不打印 AK/SK、Authorization 或原始响应。单元测试中的固定签名只能防止实现回归，不能证明云端一定接受签名，因此首次使用必须与火山控制台或 CC Switch 的数值交叉核对。

## 常见问题

- **提示鉴权失败**：检查是否使用账号 AK/SK 而非推理 Key，确认 IAM 权限与本机系统时间。
- **显示未找到套餐**：确认当前账号订阅了 Agent Plan 或 Coding Plan。
- **数据可能过期**：检查网络、代理或服务状态；界面显示的是最近一次成功数据。
- **Coding Plan 没有 token 总数**：接口只返回百分比时，程序不会虚构绝对容量。
- **不想保留凭据**：在 Windows 凭据管理器中手动删除上述两项，然后删除本项目目录。

## 参考与许可

- [CC Switch](https://github.com/farion1231/cc-switch)：`src-tauri/src/services/coding_plan.rs`，调研固定 revision `ef7b77dee255e2c3b9a594de454eae21476ca687`。火山方舟签名与响应解析参考它。
- [阿里云百炼 CLI](https://github.com/modelstudioai/cli)：`packages/core/src/client/acs.ts`、`packages/core/src/console/gateway.ts`、`packages/core/src/console/models.ts` 与 `packages/commands/src/commands/usage/`，调研固定 revision `fa8330c27862635f7d769c5488c33a06a47063d5`。百炼的 ACS3 签名、控制台网关协议与额度解析参考它。
- 两者的版权与许可文本（MIT / Apache-2.0）见 `THIRD_PARTY_NOTICES.md`。
