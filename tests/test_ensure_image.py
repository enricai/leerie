"""Tests for ensure_image() in the leerie launcher.

Phase 1B: the launcher's RUNTIME=fly branch calls ensure_image() before
provision_machine to close the operator-step gap where the first remote
run fails because the registry tag wasn't built/pushed yet.

Strategy: cache positive hits at ~/.cache/leerie/published-tags.txt; on
miss, invoke build-push.sh --push (which is idempotent at the registry).

ensure_image() lives in the bash launcher, so the tests use the same
isolated-harness pattern as test_launcher_runtime_knob.py / source the
launcher's REAL function verbatim (mirroring
`tests/test_resolve_ec2_vars.py`'s `_extract_resolve_ec2_knob`) into a
minimal bash script with build-push.sh/flyctl stubbed, rather than
hand-reproducing the function. A hand-copied reproduction is body-blind by
construction — the tests would run a string literal defined in this file,
so no change to the launcher's actual logic could affect them. This
mattered in practice: the previous hand-copied harness predated the
function's per-repo-Dockerfile branch (`_FLY_PER_REPO_DOCKERFILE`) and its
switch from bare `echo ... >&2` to `remote_log`, so it was silently testing
an old shape of the function.

Verified live per N13's documented trap: an inert sabotage is not a valid
falsification. The discriminating falsification used here is inverting the
cache-hit/cache-miss branch (returning success on a cache MISS instead of a
cache HIT): with the extraction, `test_cache_hit_skips_build_push` and
`test_cache_miss_invokes_build_push_and_records_tag` both fail; against the
old hand-copied harness they could not have, since that harness never read
the launcher's source at all.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.fly_stub import make_fake_flyctl

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_ensure_image() -> str:
    """The REAL `ensure_image()` function, lifted out of the launcher
    verbatim. See module docstring for the body-blindness rationale and
    the live falsification result."""
    src = LAUNCHER.read_text()
    start = src.index("ensure_image() {")
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


# Bash harness wrapping the REAL ensure_image() extracted above.
#
# Test inputs:
#   $XDG_CACHE_HOME → forced to a temp dir so the test never touches
#                     the real user cache.
#   $LEERIE_REPO      → forced to a temp dir holding a stub build-push.sh.
#   $LEERIE_FLY_APP   → app name (required).
#   $LOCAL_BUILD    → "true" to forward --local-build to build-push.sh.
#   PATH            → must include a stub `flyctl` that handles
#                     `apps list --json` and `apps create`.
#   $1              → image tag to ensure.
def _harness() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        # remote_log matches the real _log.sh format closely enough for
        # these tests' substring assertions ("[leerie] ..." on stderr).
        'remote_log() { echo "[leerie] $*" >&2; }\n'
        f"{_extract_ensure_image()}\n"
        'ensure_image "$1"\n'
    )


def _stub_flyctl(tmp_path: Path, existing_apps: list[str] | None = None,
                 create_succeeds: bool = True) -> Path:
    """Write a stub `flyctl` that handles `apps list --json` and `apps
    create`. Records argv to flyctl.log.

    existing_apps: names the stub reports as already created. If None,
    returns an empty list (forcing auto-create path).
    create_succeeds: whether `apps create <name>` exits 0.
    """
    apps = existing_apps or []
    extra_preamble = f'echo "$@" >> {tmp_path}/flyctl.log\n'
    case_body = (
        'if [ "$SUB" = "apps" ] && [ "$MSUB" = "list" ]; then\n'
        '  cat <<JSON\n'
        + "[" + ", ".join(f'{{"Name": "{n}"}}' for n in apps) + "]\n"
        + "JSON\n"
        '  exit 0\n'
        'fi\n'
        'if [ "$SUB" = "apps" ] && [ "$MSUB" = "create" ]; then\n'
        f"  exit {0 if create_succeeds else 1}\n"
        'fi\n'
        'exit 0\n'
    )
    return make_fake_flyctl(tmp_path, case_body, extra_preamble=extra_preamble)


def _run(tag: str, *, env: dict, cwd: Path,
         flyctl_dir: Path | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    if flyctl_dir is not None:
        base_env["PATH"] = f"{flyctl_dir}:{base_env.get('PATH', '')}"
    base_env.update(env)
    return subprocess.run(
        ["bash", "-c", _harness(), "harness", tag],
        env=base_env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )


def _stub_build_push(repo: Path, *, exit_code: int = 0, log_file: Path | None = None) -> Path:
    """Write a stub scripts/remote/build-push.sh that records its invocation."""
    scripts = repo / "scripts" / "remote"
    scripts.mkdir(parents=True)
    bp = scripts / "build-push.sh"
    log_arg = f'"{log_file}"' if log_file is not None else '/dev/null'
    bp.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "build-push stub invoked: $*" >> {log_arg}\n'
        f"exit {exit_code}\n"
    )
    bp.chmod(0o755)
    return bp


def test_cache_hit_skips_build_push(tmp_path: Path):
    """If the tag is in the cache, ensure_image returns 0 without invoking build-push.
    Cache hit also skips flyctl apps list (the short-circuit is the first thing)."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    log = tmp_path / "build-push-invocations.log"
    _stub_build_push(repo, log_file=log)

    cache_home = tmp_path / "cache"
    cache_dir = cache_home / "leerie"
    cache_dir.mkdir(parents=True)
    (cache_dir / "published-tags.txt").write_text(
        "registry.fly.io/leerie:0.2.1\n"
    )

    # No flyctl stub needed — cache hit skips that step.
    result = _run(
        "registry.fly.io/leerie:0.2.1",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "leerie",
        },
        cwd=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert not log.exists() or log.read_text() == "", (
        "build-push.sh should not be invoked on cache hit"
    )


