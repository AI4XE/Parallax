#!/usr/bin/env node
// Read-only context-usage probe for DSH sessions (Node port of the former Python probe).
//
// Parses the newest persisted session log under $DSH_HOME/sessions/ and
// reports the latest token-usage record as an approximate context occupancy.
// Heuristic: for LLM APIs with prefix caching, the final request's
// inputTokens + cacheReadTokens approximates the live context size; output
// tokens of the last turn are reported separately.
//
// This script never writes. It does not decide phases or completion.
//
// Usage:
//   node context.mjs usage [--cwd DIR] [--session-id ID] [--window-tokens N]
//   node context.mjs --self-check
//
// Output is a single JSON object on stdout. `--session-id` is required when
// the target is a subagent session (their directories are plain UUIDs;
// without the flag the newest log in the projectKey directory wins).
//
// `--cwd` defaults to the current working directory. `--window-tokens`
// defaults to 1000000 when the model window cannot be confirmed by the
// caller (the caller is expected to pass a confirmed value).
//
// Runtime: only the Node that ships with dsh. No Python, no zstd CLI:
// decompression uses node:zlib (zstd support needs Node >= 22.15 / 23.8).
//
// This module also hosts the primitives shared with session.mjs
// (escapedCwd / process-backend detection) so the two CLIs stay
// dependency-free and import in one direction only.

import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { pathToFileURL } from "node:url";

const ZSTD_MAGIC = 0xfd2fb528;
const ZSTD_SKIPPABLE_MAGIC = 0x184d2a50;
const ZSTD_MAX_BLOCK_SIZE = 128 * 1024;

/**
 * DSH projectKey for a session cwd (dsh 0.2.2-rc.1, session-persistence-jsonl).
 *
 * Faithful port of the harness `projectKey()` (UTF-16 code-unit iteration):
 * separator runs ("/", "\\", ":") collapse to a single "-"; safe chars
 * [A-Za-z0-9._-] stay literal; "~" and every other code unit become "~XXXX"
 * (uppercase hex); all leading dashes are stripped ("root" if empty); the
 * result is wrapped in leading and trailing "--".
 * Example: /opt/x/Y -> --opt-x-Y--
 */
export function escapedCwd(cwd) {
  const raw = String(cwd);
  let readable = "";
  let separatorRun = false;
  for (let i = 0; i < raw.length; i++) {
    const code = raw.charCodeAt(i);
    const ch = String.fromCharCode(code);
    if (ch === "/" || ch === "\\" || ch === ":") {
      if (!separatorRun) readable += "-";
      separatorRun = true;
    } else if (ch !== "~" && /^[A-Za-z0-9._-]$/.test(ch)) {
      readable += ch;
      separatorRun = false;
    } else {
      readable += "~" + code.toString(16).toUpperCase().padStart(4, "0");
      separatorRun = false;
    }
  }
  const core = readable.replace(/^-+/, "") || "root";
  return `--${core.slice(0, 251)}--`;
}

/** Last path component for either separator style (portable across OSes). */
export function baseName(p) {
  const parts = String(p).split(/[\\/]/);
  return parts[parts.length - 1] || parts[parts.length - 2] || "";
}

/** Newest directory whose name embeds the cwd basename, or null. */
export function findSessionDir(dshHome, cwd) {
  const root = path.join(dshHome, "sessions");
  const exact = path.join(root, escapedCwd(cwd));
  if (isDir(exact)) return exact;
  if (!isDir(root)) return null;
  const base = baseName(cwd);
  let candidates = [];
  try {
    candidates = fs
      .readdirSync(root, { withFileTypes: true })
      .filter((entry) => entry.isDirectory() && entry.name.includes(base))
      .map((entry) => path.join(root, entry.name));
  } catch {
    return null;
  }
  if (candidates.length === 0) return null;
  return candidates.reduce((best, dir) => (mtime(dir) > mtime(best) ? dir : best));
}

/**
 * Locate a session log under a projectKey directory.
 *
 * Session subdirectories are not uniformly named: top-level/web sessions use
 * `session-<id>` while subagent sessions use a plain UUID. Scan every
 * subdirectory, then filter by `--session-id` when one is given.
 */
