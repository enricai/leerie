"""The launcher's runtime detection gives the kernel's answer under the
launcher's own shell options (DESIGN §6 *What the runtime must allow*).

The launcher runs `set -euo pipefail`. A predicate computed as a pipeline
whose consumer stops reading early (`grep -q` at its first match, `head`,
`awk … exit`) leaves the producer to die of SIGPIPE on its next write, and
pipefail turns the pipe's status into failure: the predicate says "no" when
the answer is "yes". Measured (review r22 H1): `runtime_has_apparmor` and
`runtime_apparmor_current` were false on every Ubuntu run — the LSM list has
entries after `apparmor`, the profile directory has 164 entries — so the
launcher never passed `apparmor=leerie-container` and every local run died
at the confinement entry. The old tests stubbed `_runtime_on_kernel` with a
single `echo` and ran without `-e`, so the producer never had a second write
to die on.

Two halves:
- substance: the REAL probes (and the launcher's real `APPARMOR_OPT` block
  on top of them), under `set -euo pipefail`, against a fixture kernel tree
  whose match is not last;
- the rule: no shell file run under pipefail feeds a pipe into a consumer
  that can exit before EOF.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
SCRIPTS = REPO_ROOT / "scripts"
RUNTIME_INSTALL = SCRIPTS / "runtime-install.sh"
PROFILE = SCRIPTS / "apparmor" / "leerie-container"

# Ubuntu 24.04+'s list, in the kernel's own form: no trailing newline, and
# `apparmor` followed by two more modules.
UBUNTU_LSM = "lockdown,capability,landlock,yama,apparmor,ima,evm"


def _kernel(tmp_path: Path, *, lsm: str, loaded: bool = True,
            installed: str | None = None, n_profiles: int = 300) -> Path:
    """A fixture of the kernel's securityfs: the LSM list and a profile
    directory of `n_profiles` entries, `leerie-container` among the first."""
    root = tmp_path / "kernel"
    (root / "profiles").mkdir(parents=True)
    (root / "lsm").write_text(lsm)
    for i in range(n_profiles):
        name = ("leerie-container" if loaded and i == 3
                else f"profile-{i:04d}")
        d = root / "profiles" / f"{i:04d}-{name}.{i}"
        d.mkdir()
        (d / "name").write_text(name + "\n")
    (root / "installed").write_text(
        PROFILE.read_text() if installed is None else installed)
    return root


def _probe(root: Path, body: str) -> subprocess.CompletedProcess:
    """Source the real runtime-install.sh under the launcher's exact options
    and point its probes at the fixture: nothing is stubbed but the paths."""
    script = f"""
set -euo pipefail
DRY_RUN=false
. {RUNTIME_INSTALL}
_RUNTIME_LSM_FILE={root}/lsm
_RUNTIME_AA_PROFILES_DIR={root}/profiles
LEERIE_APPARMOR_DEST={root}/installed
{body}
"""
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, timeout=60)


YES_NO = "if {}; then echo YES; else echo NO; fi"


@pytest.mark.parametrize("attempt", range(5))
def test_has_apparmor_true_when_apparmor_is_not_last(tmp_path, attempt):
    r = _probe(_kernel(tmp_path, lsm=UBUNTU_LSM),
               YES_NO.format("runtime_has_apparmor"))
    assert r.stdout.strip() == "YES", r.stderr


@pytest.mark.parametrize("lsm,want", [
    ("lockdown,capability,landlock,yama,selinux,ima,evm", "NO"),
    ("apparmor", "YES"),
    ("landlock,apparmor", "YES"),
    # A prefix of the name is not the module.
    ("landlock,apparmorx,ima", "NO"),
])
def test_has_apparmor_reads_the_list(tmp_path, lsm, want):
    r = _probe(_kernel(tmp_path, lsm=lsm),
               YES_NO.format("runtime_has_apparmor"))
    assert r.stdout.strip() == want, r.stderr


def test_has_apparmor_false_without_securityfs(tmp_path):
    root = _kernel(tmp_path, lsm=UBUNTU_LSM)
    (root / "lsm").unlink()
    r = _probe(root, YES_NO.format("runtime_has_apparmor"))
    assert r.stdout.strip() == "NO", r.stderr


@pytest.mark.parametrize("attempt", range(5))
def test_apparmor_current_true_when_loaded_profile_is_not_last(tmp_path,
                                                                 attempt):
    r = _probe(_kernel(tmp_path, lsm=UBUNTU_LSM),
               YES_NO.format(f"runtime_apparmor_current {PROFILE}"))
    assert r.stdout.strip() == "YES", r.stderr


@pytest.mark.parametrize("kw,want", [
    ({"loaded": False}, "NO"),
    ({"installed": "profile leerie-container {}\n"}, "NO"),
    ({}, "YES"),
])
def test_apparmor_current_needs_loaded_and_identical(tmp_path, kw, want):
    r = _probe(_kernel(tmp_path, lsm=UBUNTU_LSM, **kw),
               YES_NO.format(f"runtime_apparmor_current {PROFILE}"))
    assert r.stdout.strip() == want, r.stderr


def test_apparmor_current_false_without_an_installed_file(tmp_path):
    root = _kernel(tmp_path, lsm=UBUNTU_LSM)
    (root / "installed").unlink()
    r = _probe(root, YES_NO.format(f"runtime_apparmor_current {PROFILE}"))
    assert r.stdout.strip() == "NO", r.stderr


def _secopt_block() -> str:
    src = LAUNCHER.read_text()
    start = src.index("  SECOPT=(")
    end = src.index("\n  fi\n", src.index("  APPARMOR_OPT=()", start)) + 6
    return src[start:end]


@pytest.mark.parametrize("attempt", range(3))
def test_launcher_block_passes_the_profile_on_an_apparmor_kernel(tmp_path,
                                                                attempt):
    """The launcher's own block over the real probes: on Ubuntu's LSM list
    with the profile loaded and current, the run argv gets
    `apparmor=leerie-container` and nothing is installed."""
    body = f"""
