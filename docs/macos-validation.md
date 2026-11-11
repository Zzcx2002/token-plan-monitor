# macOS 验收记录

日期：2026-11-08。分支：`codex/macos-app`。

开发/验证机器：Apple Silicon，macOS 26.6（25G72）。构建使用 python.org Python 3.13.9 framework、py2app 0.28.10；Python 及依赖都在项目隔离目录，未安装到系统 Python。依赖版本见 `requirements-macos-build.txt`。

## v0.1.1 发布记录（2026-11-11）

分支 `release/v0.1.0`，提交 `3f005be`，附注标签 `v0.1.1`。

- 为什么是新版本而不是原地替换 v0.1.0 资产：`v0.1.0` 标签指向 `ffe322d`，早于本次界面重做与安全修复。直接换掉 v0.1.0 的资产会让该 Release 的镜像与它自己的标签不一致，且已有 2 次下载记录，同名不同内容比多一个版本号更糟。
- 版本名单一来源：镜像文件名改由 `token_plan_monitor.__version__` 推导（`scripts/build_macos.py`），与 `CFBundleShortVersionString` 同源。此前脚本里硬写 `TokenPlanMonitor-0.1.0-arm64.dmg`，正是产物名与包内版本能各自漂移的原因。`CFBundleVersion` 1 → 2。
- 发布前对最终产物验证：Info.plist 报告 `0.1.1` / build `2`、无 `NSRequiresAquaSystemAppearance`；`codesign --verify --deep --strict` 通过；`hdiutil verify` 通过；109 个 Mach-O 均为 arm64 且部署目标不高于 macOS 14；从只读挂载的镜像运行打包应用的原生 smoke test，浅色与深色各一次。
- 发布后按使用者路径复验：从 Release 资产链接重新下载 DMG 与 `.sha256`（均 200，34263756 / 99 字节），`shasum -a 256 -c` 通过，挂载后 plist 与签名正确，从该挂载副本再次跑通深色 smoke test。GitHub 侧计算的镜像摘要与本地一致（`a929060578e01…`）。
- 未改动 v0.1.0：其 Release 与两个资产保持原样，已核对它自己的 `.sha256` 仍与 DMG 摘要一致，老用户的校验不会失败。
- 离线测试 269 passed、3 skipped；`README.md` / `README.en.md` 的下载与校验链接已指向 v0.1.1。

仍未实机验证的项目与本次发布一同记入 Release 说明：干净 macOS 14、真实钥匙串/OpenAPI、VoiceOver、硬件鼠标、多屏/Spaces、浏览器下载后的 Gatekeeper 流程，以及深色模式下只能目视的背景模糊、窗口外阴影和"激活窗口点亮正确输入行"。

## Claude Code 接入的两处安全缺陷修复（2026-11-11）

这两处是在界面改版的独立复查中顺带发现的，属于并行推进中的 Claude Code 接入功能，不是界面改造本身。两处都先在本仓库代码上实测复现，再动手修。

### 缺陷与复现

- **备份是全局可读的，里面是明文 Key。** `backup_file` 用 `write_bytes` 创建备份，默认 umask 下得到 `0644`；而它备份的 `settings.json` 在首次写入后含明文 `ANTHROPIC_AUTH_TOKEN`，且 `apply_request` 是**先备份再写**，于是每一份历史备份都是一份历史密钥的副本。同一模块里 `write_document_atomic` 已把正式文件刻意锁成 `0600`——防护只做在写入侧，漏了备份侧。
  复现：live `0o600`，backup `0o644`，备份内容含 `sk-sp-OLD`。
  本机现状说明：唯一的 `~/.claude/settings.json.bak.20261110-160522` **不是**本应用产生的（命名不是 `int(now)` 的秒级格式，且已是 0600），所以这个漏洞在本机尚未实际发生；但用户用过一次写入就会出现。
- **Region 未校验就被拼进 Base URL。** 接入路径直接用字段原文渲染服务商模板（裸 `str.format`）。复现：Region 填 `cn-beijing@evil.example.com/` 得到 `https://token-plan.cn-beijing@evil.example.com/.maas.aliyuncs.com/apps/anthropic`，其真实主机名是 `evil.example.com`，密钥会被发往那里。`normalize_base_url` 放行它的原因是其中一条提前返回只看 path 含 `/apps/anthropic` 就原样返回；它检查 scheme 与 `netloc` 非空，但既不看 userinfo 也不看主机身份。保存路径本来**有**校验（委托给 `Settings`，正则 + 服务商地域白名单），接入路径完全绕开；对比控制面 `bailian.py` 的 `_reject_unofficial_host` 守卫，接入路径是唯一让 Region 未经清洗进入 URL 的地方，且下游无人复校。

### 修法

- 备份改用本模块既有的**私有临时文件 + rename** 惯用法（与 `write_document_atomic` 同形）。两处 `chmod` 都必需：`O_CREAT` 的 mode 会被 umask 削掉，而 rename 保留的是临时文件的 mode。
  同秒重复写入仍然覆盖而不报错：这是文档承诺的"结果一致"，且一个尚未触碰 live 文件的动作不该因撞名而中止（`O_EXCL` 还会让烟测里的连续写入随机变红）。
  **中途改过一次实现**：最初写的是"就地 `O_TRUNC` + 失败时删除目标"，反向验证暴露它会在写入失败时把**上一份备份**清空甚至删掉——恰是最需要回滚副本的时刻。改为暂存到 `{name}.bak.<秒>.tmp` 再 rename，失败只清理临时文件；临时名带自己的后缀，任何 `*.bak.*` 扫描都不会把它误认成已完成的备份。
