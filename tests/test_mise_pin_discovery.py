"""Unit tests for the mise/idiomatic-version-pin discovery helpers that
feed `_synth_mise_go_override` (DESIGN §6½ "Per-repo dependency
provisioning"): `_existing_mise_toml_path`, `_go_already_pinned`,
`_existing_mise_toml_tool_keys`, `_read_idiomatic_pins`.
"""


class TestExistingMiseTomlPath:
    def test_prefers_mise_toml_over_dotted_when_both_exist(self, leerie, tmp_path):
        (tmp_path / "mise.toml").write_text("[tools]\n")
        (tmp_path / ".mise.toml").write_text("[tools]\n")
        assert leerie._existing_mise_toml_path(tmp_path) == tmp_path / "mise.toml"

    def test_returns_dotted_when_only_dotted_exists(self, leerie, tmp_path):
        (tmp_path / ".mise.toml").write_text("[tools]\n")
        assert leerie._existing_mise_toml_path(tmp_path) == tmp_path / ".mise.toml"

    def test_returns_none_when_neither_exists(self, leerie, tmp_path):
        assert leerie._existing_mise_toml_path(tmp_path) is None

    def test_ignores_a_directory_named_mise_toml(self, leerie, tmp_path):
        (tmp_path / "mise.toml").mkdir()
        assert leerie._existing_mise_toml_path(tmp_path) is None


class TestGoAlreadyPinned:
    def test_false_when_nothing_present(self, leerie, tmp_path):
        assert leerie._go_already_pinned(tmp_path) is False

    def test_true_for_go_version_file(self, leerie, tmp_path):
        (tmp_path / ".go-version").write_text("1.22.0\n")
        assert leerie._go_already_pinned(tmp_path) is True

    def test_true_for_go_entry_in_tool_versions(self, leerie, tmp_path):
        (tmp_path / ".tool-versions").write_text("go 1.22.0\nnodejs 20.11.0\n")
        assert leerie._go_already_pinned(tmp_path) is True

    def test_false_when_tool_versions_lacks_go(self, leerie, tmp_path):
        (tmp_path / ".tool-versions").write_text("nodejs 20.11.0\n")
        assert leerie._go_already_pinned(tmp_path) is False

    def test_tool_versions_ignores_comments_and_blank_lines(self, leerie, tmp_path):
        (tmp_path / ".tool-versions").write_text("# go 1.22.0\n\nnodejs 20.11.0\n")
        assert leerie._go_already_pinned(tmp_path) is False

    def test_tool_versions_matches_case_insensitively(self, leerie, tmp_path):
        (tmp_path / ".tool-versions").write_text("Go 1.22.0\n")
        assert leerie._go_already_pinned(tmp_path) is True

    def test_true_for_go_pin_in_mise_toml(self, leerie, tmp_path):
        (tmp_path / "mise.toml").write_text('[tools]\ngo = "1.22.0"\n')
        assert leerie._go_already_pinned(tmp_path) is True

    def test_true_for_go_pin_in_dotted_mise_toml(self, leerie, tmp_path):
        (tmp_path / ".mise.toml").write_text('[tools]\ngo = "1.22.0"\n')
        assert leerie._go_already_pinned(tmp_path) is True

    def test_false_when_mise_toml_has_no_go_pin(self, leerie, tmp_path):
        (tmp_path / "mise.toml").write_text('[tools]\nnode = "20.11.0"\n')
        assert leerie._go_already_pinned(tmp_path) is False

    def test_unreadable_tool_versions_is_tolerated(self, leerie, tmp_path):
        d = tmp_path / ".tool-versions"
        d.mkdir()
        assert leerie._go_already_pinned(tmp_path) is False

    def test_unreadable_mise_toml_is_tolerated(self, leerie, tmp_path):
        d = tmp_path / "mise.toml"
        d.mkdir()
        assert leerie._go_already_pinned(tmp_path) is False


class TestExistingMiseTomlToolKeys:
    def test_none_returns_empty_set(self, leerie):
        assert leerie._existing_mise_toml_tool_keys(None) == set()

    def test_empty_string_returns_empty_set(self, leerie):
        assert leerie._existing_mise_toml_tool_keys("") == set()

    def test_extracts_keys_from_tools_section(self, leerie):
        text = '[tools]\ngo = "1.22.0"\nnode = "20.11.0"\n'
        assert leerie._existing_mise_toml_tool_keys(text) == {"go", "node"}

    def test_ignores_keys_outside_tools_section(self, leerie):
        text = '[env]\nFOO = "bar"\n\n[tools]\ngo = "1.22.0"\n'
        assert leerie._existing_mise_toml_tool_keys(text) == {"go"}

    def test_stops_collecting_at_next_section(self, leerie):
        text = '[tools]\ngo = "1.22.0"\n\n[settings]\nnode = "20.11.0"\n'
        assert leerie._existing_mise_toml_tool_keys(text) == {"go"}

    def test_no_tools_section_returns_empty_set(self, leerie):
        text = '[settings]\nfoo = "bar"\n'
        assert leerie._existing_mise_toml_tool_keys(text) == set()

    def test_malformed_lines_are_skipped(self, leerie):
        text = "[tools]\nnot a valid line\ngo = \"1.22.0\"\n"
        assert leerie._existing_mise_toml_tool_keys(text) == {"go"}

    def test_handles_hyphen_and_underscore_in_keys(self, leerie):
        text = '[tools]\nnode-gyp = "1.0"\nfoo_bar = "2.0"\n'
        assert leerie._existing_mise_toml_tool_keys(text) == {"node-gyp", "foo_bar"}


