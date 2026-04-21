"""Unit tests for connectors/ — ADO, Teams, Outlook.

All HTTP is mocked via unittest.mock so no network calls are made.
"""
from __future__ import annotations

import base64
import json
from unittest.mock import MagicMock, patch

import pytest

# ══════════════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════════════

def _mock_response(data: dict | str | None, status: int = 200):
    """Build a fake urllib response context manager."""
    resp = MagicMock()
    resp.status = status
    if data is None:
        resp.read.return_value = b""
    elif isinstance(data, str):
        resp.read.return_value = data.encode()
    else:
        resp.read.return_value = json.dumps(data).encode()
    resp.__enter__ = lambda s: s
    resp.__exit__ = MagicMock(return_value=False)
    return resp


def _http_error(code: int, body: str = "error"):
    import urllib.error
    err = urllib.error.HTTPError(
        url="http://test",
        code=code,
        msg="error",
        hdrs=None,  # type: ignore[arg-type]
        fp=None,    # type: ignore[arg-type]
    )
    err.read = lambda: body.encode()
    return err


# ══════════════════════════════════════════════════════════════════════
# ConnectorStatus
# ══════════════════════════════════════════════════════════════════════

class TestConnectorStatus:
    def test_str_disabled(self):
        from connectors.base import ConnectorStatus
        s = ConnectorStatus("ado", enabled=False, healthy=False, error="no creds")
        assert "disabled" in str(s)

    def test_str_healthy(self):
        from connectors.base import ConnectorStatus
        s = ConnectorStatus("ado", enabled=True, healthy=True)
        assert "ok" in str(s)

    def test_str_error(self):
        from connectors.base import ConnectorStatus
        s = ConnectorStatus("ado", enabled=True, healthy=False, error="timeout")
        assert "timeout" in str(s)


# ══════════════════════════════════════════════════════════════════════
# GraphTokenCache
# ══════════════════════════════════════════════════════════════════════

class TestGraphTokenCache:
    def _make_token_response(self, token: str = "tok", expires_in: int = 3600):
        return {"access_token": token, "expires_in": expires_in}

    def test_fetches_token_on_first_call(self):
        from connectors.graph_auth import GraphTokenCache
        cache = GraphTokenCache()
        resp = _mock_response(self._make_token_response("abc123"))
        with patch("urllib.request.urlopen", return_value=resp):
            token = cache.get("tenant", "client", "secret")
        assert token == "abc123"

    def test_returns_cached_token(self):
        from connectors.graph_auth import GraphTokenCache
        cache = GraphTokenCache()
        resp = _mock_response(self._make_token_response("tok1"))
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            cache.get("tenant", "client", "secret")
            cache.get("tenant", "client", "secret")
        assert mock_open.call_count == 1

    def test_invalidate_forces_refresh(self):
        from connectors.graph_auth import GraphTokenCache
        cache = GraphTokenCache()
        r1 = _mock_response(self._make_token_response("tok1"))
        r2 = _mock_response(self._make_token_response("tok2"))
        with patch("urllib.request.urlopen", side_effect=[r1, r2]):
            t1 = cache.get("tenant", "client", "secret")
            cache.invalidate("tenant", "client")
            t2 = cache.get("tenant", "client", "secret")
        assert t1 == "tok1"
        assert t2 == "tok2"

    def test_raises_on_http_error(self):
        from connectors.graph_auth import GraphTokenCache
        cache = GraphTokenCache()
        with patch("urllib.request.urlopen", side_effect=_http_error(401)):
            with pytest.raises(RuntimeError, match="401"):
                cache.get("tenant", "client", "bad_secret")


# ══════════════════════════════════════════════════════════════════════
# AzureDevOpsConnector
# ══════════════════════════════════════════════════════════════════════

