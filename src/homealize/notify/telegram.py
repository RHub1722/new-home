"""Telegram Bot API через httpx: отправка сообщений и приём команд (long polling).

Токен — часть адреса запроса, поэтому ни адреса, ни исходные тексты ошибок httpx наружу не выходят.
"""

import httpx

CHUNK = 4000  # Telegram принимает до 4096 символов; оставляем запас


class TelegramError(Exception):
    """Telegram не принял запрос. permanent — это сообщение повторять бессмысленно (HTTP 400)."""

    def __init__(self, message: str, *, permanent: bool = False, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.permanent = permanent
        self.retry_after = retry_after


class TelegramClient:
    def __init__(
        self,
        token: str,
        *,
        api_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._token = token
        self._client = httpx.Client(
            base_url=f"{api_url.rstrip('/')}/bot{token}/", timeout=timeout_seconds, transport=transport
        )

    def send_message(self, chat_id: int, text: str, *, preview_url: str | None = None) -> None:
        """HTML-сообщение; длинное уходит несколькими. preview_url — картинка над текстом первой части."""
        for index, chunk in enumerate(split_text(text)):
            payload: dict = {"chat_id": chat_id, "text": chunk, "parse_mode": "HTML"}
            if index == 0 and preview_url:
                payload["link_preview_options"] = {
                    "url": preview_url, "prefer_large_media": True, "show_above_text": True,
                }
            else:
                payload["link_preview_options"] = {"is_disabled": True}
            self._call("sendMessage", payload)

    def get_updates(self, offset: int | None, timeout: int) -> list[dict]:
        """Новые сообщения боту; ждёт до timeout секунд, если их нет."""
        payload: dict = {"timeout": timeout, "allowed_updates": ["message"]}
        if offset is not None:
            payload["offset"] = offset
        result = self._call("getUpdates", payload, timeout=timeout + 10)
        return result if isinstance(result, list) else []

    def get_me(self) -> dict:
        result = self._call("getMe", {})
        return result if isinstance(result, dict) else {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TelegramClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _call(self, method: str, payload: dict, *, timeout: float | None = None) -> object:
        try:
            response = self._client.post(
                method, json=payload, timeout=timeout if timeout is not None else httpx.USE_CLIENT_DEFAULT
            )
        except httpx.RequestError as exc:
            raise TelegramError(f"Telegram недоступен: {type(exc).__name__}: {self._clean(exc)}") from None
        try:
            data = response.json()
        except ValueError:
            raise TelegramError(f"Telegram ответил не JSON (HTTP {response.status_code})") from None
        if not isinstance(data, dict):
            raise TelegramError(f"Telegram ответил не объектом (HTTP {response.status_code})")
        if data.get("ok") is True:
            return data.get("result")
        code = data.get("error_code", response.status_code)
        parameters = data.get("parameters") if isinstance(data.get("parameters"), dict) else {}
        retry_after = parameters.get("retry_after")
        raise TelegramError(
            f"Telegram: {self._clean(data.get('description') or f'HTTP {code}')}",
            permanent=code == 400,
            retry_after=float(retry_after) if isinstance(retry_after, (int, float)) else None,
        )

    def _clean(self, value: object) -> str:
        return str(value).replace(self._token, "***")[:200]


def split_text(text: str, limit: int = CHUNK) -> list[str]:
    """Делит текст по строкам на части не длиннее limit; слишком длинная строка режется."""
    chunks: list[str] = []
    lines: list[str] = []
    size = 0
    for line in text.split("\n"):
        while len(line) > limit:
            if lines:
                chunks.append("\n".join(lines))
                lines, size = [], 0
            chunks.append(line[:limit])
            line = line[limit:]
        extra = len(line) + (1 if lines else 0)
        if lines and size + extra > limit:
            chunks.append("\n".join(lines))
            lines, size, extra = [], 0, len(line)
        lines.append(line)
        size += extra
    chunks.append("\n".join(lines))
    return [chunk for chunk in chunks if chunk] or [""]
