export const SITE_ORIGIN = 'https://zzcx2002.github.io';
export const REPOSITORY = 'https://github.com/Zzcx2002/token-plan-monitor';

const base = import.meta.env.BASE_URL.endsWith('/')
  ? import.meta.env.BASE_URL
  : `${import.meta.env.BASE_URL}/`;

export function withBase(path = ''): string {
  return `${base}${String(path).replace(/^\/+/, '')}`;
}

export function absoluteUrl(path = ''): string {
  return new URL(withBase(path), SITE_ORIGIN).href;
}

export const links = {
  repository: REPOSITORY,
  releases: `${REPOSITORY}/releases`,
  issues: `${REPOSITORY}/issues`,
  license: `${REPOSITORY}/blob/main/LICENSE`,
  privacyZh: `${REPOSITORY}/blob/main/README.md#隐私与安全边界`,
  privacyEn: `${REPOSITORY}/blob/main/README.en.md#privacy-and-security-boundaries`,
  windowsZh: `${REPOSITORY}/blob/main/README.md#windows`,
  windowsEn: `${REPOSITORY}/blob/main/README.en.md#windows`,
  claudeZh: `${REPOSITORY}/blob/main/README.md#接入-claude-code`,
  claudeEn: `${REPOSITORY}/blob/main/README.en.md#claude-code-hookup`,
};