class TestADOConnector:
    def _make_connector(self):
        from connectors.ado import AzureDevOpsConnector
        return AzureDevOpsConnector(
            url="https://tfs.company.com/DefaultCollection",
            pat="test-pat",
            project="MyProject",
            team="MyProject Team",
        )

    def test_health_check_disabled_when_no_url(self):
        from connectors.ado import AzureDevOpsConnector
        conn = AzureDevOpsConnector(url="", pat="x", project="p", team="t")
        status = conn.health_check()
        assert not status.enabled
        assert not status.healthy

    def test_health_check_ok(self):
        conn = self._make_connector()
        resp = _mock_response({"id": "proj-123", "name": "MyProject"})
        with patch("urllib.request.urlopen", return_value=resp):
            status = conn.health_check()
        assert status.enabled
        assert status.healthy

    def test_health_check_error(self):
        conn = self._make_connector()
        with patch("urllib.request.urlopen", side_effect=_http_error(401)):
            status = conn.health_check()
        assert status.enabled
        assert not status.healthy
        assert "401" in status.error

    def test_auth_header_uses_pat(self):
        """Auth header must be Basic base64(':{PAT}')."""
        conn = self._make_connector()
        expected = "Basic " + base64.b64encode(b":test-pat").decode()
        assert conn._auth_header == expected

    def test_get_current_sprint_returns_none_when_empty(self):
        conn = self._make_connector()
        resp = _mock_response({"value": []})
        with patch("urllib.request.urlopen", return_value=resp):
            sprint = conn.get_current_sprint()
        assert sprint is None

    def test_get_current_sprint_parses_response(self):
        conn = self._make_connector()
        data = {"value": [{
            "id": "sprint-guid",
            "name": "Sprint 42",
            "path": "MyProject\\Sprint 42",
            "attributes": {
                "startDate": "2026-04-14T00:00:00Z",
                "finishDate": "2026-04-28T00:00:00Z",
            },
        }]}
        resp = _mock_response(data)
        with patch("urllib.request.urlopen", return_value=resp):
            sprint = conn.get_current_sprint()
        assert sprint is not None
        assert sprint.name == "Sprint 42"
        assert sprint.finish_date == "2026-04-28T00:00:00Z"
        assert sprint.team_name == "MyProject Team"

    def test_get_work_items_empty_wiql_result(self):
        conn = self._make_connector()
        # WIQL returns empty, no bulk fetch
        wiql_resp = _mock_response({"workItems": []})
        with patch("urllib.request.urlopen", return_value=wiql_resp):
            items = conn.get_work_items()
        assert items == []

    def test_get_work_items_parses_items(self):
        conn = self._make_connector()
        wiql_resp = _mock_response({"workItems": [{"id": 4821}, {"id": 4834}]})
        bulk_resp = _mock_response({"value": [
            {
                "id": 4821,
                "fields": {
                    "System.Title": "Auth middleware refactor",
                    "System.WorkItemType": "User Story",
                    "System.State": "Active",
                    "System.AssignedTo": {"displayName": "Dev1", "uniqueName": "dev1@company.com"},
                    "Microsoft.VSTS.Scheduling.StoryPoints": 8,
                    "System.IterationPath": "MyProject\\Sprint 42",
                    "System.AreaPath": "MyProject\\Frontend",
                    "System.Tags": "auth; security",
                    "Microsoft.VSTS.Common.Priority": 1,
                },
            },
            {
                "id": 4834,
                "fields": {
                    "System.Title": "CartService unit tests",
                    "System.WorkItemType": "Task",
                    "System.State": "New",
                    "System.AssignedTo": {"displayName": "Dev1", "uniqueName": "dev1@company.com"},
                    "Microsoft.VSTS.Scheduling.StoryPoints": 3,
                    "System.IterationPath": "MyProject\\Sprint 42",
                    "System.AreaPath": "MyProject\\Frontend",
                    "System.Tags": "",
                    "Microsoft.VSTS.Common.Priority": 2,
                },
            },
        ]})
        with patch("urllib.request.urlopen", side_effect=[wiql_resp, bulk_resp]):
            items = conn.get_work_items()
        assert len(items) == 2
        assert items[0].id == 4821
        assert items[0].title == "Auth middleware refactor"
        assert items[0].natl_status == "in_progress"
        assert items[0].story_points == 8.0
        assert "auth" in items[0].tags
        assert items[1].natl_status == "pending"

    def test_update_work_item_state_maps_status(self):
        conn = self._make_connector()
        resp = _mock_response({"id": 4821})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            result = conn.update_work_item_state(4821, "in_progress")
        assert result is True
        # Verify PATCH body contains "Active"
        call_args = mock_open.call_args
        req = call_args[0][0]
        body = json.loads(req.data)
        assert any(op.get("value") == "Active" for op in body)

    def test_update_work_item_state_unknown_status(self):
        conn = self._make_connector()
        result = conn.update_work_item_state(4821, "negotiating")
        assert result is False  # no mapping, no HTTP call

    def test_update_work_item_state_with_comment(self):
        conn = self._make_connector()
        resp = _mock_response({"id": 4821})
        with patch("urllib.request.urlopen", return_value=resp):
            result = conn.update_work_item_state(4821, "completed", comment="PR merged.")
        assert result is True

    def test_add_comment(self):
        conn = self._make_connector()
        resp = _mock_response({"id": "comment-1"})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            result = conn.add_comment(4821, "Standup: working on auth middleware.")
        assert result is True
        req = mock_open.call_args[0][0]
        body = json.loads(req.data)
        assert "Standup" in body["text"]

    def test_add_comment_error_returns_false(self):
        conn = self._make_connector()
        with patch("urllib.request.urlopen", side_effect=_http_error(404)):
            result = conn.add_comment(9999, "test")
        assert result is False

    def test_create_pull_request(self):
        conn = self._make_connector()
        resp = _mock_response({
            "pullRequestId": 112,
            "title": "Auth middleware refactor",
            "status": "active",
            "sourceRefName": "refs/heads/feature/auth",
            "targetRefName": "refs/heads/main",
        })
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            pr = conn.create_pull_request(
                repository="MyRepo",
                title="Auth middleware refactor",
                source_branch="feature/auth",
                target_branch="main",
                description="Closes #4821",
                work_item_ids=[4821],
            )
        assert pr is not None
        assert pr.id == 112
        assert pr.status == "active"
        assert "112" in pr.url
        # Verify work item refs in payload
        req = mock_open.call_args[0][0]
        body = json.loads(req.data)
        assert body["workItemRefs"][0]["id"] == "4821"

    def test_create_pull_request_branch_ref_prefix(self):
        """Branches without refs/heads/ prefix should be auto-prefixed."""
        conn = self._make_connector()
        resp = _mock_response({"pullRequestId": 1, "title": "t", "status": "active"})
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            conn.create_pull_request("repo", "title", "feature/x", "main")
        req = mock_open.call_args[0][0]
        body = json.loads(req.data)
        assert body["sourceRefName"] == "refs/heads/feature/x"
        assert body["targetRefName"] == "refs/heads/main"

    def test_disabled_connector_returns_safe_defaults(self):
        from connectors.ado import AzureDevOpsConnector
        conn = AzureDevOpsConnector(url="", pat="", project="", team="")
        assert conn.get_current_sprint() is None
        assert conn.get_work_items() == []
        assert conn.update_work_item_state(1, "in_progress") is False
        assert conn.add_comment(1, "test") is False
        assert conn.create_pull_request("r", "t", "s", "m") is None

    def test_get_current_sprint_handles_network_error(self):
        conn = self._make_connector()
        with patch("urllib.request.urlopen", side_effect=OSError("connection refused")):
            sprint = conn.get_current_sprint()
        assert sprint is None