export function findSessionLog(sessionDir, sessionId) {
  let logs = [];
  let entries = [];
  try {
    entries = fs.readdirSync(sessionDir, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;
    const sub = path.join(sessionDir, entry.name);
    let files = [];
    try {
      files = fs.readdirSync(sub, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const file of files) {
      if (file.isFile() && /^session\.v.*\.jsonl\.zstd$/.test(file.name)) {
        logs.push(path.join(sub, file.name));
      }
    }
  }
  logs.sort((a, b) => mtime(a) - mtime(b));
  if (sessionId) logs = logs.filter((log) => path.basename(path.dirname(log)).includes(sessionId));
  if (logs.length === 0) return null;
  return logs[logs.length - 1];
}

/**
 * Decode frames using their standard headers and block lengths, never payload
 * magic. Node's one-shot decoder can stop after the first unknown-size frame,
 * so each structurally complete standard frame is decoded separately. Native
 * decoding validates compressed data, dictionaries, content size and checksum;
 * any error in a complete frame is fatal, including the last frame.
 *
 * An incomplete final structure can be an active writer's tail, but a snapshot
 * cannot prove that a writer is active. Keep only prior complete frames and
 * report its offset/stage explicitly. Callers requiring this distinction should
 * use this API rather than the legacy Buffer-only decodeZstd wrapper.
 */
export function decodeZstdDetailed(buffer, fileLabel = "<buffer>") {
  if (typeof zlib.zstdDecompressSync !== "function") {
    throw new Error(
      `no zstd support in this Node (${process.version}): upgrade to Node >= 22.15 / 23.8, ` +
        `or read the occupancy from the harness UI instead`
    );
  }
  if (!Buffer.isBuffer(buffer)) throw new TypeError("zstd input must be a Buffer");
  const decodeError = (offset, detail) =>
    new Error(
      `cannot decode zstd session log ${fileLabel} at byte ${offset}: ${detail}. ` +
        `The log may be corrupted; read the occupancy from the harness UI instead`
    );
  const parts = [];
  let pos = 0;
  let frames = 0;
  let skippableFrames = 0;
  let incompleteTail = null;
  const need = (cursor, length, stage) => {
    if (buffer.length - cursor >= length) return false;
    incompleteTail = {
      offset: pos,
      stage,
      availableBytes: buffer.length - pos,
      missingBytes: length - (buffer.length - cursor),
    };
    return true;
  };

  while (pos < buffer.length) {
    // Only a prefix of a legal magic may be classified as a partial header.
    if (buffer.length - pos < 4) {
      const tail = buffer.subarray(pos);
      const standard = Buffer.from([0x28, 0xb5, 0x2f, 0xfd]);
      const skippable = Buffer.from([0x50, 0x2a, 0x4d, 0x18]);
      const isStandard = tail.equals(standard.subarray(0, tail.length));
      const isSkippable = tail[0] >= 0x50 && tail[0] <= 0x5f &&
        tail.subarray(1).equals(skippable.subarray(1, tail.length));
      if (!isStandard && !isSkippable) throw decodeError(pos, "invalid frame magic");
      need(pos, 4, "magic");
      break;
    }
    const magic = buffer.readUInt32LE(pos);
    if (magic >= ZSTD_SKIPPABLE_MAGIC && magic <= ZSTD_SKIPPABLE_MAGIC + 15) {
      if (need(pos + 4, 4, "skippable header")) break;
      const size = buffer.readUInt32LE(pos + 4);
      if (need(pos + 8, size, "skippable payload")) break;
      pos += 8 + size;
      skippableFrames++;
      continue;
    }
    if (magic !== ZSTD_MAGIC) throw decodeError(pos, "invalid frame magic");
    if (need(pos + 4, 1, "frame header descriptor")) break;
    const descriptor = buffer[pos + 4];
    // The reserved bit must be zero. The unused bit (0x10) is ignored by
    // decoders, as required by the standard, rather than treated as reserved.
    if (descriptor & 0x08) throw decodeError(pos + 4, "invalid reserved frame header bit");
    const singleSegment = Boolean(descriptor & 0x20);
    const checksum = Boolean(descriptor & 0x04);
    const dictionarySize = [0, 1, 2, 4][descriptor & 3];
    const contentSizeFlag = descriptor >>> 6;
    const contentSizeBytes = contentSizeFlag === 0 ? (singleSegment ? 1 : 0) : 2 ** contentSizeFlag;
    let cursor = pos + 5;
    let windowSize = 0;
    if (!singleSegment) {
      if (need(cursor, 1, "window descriptor")) break;
      const windowDescriptor = buffer[cursor++];
      const base = 2 ** (10 + (windowDescriptor >>> 3));
      windowSize = base + (base / 8) * (windowDescriptor & 7);
    }
    if (need(cursor, dictionarySize, "dictionary id")) break;
    cursor += dictionarySize;
    if (need(cursor, contentSizeBytes, "frame content size")) break;
    let contentSize = null;
    if (contentSizeBytes) {
      contentSize = contentSizeBytes === 8
        ? buffer.readBigUInt64LE(cursor)
        : BigInt(buffer.readUIntLE(cursor, contentSizeBytes));
      if (contentSizeBytes === 2) contentSize += 256n;
      if (singleSegment) windowSize = Number(contentSize);
    }
    cursor += contentSizeBytes;
    const maxBlockSize = Math.min(ZSTD_MAX_BLOCK_SIZE, windowSize);
    let lastBlock = false;
    while (!lastBlock) {
      // Block type is already knowable from the first header byte.
      if (cursor < buffer.length && ((buffer[cursor] >>> 1) & 3) === 3) {
        throw decodeError(cursor, "reserved block type");
      }
      if (need(cursor, 3, "block header")) break;
      const blockHeader = buffer.readUIntLE(cursor, 3);
      const blockType = (blockHeader >>> 1) & 3;
      const blockSize = blockHeader >>> 3;
      if (blockSize > maxBlockSize) throw decodeError(cursor, "block exceeds frame window or 128 KiB limit");
      lastBlock = Boolean(blockHeader & 1);
      cursor += 3;
      // An RLE block stores one byte, not blockSize bytes.
      if (need(cursor, blockType === 1 ? 1 : blockSize, "block payload")) break;
      cursor += blockType === 1 ? 1 : blockSize;
    }
    if (incompleteTail) break;
    if (checksum) {
      if (need(cursor, 4, "checksum")) break;
      cursor += 4;
    }
    let decoded;
    try {
      decoded = zlib.zstdDecompressSync(buffer.subarray(pos, cursor));
    } catch (error) {
      throw decodeError(pos, error.message);
    }
    if (contentSize !== null && BigInt(decoded.length) !== contentSize) {
      throw decodeError(pos, "decoded length does not match frame content size");
    }
    parts.push(decoded);
    frames++;
    pos = cursor;
  }
  return { data: Buffer.concat(parts), frames, skippableFrames, consumedBytes: pos, incompleteTail };
}

/** Legacy Buffer return contract; use decodeZstdDetailed for tail diagnostics. */
export function decodeZstd(buffer, fileLabel = "<buffer>") {
  return decodeZstdDetailed(buffer, fileLabel).data;
}

/** Decoded text lines; optional diagnostics distinguish incomplete live tails. */
export function zstdLines(filePath, diagnostics = null) {
  const result = decodeZstdDetailed(fs.readFileSync(filePath), filePath);
  if (diagnostics) {
    Object.assign(diagnostics, {
      frames: result.frames,
      skippableFrames: result.skippableFrames,
      consumedBytes: result.consumedBytes,
      incompleteTail: result.incompleteTail,
    });
  }
  return result.data.toString("utf8").split(/\r?\n/);
}

/** Return the last usage-looking object found in a session event stream. */
export function findUsage(lines) {
  let last = null;
  const scan = (obj) => {
    if (Array.isArray(obj)) {
      for (const item of obj) scan(item);
    } else if (obj && typeof obj === "object") {
      if ("inputTokens" in obj && "outputTokens" in obj) last = obj;
      for (const value of Object.values(obj)) scan(value);
    }
  };
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line) continue;
    try {
      scan(JSON.parse(line));
    } catch {
      continue;
    }
  }
  return last;
}

