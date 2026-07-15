"""Tests for the FLY_VM_DISK_GB opt-in volume in provision.sh.

The volume is the operational lever the user pulls when a run hits
ENOSPC on Fly's default ephemeral disk. These tests assert two
things via flyctl-stub interception:

  (a) UNSET: `flyctl machine run` is called WITHOUT --volume, and
      `flyctl volumes create` is NEVER called. The invocation must be
      byte-for-byte identical to today's behavior.

  (b) SET: `flyctl volumes create ... --size N` runs first, the volume
      ID is captured, and `flyctl machine run --volume <id>:/work`
      runs after. volume_id ends up in the persisted run.json.

The stub flyctl records every invocation into a log file so the test
can replay-assert the exact argv sequence.
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "provision.sh"


def _run_bash(script: str, env: dict | None = None,
              cwd: Path | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
    )


def _make_recording_flyctl(tmp_path: Path) -> tuple[Path, Path]:
    """Stub flyctl that records every invocation and emits realistic
    output so provision.sh can parse machine/volume IDs.

    Returns: (stub_path, invocation_log_path). The log is one line per
    call, with arguments joined by '|' for unambiguous parsing.
    """
    log_path = tmp_path / "flyctl-calls.log"
    log_path.write_text("")
    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'LOG="{log_path}"\n'
        '# Record the argv with | as separator (no | in any of our args).\n'
        'argv=""\n'
        'for a in "$@"; do\n'
        '  if [ -z "$argv" ]; then argv="$a"; else argv="$argv|$a"; fi\n'
        'done\n'
        'echo "$argv" >> "$LOG"\n'
        # Dispatch by subcommand.
        'case "$1" in\n'
        '  auth)\n'
        '    case "${2:-}" in\n'
        '      status) exit 0 ;;\n'
        '      *)      exit 0 ;;\n'
        '    esac\n'
        '    ;;\n'
        '  volumes|volume)\n'
        '    case "${2:-}" in\n'
        '      create)\n'
        '        # Emit a realistic create output the provision.sh\n'
        '        # parser can read.\n'
        '        cat <<EOF\n'
        '                  ID: vol_abcdef0123456789\n'
        '                Name: test_volume_name\n'
        '                 App: leerie\n'
        '              Region: iad\n'
        '                Size: 30 GB\n'
        'EOF\n'
        '        exit 0\n'
        '        ;;\n'
        '      destroy) exit 0 ;;\n'
        '    esac\n'
        '    ;;\n'
        '  machine)\n'
        '    case "${2:-}" in\n'
        '      run)\n'
        '        # Output a realistic Machine ID line.\n'
        '        echo "  Machine ID: machine-deadbeef1234"\n'
        '        echo "  State: started"\n'
        '        exit 0\n'
        '        ;;\n'
        '      list)\n'
        '        # Shape measured against a live Fly machine: config.mounts[]\n'
        '        # carries the volume, and keeps carrying it while stopped.\n'
        '        # $FLY_STUB_MOUNT_VOLUME lets a test emit a machine with no\n'
        '        # mounts (the no-volume case).\n'
        '        _mid="${FLY_STUB_MACHINE_ID:-machine-deadbeef1234}"\n'
        '        if [ -n "${FLY_STUB_MOUNT_VOLUME:-}" ]; then\n'
        '          printf \'[{"id":"%s","state":"stopped","config":{"mounts":[{"volume":"%s","name":"leerie_data_test","path":"/work","size_gb":1,"encrypted":true}]}}]\\n\' "$_mid" "$FLY_STUB_MOUNT_VOLUME"\n'
        '        else\n'
        '          printf \'[{"id":"%s","state":"stopped","config":{"mounts":[]}}]\\n\' "$_mid"\n'
        '        fi\n'
        '        exit 0\n'
        '        ;;\n'
        '      status)\n'
        '        echo "started"\n'
        '        exit 0\n'
        '        ;;\n'
        '      stop|destroy) exit 0 ;;\n'
        '    esac\n'
        '    ;;\n'
        'esac\n'
        'exit 0\n'
    )
    stub.chmod(0o755)
    return stub, log_path


def _read_calls(log_path: Path) -> list[list[str]]:
    """Read the recorded calls and return them as a list of argv lists."""
    if not log_path.exists():
        return []
    return [
        line.split("|")
        for line in log_path.read_text().splitlines()
        if line.strip()
    ]


def _make_run_dir(tmp_path: Path, run_id: str) -> Path:
    """Create a minimal host-side run dir with run.json that
    provision.sh's `update_run_json` can write into."""
    run_dir = tmp_path / "user-repo" / ".leerie" / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text("{}")
    return run_dir


