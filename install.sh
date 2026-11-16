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

# check_frontmatter_safe <file> — reject skill/agent frontmatter that strict
# YAML loaders (VS Code) would drop silently.
#
# Rules (frontmatter block only, first `description:` line):
#   * a value starting with " must be closed by an unescaped " — the escaped
#     form ("...\"") is not valid for our consumers either;
#   * a value starting with ' must match ^'([^']|'')*'$;
#   * an unquoted value may not contain ": " (space after the colon).
# awk uses [[:space:]] instead of \s so it behaves the same under gawk and
# BSD/macOS awk.
check_frontmatter_safe() { # <file>
    local file="$1"
    [ -f "$file" ] || return 0
    local desc
    desc="$(awk '
        NR == 1 { if ($0 == "---") in_fm = 1; next }
        in_fm && $0 == "---" { exit }
        in_fm && /^description:/ {
            sub(/^description:/, "")
            sub(/^[[:space:]]+/, "")
            print
            exit
        }
    ' "$file")"
    [ -n "$desc" ] || return 0

    local len first last
    len=${#desc}
    first="${desc:0:1}"
    last="${desc:$((len - 1)):1}"
    if [ "$first" = '"' ]; then
        { [ "$len" -ge 2 ] && [ "$last" = '"' ]; } \
            || die "unsafe frontmatter (unterminated double-quoted description): $file"
        # The closing quote must not be escaped: count trailing backslashes.
        local body="${desc%\"}" escapes=0
        while [ -n "$body" ] && [ "${body:$(( ${#body} - 1 )):1}" = '\' ]; do
            body="${body%?}"
            escapes=$((escapes + 1))
        done
        [ $((escapes % 2)) -eq 0 ] \
            || die "unsafe frontmatter (escaped closing quote): $file"
        return 0
    fi
    if [ "$first" = "'" ]; then
        { [ "$len" -ge 2 ] && [ "$last" = "'" ]; } \
            || die "unsafe frontmatter (unterminated single-quoted description): $file"
        local inner="${desc:1:$((len - 2))}"
        inner="${inner//\'\'/}"
        case "$inner" in
            *"'"*) die "unsafe frontmatter (bare quote inside single-quoted description): $file" ;;
        esac
        return 0
    fi
    case "$desc" in
        *": "*) die "unsafe frontmatter (unquoted description contains ': '): $file" ;;
    esac
    return 0
}

check_frontmatter_tree() { # <platform> <src-root>
    local platform="$1" src="$2" name file
    for name in $SKILL_NAMES; do
        check_frontmatter_safe "$src/$name/SKILL.md"
    done
    if [ "$platform" = "copilot" ]; then
        for file in "$SCRIPT_DIR/platforms/copilot/agents"/*.agent.md; do
            [ -f "$file" ] && check_frontmatter_safe "$file"
        done
    fi
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

# copy_tree_merge <src> <dst> — copy over the target, keeping files that exist
# only in the target (local additions). Plain `cp -R` never tries to restore
# directory ownership/mtime, so it also works when the target directory is owned
# by another user (tar would fail with "Cannot utime/chmod: Operation not
# permitted"). Local *.conf handling lives in merge_one so local edits survive.
copy_tree_merge() { # <src> <dst>
    cp -R "$1"/. "$2"/
}

# merge_one <skill-name> <target-skills-dir> <src-root> <backup-root>
# Non-destructive upgrade used for deployments:
#   * every source file overwrites its target counterpart;
#   * target-only files (local scripts, gclear_context*, .operations/, tests,
#     logs) are left untouched;
#   * existing local *.conf is restored over the shipped default;
#   * the existing skill directory is copied to <backup-root>/<skill>/ first.
merge_one() { # <skill-name> <target-skills-dir> <src-root> <backup-root>
    local name="$1" skills_dir="$2" src_root="$3" backup_root="$4"
    local src="$src_root/$name"
    [ -d "$src" ] || die "source skill missing: $src"
    local dest="$skills_dir/$name"
    local had_dir=0
    if [ -d "$dest" ]; then
        had_dir=1
        mkdir -p "$backup_root" || die "cannot create backup dir: $backup_root"
        cp -R "$dest" "$backup_root/$name" || die "backup failed for $name (nothing changed)"
    fi
    mkdir -p "$dest" || die "cannot create $dest"
    copy_tree_merge "$src" "$dest" || die "merge copy failed for $name"
    if [ "$had_dir" = 1 ]; then
        local conf base
        for conf in "$backup_root/$name"/*.conf; do
            [ -f "$conf" ] || continue
            base="$(basename "$conf")"
            cp "$conf" "$dest/$base" || die "cannot restore local conf $name/$base"
            say "  kept local conf: $name/$base"
        done
    fi
    say "  merged: $name"
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
    say "usage: install.sh [--platform claude-code|deepseek-harness|copilot] [--merge] <target-project-dir>"
    say "       install.sh [--platform <p>] --uninstall <target-project-dir>"
    say "platform defaults to claude-code when omitted."
    say "--merge upgrades in place: overwrite shipped files, keep local extras and *.conf,"
    say "        and back up the previous skill dirs under <skills_dir>/.parallax-skills-backup-<UTC>/."
}

main() {
    local mode="install" target="" merge=0
    while [ $# -gt 0 ]; do
        case "$1" in
            --platform)
                [ $# -ge 2 ] || { usage; exit 1; }
                PLATFORM="$2"; shift 2 ;;
            --platform=*)
                PLATFORM="${1#--platform=}"; shift ;;
            --merge)
                merge=1; shift ;;
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
    check_frontmatter_tree "$PLATFORM" "$src"
    mkdir -p "$skills_dir" || die "cannot create $skills_dir"
    local count backup_root=""
    if [ "$merge" = 1 ]; then
        backup_root="$skills_dir/.parallax-skills-backup-$(date -u +%Y%m%dT%H%M%SZ)"
        for name in $SKILL_NAMES; do merge_one "$name" "$skills_dir" "$src" "$backup_root"; done
    else
        for name in $SKILL_NAMES; do install_one "$name" "$skills_dir" "$src"; done
    fi
    count=$(printf '%s\n' $SKILL_NAMES | wc -l | tr -d ' ')
    if [ "$merge" = 1 ]; then
        say "Done. $count skills merged ($PLATFORM). Local extras kept; previous copies backed up under $backup_root/"
    else
        say "Done. $count skills installed ($PLATFORM). Local *.conf edits (if any) were preserved."
    fi
    install_extras "$PLATFORM" "$target"
    platform_hint "$PLATFORM" "$target"
}

main "$@"
