"""Base types shared across all connectors."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConnectorStatus:
    """Health / availability status for a connector."""

    name: str
    enabled: bool
    healthy: bool
    error: str = ""

    def __str__(self) -> str:
        if not self.enabled:
            return f"{self.name}: disabled"
        if self.healthy:
            return f"{self.name}: ok"
        return f"{self.name}: error — {self.error}"