- 顺手收紧**本应用自己命名**的历史备份（只匹配 `{name}.bak.<纯数字>`）：手工命名的 `settings.json.bak.20261110-160522` 与无关文件一律不碰；收紧失败只静默跳过，绝不影响刚成功的写入。
- Region 规则从 `Settings.__post_init__` 抽成 `models.validate_region(provider, region)`，保存路径与接入路径共用同一实现；接入路径在**渲染模板之前**校验，并以 `FieldValidationError("region", ...)` 报出，使 UI 把焦点落到 Region 行。报错文案逐字不变。
- `normalize_base_url` 在 `netloc` 检查之后、那条提前返回**之前**拒绝带 userinfo 的地址（`"@" in netloc`，可同时拦住空 userinfo）。位置很关键：放在提前返回之后就等于没拦。这不破坏"用户可自填任意代理域名"的既有契约——主机仍然任意，只是禁止把正规域名变成 userinfo。
- 不加第三层"模板产物主机后缀白名单"：Region 经上述校验后只可能是 `[a-z0-9-]`，无法再携带 `@` 或 `/`，该检查属于冗余，且要新增服务商数据位。若将来出现第二个带 Region 模板的服务商，再重新评估。

### 验证

- 离线 pytest：**269 项通过**（本次新增 14 项：备份自身权限、历史备份被收紧而外来文件不受影响、同秒重复不失败且只留一份、三种 userinfo 地址被拒、代理域名仍然通过、恶意/越界 Region 在接入路径被拒并定位到 region 字段、空 Region 仍回落官方地址、两个受支持地域各自生成正确主机）。
- **每条新断言都做过反向验证，而第一轮验证揪出了一个空过测试**：最初的"备份权限"断言在改回 `write_bytes` 的缺陷版本上**仍然通过**——因为随后收紧历史备份的扫尾把刚写坏的新备份也一并修好了，两条机制互相掩盖。于是补了一条只针对写入路径本身的测试（在 rename 那一刻查看临时文件的 mode，断言备份在拿到最终名字之前就已经是 0600）；只有它能区分两种实现：缺陷版本根本没有临时名，立刻失败。
  最终矩阵里每个退化变体只让对应的那一条失败，互不掩盖：改回 `write_bytes` → 失败于"私有于最终名之前"；删掉扫尾收紧 → 失败于"历史全局可读备份被收紧"；改回就地 `O_TRUNC` → 失败于"备份失败不毁掉上一份"；删掉 userinfo 检查 → 3 条失败；去掉 Region 闸门 → 3 条失败。恢复后全绿。
- 既有文案约束确认：`tests/test_macos.py` 与 `tests/test_providers.py` 中原有的 Region/默认地域断言**一字未改**地通过，说明这次抽取没有改变任何用户可见提示。
- 原生烟测（含 hermetic `claude_paths_override` 的真实写入流程）浅色、深色各通过一次；源码运行与打包应用均验证。
- 文档同步：中英文 README 的"安全""要知道的代价""Base URL 与模型""彻底清除"四处，以及 `docs/development.md` 的覆盖清单，都改为同时覆盖备份权限与 URL 校验。

### 遗留

- 备份数量无上限、无轮转：每次写入累积一份。属磁盘卫生而非安全问题，且轮转要引入删除用户文件的决定，留作单独议题。
- `~/.claude/settings.json` 里密钥仍是明文（Claude Code 通过环境变量消费，无法避免），README 已如实说明；本次保证的是**只有本用户可读**，且不再从备份外泄。
- UI 层 Region 仍是自由文本输入，校验放在规则层由两条路径共享；未做实机 VoiceOver 或真实网络验证。

## 界面质感重做与深色模式（2026-11-11）

本轮不改布局结构之外的任何业务行为，只重做视觉：颜色、字体、圆角、输入框和窗口分层，并解除 v0.1.0 的强制浅色记录。

### 改了什么

- 新增 `src/token_plan_monitor/macos/theme.py`：颜色、字阶、圆角、间距的唯一来源。窗口和卡片不再使用硬编码 `colorWithCalibratedWhite/RGB`，也不再直接写 `layer().setBackgroundColor_`。
- 主窗口、设置窗口和桌面卡片跟随系统浅色/深色外观。原先三处强制浅色（`ui.py` 两个 `setAppearance_(Aqua)`、`setup_macos.py` 的 `NSRequiresAquaSystemAppearance`）全部移除；打包后 Info.plist 不再包含该键，需重建应用才生效。
- 卡片去描边：不再画 0.5pt 灰线，层级完全由“卡片底色高于页面底色、输入凹槽低于卡片底色”表达，圆角改用系统的连续曲线（squircle）。
- 设置窗改成系统设置式的分组卡片（“连接服务商”“Claude Code 接入”），22pt 粗体大标题降为 15pt 半粗体；输入框改为无边框文本框 + 圆角凹槽，静止时无任何描边，聚焦时由凹槽画强调色描边。
- 字重全面从 true bold 降到 semibold/medium；百分比和时刻数字改用等宽数字 + SF Rounded 字面，刷新时不再左右抖动。
- 状态色（正常/过期/失败/刷新中）按外观配对：浅色沿用 v0.1.0 已验收的深色字值，深色改用新的浅色字值。实测对比度在两种外观、卡片与页面两种底上均 ≥ 4.5:1；Apple 自带 `systemGreen/Orange/Red` 在本应用的深色卡片上只有 2.65–4.26:1，因此没有直接采用。
- 次要文字（12pt 说明、11pt 时间）不用 `secondaryLabelColor`：实测它在白卡上 3.95:1、深色卡上 4.14:1，低于本项目在 Windows 侧已执行的 4.5:1 标准，改用同色相但更实的配对灰。

