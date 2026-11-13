"""Each Node version's corepack shims go in its own `bin` (DESIGN §6 *Each
Node version's corepack shims go in its own `bin`*).

mise's implicit enable (`MISE_NODE_COREPACK=true`) ran `corepack enable`
without `--install-directory`, which writes next to the first `corepack` on
PATH: the image's baked Node. Confined, that was EROFS on
`/usr/local/share/mise/installs/node/24.22.2/bin/pnpm` and every run needing
a Node not yet installed died in provisioning. These drive the real
`_run_mise_install` with only its process spawns stubbed; the real-container
proof is in the commit message.
"""
from __future__ import annotations

import asyncio
import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
class _Grants:
    env = ()
    drop = ()


GRANTS = _Grants()


def _state(leerie, tmp_path):
    root = tmp_path / ".leerie"
    (root / "runs" / "r" / "logs").mkdir(parents=True)
    st = leerie.State(root, "r")
    st.data = {"task": "t"}
    st.save()
    return st


def _version(root: Path, name: str, *, corepack=True, pnpm=False) -> Path:
    b = root / "installs" / "node" / name / "bin"
    b.mkdir(parents=True)
    (b / "node").write_text("")
    if corepack:
        (b / "corepack").write_text("")
    if pnpm:
        (b / "pnpm").write_text("")
    return b


def _drive(leerie, tmp_path, monkeypatch, *, data, on_install=None,
           enable_rc=0):
    calls: list[tuple[list[str], dict, object]] = []

    async def fake_streaming(cmd, *, grants, env=None, **kw):
        calls.append((list(cmd), dict(env or {}), grants))
        if cmd == ["mise", "install"]:
            if on_install:
                on_install()
            return 0, ""
        return enable_rc, "enable tail"

    async def fake_run_proc(argv, **kw):
        return subprocess.CompletedProcess(argv, 0, "{}", "")

    class _Lock:
        def shared(self):
            return _ACM()

    class _ACM:
        async def __aenter__(self):
            return None

        async def __aexit__(self, *a):
            return False

    monkeypatch.setenv("MISE_DATA_DIR", str(data))
    monkeypatch.setenv("MISE_NODE_COREPACK", "true")   # the 0.44.1 image's
    monkeypatch.setattr(leerie, "_measurement_grants", lambda *a: GRANTS)
    monkeypatch.setattr(leerie, "_run_streaming", fake_streaming)
    monkeypatch.setattr(leerie, "_run_proc", fake_run_proc)
    monkeypatch.setattr(leerie, "_confined_argv", lambda g, a, **kw: a)
    monkeypatch.setattr(leerie, "_exec_lock", lambda: _Lock())
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".nvmrc").write_text("22.19.0\n")
    st = _state(leerie, tmp_path)
    asyncio.run(leerie._run_mise_install(repo, tmp_path / "logs", st))
    return calls


def test_install_disables_implicit_enable_and_enables_into_own_bin(
        leerie, tmp_path, monkeypatch):
    data = tmp_path / "data"
    system = tmp_path / "system"
    sys_bin = _version(system, "24.22.2")          # baked, no shims: untouched
    monkeypatch.setenv("MISE_SYSTEM_DATA_DIR", str(system))
    made: list[Path] = []
    calls = _drive(leerie, tmp_path, monkeypatch, data=data,
                   on_install=lambda: made.append(_version(data, "22.19.0")))
    install, *rest = calls
    assert install[0] == ["mise", "install"]
    assert install[1]["MISE_NODE_COREPACK"] == "false"
    v = made[0]
    assert [c[0] for c in rest] == [
        [str(v / "node"), str(v / "corepack"), "enable",
         "--install-directory", str(v)]]
    assert rest[0][2] is GRANTS                     # the toolchain grant
    assert all(str(sys_bin) not in " ".join(c[0]) for c in calls)


def test_skips_enabled_versions_aliases_and_versions_without_corepack(
        leerie, tmp_path, monkeypatch):
    data = tmp_path / "data"
    _version(data, "20.0.0", pnpm=True)             # already enabled
    _version(data, "14.0.0", corepack=False)        # no corepack shipped
    shimless = _version(data, "22.19.0")            # left by a failed run
    (data / "installs" / "node" / "22").symlink_to("22.19.0")
    calls = _drive(leerie, tmp_path, monkeypatch, data=data)
    assert [c[0] for c in calls[1:]] == [
        [str(shimless / "node"), str(shimless / "corepack"), "enable",
         "--install-directory", str(shimless)]]


def test_a_failed_enable_fails_provisioning(leerie, tmp_path, monkeypatch):
    data = tmp_path / "data"
    _version(data, "22.19.0")
    with pytest.raises(SystemExit):
        _drive(leerie, tmp_path, monkeypatch, data=data, enable_rc=1)


def test_image_turns_off_implicit_enable_and_enables_baked_node_itself():
    df = (REPO / "Dockerfile").read_text()
    assert re.findall(r"^ENV MISE_NODE_COREPACK=(\S+)$", df, re.M) == ["false"]
    step = df[df.index("RUN set -eux; \\\n    node_dir="):]
    step = step[:step.index("\n\n")]
    want = ('"${node_dir%/}/bin/node" "${node_dir%/}/bin/corepack" enable '
            '--install-directory "${node_dir%/}/bin"')
    assert want in " ".join(step.replace("\\\n", " ").split())
    bake = (REPO / "scripts/remote/ec2-bake.sh").read_text()
    assert want in " ".join(bake.replace("\\\n", " ").split())


def test_the_image_step_runs_with_no_node_on_path(tmp_path):
    """The Dockerfile's `lts-current` step, run as the image build runs it:
    by /bin/sh, with no node on PATH. corepack is a `#!/usr/bin/env node`
    script, so run by name it fails with exit 127 (`env: 'node': No such
    file or directory`), which is how the 0.44.2 base build failed. Run by
    its own version's node it enables the shims."""
    df = (REPO / "Dockerfile").read_text()
    step = df[df.index("RUN set -eux; \\\n    node_dir="):]
    step = step[len("RUN "):step.index("\n\n")]
    root = tmp_path / "mise"
    b = root / "installs" / "node" / "24.0.0" / "bin"
    b.mkdir(parents=True)
    # node: a builtin-only stand-in that does what `corepack enable
    # --install-directory D` does, link D/pnpm (argv: corepack enable
    # --install-directory D).
    (b / "node").write_text(
        '#!/bin/sh\nexec /bin/ln -s corepack "$4/pnpm"\n')
    (b / "corepack").write_text("#!/usr/bin/env node\n")
    for f in (b / "node", b / "corepack"):
        f.chmod(0o755)
    script = step.replace("/usr/local/share/mise", str(root))
    r = subprocess.run(["/bin/sh", "-c", script],
                       env={"PATH": "/usr/sbin:/usr/bin:/sbin:/bin"},
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert (b / "pnpm").is_symlink()
