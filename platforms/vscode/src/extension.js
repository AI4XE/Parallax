'use strict';

// VS Code glue: command registration, status bar, Copilot detection and
// version notices. All install/check logic lives in install.js / check.js so it
// stays unit-testable without a VS Code runtime.

const path = require('path');
const vscode = require('vscode');
const paths = require('./paths');
const install = require('./install');
const check = require('./check');

let output;
let statusItem;

function log(line) {
  // The channel can be disposed while a command is still finishing (window
  // reload, extension restart). Logging must never turn into a failure.
  try {
    if (output) output.appendLine(line);
  } catch {
    /* disposed */
  }
}

function showOutput(preserveFocus) {
  try {
    if (output) output.show(preserveFocus);
  } catch {
    /* disposed */
  }
}

function workspaceFolders() {
  return vscode.workspace.workspaceFolders || [];
}

async function pickWorkspaceFolder() {
  const folders = workspaceFolders();
  if (!folders.length) {
    vscode.window.showWarningMessage(
      'Parallax: open a folder first. The project install writes into <folder>/.github/.'
    );
    return null;
  }
  if (folders.length === 1) return folders[0];
  const picked = await vscode.window.showQuickPick(
    folders.map((folder) => ({ label: folder.name, description: folder.uri.fsPath, folder })),
    { placeHolder: 'Select the workspace folder to install Parallax into' }
  );
  return picked ? picked.folder : null;
}

function copilotInstalled() {
  return Boolean(vscode.extensions.getExtension(paths.COPILOT_EXTENSION_ID));
}

function warnIfNoCopilot() {
  if (copilotInstalled()) return false;
  const message =
    'Parallax: GitHub Copilot Chat is required to use the bundled skills. Install it from the Marketplace to continue.';
  vscode.window
    .showWarningMessage(message, 'Open Copilot Chat extension')
    .then((choice) => {
      if (choice) {
        vscode.commands.executeCommand('workbench.extensions.search', 'GitHub.copilot-chat');
      }
    });
  return true;
}

function reportInstall(result) {
  log('');
  log(install.summarizeInstall(result));
  log(`  mode: ${result.mode}`);
  log(`  changed files: ${result.changed}, unchanged: ${result.unchanged}, written: ${result.written}`);
  for (const skill of result.skills) {
    log(`  skills/${skill.name}: ${skill.files} file(s)` + (skill.changed ? `, ${skill.changed} changed` : ''));
  }
  for (const agent of result.agents) log(`  agents/${agent.file}: ${agent.state}`);
  log(`  instructions: ${result.instructions.mode} (${result.instructions.path})`);
  if (result.backupDir) {
    log(`  backup: ${result.backupDir} (${result.backupFiles.length} file(s))`);
    log(`  restore with: copy the files from the backup back over .github/`);
  } else {
    log('  backup: none needed');
  }
}

async function runInstall(context, mode) {
  if (warnIfNoCopilot()) return;
  const folder = await pickWorkspaceFolder();
  if (!folder) return;

  if (mode === 'force') {
    const confirm = await vscode.window.showWarningMessage(
      `Force reinstall of Parallax into ${folder.name}? Modified files under .github/ will be backed up first, then overwritten.`,
      { modal: true },
      'Force Reinstall'
    );
    if (confirm !== 'Force Reinstall') return;
  }

  try {
    const result = await vscode.window.withProgress(
      { location: vscode.ProgressLocation.Notification, title: `Parallax: installing into ${folder.name}` },
      async () => install.installToWorkspace({
        extensionPath: context.extensionPath,
        workspaceRoot: folder.uri.fsPath,
        mode,
      })
    );
    reportInstall(result);
    showOutput(true);
    const choice = await vscode.window.showInformationMessage(
      `${install.summarizeInstall(result)}. Reload the window so Copilot picks up the changes.`,
      'Reload Window'
    );
    if (choice === 'Reload Window') {
      await vscode.commands.executeCommand('workbench.action.reloadWindow');
    }
  } catch (err) {
    log(`install failed: ${err && err.stack ? err.stack : err}`);
    showOutput(true);
    vscode.window.showErrorMessage(`Parallax: install failed — ${err && err.message ? err.message : err}`);
  }
}

