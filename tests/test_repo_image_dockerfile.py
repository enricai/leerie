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
              + _fns() + f'\n_repo_image_prepare {BASE}\n'
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


# Review round 32, execution X1: a `<<` was read as a heredoc wherever it
# appeared on a RUN/COPY/ADD line, so a shell shift or a here-string
# swallowed the rest of the file, an earlier stage was named the worker
# stage and the build succeeded. BuildKit reads a heredoc only where a whole
# shell word matches `^(\d*)<<(-?)([^<]*)$`. Each file's last FROM is the
# `runtime` stage, and the stage leerie names must be it.
_TAIL = "FROM ruby:3.3-slim AS runtime\nCOPY a /a\nRUN true\n"


@pytest.mark.parametrize("line", [
    "RUN echo $((1<<SHIFT)) > /shift",          # arithmetic shift
    "RUN cat <<<hello > /h",                     # here-string
    "RUN echo '<<x' > /q",                       # quoted, single
    'RUN echo "a <<x b" > /q',                   # quoted, double
    "RUN echo a\\<<x > /q",                      # escaped
    'RUN ["sh", "-c", "cat <<EOF"]',             # JSON form holds none
    "RUN echo x<<y",                             # not the word's start
])
def test_a_shift_or_here_string_is_no_heredoc(tmp_path, line):
    out = tmp_path / "Dockerfile"
    r = _write(tmp_path / "repo",
               f"FROM ruby:3.3-slim AS builder\n{line}\n{_TAIL}", out)
    assert r.returncode == 0, r.stderr
    assert f"FROM runtime AS leerie-stage-{NONCE}" in out.read_text()


@pytest.mark.parametrize("line, body", [
    ("RUN cat <<END-OF > /x", "FROM fake AS nope\nEND-OF"),
    ("RUN cat <<'END OF' > /x", "FROM fake AS nope\nEND OF"),
    ('RUN cat <<"E.O.F" > /x', "FROM fake AS nope\nE.O.F"),
    ("RUN cat <<E'O'F > /x", "FROM fake AS nope\nEOF"),
    ("RUN cat <<-EOF > /x", "\tFROM fake AS nope\n\t\tEOF"),
    ("RUN cat 0<<EOF > /x", "FROM fake AS nope\nEOF"),
    ("RUN cat <<A <<B > /x", "FROM fake AS nope\nA\nFROM fake2 AS no\nB"),
])
def test_a_heredoc_name_is_read_as_buildkit_reads_it(tmp_path, line, body):
    """The delimiter is the whole rest of the word, unquoted (`END-OF`
    was read as `END`, and the body ran past the end of the file); a
    `<<-` body's terminator may be indented by tabs."""
    out = tmp_path / "Dockerfile"
    r = _write(tmp_path / "repo",
               f"FROM ruby:3.3-slim AS builder\n{line}\n{body}\n{_TAIL}",
               out)
    assert r.returncode == 0, r.stderr
    text = out.read_text()
    assert f"FROM runtime AS leerie-stage-{NONCE}" in text


def test_a_heredoc_names_no_context_path_but_a_shifted_copy_does(tmp_path):
    df = ("FROM node:20 AS builder\nRUN echo $((1<<2))\n"
          "FROM node:20 AS runtime\nCOPY <<EOT /etc/x\nhi\nEOT\n"
          "COPY src/a.ts /app/\n")
    got, _sha, _f = _context(_repo(tmp_path, df), tmp_path / "st")
    assert got == [".dockerignore", "src/a.ts"]


# Review round 32, execution X2: leerie's file began with its own comment,
# so BuildKit read none of the repository's parser directives and a file
# written with `# escape=`` failed to parse. Its `escape` and `check`
# directives come first, as BuildKit reads them; `syntax` never does.
def test_the_repositorys_parser_directives_come_first(tmp_path):
    out = tmp_path / "Dockerfile"
    r = _write(tmp_path / "repo",
               "# Escape=`\n#check = error=true\n"
               "# syntax=attacker.invalid/frontend:1\n"
               "FROM ruby:3.3-slim\nCOPY a `\n  /a\n", out)
    assert r.returncode == 0, r.stderr
    head = out.read_text().splitlines()[:3]
    assert head[0] == "# escape=`"
    assert head[1] == "# check=error=true"
    assert head[2].startswith("# leerie:")
    assert "attacker.invalid" not in "\n".join(head)


@pytest.mark.parametrize("dockerfile", [
    # A comment or a blank line ends the directives: what follows is a
    # comment to BuildKit, and to leerie.
    "# a comment\n# escape=`\nFROM ruby:3.3-slim\n",
    "\n# escape=`\nFROM ruby:3.3-slim\n",
    # So does an unknown key.
    "# foo=bar\n# escape=`\nFROM ruby:3.3-slim\n",
])
def test_a_directive_after_the_top_is_a_comment(tmp_path, dockerfile):
    out = tmp_path / "Dockerfile"
    r = _write(tmp_path / "repo", dockerfile, out)
    assert r.returncode == 0, r.stderr
    assert out.read_text().startswith("# leerie:")


def test_a_lowercase_from_gets_a_lowercase_as(tmp_path):
    """A repository's `# check=error=true` turns BuildKit's FromAsCasing
    warning into an error; the name leerie adds is in its FROM's case."""
    out = tmp_path / "Dockerfile"
    r = _write(tmp_path / "repo", "from ruby:3.3-slim\nrun true\n", out)
    assert r.returncode == 0, r.stderr
    assert f"from ruby:3.3-slim as leerie-worker-{NONCE}" in out.read_text()
