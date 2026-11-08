# 火山方舟 Token Plan 用量监控

一个独立的 Windows 托盘工具，用于查看火山方舟 **Agent Plan / Coding Plan** 的 5 小时、每周和每月订阅容量。协议实现参考 CC Switch，但不依赖 CC Switch，也不读取或修改其配置和数据库。

## 功能

- 后台每 15 分钟查询一次，可在设置中调整为 5–1440 分钟。
- 托盘悬停简要显示 5 小时和每周剩余量；右键菜单直接列出 5 小时、每周、每月的已用/剩余百分比及最近成功更新时间。
- 单击托盘图标打开简洁用量弹窗，查看剩余百分比、进度和重置时间；“展开详情”查看已用百分比与 AFP 绝对量；也支持立即刷新、设置和退出。
- 桌面常驻一张可拖动的 macOS 风格圆角迷你卡片，显示状态、5 小时和每周剩余量；悬停看摘要，右键看三窗口详情与操作。卡片尺寸随字体和 DPI 实际测量，避免文字裁切。
- 迷你卡片默认置顶，可从卡片或托盘菜单取消置顶、隐藏和恢复；位置、可见性和置顶状态会随应用重启恢复，不会设置开机自启。
- 桌面卡片、用量弹窗、设置和悬停提示采用统一的 macOS 风格：黑白双主题、少量蓝色强调、无常态描边的圆角输入框与按钮、柔和阴影。卡片表面、剩余进度条和主要按钮采用轻微渐变。Windows 支持时在底板和留白处显示 Acrylic 毛玻璃，正文与容量面板保持不透明，不降低文字透明度。
- 透明效果遵循 Windows“辅助功能 / 视觉效果 → 透明效果”设置。系统关闭透明效果或原生接口不可用时使用当前主题的实心背景；应用不添加窗口外层投影或独立阴影窗口。窗口保留正常的键盘操作和关闭行为。
- 用量窗口、设置窗口和桌面卡片支持拖动边缘调整宽高；字体大小不变，内容随宽高重新布局。尺寸独立保存到 `data/window_geometry.json`，重启后按当前 DPI 和工作区恢复；刷新、主题切换及详情展开不会重置手动尺寸。
- 用量和设置窗口采用统一的无描边圆角外框，顶部栏替代 Windows 系统标题栏。右上角提供最小化、最大化／还原和关闭按钮；顶部空白可拖动，双击可最大化／还原，关闭用量窗口仍隐藏到托盘。最大化和最小化不会覆盖已保存的正常窗口尺寸。
- 默认跟随 Windows 的应用主题，每 2 秒检查一次；弹窗右上角的 ◐ 菜单可立即保存浅色、深色或跟随系统的选择。设置中的三段选择器支持实时预览，取消、Esc 或关闭设置会恢复原选择，保存后记住选择。
- 外观偏好独立保存到 `data/appearance.json`，不触发用量查询、不重写账号凭据，也不清除缓存或当前展开状态。设置中只改外观并保存时，不重新配置后台查询。
- 大字体或工作区较小时，窗口正文可滚动，底部操作保持可见；Tab 切换到正文中的控件时会自动滚动到该控件。Windows 原生菜单使用系统支持的外观。
- Agent Plan 额外展示接口返回的 AFP 已用、总额和剩余量；Coding Plan 通常只提供百分比。本工具不把 AFP 换算成 token，也不根据请求日志猜测容量。
- 查询失败时保留上次成功结果，并明确标记“数据可能过期”。
- AK/SK 仅保存到 Windows 凭据管理器，配置、缓存和日志不保存 Secret。

## 重要：需要哪套凭据

容量查询使用火山引擎**控制面 OpenAPI**，不是推理接口：

```text
POST https://open.volcengineapi.com/
Action=GetAFPUsage / GetCodingPlanUsage
Version=2024-01-01
```

因此需要火山账号的 **AccessKey ID + Secret AccessKey**，不能使用 `sk-...` 推理 API Key 代替。建议使用只具有 Ark 套餐用量查询所需权限的子账号/IAM 凭据，并确保本机系统时间准确。

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

- [CC Switch](https://github.com/farion1231/cc-switch)：`src-tauri/src/services/coding_plan.rs`
- 调研固定 revision：`ef7b77dee255e2c3b9a594de454eae21476ca687`
- CC Switch 的 MIT 版权与许可文本见 `THIRD_PARTY_NOTICES.md`。
