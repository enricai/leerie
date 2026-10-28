"""`remote_host_no_push` (scripts/remote/lib.sh): the launch-time --no-push
intent both remote teardowns gate auto-finalize on (DESIGN §6 *Finalization
parity*). run.json cannot carry it — the fetch strips no_push=true whenever
a run branch exists — so the runtime sidecar is the intent of record."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

LIB_SH = Path(__file__).resolve().parent.parent / "scripts" / "remote" / "lib.sh"


def _check(sidecar: Path, no_push: str = "") -> str:
    r = subprocess.run(
        ["bash", "-c", f'source {LIB_SH}; NO_PUSH="{no_push}"; remote_host_no_push "{sidecar}"'],
        capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.mark.parametrize("name", ["fly-machine.json", "ec2-instance.json"])
@pytest.mark.parametrize("value,expected", [(True, "true"), (False, "false")])
def test_reads_host_no_push_from_either_runtime_sidecar(tmp_path, name, value, expected):
    sidecar = tmp_path / name
    sidecar.write_text(json.dumps({"host_no_push": value, "run_id": "r1"}))
    assert _check(sidecar) == expected


def test_missing_or_unreadable_sidecar_reads_false(tmp_path):
    assert _check(tmp_path / "absent.json") == "false"
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert _check(bad) == "false"


def test_only_a_real_true_counts(tmp_path):
    # A truthy non-boolean (e.g. a stray string) is not an opt-out.
    sidecar = tmp_path / "fly-machine.json"
    sidecar.write_text(json.dumps({"host_no_push": "false"}))
    assert _check(sidecar) == "false"


def test_launcher_no_push_overrides_a_push_sidecar(tmp_path):
    sidecar = tmp_path / "fly-machine.json"
    sidecar.write_text(json.dumps({"host_no_push": False}))
    assert _check(sidecar, no_push="true") == "true"
