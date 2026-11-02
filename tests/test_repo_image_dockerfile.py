"""The repository image: the repository's stages, then leerie's, whose final
stage is leerie's base and one copy of the worker stage (DESIGN §6 *The
orchestrator's root is leerie's; a repository's root is only a confined
process's*; review round 28, isolation E1).

E1's proof turned around: a lockfile whose root `preinstall` plants a `.pth`
in `/usr/lib/python3/dist-packages` ran, as root, in the build of the image
the orchestrator runs in, and the orchestrator's interpreter (and the root
cgroup broker's) ran it. Here the launcher's own Dockerfile writer
(`_repo_image_dockerfile`) and image check (`_check_repo_image`) run
verbatim: the repository's lines build only the worker stage, the final
stage is `FROM <base>` and one `COPY` to `/opt/leerie-worker-root/`, a file
that swallows leerie's lines or picks its own frontend builds no final stage
of its own, and an image that is not the base plus one layer is refused.
With `LEERIE_TEST_REPO_IMAGE=<base image>` (a host with nerdctl and that
image), the real build runs too: the planted `.pth` is in the worker root
and the orchestrator's interpreter, as leerie and as root, does not run it.
That build copies a whole root (minutes, about 2.4 GB), so it is opt-in."""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.repo_image_block_extract import repo_build_functions

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
NONCE = "0123456789abcdef"
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


def _write(repo: Path, dockerfile: str, out: Path) -> subprocess.CompletedProcess:
    (repo / ".leerie").mkdir(parents=True, exist_ok=True)
    df = repo / ".leerie" / "Dockerfile"
    if not df.is_symlink():
        df.write_text(dockerfile)
    script = ("set -euo pipefail\n" + 'remote_log() { echo "$*" >&2; }\n'
              + _fns() + f'\n_repo_image_dockerfile "{out}" {NONCE} {BASE}\n')
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin",
                               "USER_REPO": str(repo)})


GENERATED = ("# leerie-generated: do not edit (regenerated from "
             ".leerie/config.toml)\nARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
             "USER root\nCOPY package-lock.json package.json ./\n"
             "RUN npm ci\n")


def _stages(text: str) -> list[str]:
    return [l for l in text.splitlines() if l.upper().startswith("FROM ")]


def test_the_final_stage_is_leeries_base_and_one_copy(tmp_path):
    out = tmp_path / "Dockerfile"
    r = _write(tmp_path / "repo", GENERATED, out)
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    final = text[text.index(f"FROM {BASE} AS leerie-final-{NONCE}"):]
    assert final.splitlines() == [
        f"FROM {BASE} AS leerie-final-{NONCE}",
        f"COPY --from=leerie-stage-{NONCE} / /opt/leerie-worker-root/"]
    # The repository's lines are all above leerie's, in its named stage.
    assert text.index("RUN npm ci") < text.index(f"leerie-baseenv-{NONCE}")
    assert f"FROM $BASE_IMAGE AS leerie-worker-{NONCE}" in text
    assert f"FROM leerie-worker-{NONCE} AS leerie-stage-{NONCE}" in text
    # No parser directive of the repository's applies: leerie's comment is
    # the first line.
    assert text.startswith("# leerie: the repository image")


def test_an_existing_last_stage_name_is_used(tmp_path):
    out = tmp_path / "Dockerfile"
    df = ("FROM debian AS build\nRUN make\nFROM $BASE_IMAGE as Tools\n"
          "COPY --from=build /x /x\n")
    r = _write(tmp_path / "repo", df, out)
    assert r.returncode == 0, r.stderr
    assert f"FROM Tools AS leerie-stage-{NONCE}" in out.read_text()


def test_continuations_and_heredocs_are_not_instructions(tmp_path):
    out = tmp_path / "Dockerfile"
    df = ("FROM a AS one\nRUN <<EOF\nFROM fake AS nope\nEOF\n"
          "RUN echo \\\n  FROM not-a-stage\nFROM b \\\n  AS two\n"
          "RUN true\n")
    r = _write(tmp_path / "repo", df, out)
    assert r.returncode == 0, r.stderr
    assert f"FROM two AS leerie-stage-{NONCE}" in out.read_text()


def test_a_swallowing_file_leaves_no_final_stage_to_build(tmp_path):
    """An unterminated heredoc or a trailing continuation swallows leerie's
    lines; the `--target` names a stage only leerie's text defines, with a
    nonce the repository cannot know, so the build finds no target."""
    for tail in ("RUN <<EOF\necho\n", "RUN echo \\\n"):
        out = tmp_path / "Dockerfile"
        r = _write(tmp_path / "repo", "FROM $BASE_IMAGE\n" + tail, out)
        assert r.returncode == 0, r.stderr
        text = out.read_text()
        assert text.count(f"leerie-final-{NONCE}") == 1
        assert NONCE not in tail


