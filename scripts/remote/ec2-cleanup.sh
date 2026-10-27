#!/usr/bin/env bash
# scripts/remote/ec2-cleanup.sh — list/delete leerie-tagged EC2 leftovers.
#
#   source scripts/remote/ec2-cleanup.sh
#   ec2_cleanup [--apply] [--keep-current] [--older-than DAYS]
#   ec2_cleanup_report          # kill path: list only, never deletes
#
# Dry-run unless --apply. A resource is eligible only if the aws call that
# described it returned a leerie tag (`leerie=managed` from ec2-defaults.sh or
# `leerie:managed=true` from ec2-ami.sh); the server-side tag-key filter is
# re-checked client-side in python3 before every mutating call, and snapshots
# are re-described by id before delete. Order: AMI deregister, then its
# snapshots. AMIs backing a live instance, and key pairs / SGs / the IAM role
# still in use, are skipped with a reason. --keep-current spares the AMI
# whose version+source-hash tags match this checkout.
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/ec2-defaults.sh"
. "$(dirname "${BASH_SOURCE[0]}")/ec2-ami.sh"

_EC2_CLEANUP_PY='
import json, sys
def tagged(tags):
    t = {x.get("Key"): x.get("Value") for x in (tags or [])}
    return t.get("leerie") == "managed" or t.get("leerie:managed") == "true"
'

# _ec2_cleanup_json <py-body> <json-on-stdin> — body sees `d`, prints rows.
_ec2_cleanup_py() {
  python3 -I -c "$_EC2_CLEANUP_PY
try: d = json.load(sys.stdin)
except Exception: d = {}
$1"
}

_ec2_cleanup_filters() {
  printf '%s\n' --filters "Name=tag-key,Values=leerie,leerie:managed"
}

# _ec2_cleanup_in_use_images / _in_use <field> — ids referenced by live instances.
_ec2_cleanup_live() { # <ImageId|KeyName>
  _ec2_aws ec2 describe-instances --filters "Name=instance-state-name,Values=pending,running,stopping,stopped" --output json 2>/dev/null \
    | _ec2_cleanup_py "
for r in d.get('Reservations', []):
    for i in r.get('Instances', []):
        print(i.get('$1', '') if '$1' != 'Profile' else i.get('IamInstanceProfile', {}).get('Arn', ''))"
}

ec2_cleanup() {
  local apply=false keep=false days=""
  while [ $# -gt 0 ]; do
    case "$1" in
      --apply) apply=true ;;
      --keep-current) keep=true ;;
      --older-than) days="${2:-}"; shift ;;
      *) remote_log "ec2_cleanup: unknown option '$1'"; return 2 ;;
    esac
    shift
  done
  local verb="would delete"; $apply && verb="deleting"
  local cur_ver="" cur_hash=""
  if $keep; then
    cur_ver="$(ec2_ami_version 2>/dev/null)" || cur_ver=""
    cur_hash="$(ec2_ami_source_hash 2>/dev/null)" || cur_hash=""
    [ -n "$cur_ver" ] && [ -n "$cur_hash" ] || { remote_log "ec2 cleanup: cannot compute current AMI version+hash; keeping all AMIs"; keep=all; }
  fi
  local live_imgs live_keys
  local live_profiles; live_profiles="$(_ec2_cleanup_live Profile)"
  live_imgs="$(_ec2_cleanup_live ImageId)"; live_keys="$(_ec2_cleanup_live KeyName)"

  # --- AMIs + snapshots ---
  local rows id snaps reason s
  rows="$(_ec2_aws ec2 describe-images --owners self --filters "Name=tag-key,Values=leerie,leerie:managed" --output json 2>/dev/null \
    | _ec2_cleanup_py "
