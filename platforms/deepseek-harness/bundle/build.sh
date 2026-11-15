#!/usr/bin/env bash
# Thin compatibility wrapper. Native Windows users can run: node build.mjs
set -eu

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd -P)"
exec node "$SCRIPT_DIR/build.mjs" "$@"
