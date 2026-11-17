import { readFile } from 'node:fs/promises';

const release = JSON.parse(await readFile(new URL('../src/data/release.json', import.meta.url), 'utf8'));
const repoApi = 'https://api.github.com/repos/Zzcx2002/token-plan-monitor';
const headers = { 'User-Agent': 'token-plan-monitor-site-verifier', Accept: 'application/vnd.github+json' };
const fail = (message) => { throw new Error(message); };
const releaseCache = new Map();

async function request(url, options = {}) {
  let lastError;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      return await fetch(url, { ...options, signal: AbortSignal.timeout(30_000) });
    } catch (error) {
      lastError = error;
      if (attempt === 1) console.warn(`Network request failed; retrying once: ${url}`);
    }
  }
  throw lastError;
}

async function getRelease(tag) {
  if (!releaseCache.has(tag)) {
    releaseCache.set(tag, request(`${repoApi}/releases/tags/${tag}`, { headers }).then(async (response) => {
      if (!response.ok) fail(`${tag}: release API returned ${response.status}.`);
      const data = await response.json();
      if (data.draft || data.prerelease) fail(`${tag}: not a public stable release.`);
      return data;
    }));
  }
  return releaseCache.get(tag);
}

for (const [platform, data] of Object.entries(release.platforms)) {
  if (data.status !== 'available') continue;
  const tagged = await getRelease(data.tag);
  const asset = tagged.assets.find((item) => item.name === data.assetName);
  if (!asset) fail(`${platform}: ${data.assetName} is not attached to ${data.tag}.`);
  if (asset.browser_download_url !== data.download) fail(`${platform}: download URL differs from GitHub.`);
  if (asset.digest && asset.digest !== `sha256:${data.sha256}`) fail(`${platform}: asset digest differs from manifest.`);

  const checksumResponse = await request(data.checksum, { headers });
  if (!checksumResponse.ok) fail(`${platform}: checksum returned ${checksumResponse.status}.`);
  const [digest, filename] = (await checksumResponse.text()).trim().split(/\s+/);
  if (digest !== data.sha256 || filename !== data.assetName) fail(`${platform}: checksum file does not match manifest.`);

  const downloadResponse = await request(data.download, { headers: { Range: 'bytes=0-0' } });
  if (![200, 206].includes(downloadResponse.status)) fail(`${platform}: download returned ${downloadResponse.status}.`);
  await downloadResponse.body?.cancel();
  console.log(`${platform}: ${data.assetName}, checksum and ranged download OK.`);
}

const latestResponse = await request(`${repoApi}/releases/latest`, { headers });
if (latestResponse.ok) {
  const latest = await latestResponse.json();
  if (latest.tag_name !== release.latestTag) console.warn(`Warning: latest public release is ${latest.tag_name}; review ${release.latestTag}.`);
}
console.log('Public release verification passed.');
