"""Every run of a held-out file is in a sandbox, and no database server
leerie starts is ever unconfined (DESIGN §6 *A held-out evaluation runs
alone, in a world that dies with it (I3)*, *No database server leerie
starts is ever unconfined*): leerie's side of both, on any host.

- Every held-out runner hands `_run_streaming` a `HeldOutSandbox` over its
  tree, with the set's files as `place` pairs — never copied onto disk —
  inside `_held_out_section`; the runner's report comes back only through
  the descriptor (here through `tests/heldout_sandbox_emulator.py`, which
  removes the in-sandbox copy, so a report leerie reads came through it).
- A command that needs a database is wrapped by `scripts/sandbox-db.sh`
  inside its own confinement: the held-out runs, a CI check, the falsifier's
  runs and the workers that need one (`claude_p(db=True)`). The URL the
  script composes passes `_test_db_url_refusal`; leerie itself never starts
  a server or runs `prisma`.

The isolation itself is tests/test_heldout_isolation.py's."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import shutil
import socket
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, pytestmark, run)

LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
SANDBOX_DB = REPO_ROOT / "scripts" / "sandbox-db.sh"


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _bash_cmd(_st, rels, _tree=None, placed=None):
    return f"bash {rels[0]}"


def _set(run, files: dict[str, str], *, defect=(), control=(), support=()):
    d = run["run_dir"] / "acceptance" / "set-9"
    d.mkdir(parents=True, exist_ok=True)
    for rel, body in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(body)
    return {"index": 9, "dir": str(d), "defect_files": list(defect),
            "control_files": list(control), "support_files": list(support),
            "cases": {}}


# --- the set is placed in the sandbox, never onto disk -----------------------

def test_a_set_run_places_its_files_and_never_writes_the_tree(
        leerie, run, monkeypatch):
    """Executed, with `_run_streaming` recording each spawn: every run of a
    set file carries the whole set as `place` pairs over the tree, in a
    section, and at no spawn is any of the set's files on disk."""
    st, view = _st(run), _view(run)
    s_ = _set(run, {"held.sh": "exit 1\n", "ctl.sh": "exit 0\n",
                    "lib/help.sh": "x\n"},
              defect=["held.sh"], control=["ctl.sh"],
              support=["lib/help.sh"])
    monkeypatch.setattr(leerie, "_acceptance_cmd", _bash_cmd)
    seen = []

    async def fake(cmd, *, grants, sandbox=None, cwd=None, **kw):
        lock = leerie._exec_lock()
        seen.append({"sandbox": sandbox, "cwd": cwd, "writer": lock.writer,
                     "on_disk": [r for r in ("held.sh", "ctl.sh",
                                             "lib/help.sh")
                                 if (view / r).exists()],
                     "rw": grants.rw})
        return (1 if "held" in " ".join(cmd) else 0), ""
    monkeypatch.setattr(leerie, "_run_streaming", fake)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, dict(leerie.DEFAULT_CAPS), view, [s_], "t", confirm=False))
    assert res["failing_files"] == ["held.sh"] and not res["passed"], res
    assert len(seen) == 2
    want = {(str(Path(s_["dir"]) / r), r)
            for r in ("held.sh", "ctl.sh", "lib/help.sh")}
    for call in seen:
        sb = call["sandbox"]
        assert isinstance(sb, leerie.HeldOutSandbox)
        assert sb.view == str(view) and set(sb.place) == want
        assert call["writer"] is True and call["cwd"] == str(view)
        assert call["on_disk"] == []
    assert not any((view / r).exists() for r in ("held.sh", "ctl.sh",
                                                 "lib"))


def test_a_set_path_the_tree_already_has_is_never_placed(leerie, run,
                                                         monkeypatch):
    st, view = _st(run), _view(run)
    (view / "held.sh").write_text("the tree's own\n")
    s_ = _set(run, {"held.sh": "exit 1\n", "ctl.sh": "exit 0\n"},
              defect=["held.sh"], control=["ctl.sh"])
    monkeypatch.setattr(leerie, "_acceptance_cmd", _bash_cmd)
    places = []

    async def fake(cmd, *, grants, sandbox=None, **kw):
        places.append(sandbox.place)
        return 0, ""
    monkeypatch.setattr(leerie, "_run_streaming", fake)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, dict(leerie.DEFAULT_CAPS), view, [s_], "t", confirm=False))
    assert res["unmeasured"] and res["unmeasured_files"] == {
        "held.sh": "path_in_use"}, res
    assert places and all(
        [r for _s, r in p] == ["ctl.sh"] for p in places), places
    assert (view / "held.sh").read_text() == "the tree's own\n"


