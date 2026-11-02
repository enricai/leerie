"""Leerie never reads through a link a repository holds (DESIGN §6 *Leerie
never reads through a link a repository holds*; review round 26 isolation
I1).

The orchestrator is unconfined: it holds the CLI's credentials in its
environment and in `~/.claude`, and the state root with every held-out set.
What it reads from a repository it hands to workers and publishes in the PR
body. A repository's content is a worker's to shape (a merged run branch, a
cloned third-party repository, a worker's own tree), and a committed link
read through the filesystem reads whatever it points at. These pin:

- the primitives (`_read_text_beneath`, `_no_link_beneath`,
  `_dir_beneath_exists`, `_listdir_beneath`, `_glob_beneath`) never follow a
  link, on the way or at the end;
- I1, inverted: the PR template is the blob committed at the run's base in
  the run's own repository, so a link at its path in the checkout — at the
  file, or at a directory on the way — puts nothing it points at in the
  `pr_writer` payload, from a credentials file or the orchestrator's own
  environment;
- the sweep: each converted reader refuses a link where it used to follow
  one;
- a rule over the module: every function that still reads with a raw
  primitive is classified, so a new one cannot appear unexamined."""
from __future__ import annotations

import ast
import asyncio
import json
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.conftest import run_git_cwd_first_stdout as _git  # noqa: E402

LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
SECRET = "sk-ant-oat01-R26-PROOF-SECRET"


# --- the primitives ----------------------------------------------------------

def test_read_text_beneath_refuses_a_link_at_the_file_and_on_the_way(
        leerie, tmp_path):
    secret = tmp_path / "outside" / "s"
    secret.parent.mkdir()
    secret.write_text(SECRET)
    root = tmp_path / "repo"
    (root / "d").mkdir(parents=True)
    (root / "d" / "f").symlink_to(secret)
    (root / "ld").symlink_to(secret.parent)
    (root / "ok").write_text("ok\n")
    for p in (root / "d" / "f", root / "ld" / "s"):
        with pytest.raises(OSError):
            leerie._read_text_beneath(root, p, limit=64)
    assert leerie._read_text_beneath(root, root / "ok", limit=64) == "ok\n"


def test_no_link_beneath_glob_and_listdir(leerie, tmp_path):
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    (outside / "x.yml").write_text("x")
    root = tmp_path / "repo"
    (root / "real").mkdir(parents=True)
    (root / "real" / "a.yml").write_text("a")
    (root / "real" / "b.yml").symlink_to(outside / "x.yml")
    (root / "linked").symlink_to(outside)
    assert leerie._no_link_beneath(root, root / "real" / "a.yml")
    assert not leerie._no_link_beneath(root, root / "real" / "b.yml")
    assert not leerie._no_link_beneath(root, root / "linked" / "x.yml")
    assert leerie._no_link_beneath(root, root)
    assert [p.name for p in leerie._glob_beneath(root, "*/*.yml")] == ["a.yml"]
    assert leerie._dir_beneath_exists(root, root / "real")
    assert not leerie._dir_beneath_exists(root, root / "linked")
    assert sorted(leerie._listdir_beneath(root, root / "real")) == [
        "a.yml", "b.yml"]
    with pytest.raises(OSError):
        leerie._listdir_beneath(root, root / "linked")


# --- I1: the PR template -----------------------------------------------------

def _checkout_and_run(tmp_path: Path, plant) -> tuple[Path, types.SimpleNamespace]:
    """A checkout whose committed content `plant(checkout)` shapes, and a
    run of it: its own repository (a bare clone) and `base_sha`."""
    co = tmp_path / "work"
    co.mkdir()
    _git(co, "init", "-q", "-b", "main")
    (co / "README.md").write_text("hi\n")
    plant(co)
    _git(co, "add", "-A")
    _git(co, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "base")
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    subprocess.run(["git", "clone", "-q", "--bare", str(co),
                    str(run_dir / "repo")], check=True, timeout=60)
    (run_dir / "run.json").write_text(json.dumps({"no_push": False}))
    st = types.SimpleNamespace(
        run_dir=run_dir, run_id="r26",
        data={"task": "t", "worker_count": 0,
              "base_sha": _git(co, "rev-parse", "HEAD")},
        bump_workers=lambda caps: None)
    return co, st


