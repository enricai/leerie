"""Tests for _infer_build_lint_test() — best-effort discovery of the
repo's build/lint/test commands. The conformer (DESIGN §9 *Post-work
conformance*) is told these inferred commands as a starting point;
inference doesn't have to be exhaustive, but it must cover the common
package-manager families.
"""
from __future__ import annotations


def _infer(leerie, tmp_path):
    return leerie._infer_build_lint_test(tmp_path)


def test_empty_repo_returns_all_empty(leerie, tmp_path):
    blt = _infer(leerie, tmp_path)
    assert blt == {"build": "", "lint": "", "test": ""}


def test_pyproject_only_infers_pytest(leerie, tmp_path):
    (tmp_path / "pyproject.toml").write_text("[tool]\nname='x'\n")
    blt = _infer(leerie, tmp_path)
    assert blt["test"] == "pytest"
    assert blt["build"] == ""
    assert blt["lint"] == ""


def test_package_json_infers_npm_build_and_test(leerie, tmp_path):
    (tmp_path / "package.json").write_text('{"name":"x"}')
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "npm run build"
    assert blt["test"] == "npm test"


def test_cargo_infers_cargo_commands(leerie, tmp_path):
    (tmp_path / "Cargo.toml").write_text("[package]\nname='x'\n")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "cargo build"
    assert blt["test"] == "cargo test"


def test_go_mod_infers_go_commands(leerie, tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "go build ./..."
    assert blt["test"] == "go test ./..."


def test_eslintrc_classic_infers_eslint(leerie, tmp_path):
    (tmp_path / ".eslintrc").write_text("{}")
    blt = _infer(leerie, tmp_path)
    assert blt["lint"] == "npx eslint ."


def test_eslintrc_json_infers_eslint(leerie, tmp_path):
    (tmp_path / ".eslintrc.json").write_text("{}")
    assert _infer(leerie, tmp_path)["lint"] == "npx eslint ."


def test_eslintrc_js_infers_eslint(leerie, tmp_path):
    (tmp_path / ".eslintrc.js").write_text("module.exports = {};")
    assert _infer(leerie, tmp_path)["lint"] == "npx eslint ."


def test_eslintrc_cjs_infers_eslint(leerie, tmp_path):
    """Third-pass audit follow-up — .eslintrc.cjs was missed by the
    original allowlist; many modern Node projects use it."""
    (tmp_path / ".eslintrc.cjs").write_text("module.exports = {};")
    assert _infer(leerie, tmp_path)["lint"] == "npx eslint ."


def test_eslintrc_yaml_infers_eslint(leerie, tmp_path):
    """Third-pass audit follow-up — .eslintrc.yaml variant."""
    (tmp_path / ".eslintrc.yaml").write_text("env:\n  node: true\n")
    assert _infer(leerie, tmp_path)["lint"] == "npx eslint ."


def test_eslintrc_yml_infers_eslint(leerie, tmp_path):
    """Third-pass audit follow-up — .eslintrc.yml variant."""
    (tmp_path / ".eslintrc.yml").write_text("env:\n  node: true\n")
    assert _infer(leerie, tmp_path)["lint"] == "npx eslint ."


def test_ruff_toml_infers_ruff(leerie, tmp_path):
    (tmp_path / "ruff.toml").write_text("line-length = 100\n")
    assert _infer(leerie, tmp_path)["lint"] == "ruff check ."


def test_polyglot_node_python_picks_npm_build(leerie, tmp_path):
    """When both package.json and pyproject.toml exist, build should be
    populated (npm wins because it's checked first); test gets a value
    too — npm test is set by package.json, not overridden by pyproject."""
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "pyproject.toml").write_text("[tool]\nname='x'\n")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "npm run build"
    # `out["test"] or "..."` short-circuits — npm test (from package.json,
    # checked first) wins over pytest.
    assert blt["test"] == "npm test"


def test_makefile_infers_make(leerie, tmp_path):
    (tmp_path / "Makefile").write_text("all:\n\techo ok\n")
    assert _infer(leerie, tmp_path)["build"] == "make"


