#!/usr/bin/env bash
# Build the FreshBooks Timesheet Claude Desktop extension (.mcpb bundle).
#
# Assembles a self-contained tree (manifest.json + the package + a bundled
# pyproject.toml) and packs it with the mcpb CLI. The bundle is a "uv" server:
# uv resolves and installs the dependencies on the host at first launch, so
# nothing platform-specific is vendored — one .mcpb works on macOS/Windows/Linux
# (provided `uv` is installed on the host).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
BUILD="$HERE/build"
VERSION="$(grep -m1 '^version' "$REPO/pyproject.toml" | cut -d'"' -f2)"
OUT="$HERE/freshbooks-timesheet-${VERSION}.mcpb"

echo "==> Assembling bundle in $BUILD"
rm -rf "$BUILD"
mkdir -p "$BUILD/server"

# pyproject.toml is the single source of truth for the version: inject it into
# the packed manifest (both the version field and the version-stamped venv path,
# which is what forces a reinstall to run new code instead of a stale venv).
VERSION="$VERSION" python3 - "$HERE/manifest.json" "$BUILD/manifest.json" <<'PY'
import json, os, sys
src, dst = sys.argv[1], sys.argv[2]
m = json.load(open(src))
v = os.environ["VERSION"]
m["version"] = v
env = m["server"]["mcp_config"]["env"]
env["UV_PROJECT_ENVIRONMENT"] = f"${{HOME}}/.freshbooks-timesheet-mcp/venv-{v}"
json.dump(m, open(dst, "w"), indent=2)
print(f"   manifest version -> {v}")
PY
cp "$REPO/pyproject.toml"     "$BUILD/server/pyproject.toml"
cp "$REPO/README.md"          "$BUILD/server/README.md"   # pyproject's readme = ...
cp -R "$REPO/freshbooks_mcp"  "$BUILD/server/freshbooks_mcp"
[ -f "$HERE/icon.png" ] && cp "$HERE/icon.png" "$BUILD/icon.png" || true

# Drop caches so they don't bloat the bundle.
find "$BUILD/server" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$BUILD/server" -name '*.pyc' -delete 2>/dev/null || true

echo "==> Packing with mcpb"
npx -y @anthropic-ai/mcpb pack "$BUILD" "$OUT"

echo "==> Built $OUT"
