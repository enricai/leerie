"""Tests for per-repo Fly.io image build wiring in the leerie launcher.

Covers the functions added to the remote/Fly path:
  _set_fly_per_repo_image  — sets LEERIE_FLY_IMAGE + _FLY_* context vars
  resolve_fly_image_tag    — honors LEERIE_FLY_IMAGE override
  ensure_image             — per-repo derived image build-push path

Uses the same bash-harness subprocess pattern as test_launcher_per_repo_image.py
and test_ensure_image.py: extract the relevant functions verbatim from the
launcher, prepend stubs, run the combined script in a subprocess.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


def _extract_block(text: str, start_marker: str, end_marker: str) -> str:
    s = text.index(start_marker)
    e = text.index(end_marker, s)
    return text[s:e]


# ---------------------------------------------------------------------------
# Harness: stubs for the Fly remote region of the launcher.
# We need: _leerie_sha256, _leerie_repo_id, resolve_fly_image_tag,
# _set_fly_per_repo_image, and ensure_image.
# ---------------------------------------------------------------------------

_HARNESS_PREFIX = r"""
#!/usr/bin/env bash
set -euo pipefail

remote_log() { echo "[leerie] $*" >&2; }

# Stub flyctl: record calls; apps list returns a JSON array with our app.
flyctl() {
  local sub="${1:-}"; shift || true
  echo "flyctl $sub $*" >> "${FLYCTL_LOG:-/dev/null}"
  case "$sub" in
    apps)
      # Always report app as existing.
      printf '[{"Name":"%s"}]\n' "${LEERIE_FLY_APP:-testapp}"
      return 0
      ;;
    deploy)
      return "${FLYCTL_DEPLOY_RC:-0}"
      ;;
    *)
      return 0
      ;;
  esac
}

# Stub build-push.sh: record invocation args to BUILD_PUSH_LOG.
# Returns BUILD_PUSH_RC (default 0).
_stub_build_push() {
  echo "$*" >> "${BUILD_PUSH_LOG:-/dev/null}"
  return "${BUILD_PUSH_RC:-0}"
}

LEERIE_VERSION="${LEERIE_VERSION:-0.99.test}"
USER_REPO="${USER_REPO:-/tmp/test-user-repo}"
LEERIE_FLY_APP="${LEERIE_FLY_APP:-testapp}"
LEERIE_REPO="${LEERIE_REPO:-/tmp/leerie-repo}"
LOCAL_BUILD="${LOCAL_BUILD:-false}"

"""

# Sha256 function block (extracted verbatim from launcher)
_SHA256_MARKER_START = "# Return the sha256 of a file, portably across Linux"
_SHA256_MARKER_END = "\n# Compute a sanitized repo identifier"

# Fly image functions block (extracted verbatim from launcher)
_FLY_MARKER_START = "# --- remote: resolve Fly.io image tag ---"
_FLY_MARKER_END = "\n# --- run ---"


def _run_harness(
    body: str,
    env: dict | None = None,
    build_push_rc: int = 0,
    flyctl_deploy_rc: int = 0,
) -> subprocess.CompletedProcess:
    launcher = _launcher_text()
    sha256_block = _extract_block(launcher, _SHA256_MARKER_START, _SHA256_MARKER_END)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    script = _HARNESS_PREFIX + sha256_block + "\n" + fly_block + "\n" + body

    base_env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
        "LEERIE_VERSION": "0.99.test",
        "LEERIE_FLY_APP": "testapp",
        "BUILD_PUSH_RC": str(build_push_rc),
        "FLYCTL_DEPLOY_RC": str(flyctl_deploy_rc),
    }
    if env:
        base_env.update(env)

    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# resolve_fly_image_tag
# ---------------------------------------------------------------------------

def test_resolve_fly_image_tag_base():
    """Without LEERIE_FLY_IMAGE set, returns registry.fly.io/<app>:<version>."""
    result = _run_harness('echo "tag=$(resolve_fly_image_tag)"')
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "registry.fly.io/testapp:0.99.test", tag


def test_resolve_fly_image_tag_honors_override():
    """LEERIE_FLY_IMAGE env override is returned verbatim."""
    result = _run_harness(
        'echo "tag=$(resolve_fly_image_tag)"',
        env={"LEERIE_FLY_IMAGE": "registry.fly.io/testapp:custom-tag"},
    )
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "registry.fly.io/testapp:custom-tag", tag


# ---------------------------------------------------------------------------
# _set_fly_per_repo_image
# ---------------------------------------------------------------------------

def test_set_fly_per_repo_image_no_dockerfile(tmp_path):
    """Without .leerie/Dockerfile, LEERIE_FLY_IMAGE is not set."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    result = _run_harness(
        '_set_fly_per_repo_image\necho "fly_image=${LEERIE_FLY_IMAGE:-UNSET}"',
        env={"USER_REPO": str(user_repo)},
    )
    assert result.returncode == 0, result.stderr
    assert "fly_image=UNSET" in result.stdout


