import { mkdir, readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import sharp from 'sharp';

const iconPath = new URL('../src/assets/brand/app.png', import.meta.url);
const publicDir = new URL('../public/', import.meta.url);
const ogDir = new URL('../public/og/', import.meta.url);
await mkdir(ogDir, { recursive: true });

const icon = await readFile(iconPath);
await sharp(icon).resize(32, 32).png().toFile(fileURLToPath(new URL('favicon-32.png', publicDir)));
await sharp(icon).resize(180, 180).png().toFile(fileURLToPath(new URL('apple-touch-icon.png', publicDir)));

const pages = [
  {
    locale: 'zh',
    title: ['套餐余量，', '一眼看清。'],
    subtitle: 'Windows 托盘与 macOS 菜单栏用量监控',
    font: 'Microsoft YaHei, PingFang SC, sans-serif',
    titleSize: 68,
  },
  {
    locale: 'en',
    title: ['Know what’s left,', 'at a glance.'],
    subtitle: 'Plan capacity in your menu bar and system tray',
    font: 'Segoe UI, Arial, sans-serif',
    titleSize: 72,
  },
];

for (const page of pages) {
  const svg = Buffer.from(`
    <svg width="1200" height="630" xmlns="http://www.w3.org/2000/svg">
      <rect width="1200" height="630" fill="#f5f6f7"/>
      <rect x="72" y="205" width="54" height="6" rx="3" fill="#007aff"/>
      <text x="184" y="126" fill="#191b20" font-family="Segoe UI, Arial, sans-serif" font-size="30" font-weight="600">Token Plan Monitor</text>
      <text x="72" y="308" fill="#191b20" font-family="${page.font}" font-size="${page.titleSize}" font-weight="650" letter-spacing="-2">${page.title[0]}</text>
      <text x="72" y="398" fill="#191b20" font-family="${page.font}" font-size="${page.titleSize}" font-weight="650" letter-spacing="-2">${page.title[1]}</text>
      <text x="72" y="486" fill="#646975" font-family="${page.font}" font-size="27">${page.subtitle}</text>
      <text x="72" y="558" fill="#0062cc" font-family="Segoe UI, Arial, sans-serif" font-size="16" font-weight="600" letter-spacing="2">OPEN SOURCE · LOCAL FIRST</text>
      <rect x="0.5" y="0.5" width="1199" height="629" fill="none" stroke="#e2e5eb"/>
    </svg>`);
  const resizedIcon = await sharp(icon).resize(88, 88).png().toBuffer();
  await sharp(svg)
    .composite([{ input: resizedIcon, left: 72, top: 72 }])
    .png({ compressionLevel: 9 })
    .toFile(fileURLToPath(new URL(`og-${page.locale}.png`, ogDir)));
}

console.log('Generated favicons and localized Open Graph images.');
