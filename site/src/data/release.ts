import manifest from './release.json';

export interface AvailableRelease {
  status: 'available';
  tag: string;
  version: string;
  publishedAt: string;
  releasePage: string;
  assetName: string;
  download: string;
  checksum: string;
  sha256: string;
}

export interface UnpublishedRelease {
  status: 'unpublished';
}

export const release = manifest as unknown as {
  latestTag: string;
  verifiedAt: string;
  platforms: {
    macos: AvailableRelease | UnpublishedRelease;
    windows: AvailableRelease | UnpublishedRelease;
  };
};
