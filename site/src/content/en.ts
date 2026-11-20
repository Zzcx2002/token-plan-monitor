import type { LandingContent } from './types';

export const en = {
  locale: 'en',
  meta: {
    title: 'Token Plan Quota Monitor — Know what’s left, at a glance',
    description: 'A Windows tray and macOS menu bar utility for 5-hour, weekly, and monthly remaining plan capacity. Official API data only, with no telemetry or usage uploads.',
    ogAlt: 'Token Plan Quota Monitor: Know what’s left, at a glance.',
  },
  brandName: 'Token Plan',
  skipLink: 'Skip to main content',
  nav: {
    ariaLabel: 'Primary',
    features: 'Features', history: 'History', privacy: 'Privacy', download: 'Download',
    github: 'GitHub', language: '中文', languageLabel: '切换到中文',
  },
  hero: {
    eyebrow: 'Open source · Local first',
    titleLines: ['Know what’s left,', 'at a glance.'],
    description: 'Keep the 5-hour, weekly, and monthly remaining capacity for Volcengine Ark and Alibaba Cloud Model Studio in your Windows tray or macOS menu bar.',
    primaryCta: 'Download for macOS', secondaryCta: 'View on GitHub',
    requirement: '{version} · macOS 14+ · Apple Silicon',
    uiNote: 'The app interface is currently Chinese-only. The preview shows the actual Chinese UI.',
    previewCaption: 'Product UI preview · Sample data',
    previewAria: 'Desktop card showing sample remaining percentages for three Volcengine Ark quota windows',
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
    { value: '2', label: 'providers' },
    { value: '3', label: 'quota windows' },
    { value: '0', label: 'telemetry or usage uploads' },
  ],
  providers: {
    eyebrow: 'Supported plans', title: 'The plans you use, together.',
    description: 'A deliberately focused set of providers and plans. Accuracy matters more than a long compatibility list.',
    items: [
      {
        mark: '火', name: 'Volcengine Ark', plans: 'Agent Plan · Coding Plan', detail: 'See 5-hour, weekly, and monthly windows, plus AFP or quota units when the API reports them.',
        tiers: [
          { plan: 'Agent Plan (Individual)', entries: [
            { name: 'Small', price: '¥40/mo', quota: '20,000 AFP' },
            { name: 'Medium', price: '¥200/mo', quota: '100,000 AFP' },
            { name: 'Large', price: '¥500/mo', quota: '250,000 AFP' },
            { name: 'Max', price: '¥1000/mo', quota: '500,000 AFP' },
          ] },
          { plan: 'Coding Plan (Individual)', entries: [
            { name: 'Lite', price: '¥40/mo', quota: 'Metered by requests' },
            { name: 'Pro', price: '¥200/mo', quota: 'Metered by requests' },
          ] },
        ],
      },
      {
        mark: '百', name: 'Alibaba Cloud Model Studio', plans: 'Token Plan (Individual) · Coding Plan', detail: 'See the remaining ratio and reset time supported by the API, without inventing absolute amounts.',
        tiers: [
          { plan: 'Token Plan (Individual)', entries: [
            { name: 'Lite', price: '¥39/mo', quota: '20,000 Credits' },
            { name: 'Essential', price: '¥79/mo', quota: '45,000 Credits' },
            { name: 'Standard', price: '¥139/mo', quota: '80,000 Credits' },
            { name: 'Pro', price: '¥312/mo', quota: '200,000 Credits' },
          ] },
          { plan: 'Coding Plan (legacy)', entries: [
            { name: 'Pro', price: '¥200/mo', quota: '90,000 requests' },
            { name: 'Lite (discontinued)', price: '¥40/mo', quota: '18,000 requests' },
          ] },
        ],
      },
    ],
    tierTable: { caption: 'tiers and list prices', tier: 'Tier', price: 'List price', quota: 'Monthly allowance' },
    pricingNote: 'List prices (CNY) verified on 2026-11-19; limited-time promotions are subject to each provider’s official campaign pages.',
  },
  features: {
    eyebrow: 'Core features', title: 'Less interruption. More certainty.',
    description: 'It stays out of your workflow until you need a clear view of capacity or what comes next.',
    items: [
      { icon: 'gauge', title: 'Capacity at a glance', description: 'Remaining percentages, progress, and reset times in the menu bar, tray, and desktop card. Refreshes every 15 minutes by default.' },
      { icon: 'bell', title: 'A warning before it runs out', description: 'Local history powers consumption-rate and exhaustion estimates. Get a system alert below your threshold, re-armed after a reset.' },
      { icon: 'code', title: 'A simpler Claude Code setup', description: 'Merge provider and model settings into your local config after an itemized confirmation, with a timestamped backup.' },
    ],
  },
  history: {
    eyebrow: 'Local history', title: 'See the trend, not just the current number.',
    description: 'Every successful query contributes to local history. See consumption over time, where resets happen, and which window needs attention.',
    points: ['24-hour, 7-day, 30-day, and 90-day views', 'Breaks at resets instead of connecting separate quota periods', 'Warnings when estimated exhaustion comes before reset; honest copy when samples are insufficient'],
    imageAlt: 'Chinese usage-history window showing sample consumption ratios, reset breaks, and alert thresholds for 5-hour and weekly windows',
    caption: 'Offline UI preview with sample values. The app interface is currently Chinese-only.',
  },
  privacy: {
    eyebrow: 'Privacy and honest data', title: 'Real data. Kept on your machine.',
    description: 'Your usage does not need to become data in another cloud account. The app only contacts fixed official HTTPS endpoints and never uploads usage data to third parties.',
    items: [
      { icon: 'shield', title: 'No telemetry or uploads', description: 'No account, third-party analytics, or cloud sync. Failed queries retain and clearly mark the last successful data as stale.' },
      { icon: 'key', title: 'OS credential storage', description: 'Control-plane credentials stay in macOS Keychain or Windows Credential Manager, never in usage history.' },
      { icon: 'database', title: 'No invented token counts', description: 'AFP and quota units are never converted into tokens. If the API returns only a percentage, that is all you see.' },
    ],
    honesty: 'History stays on your machine for up to 90 days by default. Every trend and exhaustion time is explicitly an estimate based on local records.',
    caveat: 'One exception: the optional Claude Code hookup writes an inference key in plaintext to a local config file after itemized confirmation, protected by file permissions or ACLs.',
    caveatLink: 'Read the full security notes',
  },
  download: {
    eyebrow: 'Download', title: 'Keep capacity within reach.',
    description: 'Free, MIT licensed, and no registration. Public downloads are available for both macOS and Windows.',
    verified: 'Release information checked {date}',
    mac: {
      badge: 'Available', title: 'macOS', requirements: 'macOS 14+ · Apple Silicon (arm64) · {version}',
      description: 'Open the DMG and drag TokenPlanMonitor into Applications. No separate Python installation is needed.',
      cta: 'Download DMG', checksum: 'SHA-256 checksum',
      detailsSummary: 'First launch and Gatekeeper',
      details: 'This build has an ad-hoc signature and is not notarized by Apple. After verifying its source and checksum, choose Open Anyway under System Settings → Privacy & Security. Do not disable system security protections.',
    },
    windows: {
      badge: 'Available', title: 'Windows', requirements: 'Windows 10 / 11 · 64-bit · {version}',
      description: 'Download and verify the portable ZIP, extract it to a user-writable folder, then run TokenPlanMonitor.exe. No Python installation is needed.',
      cta: 'Download portable ZIP', checksum: 'SHA-256 checksum', source: 'Or run from source',
      detailsSummary: 'First launch and SmartScreen',
      details: 'The executable is not commercially code-signed, so SmartScreen may appear. After confirming the release source and SHA-256 checksum, choose More info → Run anyway.',
    },
    uiNote: 'The app interface is currently Chinese-only. The English website and README do not change the in-app language.',
  },
  closing: {
    title: 'Keep capacity visible. Code with fewer surprises.',
    description: 'One small utility, three time windows, and fewer unexpected interruptions.',
    cta: 'Download for macOS', secondary: 'Explore the source on GitHub',
  },
  footer: {
    tagline: 'A quiet plan-quota utility for your menu bar and system tray.',
    product: 'Product', resources: 'Resources', legal: 'Open source & privacy', releases: 'Releases', issues: 'Issues', source: 'Source code', privacy: 'Privacy notes', license: 'MIT License',
    copyright: '© 2026 Token Plan Monitor', affiliation: 'Not affiliated with Volcengine or Alibaba Cloud.',
  },
} satisfies LandingContent;
