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
