[中文](README.md) | [English](README.en.md)

# Token Plan Quota Monitor

A standalone Windows tray / macOS menu-bar utility that shows **how much capacity your subscription plan has left**, across the 5-hour, weekly and monthly windows. Two providers today: **Volcengine Ark (火山方舟)** — Agent Plan / Coding Plan, and **Alibaba Cloud Model Studio (阿里云百炼)** — Token Plan (Individual) / Coding Plan.

| Desktop card · light | Desktop card · dark |
| --- | --- |
| <img src="docs/images/card-ark.png" width="380" alt="Desktop card in light appearance"> | <img src="docs/images/card-ark-dark.png" width="380" alt="Desktop card in dark appearance"> |

> Screenshots come from the offline UI preview harness with fake numbers. They do not include the out-of-window shadow or the desktop backdrop blur, so the real thing looks slightly softer.

> **The app interface is currently Chinese-only.** There is no English localization yet, so the Chinese terms quoted throughout this document are exactly what you will see on screen. They are all collected in the [glossary](#glossary-of-on-screen-chinese) below.

## Contents

- [What it does / what it does not](#what-it-does--what-it-does-not)
- [Support matrix](#support-matrix)
- [Install](#install)
- [First setup: which credentials you need](#first-setup-which-credentials-you-need)
- [Using it](#using-it)
- [Appearance](#appearance)
- [Claude Code hookup (macOS only)](#claude-code-hookup-macos-only)
- [Where files and credentials live, and how to remove them](#where-files-and-credentials-live-and-how-to-remove-them)
- [Privacy and security boundaries](#privacy-and-security-boundaries)
- [FAQ](#faq)
- [Glossary of on-screen Chinese](#glossary-of-on-screen-chinese)
- [License and acknowledgements](#license-and-acknowledgements)

## What it does / what it does not

**Does**: queries the official APIs on a schedule (every 15 minutes by default, configurable 5–1440); shows the remaining percentage, a progress bar and the reset time for each window on hover and on click; keeps a draggable, resizable card on your desktop; shows absolute amounts when the provider reports them (AFP for Volcengine Ark Agent Plan, quota units for Model Studio Coding Plan); retains the last successful result when a query fails and labels it as stale.

**Does not**:

- It never converts AFP or quota units into tokens, and never infers capacity from request logs. If the API only returns a percentage, you only get a percentage — no invented absolute numbers.
- It does not read or modify the configuration or databases of CC Switch or the Model Studio CLI (the protocol implementations were studied from both, but nothing depends on them).
- It uploads no usage data anywhere; there is no telemetry. The program only contacts fixed official HTTPS endpoints and accepts no custom scripts or arbitrary request URLs.
- It does not register itself to launch at login.

## Support matrix

| | macOS | Windows |
| --- | --- | --- |
| Distribution | DMG on GitHub Releases | run from source (no installer) |
| Requirements | macOS 14 or later, Apple Silicon (arm64) only | Python 3.11 or later |
| Providers | Volcengine Ark + Model Studio, switchable in Settings | Volcengine Ark only |
| Desktop card | three columns (5-hour / weekly / monthly), floating card or desktop component | two columns (5-hour / weekly) |
| Claude Code hookup | yes (bottom of the Settings window) | no |
| Appearance | follows the system light/dark setting, no in-app switch | light / dark / system, switchable in-app |
| Credential storage | macOS login keychain | Windows Credential Manager |

Model Studio Token Plan (Individual) is metered per subscription month: the API returns only usage ratios and reset times, with no absolute amounts. Model Studio free per-model token allowances and the Token Plan team edition are not supported.

## Install

### macOS

Download [TokenPlanMonitor-0.1.1-arm64.dmg](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.1.1/TokenPlanMonitor-0.1.1-arm64.dmg) from the [GitHub Release v0.1.1](https://github.com/Zzcx2002/token-plan-monitor/releases/tag/v0.1.1) page, open it, drag `TokenPlanMonitor.app` into Applications, and launch it. The app bundles its own Python runtime — you do not need to install anything else.

To confirm the download is intact, put the DMG and the [SHA-256 checksum file](https://github.com/Zzcx2002/token-plan-monitor/releases/download/v0.1.1/TokenPlanMonitor-0.1.1-arm64.dmg.sha256) in the same folder and run:

```sh
shasum -a 256 -c TokenPlanMonitor-0.1.1-arm64.dmg.sha256
```

This first release is only ad-hoc signed locally: no Developer ID signature, no Apple notarization. A copy downloaded from the network may be blocked by Gatekeeper on first launch. Once you are satisfied with the source, use **Open Anyway** under System Settings → Privacy & Security, then confirm **Open**. Do **not** turn off your system security protections or strip the app's quarantine attribute.

### Windows

There is no Windows installer; run it from source. Put the project wherever you like (referred to as `<project dir>` below), then in a terminal:

```bat
cd /d <project dir>
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install -e ".[dev]"
.venv\Scripts\pythonw.exe -m token_plan_monitor
```

After installing dependencies, run the shortcut script once. It creates one "火山方舟用量监控" (Volcengine Ark usage monitor) shortcut on the Desktop and one in the current user's Start Menu, so you can launch by double-clicking without a console window flashing. No administrator rights needed, no login item; re-run it if you move the project or recreate `.venv`:

```bat
powershell -NoProfile -File .\scripts\New-AppShortcuts.ps1
```

The program uses a Windows named mutex to prevent duplicate launches, so double-clicking twice will not create a second tray instance.

## First setup: which credentials you need

The Settings window opens automatically on first launch. **Both providers need control-plane credentials, not inference API keys**:

| Provider | Required | Where to get it |
| --- | --- | --- |
| Volcengine Ark | AccessKey ID + Secret AccessKey | account-level AK/SK from the Volcengine console, used to sign requests to `open.volcengineapi.com` |
| Alibaba Cloud Model Studio | AccessKey ID + Secret AccessKey | AK/SK from your Alibaba Cloud account; a RAM sub-account needs the read-only policies `AliyunTokenPlanReadOnlyAccess` and `AliyunBSSReadOnlyAccess` |

Common traps:

- `sk-...` keys (Model Studio also issues `sk-sp-...` plan keys) are **inference API keys**. They cannot be used as AK/SK. You only need an inference key in addition if you use the Claude Code hookup.
- Region: Volcengine Ark defaults to `cn-beijing`. Model Studio accepts only `cn-beijing` or `ap-southeast-1`, and Token Plan (Individual) is currently offered only in China North 2 (Beijing).
- Your system clock must be accurate — request signing depends on it.
- Leaving the Secret field empty keeps the stored secret; the UI never echoes the original secret back.
- Prefer a sub-account whose permissions are limited to quota reading.

Click 保存设置 (Save settings). The program only contacts fixed official HTTPS endpoints, and the Model Studio console access token is held in memory only — never written to disk, never logged.

## Using it

### macOS menu bar

| Action | Result |
| --- | --- |
| Left click the icon | opens the usage details window |
| Right click / Control-click | menu: remaining and used percentages for all three windows, last successful update time, then 打开用量详情 (Open usage details) / 立即刷新 (Refresh now) / 桌面卡片 (Desktop card) / 显示模式 (Display mode) / 卡片置顶 (Keep on top) / 设置… (Settings…) / 退出 (Quit) |
| Hover | tooltip summary, listing only the 5-hour and weekly windows |

The round badge on the icon changes color and mark with the status: OK, warning (stale or low quota), error, or not configured. Closing a window only hides it — the process keeps running; use 退出 (Quit, ⌘Q) in the menu to end it.

### macOS desktop card

| Action | Result |
| --- | --- |
| Plain click | does **not** open a window (opposite of Windows) |
| Drag empty area | moves the card |
| Drag an edge / corner | resizes the card |
| Right click / Control-click | action menu (without 打开用量详情 — details open only from the menu bar) |
| Round arrow, bottom right | refresh now; disabled while refreshing. Only the visible button area responds; the transparent outer ring still belongs to the card |
| Esc | hides the card, and it stays hidden after restart |

The card shows remaining capacity for 5-hour / weekly / monthly in three columns, marks the lowest one (ties included), and shows 额度偏低 (Low quota) when anything drops below 10%. Position, size, visibility and display mode are restored after restart. The card is placed against each display's available area, supports negative coordinates, self-corrects when a screen disappears, and never takes over another app's full-screen space.

**显示模式 (Display mode)** switches between **悬浮卡片 (Floating card)** and **桌面组件 (Desktop component)**. A desktop component sits at the desktop layer on all normal Spaces: other app windows can cover it and it is hidden inside full-screen apps, but it still drags, resizes and refreshes. 卡片置顶 is unavailable in that mode; switching back restores your previous top-most choice. Quitting the app removes the component.

### Usage details

<img src="docs/images/details-ark.png" width="330" alt="Usage details window: three windows of remaining capacity, progress bars and reset times">

Numbers and progress bars always mean **remaining** capacity; the used percentage is secondary. Plans with absolute amounts show them too (AFP for Volcengine Ark, quota units for Model Studio Coding Plan; Model Studio Token Plan reports percentages only). The header holds status, last successful update and the error reason, the footer action bar stays pinned, and the middle section scrolls and re-flows with window width. Scroll position and keyboard focus survive background refreshes and resizing.

### Windows

Single-click the tray icon to open the usage popup (hover for a summary, right-click for the three-window detail and actions); 展开详情 expands it to show used percentages and absolute amounts. On the desktop card, a **single click opens the popup** (a drag never misfires), hover shows the summary, right click shows the menu, and edges and corners resize. The card starts top-most; you can un-pin or hide it and restore it from the tray menu 桌面卡片.

## Appearance

- **macOS**: the usage window, Settings window and desktop card follow the system light/dark appearance with no in-app switch. Colors are all system dynamic colors or appearance-paired custom colors, so open windows redraw immediately when you change the system appearance. Accent color, button highlights and focus rings follow the system **Accent color**; card corners, shadows and materials are native and respect System Settings → Accessibility → Display → **Reduce transparency**.
- **Windows**: follows the app theme by default, re-checked every 2 seconds; the ◐ menu in the popup corner and the three-way selector in Settings apply light / dark / system immediately, with live preview and rollback on cancel or Esc. When the system turns off **Transparency effects**, solid theme backgrounds are used instead.

The Settings body scrolls; Tab, Enter to save, Esc to cancel, and native text-editing shortcuts such as ⌘C / ⌘V all work. Validation keeps what you typed and focuses the offending field; authentication or configuration errors lead with 检查凭据 (Check credentials), network errors lead with 重试 (Retry). Offline, the last successful data is retained and marked stale; after a wake, an overdue deadline triggers at most one coalesced refresh.

## Claude Code hookup (macOS only)

After picking a provider you can also pick a model and write the connection details into Claude Code's local config in one click — no hand-copying Base URLs, pasting keys or editing model names. The entry point is at the bottom of the Settings window; the desktop card has none.

<img src="docs/images/settings-claude-code.png" width="330" alt="Settings window: provider pop-up at the top, Claude Code hookup section at the bottom">

**Where it writes**: `~/.claude/settings.json` by default, honouring `CLAUDE_CONFIG_DIR`. The confirmation dialog shows the resolved absolute path — an app launched from Finder does not inherit your shell environment, so trust the path in the dialog. It also sets `hasCompletedOnboarding` to true in `~/.claude.json` to skip the first-run wizard; if that file cannot be parsed the step is **skipped**, never rebuilt (it also holds your project history).

**Which keys change** — everything else is preserved:

| Key | Behaviour |
| --- | --- |
| `env.ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` / `ANTHROPIC_MODEL` | always written |
| `env.ANTHROPIC_API_KEY` | deleted if present (it shadows `ANTHROPIC_AUTH_TOKEN`) |
| the tier the top-level `model` alias points at (`opus` → `ANTHROPIC_DEFAULT_OPUS_MODEL`, same for sonnet/haiku) | set to the chosen model |
| other tiers, `CLAUDE_CODE_SUBAGENT_MODEL` | filled only when empty; existing values untouched |
| top-level `model` | set to the concrete model name you chose |

Why touch a tier: in Claude Code `model: "opus"` is only an alias — what actually selects a model is `ANTHROPIC_DEFAULT_OPUS_MODEL`. Writing `ANTHROPIC_MODEL` alone does almost nothing for such a config. The side effect is that once top-level `model` becomes a concrete name, `/model opus` resolves through `ANTHROPIC_DEFAULT_OPUS_MODEL` (now your chosen model), which differs from your previous alias-only layering.

**Safety**: every write is preceded by a backup to `settings.json.bak.<timestamp>` — that backup holds the previous plaintext key, so it is kept at `0600` like the file itself, and any older world-readable backup left behind is tightened too; it merges rather than rebuilds; if the file is not valid JSON or `env` is not an object it **aborts without touching the original**; the result is tightened to `0600`; clicking twice is idempotent. You see an itemized change list first and must confirm.

**Costs to know about**:

- The inference key normally lives only in the keychain, but Claude Code consumes it through an environment variable, so `ANTHROPIC_AUTH_TOKEN` in `settings.json` ends up in **plaintext** (`0600`, readable only by your user; any process running as you can still read it), and each backup is plaintext too, likewise `0600`. The confirmation dialog says so.
- Rewriting `settings.json` drops `//` and `/* */` comments and key order (values are unaffected); `~/.claude.json` is re-serialized wholesale, tightened to `0600`, and created if absent.
- Official docs only promise hot reload for permissions / hooks / apiKeyHelper edits, not for `env` or `model`, so **start a new Claude Code session** to confirm it took.

**Base URL and models**: an empty Base URL uses the provider default, and the Region interpolated into that address is validated first — same format-and-supported-region rule as credential saving. URLs carrying credentials (`https://user:pass@host`) are refused outright: everything before the `@` is not the host, so a plausible-looking domain can silently become the machine your key is sent to. For Model Studio the default is the verified `https://token-plan.<region>.maas.aliyuncs.com/apps/anthropic`; pasting an OpenAI-compatible address (`/compatible-mode/v1`) is rewritten automatically, and a proxy host you own under a known domain is kept verbatim. Model Studio ships a model catalogue (default `auto`); custom model IDs may contain only letters, digits and `. _ : / + -`. **Volcengine Ark has no verifiable Anthropic-compatible endpoint** (official material only gives the `/api/v3` OpenAI-compatible address and states the BaseUrl must be supplied by the user), so that field is left empty for you to fill, the UI marks it ⚠ unverified, and its model list is empty — type the model ID yourself.

Switching back and forth between providers remembers each one's model and Base URL; the inference key is kept only as an in-memory draft for this run and is never echoed back, though the field label notes "已保存，留空保持不变" (saved — leave empty to keep). Precedence is: what you typed this session → the real on-disk config → that provider's saved preferences → provider defaults. Ranking the on-disk config above saved preferences is deliberate: an endpoint you edited by hand must never be silently reverted by the next write.

## Where files and credentials live, and how to remove them

### macOS

```text
~/Library/Application Support/TokenPlanMonitor/
  config.json         provider, region, refresh interval
  claude.json         Claude Code choices (model / base URL, never the key)
  snapshot.json       last successful usage cache
  widget.json         card position, size, visibility, top-most state, display mode
  instance-*.lock     single-instance lock; the file survives exit, the OS releases the lock
~/Library/Logs/TokenPlanMonitor/app.log     sanitized run log
```

AK/SK go only into the macOS login keychain under service `TokenPlanMonitor`, with a separate account pair per provider:

| Purpose | Account |
| --- | --- |
| Volcengine Ark control plane | `volcengine_access_key_id` / `volcengine_secret_access_key` |
| Model Studio control plane | `bailian_access_key_id` / `bailian_secret_access_key` |
| Inference key for Claude Code | `volcengine_inference_api_key` / `bailian_inference_api_key` |

Switching providers never reads or overwrites the other provider's credentials, and control-plane AK/SK are never reused as inference keys. Denying keychain access surfaces an error — it does **not** fall back to a plaintext file.

To remove everything: delete exactly the accounts above in Keychain Access, delete the data and log directories above, and delete what the hookup wrote under `~/.claude` — `settings.json` with its plaintext key and every `settings.json.bak.*` copy of it. Changing provider or credentials clears the old snapshot; nothing is migrated automatically from Windows.

### Windows

```text
<project dir>\
  data\config.json              provider, region, refresh interval, other non-secret config
  data\snapshot.json            last successful usage cache
  data\widget.json              card position, visibility, top-most state
  data\window_geometry.json     saved sizes for each window and the card
  data\appearance.json          light / dark / system
  logs\app.log                  sanitized run log
  assets\app.ico                shortcut and window icon
```

Credentials live in Windows Credential Manager as `TokenPlanMonitor/volcengine_access_key_id` and `TokenPlanMonitor/volcengine_secret_access_key`. The program never deletes them on its own; to get rid of them, delete both entries under Credential Manager → Windows Credentials, then delete the project directory. Corrupt config or cache files fall back to safe defaults or an empty state rather than crashing at startup.

## Privacy and security boundaries

- Only fixed official HTTPS endpoints are contacted (Volcengine OpenAPI, Alibaba Cloud Model Studio and the Bailian console gateway). No custom scripts, no arbitrary request URLs, no telemetry.
- Secrets go only into the OS credential store — never into config files, caches or logs. The Model Studio console access token is memory-only.
- Usage data is displayed and cached locally only; nothing is uploaded to any third party.
- The app needs no write access for reading usage — the one exception is the Claude Code write, which happens only when you click it.

## FAQ

- **Authentication failed**: confirm you used account AK/SK rather than an `sk-...` inference key, then check IAM/RAM permissions and your system clock.
- **No plan found**: confirm the account actually subscribes to a supported plan (Volcengine Ark Agent Plan or Coding Plan; Model Studio Token Plan Individual or Coding Plan).
- **Data may be stale**: check your network, proxy or the service status; the UI is showing the last successful result and will not pretend it is live.
- **No absolute amounts / no token totals**: when the API returns only percentages, the program will not invent absolute capacity. Model Studio Token Plan Individual is exactly this case.
- **macOS blocks the first launch**: see the Gatekeeper note under Install — use Open Anyway, do not disable system security.
- **The interface is Chinese-only**: yes, for now. This README's [glossary](#glossary-of-on-screen-chinese) maps every on-screen label.
- **Switching accounts**: changing credentials clears the previous account's in-memory snapshot and cache, so figures from two accounts cannot mix.

## Glossary of on-screen Chinese

| On screen | English |
| --- | --- |
| 立即刷新 | Refresh now |
| 打开用量详情 | Open usage details |
| 桌面卡片 | Desktop card |
| 显示模式 | Display mode |
| 悬浮卡片 / 桌面组件 | Floating card / Desktop component |
| 卡片置顶 | Keep on top |
| 设置… / 退出 | Settings… / Quit |
| 取消 / 保存设置 | Cancel / Save settings |
| 数据正常 | Data OK |
| 数据过期 | Data stale |
| 查询失败 | Query failed |
| 正在刷新… | Refreshing… |
| 未配置 | Not configured |
| 额度偏低 | Low quota |
| 剩余 72.0% | 72.0% remaining |
| 已用 28.0% | 28.0% used |
| 暂无数据 | No data |
| 重置时间：… | Resets at … |
| 更新于：… / 最近成功更新：… | Updated / Last successful update |
| 5 小时 / 每周 / 每月 | 5-hour / Weekly / Monthly |
| · 最低 | lowest |
| 拖动移动 · 边缘/角落调整大小 | Drag to move · drag edges or corners to resize |
| 服务商 | Provider |
| 刷新间隔（分钟，5–1440） | Refresh interval (minutes, 5–1440) |
| 凭据说明 | Credential help |
| 检查凭据 / 重试 / 连接账号 | Check credentials / Retry / Connect account |
| Claude Code 接入 / 写入 Claude 配置 / 确认写入 | Claude Code hookup / Write Claude config / Confirm write |

## License and acknowledgements

MIT licensed — see [LICENSE](LICENSE). Signing and response parsing were studied from [CC Switch](https://github.com/farion1231/cc-switch) (`src-tauri/src/services/coding_plan.rs`, pinned revision `ef7b77dee255e2c3b9a594de454eae21476ca687`) and the [Alibaba Cloud Model Studio CLI](https://github.com/modelstudioai/cli) (`packages/core/src/client/acs.ts`, `packages/core/src/console/gateway.ts`, `packages/core/src/console/models.ts`, `packages/commands/src/commands/usage/`, pinned revision `fa8330c27862635f7d769c5488c33a06a47063d5`); neither is a runtime dependency. Their copyright and license texts (MIT / Apache-2.0), plus the licenses of the third-party runtime bundled into the app, are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

## Developers

Source runs, DMG packaging, offline UI preview and validation, tests and live verification: [docs/development.md](docs/development.md). macOS acceptance log: [docs/macos-validation.md](docs/macos-validation.md).
