"""Azure DevOps connector — work items, sprints, and pull requests.

Supports both **Azure DevOps Services** (cloud) and **Azure DevOps Server**
(on-premises TFS/ADO Server).

Configuration (.env)::

    ADO_URL          Base URL.
                     Cloud:   https://dev.azure.com/{organization}
                     On-prem: https://tfs.company.com/DefaultCollection
    ADO_PAT          Personal Access Token — used as Basic auth password.
    ADO_PROJECT      Project name (e.g. "MyProject").
    ADO_TEAM         Team name (e.g. "MyProject Team").
    ADO_API_VERSION  API version string (default: "7.1").

All HTTP calls are synchronous / blocking.  Wrap in ``asyncio.to_thread``
when calling from async contexts.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .base import ConnectorStatus

logger = logging.getLogger(__name__)

_DEFAULT_API_VERSION = "7.1"

# ADO work item state → NATLClaw task status
_ADO_STATE_MAP: dict[str, str] = {
    "New": "pending",
    "Active": "in_progress",
    "In Progress": "in_progress",
    "Committed": "in_progress",
    "Open": "pending",
    "Resolved": "completed",
    "Closed": "completed",
    "Done": "completed",
    "Removed": "failed",
    "Blocked": "blocked",
}

# NATLClaw task status → ADO work item state
_NATL_TO_ADO_STATE: dict[str, str] = {
    "pending": "New",
    "assigned": "Active",
    "in_progress": "Active",
    "blocked": "Blocked",
    "completed": "Resolved",
    "failed": "Removed",
}


# ── Data models ────────────────────────────────────────────────────────

@dataclass
class SprintInfo:
    """Current sprint / iteration metadata."""

    id: str
    name: str
    path: str
    start_date: str
    finish_date: str
    goal: str = ""
    team_name: str = ""


@dataclass
class WorkItem:
    """A single ADO work item (User Story, Task, Bug, Feature, etc.)."""

    id: int
    title: str
    type: str                   # "User Story" | "Task" | "Bug" | "Feature" | …
    state: str                  # ADO state string
    natl_status: str            # mapped NATLClaw status
    assigned_to: str = ""       # display name
    assigned_to_email: str = "" # UPN / email
    story_points: float | None = None
    iteration_path: str = ""
    area_path: str = ""
    description: str = ""
    acceptance_criteria: str = ""
    tags: list[str] = field(default_factory=list)
    parent_id: int | None = None
    url: str = ""               # browser URL to the work item
    priority: int = 2           # 1=Critical, 2=High, 3=Medium, 4=Low


@dataclass
class PullRequest:
    """A newly created or fetched pull request."""

    id: int
    title: str
    url: str
    source_branch: str
    target_branch: str
    status: str = "active"      # active | abandoned | completed


# ── Connector ──────────────────────────────────────────────────────────

class AzureDevOpsConnector:
    """Thin client for the Azure DevOps REST API.

    Parameters
    ----------
    url:
        Base URL (no trailing slash).  See module docstring.
    pat:
        Personal Access Token.
    project:
        ADO project name.
    team:
        ADO team name (required for iteration/sprint queries).
    api_version:
        REST API version string.  Defaults to ``"7.1"``.
    """

    def __init__(
        self,
        url: str,
        pat: str,
        project: str,
        team: str,
        api_version: str = _DEFAULT_API_VERSION,
    ) -> None:
        self._base = url.rstrip("/")
        self._project = project
        self._team = team
        self._ver = api_version
        # ADO Basic auth: base64(":{PAT}")
        token = base64.b64encode(f":{pat}".encode()).decode()
        self._auth_header = f"Basic {token}"
        self._enabled = bool(url and pat and project)

    # ── Health ─────────────────────────────────────────────────────────

    def health_check(self) -> ConnectorStatus:
        """Verify connectivity by fetching project metadata."""
        if not self._enabled:
            return ConnectorStatus("ado", enabled=False, healthy=False,
                                   error="ADO_URL / ADO_PAT / ADO_PROJECT not configured")
        try:
            self._get(f"{self._base}/{self._project}/_apis/projects/{self._project}",
                      params={"api-version": self._ver})
            return ConnectorStatus("ado", enabled=True, healthy=True)
        except Exception as exc:
            return ConnectorStatus("ado", enabled=True, healthy=False, error=str(exc))

    # ── Sprint / iteration ─────────────────────────────────────────────

    def get_current_sprint(self) -> SprintInfo | None:
        """Return the team's current iteration, or None if not found."""
        if not self._enabled:
            return None
        try:
            path = (
                f"{self._base}/{self._project}/{urllib.parse.quote(self._team)}"
                f"/_apis/work/teamsettings/iterations"
            )
            data = self._get(path, params={
                "$timeframe": "current",
                "api-version": self._ver,
            })
            items = data.get("value", [])
            if not items:
                return None
            it = items[0]
            attrs = it.get("attributes", {})
            return SprintInfo(
                id=it.get("id", ""),
                name=it.get("name", ""),
                path=it.get("path", ""),
                start_date=attrs.get("startDate", ""),
                finish_date=attrs.get("finishDate", ""),
                team_name=self._team,
            )
        except Exception as exc:
            logger.warning("[ado] get_current_sprint failed: %s", exc)
            return None

    # ── Work items ─────────────────────────────────────────────────────

    def get_work_items(
        self,
        iteration_path: str | None = None,
        assigned_to_email: str | None = None,
        states: list[str] | None = None,
        types: list[str] | None = None,
    ) -> list[WorkItem]:
        """Query work items with optional filters.

        Parameters
        ----------
        iteration_path:
            Limit to a specific iteration (e.g. "MyProject\\\\Sprint 42").
            When None, no iteration filter is applied.
        assigned_to_email:
            Filter by assignee UPN/email.  When None, returns all assignees.
        states:
            ADO states to include.  Defaults to non-terminal states.
        types:
            Work item types to include.  Defaults to User Story, Task, Bug, Feature.
        """
        if not self._enabled:
            return []

        if states is None:
            states = ["New", "Active", "In Progress", "Committed", "Open", "Blocked"]
        if types is None:
            types = ["User Story", "Task", "Bug", "Feature"]

        # Build WIQL query
        conditions: list[str] = [
            "[System.TeamProject] = @project",
            f"[System.WorkItemType] IN ({', '.join(repr(t) for t in types)})",
            f"[System.State] IN ({', '.join(repr(s) for s in states)})",
        ]
        if iteration_path:
            conditions.append(f"[System.IterationPath] UNDER '{iteration_path}'")
        if assigned_to_email:
            conditions.append(f"[System.AssignedTo] = '{assigned_to_email}'")

        wiql = (
            "SELECT [System.Id] FROM WorkItems WHERE "
            + " AND ".join(conditions)
            + " ORDER BY [Microsoft.VSTS.Common.Priority] ASC, [System.CreatedDate] ASC"
        )

        try:
            path = f"{self._base}/{self._project}/_apis/wit/wiql"
            result = self._post(path, {"query": wiql},
                                params={"api-version": self._ver})
            ids = [str(item["id"]) for item in result.get("workItems", [])]
            if not ids:
                return []
            return self._fetch_work_items(ids)
        except Exception as exc:
            logger.warning("[ado] get_work_items failed: %s", exc)
            return []

    def get_work_item(self, work_item_id: int) -> WorkItem | None:
        """Fetch a single work item by ID."""
        if not self._enabled:
            return None
        try:
            items = self._fetch_work_items([str(work_item_id)])
            return items[0] if items else None
        except Exception as exc:
            logger.warning("[ado] get_work_item(%s) failed: %s", work_item_id, exc)
            return None

    def _fetch_work_items(self, ids: list[str]) -> list[WorkItem]:
        """Bulk-fetch work items by ID list (max 200 per ADO batch limit)."""
        results: list[WorkItem] = []
        # ADO allows max 200 IDs per batch
        for batch_start in range(0, len(ids), 200):
            batch = ids[batch_start:batch_start + 200]
            path = f"{self._base}/_apis/wit/workitems"
            data = self._get(path, params={
                "ids": ",".join(batch),
                "$expand": "fields",
                "api-version": self._ver,
            })
            for raw in data.get("value", []):
                wi = _parse_work_item(raw, self._base, self._project)
                if wi:
                    results.append(wi)
        return results

    # ── Update work items ──────────────────────────────────────────────

    def update_work_item_state(
        self,
        work_item_id: int,
        natl_status: str,
        comment: str | None = None,
    ) -> bool:
        """Update the ADO state for a work item.

        Parameters
        ----------
        work_item_id:
            ADO work item ID.
        natl_status:
            NATLClaw task status string (mapped to ADO state internally).
        comment:
            Optional comment to add alongside the state change.
        """
        if not self._enabled:
            return False
        ado_state = _NATL_TO_ADO_STATE.get(natl_status)
        if not ado_state:
            logger.debug("[ado] No ADO state mapping for natl_status=%r", natl_status)
            return False
        ops: list[dict] = [
            {"op": "add", "path": "/fields/System.State", "value": ado_state},
        ]
        if comment:
            ops.append({"op": "add", "path": "/fields/System.History", "value": comment})
        try:
            path = f"{self._base}/{self._project}/_apis/wit/workitems/{work_item_id}"
            self._patch(path, ops, params={"api-version": self._ver},
                        content_type="application/json-patch+json")
            logger.debug("[ado] WI#%s → %s (%s)", work_item_id, ado_state, natl_status)
            return True
        except Exception as exc:
            logger.warning("[ado] update_work_item_state(%s) failed: %s", work_item_id, exc)
            return False

    def add_comment(self, work_item_id: int, text: str) -> bool:
        """Add a comment to an ADO work item."""
        if not self._enabled:
            return False
        try:
            path = (
                f"{self._base}/{self._project}/_apis/wit"
                f"/workitems/{work_item_id}/comments"
            )
            self._post(path, {"text": text}, params={"api-version": self._ver})
            return True
        except Exception as exc:
            logger.warning("[ado] add_comment(%s) failed: %s", work_item_id, exc)
            return False

    # ── Pull requests ──────────────────────────────────────────────────

    def create_pull_request(
        self,
        repository: str,
        title: str,
        source_branch: str,
        target_branch: str,
        description: str = "",
        work_item_ids: list[int] | None = None,
        auto_complete: bool = False,
    ) -> PullRequest | None:
        """Open a pull request and optionally link it to work items.

        Parameters
        ----------
        repository:
            Git repository name (not the full URL).
        title:
            PR title shown in ADO.
        source_branch:
            Source ref, e.g. ``"refs/heads/feature/auth-refactor"``.
        target_branch:
            Target ref, e.g. ``"refs/heads/main"``.
        description:
            PR description / body text (Markdown supported).
        work_item_ids:
            ADO work item IDs to link to this PR.
        auto_complete:
            When True, the PR will complete automatically once all policies pass.
        """
        if not self._enabled:
            return None

        def _branch_ref(b: str) -> str:
            return b if b.startswith("refs/") else f"refs/heads/{b}"

        payload: dict[str, Any] = {
            "title": title,
            "description": description,
            "sourceRefName": _branch_ref(source_branch),
            "targetRefName": _branch_ref(target_branch),
        }
        if work_item_ids:
            payload["workItemRefs"] = [{"id": str(wid)} for wid in work_item_ids]
        if auto_complete:
            payload["completionOptions"] = {"mergeStrategy": "squash"}

        try:
            path = (
                f"{self._base}/{self._project}/_apis/git"
                f"/repositories/{urllib.parse.quote(repository)}/pullrequests"
            )
            data = self._post(path, payload, params={"api-version": self._ver})
            pr_id = data.get("pullRequestId", 0)
            web_url = (
                f"{self._base}/{self._project}/_git"
                f"/{urllib.parse.quote(repository)}/pullrequest/{pr_id}"
            )
            return PullRequest(
                id=pr_id,
                title=data.get("title", title),
                url=web_url,
                source_branch=source_branch,
                target_branch=target_branch,
                status=data.get("status", "active"),
            )
        except Exception as exc:
            logger.warning("[ado] create_pull_request failed: %s", exc)
            return None

    def get_pull_request(self, repository: str, pr_id: int) -> PullRequest | None:
        """Fetch a pull request by ID."""
        if not self._enabled:
            return None
        try:
            path = (
                f"{self._base}/{self._project}/_apis/git"
                f"/repositories/{urllib.parse.quote(repository)}/pullrequests/{pr_id}"
            )
            data = self._get(path, params={"api-version": self._ver})
            web_url = (
                f"{self._base}/{self._project}/_git"
                f"/{urllib.parse.quote(repository)}/pullrequest/{pr_id}"
            )
            return PullRequest(
                id=data.get("pullRequestId", pr_id),
                title=data.get("title", ""),
                url=web_url,
                source_branch=data.get("sourceRefName", ""),
                target_branch=data.get("targetRefName", ""),
                status=data.get("status", "active"),
            )
        except Exception as exc:
            logger.warning("[ado] get_pull_request(%s) failed: %s", pr_id, exc)
            return None

    # ── HTTP helpers ───────────────────────────────────────────────────

    def _headers(self, content_type: str = "application/json") -> dict[str, str]:
        return {
            "Authorization": self._auth_header,
            "Content-Type": content_type,
            "Accept": "application/json",
        }

    def _build_url(self, path: str, params: dict | None) -> str:
        if not params:
            return path
        return f"{path}?{urllib.parse.urlencode(params)}"

    def _get(self, path: str, params: dict | None = None) -> dict:
        url = self._build_url(path, params)
        req = urllib.request.Request(url, headers=self._headers(), method="GET")
        return self._send(req)

    def _post(
        self,
        path: str,
        body: Any,
        params: dict | None = None,
        content_type: str = "application/json",
    ) -> dict:
        url = self._build_url(path, params)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers=self._headers(content_type), method="POST"
        )
        return self._send(req)

    def _patch(
        self,
        path: str,
        body: Any,
        params: dict | None = None,
        content_type: str = "application/json",
    ) -> dict:
        url = self._build_url(path, params)
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers=self._headers(content_type), method="PATCH"
        )
        return self._send(req)

    def _send(self, req: urllib.request.Request) -> dict:
        try:
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = resp.read()
                if not raw:
                    return {}
                return json.loads(raw)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(
                f"ADO API error {exc.code} for {req.full_url}: {body}"
            ) from exc


