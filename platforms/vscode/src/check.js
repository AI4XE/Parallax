'use strict';

// Installation health check, mode aware.
//
// Two independent facts are reported side by side:
//   1. what the extension itself provides (always the normal state), and
//   2. whether the optional project install in `.github/` is present and current.
// Reporting the project install as "missing" would be wrong — that path is
// optional — so it is never counted as a failure by itself.

const fs = require('fs');
const path = require('path');
const paths = require('./paths');
const install = require('./install');

const TEMPLATE_REF_RE = /`([A-Za-z0-9._-]*\/?templates\/[A-Za-z0-9._-]+(?:\/[A-Za-z0-9._-]+)*)`/g;

function readTextSafe(file) {
  try {
    return fs.readFileSync(file, 'utf8');
  } catch {
    return null;
  }
}

// `templates/foo.md` or `qgo/templates/foo.md` references inside a SKILL.md.
function templateRefs(text) {
  const refs = new Set();
  String(text || '').replace(TEMPLATE_REF_RE, (_m, ref) => {
    refs.add(ref.replace(/^\.\//, ''));
    return _m;
  });
  return [...refs].sort();
}

// Resolve a reference against an installed skills tree (cross-skill allowed:
// gclear points at the qgo skill's templates/solo.md).
function resolveTemplateRef(skillsRoot, ref) {
  if (ref.includes('/') && !ref.startsWith('templates/')) {
    return install.exists(path.join(skillsRoot, ref));
  }
  const dirs = install.listDirs(skillsRoot);
  return dirs.some((name) => install.exists(path.join(skillsRoot, name, ref)));
}

function collectBundled(extensionPath) {
  const skillsRoot = paths.skillsRoot(extensionPath);
  const names = install.listDirs(skillsRoot);
  const skills = names.map((name) => {
    const text = readTextSafe(path.join(skillsRoot, name, 'SKILL.md'));
    return {
      name,
      version: text ? install.skillVersion(text) : null,
      refs: text ? templateRefs(text) : [],
    };
  });
  const agents = install.listFiles(paths.agentsRoot(extensionPath));
  const instructionsFile = paths.instructionsPath(extensionPath, 'extension');
  const instructionsText = readTextSafe(instructionsFile);
  return {
    version: install.readExtensionVersion(extensionPath),
    root: extensionPath,
    skills,
    agents,
    instructions: {
      path: instructionsFile,
      present: instructionsText !== null,
      // Extension mode must never point at workspace paths.
      workspacePathLeak: instructionsText ? instructionsText.includes('.github/skills/') : false,
      hasFrontmatter: instructionsText ? Object.keys(install.parseFrontmatter(instructionsText)).length > 0 : false,
    },
    ok:
      skills.length > 0 &&
      skills.every((s) => s.version) &&
      agents.length > 0 &&
      instructionsText !== null &&
      !(instructionsText || '').includes('.github/skills/'),
  };
}

function checkSkill({ extensionPath, workspaceRoot, name }) {
  const srcRoot = path.join(paths.skillsRoot(extensionPath), name);
  const destRoot = path.join(paths.projectSkillsDir(workspaceRoot), name);
  const srcText = readTextSafe(path.join(srcRoot, 'SKILL.md'));
  const destText = readTextSafe(path.join(destRoot, 'SKILL.md'));
  const bundledVersion = srcText ? install.skillVersion(srcText) : null;

  if (destText === null) {
    return { name, present: false, status: 'missing', installedVersion: null, bundledVersion };
  }

  const installedVersion = install.skillVersion(destText);
  const rows = install.compareTrees(srcRoot, destRoot);
  const missingFiles = rows.filter((row) => row.state === 'missing').map((row) => row.rel);
  const changedFiles = rows.filter((row) => row.state === 'changed').map((row) => row.rel);
  const extraFiles = rows.filter((row) => row.state === 'extra').map((row) => row.rel);

  // Template references must resolve inside the installed skills tree, not only
  // inside this skill's own directory (gclear -> qgo/templates/solo.md).
  const skillsRoot = paths.projectSkillsDir(workspaceRoot);
  const unresolved = templateRefs(destText).filter((ref) => !resolveTemplateRef(skillsRoot, ref));

  let status = 'ok';
  if (installedVersion && bundledVersion && install.compareVersions(installedVersion, bundledVersion) < 0) {
    status = 'outdated';
  } else if (missingFiles.length || unresolved.length) {
    status = 'incomplete';
  } else if (changedFiles.length || extraFiles.length) {
    status = 'modified';
  }

  return {
    name,
    present: true,
    status,
    installedVersion,
    bundledVersion,
    missingFiles,
    changedFiles,
    extraFiles,
    unresolvedRefs: unresolved,
  };
}

function checkProject({ extensionPath, workspaceRoot }) {
  const bundled = collectBundled(extensionPath);
  const skills = bundled.skills.map((skill) => checkSkill({ extensionPath, workspaceRoot, name: skill.name }));

  const agentRels = bundled.agents;
  const agents = agentRels.map((rel) => {
    const src = path.join(paths.agentsRoot(extensionPath), rel);
    const dest = path.join(paths.projectAgentsDir(workspaceRoot), rel);
    const present = install.exists(dest);
    const status = !present ? 'missing' : install.sameFile(src, dest) ? 'ok' : 'modified';
    return { file: rel, present, status };
  });

  const instructionsFile = paths.projectInstructionsFile(workspaceRoot);
  const existing = readTextSafe(instructionsFile);
  const expected = install.renderInstructionBlock(
    readTextSafe(paths.instructionsPath(extensionPath, 'project')) || ''
  );
  const instructions = {
    path: instructionsFile,
    present: existing !== null,
    hasBlock: existing !== null && existing.includes(paths.MARKER_BEGIN) && existing.includes(paths.MARKER_END),
    upToDate: existing !== null && existing.replace(/\r\n/g, '\n').includes(expected.replace(/\r\n/g, '\n')),
  };

  const installed = skills.some((skill) => skill.present) || agents.some((agent) => agent.present) || instructions.present;
  const problems = [];
  for (const skill of skills) {
    if (skill.present && skill.status !== 'ok') problems.push(`${skill.name}: ${skill.status}`);
  }
  for (const agent of agents) {
    if (agent.present && agent.status !== 'ok') problems.push(`${agent.file}: ${agent.status}`);
  }
  if (instructions.present && (!instructions.hasBlock || !instructions.upToDate)) {
    problems.push('instructions block: ' + (instructions.hasBlock ? 'outdated' : 'marker missing'));
  }

  return {
    workspace: workspaceRoot,
    installed,
    skills,
    agents,
    instructions,
    problems,
    // Only meaningful when something is installed; an absent project install is
    // a normal state (the extension provides the skills itself).
    healthy: !installed || problems.length === 0,
  };
}

function formatReport(bundled, project) {
  const lines = [];
  lines.push('Parallax for GitHub Copilot');
  lines.push(`Extension version: ${bundled.version || 'unknown'}`);
  lines.push('');
  lines.push('Bundled with the extension (always available, no project files):');
  lines.push(`  skills: ${bundled.skills.length} (${bundled.skills.map((s) => s.name).join(', ')})`);
  lines.push(`  agent: ${bundled.agents.join(', ') || 'missing'}`);
  lines.push(
    `  instructions: ${bundled.instructions.present ? 'contributed' : 'missing'}` +
      (bundled.instructions.workspacePathLeak ? ' (WARNING: contains workspace paths)' : '')
  );
  lines.push(`  status: ${bundled.ok ? 'ok' : 'INCOMPLETE'}`);
  lines.push('');
  if (!project) {
    lines.push('Project install: not checked.');
    return lines.join('\n');
  }
  lines.push(`Project install (${project.workspace}):`);
  if (!project.installed) {
    lines.push('  not installed — this is the normal default (optional fallback path).');
    return lines.join('\n');
  }
  for (const skill of project.skills) {
    const version = skill.installedVersion || '?';
    let line = `  ${skill.present ? (skill.status === 'ok' ? 'ok     ' : 'ATTENTION') : 'absent'} skills/${skill.name} (${version})`;
    if (skill.status === 'outdated') line += ` — bundled ${skill.bundledVersion} is newer, run Install / Update`;
    if (skill.status === 'incomplete') line += ` — missing ${skill.missingFiles.length} file(s)`;
    if (skill.status === 'modified') line += ` — ${skill.changedFiles.length} modified file(s)`;
    if (skill.unresolvedRefs && skill.unresolvedRefs.length) line += ` — unresolved template reference(s): ${skill.unresolvedRefs.join(', ')}`;
    lines.push(line);
  }
  for (const agent of project.agents) {
    lines.push(`  ${agent.present ? (agent.status === 'ok' ? 'ok     ' : 'ATTENTION') : 'absent'} agents/${agent.file}`);
  }
  lines.push(
    `  ${project.instructions.present ? (project.instructions.upToDate ? 'ok     ' : 'ATTENTION') : 'absent'} .github/copilot-instructions.md` +
      (project.instructions.present && !project.instructions.hasBlock ? ' — marker block missing' : '')
  );
  lines.push('');
  lines.push(project.healthy ? 'Result: project install is up to date.' : 'Result: project install needs attention (Install / Update or Force Reinstall).');
  return lines.join('\n');
}

module.exports = {
  templateRefs,
  resolveTemplateRef,
  collectBundled,
  checkSkill,
  checkProject,
  formatReport,
};
