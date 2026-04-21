"""Tests for coordinator maturity features: dependencies, routing, file locks."""
from __future__ import annotations

import json
import pytest

from tasks import (
    Task,
    active_file_locks,
    assign_task,
    check_file_conflicts,
    claim_file_locks,
    complete_task,
    create_task,
    dependencies_met,
    fail_task,
    get_all_pending_tasks,
    get_pending_tasks,
    release_file_locks,
    start_task,
    unmet_dependencies,
)


# ── Task dependency tests ──────────────────────────────────────────────


class TestTaskDependencies:
    def test_create_task_with_depends_on(self):
        t = create_task("child", depends_on=["t_parent1", "t_parent2"])
        assert t.depends_on == ["t_parent1", "t_parent2"]
        assert t.status == "pending"

    def test_dependencies_met_no_deps(self):
        t = create_task("standalone")
        assert dependencies_met(t, []) is True

    def test_dependencies_met_all_complete(self):
        parent = Task(id="tp1", title="parent", status="completed")
        child = create_task("child", depends_on=["tp1"])
        assert dependencies_met(child, [parent, child]) is True

    def test_dependencies_not_met(self):
        parent = Task(id="tp1", title="parent", status="in_progress")
        child = create_task("child", depends_on=["tp1"])
        assert dependencies_met(child, [parent, child]) is False

    def test_dependencies_partial(self):
        p1 = Task(id="tp1", title="p1", status="completed")
        p2 = Task(id="tp2", title="p2", status="pending")
        child = create_task("child", depends_on=["tp1", "tp2"])
        assert dependencies_met(child, [p1, p2, child]) is False

    def test_unmet_dependencies(self):
        p1 = Task(id="tp1", title="p1", status="completed")
        p2 = Task(id="tp2", title="p2", status="pending")
        child = create_task("child", depends_on=["tp1", "tp2"])
        unmet = unmet_dependencies(child, [p1, p2, child])
        assert unmet == ["tp2"]

    def test_get_pending_tasks_filters_unmet_deps(self):
        parent = Task(id="tp1", title="parent", status="pending")
        child = create_task("child", depends_on=["tp1"])
        tasks = [parent, child]
        pending = get_pending_tasks(tasks)
        # Only parent is eligible (child blocked by dep)
        assert len(pending) == 1
        assert pending[0].id == "tp1"

    def test_get_pending_tasks_includes_met_deps(self):
        parent = Task(id="tp1", title="parent", status="completed")
        child = Task(id="tc1", title="child", status="pending",
                     depends_on=["tp1"])
        tasks = [parent, child]
        pending = get_pending_tasks(tasks)
        assert len(pending) == 1
        assert pending[0].id == "tc1"


# ── Persona routing tests ─────────────────────────────────────────────


class TestPersonaRouting:
    def test_create_task_with_target_persona(self):
        t = create_task("review code", target_persona="workspace_observer")
        assert t.target_persona == "workspace_observer"

    def test_get_pending_tasks_filters_by_persona(self):
        t1 = Task(id="t1", title="for observer", status="pending",
                  target_persona="workspace_observer")
        t2 = Task(id="t2", title="for learner", status="pending",
                  target_persona="codebase_learner")
        t3 = Task(id="t3", title="for anyone", status="pending",
                  target_persona="")
        tasks = [t1, t2, t3]

        # Observer sees t1 and t3 (no target = available to all)
        observer_pending = get_pending_tasks(tasks, "workspace_observer")
        ids = [t.id for t in observer_pending]
        assert "t1" in ids
        assert "t3" in ids
        assert "t2" not in ids

        # Learner sees t2 and t3
        learner_pending = get_pending_tasks(tasks, "codebase_learner")
        ids = [t.id for t in learner_pending]
        assert "t2" in ids
        assert "t3" in ids
        assert "t1" not in ids

    def test_get_pending_tasks_no_persona_sees_all(self):
        t1 = Task(id="t1", title="targeted", status="pending",
                  target_persona="workspace_observer")
        t2 = Task(id="t2", title="general", status="pending")
        tasks = [t1, t2]
        pending = get_pending_tasks(tasks)
        assert len(pending) == 2

    def test_get_all_pending_tasks_ignores_routing(self):
        t1 = Task(id="t1", title="targeted", status="pending",
                  target_persona="workspace_observer")
        t2 = Task(id="t2", title="general", status="pending")
        tasks = [t1, t2]
        all_pending = get_all_pending_tasks(tasks)
        assert len(all_pending) == 2


# ── File lock tests ────────────────────────────────────────────────────


