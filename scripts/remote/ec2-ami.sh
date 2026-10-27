#!/usr/bin/env bash
# scripts/remote/ec2-ami.sh — ensure_ami() (DESIGN §6 "Tag and rebake
# rule"). ec2_ami_lookup is the cache half: source hash + version tag, look
# up an already-baked AMI by tag (a miss prints nothing, returns 0).
# ensure_ami adds the bake half: on a miss, launch a stock Debian 13
# instance, run ec2-bake.sh over SSH, stop, create-image with the
# tags, wait available, and terminate the bake instance on every exit path.
#
# Usage:
#
#   source scripts/remote/ec2-ami.sh
#   ami="$(ec2_ami_lookup)"       # id on hit/override, empty on miss
#   ami="$(ensure_ami)"           # id on hit/override/fresh bake
#
# Environment variables consumed:
#
#   LEERIE_EC2_AMI         — explicit AMI override; returned verbatim with
#                            zero aws calls
#   LEERIE_AWS_REGION / AWS_REGION, LEERIE_AWS_PROFILE / AWS_PROFILE
#                          — passed to describe-images when set
#   LEERIE_EC2_BAKE_SSH_RETRIES — SSH reachability attempts (default 60, 5s apart)
#   LEERIE_EC2_BAKE_BASE_AMI    — stock AMI override (default: public SSM
#                            parameter /aws/service/debian/release/13/latest/amd64)
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

  local ami out
  out="$(aws ec2 describe-images ${aws_args[@]+"${aws_args[@]}"} \
    --owners self \
    --filters "Name=tag:$EC2_AMI_TAG_MANAGED,Values=true" \
              "Name=tag:$EC2_AMI_TAG_VERSION,Values=$version" \
              "Name=tag:$EC2_AMI_TAG_HASH,Values=$hash" \
              "Name=state,Values=available" \
    --output json)" || return 1
  ami="$(printf '%s' "$out" | python3 -c '
import json, sys
try: imgs = json.load(sys.stdin).get("Images", [])
except Exception: imgs = []
imgs = [i for i in imgs if i.get("State", "available") == "available"]
imgs.sort(key=lambda i: i.get("CreationDate", ""))
print(imgs[-1]["ImageId"] if imgs else "")')"
  if [ -z "$ami" ]; then
    remote_log "ami: cache miss (version=$version hash=${hash:0:12})"
    return 0
  fi
  remote_log "ami: cache hit $ami (version=$version hash=${hash:0:12})"
  printf '%s\n' "$ami"
}

EC2_AMI_BASE_SSM_PARAM="/aws/service/debian/release/13/latest/amd64"
_EC2_AMI_BAKE_IID=""
_EC2_AMI_BAKE_NONCE=""

_ec2_ami_aws() {
  local args=() region profile
  region="${LEERIE_AWS_REGION:-${AWS_REGION:-}}"
  profile="${LEERIE_AWS_PROFILE:-${AWS_PROFILE:-}}"
  [ -n "$region" ] && args+=(--region "$region")
  [ -n "$profile" ] && args+=(--profile "$profile")
  aws "$@" ${args[@]+"${args[@]}"}
}

# _ec2_ami_bake_cleanup — terminate the bake instance; idempotent (the id
# is cleared first so EXIT after INT does not terminate twice).
_ec2_ami_bake_cleanup() {
  local iid="$_EC2_AMI_BAKE_IID"
  if [ -z "$iid" ] && [ -n "$_EC2_AMI_BAKE_NONCE" ]; then
    # Interrupted while run-instances was in flight: the id never came
    # back, but the instance (if any) carries this bake's nonce tag.
    iid="$(_ec2_ami_aws ec2 describe-instances \
      --filters "Name=tag:leerie:bake,Values=$_EC2_AMI_BAKE_NONCE" \
                "Name=instance-state-name,Values=pending,running,stopping,stopped" \
      --output json 2>/dev/null | _ec2_ami_py '
for r in d.get("Reservations", []):
    for i in r.get("Instances", []):
        print(i["InstanceId"])' | tr '\n' ' ')"
    iid="${iid% }"
  fi
  _EC2_AMI_BAKE_NONCE=""
  [ -n "$iid" ] || return 0
  _EC2_AMI_BAKE_IID=""
  remote_log "ami: terminating bake instance $iid"
  # shellcheck disable=SC2086  # one or more ids from the nonce lookup
  _ec2_ami_aws ec2 terminate-instances --instance-ids $iid >/dev/null 2>&1 \
    || remote_log "ami: terminate of $iid attempted (may already be gone)"
}

