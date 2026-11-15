#!/usr/bin/env node
// Materialize only reviewed resources; never recursively copy or remove a tree.
import * as fs from 'node:fs/promises';
import { createHash, randomUUID } from 'node:crypto';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const SKILL_FILES = Object.freeze([
  'bcheck/SKILL.md',
  'bcheck/skill.conf',
  'gclear/SKILL.md',
  'qgo/SKILL.md',
  'qgo/scripts/context.mjs',
  'qgo/scripts/session.mjs',
  'qgo/templates/executor.md',
  'qgo/templates/orchestrator.md',
  'qgo/templates/solo.md',
  'qstart/SKILL.md',
  'qstart/templates/discussion.md',
  'rcheck/SKILL.md',
  'rcheck/skill.conf',
  'wish/SKILL.md',
  'xwrap/SKILL.md',
]);
export const MANIFEST_NAME = '.parallax-build-manifest.json';
const BUILDER_ID = 'parallax-dsh/skills';
const DEFAULT_BUNDLE_DIR = fileURLToPath(new URL('.', import.meta.url));
const DIRECTORIES = new Set(SKILL_FILES.flatMap((file) => {
  const parts = file.split('/');
  return parts.slice(0, -1).map((_, index) => parts.slice(0, index + 1).join('/'));
}));
const digest = (bytes) => createHash('sha256').update(bytes).digest('hex');
const localPath = (root, relative) => path.join(root, ...relative.split('/'));

async function statOrMissing(io, target) {
  try {
    return await io.lstat(target);
  } catch (error) {
    if (error.code === 'ENOENT') return null;
    throw error;
  }
}

async function requireDirectory(io, target) {
  const stat = await io.lstat(target);
  // Some Windows junctions report isDirectory=true and isSymbolicLink=false.
  // Resolve them explicitly; never descend through an output/resource alias.
  const expected = path.resolve(target);
  const canonical = await io.realpath(target);
  const equal = process.platform === 'win32'
    ? expected.toLowerCase() === canonical.toLowerCase()
    : expected === canonical;
  if (!stat.isDirectory() || stat.isSymbolicLink() || !equal) {
    throw new Error(`Expected a real directory, not a link or special file: ${target}`);
  }
}

async function readSource(io, sourceDir) {
  await requireDirectory(io, sourceDir);
  const resources = new Map();
  for (const directory of DIRECTORIES) {
    await requireDirectory(io, localPath(sourceDir, directory));
  }
  for (const file of SKILL_FILES) {
    const target = localPath(sourceDir, file);
    const stat = await io.lstat(target);
    if (!stat.isFile() || stat.isSymbolicLink()) {
      throw new Error(`Source resource must be a regular file: ${target}`);
    }
    resources.set(file, await io.readFile(target));
  }
  return resources;
}

function makeManifest(resources) {
  return {
    builder: BUILDER_ID,
    version: 1,
    files: Object.fromEntries([...resources].map(([file, bytes]) => [file, digest(bytes)])),
  };
}

function parseManifest(bytes, target) {
  let manifest;
  try {
    manifest = JSON.parse(bytes.toString('utf8'));
  } catch {
    throw new Error(`Invalid build manifest; preserving output: ${target}`);
  }
  if (!manifest || manifest.builder !== BUILDER_ID || manifest.version !== 1
      || !manifest.files || typeof manifest.files !== 'object' || Array.isArray(manifest.files)
      || Object.keys(manifest).sort().join(',') !== 'builder,files,version'
      || Object.keys(manifest.files).sort().join('\n') !== [...SKILL_FILES].sort().join('\n')
      || Object.values(manifest.files).some((hash) => typeof hash !== 'string' || !/^[a-f0-9]{64}$/.test(hash))) {
    throw new Error(`Unrecognized build manifest; preserving output: ${target}`);
  }
  return manifest;
}

// Unknown paths are rejected before descending; links and special files are never followed.
async function inspectTree(io, root, { requireManifest = false } = {}) {
  const stat = await statOrMissing(io, root);
  if (!stat) {
    if (requireManifest) throw new Error(`Missing generated tree: ${root}`);
    return { kind: 'missing' };
  }
  const files = new Map();
  async function visit(relative = '') {
    const directory = relative ? localPath(root, relative) : root;
    // Validate every level, including junctions that lstat reports as directories.
    await requireDirectory(io, directory);
    for (const name of await io.readdir(directory)) {
      const file = relative ? `${relative}/${name}` : name;
      const target = localPath(root, file);
      const entry = await io.lstat(target);
      if (entry.isSymbolicLink()) throw new Error(`Refusing linked output: ${target}`);
      if (entry.isDirectory() && DIRECTORIES.has(file)) {
        await visit(file);
      } else if (entry.isFile() && (SKILL_FILES.includes(file) || file === MANIFEST_NAME)) {
        files.set(file, await io.readFile(target));
      } else {
        throw new Error(`Unknown output path; preserving all existing files: ${target}`);
      }
    }
  }
  await visit();
  if (!files.size && (await io.readdir(root)).length === 0 && !requireManifest) {
    return { kind: 'empty' };
  }
  const manifestBytes = files.get(MANIFEST_NAME);
  if (requireManifest && !manifestBytes) throw new Error(`Missing build manifest: ${root}`);
  const manifest = manifestBytes && parseManifest(manifestBytes, root);
  for (const file of SKILL_FILES) {
    const bytes = files.get(file);
    if (!bytes) throw new Error(`Incomplete generated tree; preserving output: ${localPath(root, file)}`);
    if (manifest && digest(bytes) !== manifest.files[file]) {
      throw new Error(`Generated resource was modified; preserving output: ${localPath(root, file)}`);
    }
  }
  return { kind: manifest ? 'managed' : 'legacy', manifest };
}