def test_set_fly_per_repo_image_with_dockerfile(tmp_path):
    """With .leerie/Dockerfile, LEERIE_FLY_IMAGE is set to per-repo tag."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
    (leerie_dir / "Dockerfile").write_text(df_content)

    result = _run_harness(
        '_set_fly_per_repo_image\necho "fly_image=${LEERIE_FLY_IMAGE:-UNSET}"',
        env={"USER_REPO": str(user_repo), "LEERIE_VERSION": "1.2.3"},
    )
    assert result.returncode == 0, result.stderr
    fly_image = result.stdout.strip().removeprefix("fly_image=")
    assert fly_image.startswith("registry.fly.io/testapp:1.2.3-"), fly_image
    # Hash should be 12 hex chars
    suffix = fly_image.split("1.2.3-")[1]
    assert len(suffix) == 12, f"expected 12-char hash suffix, got: {suffix!r}"
    assert all(c in "0123456789abcdef" for c in suffix), suffix


def test_set_fly_per_repo_image_hash_matches_dockerfile_content(tmp_path):
    """The REPO_HASH in LEERIE_FLY_IMAGE matches first 12 chars of sha256(Dockerfile)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
    df_path = leerie_dir / "Dockerfile"
    df_path.write_text(df_content)

    expected_hash = hashlib.sha256(df_content.encode()).hexdigest()[:12]

    result = _run_harness(
        '_set_fly_per_repo_image\necho "fly_image=${LEERIE_FLY_IMAGE:-UNSET}"',
        env={"USER_REPO": str(user_repo), "LEERIE_VERSION": "2.0.0"},
    )
    assert result.returncode == 0, result.stderr
    fly_image = result.stdout.strip().removeprefix("fly_image=")
    assert fly_image == f"registry.fly.io/testapp:2.0.0-{expected_hash}", fly_image


def test_set_fly_per_repo_image_sets_base_tag_context(tmp_path):
    """_FLY_BASE_TAG is set to the unversioned base tag."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")

    result = _run_harness(
        '_set_fly_per_repo_image\necho "base=${_FLY_BASE_TAG:-UNSET}"',
        env={"USER_REPO": str(user_repo), "LEERIE_VERSION": "1.0.0"},
    )
    assert result.returncode == 0, result.stderr
    assert "base=registry.fly.io/testapp:1.0.0" in result.stdout


def test_set_fly_per_repo_image_sets_dockerfile_context(tmp_path):
    """_FLY_PER_REPO_DOCKERFILE is set to the .leerie/Dockerfile path."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")

    result = _run_harness(
        '_set_fly_per_repo_image\necho "df=${_FLY_PER_REPO_DOCKERFILE:-UNSET}"',
        env={"USER_REPO": str(user_repo)},
    )
    assert result.returncode == 0, result.stderr
    df_path = result.stdout.strip().removeprefix("df=")
    assert df_path.endswith("/.leerie/Dockerfile"), df_path


# ---------------------------------------------------------------------------
# ensure_image — base (no per-repo Dockerfile)
# ---------------------------------------------------------------------------

def _make_build_push_stub(tmp_path: Path, rc: int = 0) -> Path:
    """Create a build-push.sh stub that logs its args and returns rc."""
    log_file = tmp_path / "build-push.log"
    stub = tmp_path / "build-push.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {log_file}\n"
        f"exit {rc}\n"
    )
    stub.chmod(0o755)
    return stub


def _make_flyctl_stub(tmp_path: Path, app: str = "testapp") -> Path:
    """Create a flyctl stub that returns app-list JSON and exits 0."""
    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'if [ "${{1:-}}" = "apps" ]; then\n'
        f'  printf \'[{{"Name":"{app}"}}]\\n\'\n'
        f'  exit 0\nfi\nexit 0\n'
    )
    stub.chmod(0o755)
    return stub


