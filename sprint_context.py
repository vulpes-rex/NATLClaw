"""Sprint context injection — Feature F.

Pulls the current sprint and assigned work items from Azure DevOps and formats
them as a ``== SPRINT CONTEXT ==`` prompt block injected into every heartbeat.

The block looks like::

    == SPRINT CONTEXT ==
    Sprint: Sprint 42 (ends 2026-04-30, 14 days remaining)
    Goal:   Stabilize payment flow and complete auth refactor
    Your assigned items:
      [IN PROGRESS] ADO #4821 - Auth middleware - 5 pts
      [PENDING]     ADO #4834 - CartService tests - 3 pts
      [PENDING]     ADO #4851 - Payment flow idempotency - 8 pts [needs three amigos]

Results are cached in ``state.context["sprint_context_cache"]`` with a
configurable TTL (default 30 min) so we don't hit the ADO API every heartbeat.

When ADO is not configured (``ado_url`` / ``ado_pat`` empty) or
``sprint_context_enabled`` is False, the block is silently skipped.
"""
from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from connectors.ado import AzureDevOpsConnector, SprintInfo, WorkItem
    from tasks import Task

logger = logging.getLogger(__name__)

# Status label used in the prompt block
_STATUS_LABEL: dict[str, str] = {
    "in_progress": "IN PROGRESS",
    "pending":     "PENDING    ",
    "blocked":     "BLOCKED    ",
    "completed":   "COMPLETED  ",
    "failed":      "FAILED     ",
    "assigned":    "ASSIGNED   ",
}


# ── Data model ─────────────────────────────────────────────────────────


@dataclass
class SprintWorkItem:
    """Slim work-item summary for the prompt block."""

    ado_id: int
    title: str
    wi_type: str              # "User Story" | "Task" | "Bug" | …
    ado_state: str            # raw ADO state string
    natl_status: str          # mapped NATLClaw status
    story_points: float | None = None
    needs_three_amigos: bool = False   # pending in NATLClaw with open questions


@dataclass
class SprintContext:
    """Everything needed to render the sprint context block."""

    sprint_name: str
    sprint_goal: str
    end_date: str              # "YYYY-MM-DD"
    days_remaining: int
    work_items: list[SprintWorkItem] = field(default_factory=list)
    fetched_at: str = ""       # ISO-8601 — used for TTL check


# ── Fetching ───────────────────────────────────────────────────────────