def _template_payload(leerie, monkeypatch, co: Path,
                      st: types.SimpleNamespace) -> dict | None:
    """Drive the resume-time entry (`_compose_pr_for_a_push_chosen_after_
    finalize`) from the checkout with `claude_p` stubbed; the `template`
    field the worker was handed."""
    seen: dict = {}

    async def fake_claude_p(**kw):
        seen["payload"] = json.loads(kw["user_prompt"])
        return {"title": "x", "body": "y"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.chdir(co)
    asyncio.run(leerie._compose_pr_for_a_push_chosen_after_finalize(
        st, no_push=False, caps={"max_total_workers": 10}, models={},
        efforts={}, pr_template_override=None))
    return seen["payload"]["template"]


@pytest.mark.parametrize("where", ["file", "directory"])
def test_a_template_link_to_a_credentials_file_reaches_no_payload(
        leerie, monkeypatch, tmp_path, where):
    """Review round 26 I1's proof, inverted: a committed link at the PR
    template's path, to a credentials-shaped file outside the checkout (the
    CLI's `~/.claude/.credentials.json`, a held-out set under the state
    root). The worker is handed no template, and nothing of the target."""
    secret = tmp_path / "outside" / ".credentials.json"
    secret.parent.mkdir()
    secret.write_text(json.dumps({"claudeAiOauth": {"accessToken": SECRET}}))
    (tmp_path / "outside" / "pull_request_template.md").write_text(SECRET)

    def plant(co: Path) -> None:
        if where == "file":
            (co / ".github").mkdir()
            (co / ".github" / "pull_request_template.md").symlink_to(secret)
        else:
            (co / ".github").symlink_to(secret.parent)

    co, st = _checkout_and_run(tmp_path, plant)
    # The link is there in the checkout the orchestrator runs in.
    assert SECRET in (co / ".github" / "pull_request_template.md"
                      ).read_text()
    tpl = _template_payload(leerie, monkeypatch, co, st)
    assert tpl is None
    assert SECRET not in json.dumps(tpl)


def test_a_template_link_to_proc_environ_reaches_no_payload(tmp_path):
    """The orchestrator's own environment (`CLAUDE_CODE_OAUTH_TOKEN`)
    through `/proc/self/environ`, a fixed path a repository names without
    knowing anything. Driven in a child whose initial environment holds the
    token, which is what that file shows; the token is never printed."""
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-k", "environ_child", __file__],
        cwd=REPO_ROOT, capture_output=True, text=True, timeout=300,
        env={**os.environ, "CLAUDE_CODE_OAUTH_TOKEN": SECRET,
             "R26_ENVIRON_CHILD": "1"})
    assert r.returncode == 0, r.stdout[-2000:].replace(SECRET, "<secret>")
    assert "1 passed" in r.stdout


def test_environ_child(leerie, monkeypatch, tmp_path):
    if not os.environ.get("R26_ENVIRON_CHILD"):
        pytest.skip("runs only as the child of the test above")

    def plant(co: Path) -> None:
        (co / ".github").mkdir()
        (co / ".github" / "pull_request_template.md").symlink_to(
            "/proc/self/environ")

    co, st = _checkout_and_run(tmp_path, plant)
    # Control: read through the filesystem, the link is the secret.
    assert SECRET in (co / ".github" / "pull_request_template.md"
                      ).read_text(errors="replace")
    tpl = _template_payload(leerie, monkeypatch, co, st)
    assert tpl is None
    assert SECRET not in json.dumps(tpl)


def test_the_committed_template_reaches_the_payload(leerie, monkeypatch,
                                                    tmp_path):
    """The template the worker fills is the one committed at the run's
    base, read from the run's own repository: a regular blob is handed over
    whole, and an edit left uncommitted in the checkout is not."""
    def plant(co: Path) -> None:
        (co / ".github").mkdir()
        (co / ".github" / "pull_request_template.md").write_text(
            "## Summary\n")

    co, st = _checkout_and_run(tmp_path, plant)
    (co / ".github" / "pull_request_template.md").write_text("uncommitted\n")
    tpl = _template_payload(leerie, monkeypatch, co, st)
    assert tpl == {"path": ".github/pull_request_template.md",
                   "content": "## Summary\n", "truncated": False}


def test_a_link_in_the_multi_template_directory_is_not_a_template(
        leerie, tmp_path):
    """In a `PULL_REQUEST_TEMPLATE/` directory a committed link is skipped,
    so the alphabetically first regular template wins over it."""
    secret = tmp_path / "secret.md"
    secret.write_text(SECRET)

    def plant(co: Path) -> None:
        d = co / ".github" / "PULL_REQUEST_TEMPLATE"
        d.mkdir(parents=True)
        (d / "a.md").symlink_to(secret)
        (d / "b.md").write_text("b\n")

    _co, st = _checkout_and_run(tmp_path, plant)
    assert leerie._find_pr_template(
        st.run_dir / "repo", st.data["base_sha"]) == (
        ".github/PULL_REQUEST_TEMPLATE/b.md", "b\n", False)