### 本轮踩到并修掉的坑（后续改配色请注意）

- `colorWithName:dynamicProvider:` 造的动态色**按名字存储**。四个状态色共用一个名字时，控件重新设色只会保留第一次的结果：过期和失败状态的标题一直显示“正常”的绿色，而文字已经改变。现在每个颜色名唯一，并由 `preview.py` 的 `assert_status_tone` 在十一种状态×两种外观下逐状态核对实际标签颜色。
- 动态色在 `NSTextField.textColor` 读写、`CGColor()` 解析上都正常，但 `NSViewBackingLayer` 的 `backgroundColor`/`borderWidth` 无法通过属性读回；因此“输入框聚焦描边是否存在”改为在截图像素上验证，而不是读 layer 属性。取样也不能沿横线走——那会穿过页脚的蓝色默认按钮，报出一个并不存在的描边，所以改为沿凹槽左边缘竖着取。
- 描边颜色跟随系统“强调色”，所以像素断言不能假设它是蓝色（换成石墨色/绿色强调色的 Mac 上描边画得对、却匹配不上）。现在改为差分验证：同一列在“点亮前/点亮后”取样比较，只有被点亮的行边缘变化超过阈值、其余在屏行几乎不变才算通过；该测试已用“关掉描边”反向验证会失败。`theme.dynamic()` 也加了重名即抛错的守卫，重名这类错误从此不可能再悄悄发生。
- `systemFontOfSize:weight:design:` 在本机 PyObjC 是假可用：任何 design 值都返回 Helvetica。圆润字面要走 `fontDescriptor().fontDescriptorWithDesign_(NSFontDescriptorSystemDesignRounded)`，且叠加在等宽数字上仍保持等宽（实测同宽度）。
- 无边框文本框既不会因 `makeFirstResponder_` 发出编辑开始通知，也不会在失焦时收到 `resignFirstResponder`（窗口的 first responder 是共享的 field editor）。焦点色统一由“谁持有 field editor”判定，并在窗口更新通知与代码主动聚焦两处刷新；写这条测试时发现并修掉一个真实缺陷：窗口还没有 first responder 时 `nil is nil` 会让**所有**行同时点亮。
- 焦点还要求窗口是 key window：切到别的程序后 AppKit 仍保留 field editor，不判断就会在失焦窗口上留着一圈亮蓝描边。检查脚本没有运行事件循环，`isKeyWindow()` 在烟测里始终为假，因此“key 窗口点亮正确的行”这半只能实机确认；烟测会在 `smoke-result.txt` 里写出实际跑到的是哪一支，避免把“非 key 时全灭”误读成完整验证。

### 本轮验证结果

- 离线 pytest：255 项通过（新增 `tests/test_macos_theme.py` 30 项：状态色与次要文字在两种外观、两种底色上的对比度；动态色按外观正确解析且互不覆盖；页面/卡片/凹槽三层明暗关系；字阶不低于 11pt、无 true bold；等宽圆润数字宽度一致；焦点点亮规则本身用桩对象验证——含“非 key 窗口一律不点亮”和“无 field editor 时不得全亮”）。3 项按条件跳过（Windows 原生特效、实时接口）。
- 原生假数据窗口检查 `--appearance light` 与 `--appearance dark` 各通过一次，覆盖全部状态、默认/较小/加宽窗口、设置校验与帮助、卡片手势与缩放、焦点与滚动保持。
- `--smoke-test` 的截图检查包含逐状态标题颜色、输入框聚焦描边差分、以及“校验/写入结果必须落在可见区域”的检查；两组新断言都做过反向验证（恢复共用颜色名、关掉描边时均确实失败）。
- 设置窗的校验与写入结果文本原先放在可滚动文档末尾，而窗口高度封顶 640pt：聚焦出错字段只会把**字段**滚进视口，消息仍在折线以下，用户点了保存却看不到任何回应（`settings-first-use.png` 当时完全没有错误文字）。现在这条提示固定在顶部常驻区，实测落在窗口 y=540–574（可视区 0–640）内；提示带按需增高，不剪裁文本。
- 同一路径还修掉一个配色串台：`save_settings` 直接写标签文本而不设颜色，于是“写入 Claude 配置成功”（绿色）之后再触发校验失败，错误文字会以**成功绿**显示。
- 视图截图仍不包含背景模糊、窗口外阴影与系统原生焦点环（自绘的输入框聚焦描边会出现在截图里，系统控件的原生焦点环不会），这三项只能实机目视，见下方手动清单。
- `smoke-result.txt` 现在会写出焦点检查实际跑到的是哪一支。本机两次运行都是 `['inactive']`——烟测没有事件循环，窗口永远不是 key window，所以“key 窗口下点亮正确的那一行”只有自动化的桩测试和实机目视两层保障，不要把这条通过当成端到端验证。
- 字阶角色此前只有字号生效：`text_label` 从 `ROLES` 取 size，却只按 `bold` 参数决定字重，于是 15pt 半粗体的分组标题实际渲染成 Regular，和下面 13pt 的字段标签几乎分不出层级——正是这次改版要修的“层级丢失”。现在角色同时决定字号与字重，并由测试锁住（`test_role_weights_reach_the_font_not_just_the_table`）。
- 设置窗切换服务商会把窗口尺寸弹回 500pt：`build_settings` 固定用 `SETTINGS_WIDTH` 重算。现在保留用户拉宽的宽度（实测 680pt 切换后仍是 680，字段随之重新量宽）。这项在分组卡片开始跟随宽度之后尤其明显。
- 校验/写入提示的颜色此前只有一条写入路径负责：`show_form_message` 会按 tone 设色，而 `save_settings` 的两处直接 `setStringValue_`，于是成功写入（绿）之后触发校验失败，错误文字会以绿色显示。已统一走一个写入者，并把“先成功再失败”的顺序写进烟测——只查第一次失败的配色是查不出这个 bug 的（标签本来就是红色），反向验证过。
- 详情窗卡片此前左 12pt、右 36.5pt（文档为右侧滚动条预留了 24pt，卡片却没把它算进去）。去描边之后卡片边缘就是唯一的分块依据，偏心会直接读成排版错误；现改为在视口中居中（实测 24/24.5pt，480 与 720 两种宽度一致），卡片与内容的宽度关系仍由 `assert_details_width` 精确钉住。
- 分组卡片此前不随窗口宽度变化（自动 resize 掩码为 0），字段拉到 654pt 时卡片仍停在 476pt，整行溢出自己的分组——去描边后卡片边缘是唯一的分组线索，这会被读成布局坏了。已改为跟随宽度，烟测的加宽检查也覆盖到。