# ══════════════════════════════════════════════════════════════════════
# _parse_work_item
# ══════════════════════════════════════════════════════════════════════

class TestParseWorkItem:
    def test_parses_full_work_item(self):
        from connectors.ado import _parse_work_item
        raw = {
            "id": 4821,
            "fields": {
                "System.Title": "Auth middleware",
                "System.WorkItemType": "User Story",
                "System.State": "Active",
                "System.AssignedTo": {"displayName": "Alice", "uniqueName": "alice@co.com"},
                "Microsoft.VSTS.Scheduling.StoryPoints": 5,
                "System.IterationPath": "Proj\\Sprint 1",
                "System.AreaPath": "Proj\\Frontend",
                "System.Description": "<p>Do the thing.</p>",
                "Microsoft.VSTS.Common.AcceptanceCriteria": "<p>It works.</p>",
                "System.Tags": "auth; security",
                "Microsoft.VSTS.Common.Priority": 1,
            },
        }
        wi = _parse_work_item(raw, "https://tfs.company.com", "Proj")
        assert wi is not None
        assert wi.id == 4821
        assert wi.natl_status == "in_progress"
        assert wi.assigned_to == "Alice"
        assert wi.assigned_to_email == "alice@co.com"
        assert wi.story_points == 5.0
        assert "auth" in wi.tags
        assert "security" in wi.tags
        # HTML stripped from description
        assert "<p>" not in wi.description
        assert "Do the thing." in wi.description

    def test_handles_string_assigned_to(self):
        from connectors.ado import _parse_work_item
        raw = {
            "id": 1,
            "fields": {
                "System.Title": "T",
                "System.WorkItemType": "Task",
                "System.State": "New",
                "System.AssignedTo": "Alice <alice@co.com>",
            },
        }
        wi = _parse_work_item(raw, "https://base", "Proj")
        assert wi is not None
        assert wi.assigned_to == "Alice <alice@co.com>"

    def test_handles_missing_optional_fields(self):
        from connectors.ado import _parse_work_item
        raw = {"id": 2, "fields": {"System.Title": "Minimal", "System.WorkItemType": "Task", "System.State": "New"}}
        wi = _parse_work_item(raw, "https://base", "Proj")
        assert wi is not None
        assert wi.story_points is None
        assert wi.tags == []


