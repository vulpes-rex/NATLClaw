from __future__ import annotations

from error_classification import classify_error_text, top_error_types


def test_classify_timeout():
    assert classify_error_text("Request timed out after 30s") == "timeout"


def test_classify_network():
    assert classify_error_text("failed to connect to upstream") == "network"


def test_classify_auth():
    assert classify_error_text("Invalid API key for provider") == "auth"


def test_classify_validation():
    assert classify_error_text("JSON decode error in payload") == "validation"


def test_top_error_types_counts():
    results = top_error_types(
        [
            "failed to connect to db",
            "connection refused",
            "request timed out",
            "invalid api key",
            "invalid api key",
        ],
        limit=3,
    )
    assert results[0]["type"] in {"auth", "network"}
    assert sum(item["count"] for item in results) == 5
