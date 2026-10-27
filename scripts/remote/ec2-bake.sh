#!/usr/bin/env bash
# scripts/remote/ec2-bake.sh — host-side helper for ensure_ami: emits the
# instance-side bake script (ec2-bake-provision.sh) for transport to the bake
# instance. Sourced, not executed.

# ec2_bake_provision_path — absolute path of the instance-side script.
ec2_bake_provision_path() {
  printf '%s/ec2-bake-provision.sh\n' "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
}

# ec2_bake_emit — write the instance-side script to stdout.
ec2_bake_emit() {
  cat "$(ec2_bake_provision_path)"
}
