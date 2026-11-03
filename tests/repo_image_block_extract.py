"""Shared _extract_block helper for the per-repo image test harnesses.

Used by test_launcher_per_repo_image.py, test_fly_per_repo_image.py, and
test_build_repo_image.py, whose own copies were verified byte-identical
(AST-diff, body minus docstring). test_no_verify_push_env_seed.py and
test_resolve_log_file.py define their own distinct _extract_block and are
deliberately not consolidated here.
"""
from __future__ import annotations


def extract_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between start_marker and end_marker (exclusive)."""
    s = text.index(start_marker)
    e = text.index(end_marker, s)
    return text[s:e]


# `nerdctl image inspect -f <format> <tag>` in a harness's bash `nerdctl`
# stub: the base image's layers and configuration, and the repository
# image's as leerie builds it (DESIGN §6 *The orchestrator's root is
# leerie's; a repository's root is only a confined process's*) — the base's
# layers plus one and the base's configuration, which `_check_repo_image`
# requires. Builtins only. Returns 0 when it answered a `-f` query, 1 for a
# bare presence probe, which the stub's own exit code answers: call it as
# `_stub_image_inspect "$@" && return 0` from the stub's `image)` branch,
# after the subcommand word is shifted off. The repository image's digest
# (`{{index .RepoDigests 0}}`) is `$STUB_REPO_DIGEST` (default
# `REPO_DIGEST`), present when `$NERDCTL_INSPECT_RC` is 0 or once the
# harness's `build` branch has called `_stub_build_done`.
REPO_DIGEST = "sha256:" + "a" * 64
NERDCTL_IMAGE_INSPECT_STUB = r'''
_STUB_BUILT=""
_stub_build_done() { _STUB_BUILT=1; }
_stub_image_inspect() {
  local _fmt="" _tag=""
  while [ $# -gt 0 ]; do
    case "$1" in
      -f) _fmt="${2:-}"; shift 2 || shift ;;
      inspect) shift ;;
      *) _tag="$1"; shift ;;
    esac
  done
  case "$_fmt" in
    *RepoDigests*)
      if [ "${NERDCTL_INSPECT_RC:-0}" = 0 ] || [ -n "$_STUB_BUILT" ]; then
        echo "${_tag%:*}@${STUB_REPO_DIGEST:-sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa}"
      else
        return 1
      fi ;;
    *RootFS.Layers*)
      if [ "$_tag" = "${IMAGE_TAG:-}" ]; then echo '["sha256:b"]'
      else echo '["sha256:b","sha256:w"]'; fi ;;
    *Config*) echo '{"Entrypoint":["/e"],"Image":"'"$_tag"'"}' ;;
    *.Id*) echo 'sha256:base-id' ;;
    *) return 1 ;;
  esac
}
'''


def repo_build_functions(launcher: str) -> str:
    """The launcher's repository-image build helpers, verbatim:
    `REPO_IMAGE_LAYOUT`, `_repo_build_dir`/`_prepare`/`_done`/`_fail`,
    `_repo_image_dockerfile`, `_repo_image_nonce`, `_check_repo_image` and
    `_repo_image_prepare` (everything from the block's first comment up to
    `build_repo_image`)."""
    return extract_block(launcher, "# The repository image (DESIGN §6",
                         "# Build the repository image from")
