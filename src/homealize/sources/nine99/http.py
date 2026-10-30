"""HTTP-клиент, который ходит на сайт бережно: по одному запросу, с паузами и повторами."""

import logging
import random
import time
from collections.abc import Callable

import httpx

from homealize.sources.base import RateLimitedError, SourceSchemaError, SourceUnavailableError

log = logging.getLogger(__name__)

RATE_LIMIT_MARKER = "999.md_request_limit"
RETRY_CLIENT_STATUSES = {408, 429}
FORBIDDEN_STATUSES = {401, 403}  # сайт не пускает: это недоступность, а не изменение схемы


def is_retryable_status(status: int) -> bool:
    """Повторяем 408, 429 и любой 5xx (в том числе CDN-коды вроде 522), остальное — нет."""
    return status in RETRY_CLIENT_STATUSES or status >= 500


def is_rate_limit_page(response: httpx.Response) -> bool:
    """Страница лимита: переадресация на неё или ссылка на неё в HTML. В JSON-ответе маркер не ищем."""
    if RATE_LIMIT_MARKER in str(response.url):
        return True
    if "json" in response.headers.get("content-type", ""):
        return False
    return RATE_LIMIT_MARKER in response.text[:2000]


class PoliteClient:
    def __init__(
        self,
        base_url: str,
        *,
        headers: dict[str, str],
        delay_seconds: tuple[float, float],
        timeout_seconds: float,
        backoff_seconds: list[float],
        max_retries: int,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        uniform: Callable[[float, float], float] = random.uniform,
    ) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            headers=headers,
            timeout=timeout_seconds,
            transport=transport,
            follow_redirects=True,
        )
        self._delay = delay_seconds
        self._backoff = backoff_seconds
        self._max_retries = max_retries
        self._sleep = sleep
        self._uniform = uniform
        self._requests_made = 0

    def post_json(self, path: str, payload: dict) -> dict:
        """POST с JSON-телом; возвращает разобранный JSON-объект ответа."""
        attempt = 0
        while True:
            self._pause_between_requests()
            cause: Exception | None = None
            try:
                response = self._client.post(path, json=payload)
            except httpx.RequestError as exc:
                failure = f"{type(exc).__name__}: {exc}"
                cause = exc
            else:
                if is_rate_limit_page(response):
                    raise RateLimitedError(f"999.md ограничил частоту запросов ({response.url})")
                if response.status_code in FORBIDDEN_STATUSES:
                    raise SourceUnavailableError(f"{path}: 999.md не пускает (HTTP {response.status_code})")
                if not is_retryable_status(response.status_code):
                    return self._decode(response)
                failure = f"HTTP {response.status_code}"
            if attempt >= self._max_retries:
                raise SourceUnavailableError(f"{path}: {failure} после {attempt + 1} попыток") from cause
            wait = self._backoff[attempt]
            log.warning("%s: %s, повтор через %s с", path, failure, wait)
            self._sleep(wait)
            attempt += 1

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "PoliteClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    def _pause_between_requests(self) -> None:
        if self._requests_made:
            self._sleep(self._uniform(*self._delay))
        self._requests_made += 1

    @staticmethod
    def _decode(response: httpx.Response) -> dict:
        if response.status_code != 200:
            raise SourceSchemaError(f"неожиданный ответ HTTP {response.status_code}: {response.text[:300]}")
        try:
            data = response.json()
        except ValueError as exc:
            raise SourceSchemaError(f"ответ не является JSON: {exc}") from exc
        if not isinstance(data, dict):
            raise SourceSchemaError("JSON-ответ не является объектом")
        return data
