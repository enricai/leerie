"""Tests for the Linux rootless runtime install path in
``scripts/runtime-install.sh`` and the claude auto-install in
``scripts/install.sh``.

Background (the incident these encode against): a fresh Ubuntu install
following the docs dead-ended through three walls, because the old
``runtime_install_linux`` installed only ``containerd`` (apt) + the bare
``nerdctl`` binary and reported success without verifying reachability — no
CNI plugins (so ``nerdctl run`` failed on the missing bridge plugin), no
BuildKit (so leerie's image build failed on a missing ``buildctl``), no
rootless setup, and no ``nerdctl info`` check. The rework installs the full
rootless stack (the exact separate-install sequence proven by hand on the
server) and verifies reachability, and ``install.sh`` auto-installs the
``claude`` CLI instead of only hinting.

Strategy: source the real scripts under ``DRY_RUN=true`` with detection
helpers stubbed (mirroring the extract-and-run-the-real-arm approach in
``test_config_verb.py``). Under dry-run every command is *printed* not run,
so we assert on the emitted command sequence — no network, no apt, no
container. The Fedora/Arch tests pin the deliberate Debian/Ubuntu-only
narrowing so a future contributor can't assume those arms are wired.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_INSTALL = REPO_ROOT / "scripts" / "runtime-install.sh"
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"


# ---------------------------------------------------------------------------
# runtime-install.sh — Linux rootless path
# ---------------------------------------------------------------------------
# Harness: source the real script, then stub the three detection seams
# (_runtime_detect_distro / _runtime_nerdctl_arch / _runtime_have_runnable)
# so runtime_install_linux runs a chosen distro/arch under DRY_RUN=true and
# prints (never executes) its command plan.
def _run_linux_install(distro: str, arch: str = "amd64") -> subprocess.CompletedProcess:
    # No `set -e`: runtime_install_linux legitimately returns 1 on the
    # hint paths, and we must still reach the `echo "__rc=$?"` line to observe
    # it. `set -u` alone is fine.
    script = f"""
      set -u
      . "{RUNTIME_INSTALL}"
      _runtime_detect_distro() {{ echo {distro}; }}
      _runtime_nerdctl_arch()  {{ echo {arch}; }}
      # Force "not already installed / not reachable" so the install body runs.
      _runtime_have_runnable() {{ return 1; }}
      nerdctl() {{ return 1; }}
      runtime_install_linux
      echo "__rc=$?"
    """
    return subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"DRY_RUN": "true", "PATH": "/usr/bin:/bin", "HOME": "/home/tester"},
    )


def test_debian_emits_full_rootless_sequence_in_order() -> None:
    r = _run_linux_install("debian")
    out = r.stdout
    # The load-bearing assertion: every step of the proven sequence appears,
    # in order. We locate each marker's index in the output and assert the
    # indices are strictly increasing.
    markers = [
        # 1. apt prereqs — containerd + the rootless prerequisite set.
        "apt-get install -y containerd rootlesskit slirp4netns uidmap dbus-user-session",
        # 2. nerdctl binary from the pinned upstream tarball.
        "nerdctl-2.3.1-linux-amd64.tar.gz",
        # 3. CNI plugins into /opt/cni/bin (the CNI_PATH nerdctl names).
        "/opt/cni/bin",
        # 4. BuildKit into ~/.local (puts buildctl/buildkitd on PATH).
        "buildkit-v0.31.2.linux-amd64.tar.gz",
        # 5. subuid/subgid guard.
        "usermod --add-subuids 100000-165535 --add-subgids 100000-165535",
        # 6. rootless setuptool install, then the buildkit-containerd worker.
        "containerd-rootless-setuptool.sh install",
        "install-buildkit-containerd",
        # 7. enable-linger.
        "loginctl enable-linger",
    ]
    last = -1
    for m in markers:
        idx = out.find(m)
        assert idx != -1, f"missing step {m!r} in:\n{out}"
        assert idx > last, f"step {m!r} out of order in:\n{out}"
        last = idx


def test_debian_cni_before_buildkit_before_rootless() -> None:
    # Ordering that matters for correctness: CNI (for nerdctl run) and the
    # BuildKit binary (buildkitd on PATH) must both precede the rootless
    # setuptool — install-buildkit-containerd exits 1 without buildkitd.
    out = _run_linux_install("debian").stdout
    assert out.index("/opt/cni/bin") < out.index("buildkit-v0.31.2")
    assert out.index("buildkit-v0.31.2") < out.index(
        "install-buildkit-containerd"
    )


def test_debian_buildkit_uses_containerd_worker_variant() -> None:
    # Must be install-buildkit-containerd (containerd worker; leerie's
    # ensure_base_in_buildkit_ns needs the buildkit namespace), NOT the plain
    # OCI-worker install-buildkit.
    out = _run_linux_install("debian").stdout
    assert "install-buildkit-containerd" in out
    assert "CONTAINERD_NAMESPACE=default" in out
    # The bare OCI variant name must not appear as a standalone command.
    assert not re.search(r"install-buildkit\b(?!-containerd)", out)


def test_debian_rootless_setup_runs_without_sudo() -> None:
    # The setuptool installs a `systemctl --user` unit and must NOT be run
    # under sudo. Assert the setuptool line carries no leading sudo.
    out = _run_linux_install("debian").stdout
    for line in out.splitlines():
        if "containerd-rootless-setuptool.sh install" in line:
            assert "sudo" not in line, f"setuptool must not run under sudo: {line!r}"


def test_rootless_setup_puts_buildkitd_on_path(tmp_path) -> None:
    # Regression (2026-07-26): BuildKit installs to ~/.local/bin, but on a fresh
    # `curl | bash` (non-login) shell ~/.local/bin is NOT on PATH — so
    # `containerd-rootless-setuptool.sh install-buildkit-containerd`'s own
    # `command -v buildkitd` precheck would fail and exit 1, resurfacing the
    # exact wall this change fixes. _runtime_linux_rootless_setup must prepend
    # ~/.local/bin so buildkitd resolves regardless of the caller's PATH.
    #
    # This runs the REAL (non-dry-run) helper with a buildkitd stub in a fake
    # ~/.local/bin that is deliberately absent from the base PATH, and stubs the
    # setuptool to record whether buildkitd resolved at call time.
    home = tmp_path
    (home / ".local" / "bin").mkdir(parents=True)
    (home / ".local" / "bin" / "buildkitd").write_text("#!/bin/sh\necho buildkitd\n")
    (home / ".local" / "bin" / "buildkitd").chmod(0o755)
    marker = tmp_path / "resolved.txt"
    # Stub the setuptool as a REAL executable on a dedicated stub dir (not a
    # bash function) so the `env CONTAINERD_NAMESPACE=default …
    # containerd-rootless-setuptool.sh` call — which does a PATH lookup and
    # can't see shell functions — resolves it, exactly as production's
    # /usr/local/bin copy would. It records whether buildkitd is on PATH at
    # call time. sudo (loginctl) is stubbed the same way.
    stub = tmp_path / "stub"
    stub.mkdir()
    (stub / "containerd-rootless-setuptool.sh").write_text(
        f'#!/bin/sh\nif command -v buildkitd >/dev/null 2>&1; then\n'
        f'  echo resolved >> "{marker}"\nelse\n  echo missing >> "{marker}"\nfi\n'
    )
    (stub / "containerd-rootless-setuptool.sh").chmod(0o755)
    (stub / "sudo").write_text('#!/bin/sh\nexec "$@"\n')  # loginctl → no-op-ish
    (stub / "sudo").chmod(0o755)
    (stub / "loginctl").write_text("#!/bin/sh\nexit 0\n")
    (stub / "loginctl").chmod(0o755)
    script = f"""
      set -u
      . "{RUNTIME_INSTALL}"
      DRY_RUN=false
      _runtime_linux_rootless_setup
      echo "__rc=$?"
    """
    r = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        # Base PATH has the stub dir + system dirs, but deliberately EXCLUDES
        # ~/.local/bin — the production trap the fix must overcome.
        env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(home)},
    )
    assert "__rc=0" in r.stdout, f"rootless_setup should succeed:\n{r.stdout}\n{r.stderr}"
    recorded = marker.read_text() if marker.exists() else ""
    # The setuptool ran (at least once) and saw buildkitd resolvable — never
    # "missing", which is the exit-1 production failure.
    assert "resolved" in recorded, f"buildkitd did not resolve on PATH: {recorded!r}"
    assert "missing" not in recorded, (
        f"buildkitd was missing from PATH at setuptool call — the exit-1 bug: {recorded!r}"
    )


def test_fedora_falls_back_to_docs_hint_not_autoinstall() -> None:
    r = _run_linux_install("fedora")
    assert "__rc=1" in r.stdout, "fedora must return non-zero (hint path)"
    # It must NOT emit any package-manager / rootless install commands.
    for forbidden in (
        "apt-get install",
        "dnf install",
        "pacman -S",
        "containerd-rootless-setuptool.sh",
        "/opt/cni/bin",
    ):
        assert forbidden not in r.stdout, (
            f"fedora must not auto-install ({forbidden!r} leaked):\n{r.stdout}"
        )
    assert "docs/INSTALL.md" in r.stderr


def test_arch_falls_back_to_docs_hint_not_autoinstall() -> None:
    r = _run_linux_install("arch")
    assert "__rc=1" in r.stdout
    for forbidden in (
        "apt-get install",
        "pacman -S",
        "containerd-rootless-setuptool.sh",
        "/opt/cni/bin",
    ):
        assert forbidden not in r.stdout, (
            f"arch must not auto-install ({forbidden!r} leaked):\n{r.stdout}"
        )
    assert "docs/INSTALL.md" in r.stderr


def test_unknown_distro_returns_hint() -> None:
    r = _run_linux_install("unknown")
    assert "__rc=1" in r.stdout
    assert "docs/INSTALL.md" in r.stderr
    assert "containerd-rootless-setuptool.sh" not in r.stdout


def test_version_constants_pinned() -> None:
    # Pin the pinned versions (like test_resolve_confidence_rounds.py pins
    # DEFAULT_CAPS) so a bump is a deliberate, reviewed change kept in lockstep
    # with docs/INSTALL.md.
    text = RUNTIME_INSTALL.read_text()
    assert 'NERDCTL_VERSION="${NERDCTL_VERSION:-2.3.1}"' in text
    assert 'CNI_VERSION="${CNI_VERSION:-v1.9.1}"' in text
    assert 'BUILDKIT_VERSION="${BUILDKIT_VERSION:-v0.31.2}"' in text


# ---------------------------------------------------------------------------
# install.sh — claude auto-install with opt-out
# ---------------------------------------------------------------------------
# Harness: extract the preflight section is impractical (it depends on many
# helpers), so instead drive the real install.sh under --dry-run with git and
# curl stubbed as present and claude stubbed as ABSENT, and observe whether
# the claude installer is invoked. We stub `install_claude`'s side-effect by
# putting a fake `claude.ai` unreachable behind --dry-run: under --dry-run the
# `run` helper only prints the command, so we assert on that printed line.
def _run_install_preflight(no_claude_install_env: str | None) -> subprocess.CompletedProcess:
    # Build a PATH with stub git+curl present and claude absent. The stubs
    # live in a temp dir created inside the bash script itself.
    env = {"PATH": "/usr/bin:/bin", "HOME": "/home/tester"}
    if no_claude_install_env is not None:
        env["LEERIE_NO_CLAUDE_INSTALL"] = no_claude_install_env
    script = f"""
      set -eu
      stub=$(mktemp -d)
      # git + curl present (real ones are fine); claude absent from PATH.
      ln -s "$(command -v git)"  "$stub/git"
      ln -s "$(command -v curl)" "$stub/curl"
      export PATH="$stub:/usr/bin:/bin"
      # Run install.sh in --dry-run so nothing is actually installed; it
      # exits after preflight only if we make later steps no-op. We only care
      # about preflight output, so capture up to the runtime phase.
      bash "{INSTALL_SH}" --dry-run 2>&1 | sed -n '1,60p' || true
    """
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env
    )


def test_claude_autoinstall_invoked_when_missing_and_optout_off() -> None:
    r = _run_install_preflight(no_claude_install_env=None)
    # Under --dry-run the `run` helper prints the command; the claude
    # installer line must appear (auto-install fired), not the manual hint.
    assert "https://claude.ai/install.sh" in r.stdout, (
        f"claude auto-install should fire when missing:\n{r.stdout}"
    )


def test_claude_autoinstall_skipped_with_optout() -> None:
    r = _run_install_preflight(no_claude_install_env="1")
    # With opt-out, the installer must NOT run; the manual hint must show and
    # preflight must fail (exit 1 path — surfaced as the missing hint).
    assert "https://claude.ai/install.sh" not in r.stdout, (
        f"opt-out must skip the claude installer:\n{r.stdout}"
    )
    assert "claude CLI is missing" in r.stdout


def test_install_sh_documents_no_claude_install_flag() -> None:
    text = INSTALL_SH.read_text()
    assert "--no-claude-install" in text
    assert "LEERIE_NO_CLAUDE_INSTALL" in text


# ---------------------------------------------------------------------------
# runtime-install.sh — macOS/Colima path (Darwin-gated)
# ---------------------------------------------------------------------------
# Every function under test opens with `[ "$(uname -s)" = "Darwin" ] || return
# 0`, so on this (Linux) test host they no-op unless `uname` itself is stubbed
# on PATH to report Darwin — mirroring the file's existing PATH-stub pattern
# for apt-get/dnf/pacman above. A dedicated stub bin dir is prepended to PATH
# per test so the guard's `uname -s` call resolves to the fake.
def _darwin_stub_dir(tmp_path: Path) -> Path:
    stub = tmp_path / "stub"
    stub.mkdir(exist_ok=True)
    uname = stub / "uname"
    uname.write_text('#!/bin/sh\nif [ "$1" = "-s" ]; then echo Darwin; fi\n')
    uname.chmod(0o755)
    return stub


def _run_macos_snippet(
    tmp_path: Path,
    body: str,
    *,
    darwin: bool = True,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    path = "/usr/bin:/bin"
    if darwin:
        stub = _darwin_stub_dir(tmp_path)
        path = f"{stub}:{path}"
    script = f"""
      set -u
      . "{RUNTIME_INSTALL}"
      {body}
    """
    env = {"DRY_RUN": "true", "PATH": path, "HOME": str(home)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=env
    )


def test_darwin_guards_are_inert_on_the_real_linux_test_host() -> None:
    # Control: without the uname stub (darwin=False), the real host is Linux,
    # so every Darwin-gated function must no-op (return 0, emit nothing) even
    # though DRY_RUN=true and nothing else is stubbed.
    r = _run_macos_snippet(
        Path("/tmp"),
        """
          out1="$(_runtime_colima_size_flags)"; rc1=$?
          _runtime_check_colima_sizing; rc2=$?
          out3="$(_runtime_colima_swap_yaml)"; rc3=$?
          _runtime_install_colima_swap_yaml; rc4=$?
          _runtime_check_colima_swap; rc5=$?
          echo "flags=[$out1] rc1=$rc1 rc2=$rc2 rc4=$rc4 rc5=$rc5"
        """,
        darwin=False,
    )
    assert "flags=[] rc1=0 rc2=0 rc4=0 rc5=0" in r.stdout, r.stdout
    # _runtime_colima_swap_yaml has no Darwin guard (it's a pure heredoc
    # emitter with no OS-specific behavior), so it is not part of the
    # inertness assertion above; its output is pinned separately in
    # test_colima_swap_yaml_contains_sentinel_and_provision_block.


def test_colima_size_flags_clamps_cpu_and_mem(tmp_path: Path) -> None:
    stub = _darwin_stub_dir(tmp_path)
    sysctl = stub / "sysctl"
    # 20 cpu / 64 GiB host -> cpu 20/2=10 clamped to 8, mem 64/2=32 clamped to 16.
    sysctl.write_text(
        '#!/bin/sh\n'
        'if [ "$2" = "hw.ncpu" ]; then echo 20; '
        'elif [ "$2" = "hw.memsize" ]; then echo $((64 * 1073741824)); fi\n'
    )
    sysctl.chmod(0o755)
    r = _run_macos_snippet(tmp_path, '_runtime_colima_size_flags')
    assert r.stdout.strip() == "--cpu 8 --memory 16", r.stdout


def test_colima_size_flags_clamps_to_minimums(tmp_path: Path) -> None:
    stub = _darwin_stub_dir(tmp_path)
    sysctl = stub / "sysctl"
    # 2 cpu / 4 GiB host -> cpu 2/2=1 clamped to 2, mem 4/2=2 clamped to 4.
    sysctl.write_text(
        '#!/bin/sh\n'
        'if [ "$2" = "hw.ncpu" ]; then echo 2; '
        'elif [ "$2" = "hw.memsize" ]; then echo $((4 * 1073741824)); fi\n'
    )
    sysctl.chmod(0o755)
    r = _run_macos_snippet(tmp_path, '_runtime_colima_size_flags')
    assert r.stdout.strip() == "--cpu 2 --memory 4", r.stdout


def _write_sysctl_stub(stub: Path, cpu: int, mem_gb: int) -> None:
    sysctl = stub / "sysctl"
    sysctl.write_text(
        '#!/bin/sh\n'
        f'if [ "$2" = "hw.ncpu" ]; then echo {cpu * 2}; '
        f'elif [ "$2" = "hw.memsize" ]; then echo $(({mem_gb * 2} * 1073741824)); fi\n'
    )
    sysctl.chmod(0o755)


def test_check_colima_sizing_warns_only_when_both_below_recommendation(
    tmp_path: Path,
) -> None:
    stub = _darwin_stub_dir(tmp_path)
    # Recommendation resolves to --cpu 4 --memory 8 (host reports 8 cpu/16GB).
    _write_sysctl_stub(stub, cpu=4, mem_gb=8)
    home = tmp_path / "home"
    (home / ".colima" / "default").mkdir(parents=True)
    (home / ".colima" / "default" / "colima.yaml").write_text(
        "cpu: 2\nmemory: 4\n"
    )
    r = _run_macos_snippet(tmp_path, "_runtime_check_colima_sizing")
    assert "Colima is running with 2 cpu / 4 GB" in r.stdout, r.stdout
    assert "≥4 cpu / 8 GB" in r.stdout, r.stdout


def test_check_colima_sizing_silent_when_only_one_axis_is_low(
    tmp_path: Path,
) -> None:
    stub = _darwin_stub_dir(tmp_path)
    _write_sysctl_stub(stub, cpu=4, mem_gb=8)
    home = tmp_path / "home"
    (home / ".colima" / "default").mkdir(parents=True)
    # cpu below recommendation, mem above — must not warn (half-match).
    (home / ".colima" / "default" / "colima.yaml").write_text(
        "cpu: 2\nmemory: 32\n"
    )
    r = _run_macos_snippet(tmp_path, "_runtime_check_colima_sizing")
    assert "Colima is running with" not in r.stdout, r.stdout


def test_check_colima_sizing_no_config_is_a_silent_noop(tmp_path: Path) -> None:
    _darwin_stub_dir(tmp_path)
    r = _run_macos_snippet(
        tmp_path, '_runtime_check_colima_sizing; echo "__rc=$?"'
    )
    assert "__rc=0" in r.stdout
    assert r.stderr == ""


def test_colima_swap_yaml_contains_sentinel_and_provision_block() -> None:
    r = _run_macos_snippet(Path("/tmp"), "_runtime_colima_swap_yaml")
    assert "# leerie:swap-provision-v1 BEGIN" in r.stdout
    assert "# leerie:swap-provision-v1 END" in r.stdout
    assert "vm.swappiness=10" in r.stdout
    assert "SWAPSIZE_GB=4" in r.stdout


def test_install_colima_swap_yaml_writes_when_absent(tmp_path: Path) -> None:
    _darwin_stub_dir(tmp_path)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    r = _run_macos_snippet(
        tmp_path,
        '_runtime_install_colima_swap_yaml; echo "__rc=$?"',
        extra_env={"DRY_RUN": "false"},
    )
    assert "__rc=0" in r.stdout, r.stdout
    cfg = home / ".colima" / "default" / "colima.yaml"
    assert cfg.exists(), r.stdout + r.stderr
    assert "leerie:swap-provision-v1" in cfg.read_text()


def test_install_colima_swap_yaml_never_overwrites_existing(tmp_path: Path) -> None:
    _darwin_stub_dir(tmp_path)
    home = tmp_path / "home"
    (home / ".colima" / "default").mkdir(parents=True)
    cfg = home / ".colima" / "default" / "colima.yaml"
    cfg.write_text("cpu: 6\nmemory: 12\n# a user's own custom tuning\n")
    original = cfg.read_text()
    r = _run_macos_snippet(
        tmp_path,
        '_runtime_install_colima_swap_yaml; echo "__rc=$?"',
        extra_env={"DRY_RUN": "false"},
    )
    assert "__rc=0" in r.stdout, r.stdout
    assert cfg.read_text() == original, "existing colima.yaml must not be mutated"


def test_install_colima_swap_yaml_is_a_noop_under_dry_run(tmp_path: Path) -> None:
    _darwin_stub_dir(tmp_path)
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    r = _run_macos_snippet(
        tmp_path, '_runtime_install_colima_swap_yaml; echo "__rc=$?"'
    )
    assert "__rc=0" in r.stdout, r.stdout
    assert not (home / ".colima").exists()


def test_check_colima_swap_hints_only_when_sentinel_missing(tmp_path: Path) -> None:
    _darwin_stub_dir(tmp_path)
    home = tmp_path / "home"
    (home / ".colima" / "default").mkdir(parents=True)
    (home / ".colima" / "default" / "colima.yaml").write_text(
        "cpu: 4\nmemory: 8\n"
    )
    r = _run_macos_snippet(tmp_path, "_runtime_check_colima_swap")
    assert "without leerie's swap provisioning" in r.stdout, r.stdout
    assert "leerie:swap-provision-v1 BEGIN" in r.stderr


def test_check_colima_swap_silent_when_sentinel_present(tmp_path: Path) -> None:
    _darwin_stub_dir(tmp_path)
    home = tmp_path / "home"
    (home / ".colima" / "default").mkdir(parents=True)
    (home / ".colima" / "default" / "colima.yaml").write_text(
        "cpu: 4\nmemory: 8\n# leerie:swap-provision-v1 BEGIN\n"
    )
    r = _run_macos_snippet(tmp_path, "_runtime_check_colima_swap")
    assert "without leerie's swap provisioning" not in r.stdout, r.stdout
    assert r.stderr == ""


# --- runtime_install_macos: the three top-level paths -----------------------
def _colima_brew_stub(
    stub: Path,
    *,
    colima_present: bool,
    brew_present: bool,
    colima_running: bool,
) -> Path:
    log = stub.parent / "calls.log"
    if colima_present:
        colima = stub / "colima"
        colima.write_text(
            f"""#!/bin/sh
