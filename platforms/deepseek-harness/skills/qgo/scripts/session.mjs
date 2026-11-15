#!/usr/bin/env node
// Read-only session probe for DSH (Node port of the former Python probe).
//
// Side evidence only: which session directory serves a cwd, which session id
// is newest there, and which dsh processes are alive. Real-time executor
// state must come from the orchestrator's own subagent control tools
// (list_agents / completion notices / send_message). This script never
// writes and never controls processes.
//
// Usage:
//   node session.mjs identity [--cwd DIR] [--session-id ID]
//   node session.mjs idle [--cwd DIR]
//   node session.mjs --self-check
//
// `idle` is a conservative approximation under headless semantics: a
// headless session's process is alive exactly while it runs, so "no live
// dsh headless/web process" approximates idle. The pid->session mapping is
// not exposed by the harness, so the check is process-level. On a host that
// keeps a resident `dsh web` process, `idle` is always false — use
// `identity --session-id <ID>` (`session_processes`) for resume gating.
//
// Process enumeration backends: Linux /proc, macOS/BSD `ps -axo pid=,command=`,
// Windows PowerShell CIM (`Win32_Process`). Where none works the probe
// reports null ("unknown"), never a false "nothing is running".
//
// Runtime: only the Node that ships with dsh.

import fs from "node:fs";
import path from "node:path";
import { spawn } from "node:child_process";
import { pathToFileURL } from "node:url";

import { baseName, detectProcessBackend, escapedCwd, selfCheck as contextSelfCheck } from "./context.mjs";

/** Windows/POSIX path normalization used before any comparison. */
function normalize(p) {
  return String(p).replace(/\\/g, "/");
}

/** Split a raw command line into argv tokens, honoring double quotes. */
export function splitCommandLine(line) {
  const tokens = [];
  let current = "";
  let quoted = false;
  let started = false;
  for (const ch of String(line)) {
    if (ch === '"') {
      quoted = !quoted;
      started = true;
    } else if (!quoted && (ch === " " || ch === "\t")) {
      if (started || current) tokens.push(current);
      current = "";
      started = false;
    } else {
      current += ch;
      started = true;
    }
  }
  if (started || current) tokens.push(current);
  return tokens;
}

/**
 * True when an argv identifies a dsh entry point.
 *
 * The entry is only looked for at argv[0] or at node's script position, so
 * `tail -f /var/log/dsh`, `vim .../dsh` and `grep dsh` never match.
 *   A. argv[0] basename is `dsh` / `dsh.exe`.
 *   B. argv[0] is `node` / `node.exe` and the first non-option token ends
 *      with "bin/dsh" or "@deepseek-ai/dsh/lib/bin.js" (covers
 *      `node .../app/bin/dsh web` and Windows `dsh.cmd` -> node).
 *   C. Windows shell launchers: argv[0] is a shell (cmd/powershell/pwsh)
 *      and a token is `.../app/dsh.cmd`.
 * Shell-wrapped invocations (`bash -lc dsh ...`) are deliberately not
 * recognized: a miss is safer than a false positive.
 */
export function isDshInvocation(argv, { windows = process.platform === "win32" } = {}) {
  if (!argv || argv.length === 0) return false;
  const eq = (a, b) => (windows ? a.toLowerCase() === b.toLowerCase() : a === b);
  const head = baseName(argv[0]);
  if (eq(head, "dsh") || eq(head, "dsh.exe")) return true;

  const rest = argv.slice(1);
  const nonOption = rest.filter((token) => token && !token.startsWith("-"));

  if (eq(head, "node") || eq(head, "node.exe")) {
    const script = nonOption[0];
    if (!script) return false;
    const scriptPath = normalize(script);
    return scriptPath.endsWith("/bin/dsh") || scriptPath.endsWith("/@deepseek-ai/dsh/lib/bin.js");
  }

  if (eq(head, "cmd") || eq(head, "cmd.exe") || eq(head, "powershell") || eq(head, "powershell.exe") || eq(head, "pwsh") || eq(head, "pwsh.exe")) {
    return nonOption.some((token) => normalize(token).toLowerCase().endsWith("/app/dsh.cmd"));
  }
  return false;
}

