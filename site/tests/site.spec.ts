import { test, expect } from '@playwright/test';
import AxeBuilder from '@axe-core/playwright';

const pages = [
  { path: '', lang: 'zh-CN', name: 'zh' },
  { path: 'en/', lang: 'en', name: 'en' },
];

for (const item of pages) {
  test(`${item.name}: metadata, release links, and language switch`, async ({ page }) => {
    const errors: string[] = [];
    page.on('pageerror', (error) => errors.push(error.message));
    await page.goto(item.path);
    await expect(page.locator('html')).toHaveAttribute('lang', item.lang);
    await expect(page.locator('h1')).toBeVisible();
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute('href', new RegExp(`/token-plan-monitor/${item.path}$`));
    await expect(page.locator('a[data-download="macos"]').first()).toHaveAttribute('href', /TokenPlanMonitor-0\.2\.1-arm64\.dmg$/);
    await expect(page.locator('a[data-download="windows"]')).toHaveAttribute('href', /TokenPlanMonitor-0\.3\.0-win64\.zip$/);
    await expect(page.locator('link[hreflang="x-default"]')).toHaveAttribute('href', 'https://zzcx2002.github.io/token-plan-monitor/');
    await page.locator('.language-link').click();
    await expect(page.locator('html')).toHaveAttribute('lang', item.lang === 'en' ? 'zh-CN' : 'en');
    expect(errors).toEqual([]);
  });

  test(`${item.name}: no serious accessibility violations`, async ({ page }) => {
    // Disable the hero reveal animation so axe never samples mid-fade contrast.
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(item.path);
    const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
    expect(results.violations.filter((issue) => ['serious', 'critical'].includes(issue.impact ?? ''))).toEqual([]);
  });

  test(`${item.name}: mobile layout has no horizontal overflow`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(item.path);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expect(page.locator('a[data-download="macos"]').first()).toBeVisible();
    await page.locator('.history-figure img').scrollIntoViewIfNeeded();
    await page.waitForFunction(() => { const image = document.querySelector('.history-figure img'); return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0; });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: test.info().outputPath(`${item.name}-mobile.png`), fullPage: true });
  });

  test(`${item.name}: desktop visual capture and reduced motion`, async ({ page }) => {
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto(item.path);
    await page.locator('.history-figure img').scrollIntoViewIfNeeded();
    await page.waitForFunction(() => { const image = document.querySelector('.history-figure img'); return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0; });
    await page.evaluate(() => window.scrollTo(0, 0));
    await page.screenshot({ path: test.info().outputPath(`${item.name}-desktop.png`), fullPage: true });
    expect(await page.locator('.preview-wrap').evaluate((node) => parseFloat(getComputedStyle(node).animationDuration))).toBeLessThanOrEqual(0.001);
  });
}

test('dark mode remains readable and overflow-free', async ({ page }) => {
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('');
  await page.locator('.history-figure img').scrollIntoViewIfNeeded();
  await page.waitForFunction(() => { const image = document.querySelector('.history-figure img'); return image instanceof HTMLImageElement && image.complete && image.naturalWidth > 0; });
  await page.evaluate(() => window.scrollTo(0, 0));
  await page.screenshot({ path: test.info().outputPath('zh-dark-desktop.png'), fullPage: true });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21aa']).analyze();
  expect(results.violations.filter((issue) => ['serious', 'critical'].includes(issue.impact ?? ''))).toEqual([]);
});

for (const width of [320, 768, 1024]) {
  test(`both locales reflow without overflow at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    for (const path of ['', 'en/']) {
      await page.goto(path);
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
      for (const anchor of await page.locator('a[href^="#"]').evaluateAll((nodes) => nodes.map((node) => node.getAttribute('href')))) {
        expect(anchor).not.toBe('#');
        expect(await page.locator(anchor!).count()).toBeGreaterThan(0);
      }
    }
  });
}

test('page is complete with JavaScript disabled', async ({ browser }) => {
  const context = await browser.newContext({ javaScriptEnabled: false, baseURL: 'http://127.0.0.1:4321/token-plan-monitor/' });
  const page = await context.newPage();
  await page.goto('');
  await expect(page.locator('h1')).toContainText('套餐余量');
  await expect(page.locator('.history-figure img')).toBeVisible();
  await context.close();
});