function toInt(value) {
  const n = Number(value);
  return Number.isFinite(n) ? Math.trunc(n) : 0;
}

export function usage({ cwd = process.cwd(), dshHome = process.env.DSH_HOME, sessionId = null, windowTokens = 1000000 } = {}) {
  if (!dshHome) throw new Error("DSH_HOME not set; source the harness env first");
  const sessionDir = findSessionDir(dshHome, cwd);
  if (!sessionDir) throw new Error(`no session directory found for cwd: ${cwd}`);
  const log = findSessionLog(sessionDir, sessionId);
  if (!log) throw new Error(`no session log found in ${sessionDir}`);
  const diagnostics = {};
  const record = findUsage(zstdLines(log, diagnostics));
  if (!record) {
    const tail = diagnostics.incompleteTail ? ` (incomplete zstd tail at byte ${diagnostics.incompleteTail.offset})` : "";
    throw new Error(`no usage record found in ${path.basename(log)}${tail}`);
  }
  const used = toInt(record.inputTokens) + toInt(record.cacheReadTokens);
  const window = toInt(windowTokens);
  return {
    session_id: path.basename(path.dirname(log)),
    log,
    input_tokens: record.inputTokens ?? null,
    cache_read_tokens: record.cacheReadTokens ?? null,
    output_tokens: record.outputTokens ?? null,
    context_used_est: used,
    window_tokens: window,
    pct: window ? Math.round((1000 * used) / window) / 10 : null,
    ...(diagnostics.incompleteTail ? { zstd_incomplete_tail: diagnostics.incompleteTail } : {}),
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

/**
 * Which process-enumeration backend this platform uses:
 * "procfs" (Linux /proc) | "ps" (macOS/BSD) | "powershell" (Windows) | null.
 * Shared with session.mjs so `--self-check` names the same backend the
 * session probe actually uses.
 */
export function detectProcessBackend() {
  if (process.platform === "linux" && isDir("/proc")) return "procfs";
  if (process.platform === "win32") return "powershell";
  if (process.platform === "darwin") return "ps";
  return "ps";
}

export function selfCheck() {
  let canonicalCwd = null;
  try {
    canonicalCwd = fs.realpathSync(process.cwd());
  } catch {
    canonicalCwd = path.resolve(process.cwd());
  }
  return {
    platform: process.platform,
    node: process.version,
    backend: detectProcessBackend(),
    zstd: typeof zlib.zstdDecompressSync === "function",
    dsh_home: process.env.DSH_HOME || null,
    canonical_cwd: canonicalCwd,
  };
}

const USAGE_HELP = `Read-only DSH context-usage probe (Node).

Usage:
  node context.mjs usage [--cwd DIR] [--session-id ID] [--window-tokens N]
  node context.mjs --self-check

Options:
  --cwd DIR            project directory of the session (default: $PWD)
  --session-id ID      pick the session whose directory name contains ID
  --window-tokens N    context window for pct (default: 1000000)
  --self-check         print platform/runtime diagnostics as JSON
  -h, --help           this message
`;

export function parseArgs(argv) {
  const args = { command: null, cwd: process.cwd(), sessionId: null, windowTokens: 1000000, selfCheck: false, help: false };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    const take = (name) => {
      if (i + 1 >= argv.length) throw new Error(`${name} needs a value`);
      return argv[++i];
    };
    if (arg === "usage" || arg === "identity" || arg === "idle") args.command = arg;
    else if (arg === "--cwd") args.cwd = take("--cwd");
    else if (arg === "--session-id") args.sessionId = take("--session-id");
    else if (arg === "--window-tokens") args.windowTokens = toInt(take("--window-tokens"));
    else if (arg === "--self-check") args.selfCheck = true;
    else if (arg === "-h" || arg === "--help") args.help = true;
    else throw new Error(`unknown argument: ${arg}`);
  }
  return args;
}

function main(argv = process.argv.slice(2)) {
  try {
    const args = parseArgs(argv);
    if (args.help) {
      process.stdout.write(USAGE_HELP);
      return;
    }
    if (args.selfCheck) {
      process.stdout.write(JSON.stringify(selfCheck()) + "\n");
      return;
    }
    if (args.command !== "usage") {
      process.stderr.write(USAGE_HELP);
      process.exitCode = 1;
      return;
    }
    const result = usage({ cwd: args.cwd, sessionId: args.sessionId, windowTokens: args.windowTokens });
    process.stdout.write(JSON.stringify(result) + "\n");
  } catch (error) {
    process.stderr.write(`${error.message}\n`);
    process.exitCode = 1;
  }
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  main();
}
