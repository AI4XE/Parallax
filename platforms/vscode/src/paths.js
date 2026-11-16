'use strict';

// Shared constants and path helpers. No vscode dependency: this module is also
// loaded by the unit tests.
const path = require('path');

// Oldest VS Code version this extension has actually been exercised on.
// `engines.vscode` allows older releases on purpose so that the optional
// project install can still be used there; anything below this constant gets a
// one-time "not tested on this version" notice.
const MIN_TESTED_VSCODE = '1.146.0';

// Marker block used when the optional project install merges its instructions
// into an existing copilot-instructions.md. Only this block is ever replaced.
const MARKER_BEGIN = '<!-- parallax:begin -->';
const MARKER_END = '<!-- parallax:end -->';

// Copilot Chat ships built in on recent VS Code builds; older setups install it
// from the marketplace. Either way the id is the same and the lookup is
// case-insensitive.
const COPILOT_EXTENSION_ID = 'GitHub.copilot-chat';

// Directories that must never be copied out of the source tree.
const SKIP_DIRS = new Set(['__pycache__', 'node_modules', '.git', '.pytest_cache']);
// Runtime leftovers that may appear in a skill directory.
const SKIP_FILE_RE = /(^|\/)(\.gitignore|\.gitkeep|\.DS_Store|Thumbs\.db|\.dropin)$|\.pyc$|\.log$/;

const SKILL_DIR = 'media/skills';
const AGENT_DIR = 'media/agents';
const INSTRUCTIONS_DIR = 'media/instructions';
// Project-mode copy of the suite instructions (verbatim; written into the
// workspace .github/ by the optional project install).
const PROJECT_INSTRUCTIONS = 'media/instructions/copilot-instructions.md';
// Extension-mode instructions (contributed to Copilot as chat instructions).
const EXTENSION_INSTRUCTIONS = 'media/instructions/parallax.instructions.md';

function mediaRoot(extensionPath) {
  return path.join(extensionPath, 'media');
}

function skillsRoot(extensionPath) {
  return path.join(extensionPath, SKILL_DIR);
}

function agentsRoot(extensionPath) {
  return path.join(extensionPath, AGENT_DIR);
}

function instructionsPath(extensionPath, kind) {
  return path.join(extensionPath, kind === 'project' ? PROJECT_INSTRUCTIONS : EXTENSION_INSTRUCTIONS);
}

function githubDir(workspaceRoot) {
  return path.join(workspaceRoot, '.github');
}

function projectSkillsDir(workspaceRoot) {
  return path.join(githubDir(workspaceRoot), 'skills');
}

function projectAgentsDir(workspaceRoot) {
  return path.join(githubDir(workspaceRoot), 'agents');
}

function projectInstructionsFile(workspaceRoot) {
  return path.join(githubDir(workspaceRoot), 'copilot-instructions.md');
}

// `.github/parallax-backup-<UTC timestamp>/`
function backupDir(workspaceRoot, date) {
  const stamp = (date || new Date()).toISOString().replace(/[:.]/g, '-').replace('T', '_').replace('Z', '');
  return path.join(githubDir(workspaceRoot), 'parallax-backup-' + stamp);
}

module.exports = {
  MIN_TESTED_VSCODE,
  MARKER_BEGIN,
  MARKER_END,
  COPILOT_EXTENSION_ID,
  SKIP_DIRS,
  SKIP_FILE_RE,
  SKILL_DIR,
  AGENT_DIR,
  INSTRUCTIONS_DIR,
  PROJECT_INSTRUCTIONS,
  EXTENSION_INSTRUCTIONS,
  mediaRoot,
  skillsRoot,
  agentsRoot,
  instructionsPath,
  githubDir,
  projectSkillsDir,
  projectAgentsDir,
  projectInstructionsFile,
  backupDir,
};