def test_cache_miss_invokes_build_push_and_records_tag(tmp_path: Path):
    """On cache miss, ensure_image runs build-push.sh and appends the tag to the cache.
    With an existing Fly app, skips apps create."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    log = tmp_path / "build-push-invocations.log"
    _stub_build_push(repo, log_file=log)
    _stub_flyctl(tmp_path, existing_apps=["leerie"])

    cache_home = tmp_path / "cache"

    result = _run(
        "registry.fly.io/leerie:9.9.9",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "leerie",
        },
        cwd=tmp_path,
        flyctl_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    # build-push.sh should have been invoked with --app leerie --push.
    assert log.exists(), "build-push.sh should be invoked on cache miss"
    invocation = log.read_text().strip()
    assert "--app leerie --push" in invocation, invocation
    # No --local-build forwarded by default.
    assert "--local-build" not in invocation, invocation
    # Tag should now be recorded in the cache.
    cache_file = cache_home / "leerie" / "published-tags.txt"
    assert cache_file.exists()
    assert "registry.fly.io/leerie:9.9.9" in cache_file.read_text()
    # flyctl apps list called, but apps create NOT called (app exists).
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert "apps list --json" in flyctl_log
    assert "apps create" not in flyctl_log


def test_build_push_failure_propagates(tmp_path: Path):
    """If build-push.sh exits non-zero, ensure_image returns 1 and does not cache."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    _stub_build_push(repo, exit_code=2)
    _stub_flyctl(tmp_path, existing_apps=["leerie"])

    cache_home = tmp_path / "cache"

    result = _run(
        "registry.fly.io/leerie:bad",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "leerie",
        },
        cwd=tmp_path,
        flyctl_dir=tmp_path,
    )
    assert result.returncode == 1, result.stderr
    assert "build-push.sh failed" in result.stderr
    # The failed tag must NOT be recorded — that's how the cache stays
    # a positive list (a missing tag means "probe", not "absent").
    cache_file = cache_home / "leerie" / "published-tags.txt"
    assert not cache_file.exists() or "bad" not in cache_file.read_text()


def test_missing_build_push_script_errors(tmp_path: Path):
    """If scripts/remote/build-push.sh is missing, ensure_image errors with a clear message."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    # No build-push.sh.

    cache_home = tmp_path / "cache"

    # No flyctl stub needed — error occurs before the apps-list step.
    result = _run(
        "registry.fly.io/leerie:0.0.0",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "leerie",
        },
        cwd=tmp_path,
    )
    assert result.returncode == 1
    assert "build-push.sh" in result.stderr
    assert "not found" in result.stderr or "not executable" in result.stderr


def test_positive_cache_only_unrelated_tags_still_probe(tmp_path: Path):
    """A cache entry for tag A must not satisfy a lookup for tag B."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    log = tmp_path / "build-push.log"
    _stub_build_push(repo, log_file=log)
    _stub_flyctl(tmp_path, existing_apps=["leerie"])

    cache_home = tmp_path / "cache"
    cache_dir = cache_home / "leerie"
    cache_dir.mkdir(parents=True)
    (cache_dir / "published-tags.txt").write_text(
        "registry.fly.io/leerie:0.1.0\n"
    )

    result = _run(
        "registry.fly.io/leerie:0.2.0",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "leerie",
        },
        cwd=tmp_path,
        flyctl_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    assert log.exists(), "build-push.sh should be invoked for unrelated tag"


def test_no_auto_publish_flag_consumed_by_launcher():
    """The launcher consumes --no-auto-publish in REWRITTEN_ARGS, not forwarded to orch."""
    leerie_launcher = REPO_ROOT / "leerie"
    text = leerie_launcher.read_text()
    # The flag must be parsed early (env + arg loop).
    assert "NO_AUTO_PUBLISH" in text
    assert "LEERIE_NO_AUTO_PUBLISH" in text
    # The flag must be in the REWRITTEN_ARGS consumption block so the
    # orchestrator's argparse never sees it.
    assert "--no-auto-publish)" in text