def test_no_volume_when_disk_gb_unset(tmp_path):
    """The byte-for-byte-today guarantee: with FLY_VM_DISK_GB unset,
    no `flyctl volumes create` happens and no `--volume` arg is passed
    to `flyctl machine run`."""
    stub, log = _make_recording_flyctl(tmp_path)
    run_id = "feat-test-001"
    run_dir = _make_run_dir(tmp_path, run_id)
    user_repo = tmp_path / "user-repo"
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
        "LEERIE_FLY_APP": "leerie",
        "FLY_APP": "leerie",
        "USER_REPO": str(user_repo),
        "LEERIE_RUN_ID": run_id,
        # Skip the wait-for-started polling loop.
        "LEERIE_MACHINE_START_TIMEOUT": "1",
    }
    # Stub wait_for_started to a no-op so the test doesn't actually
    # poll. We do this by overriding the function after sourcing.
    result = _run_bash(
        f"source {PROVISION_SH}\n"
        "wait_for_started() { return 0; }\n"
        "provision_machine\n",
        env=env,
    )
    calls = _read_calls(log)
    # Filter out auth status (called by require_flyctl).
    non_auth = [c for c in calls if c[0] != "auth"]
    # Sanity check: we should see at least one machine-run call.
    machine_runs = [c for c in non_auth if c[:2] == ["machine", "run"]]
    assert machine_runs, f"expected machine run call; calls={calls}"
    # No volume create should have happened.
    volume_creates = [c for c in non_auth if c[:2] == ["volumes", "create"]]
    assert not volume_creates, (
        f"FLY_VM_DISK_GB unset must NOT trigger volume create.\n"
        f"calls={calls}"
    )
    # No --volume arg on the machine-run call.
    for mr in machine_runs:
        assert "--volume" not in mr, (
            f"FLY_VM_DISK_GB unset must NOT pass --volume to machine run.\n"
            f"argv={mr}"
        )
    # And the persisted run.json should not have volume_id.
    sidecar = json.loads((run_dir / "run.json").read_text())
    assert "volume_id" not in sidecar, sidecar


def test_volume_created_when_disk_gb_set(tmp_path):
    """With FLY_VM_DISK_GB=30, volume-create runs first with --size 30,
    machine-run gets --volume "<id>:/work", and run.json
    captures the volume_id."""
    stub, log = _make_recording_flyctl(tmp_path)
    run_id = "feat-test-002"
    run_dir = _make_run_dir(tmp_path, run_id)
    user_repo = tmp_path / "user-repo"
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
        "LEERIE_FLY_APP": "leerie",
        "FLY_APP": "leerie",
        "USER_REPO": str(user_repo),
        "LEERIE_RUN_ID": run_id,
        "FLY_VM_DISK_GB": "30",
        "LEERIE_MACHINE_START_TIMEOUT": "1",
    }
    result = _run_bash(
        f"source {PROVISION_SH}\n"
        "wait_for_started() { return 0; }\n"
        "provision_machine\n",
        env=env,
    )
    assert result.returncode == 0, (
        f"provision_machine should succeed; stderr={result.stderr}"
    )
    calls = _read_calls(log)
    # Ignore auth.
    non_auth = [c for c in calls if c[0] != "auth"]
    # First non-auth call: volumes create with --size 30.
    volume_creates = [c for c in non_auth if c[:2] == ["volumes", "create"]]
    assert len(volume_creates) == 1, (
        f"expected one volumes create; calls={calls}"
    )
    vc = volume_creates[0]
    assert "--size" in vc and vc[vc.index("--size") + 1] == "30", vc
    assert "--app" in vc and vc[vc.index("--app") + 1] == "leerie", vc
    # The volume name should be the leerie_data_<6hex> shape.
    # The volume name is the first positional after `volumes create`.
    vol_name = vc[2]
    assert vol_name.startswith("leerie_data_"), vol_name
    # The machine-run call should include --volume with the parsed ID.
    machine_runs = [c for c in non_auth if c[:2] == ["machine", "run"]]
    assert len(machine_runs) == 1, calls
    mr = machine_runs[0]
    assert "--volume" in mr, mr
    vol_arg = mr[mr.index("--volume") + 1]
    assert vol_arg == "vol_abcdef0123456789:/work", vol_arg
    # Ordering: volumes-create must precede machine-run.
    vc_idx = non_auth.index(vc)
    mr_idx = non_auth.index(mr)
    assert vc_idx < mr_idx, (
        f"volumes create must come before machine run; "
        f"vc_idx={vc_idx} mr_idx={mr_idx}"
    )
    # And the persisted run.json should carry volume_id.
    sidecar = json.loads((run_dir / "run.json").read_text())
    assert sidecar.get("volume_id") == "vol_abcdef0123456789", sidecar
    assert sidecar.get("fly_machine_id") == "machine-deadbeef1234", sidecar