async function runCheck(context) {
  const noCopilot = !copilotInstalled();
  const bundled = check.collectBundled(context.extensionPath);
  let project = null;
  const folders = workspaceFolders();
  if (folders.length) {
    const folder = folders.length === 1
      ? folders[0]
      : await vscode.window.showQuickPick(
        folders.map((f) => ({ label: f.name, description: f.uri.fsPath, folder: f })),
        { placeHolder: 'Select a workspace folder to check (Esc: check the bundled content only)' }
      ).then((picked) => (picked ? picked.folder : null));
    if (folder) project = check.checkProject({ extensionPath: context.extensionPath, workspaceRoot: folder.uri.fsPath });
  }

  try {
    if (output) output.clear();
  } catch {
    /* disposed */
  }
  log(check.formatReport(bundled, project));
  if (noCopilot) log('\nGitHub Copilot Chat: NOT installed — the skills cannot run until it is.');
  showOutput(true);

  if (noCopilot) {
    warnIfNoCopilot();
    return;
  }
  if (!bundled.ok) {
    vscode.window.showErrorMessage('Parallax: the extension package looks incomplete (bundled content missing). Reinstall the extension.');
    return;
  }
  if (project && project.installed && !project.healthy) {
    const choice = await vscode.window.showWarningMessage(
      `Parallax: project install needs attention — ${project.problems.join('; ')}`,
      'Install / Update',
      'Force Reinstall'
    );
    if (choice === 'Install / Update') await runInstall(context, 'update');
    if (choice === 'Force Reinstall') await runInstall(context, 'force');
    return;
  }
  if (project && project.installed) {
    vscode.window.showInformationMessage(
      `Parallax: bundled skills ok (extension ${bundled.version}); project install up to date in ${path.basename(project.workspace)}.`
    );
    return;
  }
  vscode.window.showInformationMessage(
    `Parallax: bundled skills ok (extension ${bundled.version}) — no project install (optional).`
  );
}

async function openDocumentation(context) {
  const readme = path.join(context.extensionPath, 'README.md');
  if (install.exists(readme)) {
    await vscode.commands.executeCommand('markdown.showPreview', vscode.Uri.file(readme));
    return;
  }
  await vscode.env.openExternal(vscode.Uri.parse('https://github.com/AI4XE/Parallax'));
}

async function showMenu(context) {
  const items = [
    { label: '$(cloud-download) Install / Update into project', command: 'parallax.install' },
    { label: '$(check) Check Installation', command: 'parallax.check' },
    { label: '$(sync) Force Reinstall (with backup)', command: 'parallax.forceReinstall' },
    { label: '$(book) Open Documentation', command: 'parallax.openDocs' },
  ];
  const picked = await vscode.window.showQuickPick(items, { placeHolder: 'Parallax for GitHub Copilot' });
  if (picked) await vscode.commands.executeCommand(picked.command);
}

function versionNotice(context) {
  if (!install.shouldShowVersionNotice(vscode.version, paths.MIN_TESTED_VSCODE)) return;
  const key = `parallax.versionNotice.${vscode.version}`;
  if (context.globalState.get(key)) return;
  context.globalState.update(key, true);
  vscode.window
    .showWarningMessage(
      `Parallax has only been tested on VS Code ${paths.MIN_TESTED_VSCODE} and newer (this is ${vscode.version}). ` +
        'If the bundled skills do not appear, use "Parallax: Install / Update" to install them into the project instead.',
      'Install / Update'
    )
    .then((choice) => {
      if (choice === 'Install / Update') runInstall(context, 'update');
    });
}

function activate(context) {
  output = vscode.window.createOutputChannel('Parallax');
  context.subscriptions.push(output);

  const version = (context.extension.packageJSON && context.extension.packageJSON.version) || '0.0.0';
  log(`Parallax for GitHub Copilot ${version} activated (VS Code ${vscode.version}).`);

  statusItem = vscode.window.createStatusBarItem(vscode.StatusBarAlignment.Right, 100);
  statusItem.command = 'parallax.menu';
  statusItem.text = '$(book) Parallax';
  statusItem.tooltip = `Parallax for GitHub Copilot ${version} — click for commands`;
  context.subscriptions.push(statusItem);
  statusItem.show();

  if (!copilotInstalled()) {
    statusItem.text = '$(warning) Parallax';
    statusItem.tooltip = 'GitHub Copilot Chat is not installed — Parallax skills cannot run.';
    log('GitHub Copilot Chat not found.');
  }

  context.subscriptions.push(
    vscode.commands.registerCommand('parallax.install', () => runInstall(context, 'update')),
    vscode.commands.registerCommand('parallax.check', () => runCheck(context)),
    vscode.commands.registerCommand('parallax.forceReinstall', () => runInstall(context, 'force')),
    vscode.commands.registerCommand('parallax.openDocs', () => openDocumentation(context)),
    vscode.commands.registerCommand('parallax.menu', () => showMenu(context))
  );

  try {
    versionNotice(context);
  } catch (err) {
    log(`version notice failed: ${err && err.message ? err.message : err}`);
  }
}

function deactivate() {
  if (statusItem) statusItem.dispose();
  if (output) output.dispose();
}

module.exports = { activate, deactivate };
