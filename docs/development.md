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

脚本替换生成的 `build/`、`dist/TokenPlanMonitor.app` 和 DMG 暂存目录，产出独立应用、DMG 和可用 `shasum -a 256 -c` 校验的同名 `.dmg.sha256`。构建过程会检查所有 Mach-O 文件是 arm64、最低系统版本不高于 14、动态链接不依赖开发机路径，并验证项目及第三方许可证资源已入包、本地 ad-hoc 签名有效。Bundle ID 固定为 `com.zzcx2002.tokenplanmonitor`，应用数据不写进 `.app` 内。

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
- 在线模型清单：compatible-mode 地址推导（保留 region/代理主机）、图像/视频/语音/决策模型过滤、恶意模型 ID 形状拒绝、去重保序、非 200/断网/坏 JSON/空列表回退内置清单、合并保留内置标签与轻量标记并追加新模型、缓存按服务商隔离与损坏回退、新模型进下拉框而非自定义框；macOS smoke 以注入桩驱动「拉取→主线程排空→原地刷新下拉框」全链路并验证选中保留与下架模型降级。

## Windows：离线界面预览与交互检查

以下命令使用假数据覆盖 Agent Plan、Coding Plan、低余额、刷新中、过期、未配置和错误七种场景，不读取真实凭据、不访问云端。浅色与深色主题都会以 100%、150%、200% DPI 检查收起/展开弹窗、卡片文字边界与设置窗口滚动布局：

```bat
.venv\Scripts\python.exe tests\gui_preview.py
```

- `tests\gui_material_smoke.py`：独立假数据窗口里验证点击、拖动、置顶、悬停、隐藏恢复、无额外阴影窗口、字段内联校验、设置保存、主题保存与取消回滚、系统主题变化及主题切换不触发查询，不操作正在运行的监控实例。
- `tests\gui_material_preview.py`：额外生成浅色、深色条纹背景下的材质对照图（`artifacts\material-*.png`）。背景模糊与内部柔和阴影以屏幕截图为准，PrintWindow 窗口截图不含完整合成效果。
- `tests\gui_resize_smoke.py`：三档 DPI 下的最小尺寸、边缘缩放、刷新与主题切换保留尺寸、重启恢复。
- `tests\gui_chrome_smoke.py`：完整原生窗口边界、八方向缩放命中、最大化工作区、最小化恢复、关闭回滚与正常尺寸保留；完整窗口截图在 `artifacts\chrome-*-full.png`。
- `tests\gui_shell_smoke.py`：完整的托盘与桌面卡片原生交互检查，使用独立假数据外壳，先关闭其他监控实例再运行。它会实际操作托盘悬停/右键、卡片悬停/单击/拖动/置顶/隐藏与恢复，并通过托盘退出和重启验证偏好持久化及屏外位置校正。所有数据在临时目录，凭据后端禁用，不访问云端；截图在 `artifacts\native-*.png`。**运行期间不要移动鼠标或操作键盘。**
- `tests\gui_smoke.py`：兼容入口，同样运行隔离交互检查，不需要预先启动应用。
- `tests\gui_settings_provider_smoke.py`：设置窗服务商切换全链路——双 provider 的 keyring 预置、切换后 region 回退与告警值保留、AK/SK 按 provider 隔离不串号、保存后监控收到正确 provider、旧 provider 凭据不变；不访问云端、不起托盘。
- `tests\gui_history_smoke.py`：用量历史窗口与 macOS 同源规则——每个存在的窗口一张图、重置断点不连线、24 小时/7/30/90 天范围切换、悬停提示走共享纯函数、「数据列表」披露表（无绝对值时隐藏该列）、空/薄数据文案、主题重绘与窗口生命周期；全部使用临时目录假数据。
- `tests\gui_claude_smoke.py`：Claude Code 接入——所有写入经 `claude_paths_override` 落临时目录，**绝不触碰真实 `~/.claude`**；模型清单拉取与确认框均为注入桩。覆盖带备份的校验写入与 onboarding 合并、推理 Key 不回显、取消/校验失败零写入、按 provider 的草稿记忆、后台清单更新、shutdown 清空内存 Key 草稿。