def _run_ensure_image(
    tag: str,
    tmp_path: Path,
    extra_env: dict | None = None,
    build_push_rc: int = 0,
    cache_hits: list[str] | None = None,
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    """Run ensure_image in a harness; return (result, build_push_log, cache_file).

    ensure_image resolves cache_dir as ${XDG_CACHE_HOME:-$HOME/.cache}/leerie.
    We set XDG_CACHE_HOME to tmp_path so cache_dir = tmp_path/leerie/.
    """
    launcher = _launcher_text()
    sha256_block = _extract_block(launcher, _SHA256_MARKER_START, _SHA256_MARKER_END)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    build_push_stub = _make_build_push_stub(tmp_path, build_push_rc)
    _make_flyctl_stub(tmp_path)
    build_push_log = tmp_path / "build-push.log"
    # cache_dir = XDG_CACHE_HOME/leerie = tmp_path/leerie
    cache_dir = tmp_path / "leerie"
    cache_dir.mkdir()
    cache_file = cache_dir / "published-tags.txt"
    if cache_hits:
        cache_file.write_text("\n".join(cache_hits) + "\n")

    # Override LEERIE_REPO to point build-push path at our stub.
    leerie_repo = tmp_path / "leerie-repo"
    leerie_repo.mkdir()
    bp_dir = leerie_repo / "scripts" / "remote"
    bp_dir.mkdir(parents=True)
    import shutil
    shutil.copy(build_push_stub, bp_dir / "build-push.sh")
    (bp_dir / "build-push.sh").chmod(0o755)

    body = f'ensure_image "{tag}"\necho "rc=$?"'
    script = (
        _HARNESS_PREFIX
        + sha256_block
        + "\n"
        + fly_block
        + "\n"
        + body
    )

    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path),  # cache_dir = tmp_path/leerie/
        "LEERIE_VERSION": "0.99.test",
        "LEERIE_FLY_APP": "testapp",
        "LEERIE_REPO": str(leerie_repo),
        "USER_REPO": str(tmp_path / "user-repo"),
    }
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )
    return result, build_push_log, cache_file


def test_ensure_image_base_cache_hit(tmp_path):
    """When tag is already in published-tags.txt, build-push is not called."""
    tag = "registry.fly.io/testapp:0.99.test"
    result, bp_log, _ = _run_ensure_image(
        tag, tmp_path, cache_hits=[tag]
    )
    assert result.returncode == 0, result.stderr
    assert not bp_log.exists(), "build-push should not have been called on cache hit"
    assert "rc=0" in result.stdout


def test_ensure_image_base_cache_miss_calls_build_push(tmp_path):
    """On cache miss, build-push.sh is called and tag appended to cache."""
    tag = "registry.fly.io/testapp:0.99.test"
    result, bp_log, cache_file = _run_ensure_image(tag, tmp_path)
    assert result.returncode == 0, result.stderr
    assert bp_log.exists(), "build-push should have been called"
    log = bp_log.read_text()
    assert "--push" in log
    assert "--app testapp" in log
    # Tag should now be in cache
    assert tag in cache_file.read_text()


def test_ensure_image_base_build_push_failure(tmp_path):
    """When build-push.sh fails, ensure_image returns 1."""
    tag = "registry.fly.io/testapp:0.99.test"
    result, _, _ = _run_ensure_image(tag, tmp_path, build_push_rc=1)
    assert result.returncode != 0 or "rc=1" in result.stdout


# ---------------------------------------------------------------------------
# ensure_image — per-repo derived image path
# ---------------------------------------------------------------------------

