"""Короткое описание сбоя внешнего сервиса для предупреждений и журнала прогонов."""

import httpx

MAX_LENGTH = 200


def describe_error(exc: Exception) -> str:
    """«HTTP 502» для ответа с ошибкой, иначе «Тип: первая строка сообщения» не длиннее MAX_LENGTH."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"  # в тексте httpx есть полный URL с запросом: он не нужен
    first_line = next(iter(str(exc).splitlines()), "")
    name = type(exc).__name__
    return (f"{name}: {first_line}" if first_line else name)[:MAX_LENGTH]
