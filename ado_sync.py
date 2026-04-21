"""Bidirectional ADO ↔ NATLClaw sync — Feature B (ado_sync.py).

Two directions:

* **Import** (ADO → NATLClaw): fetches work items from the current sprint for
  the configured assignees and creates / updates NATLClaw tasks to match.
* **Export** (NATLClaw → ADO): pushes status changes for tasks that originated
  in ADO back to the corresponding work item's state field.

ADO is treated as **the source of truth for requirements**; NATLClaw is the
**source of truth for execution state**.  Once a task is ``in_progress`` or
``blocked`` in NATLClaw, the ADO state is updated to reflect that — but
NATLClaw's status is never overwritten by a subsequent ADO poll.

Usage::

    from connectors.ado import connector_from_config
    from ado_sync import sync

    connector = connector_from_config(config)
    tasks = await load_tasks(config.state_file)
    result = sync(connector, tasks, config)
    await save_tasks(tasks, config.state_file)
    print(result.summary())
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from connectors.ado import AzureDevOpsConnector, WorkItem
    from tasks import Task

logger = logging.getLogger(__name__)

# ADO priority integer → NATLClaw priority string
_ADO_PRIORITY_MAP: dict[int, str] = {
    1: "urgent",
    2: "high",
    3: "medium",
    4: "low",
}

# NATLClaw terminal statuses — never overwrite these from ADO on re-import
_TERMINAL_STATUSES = frozenset({"completed", "failed"})

# NATLClaw statuses where execution has started — ADO polls don't overwrite
_ACTIVE_STATUSES = frozenset({"in_progress", "blocked", "assigned", "negotiating"})


# ── Result dataclass ───────────────────────────────────────────────────


@dataclass
class SyncResult:
    """Counts from a single sync run."""

    imported: int = 0        # new NATLClaw tasks created from ADO
    updated: int = 0         # existing tasks refreshed from ADO
    pushed: int = 0          # status changes pushed back to ADO
    skipped: int = 0         # items already in sync — no action
    errors: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = []
        if self.imported:
            parts.append(f"{self.imported} imported")
        if self.updated:
            parts.append(f"{self.updated} updated")
        if self.pushed:
            parts.append(f"{self.pushed} pushed to ADO")
        if self.skipped:
            parts.append(f"{self.skipped} skipped")
        if self.errors:
            parts.append(f"{len(self.errors)} error(s)")
        return "Sync: " + (", ".join(parts) or "nothing to do")


# ── Import direction (ADO → NATLClaw) ─────────────────────────────────


def import_from_ado(
    connector: "AzureDevOpsConnector",
    tasks: "list[Task]",
    config: Any,
    *,
    dry_run: bool = False,
) -> SyncResult:
    """Pull work items from ADO and create / update NATLClaw tasks.

    Parameters
    ----------
    connector:
        Configured ``AzureDevOpsConnector``.
    tasks:
        The current NATLClaw task list (mutated in-place when not dry_run).
    config:
        ``AppConfig`` — reads ``ado_assignees`` and ``persona``.
    dry_run:
        When True, log what would happen without mutating the task list.
    """
    result = SyncResult()

    # Build index of existing ADO-linked tasks
    ado_index: dict[int, "Task"] = {
        t.ado_id: t for t in tasks if t.ado_id
    }

    # Fetch the current sprint
    sprint = connector.get_current_sprint()
    if sprint is None:
        logger.warning("[ado_sync] No current sprint found — skipping import")
        result.errors.append("No current sprint found")
        return result

    logger.info("[ado_sync] Importing from sprint: %s", sprint.name)

    # Fetch work items for each configured assignee (or all if none configured)
    assignee_emails: list[str | None] = list(getattr(config, "ado_assignees", ()) or ())
    if not assignee_emails:
        assignee_emails = [None]  # fetch all assignees

    seen_ids: set[int] = set()
    work_items: list["WorkItem"] = []
    for email in assignee_emails:
        try:
            items = connector.get_work_items(
                iteration_path=sprint.path,
                assigned_to_email=email,
            )
            for wi in items:
                if wi.id not in seen_ids:
                    seen_ids.add(wi.id)
                    work_items.append(wi)
        except Exception as exc:
            msg = f"get_work_items({email!r}) failed: {exc}"
            logger.warning("[ado_sync] %s", msg)
            result.errors.append(msg)

    logger.info("[ado_sync] Fetched %d work item(s)", len(work_items))

    default_persona = getattr(config, "persona", "") or ""

    for wi in work_items:
        existing = ado_index.get(wi.id)

        if existing is None:
            # New work item — create a NATLClaw task
            if dry_run:
                logger.info("[ado_sync][dry-run] Would import ADO #%d: %s", wi.id, wi.title)
            else:
                task = _work_item_to_task(wi, default_persona)
                tasks.append(task)
                logger.info("[ado_sync] Imported ADO #%d → task %s: %s",
                            wi.id, task.id, wi.title)
            result.imported += 1

        else:
            # Existing task — decide whether to update
            if existing.status in _TERMINAL_STATUSES:
                # Completed / failed tasks are not re-opened from ADO
                result.skipped += 1
                continue

            changed = _refresh_task_from_work_item(existing, wi)
            if changed:
                if dry_run:
                    logger.info("[ado_sync][dry-run] Would update task %s (ADO #%d)",
                                existing.id, wi.id)
                else:
                    logger.info("[ado_sync] Updated task %s from ADO #%d", existing.id, wi.id)
                result.updated += 1
            else:
                result.skipped += 1

    return result


def _work_item_to_task(wi: "WorkItem", default_persona: str) -> "Task":
    """Convert an ADO ``WorkItem`` into a new NATLClaw ``Task``."""
    from tasks import Task

    priority = _ADO_PRIORITY_MAP.get(wi.priority, "medium")

    description_parts = [wi.description] if wi.description else []
    if wi.acceptance_criteria:
        description_parts.append(f"Acceptance Criteria:\n{wi.acceptance_criteria}")
    description = "\n\n".join(description_parts) or wi.title

    return Task(
        title=wi.title,
        description=description,
        priority=priority,
        status="pending",
        assigned_to=default_persona,
        created_by="ado_sync",
        ado_id=wi.id,
        ado_url=wi.url,
    )


def _refresh_task_from_work_item(task: "Task", wi: "WorkItem") -> bool:
    """Update mutable fields of an existing task from a polled work item.

    Returns True when any change was made.
    ADO's state does NOT overwrite the NATLClaw status when the task is
    already active (in_progress / blocked / assigned) — NATLClaw owns
    execution state once work has started.
    """
    changed = False

    if task.title != wi.title:
        task.title = wi.title
        changed = True

    new_priority = _ADO_PRIORITY_MAP.get(wi.priority, "medium")
    if task.priority != new_priority:
        task.priority = new_priority
        changed = True

    if task.ado_url != wi.url:
        task.ado_url = wi.url
        changed = True

    # Only overwrite NATLClaw status when the task hasn't been started yet
    if task.status == "pending" and wi.natl_status != task.status:
        task.status = wi.natl_status
        changed = True

    return changed


# ── Export direction (NATLClaw → ADO) ─────────────────────────────────


def export_to_ado(
    connector: "AzureDevOpsConnector",
    tasks: "list[Task]",
    *,
    post_progress: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Push NATLClaw task status changes back to ADO work items.

    Parameters
    ----------
    connector:
        Configured ``AzureDevOpsConnector``.
    tasks:
        The current NATLClaw task list (``ado_synced_status`` is updated in-place).
    post_progress:
        When True, also post the latest progress note as an ADO comment.
    dry_run:
        When True, log changes without pushing.
    """
    result = SyncResult()

    ado_tasks = [t for t in tasks if t.ado_id]
    if not ado_tasks:
        logger.debug("[ado_sync] No ADO-linked tasks to export")
        return result

    for task in ado_tasks:
        if task.status == task.ado_synced_status:
            result.skipped += 1
            continue  # nothing changed since last push

        if dry_run:
            logger.info(
                "[ado_sync][dry-run] Would push ADO #%d: %s -> %s",
                task.ado_id, task.ado_synced_status or "(none)", task.status,
            )
            result.pushed += 1
            continue

        comment: str | None = None
        if post_progress and task.progress_notes:
            comment = f"NATLClaw update: {task.progress_notes[-1][:500]}"

        try:
            ok = connector.update_work_item_state(task.ado_id, task.status, comment=comment)
            if ok:
                task.ado_synced_status = task.status
                logger.info(
                    "[ado_sync] Pushed ADO #%d: %s -> %s",
                    task.ado_id, task.ado_synced_status, task.status,
                )
                result.pushed += 1
            else:
                msg = f"update_work_item_state(#{task.ado_id}, {task.status!r}) returned False"
                logger.warning("[ado_sync] %s", msg)
                result.errors.append(msg)
        except Exception as exc:
            msg = f"export ADO #{task.ado_id}: {exc}"
            logger.warning("[ado_sync] %s", msg)
            result.errors.append(msg)

    return result