# --- the sweep ---------------------------------------------------------------

def _secret(tmp_path: Path) -> Path:
    p = tmp_path / "outside" / "secret"
    p.parent.mkdir(exist_ok=True)
    p.write_text(f'model = "{SECRET}"\n{SECRET}\n')
    return p


def test_config_files_are_read_and_written_never_through_a_link(
        leerie, tmp_path):
    secret = _secret(tmp_path)
    root = tmp_path / "repo"
    root.mkdir()
    (root / "leerie.toml").symlink_to(secret)
    assert leerie._read_toml_key(root / "leerie.toml", "model") is None
    (root / ".leerie").symlink_to(secret.parent)
    assert leerie._read_toml_key(root / ".leerie" / "secret", "model") is None
    # The host-side upsert: a link at `.leerie` refuses the write, and a
    # link at the file is replaced, its target neither read nor written.
    with pytest.raises(OSError):
        leerie._write_config_toml_keys(root / ".leerie" / "config.toml",
                                       {"build": "make"})
    assert not (secret.parent / "config.toml").exists()
    (root / ".leerie").unlink()
    (root / ".leerie").mkdir()
    (root / ".leerie" / "config.toml").symlink_to(secret)
    leerie._write_config_toml_keys(root / ".leerie" / "config.toml",
                                   {"build": "make"})
    cfg = root / ".leerie" / "config.toml"
    assert not cfg.is_symlink()
    assert SECRET not in cfg.read_text() and 'build = "make"' in \
        cfg.read_text()
    assert secret.read_text().startswith('model = "')


def test_repository_inputs_to_workers_skip_a_link(leerie, tmp_path):
    secret = _secret(tmp_path)
    root = tmp_path / "repo"
    (root / ".github" / "workflows").mkdir(parents=True)
    (root / "README.md").symlink_to(secret)
    (root / "package.json").symlink_to(secret)
    (root / ".github" / "workflows" / "ci.yml").symlink_to(secret)
    (root / "CONTRIBUTING.md").symlink_to(secret)
    (root / "go.mod").symlink_to(secret)
    fx = leerie._gather_provision_fixtures(root)
    assert SECRET not in json.dumps(fx)
    assert SECRET not in leerie._gather_dep_manifests(root)
    assert leerie._package_json_scripts(root) == {}
    assert leerie._ci_config_files(root) == []
    assert leerie._ci_check_problems(
        {"source": {"file": ".github/workflows/ci.yml"}, "argv": [SECRET]},
        root) == ["source file missing or outside the repo"]


def test_task_references_and_task_file_skip_a_link(leerie, monkeypatch,
                                                   tmp_path):
    secret = _secret(tmp_path)
    root = tmp_path / "repo"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "spec.md").symlink_to(secret)
    (root / "docs" / "real.md").write_text("real\n")
    got = leerie._glob_task_references("see docs/*.md", root)
    assert [p.name for p in got] == ["real.md"]
    (root / "TASK.md").symlink_to(secret)
    monkeypatch.chdir(root)
    with pytest.raises(SystemExit):
        leerie.resolve_task_argument("TASK.md")


def test_a_checkpoint_outside_the_notes_directory_is_not_read(leerie,
                                                              tmp_path):
    secret = _secret(tmp_path)
    notes = tmp_path / "notes" / "s1"
    notes.mkdir(parents=True)
    err = leerie._validate_checkpoint(str(secret), notes_dir=notes)
    assert err and SECRET not in err
    (notes / "checkpoint.md").symlink_to(secret)
    err = leerie._validate_checkpoint(str(notes / "checkpoint.md"),
                                      notes_dir=notes)
    assert err and SECRET not in err


def test_project_settings_are_read_never_through_a_link(leerie, tmp_path):
    secret = tmp_path / "outside" / "settings.json"
    secret.parent.mkdir()
    secret.write_text(json.dumps({"env": {"X": SECRET}}))
    root = tmp_path / "repo"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").symlink_to(secret)
    assert leerie._settings_file(root / ".claude" / "settings.json",
                                 root=root) == {}


@pytest.mark.parametrize("link", [".claude/settings.json",
                                  ".claude/settings.local.json", ".claude"])
