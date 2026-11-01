"""Leerie never writes through a path confined code shaped (DESIGN §6
*Leerie never writes through a path confined code shaped*; review round 21
isolation R1, R2, R5).

Confined code writes its trees, a committing tree's repository, its
measurement directory and its stores; leerie's own housekeeping around it is
unconfined. These pin the rule on both sides:

- the primitives (`_write_beneath`, `_read_beneath`, `_mkdir_private`,
  `_remove_beneath`, `_add_dir_bits_beneath`) never follow a link a confined
  process left, on the way or at the destination;
- R1: a held-out run's report is parsed from the sandbox's descriptor and is
  never written back to disk, where a measurement of the same tree planted a
  link in its `reports/`;
- R5: the measurement id and the settings restore, the two writes round 21
  redirected into `state.json` and a held-out set, now land only where they
  name;
- R2: the held-out side's workers write a cache set of their own, which no
  fixer's grant reaches;
- a rule over the module: every function that still uses a raw write
  primitive is classified, so a new one cannot appear unexamined.

The real-sandbox half of R1 needs a host that can build one and skips
elsewhere, as tests/test_heldout_isolation.py does."""
from __future__ import annotations

import ast
import asyncio
import os
import stat
import subprocess
import textwrap
import uuid
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, _grants, pytestmark as _landlock_mark, run)

LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


# --- the primitives ----------------------------------------------------------

def test_write_beneath_replaces_a_link_at_the_destination(leerie, tmp_path):
    anchor, victim = tmp_path / "a", tmp_path / "victim"
    anchor.mkdir()
    victim.write_text("victim\n")
    (anchor / "f").symlink_to(victim)
    leerie._write_beneath(anchor, anchor / "f", b"leerie\n")
    assert victim.read_text() == "victim\n"
    assert not (anchor / "f").is_symlink()
    assert (anchor / "f").read_bytes() == b"leerie\n"
    # Nothing but the file itself is left in the directory.
    assert sorted(os.listdir(anchor)) == ["f"]


def test_write_beneath_refuses_a_link_on_the_way(leerie, tmp_path):
    anchor, outside = tmp_path / "a", tmp_path / "outside"
    anchor.mkdir()
    outside.mkdir()
    (anchor / "d").symlink_to(outside)
    with pytest.raises(OSError):
        leerie._write_beneath(anchor, anchor / "d" / "f", b"x")
    assert list(outside.iterdir()) == []


def test_write_beneath_keeps_the_mode_of_the_file_it_replaces(leerie,
                                                              tmp_path):
    (tmp_path / "run.sh").write_text("old\n")
    (tmp_path / "run.sh").chmod(0o751)
    leerie._write_beneath(tmp_path, tmp_path / "run.sh", b"new\n")
    assert stat.S_IMODE((tmp_path / "run.sh").stat().st_mode) == 0o751


def test_read_beneath_refuses_a_link_and_a_fifo(leerie, tmp_path):
    secret = tmp_path / "secret"
    secret.write_text("held-out\n")
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "r").symlink_to(secret)
    with pytest.raises(OSError):
        leerie._read_beneath(tmp_path / "a", tmp_path / "a" / "r")
    os.mkfifo(tmp_path / "a" / "p")
    # Refused at once, never waited on for a writer.
    with pytest.raises(OSError):
        leerie._read_beneath(tmp_path / "a", tmp_path / "a" / "p")


def test_mkdir_private_replaces_a_planted_link_with_a_directory(leerie,
                                                                tmp_path):
    top, outside = tmp_path / "run", tmp_path / "outside"
    (top / "measure" / "t-1").mkdir(parents=True)
    outside.mkdir()
    (top / "measure" / "t-1" / "reports").symlink_to(outside)
    leerie._mkdir_private(top / "measure" / "t-1" / "reports" / "x", top)
    reports = top / "measure" / "t-1" / "reports"
    assert reports.is_dir() and not reports.is_symlink()
    assert (reports / "x").is_dir()
    assert list(outside.iterdir()) == []