async function removeManagedTree(io, root) {
  const { manifest } = await inspectTree(io, root, { requireManifest: true });
  // A manifest can authorize only these exact files, never arbitrary paths or recursive rm.
  for (const file of SKILL_FILES) {
    const target = localPath(root, file);
    // Revalidate ancestors immediately before each deletion, not just during
    // inspection: a directory may have become an alias during prior cleanup.
    await requireDirectory(io, path.dirname(target));
    const stat = await io.lstat(target);
    if (!stat.isFile() || stat.isSymbolicLink() || digest(await io.readFile(target)) !== manifest.files[file]) {
      throw new Error(`Output changed before cleanup; preserving remaining files: ${target}`);
    }
    await io.unlink(target);
  }
  for (const directory of [...DIRECTORIES].sort((a, b) => b.split('/').length - a.split('/').length)) {
    const target = localPath(root, directory);
    await requireDirectory(io, target);
    await io.rmdir(target);
  }
  await requireDirectory(io, root);
  await io.unlink(path.join(root, MANIFEST_NAME));
  await io.rmdir(root);
}

/** io is injectable for deterministic filesystem-failure tests; the CLI uses Node builtins only. */
export async function buildBundle({ bundleDir = DEFAULT_BUNDLE_DIR, sourceDir, io = fs } = {}) {
  // Resolve the caller's root once, allowing a checkout reached through a symlink.
  bundleDir = await io.realpath(path.resolve(bundleDir));
  sourceDir = path.resolve(sourceDir ?? path.join(bundleDir, '..', 'skills'));
  const outputDir = path.join(bundleDir, 'skills');
  const resources = await readSource(io, sourceDir);
  await inspectTree(io, outputDir);
  const manifest = makeManifest(resources);
  const stagingDir = path.join(bundleDir, `.parallax-skills-staging-${randomUUID()}`);
  const backupDir = path.join(bundleDir, `.parallax-skills-backup-${randomUUID()}`);
  let stagingValidated = false;
  let movedOld = false;
  let installed = false;
  let previous;
  const warnings = [];
  try {
    await io.mkdir(stagingDir);
    for (const directory of DIRECTORIES) {
      await io.mkdir(localPath(stagingDir, directory), { recursive: true });
    }
    for (const [file, bytes] of resources) {
      await io.writeFile(localPath(stagingDir, file), bytes, { flag: 'wx' });
    }
    await io.writeFile(path.join(stagingDir, MANIFEST_NAME), `${JSON.stringify(manifest, null, 2)}\n`, { flag: 'wx' });
    await inspectTree(io, stagingDir, { requireManifest: true });
    stagingValidated = true;
    // Recheck immediately before moving the old tree; no deletion occurs during the swap.
    previous = await inspectTree(io, outputDir);
    if (previous.kind !== 'missing') {
      await io.rename(outputDir, backupDir);
      movedOld = true;
    }
    await io.rename(stagingDir, outputDir);
    installed = true;
  } catch (error) {
    const recovery = [];
    if (movedOld && !installed) {
      try {
        if (await statOrMissing(io, outputDir)) throw new Error(`Destination appeared: ${outputDir}`);
        await io.rename(backupDir, outputDir);
      } catch (rollbackError) {
        recovery.push(`ROLLBACK FAILED: ${rollbackError.message}; old output retained at ${backupDir}`);
      }
    }
    if (stagingValidated) {
      try {
        await removeManagedTree(io, stagingDir);
      } catch (cleanupError) {
        recovery.push(`Staging retained at ${stagingDir}: ${cleanupError.message}`);
      }
    } else if (await statOrMissing(io, stagingDir)) {
      // An incomplete tree has no trustworthy manifest. Preserve it rather than guessing.
      recovery.push(`Unvalidated staging retained at ${stagingDir}`);
    }
    throw new Error([`Bundle build failed: ${error.message}`, ...recovery].join('\n'), { cause: error });
  }
  if (movedOld && previous.kind === 'managed') {
    try {
      await removeManagedTree(io, backupDir);
    } catch (error) {
      warnings.push(`Bundle installed, but cleanup stopped; remaining previous output retained at ${backupDir}: ${error.message}`);
    }
  }
  return {
    outputDir,
    files: [...SKILL_FILES],
    migratedLegacy: previous.kind === 'legacy',
    // Legacy and empty pre-manifest trees are retained, never deleted.
    backupDir: movedOld && previous.kind !== 'managed' ? backupDir : null,
    warnings,
  };
}

export async function main() {
  try {
    const result = await buildBundle();
    console.log(`Bundle synced: ${result.outputDir} (${result.files.length} resources)`);
    if (result.backupDir) console.log(`Previous unmanifested output preserved: ${result.backupDir}`);
    for (const warning of result.warnings) console.warn(warning);
  } catch (error) {
    console.error(error.message);
    process.exitCode = 1;
  }
}

// Importing this module does not build or mutate any files, including through a symlink.
if (process.argv[1]) {
  const entry = await fs.realpath(path.resolve(process.argv[1])).catch(() => null);
  if (entry && pathToFileURL(entry).href === import.meta.url) await main();
}
