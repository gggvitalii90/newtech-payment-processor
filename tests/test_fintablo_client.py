from __future__ import annotations

import json

import pytest

from payment_processor.fintablo_client import FinTabloClient, FinTabloError, FinTabloSettings, load_fintablo_settings


def make_transport(responses, captured):
    queue = list(responses)

    def transport(request, timeout):
        captured.append({
            "url": request.full_url,
            "authorization": request.get_header("Authorization"),
            "timeout": timeout,
        })
        status, headers, payload = queue.pop(0)
        return status, headers, json.dumps(payload).encode("utf-8")

    return transport


def test_load_fintablo_settings_requires_token() -> None:
    with pytest.raises(FinTabloError):
        load_fintablo_settings({})


def test_client_sends_bearer_token_and_query_params() -> None:
    captured = []
    client = FinTabloClient(
        FinTabloSettings(token="secret", base_url="https://example.test"),
        transport=make_transport([(200, {"X-Request-Id": "req-1"}, {"status": 200, "items": []})], captured),
    )

    assert client.list_transactions(date_from="01.07.2026", date_to="01.07.2026") == []

    assert captured[0]["authorization"] == "Bearer secret"
    assert "dateFrom=01.07.2026" in captured[0]["url"]
    assert "dateTo=01.07.2026" in captured[0]["url"]
    assert "pageSize=1000" in captured[0]["url"]


def test_client_paginates_until_short_page() -> None:
    captured = []
    first = [{"id": index} for index in range(1000)]
    second = [{"id": 1001}]
    client = FinTabloClient(
        FinTabloSettings(token="secret", base_url="https://example.test"),
        transport=make_transport([
            (200, {}, {"status": 200, "items": first}),
            (200, {}, {"status": 200, "items": second}),
        ], captured),
    )

    items = client.list_moneybags()

    assert len(items) == 1001
    assert "page=1" in captured[0]["url"]
    assert "page=2" in captured[1]["url"]


def test_client_raises_on_http_error() -> None:
    client = FinTabloClient(
        FinTabloSettings(token="secret", base_url="https://example.test"),
        transport=make_transport([(401, {"X-Request-Id": "bad"}, {"status": 401, "statusText": "invalid"})], []),
    )

    with pytest.raises(FinTabloError, match="HTTP 401"):
        client.list_moneybags()


def test_client_retries_transient_http_errors() -> None:
    captured = []
    client = FinTabloClient(
        FinTabloSettings(token="secret", base_url="https://example.test"),
        transport=make_transport([
            (503, {}, {"status": 503, "statusText": "busy"}),
            (200, {}, {"status": 200, "items": [{"id": 1}]}),
        ], captured),
        retry_delay_seconds=0,
    )

    assert client.list_moneybags() == [{"id": 1}]
    assert len(captured) == 2