### 需要实机补充的验收项

- 在系统设置里来回切换浅色/深色：确认三个界面即时跟随，无白闪、无残留浅色块；开启“降低透明度”后再各切换一次，确认卡片仍为实心圆角。
- 逐个点击设置的输入框：确认聚焦时凹槽出现强调色描边、失焦即消失，且同一时刻只有一行高亮；Tab 顺序与回车保存不变。
- 深色菜单栏下的托盘图标与状态圆点是否清晰。
- 重建应用后 `plutil -p dist/TokenPlanMonitor.app/Contents/Info.plist` 不再出现 `NSRequiresAquaSystemAppearance`。

## 设置窗口界面修复验证（2026-11-10）

分支：`release/v0.1.0`。修三项用户实测反馈：设置窗口过长、拖宽无反应、火山/百炼来回切换时模型与 Base URL 丢失。桌面卡片未加入口，Windows 端仍不提供该功能。

- **高度**：正文改为 `NSScrollView` + 固定页脚，默认封顶 640pt（此前实测 500×960pt，`artifacts/macos/settings*.png` 现全部为 500×640pt）。服务商下拉固定在滚动区之上，`取消 / 保存设置` 固定在页脚。
- **宽度**：改由 autoresizing mask 承载（字段与标签左右边距固定、宽度弹性；左对齐按钮钉左、页脚按钮钉右），配合 `layout_settings()` 只做尺寸同步、**不重建表单**——重建会丢输入和焦点。最小宽度锁死 500：所有换行文本高度按 452pt 量得，允许变窄就会裁字。
- **切换丢数据**：新增按服务商的内存草稿（非敏感三字段同时落 `claude.json`，推理 Key 仅内存、不回显）。
- 取值优先级**修正为 本次填写 → 磁盘真实配置 → 已存偏好 → 服务商默认**，其中磁盘真实配置排在已存偏好之前。这偏离了原计划写的 `draft → prefs → live`：照原顺序，用户手工改过的接入地址会被下一次写入静默还原成旧值，违背 README 里已承诺的安全性质；是既有 smoke 断言（重放不得重置代理域名）暴露了这一点，据此改了纯函数顺序而非放宽断言。
- 离线 pytest：**225 项通过，3 项跳过**。新增 `resolve_claude_seed` 纯函数与 6 项单测（含"从未配置过的服务商必须拿到磁盘真值而不是空白"这条回归钉版）。
- 原生 smoke 新增：窗口高度上限、正文确实可滚动（文档高 > 视口高）、加宽后字段变宽且 `origin.x` 不变、页脚按钮跟随右缘并保持 12pt 间距与间隙、还原宽度后尺寸回到原值、百炼→火山→百炼往返后模型与 Base URL 原样恢复、Key 不回显但字段标题变为"已保存，留空保持不变"且留空提交仍能写入、`claude.json`/`config.json` 中不含该 Key。
- 新增断言抓出一处真实缺陷：页脚 `取消` 按钮我按 `width - 236` 摆放，实际应为 `width - 248`，导致与 `保存设置` 的 12pt 间隙被吃掉、并整体右移 12pt（改代码而非放宽断言）。
- 顺带删除一段脆弱坐标逻辑：`toggle_credential_help` 原先靠移动两个页脚按钮 + 改窗口尺寸腾空间，现仅切换隐藏并调整文档高度。

仍未实测：对真实 `~/.claude/settings.json` 的端到端写入与重开会话生效验证（离线 smoke 全程写入临时目录）；火山方舟 Anthropic 端点可用性（无可靠来源，界面标未验证、默认留空）；Windows 端（本期无该入口，仅 `compileall` 与导入隔离检查）。

## Claude Code 接入改动验证（2026-11-10）