LEERIE_REPO={REPO_ROOT}
NO_RUNTIME_INSTALL=true
remote_log() {{ echo "LOG: $*" >&2; }}
runtime_install_apparmor() {{ echo "INSTALL $1" >&2; return 1; }}
exec 0</dev/null
{_secopt_block()}
echo "APPARMOR_OPT=${{APPARMOR_OPT[*]-}}"
"""
    r = _probe(_kernel(tmp_path, lsm=UBUNTU_LSM), body)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip().splitlines()[-1] == (
        "APPARMOR_OPT=--security-opt apparmor=leerie-container")
    assert "INSTALL" not in r.stderr and "not loaded" not in r.stderr


# --- the rule ---------------------------------------------------------------

def _shell_files() -> list[Path]:
    return [LAUNCHER, *sorted(SCRIPTS.rglob("*.sh"))]


_SETS_PIPEFAIL = re.compile(r"^\s*set\s+-[a-z]*o\s+pipefail\b|"
                            r"^\s*set\s+(-[a-z]+\s+)*-o\s+pipefail\b", re.M)
_SOURCES = re.compile(r"^\s*(?:\.|source)\s+\S*?([\w.-]+\.sh)\b", re.M)


def _pipefail_files() -> set[Path]:
    """Files that set pipefail, plus every file they source, transitively:
    a sourced library's pipelines run under its caller's options."""
    files = _shell_files()
    by_name = {f.name: f for f in files}
    under = {f for f in files if _SETS_PIPEFAIL.search(f.read_text())}
    grew = True
    while grew:
        grew = False
        for f in list(under):
            for name in _SOURCES.findall(f.read_text()):
                g = by_name.get(name)
                if g is not None and g not in under:
                    under.add(g)
                    grew = True
    return under


_HEREDOC = re.compile(r"<<-?\s*(['\"]?)(\w+)\1")


def _code(text: str) -> str:
    """The shell code alone: full-line comments and heredoc bodies (data
    written to another program, not run by this shell) blanked out."""
    out: list[str] = []
    terminator: str | None = None
    for line in text.splitlines():
        if terminator is not None:
            out.append("")
            if line.strip() == terminator:
                terminator = None
            continue
        if line.lstrip().startswith("#"):
            out.append("")
            continue
        m = _HEREDOC.search(line.replace("<<<", "   "))
        if m:
            terminator = m.group(2)
        out.append(line)
    return "\n".join(out)


_PIPE = re.compile(r"(?<![|])\|(?![|])(?:\s|\\\n)*")


def _quoted_program(rest: str) -> str:
    """The first quoted argument in `rest` (an awk or sed program)."""
    m = re.search(r"'([^']*)'|\"((?:[^\"\\]|\\.)*)\"", rest)
    return (m.group(1) if m.group(1) is not None else m.group(2)) if m else ""


def _early_exit(rest: str) -> str | None:
    """Why the command starting `rest` may stop reading its stdin before EOF,
    or None."""
    word = re.match(r"[\w.-]+", rest)
    if not word:
        return None
    cmd = word.group(0)
    if cmd == "head":
        return "head"
    if cmd in ("cmp", "read"):
        return cmd
    if cmd == "grep":
        args = re.split(r"[|;&)\n]", rest, maxsplit=1)[0].split()[1:]
        for a in args:
            if a == "--" or not a.startswith("-"):
                break
            if a.startswith("--"):
                if a.split("=")[0] in ("--quiet", "--silent", "--max-count",
                                       "--files-with-matches",
                                       "--files-without-match"):
                    return f"grep {a}"
            elif set(a[1:]) & set("qmlL"):
                return f"grep {a}"
        return None
    if cmd == "awk":
        prog = re.sub(r"\bEND\s*\{.*", "", _quoted_program(rest), flags=re.S)
        return "awk exit" if re.search(r"\bexit\b", prog) else None
    if cmd == "sed":
        prog = _quoted_program(rest)
        return "sed q" if re.search(r"(^|[;{}\s\d$/])[qQ]\s*($|[;}\s\d])",
                                    prog) else None
    return None


def _violations(text: str) -> list[tuple[int, str]]:
    code = _code(text)
    found = []
    for m in _PIPE.finditer(code):
        why = _early_exit(code[m.end():])
        if why:
            found.append((code.count("\n", 0, m.start()) + 1, why))
    return found


def test_the_pipefail_set_is_derived_from_the_scripts():
    """Substance for the derivation: the files that set pipefail and the
    libraries only sourced by one are both in it."""
    under = {f.name for f in _pipefail_files()}
    for name in ("leerie", "runtime-install.sh", "worktree-lib.sh",
                 "host-finalize.sh", "ec2-defaults.sh", "setup-run.sh"):
        assert name in under, name
    # `set -e` alone, sourced by nothing that sets pipefail.
    assert "container-entry.sh" not in under


@pytest.mark.parametrize("snippet,why", [
    ("x=1\nif a | grep -qx b; then :; fi\n", "grep -qx"),
    ("if printf '%s' \"$v\" | cut -c4- \\\n  | grep -Fxq -- y; then :; fi\n",
     "grep -Fxq"),
    ("v=$(git log | head -1)\n", "head"),
    ("s=$(cmd \\\n  | awk '/^State: / { print $2; exit }')\n", "awk exit"),
    ("s=$(cmd | sed '/x/q')\n", "sed q"),
    ("cat a | cmp -s - b\n", "cmp"),
    ("ssh-add -l | grep --quiet CERT\n", "grep --quiet"),
])
def test_the_rule_catches_each_early_exit_consumer(snippet, why):
    assert [w for _, w in _violations(snippet)] == [why]


@pytest.mark.parametrize("snippet", [
    "if grep -qx b < <(a); then :; fi\n",
    "v=$(git log | sed -n 1p)\n",
    "s=$(cmd | awk '/^State: / && !f { print $2; f = 1 }')\n",
    "n=$(cmd | awk '{ n++ } END { exit n == 0 }')\n",
    "x=$(a || b)\n",
    "grep -c foo file | tr -d ' '\n",
    "# a | grep -q b in a comment\n",
    "cat <<'EOF'\nswapon | grep -qx /var/swapfile\nEOF\n",
])
def test_the_rule_passes_constructs_that_read_to_eof(snippet):
    assert _violations(snippet) == []


def test_no_pipefail_script_pipes_into_an_early_exit_consumer():
    """Every pipeline in a pipefail shell reads to EOF, so its status is its
    producers' real status. Test a value with `case`, or feed `grep -q` by
    process substitution (`grep -q P < <(cmd)`), whose writer's death no
    status sees; take a first line with `sed -n 1p`; stop an awk program
    with a flag, not `exit`."""
    bad = [f"{f.relative_to(REPO_ROOT)}:{line}: {why}"
           for f in sorted(_pipefail_files())
           for line, why in _violations(f.read_text())]
    assert not bad, "\n".join(bad)
