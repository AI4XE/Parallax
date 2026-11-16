#!/usr/bin/env node
// sync-content.mjs — generate `media/` (the tree shipped inside the VSIX) from
// the Copilot platform in this repository.
//
//   node scripts/sync-content.mjs           # regenerate media/
//   node scripts/sync-content.mjs --check   # verify media/ matches the sources
//
// Rules:
//   * skills are copied as whole directories (templates/ included — gclear points
//     at the qgo skill's templates/solo.md, so cross-skill references must work);
//   * the reviewer agent is copied verbatim;
//   * the project-mode instructions are copied verbatim (the optional project
//     install writes them into .github/ with .github/skills/ wording);
//   * the extension-mode instructions get frontmatter + extension wording, and
//     the build fails if any `.github/skills/` path survives;
//   * every `templates/...` reference in every SKILL.md must resolve somewhere
//     inside the generated media/skills tree.
//
// media/ is committed on purpose: the VSIX must be self-contained and --check
// keeps it honest against platforms/copilot/.

import fs from 'node:fs';
import path from 'node:path';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(HERE, '..'); // platforms/vscode
const REPO = path.resolve(ROOT, '..', '..'); // repository root
const require = createRequire(import.meta.url);
const paths = require('../src/paths.js');
const install = require('../src/install.js');
const check = require('../src/check.js');

const SOURCE = path.join(REPO, 'platforms', 'copilot');
const MEDIA = path.join(ROOT, 'media');
const ICON = path.join(ROOT, 'assets', 'icon.png');
const MANAGED = ['skills', 'agents', 'instructions'];

function listFiles(root) {
  return install.listFiles(root);
}

function listDirs(root) {
  return install.listDirs(root);
}

function copyTree(srcRoot, destRoot) {
  for (const rel of listFiles(srcRoot)) {
    const src = path.join(srcRoot, rel);
    const dest = path.join(destRoot, rel);
    fs.mkdirSync(path.dirname(dest), { recursive: true });
    if (rel.endsWith('SKILL.md') || rel.endsWith('.agent.md')) {
      // Frontmatter of skill/agent files goes through YAML normalization.
      fs.writeFileSync(dest, normalizeFrontmatter(fs.readFileSync(src, 'utf8')), 'utf8');
    } else {
      fs.copyFileSync(src, dest);
    }
  }
}

