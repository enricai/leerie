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
import re
import subprocess
from pathlib import Path

from tests.conftest import BUILDCTL_STUB, FRONTEND_SH_SOURCE, LOG_SH_SOURCE
from tests.fly_stub import make_fake_flyctl
from tests.repo_image_block_extract import extract_block as _extract_block
from tests.repo_image_block_extract import fly_repo_hash, repo_build_functions

REPO_ROOT = Path(__file__).resolve().parent.parent


def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


# ---------------------------------------------------------------------------
# Harness: stubs for the Fly remote region of the launcher.
# We need: the repository-image build helpers (repo_build_functions),
# resolve_fly_image_tag, _set_fly_per_repo_image, and ensure_image.
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
_HARNESS_PREFIX = (LOG_SH_SOURCE + FRONTEND_SH_SOURCE + BUILDCTL_STUB
                   + _HARNESS_PREFIX)

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
    build_block = repo_build_functions(launcher)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    script = _HARNESS_PREFIX + build_block + "\n" + fly_block + "\n" + body

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
    if "USER_REPO" in base_env and "LEERIE_STATE_HOST_DIR" not in base_env:
        base_env["LEERIE_STATE_HOST_DIR"] = str(
            Path(base_env["USER_REPO"]).parent / "state")

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
    """The REPO_HASH in LEERIE_FLY_IMAGE is the first 12 chars of the sha256
    of the Dockerfile's sha256 and its (here empty) context's."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
    df_path = leerie_dir / "Dockerfile"
    df_path.write_text(df_content)

    expected_hash = fly_repo_hash(df_content.encode())

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
    """_FLY_REPO_DF_SHA is the digest of the bytes leerie's Dockerfile was
    made from, which ensure_image requires again."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
    (leerie_dir / "Dockerfile").write_text(df_content)

    result = _run_harness(
        '_set_fly_per_repo_image\necho "df=${_FLY_REPO_DF_SHA:-UNSET}"',
        env={"USER_REPO": str(user_repo)},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == (
        "df=" + hashlib.sha256(df_content.encode()).hexdigest())


def test_a_linked_dockerfile_is_never_read(tmp_path):
    """A committed `.leerie/Dockerfile` that is a link out of the checkout:
    the launcher reads nothing through it, so no digest of the file it names
    names an image; the launch stops (DESIGN §6 *Leerie never reads through
    a link a repository holds*)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    outside = tmp_path / "operator-file"
    outside.write_text("FROM debian:13\n")
    (leerie_dir / "Dockerfile").symlink_to(outside)

    result = _run_harness(
        '_set_fly_per_repo_image\necho "fly_image=${LEERIE_FLY_IMAGE:-UNSET}"',
        env={"USER_REPO": str(user_repo)},
    )
    digest = hashlib.sha256(outside.read_bytes()).hexdigest()[:12]
    assert digest not in result.stdout + result.stderr
    assert result.returncode == 1, result.stdout
    assert "cannot be read as a regular file beneath the checkout" in \
        result.stderr


# ---------------------------------------------------------------------------
# ensure_image (review round 29, isolation F1): each image is pushed under
# exactly the tag asked for, recorded by digest, and started by digest; a
# repository image is built FROM the base's digest and its layout checked.
# ---------------------------------------------------------------------------

BASE_DIGEST = "sha256:" + "b" * 64
FLY_REPO_DIGEST = "sha256:" + "c" * 64
STAGE_DIGEST = "sha256:" + "d" * 64
OTHER_DIGEST = "sha256:" + "e" * 64
BASE_TAG = "registry.fly.io/testapp:0.99.test"
LAYOUT = re.search(r'^REPO_IMAGE_LAYOUT="([^"]+)"', _launcher_text(),
                   re.M).group(1)


def _install_build_push(tmp_path: Path, rc: int = 0) -> tuple[Path, Path]:
    """A stub scripts/remote/build-push.sh under a stand-in LEERIE_REPO: it
    records its argv, keeps a copy of the Dockerfile it is given
    (`repo-dockerfile.copy` for a push with `--no-run`, the repository's
    own stage; `dockerfile.copy` otherwise), and on success reports through
    `--digest-file` STAGE_DIGEST for the repository's stage,
    FLY_REPO_DIGEST for a push with `--layout-base` (leerie's repository
    image), BASE_DIGEST otherwise. Builtins only."""
    log = tmp_path / "build-push.log"
    copy = tmp_path / "dockerfile.copy"
    leerie_repo = tmp_path / "leerie-repo"
    scripts_dir = leerie_repo / "scripts" / "remote"
    scripts_dir.mkdir(parents=True)
    stub = scripts_dir / "build-push.sh"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        f"[ {rc} -eq 0 ] || exit {rc}\n"
        f'd="{BASE_DIGEST}"; out="{copy}"; dfp=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        f'    --layout-base) d="{FLY_REPO_DIGEST}" ;;\n'
        f'    --no-run) d="{STAGE_DIGEST}"; out="{tmp_path}/repo-dockerfile.copy" ;;\n'
        '    --dockerfile) dfp="$2" ;;\n'
        '    --digest-file) df="$2" ;;\n'
        '  esac\n'
        '  shift\n'
        'done\n'
        'if [ -n "$dfp" ]; then\n'
        '  while IFS= read -r l; do printf "%s\\n" "$l"; done < "$dfp" > "$out"\n'
        'fi\n'
        'printf "%s\\n" "$d" > "$df"\n'
        "exit 0\n"
    )
    stub.chmod(0o755)
    return leerie_repo, log


def _run_ensure(tmp_path: Path, *, per_repo: bool, cache: str = "",
                rc: int = 0) -> tuple[subprocess.CompletedProcess, Path,
                                      Path, str]:
    """Run the launcher's real `_set_fly_per_repo_image` (per_repo) and
    `ensure_image` on the tag `resolve_fly_image_tag` gives; return (result,
    build-push log, cache file, tag)."""
    launcher = _launcher_text()
    build_block = repo_build_functions(launcher)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)
    leerie_repo, log = _install_build_push(tmp_path, rc)
    _make_flyctl_stub(tmp_path)
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
    tag = BASE_TAG
    if per_repo:
        (user_repo / ".leerie").mkdir()
        (user_repo / ".leerie" / "Dockerfile").write_text(df_content)
        repo_hash = fly_repo_hash(df_content.encode())
        tag = f"{BASE_TAG}-{repo_hash}"
    cache_dir = tmp_path / "leerie"
    cache_dir.mkdir()
    cache_file = cache_dir / "published-tags.txt"
    if cache:
        cache_file.write_text(cache)
    body = (("_set_fly_per_repo_image\n" if per_repo else "")
            + 'ensure_image "$(resolve_fly_image_tag)"\n'
            + 'echo "ref=$FLY_IMAGE_REF"\n')
    script = _HARNESS_PREFIX + build_block + "\n" + fly_block + "\n" + body
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path),
        "XDG_CACHE_HOME": str(tmp_path),  # cache_dir = tmp_path/leerie/
        "LEERIE_VERSION": "0.99.test",
        "LEERIE_FLY_APP": "testapp",
        "LEERIE_REPO": str(leerie_repo),
        "USER_REPO": str(user_repo),
        "LEERIE_STATE_HOST_DIR": str(tmp_path / "state"),
    }
    result = subprocess.run(["bash", "-c", script], env=env,
                            capture_output=True, text=True)
    return result, log, cache_file, tag


def _make_flyctl_stub(tmp_path: Path, app: str = "testapp") -> Path:
    """Create a flyctl stub that returns app-list JSON and exits 0."""
    case_body = (
        f'if [ "$SUB" = "apps" ]; then\n'
        f'  printf \'[{{"Name":"{app}"}}]\\n\'\n'
        '  exit 0\n'
        'fi\n'
        'exit 0\n'
    )
    return make_fake_flyctl(tmp_path, case_body)


def test_ensure_image_base_cache_hit(tmp_path):
    """A cached digest for the tag: build-push is not called, and the
    machine starts from that digest."""
    result, bp_log, _, _ = _run_ensure(
        tmp_path, per_repo=False, cache=f"{BASE_TAG} {BASE_DIGEST}\n")
    assert result.returncode == 0, result.stderr
    assert not bp_log.exists(), "build-push should not have been called on cache hit"
    assert f"ref=registry.fly.io/testapp@{BASE_DIGEST}" in result.stdout


def test_ensure_image_base_cache_miss_calls_build_push(tmp_path):
    """On a miss, build-push.sh pushes exactly the tag and reports its
    digest, which is recorded and started from."""
    result, bp_log, cache_file, _ = _run_ensure(tmp_path, per_repo=False)
    assert result.returncode == 0, result.stderr
    log = bp_log.read_text()
    assert "--push" in log
    assert "--app testapp" in log
    assert f"--tag {BASE_TAG} --digest-file " in log
    assert cache_file.read_text() == f"{BASE_TAG} {BASE_DIGEST}\n"
    assert f"ref=registry.fly.io/testapp@{BASE_DIGEST}" in result.stdout


def test_ensure_image_base_build_push_failure(tmp_path):
    """When build-push.sh fails, ensure_image fails and records nothing."""
    result, _, cache_file, _ = _run_ensure(tmp_path, per_repo=False, rc=1)
    assert result.returncode != 0
    assert not cache_file.exists()


def test_ensure_image_per_repo_pushes_its_own_tag_from_the_base_digest(
        tmp_path):
    """The repository image is pushed under its own tag, never the base's,
    built FROM the base's digest, with its layout checked against that
    base; the machine starts from the digest pushed."""
    cache = f"{BASE_TAG} {BASE_DIGEST}\n"
    result, bp_log, cache_file, tag = _run_ensure(tmp_path, per_repo=True,
                                                  cache=cache)
    assert result.returncode == 0, result.stderr
    invocations = bp_log.read_text().strip().split("\n")
    assert len(invocations) == 2, invocations
    stage, final = invocations
    base_ref = f"registry.fly.io/testapp@{BASE_DIGEST}"
    # Two builds (DESIGN §6 *One image, two roots*). The repository's
    # Dockerfile as committed, from the committed tree, on the base's
    # digest, pushed under its own tag, never started here, no target.
    assert re.search(r"--dockerfile \S+/\.image-build/b\.\d+\.\w+/repo/Dockerfile",
                     stage)
    assert re.search(r"--context \S+/\.image-build/b\.\d+\.\w+/context", stage)
    assert f"--build-arg BASE_IMAGE={base_ref}" in stage
    assert "--no-run" in stage and "--layout-base" not in stage
    assert f"--tag {tag}-repo --digest-file " in stage
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
    assert (tmp_path / "repo-dockerfile.copy").read_text() == df_content
    # Then leerie's own, FROM the base's digest and the stage's, its layout
    # checked against that base; it alone carries the per-repo tag.
    assert re.search(r"--dockerfile \S+/\.image-build/b\.\d+\.\w+/wrap/Dockerfile",
                     final)
    assert re.search(r"--context \S+/\.image-build/b\.\d+\.\w+/wrap( |$)", final)
    assert f"--layout-base {base_ref}" in final and "--no-run" not in final
    assert f"--tag {tag} --digest-file " in final
    assert "--target" not in stage + final
    assert f"--tag {BASE_TAG} " not in stage + final
    wrapper = (tmp_path / "dockerfile.copy").read_text().splitlines()
    assert f"FROM {base_ref} AS leerie-baseenv" in wrapper
    assert (f"FROM registry.fly.io/testapp@{STAGE_DIGEST} AS leerie-stage"
            in wrapper)
    assert wrapper[-2:] == [f"FROM {base_ref}",
                            "COPY --from=leerie-stage / /opt/leerie-worker-root/"]
    assert cache_file.read_text() == (
        cache + f"{tag} {FLY_REPO_DIGEST} {BASE_DIGEST} {LAYOUT}\n")
    assert f"ref=registry.fly.io/testapp@{FLY_REPO_DIGEST}" in result.stdout
    # The build directory is removed after the push.
    assert not list((tmp_path / "state" / ".image-build").glob("b.*"))


def test_ensure_image_per_repo_ensures_base_first_when_not_cached(tmp_path):
    """No cached base digest: the base is pushed under the base tag first,
    then the repository image under its own."""
    result, bp_log, cache_file, tag = _run_ensure(tmp_path, per_repo=True)
    assert result.returncode == 0, result.stderr
    invocations = bp_log.read_text().strip().split("\n")
    assert len(invocations) == 3, invocations
    assert "--dockerfile" not in invocations[0], invocations[0]
    assert f"--tag {BASE_TAG} --digest-file " in invocations[0]
    assert "--dockerfile" in invocations[1], invocations[1]
    assert f"--tag {tag}-repo --digest-file " in invocations[1]
    assert f"--tag {tag} --digest-file " in invocations[2]
    assert cache_file.read_text() == (
        f"{BASE_TAG} {BASE_DIGEST}\n"
        f"{tag} {FLY_REPO_DIGEST} {BASE_DIGEST} {LAYOUT}\n")


def test_ensure_image_per_repo_cache_hit_skips_build(tmp_path):
    """The repository image cached on the base digest cached now: no build,
    and the machine starts from its digest."""
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
    tag = f"{BASE_TAG}-{fly_repo_hash(df_content.encode())}"
    result, bp_log, _, _ = _run_ensure(
        tmp_path, per_repo=True,
        cache=(f"{BASE_TAG} {BASE_DIGEST}\n"
               f"{tag} {FLY_REPO_DIGEST} {BASE_DIGEST} {LAYOUT}\n"))
    assert result.returncode == 0, result.stderr
    assert not bp_log.exists(), bp_log.read_text()
    assert f"ref=registry.fly.io/testapp@{FLY_REPO_DIGEST}" in result.stdout


def test_ensure_image_per_repo_on_another_base_is_rebuilt(tmp_path):
    """A repository image cached on a base digest other than the one cached
    now is built again on the current base."""
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
    tag = f"{BASE_TAG}-{fly_repo_hash(df_content.encode())}"
    result, bp_log, _, _ = _run_ensure(
        tmp_path, per_repo=True,
        cache=(f"{BASE_TAG} {BASE_DIGEST}\n"
               f"{tag} {OTHER_DIGEST} {OTHER_DIGEST} {LAYOUT}\n"))
    assert result.returncode == 0, result.stderr
    assert f"--tag {tag} --digest-file " in bp_log.read_text()
    assert f"ref=registry.fly.io/testapp@{FLY_REPO_DIGEST}" in result.stdout


def test_ensure_image_per_repo_in_another_layout_is_rebuilt(tmp_path):
    """A repository image cached in a layout other than REPO_IMAGE_LAYOUT
    (or with none recorded) is built again, never reused (review round 29,
    execution L2)."""
    df_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
    tag = f"{BASE_TAG}-{fly_repo_hash(df_content.encode())}"
    for stale in (f"{tag} {OTHER_DIGEST} {BASE_DIGEST} worker-root-0\n",
                  f"{tag} {OTHER_DIGEST} {BASE_DIGEST}\n"):
        sub = tmp_path / str(len(stale))
        sub.mkdir()
        result, bp_log, _, _ = _run_ensure(
            sub, per_repo=True, cache=f"{BASE_TAG} {BASE_DIGEST}\n" + stale)
        assert result.returncode == 0, result.stderr
        assert f"--tag {tag} --digest-file " in bp_log.read_text()
        assert f"ref=registry.fly.io/testapp@{FLY_REPO_DIGEST}" in \
            result.stdout


def test_ensure_image_per_repo_build_push_failure(tmp_path):
    """When build-push.sh fails on per-repo build, ensure_image fails and
    records nothing for the repository image."""
    result, _, cache_file, tag = _run_ensure(
        tmp_path, per_repo=True, rc=1,
        cache=f"{BASE_TAG} {BASE_DIGEST}\n")
    assert result.returncode != 0
    assert tag not in cache_file.read_text()


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
    repo_hash = fly_repo_hash(df_content.encode())

    launcher = _launcher_text()
    build_block = repo_build_functions(launcher)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    body = (
        "_set_fly_per_repo_image\n"
        'echo "tag=$(resolve_fly_image_tag)"\n'
    )
    script = _HARNESS_PREFIX + build_block + "\n" + fly_block + "\n" + body

    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
        "LEERIE_VERSION": "1.5.0",
        "LEERIE_FLY_APP": "myapp",
        "USER_REPO": str(user_repo),
        "LEERIE_STATE_HOST_DIR": str(tmp_path / "state"),
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
    build_block = repo_build_functions(launcher)
    fly_block = _extract_block(launcher, _FLY_MARKER_START, _FLY_MARKER_END)

    body = (
        "_set_fly_per_repo_image\n"
        'echo "tag=$(resolve_fly_image_tag)"\n'
    )
    script = _HARNESS_PREFIX + build_block + "\n" + fly_block + "\n" + body

    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
        "LEERIE_VERSION": "1.5.0",
        "LEERIE_FLY_APP": "myapp",
        "USER_REPO": str(user_repo),
        "LEERIE_STATE_HOST_DIR": str(tmp_path / "state"),
    }
    result = subprocess.run(["bash", "-c", script], env=env,
                             capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "registry.fly.io/myapp:1.5.0", tag


def test_the_machine_starts_from_the_digest_ensure_image_pushed(tmp_path):
    """The launcher's Fly branch, verbatim from `_set_fly_per_repo_image`
    through the build directory's removal: after `ensure_image`, the image
    handed to provision_machine (FLY_IMAGE_TAG) is the digest reference it
    set, not the tag (review round 29, isolation F1)."""
    launcher = _launcher_text()
    start = launcher.index("  _set_fly_per_repo_image\n  FLY_IMAGE_TAG=")
    end = launcher.index("  _repo_build_done\n", start) + len(
        "  _repo_build_done\n")
    wiring = launcher[start:end]
    digest = "sha256:" + "f" * 64
    body = (
        "_set_fly_per_repo_image() { :; }\n"
        "_repo_build_done() { :; }\n"
        'resolve_fly_image_tag() { echo "registry.fly.io/testapp:1.0"; }\n'
        "ensure_image() {\n"
        '  [ "$1" = "registry.fly.io/testapp:1.0" ] || return 1\n'
        f'  FLY_IMAGE_REF="registry.fly.io/testapp@{digest}"\n'
        "}\n"
        'NO_AUTO_PUBLISH=false\n'
        + wiring
        + 'echo "machine=$FLY_IMAGE_TAG"\n'
        + "bash -c 'echo \"exported=$FLY_IMAGE_TAG\"'\n")
    result = subprocess.run(["bash", "-c", "set -euo pipefail\n" + body],
                            env={"PATH": "/usr/bin:/bin"},
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert f"machine=registry.fly.io/testapp@{digest}" in result.stdout
    assert f"exported=registry.fly.io/testapp@{digest}" in result.stdout