def test_a_held_out_run_end_to_end_reads_its_report_through_the_descriptor(
        leerie, run, monkeypatch):
    """Through the real `_run_acceptance_file`, `_run_streaming` and shim
    (behind the emulator): the set file is placed only for its run, its
    per-case report reaches leerie although the in-sandbox copy is gone,
    and the tree is as it was."""
    st, view = _st(run), _view(run)
    s_ = _set(run, {"held.sh": textwrap.dedent("""\
        printf '{"cases": {"held.sh::a": "failed"}}' > "$LEERIE_REPORT"
        exit 1
        """)}, defect=["held.sh"])
    monkeypatch.setattr(leerie, "_acceptance_cmd", _bash_cmd)
    captured = {}

    def spec(cmd, report, files):
        captured["report"] = report
        return (cmd, {**os.environ, "LEERIE_REPORT": str(report)}, "probe")

    def parse(kind, report):
        captured["read"] = report.decode()
        return {"collection_error": False, "executed": 1,
                "cases": {"held.sh::a": "failed"}, "messages": {}}
    monkeypatch.setattr(leerie, "_acceptance_report_spec", spec)
    monkeypatch.setattr(leerie, "_parse_runner_report", parse)
    obs: dict = {}
    v = asyncio.run(leerie._run_acceptance_file(
        st, dict(leerie.DEFAULT_CAPS), str(view), "held.sh",
        run["run_dir"] / "logs" / "acc.log", "acc", cases_out=obs,
        place=((str(Path(s_["dir"]) / "held.sh"), "held.sh"),)))
    assert v is False and obs["failing_cases"] == ["held.sh::a"], obs
    assert json.loads(captured["read"]) == {"cases": {"held.sh::a":
                                                      "failed"}}
    # Parsed from the descriptor's bytes; no copy put back on disk (DESIGN
    # §6 *Leerie never writes through a path confined code shaped*).
    assert not Path(captured["report"]).exists()
    assert not (view / "held.sh").exists()
    git = subprocess.run(["git", "-C", str(view), "status", "--porcelain"],
                         capture_output=True, text=True)
    assert git.stdout == ""


def test_the_parse_probe_is_placed_and_its_marker_is_the_report(
        leerie, run, monkeypatch):
    st, view = _st(run), _view(run)
    monkeypatch.setattr(leerie, "_acceptance_cmd", _bash_cmd)
    seen = {}

    async def fake(cmd, *, grants, sandbox=None, **kw):
        seen["sandbox"] = sandbox
        seen["writer"] = leerie._exec_lock().writer
        (src, rel), = sandbox.place
        seen["src_text"] = Path(src).read_text()
        seen["in_tree"] = (view / rel).exists()
        kw["report_into"] += b"ok"
        return 0, ""
    monkeypatch.setattr(leerie, "_run_streaming", fake)
    got, why = asyncio.run(leerie._acceptance_parse_probe(
        st, dict(leerie.DEFAULT_CAPS), str(view), "test_x.py",
        ["test_x.py"], run["run_dir"] / "logs" / "p.log", "acc"))
    assert (got, why) == (True, ""), why
    sb = seen["sandbox"]
    assert sb.view == str(view) and seen["writer"] is True
    assert seen["in_tree"] is False and "compile" in seen["src_text"]
    assert Path(sb.report).is_relative_to(leerie._measure_dir(st, view))
    # The probe's source is leerie's own temporary file, gone afterwards.
    assert not Path(sb.place[0][0]).exists()