def test_destroy_machine_destroys_volume_when_set(tmp_path):
    """destroy_machine should destroy the volume after the machine when
    LEERIE_VOLUME_ID is set."""
    stub, log = _make_recording_flyctl(tmp_path)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "LEERIE_FLY_APP": "leerie",
        "FLY_APP": "leerie",
    }
    result = _run_bash(
        f"source {PROVISION_SH}\n"
        'LEERIE_MACHINE_ID="machine-foo"\n'
        'LEERIE_VOLUME_ID="vol_bar"\n'
        "destroy_machine\n",
        env=env,
    )
    calls = _read_calls(log)
    # Expect: machine destroy, volumes destroy.
    machine_destroys = [c for c in calls if c[:2] == ["machine", "destroy"]]
    volume_destroys = [c for c in calls if c[:2] == ["volumes", "destroy"]]
    assert machine_destroys, calls
    assert volume_destroys, calls
    # Ordering: machine destroy must precede volume destroy. (Fly
    # refuses to destroy a volume still attached to a live machine.)
    md_idx = calls.index(machine_destroys[0])
    vd_idx = calls.index(volume_destroys[0])
    assert md_idx < vd_idx, (
        f"machine destroy must precede volume destroy; "
        f"md_idx={md_idx} vd_idx={vd_idx}"
    )
    # Volume destroy must reference the right ID.
    vd = volume_destroys[0]
    assert "vol_bar" in vd, vd


def test_destroy_machine_does_not_call_volumes_when_unset(tmp_path):
    """destroy_machine should NOT call volumes destroy when
    LEERIE_VOLUME_ID is empty (the today's-behavior case)."""
    stub, log = _make_recording_flyctl(tmp_path)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "LEERIE_FLY_APP": "leerie",
        "FLY_APP": "leerie",
    }
    result = _run_bash(
        f"source {PROVISION_SH}\n"
        'LEERIE_MACHINE_ID="machine-foo"\n'
        'LEERIE_VOLUME_ID=""\n'
        "destroy_machine\n",
        env=env,
    )
    calls = _read_calls(log)
    machine_destroys = [c for c in calls if c[:2] == ["machine", "destroy"]]
    volume_destroys = [c for c in calls if c[:2] == ["volumes", "destroy"]]
    assert machine_destroys, calls
    assert not volume_destroys, (
        f"volumes destroy must NOT fire when LEERIE_VOLUME_ID is empty.\n"
        f"calls={calls}"
    )