class TestReadIdiomaticPins:
    def test_empty_repo_returns_empty_list(self, leerie, tmp_path):
        assert leerie._read_idiomatic_pins(tmp_path, set()) == []

    def test_reads_nvmrc(self, leerie, tmp_path):
        (tmp_path / ".nvmrc").write_text("v20.11.0\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("node", "20.11.0")]

    def test_strips_leading_v_from_node_version(self, leerie, tmp_path):
        (tmp_path / ".node-version").write_text("V18.0.0\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("node", "18.0.0")]

    def test_python_version_not_transformed(self, leerie, tmp_path):
        (tmp_path / ".python-version").write_text("3.12.1\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("python", "3.12.1")]

    def test_ruby_version(self, leerie, tmp_path):
        (tmp_path / ".ruby-version").write_text("3.3.0\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("ruby", "3.3.0")]

    def test_skips_tool_already_in_already_pinned(self, leerie, tmp_path):
        (tmp_path / ".nvmrc").write_text("v20.11.0\n")
        pins = leerie._read_idiomatic_pins(tmp_path, {"node"})
        assert pins == []

    def test_already_pinned_set_is_mutated_with_new_pins(self, leerie, tmp_path):
        (tmp_path / ".python-version").write_text("3.12.1\n")
        already_pinned = set()
        leerie._read_idiomatic_pins(tmp_path, already_pinned)
        assert "python" in already_pinned

    def test_nvmrc_and_node_version_both_present_prefers_nvmrc(self, leerie, tmp_path):
        # .nvmrc is listed first in _IDIOMATIC_VERSION_FILES, so it wins
        # and .node-version is skipped via the already_pinned mutation.
        (tmp_path / ".nvmrc").write_text("v20.11.0\n")
        (tmp_path / ".node-version").write_text("18.0.0\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("node", "20.11.0")]

    def test_empty_file_yields_no_pin(self, leerie, tmp_path):
        (tmp_path / ".python-version").write_text("\n")
        assert leerie._read_idiomatic_pins(tmp_path, set()) == []

    def test_uses_first_nonblank_line_only(self, leerie, tmp_path):
        (tmp_path / ".python-version").write_text("3.12.1\nsome-other-line\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("python", "3.12.1")]

    def test_tool_versions_parsed_line_by_line(self, leerie, tmp_path):
        (tmp_path / ".tool-versions").write_text(
            "nodejs 20.11.0\npython 3.12.1\n# comment line\n\n"
        )
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert ("node", "20.11.0") in pins
        assert ("python", "3.12.1") in pins

    def test_tool_versions_asdf_alias_normalized_to_mise_name(self, leerie, tmp_path):
        (tmp_path / ".tool-versions").write_text("nodejs 20.11.0\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("node", "20.11.0")]

    def test_tool_versions_skips_tool_already_pinned(self, leerie, tmp_path):
        (tmp_path / ".tool-versions").write_text("go 1.22.0\npython 3.12.1\n")
        pins = leerie._read_idiomatic_pins(tmp_path, {"go"})
        assert pins == [("python", "3.12.1")]

    def test_tool_versions_does_not_double_pin_nvmrc_and_nodejs_alias(self, leerie, tmp_path):
        # A repo with both .nvmrc (injects "node") and .tool-versions
        # carrying "nodejs 20.11.0" must not end up with both "node" and
        # "nodejs" pinned — mise treats them as the same tool.
        (tmp_path / ".nvmrc").write_text("v20.11.0\n")
        (tmp_path / ".tool-versions").write_text("nodejs 18.0.0\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("node", "20.11.0")]

    def test_tool_versions_malformed_line_with_no_version_is_skipped(self, leerie, tmp_path):
        (tmp_path / ".tool-versions").write_text("go\npython 3.12.1\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("python", "3.12.1")]

    def test_missing_tool_versions_file_is_a_no_op(self, leerie, tmp_path):
        (tmp_path / ".python-version").write_text("3.12.1\n")
        pins = leerie._read_idiomatic_pins(tmp_path, set())
        assert pins == [("python", "3.12.1")]

    def test_unreadable_idiomatic_file_is_tolerated(self, leerie, tmp_path):
        d = tmp_path / ".python-version"
        d.mkdir()
        assert leerie._read_idiomatic_pins(tmp_path, set()) == []
