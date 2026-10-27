#!/usr/bin/env bash
# scripts/remote/ec2-defaults.sh — zero-config defaults for the EC2 runtime.
#
# Layered on top of ec2-lib.sh's resolve_* helpers, which remain the override
# layer: every function here returns the explicit knob verbatim (making no aws
# call at all) when it is set, and otherwise resolves or creates a
# leerie-tagged default. All JSON is parsed with python3; bash 3.2 portable.
#
# Functions print the resolved value on stdout; failures print an actionable
# message on stderr and return 1. Tagged resources carry leerie=managed.
# shellcheck disable=SC1091
. "$(dirname "${BASH_SOURCE[0]}")/ec2-lib.sh"

EC2_DEFAULT_INSTANCE_TYPE="m5.xlarge"
EC2_TAG_KEY="leerie"
EC2_TAG_VALUE="managed"
EC2_SG_NAME="leerie-ssh"
EC2_KEY_NAME_DEFAULT="leerie-ssh"
EC2_ROLE_NAME="leerie-ssm"
EC2_SSM_POLICY_ARN="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

# _ec2_aws <aws args...> — aws with region/profile appended.
_ec2_aws() {
  local _args=() _a
  while IFS= read -r _a; do _args+=("$_a"); done < <(_aws_region_profile_args)
  aws "$@" ${_args[@]+"${_args[@]}"}
}

_ec2_state_dir() {
  printf '%s' "${LEERIE_STATE_HOST_DIR:-${LEERIE_STATE_DIR:-$HOME/.leerie}}"
}

_ec2_tag_spec() { # <resource-type>
  printf 'ResourceType=%s,Tags=[{Key=%s,Value=%s}]' "$1" "$EC2_TAG_KEY" "$EC2_TAG_VALUE"
}

# --- default_instance_type --------------------------------------------------
default_instance_type() {
  local v
  v="$(resolve_instance_type)"
  printf '%s' "${v:-$EC2_DEFAULT_INSTANCE_TYPE}"
}

# _ec2_default_vpc_id — prints the default VPC id or fails naming the fix.
_ec2_default_vpc_id() {
  local out vpc
  out="$(_ec2_aws ec2 describe-vpcs --filters Name=isDefault,Values=true --output json 2>/dev/null)" || out=""
  vpc="$(printf '%s' "$out" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
for v in d.get("Vpcs",[]):
    if v.get("IsDefault",True):
        print(v["VpcId"]); break' 2>/dev/null)"
  if [ -z "$vpc" ]; then
    remote_log "error: no default VPC in this region, so leerie cannot pick a subnet."
    echo "  Run: aws ec2 create-default-vpc  (or set LEERIE_EC2_SUBNET_ID / --ec2-subnet-id)" >&2
    return 1
  fi
  printf '%s' "$vpc"
}

# --- default_subnet_id ------------------------------------------------------
default_subnet_id() {
  local v out
  v="$(resolve_subnet_id)"
  if [ -n "$v" ]; then printf '%s' "$v"; return 0; fi
  local vpc
  vpc="$(_ec2_default_vpc_id)" || return 1
  out="$(_ec2_aws ec2 describe-subnets --filters "Name=vpc-id,Values=$vpc" "Name=map-public-ip-on-launch,Values=true" --output json 2>/dev/null)" || out=""
  v="$(printf '%s' "$out" | python3 -c '
import json,sys
vpc=sys.argv[1]
try: d=json.load(sys.stdin)
except Exception: d={}
for s in sorted(d.get("Subnets",[]), key=lambda s:s.get("AvailabilityZone","")):
    if s.get("VpcId",vpc)==vpc and s.get("MapPublicIpOnLaunch",True):
        print(s["SubnetId"]); break' "$vpc" 2>/dev/null)"
  if [ -z "$v" ]; then
    remote_log "error: default VPC $vpc has no public subnet."
    echo "  Run: aws ec2 create-default-subnet --availability-zone <az>  (or set LEERIE_EC2_SUBNET_ID)" >&2
    return 1
  fi
  printf '%s' "$v"
}