def get_sprint_context(
    connector: "AzureDevOpsConnector",
    assignee_emails: list[str],
    natl_tasks: "list[Task]" | None = None,
    *,
    now: datetime | None = None,
) -> SprintContext | None:
    """Fetch the current sprint and assigned work items from ADO.

    Parameters
    ----------
    connector:
        Configured ``AzureDevOpsConnector`` instance.
    assignee_emails:
        List of UPN/email addresses to filter work items by.
        Pass an empty list to fetch all assignees.
    natl_tasks:
        Current NATLClaw task list (used to flag three-amigos items).
    now:
        Override current time for testing.

    Returns ``None`` when the sprint cannot be fetched (e.g. ADO not
    configured, network error).
    """
    if now is None:
        now = datetime.now(timezone.utc)

    sprint: "SprintInfo | None" = connector.get_current_sprint()
    if sprint is None:
        logger.debug("[sprint_context] No current sprint found")
        return None

    # Days remaining
    days_remaining = 0
    if sprint.finish_date:
        try:
            end_dt = datetime.fromisoformat(sprint.finish_date.replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            days_remaining = max(0, (end_dt.date() - now.date()).days)
            end_date = end_dt.strftime("%Y-%m-%d")
        except (ValueError, TypeError):
            end_date = sprint.finish_date[:10] if sprint.finish_date else ""
    else:
        end_date = ""

    # Build three-amigos lookup: ado_id → has open questions
    _three_amigos_ids: set[int] = set()
    if natl_tasks:
        for t in natl_tasks:
            ado_id = getattr(t, "ado_id", 0)
            if not ado_id or t.status != "pending":
                continue
            answers_ids = {a.get("question_id", "") for a in getattr(t, "answers", [])}
            has_open = any(
                q.get("id", "") not in answers_ids
                for q in getattr(t, "questions", [])
            )
            if has_open:
                _three_amigos_ids.add(ado_id)

    # Fetch work items for this sprint
    work_items: list[SprintWorkItem] = []
    for email in (assignee_emails or [None]):  # type: ignore[list-item]
        raw_items: list[WorkItem] = connector.get_work_items(
            iteration_path=sprint.path,
            assigned_to_email=email if email else None,
        )
        for wi in raw_items:
            work_items.append(SprintWorkItem(
                ado_id=wi.id,
                title=wi.title,
                wi_type=wi.type,
                ado_state=wi.state,
                natl_status=wi.natl_status,
                story_points=wi.story_points,
                needs_three_amigos=wi.id in _three_amigos_ids,
            ))

    # Deduplicate (multiple assignee emails could return same item)
    seen: set[int] = set()
    deduped: list[SprintWorkItem] = []
    for item in work_items:
        if item.ado_id not in seen:
            seen.add(item.ado_id)
            deduped.append(item)

    return SprintContext(
        sprint_name=sprint.name,
        sprint_goal=sprint.goal,
        end_date=end_date,
        days_remaining=days_remaining,
        work_items=deduped,
        fetched_at=now.isoformat(),
    )


# ── Formatting ─────────────────────────────────────────────────────────


def build_sprint_context_block(ctx: SprintContext) -> str:
    """Render a ``SprintContext`` as a ``== SPRINT CONTEXT ==`` prompt block."""
    if not ctx:
        return ""

    lines: list[str] = ["== SPRINT CONTEXT =="]

    end_part = f"ends {ctx.end_date}, " if ctx.end_date else ""
    days_part = f"{ctx.days_remaining} day{'s' if ctx.days_remaining != 1 else ''} remaining"
    lines.append(f"Sprint: {ctx.sprint_name} ({end_part}{days_part})")

    if ctx.sprint_goal:
        lines.append(f"Goal:   {ctx.sprint_goal}")

    if ctx.work_items:
        lines.append("Your assigned items:")
        for wi in ctx.work_items:
            label = _STATUS_LABEL.get(wi.natl_status, wi.natl_status.upper().ljust(11))
            pts = f" - {wi.story_points:.0f} pts" if wi.story_points is not None else ""
            flag = " [needs three amigos]" if wi.needs_three_amigos else ""
            lines.append(f"  [{label}] ADO #{wi.ado_id} - {wi.title}{pts}{flag}")
    else:
        lines.append("Your assigned items: (none found for this sprint)")

    return "\n".join(lines)


# ── TTL / caching helpers ──────────────────────────────────────────────


def should_refresh(
    fetched_at: str | None,
    *,
    ttl_minutes: int = 30,
    now: datetime | None = None,
) -> bool:
    """Return True when the cached sprint context is stale or missing."""
    if not fetched_at:
        return True
    if now is None:
        now = datetime.now(timezone.utc)
    try:
        ts = fetched_at.replace("Z", "+00:00")
        fetched_dt = datetime.fromisoformat(ts)
        if fetched_dt.tzinfo is None:
            fetched_dt = fetched_dt.replace(tzinfo=timezone.utc)
        age = (now - fetched_dt).total_seconds() / 60
        return age >= ttl_minutes
    except (ValueError, TypeError):
        return True


def cache_to_dict(ctx: SprintContext) -> dict[str, Any]:
    """Serialize a ``SprintContext`` to a plain dict for storage in state."""
    return asdict(ctx)


def cache_from_dict(data: dict[str, Any]) -> SprintContext | None:
    """Deserialize a ``SprintContext`` from state cache dict."""
    if not data:
        return None
    try:
        work_items = [
            SprintWorkItem(**{k: v for k, v in wi.items()
                              if k in SprintWorkItem.__dataclass_fields__})
            for wi in data.get("work_items", [])
        ]
        return SprintContext(
            sprint_name=data.get("sprint_name", ""),
            sprint_goal=data.get("sprint_goal", ""),
            end_date=data.get("end_date", ""),
            days_remaining=data.get("days_remaining", 0),
            work_items=work_items,
            fetched_at=data.get("fetched_at", ""),
        )
    except Exception as exc:
        logger.debug("[sprint_context] cache_from_dict failed: %s", exc)
        return None


# ── Convenience: get block for scheduler use ───────────────────────────


def get_sprint_block(
    config: Any,
    natl_tasks: "list[Task]" | None = None,
    cached: dict[str, Any] | None = None,
    *,
    now: datetime | None = None,
) -> tuple[str, dict[str, Any]]:
    """Return (prompt_block, updated_cache_dict) for use in the scheduler.

    Reads from cache if fresh; fetches from ADO otherwise.  Always returns
    an empty block string when ADO is not configured or an error occurs.

    Parameters
    ----------
    config:
        ``AppConfig`` instance (reads ``ado_*`` and ``sprint_context_ttl_minutes``).
    natl_tasks:
        Current NATLClaw task list (for three-amigos flagging).
    cached:
        Previously stored ``cache_to_dict()`` output from ``state.context``.
    now:
        Override current time for testing.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    ttl = int(getattr(config, "sprint_context_ttl_minutes", 30))

    # Try cache first
    if cached:
        ctx = cache_from_dict(cached)
        if ctx and not should_refresh(ctx.fetched_at, ttl_minutes=ttl, now=now):
            return build_sprint_context_block(ctx), cached

    # Need a fresh fetch — requires ADO
    try:
        from connectors.ado import connector_from_config
        connector = connector_from_config(config)
        if not connector._enabled:
            return "", cached or {}

        assignee_emails = list(getattr(config, "ado_assignees", ()) or ())
        ctx = get_sprint_context(connector, assignee_emails, natl_tasks, now=now)
        if ctx is None:
            return "", cached or {}

        new_cache = cache_to_dict(ctx)
        return build_sprint_context_block(ctx), new_cache
    except Exception as exc:
        logger.warning("[sprint_context] fetch failed: %s", exc)
        return "", cached or {}
