"""Tests for project_context.py — project detection, metadata, persistence."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from project_context import (
    PROJECT_DEFAULTS,
    Project,
    detect_project,
    infer_active_work,
    save_project,
    load_projects,
    _is_git_repo,
    _detect_vcs,
)


# ── Project detection ──────────────────────────────────────────────────


class TestDetectProject:
    def test_python_pyproject(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[build-system]\nbuild-backend = 'hatchling'\n")
        proj = detect_project(tmp_path)
        assert proj is not None
        assert proj.language == "python"
        assert proj.framework == "hatch"
        assert proj.test_cmd == "pytest"

    def test_python_poetry(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'myapp'\n")
        proj = detect_project(tmp_path)
        assert proj is not None
        assert proj.language == "python"
        assert proj.framework == "poetry"

    def test_python_setuptools(self, tmp_path):
        (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires = ['setuptools']\n")
        proj = detect_project(tmp_path)
        assert proj.framework == "setuptools"

    def test_javascript_package_json(self, tmp_path):
        pkg = {"name": "my-app", "scripts": {"test": "jest", "build": "webpack"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))
        proj = detect_project(tmp_path)
        assert proj is not None
        assert proj.language == "javascript"
        assert proj.name == "my-app"
        assert proj.test_cmd == "jest"
        assert proj.build_cmd == "webpack"

    def test_javascript_invalid_package_json(self, tmp_path):
        (tmp_path / "package.json").write_text("not valid json")
        proj = detect_project(tmp_path)
        assert proj is not None
        assert proj.language == "javascript"
        assert proj.test_cmd == PROJECT_DEFAULTS["javascript"]["test"]

    def test_rust_cargo(self, tmp_path):
        (tmp_path / "Cargo.toml").write_text("[package]\nname = 'myrs'\n")
        proj = detect_project(tmp_path)
        assert proj is not None
        assert proj.language == "rust"
        assert proj.test_cmd == "cargo test"

    def test_java_pom(self, tmp_path):
        (tmp_path / "pom.xml").write_text("<project></project>")
        proj = detect_project(tmp_path)
        assert proj is not None
        assert proj.language == "java"

    def test_dotnet_csproj(self, tmp_path):
        (tmp_path / ".csproj").write_text("<Project></Project>")
        proj = detect_project(tmp_path)
        assert proj is not None
        assert proj.language == "dotnet"

    def test_generic_git_repo(self, tmp_path):
        (tmp_path / ".git").mkdir()
        with patch("project_context._get_git_branch", return_value="main"):
            proj = detect_project(tmp_path)
        assert proj is not None
        assert proj.language == "generic"
        assert proj.vcs == "git"

    def test_no_project(self, tmp_path):
        proj = detect_project(tmp_path)
        assert proj is None

    def test_nonexistent_path(self):
        proj = detect_project("/nonexistent/path/12345")
        assert proj is None


# ── VCS detection ──────────────────────────────────────────────────────


class TestVcsDetection:
    def test_git(self, tmp_path):
        (tmp_path / ".git").mkdir()
        assert _is_git_repo(tmp_path) is True
        assert _detect_vcs(tmp_path) == "git"

    def test_hg(self, tmp_path):
        (tmp_path / ".hg").mkdir()
        assert _detect_vcs(tmp_path) == "hg"

    def test_svn(self, tmp_path):
        (tmp_path / ".svn").mkdir()
        assert _detect_vcs(tmp_path) == "svn"

    def test_none(self, tmp_path):
        assert _is_git_repo(tmp_path) is False
        assert _detect_vcs(tmp_path) == ""


# ── Active work inference ──────────────────────────────────────────────


class TestInferActiveWork:
    def test_with_commits(self):
        proj = Project(path=".", name="test", language="python", branch="feat-auth")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = "Fix auth\nAdd login page\nUpdate deps\n"
            result = infer_active_work(proj)
        assert "feat-auth" in result
        assert "Fix auth" in result

    def test_no_commits(self):
        proj = Project(path=".", name="test", language="python", branch="main")
        with patch("subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            result = infer_active_work(proj)
        assert "main" in result

    def test_git_failure(self):
        proj = Project(path=".", name="test", language="python", branch="dev")
        with patch("subprocess.run", side_effect=Exception("git error")):
            result = infer_active_work(proj)
        assert "dev" in result


# ── Persistence ────────────────────────────────────────────────────────


class TestPersistence:
    def test_save_and_load(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        proj = Project(path="/test/proj", name="myproj", language="python")
        save_project(proj, state_file)

        loaded = load_projects(state_file)
        assert len(loaded) == 1
        assert loaded[0].name == "myproj"
        assert loaded[0].language == "python"

    def test_save_updates_existing(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        proj = Project(path="/test/proj", name="myproj", language="python")
        save_project(proj, state_file)

        proj.framework = "poetry"
        save_project(proj, state_file)

        loaded = load_projects(state_file)
        assert len(loaded) == 1
        assert loaded[0].framework == "poetry"

    def test_load_no_file(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        assert load_projects(state_file) == []

    def test_load_corrupt_file(self, tmp_path):
        state_file = str(tmp_path / "agent_state.json")
        projects_file = tmp_path / "projects.json"
        projects_file.write_text("not json", encoding="utf-8")
        assert load_projects(state_file) == []


# ── Project defaults ──────────────────────────────────────────────────


class TestProjectDefaults:
    def test_all_languages_have_defaults(self):
        for lang in ("python", "javascript", "rust", "java", "dotnet", "typescript"):
            assert lang in PROJECT_DEFAULTS
            defaults = PROJECT_DEFAULTS[lang]
            assert "test" in defaults
            assert "build" in defaults
            assert "format" in defaults


# ── Project dataclass ─────────────────────────────────────────────────


class TestProjectDataclass:
    def test_auto_timestamps(self):
        proj = Project(path=".", name="test", language="python")
        assert proj.detected_at != ""
        assert proj.last_activity != ""
        assert proj.last_activity == proj.detected_at
