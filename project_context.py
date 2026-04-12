"""Project context detection for NATLClaw.

Automatically detects project metadata (language, framework, test/build commands)
from workspace files and injects it into heartbeat prompts.

Features:
- Auto-detect from pyproject.toml, package.json, Cargo.toml, .csproj, etc.
- Active-work inference from git branch and recent commit messages
- Project switching (natl project set/unset/list)
- Context injection into heartbeat instructions
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Project types and their default commands
PROJECT_DEFAULTS = {
    "python": {"test": "pytest", "build": "python -m build", "format": "ruff"},
    "javascript": {"test": "npm test", "build": "npm run build", "format": "prettier"},
    "rust": {"test": "cargo test", "build": "cargo build", "format": "rustfmt"},
    "java": {"test": "mvn test", "build": "mvn package", "format": "google-java-format"},
    "dotnet": {"test": "dotnet test", "build": "dotnet build", "format": "dotnet format"},
    "typescript": {"test": "npm test", "build": "npm run build", "format": "prettier"},
}


@dataclass
class Project:
    """Project metadata."""

    path: str  # absolute path to project root
    name: str
    language: str
    framework: str = ""
    test_cmd: str = ""
    build_cmd: str = ""
    format_cmd: str = ""
    vcs: str = ""  # git, hg, etc.
    branch: str = ""
    active_work: str = ""  # inferred from recent commits
    detected_at: str = ""
    last_activity: str = ""

    def __post_init__(self) -> None:
        if not self.detected_at:
            self.detected_at = datetime.now(timezone.utc).isoformat()
        if not self.last_activity:
            self.last_activity = self.detected_at


def detect_project(root_path: str | Path) -> Project | None:
    """Detect project type and metadata from workspace files.

    Args:
        root_path: Project root directory to scan.

    Returns:
        Project instance if a supported project type is detected, None otherwise.
    """
    path = Path(root_path)
    if not path.exists():
        return None

    # Try to detect project type from common config files
    if path.joinpath("pyproject.toml").exists():
        return _detect_python_project(path)
    if path.joinpath("package.json").exists():
        return _detect_javascript_project(path)
    if path.joinpath("Cargo.toml").exists():
        return _detect_rust_project(path)
    if path.joinpath("pom.xml").exists():
        return _detect_java_project(path)
    if path.joinpath(".csproj").exists():
        return _detect_dotnet_project(path)

    # Fallback: generic directory with git
    if _is_git_repo(path):
        return Project(
            path=str(path),
            name=path.name,
            language="generic",
            vcs="git",
            branch=_get_git_branch(path),
        )

    return None


def _detect_python_project(path: Path) -> Project:
    """Detect Python project metadata from pyproject.toml."""
    # Try to read pyproject.toml to infer build/test tools
    toml_path = path / "pyproject.toml"
    if toml_path.exists():
        try:
            with open(toml_path, "r", encoding="utf-8") as f:
                content = f.read()
            # Check for common tools
            if "build-backend" in content or "hatchling" in content:
                framework = "hatch"
            elif "poetry" in content:
                framework = "poetry"
            elif "flit" in content:
                framework = "flit"
            elif "setuptools" in content:
                framework = "setuptools"
            else:
                framework = "unknown"
        except Exception:
            framework = "unknown"

    # Use defaults if not detected
    return Project(
        path=str(path),
        name=path.name,
        language="python",
        framework=framework,
        test_cmd=PROJECT_DEFAULTS["python"]["test"],
        build_cmd=PROJECT_DEFAULTS["python"]["build"],
        format_cmd=PROJECT_DEFAULTS["python"]["format"],
        vcs=_detect_vcs(path),
        branch=_get_git_branch(path) if _is_git_repo(path) else "",
    )


def _detect_javascript_project(path: Path) -> Project:
    """Detect JavaScript/Node.js project metadata from package.json."""
    try:
        with open(path / "package.json", "r", encoding="utf-8") as f:
            data = json.load(f)
        name = data.get("name", path.name)
        scripts = data.get("scripts", {})
        test_cmd = scripts.get("test", PROJECT_DEFAULTS["javascript"]["test"])
        build_cmd = scripts.get("build", PROJECT_DEFAULTS["javascript"]["build"])
        return Project(
            path=str(path),
            name=name,
            language="javascript",
            framework="npm",
            test_cmd=test_cmd,
            build_cmd=build_cmd,
            format_cmd=PROJECT_DEFAULTS["javascript"]["format"],
            vcs=_detect_vcs(path),
            branch=_get_git_branch(path) if _is_git_repo(path) else "",
        )
    except Exception:
        return Project(
            path=str(path),
            name=path.name,
            language="javascript",
            framework="npm",
            test_cmd=PROJECT_DEFAULTS["javascript"]["test"],
            build_cmd=PROJECT_DEFAULTS["javascript"]["build"],
            format_cmd=PROJECT_DEFAULTS["javascript"]["format"],
            vcs=_detect_vcs(path),
            branch=_get_git_branch(path) if _is_git_repo(path) else "",
        )


def _detect_rust_project(path: Path) -> Project:
    """Detect Rust project metadata from Cargo.toml."""
    return Project(
        path=str(path),
        name=path.name,
        language="rust",
        framework="cargo",
        test_cmd=PROJECT_DEFAULTS["rust"]["test"],
        build_cmd=PROJECT_DEFAULTS["rust"]["build"],
        format_cmd=PROJECT_DEFAULTS["rust"]["format"],
        vcs=_detect_vcs(path),
        branch=_get_git_branch(path) if _is_git_repo(path) else "",
    )


def _detect_java_project(path: Path) -> Project:
    """Detect Java project metadata from pom.xml."""
    return Project(
        path=str(path),
        name=path.name,
        language="java",
        framework="maven",
        test_cmd=PROJECT_DEFAULTS["java"]["test"],
        build_cmd=PROJECT_DEFAULTS["java"]["build"],
        format_cmd=PROJECT_DEFAULTS["java"]["format"],
        vcs=_detect_vcs(path),
        branch=_get_git_branch(path) if _is_git_repo(path) else "",
    )


def _detect_dotnet_project(path: Path) -> Project:
    """Detect .NET project metadata from .csproj files."""
    return Project(
        path=str(path),
        name=path.name,
        language="dotnet",
        framework=".net",
        test_cmd=PROJECT_DEFAULTS["dotnet"]["test"],
        build_cmd=PROJECT_DEFAULTS["dotnet"]["build"],
        format_cmd=PROJECT_DEFAULTS["dotnet"]["format"],
        vcs=_detect_vcs(path),
        branch=_get_git_branch(path) if _is_git_repo(path) else "",
    )


def _detect_vcs(path: Path) -> str:
    """Detect version control system."""
    if (path / ".git").exists():
        return "git"
    if (path / ".hg").exists():
        return "hg"
    if (path / ".svn").exists():
        return "svn"
    return ""


def _is_git_repo(path: Path) -> bool:
    """Check if a directory is a git repository."""
    return (path / ".git").exists()


def _get_git_branch(path: Path) -> str:
    """Get current git branch name."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.stdout.strip() or "main"
    except Exception:
        return "main"