# _ec2_ami_py <body> — python3 over JSON on stdin; body sees `d`.
_ec2_ami_py() {
  python3 -I -c "import json, sys
try: d = json.load(sys.stdin)
except Exception: d = {}
$1"
}

# _ec2_ami_instance_field <instance-id> <field> — State.Name / PublicIpAddress.
_ec2_ami_instance_field() {
  _ec2_ami_aws ec2 describe-instances --instance-ids "$1" --output json 2>/dev/null \
    | _ec2_ami_py "
try: i = d['Reservations'][0]['Instances'][0]
except (KeyError, IndexError): sys.exit(0)
print(i['State']['Name'] if '$2' == 'State' else i.get('$2', ''))"
}

_ec2_ami_wait_state() { # <instance-state> <instance-id>
  local want="$1" iid="$2" i st
  for i in $(seq 1 "${LEERIE_EC2_BAKE_WAIT_TRIES:-120}"); do
    st="$(_ec2_ami_instance_field "$iid" State || true)"
    [ "$st" = "$want" ] && return 0
    case "$st" in terminated|shutting-down) return 1 ;; esac
    sleep "${LEERIE_EC2_BAKE_POLL_S:-5}"
  done
  return 1
}

# _ec2_ami_bake <version> <hash> — prints the new AMI id. Body runs the
# bake with the teardown trap armed before the first wait loop.
_ec2_ami_bake() {
  local version="$1" hash="$2"
  # shellcheck disable=SC1091
  . "$_EC2_AMI_DIR/ec2-defaults.sh"

  local base_ami="${LEERIE_EC2_BAKE_BASE_AMI:-}"
  if [ -z "$base_ami" ]; then
    base_ami="$(_ec2_ami_aws ssm get-parameter --name "$EC2_AMI_BASE_SSM_PARAM" --output json \
      | _ec2_ami_py "print(d.get('Parameter', {}).get('Value', ''))")" || base_ami=""
    if [ -z "$base_ami" ]; then
      remote_log "error: could not resolve the stock Debian 13 AMI from $EC2_AMI_BASE_SSM_PARAM."
      echo "  Check ssm:GetParameter, or set LEERIE_EC2_BAKE_BASE_AMI (or LEERIE_EC2_AMI to skip the bake)." >&2
      return 1
    fi
  fi
  local itype subnet sg key
  itype="$(ec2_default_instance_type)" || return 1
  subnet="$(ec2_default_subnet_id)" || return 1
  sg="$(ec2_default_security_group)" || return 1
  key="$(ec2_default_key_name)" || return 1

  local prev_exit prev_int prev_term
  prev_exit="$(trap -p EXIT)"; prev_int="$(trap -p INT)"; prev_term="$(trap -p TERM)"
  trap '_ec2_ami_bake_cleanup' EXIT
  trap '_ec2_ami_bake_cleanup; exit 130' INT
  trap '_ec2_ami_bake_cleanup; exit 143' TERM

  local rc=0 image_id=""
  _ec2_ami_bake_steps "$base_ami" "$itype" "$subnet" "$sg" "$key" "$version" "$hash" || rc=$?
  image_id="$_EC2_AMI_BAKE_RESULT"
  _ec2_ami_bake_cleanup
  trap - EXIT INT TERM
  [ -n "$prev_exit" ] && eval "$prev_exit"
  [ -n "$prev_int" ] && eval "$prev_int"
  [ -n "$prev_term" ] && eval "$prev_term"
  [ "$rc" -eq 0 ] || return "$rc"
  printf '%s\n' "$image_id"
}

