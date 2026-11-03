"""The repository image: the repository's Dockerfile built as it is, then
leerie's own Dockerfile, whose final stage is leerie's base and one copy of
the first build's image (DESIGN §6 *The orchestrator's root is leerie's; a
repository's root is only a confined process's*, *One image, two roots*;
review round 28, isolation E1; review rounds 32 and 33).

E1's proof turned around: a lockfile whose root `preinstall` plants a `.pth`
in `/usr/lib/python3/dist-packages` ran, as root, in the build of the image
the orchestrator runs in, and the orchestrator's interpreter (and the root
cgroup broker's) ran it. Here the launcher's own helpers run verbatim:
`_repo_image_dockerfile` copies the repository's file unchanged,
`_repo_image_wrap` writes leerie's file with no repository text in it,
`build_repo_image` gives the first build the repository's bytes and no
target and the second the first's image as a named context, and
`_check_repo_image` refuses an image that is not the base plus one layer.
Rounds 32 and 33 found, three times over, readings in which leerie's lexer
and BuildKit's differed and a rewrite of the repository's file chose the
wrong stage; each such shape is here, given to the build byte for byte.
With `LEERIE_TEST_REPO_IMAGE=<base image>` (a host with nerdctl and that
image), the real builds run too: a two-stage repository file's final stage
is the worker root, and the planted `.pth` is in the worker root and the
orchestrator's interpreter, as leerie and as root, does not run it. Each
copies a whole root (minutes, about 2.4 GB), so they are opt-in."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import BUILDCTL_STUB, FRONTEND_SH_SOURCE, LOG_SH_SOURCE
from tests.repo_image_block_extract import repo_build_functions

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
BASE = "leerie:9.9.9"

_PTH = 'import sys; sys.stderr.write("R28-PLANT" + chr(10))'
PLANT_PKG = json.dumps({"name": "victim", "version": "1.0.0", "scripts": {
    "preinstall": "node -e 'require(\"fs\").writeFileSync(\"/usr/lib/"
                  "python3/dist-packages/zz-plant.pth\", " + json.dumps(_PTH)
                  .replace("'", "") + " + String.fromCharCode(10))'"}})
PLANT_LOCK = ('{"name":"victim","version":"1.0.0","lockfileVersion":3,'
              '"requires":true,"packages":{"":{"name":"victim",'
              '"version":"1.0.0","hasInstallScript":true}}}')


def _fns() -> str:
    return repo_build_functions(LAUNCHER.read_text())


def _launcher_fn(name: str) -> str:
    text = LAUNCHER.read_text()
    fn = text[text.index(f"{name}() {{"):]
    return fn[:fn.index("\n}\n") + 3]


def _write(repo: Path, dockerfile: str | bytes,
           out: Path) -> subprocess.CompletedProcess:
    """`_repo_image_dockerfile <out>`: what it copied is
    `<out>/repo/Dockerfile`."""
    (repo / ".leerie").mkdir(parents=True, exist_ok=True)
    df = repo / ".leerie" / "Dockerfile"
    if not df.is_symlink():
        df.write_bytes(dockerfile if isinstance(dockerfile, bytes)
                       else dockerfile.encode())
    out.mkdir(parents=True, exist_ok=True)
    script = ("set -euo pipefail\n" + 'remote_log() { echo "$*" >&2; }\n'
              + _fns() + f'\n_repo_image_dockerfile "{out}"\n')
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin",
                               "USER_REPO": str(repo)})


GENERATED = ("# leerie-generated: do not edit (regenerated from "
             ".leerie/config.toml)\nARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
             "USER root\nCOPY package-lock.json package.json ./\n"
             "RUN npm ci\n")


def test_leeries_dockerfile_holds_no_repository_text(tmp_path):
    """The second build's file is leerie's alone: the base's and the worker
    stage's environments recorded, and a final stage, its default target,
    that is the base and one copy of the worker stage."""
    out = tmp_path / "wrap" / "Dockerfile"
    r = subprocess.run(
        ["bash", "-c", _fns() + f'\n_repo_image_wrap "{out}" {BASE} '
         "leerie-worker\n"], capture_output=True, text=True, timeout=60,
        env={"PATH": "/usr/bin:/bin"})
    assert r.returncode == 0, r.stderr
    assert out.read_text().splitlines() == [
        "# leerie: the repository image (DESIGN §6 *One image, two roots*).",
        f"FROM {BASE} AS leerie-baseenv",
        'RUN ["/bin/sh", "-c", "env -0 > /.leerie-base-env"]',
        "FROM leerie-worker AS leerie-stage",
        "USER root",
        "COPY --from=leerie-baseenv /.leerie-base-env /.leerie-base-env",
        'RUN ["/bin/sh", "-c", "env -0 > /.leerie-worker-env"]',
        f"FROM {BASE}",
        "COPY --from=leerie-stage / /opt/leerie-worker-root/"]


def test_a_linked_dockerfile_is_refused(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".leerie").mkdir(parents=True)
    (tmp_path / "elsewhere").write_text("FROM x\n")
    (repo / ".leerie" / "Dockerfile").symlink_to(tmp_path / "elsewhere")
    r = _write(repo, "", tmp_path / "out")
    assert r.returncode != 0
    assert not (tmp_path / "out" / "repo" / "Dockerfile").exists()


def _check(layers_base: str, layers_repo: str, cfg_base: str, cfg_repo: str,
           tmp_path: Path) -> subprocess.CompletedProcess:
    stub = f'''
nerdctl() {{
  [ "$1" = --namespace ] && shift 2
  if [ "$1" = image ] && [ "$2" = inspect ]; then
    case "$4" in
      *RootFS*) if [ "$5" = "{BASE}" ]; then echo '{layers_base}'; else echo '{layers_repo}'; fi ;;
      *Config*) if [ "$5" = "{BASE}" ]; then echo '{cfg_base}'; else echo '{cfg_repo}'; fi ;;
    esac
    return 0
  fi
  if [ "$1" = image ] && [ "$2" = rm ]; then echo "removed $3" >> "{tmp_path}/rm.log"; fi
  return 0
}}
'''
    script = ("set -euo pipefail\n" + FRONTEND_SH_SOURCE
              + 'remote_log() { echo "$*" >&2; }\n'
              + stub + _fns() + f'\nIMAGE_TAG={BASE}\n'
              '_check_repo_image leerie-repo/x:1 && echo OK\n')
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60, env={"PATH": "/usr/bin:/bin"})


CFG = '{"Entrypoint":["/opt/leerie-image/scripts/container-entry.sh"],"Image":"%s"}'


@pytest.mark.parametrize("layers, cfg, ok", [
    ('["b1","b2","w"]', CFG % "leerie-repo/x:1", True),
    ('["b1","b2"]', CFG % "leerie-repo/x:1", False),            # no worker root
    ('["b1","b2","w","x"]', CFG % "leerie-repo/x:1", False),    # a layer more
    ('["b1","zz","w"]', CFG % "leerie-repo/x:1", False),        # not the base
    ('["b1","b2","w"]', '{"Entrypoint":["/bin/sh"],"Image":"x"}', False),
])
def test_the_image_must_be_the_base_plus_one_layer(tmp_path, layers, cfg, ok):
    r = _check('["b1","b2"]', layers, CFG % BASE, cfg, tmp_path)
    assert ("OK" in r.stdout) is ok, r.stderr
    assert (tmp_path / "rm.log").exists() is not ok


# --- the build is given the repository's bytes, unchanged ------------------
# Review rounds 32 and 33: each shape below made leerie's rewrite of the
# repository's file diverge from BuildKit — a stage chosen from a heredoc's
# body or an earlier stage, a parser directive lost or applied to leerie's
# lines, a BOM left mid-file. The first build is now handed the file as it
# is, byte for byte, with no `--target` (BuildKit's own default target), and
# the second build is handed only leerie's file, its worker stage the first
# build's one image.

ROUND_SHAPES = {
    # Round 32 X1: an arithmetic shift and a here-string are no heredoc.
    "shift": b"FROM a AS builder\nRUN echo $((1<<N)) > /s\n"
             b"FROM b AS runtime\nRUN true\n",
    "here-string": b"FROM a AS builder\nRUN cat <<<hello > /h\n"
                   b"FROM b AS runtime\nRUN true\n",
    # Round 33 F1: a modifier hid the heredoc, so its body's FROM was "the
    # last FROM".
    "modifier-decoy": b"FROM a AS runtime\nRUN echo ${X//a/b} <<EOF\n"
                      b"FROM users\nEOF\n",
    "offset-decoy": b"FROM a AS runtime\nRUN echo ${X:1:2} <<EOF\n"
                    b"FROM users\nEOF\n",
    "error-decoy": b"FROM a AS runtime\nRUN echo ${X:?m} <<EOF\n"
                   b"FROM users\nEOF\n",
    # Round 33 F2: lower-case instructions under check=error=true.
    "check-lowercase": b"# check=error=true\nfrom a as build\nrun true\n"
                       b"from b\ncopy --from=build /x /x\n",
    # Round 33 F3: a byte-order mark on line 1.
    "bom": b"\xef\xbb\xbf# escape=`\nFROM a\nRUN echo `\n  hi\n",
    "bom-from": b"\xef\xbb\xbfFROM a\nRUN true\n",
    # Round 32 X2: the escape directive.
    "escape-backtick": b"# escape=`\nFROM a\nCOPY a `\n  /a\n",
    # Round 33 isolation X1r: \x1c is no whitespace to Go.
    "fs-separator": b"FROM a AS builder\nCOPY a.txt /f\x1c<<EOF\n"
                    b"FROM users\nEOF\nFROM b AS runtime\n",
    # The repository's own frontend applies to its own build.
    "syntax": b"# syntax=docker/dockerfile:1\nFROM a\nRUN true\n",
    # A file that leaves a heredoc or a continuation open.
    "open-heredoc": b"FROM a\nRUN <<EOF\necho\n",
    "open-continuation": b"FROM a\nRUN echo \\\n",
}

_BUILD_STUB = r'''
nerdctl() {
  local _f="" _o="" _ctx="" _bc="" _t="" _n=0
  [ "${1:-}" = "--namespace" ] && shift 2
  case "$1" in
    build) shift ;;
    image)
      case "$4" in
        *RootFS*) if [ "$5" = "$IMAGE_TAG" ]; then echo '["b"]'; else echo '["b","w"]'; fi ;;
        *Config*) echo '{"Entrypoint":["/e"],"Image":"'"$5"'"}' ;;
      esac
      return 0 ;;
    *) return 1 ;;
  esac
  while [ $# -gt 0 ]; do
    case "$1" in
      -f) _f="$2"; shift 2 ;;
      -o) _o="$2"; shift 2 ;;
      -t) _t="$2"; shift 2 ;;
      --build-context) _bc="$2"; shift 2 ;;
      --target) printf 'TARGET %s\n' "$2" >> "$LOG"; shift 2 ;;
      --build-arg) shift 2 ;;
      *) _ctx="$1"; shift ;;
    esac
  done
  read -r _n < "$LOG.n" 2>/dev/null || _n=0
  _n=$((_n + 1))
  printf '%s\n' "$_n" > "$LOG.n"
  /usr/bin/cp "$_f" "$LOG.df$_n"
  printf 'BUILD %s f=%s o=%s ctx=%s bc=%s t=%s\n' "$_n" "$_f" "$_o" "$_ctx" "$_bc" "$_t" >> "$LOG"
  if [ -n "$_o" ]; then
    _o="${_o#*dest=}"; _o="${_o%%\",tar=false}"
    /usr/bin/mkdir -p "$_o"
    printf '{"manifests":[{"digest":"sha256:%s"}]}' \
      "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa" \
      > "$_o/index.json"
  fi
}
'''


def _build(tmp_path: Path, dockerfile: bytes) -> tuple[subprocess.CompletedProcess, Path]:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "Dockerfile").write_bytes(dockerfile)
    (repo / "a.txt").write_text("a\n")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@x", "-c",
                    "user.name=t", "commit", "-qm", "c"], check=True,
                   capture_output=True)
    state = tmp_path / "state"
    state.mkdir()
    log = tmp_path / "build.log"
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + BUILDCTL_STUB + 'remote_log() { echo "$*" >&2; }\n'
              + _BUILD_STUB + _fns()
              + "\n" + _launcher_fn("build_repo_image")
              + f"IMAGE_TAG={BASE}\n_repo_image_prepare\n"
              'echo "BUILDDIR $_REPO_BUILD_DIR"\n'
              "build_repo_image leerie-repo/x-1:9.9.9\n")
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "USER_REPO": str(repo),
                            "LEERIE_STATE_HOST_DIR": str(state),
                            "LOG": str(log)})
    return r, log


@pytest.mark.parametrize("shape", sorted(ROUND_SHAPES))
def test_the_first_build_is_given_the_repositorys_bytes_unchanged(
        tmp_path, shape):
    dockerfile = ROUND_SHAPES[shape]
    r, log = _build(tmp_path, dockerfile)
    assert r.returncode == 0, r.stderr
    builds = [l for l in log.read_text().splitlines()]
    assert len(builds) == 2 and not any(l.startswith("TARGET") for l in builds)
    build_dir = r.stdout.split("BUILDDIR ", 1)[1].split()[0]
    # The first build: the repository's file, as committed, from the
    # committed context, its own default target, out to an OCI layout.
    assert Path(f"{log}.df1").read_bytes() == dockerfile
    first = builds[0]
    assert f"f={build_dir}/repo/Dockerfile " in first
    assert f'o=type=oci,"dest={build_dir}/worker",tar=false ' in first
    assert f"ctx={build_dir}/context " in first and "bc= " in first
    # The second: leerie's file alone, its worker stage the layout.
    second = builds[1]
    assert f"f={build_dir}/wrap/Dockerfile " in second
    assert f"bc=leerie-worker=oci-layout://{build_dir}/worker " in second
    assert second.endswith("t=leerie-repo/x-1:9.9.9")
    wrap = Path(f"{log}.df2").read_text()
    assert "FROM leerie-worker AS leerie-stage\n" in wrap
    assert dockerfile.decode("utf-8", "replace") not in wrap
    # Nothing is left of the build directory's layout.
    assert not Path(build_dir, "worker").exists()


def test_a_first_build_that_leaves_other_than_one_image_stops(tmp_path):
    """`_repo_layout_digest`: the layout the second build reads must hold
    the first build's one image and nothing else."""
    lay = tmp_path / "lay"
    lay.mkdir()
    d = "sha256:" + "b" * 64
    for manifests, ok in ((f'[{{"digest":"{d}"}}]', True),
                          (f'[{{"digest":"{d}"}},{{"digest":"{d}"}}]', False),
                          ("[]", False), ('[{"digest":"sha256:zz"}]', False)):
        (lay / "index.json").write_text(f'{{"manifests":{manifests}}}')
        r = subprocess.run(["bash", "-c", _fns() + f'\n_repo_layout_digest "{lay}"\n'],
                           capture_output=True, text=True, timeout=60,
                           env={"PATH": "/usr/bin:/bin"})
        assert (r.returncode == 0 and r.stdout.strip() == d) is ok, manifests


