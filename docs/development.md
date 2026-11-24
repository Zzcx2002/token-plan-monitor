# 开发者指南

面向使用者（安装、配置、日常使用、凭据、隐私）的说明在 [README.md](../README.md) / [README.en.md](../README.en.md)。本文只讲源码运行、打包、测试与验收。

- [环境与目录](#环境与目录)
- [macOS：源码运行](#macos源码运行)
- [macOS：打包 DMG](#macos打包-dmg)
- [macOS：离线界面验证](#macos离线界面验证)
- [README 截图的再生成](#readme-截图的再生成)
- [Windows：依赖与测试](#windows依赖与测试)
- [Windows：离线界面预览与交互检查](#windows离线界面预览与交互检查)
- [Windows：打包便携版](#windows打包便携版)
- [显式实时验证](#显式实时验证)
- [验收记录](#验收记录)

## 环境与目录

- 共享核心在 `src/token_plan_monitor/`：`models.py`（数据契约与输入校验）、`providers.py`（服务商注册表、凭据 account、Claude Code 元数据）、`storage.py`（数据目录与钥匙串/凭据管理器、历史 JSONL 读写与轮转）、`monitor.py`（后台刷新调度、历史追加、趋势计算、告警评估与 notifier 注入）、`presentation.py`（屏上文案与通知文案）、`history.py`（历史行契约、采样去重、保留裁剪，纯规则）、`trends.py`（周期切分、最小二乘速率、耗尽预测，纯规则）、`history_chart.py`（历史图表纯规则：范围过滤、周期切分、按像素分桶保 min/max、坐标/刻度/重置标记密度门控）、`alerts.py`（下穿触发/重新武装/重置提醒，纯规则）、`claude_code.py`（写入 Claude 配置）、`model_catalog.py`（在线模型清单：推导 compatible-mode 地址、过滤对话模型、与内置清单合并）、`volcengine.py` / `bailian.py` / 两个 signer。
- macOS 壳在 `src/token_plan_monitor/macos/`：`ui.py`（菜单栏、详情窗口、用量历史窗口、桌面卡片、设置窗口）、`chart.py`（CAShapeLayer + CGPath 折线/面积图视图与数据列表数据源；声明式图层几何，无 drawRect_/NSBezierPath）、`layout.py`（纯规则：校验、恢复动作、最低项标记、几何）、`theme.py`（动态色、字阶、材质、图表 token）、`notifications.py`（UNUserNotificationCenter + bundle 守卫 + NSUserNotification 降级）、`preview.py`（离线预览与 `--smoke-test`）。
- Windows 壳在 `src/token_plan_monitor/ui.py`、`desktop_widget.py`、`ui_graphics.py`、`window_chrome.py`、`window_geometry.py`、`appearance.py`、`settings_view.py`（设置对话框与 Claude Code 面板）、`history_view.py`（用量历史窗口）；两端共享的设置校验纯规则在 `settings_rules.py`，可写目录与打包资源解析在 `paths.py`。
- 数据目录：macOS 用 `~/Library/Application Support/TokenPlanMonitor/` 与 `~/Library/Logs/TokenPlanMonitor/`；Windows 源码运行用项目内 `data/` 和 `logs/`，PyInstaller 便携版改用 **exe 旁边的 `data/` 和 `logs/`**（`paths.is_frozen()` 分支，两者都在 `.gitignore` 里）。
- `artifacts/`、`build/`、`dist/` 同样被忽略，都是可随时重生的输出目录。

## macOS：源码运行

需要 Apple Silicon Mac，以及 **python.org 官方 Python 3.13 framework 版本**加 Xcode Command Line Tools。py2app 不支持用 uv 的便携解释器打包；纯源码运行可以用兼容的 Python 3.11 及以上。

```sh
python3.13 -m venv .venv
.venv/bin/python -m pip install -r requirements-macos-build.txt
.venv/bin/python -m pip install --no-build-isolation --no-deps -e .
.venv/bin/python -m token_plan_monitor
```

## macOS：打包 DMG

```sh
.venv/bin/python scripts/build_macos.py
```

构建脚本要求 framework Python（guard 会拒绝 uv 便携解释器）。若开发用 `.venv` 不是 framework 构建，另建 `.venv-build/`（已在 `.gitignore`）：`/Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 -m venv .venv-build`，装 `requirements-macos-build.txt` 与 `--no-deps -e .` 后用 `.venv-build/bin/python scripts/build_macos.py`。

脚本替换生成的 `build/`、`dist/TokenPlanMonitor.app` 和 DMG 暂存目录，产出独立应用与 DMG。构建过程会检查所有 Mach-O 文件是 arm64、最低系统版本不高于 14、动态链接不依赖开发机路径，并验证项目及第三方许可证资源已入包、本地 ad-hoc 签名有效。Bundle ID 固定为 `com.zzcx2002.tokenplanmonitor`，应用数据不写进 `.app` 内。

发布签名只有本地 ad-hoc（`codesign -dv` 报 `flags=0x2(adhoc)`、`Signature=adhoc`、`TeamIdentifier=not set`），没有 Developer ID 签名、没有公证。

## macOS：离线界面验证

```sh
.venv/bin/python -m pytest
.venv/bin/python -m compileall -q src scripts
.venv/bin/python -m token_plan_monitor.macos.preview --smoke-test
.venv/bin/python -m token_plan_monitor.macos.preview --state stale
```

界面改版需要逐外观复核时，用 `--appearance` 固定浅色或深色，并把两次运行写入不同目录：

```sh
.venv/bin/python -m token_plan_monitor.macos.preview --smoke-test --appearance light --artifacts artifacts/macos-light
.venv/bin/python -m token_plan_monitor.macos.preview --smoke-test --appearance dark --artifacts artifacts/macos-dark
```

预览与界面检查使用临时数据目录、内存凭据和假监控，不读真实钥匙串、不访问云端。状态包括 `agent`、`coding`、`refreshing`、`stale`、`unconfigured`、`error`、长文本 `long`，以及边界值 `limits`、周期缺失 `missing`、最低值并列 `tied`，两个服务商各跑一遍。

检查覆盖：默认/较小/加宽窗口的内容重排、进度条与无障碍标签、焦点和滚动保持、设置校验与帮助、卡片刷新按钮的单次命令路由、刷新中禁用、无障碍标签、父视图/翻转卡片坐标转换、可见按钮逐边与透明外圈命中、按钮/角落缩放分流、点击/移动/缩放的按下-拖动-释放事件路径、菜单入口、最小与加宽卡片布局、尺寸重启恢复。输出在 `artifacts/macos/`，含原生视图截图与通过记录。

**视图截图不包含**桌面合成后的完整背景模糊、窗口外阴影和系统焦点环（输入框的聚焦描边由卡片自绘，会出现在截图里；系统控件的原生焦点环不会），所以这三项必须实机目视。给打包应用做检查时请为 `--artifacts` 指定**绝对路径**，避免 py2app 改变工作目录后把截图写进应用资源目录：

```sh
dist/TokenPlanMonitor.app/Contents/MacOS/TokenPlanMonitor --smoke-test --artifacts /tmp/token-monitor-bundle-smoke
open dist/TokenPlanMonitor.app --args --preview --state agent
```

## macOS：通知链路交互验证

通知授权/投递/点击/拒绝降级无法离线断言，用编排脚本对着**已构建的 bundle** 跑（裸 venv 无 bundle proxy，守卫会报 unavailable）：

```sh
R=dist/TokenPlanMonitor.app/Contents/Resources
PYTHONPATH=$R/lib/python3.13:$R/lib/python3.13/lib-dynload:$R/lib/python313.zip \
RESOURCEPATH=$R \
dist/TokenPlanMonitor.app/Contents/MacOS/python scripts/verify_macos_notifications.py
```

`PYTHONPATH` 必须带 `lib/python313.zip`：bundle 内 pyobjc 的 Python 半部分在 zip 里，只有 py2app bootstrap 会把它加进 sys.path。首跑按提示点「允许」再点通知横幅（exit 0 = 点击回环成立）；到系统设置→通知里拒绝后重跑，验证 legacy 降级横幅与点击（同样 exit 0）。exit 2 = 没收到点击，exit 1 = 环境不是 bundle。

呈现层三个已取证的坑：同 identifier 重投被 UNC 当静默更新（故每次投递唯一标识）；专注/勿扰吞默认级通知（故 low_quota 设 timeSensitive）；覆盖安装版后授权身份漂移需 `lsregister -f` + 重拨开关。探针本身必须 `open -W` 启动并 `finishLaunching()`；PyObjC block 不接受 builtin method（如 `list.append`），bound method 可以。

## README 截图的再生成

`docs/images/` 里的四张图来自离线预览，数值是假数据。重新生成后按下面的对应关系复制：

| `docs/images/` | 来源 |
| --- | --- |
| `card-ark.png` | `artifacts/macos-light/card-agent.png` |
| `card-ark-dark.png` | `artifacts/macos-dark/card-agent.png` |
| `details-ark.png` | `artifacts/macos-light/details-agent.png` |
| `history-ark.png` | `artifacts/macos-light/history-agent.png` |
| `settings-claude-code.png` | `artifacts/macos-light/settings-claude.png` |

```sh
.venv/bin/python -m token_plan_monitor.macos.preview --smoke-test --appearance light --artifacts artifacts/macos-light
.venv/bin/python -m token_plan_monitor.macos.preview --smoke-test --appearance dark --artifacts artifacts/macos-dark
cp artifacts/macos-light/card-agent.png       docs/images/card-ark.png
cp artifacts/macos-dark/card-agent.png        docs/images/card-ark-dark.png
cp artifacts/macos-light/details-agent.png    docs/images/details-ark.png
cp artifacts/macos-light/history-agent.png    docs/images/history-ark.png
cp artifacts/macos-light/settings-claude.png  docs/images/settings-claude-code.png
```

改界面文案或状态配色后记得同步更新，README 里的图注承诺了「离线预览渲染、假数据」。

## Windows：依赖与测试

```bat
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\python.exe -m pytest
.venv\Scripts\python.exe -m compileall src
```

普通测试完全离线，实时测试默认跳过。覆盖内容包括：

- 火山签名 V4 的 canonical query、固定签名头顺序、scope 和确定性签名；阿里云 ACS3-HMAC-SHA256 签名。
- Agent / Coding Plan 与百炼 Token Plan / Coding Plan 的响应解析、重置时间、零配额回退和错误分类。
- 鉴权/网络错误不错误回退，HTTP 200 内嵌 OpenAPI 错误不会被当作成功。
- 配置与缓存恢复、Secret 不进入 JSON、损坏或版本不匹配的 JSON 回退安全默认值。
- 托盘摘要与三窗口菜单格式、原生 tooltip 长度上限，桌面卡片偏好恢复和损坏配置回退。
- 后台刷新单 flight、手动刷新合并、旧设置结果丢弃、断网保留快照、切换服务商清缓存。
- Claude Code 写入：键合并范围、备份及**备份文件自身**的 `0600` 权限、历史全局可读备份的收紧、JSONC 读取、损坏文件拒绝写入、幂等重放、推理 Key 不进入确认文本；Region 与服务商地域白名单、Base URL 拒绝含账号密码（`user:pass@host`）的地址；可选的 `CLAUDE_CODE_AUTO_MODE_MODEL`（不选完全不碰、选择才写、重型分类模型的超时警告文案）与可选的 `CLAUDE_CODE_SUBAGENT_MODEL`（不选沿用 tier 规则：已有值保留、为空回填主模型；选择则覆盖写入并退出回填）、偏好文件旧格式向后兼容。
- 在线模型清单：compatible-mode 地址推导（保留 region/代理主机）、图像/视频/语音/决策模型过滤、恶意模型 ID 形状拒绝、去重保序、非 200/断网/坏 JSON/空列表回退内置清单、合并保留内置标签与轻量标记并追加新模型、缓存按服务商隔离与损坏回退、新模型进下拉框而非自定义框；`catalog_block_reason(base_url, api_key)` 两端共用受阻判定（空 Key / 路径不含 `/apps/anthropic` 各有固定文案），`models_url_for` 在无 anthropic 段时按 `/compatible-mode` 截断防双重路径；macOS smoke 以注入桩驱动「拉取→主线程排空→原地刷新下拉框」全链路并验证选中保留与下架模型降级。
- 设置窗生命周期与自动拉取门禁（`tests\test_ui_settings_lifecycle.py`）：重入开窗合并/失败 dialog 处置、自动拉取的受阻原因上报、`(base_url, key)` 签名去重、inflight 与写入中守卫、每次开窗绕过签名去重的刷新语义。

## Windows：离线界面预览与交互检查

以下命令使用假数据覆盖 Agent Plan、Coding Plan、低余额、刷新中、过期、未配置和错误七种场景，不读取真实凭据、不访问云端。浅色与深色主题都会以 100%、150%、200% DPI 检查收起/展开弹窗、卡片文字边界与设置窗口滚动布局：

```bat
.venv\Scripts\python.exe tests\gui_preview.py
```

- `tests\gui_material_smoke.py`：独立假数据窗口里验证点击、拖动、置顶、悬停、隐藏恢复、无额外阴影窗口、字段内联校验、设置保存、主题保存与取消回滚、系统主题变化及主题切换不触发查询，不操作正在运行的监控实例。材质相关断言：默认实色路径无 `WS_EX_LAYERED`、无透明色键、保留系统阴影；设置窗材质选择器的即时预览（亚克力生效时才分层）、Windows 10 下亚克力禁用态、取消后材质回滚。任务栏归属：卡片与悬停摘要等 overrideredirect 窗口在 solid/acrylic 每次材质重放后仍带 `WS_EX_TOOLWINDOW`（不产生任务栏按钮），带边框的弹窗保持普通任务栏窗口。排版与卡片交互：详情辅助文字走 9pt `DetailMuted/DetailWarning` 样式、可见 N 个周期行只 pack N−1 条发丝分隔线；桌面卡片刷新按钮的按下/释放路由、拖出取消、无坐标释放回退、refreshing/unconfigured 禁用态、`hand2` 光标，以及空白处 press/release 不再打开弹窗（拖动保存位置同样不开窗）。
- `tests\gui_material_preview.py`：按 gating 结果生成 solid / acrylic × 浅色 / 深色的材质矩阵对照图（`artifacts\material-*.png`），并逐窗口断言 `_window_effects` 状态。背景模糊与内部柔和阴影以屏幕截图为准，PrintWindow 窗口截图不含完整合成效果。
- `tests\gui_resize_smoke.py`：三档 DPI 下的最小尺寸、边缘缩放、刷新与主题切换保留尺寸、重启恢复；280/320/520 三档宽度下刷新按钮 bbox 始终在卡内、monthly 进度条右界不超过 `refresh_progress_limit`，按钮中心点击只触发 `on_refresh`。
- `tests\gui_chrome_smoke.py`：完整原生窗口边界、八方向缩放命中、最大化工作区、最小化恢复、关闭回滚与正常尺寸保留；Windows 11 下额外断言窗口没有 GDI region（圆角完全交给 DWM）。标题栏拖动断言为 Tk 级 geometry 手势（含最大化态拖动还原），不再出现原生 `WM_NCLBUTTONDOWN` 模态移动循环。完整窗口截图在 `artifacts\chrome-*-full.png`。
- `tests\gui_service_mode_smoke.py`：设置窗原生闪退（BEX64 / `0xc0000409`）的回归。强制把 Tcl 通知器置为 `TCL_SERVICE_ALL`，在 Python 阶段排队回调后，依次驱动材质 apply、TOOLWINDOW 重声明、chrome region、两个 WNDPROC 转发、裸 `SetWindowPos(FRAMECHANGED)`，以及真实设置窗 `open → _fit → _present → 350ms geometry flush → close → retained reopen`：进程必须存活，icon/deiconify/lift/focus 等原生边界必须处于 NONE 防护中，退出后 ALL 原样恢复，延迟回调不丢失且在正常事件循环补跑。无防护时嵌套 `Tcl_ServiceAll()` 可以 NULL 线程状态重入 Python 并直接 abort（见 `defer_tcl_events` 的机制注释）。注意显式 `update_idletasks()` 本身会执行 idle 回调，这与 WndProc 的嵌套泵送不是同一条路径，不能用“idle 是否已跑”替代原生边界断言。
- `tests\gui_dpi_smoke.py`：用 `SendMessage` 向弹窗与桌面卡片投递 `WM_DPICHANGED`（96 → 144 → 96 DPI），验证建议矩形落位、全局 `tk scaling` 与各控件 scale/圆角/按钮尺寸重排、材质保持 solid；弹窗与卡片 HWND 在 Map 后包装句柄变化时子类仍生效。另断言滚动条物理宽度（10/15px）、详情 9pt 点字号与刷新按钮命中框（20/30px）随 scale 重算。
- `tests\gui_shell_smoke.py`：完整的托盘与桌面卡片原生交互检查，使用独立假数据外壳，先关闭其他监控实例再运行。它会实际操作托盘悬停/右键、卡片悬停/空白单击（断言不开窗）/右键菜单「打开详细用量」开窗/右下角刷新按钮原生点击（按显示器 DPI 换算命中框，假外壳写 `refresh-count.txt` 佐证）/拖动/置顶/隐藏与恢复，并通过托盘退出和重启验证偏好持久化及屏外位置校正。所有数据在临时目录，凭据后端禁用，不访问云端；截图在 `artifacts\native-*.png`。**运行期间不要移动鼠标或操作键盘。** 该冒烟依赖真实桌面会话与 UIA 枚举，在部分会话状态下会失败于托盘图标名断言或卡片原生菜单（`#32768`）等待——干净 HEAD 上同样复现，属环境问题；判断是否回归前先 `git stash` 跑基线对照，其余 GUI smoke 才是稳定的自动化绿灯。
- `tests\gui_smoke.py`：兼容入口，同样运行隔离交互检查，不需要预先启动应用。
- `tests\gui_settings_provider_smoke.py`：设置窗服务商切换与保留复用全链路——双 provider 的 keyring 预置、切换后 region 回退与告警值保留、AK/SK 按 provider 隔离不串号、保存后监控收到正确 provider、旧 provider 凭据不变；同 provider 重开复用 window/ClaudePanel，取消的输入和 Secret 不残留，外部存储变化仍重新读取；保存关窗前的自动模型拉取在无推理 Key 时只落「填写推理 API Key 后将自动获取模型列表」受阻状态（注入 `_start_model_fetch` 断言全程零调用、不起线程、不访问云端），不起托盘。
- `tests\gui_history_smoke.py`：用量历史窗口与 macOS 同源规则——每个存在的窗口一张图、重置断点不连线、24 小时/7/30/90 天范围切换、悬停提示走共享纯函数、「数据列表」披露表（无绝对值时隐藏该列）、空/薄数据文案、主题重绘与窗口生命周期；全部使用临时目录假数据。表格超过 12 行出现的滚动条断言为主题化的 `Vertical.TScrollbar`。
- `tests\gui_claude_smoke.py`：Claude Code 接入——所有写入经 `claude_paths_override` 落临时目录，**绝不触碰真实 `~/.claude`**；模型清单拉取与确认框均为注入桩。覆盖带备份的校验写入与 onboarding 合并、推理 Key 不回显、取消/校验失败零写入、按 provider 的草稿记忆、后台清单更新、shutdown 清空内存 Key 草稿；KeyRelease/FocusOut 的 400ms 防抖自动拉取（合成键事件在 Windows 无真实 OS 焦点时不派发，测试直接驱动 `_schedule_fetch_check`）、同签名去重与 inflight/写入中守卫、无 Key 或不支持 URL 时卡片状态条明确给受阻原因；「写入 Claude 配置」按钮点击即「写入中…」禁用态（在确认框嵌套事件循环内断言忙碌态）、结果在卡片内状态条展示并自动滚动入视、确认钩子抛 `RuntimeError` 时异常不外泄到 Tk 回调且按钮在 finally 必恢复，字段级校验错误仍只走内联、不写卡片状态条。
- `tests\gui_geometry_smoke.py`：设置窗滚动几何的回归。独立 Toplevel 内验证滚动条 disabled/enabled 两态下 canvas 宽度完全相同且 bar 始终映射；父容器首轮不足 100px、随后在 canvas 宽度不变的情况下增长到 320px 时，标签 `wraplength` 必须重新计算；设置窗打开即顶部锚定（`yview()[0] == 0.0`）、内容已映射且非沉底窄条、provider 切换重建后 canvas 宽度不跳、有限重排完成后连续 3 秒几何状态集合只有一个元素、异常 seed 高度（1048 DIP）与全新存储两次开窗等高（`cap_height` 兜底）。
- `tests\gui_settings_native_smoke.py`：从独立假数据桌面卡片真实右键并选择原生菜单「设置」，连续打开/关闭三次；三轮只构造一次设置窗，记录点击→可见→首张有正文的时间、构建/fit/present/面板重绘/材质 apply 计数，并断言后续打开不重复材质重放。每次显示后采样 6 秒、关闭后再采样 2 秒，整个 App 进程必须持续存活，并通过固定假模型结果覆盖队列完成回调。所有 HWND 按 `ready.json` 的应用真实 PID 过滤，不触碰同时运行的正式监控；用 `PrintWindow(PW_RENDERFULLCONTENT)` 短间隔采样首帧，solid 断言无大面积近黑未绘制区域，acrylic 断言正文像素和 layered 状态。默认 solid；设置 `TOKEN_PLAN_MONITOR_TEST_MATERIAL=acrylic` 跑亚克力路径。截图与报告在 `artifacts\native-card-settings-*`。**运行期间不要移动鼠标或操作键盘。**
- `tests\gui_packaged_settings_smoke.py`：对 `dist\TokenPlanMonitor-<版本>-win64` 中真正的 exe 重复上述原生卡片菜单入口，三轮均在显示后观察 8 秒、关闭后观察 3 秒，验证正文与进程存活。暂存包不写设置或凭据，未配置监控不会发起服务商请求；`TOKEN_PLAN_MONITOR_GUI_TEST=1` 只用于把 fatal stack 写到该暂存包的 `logs\fatal.log`。它和源码原生菜单测试一样依赖真实桌面 `#32768` 菜单，已知菜单未出现的环境失败不能记成通过。

GUI smoke 依赖 Tk 事件循环，曾观察到偶发的 Tk GIL 致命错误（与改动无关的环境抖动）；改动 Windows 壳后每个相关 smoke 至少连跑两遍，单跑一次通过不能作为绿灯依据。

截图保存在 `artifacts\theme-*.png`，其中 `theme-light/dark-popup-collapsed/expanded.png`、`theme-light/dark-widget.png`、`theme-light/dark-settings.png` 是两套主题的代表截图。`gui_theme_preview.py` 在 100% DPI 额外断言 ttk 查表：readonly/readonly-focus 字段底等于 card（深色下不允许回到 `#ffffff` 或 clam 泄漏色 `#dcdad5/#4a6984/#999999`）、focus 边框取 accent、disabled 箭头取 muted；再以 `ttk::combobox::Post` 真实弹出下拉层，断言经典 Listbox 的背景/前景/选中色并在弹出层保持打开时切主题验证直接重着色，截图为 `theme-light/dark-settings-combobox.png`（含普通行与选中行对照）。需要重新生成多尺寸应用图标 `assets\app.ico` 和 README 使用的 `assets\app.png` 时，运行 `.venv\Scripts\python.exe scripts\generate_app_icon.py`，再重新执行快捷方式脚本。

## Windows：窗口材质、圆角与 DPI 架构

窗口外观全部在 `ui_graphics.py` / `window_chrome.py` 通过 ctypes 直调 Win32/DWM，Tk 只负责控件绘制，五条核心约定：

- **材质双路径（fail-closed）**：`AppearancePreferences.material` 取值 `solid`（默认）/ `acrylic`，持久化在 `appearance.json`，旧配置缺字段时回退 solid。亚克力只有在 `is_windows_11()`（`RtlGetVersion` build ≥ 22000，不受 manifest 兼容层影响）且系统「透明效果」开关开启时才生效；任一生机 API 失败都回滚 accent 状态并走实色，不允许留下透明洞。实色路径会显式清掉 `-transparentcolor` 色键与 `WS_EX_LAYERED`——色键分层窗口会被 Windows 强制关闭 ClearType，这是实色模式文字锐利的前提，也是亚克力模式文字稍软的根因。Map 钩子按 `(原生 HWND、surface、有效 material)` 去重：首次 Map 仍完整执行原生框架稳定化；已经成功 Map 的保留窗口以相同状态再次显示时，不再执行透明色键/合成/擦除重放，HWND 或外观变化仍必须重装，失败回退到 solid 时不会被误记为成功的 acrylic。
- **圆角**：Windows 11 只使用 DWM `DWMWA_WINDOW_CORNER_PREFERENCE`（attr 33，原生约 8px 抗锯齿 + 系统阴影），`set_rounded_region` 在 Win11 短路 no-op 并 `SetWindowRgn(NULL)` 清残留；Windows 10 没有 DWM 圆角，回退 `CreateRoundRectRgn`，半径统一走 `appearance.py` 的 DIP token 并在函数内乘 `dpi_scale`，最大化时移除 region。
- **DPI 链路**：打包 exe 靠 `assets\TokenPlanMonitor.manifest` 声明 PerMonitorV2（manifest 会替换 PyInstaller 默认模板，所以 common-controls v6 等依赖也在同一文件自带，缺失 manifest 时构建直接报错）；源码运行由 `__main__.py` 的 `SetProcessDpiAwarenessContext(-4)` 兜底。运行期 `WM_DPICHANGED` 先按 lParam 建议矩形 `SetWindowPos`，再经 `after_idle` 触发 `_on_dpi_changed`：更新全局 `tk scaling` → `invalidate_scaled_caches()` → 重建 ttk 元素与各控件 `refresh_scale()`。overrideredirect 窗口（桌面卡片等）的 WNDPROC 子类在每次 `<Map>` 按当前 HWND 幂等重装——Tk 在 Map 后可能换上外层包装 HWND，构造期装的子类会失效。
- **Tcl 服务模式防护（防 0xc0000409 原生闪退）**：Python 回调执行期间 Tcl 线程状态 `tcl_tstate` 为 NULL；此时若通知器处于 `TCL_SERVICE_ALL`，同步泵送窗口消息的 user32 调用会让 Tk WndProc 里的 `Tcl_ServiceAll()` 执行排队的 Python 回调，`ENTER_PYTHON` 以 NULL tstate 调 `PyEval_RestoreThread` → `Py_FatalError` → abort，windowed 模式通常没有 Python traceback。所有会同步泵送消息的原生边界（两个 WNDPROC 整体、`SetWindowLongPtrW`/`SetWindowPos`/`SetWindowRgn`/`CallWindowProcW`、材质 apply、设置 icon/geometry/deiconify/lift/focus/destroy）必须经 `defer_tcl_events()` / `restore_tcl_events()` 或 `tcl_events_deferred()` 包住；嵌套调用逐层保存并 finally 恢复 ambient mode。绑定优先使用当前进程已加载的 Tcl 完整路径，其后才是 `_tkinter`/frozen `_internal` 相对路径，且只缓存成功结果；Windows 上导出不可解析会抛 `TclServiceGuardUnavailable`，禁止静默退化为无防护。 frozen `--self-check` 强制验证绑定路径、NONE/ALL 往返及原生 WndProc 不重入，构建缺少 `TCL_SERVICE_*_OK` / `TCL_SETTINGS_PRESENT_OK` 标记即失败。标题栏拖动仍禁止使用 `SendMessage(WM_NCLBUTTONDOWN)` 原生模态移动循环；回归在 `tests\gui_service_mode_smoke.py` 与 `gui_chrome_smoke.py`。
- **已知限制**：Tk 8.6.15 的 `tk scaling` 进程全局唯一，多显示器 DPI 不同时，以交互窗口所在显示器的 DPI 为准统一重排，不做 per-window 字号；tooltip 不装 DPI 子类，DPI 变化时隐藏重建。

## Windows：ttk 主题化、详情排版与卡片刷新按钮

- **滚动条**：clam 主题默认滚动条是暖灰浮雕配色，深色下与应用色板冲突。`ui_graphics.py` 的 `scrollbar_palette(theme)` 是纯函数（thumb/arrow 取 `mix_color(muted, trough, 0.35)`，active/pressed/disabled 走 accent token），`configure_scrollbars(style, theme)` 只做 `style.configure/style.map`，不建位图 element——thumb 长度仍由 Tk 按视图比例计算。宽 10 DIP、箭头 10 DIP，经 `dpi_scale` 换算物理像素，`gripcount=0`、`relief="flat"`、`borderwidth=0` 去掉浮雕与握把；两个构造点（`ScrollableBody` 与历史表格）显式指定 `Vertical.TScrollbar`，主题切换与 DPI 变化随 `_configure_style()` 重跑自动覆盖。`ScrollableBody` 的滚动条在 grid 固定列常显，内容不超高时只切 disabled 态而不隐藏（几何原因见下一节）。
- **下拉框与弹出层**：clam 的 `TCombobox` readonly 状态硬编码暖灰字段色（`#dcdad5`）、readonly-focus 是钢青 `#4a6984`，深色下表现为灰/白框；其弹出层不是 ttk 控件，而是走 Windows 系统色的经典 Listbox（路径 `<combo>.popdown.f.l`，不在 `winfo_children()` 树内）。`ui_graphics.py` 的 `combobox_palette(theme)` 与 `scrollbar_palette` 同为纯函数（单测在 `tests\test_ui_graphics.py`，断言调色板与泄漏色集合不相交）；`configure_combobox(style, theme)` 同时写 `TCombobox` configure/state map（`(readonly, focus)` 条目必须排在 `readonly` 之前才能压过 clam）、`ComboboxPopdownFrame` 和 option 数据库的 `*TCombobox*Listbox.*`——option 数据库只对之后懒创建的弹出层生效，已存在的弹出层由 `_set_theme()` 遍历到 Combobox 时经 `recolor_combobox_popdown()` 用裸 `tk.call` 直接重配。同类 clam 陷阱：复选/单选指示器只认 `indicatorbackground`/`indicatorforeground`，写 `indicatorcolor` 不生效，深色下会留下白色方块。
- **套餐详情排版**：辅助信息（重置时间、已用%、绝对值、完整重置、趋势）统一 9pt 的 `DetailMuted.TLabel` / `DetailWarning.TLabel`（`DETAIL_FONT_SIZE` 常量，设置窗的 `CardMuted` 仍为 10pt 不动），与 10pt bold 周期名、18pt 数值拉开三级字阶。三个周期行仍在同一个 `RoundedPanel` 内，行间是 1px `tk.Frame` 发丝线（`bg=border`，主题 remap 自动换色）；`_set_popup_layout()` 先 forget 全部行与分隔线再按可见行重 pack，N 个窗口只出现 N−1 条线，窗口集合变化无残留。
- **卡片刷新按钮**：几何常量对齐 macOS `CardRefreshButton`——32 DIP frame、12 DIP 右下外边距、6 DIP 内缩得到 20 DIP 命中框，全部落在 `desktop_widget.py` 的纯函数（`refresh_button_frame` / `refresh_button_hit_bounds` / `hit_refresh_button` / `refresh_progress_limit`，DIP 入参、物理像素返回，单测在 `tests\test_desktop_widget.py`，带 win32 平台守卫）。圆形底板按 normal/hover/pressed/disabled 四态换色，箭头用 Segoe UI Symbol 的 U+21BB 字形（负像素字号，跟随卡片自身 scale，不依赖进程全局 `tk scaling`）；所有 Canvas item 打 `refresh` tag，monthly 进度条截断到 `refresh_progress_limit` 防重叠。事件路由：命中判定先于边缘缩放与拖动，禁用态也消费手势；释放点重新命中判定，按下后拖出不触发；空白处单击/双击不再开窗（与 macOS 对齐，入口只剩托盘单击与右键菜单「打开详细用量」），拖动保存位置逻辑不变。

## Windows：可滚动窗体的几何稳定性（ScrollableBody）

设置窗、历史窗与弹窗共用 `ui_graphics.py` 的 `ScrollableBody`（Canvas + `create_window` 承载 content frame）。用户实测曾出现设置窗滚动条持续压缩、最终内容塌缩为空白，根因与约定如下，改动这个类时必须同时守住：

- **滚动条常显固定列，禁止再用 pack/pack_forget 切显隐**：canvas 与 bar 以 grid 放在第 0/1 列，bar 始终映射；内容不超高时 `bar.state(["disabled"])`（disabled 配色由 `configure_scrollbars` 的 state map 提供，无需新主题 token），超高时切 `!disabled`。旧实现按溢出条件动态 pack/forget 滚动条，使 canvas 物理宽度在 W 与 W−bar 宽之间跳变，触发 `<Configure>` → canvas item 宽度重钉 → wraplength 标签重排 → content reqheight 变化，又反馈回显隐决策，构成可把内容压缩到空白的几何反馈环。视觉上右侧始终保留一条细滚动条槽，不超高时为禁用态——Windows 经典应用的常见行为。
- **原生菜单回调不直接创建窗口**：`DesktopWidget` 的「设置」命令只用 `root.after(0, on_settings)` 安排后续 timer，让当前菜单命令先返回；不能用可能在其他嵌套 idle 处理中提前执行、或没有新事件时迟迟不执行的 `after_idle` 代替。Tk 8.6.15 的 Win32 popup 路径本身在 `TrackPopupMenu` 周围设置 `TCL_SERVICE_NONE`，所以这里保留现有 `after(0)`，不把它误改成菜单生命周期重写。菜单对象保持原有生命周期，不能在 `tk_popup()` 返回时立刻销毁，否则菜单项可能无法点击。
- **隐藏构建、保留复用、一次显示**：设置 `Toplevel` 首次创建后立即 `withdraw()`，材质/chrome、表单与 `_fit()` 都在隐藏状态完成；随后进入一次性 `_present()` 做 `deiconify`，下一事件轮次再从顶部锚定并 `lift/focus_force`。icon、fit/geometry、present 与 close/dispose 的同步原生边界全部使用 `tcl_events_deferred()`；`_fit/_present/_anchor/_rebuild` 的 after id 在关闭时取消，重复 close 为 no-op。`MonitorUI.open_settings()` 以 dialog 是否存在及 `_opening/_presented` 状态做 single-flight：构建中重入直接返回，可见窗口重复打开只 focus，异常则 `dispose()` 清理半成品。关闭只 `withdraw()` 并清空 Secret、取消预览，保留原生窗口和控件树；同 provider 重开从存储原地刷新 entries/变量/ClaudePanel，provider 改变才完整重建，普通重开不重复 fit/栅格化。应用退出和构建失败才 `dispose()` 真正销毁。隐藏期间外观变化在下次 `_sync_window_to_app()` 同步，模型自动拉取延后到首个可见 anchor，隐藏 reset 不发请求。首次打开不自动聚焦某个 Entry，避免半成品几何触发 FocusIn 滚动；provider 重建后的输入框聚焦与 Tab 揭示仍保留。首次显示还要按 fit 使用的同一工作区居中，避免 Windows 默认级联原点把底栏推到任务区下方。
- **wraplength 按实际父容器重算**：`_wrap_labels` 已把 Configure 突发合并成一次 idle walk，遍历时可跳过当前不足 100px 的父容器，并在值未变化时不写 `wraplength`。不得再按“canvas 宽度与上轮相同”整轮提前返回：面板可能首轮很窄、随后才变宽，provider 重建也会在同一个 canvas 宽度下加入新标签；这些迟到的 `<Configure>` 必须能重新走到此前跳过的标签。与其他列共享同一 master 的网格标签（Claude 卡片的模型状态条与「刷新模型列表」按钮同行，按钮占第 0 列）不能只按 master 宽减固定 inset 估算——那会把 wrap 算得比真实单元格宽，长状态文案在重排中途或窄窗内被裁。规则：仅对 `sticky="ew"`（宽度跟随加权单元格）的标签再取 `min(估算值, 实际分配宽度)` 封顶；`sticky="w"` 标签的 `winfo_width` 就是自身 reqwidth，封顶会把 wrap 锁死在收缩期的瞬态值且无法恢复，因此不适用，需要单元格跟随的标签应直接声明 `sticky="ew"`（见 `ClaudePanel.status_label`）。回归在 `gui_resize_smoke.py`（强制 400px 宽 + 单 pump 后 `assert_layout` 无窄/裁标签）与 `gui_geometry_smoke.py`。
- **焦点揭示的 `_ready`/`_armed` 双标志守卫**：`_reveal_focus` 只在 fit 已跑（`_ready`）且 canvas 经真实 `<Configure>` 达到过 fitted 高度（`_armed`）后才允许滚动。开窗首轮 `focus_set` 可能在布局中途以陈旧 `winfo_rooty` 算出 >1 的 yview 分数，Tk 钳到 1.0 就把视图钉在内容最底部——这是"看起来空白"的直接机制。另要求 `event.widget == focus_get()`（Tk 也会向祖先容器发送后代获得焦点的 `FocusIn`，不能让高表单容器抢滚到底部）、目标控件已映射且宽高 >1，最终分数落在 [0,1] 才执行 `yview_moveto`。用户手动缩窗不应永久禁用 Tab 揭示，因此武装条件选"达到过 fitted 高度"而非当前高度判断。
- **持久化高度封顶（`cap_height`）**：`WindowGeometry(..., cap_height=True)` 时，持久化高度不超过当次 fit 精算的 default 高度，防止布局风暴中被 flush 保存的异常值（现场曾出现 1048 DIP）重开一个只剩空白的高窗；不手改用户 data 文件，首次正常 flush 即自然回正。目前仅设置窗启用，popup 与历史窗行为不变。

回归在 `tests\gui_geometry_smoke.py` 与 `tests\gui_settings_native_smoke.py`（断言要点见上文 smoke 清单）。后者走真实原生菜单，不能只靠直接 `open_settings()` 的几何测试替代；直接窗口截图也不能用 `ImageGrab` 屏幕区域截图代替，否则前置终端遮挡时会把终端内容误存成设置窗“证据”。材质合成效果仍需屏幕目视，`PrintWindow` 只证明客户端内容确实绘制。

## Windows：打包便携版

构建依赖固定在 `requirements-windows-build.txt`（等价于可选依赖组 `.[windows-build]` 加上版本钉死），在 Windows 10/11 + Python 3.13 的 venv 里执行：

```bat
.venv\Scripts\python.exe -m pip install -r requirements-windows-build.txt
.venv\Scripts\python.exe scripts\build_windows.py
```

脚本一条龙完成，任一步失败即中止：

1. 清理旧的 `build\` 与 `dist\TokenPlanMonitor`（Defender 扫描新 exe 时会短暂占用文件，删除带 6 次线性退避重试）。
2. PyInstaller **onedir** 打包：`--windowed --name TokenPlanMonitor --icon assets/app.ico --manifest assets/TokenPlanMonitor.manifest --add-data "assets;assets"`，hidden imports 显式带上 `pystray._win32` 与 `keyring.backends.Windows`。manifest 替换 PyInstaller 默认模板，自带 PerMonitorV2 DPI 声明、common-controls v6 与 supportedOS，文件缺失时脚本在打包前直接报错。入口是包外的 `scripts/windows_launcher.py`——直接把包内 `__main__.py` 当脚本会被 PyInstaller 当成顶层脚本，相对导入失败。PyInstaller 子进程的 `PATH` 由 `build_environment()` 白名单化（只留 System32、Windows、Wbem、SysWOW64 与当前解释器目录）：构建机 PATH 上的第三方工具链（MiKTeX、Inkscape 等）若带物理 `ucrtbase.dll` / `api-ms-win-*.dll`，会经 PATH 回退搜索被收进包。
3. 校验 `TokenPlanMonitor.exe` 与 `_internal\assets\app.ico` 存在；`assert_clean_crt()` 递归检查 `_internal`，出现任何 `ucrtbase.dll` 或 `api-ms-win-*.dll` 直接判构建失败。随后把 `THIRD_PARTY_NOTICES.md` 与 `licenses\` 复制进产物目录。
4. 运行 `TokenPlanMonitor.exe --self-check`：windowed bootloader 没有 stdout，自检把结果写到 exe 旁的 `self-check-result.txt`。检查项包括 frozen 平台、全部关键导入（含 pystray / keyring 的 Windows 后端）、keyring 实际后端是 `WinVaultKeyring`、嵌入 manifest 的 PerMonitorV2 DPI awareness 真实生效（`GetThreadDpiAwarenessContext` 等于 PER_MONITOR_AWARE_V2，漏传 manifest 时构建失败）、经 `EnumProcessModulesEx` 枚举确认进程内只有一份且位于 `%SystemRoot%` 下的 `ucrtbase.dll`（双 CRT 会在原生窗口创建路径触发 `abort()` / 0xc0000409，Python 日志无任何 traceback）、真实 Tcl service-mode 导出路径与 NONE/ALL 往返、强制 ALL 下原生 `SetWindowPos(FRAMECHANGED)` 不重入排队 idle 且随后不丢失、离屏执行真实 `SettingsDialog._present()` 进程存活并恢复模式、便携 data/logs 路径、打包图标与 CA 证书可解析、配置与历史 JSON 往返、凭据管理器真实 set/get/delete 往返（用完即删）。`TCL_SERVICE_GUARD_OK`、`TCL_SERVICE_ROUNDTRIP_OK`、`TCL_SERVICE_DEFER_OK`、`TCL_SETTINGS_PRESENT_OK` 任一缺失都判构建失败。
5. 托盘存活探针：实际启动 exe，轮询 12 秒确认进程稳定、`logs\app.log` 生成且无 Traceback，然后 `taskkill /PID /T /F` 收尾。
6. 清掉探针产生的 data/logs，暂存 `dist\TokenPlanMonitor-<版本>-win64\` 并压缩为同名 zip，生成 `.sha256`（LF 行尾、basename 文件名，保证 `sha256sum -c` 直接可用），再回读压缩包复核摘要。

产物是便携布局：`TokenPlanMonitor.exe` + `_internal\`（运行时、DLL、tcl/tk、只读资源）+ 声明文件；运行期数据只写 exe 旁的 `data\`、`logs\`。版本号从 `token_plan_monitor.__version__` 推导，脚本里不硬编码。选 onedir 而非 onefile 是有意的：pystray / keyring 后端加载更稳、没有每次启动的解压临时目录、杀软误报面更小。

**UCRT 是系统组件，禁止随包分发。** Windows 10/11 自带 `ucrtbase.dll` 与 `api-ms-win-*` API 集，PyInstaller 对 System32 副本本就按目录规则排除，但 PATH 上第三方工具链目录里的物理副本会绕过该规则（实测 MiKTeX 的 2021 版 UCRT 曾被收进 0.2.0/0.3.0 产物，导致进程双 CRT、打开设置窗口时原生 `abort()` 闪退）。三道防线分别是构建期 PATH 白名单、产物 `assert_clean_crt()` 守卫、冻结自检的单 UCRT 探针；若守卫报错，先清理构建机 PATH 上的工具链目录，不要把这些 DLL 加进打包白名单。

发布资产校验：

```bat
certutil -hashfile TokenPlanMonitor-<版本>-win64.zip SHA256
sha256sum -c TokenPlanMonitor-<版本>-win64.zip.sha256
```

exe 未购买代码签名证书，首次运行的 SmartScreen 拦截是预期行为；面向使用者的安装与校验说明在 README 的 Windows 安装节。

## 显式实时验证

先通过应用设置保存真实 AK/SK，再在本机执行。实时查询默认永远跳过，必须显式启用。

macOS：

```sh
TOKEN_PLAN_MONITOR_LIVE=1 .venv/bin/python -m pytest -m live -s
```

Windows：

```bat
set TOKEN_PLAN_MONITOR_LIVE=1
.venv\Scripts\python.exe -m pytest -m live -s
set TOKEN_PLAN_MONITOR_LIVE=
```

实时测试只打印套餐名称和脱敏百分比，不打印 AK/SK、Authorization 或原始响应。单元测试里的固定签名只能防止实现回归，不能证明云端一定接受签名，因此首次使用必须与火山控制台或百炼控制台的数值交叉核对。

## 验收记录

每轮改动的验证结果与尚需实机复核的场景应在内部验收记录中留痕，该记录不随公开仓库分发。macOS 14 最低版本声明和二进制检查不能替代 macOS 14 实机运行测试。

发布前请确认 Release 资产自洽：GitHub 上当前 `TokenPlanMonitor-<版本>-arm64.dmg` 与 `TokenPlanMonitor-<版本>-win64.zip` 的实际摘要，分别与同目录 `.sha256` 文件内容一致，否则使用者按 README 校验必然失败。本地 `dist/` 在重新构建后与 `.sha256` 不一致是正常现象，以 Release 为准。Windows 资产发布后应重新下载 zip 与 `.sha256` 到空目录跑一次 `sha256sum -c`，走一遍使用者的真实路径。

镜像文件名现在由 `token_plan_monitor.__version__` 推导（`scripts/build_macos.py` 与 `scripts/build_windows.py` 同源），macOS 侧与 `CFBundleShortVersionString` 同源；发新版本改 `__init__.py`、`pyproject.toml` 与 `setup_macos.py` 三处版本号，不要在脚本里硬写文件名。