import datetime
days = '$days'
now = datetime.datetime.now(datetime.timezone.utc)
for im in d.get('Images', []):
    if not tagged(im.get('Tags')): continue
    t = {x['Key']: x['Value'] for x in im.get('Tags', [])}
    age_ok = True
    if days:
        try:
            c = datetime.datetime.fromisoformat(im.get('CreationDate', '').replace('Z', '+00:00'))
            age_ok = (now - c).days >= int(days)
        except Exception: age_ok = False
    snaps = ','.join(b['Ebs']['SnapshotId'] for b in im.get('BlockDeviceMappings', []) if b.get('Ebs', {}).get('SnapshotId'))
    print('|'.join([im['ImageId'], t.get('leerie:version', ''), t.get('leerie:source-hash', ''), 'old' if age_ok else 'young', snaps]))")"
  local ver hash age
  while IFS='|' read -r id ver hash age snaps; do
    [ -n "$id" ] || continue
    reason=""
    if [ "$keep" = all ] || { [ "$keep" = true ] && [ "$ver" = "$cur_ver" ] && [ "$hash" = "$cur_hash" ]; }; then reason="current version+hash AMI kept"
    elif [ "$age" = young ]; then reason="newer than --older-than"
    elif printf '%s\n' "$live_imgs" | grep -qFx "$id"; then reason="in use by an instance"; fi
    if [ -n "$reason" ]; then echo "  skip AMI $id ($reason)"; continue; fi
    echo "  $verb AMI $id (snapshots: ${snaps:-none})"
    $apply || continue
    _ec2_aws ec2 deregister-image --image-id "$id" >/dev/null || { echo "  failed to deregister $id"; continue; }
    for s in ${snaps//,/ }; do
      _ec2_aws ec2 describe-snapshots --snapshot-ids "$s" --output json 2>/dev/null \
        | _ec2_cleanup_py "
sys.exit(0 if any(tagged(s.get('Tags')) for s in d.get('Snapshots', [])) else 1)" \
        && _ec2_aws ec2 delete-snapshot --snapshot-id "$s" >/dev/null
    done
  done <<<"$rows"

  # --- key pairs ---
  rows="$(_ec2_aws ec2 describe-key-pairs --filters "Name=tag-key,Values=leerie,leerie:managed" --output json 2>/dev/null \
    | _ec2_cleanup_py "
for k in d.get('KeyPairs', []):
    if tagged(k.get('Tags')): print(k['KeyName'])")"
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    if printf '%s\n' "$live_keys" | grep -qFx "$id"; then echo "  skip key pair $id (in use by an instance)"; continue; fi
    echo "  $verb key pair $id"
    $apply || continue
    _ec2_aws ec2 delete-key-pair --key-name "$id" >/dev/null && rm -f "$(_ec2_state_dir)/$id.pem"
  done <<<"$rows"

  # --- security groups ---
  rows="$(_ec2_aws ec2 describe-security-groups --filters "Name=tag-key,Values=leerie,leerie:managed" --output json 2>/dev/null \
    | _ec2_cleanup_py "
for g in d.get('SecurityGroups', []):
    if tagged(g.get('Tags')): print(g['GroupId'])")"
  while IFS= read -r id; do
    [ -n "$id" ] || continue
    if _ec2_aws ec2 describe-network-interfaces --filters "Name=group-id,Values=$id" --output json 2>/dev/null \
        | _ec2_cleanup_py "sys.exit(0 if d.get('NetworkInterfaces') else 1)"; then
      echo "  skip security group $id (still attached)"; continue
    fi
    echo "  $verb security group $id"
    $apply || continue
    _ec2_aws ec2 delete-security-group --group-id "$id" >/dev/null || echo "  failed to delete $id"
  done <<<"$rows"

  # --- IAM role + instance profile created by ensure_instance_profile ---
  local role="$EC2_ROLE_NAME"
  if printf '%s\n' "$live_profiles" | grep -q "/$role\$"; then
    echo "  skip IAM role/instance profile $role (in use by an instance)"
  elif _ec2_aws iam get-role --role-name "$role" --output json 2>/dev/null \
      | _ec2_cleanup_py "sys.exit(0 if tagged(d.get('Role', {}).get('Tags')) else 1)"; then
    echo "  $verb IAM role/instance profile $role"
    if $apply; then
      _ec2_aws iam remove-role-from-instance-profile --instance-profile-name "$role" --role-name "$role" >/dev/null 2>&1 || true
      _ec2_aws iam delete-instance-profile --instance-profile-name "$role" >/dev/null 2>&1 || true
      _ec2_aws iam detach-role-policy --role-name "$role" --policy-arn "$EC2_SSM_POLICY_ARN" >/dev/null 2>&1 || true
      _ec2_aws iam delete-role --role-name "$role" >/dev/null 2>&1 || echo "  failed to delete role $role"
    fi
  fi
}

# ec2_cleanup_report — dry-run listing keeping the current AMI; kill uses it
# so shared AMI/key/SG are reported, never deleted.
ec2_cleanup_report() { ec2_cleanup --keep-current; }
