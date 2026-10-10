#!/usr/bin/env bash
# Build dist-mcpb/bing-webmaster-ai-cli-mcp-<pyproject version>.mcpb.
# The only build entry point, locally and in CI. uv and the mcpb CLI run in
# Docker as the current user; nothing is installed on the host. version is
# read from pyproject.toml and is not stored in mcpb/manifest.json. Tool names
# and descriptions are copied from the staged server's plan-mode list, which is
# what the extension advertises when allow_writes is left at its default.
set -euo pipefail

cd "$(git rev-parse --show-toplevel)"

NODE_IMAGE=node:24.21.0-slim
UV_IMAGE=ghcr.io/astral-sh/uv:0.12.23-python3.14-trixie-slim
MCPB_CLI=@anthropic-ai/mcpb@2.1.2
OUT=dist-mcpb
NPM_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/promo-mcpb-npm"
UV_CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/bing-webmaster-mcpb-uv"
UID_GID="$(id -u):$(id -g)"
VERSION="$(python3 -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')"
OUT_FILE="$OUT/bing-webmaster-ai-cli-mcp-${VERSION}.mcpb"
LIMIT_BYTES=$((5 * 1024 * 1024))

if [ -z "$VERSION" ]; then
  echo "pyproject.toml version is empty" >&2
  exit 1
fi

rm -rf "$OUT"
mkdir -p "$OUT" "$NPM_CACHE" "$UV_CACHE"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
stage="$work/stage"
mkdir -p "$stage"

# Stage only the files the extension runs. Tests, git metadata, caches, env
# files and TypeScript sources are never copied in.
cp pyproject.toml README.md LICENSE "$stage/"
cp -a bing_webmaster_ai_cli_mcp "$stage/bing_webmaster_ai_cli_mcp"
cp mcpb/launch.py "$stage/launch.py"
find "$stage" -type d -name __pycache__ -print0 | xargs -0r rm -rf
find "$stage" -name '*.pyc' -delete

cat >"$stage/.mcpbignore" <<'EOF'
**/.git/**
**/__pycache__/**
**/*.pyc
**/.env
**/.env.*
**/.venv/**
**/tests/**
**/*.ts
**/*.tsx
**/*.mts
**/*.cts
tools-list.json
uv.lock
EOF

docker run --rm -u "$UID_GID" -e HOME=/tmp \
  -e UV_CACHE_DIR=/cache \
  -e UV_PROJECT_ENVIRONMENT=/tmp/bing-wm-venv \
  -e PYTHONDONTWRITEBYTECODE=1 \
  -e BING_WM_API_KEY=dummy \
  -e BING_WM_ALLOW_WRITES=false \
  -v "$UV_CACHE":/cache \
  -v "$stage":/ext \
  -w /ext \
  "$UV_IMAGE" \
  uv run --directory /ext python -c 'import json, pathlib
from bing_webmaster_ai_cli_mcp.mcp_server import tool_specs
tools = [{"name": spec.name, "description": spec.description} for spec in tool_specs(False).values()]
names = [item["name"] for item in tools]
if len(names) != len(set(names)) or "bing_plan_submit_url" not in names or "bing_submit_url" in names:
    raise SystemExit("plan-mode tool list is wrong")
pathlib.Path("/ext/tools-list.json").write_text(json.dumps(tools, ensure_ascii=False) + "\n", encoding="utf-8")
print("listed %d tools" % len(tools))'

python3 - "$VERSION" "$stage/tools-list.json" mcpb/manifest.json "$stage/manifest.json" <<'PY'
import json
import pathlib
import sys

version, tools_path, src, dest = sys.argv[1:]
manifest = json.loads(pathlib.Path(src).read_text(encoding="utf-8"))
if "version" in manifest:
    sys.exit("mcpb/manifest.json must not store version; the build stamps it from pyproject.toml")
tools = json.loads(pathlib.Path(tools_path).read_text(encoding="utf-8"))
if not tools:
    sys.exit("tool list is empty")
manifest["version"] = version
manifest["tools"] = [
    {"name": item["name"], "description": item.get("description") or ""} for item in tools
]
empty = [item["name"] for item in manifest["tools"] if not item["description"].strip()]
if empty:
    sys.exit("tools with an empty description: %s" % empty)
manifest.pop("tools_generated", None)
pathlib.Path(dest).write_text(
    json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
    encoding="utf-8",
)
PY

rm -f "$stage/tools-list.json" "$stage/uv.lock"
rm -rf "$stage/.venv"
find "$stage" -type d -name __pycache__ -print0 | xargs -0r rm -rf

docker run --rm -u "$UID_GID" -e HOME=/tmp -e npm_config_cache=/npm \
  -v "$NPM_CACHE":/npm \
  -v "$stage":/work \
  -v "$PWD/$OUT":/out \
  "$NODE_IMAGE" \
  npx -y "$MCPB_CLI" pack /work "/out/bing-webmaster-ai-cli-mcp-${VERSION}.mcpb"

python3 - "$OUT_FILE" "$LIMIT_BYTES" <<'PY'
import sys
import zipfile

path, limit = sys.argv[1], int(sys.argv[2])
with zipfile.ZipFile(path) as archive:
    names = archive.namelist()
bad = []
for name in names:
    base = name.rstrip("/").split("/")[-1]
    if (
        name.startswith(("tests/", ".git/"))
        or "/tests/" in name
        or "/.git/" in name
        or name.startswith("__pycache__/")
        or "/__pycache__/" in name
        or base.startswith(".env")
        or (name.endswith((".ts", ".tsx", ".mts", ".cts")) and not name.startswith("node_modules/"))
    ):
        bad.append(name)
for required in ("manifest.json", "pyproject.toml", "README.md", "LICENSE", "launch.py"):
    if required not in names:
        bad.append("missing " + required)
if not any(name.startswith("bing_webmaster_ai_cli_mcp/") for name in names):
    bad.append("missing bing_webmaster_ai_cli_mcp/")
if bad:
    sys.exit("bundle has forbidden or missing files:\n" + "\n".join(bad[:40]))
print("archive files: %d" % len(names))
PY

size="$(stat -c %s "$OUT_FILE")"
if [ "$size" -gt "$LIMIT_BYTES" ]; then
  echo "$OUT_FILE is $size bytes, limit $LIMIT_BYTES" >&2
  exit 1
fi
echo "built $OUT_FILE ($size bytes)"
