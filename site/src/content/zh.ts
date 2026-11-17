import type { LandingContent } from './types';

export const zh = {
  locale: 'zh-CN',
  meta: {
    title: 'Token Plan 用量监控 — 套餐余量，一眼看清',
    description: 'Windows 托盘与 macOS 菜单栏用量工具，查看火山方舟、阿里云百炼的 5 小时、每周与每月剩余额度。无遥测，不上传用量。',
    ogAlt: 'Token Plan 用量监控：套餐余量，一眼看清。',
  },
  brandName: 'Token Plan',
  skipLink: '跳到主要内容',
  nav: {
    ariaLabel: '主导航',
    features: '功能', history: '历史', privacy: '隐私', download: '下载',
    github: 'GitHub', language: 'English', languageLabel: 'Switch to English',
  },
  hero: {
    eyebrow: '开源 · 本地优先',
    titleLines: ['套餐余量，', '一眼看清。'],
    description: '在 Windows 托盘和 macOS 菜单栏，随时查看火山方舟、阿里云百炼的 5 小时、每周与每月剩余额度。',
    primaryCta: '下载 macOS 版', secondaryCta: '查看 GitHub',
    requirement: '{version} · macOS 14+ · Apple Silicon',
    uiNote: 'Windows 10 / 11 便携版已发布，可在下方直接下载。',
    previewCaption: '产品界面预览 · 示例数据',
    previewAria: '桌面卡片显示火山方舟三个额度窗口的剩余百分比示例',
  },
  quotaPreview: {
    provider: '火山方舟', status: '数据正常',
    windows: [
      { label: '5 小时剩余', value: 72 },
      { label: '每周剩余', value: 36 },
      { label: '每月剩余', value: 19, lowest: true },
    ],
    lowest: '最低', updated: '更新于 23:12', refresh: '刷新',
  },
  facts: [
    { value: '2', label: '家服务商' },
    { value: '3', label: '个额度窗口' },
    { value: '0', label: '遥测与用量上传' },
  ],
  providers: {
    eyebrow: '支持范围', title: '常用的套餐，放在一起看。',
    description: '只接入已经支持的服务商与套餐，不追求看起来很多，只确保显示得准确。',
    items: [
      { mark: '火', name: '火山方舟', plans: 'Agent Plan · Coding Plan', detail: '查看 5 小时、每周与每月窗口；接口提供时同时显示 AFP 或额度。' },
      { mark: '百', name: '阿里云百炼', plans: 'Token Plan 个人版 · Coding Plan', detail: '按接口能力展示剩余比例与重置时间，不显示并不存在的绝对数字。' },
    ],
  },
  features: {
    eyebrow: '核心能力', title: '少一点打扰，多一点确定。',
    description: '不接管你的工作流，只在需要的时候，把额度和趋势放到眼前。',
    items: [
      { icon: 'gauge', title: '额度就在眼前', description: '菜单栏、托盘与桌面卡片集中显示剩余百分比、进度和重置时间。后台默认每 15 分钟刷新。' },
      { icon: 'bell', title: '提前知道将用完', description: '根据本机历史估算消耗速率与预计耗尽时刻；低于阈值时发送系统提醒，重置后自动恢复。' },
      { icon: 'code', title: '顺手接入 Claude Code', description: '选择服务商和模型后合并写入本地配置，先列出变更并确认，同时保留时间戳备份。' },
    ],
  },
  history: {
    eyebrow: '本地历史', title: '不只看现在，也看见接下来的趋势。',
    description: '每次成功查询都会成为本机历史的一部分。用量如何变化、哪里发生重置、哪个窗口更紧张，都有迹可循。',
    points: ['24 小时、7 天、30 天与 90 天视图', '重置处断开，不用折线掩盖周期变化', '预计耗尽早于重置时给出提醒；样本不足就如实说明'],
    imageAlt: '用量历史窗口，展示 5 小时与每周额度的已用比例、周期重置断点和告警阈值线',
    caption: '离线界面预览，数值为示例数据；实际界面以真机为准。',
  },
  privacy: {
    eyebrow: '隐私与真实数据', title: '只显示真实数据，也只留在本机。',
    description: '你的用量不需要成为另一个云端账号的数据。应用只访问固定的官方 HTTPS 地址，不上传任何用量到第三方。',
    items: [
      { icon: 'shield', title: '无遥测，无上传', description: '没有账号体系、第三方统计或云端同步；查询失败时保留并明确标记上次成功数据。' },
      { icon: 'key', title: '系统级凭据存储', description: '控制面凭据保存在 macOS 钥匙串或 Windows 凭据管理器，不写进历史记录。' },
      { icon: 'database', title: '不虚构 token 数字', description: '不把 AFP 或额度换算成 token。接口只返回百分比时，就只显示百分比。' },
    ],
    honesty: '历史仅保存在本机，默认保留 90 天；所有趋势与耗尽时刻都是基于本地记录的估算。',
    caveat: '例外说明：可选的 Claude Code 接入会在逐项确认后，将推理 Key 明文写入本地配置文件，并依靠文件权限或 ACL 保护。',
    caveatLink: '查看完整安全说明',
  },
  download: {
    eyebrow: '下载', title: '把额度，放在伸手可见的地方。',
    description: '免费、MIT 开源，不需要注册。macOS 与 Windows 均提供公开发行包。',
    verified: '发布信息核对日期：{date}',
    mac: {
      badge: '可下载', title: 'macOS', requirements: 'macOS 14+ · Apple Silicon (arm64) · {version}',
      description: '下载 DMG 后，将 TokenPlanMonitor 拖入“应用程序”即可，无需另外安装 Python。',
      cta: '下载 DMG', checksum: 'SHA-256 校验文件',
      detailsSummary: '首次打开与 Gatekeeper',
      details: '当前安装包为 ad-hoc 签名，未经过 Apple 公证。确认来源与校验值后，可在“系统设置 → 隐私与安全性”选择“仍要打开”。无需也不应关闭系统安全保护。',
    },
    windows: {
      badge: '可下载', title: 'Windows', requirements: 'Windows 10 / 11 · 64 位 · {version}',
      description: '下载便携 ZIP 并校验完整性，解压到当前用户可写的目录后运行 TokenPlanMonitor.exe，无需安装 Python。',
      cta: '下载便携 ZIP', checksum: 'SHA-256 校验文件', source: '也可以从源码运行',
      detailsSummary: '首次运行与 SmartScreen',
      details: '当前 exe 未购买代码签名证书，首次运行可能出现 SmartScreen。确认下载来自本仓库 Release 且 SHA-256 校验通过后，再选择“更多信息 → 仍要运行”。',
    },
    uiNote: '应用界面目前为中文。英文网站与英文 README 不影响应用内语言。',
  },
  closing: {
    title: '让额度看得见，让 coding 更安心。',
    description: '一个小工具，三个时间窗口，少一些出乎意料的中断。',
    cta: '下载 macOS 版', secondary: '在 GitHub 上查看源码',
  },
  footer: {
    tagline: '安静驻留在菜单栏与托盘里的套餐用量工具。',
    product: '产品', resources: '资源', legal: '开源与隐私', releases: '版本发布', issues: '问题反馈', source: '源代码', privacy: '隐私说明', license: 'MIT 许可证',
    copyright: '© 2026 Token Plan Monitor', affiliation: '非火山引擎或阿里云官方产品。',
  },
} satisfies LandingContent;
