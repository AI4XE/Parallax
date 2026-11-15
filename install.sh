#!/usr/bin/env bash
# install.sh — Parallax skill suite installer (multi-platform).
#
# Usage:
#   ./install.sh [--platform claude-code|deepseek-harness|copilot] <target-project-dir>
#   ./install.sh [--platform <p>] --uninstall <target-project-dir>
#
# Platforms:
#   claude-code     (default) install into <target>/.claude/skills/
#   deepseek-harness          install into <target>/.agents/skills/
#   copilot                   install into <target>/.github/skills/ (+ agents +
#                             instructions extras into <target>/.github/)
#
# Per-skill behavior (both platforms):
#   1. Assemble a complete copy in <target-skills-dir>/.staging-<name>-$$/
#   2. Back up any *.conf in the existing skill directory
#   3. mv the old directory to .trash-<name>-$$ (same-filesystem rename)
#   4. mv the staging directory into place
#   5. Restore the backed-up conf files (local edits preserved)
#   6. Remove the trash
#   On any failure the previous directory is restored from the trash.
#
# This installer is a convenience layer (atomic swap + conf preservation).
# It is NOT a functional dependency: manually copying the skill folders into
# the platform's skills directory works identically — the shipped conf
# defaults take effect as-is. Manual re-copying overwrites local conf edits;
# prefer this installer for upgrades.

set -u

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd -P)"
PLATFORM="claude-code"
# Empty means "platform default"; resolved after --platform is parsed so the
# DeepSeek Harness tree gets its extra skill and Claude Code never does.
SKILL_NAMES="${PARALLAX_SKILLS:-}"

say() { printf '%s\n' "$*"; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }

skills_root() { # <platform> — source skills dir inside this repo
    case "$1" in
        claude-code)      echo "$SCRIPT_DIR/platforms/claude-code/skills" ;;
        deepseek-harness) echo "$SCRIPT_DIR/platforms/deepseek-harness/skills" ;;
        copilot)          echo "$SCRIPT_DIR/platforms/copilot/skills" ;;
        *) return 1 ;;
    esac
}

default_skills() { # <platform> — skill list installed when PARALLAX_SKILLS is unset
    case "$1" in
        claude-code)      echo "qstart qgo bcheck gclear rcheck xwrap wish" ;;
        deepseek-harness) echo "qstart qgo bcheck gclear rcheck xwrap wish" ;;
        copilot)          echo "qstart qgo bcheck gclear rcheck xwrap wish" ;;
        *) return 1 ;;
    esac
}

target_skills_dir() { # <platform> <target-project-dir>
    case "$1" in
        claude-code)      echo "$2/.claude/skills" ;;
        deepseek-harness) echo "$2/.agents/skills" ;;
        copilot)          echo "$2/.github/skills" ;;
    esac
}

# install_extras <platform> <target> — platform-specific extras beyond skills.
# Copilot also needs the reviewer agent and the global instructions file.
# Never overwrites an existing copilot-instructions.md (prints a merge hint).
install_extras() { # <platform> <target>
    case "$1" in
        copilot)
            local gh="$2/.github"
            local src_agents="$SCRIPT_DIR/platforms/copilot/agents"
            local src_instr="$SCRIPT_DIR/platforms/copilot/instructions/copilot-instructions.md"
            if [ -d "$src_agents" ]; then
                mkdir -p "$gh/agents"
                copy_tree "$src_agents" "$gh/agents"
                say "  installed: agents -> $gh/agents/"
            fi
            if [ -f "$src_instr" ]; then
                if [ -f "$gh/copilot-instructions.md" ]; then
                    say "  NOTE: $gh/copilot-instructions.md already exists — NOT overwritten."
                    say "        Merge the Parallax block from $src_instr manually."
                else
                    cp "$src_instr" "$gh/copilot-instructions.md"
                    say "  installed: copilot-instructions.md -> $gh/"
                fi
            fi
            ;;
        *) ;;
    esac
}

