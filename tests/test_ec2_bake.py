"""ec2-bake.sh must reproduce the Dockerfile with pins read from it, and leave
an image whose transports and orchestrator work without container-entry.sh."""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "remote" / "ec2-bake.sh"
DOCKERFILE = (ROOT / "Dockerfile").read_text()
BODY = SCRIPT.read_text()


def test_bash_syntax():
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


@pytest.mark.skipif(not shutil.which("shellcheck"), reason="shellcheck absent")
def test_shellcheck():
    subprocess.run(["shellcheck", str(SCRIPT)], check=True)


def _fn(name: str) -> str:
    return re.search(rf"{name}\(\) \{{.*?\n\}}\n", BODY, re.S).group(0)


def _extract(snippet: str, dockerfile: Path = ROOT / "Dockerfile") -> str:
    """Run one of the script's own extraction snippets against a Dockerfile."""
    return subprocess.run(
        ["bash", "-c", f'DOCKERFILE={dockerfile}; {snippet}'],
        capture_output=True, text=True, check=True,
    ).stdout


def test_apt_packages_derived_equal_dockerfile():
    got = set(_extract(_fn("dockerfile_apt_packages") + "dockerfile_apt_packages").split())
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


def _env_snippet() -> str:
    base = re.search(r"^BASE_PATH=(\S+)$", BODY, re.M).group(0)
    return base + "\n" + _fn("dockerfile_env") + "dockerfile_env"


def test_env_file_is_derived_from_the_dockerfile_env_lines(tmp_path):
    # Values that exist in no real Dockerfile, so only a derivation (not a
    # hand-copied list) can produce them, and $PATH must be expanded.
    df = tmp_path / "Dockerfile"
    df.write_text(
        "FROM debian:13\n"
        "ENV ALPHA_ONE=first-value\n"
        "RUN echo ENV NOT_THIS=ignored\n"
        "ENV PATH=/opt/zz/bin:$PATH:/opt/yy/bin\n"
    )
    out = _extract(_env_snippet(), df).splitlines()
    assert out == [
        "ALPHA_ONE=first-value",
        "PATH=/opt/zz/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/opt/yy/bin",
    ]


def test_env_file_from_real_dockerfile_carries_every_env_line():
    want = re.findall(r"^ENV ([A-Za-z_][A-Za-z0-9_]*)=", DOCKERFILE, re.M)
    got = [l.split("=", 1)[0] for l in _extract(_env_snippet()).splitlines()]
    assert got == want and "MISE_RUBY_COMPILE" in got and "PATH" in got
    path = dict(l.split("=", 1) for l in _extract(_env_snippet()).splitlines())["PATH"]
    assert "$PATH" not in path and path.startswith("/usr/local/share/mise/shims:")


def test_env_file_written_before_mise_install_and_sourced_for_it():
    write = BODY.index('dockerfile_env > "$LEERIE_ENV_FILE"')
    source = BODY.index('. "$LEERIE_ENV_FILE"')
    install = BODY.index("mise install --system node@lts python@3.12")
    assert write < source < install


def test_ec2_user_is_cloud_inits_default_user_with_sudo_and_no_baked_key():
    cfg = re.search(r"cat > /etc/cloud/cloud.cfg.d/99-leerie.cfg <<CFG\n(.*?)\nCFG\n", BODY, re.S).group(1)
    assert "default_user:" in cfg and "name: ec2-user" in cfg
    assert 'sudo: ["ALL=(ALL) NOPASSWD:ALL"]' in cfg
    assert "groups: [$leerie_group]" in cfg
    # No key copied into ec2-user; the bootstrap key is removed from the image.
    assert "/home/ec2-user/.ssh/authorized_keys" not in BODY
    assert "rm -f /home/admin/.ssh/authorized_keys" in BODY


def test_cloud_init_is_reset_last():
    lines = [l for l in BODY.splitlines() if l.strip() and not l.lstrip().startswith("#")]
    assert lines[-2] == "cloud-init clean --logs --machine-id"
    assert lines[-1].startswith('log "bake provisioning complete"')


def test_leerie_home_is_leerie_owned_after_its_dirs_are_made():
    mk = BODY.index("mkdir -p /home/leerie/.local/share/mise")
    chown = BODY.index("chown -R leerie: /home/leerie")
    assert mk < chown
    assert "container-entry.sh's rootful guard" not in BODY


def test_ssm_agent_prefers_regional_bucket_then_global_and_is_started():
    regional = BODY.index("https://s3.${LEERIE_BAKE_REGION}.amazonaws.com/amazon-ssm-${LEERIE_BAKE_REGION}/latest/debian_${arch}/amazon-ssm-agent.deb")
    glob = BODY.index("https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/debian_${arch}/amazon-ssm-agent.deb")
    assert regional < BODY.index('curl -fsSL -o "$ssm_deb" "$ssm_global"')
    assert glob < BODY.index("systemctl enable --now amazon-ssm-agent")


@pytest.mark.parametrize("needle", [
    "useradd -u \"$LEERIE_UID\"", "git config --system --add safe.directory '*'",
    "mkdir -p /work", "mkdir -p /inspect", "lts-current", "mise install --system node@lts python@3.12",
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