def test_remove_beneath_never_removes_through_a_link(leerie, tmp_path):
    anchor, outside = tmp_path / "tree", tmp_path / "outside"
    anchor.mkdir()
    (outside / "keep").mkdir(parents=True)
    (outside / "keep" / "f").write_text("keep\n")
    (anchor / "d").symlink_to(outside)
    leerie._remove_beneath(anchor, anchor / "d" / "keep")
    assert (outside / "keep" / "f").read_text() == "keep\n"
    # A real directory beneath the anchor is removed whole.
    (anchor / "real" / "sub").mkdir(parents=True)
    (anchor / "real" / "sub" / "f").write_text("x\n")
    leerie._remove_beneath(anchor, anchor / "real")
    assert not (anchor / "real").exists()


def test_add_dir_bits_never_chmods_through_a_link(leerie, tmp_path):
    tree, outside = tmp_path / "tree", tmp_path / "outside"
    tree.mkdir()
    (outside / "inner").mkdir(parents=True)
    (outside / "inner").chmod(0o500)
    (tree / "d").symlink_to(outside)
    try:
        leerie._writable_dirs_to(tree, "d/inner/f")
        assert stat.S_IMODE((outside / "inner").stat().st_mode) == 0o500
    finally:
        (outside / "inner").chmod(0o700)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.email=t@x", "-c", "user.name=t", *args],
        cwd=str(cwd), capture_output=True, text=True, check=True).stdout


def test_restoring_a_tree_never_writes_through_a_link_the_command_left(
        leerie, tmp_path):
    """A command (the code under measurement) edits a tracked file, then
    replaces its directory with a link to a victim directory holding a file
    of the same name. Putting the tree back writes nothing through it."""
    tree = tmp_path / "tree"
    tree.mkdir()
    _git(tree, "init", "-q", "-b", "main")
    (tree / "pkg").mkdir()
    (tree / "pkg" / "mod.py").write_text("ORIGINAL = 1\n")
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", "base")
    (tree / "pkg" / "mod.py").write_text("EDITED = 1\n")    # a worker's edit
    before = leerie._tracked_content(tree)
    victim = tmp_path / "victim"
    victim.mkdir()
    (victim / "mod.py").write_text("victim\n")
    (tree / "pkg" / "mod.py").unlink()
    (tree / "pkg").rmdir()
    (tree / "pkg").symlink_to(victim)
    leerie._restore_tracked_content(tree, before)
    assert (victim / "mod.py").read_text() == "victim\n"


def test_a_set_is_never_placed_through_a_committed_link(leerie, tmp_path):
    """`_place_set` copies a set into a checkout of committed code: a
    directory of the set's path committed as a link is a taken path, and
    nothing lands at its target."""
    wt, outside = tmp_path / "wt", tmp_path / "fixers-tree"
    wt.mkdir()
    outside.mkdir()
    (wt / "tests").symlink_to(outside)
    sd = tmp_path / "set"
    (sd / "tests").mkdir(parents=True)
    (sd / "tests" / "test_held.py").write_text("HELD\n")
    copied, complete = leerie._place_set(
        {"dir": str(sd), "defect_files": ["tests/test_held.py"],
         "control_files": [], "support_files": []}, wt)
    assert copied == [] and complete is False
    assert list(outside.iterdir()) == []


# --- R1: a held-out run's report is never written back to disk --------------

def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


def _bash_cmd(_st, rels, _tree=None, placed=None):
    return f"bash {rels[0]}"