# uninstall_extras <platform> <target> — inverse of install_extras (conservative:
# only removes files the installer shipped; never touches a merged instructions file).
uninstall_extras() { # <platform> <target>
    case "$1" in
        copilot)
            local gh="$2/.github"
            if [ -d "$gh/agents" ]; then
                local agent
                for agent in "$SCRIPT_DIR/platforms/copilot/agents"/*.agent.md; do
                    [ -f "$gh/agents/$(basename "$agent")" ] && rm -f "$gh/agents/$(basename "$agent")"
                done
                say "  removed: agents shipped by this pack (from $gh/agents/)"
            fi
            [ -f "$gh/copilot-instructions.md" ] && say "  kept: $gh/copilot-instructions.md (may contain merged local content — remove manually if unwanted)"
            ;;
        *) ;;
    esac
}

platform_hint() { # <platform> <target>
    case "$1" in
        claude-code)      say "Open Claude Code in $2 and say: qstart" ;;
        deepseek-harness) say "Open a DSH session with $2 as the working directory and say: qstart" ;;
        copilot)          say "Open the folder $2 in VS Code, run 'Developer: Reload Window' once, trust the workspace if asked, then say: qstart" ;;
    esac
}

copy_tree() { # <src> <dst> — portable across GNU/Linux and macOS
    (cd "$1" && tar cf - .) | (cd "$2" && tar xf -)
}

install_one() { # <skill-name> <target-skills-dir> <src-root>
    local name="$1" skills_dir="$2" src_root="$3"
    local src="$src_root/$name"
    [ -d "$src" ] || die "source skill missing: $src"
    local staging="$skills_dir/.staging-$name-$$"
    local trash="$skills_dir/.trash-$name-$$"
    local conf_backup="$skills_dir/.conf-backup-$name-$$"
    rm -rf "$staging" "$trash" "$conf_backup"
    mkdir -p "$staging" || die "cannot create staging dir: $staging"
    copy_tree "$src" "$staging" || { rm -rf "$staging"; die "copy failed for $name"; }

    local had_old=0
    if [ -d "$skills_dir/$name" ]; then
        had_old=1
        mkdir -p "$conf_backup"
        for conf in "$skills_dir/$name"/*.conf; do
            [ -f "$conf" ] && cp "$conf" "$conf_backup/"
        done
        mv "$skills_dir/$name" "$trash" || { rm -rf "$staging" "$conf_backup"; die "cannot move old $name aside"; }
    fi
    if mv "$staging" "$skills_dir/$name"; then
        if [ -d "$conf_backup" ]; then
            for conf in "$conf_backup"/*.conf; do
                [ -f "$conf" ] && cp "$conf" "$skills_dir/$name/"
            done
            rm -rf "$conf_backup"
        fi
        rm -rf "$trash"
        say "  installed: $name"
    else
        if [ "$had_old" = 1 ]; then
            mv "$trash" "$skills_dir/$name" || die "ROLLBACK FAILED for $name; previous copy preserved at $trash"
        fi
        rm -rf "$staging" "$conf_backup"
        die "install failed for $name (previous state restored)"
    fi
}

uninstall_one() { # <skill-name> <target-skills-dir> <conf-keep-dir>
    local name="$1" skills_dir="$2" keep="$3"
    local dir="$skills_dir/$name"
    [ -d "$dir" ] || { say "  not present, skipped: $name"; return 0; }
    mkdir -p "$keep/$name"
    for conf in "$dir"/*.conf; do
        [ -f "$conf" ] && cp "$conf" "$keep/$name/"
    done
    rm -rf "$dir"
    say "  removed: $name (conf preserved under $keep/$name)"
}

usage() {
    say "usage: install.sh [--platform claude-code|deepseek-harness|copilot] <target-project-dir>"
    say "       install.sh [--platform <p>] --uninstall <target-project-dir>"
    say "platform defaults to claude-code when omitted."
}

main() {
    local mode="install" target=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --platform)
                [ $# -ge 2 ] || { usage; exit 1; }
                PLATFORM="$2"; shift 2 ;;
            --platform=*)
                PLATFORM="${1#--platform=}"; shift ;;
            --uninstall)
                mode="uninstall"; shift ;;
            -h|--help)
                usage; exit 0 ;;
            -*)
                usage; exit 1 ;;
            *)
                target="$1"; shift ;;
        esac
    done

    local src
    src="$(skills_root "$PLATFORM")" || { usage; die "unknown platform: $PLATFORM"; }
    if [ -z "$SKILL_NAMES" ]; then
        SKILL_NAMES="$(default_skills "$PLATFORM")" || die "no default skill list for platform: $PLATFORM"
    fi
    [ -d "$src" ] || die "skills for platform '$PLATFORM' not found next to install.sh ($src)"
    [ -n "$target" ] || { usage; die "missing target project directory"; }
    [ -d "$target" ] || die "target is not a directory: $target"

    local skills_dir
    skills_dir="$(target_skills_dir "$PLATFORM" "$target")"
    if [ "$mode" = "uninstall" ]; then
        local keep="$skills_dir/.parallax-removed-conf"
        say "Uninstalling Parallax skills ($PLATFORM) from: $target"
        mkdir -p "$keep"
        for name in $SKILL_NAMES; do uninstall_one "$name" "$skills_dir" "$keep"; done
        uninstall_extras "$PLATFORM" "$target"
        say "Done. Local conf files were preserved under $keep/"
        return 0
    fi

    say "Installing Parallax skills ($PLATFORM) into: $skills_dir"
    mkdir -p "$skills_dir" || die "cannot create $skills_dir"
    for name in $SKILL_NAMES; do install_one "$name" "$skills_dir" "$src"; done
    local count
    count=$(printf '%s\n' $SKILL_NAMES | wc -l | tr -d ' ')
    say "Done. $count skills installed ($PLATFORM). Local *.conf edits (if any) were preserved."
    install_extras "$PLATFORM" "$target"
    platform_hint "$PLATFORM" "$target"
}

main "$@"
