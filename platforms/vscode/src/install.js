'use strict';

// Install / update / force-reinstall of the Parallax skills, reviewer agent and
// instructions into a workspace `.github/` tree.
//
// Design: everything except `installToWorkspace` is a small pure-ish helper;
// the module never imports `vscode`, so the unit tests load it directly.
// Writes are atomic (temp file + rename, temp dir + swap) so an interrupted
// install never leaves a half-written skill behind.

const fs = require('fs');
const path = require('path');
const paths = require('./paths');

/* ------------------------------------------------------------------ text -- */

function parseFrontmatter(text) {
  const normalized = String(text || '').replace(/^﻿/, '');
  if (!normalized.startsWith('---')) return {};
  const end = normalized.indexOf('\n---', 3);
  if (end === -1) return {};
  const block = normalized.slice(3, end);
  const data = {};
  for (const line of block.split(/\r?\n/)) {
    const m = /^([A-Za-z0-9_-]+)\s*:\s*(.*)$/.exec(line.trim());
    if (m) data[m[1]] = m[2].replace(/^["']|["']$/g, '').trim();
  }
  return data;
}

// Version declared by an installed SKILL.md (`version:` in its frontmatter).
function skillVersion(text) {
  const v = parseFrontmatter(text).version;
  return v && v.length ? v : null;
}

function splitVersion(value) {
  const parts = String(value || '').trim().replace(/^v/i, '').split('.');
  return parts.map((p) => {
    const m = /^(\d+)/.exec(p);
    return m ? Number(m[1]) : 0;
  });
}

// Numeric segment comparison; missing segments count as 0, so "1.146" < "1.146.0"
// is false and "1.9.0" < "1.146.0" is true (not a string compare).
function compareVersions(a, b) {
  const left = splitVersion(a);
  const right = splitVersion(b);
  const len = Math.max(left.length, right.length);
  for (let i = 0; i < len; i += 1) {
    const l = left[i] || 0;
    const r = right[i] || 0;
    if (l !== r) return l < r ? -1 : 1;
  }
  return 0;
}

function isVersionNewer(candidate, base) {
  return compareVersions(candidate, base) > 0;
}

// True when the running VS Code is older than the version this extension was
// exercised on — the notice + "use the project install" hint then applies.
function shouldShowVersionNotice(runningVersion, testedVersion) {
  return compareVersions(runningVersion, testedVersion || paths.MIN_TESTED_VSCODE) < 0;
}

/* ---------------------------------------------------------- instructions -- */

function renderInstructionBlock(instructions) {
  const body = String(instructions || '').replace(/\r\n/g, '\n').replace(/\s+$/, '');
  return `${paths.MARKER_BEGIN}\n${body}\n${paths.MARKER_END}\n`;
}

// Merge the marked block into existing instructions.
// Returns { content, mode } with mode one of created | replaced | appended.
// User text outside the markers is never touched.
function mergeInstructions(existing, block) {
  const current = String(existing || '');
  const normalized = current.replace(/\r\n/g, '\n');
  if (!normalized.trim()) return { content: block, mode: 'created' };

  const begin = normalized.indexOf(paths.MARKER_BEGIN);
  const end = begin === -1 ? -1 : normalized.indexOf(paths.MARKER_END, begin + paths.MARKER_BEGIN.length);
  if (begin !== -1 && end !== -1) {
    const after = end + paths.MARKER_END.length;
    const tail = normalized.slice(after).replace(/^\n+/, '');
    const head = normalized.slice(0, begin);
    const content = head + block + (tail ? '\n' + tail : '');
    return { content, mode: 'replaced' };
  }

  const sep = normalized.endsWith('\n') ? '\n' : '\n\n';
  return { content: normalized + sep + block, mode: 'appended' };
}

/* ------------------------------------------------------------------- fs -- */

function exists(p) {
  try {
    fs.lstatSync(p);
    return true;
  } catch {
    return false;
  }
}

function isDirectory(p) {
  try {
    return fs.lstatSync(p).isDirectory();
  } catch {
    return false;
  }
}

function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

function toPosix(p) {
  return p.split(path.sep).join('/');
}

function listDirs(root) {
  if (!isDirectory(root)) return [];
  return fs
    .readdirSync(root, { withFileTypes: true })
    .filter((entry) => entry.isDirectory() && !paths.SKIP_DIRS.has(entry.name))
    .map((entry) => entry.name)
    .sort();
}

// Recursive file listing (posix relative paths, sorted, runtime junk excluded).
function listFiles(root) {
  const out = [];
  if (!isDirectory(root)) return out;
  const walk = (abs, rel) => {
    for (const entry of fs.readdirSync(abs, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      if (entry.isSymbolicLink()) continue;
      const nextAbs = path.join(abs, entry.name);
      const nextRel = rel ? `${rel}/${entry.name}` : entry.name;
      if (entry.isDirectory()) {
        if (paths.SKIP_DIRS.has(entry.name)) continue;
        walk(nextAbs, nextRel);
      } else if (entry.isFile() && !paths.SKIP_FILE_RE.test(nextRel)) {
        out.push(nextRel);
      }
    }
  };
  walk(root, '');
  return out.sort();
}

function readText(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

function sameFile(a, b) {
  try {
    const sa = fs.statSync(a);
    const sb = fs.statSync(b);
    if (sa.size !== sb.size) return false;
    return fs.readFileSync(a).equals(fs.readFileSync(b));
  } catch {
    return false;
  }
}

function copyFileAtomic(src, dest) {
  ensureDir(path.dirname(dest));
  const tmp = path.join(path.dirname(dest), `.parallax-tmp-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  fs.copyFileSync(src, tmp);
  fs.renameSync(tmp, dest);
}

function writeFileAtomic(dest, content) {
  ensureDir(path.dirname(dest));
  const tmp = path.join(path.dirname(dest), `.parallax-tmp-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`);
  fs.writeFileSync(tmp, content, 'utf8');
  fs.renameSync(tmp, dest);
}

function removeDir(dir) {
  try {
    fs.rmSync(dir, { recursive: true, force: true });
  } catch {
    /* best effort */
  }
}

// Replace `destRoot` with an exact copy of `srcRoot`: build a sibling temp tree
// first, then swap, so a failure never leaves a partial destination.
function copyTreeAtomic(srcRoot, destRoot) {
  const parent = path.dirname(destRoot);
  ensureDir(parent);
  const tag = `${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2)}`;
  const tmpRoot = path.join(parent, `.parallax-tmp-${tag}`);
  const oldRoot = path.join(parent, `.parallax-old-${tag}`);
  removeDir(tmpRoot);
  fs.mkdirSync(tmpRoot, { recursive: true });
  try {
    for (const rel of listFiles(srcRoot)) {
      copyFileAtomic(path.join(srcRoot, rel), path.join(tmpRoot, rel));
    }
    const hadDest = exists(destRoot);
    if (hadDest) fs.renameSync(destRoot, oldRoot);
    fs.renameSync(tmpRoot, destRoot);
    if (hadDest) removeDir(oldRoot);
    return listFiles(destRoot).length;
  } catch (err) {
    removeDir(tmpRoot);
    if (exists(oldRoot) && !exists(destRoot)) {
      try {
        fs.renameSync(oldRoot, destRoot);
      } catch {
        /* keep the backup in place; the error below is the actionable one */
      }
    }
    throw err;
  }
}

// Compare a source tree against an installed tree.
// state: same | changed | missing (source only) | extra (installed only)
function compareTrees(srcRoot, destRoot) {
  const srcFiles = listFiles(srcRoot);
  const destFiles = listFiles(destRoot);
  const srcSet = new Set(srcFiles);
  const destSet = new Set(destFiles);
  const rows = [];
  for (const rel of srcFiles) {
    if (!destSet.has(rel)) {
      rows.push({ rel, state: 'missing' });
    } else if (sameFile(path.join(srcRoot, rel), path.join(destRoot, rel))) {
      rows.push({ rel, state: 'same' });
    } else {
      rows.push({ rel, state: 'changed' });
    }
  }
  for (const rel of destFiles) {
    if (!srcSet.has(rel)) rows.push({ rel, state: 'extra' });
  }
  return rows;
}

/* ---------------------------------------------------------------- backup -- */

// Copy the listed installed files into `destRoot`, preserving relative paths.
// Returns the relative paths that were copied.
function backupInstalled({ installedRoot, rels, destRoot }) {
  const copied = [];
  for (const rel of rels) {
    const src = path.join(installedRoot, rel);
    if (!exists(src)) continue;
    copyFileAtomic(src, path.join(destRoot, rel));
    copied.push(toPosix(rel));
  }
  return copied;
}

/* --------------------------------------------------------------- install -- */

function readExtensionVersion(extensionPath) {
  const raw = readText(path.join(extensionPath, 'package.json'));
  if (!raw) return null;
  try {
    return JSON.parse(raw).version || null;
  } catch {
    return null;
  }
}

function readProjectInstructionsTemplate(extensionPath) {
  const raw = readText(paths.instructionsPath(extensionPath, 'project'));
  if (raw === null) throw new Error('bundled project instructions are missing (run: npm run sync)');
  return raw;
}

/**
 * Install Parallax content into a workspace.
 *
 * @param {object} options
 * @param {string} options.extensionPath  VS Code extension root
 * @param {string} options.workspaceRoot  target workspace folder
 * @param {'update'|'force'} [options.mode]
 * @returns {object} structured report
 */
function installToWorkspace(options) {
  const extensionPath = options && options.extensionPath;
  const workspaceRoot = options && options.workspaceRoot;
  const mode = options && options.mode === 'force' ? 'force' : 'update';
  if (!extensionPath) throw new Error('extensionPath is required');
  if (!workspaceRoot) throw new Error('no workspace folder is open');

  const gh = paths.githubDir(workspaceRoot);
  const report = {
    workspace: workspaceRoot,
    extensionVersion: readExtensionVersion(extensionPath),
    mode,
    skills: [],
    agents: [],
    instructions: null,
    backupDir: null,
    backupFiles: [],
    written: 0,
    changed: 0,
    unchanged: 0,
    startedAt: new Date().toISOString(),
  };

  const backupRoot = paths.backupDir(workspaceRoot);
  let backupUsed = false;
  // `prefix` keeps the backup layout readable: skills/<name>/..., agents/..., and
  // copilot-instructions.md at the root of the backup directory.
  const recordBackup = (installedRoot, rels, prefix) => {
    if (!rels.length) return;
    const copied = backupInstalled({ installedRoot, rels, destRoot: path.join(backupRoot, prefix) });
    if (!copied.length) return;
    backupUsed = true;
    report.backupFiles.push(...copied.map((rel) => `${prefix ? prefix + '/' : ''}${rel}`));
  };

  // Skills: whole directory (including templates/ for qgo, qstart, gclear refs).
  const skillsSrcRoot = paths.skillsRoot(extensionPath);
  const skillNames = listDirs(skillsSrcRoot);
  if (!skillNames.length) throw new Error('no bundled skills found — the extension package is incomplete (run: npm run sync)');

  for (const name of skillNames) {
    const src = path.join(skillsSrcRoot, name);
    const dest = path.join(paths.projectSkillsDir(workspaceRoot), name);
    const rows = compareTrees(src, dest);
    const changedRows = rows.filter((row) => row.state === 'changed' || row.state === 'extra');
    report.changed += changedRows.filter((row) => row.state === 'changed').length;
    report.unchanged += rows.filter((row) => row.state === 'same').length;
    if (mode === 'force') recordBackup(dest, changedRows.map((row) => row.rel), `skills/${name}`);
    const files = copyTreeAtomic(src, dest);
    report.written += files;
    report.skills.push({
      name,
      files,
      changed: changedRows.filter((row) => row.state === 'changed').length,
      // files present in the installed copy but not in this extension version
      removed: changedRows.filter((row) => row.state === 'extra').length,
    });
  }

  // Reviewer agent(s): copied file by file so other agents in .github/agents survive.
  const agentsSrcRoot = paths.agentsRoot(extensionPath);
  for (const rel of listFiles(agentsSrcRoot)) {
    const src = path.join(agentsSrcRoot, rel);
    const dest = path.join(paths.projectAgentsDir(workspaceRoot), rel);
    const state = !exists(dest) ? 'missing' : sameFile(src, dest) ? 'same' : 'changed';
    if (mode === 'force' && state === 'changed') {
      recordBackup(paths.projectAgentsDir(workspaceRoot), [rel], 'agents');
    }
    if (state !== 'same') {
      copyFileAtomic(src, dest);
      report.written += 1;
    }
    if (state === 'changed') report.changed += 1;
    if (state === 'same') report.unchanged += 1;
    report.agents.push({ file: toPosix(rel), state });
  }

  // Instructions: only the marked block is ever rewritten.
  const instructionsFile = paths.projectInstructionsFile(workspaceRoot);
  const existing = readText(instructionsFile);
  const block = renderInstructionBlock(readProjectInstructionsTemplate(extensionPath));
  const merged = mergeInstructions(existing === null ? '' : existing, block);
  if (merged.content !== (existing === null ? '' : existing)) {
    if (mode === 'force' && existing !== null) recordBackup(gh, [path.relative(gh, instructionsFile)], '');
    writeFileAtomic(instructionsFile, merged.content);
    report.written += 1;
  }
  report.instructions = {
    path: instructionsFile,
    mode: merged.mode,
    managed: true,
  };

  if (backupUsed) report.backupDir = backupRoot;

  report.finishedAt = new Date().toISOString();
  return report;
}

function summarizeInstall(report) {
  const parts = [
    `${report.skills.length} skills`,
    `${report.agents.length} agent${report.agents.length === 1 ? '' : 's'}`,
    'instructions',
  ];
  let line = `Parallax ${report.extensionVersion || ''} installed into ${report.workspace}: ${parts.join(', ')}`;
  if (report.backupDir) line += ` (backup: ${report.backupDir})`;
  return line;
}

module.exports = {
  parseFrontmatter,
  skillVersion,
  compareVersions,
  isVersionNewer,
  shouldShowVersionNotice,
  renderInstructionBlock,
  mergeInstructions,
  listDirs,
  listFiles,
  compareTrees,
  sameFile,
  copyFileAtomic,
  copyTreeAtomic,
  writeFileAtomic,
  backupInstalled,
  readExtensionVersion,
  installToWorkspace,
  summarizeInstall,
  exists,
  isDirectory,
};