def test_every_held_out_runner_passes_a_sandbox_inside_a_section():
    """By rule: in every function that runs a held-out file, each
    `_run_streaming` call passes `sandbox=` and sits inside
    `_held_out_section`; `_acceptance_run_placed` copies nothing and holds
    no section of its own (each run's is the evaluation)."""
    module = ast.parse(LEERIE_PY.read_text())
    funcs = {n.name: n for n in ast.walk(module)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    for name in ("_run_acceptance_file", "_acceptance_parse_probe_run"):
        fn = funcs[name]
        sec = [w for w in ast.walk(fn) if isinstance(w, ast.AsyncWith)
               and any("_held_out_section" in ast.unparse(i.context_expr)
                       for i in w.items)]
        inside = {id(c) for w in sec for c in ast.walk(w)}
        calls = [c for c in ast.walk(fn) if isinstance(c, ast.Call)
                 and ast.unparse(c.func) == "_run_streaming"]
        assert calls, name
        for c in calls:
            assert id(c) in inside, name
            assert any(k.arg == "sandbox" for k in c.keywords), name
    placed = funcs["_acceptance_run_placed"]
    names = {ast.unparse(c.func) for c in ast.walk(placed)
             if isinstance(c, ast.Call)}
    assert not names & {"shutil.copy2", "_place_set", "_held_out_section"}
    # The case-failure capture and the discovery reruns hand on the set.
    for name in ("_acceptance_case_failures", "_acceptance_discover"):
        runs = [c for c in ast.walk(funcs[name]) if isinstance(c, ast.Call)
                and ast.unparse(c.func) == "_run_acceptance_file"]
        assert runs and all(any(k.arg == "place" for k in c.keywords)
                            for c in runs), name


# --- no database server leerie starts is unconfined --------------------------

def _prisma_repo(root: Path, provider: str = "postgresql") -> None:
    (root / "prisma").mkdir(parents=True, exist_ok=True)
    (root / "prisma" / "schema.prisma").write_text(
        f'datasource db {{\n  provider = "{provider}"\n  url = env("X")\n}}\n')
    (root / "package.json").write_text(json.dumps(
        {"name": "p", "devDependencies": {"prisma": "6"}}))
    subprocess.run(["git", "-C", str(root), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.email=t@x", "-c",
                    "user.name=t", "commit", "-qm", "prisma"], check=True)


def test_the_url_refusal_accepts_only_the_commands_own_loopback(leerie):
    """M2 (review round 21): the URL is plain TCP on the command's own
    loopback, `127.0.0.1:5432` — a repository's tooling that reads host and
    port from it reaches the server — and nothing else is accepted."""
    name = leerie._test_db_name("k")
    good = leerie._test_db_url(name)
    user = good.split("://", 1)[1].split("@", 1)[0]
    assert good == f"postgresql://{user}@127.0.0.1:5432/{name}"
    assert leerie._test_db_url_refusal(good, good) is None
    for bad, why in (
            (f"mysql://{user}@127.0.0.1:5432/{name}", "scheme"),
            (f"postgresql://{user}@localhost/{name}?host=/tmp/leerie-pg",
             "host"),
            (f"postgresql://{user}@127.0.0.1:5433/{name}", "host"),
            (f"postgresql://{user}@127.0.0.1/{name}", "host"),
            (f"postgresql://{user}@db.example:5432/{name}", "host"),
            (f"postgresql://{user}@127.0.0.1:5432/{name}?host=/tmp/x",
             "parameters"),
            (f"postgresql://{user}@127.0.0.1:5432/prod", "database"),
            (f"postgresql://{user}@127.0.0.1:5432/leerie_xyz_test",
             "database")):
        got = leerie._test_db_url_refusal(bad, bad)
        assert got and why in got, (bad, got)
    assert "DIRECT_URL" in leerie._test_db_url_refusal(good, good + "x")


def test_test_db_argv_wraps_only_a_prisma_postgres_repo(leerie, run):
    st, view = _st(run), _view(run)
    assert leerie._test_db_argv(st, view, "k") == []
    _prisma_repo(run["user"], "mysql")
    assert leerie._test_db_argv(st, view, "k") == []
    _prisma_repo(run["user"])
    got = leerie._test_db_argv(st, view, "k")
    assert got[:2] == ["bash", str(SANDBOX_DB)] and got[-1] == "--"
    assert got[2] == str(view)
    name = got[3]
    assert leerie._TEST_DB_NAME_RE.fullmatch(name)
    url = leerie._test_db_url(name)
    assert leerie._test_db_url_refusal(url, url) is None
    # One database per command: another key is another name.
    assert leerie._test_db_argv(st, view, "k2")[3] != name


def test_a_held_out_run_in_a_prisma_repo_is_wrapped_inside_its_sandbox(
        leerie, run, monkeypatch):
    st, view = _st(run), _view(run)
    _prisma_repo(run["user"])
    monkeypatch.setattr(leerie, "_acceptance_cmd", _bash_cmd)
    seen = {}

    async def fake(cmd, *, grants, sandbox=None, **kw):
        seen["cmd"], seen["sandbox"] = cmd, sandbox
        return 0, ""
    monkeypatch.setattr(leerie, "_run_streaming", fake)
    asyncio.run(leerie._run_acceptance_file(
        st, dict(leerie.DEFAULT_CAPS), str(view), "held.sh",
        run["run_dir"] / "logs" / "a.log", "acc",
        place=(("/x/held.sh", "held.sh"),)))
    assert seen["cmd"][:3] == ["bash", str(SANDBOX_DB), str(view)]
    assert seen["cmd"][4] == "--"
    assert "bash held.sh" in " ".join(seen["cmd"][5:])
    assert seen["sandbox"].place == (("/x/held.sh", "held.sh"),)


def test_a_worker_that_needs_a_database_is_started_through_the_wrapper(
        leerie, run, monkeypatch):
    """`claude_p(db=True)` in a Prisma/PostgreSQL repo: the worker's argv
    is `sandbox-db.sh <cwd> <name> -- claude …`, confined like any worker's
    (`_invoke` puts the shim in front); elsewhere `db` changes nothing. In
    a checkout of its own (the run repository's view), as a worker that
    does not commit runs, so no branch is taken after it."""
    rd, wt = run["run_dir"], _view(run)
    seen: list = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, *, grants, **_kw):
        seen.append(cmd)
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {
                    "title": "t", "body": "b", "used_template": None}}
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_append_system_prompt_file_supported",
                        lambda: False)
    (rd / "logs").mkdir(exist_ok=True)
    st = _St(run["user"], rd)
    st.path = rd / "state.json"
    st.data = {"verbosity": "quiet"}
    st.bump_workers = lambda *a, **k: None
    st.add_telemetry = lambda *a, **k: None

    def call(db: bool):
        asyncio.run(leerie.claude_p(
            user_prompt="u", system_prompt="s", schema_key="pr_writer",
            cwd=str(wt), allowed_tools=leerie.ACT_TOOLS, max_turns=1,
            autonomous=False, caps=dict(leerie.DEFAULT_CAPS), st=st,
            model="haiku", sid=f"w-{db}", db=db))
    call(True)
    assert seen[-1][0] == "claude"
    _prisma_repo(run["user"])
    call(False)
    assert seen[-1][0] == "claude"
    call(True)
    cmd = seen[-1]
    assert cmd[:3] == ["bash", str(SANDBOX_DB), str(wt)]
    assert cmd[4] == "--" and cmd[5] == "claude"
    assert not any(a.startswith("PRISMA_USER_CONSENT") for a in cmd)