def test_ensure_image_extraction_contains_load_bearing_lines():
    """Sanity check on the extraction itself: the load-bearing lines this
    file's tests rely on must actually be present in the extracted
    function body. Guards against a marker-string change in the launcher
    silently truncating `_extract_ensure_image()`'s extraction (e.g. a
    renamed function, or the `\\n}\\n` end-marker matching too early)."""
    extracted = _extract_ensure_image()
    sentinels = [
        'cache_file="$cache_dir/published-tags.txt"',
        'grep -Fxq "$tag" "$cache_file"',
        # Part H: app auto-create + LOCAL_BUILD forwarding.
        'flyctl apps list --json',
        'flyctl apps create "$fly_app"',
        'build_args=(--app "$fly_app" --push)',
        'build_args+=(--local-build)',
        'printf \'%s\\n\' "$tag" >> "$cache_file"',
    ]
    for s in sentinels:
        assert s in extracted, f"missing in extracted function body: {s}"


# --- Part H: app auto-create + --local-build forwarding -----------------

def test_missing_fly_app_triggers_apps_create(tmp_path: Path):
    """When `flyctl apps list` doesn't include the target app, ensure_image
    invokes `flyctl apps create <app>` before build-push.sh."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    log = tmp_path / "build-push.log"
    _stub_build_push(repo, log_file=log)
    # Empty app list — forces auto-create path.
    _stub_flyctl(tmp_path, existing_apps=[])

    cache_home = tmp_path / "cache"
    result = _run(
        "registry.fly.io/myapp:9.9.9",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "myapp",
        },
        cwd=tmp_path,
        flyctl_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert "apps list --json" in flyctl_log
    assert "apps create myapp" in flyctl_log
    assert "Fly app 'myapp' does not exist" in result.stderr


def test_existing_fly_app_skips_apps_create(tmp_path: Path):
    """When `flyctl apps list` includes the target app, ensure_image
    does NOT call `flyctl apps create`."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    log = tmp_path / "build-push.log"
    _stub_build_push(repo, log_file=log)
    _stub_flyctl(tmp_path, existing_apps=["leerie", "otherapp"])

    cache_home = tmp_path / "cache"
    result = _run(
        "registry.fly.io/leerie:9.9.9",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "leerie",
        },
        cwd=tmp_path,
        flyctl_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    flyctl_log = (tmp_path / "flyctl.log").read_text()
    assert "apps list --json" in flyctl_log
    assert "apps create" not in flyctl_log


def test_apps_create_failure_propagates(tmp_path: Path):
    """If `flyctl apps create` fails, ensure_image returns 1 and doesn't
    invoke build-push.sh."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    log = tmp_path / "build-push.log"
    _stub_build_push(repo, log_file=log)
    _stub_flyctl(tmp_path, existing_apps=[], create_succeeds=False)

    cache_home = tmp_path / "cache"
    result = _run(
        "registry.fly.io/leerie:9.9.9",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "leerie",
        },
        cwd=tmp_path,
        flyctl_dir=tmp_path,
    )
    assert result.returncode == 1
    assert "flyctl apps create leerie failed" in result.stderr
    assert not log.exists() or log.read_text() == "", \
        "build-push.sh must not be invoked when apps create fails"


def test_local_build_env_forwards_flag_to_build_push(tmp_path: Path):
    """When LOCAL_BUILD=true is exported (mirroring --local-build /
    LEERIE_LOCAL_BUILD=1 in the launcher), ensure_image forwards
    --local-build to build-push.sh."""
    repo = tmp_path / "leerie-repo"
    repo.mkdir()
    log = tmp_path / "build-push.log"
    _stub_build_push(repo, log_file=log)
    _stub_flyctl(tmp_path, existing_apps=["leerie"])

    cache_home = tmp_path / "cache"
    result = _run(
        "registry.fly.io/leerie:9.9.9",
        env={
            "XDG_CACHE_HOME": str(cache_home),
            "LEERIE_REPO": str(repo),
            "LEERIE_FLY_APP": "leerie",
            "LOCAL_BUILD": "true",
        },
        cwd=tmp_path,
        flyctl_dir=tmp_path,
    )
    assert result.returncode == 0, result.stderr
    invocation = log.read_text().strip()
    assert "--local-build" in invocation, invocation


def test_local_build_flag_consumed_by_launcher():
    """The launcher consumes --local-build in REWRITTEN_ARGS, not forwarded to orch.
    Also honors LEERIE_LOCAL_BUILD env var."""
    leerie_launcher = REPO_ROOT / "leerie"
    text = leerie_launcher.read_text()
    # The flag must be parsed early (env + arg loop).
    assert "LOCAL_BUILD" in text
    assert "LEERIE_LOCAL_BUILD" in text
    # The flag must be in the REWRITTEN_ARGS consumption block so the
    # orchestrator's argparse never sees it.
    assert "--local-build)" in text