# --- the real builds (opt-in) -----------------------------------------------

_REAL = os.environ.get("LEERIE_TEST_REPO_IMAGE")
_real = pytest.mark.skipif(
    not _REAL or not shutil.which("nerdctl"),
    reason="LEERIE_TEST_REPO_IMAGE=<base image, in the `leerie` containerd "
           "namespace> builds a real repository image (minutes, ~2.4 GB)")


def _real_build(tmp_path: Path, files: dict[str, str], dockerfile: str,
                tag: str) -> subprocess.CompletedProcess:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    for rel, text in files.items():
        (repo / rel).write_text(text)
    (repo / ".env").write_text("untracked-secret\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "Dockerfile").write_text(dockerfile)
    subprocess.run(["git", "-C", str(repo), "add", *files,
                    ".leerie/Dockerfile"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c",
                    "user.name=a", "commit", "-qm", "i"], check=True)
    state = tmp_path / "state"
    state.mkdir()
    script = ("set -euo pipefail\n" + LOG_SH_SOURCE + FRONTEND_SH_SOURCE
              + 'remote_log() { echo "$*" >&2; }\n' + _fns() + "\n"
              + _launcher_fn("build_repo_image")
              + f"IMAGE_TAG={_REAL}\n_repo_image_prepare\n"
              f"build_repo_image {tag}\n"
              f"_check_repo_image {tag} && echo CHECKED\n_repo_build_done\n")
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=3600,
                          env={**os.environ, "USER_REPO": str(repo),
                               "LEERIE_STATE_HOST_DIR": str(state)})