def _make_recording_flyctl_failing_machine_run(tmp_path: Path) -> tuple[Path, Path]:
    """Variant of _make_recording_flyctl where `machine run` exits non-zero
    (and prints nothing parseable to stdout). Used to exercise the
    orphan-volume cleanup path at provision.sh:533-545 — when machine-
    create fails AFTER volumes-create succeeded, the volume must be
    reaped explicitly because the EXIT trap is not yet registered.
    """
    log_path = tmp_path / "flyctl-calls.log"
    log_path.write_text("")
    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'LOG="{log_path}"\n'
        'argv=""\n'
        'for a in "$@"; do\n'
        '  if [ -z "$argv" ]; then argv="$a"; else argv="$argv|$a"; fi\n'
        'done\n'
        'echo "$argv" >> "$LOG"\n'
        'case "$1" in\n'
        '  auth)\n'
        '    case "${2:-}" in status) exit 0 ;; *) exit 0 ;; esac\n'
        '    ;;\n'
        '  volumes|volume)\n'
        '    case "${2:-}" in\n'
        '      create)\n'
        # Realistic create output so the parser captures the ID.
        '        cat <<EOF\n'
        '                  ID: vol_abcdef0123456789\n'
        '                Name: test_volume_name\n'
        '                 App: leerie\n'
        'EOF\n'
        '        exit 0\n'
        '        ;;\n'
        # The orphan-cleanup path calls `volumes destroy` — must succeed
        # so we can assert the reaping ran.
        '      destroy) exit 0 ;;\n'
        '    esac\n'
        '    ;;\n'
        '  machine)\n'
        '    case "${2:-}" in\n'
        # The failure under test: machine run exits non-zero with no
        # parseable "Machine ID:" line. The provision parser ends up
        # with machine_id="" and falls into the orphan-cleanup branch.
        '      run)\n'
        '        echo "ERROR: capacity exhausted in region iad" >&2\n'
        '        exit 1\n'
        '        ;;\n'
        '      stop|destroy) exit 0 ;;\n'
        '    esac\n'
        '    ;;\n'
        'esac\n'
        'exit 0\n'
    )
    stub.chmod(0o755)
    return stub, log_path


def test_orphan_volume_cleaned_on_machine_create_failure(tmp_path):
    """When `flyctl machine run` fails AFTER `flyctl volumes create`
    succeeded, the orphan-volume cleanup path at provision.sh:533-545
    must run — otherwise the volume keeps incurring per-GB-month
    charges with no machine to attach it to. The EXIT trap isn't
    registered yet at this point (it fires only after a machine-id is
    captured), so the cleanup has to be explicit.
    """
    stub, log = _make_recording_flyctl_failing_machine_run(tmp_path)
    run_id = "feat-test-orphan"
    _make_run_dir(tmp_path, run_id)
    user_repo = tmp_path / "user-repo"
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
        "LEERIE_FLY_APP": "leerie",
        "FLY_APP": "leerie",
        "USER_REPO": str(user_repo),
        "LEERIE_RUN_ID": run_id,
        "FLY_VM_DISK_GB": "30",
        "LEERIE_MACHINE_START_TIMEOUT": "1",
    }
    result = _run_bash(
        f"source {PROVISION_SH}\n"
        "wait_for_started() { return 0; }\n"
        "provision_machine\n",
        env=env,
    )
    # provision_machine should fail.
    assert result.returncode != 0, (
        f"expected provision_machine to fail; stderr={result.stderr}"
    )
    calls = _read_calls(log)
    non_auth = [c for c in calls if c[0] != "auth"]
    # The sequence must be: volumes create (success) → machine run
    # (fail) → volumes destroy (cleanup).
    volume_creates = [c for c in non_auth if c[:2] == ["volumes", "create"]]
    machine_runs = [c for c in non_auth if c[:2] == ["machine", "run"]]
    volume_destroys = [c for c in non_auth if c[:2] == ["volumes", "destroy"]]
    assert len(volume_creates) == 1, (
        f"expected exactly one volumes create; calls={non_auth}"
    )
    assert len(machine_runs) == 1, (
        f"expected exactly one machine run (the failing one); calls={non_auth}"
    )
    assert len(volume_destroys) == 1, (
        f"expected exactly one volumes destroy (the orphan cleanup); "
        f"calls={non_auth}"
    )
    # The orphan destroy must target the SAME volume that was created
    # (not some other volume we never knew about).
    vc_id = volume_creates[0][2]  # positional arg after `volumes create`
    vd = volume_destroys[0]
    # `volumes destroy` argv: ["volumes", "destroy", "<id>", "--app", ..., "--yes"]
    assert vd[2] == "vol_abcdef0123456789", (
        f"orphan destroy must target the volume we created (vol_abcdef…); "
        f"got argv={vd}"
    )
    # Ordering: create → run → destroy. Anything else is a sequencing bug.
    vc_idx = non_auth.index(volume_creates[0])
    mr_idx = non_auth.index(machine_runs[0])
    vd_idx = non_auth.index(volume_destroys[0])
    assert vc_idx < mr_idx < vd_idx, (
        f"expected volumes create < machine run < volumes destroy; "
        f"vc_idx={vc_idx} mr_idx={mr_idx} vd_idx={vd_idx}"
    )