def _run_ensure_image_per_repo(
    tmp_path: Path,
    build_push_rc: int = 0,
    base_cached: bool = False,
) -> tuple[subprocess.CompletedProcess, Path, Path, str, str]:
    """Run ensure_image with _FLY_PER_REPO_DOCKERFILE set (per-repo path)."""
    launcher = _launcher_text()
    sha256_block = _extract_block(launcher, _SHA256_MARKER_START, _SHA256_MARKER_END)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    user_repo = tmp_path / "user-repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
    (leerie_dir / "Dockerfile").write_text(df_content)

    repo_hash = hashlib.sha256(df_content.encode()).hexdigest()[:12]
    base_tag = "registry.fly.io/testapp:0.99.test"
    per_repo_tag = f"registry.fly.io/testapp:0.99.test-{repo_hash}"

    build_push_log = tmp_path / "build-push.log"
    build_push_stub = tmp_path / "build-push.sh"
    build_push_stub.write_text(
        "#!/usr/bin/env bash\n"
        f"echo \"$@\" >> {build_push_log}\n"
        f"exit {build_push_rc}\n"
    )
    build_push_stub.chmod(0o755)

    flyctl_stub = tmp_path / "flyctl"
    flyctl_stub.write_text(
        f'#!/usr/bin/env bash\n'
        f'if [ "${{1:-}}" = "apps" ]; then\n'
        f'  printf \'[{{"Name":"testapp"}}]\\n\'\n'
        f'  exit 0\nfi\nexit 0\n'
    )
    flyctl_stub.chmod(0o755)

    leerie_repo = tmp_path / "leerie-repo"
    scripts_dir = leerie_repo / "scripts" / "remote"
    scripts_dir.mkdir(parents=True)
    import shutil
    shutil.copy(build_push_stub, scripts_dir / "build-push.sh")
    (scripts_dir / "build-push.sh").chmod(0o755)

    # cache_dir = XDG_CACHE_HOME/leerie = tmp_path/leerie/
    cache_dir = tmp_path / "leerie"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "published-tags.txt"
    if base_cached:
        cache_file.write_text(base_tag + "\n")

    body = (
        f'_FLY_PER_REPO_DOCKERFILE="{leerie_dir / "Dockerfile"}"\n'
        f'_FLY_BASE_TAG="{base_tag}"\n'
        f'ensure_image "{per_repo_tag}"\n'
        f'echo "rc=$?"\n'
    )
    script = _HARNESS_PREFIX + sha256_block + "\n" + fly_block + "\n" + body

    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path),  # cache_dir = tmp_path/leerie/
        "LEERIE_VERSION": "0.99.test",
        "LEERIE_FLY_APP": "testapp",
        "LEERIE_REPO": str(leerie_repo),
        "USER_REPO": str(user_repo),
    }

    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )
    return result, build_push_log, cache_file, per_repo_tag, base_tag


def test_ensure_image_per_repo_calls_build_push_with_dockerfile(tmp_path):
    """Per-repo path calls build-push.sh with --dockerfile and --build-arg."""
    result, bp_log, cache_file, per_repo_tag, base_tag = \
        _run_ensure_image_per_repo(tmp_path, base_cached=True)
    assert result.returncode == 0, result.stderr
    assert bp_log.exists(), f"build-push not called. stderr: {result.stderr}"
    log = bp_log.read_text()
    assert "--dockerfile" in log
    assert ".leerie/Dockerfile" in log
    assert f"BASE_IMAGE={base_tag}" in log
    assert "--tag" in log
    assert per_repo_tag in log


def test_ensure_image_per_repo_caches_per_repo_tag(tmp_path):
    """Per-repo tag is recorded in published-tags.txt after successful build."""
    result, bp_log, cache_file, per_repo_tag, _ = \
        _run_ensure_image_per_repo(tmp_path, base_cached=True)
    assert result.returncode == 0, result.stderr
    assert per_repo_tag in cache_file.read_text()


def test_ensure_image_per_repo_ensures_base_first_when_not_cached(tmp_path):
    """When base is not cached, build-push is called for base first, then per-repo."""
    result, bp_log, cache_file, per_repo_tag, base_tag = \
        _run_ensure_image_per_repo(tmp_path, base_cached=False)
    assert result.returncode == 0, result.stderr
    assert bp_log.exists()
    # Two build-push invocations: one for base (no --dockerfile), one for per-repo
    invocations = bp_log.read_text().strip().split("\n")
    assert len(invocations) >= 2, f"expected >=2 build-push calls, got: {invocations}"
    # First call: no --dockerfile (base image)
    assert "--dockerfile" not in invocations[0], invocations[0]
    # Second call: has --dockerfile (per-repo)
    assert "--dockerfile" in invocations[1], invocations[1]
    # Both tags cached
    cached = cache_file.read_text()
    assert base_tag in cached
    assert per_repo_tag in cached


