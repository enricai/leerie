#!/bin/sh
# Writes mise's system configuration, /etc/mise/config.toml: every tool mise
# ships (a `core:` backend in `mise registry`) has its idiomatic version files
# read — .ruby-version, .nvmrc, .python-version, rust-toolchain.toml,
# .go-version, .java-version, .bun-version, global.json, .zig-version, ... —
# so the runtime a repository pins is the one mise discovers from its own
# files, and no list of leerie's decides which (DESIGN §6½ *A repository's
# runtimes are the ones mise discovers*). Idiomatic files are opt-in per tool
# in current mise; a tool left out is silently ignored. Run as root at image
# build (Dockerfile) and instance bake (scripts/remote/ec2-bake.sh). $1: the
# file to write (default /etc/mise/config.toml, mise's system configuration).
set -eu
out="${1:-/etc/mise/config.toml}"
tools="$(mise registry | awk '$2 ~ /^core:/ { printf "%s\"%s\"", sep, $1; sep = ", " }')"
case "$tools" in
  *'"node"'*) ;;
  *) echo "mise-system-config: mise registry lists no core tools: '$tools'" >&2; exit 1 ;;
esac
mkdir -p "$(dirname "$out")"
printf '[settings]\nidiomatic_version_file_enable_tools = [%s]\n' "$tools" > "$out"