def test_the_launcher_never_reads_claude_settings_through_a_link(
        tmp_path, link):
    """Review round 27, execution L2: `detect_bedrock_mode` and
    `bedrock_preflight` `json.load(open(...))` the checkout's
    `.claude/settings{,.local}.json`; one linked to a file outside the
    checkout switched the run into Bedrock mode. The launcher now refuses
    the link before any verb reads it (`_checkout_config_links`)."""
    outside = tmp_path / "outside"
    outside.mkdir()
    for name in ("settings.json", "settings.local.json"):
        (outside / name).write_text(json.dumps(
            {"env": {"CLAUDE_CODE_USE_BEDROCK": "1"}}))
    home, co = tmp_path / "home", tmp_path / "checkout"
    home.mkdir()
    subprocess.run(["git", "init", "-q", str(co)], check=True, timeout=60)
    if link == ".claude":
        (co / ".claude").symlink_to(outside)
    else:
        (co / ".claude").mkdir()
        (co / link).symlink_to(outside / Path(link).name)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("LEERIE_")}
    env["HOME"] = str(home)
    r = subprocess.run([str(REPO_ROOT / "leerie"), "list"], cwd=co, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert f"{link} in {co} is a symbolic link" in r.stderr, r.stderr
    # Control: the same content as a regular file is the checkout's own.
    (co / ".claude").unlink() if link == ".claude" else (co / link).unlink()
    (co / ".claude").mkdir(exist_ok=True)
    (co / ".claude" / "settings.json").write_text("{}")
    r = subprocess.run([str(REPO_ROOT / "leerie"), "list"], cwd=co, env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr


def _sparse(path: Path, size: int = 1 << 40) -> Path:
    """A file of `size` bytes that uses no disk, as a worker makes one with
    `truncate -s 1T`."""
    with open(path, "wb") as f:
        f.truncate(size)
    return path


def test_a_sparse_checkpoint_is_refused_not_read(leerie, tmp_path):
    """Review round 27, isolation I3, inverted: `_read_beneath` read the
    whole file, so a 1 TiB sparse checkpoint raised `MemoryError` out of
    `_validate_checkpoint` (or had the container OOM-killed). Its size is
    now checked on the opened descriptor, and the gate reports it."""
    notes = tmp_path / "notes" / "s1"
    notes.mkdir(parents=True)
    cp = _sparse(notes / "checkpoint.md")
    assert os.stat(cp).st_blocks == 0
    err = leerie._validate_checkpoint(str(cp), notes_dir=notes)
    assert isinstance(err, str) and f"is over {leerie.READ_LIMIT_CONFIG} bytes" in err, err


def test_a_budgeted_read_reads_only_its_budget(leerie, tmp_path):
    _sparse(tmp_path / "package.json")
    got = leerie._read_file_safely(tmp_path, tmp_path / "package.json", 4000)
    assert got == "\0" * 4000


def test_the_limit_is_exact(leerie, tmp_path):
    import errno
    f = tmp_path / "f"
    f.write_bytes(b"x" * 10)
    assert leerie._read_beneath(tmp_path, f, limit=10) == b"x" * 10
    with pytest.raises(OSError) as e:
        leerie._read_beneath(tmp_path, f, limit=9)
    assert e.value.errno == errno.EFBIG
    assert leerie._read_head_beneath(tmp_path, f, limit=4) == b"xxxx"
    with pytest.raises(TypeError):
        leerie._copy_beneath(f, tmp_path, tmp_path / "g", src_anchor=tmp_path)


_LIMITED = {"_read_beneath", "_read_text_beneath", "_read_head_beneath"}


def test_every_read_beneath_states_its_limit():
    """Every call of the readers passes `limit=`, and every copy read beneath
    a `src_anchor` does too: no read of a repository's file is unbounded
    (DESIGN §6 *No read is unbounded*)."""
    tree = ast.parse(LEERIE_PY.read_text())
    seen, bad = 0, []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", None)
        kws = {k.arg for k in node.keywords}
        if name in _LIMITED or (name in ("_copy_beneath", "_copytree_beneath")
                                and "src_anchor" in kws):
            seen += 1
            if "limit" not in kws:
                bad.append(node.lineno)
    assert seen > 50, seen
    assert bad == []


def test_a_fifo_in_a_tree_repository_never_blocks_the_orchestrator(
        leerie, tmp_path):
    """A committing worker writes its tree repository whole; a FIFO at a
    worktree record is refused at once, never waited on for a writer."""
    common = tmp_path / "tr"
    (common / "worktrees" / "w").mkdir(parents=True)
    os.mkfifo(common / "worktrees" / "w" / "gitdir")
    assert leerie._admin_dir_of(common, tmp_path / "tree") is None


# --- the rule over the module -----------------------------------------------

# Every function of leerie.py that still reads with a raw primitive, and why
# what it reads is not a repository's content reached through a path a
# repository shapes. A new function that reads raw fails here until it is
# classified, or moved onto the `_beneath` primitives or onto git blobs
# (DESIGN §6 *Leerie never reads through a link a repository holds*).
_PRIMITIVES = {
    "_glob_beneath", "_listdir_beneath",
    # Raw only for a source of leerie's own (`src_anchor` None).
    "_copy_beneath", "_copytree_beneath",
}
_LEERIE_OWN = {
    # Leerie's install: its prompts, version, hook and self-check config.
    "_load_prompt", "resolve_prompt", "_read_version",
    "_forbidden_command_hook_main", "selfcheck_main", "_selfcheck_calls",
    # The run directory and the state root, which no grant names for
    # writing: state, records, plans, logs, transcripts, sidecars.
    "load", "main", "_write_run_json", "_read_run_json_sidecar",
    "_record_run_health", "_compose_pr_via_llm", "_discover_runs",
    "_live_duplicate_runs", "_report_run", "_aggregate_calls",
    "_iter_ndjson_records", "_memory_peak", "_iter_transcript_events",
    "_registered_transcripts", "_ci_log_text", "_tsc_lists_paths",
    "_heal_replay_patched", "_extract_contract_literals",
    "_format_owned_region_section", "_format_upstream_artifacts_for_sid",
    "_read_upstream_artifacts", "_incoming_subtask_view", "_settle_subtask",
    "_settled_predecessors", "_run_delivery_redrive",
    "_prior_delivery_residual", "_prior_acceptance_dispute_record",
    "_acceptance_report_base", "_record_capture", "apply_captured_deps",
    "run_recapture_deps", "_backstop_capture_prior_runs",
    "_remove_trees_private_tmps", "_sweep_measure_dirs", "_reap_idle_worktrees",
    "_cleanup_on_abnormal_exit", "_ensure_run_repo", "_resumed_wid",
    "_worker_dirs", "_claude_config_template", "_run_last_activity",
    "_invoke", "_restore_settings",
    # The held-out sets and probes: leerie's copies, made through
    # `_read_beneath`, so regular files only.
    "_set_production_imports", "_acceptance_mechanical_findings",
    "_acceptance_scope_judge_sets", "_audit_case_key", "_acceptance_audit_in",
    "_redrive_exercised", "_heldout_fingerprints", "_falsifier_place_leads",
    # A repository's worktree registrations, read by git's own listing of
    # what it is about to prune (the user's repository, or the run's).
    "_prune_leerie_worktrees_locked",
}
_SYSTEM = {
    # The kernel's and the container's own files.
    "_auto_worker_memory_max_legacy", "_host_cpu_psi", "_mem_available_bytes",
    "_own_addresses", "_sigchld_is_ignored", "_collect_memory_sample",
    "_network_local_host", "_beside", "_service_credential_source",
    "_disk_place",
}
_OPERATOR = {
    # A file the operator named: `--answers`, host settings, a task file
    # outside the checkout, their own CLI settings.
    "_absorb_supplied_answers", "_run_phases", "_take_host_settings",
    "_task_file_text", "_settings_file",
}
_NAMES_ONLY = {
    # Directory listings that follow no link (`os.walk` without
    # `followlinks`, `follow_symlinks=False`, `lstat`) or keep only what
    # `_no_link_beneath` passes; any content is read through `_beneath`.
    "_build_repo_map", "_category_evidence_found", "_glob_task_references",
    "_node_lockfile_paths", "_output_entries", "_same_settings_path",
    "_tree_bytes", "_vitest_filter_colliders", "_restore_write_bits",
    "_rmtree_owned",
}


def _raw_readers() -> dict[str, set[str]]:
    attr = {"read_text", "read_bytes", "iterdir", "glob", "rglob"}

    def writes(mode: ast.AST | None) -> bool:
        return (isinstance(mode, ast.Constant) and isinstance(mode.value, str)
                and bool(set(mode.value) & set("wax+")))

    def raw(call: ast.Call) -> str | None:
        f = call.func
        if isinstance(f, ast.Attribute):
            base = f.value.id if isinstance(f.value, ast.Name) else None
            if base == "os":
                return (f"os.{f.attr}" if f.attr in
                        {"walk", "listdir", "scandir"} else None)
            if base in ("json", "tomllib") and f.attr == "load":
                return f"{base}.load"
            if base == "ast":
                return None
            if f.attr in attr:
                return f".{f.attr}"
            if f.attr == "open" and not writes(
                    call.args[0] if call.args else None):
                return ".open"
        if isinstance(f, ast.Name) and f.id == "open" and not writes(
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


def test_every_raw_read_is_classified():
    found = _raw_readers()
    known = _PRIMITIVES | _LEERIE_OWN | _SYSTEM | _OPERATOR | _NAMES_ONLY
    unclassified = sorted(set(found) - known)
    assert not unclassified, (
        "these functions read with a raw primitive and are not classified: "
        f"{unclassified}. Read a repository's file beneath its root "
        "(`_read_text_beneath`, `_regular_file_beneath`, `_glob_beneath`, "
        "`_listdir_beneath`) or as a git blob; else add it here, saying why.")
    # The converse: an entry whose function no longer reads raw is stale.
    assert sorted(known - set(found)) == []


# Path probes that follow a link: in a converted function each one decided
# what was read (`_existing_mise_toml_path` returned a path `.is_file()`
# passed; `_stage_worktree_extras` walked what `.is_dir()` passed), so its
# conversion is to `_regular_file_beneath` / `_dir_beneath_exists`, and a
# copy's to `src_anchor=`. Without these, three of the converted functions
# could revert to following a link and the rule below stayed green.
_FOLLOWING_PROBES = {"is_file", "is_dir", "exists"}
# The converted functions whose remaining probes only test whether a file
# exists, to choose between leerie's own commands or report a missing file
# (DESIGN §6 part 2): what they read, they read beneath.
_EXISTENCE_ONLY = {"_infer_build_lint_test", "_binary_install_source",
                   "_validate_checkpoint"}


def _following_probes() -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    tree = ast.parse(LEERIE_PY.read_text())
    for fn in tree.body:
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for c in ast.walk(fn):
            if not isinstance(c, ast.Call):
                continue
            f = c.func
            if isinstance(f, ast.Attribute) and f.attr in _FOLLOWING_PROBES:
                out.setdefault(fn.name, set()).add(f".{f.attr}")
            if (isinstance(f, ast.Attribute) and f.attr in
                    {"isfile", "isdir", "exists"}
                    and isinstance(f.value, ast.Attribute)
                    and f.value.attr == "path"):
                out.setdefault(fn.name, set()).add(f"os.path.{f.attr}")
            if (getattr(f, "id", None) in ("_copy_beneath",
                                           "_copytree_beneath")
                    and "src_anchor" not in {k.arg for k in c.keywords}):
                out.setdefault(fn.name, set()).add(f"{f.id} without "
                                                   "src_anchor")
    return out


def test_the_converted_sites_read_only_beneath():
    """The functions that read a repository's content — the checkout, a
    worker's tree, a tree repository, a measurement's output — use no raw
    read primitive, and decide what to read with no probe that follows a
    link."""
    found = _raw_readers()
    probes = _following_probes()
    for fn in ("_find_pr_template", "_read_toml_key",
               "_write_config_toml_keys", "_gather_provision_fixtures",
               "_read_file_safely", "_sample_workspace_manifests",
               "_gather_dep_manifests", "_workspace_globs",
               "_parse_repo_file", "_extract_symbol_ranges",
               "_subfile_split", "_peel_oversized_file", "_is_same_document",
               "_d1_report_sources", "_go_already_pinned",
               "_read_idiomatic_pins", "_synth_mise_go_override",
               "_existing_mise_toml_path", "_infer_build_lint_test",
               "_js_runner_scoped_templates", "_package_json_name",
               "_package_json_scripts", "_reads_package_manager",
               "_ci_config_files", "_ci_check_problems", "_ci_coverage_pcts",
               "_prisma_project", "_prisma_reset_scripts",
               "_prisma_mock_hits", "_go_test_func_names",
               "_binary_install_source", "_validate_checkpoint",
               "_introduced_conflict_markers", "_restore_git_pointer",
               "_admin_dir_of", "_run_tree_location",
               "_stage_worktree_extras", "_copy_task_references",
               "_verify_set_audit_case"):
        assert fn not in found, (fn, found.get(fn))
        if fn not in _EXISTENCE_ONLY:
            assert fn not in probes, (fn, probes.get(fn))
