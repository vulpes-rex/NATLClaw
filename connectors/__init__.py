"""External service connectors for NATLClaw.

Available connectors:

    AzureDevOpsConnector — pull work items from ADO sprints, push status
                           and comments back, create pull requests.
    TeamsConnector       — post messages and Adaptive Cards to Microsoft
                           Teams channels via incoming webhook or Graph API.
    OutlookConnector     — send email and read replies via Microsoft Graph.

All connectors are optional: if the relevant credentials are missing from
config, the connector marks itself disabled and all operations are no-ops.
No new *required* dependencies — HTTP uses stdlib ``urllib``, Graph auth
uses the same pattern.
"""
from __future__ import annotations

from .ado import AzureDevOpsConnector, PullRequest, SprintInfo, WorkItem
from .base import ConnectorStatus
from .outlook import OutlookConnector
from .teams import TeamsConnector

__all__ = [
    "AzureDevOpsConnector",
    "WorkItem",
    "SprintInfo",
    "PullRequest",
    "TeamsConnector",
    "OutlookConnector",
    "ConnectorStatus",
]
