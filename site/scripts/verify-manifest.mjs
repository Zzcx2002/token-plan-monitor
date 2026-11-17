import { readFile } from 'node:fs/promises';

const release = JSON.parse(await readFile(new URL('../src/data/release.json', import.meta.url), 'utf8'));
const releaseOrigin = 'https://github.com/Zzcx2002/token-plan-monitor/releases/';
const fail = (message) => { throw new Error(message); };
const validTag = (tag) => /^v\d+\.\d+\.\d+$/.test(tag);

if (!validTag(release.latestTag)) fail('Latest tag must use vMAJOR.MINOR.PATCH.');

for (const [platform, data] of Object.entries(release.platforms)) {
  if (data.status === 'available') {
    for (const field of ['tag', 'version', 'releasePage', 'assetName', 'download', 'checksum', 'sha256']) {
      if (!data[field]) fail(`${platform}: available release is missing ${field}.`);
    }
    if (!validTag(data.tag) || data.version !== data.tag.slice(1)) fail(`${platform}: version does not match tag.`);
    if (data.releasePage !== `${releaseOrigin}tag/${data.tag}`) fail(`${platform}: unexpected release page.`);
    if (!data.download.startsWith(releaseOrigin) || !data.checksum.startsWith(releaseOrigin)) fail(`${platform}: unexpected download origin.`);
    if (!data.assetName.includes(data.version) || !data.download.includes(data.tag)) fail(`${platform}: asset does not match version.`);
    if (!/^[a-f0-9]{64}$/.test(data.sha256)) fail(`${platform}: invalid SHA-256 digest.`);
  } else if (data.status === 'unpublished') {
    for (const field of ['tag', 'version', 'releasePage', 'assetName', 'download', 'checksum', 'sha256']) {
      if (data[field]) fail(`${platform}: unpublished release must not include ${field}.`);
    }
  } else {
    fail(`${platform}: unknown release status.`);
  }
}

console.log(`Release manifest OK: ${Object.entries(release.platforms).map(([name, data]) => `${name} ${data.status === 'available' ? data.tag : 'unpublished'}`).join(', ')}.`);