echo "colima $*" >> "{log}"
if [ "$1" = "--version" ]; then exit 0; fi
if [ "$1" = "status" ]; then exit {0 if colima_running else 1}; fi
if [ "$1" = "start" ]; then exit 0; fi
exit 0
"""
        )
        colima.chmod(0o755)
    if brew_present:
        brew = stub / "brew"
        colima_after_install = stub / "colima"
        # Simulate `brew install colima` actually placing the binary on PATH,
        # so runtime_install_macos's subsequent `colima start` call resolves.
        brew.write_text(
            rf"""#!/bin/sh
echo "brew $*" >> "{log}"
if [ "$1" = "--version" ]; then exit 0; fi
if [ "$1 $2" = "install colima" ]; then
  cat > "{colima_after_install}" <<SH
#!/bin/sh
echo "colima \$*" >> "{log}"
if [ "\$1" = "--version" ]; then exit 0; fi
if [ "\$1" = "status" ]; then exit 1; fi
if [ "\$1" = "start" ]; then exit 0; fi
exit 0
SH
  chmod 755 "{colima_after_install}"
fi
exit 0
"""
        )
        brew.chmod(0o755)
    return log


def test_runtime_install_macos_installs_via_brew_when_colima_missing(
    tmp_path: Path,
) -> None:
    stub = _darwin_stub_dir(tmp_path)
    log = _colima_brew_stub(
        stub, colima_present=False, brew_present=True, colima_running=False
    )
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    r = _run_macos_snippet(
        tmp_path,
        'runtime_install_macos; echo "__rc=$?"',
        extra_env={"DRY_RUN": "false"},
    )
    assert "__rc=0" in r.stdout, r.stdout + r.stderr
    calls = log.read_text() if log.exists() else ""
    assert "brew install colima" in calls, calls
    assert "colima start" in calls, calls
    cfg = home / ".colima" / "default" / "colima.yaml"
    assert cfg.exists(), "swap yaml must be installed before first start"


def test_runtime_install_macos_errors_without_brew_when_colima_missing(
    tmp_path: Path,
) -> None:
    stub = _darwin_stub_dir(tmp_path)
    _colima_brew_stub(
        stub, colima_present=False, brew_present=False, colima_running=False
    )
    r = _run_macos_snippet(
        tmp_path,
        'runtime_install_macos; echo "__rc=$?"',
        extra_env={"DRY_RUN": "false"},
    )
    assert "__rc=1" in r.stdout, r.stdout + r.stderr
    assert "Homebrew is needed" in r.stderr


def test_runtime_install_macos_starts_vm_when_installed_but_not_running(
    tmp_path: Path,
) -> None:
    stub = _darwin_stub_dir(tmp_path)
    log = _colima_brew_stub(
        stub, colima_present=True, brew_present=True, colima_running=False
    )
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    r = _run_macos_snippet(
        tmp_path,
        'runtime_install_macos; echo "__rc=$?"',
        extra_env={"DRY_RUN": "false"},
    )
    assert "__rc=0" in r.stdout, r.stdout + r.stderr
    calls = log.read_text() if log.exists() else ""
    assert "brew install colima" not in calls, "colima already installed"
    assert "colima start" in calls, calls
    cfg = home / ".colima" / "default" / "colima.yaml"
    assert cfg.exists(), "swap yaml must be installed before starting a stopped VM"


def test_runtime_install_macos_leaves_running_vm_alone_and_hints(
    tmp_path: Path,
) -> None:
    stub = _darwin_stub_dir(tmp_path)
    log = _colima_brew_stub(
        stub, colima_present=True, brew_present=True, colima_running=True
    )
    _write_sysctl_stub(stub, cpu=4, mem_gb=8)
    home = tmp_path / "home"
    (home / ".colima" / "default").mkdir(parents=True)
    # Deliberately undersized on both axes and missing the swap sentinel, so
    # both _runtime_check_colima_sizing and _runtime_check_colima_swap fire.
    (home / ".colima" / "default" / "colima.yaml").write_text(
        "cpu: 2\nmemory: 4\n"
    )
    r = _run_macos_snippet(
        tmp_path,
        'runtime_install_macos; echo "__rc=$?"',
        extra_env={"DRY_RUN": "false"},
    )
    assert "__rc=0" in r.stdout, r.stdout + r.stderr
    calls = log.read_text() if log.exists() else ""
    assert "colima start" not in calls, "an already-running VM must not be restarted"
    assert "brew install colima" not in calls
    assert "Colima is running with 2 cpu / 4 GB" in r.stdout
    assert "without leerie's swap provisioning" in r.stdout