def infer_active_work(project: Project) -> str:
    """Infer current work from recent commits and branch.

    Returns:
        String describing current activity, e.g., "Working on auth refactor in main branch"
    """
    try:
        # Get recent commit messages (last 5)
        result = subprocess.run(
            ["git", "-C", project.path, "log", "--format=%s", "-5"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        commits = [c.strip() for c in result.stdout.splitlines() if c.strip()]
        if commits:
            recent = " • ".join(commits[:3])  # most recent 3
            return f"Working on '{recent}' in {project.branch} branch"
    except Exception:
        pass
    return f"Active in {project.branch or 'default'} branch"


def save_project(project: Project, state_file: str) -> None:
    """Save project metadata to disk."""
    projects_file = os.path.join(os.path.dirname(state_file), "projects.json")
    try:
        # Load existing projects if any
        if os.path.exists(projects_file):
            with open(projects_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        else:
            data = []
        # Find and update existing project or append
        found = False
        for i, p in enumerate(data):
            if p["path"] == project.path:
                data[i] = project.__dict__
                found = True
                break
        if not found:
            data.append(project.__dict__)
        with open(projects_file, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as e:
        logger.warning("Failed to save projects.json: %s", e)


def load_projects(state_file: str) -> list[Project]:
    """Load project metadata from disk."""
    projects_file = os.path.join(os.path.dirname(state_file), "projects.json")
    if not os.path.exists(projects_file):
        return []
    try:
        with open(projects_file, "r", encoding="utf-8") as f:
            data = json.load(f)
        return [Project(**p) for p in data]
    except (json.JSONDecodeError, OSError, TypeError) as e:
        logger.warning("Failed to load projects.json: %s", e)
        return []


def detect_and_save_project(state_file: str, config: Any) -> Project | None:
    """Detect project in workspace and save it."""
    # Try to detect project from current directory
    project = detect_project(".")
    if project:
        save_project(project, state_file)
        logger.info("Detected project: %s (%s)", project.name, project.language)
    return project