def _in_image(tag: str, cmd: str, user: str = "0") -> str:
    return subprocess.run(
        ["nerdctl", "run", "--rm", "--network", "none", "--user", user,
         "--entrypoint", "/bin/sh", tag, "-c", cmd],
        capture_output=True, text=True, timeout=300).stdout


# A two-stage repository file whose final stage is not its first, with a
# decoy `FROM` in a heredoc's body (round 33 F1) and lower-case
# instructions under `# check=error=true` (round 33 F2): built as it is,
# the worker root is the final stage.
TWO_STAGE = ("# check=error=true\narg BASE_IMAGE=scratch\n"
             "from $BASE_IMAGE as early\nrun echo early > /early-stage\n"
             "from $BASE_IMAGE as final\n"
             "copy --from=early /early-stage /from-early\n"
             "run cat <<EOF > /decoy\nfrom $BASE_IMAGE as decoy\nEOF\n"
             "env LEERIE_TEST_FINAL=final-stage\n"
             "run echo final > /final-stage\n")


@_real
def test_the_worker_root_is_the_repositorys_final_stage(tmp_path):
    tag = "leerie-test-two-stage:1"
    try:
        r = _real_build(tmp_path, {}, TWO_STAGE, tag)
        assert r.returncode == 0 and "CHECKED" in r.stdout, r.stderr
        out = _in_image(tag, (
            "cd /opt/leerie-worker-root && ls final-stage from-early decoy; "
            "test -e early-stage || echo no-early; "
            "test -e /final-stage || echo base-clean; "
            "tr '\\0' '\\n' < .leerie-worker-env | grep LEERIE_TEST_FINAL"))
        assert "final-stage\nfrom-early\n" in out.replace("decoy\n", "")
        assert "no-early" in out and "base-clean" in out
        assert "LEERIE_TEST_FINAL=final-stage" in out
    finally:
        subprocess.run(["nerdctl", "image", "rm", tag], capture_output=True)