_EC2_AMI_BAKE_RESULT=""
_ec2_ami_bake_steps() {
  local base_ami="$1" itype="$2" subnet="$3" sg="$4" key="$5" version="$6" hash="$7"
  local iid host i
  _EC2_AMI_BAKE_RESULT=""
  # The nonce is set before run-instances so an interrupt that lands while
  # the call is in flight can still find and terminate the instance.
  _EC2_AMI_BAKE_NONCE="$(python3 -c 'import uuid; print(uuid.uuid4().hex)')"
  # --network-interfaces carries the public IP explicitly: a subnet
  # override need not auto-assign one, and the bake is driven over SSH.
  iid="$(_ec2_ami_aws ec2 run-instances --image-id "$base_ami" --instance-type "$itype" \
    --key-name "$key" --count 1 \
    --network-interfaces "AssociatePublicIpAddress=true,DeviceIndex=0,SubnetId=$subnet,Groups=$sg" \
    --block-device-mappings 'DeviceName=/dev/xvda,Ebs={VolumeSize=30,DeleteOnTermination=true}' \
    --tag-specifications "$(_ec2_tag_spec instance leerie:bake "$_EC2_AMI_BAKE_NONCE")" \
    --output json | _ec2_ami_py "print(d['Instances'][0]['InstanceId'])")" || return 1
  [ -n "$iid" ] || return 1
  _EC2_AMI_BAKE_IID="$iid"
  remote_log "ami: bake instance $iid (base $base_ami)"

  _ec2_ami_wait_state running "$iid" || { remote_log "ami: bake instance never ran"; return 1; }
  host="$(_ec2_ami_instance_field "$iid" PublicIpAddress)"
  [ -n "$host" ] || { remote_log "ami: bake instance has no public IP"; return 1; }

  # Host-key checking is off: the throwaway instance's key is new by
  # construction and never trusted afterwards.
  local ssh_opts=() _a
  while IFS= read -r _a; do ssh_opts+=("$_a"); done < <(ec2_ssh_identity_args)
  ssh_opts+=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null
             -o LogLevel=ERROR -o ConnectTimeout=10 -o BatchMode=yes)
  for i in $(seq 1 "${LEERIE_EC2_BAKE_SSH_RETRIES:-60}"); do
    ssh "${ssh_opts[@]}" "admin@$host" true 2>/dev/null && break
    [ "$i" -lt "${LEERIE_EC2_BAKE_SSH_RETRIES:-60}" ] || { remote_log "ami: bake instance unreachable over SSH"; return 1; }
    sleep "${LEERIE_EC2_BAKE_POLL_S:-5}"
  done

  local root; root="$(_ec2_ami_root)"
  tar -C "$root" --exclude=__pycache__ --exclude='*.pyc' --exclude=.git -cf - "${_EC2_AMI_HASH_PATHS[@]}" \
    | ssh "${ssh_opts[@]}" "admin@$host" 'rm -rf /tmp/leerie-bake && mkdir -p /tmp/leerie-bake && tar -C /tmp/leerie-bake -xf -' \
    || { remote_log "ami: shipping repo to bake instance failed"; return 1; }
  local region="${LEERIE_AWS_REGION:-${AWS_REGION:-}}"
  [ -n "$region" ] || region="$(aws configure get region ${LEERIE_AWS_PROFILE:+--profile "$LEERIE_AWS_PROFILE"} 2>/dev/null || true)"
  # shellcheck disable=SC2029  # $region is meant to expand host-side
  ssh "${ssh_opts[@]}" "admin@$host" \
      "sudo env LEERIE_BAKE_DIR=/tmp/leerie-bake LEERIE_BAKE_REGION=$region bash -s" \
      < "$_EC2_AMI_DIR/ec2-bake.sh" \
    || { remote_log "ami: bake payload failed"; return 1; }

  _ec2_ami_aws ec2 stop-instances --instance-ids "$iid" >/dev/null || return 1
  _ec2_ami_wait_state stopped "$iid" || return 1

  # Tags go on at creation so there is never an untagged-then-tagged
  # window; a failed bake never reaches here, and lookup requires
  # state=available, so a pending/failed image cannot read as a hit.
  local tags="{Key=$EC2_AMI_TAG_VERSION,Value=$version},{Key=$EC2_AMI_TAG_HASH,Value=$hash},{Key=$EC2_AMI_TAG_MANAGED,Value=true}"
  local image_id
  image_id="$(_ec2_ami_aws ec2 create-image --instance-id "$iid" \
    --name "leerie-$version-${hash:0:12}-$(date +%s)" \
    --tag-specifications "ResourceType=image,Tags=[$tags]" "ResourceType=snapshot,Tags=[$tags]" \
    --output json | _ec2_ami_py "print(d.get('ImageId', ''))")" || return 1
  [ -n "$image_id" ] || return 1
  if ! _ec2_ami_aws ec2 wait image-available --image-ids "$image_id"; then
    _ec2_ami_aws ec2 deregister-image --image-id "$image_id" >/dev/null 2>&1 || true
    return 1
  fi
  _EC2_AMI_BAKE_RESULT="$image_id"
}

# ensure_ami — print an AMI id: override, cache hit, or a fresh bake.
ensure_ami() {
  if [ -n "${LEERIE_EC2_AMI:-}" ]; then
    ec2_ami_lookup
    return
  fi
  local ami version hash
  ami="$(ec2_ami_lookup)" || return 1
  if [ -n "$ami" ]; then printf '%s\n' "$ami"; return 0; fi
  version="$(ec2_ami_version)" || return 1
  hash="$(ec2_ami_source_hash)" || return 1
  remote_log "ami: baking (version=$version hash=${hash:0:12})"
  _ec2_ami_bake "$version" "$hash"
}