# --- orphan-volume reaping (the three gaps) ------------------------------
#
# Fly volumes outlive their machines by design ("a Machine can be destroyed
# without destroying its volume" — Fly docs; the leftover is a documented
# "unattached volume"). There is no platform-side lifecycle hook, so leerie
# must reap the volume itself on every path that kills a machine. These
# three tests pin the paths where it silently did not.


def test_destroy_volume_reaps_without_a_machine_id(tmp_path):
    """GAP 1: a known volume whose machine is already gone must still be
    reaped.

    `destroy_machine` early-returns on an empty LEERIE_MACHINE_ID, which
    made its own volume-destroy block unreachable — so a volume whose
    machine died first (the orphan shape) leaked forever. Reaping now lives
    in `destroy_volume`, callable independently.
    """
    stub, log = _make_recording_flyctl(tmp_path)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "LEERIE_FLY_APP": "leerie",
        "FLY_APP": "leerie",
    }
    result = _run_bash(
        f"source {PROVISION_SH}\n"
        'LEERIE_MACHINE_ID=""\n'          # machine already destroyed
        'LEERIE_VOLUME_ID="vol_orphan99"\n'  # but we know the volume
        "destroy_machine\n",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    calls = _read_calls(log)
    volume_destroys = [c for c in calls if c[:2] == ["volumes", "destroy"]]
    assert volume_destroys, (
        f"a known volume must be reaped even with no machine id; calls={calls}")
    assert "vol_orphan99" in volume_destroys[0], volume_destroys[0]


def test_destroy_volume_is_a_noop_without_a_volume_id(tmp_path):
    """`destroy_volume` must not call flyctl when there is no volume —
    guards the no-FLY_VM_DISK_GB path from spurious calls."""
    stub, log = _make_recording_flyctl(tmp_path)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "LEERIE_FLY_APP": "leerie",
        "FLY_APP": "leerie",
    }
    result = _run_bash(
        f"source {PROVISION_SH}\n"
        'LEERIE_MACHINE_ID=""\n'
        'LEERIE_VOLUME_ID=""\n'
        "destroy_volume\n",
        env=env,
    )
    assert result.returncode == 0, result.stderr
    calls = _read_calls(log)
    assert not [c for c in calls if c[:2] == ["volumes", "destroy"]], calls