# ══════════════════════════════════════════════════════════════════════
# TeamsConnector
# ══════════════════════════════════════════════════════════════════════

class TestTeamsConnector:
    def _make_connector(self):
        from connectors.teams import TeamsConnector
        return TeamsConnector(webhook_url="https://hooks.example.com/wh")

    def test_health_check_disabled_when_no_config(self):
        from connectors.teams import TeamsConnector
        conn = TeamsConnector()
        status = conn.health_check()
        assert not status.enabled

    def test_health_check_enabled_with_webhook(self):
        status = self._make_connector().health_check()
        assert status.enabled
        assert status.healthy

    def test_send_message_posts_to_webhook(self):
        conn = self._make_connector()
        resp = _mock_response("1", status=200)
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            result = conn.send_message("Hello team!", title="Test")
        assert result is True
        req = mock_open.call_args[0][0]
        body = json.loads(req.data)
        # Should be an Adaptive Card wrapper
        assert body["type"] == "message"
        assert body["attachments"][0]["contentType"] == "application/vnd.microsoft.card.adaptive"

    def test_send_message_returns_false_on_error(self):
        conn = self._make_connector()
        with patch("urllib.request.urlopen", side_effect=_http_error(500)):
            result = conn.send_message("Test")
        assert result is False

    def test_send_notification_includes_urgency(self):
        conn = self._make_connector()
        resp = _mock_response("1", status=200)
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            conn.send_notification("Title", "Body", urgency="urgent", task_id="t1", persona="dev")
        req = mock_open.call_args[0][0]
        body = json.loads(req.data)
        card = body["attachments"][0]["content"]
        # urgency "urgent" → colour "Attention"
        body_items = card["body"]
        title_block = body_items[0]
        assert title_block["color"] == "Attention"

    def test_send_standup_report_contains_persona_names(self):
        conn = self._make_connector()
        entries = [
            {"persona": "react_developer", "yesterday": "PR #112", "today": "tests", "blockers": ""},
            {"persona": "dotnet_developer", "yesterday": "Fixed bug", "today": "xUnit", "blockers": "Waiting on DB"},
        ]
        resp = _mock_response("1", status=200)
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            conn.send_standup_report(entries)
        req = mock_open.call_args[0][0]
        body_str = req.data.decode()
        assert "react_developer" in body_str
        assert "dotnet_developer" in body_str
        assert "xUnit" in body_str

    def test_send_standup_report_includes_three_amigos(self):
        conn = self._make_connector()
        entries = [{
            "persona": "pm",
            "yesterday": "Sprint planning",
            "today": "Facilitate 3A",
            "blockers": "",
            "three_amigos": ["Story #4851 — idempotency unclear"],
        }]
        resp = _mock_response("1", status=200)
        with patch("urllib.request.urlopen", return_value=resp) as mock_open:
            conn.send_standup_report(entries)
        body_str = mock_open.call_args[0][0].data.decode()
        assert "idempotency" in body_str
        assert "Three amigos" in body_str

    def test_no_send_when_no_transport(self):
        from connectors.teams import TeamsConnector
        conn = TeamsConnector()  # no webhook, no graph
        result = conn.send_message("test")
        assert result is False

    def test_webhook_http_400_returns_false(self):
        conn = self._make_connector()
        resp = _mock_response("error", status=400)
        with patch("urllib.request.urlopen", return_value=resp):
            result = conn.send_message("test")
        assert result is False


