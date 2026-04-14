from __future__ import annotations

from collections import Counter
from typing import Iterable


def classify_error_text(text: str) -> str:
    """Classify free-form error text into a stable operator-facing category."""
    t = (text or "").lower()
    if not t:
        return "unknown"

    if "timeout" in t or "timed out" in t:
        return "timeout"
    if "auth" in t or "unauthorized" in t or "forbidden" in t or "api key" in t:
        return "auth"
    if any(x in t for x in ("connect", "connection", "dns", "socket", "network", "refused")):
        return "network"
    if any(x in t for x in ("file", "ioerror", "oserror", "permission", "no such file", "disk")):
        return "io"
    if any(x in t for x in ("json", "decode", "parse", "schema", "validation", "valueerror")):
        return "validation"
    if any(x in t for x in ("tool", "mcp", "function call", "subprocess")):
        return "tooling"
    if any(x in t for x in ("memory", "state", "sqlite", "database", "db")):
        return "state"
    return "unknown"


def top_error_types(error_texts: Iterable[str], *, limit: int = 3) -> list[dict]:
    """Return the most common classified error categories."""
    counts = Counter(classify_error_text(text) for text in error_texts)
    return [{"type": error_type, "count": count} for error_type, count in counts.most_common(limit)]