def test_a_ci_check_that_requires_a_database_is_wrapped(leerie, run,
                                                        monkeypatch):
    st, view = _st(run), _view(run)
    _prisma_repo(run["user"])
    seen = []

    async def contained(argv, **kw):
        seen.append(argv)
        return {"rc": 0, "timed_out": False}
    monkeypatch.setattr(leerie, "_run_contained", contained)

    async def go(rec):
        return await leerie._ci_run(["npm", "test"], tree=view, rec=rec,
                                    st=st, caps=dict(leerie.DEFAULT_CAPS),
                                    label="c")
    asyncio.run(go({"id": "c"}))
    asyncio.run(go({"id": "c", "requires": ["database"]}))
    assert seen[0][:2] != ["bash", str(SANDBOX_DB)]
    assert seen[1][:2] == ["bash", str(SANDBOX_DB)] and \
        seen[1][5:] == seen[0], seen


def test_leerie_never_starts_a_server_or_runs_prisma_itself():
    """By rule over the module: no argv leerie builds names a PostgreSQL
    server tool or `prisma`, and the unconfined cluster's functions are
    gone; the one owner of both is `scripts/sandbox-db.sh`, which leerie
    only ever puts in front of a command it runs confined."""
    module = ast.parse(LEERIE_PY.read_text())
    names = {n.name for n in ast.walk(module)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    assert not names & {"_start_test_db_cluster", "_test_db_session",
                        "_create_test_db", "_drop_test_db",
                        "_stop_test_db_cluster", "_pg_server_bin"}
    tools = {"initdb", "pg_ctl", "postgres", "createdb", "dropdb", "psql"}
    for node in ast.walk(module):
        if isinstance(node, ast.List):
            words = {e.value for e in node.elts
                     if isinstance(e, ast.Constant)
                     and isinstance(e.value, str)}
            assert not words & tools, ast.unparse(node)[:120]
            assert not ({"db", "push"} <= words), ast.unparse(node)[:120]
    wraps = [c for c in ast.walk(module) if isinstance(c, ast.Call)
             and getattr(c.func, "id", None) == "_test_db_argv"]
    assert len(wraps) >= 4


def _real_pg() -> Path | None:
    bins = sorted(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
    return bins[-1].parent if bins else None


@pytest.fixture()
def pg_bin(tmp_path):
    """The server tools `scripts/sandbox-db.sh` will use: the newest real
    install when there is one (it prefers it), else stand-ins on PATH that
    do just enough for its steps."""
    real = _real_pg()
    if real is not None:
        return real
    bin_ = tmp_path / "pgbin"
    bin_.mkdir()
    (bin_ / "initdb").write_text(
        "#!/bin/sh\nwhile [ $# -gt 0 ]; do [ \"$1\" = -D ] && mkdir -p \"$2\";"
        " shift; done\n")
    (bin_ / "pg_ctl").write_text("#!/bin/sh\nexit 0\n")
    (bin_ / "createdb").write_text("#!/bin/sh\nexit 0\n")
    for f in bin_.iterdir():
        f.chmod(0o755)
    return bin_


@pytest.fixture()
def own_pg_dir():
    """`/tmp/leerie-pg`, granted as an isolated command's own `/tmp` would
    be: this host cannot isolate, so the test grants the one directory."""
    pg = Path("/tmp/leerie-pg")
    if os.path.lexists(pg):
        pytest.skip("/tmp/leerie-pg exists on this host")
    pg.mkdir(mode=0o700)
    yield pg
    shutil.rmtree(pg, ignore_errors=True)


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _wrapped(leerie, run, pg_bin, own_pg_dir, name, script):
    # Unisolated here, the server's 127.0.0.1:5432 is the host's: a host
    # with its own PostgreSQL there gives the script a failed start.
    try:
        socket.create_connection(("127.0.0.1", 5432), 1).close()
        pytest.skip("127.0.0.1:5432 is taken on this unisolated host")
    except OSError:
        pass
    view = _view(run)
    g = leerie._measurement_grants(_st(run), view)
    g = g._replace(rw=g.rw + (str(own_pg_dir),), ro=g.ro + (str(pg_bin),))
    argv = ["bash", str(SANDBOX_DB), str(view), name, "--", "bash", "-c",
            script]
    return subprocess.run(
        leerie._confined_argv(g, argv), cwd=str(view), capture_output=True,
        text=True, timeout=120,
        env={**os.environ, "PATH": f"{pg_bin}:{os.environ['PATH']}",
             **dict(g.env)})


def test_the_schema_push_runs_inside_the_commands_confinement(
        leerie, run, pg_bin, own_pg_dir):
    """F2, turned round (the measured design's §1.5): `prisma db push` —
    which loads the tree's own `prisma.config.ts`, a worker's file — runs
    as part of the confined command, under its Landlock domain: the
    tree's planted `prisma` finds `NoNewPrivs: 1` and cannot read the
    held-out sets, and the command it hands on gets the URL but never the
    consent."""
    view = _view(run)
    held = run["run_dir"] / "acceptance" / "set-1" / "test_held.py"
    prisma = view / "node_modules" / ".bin" / "prisma"
    prisma.parent.mkdir(parents=True)
    prisma.write_text(textwrap.dedent(f"""\
        #!/bin/sh
        grep NoNewPrivs /proc/self/status > "$PWD/push.out"
        if cat {held} >/dev/null 2>&1; then echo HELD-READ >> "$PWD/push.out"
        else echo HELD-DENIED >> "$PWD/push.out"; fi
        echo "consent=${{PRISMA_USER_CONSENT_FOR_DANGEROUS_AI_ACTION:+set}}" >> "$PWD/push.out"
        """))
    prisma.chmod(0o755)
    name = leerie._test_db_name("f2")
    r = _wrapped(leerie, run, pg_bin, own_pg_dir, name,
                 "echo URL=$DATABASE_URL; echo DIRECT=$DIRECT_URL; "
                 "echo CONSENT=${PRISMA_USER_CONSENT_FOR_DANGEROUS_AI_ACTION:-}")
    assert r.returncode == 0, r.stderr
    out = (view / "push.out").read_text()
    assert "NoNewPrivs:\t1" in out and "HELD-DENIED" in out, out
    assert "consent=set" in out
    url = leerie._test_db_url(name)
    assert f"URL={url}\nDIRECT={url}\nCONSENT=\n" in r.stdout, r.stdout


@pytest.mark.skipif(_real_pg() is None or os.geteuid() == 0,
                    reason="no PostgreSQL server here, or root")
def test_f1_the_commands_own_server_runs_nothing_outside_its_grant(
        leerie, run, pg_bin, own_pg_dir):
    """F1, turned round (the measured design's §1.4), with a real server:
    started by the wrapper inside the command's confinement, it answers the
    command over the plain TCP URL (`127.0.0.1:5432`, M2), runs `COPY … TO
    PROGRAM` only inside the same Landlock domain — so it cannot read
    `acceptance/` any more than the command can — and is gone when the
    command is."""
    view = _view(run)
    prisma = view / "node_modules" / ".bin" / "prisma"
    prisma.parent.mkdir(parents=True)
    prisma.write_text("#!/bin/sh\nexit 0\n")
    prisma.chmod(0o755)
    held = run["run_dir"] / "acceptance" / "set-1" / "test_held.py"
    leak = view / "leak.txt"
    psql = pg_bin / "psql"
    name = leerie._test_db_name("f1")
    r = _wrapped(leerie, run, pg_bin, own_pg_dir, name, (
        f"{psql} \"$DATABASE_URL\" -tAc 'select 41+1'; "
        f"{psql} \"$DATABASE_URL\" -c \"COPY (SELECT 1) TO PROGRAM "
        f"'cat {held} > {leak}'\" 2>&1; "
        "echo PIDFILE=$(head -1 /tmp/leerie-pg/data/postmaster.pid); "
        "awk '$4==\"0A\"' /proc/net/tcp /proc/net/tcp6 2>/dev/null "
        "| wc -l | sed 's/^/LISTEN-ANY=/'"))
    assert "42" in r.stdout.split(), (r.stdout, r.stderr)
    assert not leak.exists() or "held" not in leak.read_text(), r.stdout
    pid = int(r.stdout.split("PIDFILE=")[1].split()[0])
    assert not _alive(pid), pid
    # The "42" above came over the plain TCP URL (M2): the server
    # listened on the command's 127.0.0.1:5432.
    assert "@127.0.0.1:5432/" in leerie._test_db_url(name)


def test_the_script_refuses_a_name_that_is_not_a_test_database():
    r = subprocess.run(["bash", str(SANDBOX_DB), "/x", "prod", "--", "true"],
                       capture_output=True, text=True)
    assert r.returncode == 125 and "refused" in r.stderr


def test_without_its_own_tmp_a_command_runs_without_a_database(leerie,
                                                               run):
    """Confined but unisolated (no grant names `/tmp`), the script cannot
    make its cluster: it says so on stderr and runs the command unchanged,
    whose stdout is its own — and no server is left anywhere. Only ever run
    under the shim: unconfined, a host's real server would outlive it."""
    view = _view(run)
    g = leerie._measurement_grants(_st(run), view)
    r = subprocess.run(
        leerie._confined_argv(g, ["bash", str(SANDBOX_DB), str(view),
                                  leerie._test_db_name("none"), "--",
                                  "echo", "ran"]),
        cwd=str(view), capture_output=True, text=True, timeout=60,
        env={**os.environ, **dict(g.env)})
    assert r.returncode == 0 and r.stdout == "ran\n", r
    assert "without a database" in r.stderr


# --- a promised database is one that is there (review round 21, L2) ---------

def _fake_wrapper(tmp_path: Path, url_expr: str) -> Path:
    """A stand-in for `sandbox-db.sh` that hands its command `url_expr`
    (shell, `$2` the database name) as both URLs — or none when empty."""
    p = tmp_path / "fake-sandbox-db.sh"
    export = (f'export DATABASE_URL="{url_expr}" DIRECT_URL="{url_expr}"; '
              if url_expr else "")
    p.write_text(f"#!/usr/bin/env bash\n{export}shift 3\nexec \"$@\"\n")
    return p


def test_require_puts_the_guard_after_the_wrapper(leerie, run):
    st, view = _st(run), _view(run)
    _prisma_repo(run["user"])
    plain = leerie._test_db_argv(st, view, "k")
    got = leerie._test_db_argv(st, view, "k", require=True)
    assert got[:len(plain)] == plain
    url = leerie._test_db_url(plain[3])
    assert got[len(plain):] == ["bash", str(leerie.REQUIRE_TEST_DB_SCRIPT),
                                url, "--"]


@pytest.mark.parametrize("handed,present", [
    ("composed", True), ("other", False), ("none", False)])
def test_db_present_is_the_wrappers_outcome(leerie, run, monkeypatch,
                                            tmp_path, handed, present):
    """Executed under the shim: True only when the command was handed the
    URL the wrapper composes — another URL (a DATABASE_URL from elsewhere)
    or none (the wrapper's fallback) is no database."""
    st, view = _st(run), _view(run)
    _prisma_repo(run["user"])
    user = leerie._test_db_url("x").split("://", 1)[1].split("@", 1)[0]
    expr = {"composed": (f"postgresql://{user}@{leerie.TEST_DB_HOST}:"
                         f"{leerie.TEST_DB_PORT}/$2"),
            "other": "postgresql://u@db.example/prod", "none": ""}[handed]
    monkeypatch.setattr(leerie, "SANDBOX_DB_SCRIPT",
                        _fake_wrapper(tmp_path, expr))
    got = asyncio.run(leerie._test_db_present(
        st, view, "chk", log_path=run["run_dir"] / "logs" / "db.log"))
    assert got is present


def test_db_present_is_false_where_the_real_wrapper_falls_back(leerie, run):
    """The reviewer's measured case, with the real script: unisolated (no
    grant names `/tmp`), it cannot make its cluster and runs the command
    without a database — which the check reads as none."""
    st, view = _st(run), _view(run)
    _prisma_repo(run["user"])
    assert asyncio.run(leerie._test_db_present(
        st, view, "chk", log_path=run["run_dir"] / "logs" / "db.log")) is False


def test_db_present_spawns_nothing_outside_a_prisma_postgres_repo(
        leerie, run, monkeypatch):
    async def boom(*a, **k):
        raise AssertionError("spawned")
    monkeypatch.setattr(leerie, "_run_streaming", boom)
    assert asyncio.run(leerie._test_db_present(
        _st(run), _view(run), "chk", log_path=run["run_dir"] / "x.log")) \
        is False


def test_a_worker_promised_a_database_never_starts_without_it(
        leerie, run, monkeypatch):
    """`claude_p(db=True, db_required=True)`: the guard stands between the
    wrapper and `claude`; `db_required=False` leaves the plain wrapper."""
    rd, wt = run["run_dir"], _view(run)
    seen: list = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, *, grants, **_kw):
        seen.append(cmd)
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {
                    "title": "t", "body": "b", "used_template": None}}
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_append_system_prompt_file_supported",
                        lambda: False)
    (rd / "logs").mkdir(exist_ok=True)
    st = _St(run["user"], rd)
    st.path = rd / "state.json"
    st.data = {"verbosity": "quiet"}
    st.bump_workers = lambda *a, **k: None
    st.add_telemetry = lambda *a, **k: None
    _prisma_repo(run["user"])
    for required in (True, False):
        asyncio.run(leerie.claude_p(
            user_prompt="u", system_prompt="s", schema_key="pr_writer",
            cwd=str(wt), allowed_tools=leerie.ACT_TOOLS, max_turns=1,
            autonomous=False, caps=dict(leerie.DEFAULT_CAPS), st=st,
            model="haiku", sid=f"w-{required}", db=True,
            db_required=required))
    req, plain = seen
    assert req[4] == "--" and req[5:7] == [
        "bash", str(leerie.REQUIRE_TEST_DB_SCRIPT)]
    assert req[8] == "--" and req[9] == "claude"
    assert plain[4] == "--" and plain[5] == "claude"