/** Parse `/proc/<pid>/cmdline` contents into an argv (empty on failure). */
function parseCmdline(buffer) {
  const parts = buffer.toString("utf8").split("\0").filter((part) => part.length > 0);
  return parts;
}

/**
 * Linux: enumerate via /proc. Returns null when /proc is unavailable.
 * `procRoot` is injectable so tests can point at a fixture tree.
 */
export function procfsProcesses(procRoot = "/proc") {
  let entries;
  try {
    entries = fs.readdirSync(procRoot, { withFileTypes: true });
  } catch {
    return null;
  }
  const result = [];
  for (const entry of entries) {
    if (!entry.isDirectory() || !/^\d+$/.test(entry.name)) continue;
    let argv;
    try {
      argv = parseCmdline(fs.readFileSync(path.join(procRoot, entry.name, "cmdline")));
    } catch {
      continue;
    }
    if (argv.length > 0 && isDshInvocation(argv, { windows: false })) {
      result.push({ pid: Number(entry.name), argv });
    }
  }
  return result;
}

/** Parse `ps -axo pid=,command=` text into dsh process records. */
export function parsePsOutput(text, { windows = false } = {}) {
  const result = [];
  for (const line of String(text).split(/\r?\n/)) {
    const match = line.trim().match(/^(\d+)\s+(.*)$/);
    if (!match) continue;
    const argv = splitCommandLine(match[2]);
    if (argv.length > 0 && isDshInvocation(argv, { windows })) {
      result.push({ pid: Number(match[1]), argv });
    }
  }
  return result;
}

function run(command, args, timeoutMs) {
  return new Promise((resolve) => {
    let child;
    try {
      child = spawn(command, args, { stdio: ["ignore", "pipe", "pipe"] });
    } catch {
      resolve(null);
      return;
    }
    const chunks = [];
    const timer = setTimeout(() => {
      try {
        child.kill();
      } catch {
        /* already gone */
      }
      resolve(null);
    }, timeoutMs);
    child.stdout.on("data", (chunk) => chunks.push(chunk));
    child.on("error", () => {
      clearTimeout(timer);
      resolve(null);
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      resolve(code === 0 ? Buffer.concat(chunks) : null);
    });
  });
}

/** macOS/BSD: `ps -axo pid=,command=`. Returns null when unavailable. */
export async function psProcesses() {
  const output = await run("ps", ["-axo", "pid=,command="], 10000);
  if (output === null) return null;
  return parsePsOutput(output.toString("utf8"));
}

/** Parse PowerShell `Win32_Process` JSON (object or array). */
export function parsePowershellJson(text) {
  let data;
  try {
    data = JSON.parse(String(text).trim() || "[]");
  } catch {
    return null;
  }
  const rows = Array.isArray(data) ? data : [data];
  const result = [];
  for (const row of rows) {
    if (!row || typeof row.CommandLine !== "string" || row.CommandLine.length === 0) continue;
    const argv = splitCommandLine(row.CommandLine);
    if (argv.length > 0 && isDshInvocation(argv, { windows: true })) {
      result.push({ pid: Number(row.ProcessId), argv });
    }
  }
  return result;
}

/** Windows: PowerShell CIM `Win32_Process`. Returns null on failure/timeout. */
export async function powershellProcesses() {
  const script =
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; " +
    "Get-CimInstance Win32_Process | Select-Object ProcessId,CommandLine | ConvertTo-Json -Compress";
  const output = await run("powershell.exe", ["-NoProfile", "-NonInteractive", "-Command", script], 20000);
  if (output === null) return null;
  return parsePowershellJson(output.toString("utf8"));
}

/**
 * Enumerate live dsh processes with the platform's backend.
 * Returns { backend, processes } where processes is null when unknown.
 */
export async function enumerateDshProcesses() {
  const backend = detectProcessBackend();
  if (backend === "procfs") {
    const procs = procfsProcesses();
    if (procs !== null) return { backend, processes: procs };
    const fallback = await psProcesses();
    return fallback === null ? { backend: null, processes: null } : { backend: "ps", processes: fallback };
  }
  const procs = backend === "powershell" ? await powershellProcesses() : await psProcesses();
  return procs === null ? { backend: null, processes: null } : { backend, processes: procs };
}

