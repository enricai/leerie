"""The launcher owns `--aws-region` / `--aws-profile` resolution.

These are leerie's own knobs for which AWS region/profile it uses when
provisioning `--runtime ec2` machines — distinct from the AWS SDK's
`AWS_REGION` / `AWS_PROFILE` credential-chain vars, which
`scripts/remote/aws-credentials.sh` resolves independently.

Until 2026-08-10 the ORCHESTRATOR resolved them, into `args.aws_region` /
`args.aws_profile`, and nothing read the result — it runs inside the
container, where a host-side provisioning region is meaningless. The
launcher, the only real consumer, honoured `LEERIE_AWS_*` alone. So the
documented CLI flag and the `leerie.toml` keys were **both silently inert**
from the day they were documented, while the env var worked. Resolution now
lives beside the consumers.

The block is EXTRACTED from the real launcher rather than reproduced here
(the `_extract_config_arm` / `_extract_forwarding_loop` idiom). A
reproduction is a second copy of a rule, and two copies drift exactly the
way two copies of a list do — see `tests/test_no_duplicate_state_walks.py`
and `tests/launcher_blocks.py` for the same lesson learned twice.

The load-bearing test here is `TestReachesAConsumer`. Pinning resolution
alone is precisely what the deleted `tests/test_resolve_aws_prefs.py` did:
it proved the value was computed and never that anything used it.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
EC2_PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "ec2-provision.sh"

_START = "# --- _resolve_ec2_knob ---"
_END = "export LEERIE_AWS_REGION LEERIE_AWS_PROFILE"


def _extract_aws_block() -> str:
    """The real launcher's `_resolve_ec2_knob` + AWS resolution block."""
    src = LAUNCHER.read_text()
    i = src.index(_START)
    j = src.index(_END, i) + len(_END)
    return src[i:j]


def _run(argv: list[str], env: dict[str, str], repo: Path) -> dict[str, str]:
    """Execute the extracted block with `argv` as `$@`, print the results."""
    script = (
        f'USER_REPO={repo}\n'
        + _extract_aws_block()
        + '\nprintf "REGION=%s\\nPROFILE=%s\\n" '
          '"${LEERIE_AWS_REGION:-}" "${LEERIE_AWS_PROFILE:-}"\n'
    )
    full_env = {"PATH": "/usr/bin:/bin", "HOME": str(repo)}
    full_env.update(env)
    out = subprocess.run(
        ["bash", "-c", script, "bash", *argv],
        capture_output=True, text=True, env=full_env, timeout=60,
    )
    assert out.returncode == 0, out.stderr
    return dict(
        line.split("=", 1) for line in out.stdout.strip().splitlines()
        if "=" in line
    )


@pytest.fixture
def repo(tmp_path):
    return tmp_path


class TestPrecedenceLadder:
    """CLI > env > leerie.toml > empty, per knob."""

    def test_all_unset_is_empty(self, repo):
        got = _run([], {}, repo)
        assert got["REGION"] == "" and got["PROFILE"] == ""

    def test_toml_only(self, repo):
        (repo / "leerie.toml").write_text(
            'aws_region = us-east-1\naws_profile = tomlprof\n')
        got = _run([], {}, repo)
        assert got["REGION"] == "us-east-1"
        assert got["PROFILE"] == "tomlprof"

    def test_env_beats_toml(self, repo):
        (repo / "leerie.toml").write_text('aws_region = us-east-1\n')
        got = _run([], {"LEERIE_AWS_REGION": "eu-west-1"}, repo)
        assert got["REGION"] == "eu-west-1"

    def test_cli_beats_env_and_toml(self, repo):
        (repo / "leerie.toml").write_text('aws_region = us-east-1\n')
        got = _run(["--aws-region", "ap-south-1"],
                   {"LEERIE_AWS_REGION": "eu-west-1"}, repo)
        assert got["REGION"] == "ap-south-1"

    def test_equals_form(self, repo):
        got = _run(["--aws-region=sa-east-1", "--aws-profile=eqprof"], {}, repo)
        assert got["REGION"] == "sa-east-1"
        assert got["PROFILE"] == "eqprof"

    def test_quoted_toml_value(self, repo):
        (repo / "leerie.toml").write_text('aws_region = "us-west-2"\n')
        assert _run([], {}, repo)["REGION"] == "us-west-2"

    def test_knobs_are_independent(self, repo):
        """Setting one must not populate or clobber the other."""
        got = _run(["--aws-region", "us-east-2"], {}, repo)
        assert got["REGION"] == "us-east-2"
        assert got["PROFILE"] == ""

    def test_profile_value_with_spaces_survives(self, repo):
        """A named AWS profile may legitimately contain spaces — the reason
        `_aws_region_profile_args` emits one token per line downstream."""
        got = _run(["--aws-profile", "my dev profile"], {}, repo)
        assert got["PROFILE"] == "my dev profile"

    def test_flag_among_other_args(self, repo):
        """The scan walks all of `$@`, so position must not matter."""
        got = _run(["some task text", "--runtime", "ec2",
                    "--aws-profile", "midprof", "--effort", "high"], {}, repo)
        assert got["PROFILE"] == "midprof"