def test_resolve_volume_id_falls_through_to_run_json(tmp_path):
    """GAP 3: the resolver must keep looking when a file exists but carries
    no volume_id.

    It used to `return 0` on the first *existing* file, so a
    fly-machine.json without volume_id blocked the fall-through to run.json
    entirely — and provision.sh writes volume_id to fly-machine.json only
    conditionally (`if vol_id:`) while always writing it to run.json. That
    combination is a real leak path, not a hypothetical one.
    """
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    # fly-machine.json exists but has NO volume_id.
    (run_dir / "fly-machine.json").write_text(json.dumps({
        "fly_app": "leerie", "fly_machine_id": "m1", "run_id": None,
    }))
    # run.json is where the volume_id actually is.
    (run_dir / "run.json").write_text(json.dumps({
        "fly_machine_id": "m1", "volume_id": "vol_in_run_json",
    }))
    result = _run_bash(
        # Extract just the resolver from the launcher and exercise it.
        f'sed -n "/^_resolve_volume_id_from_run_dir()/,/^}}/p" '
        f'"{REPO_ROOT / "leerie"}" > "{tmp_path}/fn.sh"\n'
        f'. "{tmp_path}/fn.sh"\n'
        f'_resolve_volume_id_from_run_dir "{run_dir}"\n',
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "vol_in_run_json", (
        f"resolver must fall through to run.json when fly-machine.json has "
        f"no volume_id; got {result.stdout!r}")


def test_resolve_volume_id_from_fly_reads_machine_mounts(tmp_path):
    """GAP 2 (unit): the Fly lookup must find a machine's mounted volume.

    Shape pinned against a live Fly machine: `machine list --json` carries
    `.config.mounts[].volume`, and still does while `state=stopped` (the
    --stop-then-kill path). `machine status` has no --json flag, so it is
    deliberately not used.
    """
    stub, log = _make_recording_flyctl(tmp_path)
    env = {
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "FLY_STUB_MACHINE_ID": "m-target",
        "FLY_STUB_MOUNT_VOLUME": "vol_from_fly",
    }
    result = _run_bash(
        f'sed -n "/^_resolve_volume_id_from_fly()/,/^}}/p" '
        f'"{REPO_ROOT / "leerie"}" > "{tmp_path}/fn.sh"\n'
        f'. "{tmp_path}/fn.sh"\n'
        '_resolve_volume_id_from_fly "m-target" "leerie"\n',
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "vol_from_fly", result.stdout
    calls = _read_calls(log)
    assert ["machine", "list", "--app", "leerie", "--json"] in calls, calls


def test_resolve_volume_id_from_fly_empty_when_no_mounts(tmp_path):
    """A machine with no volume must yield nothing — not a spurious reap."""
    stub, log = _make_recording_flyctl(tmp_path)
    env = {
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "FLY_STUB_MACHINE_ID": "m-target",
        # FLY_STUB_MOUNT_VOLUME unset -> stub emits "mounts":[]
    }
    result = _run_bash(
        f'sed -n "/^_resolve_volume_id_from_fly()/,/^}}/p" '
        f'"{REPO_ROOT / "leerie"}" > "{tmp_path}/fn.sh"\n'
        f'. "{tmp_path}/fn.sh"\n'
        '_resolve_volume_id_from_fly "m-target" "leerie"\n',
        env=env,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", result.stdout


def test_kill_with_machine_id_and_no_run_dir_reaps_volume(tmp_path):
    """GAP 2 (end-to-end): `--kill --machine-id <id>` with no sidecar must
    still reap the volume.

    The launcher advertises this flag for "an orphan machine without a
    sidecar", but volume resolution was gated on the run dir existing — so
    the documented escape hatch for orphans was itself an orphan-maker:
    machine destroyed, success reported, volume billing forever.

    Also pins the load-bearing ordering: the Fly lookup must precede the
    machine destroy (the volume->machine link vanishes with the machine),
    and the volume destroy must follow it (Fly refuses to destroy an
    attached volume).
    """
    stub, log = _make_recording_flyctl(tmp_path)
    state = tmp_path / "state"
    (state / "runs").mkdir(parents=True)  # NOTE: no runs/<id>/ -> no sidecar
    env = {
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "LEERIE_STATE_DIR": str(state),   # NB: _STATE_DIR, not _STATE_HOST_DIR
        "LEERIE_FORCE_KILL": "1",
        "LEERIE_FLY_APP": "leerie",
        "LEERIE_RUNTIME": "fly",
        "FLY_STUB_MACHINE_ID": "m-orphan",
        "FLY_STUB_MOUNT_VOLUME": "vol_leaked",
    }
    result = _run_bash(
        f'"{REPO_ROOT / "leerie"}" --kill --machine-id m-orphan\n',
        env=env,
        cwd=REPO_ROOT,
    )
    calls = _read_calls(log)
    volume_destroys = [c for c in calls if c[:2] == ["volumes", "destroy"]]
    assert volume_destroys, (
        f"--kill --machine-id must reap the volume via the Fly lookup; "
        f"calls={calls} stderr={result.stderr}")
    assert "vol_leaked" in volume_destroys[0], volume_destroys[0]

    def _idx(pred):
        return next((i for i, c in enumerate(calls) if pred(c)), -1)

    list_idx = _idx(lambda c: c[:2] == ["machine", "list"])
    md_idx = _idx(lambda c: c[:2] == ["machine", "destroy"])
    vd_idx = _idx(lambda c: c[:2] == ["volumes", "destroy"])
    assert list_idx < md_idx, (
        f"the Fly lookup must precede machine destroy — the volume->machine "
        f"link vanishes with the machine; calls={calls}")
    assert md_idx < vd_idx, (
        f"machine destroy must precede volume destroy — Fly refuses to "
        f"destroy an attached volume; calls={calls}")