def test_ensure_image_per_repo_cache_hit_skips_build(tmp_path):
    """When per-repo tag is already in published-tags.txt, build is skipped."""
    launcher = _launcher_text()
    sha256_block = _extract_block(launcher, _SHA256_MARKER_START, _SHA256_MARKER_END)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    user_repo = tmp_path / "user-repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
    (leerie_dir / "Dockerfile").write_text(df_content)
    repo_hash = hashlib.sha256(df_content.encode()).hexdigest()[:12]
    per_repo_tag = f"registry.fly.io/testapp:0.99.test-{repo_hash}"

    build_push_log = tmp_path / "build-push.log"
    build_push_stub = tmp_path / "build-push.sh"
    # Would fail if called — verifies cache hit skips it
    build_push_stub.write_text(
        f"#!/usr/bin/env bash\necho \"$@\" >> {build_push_log}\nexit 1\n"
    )
    build_push_stub.chmod(0o755)

    leerie_repo = tmp_path / "leerie-repo"
    scripts_dir = leerie_repo / "scripts" / "remote"
    scripts_dir.mkdir(parents=True)
    import shutil
    shutil.copy(build_push_stub, scripts_dir / "build-push.sh")
    (scripts_dir / "build-push.sh").chmod(0o755)

    flyctl_stub = tmp_path / "flyctl"
    flyctl_stub.write_text(
        f'#!/usr/bin/env bash\nprintf \'[{{"Name":"testapp"}}]\\n\'\nexit 0\n'
    )
    flyctl_stub.chmod(0o755)

    # cache_dir = XDG_CACHE_HOME/leerie = tmp_path/leerie/
    cache_dir = tmp_path / "leerie"
    cache_dir.mkdir(parents=True)
    cache_file = cache_dir / "published-tags.txt"
    # Pre-populate with per-repo tag
    cache_file.write_text(per_repo_tag + "\n")

    body = (
        f'_FLY_PER_REPO_DOCKERFILE="{leerie_dir / "Dockerfile"}"\n'
        f'_FLY_BASE_TAG="registry.fly.io/testapp:0.99.test"\n'
        f'ensure_image "{per_repo_tag}"\n'
        f'echo "rc=$?"\n'
    )
    script = _HARNESS_PREFIX + sha256_block + "\n" + fly_block + "\n" + body

    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path),  # cache_dir = tmp_path/leerie/
        "LEERIE_VERSION": "0.99.test",
        "LEERIE_FLY_APP": "testapp",
        "LEERIE_REPO": str(leerie_repo),
        "USER_REPO": str(user_repo),
    }
    result = subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "rc=0" in result.stdout
    # build-push should NOT have been called (cache hit)
    assert not build_push_log.exists(), f"build-push was called on cache hit: {build_push_log.read_text()}"


def test_ensure_image_per_repo_build_push_failure(tmp_path):
    """When build-push.sh fails on per-repo build, ensure_image returns non-zero."""
    result, _, _, _, _ = _run_ensure_image_per_repo(
        tmp_path, build_push_rc=1, base_cached=True
    )
    assert result.returncode != 0 or "rc=1" in result.stdout


# ---------------------------------------------------------------------------
# Integration: _set_fly_per_repo_image → resolve_fly_image_tag chain
# ---------------------------------------------------------------------------

def test_fly_image_tag_pipeline_with_dockerfile(tmp_path):
    """With .leerie/Dockerfile: _set_fly_per_repo_image sets LEERIE_FLY_IMAGE,
    then resolve_fly_image_tag returns the per-repo tag."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
    (leerie_dir / "Dockerfile").write_text(df_content)
    repo_hash = hashlib.sha256(df_content.encode()).hexdigest()[:12]

    launcher = _launcher_text()
    sha256_block = _extract_block(launcher, _SHA256_MARKER_START, _SHA256_MARKER_END)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    body = (
        "_set_fly_per_repo_image\n"
        'echo "tag=$(resolve_fly_image_tag)"\n'
    )
    script = _HARNESS_PREFIX + sha256_block + "\n" + fly_block + "\n" + body

    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
        "LEERIE_VERSION": "1.5.0",
        "LEERIE_FLY_APP": "myapp",
        "USER_REPO": str(user_repo),
    }
    result = subprocess.run(["bash", "-c", script], env=env,
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == f"registry.fly.io/myapp:1.5.0-{repo_hash}", tag


def test_fly_image_tag_pipeline_without_dockerfile(tmp_path):
    """Without .leerie/Dockerfile: resolve_fly_image_tag returns base tag."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()

    launcher = _launcher_text()
    sha256_block = _extract_block(launcher, _SHA256_MARKER_START, _SHA256_MARKER_END)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    body = (
        "_set_fly_per_repo_image\n"
        'echo "tag=$(resolve_fly_image_tag)"\n'
    )
    script = _HARNESS_PREFIX + sha256_block + "\n" + fly_block + "\n" + body

    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
        "LEERIE_VERSION": "1.5.0",
        "LEERIE_FLY_APP": "myapp",
        "USER_REPO": str(user_repo),
    }
    result = subprocess.run(["bash", "-c", script], env=env,
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "registry.fly.io/myapp:1.5.0", tag