@_real
def test_a_planted_pth_never_reaches_the_orchestrators_interpreter(tmp_path):
    tag = "leerie-test-e1:1"
    try:
        r = _real_build(tmp_path, {"package.json": PLANT_PKG,
                                   "package-lock.json": PLANT_LOCK},
                        GENERATED, tag)
        assert r.returncode == 0 and "CHECKED" in r.stdout, r.stderr
        for user in ("leerie", "0"):
            o = subprocess.run(
                ["nerdctl", "run", "--rm", "--network", "none", "--user",
                 user, "--entrypoint", "python3", tag, "-c", "print('ran')"],
                capture_output=True, text=True, timeout=300)
            assert "ran" in o.stdout and "R28-PLANT" not in o.stderr, o
        w = _in_image(tag, (
            "test -f /opt/leerie-worker-root/usr/lib/python3/dist-packages/"
            "zz-plant.pth && echo planted-in-worker-root; "
            "test -e /usr/lib/python3/dist-packages/zz-plant.pth || "
            "echo base-clean; find /opt/leerie-worker-root -maxdepth 2 "
            "-name .env"))
        assert "planted-in-worker-root" in w
        assert "base-clean" in w and ".env" not in w
    finally:
        subprocess.run(["nerdctl", "image", "rm", tag], capture_output=True)