@_landlock_mark
def test_a_held_out_report_is_parsed_from_its_descriptor_never_from_disk(
        leerie, run, monkeypatch):
    """An ordinary, confined measurement of the view replaces `reports/`
    with a link to a directory of the view (one it can write). A held-out
    run of the same view (behind the emulator, whose command writes its
    report through that link) still gives its per-case verdict, from the
    descriptor's bytes, and leerie writes no copy of the report through the
    link: before this rule, `_run_streaming` wrote it back to the path, and
    with it whatever the evaluated code put in the report."""
    st, view = _St(run["user"], run["run_dir"]), _view(run)
    own = leerie._measure_dir(st, view)
    reports, planted = own / "reports", view / "planted"
    g = leerie._measurement_grants(st, view)
    rc, tail = asyncio.run(leerie._run_streaming(
        ["bash", "-c", f"mkdir -p {planted} && rm -rf {reports} && "
                       f"ln -s {planted} {reports} && echo PLANTED"],
        grants=g, cwd=str(view), timeout=60, verbosity="quiet"))
    assert rc == 0 and "PLANTED" in tail, tail
    assert reports.is_symlink()
    secret = f"HELD_OUT_{uuid.uuid4().hex[:12]}"
    d = run["run_dir"] / "acceptance" / "set-7"
    d.mkdir(parents=True)
    (d / "held.sh").write_text(textwrap.dedent(f"""\
        mkdir -p "$(dirname "$LEERIE_REPORT")"
        printf '%s' '{{"cases": {{"held.sh::a": "failed"}}, "s": "{secret}"}}' \\
            > "$LEERIE_REPORT"
        exit 1
        """))
    monkeypatch.setattr(leerie, "_acceptance_cmd", _bash_cmd)
    read = {}

    def spec(cmd, report, files):
        return (cmd, {**os.environ, "LEERIE_REPORT": str(report)}, "probe")

    def parse(kind, report):
        read["bytes"] = report
        return {"collection_error": False, "executed": 1,
                "cases": {"held.sh::a": "failed"}, "messages": {}}
    monkeypatch.setattr(leerie, "_acceptance_report_spec", spec)
    monkeypatch.setattr(leerie, "_parse_runner_report", parse)
    obs: dict = {}
    v = asyncio.run(leerie._run_acceptance_file(
        st, dict(leerie.DEFAULT_CAPS), str(view), "held.sh",
        run["run_dir"] / "logs" / "acc.log", "acc", cases_out=obs,
        place=((str(d / "held.sh"), "held.sh"),)))
    assert v is False and obs["failing_cases"] == ["held.sh::a"], obs
    assert secret.encode() in read["bytes"]
    # The emulator removed the in-sandbox copy; leerie put none back.
    assert [p for p in planted.rglob("*") if p.is_file()] == []


@_landlock_mark
@pytest.mark.real_heldout_sandbox
def test_the_set_never_reaches_a_worker_tree_through_the_report(
        leerie, run, monkeypatch):
    """Review round 21 R1's proof, inverted: in a real sandbox, a held-out
    run's report (the set's text, copied in by the evaluated code) does not
    land in the implementer's tree through the link an ordinary measurement
    planted in `reports/`."""
    lk = leerie._landlock()
    why = lk.isolation_error() if hasattr(lk, "isolation_error") else "old"
    if why is not None:
        pytest.skip(f"this host cannot build a sandbox: {why}")
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    st = _St(run["user"], run["run_dir"])
    view, worker_tree = _view(run), run["wts"]["wt-a"]
    secret = f"HELD_OUT_ASSERTION_{uuid.uuid4().hex[:12]}"
    reports = leerie._measure_dir(st, view) / "reports"
    g = leerie._measurement_grants(st, view)
    rc, tail = asyncio.run(leerie._run_streaming(
        ["bash", "-c", f"rm -rf {reports} && ln -s {worker_tree} {reports}"
                       " && echo PLANTED"],
        grants=g, cwd=str(view), timeout=60, verbosity="quiet"))
    assert rc == 0 and "PLANTED" in tail, tail
    vb = view / "node_modules" / ".bin"
    vb.mkdir(parents=True)
    (vb / "vitest").write_text(
        "#!/bin/bash\n"
        "out=; held=\n"
        "for a in \"$@\"; do case $a in --outputFile=*) out=${a#*=};;"
        " *.test.ts) held=$a;; esac; done\n"
        "d=$(dirname \"$out\"); rm -f \"$d\"; mkdir -p \"$d\"\n"
        "cat \"$held\" > \"$out\"\nexit 1\n")
    (vb / "vitest").chmod(0o755)
    monkeypatch.setattr(leerie, "_acceptance_cmd",
                        lambda _st, rels, _tree=None, placed=None:
                        f"node_modules/.bin/vitest run {rels[0]}")
    d = run["run_dir"] / "acceptance" / "set-7"
    d.mkdir(parents=True, exist_ok=True)
    (d / "held.test.ts").write_text(f"// {secret}\n")
    asyncio.run(leerie._run_acceptance_file(
        st, dict(leerie.DEFAULT_CAPS), str(view), "held.test.ts",
        run["run_dir"] / "logs" / "acc.log", "acc", cases_out={},
        place=((str(d / "held.test.ts"), "held.test.ts"),)))
    assert not (view / "held.test.ts").exists()
    leaked = [p for p in worker_tree.rglob("*") if p.is_file()
              and secret in p.read_text(errors="replace")]
    assert leaked == []


# --- R5: leerie's records in a tree repository ------------------------------