分支：`release/v0.1.0`。设置窗口新增「Claude Code 接入」段落：选模型后把服务商接入信息写入 `~/.claude/settings.json`（遵循 `CLAUDE_CONFIG_DIR`），推理 Key 独立存钥匙串，桌面卡片不加入口。

- 离线 pytest：**219 项通过，3 项跳过**。新增 `tests/test_claude_config.py`（36 项：Base URL 归一化与改写、别名→档位解析、merge-only 保留未知键且不 mutate 入参、JSONC、0600、备份、幂等重放、**畸形文件零写入且字节不变**、`env` 非对象拒绝写入、`.claude.json` 畸形则跳过、repr 不泄漏 Key），以及 storage / providers / macos layout 的接入用例。
- 原生 AppKit smoke（`--preview --smoke-test`）通过，并把整条写入路径**完全隔离在临时目录**（`claude_paths_override`）：新建文件、从真实配置预填（代理域名不被重置）、仅改别名对应档位而保留 sonnet 手配分层、生成 `settings.json.bak.*`、写入期间挂起窗口重建、火山方舟因端点未验证而拒绝并留下原因。
- 测试期间抓出两处真实缺陷：临时文件清理用 `unlink(missing_ok=True)` 会把 `NotADirectoryError` 之类二次错误抛出、掩盖原始失败原因（改为 best-effort `_discard`）；默认时钟误写 `os.time`（该属性不存在）。
- 与参考实现的**有意分歧**，两处都更安全，已在 `THIRD_PARTY_NOTICES.md` 记录：畸形 settings 拒绝写入而非替换成空文档；只改顶层别名对应的那一档，不清空用户手配的分层模型。

仍未实测：用真实 `sk-sp-…` 对**真实** `~/.claude/settings.json` 做端到端写入并重开 Claude Code 会话验证生效（离线 smoke 全程走临时目录，不碰真实配置）；火山方舟的 Anthropic 兼容端点是否可用（无可靠来源，界面上标为未验证且默认留空）；Windows 端（本期未提供该入口，仅做编译与导入隔离检查）。

## 多服务商（阿里云百炼）改动验证（2026-11-10）

分支：`release/v0.1.0`。为「更换其他订阅的 token 余量查询」新增服务商抽象与阿里云百炼（Token Plan 个人版 / Coding Plan）数据源；macOS 设置窗口新增服务商选择器，Windows 端保持只查火山方舟但做防回归处理。

- 离线 pytest：**158 项通过，3 项跳过**；跳过项为 Windows 原生特效与两个 `TOKEN_PLAN_MONITOR_LIVE=1` 实时接口测试。新增 `tests/test_bailian.py`（23 项：比值→百分比换算、毫秒重置时间、窗口动态发现与排序、信封解包、NotLogined 只重试一次、回落与错误分类、非官方地址拒绝）、`tests/test_providers.py`、以及 signer/storage/monitor/presentation/macos 的 provider 用例。
- ACS3-HMAC-SHA256 固定回归向量由**独立参考实现**（按公开签名规则另写的脚本）推导，不复制被测模块逻辑；该向量在开发过程中抓出两处真实缺陷（`NotLogined` 大小写匹配、`x-acs-date` 缺冒号）。
- 原生 AppKit 假数据 smoke（`--preview --smoke-test`）通过：状态数由 10 增至 14，新增百炼 Token Plan（仅百分比、无绝对额度）、百炼 Coding Plan（含 Credits 绝对额度）、百炼未配置与鉴权失败；并新增设置窗口断言：服务商切换为延迟重建、不支持的 Region 回落 `cn-beijing`、字段标签随服务商变化、两个服务商的钥匙串条目互不覆盖、Secret 不写入 `config.json`。截图输出于 `artifacts/macos/`（含 `settings-bailian.png`、`details-bailian_token.png`、`details-bailian_coding.png`、`card-bailian_token.png`），人工目视确认标题、单位与无裁切。
- `git diff --check`、`python -m compileall src tests` 通过；`tests/test_macos.py::test_entrypoint_does_not_import_windows_ui` 仍通过。
- 百炼两个官方端点（`modelstudio.cn-beijing.aliyuncs.com` 的 `generateAccessToken`、`bailian-cs.console.aliyun.com` 的 `/cli/api.json`）在开发期以未签名请求探测确认路由存在与错误信封形状；**未使用真实凭据做过端到端查询**。

仍未实测：真实阿里云 AK/SK 下的 Token Plan / Coding Plan 查询（需在设置中保存百炼凭据后运行 `TOKEN_PLAN_MONITOR_LIVE=1 python -m pytest tests/test_live.py -q -s -m live`，或与 `bl usage token-plan --output json` 交叉核对）；Windows 端真机运行与 `tests/gui_*` smoke（这些脚本依赖 `ctypes.windll`/pystray，无法在 macOS 上执行，本次仅做编译与入口隔离检查）。不得由上述程序化检查推断这些项目已通过。

## v0.1.0 GitHub Release 发布前验证（2026-11-10）

