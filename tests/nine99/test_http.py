import httpx
import pytest

from homealize.sources.base import RateLimitedError, SourceSchemaError, SourceUnavailableError
from homealize.sources.nine99.http import PoliteClient

OK = {"data": {"ok": True}}


def make_client(responses, *, max_retries=3, backoff=(5.0, 15.0, 45.0)):
    """Клиент с поддельным транспортом: отдаёт ответы по очереди, последний повторяет."""
    requests, sleeps = [], []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        item = responses[min(len(requests), len(responses)) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    client = PoliteClient(
        "https://999.md",
        headers={"lang": "ru"},
        delay_seconds=(2.0, 3.0),
        timeout_seconds=5,
        backoff_seconds=list(backoff),
        max_retries=max_retries,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
        uniform=lambda low, high: low,
    )
    return client, requests, sleeps


def test_returns_json_and_sends_headers():
    client, requests, sleeps = make_client([httpx.Response(200, json=OK)])
    assert client.post_json("/graphql", {"query": "q"}) == OK
    assert requests[0].method == "POST"
    assert requests[0].url == "https://999.md/graphql"
    assert requests[0].headers["lang"] == "ru"
    assert sleeps == []


def test_pauses_between_requests_but_not_before_first():
    client, _, sleeps = make_client([httpx.Response(200, json=OK)])
    client.post_json("/graphql", {})
    client.post_json("/graphql", {})
    assert sleeps == [2.0]


def test_retries_on_server_error_then_succeeds():
    client, requests, sleeps = make_client(
        [httpx.Response(503), httpx.Response(503), httpx.Response(200, json=OK)]
    )
    assert client.post_json("/graphql", {}) == OK
    assert len(requests) == 3
    assert sleeps == [5.0, 2.0, 15.0, 2.0]


def test_retries_on_timeout():
    client, requests, _ = make_client(
        [httpx.ConnectTimeout("timeout"), httpx.Response(200, json=OK)]
    )
    assert client.post_json("/graphql", {}) == OK
    assert len(requests) == 2


def test_gives_up_after_max_retries():
    client, requests, _ = make_client([httpx.Response(429)], max_retries=2, backoff=(1.0, 2.0))
    with pytest.raises(SourceUnavailableError, match="HTTP 429"):
        client.post_json("/graphql", {})
    assert len(requests) == 3


def test_rate_limit_page_stops_immediately():
    page = httpx.Response(200, text="<html><a href='/999.md_request_limit.html'>limit</a></html>")
    client, requests, _ = make_client([page])
    with pytest.raises(RateLimitedError):
        client.post_json("/graphql", {})
    assert len(requests) == 1


def test_non_json_response_is_schema_error():
    client, _, _ = make_client([httpx.Response(200, text="<html>oops</html>")])
    with pytest.raises(SourceSchemaError):
        client.post_json("/graphql", {})


def test_client_error_status_is_schema_error():
    client, requests, _ = make_client([httpx.Response(400, json={"errors": [{"message": "bad"}]})])
    with pytest.raises(SourceSchemaError, match="400"):
        client.post_json("/graphql", {})
    assert len(requests) == 1


@pytest.mark.parametrize("status", [408, 429, 501, 522])
def test_transient_status_is_retried_then_succeeds(status):
    client, requests, _ = make_client([httpx.Response(status), httpx.Response(200, json=OK)])
    assert client.post_json("/graphql", {}) == OK
    assert len(requests) == 2


def test_marker_inside_json_is_not_a_rate_limit():
    body = {"data": {"note": "999.md_request_limit"}}
    client, _, _ = make_client([httpx.Response(200, json=body)])
    assert client.post_json("/graphql", {}) == body


def test_redirect_to_the_limit_page_is_a_rate_limit():
    client, requests, _ = make_client(
        [
            httpx.Response(302, headers={"Location": "/999.md_request_limit.html"}),
            httpx.Response(200, text="<html>лимит</html>"),
        ]
    )
    with pytest.raises(RateLimitedError):
        client.post_json("/graphql", {})
    assert len(requests) == 2


@pytest.mark.parametrize("status", [401, 403])
def test_forbidden_is_unavailable_not_schema_change(status):
    client, requests, _ = make_client([httpx.Response(status, text="forbidden")])
    with pytest.raises(SourceUnavailableError, match=str(status)):
        client.post_json("/graphql", {})
    assert len(requests) == 1


def test_any_request_error_is_retried_and_kept_as_cause():
    client, requests, _ = make_client([httpx.DecodingError("битый gzip")], max_retries=1, backoff=(1.0,))
    with pytest.raises(SourceUnavailableError, match="DecodingError") as caught:
        client.post_json("/graphql", {})
    assert isinstance(caught.value.__cause__, httpx.DecodingError)
    assert len(requests) == 2


def test_client_is_a_context_manager():
    client, _, _ = make_client([httpx.Response(200, json=OK)])
    with client as same:
        assert same is client
    assert client._client.is_closed