def test_a_repository_syntax_directive_is_not_the_first_line(tmp_path):
    out = tmp_path / "Dockerfile"
    r = _write(tmp_path / "repo",
               "# syntax=attacker.invalid/frontend:1\nFROM $BASE_IMAGE\n", out)
    assert r.returncode == 0, r.stderr
    first = out.read_text().splitlines()[0]
    assert first.startswith("# leerie:") and "=" not in first


def test_a_linked_dockerfile_is_refused(tmp_path):
    repo = tmp_path / "repo"
    (repo / ".leerie").mkdir(parents=True)
    (tmp_path / "elsewhere").write_text("FROM x\n")
    (repo / ".leerie" / "Dockerfile").symlink_to(tmp_path / "elsewhere")
    r = _write(repo, "", tmp_path / "Dockerfile")
    assert r.returncode != 0 and not (tmp_path / "Dockerfile").exists()


def _check(layers_base: str, layers_repo: str, cfg_base: str, cfg_repo: str,
           tmp_path: Path) -> subprocess.CompletedProcess:
    stub = f'''
nerdctl() {{
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
    script = ("set -euo pipefail\n" + 'remote_log() { echo "$*" >&2; }\n'
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


def test_build_repo_image_builds_leeries_target_from_the_committed_tree():
    text = LAUNCHER.read_text()
    fn = text[text.index("build_repo_image() {"):]
    fn = fn[:fn.index("\n}\n")]
    assert '--target "leerie-final-$_nonce"' in fn
    assert '-f "$_REPO_BUILD_DIR/Dockerfile"' in fn
    assert '"$_REPO_BUILD_CTX"' in fn and '"$USER_REPO"' not in fn
    assert "_check_repo_image" in fn
    prep = text[text.index("_repo_build_prepare() {"):]
    prep = prep[:prep.index("\n}\n")]
    assert 'archive --format=tar HEAD' in prep


# --- the real build (opt-in) ------------------------------------------------

_REAL = os.environ.get("LEERIE_TEST_REPO_IMAGE")


@pytest.mark.skipif(not _REAL or not shutil.which("nerdctl"),
                    reason="LEERIE_TEST_REPO_IMAGE=<base image> builds a real "
                           "repository image (minutes, ~2.4 GB)")
def test_a_planted_pth_never_reaches_the_orchestrators_interpreter(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    (repo / "package.json").write_text(PLANT_PKG)
    (repo / "package-lock.json").write_text(PLANT_LOCK)
    (repo / ".env").write_text("untracked-secret\n")
    subprocess.run(["git", "-C", str(repo), "add", "package.json",
                    "package-lock.json"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=a@b", "-c",
                    "user.name=a", "commit", "-qm", "i"], check=True)
    tag = "leerie-test-e1:" + NONCE
    state = tmp_path / "state"
    state.mkdir()
    script = ("set -euo pipefail\n" + 'remote_log() { echo "$*" >&2; }\n'
              + _fns() + "\n" + LAUNCHER.read_text()[
                  LAUNCHER.read_text().index("build_repo_image() {"):].split(
                  "\n}\n", 1)[0] + "\n}\n"
              + f"IMAGE_TAG={_REAL}\nbuild_repo_image {tag}\n")
    (repo / ".leerie").mkdir()
    (repo / ".leerie" / "Dockerfile").write_text(GENERATED)
    try:
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, timeout=3600,
                           env={**os.environ, "USER_REPO": str(repo),
                                "LEERIE_STATE_HOST_DIR": str(state)})
        assert r.returncode == 0, r.stderr
        for user in ("leerie", "0"):
            o = subprocess.run(
                ["nerdctl", "run", "--rm", "--network", "none", "--user",
                 user, "--entrypoint", "python3", tag, "-c", "print('ran')"],
                capture_output=True, text=True, timeout=300)
            assert "ran" in o.stdout and "R28-PLANT" not in o.stderr, o
        w = subprocess.run(
            ["nerdctl", "run", "--rm", "--network", "none", "--entrypoint",
             "/bin/sh", tag, "-c",
             "test -f /opt/leerie-worker-root/usr/lib/python3/dist-packages/"
             "zz-plant.pth && echo planted-in-worker-root; "
             "test -e /usr/lib/python3/dist-packages/zz-plant.pth || "
             "echo base-clean; find /opt/leerie-worker-root -maxdepth 2 "
             "-name .env"], capture_output=True, text=True, timeout=300)
        assert "planted-in-worker-root" in w.stdout
        assert "base-clean" in w.stdout and ".env" not in w.stdout
    finally:
        subprocess.run(["nerdctl", "image", "rm", tag], capture_output=True)