- 整合远端 `main` 的窗口 chrome、主题、外观和尺寸持久化改动与 `codex/macos-app` 的原生 macOS 实现。保留 macOS 专用钥匙串后端及远端窗口几何/外观存储；为 Windows 注册表和原生回调增加非 Windows import 隔离。
- 离线 pytest：**116 项通过，2 项跳过**；跳过真实 OpenAPI 和 Windows 原生特效测试。`git diff --check`、源码编译检查及原生 AppKit 隔离 smoke test 通过。
- 使用 `.venv-build` 中 python.org Python 3.13.9 framework 重新构建，未复用此前 DMG。构建检查 **109 个 Mach-O 文件均为 arm64**，部署目标不高于 macOS 14，动态链接不依赖开发机路径。
- Info.plist 核对版本 `0.1.0`、build `1`、Bundle ID `com.zzcx2002.tokenplanmonitor`、最低系统 macOS 14 和版权信息。app 为 ad-hoc 签名，`codesign --verify --deep --strict` 通过，无 Team Identifier，未做 Developer ID 签名或 Apple 公证。
- 项目 LICENSE、CC Switch 声明、Python/依赖许可证、OpenSSL 3.0.18、zlib-ng 2.3.3 及 Pillow 上游原生库声明已在 ad-hoc 签名前放入 `Contents/Resources`，并随应用一并签名；构建对必需资源执行存在性检查。
- `hdiutil verify` 通过。已生成与 Release DMG 同名的 `.sha256` 伴随资产；校验值不写入受版本控制的文档，以 Release 资产为准。
- 只读挂载最终 DMG，确认 `Applications -> /Applications`、应用和许可证资源；从挂载副本执行打包二进制的完整原生 smoke test 和 `codesign --verify --deep --strict`，均通过。截图输出位于 `/tmp/token-plan-monitor-v0.1.0-dmg-smoke/`，未写入镜像。
- 检查未发现应用配置、用量快照、用户凭据、日志或项目测试截图被打包；Python 标准库自带的 `test/tkinterdata/python.png` 属于运行时上游测试资源，不是本项目验收截图。
- 本节仅记录发布前本地验收。干净 macOS 14、真实 Keychain/OpenAPI、VoiceOver、硬件鼠标/多屏/Spaces，以及浏览器下载后的 Gatekeeper“仍要打开”流程仍未实测；不得由上述程序化检查推断这些项目已通过。

## 已验证

- 离线 pytest：58 项通过；实时接口测试和 Windows 原生特效测试按条件跳过。
- 源码编译检查和 `git diff --check` 通过。
- 原生 AppKit 假数据窗口测试：Agent Plan、Coding Plan、刷新中、过期、未配置、鉴权错误、长文本、0%/100% 边界值、周期缺失和最低值并列十种状态。
- 设置保存、Secret 留空保持、无数据菜单、卡片置顶切换/隐藏恢复、屏外位置校正及偏好持久化、窗口关闭隐藏、退出清理的程序化检查通过。
- 源码与打包应用均生成原生视图截图，人工视觉检查详情、卡片和设置；截图不代表桌面背景模糊与外部阴影效果。
- 单实例真实进程测试：拒绝重复锁、重复实例释放不影响持有者、强制结束持有者后可以重新获取锁。
- 路径选择、负坐标屏幕布局、屏幕移除、钥匙串拒绝访问不落盘、设置校验和唤醒刷新合并的离线测试通过。
- 独立应用二进制检查：109 个 Mach-O 文件为 arm64，部署目标均不高于 macOS 14，无开发机路径动态链接；ad-hoc 签名校验通过。
- 打包应用的原生窗口假数据测试通过；通过 LaunchServices（`open … --args --preview`）启动成功，进程命令行确认使用假数据模式，测试实例随后关闭。
- 打包测试检查 Python 标准库、requests、Pillow、PyObjC、钥匙串后端均从 `.app` 内加载，并检查内嵌 TLS 证书文件存在；没有调用真实钥匙串读写。
- 最终 DMG 校验和验证通过，已只读挂载并确认包含应用与 Applications 快捷入口；直接从只读镜像运行上述独立应用检查也通过。

## 本轮界面改善验证

- 源码及重新打包应用的十状态检查通过；每种状态均截图默认 480 × 650 点与最小 480 × 338 点内容区域。最小窗口中的底部操作完整可见。
- 断言进度条取值为剩余百分比，并包含对应周期的无障碍名称与百分比描述；三周期卡片显示完整，缺失周期显示“—”，并列最低项均被标记。
- 长文本状态刷新前后滚动位置及键盘焦点保持不变；设置首次校验保留 AK 输入，并将焦点定位到缺失的 Secret；凭据说明可展开/收起。
- 实际电脑操作确认：卡片常显菜单按钮可用；Tab 可聚焦菜单和详情按钮，回车打开详情；设置错误保留输入并选中无效 Region；打包应用的“检查凭据”进入设置并选中 AK。
- 从本轮 `native-card.png` 的辅助文字实色笔画及临近背景采样，RGB (89,90,92) / (216,220,223) 的对比度约 5.0:1。该结果仅代表本轮桌面合成背景，不构成所有背景或状态的无障碍合规结论。
- 二进制与 ad-hoc 签名再次验证通过；重新生成的 DMG 校验和验证通过。打包检查的截图已放在应用外，最终 `.app` 签名有效。

## 本轮桌面组件模式验证

