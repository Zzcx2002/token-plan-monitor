# 开发者指南

面向使用者（安装、配置、日常使用、凭据、隐私）的说明在 [README.md](../README.md) / [README.en.md](../README.en.md)。本文只讲源码运行、打包、测试与验收。

- [环境与目录](#环境与目录)
- [macOS：源码运行](#macos源码运行)
- [macOS：打包 DMG](#macos打包-dmg)
- [macOS：离线界面验证](#macos离线界面验证)
- [README 截图的再生成](#readme-截图的再生成)
- [Windows：依赖与测试](#windows依赖与测试)
- [Windows：离线界面预览与交互检查](#windows离线界面预览与交互检查)
- [显式实时验证](#显式实时验证)
- [验收记录](#验收记录)

## 环境与目录

- 共享核心在 `src/token_plan_monitor/`：`models.py`（数据契约与输入校验）、`providers.py`（服务商注册表、凭据 account、Claude Code 元数据）、`storage.py`（数据目录与钥匙串/凭据管理器）、`monitor.py`（后台刷新调度）、`presentation.py`（屏上文案）、`claude_code.py`（写入 Claude 配置）、`volcengine.py` / `bailian.py` / 两个 signer。
- macOS 壳在 `src/token_plan_monitor/macos/`：`ui.py`（菜单栏、详情窗口、桌面卡片、设置窗口）、`layout.py`（纯规则：校验、恢复动作、最低项标记、几何）、`theme.py`（动态色、字阶、材质）、`preview.py`（离线预览与 `--smoke-test`）。
- Windows 壳在 `src/token_plan_monitor/ui.py`、`desktop_widget.py`、`ui_graphics.py`、`window_chrome.py`、`window_geometry.py`、`appearance.py`。
- 数据目录：macOS 用 `~/Library/Application Support/TokenPlanMonitor/` 与 `~/Library/Logs/TokenPlanMonitor/`；其他平台用项目内 `data/` 和 `logs/`（两者都在 `.gitignore` 里）。
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

## README 截图的再生成

`docs/images/` 里的四张图来自离线预览，数值是假数据。重新生成后按下面的对应关系复制：

| `docs/images/` | 来源 |
| --- | --- |
| `card-ark.png` | `artifacts/macos-light/card-agent.png` |
| `card-ark-dark.png` | `artifacts/macos-dark/card-agent.png` |
| `details-ark.png` | `artifacts/macos-light/details-agent.png` |
| `settings-claude-code.png` | `artifacts/macos-light/settings-claude.png` |

```sh
.venv/bin/python -m token_plan_monitor.macos.preview --smoke-test --appearance light --artifacts artifacts/macos-light
.venv/bin/python -m token_plan_monitor.macos.preview --smoke-test --appearance dark --artifacts artifacts/macos-dark
cp artifacts/macos-light/card-agent.png       docs/images/card-ark.png
cp artifacts/macos-dark/card-agent.png        docs/images/card-ark-dark.png
cp artifacts/macos-light/details-agent.png    docs/images/details-ark.png
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
- Claude Code 写入：键合并范围、备份及**备份文件自身**的 `0600` 权限、历史全局可读备份的收紧、JSONC 读取、损坏文件拒绝写入、幂等重放、推理 Key 不进入确认文本；Region 与服务商地域白名单、Base URL 拒绝含账号密码（`user:pass@host`）的地址。

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

截图保存在 `artifacts\theme-*.png`，其中 `theme-light/dark-popup-collapsed/expanded.png`、`theme-light/dark-widget.png`、`theme-light/dark-settings.png` 是两套主题的代表截图。需要重新生成多尺寸应用图标时运行 `.venv\Scripts\python.exe scripts\generate_app_icon.py`，再重新执行快捷方式脚本。

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

[docs/macos-validation.md](macos-validation.md) 记录每轮改动的验证结果与尚需实机复核的场景。macOS 14 最低版本声明和二进制检查不能替代 macOS 14 实机运行测试。

发布前请确认 Release 资产自洽：GitHub 上 `TokenPlanMonitor-0.1.0-arm64.dmg` 的实际摘要与同目录 `.sha256` 文件内容一致，否则使用者按 README 校验必然失败。本地 `dist/` 在重新构建后与 `.sha256` 不一致是正常现象，以 Release 为准。