# --- the build context is what the Dockerfile reads, and keys the image ----
# (DESIGN §6 *A repository image is the one built from what it reads*;
# review round 30 isolation R1: the image was built from `git archive HEAD`
# but reused on the Dockerfile's bytes alone, so a branch's COPY'd files
# stayed in the image a later run on another branch reused.)

def _commit(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    for args in (["add", "-A"], ["-c", "user.email=t@x", "-c",
                                 "user.name=t", "commit", "-qm", "c"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True,
                       capture_output=True)


def _context(repo: Path, state: Path) -> tuple[list[str], str, list[str]]:
    """(context paths, context sha, files in the extracted context), from
    the launcher's own `_repo_image_prepare`."""
    script = ("set -euo pipefail\n" + 'remote_log() { echo "$*" >&2; }\n'
              + _fns() + '\n_repo_image_prepare\n'
              'tr "\\0" "\\n" < "$_REPO_BUILD_DIR/context-paths"\n'
              'echo "CTX $_REPO_CTX_SHA"\n'
              '(cd "$_REPO_BUILD_CTX" && find . -type f | sort)\n'
              "_repo_build_done\n")
    state.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "USER_REPO": str(repo),
                            "LEERIE_STATE_HOST_DIR": str(state)})
    assert r.returncode == 0, r.stderr
    head, _, files = r.stdout.partition("CTX ")
    sha, _, files = files.partition("\n")
    return ([p for p in head.splitlines() if p], sha,
            [f[2:] for f in files.splitlines()])