- 离线 pytest：66 项通过，实时接口及 Windows 原生特效测试共 2 项跳过；源码编译检查及 `git diff --check` 通过。
- 源码 AppKit 隔离测试通过：两种置顶偏好下切换悬浮/桌面模式，验证菜单互斥勾选、桌面模式禁用置顶、窗口层级与 Spaces 标志、位置和隐藏状态保持。
- 在可见和隐藏状态分别从保存的配置创建新卡片，验证桌面模式、位置及置顶偏好恢复；验证后台刷新和打开详情不提升卡片层级，以及桌面模式下移动保存、隐藏恢复。
- 旧配置缺少 `desktop_mode` 时保持原来的位置、可见性及置顶偏好；新增字段严格要求布尔值，损坏配置使用现有默认恢复逻辑。
- 验证数据使用临时目录和内存凭据，不读取真实钥匙串或访问云端。源码检查材料位于 `/tmp/token-monitor-desktop-mode-smoke/`。
- 已重新生成 `.app` 与 DMG；109 个 arm64 Mach-O 文件、最低系统部署目标和本地 ad-hoc 签名检查通过。打包应用通过同一组原生隔离测试，并验证内嵌运行时、证书和钥匙串后端导入；材料位于 `/tmp/token-monitor-desktop-mode-bundle-smoke/`，构建日志位于 `/tmp/token-monitor-desktop-build.log`（日志末尾的沙箱内 DMG 创建失败已通过单独运行 `hdiutil create` 完成恢复）。
- **待实机复核**：普通窗口遮挡、显示桌面、切换所有普通 Spaces、全屏应用、Mission Control 和台前调度。本轮桌面控制工具连接超时，程序化属性检查不计为这些系统交互的实测通过。

## 本轮卡片交互与缩放验证

- 离线 pytest：77 项通过，实时接口及 Windows 原生特效测试共 2 项跳过；源码编译检查通过。
- 源码 AppKit 隔离 smoke 通过：十种状态在默认、较小及加宽详情窗口中重排，配额背景、文字和进度条使用实际可用宽度；缩放及后台刷新保持滚动位置和键盘焦点。
- 卡片普通非拖动 `mouseUp_` 不打开详情；卡片右键菜单不包含详情项，菜单栏菜单保留。右上角“…”和“查看详情”按钮及其键盘入口已移除。
- 纯几何测试覆盖左右、上下及角落缩放、最小尺寸、对侧边缘锚定和显示器边界。原生检查进一步执行生产代码中的普通点击、右下角缩放及空白移动的完整按下→拖动→释放路径，验证三列等宽布局、320×160 最小布局、加宽布局、尺寸保存和新实例恢复；悬浮/桌面模式、位置及隐藏状态保持。
- 验证使用临时目录、内存凭据和假监控，不读取真实钥匙串或访问云端。本轮源码截图及通过记录位于 `/tmp/token-monitor-manual-validation/`，包括 `card-minimum.png`、`card-resized.png` 和各状态的 `details-*-wide.png`。
- **待实机复核**：真实硬件鼠标的八方向缩放光标、跨屏及屏幕断开。当前环境没有辅助功能和屏幕录制权限，系统级外部点击未生效；应用内手势路径和截图断言不计为人工鼠标验收。

## 本轮卡片刷新按钮验证

- 离线 pytest：77 项通过，实时接口及 Windows 原生特效测试共 2 项跳过；源码编译检查通过。
- 源码 AppKit 隔离 smoke 通过：右下角按钮复用现有 `refresh` 命令，`performClick_` 恰好增加一次刷新请求，不打开详情且不改变卡片 frame；刷新中禁用并显示“正在刷新”的无障碍标签，恢复后重新启用。
- 默认、320×160 最小及加宽卡片均检查按钮与底部文字不重叠；按钮中心（包括禁用状态）命中按钮，最外侧右下角仍命中卡片及双向缩放热区。原有移动、缩放、尺寸恢复和悬浮/桌面模式检查继续通过。
- 验证材料位于 `/tmp/token-monitor-refresh-button-smoke/`，包括默认、刷新中、最小和加宽卡片截图。已重新生成 `.app` 与 DMG，签名检查及打包版同一组原生 smoke 通过；打包版材料位于 `/tmp/token-monitor-refresh-button-bundle-smoke/`。真实硬件鼠标点击、键盘焦点及 VoiceOver 朗读仍按实机项目验收。

## 本轮刷新按钮精确命中验证

- 离线 pytest：77 项通过，实时接口及 Windows 原生特效测试共 2 项跳过；源码编译检查及 `git diff --check` 通过。
- 保留原生按钮 32×32 点绘制 frame，中央 20×20 点可见按钮区域用于命中；tooltip 使用同一内缩范围。`CardView.hitTest_` 不再把透明 frame 留白返回给刷新按钮，其他按钮不变。
- 源码 AppKit 隔离 smoke 在默认、320×160 最小及加宽卡片上验证中心、四边内侧命中按钮；四边外侧、透明 frame 四角、原 frame 外部、底部文字、外边距命中卡片。右侧、下侧和双向角落缩放热区保持正确分流。
- 按原生命中结果驱动按钮 `performClick_` 或卡片按下/释放路径：所有外侧点击均不增加刷新次数、不改变卡片 frame、不打开详情；启用时按钮中心恰好刷新一次，刷新中禁用时不刷新。动态 tooltip 提供方法及无障碍标签同步检查通过。
- 源码验证材料位于 `/tmp/token-monitor-refresh-hit-smoke/`，默认和最小卡片截图确认视觉样式未改变。真实系统鼠标分发和悬停提示显示仍需实机复核；上述结果属于原生视图命中与命令路由检查，不冒充硬件鼠标验收。
- 已重新生成 `.app` 与 DMG；打包版同一组原生 smoke、内嵌运行时检查和 ad-hoc 签名校验通过，打包版材料位于 `/tmp/token-monitor-refresh-hit-bundle-smoke/`。