class TestFileLocks:
    def test_claim_file_locks(self):
        t = create_task("edit files")
        claim_file_locks(t, ["src/main.py", "src/utils.py"])
        assert t.file_locks == ["src/main.py", "src/utils.py"]

    def test_claim_file_locks_idempotent(self):
        t = create_task("edit files")
        claim_file_locks(t, ["src/main.py"])
        claim_file_locks(t, ["src/main.py", "src/utils.py"])
        assert len(t.file_locks) == 2

    def test_release_file_locks(self):
        t = create_task("edit files")
        t.file_locks = ["src/main.py", "src/utils.py"]
        released = release_file_locks(t)
        assert released == ["src/main.py", "src/utils.py"]
        assert t.file_locks == []

    def test_active_file_locks(self):
        t1 = Task(id="t1", title="a", status="in_progress",
                  file_locks=["src/main.py"])
        t2 = Task(id="t2", title="b", status="pending",
                  file_locks=["src/utils.py"])  # not active
        t3 = Task(id="t3", title="c", status="assigned",
                  file_locks=["src/config.py"])
        locks = active_file_locks([t1, t2, t3])
        assert "src/main.py" in locks
        assert "src/config.py" in locks
        assert "src/utils.py" not in locks  # pending task doesn't hold locks

    def test_check_file_conflicts(self):
        t1 = Task(id="t1", title="a", status="in_progress",
                  file_locks=["src/main.py"])
        t2 = Task(id="t2", title="b", status="pending",
                  file_locks=["src/main.py", "src/other.py"])
        conflicts = check_file_conflicts(t2, [t1, t2])
        assert len(conflicts) == 1
        assert conflicts[0] == ("src/main.py", "t1")

    def test_check_file_conflicts_no_overlap(self):
        t1 = Task(id="t1", title="a", status="in_progress",
                  file_locks=["src/main.py"])
        t2 = Task(id="t2", title="b", status="pending",
                  file_locks=["src/other.py"])
        conflicts = check_file_conflicts(t2, [t1, t2])
        assert conflicts == []

    def test_complete_task_releases_locks(self):
        t = Task(id="t1", title="a", status="in_progress",
                 file_locks=["src/main.py"])
        complete_task(t)
        assert t.file_locks == []

    def test_fail_task_releases_locks(self):
        t = Task(id="t1", title="a", status="in_progress",
                 file_locks=["src/main.py"])
        fail_task(t, "oops")
        assert t.file_locks == []

    def test_path_normalization(self):
        t1 = Task(id="t1", title="a", status="in_progress",
                  file_locks=["src\\main.py"])
        t2 = Task(id="t2", title="b", status="pending",
                  file_locks=["src/main.py"])
        conflicts = check_file_conflicts(t2, [t1, t2])
        assert len(conflicts) == 1  # normalized paths match


# ── Combined scenarios ─────────────────────────────────────────────────


class TestCombinedScenarios:
    def test_deps_plus_routing(self):
        """Task with both dependencies and persona routing."""
        parent = Task(id="tp1", title="gather", status="completed",
                      target_persona="workspace_observer")
        child = Task(id="tc1", title="analyse", status="pending",
                     depends_on=["tp1"], target_persona="codebase_learner")
        tasks = [parent, child]

        # Learner sees the child because dep is met and routing matches
        pending = get_pending_tasks(tasks, "codebase_learner")
        assert len(pending) == 1
        assert pending[0].id == "tc1"

        # Observer does NOT see the child (wrong persona)
        pending = get_pending_tasks(tasks, "workspace_observer")
        assert len(pending) == 0

    def test_deps_not_met_blocks_even_with_correct_routing(self):
        parent = Task(id="tp1", title="gather", status="pending")
        child = Task(id="tc1", title="analyse", status="pending",
                     depends_on=["tp1"], target_persona="codebase_learner")
        tasks = [parent, child]
        pending = get_pending_tasks(tasks, "codebase_learner")
        # Child blocked by unmet dep, even though routing matches
        assert all(t.id != "tc1" for t in pending)

    def test_delegation_json_parsing(self):
        """Test extraction of delegation JSON from coordinator output."""
        from workflow import _extract_delegations

        roster = ["workspace_observer", "codebase_learner"]
        text = (
            'Good progress.\n'
            '{"delegate": ['
            '  {"persona": "workspace_observer", "task": "Check recent commits", "files": ["scheduler.py"]},'
            '  {"persona": "codebase_learner", "task": "Map module dependencies"}'
            ']}'
        )
        result = _extract_delegations(text, roster)
        assert len(result) == 2
        assert result[0]["persona"] == "workspace_observer"
        assert result[0]["task"] == "Check recent commits"
        assert result[0]["files"] == ["scheduler.py"]
        assert result[1]["persona"] == "codebase_learner"
        assert result[1]["files"] == []

    def test_delegation_rejects_unknown_persona(self):
        from workflow import _extract_delegations

        roster = ["workspace_observer"]
        text = '{"delegate": [{"persona": "hacker", "task": "bad stuff"}]}'
        result = _extract_delegations(text, roster)
        assert result == []

    def test_delegation_no_json(self):
        from workflow import _extract_delegations

        roster = ["workspace_observer"]
        text = "Everything looks good. Focus on tests next cycle."
        result = _extract_delegations(text, roster)
        assert result == []

    def test_task_board_block(self):
        """_build_task_board_block produces readable output."""
        from workflow import _build_task_board_block

        tasks = [
            Task(id="t1", title="observe", status="in_progress",
                 assigned_to="workspace_observer", file_locks=["scheduler.py"],
                 heartbeats_spent=2, max_heartbeats=5),
            Task(id="t2", title="learn", status="pending",
                 target_persona="codebase_learner", depends_on=["t1"]),
            Task(id="t3", title="question", status="blocked",
                 assigned_to="workspace_observer",
                 questions=[{"question": "Which branch?"}]),
        ]
        roster = ["workspace_observer", "codebase_learner"]
        block = _build_task_board_block(tasks, roster)
        assert "TASK BOARD" in block
        assert "@workspace_observer" in block
        assert "scheduler.py" in block
        assert "waiting: t1" in block
        assert "Which branch?" in block