# ══════════════════════════════════════════════════════════════════════
# OutlookConnector
# ══════════════════════════════════════════════════════════════════════

class TestOutlookConnector:
    def _make_connector(self):
        from connectors.outlook import OutlookConnector
        return OutlookConnector(
            tenant_id="t1",
            client_id="c1",
            client_secret="s1",
            sender="agent@company.com",
        )

    def test_health_check_disabled_when_missing_creds(self):
        from connectors.outlook import OutlookConnector
        conn = OutlookConnector()
        status = conn.health_check()
        assert not status.enabled

    def test_health_check_ok(self):
        conn = self._make_connector()
        user_resp = _mock_response({"displayName": "NATLClaw Agent"})
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=user_resp):
            status = conn.health_check()
        assert status.enabled
        assert status.healthy

    def test_send_email_posts_to_graph(self):
        conn = self._make_connector()
        send_resp = _mock_response(None, status=202)
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=send_resp) as mock_open:
            result = conn.send_email(
                to=["dev@company.com"],
                subject="Test subject",
                body="<p>Hello</p>",
            )
        assert result is True
        req = mock_open.call_args[0][0]
        assert "sendMail" in req.full_url
        body = json.loads(req.data)
        assert body["message"]["subject"] == "Test subject"
        assert body["message"]["toRecipients"][0]["emailAddress"]["address"] == "dev@company.com"

    def test_send_email_string_recipient(self):
        conn = self._make_connector()
        send_resp = _mock_response(None, status=202)
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=send_resp):
            result = conn.send_email(to="single@company.com", subject="s", body="b")
        assert result is True

    def test_send_email_disabled_returns_false(self):
        from connectors.outlook import OutlookConnector
        conn = OutlookConnector()
        result = conn.send_email("x@y.com", "s", "b")
        assert result is False

    def test_get_unread_emails_parses_response(self):
        conn = self._make_connector()
        mail_resp = _mock_response({"value": [
            {
                "id": "msg-1",
                "subject": "Re: Blocked on Story #4851",
                "from": {"emailAddress": {"name": "Alice", "address": "alice@co.com"}},
                "body": {"content": "<p>Use server-side.</p>"},
                "receivedDateTime": "2026-04-15T09:30:00Z",
                "isRead": False,
                "conversationId": "conv-1",
                "replyTo": [],
            }
        ]})
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=mail_resp):
            emails = conn.get_unread_emails()
        assert len(emails) == 1
        assert emails[0].id == "msg-1"
        assert emails[0].sender == "Alice"
        assert emails[0].sender_email == "alice@co.com"
        assert "Use server-side" in emails[0].body
        assert not emails[0].is_read

    def test_get_unread_emails_empty(self):
        conn = self._make_connector()
        mail_resp = _mock_response({"value": []})
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=mail_resp):
            emails = conn.get_unread_emails()
        assert emails == []

    def test_get_unread_emails_error_returns_empty(self):
        conn = self._make_connector()
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", side_effect=_http_error(403)):
            emails = conn.get_unread_emails()
        assert emails == []

    def test_mark_as_read(self):
        conn = self._make_connector()
        patch_resp = _mock_response({"isRead": True})
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=patch_resp) as mock_open:
            result = conn.mark_as_read("msg-1")
        assert result is True
        req = mock_open.call_args[0][0]
        assert req.get_method() == "PATCH"
        body = json.loads(req.data)
        assert body["isRead"] is True

    def test_reply_to_email(self):
        conn = self._make_connector()
        reply_resp = _mock_response(None, status=202)
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=reply_resp):
            result = conn.reply_to_email("msg-1", "Use server-side idempotency key.")
        assert result is True

    def test_send_standup_email(self):
        conn = self._make_connector()
        send_resp = _mock_response(None, status=202)
        entries = [{"persona": "react_developer", "yesterday": "PR #112", "today": "tests", "blockers": ""}]
        with patch.object(conn, "_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=send_resp) as mock_open:
            result = conn.send_standup_email(["team@company.com"], entries)
        assert result is True
        req = mock_open.call_args[0][0]
        body = json.loads(req.data)
        assert "Standup" in body["message"]["subject"]
        assert "react_developer" in body["message"]["body"]["content"]


# ══════════════════════════════════════════════════════════════════════
# connector_from_config helpers
# ══════════════════════════════════════════════════════════════════════

class TestConnectorFromConfig:
    def _make_config(self, **kwargs):
        cfg = MagicMock()
        defaults = {
            "ado_url": "", "ado_pat": "", "ado_project": "", "ado_team": "",
            "ado_api_version": "7.1",
            "teams_webhook_url": "", "ms_tenant_id": "", "ms_client_id": "",
            "ms_client_secret": "", "teams_team_id": "", "teams_channel_id": "",
            "outlook_sender": "", "outlook_reply_to": "",
        }
        defaults.update(kwargs)
        for k, v in defaults.items():
            setattr(cfg, k, v)
        return cfg

    def test_ado_connector_from_config(self):
        from connectors.ado import connector_from_config
        cfg = self._make_config(
            ado_url="https://tfs.co.com/DC",
            ado_pat="pat",
            ado_project="Proj",
            ado_team="Team",
        )
        conn = connector_from_config(cfg)
        assert conn._base == "https://tfs.co.com/DC"
        assert conn._project == "Proj"
        assert conn._enabled is True

    def test_teams_connector_from_config(self):
        from connectors.teams import connector_from_config
        cfg = self._make_config(teams_webhook_url="https://hooks.example.com/wh")
        conn = connector_from_config(cfg)
        assert conn._webhook_url == "https://hooks.example.com/wh"
        assert conn._webhook_enabled is True

    def test_outlook_connector_from_config(self):
        from connectors.outlook import connector_from_config
        cfg = self._make_config(
            ms_tenant_id="t", ms_client_id="c", ms_client_secret="s",
            outlook_sender="agent@co.com",
        )
        conn = connector_from_config(cfg)
        assert conn._sender == "agent@co.com"
        assert conn._enabled is True


# ══════════════════════════════════════════════════════════════════════
# notification_dispatch Teams + Outlook integration
# ══════════════════════════════════════════════════════════════════════

class TestDispatchToTeams:
    def _make_config(self, webhook_url="https://hook.example.com/wh", tenant_id=""):
        cfg = MagicMock()
        cfg.teams_webhook_url = webhook_url
        cfg.ms_tenant_id = tenant_id
        cfg.ms_client_id = ""
        cfg.ms_client_secret = ""
        cfg.teams_team_id = ""
        cfg.teams_channel_id = ""
        return cfg

    def _make_message(self, urgency="normal"):
        msg = MagicMock()
        msg.title = "Test notification"
        msg.body = "Something happened"
        msg.urgency = urgency
        msg.task_id = "t_abc"
        msg.persona = "react_developer"
        return msg

    @pytest.mark.asyncio
    async def test_dispatches_to_teams_when_webhook_configured(self):
        from notification_dispatch import dispatch_to_teams
        cfg = self._make_config(webhook_url="https://hook.example.com")
        msg = self._make_message()
        resp = _mock_response("1", status=200)
        with patch("urllib.request.urlopen", return_value=resp):
            await dispatch_to_teams(msg, cfg)

    @pytest.mark.asyncio
    async def test_skips_when_not_configured(self):
        from notification_dispatch import dispatch_to_teams
        cfg = self._make_config(webhook_url="", tenant_id="")
        msg = self._make_message()
        # Should not raise, no HTTP call
        with patch("urllib.request.urlopen") as mock_open:
            await dispatch_to_teams(msg, cfg)
        mock_open.assert_not_called()
