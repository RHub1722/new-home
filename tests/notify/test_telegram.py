import json

import httpx
import pytest

from homealize.notify.telegram import TelegramClient, TelegramError, split_text

TOKEN = "123456:SECRET-token"


def make_client(handler):
    return TelegramClient(
        TOKEN, api_url="https://api.telegram.org", timeout_seconds=5, transport=httpx.MockTransport(handler)
    )


def recorder(requests, result=True):
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": result})

    return handler


def test_send_message_posts_html_with_a_photo_preview():
    requests = []
    make_client(recorder(requests, {"message_id": 1})).send_message(
        42, "<b>Привет</b>", preview_url="https://i.example/1.jpg"
    )
    request = requests[0]
    assert request.url == f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    body = json.loads(request.content)
    assert (body["chat_id"], body["text"], body["parse_mode"]) == (42, "<b>Привет</b>", "HTML")
    assert body["link_preview_options"] == {
        "url": "https://i.example/1.jpg", "prefer_large_media": True, "show_above_text": True,
    }


def test_message_without_photo_has_no_preview():
    requests = []
    make_client(recorder(requests)).send_message(42, "текст")
    assert json.loads(requests[0].content)["link_preview_options"] == {"is_disabled": True}


def test_long_text_is_split_by_lines_and_only_the_first_part_has_the_photo():
    requests = []
    text = "\n".join(f"строка {i} " + "x" * 90 for i in range(100))
    make_client(recorder(requests)).send_message(42, text, preview_url="https://i.example/1.jpg")
    bodies = [json.loads(request.content) for request in requests]
    assert len(bodies) == 3
    assert all(len(body["text"]) <= 4000 for body in bodies)
    assert "\n".join(body["text"] for body in bodies) == text
    assert bodies[0]["link_preview_options"]["url"] == "https://i.example/1.jpg"
    assert bodies[1]["link_preview_options"] == {"is_disabled": True}


def test_split_text():
    assert split_text("a\n\nb") == ["a\n\nb"]
    assert split_text("x" * 9000) == ["x" * 4000, "x" * 4000, "x" * 1000]


def test_bad_request_is_permanent_and_hides_the_token():
    def handler(request):
        return httpx.Response(
            400, json={"ok": False, "error_code": 400, "description": f"Bad Request: bot{TOKEN} can't parse entities"}
        )

    with pytest.raises(TelegramError) as caught:
        make_client(handler).send_message(1, "x")
    assert caught.value.permanent
    assert TOKEN not in str(caught.value)
    assert "can't parse entities" in str(caught.value)


def test_flood_limit_reports_retry_after():
    def handler(request):
        return httpx.Response(
            429,
            json={"ok": False, "error_code": 429, "description": "Too Many Requests", "parameters": {"retry_after": 3}},
        )

    with pytest.raises(TelegramError) as caught:
        make_client(handler).send_message(1, "x")
    assert (caught.value.permanent, caught.value.retry_after) == (False, 3.0)


def test_network_error_is_temporary_and_does_not_leak_the_url():
    def handler(request):
        raise httpx.ConnectError(f"cannot connect to {request.url}")

    with pytest.raises(TelegramError) as caught:
        make_client(handler).send_message(1, "x")
    assert not caught.value.permanent
    assert TOKEN not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__suppress_context__


def test_non_json_answer_is_temporary():
    with pytest.raises(TelegramError, match="502"):
        make_client(lambda request: httpx.Response(502, text="Bad Gateway")).get_me()


def test_get_updates_sends_offset_and_waits_longer_than_the_poll():
    requests = []
    updates = [{"update_id": 7, "message": {"text": "/top"}}]
    client = make_client(recorder(requests, updates))
    assert client.get_updates(7, 50) == updates
    body = json.loads(requests[0].content)
    assert (body["offset"], body["timeout"], body["allowed_updates"]) == (7, 50, ["message"])
    assert requests[0].extensions["timeout"]["read"] == 60


def test_get_updates_without_offset():
    requests = []
    make_client(recorder(requests, [])).get_updates(None, 0)
    assert "offset" not in json.loads(requests[0].content)