# ── Combined sync ──────────────────────────────────────────────────────


def sync(
    connector: "AzureDevOpsConnector",
    tasks: "list[Task]",
    config: Any,
    *,
    pull: bool = True,
    push: bool = True,
    post_progress: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Run both import and export in one call.

    Parameters
    ----------
    pull:
        Run the import direction (ADO → NATLClaw).
    push:
        Run the export direction (NATLClaw → ADO).
    post_progress:
        When True, post the latest progress note as an ADO comment on export.
    dry_run:
        Log intended changes without mutating state or calling ADO APIs.
    """
    combined = SyncResult()

    if pull:
        import_result = import_from_ado(connector, tasks, config, dry_run=dry_run)
        combined.imported += import_result.imported
        combined.updated += import_result.updated
        combined.skipped += import_result.skipped
        combined.errors.extend(import_result.errors)

    if push:
        export_result = export_to_ado(
            connector, tasks, post_progress=post_progress, dry_run=dry_run
        )
        combined.pushed += export_result.pushed
        combined.skipped += export_result.skipped
        combined.errors.extend(export_result.errors)

    return combined


# ── Convenience: build connector + run sync from AppConfig ─────────────


def sync_from_config(
    config: Any,
    tasks: "list[Task]",
    *,
    pull: bool = True,
    push: bool = True,
    post_progress: bool = False,
    dry_run: bool = False,
) -> SyncResult:
    """Build the ADO connector from *config* and run :func:`sync`.

    Returns an empty ``SyncResult`` with an error if ADO is not configured.
    """
    try:
        from connectors.ado import connector_from_config
        connector = connector_from_config(config)
        if not connector._enabled:
            result = SyncResult()
            result.errors.append("ADO not configured (ADO_URL / ADO_PAT / ADO_PROJECT missing)")
            return result
        return sync(
            connector, tasks, config,
            pull=pull, push=push,
            post_progress=post_progress,
            dry_run=dry_run,
        )
    except Exception as exc:
        result = SyncResult()
        result.errors.append(f"sync_from_config failed: {exc}")
        return result