def _repo(tmp_path: Path, dockerfile: str) -> Path:
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)],
                   check=True)
    _commit(repo, {".leerie/Dockerfile": dockerfile,
                   "package.json": "{}\n", "package-lock.json": "{}\n",
                   "src/a.ts": "a\n", "docs/d.md": "d\n",
                   "README.md": "r\n", ".dockerignore": "node_modules\n"})
    return repo


READS = ("FROM node:20 AS deps\nWORKDIR /app\nCOPY package*.json ./\n"
         "COPY --chown=1:1 src/ /app/src/\n"
         "RUN --mount=type=bind,source=docs,target=/d true\n"
         "COPY --from=deps /x /y\nCOPY <<EOT /etc/x\nhi\nEOT\n"
         "ADD https://example.com/x /x\n")


def test_the_context_is_what_the_dockerfile_reads(tmp_path):
    paths, _sha, files = _context(_repo(tmp_path, READS), tmp_path / "st")
    assert paths == [".dockerignore", "docs", "package-lock.json",
                     "package.json", "src"]
    assert files == [".dockerignore", "docs/d.md", "package-lock.json",
                     "package.json", "src/a.ts"]


def test_the_key_moves_with_what_the_image_reads_and_nothing_else(tmp_path):
    """The reviewer's two branches: the same Dockerfile bytes, different
    COPY'd content, now different keys; a file no instruction reads leaves
    the key (and the image) as it was."""
    repo = _repo(tmp_path, READS)
    _p, base, _f = _context(repo, tmp_path / "st")
    _commit(repo, {"README.md": "changed\n"})
    assert _context(repo, tmp_path / "st")[1] == base
    _commit(repo, {"src/a.ts": "planted\n"})
    planted = _context(repo, tmp_path / "st")[1]
    assert planted != base
    _commit(repo, {"src/a.ts": "a\n"})
    assert _context(repo, tmp_path / "st")[1] == base


def test_the_local_key_and_the_fly_tag_carry_the_context():
    text = LAUNCHER.read_text()
    assert ('_cur_hash="${LEERIE_VERSION}:${REPO_IMAGE_LAYOUT}:${_base_id}:'
            '${_cur_df_sha}:${_REPO_CTX_SHA}"') in text
    fly = text[text.index("_set_fly_per_repo_image() {"):]
    fly = fly[:fly.index("\n}\n")]
    assert '"$_REPO_DF_SHA:$_REPO_CTX_SHA"' in fly


@pytest.mark.parametrize("copy, paths", [
    ("COPY . /app", ["."]),
    ("COPY ./ /app", ["."]),
    ("COPY $SRC /app", ["."]),
    ("COPY src/$X/a.ts /app", ["src"]),
    ("COPY */a.ts /app", ["."]),
    ("COPY [\"src/a.ts\", \"/app/\"]", ["src/a.ts"]),
    ("COPY ../x /app", ["."]),
    ("COPY absent.txt /app", []),
    ("ONBUILD COPY docs /d", ["docs"]),
    ("RUN --mount=target=/w true", ["."]),
    ("RUN --mount=type=cache,target=/c true", []),
    ("COPY package?json /app", ["package.json"]),
    ("COPY [p]ackage-lock.json /app", ["package-lock.json"]),
])
def test_each_source_form_names_its_context(tmp_path, copy, paths):
    got, _sha, _f = _context(_repo(tmp_path, f"FROM node:20\n{copy}\n"),
                             tmp_path / "st")
    assert got == (paths if paths == ["."]
                   else sorted(paths + [".dockerignore"]))