/** Public shape kept from the former Python probe: pid + the first four argv tokens. */
function toPublic(procs) {
  if (procs === null) return null;
  return procs.map((proc) => ({ pid: proc.pid, argv_head: proc.argv.slice(0, 4) }));
}

/**
 * Live processes whose argv is a dsh entry point.
 * A list, or null when this platform exposes no usable enumeration.
 */
export async function runningDshProcesses() {
  const { processes } = await enumerateDshProcesses();
  return toPublic(processes);
}

export async function identity(
  cwd = process.cwd(),
  { dshHome = process.env.DSH_HOME, sessionId = null, enumerate = enumerateDshProcesses } = {}
) {
  if (!dshHome) throw new Error("DSH_HOME not set; source the harness env first");
  const sessionDir = path.join(dshHome, "sessions", escapedCwd(cwd));
  let latest = null;
  if (isDir(sessionDir)) {
    const sessions = fs
      .readdirSync(sessionDir, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name.startsWith("session-"))
      .map((entry) => path.join(sessionDir, entry.name))
      .sort((a, b) => mtime(a) - mtime(b));
    latest = sessions.length > 0 ? path.basename(sessions[sessions.length - 1]) : null;
  }
  const { processes } = await enumerate();
  let sessionProcesses = null;
  if (processes !== null) {
    sessionProcesses = sessionId
      ? toPublic(processes.filter((proc) => proc.argv.join(" ").includes(sessionId)))
      : [];
  }
  return {
    cwd: String(cwd),
    sessions_dir: sessionDir,
    session_dir_exists: isDir(sessionDir),
    latest_session_id: latest,
    running_dsh_processes: toPublic(processes),
    session_processes: sessionProcesses,
  };
}

export async function idle(cwd = process.cwd(), { dshHome = process.env.DSH_HOME, enumerate = enumerateDshProcesses } = {}) {
  const info = await identity(cwd, { dshHome, enumerate });
  const procs = info.running_dsh_processes;
  return {
    cwd: String(cwd),
    idle: procs === null ? null : procs.length === 0,
    note:
      "heuristic: true when no live dsh process exists at all; null (unknown) when this " +
      "platform cannot enumerate processes; a resident `dsh web` host makes it always false — " +
      "executor subagent state must come from subagent control tools",
  };
}

function isDir(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function mtime(p) {
  try {
    return fs.statSync(p).mtimeMs;
  } catch {
    return 0;
  }
}

const HELP = `Read-only DSH session probe (Node).

Usage:
  node session.mjs identity [--cwd DIR] [--session-id ID]
  node session.mjs idle [--cwd DIR]
  node session.mjs --self-check

Options:
  --cwd DIR            project directory of the session (default: $PWD)
  --session-id ID      report only dsh processes whose command line contains ID
  --self-check         print platform/runtime diagnostics as JSON
  -h, --help           this message
`;

function parseArgs(argv) {
  const args = { command: null, cwd: process.cwd(), sessionId: null, selfCheck: false, help: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const take = (name) => {
      if (i + 1 >= argv.length) throw new Error(`${name} needs a value`);
      return argv[++i];
    };
    if (arg === "identity" || arg === "idle") args.command = arg;
    else if (arg === "--cwd") args.cwd = take("--cwd");
    else if (arg === "--session-id") args.sessionId = take("--session-id");
    else if (arg === "--self-check") args.selfCheck = true;
    else if (arg === "-h" || arg === "--help") args.help = true;
    else throw new Error(`unknown argument: ${arg}`);
  }
  return args;
}

async function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv);
    if (args.help) {
      process.stdout.write(HELP);
      return;
    }
    if (args.selfCheck) {
      process.stdout.write(JSON.stringify(contextSelfCheck()) + "\n");
      return;
    }
    if (args.command === "identity") {
      process.stdout.write(JSON.stringify(await identity(args.cwd, { sessionId: args.sessionId })) + "\n");
    } else if (args.command === "idle") {
      process.stdout.write(JSON.stringify(await idle(args.cwd)) + "\n");
    } else {
      process.stderr.write(HELP);
      process.exitCode = 1;
    }
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