def test_rails_repo_infers_bin_rails_test(leerie, tmp_path):
    (tmp_path / "Gemfile.lock").write_text("GEM\n  specs:\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "rails").write_text("#!/usr/bin/env ruby\n")
    blt = _infer(leerie, tmp_path)
    assert blt["test"] == "bin/rails test"
    assert blt["build"] == ""


def test_gemfile_lock_without_bin_rails_does_not_infer_rails_test(leerie, tmp_path):
    """Sinatra/Grape repos have Gemfile.lock but no bin/rails."""
    (tmp_path / "Gemfile.lock").write_text("GEM\n  specs:\n")
    assert _infer(leerie, tmp_path)["test"] == ""


def test_rubocop_yml_infers_rubocop(leerie, tmp_path):
    (tmp_path / ".rubocop.yml").write_text("AllCops:\n  NewCops: enable\n")
    blt = _infer(leerie, tmp_path)
    assert blt["lint"] == "bundle exec rubocop"
    assert blt["test"] == ""


def test_rubocop_yaml_infers_rubocop(leerie, tmp_path):
    (tmp_path / ".rubocop.yaml").write_text("AllCops:\n  NewCops: enable\n")
    assert _infer(leerie, tmp_path)["lint"] == "bundle exec rubocop"


def test_polyglot_node_rails_picks_npm_test_and_rails_test_not_overridden(leerie, tmp_path):
    """When both package.json and a Rails repo exist, npm test wins for
    test (checked first); Rails detection does not overwrite it."""
    (tmp_path / "package.json").write_text('{"name":"x"}')
    (tmp_path / "Gemfile.lock").write_text("GEM\n  specs:\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "rails").write_text("#!/usr/bin/env ruby\n")
    blt = _infer(leerie, tmp_path)
    assert blt["test"] == "npm test"


# --- Java / Maven ---

def test_pom_xml_infers_maven(leerie, tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "mvn package"
    assert blt["test"] == "mvn test"


# --- Java / Kotlin / Gradle ---

def test_build_gradle_infers_gradle(leerie, tmp_path):
    (tmp_path / "build.gradle").write_text("apply plugin: 'java'\n")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "gradle build"
    assert blt["test"] == "gradle test"


def test_build_gradle_kts_infers_gradle(leerie, tmp_path):
    (tmp_path / "build.gradle.kts").write_text("plugins { java }\n")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "gradle build"
    assert blt["test"] == "gradle test"


def test_gradlew_prefers_wrapper(leerie, tmp_path):
    (tmp_path / "build.gradle").write_text("apply plugin: 'java'\n")
    (tmp_path / "gradlew").write_text("#!/bin/sh\n")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "./gradlew build"
    assert blt["test"] == "./gradlew test"


def test_pom_and_gradle_maven_wins(leerie, tmp_path):
    """Polyglot: Maven checked first, so pom.xml takes precedence."""
    (tmp_path / "pom.xml").write_text("<project/>")
    (tmp_path / "build.gradle").write_text("apply plugin: 'java'\n")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "mvn package"
    assert blt["test"] == "mvn test"


# --- C# / .NET ---

def test_sln_infers_dotnet(leerie, tmp_path):
    (tmp_path / "Foo.sln").write_text("")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "dotnet build"
    assert blt["test"] == "dotnet test"


def test_csproj_infers_dotnet(leerie, tmp_path):
    (tmp_path / "Foo.csproj").write_text("<Project/>")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "dotnet build"
    assert blt["test"] == "dotnet test"


# --- PHP ---

def test_phpunit_xml_infers_phpunit(leerie, tmp_path):
    (tmp_path / "phpunit.xml").write_text("<phpunit/>")
    blt = _infer(leerie, tmp_path)
    assert blt["test"] == "vendor/bin/phpunit"


def test_phpunit_xml_dist_infers_phpunit(leerie, tmp_path):
    (tmp_path / "phpunit.xml.dist").write_text("<phpunit/>")
    blt = _infer(leerie, tmp_path)
    assert blt["test"] == "vendor/bin/phpunit"


def test_phpstan_neon_infers_phpstan(leerie, tmp_path):
    (tmp_path / "phpstan.neon").write_text("parameters:\n  level: max\n")
    blt = _infer(leerie, tmp_path)
    assert blt["lint"] == "vendor/bin/phpstan analyse"


# --- Kotlin / detekt ---

def test_detekt_yml_infers_detekt(leerie, tmp_path):
    (tmp_path / "detekt.yml").write_text("complexity:\n")
    blt = _infer(leerie, tmp_path)
    assert blt["lint"] == "detekt"
    assert blt["build"] == ""
    assert blt["test"] == ""


def test_detekt_yaml_infers_detekt(leerie, tmp_path):
    (tmp_path / "detekt.yaml").write_text("complexity:\n")
    assert _infer(leerie, tmp_path)["lint"] == "detekt"


def test_gradle_and_detekt_fills_build_test_and_lint(leerie, tmp_path):
    """Gradle fills build/test; detekt fills only the lint axis — the two
    families are independent and compose rather than override each other."""
    (tmp_path / "build.gradle.kts").write_text("plugins { kotlin(\"jvm\") }\n")
    (tmp_path / "detekt.yml").write_text("complexity:\n")
    blt = _infer(leerie, tmp_path)
    assert blt["build"] == "gradle build"
    assert blt["test"] == "gradle test"
    assert blt["lint"] == "detekt"