# --- ec2_egress_cidr --------------------------------------------------------
# LEERIE_EC2_SSH_CIDR when set, else the launcher's public IPv4 as /32.
# Validated mechanically; never 0.0.0.0/0.
ec2_egress_cidr() {
  local cidr="${LEERIE_EC2_SSH_CIDR:-}" ip
  if [ -z "$cidr" ]; then
    ip="$(curl -fsS --max-time 10 https://checkip.amazonaws.com 2>/dev/null | tr -d '[:space:]')"
    cidr="${ip:+$ip/32}"
  fi
  if ! printf '%s' "$cidr" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}(/[0-9]{1,2})?$'; then
    remote_log "error: could not determine the launcher's egress IP for the SSH security-group rule."
    echo "  Set LEERIE_EC2_SSH_CIDR=<your-ip>/32 and re-run." >&2
    return 1
  fi
  case "$cidr" in */*) ;; *) cidr="$cidr/32" ;; esac
  if [ "$cidr" = "0.0.0.0/0" ]; then
    echo "  error: refusing to open 22/tcp to 0.0.0.0/0; set LEERIE_EC2_SSH_CIDR." >&2
    return 1
  fi
  printf '%s' "$cidr"
}

# --- ensure_security_group --------------------------------------------------
ensure_security_group() {
  local sg
  sg="$(resolve_security_group)"
  if [ -n "$sg" ]; then printf '%s' "$sg"; return 0; fi
  local cidr vpc out gid have
  cidr="$(ec2_egress_cidr)" || return 1
  vpc="$(_ec2_default_vpc_id)" || return 1
  out="$(_ec2_aws ec2 describe-security-groups --filters "Name=tag:$EC2_TAG_KEY,Values=$EC2_TAG_VALUE" "Name=group-name,Values=$EC2_SG_NAME" "Name=vpc-id,Values=$vpc" --output json 2>/dev/null)" || out=""
  gid="$(printf '%s' "$out" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
g=d.get("SecurityGroups",[])
print(g[0]["GroupId"] if g else "")' 2>/dev/null)"
  if [ -z "$gid" ]; then
    gid="$(_ec2_aws ec2 create-security-group --group-name "$EC2_SG_NAME" --description "leerie SSH from launcher" --vpc-id "$vpc" --tag-specifications "$(_ec2_tag_spec security-group)" --output json 2>/dev/null | python3 -c '
import json,sys
try: print(json.load(sys.stdin).get("GroupId",""))
except Exception: print("")' 2>/dev/null)"
    if [ -z "$gid" ]; then
      remote_log "error: could not create the leerie security group (needs ec2:CreateSecurityGroup)."
      echo "  Or set LEERIE_EC2_SECURITY_GROUP to an existing group id." >&2
      return 1
    fi
    have=""
  else
    have="$(printf '%s' "$out" | python3 -c '
import json,sys
d=json.load(sys.stdin)
for p in d["SecurityGroups"][0].get("IpPermissions",[]):
    if p.get("FromPort")==22:
        for r in p.get("IpRanges",[]): print(r.get("CidrIp",""))' 2>/dev/null)"
  fi
  if ! printf '%s\n' "$have" | grep -qxF "$cidr"; then
    local old
    while IFS= read -r old; do
      [ -n "$old" ] && _ec2_aws ec2 revoke-security-group-ingress --group-id "$gid" --protocol tcp --port 22 --cidr "$old" >/dev/null 2>&1
    done <<EOF
$have
EOF
    if ! _ec2_aws ec2 authorize-security-group-ingress --group-id "$gid" --protocol tcp --port 22 --cidr "$cidr" >/dev/null 2>&1; then
      remote_log "error: could not authorize 22/tcp from $cidr on $gid."
      return 1
    fi
  fi
  printf '%s' "$gid"
}

# --- ensure_key_pair --------------------------------------------------------
# Prints the key name; exports LEERIE_EC2_SSH_KEY_FILE when leerie owns the key
# (or when LEERIE_EC2_SSH_KEY_FILE was given alongside an override name).
# NOTE: call without $(...) if the export must reach the caller:
#   ensure_key_pair >/dev/null  # then read LEERIE_EC2_KEY_NAME
ensure_key_pair() {
  local name
  name="$(resolve_key_name)"
  if [ -n "$name" ]; then printf '%s' "$name"; return 0; fi
  local dir keyfile out found
  dir="$(_ec2_state_dir)"
  keyfile="$dir/$EC2_KEY_NAME_DEFAULT.pem"
  out="$(_ec2_aws ec2 describe-key-pairs --filters "Name=tag:$EC2_TAG_KEY,Values=$EC2_TAG_VALUE" "Name=key-name,Values=$EC2_KEY_NAME_DEFAULT" --output json 2>/dev/null)" || out=""
  found="$(printf '%s' "$out" | python3 -c '
import json,sys
try: d=json.load(sys.stdin)
except Exception: d={}
print(d["KeyPairs"][0]["KeyName"] if d.get("KeyPairs") else "")' 2>/dev/null)"
  if [ -n "$found" ]; then
    if [ ! -f "$keyfile" ]; then
      remote_log "error: key pair $found exists in AWS but its private key is not at $keyfile."
      echo "  Restore the file, or run: aws ec2 delete-key-pair --key-name $found  (leerie will recreate it)," >&2
      echo "  or set LEERIE_EC2_KEY_NAME to a key you hold." >&2
      return 1
    fi
  else
    mkdir -p "$dir" || return 1
    local material
    material="$(_ec2_aws ec2 create-key-pair --key-name "$EC2_KEY_NAME_DEFAULT" --key-type rsa --tag-specifications "$(_ec2_tag_spec key-pair)" --output json 2>/dev/null | python3 -c '
import json,sys
try: print(json.load(sys.stdin).get("KeyMaterial",""))
except Exception: print("")' 2>/dev/null)"
    if [ -z "$material" ]; then
      remote_log "error: could not create the leerie key pair (needs ec2:CreateKeyPair)."
      echo "  Or set LEERIE_EC2_KEY_NAME to an existing key pair." >&2
      return 1
    fi
    ( umask 077; printf '%s\n' "$material" > "$keyfile" ) || return 1
    chmod 600 "$keyfile"
    found="$EC2_KEY_NAME_DEFAULT"
  fi
  LEERIE_EC2_SSH_KEY_FILE="$keyfile"
  export LEERIE_EC2_SSH_KEY_FILE
  printf '%s' "$found"
}

# --- ensure_instance_profile ------------------------------------------------
# Prints the instance-profile name, or nothing (return 0) when IAM denies
# creation — SSM may still work via Default Host Management Configuration.
ensure_instance_profile() {
  local p="${LEERIE_EC2_INSTANCE_PROFILE:-}"
  if [ -n "$p" ]; then printf '%s' "$p"; return 0; fi
  p="$EC2_ROLE_NAME"
  if _ec2_aws iam get-instance-profile --instance-profile-name "$p" >/dev/null 2>&1; then
    printf '%s' "$p"; return 0
  fi
  local trust='{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"ec2.amazonaws.com"},"Action":"sts:AssumeRole"}]}'
  local tags="Key=$EC2_TAG_KEY,Value=$EC2_TAG_VALUE" err
  err="$(_ec2_aws iam create-role --role-name "$p" --assume-role-policy-document "$trust" --tags "$tags" 2>&1 >/dev/null)" || {
    if _ec2_iam_denied "$err"; then _ec2_iam_warn; return 0; fi
    case "$err" in *EntityAlreadyExists*) ;; *) remote_log "warning: iam create-role failed: $err"; return 0 ;; esac
  }
  _ec2_aws iam attach-role-policy --role-name "$p" --policy-arn "$EC2_SSM_POLICY_ARN" >/dev/null 2>&1 || { _ec2_iam_warn; return 0; }
  _ec2_aws iam create-instance-profile --instance-profile-name "$p" --tags "$tags" >/dev/null 2>&1 || { _ec2_iam_warn; return 0; }
  _ec2_aws iam add-role-to-instance-profile --instance-profile-name "$p" --role-name "$p" >/dev/null 2>&1 || { _ec2_iam_warn; return 0; }
  sleep "${LEERIE_EC2_IAM_PROPAGATION_S:-10}"
  printf '%s' "$p"
}

# _ec2_iam_denied <stderr> — AWS CLI v2 error shape is "An error occurred
# (AccessDenied) when calling ..."; match the code field, not the prose.
_ec2_iam_denied() {
  printf '%s' "$1" | grep -Eq 'An error occurred \((AccessDenied|AccessDeniedException|UnauthorizedOperation)\)'
}

_ec2_iam_warn() {
  remote_log "warning: could not create the leerie SSM instance profile (missing IAM permissions); continuing without one."
  echo "  SSM then needs Default Host Management Configuration, or set LEERIE_EC2_INSTANCE_PROFILE." >&2
}