# ── Work item parsing ──────────────────────────────────────────────────

def _parse_work_item(raw: dict, base_url: str, project: str) -> WorkItem | None:
    """Parse a raw ADO work item dict into a ``WorkItem`` dataclass."""
    try:
        f = raw.get("fields", {})
        wi_id = raw.get("id", 0)
        assigned = f.get("System.AssignedTo") or {}
        if isinstance(assigned, str):
            assigned_name = assigned
            assigned_email = ""
        else:
            assigned_name = assigned.get("displayName", "")
            assigned_email = assigned.get("uniqueName", "")

        state = f.get("System.State", "")
        tags_raw = f.get("System.Tags", "") or ""
        tags = [t.strip() for t in tags_raw.split(";") if t.strip()]

        story_points_raw = f.get("Microsoft.VSTS.Scheduling.StoryPoints")
        story_points = float(story_points_raw) if story_points_raw is not None else None

        priority_raw = f.get("Microsoft.VSTS.Common.Priority", 2)
        try:
            priority = int(priority_raw)
        except (TypeError, ValueError):
            priority = 2

        web_url = (
            f"{base_url}/{urllib.parse.quote(project)}/_workitems/edit/{wi_id}"
        )

        return WorkItem(
            id=wi_id,
            title=f.get("System.Title", ""),
            type=f.get("System.WorkItemType", ""),
            state=state,
            natl_status=_ADO_STATE_MAP.get(state, "pending"),
            assigned_to=assigned_name,
            assigned_to_email=assigned_email,
            story_points=story_points,
            iteration_path=f.get("System.IterationPath", ""),
            area_path=f.get("System.AreaPath", ""),
            description=_strip_html(f.get("System.Description", "") or ""),
            acceptance_criteria=_strip_html(
                f.get("Microsoft.VSTS.Common.AcceptanceCriteria", "") or ""
            ),
            tags=tags,
            parent_id=f.get("System.Parent"),
            url=web_url,
            priority=priority,
        )
    except Exception as exc:
        logger.warning("[ado] Failed to parse work item: %s", exc)
        return None


def _strip_html(text: str) -> str:
    """Very light HTML → plain text (no dependencies)."""
    import re
    # Replace common block tags with newlines
    text = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", text, flags=re.IGNORECASE)
    # Remove all remaining tags
    text = re.sub(r"<[^>]+>", "", text)
    # Collapse whitespace
    text = re.sub(r"\n{3,}", "\n\n", text.strip())
    return text


# ── Convenience: build connector from AppConfig ────────────────────────

def connector_from_config(config: "Any") -> AzureDevOpsConnector:
    """Build an ``AzureDevOpsConnector`` from an ``AppConfig`` instance."""
    return AzureDevOpsConnector(
        url=getattr(config, "ado_url", ""),
        pat=getattr(config, "ado_pat", ""),
        project=getattr(config, "ado_project", ""),
        team=getattr(config, "ado_team", ""),
        api_version=getattr(config, "ado_api_version", _DEFAULT_API_VERSION),
    )
