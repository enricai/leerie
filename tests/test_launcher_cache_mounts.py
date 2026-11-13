"""The host caches the launcher mounts into the container (DESIGN §6½ *The
repository image holds no dependency*): the host's toolchains (mise-data),
and nothing else. Package stores are the run's own, in the state mount (§6
*The stores a view installs from are leerie's*), so no host Bundler cache is
mounted, and no `BUNDLE_*` variable is passed: until 0.65.0 the launcher
mounted `~/.cache/leerie/bundle`, shared by every run of every repository,
and set `BUNDLE_PATH`/`BUNDLE_CACHE_ALL` to it.

Runs the launcher's own cache-mount section in bash (HOME a temporary
directory) and reads the array it built.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_START = 'mkdir -p "$HOME/.cache/leerie/mise-data"'
_END = "# --- remote: resolve Fly.io image tag"


def _cache_section(launcher: Path) -> str:
    text = launcher.read_text()
    s = text.index(_START)
    return text[s:text.index(_END, s)]


def _run_section(tmp_path: Path, launcher: Path = REPO_ROOT / "leerie"
                 ) -> tuple[list[str], Path]:
    home = tmp_path / "home"
    home.mkdir()
    script = (
        "set -euo pipefail\n"
        f'LEERIE_STATE_HOST_DIR="{tmp_path}/state"\n'
        f'_leerie_host_dir="{tmp_path}/host"\n'
        + _cache_section(launcher)
        + '\nprintf "%s\\n" "${CACHE_MOUNTS[@]}"\n')
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, env={"PATH": "/usr/bin:/bin",
                                       "HOME": str(home)})
    assert r.returncode == 0, r.stderr
    return r.stdout.splitlines(), home


def test_only_the_toolchain_cache_is_mounted(tmp_path):
    """The array the container is run with mounts mise-data at the image's
    `MISE_DATA_DIR`, and holds no bundle mount and no `BUNDLE_*` `-e`; the
    host's bundle cache directory is not even made. Falsified against
    0.65.0's launcher: its array held `-v ~/.cache/leerie/bundle:…`,
    `-e BUNDLE_CACHE_ALL=1` and `-e BUNDLE_PATH=…`, and it made the
    directory."""
    args, home = _run_section(tmp_path)
    mise = f"{home}/.cache/leerie/mise-data:/home/leerie/.local/share/mise"
    assert args == ["-v", mise], args
    assert not any("bundle" in a.lower() for a in args), args
    assert (home / ".cache/leerie/mise-data").is_dir()
    assert not (home / ".cache/leerie/bundle").exists()
