"""`--model` carries `[1m]` behind the strict-output proxy (S1).

Owning `ANTHROPIC_BASE_URL` — which is how `--dangerously-force-strict-output`
reaches workers — makes the Claude Code CLI treat the session as gateway-routed
and apply a conservative client-side context ceiling instead of the model's
native window. It then refuses an oversized prompt *itself*, with no API call.

Measured across five arms on one 225 KB worker payload (same model, tools and
cwd, varying only `ANTHROPIC_BASE_URL`):

    direct            sonnet       20 reqs  235,805 tok  0 refusals
    passthrough proxy sonnet        2 reqs  224,127 tok  1 refusal
    strict proxy      sonnet       12 reqs  224,247 tok  1 refusal
    passthrough proxy sonnet[1m]   27 reqs  261,014 tok  0 refusals
    strict proxy      sonnet[1m]   21 reqs  276,579 tok  0 refusals  <- completed

The two refusals reproduce 120 tokens apart, so the cause is the base-URL
override alone and not the schema rewriting (the passthrough arm rewrites
nothing). `[1m]` cleared it on both proxy paths.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import leerie  # noqa: E402


class _FakeProxy:
    """Stand-in for a live `_StrictOutputProxy` — only its presence matters."""
    port = 51234


@pytest.fixture
def proxy_on(monkeypatch):
    monkeypatch.setattr(leerie, "_STRICT_PROXY", _FakeProxy())


@pytest.fixture
def proxy_off(monkeypatch):
    monkeypatch.setattr(leerie, "_STRICT_PROXY", None)


class TestSuffixAppliedOnlyBehindTheProxy:
    def test_sonnet_gets_the_suffix_when_proxy_active(self, proxy_on):
        assert leerie._model_arg("sonnet") == "sonnet[1m]"

    def test_opus_gets_the_suffix_when_proxy_active(self, proxy_on):
        assert leerie._model_arg("opus") == "opus[1m]"

    def test_no_suffix_when_proxy_inactive(self, proxy_off):
        # On the direct path `sonnet` already resolves to Sonnet 5's native
        # 1M window, so the suffix would be redundant. Keeping argv identical
        # to the pre-feature invocation is what makes this fix inert for the
        # overwhelmingly common case.
        assert leerie._model_arg("sonnet") == "sonnet"
        assert leerie._model_arg("opus") == "opus"

    def test_haiku_never_gets_the_suffix(self, proxy_on):
        # Haiku 4.5 has no 1M variant; the CLI rejects the suffix rather than
        # ignoring it, so appending it would break every haiku worker behind
        # the proxy.
        assert leerie._model_arg("haiku") == "haiku"

    def test_unknown_model_string_passes_through_untouched(self, proxy_on):
        # `resolve_models` is not restricted to MODEL_VALUES on the env/TOML
        # path, so a full model id can reach here. Only the two aliases known
        # to have a 1M variant are rewritten.
        assert leerie._model_arg("claude-sonnet-5") == "claude-sonnet-5"

    def test_suffix_is_not_doubled(self, proxy_on):
        # Guards a re-entrancy mistake: an already-suffixed value must not
        # become `sonnet[1m][1m]`.
        assert leerie._model_arg("sonnet[1m]") == "sonnet[1m]"


class TestOneMModelSet:
    def test_membership(self):
        assert "sonnet" in leerie._ONE_M_CONTEXT_MODELS
        assert "opus" in leerie._ONE_M_CONTEXT_MODELS
        assert "haiku" not in leerie._ONE_M_CONTEXT_MODELS

    def test_every_member_is_a_real_alias(self):
        # A typo here would silently disable the fix rather than fail loudly.
        assert leerie._ONE_M_CONTEXT_MODELS <= set(leerie.MODEL_VALUES)


class TestCallSiteIsWired:
    """The helper is inert unless `claude_p` actually calls it.

    A present-but-unwired fix is the failure mode this repo has already shipped
    once (the coverage gate that raised TypeError on every invocation while its
    own broad `except` logged a clean degrade), so the wiring gets its own pin.
    """

    def test_argv_builder_routes_model_through_the_helper(self):
        """Behavioural, not a source pin.

        This asserted the literal `'"--model", _model_arg(model)'` inside
        `claude_p`'s source until the argv construction moved into the shared
        `_contained_claude_argv` builder (so `preflight`'s smoke test could
        stop hand-rolling its own uncontained argv). A source pin on one
        function cannot follow that move, and — more to the point — could
        never have observed the OTHER call site, which is the one that had no
        `--model` at all. Executing the builder covers both.
        """
        argv = leerie._contained_claude_argv(
            schema="{}", allowed_tools="Read", max_turns=1, model="sonnet")
        assert argv[argv.index("--model") + 1] == "sonnet"

        prev = leerie._STRICT_PROXY
        leerie._STRICT_PROXY = object()
        try:
            argv = leerie._contained_claude_argv(
                schema="{}", allowed_tools="Read", max_turns=1,
                model="sonnet")
        finally:
            leerie._STRICT_PROXY = prev
        assert argv[argv.index("--model") + 1] == "sonnet[1m]", (
            "the builder must route --model through _model_arg; a bare "
            "`model` re-introduces the lowered context ceiling")

        import inspect
        assert '"--model", model,' not in inspect.getsource(
            leerie._contained_claude_argv)

    def test_telemetry_records_the_effective_model(self):
        """`calls.ndjson` must record what the CLI was actually handed.

        `_model_arg` rewrites the alias on the way to argv, so a capture record
        holding the bare alias would disagree with the invocation it claims to
        describe. Reconstructing a run's real invocation is the entire purpose
        of that file — and this investigation lost hours to exactly that class
        of gap, where `dangerously_force_strict_output` was not recorded at all
        and the flag's effects could not be attributed after the fact.
        """
        import inspect
        src = inspect.getsource(leerie.claude_p)
        assert '"model": _model_arg(model)' in src, (
            "telemetry must record the effective --model value, not the "
            "resolved alias")
        # Guard the guard: a bare `"model": model,` would silently reintroduce
        # the divergence, and the assertion above alone would not catch a
        # second capture site.
        assert '"model": model,' not in src
