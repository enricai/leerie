"""ec2-bake-provision.sh must reproduce the Dockerfile with pins read from it."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "remote" / "ec2-bake-provision.sh"
DOCKERFILE = (ROOT / "Dockerfile").read_text()
BODY = SCRIPT.read_text()


def test_bash_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)
    subprocess.run(["bash", "-n", str(SCRIPT.with_name("ec2-bake.sh"))], check=True)


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck absent")
def test_shellcheck():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)


def _extract(fn: str) -> str:
    """Run one of the script's own extraction snippets against the real Dockerfile."""
    return subprocess.run(
        ["bash", "-c", f'DOCKERFILE={ROOT / "Dockerfile"}; {fn}'],
        capture_output=True, text=True, check=True,
    ).stdout


def test_apt_packages_derived_equal_dockerfile():
    fn = re.search(r"dockerfile_apt_packages\(\) \{.*?\n\}\n", BODY, re.S).group(0)
    got = set(_extract(fn + "dockerfile_apt_packages").split())
    assert {"git", "build-essential", "chromium", "gnupg", "libc6",
            "default-libmysqlclient-dev", "rsync"} <= got
    assert "gh" not in got  # installed explicitly from the GitHub repo


def test_pins_derived_equal_dockerfile():
    mise = re.search(r"^ARG MISE_VERSION=(\S+)", DOCKERFILE, re.M).group(1)
    claude = re.search(r"npm install -g '(@anthropic-ai/claude-code@[^']*)'", DOCKERFILE).group(1)
    snippet = "\n".join(
        l for l in BODY.splitlines() if l.startswith(("MISE_VERSION=", "CLAUDE_SPEC="))
    )
    out = _extract(snippet + '\nprintf "%s\\n%s" "$MISE_VERSION" "$CLAUDE_SPEC"')
    assert out.split("\n") == [mise, claude]
    assert "2026" not in BODY.replace("2026-", "")  # no hand-copied mise version


@pytest.mark.parametrize("needle", [
    "useradd -u \"$LEERIE_UID\"", "git config --system --add safe.directory '*'",
    "mkdir -p /work", "mkdir -p /inspect", "lts-current", "mise install --system node@lts python@3.12",
    "systemctl enable amazon-ssm-agent", "ec2-user", "authorized_keys",
    "pip3 install --break-system-packages", "apt-get install -y --no-install-recommends gh",
])
def test_component_step_present(needle):
    assert needle in BODY


def test_opt_leerie_image_matches_dockerfile_copy_list():
    dests = re.findall(r"^COPY \S+ (/opt/leerie-image/\S+)", DOCKERFILE, re.M)
    assert sorted(dests) == [
        "/opt/leerie-image/.claude-plugin/", "/opt/leerie-image/orchestrator/",
        "/opt/leerie-image/prompts/", "/opt/leerie-image/scripts/",
    ]
    # the script iterates the Dockerfile's COPY lines rather than a hand list
    assert "grep '^COPY ' \"$DOCKERFILE\"" in BODY