GUI smoke 依赖 Tk 事件循环，曾观察到偶发的 Tk GIL 致命错误（与改动无关的环境抖动）；改动 Windows 壳后每个相关 smoke 至少连跑两遍，单跑一次通过不能作为绿灯依据。

截图保存在 `artifacts\theme-*.png`，其中 `theme-light/dark-popup-collapsed/expanded.png`、`theme-light/dark-widget.png`、`theme-light/dark-settings.png` 是两套主题的代表截图。需要重新生成多尺寸应用图标 `assets\app.ico` 和 README 使用的 `assets\app.png` 时，运行 `.venv\Scripts\python.exe scripts\generate_app_icon.py`，再重新执行快捷方式脚本。

## Windows：打包便携版

构建依赖固定在 `requirements-windows-build.txt`（等价于可选依赖组 `.[windows-build]` 加上版本钉死），在 Windows 10/11 + Python 3.13 的 venv 里执行：

```bat
.venv\Scripts\python.exe -m pip install -r requirements-windows-build.txt
.venv\Scripts\python.exe scripts\build_windows.py
```

脚本一条龙完成，任一步失败即中止：

1. 清理旧的 `build\` 与 `dist\TokenPlanMonitor`（Defender 扫描新 exe 时会短暂占用文件，删除带 6 次线性退避重试）。
2. PyInstaller **onedir** 打包：`--windowed --name TokenPlanMonitor --icon assets/app.ico --add-data "assets;assets"`，hidden imports 显式带上 `pystray._win32` 与 `keyring.backends.Windows`。入口是包外的 `scripts/windows_launcher.py`——直接把包内 `__main__.py` 当脚本会被 PyInstaller 当成顶层脚本，相对导入失败。
3. 校验 `TokenPlanMonitor.exe` 与 `_internal\assets\app.ico` 存在，把 `THIRD_PARTY_NOTICES.md` 与 `licenses\` 复制进产物目录。
4. 运行 `TokenPlanMonitor.exe --self-check`：windowed bootloader 没有 stdout，自检把结果写到 exe 旁的 `self-check-result.txt`。检查项包括 frozen 平台、全部关键导入（含 pystray / keyring 的 Windows 后端）、keyring 实际后端是 `WinVaultKeyring`、便携 data/logs 路径、打包图标与 CA 证书可解析、配置与历史 JSON 往返、凭据管理器真实 set/get/delete 往返（用完即删）。
5. 托盘存活探针：实际启动 exe，轮询 12 秒确认进程稳定、`logs\app.log` 生成且无 Traceback，然后 `taskkill /PID /T /F` 收尾。
6. 清掉探针产生的 data/logs，暂存 `dist\TokenPlanMonitor-<版本>-win64\` 并压缩为同名 zip，生成 `.sha256`（LF 行尾、basename 文件名，保证 `sha256sum -c` 直接可用），再回读压缩包复核摘要。

产物是便携布局：`TokenPlanMonitor.exe` + `_internal\`（运行时、DLL、tcl/tk、只读资源）+ 声明文件；运行期数据只写 exe 旁的 `data\`、`logs\`。版本号从 `token_plan_monitor.__version__` 推导，脚本里不硬编码。选 onedir 而非 onefile 是有意的：pystray / keyring 后端加载更稳、没有每次启动的解压临时目录、杀软误报面更小。

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

发布前请确认 Release 资产自洽：GitHub 上当前 `TokenPlanMonitor-<版本>-arm64.dmg` 与 `TokenPlanMonitor-<版本>-win64.zip` 的实际摘要，分别与同目录 `.sha256` 文件内容一致，否则使用者按 README 校验必然失败。macOS 本地构建会一起更新并校验 DMG 和摘要文件。Windows 资产发布后应重新下载 zip 与 `.sha256` 到空目录跑一次 `sha256sum -c`，走一遍使用者的真实路径。

镜像文件名现在由 `token_plan_monitor.__version__` 推导（`scripts/build_macos.py` 与 `scripts/build_windows.py` 同源），macOS 侧与 `CFBundleShortVersionString` 同源；发新版本改 `__init__.py`、`pyproject.toml` 与 `setup_macos.py` 三处版本号，不要在脚本里硬写文件名。