// A plain (unquoted) YAML scalar may not contain ": " — "Trigger words: bcheck"
// inside a description makes the whole frontmatter invalid YAML, and VS Code's
// skill loader then drops the skill without any error. Re-emit such values as a
// double-quoted YAML scalar. Content is unchanged; only the syntax is.
export function normalizeFrontmatter(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---(\r?\n|$)/.exec(text);
  if (!m) return text;
  const lines = m[1].split(/\r?\n/);
  let changed = false;
  const out = lines.map((line) => {
    const hit = /^description:[ \t]*(.*)$/.exec(line);
    if (!hit) return line;
    const raw = hit[1].trim();
    if (!raw || /^["'].*["']$/s.test(raw)) return line;
    if (!/:\s|\s#/.test(raw) && !/^[-?:,[\]{}#&*!|>'"%@`]/.test(raw)) return line;
    changed = true;
    return 'description: ' + JSON.stringify(raw);
  });
  if (!changed) return text;
  return '---\n' + out.join('\n') + '\n---' + m[2] + text.slice(m[0].length);
}

// Guard: no unquoted description may still contain a colon-space after generation.
function assertFrontmatterSafe(root) {
  const problems = [];
  for (const rel of listFiles(root)) {
    if (!rel.endsWith('SKILL.md') && !rel.endsWith('.agent.md')) continue;
    const text = fs.readFileSync(path.join(root, rel), 'utf8');
    const m = /^---\r?\n([\s\S]*?)\r?\n---/.exec(text);
    if (!m) {
      problems.push(`${rel}: no frontmatter`);
      continue;
    }
    for (const line of m[1].split(/\r?\n/)) {
      const hit = /^description:[ \t]*(.*)$/.exec(line);
      if (!hit) continue;
      const raw = hit[1].trim();
      const quoted = /^["'].*["']$/s.test(raw);
      if (!quoted && /:\s/.test(raw)) problems.push(`${rel}: unquoted description contains ": "`);
    }
  }
  if (problems.length) throw new Error('frontmatter check failed:\n  ' + problems.join('\n  '));
}

// Extension-mode instructions: same content, extension wording, plus the
// frontmatter that the probe measured as working (description + applyTo).
export function renderExtensionInstructions(source) {
  let body = String(source).replace(/\r\n/g, '\n').trimStart();

  body = body.replace(
    'This workspace has the Parallax lightweight development flow suite installed (solo direct-execution tier):',
    'This VS Code installation has the Parallax lightweight development flow suite available (bundled with the Parallax extension, solo direct-execution tier):'
  );
  body = body.replace(
    'The seven skills (including wish) live under `.github/skills/` and can be invoked explicitly',
    'The seven skills (including wish) are provided by this extension and can be invoked explicitly'
  );
  body = body.replace(/`\.github\/skills\/`/g, 'the Parallax skills provided by this extension');
  body = body.replace(/`\.github\/agents\/`/g, 'the reviewer agent provided by this extension');

  if (body.includes('.github/skills/')) {
    throw new Error('extension instructions still reference .github/skills/ — update the rewrite rules');
  }

  const frontmatter = [
    '---',
    'description: Parallax development flow (suite instructions)',
    'applyTo: "**"',
    '---',
    '',
  ].join('\n');
  return frontmatter + body;
}

function assertTemplateRefs(skillsRoot) {
  const problems = [];
  for (const name of listDirs(skillsRoot)) {
    const text = fs.readFileSync(path.join(skillsRoot, name, 'SKILL.md'), 'utf8');
    for (const ref of check.templateRefs(text)) {
      if (!check.resolveTemplateRef(skillsRoot, ref)) {
        problems.push(`${name}: unresolved template reference '${ref}'`);
      }
    }
  }
  if (problems.length) throw new Error('template reference check failed:\n  ' + problems.join('\n  '));
}

export function build({ dest = MEDIA, log = () => {}, iconSource = ICON } = {}) {
  const sourceSkills = path.join(SOURCE, 'skills');
  const sourceAgents = path.join(SOURCE, 'agents');
  const sourceInstructions = path.join(SOURCE, 'instructions', 'copilot-instructions.md');

  if (!fs.existsSync(sourceInstructions)) throw new Error(`missing source instructions: ${sourceInstructions}`);
  if (!fs.existsSync(iconSource)) throw new Error(`missing icon source: ${iconSource}`);

  for (const sub of MANAGED) fs.rmSync(path.join(dest, sub), { recursive: true, force: true });
  fs.mkdirSync(dest, { recursive: true });

  // Skills (whole directories, templates included).
  const skills = listDirs(sourceSkills);
  if (!skills.length) throw new Error('no skills found in ' + sourceSkills);
  for (const name of skills) copyTree(path.join(sourceSkills, name), path.join(dest, 'skills', name));

  // Reviewer agent(s).
  copyTree(sourceAgents, path.join(dest, 'agents'));

  // Instructions: project mode (verbatim) + extension mode (rewritten).
  const instructions = fs.readFileSync(sourceInstructions, 'utf8');
  fs.mkdirSync(path.join(dest, 'instructions'), { recursive: true });
  fs.writeFileSync(path.join(dest, 'instructions', 'copilot-instructions.md'), instructions, 'utf8');
  fs.writeFileSync(
    path.join(dest, 'instructions', 'parallax.instructions.md'),
    renderExtensionInstructions(instructions),
    'utf8'
  );

  // Icon (hand-made asset; copied so media/ stays a pure generated tree).
  fs.mkdirSync(dest, { recursive: true });
  fs.copyFileSync(iconSource, path.join(dest, 'icon.png'));

  assertTemplateRefs(path.join(dest, 'skills'));
  assertFrontmatterSafe(path.join(dest, 'skills'));
  assertFrontmatterSafe(path.join(dest, 'agents'));
  log(`generated ${dest}: ${skills.length} skills, ${listFiles(path.join(dest, 'agents')).length} agent file(s), 2 instruction files, 1 icon`);
  return { skills, dest };
}

export function check_({ media = MEDIA } = {}) {
  const tmp = fs.mkdtempSync(path.join(require('node:os').tmpdir(), 'parallax-sync-'));
  try {
    build({ dest: tmp, log: () => {} });
    const rows = install.compareTrees(tmp, media);
    const problems = [];
    for (const row of rows) {
      if (row.state !== 'same') problems.push(`${row.rel}: ${row.state}`);
    }
    // Files under media/ that this generator does not own.
    for (const sub of MANAGED) {
      for (const rel of listFiles(path.join(media, sub))) {
        if (!fs.existsSync(path.join(tmp, sub, rel))) problems.push(`${sub}/${rel}: unexpected file in media/`);
      }
    }
    if (!fs.existsSync(path.join(media, 'icon.png'))) problems.push('icon.png: missing');
    return { ok: problems.length === 0, problems };
  } finally {
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

function main() {
  const args = process.argv.slice(2);
  if (args.includes('--check')) {
    const result = check_();
    if (result.ok) {
      console.log('media/ is in sync with platforms/copilot/');
      return 0;
    }
    console.error('media/ is out of sync — run `node scripts/sync-content.mjs`:\n  ' + result.problems.join('\n  '));
    return 1;
  }
  build({ log: (line) => console.log(line) });
  return 0;
}

if (process.argv[1] && import.meta.url === pathToFileURL(path.resolve(process.argv[1])).href) {
  process.exit(main());
}
