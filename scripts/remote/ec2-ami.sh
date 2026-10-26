#!/usr/bin/env bash
# scripts/remote/ec2-ami.sh — cache half of ensure_ami() (DESIGN §6 "Tag
# and rebake rule"): compute the source hash + version tag and look up an
# already-baked AMI by tag. The bake half lives elsewhere; a miss here
# prints nothing and returns 0 so the caller knows to bake.
#
# Usage:
#
#   source scripts/remote/ec2-ami.sh
#   ami="$(ec2_ami_lookup)"       # id on hit/override, empty on miss
#
# Environment variables consumed:
#
#   LEERIE_EC2_AMI         — explicit AMI override; returned verbatim with
#                            zero aws calls
#   LEERIE_AWS_REGION / AWS_REGION, LEERIE_AWS_PROFILE / AWS_PROFILE
#                          — passed to describe-images when set
#   LEERIE_EC2_AMI_ROOT    — repo root to hash (default: two dirs above
#                            this file)

set -euo pipefail

_EC2_AMI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v remote_log >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  . "$_EC2_AMI_DIR/_log.sh"
fi

# Tag keys — the bake half must write exactly these.
EC2_AMI_TAG_VERSION="leerie:version"
EC2_AMI_TAG_HASH="leerie:source-hash"
EC2_AMI_TAG_MANAGED="leerie:managed"

# Paths baked into the AMI, relative to the repo root. The bake script is
# hashed so a change to how the image is built also invalidates the cache.
_EC2_AMI_HASH_PATHS=(Dockerfile requirements.txt orchestrator scripts prompts .claude-plugin)

_ec2_ami_root() {
  printf '%s' "${LEERIE_EC2_AMI_ROOT:-$(cd "$_EC2_AMI_DIR/../.." && pwd)}"
}

# ec2_ami_version — leerie version from .claude-plugin/plugin.json.
ec2_ami_version() {
  python3 -I - "$(_ec2_ami_root)/.claude-plugin/plugin.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["version"])
PY
}

# ec2_ami_source_hash — deterministic sha256 over the baked-in files
# (sorted relative paths + contents) plus the leerie version. python3
# rather than sha256sum: macOS has none by default. Bytecode caches and
# .git are skipped so a prior test run doesn't perturb the hash.
ec2_ami_source_hash() {
  local root version
  root="$(_ec2_ami_root)"
  version="$(ec2_ami_version)" || return 1
  python3 -I - "$root" "$version" "${_EC2_AMI_HASH_PATHS[@]}" <<'PY'
import hashlib, os, sys
root, version, *paths = sys.argv[1:]
files = []
for p in paths:
    full = os.path.join(root, p)
    if os.path.isfile(full):
        files.append(p)
    for d, dirs, names in os.walk(full):
        dirs[:] = [x for x in dirs if x not in ("__pycache__", ".git")]
        for n in names:
            if not n.endswith(".pyc"):
                files.append(os.path.relpath(os.path.join(d, n), root))
h = hashlib.sha256()
h.update(b"version\0" + version.encode() + b"\0")
for rel in sorted(set(files)):
    with open(os.path.join(root, rel), "rb") as f:
        data = f.read()
    h.update(rel.encode() + b"\0" + str(len(data)).encode() + b"\0" + data)
print(h.hexdigest())
PY
}

# ec2_ami_lookup — print the id of an available, self-owned AMI whose
# tags match the current version + source hash; print nothing on a miss.
# Newest wins if several match.
ec2_ami_lookup() {
  if [ -n "${LEERIE_EC2_AMI:-}" ]; then
    remote_log "ami: using override LEERIE_EC2_AMI=$LEERIE_EC2_AMI (no lookup)"
    printf '%s\n' "$LEERIE_EC2_AMI"
    return 0
  fi
  local version hash region profile
  version="$(ec2_ami_version)" || return 1
  hash="$(ec2_ami_source_hash)" || return 1
  region="${LEERIE_AWS_REGION:-${AWS_REGION:-}}"
  profile="${LEERIE_AWS_PROFILE:-${AWS_PROFILE:-}}"
  local aws_args=()
  [ -n "$region" ] && aws_args+=(--region "$region")
  [ -n "$profile" ] && aws_args+=(--profile "$profile")

  local ami
  ami="$(aws ec2 describe-images ${aws_args[@]+"${aws_args[@]}"} \
    --owners self \
    --filters "Name=tag:$EC2_AMI_TAG_MANAGED,Values=true" \
              "Name=tag:$EC2_AMI_TAG_VERSION,Values=$version" \
              "Name=tag:$EC2_AMI_TAG_HASH,Values=$hash" \
              "Name=state,Values=available" \
    --query 'sort_by(Images,&CreationDate)[-1].ImageId' \
    --output text)" || return 1
  if [ -z "$ami" ] || [ "$ami" = "None" ]; then
    remote_log "ami: cache miss (version=$version hash=${hash:0:12})"
    return 0
  fi
  remote_log "ami: cache hit $ami (version=$version hash=${hash:0:12})"
  printf '%s\n' "$ami"
}
