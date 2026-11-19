export type Locale = 'zh-CN' | 'en';
export type IconName = 'gauge' | 'bell' | 'code' | 'shield' | 'database' | 'key';

export interface FeatureItem {
  icon: IconName;
  title: string;
  description: string;
}

export interface LandingContent {
  locale: Locale;
  meta: {
    title: string;
    description: string;
    ogAlt: string;
  };
  brandName: string;
  skipLink: string;
  nav: {
    ariaLabel: string;
    features: string;
    history: string;
    privacy: string;
    download: string;
    github: string;
    language: string;
    languageLabel: string;
  };
  hero: {
    eyebrow: string;
    titleLines: [string, string];
    description: string;
    primaryCta: string;
    secondaryCta: string;
    requirement: string;
    uiNote: string;
    previewCaption: string;
    previewAria: string;
  };
  quotaPreview: {
    provider: string;
    status: string;
    windows: Array<{ label: string; value: number; lowest?: boolean }>;
    lowest: string;
    updated: string;
    refresh: string;
  };
  facts: Array<{ value: string; label: string }>;
  providers: {
    eyebrow: string;
    title: string;
    description: string;
    items: Array<{
      mark: string;
      name: string;
      plans: string;
      detail: string;
      tiers: Array<{ plan: string; entries: Array<{ name: string; price: string; quota: string }> }>;
    }>;
    tierTable: { caption: string; tier: string; price: string; quota: string };
    pricingNote: string;
  };
  features: {
    eyebrow: string;
    title: string;
    description: string;
    items: FeatureItem[];
  };
  history: {
    eyebrow: string;
    title: string;
    description: string;
    points: string[];
    imageAlt: string;
    caption: string;
  };
  privacy: {
    eyebrow: string;
    title: string;
    description: string;
    items: FeatureItem[];
    honesty: string;
    caveat: string;
    caveatLink: string;
  };
  download: {
    eyebrow: string;
    title: string;
    description: string;
    verified: string;
    mac: {
      badge: string;
      title: string;
      requirements: string;
      description: string;
      cta: string;
      checksum: string;
      detailsSummary: string;
      details: string;
    };
    windows: {
      badge: string;
      title: string;
      requirements: string;
      description: string;
      cta: string;
      checksum: string;
      source: string;
      detailsSummary: string;
      details: string;
    };
    uiNote: string;
  };
  closing: {
    title: string;
    description: string;
    cta: string;
    secondary: string;
  };
  footer: {
    tagline: string;
    product: string;
    resources: string;
    legal: string;
    releases: string;
    issues: string;
    source: string;
    privacy: string;
    license: string;
    copyright: string;
    affiliation: string;
  };
}