@_landlock_mark
def test_a_planted_measure_id_link_redirects_nothing(leerie, run):
    """Review round 21 R5 site 1, inverted: the implementer, confined by its
    real grant, swaps its tree's measurement id for a link to a held-out set
    and then to `state.json`; leerie's next grant for the tree writes
    neither, and the id is a file of its own again."""
    st = _St(run["user"], run["run_dir"])
    tree = run["wts"]["wt-a"]
    held = run["run_dir"] / "acceptance" / "set-1" / "test_held.py"
    state = run["run_dir"] / "state.json"
    before_held, before_state = held.read_text(), state.read_text()
    first = leerie._measure_dir(st, tree)
    assert leerie._measure_dir(st, tree) == first     # the id persists
    loc = leerie._run_tree_location(tree)
    id_file = Path(loc[1]) / leerie._MEASURE_ID_FILE
    assert id_file.is_file()
    g = _grants(leerie, run, schema_key="implementer")
    for target in (held, state):
        r = subprocess.run(
            leerie._confined_argv(g, ["bash", "-c",
                                      f"rm -f {id_file} && "
                                      f"ln -s {target} {id_file} && echo ok"]),
            capture_output=True, text=True, cwd=str(tree),
            env={**os.environ, **run["env"]})
        assert "ok" in r.stdout, r.stderr
        leerie._measurement_grants(st, tree)
    assert held.read_text() == before_held
    assert state.read_text() == before_state
    assert id_file.is_file() and not id_file.is_symlink()
    assert leerie._MEASURE_ID_RE.fullmatch(id_file.read_text().strip())


@_landlock_mark
def test_a_planted_settings_temp_link_redirects_nothing(leerie, run):
    """Review round 21 R5 site 2, inverted: the worker changes its tree
    repository's `config` and plants a link at the name the restore used
    for its temp copy; the restore puts the config back and writes nothing
    through any link."""
    tree = run["wts"]["wt-a"]
    loc = leerie._run_settings_of(tree)
    if loc is None or not (loc[1] / "config").is_file():
        pytest.skip("no settings copy in this fixture")
    common, s = loc
    state = run["run_dir"] / "state.json"
    before = state.read_text()
    g = _grants(leerie, run, schema_key="implementer")
    r = subprocess.run(
        leerie._confined_argv(g, ["bash", "-c",
                                  f"echo '[x]' >> {common}/config && "
                                  f"ln -s {state} "
                                  f"{common}/.config.leerie-{os.getpid()} && "
                                  "echo ok"]),
        capture_output=True, text=True, cwd=str(tree),
        env={**os.environ, **run["env"]})
    assert "ok" in r.stdout, r.stderr
    assert "config" in leerie._restore_settings(tree)
    assert state.read_text() == before
    assert (common / "config").read_bytes() == (s / "config").read_bytes()
    assert not (common / "config").is_symlink()


# --- R2: the held-out side has its own cache set ----------------------------

def test_each_worker_kind_writes_its_sides_cache_set_only(leerie, tmp_path):
    """Derived from the worker kinds: a held-out-side worker
    (`HELD_OUT_LOG_WORKERS`) is granted the held-out cache set and never the
    fixers', every other kind the fixers' and never the held-out one; the
    environment `_invoke` gives it follows its grant."""
    run_dir = tmp_path / "state" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    shared = run_dir / "workers" / leerie.WORKER_CACHE_DIR
    held = run_dir / "workers" / leerie.HELD_OUT_CACHE_DIR
    assert leerie.HELD_OUT_LOG_WORKERS <= set(leerie.WORKER_TYPES)
    for kind in leerie.WORKER_TYPES:
        g = leerie._worker_grants(
            run_dir=run_dir, state={}, schema_key=kind, cwd=tmp_path,
            allowed_tools=leerie.PAYLOAD_ONLY_TOOLS, env={})
        rw = {os.path.realpath(p) for p in g.rw}
        own, other = ((held, shared) if kind in leerie.HELD_OUT_LOG_WORKERS
                      else (shared, held))
        assert os.path.realpath(own) in rw, kind
        assert os.path.realpath(other) not in rw, kind
        env = leerie._worker_cache_env(
            {}, run_dir, leerie._granted_cache_dir(g, run_dir))
        assert Path(env["XDG_CACHE_HOME"]).is_relative_to(own), kind


