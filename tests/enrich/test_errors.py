import httpx

from homealize.enrich.errors import describe_error


def test_http_status_error_gives_only_the_code():
    request = httpx.Request("POST", "https://overpass-api.de/api/interpreter?data=" + "x" * 500)
    response = httpx.Response(502, request=request)
    exc = httpx.HTTPStatusError(
        "Server error '502 Bad Gateway' for url '" + str(request.url) + "'", request=request, response=response
    )
    assert describe_error(exc) == "HTTP 502"


def test_other_error_gives_type_and_first_line():
    assert describe_error(ValueError("первая строка\nвторая строка")) == "ValueError: первая строка"
    assert describe_error(KeyError("id")) == "KeyError: 'id'"


def test_long_message_is_cut_to_200_characters():
    text = describe_error(RuntimeError("я" * 500))
    assert len(text) == 200
    assert text.startswith("RuntimeError: яяя")


def test_empty_message_gives_only_the_type():
    assert describe_error(RuntimeError()) == "RuntimeError"
