# Token Plan Monitor product site

A bilingual static product page built with Astro. Chinese is served at `/`, English at `/en/`; GitHub Pages adds the `/token-plan-monitor/` project base.

## Local development

```sh
npm ci
npm run make:assets
npm run dev
```

Open `http://localhost:4321/token-plan-monitor/`.

## Verification

```sh
npm run check
npm run build
npm run verify:release
npm run test:install
npm test
```

`verify:release` uses the public GitHub API and performs a ranged request instead of downloading the full DMG. The regular build only runs the offline manifest validator so network failures do not block deployment.

## Updating a release

Edit `src/data/release.json`, then run `npm run verify:release`. Download buttons render only for platforms marked `available`. An `unpublished` platform must not contain any asset URL or digest. Do not guess release asset filenames or link local files from `dist/`.

Localized copy lives in `src/content/zh.ts` and `src/content/en.ts`, both checked against the same `LandingContent` type. Keep the Chinese-only app UI disclosure and sample-data captions accurate.

Run `node scripts/make-icons-and-og.mjs` after changing the brand icon or OG copy. The site has no analytics, external fonts, or runtime client-side JavaScript.