@_landlock_mark
def test_a_held_out_side_workers_cache_is_never_a_fixers(leerie, run):
    """Review round 21 R2's proof, inverted: a confined acceptance writer's
    processes leave the held-back set's text in their `GOCACHE` (as `go
    test` leaves its compiled package there); a later confined implementer,
    with its real grant and the environment `_invoke` gives it, has another
    `GOCACHE`, and cannot read the writer's."""
    run_dir = run["run_dir"]
    secret = f"HELD_BACK_ASSERTION_{uuid.uuid4().hex[:12]}"
    writer_cwd = run_dir / "worktrees" / "acc-writer-clone"
    writer_cwd.mkdir(parents=True)

    def env_for(wid: str, g) -> dict:
        own = run_dir / "workers" / wid
        env = {"TMPDIR": str(own / "tmp"),
               "CLAUDE_CONFIG_DIR": str(own / "config")}
        for p in env.values():
            Path(p).mkdir(parents=True, exist_ok=True)
        return leerie._worker_cache_env(
            {**os.environ, **env}, run_dir,
            leerie._granted_cache_dir(g, run_dir))

    gw = leerie._worker_grants(
        run_dir=run_dir, state={}, schema_key="acceptance_writer",
        cwd=writer_cwd, allowed_tools=leerie.ACT_TOOLS,
        env={"TMPDIR": str(run_dir / "workers" / "acc-w" / "tmp")})
    wenv = env_for("acc-w", gw)
    w = subprocess.run(
        leerie._confined_argv(gw, ["bash", "-c",
                                   'mkdir -p "$GOCACHE/ab" && '
                                   f'echo {secret} > "$GOCACHE/ab/x-d"']),
        capture_output=True, text=True, env=wenv, cwd=str(writer_cwd))
    assert w.returncode == 0, w.stderr
    gi = _grants(leerie, run, schema_key="implementer")
    ienv = env_for("impl-1", gi)
    assert ienv["GOCACHE"] != wenv["GOCACHE"]
    r = subprocess.run(
        leerie._confined_argv(gi, ["bash", "-c",
                                   f'cat "$GOCACHE/ab/x-d" '
                                   f'"{wenv["GOCACHE"]}/ab/x-d"']),
        capture_output=True, text=True, env=ienv,
        cwd=str(run["wts"]["wt-a"]))
    assert secret not in r.stdout


# --- the rule over the module -----------------------------------------------

# Every function of leerie.py that still calls a raw write primitive, and
# why each target is out of every confined process's reach, or is the
# primitive itself. A new function that writes raw fails here until it is
# classified, or moved onto the `_beneath` primitives (DESIGN §6 *Leerie
# never writes through a path confined code shaped*).
_PRIMITIVES = {
    "_open_dir_beneath", "_write_beneath", "_unlink_beneath",
    "_remove_beneath", "_rmdirs_beneath", "_copytree_beneath",
    "_copy_out_of_tree", "_add_dir_bits_beneath", "_mkdir_private",
    "_rmtree_owned", "_own_private_dir",
}
_LEERIE_ONLY = {
    # The run's own files, logs, prompts, records and repository, which no
    # grant names for writing (`_MEASURE_PRIVATE`; DESIGN §6 *A worker
    # writes only where it is granted (I5)*).
    "__init__", "save", "main", "_write_run_json", "_write_plan",
    "_write_subtask_artifacts", "_settle_subtask", "gather_answers",
    "_absorb_supplied_answers", "_surface_clarification", "phase_judge",
    "_replay_and_judge_sample", "_replay_capture", "_heal_apply_patch",
    "_heal_baseline", "_heal_replay_patched", "_write_heal_report",
    "_install_run_log_tee", "_run_streaming", "_register_worker_transcript",
    "_capture_call", "_write_memory_sample", "_invoke", "claude_p",
    "_append_system_prompt_file_supported", "_write_system_prompt_file",
    "_ci_run", "_tsc_lists_paths", "_run_setup_hook", "_run_mise_install",
    "phase_provision", "_synth_mise_go_override", "_ensure_run_repo",
    "_take_run_settings", "_take_host_settings", "_make_clone_git_dir",
    "_claude_config_template", "_worker_dirs", "_record_run_id_for_launcher",
    "_record_capture", "apply_captured_deps", "capture_repo_deps",
    "run_recapture_deps", "_build_repo_map", "_tree_sitter_extraction_works",
    "_selfcheck_prepare", "_require_heldout_sandbox", "_leerie_stores",
    "_store_fetch_lock", "_acquire_build_slot", "_populate_leerie_store",
    "_sweep_measure_dirs", "_measure_dir_at", "_reap_idle_worktrees",
    "_acceptance_parse_probe", "_acceptance_parse_probe_run",
    "_falsifier_gates", "_drop_lying_index", "_move_view",
    # A grant root itself, whose entry lies in a directory no grant names:
    # making it follows no link a confined process could have planted.
    "_payload_only_cwd", "_acceptance_scope_judge_sets",
    "_ratchet_verifiability", "_worker_grants", "_measurement_grants",
    "preflight",
    # Host-side: the user's checkout, outside the container.
    "_write_config_toml_keys",
}
_CONFINED = {
    # Runs inside a confined worker's process tree (the re-drive's
    # self-check, `scripts/selfcheck.py`), under that worker's grant.
    "selfcheck_main", "_share_ignored_paths",
}


