// Parallax DSH bundle plugin.
//
// Contributes the seven Parallax skills (qstart / qgo / bcheck / gclear /
// rcheck / xwrap / wish) to the DSH session skill registry via ctx.skills.register().
// The bundled skills/<name>/SKILL.md files are the single content source:
// this wrapper parses their frontmatter and registers each body verbatim,
// with a directory resourceBase so templates, conf files and scripts ship
// and resolve next to the skill body (the harness turns resourceBase into
// the "Base directory for this skill: <path>" sentence).
//
// Zero network access at load; zero machine-specific paths (everything is
// derived from this module's own URL).
//
// Cordis plugin contract: named exports `name`, `inject` and `apply`.
// Injection declares the skill/tool registries, session durability checkpoints,
// and calling-agent working directory. The handoff module uses only these
// native services and Node builtins, never private package imports or shells.

import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { registerHandoff } from "./handoff.js";

const SKILL_NAMES = ["qstart", "qgo", "bcheck", "gclear", "rcheck", "xwrap", "wish"];
const SKILLS_DIR = path.join(path.dirname(fileURLToPath(import.meta.url)), "..", "skills");

/**
 * Minimal frontmatter parser for the subset of YAML the skills use:
 * top-level `key: value` lines plus one level of `metadata:` nesting.
 * Unknown keys are preserved inside metadata when unambiguous, ignored
 * otherwise. Never throws on skill prose — worst case it yields fewer fields.
 */
function parseFrontmatter(text) {
  const lines = text.split(/\r?\n/);
  if (lines[0]?.trim() !== "---") return { fields: {}, body: text };
  let end = -1;
  for (let i = 1; i < lines.length; i++) {
    if (lines[i].trim() === "---") {
      end = i;
      break;
    }
  }
  if (end === -1) return { fields: {}, body: text };
  const fields = {};
  let nested = null;
  for (let i = 1; i < end; i++) {
    const line = lines[i];
    if (/^\s*#/.test(line) || line.trim() === "") continue;
    const child = line.match(/^  ([A-Za-z][A-Za-z0-9_-]*):\s+(.+)$/);
    if (child && nested === "metadata") {
      fields.metadata[child[1]] = child[2].replace(/^(["'])(.*)\1$/, "$2");
      continue;
    }
    const match = line.match(/^([A-Za-z][A-Za-z0-9_-]*):(?:\s+(.*))?$/);
    if (!match) continue;
    const [, key, inline] = match;
    nested = inline === undefined && key === "metadata" ? key : null;
    if (nested) fields.metadata = {};
    else if (inline !== undefined) fields[key] = inline.replace(/^(["'])(.*)\1$/, "$2");
  }
  return { fields, body: lines.slice(end + 1).join("\n").replace(/^\n/, "") };
}

function loadSkill(name) {
  const dir = path.join(SKILLS_DIR, name);
  const parsed = parseFrontmatter(readFileSync(path.join(dir, "SKILL.md"), "utf8"));
  const f = parsed.fields;
  const metadata = { ...f.metadata, ...(f.portable ? { portable: f.portable } : {}) };
  const skill = {
    name: String(f.name || name),
    description: String(f.description || ""),
    content: parsed.body,
    source: "bundled",
    resourceBase: { kind: "directory", path: dir },
  };
  if (f.whenToUse) skill.whenToUse = String(f.whenToUse);
  if (Object.keys(metadata).length > 0) skill.metadata = metadata;
  const invocation = {
    modelInvocable: f["disable-model-invocation"] !== "true",
    userInvocable: f["user-invocable"] !== "false",
  };
  if (!invocation.modelInvocable || !invocation.userInvocable) skill.invocation = invocation;
  return skill;
}

/** Cordis plugin name. */
const name = "parallax-dsh";
/** Native services used by skill registration and same-session handoff. */
const inject = ["skills", "tools", "sessions", "workingDirectory"];

/** Register all seven skills; returns a composite disposer. */
function apply(ctx) {
  const disposers = [registerHandoff(ctx)];
  for (const skillName of SKILL_NAMES) {
    const skill = loadSkill(skillName);
    if (!skill.description) {
      ctx.logger?.warn?.(`parallax-dsh: skill "${skillName}" has no description, skipped`);
      continue;
    }
    disposers.push(ctx.skills.register(skill));
  }
  return () => {
    for (const dispose of disposers.reverse()) dispose();
  };
}

export { apply, inject, name };
