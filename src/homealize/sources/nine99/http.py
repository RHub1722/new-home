"""HTTP-клиент, который ходит на сайт бережно: по одному запросу, с паузами и повторами."""

import logging
import random
import time
from collections.abc import Callable

import httpx

from homealize.sources.base import RateLimitedError, SourceSchemaError, SourceUnavailableError

log = logging.getLogger(__name__)

RATE_LIMIT_MARKER = "request_limit"
RETRY_STATUSES = {429, 500, 502, 503, 504}


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
            try:
                response = self._client.post(path, json=payload)
            except httpx.TransportError as exc:
                failure = f"{type(exc).__name__}: {exc}"
            else:
                if RATE_LIMIT_MARKER in str(response.url) or RATE_LIMIT_MARKER in response.text[:2000]:
                    raise RateLimitedError(f"999.md ограничил частоту запросов ({response.url})")
                if response.status_code not in RETRY_STATUSES:
                    return self._decode(response)
                failure = f"HTTP {response.status_code}"
            if attempt >= self._max_retries:
                raise SourceUnavailableError(f"{path}: {failure} после {attempt + 1} попыток")
            wait = self._backoff[attempt]
            log.warning("%s: %s, повтор через %s с", path, failure, wait)
            self._sleep(wait)
            attempt += 1

    def close(self) -> None:
        self._client.close()

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