def test_a_whole_tree_copy_keys_on_the_whole_tree(tmp_path):
    repo = _repo(tmp_path, "FROM node:20\nCOPY . /app\n")
    base = _context(repo, tmp_path / "st")[1]
    _commit(repo, {"README.md": "changed\n"})
    assert _context(repo, tmp_path / "st")[1] != base


# Review round 31, execution 6: the reading followed BuildKit's lexing only
# approximately, so each shape below read short and a build that passed with
# the whole tree failed with "not found". Each is lexed as BuildKit lexes it.
@pytest.mark.parametrize("dockerfile, paths", [
    # Any whitespace ends the instruction word.
    ("FROM node:20\nCOPY\tsrc/a.ts /app/\n", ["src/a.ts"]),
    ("FROM node:20\nCOPY --chown=1:1\tsrc/ /app/\n", ["src"]),
    # An empty line inside a continuation is skipped, not the end of it.
    ("FROM node:20\nCOPY package.json \\\n\n  docs /app/\n",
     ["docs", "package.json"]),
    # A comment line inside a continuation too, and a whitespace-only one.
    ("FROM node:20\nCOPY package.json \\\n  # c\n \t\n  docs /app/\n",
     ["docs", "package.json"]),
    # A continuation is joined with no separator.
    ("FROM node:20\nCOPY READ\\\nME.md /app/\n", ["README.md"]),
    # `# escape=` sets the continuation character.
    ("# escape=`\nFROM node:20\nCOPY package.json `\n  docs /app/\n",
     ["docs", "package.json"]),
])
def test_the_dockerfile_is_lexed_as_buildkit_lexes_it(tmp_path, dockerfile,
                                                      paths):
    got, _sha, _f = _context(_repo(tmp_path, dockerfile), tmp_path / "st")
    assert got == sorted(paths + [".dockerignore"])


def _linked_repo(tmp_path: Path, dockerfile: str) -> Path:
    repo = _repo(tmp_path, dockerfile)
    _commit(repo, {"vendor/lib/v.txt": "v\n", "vendor/x.txt": "x\n"})
    os.symlink("vendor/lib", repo / "lib")
    os.symlink("../../outside", repo / "vendor" / "away")
    os.symlink("/etc", repo / "abs")
    _commit(repo, {})
    return repo


@pytest.mark.parametrize("copy, paths", [
    # A committed link to a directory: the link and what it names.
    ("COPY lib/ /x/", ["lib", "vendor/lib"]),
    # A path through a linked directory.
    ("COPY lib/v.txt /x/", ["lib", "vendor/lib/v.txt"]),
    # A wildcard that matches a link.
    ("COPY li? /x/", ["lib", "vendor/lib"]),
    # A target leaving the tree adds nothing past the link.
    ("COPY vendor/away /x/", ["vendor/away"]),
    ("COPY abs /x/", ["abs"]),
])
def test_a_source_through_a_committed_link_reads_what_it_names(
        tmp_path, copy, paths):
    got, _sha, files = _context(
        _linked_repo(tmp_path, f"FROM node:20\n{copy}\n"), tmp_path / "st")
    assert got == sorted(paths + [".dockerignore"])
    if "vendor/lib" in paths:
        assert "vendor/lib/v.txt" in files


# Review rounds 32 and 33 (execution X1, F1; isolation X1r): heredocs the
# context reading must see as BuildKit does. A body is not instructions, so
# a `COPY` in it names nothing; what follows the body does. Before the
# repository's file was built as it is, each of these chose the stage
# leerie copied; now a reading that differed could only widen the context
# or fail the build by name.
_AFTER = "COPY src/a.ts /app/\n"


@pytest.mark.parametrize("line", [
    "RUN echo $((1<<SHIFT)) > /shift",          # arithmetic shift
    "RUN cat <<<hello > /h",                     # here-string
    "RUN echo '<<x' > /q",                       # quoted, single
    'RUN echo "a <<x b" > /q',                   # quoted, double
    "RUN echo a\\<<x > /q",                    # escaped
    'RUN ["sh", "-c", "cat <<EOF"]',             # JSON form holds none
    "RUN echo x<<y",                             # not the word's start
    "COPY README.md /f\x1c<<EOF",               # \x1c is no space to Go
])
def test_a_shift_or_here_string_is_no_heredoc(tmp_path, line):
    got, _sha, _f = _context(
        _repo(tmp_path, f"FROM node:20 AS builder\n{line}\n{_AFTER}"),
        tmp_path / "st")
    assert "src/a.ts" in got