class TestReachesAConsumer:
    """THE test this file exists for.

    The deleted `test_resolve_aws_prefs.py` pinned the argparse flag and the
    resolver and passed for months while the value reached no consumer. So
    assert the whole circuit: resolve from a tier that was previously inert,
    then feed the result to the REAL consumer — `ec2-provision.sh`'s
    `_aws_region_profile_args`, which every `aws ec2` / `aws sts` call in
    that file appends — and require the flags to come out.
    """

    @staticmethod
    def _consumer_args(env: dict[str, str]) -> list[str]:
        script = (
            f'. "{EC2_PROVISION_SH}" 2>/dev/null || true\n'
            'args=()\n'
            'while IFS= read -r _a; do args+=("$_a"); done '
            '< <(_aws_region_profile_args)\n'
            'printf "%s\\n" ${args[@]+"${args[@]}"}\n'
        )
        full_env = {"PATH": "/usr/bin:/bin"}
        full_env.update(env)
        out = subprocess.run(["bash", "-c", script], capture_output=True,
                             text=True, env=full_env, timeout=60)
        assert out.returncode == 0, out.stderr
        return out.stdout.split()

    def test_cli_tier_reaches_the_aws_argv(self, repo):
        """`--aws-profile` alone (no env, no toml) must produce `--profile`
        in the argv handed to `aws`. This is the tier that did nothing."""
        got = _run(["--aws-profile", "cliprof", "--aws-region", "us-east-1"],
                   {}, repo)
        args = self._consumer_args({
            "LEERIE_AWS_PROFILE": got["PROFILE"],
            "LEERIE_AWS_REGION": got["REGION"],
        })
        assert "--profile" in args and "cliprof" in args
        assert "--region" in args and "us-east-1" in args

    def test_toml_tier_reaches_the_aws_argv(self, repo):
        """The other previously-inert tier."""
        (repo / "leerie.toml").write_text(
            'aws_region = eu-central-1\naws_profile = tomlprof\n')
        got = _run([], {}, repo)
        args = self._consumer_args({
            "LEERIE_AWS_PROFILE": got["PROFILE"],
            "LEERIE_AWS_REGION": got["REGION"],
        })
        assert "--profile" in args and "tomlprof" in args
        assert "--region" in args and "eu-central-1" in args

    def test_nothing_set_emits_no_flags(self, repo):
        """ANTI-VACUITY: if the consumer emitted flags unconditionally the
        two tests above would pass without resolution doing anything."""
        got = _run([], {}, repo)
        assert got["REGION"] == "" and got["PROFILE"] == ""
        assert self._consumer_args(
            {"LEERIE_AWS_PROFILE": "", "LEERIE_AWS_REGION": ""}) == []


class TestLauncherWiring:
    """Source-coupling pins the extraction and sub-shell cannot cover."""

    def test_block_is_extractable(self):
        block = _extract_aws_block()
        assert "_resolve_ec2_knob()" in block
        assert "--aws-region" in block and "--aws-profile" in block

    def test_resolution_precedes_the_verb_dispatch(self):
        """The placement that makes this a real fix rather than a partial
        one. `accept-blocked`, `stop`, `kill` and `finalize` all read
        LEERIE_AWS_* inside their arms of the top-level dispatch, so
        resolving after it would leave those four on the env tier alone."""
        src = LAUNCHER.read_text()
        resolved_at = src.index(_END)
        dispatch_at = src.index('case "${1:-}" in', src.index("USER_REPO="))
        assert resolved_at < dispatch_at, (
            "AWS resolution must run before the top-level verb dispatch")

    def test_only_one_definition_of_the_helper(self):
        """Moving the helper up must not have left a second copy behind."""
        assert LAUNCHER.read_text().count("_resolve_ec2_knob() {") == 1

    def test_ec2_knobs_still_resolve_after_the_moved_helper(self):
        """The helper's original call site is far below its new definition;
        a move that broke that ordering would be a runtime `command not
        found` under `set -e`, invisible to `bash -n`."""
        src = LAUNCHER.read_text()
        assert (src.index("_resolve_ec2_knob() {")
                < src.index('LEERIE_EC2_AMI="$(_resolve_ec2_knob'))

    def test_flags_are_stripped_from_rewritten_args(self):
        """The orchestrator declares no such flags and `parse_args()` is
        strict, so a forwarded `--aws-region` would abort the run."""
        src = LAUNCHER.read_text()
        assert "--aws-region|--aws-profile)" in src
        assert "--aws-region=*|--aws-profile=*)" in src

    def test_both_vars_are_on_the_env_denylist(self):
        """Host-only knobs must not leak into the container."""
        src = LAUNCHER.read_text()
        denylist = re.search(r"_leerie_env_denylist=\"(.*?)\"", src, re.S)
        assert denylist, "could not locate _leerie_env_denylist"
        assert "LEERIE_AWS_REGION" in denylist.group(1)
        assert "LEERIE_AWS_PROFILE" in denylist.group(1)

    def test_orchestrator_no_longer_resolves_them(self):
        """The whole point: one owner. A reintroduced Python resolver would
        silently shadow nothing and rot again."""
        py = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
        assert "resolve_aws_region" not in py
        assert "resolve_aws_profile" not in py
        assert 'add_argument("--aws-region"' not in py
        assert 'add_argument("--aws-profile"' not in py