def _raw_writers() -> dict[str, set[str]]:
    os_ = {"replace", "rename", "unlink", "remove", "symlink", "link",
           "mkdir", "makedirs", "chmod", "rmdir", "removedirs", "truncate"}
    sh = {"copy", "copy2", "copyfile", "copytree", "move"}
    attr = {"write_text", "write_bytes", "mkdir", "unlink", "touch", "chmod",
            "symlink_to", "rename", "hardlink_to"}

    def writes(mode: ast.AST | None) -> bool:
        return (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                and bool(set(mode.value) & set("wax+")))

    def raw(call: ast.Call) -> str | None:
        f = call.func
        if isinstance(f, ast.Attribute):
            base = f.value.id if isinstance(f.value, ast.Name) else None
            if base == "os":
                return f"os.{f.attr}" if f.attr in os_ else None
            if base == "shutil":
                return f"shutil.{f.attr}" if f.attr in sh else None
            if f.attr in attr:
                return f".{f.attr}"
            if f.attr == "open" and writes(
                    call.args[0] if call.args else None):
                return ".open"
        if isinstance(f, ast.Name) and f.id == "open" and writes(
                call.args[1] if len(call.args) > 1 else None):
            return "open"
        return None

    out: dict[str, set[str]] = {}

    def walk(node: ast.AST, fn: str | None) -> None:
        for ch in ast.iter_child_nodes(node):
            if isinstance(ch, (ast.FunctionDef, ast.AsyncFunctionDef)):
                walk(ch, fn or ch.name)
                continue
            if isinstance(ch, ast.Call) and (r := raw(ch)):
                out.setdefault(fn or "<module>", set()).add(r)
            walk(ch, fn)
    walk(ast.parse(LEERIE_PY.read_text()), None)
    return out


def test_every_raw_write_is_classified():
    found = _raw_writers()
    known = _PRIMITIVES | _LEERIE_ONLY | _CONFINED
    unclassified = sorted(set(found) - known)
    assert not unclassified, (
        "these functions write with a raw primitive and are not classified: "
        f"{unclassified}. Write beneath the target's anchor "
        "(`_write_beneath`, `_unlink_beneath`, `_remove_beneath`, "
        "`_copy_beneath`, `_mkdir_private`) when a confined process can "
        "write any directory on its path; else add it here, saying why.")
    # The converse: an entry whose function no longer writes raw is stale.
    assert sorted(known - set(found)) == []


def test_the_converted_sites_write_only_beneath():
    """The functions that write where confined code writes — a tree, a tree
    repository, a measurement directory — use no raw write primitive."""
    found = _raw_writers()
    for fn in ("_restore_tracked_content", "_remove_new_untracked",
               "_restore_settings", "_place_set", "_unplace",
               "_copy_task_references", "_checkout_ignore",
               "_falsifier_place_leads", "_falsifier_save_probe",
               "_collect_acceptance_set", "_stage_worktree_extras",
               "_overlay_other_side", "_try_json_key_merge",
               "_measure_subtask_coverage", "_vitest_run",
               "_capture_failing_test_ids", "_run_acceptance_file",
               "_probe_case_facts_placed", "_acceptance_report_spec",
               "_redrive_ast_hits", "_rescue_integrator_work",
               "_writable_dirs_to", "_restore_write_bits",
               "_tree_store_env", "_cache_vars"):
        assert fn not in found, (fn, found.get(fn))
