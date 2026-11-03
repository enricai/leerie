"""DESIGN §6 *The host never runs a program the checkout chose*, part 3
(review round 27, isolation I4).

The Fly/EC2 seed lists the checkout's submodules with
`_seed_submodule_dirs` and bundles each from the host with `git -C <dir>
bundle create - --all`. The list was one "<displaypath>\\t<dir>" line per
submodule, but a submodule path is repository content: `.gitmodules`
`path = "z\\nX\\t/abs/repo\\nY"` is valid git and `submodule update --init`
populates it, so one submodule forged a second entry whose directory was any
repository on the host, and the seed shipped that repository to the machine.
The bundle name made from the same path was also spliced into the remote
`sh -c 'cat > /tmp/leerie-subs/$bn'`.

The reviewer's proof, inverted on the shipped function and the shipped
loops: the records are NUL-terminated, so the victim is never listed, and
every bundle name is hex, never shell syntax, and the same on both sides.
"""
from __future__ import annotations

import functools
import re
import subprocess
from pathlib import Path

from tests.conftest import run_git_out  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
COMMON = REPO / "scripts" / "remote" / "seed-common.sh"
SEEDS = (REPO / "scripts" / "remote" / "seed-repo.sh",
         REPO / "scripts" / "remote" / "ec2-seed-repo.sh")


_git = functools.partial(run_git_out, Path("/"), strip=False, config=(
    "user.email=v@x.invalid", "user.name=v", "protocol.file.allow=always"))


def _fn(text: str, name: str) -> str:
    return re.search(rf"^{name}\(\) \{{.*?^\}}", text, re.S | re.M).group(0)


def _hostile_checkout(tmp_path: Path, path: str) -> tuple[Path, Path]:
    victim = tmp_path / "victim"
    _git("init", "-q", str(victim))
    _git("-C", str(victim), "commit", "-q", "--allow-empty", "-m", "VICTIM")
    sub = tmp_path / "sub"
    _git("init", "-q", str(sub))
    _git("-C", str(sub), "commit", "-q", "--allow-empty", "-m", "s")
    top = tmp_path / "checkout"
    _git("init", "-q", str(top))
    quoted = path.replace("\\", "\\\\").replace("\n", "\\n") \
        .replace("\t", "\\t").replace('"', '\\"')
    (top / ".gitmodules").write_text(
        f'[submodule "n"]\n\tpath = "{quoted}"\n\turl = {sub}\n')
    _git("-C", str(top), "add", ".gitmodules")
    sha = _git("-C", str(sub), "rev-parse", "HEAD").strip()
    _git("-C", str(top), "update-index", "--add", "--cacheinfo",
         f"160000,{sha},{path}")
    _git("-C", str(top), "submodule", "update", "--init")
    return top, victim


def _listed(top: Path) -> list[tuple[str, str]]:
    """Run the shipped function and every shipped loop header over `top`;
    the (display path, directory, bundle name) each loop would bundle."""
    common = COMMON.read_text()
    out = []
    for seed in SEEDS:
        for m in re.finditer(
                r"(_seed_submodule_dirs \"\$\w+\" \| while IFS= read -r -d '' "
                r"_sm_disp \\\n\s*&& IFS= read -r -d '' _sm_dir; do\n"
                r"\s*bn=\"\$\(_seed_bundle_name \"\$_sm_disp\"\)\")",
                seed.read_text()):
            head = m.group(1).replace('"$USER_REPO"', '"$1"') \
                .replace('"$host"', '"$1"')
            script = (_fn(common, "_seed_submodule_dirs") + "\n"
                      + _fn(common, "_seed_bundle_name") + "\n"
                      + head + "\n  printf 'B<%s|%s|%s>' \"$_sm_disp\" "
                      "\"$_sm_dir\" \"$bn\"\ndone\n")
            r = subprocess.run(["/bin/bash", "-c", script, "x", str(top)],
                               cwd="/", capture_output=True, text=True,
                               check=True, timeout=60)
            out += re.findall(r"B<(.*?)\|(.*?)\|(.*?)>", r.stdout, re.S)
    return out


def test_every_shipped_loop_reads_the_nul_list() -> None:
    """Three loops: the Fly seed, the Fly inspect-dir seed, the EC2 seed."""
    n = sum(len(re.findall(r"\| while IFS= read -r -d '' _sm_disp",
                           s.read_text())) for s in SEEDS)
    assert n == 3
    for s in SEEDS:
        assert "IFS=\"$(printf '\\t')\" read" not in s.read_text()


def test_a_submodule_path_cannot_forge_a_host_repository(
        tmp_path: Path) -> None:
    path = f"z\nX\t{tmp_path / 'victim'}\nY"
    top, victim = _hostile_checkout(tmp_path, path)
    listed = _listed(top)
    assert len(listed) == 3, listed
    for disp, d, bn in listed:
        assert disp == path
        assert d == f"{top}/{path}" and d != str(victim)
        assert re.fullmatch(r"[0-9a-f]+\.bundle", bn), bn
        assert bytes.fromhex(bn[:-len(".bundle")]).decode() == path
    # Control: the line-based parse the seed used forges the victim.
    old = subprocess.run(
        ["/bin/bash", "-c",
         "git -C \"$1\" submodule --quiet foreach --recursive "
         "'printf \"%s\\t%s\\n\" \"$displaypath\" \"$toplevel/$sm_path\"' | "
         "while IFS=\"$(printf '\\t')\" read -r a b; do printf 'B<%s>' \"$b\";"
         " done", "x", str(top)], cwd="/", capture_output=True, text=True,
        check=True, timeout=60).stdout
    assert f"B<{victim}>" in old, old


def test_a_quote_in_a_path_never_reaches_the_remote_shell(
        tmp_path: Path) -> None:
    """`$bn` sits inside the remote `sh -c '…'`; a `'` in the path used to
    end that quote."""
    path = "a'; touch pwned; '"
    top, _ = _hostile_checkout(tmp_path, path)
    listed = _listed(top)
    assert listed and all(re.fullmatch(r"[0-9a-f]+\.bundle", bn)
                          for _, _, bn in listed), listed


def test_the_machine_side_names_the_bundle_the_same(tmp_path: Path) -> None:
    """The machine-side snippet (`_seed_machine_submodules`, embedded in all
    three machine scripts) computes the bundle name with the same `od`
    pipeline as `_seed_bundle_name`; a nested, spaced, unicode path gets
    the same name from both."""
    common = COMMON.read_text()
    machine = re.compile(
        r'bn=\$\(printf %s "\$1\$path" \| od -An -v -tx1 \| tr -d " \\n"\)'
        r'\.bundle')
    assert len(machine.findall(_fn(common, "_seed_machine_submodules"))) == 1
    sites = sum(s.read_text().count("$(_seed_machine_submodules ")
                for s in SEEDS)
    assert sites == 3, sites
    for prefix, sm in (("vendor/", "foo"), ("", "a b/ç"), ("", "x" * 40)):
        host = subprocess.run(
            ["/bin/bash", "-c", _fn(common, "_seed_bundle_name")
             + '\n_seed_bundle_name "$1"', "x", prefix + sm],
            capture_output=True, text=True, check=True, timeout=30).stdout
        mach = subprocess.run(
            ["/bin/bash", "-c", 'path=$2; bn=$(printf %s "$1$path" | od -An '
             '-v -tx1 | tr -d " \\n").bundle; printf %s "$bn"', "x", prefix,
             sm], capture_output=True, text=True, check=True,
            timeout=30).stdout
        assert host == mach == (prefix + sm).encode().hex() + ".bundle"