@pytest.mark.parametrize("line, body", [
    ("RUN cat <<END-OF > /x", "COPY docs /nope\nEND-OF"),
    ("RUN cat <<'END OF' > /x", "COPY docs /nope\nEND OF"),
    ('RUN cat <<"E.O.F" > /x', "COPY docs /nope\nE.O.F"),
    ("RUN cat <<E'O'F > /x", "COPY docs /nope\nEOF"),
    ("RUN cat <<-EOF > /x", "\tCOPY docs /nope\n\t\tEOF"),
    ("RUN cat 0<<EOF > /x", "COPY docs /nope\nEOF"),
    ("RUN cat <<A <<B > /x", "COPY docs /nope\nA\nCOPY README.md /no\nB"),
    # Round 33 F1: a modifier on the line does not hide its heredoc.
    ("RUN echo ${X//a/b} <<EOF", "COPY docs /nope\nEOF"),
    ("RUN echo ${X/a/b} <<EOF", "COPY docs /nope\nEOF"),
    ("RUN echo ${X:1:2} <<EOF", "COPY docs /nope\nEOF"),
    ("RUN echo ${X:?m} <<EOF", "COPY docs /nope\nEOF"),
    ("RUN echo ${X##*/} <<EOF", "COPY docs /nope\nEOF"),
])
def test_a_heredoc_name_is_read_as_buildkit_reads_it(tmp_path, line, body):
    """The delimiter is the whole rest of the word, unquoted (`END-OF`
    was read as `END`, and the body ran past the end of the file); a
    `<<-` body's terminator may be indented by tabs."""
    got, _sha, _f = _context(
        _repo(tmp_path, f"FROM node:20 AS builder\n{line}\n{body}\n{_AFTER}"),
        tmp_path / "st")
    assert got == [".dockerignore", "src/a.ts"]


def test_a_heredoc_names_no_context_path_but_a_shifted_copy_does(tmp_path):
    df = ("FROM node:20 AS builder\nRUN echo $((1<<2))\n"
          "FROM node:20 AS runtime\nCOPY <<EOT /etc/x\nhi\nEOT\n"
          "COPY src/a.ts /app/\n")
    got, _sha, _f = _context(_repo(tmp_path, df), tmp_path / "st")
    assert got == [".dockerignore", "src/a.ts"]


@pytest.mark.parametrize("dockerfile, paths", [
    # Round 32 X2: the escape directive sets the continuation character.
    ("# Escape=`\n#check = error=true\n# syntax=docker/dockerfile:1\n"
     "FROM node:20\nCOPY package.json `\n  docs /app/\n",
     ["docs", "package.json"]),
    # A comment, a blank line or an unknown key ends the directives: what
    # follows is a comment, and backtick no continuation.
    ("# a comment\n# escape=`\nFROM node:20\nCOPY package.json `\n"
     "  docs /app/\n", ["package.json"]),
    ("\n# escape=`\nFROM node:20\nCOPY package.json `\n  docs /app/\n",
     ["package.json"]),
    ("# foo=bar\n# escape=`\nFROM node:20\nCOPY package.json `\n"
     "  docs /app/\n", ["package.json"]),
    # Round 33 F3: a byte-order mark is dropped once, so line 1 is still a
    # directive, or an instruction.
    ("\ufeff# escape=`\nFROM node:20\nCOPY package.json `\n  docs /app/\n",
     ["docs", "package.json"]),
    ("\ufeffCOPY src/a.ts /app/\n", ["src/a.ts"]),
    # Lower case is read as BuildKit reads it.
    ("from node:20\ncopy src/a.ts /app/\n", ["src/a.ts"]),
])
def test_the_parser_directives_are_read_as_buildkit_reads_them(
        tmp_path, dockerfile, paths):
    got, _sha, _f = _context(_repo(tmp_path, dockerfile), tmp_path / "st")
    assert got == sorted(paths + [".dockerignore"])