## 刷新按钮原生点击坐标修复

- 用户实机反馈可见按钮点击不刷新且没有刷新中过渡。进一步检查确认 AppKit 向 `CardView.hitTest_` 传入未翻转父视图坐标，而子按钮 frame 使用翻转后的卡片局部坐标；旧实现直接比较两者，真实可见按钮点击被退回给 `CardView`。
- 原生对照中，按钮中心局部坐标 `(356,132)` 对应实际 hit-test 输入 `(356,28)`，旧实现返回卡片而非按钮。上一节测试直接把局部坐标传给 `hitTest_`，因此只证明手工坐标下的分流，不能证明真实鼠标入口；该覆盖缺口已修正。
- `CardView.hitTest_` 现在先用 `convertPoint_fromView_(point, self.superview())` 转为局部坐标，再检查紧凑按钮 frame。预览测试统一使用反向 `convertPoint_toView_` 构造实际输入，并为模拟鼠标事件生成窗口坐标；新增上下镜像误触区回归。
- 离线 pytest：77 项通过，实时接口及 Windows 原生特效测试共 2 项跳过；源码编译检查及 `git diff --check` 通过。隔离源码 smoke 验证可见按钮中心及四边、透明外圈、镜像区域、文字、外边距和缩放热区，启用时中心点击恰好刷新一次，所有外侧点击不刷新且 frame 不变。
- 源码材料位于 `/tmp/token-monitor-refresh-native-hit-smoke/`，截图确认外观不变。真实硬件鼠标和刷新态过渡仍需用户在重建并重启后的应用上复核。
- 已重新生成 `.app` 与 DMG；打包版同一组 smoke 和内嵌运行时检查通过，材料位于 `/tmp/token-monitor-refresh-native-hit-bundle-smoke/`；ad-hoc 签名校验通过。
- 补充合成 `NSEvent` 检查时，修正后的 `mouseDown` 进入按钮原生跟踪并等待 `mouseUp`；顺序式注入器没有预先提供 up 事件而阻塞，因此该次检查不计为完整鼠标点击通过。

## 验证材料

- `artifacts/macos/`：源码运行的七种状态截图及 `smoke-result.txt`。
- `artifacts/macos-bundle/`：独立应用的假数据检查截图及通过记录。
- `artifacts/macos-improvements/`：本轮源码十状态截图、实际交互截图和构建日志；`bundle/` 包含独立应用十状态截图及通过记录。
- `dist/TokenPlanMonitor.app`：独立应用。
- `dist/TokenPlanMonitor-0.1.0-arm64.dmg`：包含应用和 Applications 快捷入口的安装镜像。

这些输出目录不提交 Git；重新构建和运行 README 中的检查命令可以生成。

## 待实机验收

- **macOS 14 arm64**：当前仅有 macOS 26.6 环境，最低版本尚未实机确认。Info.plist 和二进制部署目标检查不能替代实机测试。
- **Windows 回归**：共用业务逻辑测试通过，Windows 原生界面、命名互斥锁和快捷方式需要 Windows 实机复核。
- **剩余实际交互**：菜单栏左右键与悬停、卡片真实移动及八方向缩放、缩放光标、右键和 Control-单击仍需手工复核。已用纯几何测试和原生视图断言覆盖缩放计算、边缘命中及普通点击行为；本轮电脑工具对无边框卡片执行拖动时返回 `noWindowsAvailable`，未将程序化断言计为真实鼠标验收。
- **VoiceOver**：已读取并核对实际 AX 树中的进度条名称、剩余值描述、设置字段标签及菜单/详情按钮；完整屏幕阅读器朗读流程尚未实测。
- **显示与系统事件**：Retina/混合缩放多屏、真实外接屏断开、切换空间及全屏应用、系统降低透明度开关、真实睡眠唤醒。几何和到期逻辑有离线测试，系统事件需要实机操作。
- **真实钥匙串和用量**：测试只使用内存或拒绝访问的假后端，不读取真实凭据。需要用户首次保存凭据后，显式开启 live 测试并与控制台核对三周期用量。
- **正式分发**：当前仅 ad-hoc 签名，未做 Developer ID 签名、公证或下载隔离后的 Gatekeeper 完整流程。

## 手动检查顺序

1. 挂载 DMG，将应用拖入 Applications，双击启动；确认无控制台、首次自动打开设置、不显示 Dock 图标。
2. 保存凭据并核对控制台；确认 SK 不回显，关闭窗口后菜单栏和卡片继续运行。
3. 检查菜单栏左右键、悬停和所有菜单操作；确认卡片普通单击不打开详情，仅可见刷新按钮响应点击和悬停提示，透明外圈不刷新且仍可按卡片处理，刷新中禁用，最外侧右下角仍可缩放；切换卡片置顶、隐藏、恢复，分别拖动空白处和各边缘/角落，重启后核对位置及尺寸。
4. 在不同显示器与缩放下检查文字、滚动、负坐标及屏幕断开校正；切换降低透明度并检查实色圆角和正常文字对比度。
5. 在系统外观里浅色↔深色各切一次，三个界面即时跟随且无白闪；配合降低透明度再各查一次，并逐个点击设置输入框确认聚焦描边只出现在当前行。
6. 断网刷新确认旧数据及过期标记；睡眠超过刷新周期后唤醒，确认仅一次到期刷新。
7. 重复启动确认不产生第二个实例；退出后确认无残留进程，可再次正常启动。
