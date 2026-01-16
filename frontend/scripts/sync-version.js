#!/usr/bin/env node

/**
 * Sync package.json/package-lock.json version with pyproject.toml.
 * This keeps the frontend metadata aligned to the backend single source of truth.
 */

import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const repoRoot = path.resolve(__dirname, '..', '..');
const pyprojectPath = path.join(repoRoot, 'pyproject.toml');
const packageJsonPath = path.join(repoRoot, 'frontend', 'package.json');
const packageLockPath = path.join(repoRoot, 'frontend', 'package-lock.json');

const pyprojectRaw = fs.readFileSync(pyprojectPath, 'utf8');
const versionMatch = pyprojectRaw.match(/^\s*version\s*=\s*"([^"]+)"/m);

if (!versionMatch) {
  console.error('Could not find version in pyproject.toml');
  process.exit(1);
}

const version = versionMatch[1];

const updateJsonFile = (filePath, isLockFile = false) => {
  const json = JSON.parse(fs.readFileSync(filePath, 'utf8'));
  let updated = false;

  if (json.version !== version) {
    json.version = version;
    updated = true;
  }

  // package-lock.json also has version in packages[""]
  if (isLockFile && json.packages?.['']?.version !== version) {
    json.packages[''].version = version;
    updated = true;
  }

  if (updated) {
    fs.writeFileSync(filePath, `${JSON.stringify(json, null, 2)}\n`);
  }
  return updated;
};

const updatedPkg = updateJsonFile(packageJsonPath);
const updatedLock = updateJsonFile(packageLockPath, true);

if (updatedPkg || updatedLock) {
  console.log(`Synced frontend package version to ${version}`);
} else {
  console.log('Frontend package version already in sync');
}
